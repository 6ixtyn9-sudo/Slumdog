"""Contract test for the staged replacement of pipeline.yml.

**Incident this test exists to prevent recurring (2026-09-28):** an earlier
version of this staging was named ``docs/owner_paste/pipeline_backtest_step.yml``
-- a full copy of ``pipeline.yml`` plus one job -- and the owner applied it
by *adding* the file to ``.github/workflows/`` instead of *replacing*
``pipeline.yml``, because nothing about the name said "this replaces
pipeline.yml". The result was a second, complete "Slumdog · Forebet Depth
Pipeline" workflow: same name, same two cron schedules, same
``slumdog-depth-build`` concurrency group -- a full second 11-sport Depth
Build queued behind the first on every trigger, doubling relay load on the
exact relay whose IP reputation was already the day's open incident. The
owner deleted it (commit ``b7236fb`` on ``main``).

The fix has two parts, both enforced here: (1) this file is now named
**exactly** for the live file it replaces --
``docs/owner_paste/pipeline.yml``, matching ``.github/workflows/pipeline.yml``
-- so the only sane reading of "paste this" is "replace", never "add"; (2)
this test diffs the staged copy against ``.github/workflows/pipeline.yml``
on disk (this branch's own copy, verified against the live ``main`` file
before being staged) and pins the diff to be exactly the two new jobs
(``canary``, ``backtest``) plus one trailing comment -- nothing about any
existing job, the workflow name, the schedules, or the concurrency group
may ever change here again.

An agent token is refused when a push touches ``.github/workflows/``, so
this stays a plain file under ``docs/owner_paste/`` for the owner to paste
across in the GitHub web UI -- same mechanism as every other entry in
``docs/owner_paste/README.md``, which must say **REPLACE
.github/workflows/pipeline.yml**, never "add" or "create".
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

STAGED = Path("docs/owner_paste/pipeline.yml")
LIVE = Path(".github/workflows/pipeline.yml")
README = Path("docs/owner_paste/README.md")


def _staged_text() -> str:
    return STAGED.read_text()


def _live_text() -> str:
    return LIVE.read_text()


def _staged_parsed() -> dict:
    return yaml.safe_load(_staged_text())


def _live_parsed() -> dict:
    return yaml.safe_load(_live_text())


def _job_block(text: str, job_name: str) -> str:
    """The raw YAML text of one top-level job, from its header line up to
    (but not including) the next top-level job header or end of file. A
    top-level job header is a line indented by exactly two spaces, of the
    form ``  <name>:`` (the ``jobs:`` children)."""
    lines = text.splitlines(keepends=True)
    top_level = re.compile(r"^  [A-Za-z0-9_.-]+:\s*(#.*)?$")
    start_idx = None
    end_idx = len(lines)
    for i, line in enumerate(lines):
        if line == f"  {job_name}:\n" or line.rstrip() == f"  {job_name}:":
            start_idx = i
            continue
        if start_idx is not None and i > start_idx and top_level.match(line):
            end_idx = i
            break
    if start_idx is None:
        raise AssertionError(f"job {job_name!r} not found")
    return "".join(lines[start_idx:end_idx])



class TestTheFileIsNamedForWhatItReplaces:
    def test_the_staged_file_is_named_exactly_like_the_live_file(self):
        assert STAGED.name == LIVE.name == "pipeline.yml"

    def test_the_old_wrongly_named_staged_file_is_gone(self):
        assert not Path("docs/owner_paste/pipeline_backtest_step.yml").exists()

    def test_the_readme_says_replace_not_add(self):
        text = README.read_text()
        assert "REPLACE .github/workflows/pipeline.yml" in text
        # The exact wrong instruction from the incident (used verbatim for
        # a brand-new file in an earlier staging) must never appear at all
        # once this apply instruction exists.
        assert "create a new file at the same path" not in text


class TestItParsesAndMatchesTheLiveFileExactlyExceptTwoNewJobs:
    def test_both_parse_as_yaml(self):
        assert isinstance(_staged_parsed(), dict)
        assert isinstance(_live_parsed(), dict)

    def test_every_existing_job_is_byte_identical_to_the_live_file(self):
        staged = _staged_parsed()
        live = _live_parsed()
        for job_name, job_body in live["jobs"].items():
            assert staged["jobs"][job_name] == job_body, job_name

    def test_the_workflow_name_trigger_and_permissions_are_unchanged(self):
        staged = _staged_parsed()
        live = _live_parsed()
        assert staged["name"] == live["name"]
        assert staged.get("on", staged.get(True)) == live.get("on", live.get(True))
        assert staged["permissions"] == live["permissions"] == {"contents": "read"}
        assert staged["concurrency"] == live["concurrency"]

    def test_exactly_two_jobs_were_added(self):
        staged = _staged_parsed()
        live = _live_parsed()
        added = set(staged["jobs"]) - set(live["jobs"])
        assert added == {"canary", "backtest"}


class TestNoNewWorkflowFileNoNewSchedule:
    def test_the_staged_file_declares_no_additional_on_schedule(self):
        staged = _staged_parsed()
        live = _live_parsed()
        staged_on = staged.get("on", staged.get(True))
        live_on = live.get("on", live.get(True))
        assert staged_on.get("schedule") == live_on.get("schedule")

    def test_the_dead_cron_file_and_its_contract_are_gone(self):
        assert not Path("docs/owner_paste/probe_canary_cron.yml").exists()
        assert not Path("tests/test_probe_canary_cron_contract.py").exists()


class TestTheCanaryJobIsCheapAndNonBlocking:
    def test_it_has_no_needs_and_nothing_needs_it(self):
        staged = _staged_parsed()
        jobs = staged["jobs"]
        assert "needs" not in jobs["canary"]
        for name, job in jobs.items():
            if name == "canary":
                continue
            needs = job.get("needs")
            if needs is None:
                continue
            needed = [needs] if isinstance(needs, str) else needs
            assert "canary" not in needed, name

    def test_it_uses_canary_only_mode_and_nothing_heavier(self):
        text = _job_block(_staged_text(), "canary")
        assert "--canary-only" in text
        assert "--circuit-breaker-probe" not in text
        assert "--hunt" not in text
        assert "ForebetCollector" not in text
        assert "capture_selected" not in text

    def test_it_has_a_short_timeout(self):
        staged = _staged_parsed()
        assert staged["jobs"]["canary"]["timeout-minutes"] <= 10

    def test_it_declares_no_write_permission(self):
        staged = _staged_parsed()
        assert "permissions" not in staged["jobs"]["canary"]


class TestTheBacktestJobIsNarrowlyScoped:
    def test_write_permission_is_job_level_only(self):
        staged = _staged_parsed()
        job = staged["jobs"]["backtest"]
        assert job["permissions"] == {"contents": "write"}

    def test_it_depends_on_history_and_runs_regardless_of_partial_failure(self):
        staged = _staged_parsed()
        job = staged["jobs"]["backtest"]
        assert job["needs"] == ["history"]
        assert job["if"] == "always()"

    def test_it_has_a_bounded_timeout(self):
        staged = _staged_parsed()
        assert staged["jobs"]["backtest"]["timeout-minutes"] <= 30

    def test_it_makes_no_capture_and_no_new_network_fetch(self):
        text = _job_block(_staged_text(), "backtest")
        assert "ForebetCollector" not in text
        assert "depth-sweep" not in text

    def test_it_only_commits_the_backtest_report_paths(self):
        text = _job_block(_staged_text(), "backtest")
        assert "git add -f data/reports/r1_backtest_*.json data/reports/r1_backtest_*.md" in text
        assert "git add -A" not in text
        assert "git add ." not in text

    def test_it_pushes_only_to_main_with_no_force_flag(self):
        text = _job_block(_staged_text(), "backtest")
        push_lines = [line for line in text.splitlines() if "git push" in line]
        assert push_lines
        for line in push_lines:
            assert "origin HEAD:main" in line
            assert "--force" not in line
            assert "-f " not in line

    def test_the_run_step_invokes_the_documented_command(self):
        text = _job_block(_staged_text(), "backtest")
        assert "python -m slumdog.cli r1-backtest --root ." in text

    def test_the_run_step_cannot_fail_the_job(self):
        text = _job_block(_staged_text(), "backtest")
        run_step = text.split("Run R1 rule backtest", 1)[1].split("- name:", 1)[0]
        assert "set -euo pipefail" not in run_step
        assert "exit 0" in run_step

    def test_the_summary_and_upload_steps_run_even_if_earlier_steps_failed(self):
        staged = _staged_parsed()
        steps = staged["jobs"]["backtest"]["steps"]
        by_name = {s.get("name"): s for s in steps if "name" in s}
        assert by_name["Publish backtest summary"].get("if") == "always()"
        assert by_name["Persist small evidence to git (r1_backtest report only, a few KB)"].get("if") == "always()"
        upload_steps = [s for s in steps if isinstance(s.get("uses"), str)
                         and s["uses"].startswith("actions/upload-artifact")]
        assert upload_steps and upload_steps[-1].get("if") == "always()"

    def test_artifact_name_and_path(self):
        staged = _staged_parsed()
        steps = staged["jobs"]["backtest"]["steps"]
        upload = next(s for s in steps if isinstance(s.get("uses"), str)
                      and s["uses"].startswith("actions/upload-artifact"))
        assert upload["with"]["name"] == "slumdog-r1-backtest"
        assert upload["with"]["path"] == "data/reports/r1_backtest_*"
