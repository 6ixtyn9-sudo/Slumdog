"""Contract test for the live .github/workflows/pipeline.yml workflow.

``docs/workflow_staging/pipeline.yml`` (staged 2026-09-28, named
``docs/owner_paste/pipeline.yml`` at the time) added two new jobs to the
existing "Slumdog · Forebet Depth Pipeline" workflow: a non-blocking
``canary`` availability sampler and an R1-rule historical ``backtest`` job.
No new workflow file, no new schedule. The owner applied it directly to
``main`` (commit ``10ad139``, "Add canary and backtest jobs to pipeline") —
this time as a correct REPLACE of the existing file, not the earlier
mis-applied ADD that produced a duplicate workflow (see
``docs/workflow_staging/README.md``'s ``pipeline.yml`` entry for the
incident). This branch picked the change up via a merge of ``origin/main``
(merge commit ``f49de40``) rather than an authored diff — merging in an
already owner-committed workflow change is accepted by the restricted push
token even though authoring a fresh one to that path is not (same
precedent documented in ``tests/test_workflow_persist_contract.py`` and
``tests/test_probe_workflow_persist_contract.py``).

These tests now guard the live file directly. The staged copy and its
"not yet applied" naming/diff tests (``tests/test_owner_paste_pipeline_
contract.py``) are gone, migrated here — the same pattern
``test_workflow_persist_contract.py`` and
``test_probe_workflow_persist_contract.py`` already went through for
``forward_shadow.yml`` and ``probe_kickoff_timezone.yml``.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

LIVE = Path(".github/workflows/pipeline.yml")


def _live_text() -> str:
    return LIVE.read_text()


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


class TestTheWorkflowIdentityIsUnchanged:
    def test_it_parses(self):
        assert isinstance(_live_parsed(), dict)

    def test_name_trigger_and_permissions_are_the_known_baseline(self):
        live = _live_parsed()
        assert live["name"] == "Slumdog \u00b7 Forebet Depth Pipeline"
        on = live.get("on", live.get(True))
        assert "schedule" in on
        assert live["permissions"] == {"contents": "read"}
        assert live["concurrency"]["group"] == "slumdog-depth-build"

    def test_the_staged_copies_and_their_old_contract_test_are_gone(self):
        assert not Path("docs/workflow_staging/pipeline.yml").exists()
        assert not Path("docs/owner_paste/pipeline.yml").exists()
        assert not Path("tests/test_owner_paste_pipeline_contract.py").exists()

    def test_every_pre_existing_job_is_still_present(self):
        jobs = _live_parsed()["jobs"]
        for name in ("census", "history", "aggregate", "research"):
            assert name in jobs

    def test_exactly_the_two_expected_jobs_were_added_on_top_of_the_baseline(self):
        jobs = _live_parsed()["jobs"]
        assert set(jobs) == {
            "canary", "census", "history", "backtest", "aggregate", "research",
        }


class TestTheCanaryJobIsCheapAndNonBlocking:
    def test_it_has_no_needs_and_nothing_needs_it(self):
        jobs = _live_parsed()["jobs"]
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
        text = _job_block(_live_text(), "canary")
        assert "--canary-only" in text
        assert "--circuit-breaker-probe" not in text
        assert "--hunt" not in text
        assert "ForebetCollector" not in text
        assert "capture_selected" not in text

    def test_it_has_a_short_timeout(self):
        live = _live_parsed()
        assert live["jobs"]["canary"]["timeout-minutes"] <= 10

    def test_it_declares_no_write_permission(self):
        live = _live_parsed()
        assert "permissions" not in live["jobs"]["canary"]


class TestTheBacktestJobIsNarrowlyScoped:
    def test_write_permission_is_job_level_only(self):
        job = _live_parsed()["jobs"]["backtest"]
        assert job["permissions"] == {"contents": "write"}

    def test_it_depends_on_history_and_runs_regardless_of_partial_failure(self):
        job = _live_parsed()["jobs"]["backtest"]
        assert job["needs"] == ["history"]
        assert job["if"] == "always()"

    def test_it_has_a_bounded_timeout(self):
        assert _live_parsed()["jobs"]["backtest"]["timeout-minutes"] <= 30

    def test_it_makes_no_capture_and_no_new_network_fetch(self):
        text = _job_block(_live_text(), "backtest")
        assert "ForebetCollector" not in text
        assert "depth-sweep" not in text

    def test_it_only_commits_the_backtest_report_paths(self):
        text = _job_block(_live_text(), "backtest")
        assert "git add -f data/reports/r1_backtest_*.json data/reports/r1_backtest_*.md" in text
        assert "git add -A" not in text
        assert "git add ." not in text

    def test_it_pushes_only_to_main_with_no_force_flag(self):
        text = _job_block(_live_text(), "backtest")
        push_lines = [line for line in text.splitlines() if "git push" in line]
        assert push_lines
        for line in push_lines:
            assert "origin HEAD:main" in line
            assert "--force" not in line
            assert "-f " not in line

    def test_the_run_step_invokes_the_documented_command(self):
        text = _job_block(_live_text(), "backtest")
        assert "python -m slumdog.cli r1-backtest --root ." in text

    def test_the_run_step_cannot_fail_the_job(self):
        text = _job_block(_live_text(), "backtest")
        run_step = text.split("Run R1 rule backtest", 1)[1].split("- name:", 1)[0]
        assert "set -euo pipefail" not in run_step
        assert "exit 0" in run_step

    def test_the_summary_and_upload_steps_run_even_if_earlier_steps_failed(self):
        steps = _live_parsed()["jobs"]["backtest"]["steps"]
        by_name = {s.get("name"): s for s in steps if "name" in s}
        assert by_name["Publish backtest summary"].get("if") == "always()"
        assert by_name["Persist small evidence to git (r1_backtest report only, a few KB)"].get("if") == "always()"
        upload_steps = [s for s in steps if isinstance(s.get("uses"), str)
                         and s["uses"].startswith("actions/upload-artifact")]
        assert upload_steps and upload_steps[-1].get("if") == "always()"

    def test_artifact_name_and_path(self):
        steps = _live_parsed()["jobs"]["backtest"]["steps"]
        upload = next(s for s in steps if isinstance(s.get("uses"), str)
                      and s["uses"].startswith("actions/upload-artifact"))
        assert upload["with"]["name"] == "slumdog-r1-backtest"
        assert upload["with"]["path"] == "data/reports/r1_backtest_*"
