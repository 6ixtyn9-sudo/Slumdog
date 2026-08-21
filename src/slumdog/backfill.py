"""Bounded historical capture and settlement accrual."""
from __future__ import annotations

import gzip
import json
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

from .clock import yesterday_iso
from .forebet import ForebetCollector
from .settlement import (
    append_settled_from_capture,
    parse_cricket_settled,
    parse_esoccer_settled,
    parse_football_settled,
    parse_html_settled,
    parse_mma_settled,
)
from .sports import HISTORY_STARTS, SPORTS


def date_range(start: str, end: str) -> list[str]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if last < first:
        raise ValueError("end before start")
    days = []
    current = first
    while current <= last:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def backfill(
    start: str | None = None,
    end: str | None = None,
    root: Path | str = ".",
    workers: int = 4,
    delay_seconds: float = 60.0,
) -> Path:
    """Bounded all-sport discovery probe.

    Defaults to the trailing seven days ending yesterday so a bare invocation
    is always a safe, clock-derived probe; explicit dates are overrides.
    """
    root = Path(root)
    end = end or yesterday_iso()
    start = start or (date.fromisoformat(end) - timedelta(days=6)).isoformat()
    days = date_range(start, end)
    collector = ForebetCollector(root, workers=workers)
    history = root / "data" / "interim" / "settled_history.json"
    for index, day in enumerate(days):
        collector.capture_all(day)
        history = append_settled_from_capture(day, root)
        if index + 1 < len(days) and delay_seconds > 0:
            time.sleep(delay_seconds)
    return history


def _parse_settled_body(sport: str, body: bytes, day: str):
    if sport == "football":
        return parse_football_settled(body, day)
    if sport == "mma":
        return parse_mma_settled(body, day)
    if sport == "cricket":
        return parse_cricket_settled(body, day)
    if sport == "esoccer":
        return parse_esoccer_settled(body, day)
    return parse_html_settled(body, sport, day)


def _load_manifest(report_dir: Path, sport: str) -> dict:
    path = report_dir / f"history_{sport}.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict) and isinstance(data.get("daily_receipts"), list):
            return data
    except Exception:
        return {}
    return {}


def backfill_sport(
    sport: str,
    end: str | None = None,
    root: Path | str = ".",
    start: str | None = None,
    workers: int = 6,
    batch_size: int = 18,
    delay_seconds: float = 62.0,
    keep_raw: bool = False,
) -> Path:
    """Accumulate one sport's dated archive into a rolling compressed ledger.

    The ledger (``history_<sport>.jsonl.gz``) and its manifest
    (``history_<sport>.json``) persist across runs: dates already captured are
    skipped, so a re-dispatch or a scheduled follow-up only fetches the days
    that are actually new. ``end`` defaults to yesterday from the runner clock.
    """
    if sport not in SPORTS or sport == "esoccer":
        raise ValueError("sport must have a dated Forebet archive")
    end = end or yesterday_iso()
    start = start or HISTORY_STARTS[sport]
    if start is None:
        raise ValueError(f"no historical start for {sport}")
    days = date_range(start, end)
    root = Path(root)
    collector = ForebetCollector(root, workers=workers)
    report_dir = root / "data" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    history_path = report_dir / f"history_{sport}.jsonl.gz"
    manifest_path = report_dir / f"history_{sport}.json"

    previous = _load_manifest(report_dir, sport)
    receipts = {str(item.get("date")) for item in previous.get("daily_receipts", [])}
    total_rows = int(previous.get("settled_rows") or 0)
    priced_rows = int(previous.get("priced_rows") or 0)
    void_rows = int(previous.get("void_rows") or 0)
    failures = list(previous.get("failures") or [])
    manifest = [item for item in previous.get("daily_receipts", []) if isinstance(item, dict)]
    done = {str(item.get("date")) for item in manifest}

    pending = [day for day in days if day not in done]
    safe_batch = max(1, min(int(batch_size), 18))

    if pending:
        with gzip.open(history_path, "at", encoding="utf-8") as output:
            for offset in range(0, len(pending), safe_batch):
                batch = pending[offset:offset + safe_batch]
                with ThreadPoolExecutor(max_workers=max(1, min(int(workers), 8))) as executor:
                    futures = {day: executor.submit(collector._fetch, sport, day) for day in batch}
                    for day in batch:
                        try:
                            capture = futures[day].result()
                            body_path = root / capture.body_path
                            rows = _parse_settled_body(sport, body_path.read_bytes(), day)
                            for row in rows:
                                output.write(json.dumps(asdict(row), sort_keys=True) + "\n")
                                total_rows += 1
                                priced_rows += row.odds_1 is not None and row.odds_2 is not None
                                void_rows += row.disposition == "VOID"
                            manifest.append({
                                "date": day, "source_url": capture.source_url,
                                "sha256": capture.sha256, "bytes": capture.bytes,
                                "settled_rows": len(rows),
                            })
                            if not keep_raw:
                                shutil.rmtree(body_path.parent, ignore_errors=True)
                        except Exception as exc:
                            failures.append(f"{day}:{type(exc).__name__}:{exc}")
                if offset + safe_batch < len(pending) and delay_seconds > 0:
                    time.sleep(delay_seconds)

    # The manifest reports the union of everything covered, not just this run.
    covered_dates = sorted(receipts | set(days))
    manifest_path.write_text(json.dumps({
        "sport": sport, "start": min(covered_dates), "end": max(covered_dates),
        "dates_requested": len(covered_dates), "dates_completed": len(manifest),
        "settled_rows": total_rows, "priced_rows": priced_rows,
        "void_rows": void_rows, "failures": failures,
        "history_file": str(history_path.relative_to(root)),
        "daily_receipts": manifest,
    }, indent=2, sort_keys=True))
    return manifest_path
