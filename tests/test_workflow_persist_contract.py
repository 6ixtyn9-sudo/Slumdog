"""Contract tests for the forward-shadow persist step.

Workflow files are owner-hand-authored: an agent token is refused when a push
touches ``.github/workflows/``. That separation has a failure mode with real
cost — the driver gains an artifact type, the persist globs are not extended,
and the artifact is written on the runner and discarded at job end. It
happened: ``selections_delta_*`` was produced on every dispatch from
2026-09-22 and never committed.

The replacement was staged under ``docs/owner_paste/`` and applied by the
owner on 2026-09-27. These tests now guard the live file directly, so the gap
cannot reopen quietly, and so a future widening of the globs cannot smuggle in
a permissions or pinning change alongside it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_workflow_evidence_globs import (
    _load_evidence,
    parse_persist_rules,
    uncovered_evidence,
)

LIVE = Path(".github/workflows/forward_shadow.yml")


@pytest.fixture(scope="module")
def live_text() -> str:
    return LIVE.read_text()


class TestEveryDeclaredArtifactIsPersisted:
    def test_nothing_the_driver_writes_is_discarded(self, live_text):
        missing = uncovered_evidence(live_text, _load_evidence())
        assert missing == [], (
            "these artifacts are written on the runner and dropped at job "
            "end: " + ", ".join(f"{r}/{f}" for r, f in missing))

    def test_the_refresh_deltas_are_named(self, live_text):
        # The specific regression: four days of refresh evidence was lost
        # because no -name matched these.
        patterns = {pattern for _, pattern in parse_persist_rules(live_text)}
        assert "selections_delta_*.json" in patterns
        assert "settlement_delta_*.json" in patterns

    def test_both_evidence_trees_are_persisted(self, live_text):
        roots = {root for root, _ in parse_persist_rules(live_text)}
        assert "data/reports/shadow" in roots
        assert "data/reports/shadow_event_day" in roots

    def test_the_staged_copy_was_removed_once_applied(self):
        assert not Path("docs/owner_paste/forward_shadow.yml").exists(), (
            "a staged paste that has been applied is a second source of "
            "truth; git history is the record")


class TestThePersistStepStaysNarrow:
    """Widening the globs must not become a security or pinning change."""

    def test_the_trigger_is_dispatch_only(self, live_text):
        assert "workflow_dispatch: {}" in live_text
        assert "schedule:" not in live_text
        assert "pull_request:" not in live_text

    def test_permissions_are_not_broader_than_needed(self, live_text):
        assert "contents: write" in live_text
        assert "packages:" not in live_text
        assert "id-token:" not in live_text

    def test_actions_are_pinned_to_full_shas(self, live_text):
        uses = [line.strip() for line in live_text.splitlines()
                if line.strip().startswith("uses:")]
        assert uses, "no actions found; the parse is wrong"
        for line in uses:
            ref = line.split("@", 1)[1]
            assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref), (
                f"action is not pinned to a full SHA: {line}")

    def test_raw_bodies_and_archives_are_never_committed(self, live_text):
        # The AGENTS.md waiver covers small JSON/text evidence only.
        patterns = {pattern for _, pattern in parse_persist_rules(live_text)}
        assert "*.txt" not in patterns
        assert "*.tar.gz" not in patterns
