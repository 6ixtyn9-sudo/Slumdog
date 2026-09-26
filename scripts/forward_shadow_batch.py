#!/usr/bin/env python3
"""Forward shadow batch driver — rolling-date capture + evaluate + bundle.

Runs the forward shadow pipeline for the next N target dates, starting
from D+2 (the earliest date reachable under the frozen 24h pre-event
timing contract). For each date:

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

The one-shot D+1 settlement runs at 04:00 UTC, so evening
US-timezone fixtures are often not posted yet and freeze as
``UNSETTLED`` (and a handful of rows as ``UNRESOLVED``). After the
D+1 pass the driver therefore runs a bounded **completion pass**
(``find_completable_runs`` / ``run_completion_for_date``): for each
recently settled run it re-fetches post-event listings and closes
open rows with an append-only ``settlement_supplement_*.json``. The
original ``settlement.json`` is never modified, decided grades are
never recomputed, and retries stop after
``DEFAULT_COMPLETION_WINDOW_DAYS`` days. Same isolation guarantees as
D+1 settlement.

CLI::

    python scripts/forward_shadow_batch.py [--dates N] [--root ROOT]
        [--pause-seconds 62] [--capture-timeout 45] [--dry-run]
        [--skip-settlement] [--skip-refresh] [--refresh-days {1,2,3}]

A daily-refresh stage (near-term re-capture) re-snapshots the trailing
T+1..T+N dates when a completed run already exists: events the original
run never admitted (late-publishing leagues) are ranked and appended as
``selections_delta_<stamp>.json`` (+ ``.sha256`` and a manifest copy)
inside the original run dir, with the original decisions byte-frozen.
Those delta rows get their own one-shot ``settlement_delta_*.json`` grade
on their D+1 alongside the first-settlement backlog.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


# Evidence trees under data/reports/. Never pooled: "shadow" is the frozen
# 24h pre-event contract, "shadow_short_notice" is the separate per-event
# kickoff-lead track added 2026-09-26 so late-publishing sports can produce a
# rank-1 pick at all. Mirrors slumdog.shadow_settle's constants (kept as plain
# strings here so the module imports without the package installed).
STANDARD_SHADOW_SUBDIR = "shadow"
SHORT_NOTICE_SHADOW_SUBDIR = "shadow_short_notice"
SHORT_NOTICE_CONFIG = "config/shadow_evaluator_short_notice.json"

# Single source of truth for every SMALL evidence file this pipeline can
# write and that must survive the runner (the scoped git waiver in AGENTS.md:
# JSON/text evidence only — never raw capture bodies, never *.tar.gz, never
# history ledgers). ``scripts/check_workflow_evidence_globs.py`` compares this
# list against the workflow's persist step, so an artifact type added here
# without a matching workflow glob is reported instead of silently discarded
# when the job ends.
PERSISTED_EVIDENCE: tuple[tuple[str, str], ...] = (
    # --- frozen 24h track -------------------------------------------------
    ("data/reports/shadow", "shadow_selections.json"),
    ("data/reports/shadow", "manifest.json"),
    ("data/reports/shadow", "settlement.json"),
    ("data/reports/shadow", "settlement.json.sha256"),
    ("data/reports/shadow", "settlement_supplement_20260914T000000Z.json"),
    ("data/reports/shadow", "settlement_supplement_20260914T000000Z.json.sha256"),
    # daily-refresh deltas (written since 2026-09-22)
    ("data/reports/shadow", "selections_delta_20260922T043000Z.json"),
    ("data/reports/shadow", "selections_delta_20260922T043000Z.json.sha256"),
    ("data/reports/shadow", "selections_delta_20260922T043000Z.manifest.json"),
    ("data/reports/shadow", "settlement_delta_20260922T043000Z.json"),
    ("data/reports/shadow", "settlement_delta_20260922T043000Z.json.sha256"),
    # bundle receipts + markers (archives themselves are never committed)
    ("data/reports/shadow", "slumdog-shadow-2026-09-22-abcd.bundle.json"),
    ("data/reports/shadow", "slumdog-shadow-2026-09-22-abcd.tar.gz.sha256"),
    ("data/reports/shadow", "forward_batch_receipt.json"),
    # --- SHORT_NOTICE track (2026-09-26) ----------------------------------
    ("data/reports/shadow_short_notice", "shadow_selections.json"),
    ("data/reports/shadow_short_notice", "manifest.json"),
    ("data/reports/shadow_short_notice", "settlement.json"),
    ("data/reports/shadow_short_notice", "settlement.json.sha256"),
    # --- capture receipts -------------------------------------------------
    ("data/reports", "capture_2026-09-26.json"),
    ("data/reports", "capture_refresh_2026-09-26_20260925T043308Z.json"),
    ("data/reports", "capture_short_notice_2026-09-26_20260926T043000Z.json"),
    # --- settlement evidence receipts -------------------------------------
    ("data/settlement_evidence", "settlement_capture_receipt.json"),
    ("data/settlement_evidence",
     "settlement_capture_receipt_completion_20260914T000000Z.json"),
)

# Evidence types the CURRENT workflow does not commit yet, pending the
# owner-authored persist-step update (workflow files are owner-only; see
# AGENTS.md / docs/STATE.md). The contract test asserts the real gap is a
# SUBSET of this list: it stays green when the owner pastes the fix (the gap
# shrinks) and fails loudly if a NEW uncovered artifact type is introduced.
KNOWN_UNCOVERED_PENDING_OWNER_PASTE: frozenset[tuple[str, str]] = frozenset({
    ("data/reports/shadow", "selections_delta_20260922T043000Z.json"),
    ("data/reports/shadow", "selections_delta_20260922T043000Z.json.sha256"),
    ("data/reports/shadow", "selections_delta_20260922T043000Z.manifest.json"),
    ("data/reports/shadow", "settlement_delta_20260922T043000Z.json"),
    ("data/reports/shadow", "settlement_delta_20260922T043000Z.json.sha256"),
    ("data/reports/shadow", "forward_batch_receipt.json"),
    ("data/reports/shadow_short_notice", "shadow_selections.json"),
    ("data/reports/shadow_short_notice", "manifest.json"),
    ("data/reports/shadow_short_notice", "settlement.json"),
    ("data/reports/shadow_short_notice", "settlement.json.sha256"),
    # NOTE: capture_short_notice_*.json is ALREADY covered — the persist step's
    # `git add -f data/reports/capture_*.json` line matches it.
})


def compute_target_dates(n: int = 5, *, base: dt.date | None = None) -> list[str]:
    """Return the next N reachable target dates (D+2 through D+N+1).

    Under the frozen 24h pre-event timing contract, the earliest reachable target date is always D+2
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
# posture as the frozen 24h pre-event cutoff, applied to the
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


