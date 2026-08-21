"""Runtime clock for Slumdog runs.

Dates are derived from the runner's clock in a pinned timezone instead of
being typed at dispatch time. This mirrors Edge-Factory's pattern: the CLI
date arguments are optional overrides, "today" and "yesterday" always mean the
clock in the configured TZ (default Africa/Johannesburg, matching the operator).
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

DEFAULT_TZ = "Africa/Johannesburg"


def local_tz() -> ZoneInfo:
    name = os.environ.get("TZ") or DEFAULT_TZ
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def now() -> datetime:
    return datetime.now(local_tz())


def today() -> date:
    return now().date()


def today_iso() -> str:
    return today().isoformat()


def yesterday_iso() -> str:
    return (today() - timedelta(days=1)).isoformat()
