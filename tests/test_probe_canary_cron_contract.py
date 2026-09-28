"""Contract test for the staged canary-cron workflow.

An agent token is refused when a push touches ``.github/workflows/`` (see
``docs/owner_paste/README.md``), so this file is staged as an ordinary file
at ``docs/owner_paste/probe_canary_cron.yml`` for the owner to paste onto
``main`` in the GitHub web UI — the same pattern already used for
``forward_shadow.yml`` and ``probe_kickoff_timezone.yml``. ``schedule``
triggers only fire from a repository's default branch, so this file has no
effect anywhere but ``main``; it cannot be verified live from a session
branch the way ``test_probe_workflow_persist_contract.py`` verifies the
already-applied push-trigger fix. These tests instead pin the staged
file's safety properties so an edit here cannot silently widen permissions,
drop the read-only guarantee, or point the job at anything other than the
one-request ``--canary-only`` mode before the owner ever applies it.

Once applied (owner pastes it onto ``main``), this file's checks should
migrate onto the live ``.github/workflows/probe_canary_cron.yml`` the same
way ``TestThePushTriggerStaysDurable`` did for the probe's own trigger fix,
and this staged copy should be deleted — git history is the record.
"""
from __future__ import annotations

from pathlib import Path

import yaml

STAGED = Path("docs/owner_paste/probe_canary_cron.yml")


def _staged_text() -> str:
    return STAGED.read_text()


def _parsed() -> dict:
    return yaml.safe_load(_staged_text())


class TestTheStagedCronFileExists:
    def test_the_file_is_present_and_parses_as_yaml(self):
        assert STAGED.exists()
        parsed = _parsed()
        assert isinstance(parsed, dict)


class TestItRunsOnlyTheCheapCanaryModeOnASchedule:
    def test_it_is_scheduled_roughly_every_two_hours(self):
        parsed = _parsed()
        on = parsed.get("on", parsed.get(True))
        schedule = on["schedule"]
        assert len(schedule) == 1
        cron = schedule[0]["cron"]
        # "<minute> */2 * * *" — some fixed minute, every 2 hours. Do not
        # pin the exact minute (the owner may retune the offset), but
        # require the hour field to be the every-2-hours form.
        fields = cron.split()
        assert len(fields) == 5, cron
        assert fields[1] == "*/2", cron

    def test_workflow_dispatch_is_also_available_for_manual_testing(self):
        parsed = _parsed()
        on = parsed.get("on", parsed.get(True))
        assert "workflow_dispatch" in on
        assert "probe_date" in on["workflow_dispatch"]["inputs"]

    def test_the_job_invokes_canary_only_and_nothing_heavier(self):
        text = _staged_text()
        assert "--canary-only" in text
        # Never the full sweep's other flags — this job must not grow into
        # a second copy of the 13-minute probe.
        assert "--circuit-breaker-probe" not in text
        assert "--hunt" not in text

    def test_the_job_makes_no_capture_and_no_evidence_tree(self):
        text = _staged_text()
        # capture_selected / ForebetCollector belong to the full pipeline
        # and the full probe sweep, not this one-request job.
        assert "capture_selected" not in text
        assert "ForebetCollector" not in text


class TestItStaysReadOnlyAndCheap:
    def test_read_only_permissions_and_a_short_timeout(self):
        text = _staged_text()
        assert "permissions:" in text
        assert "contents: read" in text
        parsed = _parsed()
        job = next(iter(parsed["jobs"].values()))
        assert job["timeout-minutes"] <= 5

    def test_pinned_actions_match_the_rest_of_the_repo(self):
        text = _staged_text()
        for needle in (
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
            "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
        ):
            assert needle in text, needle

    def test_an_unhealthy_sample_fails_the_job_so_run_history_is_the_map(self):
        text = _staged_text()
        assert "exit 1" in text


class TestItDocumentsItsOwnDeletion:
    def test_the_file_says_plainly_it_is_optional_and_deletable(self):
        text = _staged_text().lower()
        assert "delete this file" in text
        assert "measurement job" in text