def find_settleable_run(
    target_date: str,
    repo_root: Path,
    *,
    shadow_subdir: str = STANDARD_SHADOW_SUBDIR,
) -> str | None:
    """Return the run_id of the one completed, unsettled run for a date.

    Returns ``None`` if the date has no completed run, or if its
    completed run already has a ``settlement.json``. Prediction runs
    are frozen and immutable once written (per the shadow evaluator's
    no-overwrite contract), so at most one completed run per date is
    expected in current operation; if more than one existed, the first
    completed, unsettled one found (sorted by run_id) is returned so
    behavior stays deterministic.
    """
    shadow_dir = repo_root / "data" / "reports" / shadow_subdir / target_date
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
    shadow_subdir: str = STANDARD_SHADOW_SUBDIR,
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
    shadow_root = repo_root / "data" / "reports" / shadow_subdir
    if not shadow_root.is_dir():
        return []
    out: list[tuple[str, str]] = []
    for child in sorted(shadow_root.iterdir()):
        if not child.is_dir() or not _is_target_date_dir(child.name):
            continue
        target_date = child.name
        if dt.date.fromisoformat(target_date) > cutoff:
            continue
        run_id = find_settleable_run(
            target_date, repo_root, shadow_subdir=shadow_subdir)
        if run_id is not None:
            out.append((target_date, run_id))
    return out


def _sports_in_run(
    target_date: str,
    run_id: str,
    repo_root: Path,
    *,
    shadow_subdir: str = STANDARD_SHADOW_SUBDIR,
) -> list[str]:
    """Return the distinct sports actually present in a prediction run.

    Reads both ``selections[]`` and the manifest's ``considered_pool[]``
    (settlement grades both), so the settlement capture fetches exactly
    the sports it needs — no more, no less. Falls back to an empty list
    (which callers should treat as "settle with the fetch-everything
    default") if the run's files cannot be parsed; this keeps a
    corrupted or unexpected run from silently skipping settlement.
    """
    run_dir = repo_root / "data" / "reports" / shadow_subdir / target_date / run_id
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
    shadow_subdir: str = STANDARD_SHADOW_SUBDIR,
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
    sports = _sports_in_run(
        target_date, run_id, repo_root, shadow_subdir=shadow_subdir)
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
            shadow_subdir=shadow_subdir,
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
    shadow_subdir: str = STANDARD_SHADOW_SUBDIR,
) -> list[dict]:
    """Settle every overdue (D+1) prediction run, oldest date first.

    Isolated per date: one date's failure is recorded and the loop
    continues to the next date. Never touches an already-settled run
    (idempotent — safe to call on every scheduled invocation).
    """
    pending = find_settleable_dates(
        repo_root, as_of=as_of, shadow_subdir=shadow_subdir)
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
            shadow_subdir=shadow_subdir,
        ))
    return results


# ---------------------------------------------------------------------------
# Settlement completion pass (owner-authorized 2026-09-14): re-grade rows
# left UNSETTLED/UNRESOLVED by the one-shot D+1 settlement, append-only.
# ---------------------------------------------------------------------------

DEFAULT_COMPLETION_WINDOW_DAYS = 14
# Statuses the completion instrument is allowed to revisit. Mirrors
# slumdog.shadow_settle.OPEN_GRADES — duplicated as data (not imported at
# module load time) so a broken install shows up as a test failure rather
# than as an import cycle; the two are pinned together in tests.
COMPLETION_OPEN_GRADES = frozenset({"UNSETTLED", "UNRESOLVED"})


