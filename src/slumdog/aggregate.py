"""Aggregate parallel historical and current-depth job receipts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def aggregate_depth(input_root: Path | str, output: Path | str) -> Path:
    input_root, output = Path(input_root), Path(output)
    histories = []
    for path in input_root.rglob("history_*.json"):
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        if "sport" in data and "dates_requested" in data:
            histories.append(data)
    current = None
    for path in input_root.rglob("depth_sweep_*.json"):
        try:
            current = json.loads(path.read_text())
            break
        except Exception:
            continue

    lines = ["# Slumdog Full Forebet Depth Run", "", "## Historical backfill", "",
             "| Sport | Range | Dates completed/requested | Settled rows | Priced rows | Voids | Failures |",
             "|---|---|---:|---:|---:|---:|---:|"]
    for row in sorted(histories, key=lambda item: item["sport"]):
        lines.append(
            f"| {row['sport']} | {row['start']} → {row['end']} | "
            f"{row['dates_completed']}/{row['dates_requested']} | {row['settled_rows']} | "
            f"{row['priced_rows']} | {row['void_rows']} | {len(row.get('failures', []))} |"
        )
    lines.extend(["", "## Current all-detail census", "",
                  "| Sport | Events | Both prices | Details OK/requested | Enriched | Missing required |",
                  "|---|---:|---:|---:|---:|---:|"])
    if current:
        for sport, row in current.get("rows", {}).items():
            lines.append(
                f"| {sport} | {row['listing_events']} | {row['both_prices']} | "
                f"{row['details_succeeded']}/{row['details_requested']} | "
                f"{row['details_enriched']} | {row['missing_required_fields']} |"
            )
    else:
        lines.append("| current depth artifact missing |  |  |  |  |  |")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n")
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(aggregate_depth(args.input, args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
