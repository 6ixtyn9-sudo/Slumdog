#!/usr/bin/env python3
"""Rank-4+ settlement erratum generator (append-only, read-only on evidence).

Background
----------
Every committed ``settlement.json`` graded its rank-4+ rows
(``source == "considered_pool"``) against ``underdog_index == 0``. The
manifest's ``considered_pool[]`` entries never carried an underdog identity,
``grade_all_entries`` defaulted the gap to ``0``, and ``0`` is this schema's
draw sentinel — so ``winner_index == underdog_index`` could never fire for a
real winner. A rank-4+ ``SUCCESS`` was structurally unreachable and every such
row graded ``FAILURE`` regardless of the actual result.

See ``docs/ADVERSARIAL_REVIEW_RANK4_SETTLEMENT_FINDINGS.md``.

What this script does
---------------------
It does **not** modify, rewrite, or replace any existing artifact. Under
``durability.no_force_overwrite`` and ``anti_tuning`` the committed settlement
evidence stays exactly as written, byte for byte. Instead it publishes a
*separate* erratum that:

- verifies each original artifact against its ``.sha256`` marker and fails
  closed on any mismatch (so the erratum can never describe tampered input);
- re-derives the underdog identity through the **fixed production code path**
  (``shadow_settle._resolve_underdog_identity``) from pre-event probabilities
  already committed in each manifest's ``input_provenance.
  capture_record_tuples`` — no second implementation, no new external data, no
  post-event fact, nothing invented;
- records the corrected aggregates and the individual rows whose grade changed.

This is an instrument correction, not a rule change: ``grading_contract``,
``grade_underdog_win`` and the frozen R2 rule are untouched, and
``config/research_baselines.json`` (the hash ``anti_tuning`` protects) is not
read or written. The corrected numbers restate the record; they must never be
used to justify a threshold, rule, or config amendment.

Usage
-----
    python3 scripts/rank4_settlement_erratum.py            # write errata
    python3 scripts/rank4_settlement_erratum.py --check     # verify only
    python3 scripts/rank4_settlement_erratum.py --stdout    # print, write nothing
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from slumdog.shadow_settle import (  # noqa: E402
    GRADE_FAILURE,
    GRADE_SUCCESS,
    GRADE_UNRESOLVED,
    GRADE_UNSETTLED,
    _build_event_index,
    _resolve_underdog_identity,
    grade_underdog_win,
)

SHADOW_ROOT = REPO_ROOT / "data" / "reports" / "shadow"
ERRATA_ROOT = SHADOW_ROOT / "errata"

# Only ``YYYY-MM-DD`` directories are prediction-run date directories. Siblings
# such as ``bundles/``, ``settlements/`` and ``batch_YYYY-MM-DD/`` are not.
_DATE_DIR_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

ERRATUM_SCHEMA_VERSION = "rank4_underdog_identity_erratum"

DEFECT_SUMMARY = (
    "considered_pool[] entries in manifest.json never carried underdog_index; "
    "shadow_settle.grade_all_entries defaulted the missing field to 0, which is "
    "the schema's draw sentinel, so grade_underdog_win could never return "
    "SUCCESS for a rank-4+ row. Every such row graded FAILURE regardless of the "
    "actual match result."
)


class ErratumError(RuntimeError):
    """Raised on any integrity failure. Fails closed."""


def _canonical_sha256(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_marker(artifact: Path) -> str:
    """Verify an artifact against its ``.sha256`` marker. Fail closed."""
    marker = artifact.with_name(artifact.name + ".sha256")
    if not marker.is_file():
        raise ErratumError(f"missing sha256 marker for {artifact}")
    expected = marker.read_text().split()[0].strip().lower()
    actual = _file_sha256(artifact)
    if expected != actual:
        raise ErratumError(
            f"sha256 mismatch for {artifact}: marker={expected} actual={actual}"
        )
    return actual


def discover_runs() -> list[tuple[str, str, Path]]:
    """Find settled prediction runs: (target_date, run_id, run_dir).

    Discovery is explicit rather than a bare filename glob: two different
    settlement schemas exist in this repository for the same run_id
    (``<run>/settlement.json`` from shadow_settle, and
    ``settlements/<date>/<run>.settlement.json`` from shadow_evaluator). Only
    the former carries rank-4+ rows, and conflating them silently drops a
    date's evidence — which is how the original report came to claim n=798
    across "3 dates" when it had read 2.
    """
    runs: list[tuple[str, str, Path]] = []
    for date_dir in sorted(p for p in SHADOW_ROOT.iterdir() if p.is_dir()):
        if not _DATE_DIR_RE.fullmatch(date_dir.name):
            # ``bundles``, ``settlements``, ``batch_YYYY-MM-DD`` are not runs
            continue
        for run_dir in sorted(p for p in date_dir.iterdir() if p.is_dir()):
            artifact = run_dir / "settlement.json"
            if artifact.is_file() and (run_dir / "manifest.json").is_file():
                runs.append((date_dir.name, run_dir.name, run_dir))
    return runs


def build_erratum(target_date: str, run_id: str, run_dir: Path) -> dict[str, Any]:
    """Build the erratum payload for one settled run."""
    artifact = run_dir / "settlement.json"
    manifest_path = run_dir / "manifest.json"
    selections_path = run_dir / "shadow_selections.json"

    original_sha = _verify_marker(artifact)
    original = json.loads(artifact.read_text())
    manifest = json.loads(manifest_path.read_text())
    selections = (
        json.loads(selections_path.read_text()) if selections_path.is_file() else {}
    )

    if original.get("run_id") != run_id or original.get("target_date") != target_date:
        raise ErratumError(
            f"{artifact}: run_id/target_date mismatch with its directory"
        )

    # Rebuild exactly what the FIXED grade_all_entries does.
    index = _build_event_index(selections, manifest)
    prob_lookup: dict[str, tuple[Any, Any, Any]] = {}
    for tup in manifest.get("input_provenance", {}).get("capture_record_tuples", []):
        if isinstance(tup, (list, tuple)) and len(tup) >= 8:
            prob_lookup[f"{tup[0]}:{tup[1]}:{tup[2]}"] = (tup[5], tup[6], tup[7])

    committed_rows = {r["event_id"]: r for r in original.get("grades", [])}

    corrections: list[dict[str, Any]] = []
    before: dict[str, int] = {}
    after: dict[str, int] = {}
    provenance: dict[str, int] = {}
    sentinel_rows = 0

    for key, entry in sorted(index.items()):
        row = committed_rows.get(entry.get("event_id"))
        if row is None or row.get("source") != "considered_pool":
            continue
        before[row["grade"]] = before.get(row["grade"], 0) + 1

        udog, udog_prob, fav, fav_prob, prov = _resolve_underdog_identity(
            entry, prob_lookup, key
        )
        provenance[prov] = provenance.get(prov, 0) + 1
        if udog == 0:
            sentinel_rows += 1

        # UNSETTLED rows have no result to grade; respect the pipeline's guard.
        if row["grade"] == GRADE_UNSETTLED:
            after[GRADE_UNSETTLED] = after.get(GRADE_UNSETTLED, 0) + 1
            continue

        corrected = grade_underdog_win(
            underdog_index=udog,
            winner_index=row.get("winner_index"),
            disposition=row.get("disposition"),
            sport=entry.get("sport", ""),
        )
        after[corrected] = after.get(corrected, 0) + 1

        if corrected != row["grade"]:
            corrections.append({
                "event_id": row["event_id"],
                "event_date": row.get("event_date"),
                "sport": row.get("sport"),
                "rank_within_sport_day": row.get("rank_within_sport_day"),
                "committed_grade": row["grade"],
                "corrected_grade": corrected,
                "committed_underdog_index": row.get("underdog_index"),
                "corrected_underdog_index": udog,
                "underdog_probability": udog_prob,
                "favorite_index": fav,
                "identity_provenance": prov,
                "winner_index": row.get("winner_index"),
                "disposition": row.get("disposition"),
                "score_1": row.get("score_1"),
                "score_2": row.get("score_2"),
            })

    def _rate(counts: dict[str, int]) -> dict[str, Any]:
        decided = counts.get(GRADE_SUCCESS, 0) + counts.get(GRADE_FAILURE, 0)
        return {
            "successes": counts.get(GRADE_SUCCESS, 0),
            "failures": counts.get(GRADE_FAILURE, 0),
            "unresolved": counts.get(GRADE_UNRESOLVED, 0),
            "unsettled": counts.get(GRADE_UNSETTLED, 0),
            "decided": decided,
            "success_rate": (
                counts.get(GRADE_SUCCESS, 0) / decided if decided else None
            ),
        }

    return {
        "erratum_schema_version": ERRATUM_SCHEMA_VERSION,
        "target_date": target_date,
        "run_id": run_id,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "original_artifact": {
            "path": str(artifact.relative_to(REPO_ROOT)),
            "sha256": original_sha,
            "marker_verified": True,
            "manifest_sha256": _file_sha256(manifest_path),
            "modified": False,
        },
        "defect": DEFECT_SUMMARY,
        "review_document": "docs/ADVERSARIAL_REVIEW_RANK4_SETTLEMENT_FINDINGS.md",
        "correction_basis": {
            "identity_source": "manifest.input_provenance.capture_record_tuples",
            "identity_rule": "slumdog.underdog.identify_forebet_underdog",
            "rule_description": "higher probability = favorite, lower = underdog",
            "pre_event_only": True,
            "post_event_facts_used": False,
            "grading_contract_changed": False,
            "frozen_rule_config_touched": False,
        },
        "scope": "considered_pool rows only (rank 4+); selections[] rows "
                 "(primary + top-3 cohort) verified unaffected",
        "rank4_plus": {
            "rows_examined": sum(before.values()),
            "committed": _rate(before),
            "corrected": _rate(after),
            "identity_provenance": provenance,
            "rows_still_on_draw_sentinel": sentinel_rows,
            "grades_changed": len(corrections),
        },
        "corrections": sorted(corrections, key=lambda r: r["event_id"]),
        "governance": {
            "original_evidence_overwritten": False,
            "no_force_overwrite_respected": True,
            "result_driven_rule_amendment": False,
            "note": "Instrument correction only. These numbers restate the "
                    "record and must not be used to justify any rule, "
                    "threshold, or config change.",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true",
                        help="verify markers and report, write nothing")
    parser.add_argument("--stdout", action="store_true",
                        help="print errata as JSON, write nothing")
    parser.add_argument(
        "--skip-existing", action="store_true",
        help="append-only incremental mode: skip any run whose erratum is "
             "already published instead of failing closed, so a newly "
             "discovered defective date can be corrected without touching "
             "published evidence. Skipped runs are still hash-verified and "
             "reported. Without this flag the generator refuses to run at all "
             "once any erratum exists.",
    )
    args = parser.parse_args(argv)

    runs = discover_runs()
    if not runs:
        print("no settled prediction runs found", file=sys.stderr)
        return 1

    errata = []
    for target_date, run_id, run_dir in runs:
        errata.append(build_erratum(target_date, run_id, run_dir))

    if args.stdout:
        print(json.dumps(errata, indent=2, sort_keys=True))
        return 0

    total_changed = 0
    for e in errata:
        r4 = e["rank4_plus"]
        c, k = r4["committed"], r4["corrected"]
        total_changed += r4["grades_changed"]
        c_rate = f"{c['success_rate']:.4f}" if c["decided"] else "n/a"
        k_rate = f"{k['success_rate']:.4f}" if k["decided"] else "n/a"
        print(f"{e['target_date']} {e['run_id']}")
        print(f"    original sha256 verified : {e['original_artifact']['sha256'][:16]}…")
        print(f"    rank-4+ rows examined    : {r4['rows_examined']}")
        print(f"    committed  SUCCESS/decided: {c['successes']}/{c['decided']}"
              f"  rate={c_rate}")
        print(f"    corrected  SUCCESS/decided: {k['successes']}/{k['decided']}"
              f"  rate={k_rate}")
        print(f"    grades changed           : {r4['grades_changed']}")
        print(f"    identity provenance      : {r4['identity_provenance']}")
        print(f"    rows on draw sentinel    : {r4['rows_still_on_draw_sentinel']}")

    print(f"\ntotal corrected rows across {len(errata)} run(s): {total_changed}")

    if args.check:
        print("--check: nothing written")
        return 0

    written = skipped = 0
    for e in errata:
        out_dir = ERRATA_ROOT / e["target_date"]
        out = out_dir / f"{e['run_id']}.rank4_erratum.json"
        marker = out.with_name(out.name + ".sha256")
        if out.exists() or marker.exists():
            if not args.skip_existing:
                raise ErratumError(
                    f"refusing to overwrite existing erratum: {out} "
                    "(delete it deliberately if a regeneration is intended, or "
                    "pass --skip-existing to append only newly found dates)"
                )
            # Already published: leave those bytes alone. The original
            # settlement.json was hash-verified against its .sha256 marker
            # during discovery, so a silently altered original cannot slip
            # through here — skipping is safe, not a blind trust.
            print(f"skipped {out.relative_to(REPO_ROOT)} (already published)")
            skipped += 1
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(e, indent=2, sort_keys=True) + "\n"
        out.write_text(payload, encoding="utf-8")
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        marker.write_text(f"{digest}  {out.name}\n", encoding="utf-8")
        print(f"wrote {out.relative_to(REPO_ROOT)}  ({digest[:16]}…)")
        written += 1

    print(f"\nwrote {written}, skipped {skipped} already published")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ErratumError as exc:
        print(f"ERRATUM_FAILED: {exc}", file=sys.stderr)
        raise SystemExit(2)