def _completion_pending_count(run_dir: Path) -> int | None:
    """Open rows in a run's settlement after applying all supplements.

    Returns the pending count (0..n), or ``None`` when the run cannot safely
    be completed (no settlement.json, or an artifact/supplement that does
    not parse or hash-verify — the completion instrument itself then fails
    closed; the driver merely skips scanning it).
    """
    import hashlib

    settlement_path = run_dir / "settlement.json"
    if not settlement_path.is_file():
        return None

    def _marker_ok(json_path: Path) -> bool:
        marker = Path(str(json_path) + ".sha256")
        if not marker.is_file():
            return False
        token = marker.read_text().split()
        return bool(
            token
            and token[0].strip().lower()
            == hashlib.sha256(json_path.read_bytes()).hexdigest()
        )

    try:
        if not _marker_ok(settlement_path):
            return None
        settlement = json.loads(settlement_path.read_text())
        grades = settlement.get("grades") if isinstance(settlement, dict) else None
        if not isinstance(grades, list):
            return 0
        covered: set[str] = set()
        for supplement in sorted(run_dir.glob("settlement_supplement_*.json")):
            if not _marker_ok(supplement):
                return None
            payload = json.loads(supplement.read_text())
            for row in payload.get("rows", []):
                covered.add(
                    f"{row.get('sport')}:{row.get('event_id')}:{row.get('event_date')}"
                )
        pending = 0
        for row in grades:
            if not isinstance(row, dict):
                continue
            if row.get("grade") not in COMPLETION_OPEN_GRADES:
                continue
            key = f"{row.get('sport')}:{row.get('event_id')}:{row.get('event_date')}"
            if key not in covered:
                pending += 1
        return pending
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        return None


def find_completable_runs(
    repo_root: Path,
    *,
    as_of: dt.date | None = None,
    max_age_days: int = DEFAULT_COMPLETION_WINDOW_DAYS,
) -> list[tuple[str, str, int]]:
    """Return ``(target_date, run_id, pending_count)`` due for completion.

    A run qualifies when:
    - it is a completed run (``shadow_selections.json``) with a committed
      ``settlement.json``;
    - ``D+1 <= target_date age <= max_age_days`` (never same-day, and only
      within the bounded retry window);
    - at least one grade is still UNSETTLED/UNRESOLVED after applying
      every existing ``settlement_supplement_*.json``.

    Oldest date first. Never raises on malformed artifacts (skips them).
    """
    as_of = as_of or dt.datetime.now(dt.timezone.utc).date()
    shadow_root = repo_root / "data" / "reports" / "shadow"
    if not shadow_root.is_dir():
        return []
    out: list[tuple[str, str, int]] = []
    for date_dir in sorted(shadow_root.iterdir()):
        if not date_dir.is_dir() or not _is_target_date_dir(date_dir.name):
            continue
        target_date = date_dir.name
        try:
            target = dt.date.fromisoformat(target_date)
        except ValueError:
            continue
        age_days = (as_of - target).days
        if age_days < 1 or age_days > max_age_days:
            continue
        for run_dir in sorted(date_dir.iterdir()):
            if not run_dir.is_dir() or run_dir.name == "BLOCKED":
                continue
            if not (run_dir / "shadow_selections.json").is_file():
                continue
            if not (run_dir / "settlement.json").is_file():
                continue  # the D+1 pass owns the first settlement
            pending = _completion_pending_count(run_dir)
            if pending:
                out.append((target_date, run_dir.name, pending))
    return out


def run_completion_for_date(
    target_date: str,
    run_id: str,
    repo_root: Path,
    *,
    pause_seconds: int = 62,
    timeout: int = 45,
    max_age_days: int = DEFAULT_COMPLETION_WINDOW_DAYS,
) -> dict:
    """Run the completion pass for one settled run. Never raises.

    Mirrors :func:`run_settlement_for_date`'s contract: every failure mode
    (integrity, network, malformed artifact) is recorded in the returned
    dict, never propagated, so one bad date cannot block another or the
    forward pass.
    """
    from slumdog.shadow_settle import complete_settlement

    result: dict = {
        "target_date": target_date,
        "run_id": run_id,
        "status": "PENDING",
        "error": None,
    }
    try:
        completion = complete_settlement(
            target_date=target_date,
            run_id=run_id,
            repo_root=repo_root,
            pause_seconds=pause_seconds,
            timeout=timeout,
            max_age_days=max_age_days,
        )
        result["status"] = completion.status
        result["rows_in_supplement"] = completion.rows_in_supplement
        result["resolved_successes"] = completion.resolved_successes
        result["resolved_failures"] = completion.resolved_failures
        result["terminal_unresolved"] = completion.terminal_unresolved
        result["still_pending"] = completion.still_pending
        result["supplement_sha256"] = completion.supplement_sha256
        if completion.error:
            result["error"] = completion.error
    except Exception as exc:  # never let one date's failure abort the batch
        result["status"] = "COMPLETION_FAILED"
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def run_completion_backlog(
    repo_root: Path,
    *,
    as_of: dt.date | None = None,
    pause_seconds: int = 62,
    timeout: int = 45,
    max_age_days: int = DEFAULT_COMPLETION_WINDOW_DAYS,
    dry_run: bool = False,
    skip_dates: set[str] | None = None,
) -> list[dict]:
    """Complete every due run with open grades, oldest date first.

    Isolated per date and idempotent: runs with no newly-available result
    write no file, so this is safe on every scheduled invocation.

    ``skip_dates`` lists dates the D+1 first-settlement pass settled during
    THIS invocation: re-capturing them minutes later at the same 04:00 UTC
    hour cannot surface evening US results that were not posted moments ago,
    so their first completion attempt waits for the next daily dispatch
    (they are then age D+2).
    """
    skip_dates = skip_dates or set()
    due = [
        item for item in find_completable_runs(
            repo_root, as_of=as_of, max_age_days=max_age_days,
        )
        if item[0] not in skip_dates
    ]
    results = []
    for i, (target_date, run_id, pending_count) in enumerate(due):
        if dry_run:
            results.append({
                "target_date": target_date,
                "run_id": run_id,
                "status": "DRY_RUN",
                "pending_at_start": pending_count,
                # Uniform keys with a live result (nothing closes in dry-run,
                # so every pending row stays pending).
                "rows_in_supplement": 0,
                "resolved_successes": 0,
                "resolved_failures": 0,
                "terminal_unresolved": 0,
                "still_pending": pending_count,
                "supplement_sha256": None,
                "error": None,
            })
            continue
        if i > 0:
            time.sleep(pause_seconds)
        entry = run_completion_for_date(
            target_date, run_id, repo_root,
            pause_seconds=pause_seconds, timeout=timeout,
            max_age_days=max_age_days,
        )
        entry["pending_at_start"] = pending_count
        results.append(entry)
    return results


