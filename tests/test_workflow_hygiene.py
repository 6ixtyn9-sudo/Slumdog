"""Guards against a repeat of the 2026-09-28 duplicate-workflow incident.

A staged owner-paste file was applied by *adding* it to
``.github/workflows/`` instead of *replacing* the file it was a copy of,
producing a second, complete "Slumdog · Forebet Depth Pipeline" workflow:
same ``name:``, same two ``cron:`` schedules, same concurrency group —
a full second 11-sport Depth Build queued behind the first on every
trigger. Any one of the four checks below would have caught it the moment
it landed. They guard the live ``.github/workflows/`` directory as checked
into this repo, not just the one file that caused the incident, so the
next workflow-shaped accident (whatever file it touches) fails a test
before it fails production.
"""
from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOWS_DIR = Path(".github/workflows")

#: The only files this repo's CI is allowed to declare. Adding a workflow
#: file requires a deliberate update to this allowlist, not a silent land.
KNOWN_WORKFLOW_FILES = {
    "forward_shadow.yml",
    "pipeline.yml",
    "probe_kickoff_timezone.yml",
}

#: Only these files may declare `on.schedule`. GitHub's own scheduler is
#: best-effort and silently skips runs (owner directive, 2026-09-28); this
#: repo's workflows are meant to be triggered externally instead, so a new
#: schedule anywhere is itself a signal something drifted from that rule.
SCHEDULE_ALLOWLIST = {"pipeline.yml"}


def _all_workflow_files() -> list[Path]:
    if not WORKFLOWS_DIR.is_dir():
        return []
    return sorted(p for p in WORKFLOWS_DIR.glob("*.yml") if p.is_file())


def _parsed(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


class TestTheWorkflowFileSetIsKnown:
    def test_the_directory_holds_exactly_the_known_files(self):
        found = {p.name for p in _all_workflow_files()}
        assert found == KNOWN_WORKFLOW_FILES, (
            f"unexpected workflow file set: {found} != {KNOWN_WORKFLOW_FILES} "
            "-- a new or duplicate .yml landed in .github/workflows/ without "
            "updating this allowlist"
        )


class TestNoTwoWorkflowsShareAName:
    def test_every_workflow_name_is_unique(self):
        names: dict[str, str] = {}
        for path in _all_workflow_files():
            name = _parsed(path).get("name")
            assert name, f"{path} has no top-level name:"
            assert name not in names, (
                f"{path.name} and {names.get(name)} both declare name={name!r} "
                "-- this is exactly the duplicate-workflow shape from the "
                "2026-09-28 incident"
            )
            names[name] = path.name


class TestNoTwoWorkflowsShareAConcurrencyGroup:
    def test_every_declared_concurrency_group_is_unique(self):
        groups: dict[str, str] = {}
        for path in _all_workflow_files():
            concurrency = _parsed(path).get("concurrency")
            if not concurrency:
                continue
            group = concurrency.get("group")
            if not group:
                continue
            assert group not in groups, (
                f"{path.name} and {groups.get(group)} share concurrency "
                f"group {group!r} -- one queues behind the other on every "
                "trigger, exactly the incident this test guards against"
            )
            groups[group] = path.name


class TestOnlyTheAllowlistedWorkflowsRunOnASchedule:
    def test_no_workflow_outside_the_allowlist_declares_on_schedule(self):
        for path in _all_workflow_files():
            parsed = _parsed(path)
            on = parsed.get("on", parsed.get(True)) or {}
            has_schedule = bool(isinstance(on, dict) and on.get("schedule"))
            if has_schedule:
                assert path.name in SCHEDULE_ALLOWLIST, (
                    f"{path.name} declares on.schedule but is not in "
                    f"SCHEDULE_ALLOWLIST={SCHEDULE_ALLOWLIST} -- GitHub's "
                    "scheduler is unreliable and this repo's workflows are "
                    "meant to be triggered externally instead (owner "
                    "directive, 2026-09-28); a new schedule anywhere else "
                    "needs a deliberate decision, not a silent add"
                )
