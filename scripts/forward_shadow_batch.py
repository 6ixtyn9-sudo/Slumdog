#!/usr/bin/env python3
"""Forward shadow batch driver — rolling-date capture + evaluate + bundle.

Runs the forward shadow pipeline for the next N target dates, starting
from D+2 (the earliest date reachable under the frozen timing-v1
contract). For each date:

1. Collision check: skip if evidence already exists.
2. Capture: one Forebet listing per sport (workers=1, 62s pauses).
3. Evaluate: run the frozen shadow evaluator.
4. Bundle + verify: create a deterministic bundle and verify it.

Before the forward pass, this driver also settles any overdue past
predictions (D+1 rule): a prediction run's ``target_date`` is treated
as safe to settle starting the day after that date, once Forebet's
final results should exist. See ``find_settleable_dates`` and
``run_settlement_for_date`` below. Settlement never mutates a
prediction run, never blocks the forward pass, and is fully isolated
per date — one date's settlement failure does not affect any other
date or the forward capture that follows.

CLI::

    python scripts/forward_shadow_batch.py [--dates N] [--root ROOT]
        [--pause-seconds 62] [--capture-timeout 45] [--dry-run]
        [--skip-settlement]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path


def compute_target_dates(n: int = 5, *, base: dt.date | None = None) -> list[str]:
    """Return the next N reachable target dates (D+2 through D+N+1).

    Under timing-v1, the earliest reachable target date is always D+2
    (the cutoff for D+1 has already passed by the time any run starts).
    """
    now_utc = dt.datetime.now(dt.timezone.utc).date()
    base = base or now_utc
    return [(base + dt.timedelta(days=i + 2)).isoformat() for i in range(n)]


def has_existing_evidence(target_date: str, repo_root: Path) -> bool:
    """Check if shadow evidence already exist for a target date.

    Returns True if any completed run exists under
    ``data/reports/shadow/<target_date>/``.
    """
    shadow_dir = repo_root / "data" / "reports" / "shadow" / target_date
    if not shadow_dir.is_dir():
        return False
    for child in shadow_dir.iterdir():
        if child.is_dir() and child.name != "BLOCKED":
            # Check for a completed run (has shadow_selections.json)
            if (child / "shadow_selections.json").exists():
                return True
    return False


# ---------------------------------------------------------------------------
# Automated D+1 settlement (owner-confirmed 2026-09-06)
#
# A prediction run's target_date is treated as safe to settle starting
# the day after that date (D+1): by then Forebet's final results for
# that date should exist. This is intentionally simple (a fixed
# calendar offset, not a kickoff-time check) — the same conservative
# posture as the frozen timing-v1 pre-event cutoff, applied to the
# other end of the run's lifecycle. Settlement is fully idempotent and
# additive: it only ever considers runs that have selections but no
# settlement.json yet, and never touches a run that is already
# settled or still blocked.
# ---------------------------------------------------------------------------


NON_DATE_SHADOW_DIRS = frozenset({"BLOCKED", "bundles", "settlements"})


def _is_target_date_dir(name: str) -> bool:
    """True if ``name`` looks like a target-date directory (YYYY-MM-DD).

    Excludes known non-date siblings under ``data/reports/shadow/``
    (``bundles``, ``settlements``) and any ``batch_*`` driver-log
    directory, without assuming an exhaustive denylist — any name that
    does not parse as an ISO date is excluded too.
    """
    if name in NON_DATE_SHADOW_DIRS:
        return False
    try:
        dt.date.fromisoformat(name)
    except ValueError:
        return False
    return True


def find_settleable_run(target_date: str, repo_root: Path) -> str | None:
    """Return the run_id of the one completed, unsettled run for a date.

    Returns ``None`` if the date has no completed run, or if its
    completed run already has a ``settlement.json``. Prediction runs
    are frozen and immutable once written (per the shadow evaluator's
    no-overwrite contract), so at most one completed run per date is
    expected in current operation; if more than one existed, the first
    completed, unsettled one found (sorted by run_id) is returned so
    behavior stays deterministic.
    """
    shadow_dir = repo_root / "data" / "reports" / "shadow" / target_date
    if not shadow_dir.is_dir():
        return None
    candidates = []
    for child in sorted(shadow_dir.iterdir()):
        if not child.is_dir() or child.name == "BLOCKED":
            continue
        if not (child / "shadow_selections.json").is_file():
            continue
        if (child / "settlement.json").exists():
            continue  # already settled — nothing to do
        candidates.append(child.name)
    return candidates[0] if candidates else None


def find_settleable_dates(
    repo_root: Path,
    *,
    as_of: dt.date | None = None,
) -> list[tuple[str, str]]:
    """Return ``(target_date, run_id)`` pairs eligible for D+1 settlement.

    A date is eligible when:
    - it is a target-date directory under ``data/reports/shadow/``;
    - ``target_date <= as_of - 1 day`` (the D+1 rule: settle starting
      the day after the predicted date, never the same day or before);
    - it has exactly one completed run without an existing
      ``settlement.json`` (see :func:`find_settleable_run`).

    Returned in ascending date order (oldest first), so a backlog
    clears from the oldest overdue date forward.
    """
    as_of = as_of or dt.datetime.now(dt.timezone.utc).date()
    cutoff = as_of - dt.timedelta(days=1)
    shadow_root = repo_root / "data" / "reports" / "shadow"
    if not shadow_root.is_dir():
        return []
    out: list[tuple[str, str]] = []
    for child in sorted(shadow_root.iterdir()):
        if not child.is_dir() or not _is_target_date_dir(child.name):
            continue
        target_date = child.name
        if dt.date.fromisoformat(target_date) > cutoff:
            continue
        run_id = find_settleable_run(target_date, repo_root)
        if run_id is not None:
            out.append((target_date, run_id))
    return out


def _sports_in_run(target_date: str, run_id: str, repo_root: Path) -> list[str]:
    """Return the distinct sports actually present in a prediction run.

    Reads both ``selections[]`` and the manifest's ``considered_pool[]``
    (settlement grades both), so the settlement capture fetches exactly
    the sports it needs — no more, no less. Falls back to an empty list
    (which callers should treat as "settle with the fetch-everything
    default") if the run's files cannot be parsed; this keeps a
    corrupted or unexpected run from silently skipping settlement.
    """
    run_dir = repo_root / "data" / "reports" / "shadow" / target_date / run_id
    sports: set[str] = set()
    try:
        selections = json.loads((run_dir / "shadow_selections.json").read_text())
        for sel in selections.get("selections", []):
            sport = sel.get("sport")
            if sport:
                sports.add(sport)
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        # Malformed/unexpected shape (e.g. top-level JSON is a list, or a
        # selection entry isn't a dict) must not raise here -- this helper
        # feeds run_settlement_for_date's "never raises" contract.
        pass
    manifest_path = run_dir / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text())
            for cp in manifest.get("considered_pool", []):
                sport = cp.get("sport")
                if sport:
                    sports.add(sport)
        except (OSError, json.JSONDecodeError, AttributeError, TypeError):
            pass
    return sorted(sports)


def run_settlement_for_date(
    target_date: str,
    run_id: str,
    repo_root: Path,
    *,
    pause_seconds: int = 62,
    timeout: int = 45,
) -> dict:
    """Settle one overdue prediction run. Never raises.

    Returns a result dict with ``status`` one of:
    ``SETTLED`` (success), ``SETTLEMENT_FAILED`` (the settlement
    module raised — logged, not fatal to the caller), or
    ``NO_SPORTS_RESOLVED`` (the run's files could not be read to
    determine which sports to fetch; settlement is skipped for this
    date rather than guessing).
    """
    from slumdog.shadow_settle import SettlementError, settle_run

    result: dict = {
        "target_date": target_date,
        "run_id": run_id,
        "status": "PENDING",
        "error": None,
    }
    sports = _sports_in_run(target_date, run_id, repo_root)
    if not sports:
        result["status"] = "NO_SPORTS_RESOLVED"
        result["error"] = "could not determine sports from run files"
        return result
    result["sports"] = sports
    try:
        settled = settle_run(
            target_date=target_date,
            run_id=run_id,
            repo_root=repo_root,
            pause_seconds=pause_seconds,
            timeout=timeout,
            sports=sports,
        )
        result["status"] = "SETTLED"
        result["settlement_artifact_path"] = settled.settlement_artifact_path
        result["settlement_artifact_sha256"] = settled.settlement_artifact_sha256
        result["primary_hit_rate"] = settled.summary.get("primary_hit_rate")
    except SettlementError as exc:
        result["status"] = "SETTLEMENT_FAILED"
        result["error"] = f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # never let one date's failure abort the batch
        result["status"] = "SETTLEMENT_FAILED"
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def run_settlement_backlog(
    repo_root: Path,
    *,
    as_of: dt.date | None = None,
    pause_seconds: int = 62,
    timeout: int = 45,
    dry_run: bool = False,
) -> list[dict]:
    """Settle every overdue (D+1) prediction run, oldest date first.

    Isolated per date: one date's failure is recorded and the loop
    continues to the next date. Never touches an already-settled run
    (idempotent — safe to call on every scheduled invocation).
    """
    pending = find_settleable_dates(repo_root, as_of=as_of)
    results = []
    for i, (target_date, run_id) in enumerate(pending):
        if dry_run:
            results.append({
                "target_date": target_date, "run_id": run_id,
                "status": "DRY_RUN", "error": None,
            })
            continue
        if i > 0:
            time.sleep(pause_seconds)
        results.append(run_settlement_for_date(
            target_date, run_id, repo_root,
            pause_seconds=pause_seconds, timeout=timeout,
        ))
    return results


def run_capture(target_date: str, repo_root: Path, *, pause_seconds: int = 62, timeout: int = 45) -> dict:
    """Capture Forebet listings for a target date.

    Uses the existing collector with workers=1 and 62s pauses.
    Returns the capture receipt dict.
    """
    from slumdog.forebet import ForebetCollector

    collector = ForebetCollector(root=repo_root, timeout=timeout, workers=1)
    captures = collector.capture_selected(target_date)
    receipt_path = repo_root / "data" / "reports" / f"capture_{target_date}.json"
    if receipt_path.is_file():
        return json.loads(receipt_path.read_text())
    return {
        "target_date": target_date,
        "captured": [{"sport": c.sport, "sha256": c.sha256} for c in captures],
        "failures": [],
    }


def run_evaluator(
    target_date: str,
    repo_root: Path,
) -> dict:
    """Run the shadow evaluator for a target date.

    Returns the evaluator output dict.
    """
    receipt_path = repo_root / "data" / "reports" / f"capture_{target_date}.json"
    config_path = repo_root / "config" / "shadow_evaluator_v1.json"
    if not receipt_path.is_file():
        raise RuntimeError(f"capture receipt not found: {receipt_path}")
    if not config_path.is_file():
        raise RuntimeError(f"shadow evaluator config not found: {config_path}")

    # Find history files
    history_args = []
    reports_dir = repo_root / "data" / "reports"
    if reports_dir.is_dir():
        for hf in sorted(reports_dir.glob("history_*.jsonl.gz")):
            history_args.extend(["--history", str(hf)])
        for hf in sorted(reports_dir.glob("history_*.json")):
            if hf.name != f"capture_{target_date}.json":
                history_args.extend(["--history", str(hf)])

    cmd = [
        sys.executable, "-m", "slumdog.shadow_evaluator",
        "--date", target_date,
        "--capture-receipt", str(receipt_path),
        "--config", str(config_path),
        "--root", str(repo_root),
    ] + history_args

    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=300, cwd=str(repo_root),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"shadow evaluator failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
    return json.loads(result.stdout)


def run_bundle(target_date: str, run_id: str, repo_root: Path) -> dict:
    """Create and verify a bundle for a completed run.

    Returns the bundle receipt dict.
    """
    run_dir = repo_root / "data" / "reports" / "shadow" / target_date / run_id
    output_dir = repo_root / "data" / "reports" / "shadow" / target_date / "bundles"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Create
    cmd_create = [
        sys.executable, "-m", "slumdog.shadow_bundle", "create",
        "--run-dir", str(run_dir),
        "--output-dir", str(output_dir),
        "--root", str(repo_root),
    ]
    result = subprocess.run(
        cmd_create, capture_output=True, text=True, timeout=120, cwd=str(repo_root),
    )
    if result.returncode != 0:
        raise RuntimeError(f"bundle create failed: {result.stderr.strip()}")
    create_receipt = json.loads(result.stdout)

    # Verify
    archive_path = create_receipt["archive_path"]
    receipt_path = create_receipt["receipt_path"]
    cmd_verify = [
        sys.executable, "-m", "slumdog.shadow_bundle", "verify",
        "--bundle", archive_path,
        "--receipt", receipt_path,
    ]
    result_v = subprocess.run(
        cmd_verify, capture_output=True, text=True, timeout=120, cwd=str(repo_root),
    )
    if result_v.returncode != 0:
        raise RuntimeError(f"bundle verify failed: {result_v.stderr.strip()}")

    return create_receipt


def process_date(
    target_date: str,
    repo_root: Path,
    *,
    pause_seconds: int = 62,
    timeout: int = 45,
    dry_run: bool = False,
) -> dict:
    """Process a single target date through the full pipeline."""
    result = {
        "target_date": target_date,
        "status": "PENDING",
        "run_id": None,
        "bundle_verified": False,
        "error": None,
    }

    # Collision check
    if has_existing_evidence(target_date, repo_root):
        result["status"] = "SKIPPED_EXISTING"
        return result

    if dry_run:
        result["status"] = "DRY_RUN"
        return result

    try:
        # Step 1: Capture
        capture_receipt = run_capture(
            target_date, repo_root,
            pause_seconds=pause_seconds, timeout=timeout,
        )
        captured_count = len(capture_receipt.get("captured", []))
        failure_count = len(capture_receipt.get("failures", []))
        result["capture"] = {
            "captured": captured_count,
            "failures": failure_count,
        }

        if captured_count == 0:
            result["status"] = "NO_CAPTURES"
            return result

        # Step 2: Evaluate
        eval_output = run_evaluator(target_date, repo_root)
        run_id = eval_output.get("run_id", "")
        run_status = eval_output.get("run_status", "")
        result["run_id"] = run_id
        result["run_status"] = run_status

        if run_status == "SHADOW_RUN_BLOCKED":
            result["status"] = "EVALUATOR_BLOCKED"
            return result

        # Step 3: Bundle + Verify (only for successful runs)
        if run_status in ("SHADOW_SELECTIONS_EMITTED", "SHADOW_NO_SELECTION"):
            try:
                bundle_receipt = run_bundle(target_date, run_id, repo_root)
                result["bundle_verified"] = True
                result["bundle"] = {
                    "archive_path": bundle_receipt.get("archive_path"),
                    "archive_sha256": bundle_receipt.get("archive_sha256"),
                }
            except RuntimeError as exc:
                result["bundle_error"] = str(exc)
                # Bundle failure doesn't invalidate the run itself

        result["status"] = "COMPLETED"

    except Exception as exc:
        result["status"] = "FAILED"
        result["error"] = f"{type(exc).__name__}: {exc}"

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Forward shadow batch: rolling-date capture + evaluate + bundle",
    )
    parser.add_argument("--dates", type=int, default=5,
                        help="Number of target dates to process (default: 5)")
    parser.add_argument("--root", type=Path, default=Path("."),
                        help="Repository root (default: cwd)")
    parser.add_argument("--pause-seconds", type=int, default=62,
                        help="Pause between sport captures (default: 62)")
    parser.add_argument("--capture-timeout", type=int, default=45,
                        help="Per-request timeout (default: 45)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without executing")
    parser.add_argument("--skip-settlement", action="store_true",
                        help="Skip the D+1 settlement backlog pass "
                             "(forward capture only)")
    args = parser.parse_args(argv)

    repo_root = args.root.resolve()

    # Settlement pass first: grade any overdue (D+1) prediction runs
    # before capturing/ranking new ones. Isolated per date and fully
    # idempotent — safe on every invocation, including this one.
    settlement_results: list[dict] = []
    if not args.skip_settlement:
        settlement_results = run_settlement_backlog(
            repo_root,
            pause_seconds=args.pause_seconds,
            timeout=args.capture_timeout,
            dry_run=args.dry_run,
        )
        if settlement_results:
            print(f"Settlement backlog: {len(settlement_results)} overdue date(s)", file=sys.stderr)
            for sr in settlement_results:
                print(f"  {sr['target_date']} ({sr['run_id']}): {sr['status']}", file=sys.stderr)
                if sr.get("error"):
                    print(f"    Error: {sr['error']}", file=sys.stderr)
        else:
            print("Settlement backlog: nothing overdue", file=sys.stderr)

    targets = compute_target_dates(args.dates)
    print(f"Forward shadow batch: {len(targets)} dates starting from {targets[0]}", file=sys.stderr)
    print(f"Repository root: {repo_root}", file=sys.stderr)

    results = []
    for i, target_date in enumerate(targets):
        if i > 0:
            # Pause between dates (not between sports — that's handled by the collector)
            time.sleep(args.pause_seconds)
        print(f"\n[{i+1}/{len(targets)}] Processing {target_date}...", file=sys.stderr)
        result = process_date(
            target_date, repo_root,
            pause_seconds=args.pause_seconds,
            timeout=args.capture_timeout,
            dry_run=args.dry_run,
        )
        results.append(result)
        print(f"  Status: {result['status']}", file=sys.stderr)
        if result.get("run_id"):
            print(f"  Run ID: {result['run_id']}", file=sys.stderr)
        if result.get("bundle_verified"):
            print("  Bundle: VERIFIED", file=sys.stderr)
        if result.get("error"):
            print(f"  Error: {result['error']}", file=sys.stderr)

    # Write batch receipt
    batch_receipt = {
        "batch_schema": "forward_shadow_batch_v1",
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "target_dates": targets,
        "results": results,
        "settlement_backlog": settlement_results,
        "summary": {
            "total": len(results),
            "completed": sum(1 for r in results if r["status"] == "COMPLETED"),
            "skipped_existing": sum(1 for r in results if r["status"] == "SKIPPED_EXISTING"),
            "failed": sum(1 for r in results if r["status"] == "FAILED"),
            "bundle_verified": sum(1 for r in results if r.get("bundle_verified")),
            "settlement_backlog_total": len(settlement_results),
            "settlement_backlog_settled": sum(
                1 for r in settlement_results if r["status"] == "SETTLED"
            ),
            "settlement_backlog_failed": sum(
                1 for r in settlement_results if r["status"] == "SETTLEMENT_FAILED"
            ),
        },
    }
    receipt_path = repo_root / "data" / "reports" / "shadow" / "forward_batch_receipt.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(batch_receipt, indent=2, sort_keys=True))
    print(f"\nBatch receipt: {receipt_path}", file=sys.stderr)
    print(json.dumps(batch_receipt["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
