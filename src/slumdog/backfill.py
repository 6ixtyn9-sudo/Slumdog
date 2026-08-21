"""Bounded historical capture and settlement accrual."""
from __future__ import annotations

import time
from datetime import date, timedelta
from pathlib import Path

from .forebet import ForebetCollector
from .settlement import append_settled_from_capture


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
    start: str,
    end: str,
    root: Path | str = ".",
    workers: int = 4,
    delay_seconds: float = 60.0,
) -> Path:
    """Capture and settle each date; delay keeps public relay below 20 req/min."""
    root = Path(root)
    days = date_range(start, end)
    collector = ForebetCollector(root, workers=workers)
    history = root / "data" / "interim" / "settled_history.json"
    for index, day in enumerate(days):
        collector.capture_all(day)
        history = append_settled_from_capture(day, root)
        if index + 1 < len(days) and delay_seconds > 0:
            time.sleep(delay_seconds)
    return history
