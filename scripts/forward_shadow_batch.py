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
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


# Evidence trees under data/reports/. Never pooled: "shadow" is the frozen
# 24h pre-event contract, "shadow_event_day" is the separate per-event
# kickoff-lead track added 2026-09-26 so late-publishing sports can produce a
# rank-1 pick at all. Mirrors slumdog.shadow_settle's constants (kept as plain
# strings here so the module imports without the package installed).
STANDARD_SHADOW_SUBDIR = "shadow"
EVENT_DAY_SHADOW_SUBDIR = "shadow_event_day"
EVENT_DAY_CONFIG = "config/shadow_evaluator_event_day.json"


def _capture_timing_logger(phase: str, target_date: str):
    """A ``ForebetCollector.capture_selected(on_capture_timing=...)``
    callback that prints one stderr line per sport-date the moment it
    finishes (elapsed seconds, request count, outcome classification).

    Priority 1 (2026-09-28): Forward Shadow #33 (run 36426785929) was
    cancelled after 92 minutes of complete stderr silence following its
    last printed line — nobody could tell which stage, sport, or date the
    time went into, because nothing printed anything until a whole stage
    finished (or never printed at all if cancelled mid-stage). This closes
    that gap: each line lands in the job log the instant it happens, so a
    killed run still shows exactly how far it got and where the time went,
    even though the receipt file for the in-flight date never gets
    written. ``phase`` distinguishes which driver stage this is (the
    capture_timing dict itself only knows the sport, not the caller).
    """
    def _log(entry: dict) -> None:
        print(
            f"    [{phase}:{target_date}] {entry['sport']}: "
            f"{entry['outcome']} "
            f"(elapsed={entry['elapsed_seconds']}s "
            f"requests={entry['requests']})",
            file=sys.stderr,
        )
    return _log


def _annotation_escape(text: str) -> str:
    """Escape a value for a GitHub Actions workflow command.

    Ported from ``scripts/probe_kickoff_timezone.py`` — same tool, same
    escaping rules, no reason for two implementations to drift apart.
    """
    return (text.replace("%", "%25").replace("\r", "%0D")
                .replace("\n", "%0A").replace("::", "%3A%3A"))


#: Run 36426785929 (Forward Shadow #33) is unreadable to this day: its only
#: annotations are the two GitHub adds automatically on cancellation
#: ("The run was canceled by @owner"). Everything it actually did — which
#: date, which sport, how long, captured or refused — lived only in stdout
#: and the 30-day artifact, both of which need a repo ADMIN's own signed URL
#: to read (confirmed 2026-09-28: an ANONYMOUS request to
#: /actions/runs/<id>/logs on this public repo gets 403 "Must have admin
#: rights to Repository" — repo visibility never mattered, only who can
#: mint that specific URL). Annotations, by contrast, are anonymous-
#: readable forever (`/check-runs/<job_id>/annotations`, verified the same
#: day with no credential at all) — exactly what scripts/probe_kickoff_
#: timezone.py already relies on via its own ``emit_section``. This gives
#: the batch driver the same property: one ::notice per phase as it
#: finishes, so a run killed at the 15-minute (probe) or job-timeout
#: (batch) wall — or cancelled outright — still leaves a readable trail
#: nobody needs to ask for.
MAX_NOTICE_CHARS = 2600


def emit_notice(title: str, payload) -> None:
    """Print one GitHub Actions ``::notice`` the moment ``payload`` exists.

    A no-op outside Actions (``GITHUB_ACTIONS`` unset), so local runs and
    tests without that env var stay silent by default — set it to exercise
    this in a test. Truncates like the probe does: a single annotation
    silently truncates around a few thousand characters, and the useful
    part of a phase's result is usually the summary counts near the front,
    not whatever list happens to be longest.
    """
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    if not payload:
        return
    blob = json.dumps(payload, sort_keys=True, default=str)[:MAX_NOTICE_CHARS]
    print(f"::notice title=forward_shadow:{title}::{_annotation_escape(blob)}",
          flush=True)


def canary_gate(*, timeout: int = 45) -> dict:
    """One cheap, standalone sample of the canary (football's
    tz=0 JSON) — see ``slumdog.forebet.sample_canary``'s docstring for the
    full rationale. Wrapped here, rather than calling ``sample_canary``
    directly from ``main()``, purely so a test can monkeypatch
    ``forward_shadow_batch.canary_gate`` the same way it already
    monkeypatches ``process_date`` — no import-path knowledge required.

    Local import: this module is meant to import even before ``slumdog``
    is installed (see the module docstring's constants above); the network
    call itself only happens when this function actually runs.
    """
    from slumdog.forebet import sample_canary
    return sample_canary(timeout=timeout)


