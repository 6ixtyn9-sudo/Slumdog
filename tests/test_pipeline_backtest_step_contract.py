"""Contract test for the staged 'backtest' job in pipeline.yml.

An agent token is refused when a push touches ``.github/workflows/`` (see
``docs/owner_paste/README.md``), so this new job is staged as an ordinary
file at ``docs/owner_paste/pipeline_backtest_step.yml`` -- a full copy of
the live ``pipeline.yml`` plus one additional ``backtest`` job -- for the
owner to paste over ``.github/workflows/pipeline.yml`` on ``main`` in the
GitHub web UI, the same pattern already used for ``forward_shadow.yml``
and ``probe_kickoff_timezone.yml``.

These tests pin the staged file's safety properties so an edit here cannot
silently widen permissions beyond the one new job, touch any existing job,
commit anything other than the backtest report, or let the new step fail
the job it runs in.

Once applied, this file's checks should migrate onto the live
``.github/workflows/pipeline.yml`` the same way
``test_probe_workflow_persist_contract.py`` did for the probe's own fix,
and the staged copy should be deleted -- git history is the record.
"""
from __future__ import annotations

from pathlib import Path

import yaml

STAGED = Path("docs/owner_paste/pipeline_backtest_step.yml")
LIVE = Path(".github/workflows/pipeline.yml")


def _staged_text() -> str:
    return STAGED.read_text()


def _live_text() -> str:
    return LIVE.read_text()


def _staged_parsed() -> dict:
    return yaml.safe_load(_staged_text())


def _live_parsed() -> dict:
    return yaml.safe_load(_live_text())


class TestTheStagedFileExistsAndParses:
    def test_the_file_is_present_and_parses_as_yaml(self):
        assert STAGED.exists()
        parsed = _staged_parsed()
        assert isinstance(parsed, dict)
        assert "backtest" in parsed["jobs"]


class TestTheDiffIsExactlyOneNewJob:
    def test_every_existing_job_is_byte_identical_to_the_live_file(self):
        staged = _staged_parsed()
        live = _live_parsed()
        for job_name, job_body in live["jobs"].items():
            assert staged["jobs"][job_name] == job_body, job_name

    def test_the_workflow_level_trigger_and_permissions_are_unchanged(self):
        staged = _staged_parsed()
        live = _live_parsed()
        assert staged.get("on", staged.get(True)) == live.get("on", live.get(True))
        assert staged["permissions"] == live["permissions"] == {"contents": "read"}

    def test_only_one_job_was_added(self):
        staged = _staged_parsed()
        live = _live_parsed()
        added = set(staged["jobs"]) - set(live["jobs"])
        assert added == {"backtest"}


class TestTheNewJobIsNarrowlyScoped:
    def test_write_permission_is_job_level_only_not_workflow_level(self):
        staged = _staged_parsed()
        job = staged["jobs"]["backtest"]
        assert job["permissions"] == {"contents": "write"}
        # The workflow-level default must still be read-only (checked above);
        # this asserts the elevation is declared on the job, not inherited.
        assert "permissions" in job

    def test_it_depends_on_history_and_runs_regardless_of_partial_failure(self):
        staged = _staged_parsed()
        job = staged["jobs"]["backtest"]
        assert job["needs"] == ["history"]
        assert job["if"] == "always()"

    def test_it_has_a_short_timeout(self):
        staged = _staged_parsed()
        job = staged["jobs"]["backtest"]
        assert job["timeout-minutes"] <= 30

    def test_it_makes_no_capture_and_no_new_network_fetch(self):
        text = _staged_text()
        backtest_block = text.split("backtest:", 1)[1].split("\n  aggregate:", 1)[0]
        assert "ForebetCollector" not in backtest_block
        assert "capture" not in backtest_block.lower()
        assert "depth-sweep" not in backtest_block

    def test_it_only_commits_the_backtest_report_paths(self):
        text = _staged_text()
        backtest_block = text.split("backtest:", 1)[1].split("\n  aggregate:", 1)[0]
        assert "git add -f data/reports/r1_backtest_*.json data/reports/r1_backtest_*.md" in backtest_block
        # Never a broad add -- this job must not become a second, wider
        # persist step for unrelated evidence.
        assert "git add -A" not in backtest_block
        assert "git add ." not in backtest_block

    def test_it_pushes_only_to_main_with_no_force_flag(self):
        text = _staged_text()
        backtest_block = text.split("backtest:", 1)[1].split("\n  aggregate:", 1)[0]
        push_lines = [line for line in backtest_block.splitlines() if "git push" in line]
        assert push_lines
        for line in push_lines:
            assert "origin HEAD:main" in line
            assert "--force" not in line
            assert "-f " not in line


class TestTheStepCannotFailTheJob:
    def test_the_run_step_invokes_the_documented_command(self):
        text = _staged_text()
        assert "python -m slumdog.cli r1-backtest --root ." in text

    def test_the_run_step_has_no_set_dash_e_and_ends_by_exiting_zero(self):
        text = _staged_text()
        backtest_block = text.split("backtest:", 1)[1].split("\n  aggregate:", 1)[0]
        run_step = backtest_block.split("Run R1 rule backtest", 1)[1].split("- name:", 1)[0]
        assert "set -e\n" not in run_step
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
        assert upload_steps
        assert upload_steps[-1].get("if") == "always()"


class TestItUploadsAndPersistsTheReport:
    def test_artifact_name_and_path(self):
        staged = _staged_parsed()
        steps = staged["jobs"]["backtest"]["steps"]
        upload = next(s for s in steps if isinstance(s.get("uses"), str)
                      and s["uses"].startswith("actions/upload-artifact"))
        assert upload["with"]["name"] == "slumdog-r1-backtest"
        assert upload["with"]["path"] == "data/reports/r1_backtest_*"