# ---------------------------------------------------------------------------
# Daily refresh (near-term re-capture) — owner directive 2026-09-22
#
# The one-shot forward capture snapshots each target date exactly once,
# several days before it enters the rolling window. Leagues that publish
# fixtures late (baseball, basketball, tennis, mma, esports — visible as
# "target date missing from HTML" failures in the capture receipts)
# therefore never enter any shadow run, and near-term boards end up
# football-heavy. The refresh re-snapshots a date that already has a
# completed run, evaluates ONLY events the original run never admitted
# (evaluator --exclude-events over the original considered ids), and
# appends the outcome as append-only delta artifacts INSIDE the original
# run dir: selections_delta_<stamp>.json (+ .sha256) plus a manifest copy.
# The original shadow_selections.json / manifest.json / bundle stay
# byte-frozen; delta rows get their own one-shot settlement delta on D+1
# via slumdog.shadow_settle --settle-deltas.
# ---------------------------------------------------------------------------


def find_refreshable_run(target_date: str, repo_root: Path) -> str | None:
    """Return the completed run id for a date, or None.

    Mirrors :func:`find_settleable_run`'s scan but only needs a completed
    run (settlement state is irrelevant to refreshing).
    """
    shadow_dir = repo_root / "data" / "reports" / "shadow" / target_date
    if not shadow_dir.is_dir():
        return None
    for child in sorted(shadow_dir.iterdir()):
        if child.is_dir() and child.name != "BLOCKED" \
                and (child / "shadow_selections.json").exists():
            return child.name
    return None


def _frozen_event_ids(run_dir: Path) -> frozenset[str]:
    """Event ids the original run admitted (selections + considered pool).

    Fail-closed: missing artifacts raise rather than silently excluding
    nothing (which would let the refresh re-decide frozen selections).
    """
    sel_path = run_dir / "shadow_selections.json"
    man_path = run_dir / "manifest.json"
    if not sel_path.is_file() or not man_path.is_file():
        raise RuntimeError(f"incomplete run artifacts in {run_dir}")
    ids: set[str] = set()
    for row in json.loads(sel_path.read_text()).get("selections", []):
        if row.get("event_id"):
            ids.add(row["event_id"])
    for row in json.loads(man_path.read_text()).get("considered_pool", []):
        if row.get("event_id"):
            ids.add(row["event_id"])
    return frozenset(ids)


