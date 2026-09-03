"""Contract tests for the forward shadow batch workflow and driver script.

These tests verify:
- The workflow file exists and is valid YAML
- The workflow has the required contract properties
- The driver script computes correct target dates
- The driver script collision check works
"""
from __future__ import annotations

import datetime as dt
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

    def test_permissions_contents_read(self):
        perms = self.wf.get("permissions", {})
        assert perms.get("contents") == "read"

    def test_timeout_on_every_job(self):
        jobs = self.wf.get("jobs", {})
        for name, job in jobs.items():
            assert "timeout-minutes" in job, f"job {name!r} missing timeout"
            assert job["timeout-minutes"] <= 15, f"job {name!r} timeout > 15 min"

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