def run_offline_r1_backtest(repo_root: Path) -> dict:
    """Run and annotate the already-seeded historical backtest, never raising.

    Forward Shadow's workflow downloads the latest history artifacts before
    this driver starts.  The backtest reads only those local bytes, so source
    availability must not gate the provenance verdict.  The complete JSON/MD
    remain under ``data/reports`` for the run artifact; annotations carry only
    the decision-critical verdict/rates and corpus inventory.
    """
    try:
        from slumdog.backtest import r1_backtest

        report_path = r1_backtest(repo_root)
        analysis = json.loads(report_path.read_text())
        provenance = analysis.get("provenance_verdict") or {}
        compact_verdict = {}
        if isinstance(provenance, dict):
            for track, check in provenance.items():
                if not isinstance(check, dict):
                    continue
                compact_verdict[track] = {
                    "verdict": check.get("verdict"),
                    "matched_pair_count": check.get("matched_to_historical_ledger"),
                    "pre_event_picks_available": check.get("pre_event_picks_available"),
                    "differing_count": check.get("differing_count"),
                    "underdog_identity_flipped_count": check.get(
                        "underdog_identity_flipped_count"),
                    "max_absolute_probability_delta_seen": check.get(
                        "max_absolute_probability_delta_seen"),
                }

        # Emit the unresolved draw result before lower-value sections. GitHub
        # has previously hidden notices beyond its display cap; this question
        # must remain visible even when the later canary aborts the network run.

        def compact_calibration(block: dict | None) -> dict:
            block = block or {}
            return {
                "mean_predicted_probability": block.get(
                    "mean_predicted_probability"),
                "observed_hit_rate": block.get("hit_rate"),
                "observed_wins": block.get("successes"),
                "n": block.get("n"),
                "wilson_95_lo": block.get("wilson_95_lo"),
                "wilson_95_hi": block.get("wilson_95_hi"),
                "observed_minus_predicted": block.get(
                    "observed_minus_predicted"),
            }

        def compact_differential(block: dict | None) -> dict:
            block = block or {}
            return {
                "n": block.get("n"),
                "differential_surplus": block.get("differential_surplus"),
                "differential_surplus_95_lo": block.get(
                    "differential_surplus_95_lo"),
                "differential_surplus_95_hi": block.get(
                    "differential_surplus_95_hi"),
                "indicative_only_n_lt_500": block.get(
                    "indicative_only_n_lt_500"),
            }

        calibration_notices = {}
        sport_calibration_notices: list[tuple[str, dict]] = []
        draw_space_notices = {}
        edge_sport_notices: list[tuple[str, dict]] = []
        holdout_notices: list[tuple[str, dict]] = []
        headline_rates = {}
        for population, scored in (analysis.get("populations") or {}).items():
            calibration = scored.get("calibration") or {}
            overall = calibration.get("overall") or {}
            bands = calibration.get("by_underdog_probability_band") or {}
            calibration_notices[population] = {
                "interpretation": calibration.get("interpretation"),
                "overall": {
                    side: compact_calibration(block)
                    for side, block in overall.items()
                },
                "by_underdog_probability_band": {
                    label: {side: compact_calibration(block)
                            for side, block in pair.items()}
                    for label, pair in bands.items()
                    if label != "unknown" or any(
                        (block or {}).get("n") for block in pair.values())
                },
            }
            sports = sorted((calibration.get("by_sport") or {}).items())
            for chunk_index in range(0, len(sports), 4):
                chunk = sports[chunk_index:chunk_index + 4]
                sport_calibration_notices.append((
                    f"r1_backtest_calibration_sports_{chunk_index // 4 + 1}",
                    {population: {
                        sport: {side: compact_calibration(block)
                                for side, block in pair.items()}
                        for sport, pair in chunk
                    }},
                ))

            split = scored.get("draw_space_split") or {}
            two_way = split.get("two_way_sports") or {}
            draw_capable = split.get("draw_capable_sports") or {}
            if split:
                draw_space_notices[population] = {
                "coverage": scored.get("coverage"),
                "two_way_sports": {
                    "sports": two_way.get("sports"),
                    "pooled": compact_calibration(two_way.get("pooled")),
                },
                "draw_capable_sports": {
                    "sports": draw_capable.get("sports"),
                    "pooled": compact_differential(draw_capable.get("pooled")),
                },
            }
            two_way_sports = sorted((two_way.get("per_sport") or {}).items())
            for chunk_index in range(0, len(two_way_sports), 20):
                chunk = two_way_sports[chunk_index:chunk_index + 20]
                edge_sport_notices.append((
                    f"r1_backtest_two_way_sports_{chunk_index // 20 + 1}",
                    {population: {sport: compact_calibration(block)
                                  for sport, block in chunk}},
                ))
            draw_sports = sorted((draw_capable.get("per_sport") or {}).items())
            for chunk_index in range(0, len(draw_sports), 20):
                chunk = draw_sports[chunk_index:chunk_index + 20]
                edge_sport_notices.append((
                    f"r1_backtest_draw_capable_sports_{chunk_index // 20 + 1}",
                    {population: {sport: compact_differential(block)
                                  for sport, block in chunk}},
                ))

            holdout = scored.get("temporal_holdout") or {}
            holdout_sports = sorted((holdout.get("per_sport") or {}).items())
            for chunk_index in range(0, len(holdout_sports), 4):
                chunk = holdout_sports[chunk_index:chunk_index + 4]
                compact_periods = {}
                for sport, periods in chunk:
                    compact_periods[sport] = {
                        "outcome_space": periods.get("outcome_space"),
                        "development": (
                            compact_calibration(periods.get(
                                "development_through_cutoff"))
                            if periods.get("outcome_space") == "TWO_WAY"
                            else compact_differential(periods.get(
                                "development_through_cutoff"))),
                        "holdout": (
                            compact_calibration(periods.get("holdout_after_cutoff"))
                            if periods.get("outcome_space") == "TWO_WAY"
                            else compact_differential(periods.get(
                                "holdout_after_cutoff"))),
                        "indicative_only_holdout_n_lt_500": periods.get(
                            "indicative_only_holdout_n_lt_500"),
                    }
                holdout_notices.append((
                    f"r1_backtest_holdout_{chunk_index // 4 + 1}",
                    {"cutoff": holdout.get("cutoff"),
                     "multiplicity_warning": holdout.get("multiplicity_warning"),
                     population: compact_periods},
                ))

            baselines = scored.get("baselines_same_rows") or {}
            headline_rates[population] = {
                "note": scored.get("raw_hit_rate_note"),
                "our_r1_pick": baselines.get("our_r1_pick"),
                "always_favourite_same_rows": baselines.get(
                    "always_favourite_same_rows"),
                "forebet_pick_same_rows": baselines.get(
                    "forebet_pick_same_rows"),
            }
        # GitHub publishes at most ten ::notice commands from this step.
        # Keep the decision-critical sequence below bounded so the final
        # canary-abort notice remains the tenth rather than being dropped.
        outcome_map = analysis.get("three_outcome_calibration_map") or {}
        if outcome_map:
            pooled_draw = ((outcome_map.get("pooled") or {}).get("draw") or {})
            point_buckets = pooled_draw.get("buckets") or {}
            bootstrap = outcome_map.get("draw_surplus_cluster_bootstrap") or {}
            sensitivity = {}
            for scheme, scheme_result in (bootstrap.get("schemes") or {}).items():
                sensitivity[scheme] = {
                    label: (scheme_result.get("buckets") or {}).get(label)
                    for label in ("<0.20", "0.35+")
                }
            primary_low = (sensitivity.get("calendar_day_PRIMARY", {})
                           .get("<0.20") or {})
            emit_notice("r1_backtest_draw_cluster_sensitivity", {
                "point_estimates": {
                    label: {
                        "n": block.get("n"),
                        "mean_predicted_probability": block.get(
                            "mean_predicted_probability"),
                        "observed_hit_rate": block.get("hit_rate"),
                        "observed_minus_predicted": block.get(
                            "observed_minus_predicted"),
                    }
                    for label, block in point_buckets.items()
                },
                "bootstrap_replicates": bootstrap.get("replicates"),
                "bootstrap_seed": bootstrap.get("seed"),
                "primary_block": bootstrap.get("primary_block"),
                "primary_block_reason": bootstrap.get("primary_block_reason"),
                "sensitivity_for_fragile_low_and_structural_high_buckets": sensitivity,
                "low_draw_surplus_survives_primary_cluster_interval": (
                    primary_low.get("bootstrap_95_lo") is not None
                    and primary_low["bootstrap_95_lo"] > 0),
                "low_draw_surplus_survives_week_blocks": (
                    (sensitivity.get("iso_week", {}).get("<0.20") or {}).get(
                        "bootstrap_95_lo") is not None
                    and (sensitivity["iso_week"]["<0.20"]["bootstrap_95_lo"] > 0)
                ),
                "low_draw_surplus_survives_month_blocks": (
                    (sensitivity.get("calendar_month", {}).get("<0.20") or {}).get(
                        "bootstrap_95_lo") is not None
                    and (sensitivity["calendar_month"]["<0.20"][
                        "bootstrap_95_lo"] > 0)
                ),
                "decision_rule": (
                    "Month lower bound >0: robust/bankable calibration lead. "
                    "Week lower bound <=0: the lead is gone."
                ),
            })

        tail = analysis.get("low_draw_tail_analysis") or {}
        if tail:
            def compact_tail_period(period: dict) -> dict:
                bootstrap = period.get("cluster_bootstrap") or {}
                schemes = bootstrap.get("schemes") or {}
                return {
                    "population": period.get("population"),
                    "forecast_exclusion_audit": period.get("forecast_exclusion_audit"),
                    "parent_lt_0_20_n": period.get("parent_lt_0_20_n"),
                    "frozen_shape_verdict": period.get("frozen_shape_verdict"),
                    "buckets": {
                    label: {
                        **(period.get("buckets", {}).get(label) or {}),
                        "calendar_day_95": (
                            (schemes.get("calendar_day_PRIMARY", {}).get("buckets", {})
                             .get(label, {}).get("bootstrap_95_lo")),
                            (schemes.get("calendar_day_PRIMARY", {}).get("buckets", {})
                             .get(label, {}).get("bootstrap_95_hi")),
                        ),
                        "calendar_month_95": (
                            (schemes.get("calendar_month", {}).get("buckets", {})
                             .get(label, {}).get("bootstrap_95_lo")),
                            (schemes.get("calendar_month", {}).get("buckets", {})
                             .get(label, {}).get("bootstrap_95_hi")),
                        ),
                    }
                    for label in tail.get("bucket_contract", [])
                    },
                }

            emit_notice("r1_backtest_low_draw_tail_all_rows", {
                "predeclared_shape_interpretation": tail.get(
                    "predeclared_shape_interpretation"),
                "result": compact_tail_period(
                    (tail.get("pooled") or {}).get("all") or {}),
                "per_sport_in_full_report": True,
            })
            emit_notice("r1_backtest_low_draw_tail_development", {
                "predeclared_shape_interpretation": tail.get(
                    "predeclared_shape_interpretation"),
                "validated_shape_verdict": (tail.get("pooled") or {}).get(
                    "validated_shape_verdict"),
                "validation_rule_application": (tail.get("pooled") or {}).get(
                    "validation_rule_application"),
                "result": compact_tail_period(
                    (tail.get("pooled") or {}).get(
                        "development_through_cutoff") or {}),
                "per_sport_in_full_report": True,
            })
            emit_notice("r1_backtest_low_draw_tail_holdout", {
                "result": compact_tail_period(
                    (tail.get("pooled") or {}).get("holdout_after_cutoff") or {}),
                "per_sport_in_full_report": True,
            })

            def compact_sport_tail(period: dict) -> dict:
                compact = compact_tail_period(period)
                return {
                    "population": compact.get("population"),
                    "forecast_exclusion_audit": compact.get(
                        "forecast_exclusion_audit"),
                    "parent_lt_0_20_n": compact.get("parent_lt_0_20_n"),
                    "lt_0_05": (compact.get("buckets") or {}).get("<0.05"),
                }

            sport_tail = sorted((tail.get("per_sport") or {}).items())
            for chunk_index in range(0, len(sport_tail), 2):
                chunk = sport_tail[chunk_index:chunk_index + 2]
                emit_notice(
                    f"r1_backtest_low_draw_tail_sports_{chunk_index // 2 + 1}",
                    {
                        sport: {
                            "draw_outcome_semantics": (
                                tail.get("draw_outcome_semantics") or {}).get(sport),
                            "development": compact_sport_tail(
                                periods.get("development_through_cutoff") or {}),
                            "holdout": compact_sport_tail(
                                periods.get("holdout_after_cutoff") or {}),
                        }
                        for sport, periods in chunk
                    },
                )

            composition = tail.get("retained_lt_0_05_composition") or {}
            for period, title_suffix in (
                ("development_through_cutoff", "development"),
                ("holdout_after_cutoff", "holdout"),
            ):
                block = composition.get(period) or {}
                emit_notice(f"r1_backtest_low_draw_composition_{title_suffix}", {
                    "pooled_retained_lt_0_05_n": block.get(
                        "pooled_retained_lt_0_05_n"),
                    "sports": {
                        sport: {
                            "n": values.get("n"),
                            "share": values.get(
                                "share_of_pooled_retained_lt_0_05"),
                            "predicted": values.get(
                                "mean_predicted_probability"),
                            "observed": values.get("observed_hit_rate"),
                            "relative": values.get(
                                "relative_surplus_observed_divided_by_predicted"),
                            "month_95": (
                                values.get("calendar_month_95_lo"),
                                values.get("calendar_month_95_hi"),
                            ),
                            "rows_per_active_day": values.get(
                                "candidate_rows_per_active_day"),
                            "exclusions": (values.get(
                                "forecast_exclusion_audit") or {}).get("excluded"),
                        }
                        for sport, values in (block.get("sports") or {}).items()
                    },
                    "reconciliation": block.get("reconciliation"),
                })

        # Provenance remains in the receipt/full report. Reserve the finite
        # notice budget for the same-population tail decomposition and canary.
        signal = analysis.get("eligible_underdog_signal") or {}
        if signal:
            def compact_signal_period(period: dict) -> dict:
                def compact_leg(calibration_key: str, bootstrap_key: str) -> dict:
                    calibration = period.get(calibration_key) or {}
                    bucket = (((period.get(bootstrap_key) or {}).get("schemes") or {})
                              .get("calendar_day_PRIMARY", {}).get("buckets", {})
                              .get("all", {}))
                    return {
                        "n": calibration.get("n"),
                        "mean_predicted_probability": calibration.get(
                            "mean_predicted_probability"),
                        "observed_hit_rate": calibration.get("hit_rate"),
                        "observed_minus_predicted": calibration.get(
                            "observed_minus_predicted"),
                        "cluster_bootstrap_95_lo": bucket.get("bootstrap_95_lo"),
                        "cluster_bootstrap_95_hi": bucket.get("bootstrap_95_hi"),
                    }

                return {
                    "underdog": compact_leg(
                        "calibration", "calendar_day_cluster_bootstrap"),
                    "favourite_control": compact_leg(
                        "favourite_control",
                        "favourite_calendar_day_cluster_bootstrap"),
                    "underdog_minus_favourite": compact_leg(
                        "differential",
                        "differential_calendar_day_cluster_bootstrap"),
                    "candidate_frequency": period.get("candidate_frequency"),
                }

            # The negative-sport gate is retired: its correctly separated
            # development intervals include zero. Keep its forensic receipt in
            # JSON, but do not spend an annotation or imply an open variant.

            emit_notice("r1_backtest_eligible_signal_overall", {
                "scope": signal.get("scope"), "cutoff": signal.get("cutoff"),
                "primary_uncertainty_block": signal.get(
                    "primary_uncertainty_block"),
                "block_reason": signal.get("block_reason"),
                "multiplicity_warning": signal.get("multiplicity_warning"),
                "development": compact_signal_period(
                    (signal.get("overall") or {}).get(
                        "development_through_cutoff") or {}),
                "holdout": compact_signal_period(
                    (signal.get("overall") or {}).get("holdout_after_cutoff") or {}),
            })
            # Per-sport eligible-underdog tables remain in the full report.
            # Their leads are exhausted; reserve annotations for the validated
            # low-draw tail's sport concentration and semantic audit.

        inventory = analysis.get("corpus_inventory") or {}
        per_sport = {}
        for sport, info in (inventory.get("per_sport") or {}).items():
            if isinstance(info, dict) and info.get("available"):
                per_sport[sport] = {
                    "settled_row_count": info.get("settled_row_count"),
                    "date_range": info.get("date_range"),
                }
        history_files = sorted(
            p.name for p in (repo_root / "data" / "reports").glob("history_*")
            if p.is_file())
        inventory_notice = {
            "seeded_history_files_on_disk": len(history_files),
            "sports_with_a_ledger_in_this_checkout": inventory.get(
                "sports_with_a_ledger_in_this_checkout"),
            "sports_with_at_least_one_settled_row": inventory.get(
                "sports_with_at_least_one_settled_row"),
            "per_sport": per_sport,
        }
        # Inventory is retained in the receipt/full report. It was already
        # proven by the provenance-unlock run; reserving notice slot ten for
        # canary_abort prevents the safety finding from being silently dropped.
        return {
            "status": "COMPLETED",
            "json_path": str(report_path.relative_to(repo_root)),
            "markdown_path": str(report_path.with_suffix(".md").relative_to(repo_root)),
            "verdict": compact_verdict,
            "calibration": calibration_notices,
            "headline_rates": headline_rates,
            "inventory": inventory_notice,
        }
    except Exception as exc:  # offline analysis must never fail the batch
        failure = {
            "status": "FAILED",
            "error": f"{type(exc).__name__}: {exc}"[:500],
        }
        emit_notice("r1_backtest_verdict", failure)
        return failure