def run_refresh_for_date(
    target_date: str,
    repo_root: Path,
    *,
    pause_seconds: int = 62,
    timeout: int = 45,
    dry_run: bool = False,
    base_date: dt.date | None = None,
) -> dict:
    """Refresh one target date. Isolated like the settlement stages: every
    failure lands in the returned dict, never raises to the batch driver."""
    from slumdog.forebet import ForebetCollector

    entry: dict = {"target_date": target_date, "status": "PENDING",
                   "run_id": None, "delta_stamp": None, "new_events": 0,
                   "excluded_frozen": 0, "error": None}
    base_date = base_date or dt.datetime.now(dt.timezone.utc).date()

    run_id = find_refreshable_run(target_date, repo_root)
    if run_id is None:
        entry["status"] = "NO_RUN"
        return entry
    entry["run_id"] = run_id

    # One refresh per (date, day): the refresh receipt is committed
    # evidence, so the guard persists across dispatches.
    reports_dir = repo_root / "data" / "reports"
    compact = base_date.strftime("%Y%m%d")
    if list(reports_dir.glob(f"capture_refresh_{target_date}_{compact}T*.json")):
        entry["status"] = "ALREADY_REFRESHED_TODAY"
        return entry

    if dry_run:
        entry["status"] = "DRY_RUN"
        return entry

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    receipt_name = f"capture_refresh_{target_date}_{stamp}.json"
    try:
        exclude_ids = _frozen_event_ids(
            repo_root / "data" / "reports" / "shadow" / target_date / run_id)
        entry["excluded_frozen"] = len(exclude_ids)

        collector = ForebetCollector(root=repo_root, timeout=timeout, workers=1)
        collector.capture_selected(
            target_date, force=True, receipt_name=receipt_name)

        exclude_path = Path(tempfile.mkstemp(
            prefix="refresh_exclude_", suffix=".json",
            dir=str(reports_dir))[1])
        try:
            exclude_path.write_text(json.dumps(sorted(exclude_ids)))
            result = run_evaluator(
                target_date, repo_root,
                receipt_name=receipt_name,
                exclude_events_path=exclude_path,
            )
        finally:
            exclude_path.unlink(missing_ok=True)

        entry["delta_stamp"] = stamp
        entry["refresh_exclusion_count"] = result.get("refresh_exclusion_count", 0)
        run_status = result.get("run_status")
        new_dir = Path(result.get("artifact_dir") or "")
        orig_dir = (repo_root / "data" / "reports" / "shadow"
                    / target_date / run_id)

        if run_status == "SHADOW_SELECTIONS_EMITTED":
            payload_bytes = (new_dir / "shadow_selections.json").read_bytes()
            manifest_bytes = (new_dir / "manifest.json").read_bytes()
            new_events = len(json.loads(payload_bytes).get("selections", []))
            delta_path = orig_dir / f"selections_delta_{stamp}.json"
            delta_man = orig_dir / f"selections_delta_{stamp}.manifest.json"
            for dest, data in ((delta_path, payload_bytes),
                               (delta_man, manifest_bytes)):
                fd, tmp = tempfile.mkstemp(
                    prefix=dest.name + ".", suffix=".tmp", dir=str(orig_dir))
                try:
                    with open(fd, "wb") as handle:
                        handle.write(data)
                    Path(tmp).replace(dest)
                except Exception:
                    Path(tmp).unlink(missing_ok=True)
                    raise
                digest = hashlib.sha256(dest.read_bytes()).hexdigest()
                Path(str(dest) + ".sha256").write_text(
                    f"{digest}  {dest.name}\n")
            shutil.rmtree(new_dir)
            entry["new_events"] = new_events
            entry["status"] = "DELTA_WRITTEN"
        elif run_status == "SHADOW_NO_SELECTION":
            shutil.rmtree(new_dir, ignore_errors=True)
            entry["status"] = "NO_NEW_EVENTS"
        else:
            if new_dir.is_dir():
                shutil.rmtree(new_dir, ignore_errors=True)
            entry["status"] = "REFRESH_FAILED"
            entry["error"] = f"evaluator run_status={run_status}"
    except Exception as exc:
        entry["status"] = "REFRESH_FAILED"
        entry["error"] = f"{type(exc).__name__}: {exc}"
    return entry


def run_refresh_backlog(
    repo_root: Path,
    *,
    refresh_days: int = 2,
    pause_seconds: int = 62,
    timeout: int = 45,
    dry_run: bool = False,
    base_date: dt.date | None = None,
) -> list:
    """Refresh the near-term dates T+1 .. T+refresh_days; per-date isolation."""
    base_date = base_date or dt.datetime.now(dt.timezone.utc).date()
    results = []
    for i in range(1, refresh_days + 1):
        target = base_date + dt.timedelta(days=i)
        if i > 1 and not dry_run:
            time.sleep(pause_seconds)
        results.append(run_refresh_for_date(
            target.isoformat(), repo_root,
            pause_seconds=pause_seconds, timeout=timeout,
            dry_run=dry_run, base_date=base_date,
        ))
    return results


# ---------------------------------------------------------------------------
# Short-notice stage (owner decision 2026-09-26): one rank-1 (R1) pick per
# sport on the event day itself.
#
# Why it exists: Forebet only publishes basketball / hockey / baseball /
# tennis / rugby / american-football boards about a day out — both the D+6
# forward capture and the T+1/T+2 refresh get "target date missing from HTML"
# for them — so under the date-anchored 24h contract those sports cannot
# produce a pick at all, and 2026-09-21..26 were football-only. This stage
# captures TODAY's board and evaluates it under the SHORT_NOTICE declaration,
# which proves pre-event status per event against the published kickoff
# (>= min_lead_minutes_before_kickoff) instead of against the date anchor.
#
# Separation is absolute: its own capture receipt name, its own declaration,
# its own artifact tree (data/reports/shadow_short_notice/), track labels in
# every payload/manifest, and its own settlement pass. Nothing in this stage
# reads or writes the 24h-frozen tree, and the two hit rates are never added
# together.
# ---------------------------------------------------------------------------


def find_short_notice_run(target_date: str, repo_root: Path) -> str | None:
    """Return the completed short-notice run id for a date, or None."""
    day_dir = (repo_root / "data" / "reports"
               / SHORT_NOTICE_SHADOW_SUBDIR / target_date)
    if not day_dir.is_dir():
        return None
    for child in sorted(day_dir.iterdir()):
        if (child.is_dir() and child.name != "BLOCKED"
                and (child / "shadow_selections.json").exists()):
            return child.name
    return None


def summarise_short_notice_run(run_dir: Path) -> dict:
    """Per-sport R1 summary of one completed short-notice run.

    Returns ``{"sports_with_r1": [...], "r1_count": n, "selection_count": n}``.
    Unreadable artifacts yield zeros rather than raising — the caller's
    contract is "never raises".
    """
    out = {"sports_with_r1": [], "r1_count": 0, "selection_count": 0}
    try:
        payload = json.loads((run_dir / "shadow_selections.json").read_text())
        selections = payload.get("selections", [])
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        return out
    sports = sorted({
        sel.get("sport") for sel in selections
        if isinstance(sel, dict) and sel.get("rank_within_sport_day") == 1
        and sel.get("sport")
    })
    out["sports_with_r1"] = sports
    out["r1_count"] = len(sports)
    out["selection_count"] = len(selections)
    return out


