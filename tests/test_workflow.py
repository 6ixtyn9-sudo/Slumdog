"""Lock the CI workflow contract: naming, cadence and job gating."""
from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "pipeline.yml"

DAILY_HISTORY_CRON = "0 3 * * 0,2-6"  # daily except Monday (weekly covers Mon)
WEEKLY_CENSUS_CRON = "0 2 * * 1"


def _workflow() -> tuple[dict, dict]:
    data = yaml.safe_load(WORKFLOW.read_text())
    on = data.get("on", data.get(True))
    return data, on


def test_workflow_exists_and_named_professionally():
    data, _ = _workflow()
    assert "Slumdog" in data["name"]
    assert "Pipeline" in data["name"]
    assert "Depth" in data["name"]


def test_run_name_is_controlled():
    data, _ = _workflow()
    assert data.get("run-name", "").startswith("Slumdog")
    assert "run_number" in data["run-name"]


def test_dispatch_takes_no_inputs():
    _, on = _workflow()
    assert on["workflow_dispatch"] == {}


def test_schedule_has_daily_history_and_weekly_census():
    _, on = _workflow()
    crons = [item["cron"] for item in on["schedule"]]
    assert DAILY_HISTORY_CRON in crons
    assert WEEKLY_CENSUS_CRON in crons


def _expand_dow(expr: str) -> set[int]:
    """Expand a cron day-of-week field like '0,2-6' into a set of weekday ints."""
    days: set[int] = set()
    for part in expr.split(","):
        if "-" in part:
            start, end = part.split("-", 1)
            days.update(range(int(start), int(end) + 1))
        elif part:
            days.add(int(part))
    return days


def test_daily_history_excludes_monday_to_avoid_clash_with_weekly():
    # Monday 02:00 UTC weekly runs history + census + aggregate; a Monday
    # 03:00 daily run would be redundant and queue behind it (same concurrency
    # group), so the daily cron must not include Monday.
    days = _expand_dow(DAILY_HISTORY_CRON.split()[-1])
    assert 1 not in days                      # Monday excluded
    assert {0, 2, 3, 4, 5, 6} <= days          # Sun + Tue..Sat present


def test_census_and_aggregate_gated_to_weekly_or_manual():
    data, _ = _workflow()
    for job in ("census", "aggregate"):
        gate = data["jobs"][job]["if"]
        assert "workflow_dispatch" in gate
        assert WEEKLY_CENSUS_CRON in gate
        assert DAILY_HISTORY_CRON not in gate


def test_history_job_runs_on_every_trigger():
    data, _ = _workflow()
    assert "if" not in data["jobs"]["history"]
