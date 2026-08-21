"""One-shot, stratified all-sport Forebet depth audit."""
from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

from .detail_worker import capture_stratified_details, enrich_events_from_details
from .forebet import ForebetCollector
from .pipeline import parse_capture_receipt
from .sports import SPORTS


def run_depth_sweep(
    target_date: str,
    root: Path | str = ".",
    per_sport: int = 3,
    workers: int = 4,
    relay_pause: float = 62.0,
) -> Path:
    root = Path(root)
    ForebetCollector(root, workers=workers).capture_all(target_date)
    events_path = parse_capture_receipt(target_date, root)
    # Listing capture uses 12 relay requests. Start detail requests in a fresh
    # rate window rather than depending on retries.
    if relay_pause > 0:
        time.sleep(relay_pause)
    detail_report_path = capture_stratified_details(
        events_path, root, per_sport=per_sport, workers=workers,
        batch_size=18, delay_seconds=relay_pause,
    )
    detailed_path = enrich_events_from_details(events_path, root)

    capture = json.loads((root / "data" / "reports" / f"capture_{target_date}.json").read_text())
    parse = json.loads((root / "data" / "reports" / f"parse_{target_date}.json").read_text())
    detail = json.loads(detail_report_path.read_text())
    missingness = json.loads((root / "data" / "reports" / "detail_missingness_latest.json").read_text())
    events = json.loads(detailed_path.read_text())

    prices = Counter()
    totals = Counter()
    for event in events:
        sport = str(event.get("sport") or "")
        totals[sport] += 1
        prices[sport] += event.get("odds_1") is not None and event.get("odds_2") is not None
    requested = Counter(item.get("sport") for item in detail.get("results", []))
    succeeded = Counter(
        item.get("sport") for item in detail.get("results", []) if item.get("status") == "OK"
    )

    rows = {}
    for sport in SPORTS:
        coverage = (missingness.get("coverage") or {}).get(sport, {})
        total = totals[sport]
        rows[sport] = {
            "listing_events": total,
            "both_prices": prices[sport],
            "price_coverage": round(prices[sport] / total, 4) if total else None,
            "details_requested": requested[sport],
            "details_succeeded": succeeded[sport],
            "details_enriched": coverage.get("events", 0),
            "missing_required_fields": coverage.get("missing_fields", 0),
            "field_presence": coverage.get("field_presence", {}),
        }

    summary = {
        "target_date": target_date,
        "per_sport_detail_target": per_sport,
        "capture_failures": capture.get("failures", []),
        "parse_failures": parse.get("failures", []),
        "rows": rows,
    }
    report_dir = root / "data" / "reports"
    json_path = report_dir / f"depth_sweep_{target_date}.json"
    json_path.write_text(json.dumps(summary, indent=2, sort_keys=True))

    lines = [
        f"# Slumdog Forebet Depth Sweep — {target_date}", "",
        "| Sport | Listing events | Both prices | Price coverage | Details OK/requested | Enriched | Missing required |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for sport, row in rows.items():
        coverage = "n/a" if row["price_coverage"] is None else f"{row['price_coverage']:.1%}"
        lines.append(
            f"| {sport} | {row['listing_events']} | {row['both_prices']} | {coverage} | "
            f"{row['details_succeeded']}/{row['details_requested']} | "
            f"{row['details_enriched']} | {row['missing_required_fields']} |"
        )
    if summary["capture_failures"] or summary["parse_failures"]:
        lines.extend(["", "## Failures", "", "```json",
                      json.dumps({"capture": summary["capture_failures"],
                                  "parse": summary["parse_failures"]}, indent=2), "```"])
    md_path = report_dir / f"depth_sweep_{target_date}.md"
    md_path.write_text("\n".join(lines) + "\n")
    return md_path