def _write_preflight_abort_receipt(repo_root: Path, targets: list[str],
                                   sample: dict, backtest: dict) -> Path:
    """Persist the fail-closed receipt for a source-blocked whole run.

    This path runs before settlement or any other capture-capable phase, so
    every phase array is necessarily empty.  Keep that explicit: absence here
    means "not attempted after a measured dual-path block", never "quiet day".
    """
    abort = {
        "aborted_before_phase": "settlement",
        "dates_completed": 0,
        "dates_skipped": targets,
        "phases_skipped": [
            "settlement", "completion", "delta_settlement", "refresh",
            "event_day_settlement", "event_day", "forward_pass",
        ],
        "canary": sample,
    }
    receipt = {
        "batch_schema": "forward_shadow_batch",
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "target_dates": targets,
        "results": [],
        "settlement_backlog": [],
        "settlement_completion": [],
        "delta_settlement": [],
        "refresh": [],
        "event_day": [],
        "event_day_settlement": [],
        "r1_backtest": backtest,
        "canary_gate": {"samples": [sample], "aborted": True, "abort": abort},
        "summary": {
            "total": 0, "completed": 0, "skipped_existing": 0,
            "failed": 0, "bundle_verified": 0,
            "settlement_backlog_total": 0, "settlement_backlog_settled": 0,
            "settlement_backlog_failed": 0, "settlement_completion_runs": 0,
            "settlement_completion_supplements": 0,
            "settlement_completion_resolved_successes": 0,
            "settlement_completion_resolved_failures": 0,
            "settlement_completion_terminal_unresolved": 0,
            "settlement_completion_failed": 0, "delta_settlement_graded": 0,
            "refresh_runs": 0, "refresh_deltas_written": 0,
            "refresh_new_events": 0, "refresh_failed": 0,
            "event_day_runs": 0, "event_day_r1_sports": 0,
            "event_day_selections": 0, "event_day_failed": 0,
            "event_day_settled": 0, "canary_samples": 1,
            "canary_aborted": True,
        },
    }
    path = (repo_root / "data" / "reports" / "shadow" /
            "forward_batch_receipt.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True))
    return path


def summarize_capture_timing(entries: list[dict] | None) -> dict:
    """Roll up one date's per-sport ``capture_timing`` into one small dict.

    A single target date can capture a dozen-plus sports; annotating each
    one for every one of the forward pass's several target dates would
    blow well past what a job's annotations can usefully carry. This is
    the "compact roll-up" — total requests and elapsed time (the two
    numbers Priority 1's cost regression is actually about) plus a count
    per outcome family, not a full per-sport breakdown. The per-sport
    detail still exists: it is what ``_capture_timing_logger`` already
    prints to stderr, one line per sport as it finishes.
    """
    entries = entries or []
    by_outcome: dict[str, int] = {}
    for entry in entries:
        outcome = str(entry.get("outcome", "UNKNOWN"))
        # Group by outcome FAMILY (the part before ":"), not the exact
        # string — "CAPTURED:relay_columns" and "CAPTURED:direct" are the
        # same answer to "did this sport-date produce something usable".
        family = outcome.split(":", 1)[0]
        by_outcome[family] = by_outcome.get(family, 0) + 1
    return {
        "sports": len(entries),
        "total_requests": sum(e.get("requests", 0) or 0 for e in entries),
        "total_elapsed_seconds": round(
            sum(e.get("elapsed_seconds", 0.0) or 0.0 for e in entries), 1),
        "by_outcome": by_outcome,
    }


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
    # --- EVENT_DAY track (2026-09-26) ----------------------------------
    ("data/reports/shadow_event_day", "shadow_selections.json"),
    ("data/reports/shadow_event_day", "manifest.json"),
    ("data/reports/shadow_event_day", "settlement.json"),
    ("data/reports/shadow_event_day", "settlement.json.sha256"),
    # --- capture receipts -------------------------------------------------
    ("data/reports", "capture_2026-09-26.json"),
    ("data/reports", "capture_refresh_2026-09-26_20260925T043308Z.json"),
    ("data/reports", "capture_event_day_2026-09-26_20260926T043000Z.json"),
    # --- settlement evidence receipts -------------------------------------
    ("data/settlement_evidence", "settlement_capture_receipt.json"),
    ("data/settlement_evidence",
     "settlement_capture_receipt_completion_20260914T000000Z.json"),
    # Per-track D+1 receipt: the event-day settlement writes its own file so
    # it cannot overwrite the frozen track's committed capture evidence.
    ("data/settlement_evidence",
     "settlement_capture_receipt_shadow_event_day.json"),
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
    ("data/reports/shadow_event_day", "shadow_selections.json"),
    ("data/reports/shadow_event_day", "manifest.json"),
    ("data/reports/shadow_event_day", "settlement.json"),
    ("data/reports/shadow_event_day", "settlement.json.sha256"),
    ("data/settlement_evidence",
     "settlement_capture_receipt_shadow_event_day.json"),
    # NOTE: capture_event_day_*.json is ALREADY covered — the persist step's
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

        # Same dead-parameter bug as run_capture (see its NOTE): forward
        # pause_seconds through so this stage is also paced and timed.
        # Deliberately does NOT pass circuit_breaker_columns — see
        # run_capture's docstring on why the breaker is opt-in per stage.
        collector = ForebetCollector(root=repo_root, timeout=timeout, workers=1)
        collector.capture_selected(
            target_date, force=True, receipt_name=receipt_name,
            pause_seconds=pause_seconds,
            on_capture_timing=_capture_timing_logger("refresh", target_date))

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
# Event-day stage (owner decision 2026-09-26): one rank-1 (R1) pick per
# sport on the event day itself.
#
# Why it exists: Forebet only publishes basketball / hockey / baseball /
# tennis / rugby / american-football boards about a day out — both the D+6
# forward capture and the T+1/T+2 refresh get "target date missing from HTML"
# for them — so under the date-anchored 24h contract those sports cannot
# produce a pick at all, and 2026-09-21..26 were football-only. This stage
# captures TODAY's board and evaluates it under the EVENT_DAY declaration,
# which proves pre-event status per event against the published kickoff
# (>= min_lead_minutes_before_kickoff) instead of against the date anchor.
#
# Separation is absolute: its own capture receipt name, its own declaration,
# its own artifact tree (data/reports/shadow_event_day/), track labels in
# every payload/manifest, and its own settlement pass. Nothing in this stage
# reads or writes the 24h-frozen tree, and the two hit rates are never added
# together.
# ---------------------------------------------------------------------------


def find_event_day_run(target_date: str, repo_root: Path) -> str | None:
    """Return the completed event-day run id for a date, or None."""
    day_dir = (repo_root / "data" / "reports"
               / EVENT_DAY_SHADOW_SUBDIR / target_date)
    if not day_dir.is_dir():
        return None
    for child in sorted(day_dir.iterdir()):
        if (child.is_dir() and child.name != "BLOCKED"
                and (child / "shadow_selections.json").exists()):
            return child.name
    return None


def summarise_event_day_run(run_dir: Path) -> dict:
    """Per-sport R1 summary of one completed event-day run.

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


def calibrate_event_day_clock(
    target_date: str,
    repo_root: Path,
    *,
    timeout: int = 45,
    pause_seconds: int = 62,
    stamp: str = "",
) -> tuple[Path | None, dict]:
    """Measure the renderer's clock for this capture, or explain why not.

    Football is the one sport this run sees through both channels: the
    tz=0 JSON, whose DATE_BAH is an instant, and the rendered board that
    every other sport also comes from. The gap between them is the
    renderer's offset for this run's egress. Returns the path to the
    written calibration (or ``None``) and a summary for the batch entry.

    Nothing here can make a run looser by accident: any failure returns
    ``None``, which leaves the evaluator exactly where it is without a
    calibration — football only.
    """
    from slumdog.capture_loader import load_capture_records
    from slumdog.forebet import ForebetCollector, board_url
    from slumdog.relay_columns import (
        event_id_from_url,
        fetch_column,
        match_url,
        scoped,
    )
    from slumdog.render_clock import calibrate_capture, instants_from_records
    from slumdog.sports import SPORTS

    stamp = stamp or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    reports_dir = repo_root / "data" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    receipt_name = f"capture_render_clock_{target_date}_{stamp}.json"
    summary: dict = {"status": "PENDING", "reason": "", "detail": "",
                     "offset_minutes": None, "samples": 0}

    def fetch_instants() -> dict[str, str]:
        collector = ForebetCollector(root=repo_root, timeout=timeout,
                                     workers=1)
        collector.capture_selected(target_date, ["football"],
                                   receipt_name=receipt_name,
                                   pause_seconds=0)
        loaded = load_capture_records(
            target_date=target_date,
            capture_receipt_path=reports_dir / receipt_name,
            repo_root=repo_root)
        return instants_from_records(loaded.records)

    def fetch_rendered() -> dict[str, str]:
        # One request, not a whole board: the calibration needs only the
        # cell that carries the match link and its rendered time. Every
        # other column would be a request spent on something the
        # measurement cannot use.
        url = board_url(SPORTS["football"], target_date)
        cells = fetch_column(url, scoped(".tnms"), timeout=timeout,
                             column="link", attempts=2, backoff=5.0)
        rendered: dict[str, str] = {}
        for cell in cells:
            link = match_url(cell)
            if link:
                rendered[f"football:{event_id_from_url(link)}"] = cell
        return rendered

    calibration = calibrate_capture(
        target_date=target_date,
        fetch_instants=fetch_instants,
        fetch_rendered=fetch_rendered,
        measured_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        source_run=stamp)
    if not calibration.proven:
        summary.update(status="REFUSED", reason=calibration.reason,
                       detail=calibration.detail[:300])
        return None, summary

    clock = calibration.clock
    path = reports_dir / f"render_clock_{target_date}_{stamp}.json"
    path.write_text(json.dumps(clock.as_dict(), indent=2, sort_keys=True))
    summary.update(status="MEASURED", offset_minutes=clock.offset_minutes,
                   samples=clock.samples, detail=str(path.name))
    return path, summary


def run_event_day_for_date(
    target_date: str,
    repo_root: Path,
    *,
    pause_seconds: int = 62,
    timeout: int = 45,
    dry_run: bool = False,
    base_date: dt.date | None = None,
) -> dict:
    """Capture + evaluate today's board on the EVENT_DAY track.

    Isolated exactly like the settlement and refresh stages: every failure
    lands in the returned dict and is never raised to the batch driver, so a
    event-day failure can never cost the 24h-frozen forward pass.
    """
    from slumdog.forebet import ForebetCollector
    from slumdog.shadow_evaluator import UTC_KICKOFF_PROVEN_SPORTS
    from slumdog.sports import SPORTS

    entry: dict = {
        "target_date": target_date, "status": "PENDING", "run_id": None,
        "timezone_hold_sports": [],
        "track": "EVENT_DAY", "capture_receipt": None,
        "captured_sports": 0, "capture_failures": 0,
        "sports_with_r1": [], "r1_count": 0, "selection_count": 0,
        "timing_rejections": {}, "error": None,
        "render_clock": {"status": "NOT_ATTEMPTED"},
    }
    base_date = base_date or dt.datetime.now(dt.timezone.utc).date()
    reports_dir = repo_root / "data" / "reports"

    existing = find_event_day_run(target_date, repo_root)
    if existing is not None:
        # One event-day decision per date. Re-running would freeze a
        # second, later-lead decision for the same day and make the track's
        # hit rate ambiguous.
        entry["status"] = "ALREADY_RUN"
        entry["run_id"] = existing
        return entry
    if dry_run:
        entry["status"] = "DRY_RUN"
        return entry

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    receipt_name = f"capture_event_day_{target_date}_{stamp}.json"
    entry["capture_receipt"] = receipt_name
    try:
        collector = ForebetCollector(root=repo_root, timeout=timeout, workers=1)
        # Which boards this track may decide from is a question about
        # evidence, not about sports. A rendered kickoff is unusable while
        # its timezone is unknown — but football is visible through both the
        # tz=0 JSON and the renderer in the same run, so the offset can be
        # measured rather than assumed. With it, every sport's rendered
        # kickoff becomes an instant; without it, nothing changes and only
        # football is fetched, because every other request would be
        # guaranteed-rejected.
        clock_path, clock_summary = calibrate_event_day_clock(
            target_date, repo_root, timeout=timeout,
            pause_seconds=pause_seconds, stamp=stamp)
        entry["render_clock"] = clock_summary
        if clock_path is None:
            selected_sports = sorted(UTC_KICKOFF_PROVEN_SPORTS)
        else:
            selected_sports = sorted(SPORTS)
        entry["selected_sports"] = selected_sports
        entry["timezone_hold_sports"] = sorted(
            s for s in SPORTS if s not in selected_sports)
        collector.capture_selected(
            target_date, selected_sports,
            force=True, receipt_name=receipt_name,
            pause_seconds=pause_seconds,
            on_capture_timing=_capture_timing_logger("event_day", target_date))
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
            config_rel=EVENT_DAY_CONFIG,
            render_clock_path=clock_path,
        )
        entry["run_id"] = result.get("run_id")
        run_status = result.get("run_status")
        run_dir = Path(result.get("artifact_dir") or "")
        if run_status == "SHADOW_RUN_BLOCKED":
            entry["status"] = "BLOCKED"
            entry["error"] = f"evaluator run_status={run_status}"
            return entry
        entry.update(summarise_event_day_run(run_dir))
        try:
            manifest = json.loads((run_dir / "manifest.json").read_text())
            entry["timing_rejections"] = manifest.get(
                "event_day_timing_rejections", {})
        except (OSError, json.JSONDecodeError):
            pass
        entry["status"] = (
            "SELECTIONS_EMITTED" if run_status == "SHADOW_SELECTIONS_EMITTED"
            else "NO_SELECTION"
        )
    except Exception as exc:
        entry["status"] = "EVENT_DAY_FAILED"
        entry["error"] = f"{type(exc).__name__}: {exc}"
    return entry


def run_capture(target_date: str, repo_root: Path, *, pause_seconds: int = 62, timeout: int = 45) -> dict:
    """Capture Forebet listings for a target date.

    Uses the existing collector with workers=1 and 62s pauses.
    Returns the capture receipt dict.

    This is the D+2..D+6 forward-pass call site, and the ONLY one that
    opts into the column-route circuit breaker (Priority 1, scoped
    2026-09-28): most sport-dates this far out genuinely have no board
    yet, so a clean "not published" refusal on the first two probed
    columns is real signal here. run_refresh_for_date (T+1/T+2, near-term)
    and run_event_day_for_date (today) deliberately do NOT pass this —
    their boards usually already exist, and a false trip there would
    silently cost a real pick or a real grade instead of a wasted probe.

    Also the ONLY call site gated by the publication-horizon check
    (Priority 1, item v, 2026-09-28, ``slumdog.horizon``): before any
    request is issued, each sport is checked against its own observed
    forward-reachability ceiling (computed offline from every committed
    capture receipt — see ``horizon.compute_observed_horizons``) and
    dropped from this date's ``sports`` list if the offset has never once
    been confirmed reachable for it. This is deliberately narrower than a
    fixed "N days ahead" table: most sports here turn out to have been
    confirmed reachable at every offset this pass ever requests (their
    boards are calendar-driven, not offset-gated), so the gate mostly
    protects against sports with zero confirmed forward reachability at
    all (e.g. esports) and against any future widening of ``--dates``
    past what has actually been observed. Every decision — allowed and
    refused — is written into this date's receipt under
    ``horizon_gate`` so the refusal (and its evidence) is auditable from
    the committed artifact itself, not just this function's return value.
    """
    from slumdog.forebet import ForebetCollector
    from slumdog.horizon import compute_observed_horizons, filter_sports_by_horizon
    from slumdog.sports import SPORTS

    as_of_date = dt.datetime.now(dt.timezone.utc).date().isoformat()
    horizons = compute_observed_horizons(repo_root)
    allowed_sports, horizon_decisions = filter_sports_by_horizon(
        list(SPORTS), target_date, as_of_date, horizons)
    refused = [d for d in horizon_decisions if not d.allowed]

    # NOTE (found 2026-09-28, while wiring per-sport-date timing): this
    # function accepted `pause_seconds` and its docstring above claimed
    # "62s pauses", but nothing below ever forwarded it to
    # capture_selected() — so this call ran through capture_selected's
    # UNTIMED, UNPACED parallel branch (ThreadPoolExecutor, workers=1, so
    # serial in effect but with no inter-board pause and none of
    # capture_selected's `capture_timing` instrumentation, which only
    # exists on the pause_seconds>0 serial path). Fixed here: the forward
    # pass is exactly the stage the new instrumentation and the circuit
    # breaker need visible.
    collector = ForebetCollector(root=repo_root, timeout=timeout, workers=1,
                                 circuit_breaker_columns=2,
                                 circuit_breaker_attempts=1)
    captures = collector.capture_selected(
        target_date, sports=allowed_sports, pause_seconds=pause_seconds,
        on_capture_timing=_capture_timing_logger("forward", target_date))
    receipt_path = repo_root / "data" / "reports" / f"capture_{target_date}.json"
    if receipt_path.is_file():
        receipt = json.loads(receipt_path.read_text())
        receipt["horizon_gate"] = {
            "as_of_date": as_of_date,
            "method": (
                "slumdog.horizon.filter_sports_by_horizon, evaluated "
                "against every committed data/reports/capture_*.json "
                "receipt at call time (no hardcoded table)"),
            "refused": [d.to_dict() for d in refused],
            "allowed_count": len(allowed_sports),
            "refused_count": len(refused),
        }
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True))
        return receipt
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
    render_clock_path: Path | None = None,
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
    # declaration, or the EVENT_DAY declaration whose artifacts land in
    # the separate data/reports/shadow_event_day tree.
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
    if render_clock_path is not None:
        cmd += ["--render-clock", str(render_clock_path)]

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
        # The receipt already carries capture_timing (Priority 1, this
        # session) — roll it up here so a killed run's per-date annotation
        # (see main()'s forward-pass loop) shows the cost, not just the
        # counts.
        result["capture_timing_summary"] = summarize_capture_timing(
            capture_receipt.get("capture_timing"))
        # Recorded for every date (Priority 1, item iii correction,
        # 2026-09-28): whether football (the canary) was itself refused
        # this date's capture. A date whose canary is down means none of
        # this date's other COVERAGE_GAP sports can be read as "not
        # published" — see forebet.ForebetCollector.capture_selected's
        # _canary_state, which already applied that correction to the
        # receipt above before this function ever saw it.
        result["canary"] = capture_receipt.get("canary")

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
    parser.add_argument("--skip-event-day", action="store_true",
                        help="Skip the EVENT_DAY stage (same-day capture "
                             "+ per-event kickoff-lead evaluation into "
                             "data/reports/shadow_event_day/) and its own "
                             "D+1 settlement pass. The frozen 24h track is "
                             "unaffected either way.")
    parser.add_argument("--completion-window-days", type=int,
                        default=DEFAULT_COMPLETION_WINDOW_DAYS,
                        help=f"Maximum age (days) of a settled run the "
                             f"completion pass retries "
                             f"(default {DEFAULT_COMPLETION_WINDOW_DAYS})")
    args = parser.parse_args(argv)

    repo_root = args.root.resolve()
    targets = compute_target_dates(args.dates)

    # Whole-run pre-flight comes before settlement, completion, refresh,
    # EVENT_DAY and the forward pass.  Every one of those phases can reach
    # Forebet; gating only the final loop allowed a blocked run to spend hours
    # before it ever reached that gate (run 36521832033).
    preflight_sample: dict | None = None
    backtest_result: dict = {"status": "SKIPPED_DRY_RUN"}
    if not args.dry_run:
        preflight_sample = canary_gate(timeout=args.capture_timeout)
        # History ledgers were seeded by the workflow before this process
        # started. Run the pure-offline provenance test after the availability
        # decision but regardless of whether that decision aborts network work.
        backtest_result = run_offline_r1_backtest(repo_root)
        if preflight_sample.get("healthy") is False:
            abort = {
                "aborted_before_phase": "settlement",
                "dates_completed": 0,
                "dates_skipped": targets,
                "phases_skipped": [
                    "settlement", "completion", "delta_settlement", "refresh",
                    "event_day_settlement", "event_day", "forward_pass",
                ],
                "canary": preflight_sample,
            }
            emit_notice("canary_abort", abort)
            receipt_path = _write_preflight_abort_receipt(
                repo_root, targets, preflight_sample, backtest_result)
            print(
                "Forward Shadow ABORTED before every capture-capable phase: "
                f"{preflight_sample.get('reason')}", file=sys.stderr, flush=True)
            print(f"Batch receipt: {receipt_path}", file=sys.stderr, flush=True)
            return 0

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
        emit_notice("settlement", {
            "count": len(settlement_results),
            "settled": sum(1 for r in settlement_results
                          if r["status"] == "SETTLED"),
            "failed": sum(1 for r in settlement_results
                         if r["status"] == "SETTLEMENT_FAILED"),
            "dates": [r["target_date"] for r in settlement_results],
        })

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
        emit_notice("completion", {
            "count": len(completion_results),
            "supplements_written": sum(
                1 for r in completion_results
                if r["status"] == "SUPPLEMENT_WRITTEN"),
            "resolved_successes": sum(
                r.get("resolved_successes", 0) for r in completion_results),
            "resolved_failures": sum(
                r.get("resolved_failures", 0) for r in completion_results),
            "dates": [r["target_date"] for r in completion_results],
        })

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
        emit_notice("delta_settlement", {
            "count": len(delta_settlement_results),
            "graded": sum(r.get("deltas_graded", 0)
                         for r in delta_settlement_results),
            "dates": [r["target_date"] for r in delta_settlement_results],
        })

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
        emit_notice("refresh", {
            "count": len(refresh_results),
            "deltas_written": sum(1 for r in refresh_results
                                  if r["status"] == "DELTA_WRITTEN"),
            "new_events": sum(r.get("new_events", 0) for r in refresh_results),
            "dates": [r["target_date"] for r in refresh_results],
        })

    # Event-day track (owner decision 2026-09-26). Runs BEFORE the
    # forward pass: today's picks are the time-critical ones, and the forward
    # pass is the long stage. Both sub-stages are isolated — a failure here
    # can never stop the 24h-frozen pipeline below.
    event_day_settlement: list[dict] = []
    event_day_results: list[dict] = []
    if not args.skip_event_day:
        if not args.skip_settlement:
            event_day_settlement = run_settlement_backlog(
                repo_root,
                pause_seconds=args.pause_seconds,
                timeout=args.capture_timeout,
                dry_run=args.dry_run,
                shadow_subdir=EVENT_DAY_SHADOW_SUBDIR,
            )
            for sr in event_day_settlement:
                print(f"  event-day settle {sr['target_date']} "
                      f"({sr['run_id']}): {sr['status']}", file=sys.stderr)
                if sr.get("error"):
                    print(f"    Error: {sr['error']}", file=sys.stderr)
        today = dt.datetime.now(dt.timezone.utc).date().isoformat()
        entry = run_event_day_for_date(
            today, repo_root,
            pause_seconds=args.pause_seconds,
            timeout=args.capture_timeout,
            dry_run=args.dry_run,
        )
        event_day_results.append(entry)
        print(
            f"  event-day {entry['target_date']}: {entry['status']}"
            + (f" (R1 in {entry['r1_count']} sport(s): "
               f"{', '.join(entry['sports_with_r1'])})"
               if entry.get("r1_count") else "")
            + (f" [{entry['error']}]" if entry.get("error") else ""),
            file=sys.stderr,
        )
        emit_notice("event_day", {
            "settlement_count": len(event_day_settlement),
            "target_date": entry.get("target_date"),
            "status": entry.get("status"),
            "r1_count": entry.get("r1_count"),
            "sports_with_r1": entry.get("sports_with_r1"),
            "selection_count": entry.get("selection_count"),
            "error": entry.get("error"),
        })

    print(f"Forward shadow batch: {len(targets)} dates starting from {targets[0]}", file=sys.stderr)
    print(f"Repository root: {repo_root}", file=sys.stderr)

    # Canary-first abort (owner directive, 2026-09-28, after two consecutive
    # relay-path WAF blocks): "path availability, not request count, may be
    # the binding constraint." Sample before EVERY date this loop is about
    # to spend a capture budget on — the first sample covers "before the
    # forward pass" (nothing has been requested yet), each subsequent one
    # covers "periodically during it". A run 36426785929-style two hours
    # (every one of ~70 sport-dates grinding its full retry budget against
    # a wall) is now four minutes and an annotation instead. Skipped in
    # --dry-run: no capture budget is at risk there, and every existing
    # dry-run test predates this gate.
    # The whole-run preflight is receipt evidence too. Per-date samples below
    # remain as mid-run re-checks; they are not replaced by the initial gate.
    canary_samples: list[dict] = (
        [preflight_sample] if preflight_sample is not None else [])
    canary_abort: dict | None = None
    results = []
    for i, target_date in enumerate(targets):
        if not args.dry_run:
            sample = canary_gate(timeout=args.capture_timeout)
            canary_samples.append(sample)
            if sample.get("healthy") is False:
                canary_abort = {
                    "aborted_before_date": target_date,
                    "date_index": f"{i + 1}/{len(targets)}",
                    "dates_completed": len(results),
                    "dates_skipped": targets[i:],
                    "canary": sample,
                }
                print(
                    f"Forward pass ABANDONED on a canary path block before "
                    f"{target_date} ({i + 1}/{len(targets)}): "
                    f"{sample.get('reason')} — {len(results)} date(s) "
                    f"already completed are kept; {len(targets) - i} "
                    "date(s) not attempted.",
                    file=sys.stderr,
                )
                emit_notice("canary_abort", canary_abort)
                break
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
        # One notice per date, emitted the instant this date is done —
        # this is the forward pass's own stage (Forward Shadow #33 ran for
        # 1h56m and left zero trace of which date/sport it had reached
        # when it was killed). Do NOT move this after the loop: a run
        # killed on date 3 of 5 must still show dates 1-2 happened.
        emit_notice(f"forward_date:{target_date}", {
            "date_index": f"{i + 1}/{len(targets)}",
            "target_date": target_date,
            "status": result.get("status"),
            "run_id": result.get("run_id"),
            "bundle_verified": result.get("bundle_verified"),
            "capture": result.get("capture"),
            "capture_timing": result.get("capture_timing_summary"),
            "canary": result.get("canary"),
            "error": result.get("error"),
        })

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
        "event_day": event_day_results,
        "event_day_settlement": event_day_settlement,
        "r1_backtest": backtest_result,
        # Canary samples taken before every forward-pass date (see
        # canary_gate() above) plus, when the pass was abandoned on a
        # canary path block rather than completing/exhausting its target
        # dates normally, the abort record itself — the "was abandoned on
        # a canary path block" statement the owner asked every run to be
        # able to make, with sample times attached. NOTE (2026-09-28): the
        # canary tests the relay first and direct once when the relay is
        # unhealthy. A block means both paths available to THIS runner were
        # refused, not that the source endpoint itself was down; an ordinary
        # IP may still receive real JSON direct at the same moment.
        "canary_gate": {
            "samples": canary_samples,
            "aborted": canary_abort is not None,
            "abort": canary_abort,
        },
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
            # EVENT_DAY track counters. Deliberately named apart from the
            # frozen-track counters above: the two records are never summed.
            "event_day_runs": len(event_day_results),
            "event_day_r1_sports": sum(
                r.get("r1_count", 0) for r in event_day_results
            ),
            "event_day_selections": sum(
                r.get("selection_count", 0) for r in event_day_results
            ),
            "event_day_failed": sum(
                1 for r in event_day_results
                if r["status"] in ("EVENT_DAY_FAILED", "BLOCKED")
            ),
            "event_day_settled": sum(
                1 for r in event_day_settlement if r["status"] == "SETTLED"
            ),
            "canary_samples": len(canary_samples),
            "canary_aborted": canary_abort is not None,
        },
    }
    receipt_path = repo_root / "data" / "reports" / "shadow" / "forward_batch_receipt.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(batch_receipt, indent=2, sort_keys=True))
    print(f"\nBatch receipt: {receipt_path}", file=sys.stderr)
    print(json.dumps(batch_receipt["summary"], indent=2, sort_keys=True))
    # Last notice of the run — everything above already went out phase by
    # phase, so this is a convenience roll-up for a run that finished
    # cleanly, not the primary source of truth for one that didn't.
    emit_notice("summary", batch_receipt["summary"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
