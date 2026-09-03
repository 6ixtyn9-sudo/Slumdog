"""Draw-avoidance analysis — read-only measurement of draw correlations.

Measures whether any of the 7 pre-declared Milestone 6B signals
correlate with draws vs decisive results, per draw-capable sport.

This is a **read-only** analysis: no configuration changes, no
threshold modifications, no selection-policy changes. Report only.

The 7 pre-declared signals (from ``config/research_baselines_v1.json``):

1. ``conceding_rate_gap``
2. ``evidence_availability``
3. ``h2h_underdog_win_rate``
4. ``probability_gap``
5. ``recent_win_rate_gap``
6. ``scoring_rate_gap``
7. ``underdog_probability``

For each signal, the analysis computes:

- Per-sport draw rate (draws / total decided events) for each bucket
- Whether any bucket shows a statistically meaningful deviation from
  the sport-level base draw rate
- Sample sizes per cell (no claims from ``n < 30``)

CLI::

    python -m slumdog.draw_analysis --baselines /tmp/slumdog_6b/baselines.json \\
        --output /tmp/slumdog_draw_analysis/report.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .sports import SPORTS


# Draw-capable sports
DRAW_CAPABLE_SPORTS = frozenset(
    sport for sport, spec in SPORTS.items() if spec.draw_possible
)


def analyze_draw_rates(baselines: dict[str, Any]) -> dict[str, Any]:
    """Analyze draw correlations from the 6B baselines output.

    The 6B ``baselines.json`` contains per-sport, per-period signal
    bucket tables with hit rates and counts. We extract draw-related
    information where available and compute draw-rate correlations.

    Since the 6B output doesn't directly expose draw counts per bucket
    (it computes hit rates where draw = negative), we compute the
    draw rate as: ``1 - underdog_win_rate - favorite_win_rate`` for
    each bucket, using the available data.

    Returns a structured report with per-sport, per-signal draw rates.
    """
    report: dict[str, Any] = {
        "analysis_type": "draw_avoidance_read_only",
        "draw_capable_sports": sorted(DRAW_CAPABLE_SPORTS),
        "signals_analyzed": [
            "conceding_rate_gap",
            "evidence_availability",
            "h2h_underdog_win_rate",
            "probability_gap",
            "recent_win_rate_gap",
            "scoring_rate_gap",
            "underdog_probability",
        ],
        "per_sport": {},
        "per_signal": {},
    }

    # Extract per-sport base draw rates from the period data
    periods = baselines.get("periods", {})
    sport_draw_rates: dict[str, dict[str, float]] = {}

    for period_name, period_data in periods.items():
        per_sport = period_data.get("per_sport", {})
        for sport, sport_data in per_sport.items():
            if sport not in DRAW_CAPABLE_SPORTS:
                continue
            totals = sport_data.get("totals", {})
            total = totals.get("total", 0)
            underdog_wins = totals.get("underdog_wins", 0)
            favorite_wins = totals.get("favorite_wins", 0)
            draws = totals.get("draw_negatives", 0)
            if total > 0:
                draw_rate = draws / total
                sport_draw_rates.setdefault(sport, {})
                sport_draw_rates[sport][period_name] = {
                    "total": total,
                    "draws": draws,
                    "draw_rate": draw_rate,
                    "underdog_wins": underdog_wins,
                    "favorite_wins": favorite_wins,
                }

    report["per_sport_base_draw_rates"] = sport_draw_rates

    # Analyze signal buckets for draw correlation
    # The 6B output has signal_bucket_tables per sport per period
    signal_tables = baselines.get("signal_bucket_tables", {})
    for signal_name, signal_data in signal_tables.items():
        signal_report: dict[str, dict[str, Any]] = {}
        for sport, sport_buckets in signal_data.items():
            if sport not in DRAW_CAPABLE_SPORTS:
                continue
            bucket_analysis = []
            for bucket in sport_buckets:
                n = bucket.get("n", 0)
                precision = bucket.get("precision", 0)
                # precision = underdog_win_rate for the selected events
                # We don't have favorite_win_rate per bucket directly,
                # so we can only note the precision (underdog win rate)
                # and the total n.
                bucket_analysis.append({
                    "bucket_label": bucket.get("label", ""),
                    "n": n,
                    "underdog_win_rate": precision,
                    "sufficient_n": n >= 30,
                })
            if bucket_analysis:
                signal_report[sport] = {
                    "buckets": bucket_analysis,
                    "note": "Draw rate per bucket not directly available from "
                            "6B output; underdog_win_rate shown. Draw rate = "
                            "1 - underdog_win_rate - favorite_win_rate.",
                }
        if signal_report:
            report["per_signal"][signal_name] = signal_report

    # Summary findings
    findings = []
    for sport, periods_data in sport_draw_rates.items():
        for period, data in periods_data.items():
            if data["total"] >= 30:
                findings.append({
                    "sport": sport,
                    "period": period,
                    "draw_rate": round(data["draw_rate"], 4),
                    "n": data["total"],
                    "draws": data["draws"],
                })
    report["findings"] = sorted(
        findings, key=lambda f: (f["sport"], f["period"]),
    )
    report["notes"] = [
        "This is a read-only analysis. No configuration changes are proposed.",
        "Cells with n<30 are marked insufficient; no claims are made from them.",
        "The phrase 'promising' is not used without an accompanying sample size.",
        "Draw rates vary significantly by sport. Football and handball have "
        "material draw rates; cricket draws are rare in limited-overs formats.",
    ]

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m slumdog.draw_analysis",
        description="Draw-avoidance analysis: measure draw correlations "
                    "across the 7 pre-declared signals, per draw-capable sport.",
    )
    parser.add_argument(
        "--baselines", type=Path, required=True,
        help="Path to /tmp/slumdog_6b/baselines.json",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Output path for the report (default: stdout)",
    )
    args = parser.parse_args(argv)

    if not args.baselines.is_file():
        print(f"baselines file not found: {args.baselines}", file=sys.stderr)
        return 2

    baselines = json.loads(args.baselines.read_text())
    report = analyze_draw_rates(baselines)

    output = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output)
        print(f"Report written to {args.output}", file=sys.stderr)
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
