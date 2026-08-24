#!/usr/bin/env python3
"""Audit MMA disposition, pricing, duplicates, and source provenance."""
from __future__ import annotations

import gzip
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


def priced(row: dict) -> bool:
    return row.get("odds_1") is not None and row.get("odds_2") is not None


def canonical(row: dict) -> bytes:
    return json.dumps(row, sort_keys=True, separators=(",", ":")).encode()


def cross_tab(rows: list[dict]) -> Counter:
    return Counter((str(row.get("disposition") or "SETTLED"), priced(row)) for row in rows)


def print_cross(label: str, rows: list[dict]) -> None:
    cross = cross_tab(rows)
    print(f"\n{label} (rows={len(rows)})")
    print(f"{'disposition':<14} {'priced':<8} {'count':>6}")
    for (disposition, has_price), count in sorted(cross.items()):
        print(f"{disposition:<14} {str(has_price):<8} {count:>6}")
    overlap = cross.get(("VOID", True), 0)
    void_total = sum(count for (disposition, _), count in cross.items() if disposition == "VOID")
    priced_total = sum(count for (_, has_price), count in cross.items() if has_price)
    print(f"void/priced overlap={overlap}; void total={void_total}; priced total={priced_total}")


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not Path(argv[1]).exists():
        print("usage: audit_mma_void_priced.py <history_mma.jsonl.gz>", file=sys.stderr)
        return 2

    with gzip.open(argv[1], "rt", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle]

    by_id: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_id[str(row.get("event_id") or "")].append(row)

    duplicate_ids = {event_id: copies for event_id, copies in by_id.items()
                     if event_id and len(copies) > 1}
    exact_ids = []
    conflicting_ids = []
    for event_id, copies in duplicate_ids.items():
        digests = {hashlib.sha256(canonical(row)).hexdigest() for row in copies}
        if len(digests) == 1:
            exact_ids.append(event_id)
        else:
            conflicting_ids.append(event_id)

    unique_rows = []
    seen = set()
    for row in rows:
        event_id = str(row.get("event_id") or "")
        if event_id and event_id in seen:
            continue
        if event_id:
            seen.add(event_id)
        unique_rows.append(row)

    any_hash = sum(bool(row.get("raw_sha256") or (row.get("facets") or {}).get("raw_sha256"))
                   for row in rows)
    captured = sum(bool(row.get("captured_at")) for row in rows)

    print(f"stored rows={len(rows)}")
    print(f"unique rows={len(unique_rows)}")
    print(f"duplicate event ids={len(duplicate_ids)}: {sorted(duplicate_ids)}")
    print(f"exact duplicate ids={len(exact_ids)}: {sorted(exact_ids)}")
    print(f"conflicting duplicate ids={len(conflicting_ids)}: {sorted(conflicting_ids)}")
    print(f"rows with raw_sha256={any_hash}/{len(rows)}")
    print(f"rows with captured_at={captured}/{len(rows)}")
    print_cross("STORED CROSS-TAB", rows)
    print_cross("DEDUPLICATED CROSS-TAB", unique_rows)
    return 1 if conflicting_ids else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
