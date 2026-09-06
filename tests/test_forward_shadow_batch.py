"""Contract tests for the forward shadow batch workflow and driver script.

These tests verify:
- The workflow file exists and is valid YAML
- The workflow has the required contract properties
- The driver script computes correct target dates
- The driver script collision check works
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


WORKFLOW_PATH = Path(".github/workflows/forward_shadow.yml")
DRIVER_SCRIPT = Path("scripts/forward_shadow_batch.py")


# ---------------------------------------------------------------------------
# Workflow contract tests
# ---------------------------------------------------------------------------


class TestForwardShadowWorkflowContract:
    """Verify the workflow file meets the forward batch contract."""

    @pytest.fixture(autouse=True)
    def _load_workflow(self):
        if not WORKFLOW_PATH.exists():
            pytest.skip(f"workflow not found: {WORKFLOW_PATH}")
        self.raw = WORKFLOW_PATH.read_text()
        self.wf = yaml.safe_load(self.raw)

    def test_workflow_exists(self):
        assert WORKFLOW_PATH.is_file()

    def test_manual_dispatch_only(self):
        triggers = self.wf.get("on", self.wf.get(True, {}))
        assert "workflow_dispatch" in triggers, "must support workflow_dispatch"
        assert "schedule" not in triggers, "must not have schedule trigger"
        assert "push" not in triggers, "must not have push trigger"
        assert "pull_request" not in triggers, "must not have pull_request trigger"

    def test_permissions_contract(self):
        # Owner amendment 2026-09-03: forward batch PERSISTS small evidence to
        # git (no ledgers in Codespace) and downloads history artifacts from
        # the depth pipeline. contents: write for the persist step; actions:
        # read for cross-run artifact download. Recorded in STATE.md.
        perms = self.wf.get("permissions", {})
        assert perms.get("contents") == "write"
        assert perms.get("actions") == "read"

    def test_timeout_on_every_job(self):
        # Owner decision 2026-09-06: raised from a 15-minute cap to 350
        # (matching the existing pipeline.yml precedent, just under
        # GitHub's practical 360-minute hosted-runner ceiling) so that
        # clearing a multi-date D+1 settlement backlog — and settling
        # additional sports once they go live — is never time-constrained.
        # The requirement that every job declare an explicit timeout is
        # unchanged; only the numeric ceiling moved.
        jobs = self.wf.get("jobs", {})
        for name, job in jobs.items():
            assert "timeout-minutes" in job, f"job {name!r} missing timeout"
            assert job["timeout-minutes"] <= 350, f"job {name!r} timeout > 350 min"

    def test_no_caches(self):
        """No cache actions in any step, no setup-python cache input."""
        jobs = self.wf.get("jobs", {})
        for name, job in jobs.items():
            for step in job.get("steps", []):
                uses = step.get("uses", "")
                assert "cache@" not in uses, (
                    f"step in {name!r}: cache action not allowed: {uses}"
                )
                with_block = step.get("with", {})
                if "setup-python" in uses:
                    assert "cache" not in with_block, (
                        f"step in {name!r}: setup-python cache input not allowed"
                    )
        # Also check for --no-cache-dir in pip install commands
        for name, job in jobs.items():
            for step in job.get("steps", []):
                run_cmd = step.get("run", "")
                if "pip install" in run_cmd:
                    assert "--no-cache-dir" in run_cmd, (
                        f"step in {name!r}: pip install must use --no-cache-dir"
                    )

    def test_pinned_action_shas(self):
        """Every 'uses:' must reference a full commit SHA, not a tag."""
        jobs = self.wf.get("jobs", {})
        for name, job in jobs.items():
            for step in job.get("steps", []):
                uses = step.get("uses", "")
                if not uses:
                    continue
                # Must be owner/repo@40-hex-sha format
                parts = uses.split("@")
                assert len(parts) == 2, f"step in {name!r}: bad uses format: {uses}"
                ref = parts[1]
                assert len(ref) == 40, (
                    f"step in {name!r}: action ref must be 40-char SHA, "
                    f"got {ref!r} ({len(ref)} chars)"
                )
                assert all(c in "0123456789abcdef" for c in ref), (
                    f"step in {name!r}: action ref must be hex, got {ref!r}"
                )

    def test_concurrency_no_cancel(self):
        conc = self.wf.get("concurrency", {})
        assert conc.get("cancel-in-progress") is False

    def test_no_credentials_beyond_github_token(self):
        # The workflow should not reference any secrets beyond GITHUB_TOKEN
        assert "secrets." not in self.raw or "GITHUB_TOKEN" in self.raw

    def test_retention_days_explicit(self):
        """Every upload-artifact step must set retention-days explicitly."""
        jobs = self.wf.get("jobs", {})
        for name, job in jobs.items():
            for step in job.get("steps", []):
                uses = step.get("uses", "")
                if "upload-artifact" not in uses:
                    continue
                with_block = step.get("with", {})
                assert "retention-days" in with_block, (
                    f"upload-artifact step in {name!r} missing retention-days"
                )

    def test_no_force_overwrite_in_steps(self):
        assert "--force" not in self.raw, "no --force flag allowed"


# ---------------------------------------------------------------------------
# Driver script tests
# ---------------------------------------------------------------------------


class TestForwardBatchDriver:
    """Test the forward batch driver script."""

    def test_driver_script_exists(self):
        assert DRIVER_SCRIPT.is_file()

    def test_driver_compiles(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(DRIVER_SCRIPT)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"py_compile failed: {result.stderr}"

    def test_help_exits_zero(self):
        result = subprocess.run(
            [sys.executable, str(DRIVER_SCRIPT), "--help"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0

    def test_compute_target_dates_d_plus_2(self):
        from scripts.forward_shadow_batch import compute_target_dates
        base = dt.date(2026, 9, 3)
        dates = compute_target_dates(5, base=base)
        assert len(dates) == 5
        # D+2 = 2026-09-05, D+3 = 2026-09-06, ...
        assert dates[0] == "2026-09-05"
        assert dates[1] == "2026-09-06"
        assert dates[4] == "2026-09-09"

    def test_compute_target_dates_returns_5(self):
        from scripts.forward_shadow_batch import compute_target_dates
        dates = compute_target_dates(5)
        assert len(dates) == 5
        # Each date should be a valid YYYY-MM-DD string
        for d in dates:
            dt.date.fromisoformat(d)  # raises if invalid

    def test_collision_check_no_evidence(self, tmp_path):
        from scripts.forward_shadow_batch import has_existing_evidence
        assert has_existing_evidence("2026-09-10", tmp_path) is False

    def test_collision_check_with_evidence(self, tmp_path):
        from scripts.forward_shadow_batch import has_existing_evidence
        run_dir = tmp_path / "data" / "reports" / "shadow" / "2026-09-10" / "abc123"
        run_dir.mkdir(parents=True)
        (run_dir / "shadow_selections.json").write_text("{}")
        assert has_existing_evidence("2026-09-10", tmp_path) is True

    def test_collision_check_blocked_only(self, tmp_path):
        from scripts.forward_shadow_batch import has_existing_evidence
        blocked_dir = tmp_path / "data" / "reports" / "shadow" / "2026-09-10" / "BLOCKED"
        blocked_dir.mkdir(parents=True)
        (blocked_dir / "BLOCKED_receipt.json").write_text("{}")
        assert has_existing_evidence("2026-09-10", tmp_path) is False

    def test_dry_run(self, tmp_path):
        from scripts.forward_shadow_batch import process_date
        result = process_date("2026-09-10", tmp_path, dry_run=True)
        assert result["status"] == "DRY_RUN"
        assert result["target_date"] == "2026-09-10"

    def test_dry_run_skips_existing(self, tmp_path):
        from scripts.forward_shadow_batch import process_date
        run_dir = tmp_path / "data" / "reports" / "shadow" / "2026-09-10" / "abc123"
        run_dir.mkdir(parents=True)
        (run_dir / "shadow_selections.json").write_text("{}")
        result = process_date("2026-09-10", tmp_path, dry_run=True)
        assert result["status"] == "SKIPPED_EXISTING"


# ---------------------------------------------------------------------------
# D+1 automated settlement backlog (owner-confirmed 2026-09-06)
# ---------------------------------------------------------------------------


def _make_completed_run(
    repo_root: Path,
    target_date: str,
    run_id: str,
    *,
    sports: list[str] | None = None,
    settled: bool = False,
) -> Path:
    """Create a minimal completed shadow run fixture on disk."""
    run_dir = repo_root / "data" / "reports" / "shadow" / target_date / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    sports = sports or ["football"]
    selections = [
        {
            "sport": sports[0],
            "event_id": f"{sports[0]}:1",
            "event_date": target_date,
            "rank_within_sport_day": 1,
            "status": "PRIMARY_SHADOW_SELECTION",
            "run_id": run_id,
        }
    ]
    manifest_pool = [
        {"sport": s, "event_id": f"{s}:99", "event_date": target_date,
         "considered_status": "ELIGIBLE_RANKED_BEYOND_TOP3"}
        for s in sports[1:]
    ]
    (run_dir / "shadow_selections.json").write_text(
        json.dumps({
            "run_id": run_id, "target_date": target_date,
            "selections": selections, "sport_day_summary": [],
        })
    )
    (run_dir / "manifest.json").write_text(
        json.dumps({
            "run_id": run_id, "target_date": target_date,
            "considered_pool": manifest_pool,
        })
    )
    if settled:
        (run_dir / "settlement.json").write_text("{}")
        (run_dir / "settlement.json.sha256").write_text("deadbeef  settlement.json\n")
    return run_dir


class TestFindSettleableRun:
    def test_no_run_dir_returns_none(self, tmp_path):
        from scripts.forward_shadow_batch import find_settleable_run
        assert find_settleable_run("2026-09-10", tmp_path) is None

    def test_completed_unsettled_run_found(self, tmp_path):
        from scripts.forward_shadow_batch import find_settleable_run
        _make_completed_run(tmp_path, "2026-09-10", "run0001aaaaaaaaaa")
        assert find_settleable_run("2026-09-10", tmp_path) == "run0001aaaaaaaaaa"

    def test_already_settled_run_not_returned(self, tmp_path):
        from scripts.forward_shadow_batch import find_settleable_run
        _make_completed_run(
            tmp_path, "2026-09-10", "run0001aaaaaaaaaa", settled=True,
        )
        assert find_settleable_run("2026-09-10", tmp_path) is None

    def test_blocked_only_returns_none(self, tmp_path):
        from scripts.forward_shadow_batch import find_settleable_run
        blocked = tmp_path / "data" / "reports" / "shadow" / "2026-09-10" / "BLOCKED"
        blocked.mkdir(parents=True)
        (blocked / "BLOCKED_x.json").write_text("{}")
        assert find_settleable_run("2026-09-10", tmp_path) is None


class TestFindSettleableDates:
    def test_same_day_not_eligible(self, tmp_path):
        from scripts.forward_shadow_batch import find_settleable_dates
        _make_completed_run(tmp_path, "2026-09-10", "run0001aaaaaaaaaa")
        pending = find_settleable_dates(tmp_path, as_of=dt.date(2026, 9, 10))
        assert pending == []

    def test_d_plus_1_is_eligible(self, tmp_path):
        from scripts.forward_shadow_batch import find_settleable_dates
        _make_completed_run(tmp_path, "2026-09-10", "run0001aaaaaaaaaa")
        pending = find_settleable_dates(tmp_path, as_of=dt.date(2026, 9, 11))
        assert pending == [("2026-09-10", "run0001aaaaaaaaaa")]

    def test_older_than_d_plus_1_still_eligible(self, tmp_path):
        from scripts.forward_shadow_batch import find_settleable_dates
        _make_completed_run(tmp_path, "2026-09-05", "run0001aaaaaaaaaa")
        pending = find_settleable_dates(tmp_path, as_of=dt.date(2026, 9, 12))
        assert pending == [("2026-09-05", "run0001aaaaaaaaaa")]

    def test_multiple_dates_returned_oldest_first(self, tmp_path):
        from scripts.forward_shadow_batch import find_settleable_dates
        _make_completed_run(tmp_path, "2026-09-08", "run0003cccccccccc")
        _make_completed_run(tmp_path, "2026-09-05", "run0001aaaaaaaaaa")
        _make_completed_run(tmp_path, "2026-09-06", "run0002bbbbbbbbbb")
        pending = find_settleable_dates(tmp_path, as_of=dt.date(2026, 9, 12))
        assert pending == [
            ("2026-09-05", "run0001aaaaaaaaaa"),
            ("2026-09-06", "run0002bbbbbbbbbb"),
            ("2026-09-08", "run0003cccccccccc"),
        ]

    def test_already_settled_excluded(self, tmp_path):
        from scripts.forward_shadow_batch import find_settleable_dates
        _make_completed_run(
            tmp_path, "2026-09-05", "run0001aaaaaaaaaa", settled=True,
        )
        pending = find_settleable_dates(tmp_path, as_of=dt.date(2026, 9, 12))
        assert pending == []

    def test_no_shadow_root_returns_empty(self, tmp_path):
        from scripts.forward_shadow_batch import find_settleable_dates
        pending = find_settleable_dates(tmp_path, as_of=dt.date(2026, 9, 12))
        assert pending == []

    def test_non_date_siblings_ignored(self, tmp_path):
        from scripts.forward_shadow_batch import find_settleable_dates
        shadow_root = tmp_path / "data" / "reports" / "shadow"
        (shadow_root / "bundles").mkdir(parents=True)
        (shadow_root / "settlements").mkdir(parents=True)
        (shadow_root / "batch_2026-09-03").mkdir(parents=True)
        _make_completed_run(tmp_path, "2026-09-05", "run0001aaaaaaaaaa")
        pending = find_settleable_dates(tmp_path, as_of=dt.date(2026, 9, 12))
        assert pending == [("2026-09-05", "run0001aaaaaaaaaa")]

    def test_blocked_date_not_double_counted(self, tmp_path):
        from scripts.forward_shadow_batch import find_settleable_dates
        blocked = tmp_path / "data" / "reports" / "shadow" / "2026-09-04" / "BLOCKED"
        blocked.mkdir(parents=True)
        (blocked / "BLOCKED_x.json").write_text("{}")
        pending = find_settleable_dates(tmp_path, as_of=dt.date(2026, 9, 12))
        assert pending == []


class TestSportsInRun:
    def test_reads_selections_and_pool(self, tmp_path):
        from scripts.forward_shadow_batch import _sports_in_run
        _make_completed_run(
            tmp_path, "2026-09-05", "run0001aaaaaaaaaa",
            sports=["football", "basketball"],
        )
        sports = _sports_in_run("2026-09-05", "run0001aaaaaaaaaa", tmp_path)
        assert sports == ["basketball", "football"]

    def test_football_only_run(self, tmp_path):
        from scripts.forward_shadow_batch import _sports_in_run
        _make_completed_run(tmp_path, "2026-09-05", "run0001aaaaaaaaaa")
        assert _sports_in_run("2026-09-05", "run0001aaaaaaaaaa", tmp_path) == ["football"]

    def test_missing_run_returns_empty(self, tmp_path):
        from scripts.forward_shadow_batch import _sports_in_run
        assert _sports_in_run("2026-09-05", "doesnotexist0000", tmp_path) == []

    def test_selections_file_is_a_list_not_a_dict_does_not_raise(self, tmp_path):
        # Regression: valid JSON but the wrong top-level shape (e.g. a bare
        # list instead of {"selections": [...]}) must be treated the same
        # as unparseable input, not raise AttributeError/TypeError out of
        # this helper -- run_settlement_for_date's "never raises" contract
        # depends on it.
        from scripts.forward_shadow_batch import _sports_in_run
        run_dir = tmp_path / "data" / "reports" / "shadow" / "2026-09-05" / "run0001aaaaaaaaaa"
        run_dir.mkdir(parents=True)
        (run_dir / "shadow_selections.json").write_text(json.dumps([1, 2, 3]))
        assert _sports_in_run("2026-09-05", "run0001aaaaaaaaaa", tmp_path) == []

    def test_selection_entry_not_a_dict_does_not_raise(self, tmp_path):
        from scripts.forward_shadow_batch import _sports_in_run
        run_dir = tmp_path / "data" / "reports" / "shadow" / "2026-09-05" / "run0001aaaaaaaaaa"
        run_dir.mkdir(parents=True)
        (run_dir / "shadow_selections.json").write_text(
            json.dumps({"selections": ["not-a-dict"]})
        )
        assert _sports_in_run("2026-09-05", "run0001aaaaaaaaaa", tmp_path) == []

    def test_manifest_wrong_shape_does_not_raise(self, tmp_path):
        from scripts.forward_shadow_batch import _sports_in_run
        run_dir = tmp_path / "data" / "reports" / "shadow" / "2026-09-05" / "run0001aaaaaaaaaa"
        run_dir.mkdir(parents=True)
        (run_dir / "shadow_selections.json").write_text(json.dumps({"selections": []}))
        (run_dir / "manifest.json").write_text(json.dumps(["not", "a", "dict"]))
        assert _sports_in_run("2026-09-05", "run0001aaaaaaaaaa", tmp_path) == []


class TestRunSettlementForDate:
    def test_no_sports_resolved_when_run_missing(self, tmp_path):
        from scripts.forward_shadow_batch import run_settlement_for_date
        result = run_settlement_for_date("2026-09-05", "doesnotexist0000", tmp_path)
        assert result["status"] == "NO_SPORTS_RESOLVED"
        assert result["error"]

    def test_never_raises_on_malformed_selections_file(self, tmp_path):
        # End-to-end version of the _sports_in_run regression above: a
        # malformed-but-valid-JSON run must resolve to NO_SPORTS_RESOLVED,
        # never propagate an exception out of run_settlement_for_date.
        from scripts.forward_shadow_batch import run_settlement_for_date
        run_dir = tmp_path / "data" / "reports" / "shadow" / "2026-09-05" / "run0001aaaaaaaaaa"
        run_dir.mkdir(parents=True)
        (run_dir / "shadow_selections.json").write_text(json.dumps([1, 2, 3]))
        result = run_settlement_for_date("2026-09-05", "run0001aaaaaaaaaa", tmp_path)
        assert result["status"] == "NO_SPORTS_RESOLVED"

    def test_settlement_error_isolated_not_raised(self, tmp_path, monkeypatch):
        from scripts.forward_shadow_batch import run_settlement_for_date
        _make_completed_run(tmp_path, "2026-09-05", "run0001aaaaaaaaaa")

        def _boom(**kwargs):
            from slumdog.shadow_settle import SettlementError
            raise SettlementError("simulated failure")

        monkeypatch.setattr("slumdog.shadow_settle.settle_run", _boom)
        result = run_settlement_for_date("2026-09-05", "run0001aaaaaaaaaa", tmp_path)
        assert result["status"] == "SETTLEMENT_FAILED"
        assert "simulated failure" in result["error"]

    def test_unexpected_exception_also_isolated(self, tmp_path, monkeypatch):
        from scripts.forward_shadow_batch import run_settlement_for_date
        _make_completed_run(tmp_path, "2026-09-05", "run0001aaaaaaaaaa")

        def _boom(**kwargs):
            raise RuntimeError("unexpected")

        monkeypatch.setattr("slumdog.shadow_settle.settle_run", _boom)
        result = run_settlement_for_date("2026-09-05", "run0001aaaaaaaaaa", tmp_path)
        assert result["status"] == "SETTLEMENT_FAILED"
        assert "unexpected" in result["error"]

    def test_success_path_records_artifact(self, tmp_path, monkeypatch):
        from scripts.forward_shadow_batch import run_settlement_for_date
        _make_completed_run(tmp_path, "2026-09-05", "run0001aaaaaaaaaa")

        class _FakeResult:
            settlement_artifact_path = "fake/path/settlement.json"
            settlement_artifact_sha256 = "fake_sha"
            summary = {"primary_hit_rate": 1.0}

        captured_kwargs = {}

        def _fake_settle_run(**kwargs):
            captured_kwargs.update(kwargs)
            return _FakeResult()

        monkeypatch.setattr("slumdog.shadow_settle.settle_run", _fake_settle_run)
        result = run_settlement_for_date("2026-09-05", "run0001aaaaaaaaaa", tmp_path)
        assert result["status"] == "SETTLED"
        assert result["settlement_artifact_sha256"] == "fake_sha"
        assert result["primary_hit_rate"] == 1.0
        # sport scoping actually reached settle_run
        assert captured_kwargs["sports"] == ["football"]


class TestRunSettlementBacklog:
    def test_empty_backlog_returns_empty(self, tmp_path):
        from scripts.forward_shadow_batch import run_settlement_backlog
        results = run_settlement_backlog(
            tmp_path, as_of=dt.date(2026, 9, 12),
        )
        assert results == []

    def test_dry_run_does_not_call_settle(self, tmp_path, monkeypatch):
        from scripts.forward_shadow_batch import run_settlement_backlog
        _make_completed_run(tmp_path, "2026-09-05", "run0001aaaaaaaaaa")

        def _boom(**kwargs):
            raise AssertionError("settle_run must not be called in dry-run")

        monkeypatch.setattr("slumdog.shadow_settle.settle_run", _boom)
        results = run_settlement_backlog(
            tmp_path, as_of=dt.date(2026, 9, 12), dry_run=True,
        )
        assert len(results) == 1
        assert results[0]["status"] == "DRY_RUN"

    def test_one_failure_does_not_block_others(self, tmp_path, monkeypatch):
        from scripts.forward_shadow_batch import run_settlement_backlog
        _make_completed_run(tmp_path, "2026-09-05", "run0001aaaaaaaaaa")
        _make_completed_run(tmp_path, "2026-09-06", "run0002bbbbbbbbbb")

        class _FakeResult:
            settlement_artifact_path = "fake/path/settlement.json"
            settlement_artifact_sha256 = "fake_sha"
            summary = {"primary_hit_rate": 0.0}

        def _flaky_settle_run(*, target_date, **kwargs):
            from slumdog.shadow_settle import SettlementError
            if target_date == "2026-09-05":
                raise SettlementError("simulated network failure")
            return _FakeResult()

        monkeypatch.setattr("slumdog.shadow_settle.settle_run", _flaky_settle_run)
        results = run_settlement_backlog(
            tmp_path, as_of=dt.date(2026, 9, 12), pause_seconds=0,
        )
        by_date = {r["target_date"]: r for r in results}
        assert by_date["2026-09-05"]["status"] == "SETTLEMENT_FAILED"
        assert by_date["2026-09-06"]["status"] == "SETTLED"

    def test_never_touches_already_settled(self, tmp_path, monkeypatch):
        from scripts.forward_shadow_batch import run_settlement_backlog
        _make_completed_run(
            tmp_path, "2026-09-05", "run0001aaaaaaaaaa", settled=True,
        )

        def _boom(**kwargs):
            raise AssertionError("settle_run must not be called for an already-settled run")

        monkeypatch.setattr("slumdog.shadow_settle.settle_run", _boom)
        results = run_settlement_backlog(
            tmp_path, as_of=dt.date(2026, 9, 12), pause_seconds=0,
        )
        assert results == []