def run_short_notice_for_date(
    target_date: str,
    repo_root: Path,
    *,
    pause_seconds: int = 62,
    timeout: int = 45,
    dry_run: bool = False,
    base_date: dt.date | None = None,
) -> dict:
    """Capture + evaluate today's board on the SHORT_NOTICE track.

    Isolated exactly like the settlement and refresh stages: every failure
    lands in the returned dict and is never raised to the batch driver, so a
    short-notice failure can never cost the 24h-frozen forward pass.
    """
    from slumdog.forebet import ForebetCollector
    from slumdog.shadow_evaluator import UTC_KICKOFF_PROVEN_SPORTS
    from slumdog.sports import SPORTS

    entry: dict = {
        "target_date": target_date, "status": "PENDING", "run_id": None,
        "timezone_hold_sports": [],
        "track": "SHORT_NOTICE", "capture_receipt": None,
        "captured_sports": 0, "capture_failures": 0,
        "sports_with_r1": [], "r1_count": 0, "selection_count": 0,
        "timing_rejections": {}, "error": None,
    }
    base_date = base_date or dt.datetime.now(dt.timezone.utc).date()
    reports_dir = repo_root / "data" / "reports"

    existing = find_short_notice_run(target_date, repo_root)
    if existing is not None:
        # One short-notice decision per date. Re-running would freeze a
        # second, later-lead decision for the same day and make the track's
        # hit rate ambiguous.
        entry["status"] = "ALREADY_RUN"
        entry["run_id"] = existing
        return entry
    if dry_run:
        entry["status"] = "DRY_RUN"
        return entry

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    receipt_name = f"capture_short_notice_{target_date}_{stamp}.json"
    entry["capture_receipt"] = receipt_name
    try:
        collector = ForebetCollector(root=repo_root, timeout=timeout, workers=1)
        # Only fetch boards this track is allowed to decide from. Every other
        # sport's HTML listing renders kickoff in the relay's local timezone,
        # so the evaluator refuses it (KICKOFF_TIMEZONE_NOT_PROVEN_UTC) and
        # fetching it would be a guaranteed-rejected request. Paced the same
        # way as the settlement capture.
        entry["timezone_hold_sports"] = sorted(
            s for s in SPORTS if s not in UTC_KICKOFF_PROVEN_SPORTS)
        collector.capture_selected(
            target_date, sorted(UTC_KICKOFF_PROVEN_SPORTS),
            force=True, receipt_name=receipt_name,
            pause_seconds=pause_seconds)
        try:
            receipt = json.loads((reports_dir / receipt_name).read_text())
            entry["captured_sports"] = len(receipt.get("captured", []))
            entry["capture_failures"] = len(receipt.get("failures", []))
        except (OSError, json.JSONDecodeError):
            pass
        if entry["captured_sports"] == 0:
            entry["status"] = "NO_CAPTURES"
            return entry

        result = run_evaluator(
            target_date, repo_root,
            receipt_name=receipt_name,
            config_rel=SHORT_NOTICE_CONFIG,
        )
        entry["run_id"] = result.get("run_id")
        run_status = result.get("run_status")
        run_dir = Path(result.get("artifact_dir") or "")
        if run_status == "SHADOW_RUN_BLOCKED":
            entry["status"] = "BLOCKED"
            entry["error"] = f"evaluator run_status={run_status}"
            return entry
        entry.update(summarise_short_notice_run(run_dir))
        try:
            manifest = json.loads((run_dir / "manifest.json").read_text())
            entry["timing_rejections"] = manifest.get(
                "short_notice_timing_rejections", {})
        except (OSError, json.JSONDecodeError):
            pass
        entry["status"] = (
            "SELECTIONS_EMITTED" if run_status == "SHADOW_SELECTIONS_EMITTED"
            else "NO_SELECTION"
        )
    except Exception as exc:
        entry["status"] = "SHORT_NOTICE_FAILED"
        entry["error"] = f"{type(exc).__name__}: {exc}"
    return entry


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
    *,
    receipt_name: str | None = None,
    exclude_events_path: Path | None = None,
    config_rel: str = "config/shadow_evaluator.json",
) -> dict:
    """Run the shadow evaluator for a target date.

    ``receipt_name`` / ``exclude_events_path`` are used by the daily-refresh
    stage: the refresh evaluator consumes the refreshed capture receipt and
    drops previously-admitted events from admission.

    Returns the evaluator output dict.
    """
    receipt_path = repo_root / "data" / "reports" / (
        receipt_name or f"capture_{target_date}.json")
    # ``config_rel`` selects the timing track: the default frozen 24h
    # declaration, or the SHORT_NOTICE declaration whose artifacts land in
    # the separate data/reports/shadow_short_notice tree.
    config_path = repo_root / config_rel
    if not receipt_path.is_file():
        raise RuntimeError(f"capture receipt not found: {receipt_path}")
    if not config_path.is_file():
        raise RuntimeError(f"shadow evaluator config not found: {config_path}")

    # Find history files. The evaluator's load_valid_history() only
    # supports two on-disk shapes: gzipped JSONL ledgers
    # (history_<sport>.jsonl.gz) and the single JSON-list interim
    # ledger named exactly settled_history.json. It does NOT support
    # history_<sport>.json — that filename is the backfill *manifest*
    # (daily_receipts/settled_rows bookkeeping, not settled events),
    # written alongside the ledger by slumdog.backfill and seeded into
    # data/reports/ by the forward_shadow.yml "Seed history ledgers"
    # step. Passing a manifest file as --history previously raised
    # HistoryPathError ("unsupported history format") inside
    # load_valid_history, which evaluate_from_disk converts to a
    # SHADOW_RUN_BLOCKED / HISTORY_LOAD_FAILED failure receipt — a
    # deterministic, pre-existing bug that blocked every forward-batch
    # date once history seeding was in place (not caused by omitting
    # a real ledger; caused by including a real *manifest*).
    history_args = []
    reports_dir = repo_root / "data" / "reports"
    if reports_dir.is_dir():
        for hf in sorted(reports_dir.glob("history_*.jsonl.gz")):
            history_args.extend(["--history", str(hf)])
        interim_ledger = reports_dir / "settled_history.json"
        if interim_ledger.is_file():
            history_args.extend(["--history", str(interim_ledger)])

    cmd = [
        sys.executable, "-m", "slumdog.shadow_evaluator",
        "--date", target_date,
        "--capture-receipt", str(receipt_path),
        "--config", str(config_path),
        "--root", str(repo_root),
    ] + history_args
    if exclude_events_path is not None:
        cmd += ["--exclude-events", str(exclude_events_path)]

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
                        help="Skip BOTH settlement passes — the D+1 first "
                             "settlement backlog and the completion pass "
                             "that closes UNSETTLED/UNRESOLVED rows via "
                             "append-only supplements (forward capture only)")
    parser.add_argument("--skip-refresh", action="store_true",
                        help="Skip the daily-refresh stage (near-term "
                             "re-capture into append-only selections_delta_* "
                             "artifacts) — forward capture only")
    parser.add_argument("--refresh-days", type=int, default=2,
                        choices=[1, 2, 3],
                        help="Trailing T+1..T+N dates the daily-refresh stage "
                             "re-snapshots when a completed run exists "
                             "(default 2; deeper windows are progressively "
                             "little refresh — fixtures are largely fixed)")
    parser.add_argument("--skip-short-notice", action="store_true",
                        help="Skip the SHORT_NOTICE stage (same-day capture "
                             "+ per-event kickoff-lead evaluation into "
                             "data/reports/shadow_short_notice/) and its own "
                             "D+1 settlement pass. The frozen 24h track is "
                             "unaffected either way.")
    parser.add_argument("--completion-window-days", type=int,
                        default=DEFAULT_COMPLETION_WINDOW_DAYS,
                        help=f"Maximum age (days) of a settled run the "
                             f"completion pass retries "
                             f"(default {DEFAULT_COMPLETION_WINDOW_DAYS})")
    args = parser.parse_args(argv)

    repo_root = args.root.resolve()

    # Settlement pass first: grade any overdue (D+1) prediction runs
    # before capturing/ranking new ones. Isolated per date and fully
    # idempotent — safe on every invocation, including this one.
    settlement_results: list[dict] = []
    completion_results: list[dict] = []
    delta_settlement_results: list[dict] = []
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

        # Completion pass second: revisit recently settled runs whose
        # one-shot D+1 grade left rows UNSETTLED/UNRESOLVED (typically
        # evening US fixtures not posted by 04:00 UTC) and close what a
        # fresh capture now decides, append-only.
        just_settled = {
            sr["target_date"]
            for sr in settlement_results
            if sr["status"] == "SETTLED"
        }
        completion_results = run_completion_backlog(
            repo_root,
            pause_seconds=args.pause_seconds,
            timeout=args.capture_timeout,
            dry_run=args.dry_run,
            # In dry-run the D+1 entries are status DRY_RUN, so this set is
            # empty there; live, it holds the dates first-settled minutes ago.
            skip_dates=just_settled,
            max_age_days=args.completion_window_days,
        )
        if completion_results:
            print(f"Settlement completion: {len(completion_results)} run(s) with open rows", file=sys.stderr)
            for cr in completion_results:
                print(
                    f"  {cr['target_date']} ({cr['run_id']}): {cr['status']} "
                    f"(+{cr.get('resolved_successes', 0)}W "
                    f"+{cr.get('resolved_failures', 0)}L, "
                    f"{cr.get('terminal_unresolved', 0)} terminal, "
                    f"{cr.get('still_pending', 0)} still pending)",
                    file=sys.stderr,
                )
                if cr.get("error"):
                    print(f"    Error: {cr['error']}", file=sys.stderr)
        else:
            print("Settlement completion: nothing due", file=sys.stderr)

        # Delta settlement third: one-shot grade of the selections_delta_*
        # payloads the daily refresh appended to the runs settled above.
        # Idempotent per delta stamp; unmatched fresh deltas stay NOT_DUE
        # under their own guard and are picked up tomorrow.
        from slumdog.shadow_settle import settle_selection_deltas
        for sr in settlement_results:
            if sr.get("status") != "SETTLED":
                continue
            run_dir = (repo_root / "data" / "reports" / "shadow"
                       / sr["target_date"] / sr["run_id"])
            has_deltas = any(run_dir.glob("selections_delta_*.json"))
            if not has_deltas:
                continue
            if args.dry_run:
                delta_settlement_results.append({
                    "target_date": sr["target_date"], "run_id": sr["run_id"],
                    "status": "DRY_RUN"})
                continue
            statuses = settle_selection_deltas(
                target_date=sr["target_date"], run_id=sr["run_id"],
                repo_root=repo_root, pause_seconds=args.pause_seconds,
                timeout=args.capture_timeout)
            graded = sum(1 for s in statuses if s["status"] == "DELTA_SETTLED")
            delta_settlement_results.append({
                "target_date": sr["target_date"], "run_id": sr["run_id"],
                "status": "DELTAS_GRADED", "deltas_graded": graded,
                "statuses": statuses})
        for dr in delta_settlement_results:
            print(f"  delta settle {dr['target_date']}: {dr['status']}"
                  + (f" ({dr.get('deltas_graded', 0)} graded)"
                     if dr.get("deltas_graded") is not None and
                     dr["status"] == "DELTAS_GRADED" else ""),
                  file=sys.stderr)

    # Daily refresh (near-term re-capture, owner directive 2026-09-22):
    # re-snapshot the T+1..T+N dates that already hold a completed run so
    # late-publishing leagues still enter the shadow pipeline. New picks
    # land as append-only selections_delta_* artifacts inside the original
    # run dir; the original decisions stay byte-frozen.
    refresh_results: list[dict] = []
    if not args.skip_refresh:
        refresh_results = run_refresh_backlog(
            repo_root,
            refresh_days=args.refresh_days,
            pause_seconds=args.pause_seconds,
            timeout=args.capture_timeout,
            dry_run=args.dry_run,
        )
        for rr in refresh_results:
            print(
                f"  refresh {rr['target_date']}: {rr['status']}"
                + (f" (+{rr['new_events']} new events)"
                   if rr.get("new_events") else "")
                + (f" [{rr['error']}]" if rr.get("error") else ""),
                file=sys.stderr,
            )

    # Short-notice track (owner decision 2026-09-26). Runs BEFORE the
    # forward pass: today's picks are the time-critical ones, and the forward
    # pass is the long stage. Both sub-stages are isolated — a failure here
    # can never stop the 24h-frozen pipeline below.
    short_notice_settlement: list[dict] = []
    short_notice_results: list[dict] = []
    if not args.skip_short_notice:
        if not args.skip_settlement:
            short_notice_settlement = run_settlement_backlog(
                repo_root,
                pause_seconds=args.pause_seconds,
                timeout=args.capture_timeout,
                dry_run=args.dry_run,
                shadow_subdir=SHORT_NOTICE_SHADOW_SUBDIR,
            )
            for sr in short_notice_settlement:
                print(f"  short-notice settle {sr['target_date']} "
                      f"({sr['run_id']}): {sr['status']}", file=sys.stderr)
                if sr.get("error"):
                    print(f"    Error: {sr['error']}", file=sys.stderr)
        today = dt.datetime.now(dt.timezone.utc).date().isoformat()
        entry = run_short_notice_for_date(
            today, repo_root,
            pause_seconds=args.pause_seconds,
            timeout=args.capture_timeout,
            dry_run=args.dry_run,
        )
        short_notice_results.append(entry)
        print(
            f"  short-notice {entry['target_date']}: {entry['status']}"
            + (f" (R1 in {entry['r1_count']} sport(s): "
               f"{', '.join(entry['sports_with_r1'])})"
               if entry.get("r1_count") else "")
            + (f" [{entry['error']}]" if entry.get("error") else ""),
            file=sys.stderr,
        )

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
        "batch_schema": "forward_shadow_batch",
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "target_dates": targets,
        "results": results,
        "settlement_backlog": settlement_results,
        "settlement_completion": completion_results,
        "delta_settlement": delta_settlement_results,
        "refresh": refresh_results,
        "short_notice": short_notice_results,
        "short_notice_settlement": short_notice_settlement,
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
            "settlement_completion_runs": len(completion_results),
            "settlement_completion_supplements": sum(
                1 for r in completion_results if r["status"] == "SUPPLEMENT_WRITTEN"
            ),
            "settlement_completion_resolved_successes": sum(
                r.get("resolved_successes", 0) for r in completion_results
            ),
            "settlement_completion_resolved_failures": sum(
                r.get("resolved_failures", 0) for r in completion_results
            ),
            "settlement_completion_terminal_unresolved": sum(
                r.get("terminal_unresolved", 0) for r in completion_results
            ),
            "settlement_completion_failed": sum(
                1 for r in completion_results if r["status"] == "COMPLETION_FAILED"
            ),
            "delta_settlement_graded": sum(
                r.get("deltas_graded", 0) for r in delta_settlement_results
            ),
            "refresh_runs": len(refresh_results),
            "refresh_deltas_written": sum(
                1 for r in refresh_results if r["status"] == "DELTA_WRITTEN"
            ),
            "refresh_new_events": sum(
                r.get("new_events", 0) for r in refresh_results
            ),
            "refresh_failed": sum(
                1 for r in refresh_results if r["status"] == "REFRESH_FAILED"
            ),
            # SHORT_NOTICE track counters. Deliberately named apart from the
            # frozen-track counters above: the two records are never summed.
            "short_notice_runs": len(short_notice_results),
            "short_notice_r1_sports": sum(
                r.get("r1_count", 0) for r in short_notice_results
            ),
            "short_notice_selections": sum(
                r.get("selection_count", 0) for r in short_notice_results
            ),
            "short_notice_failed": sum(
                1 for r in short_notice_results
                if r["status"] in ("SHORT_NOTICE_FAILED", "BLOCKED")
            ),
            "short_notice_settled": sum(
                1 for r in short_notice_settlement if r["status"] == "SETTLED"
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
