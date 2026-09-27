"""Contract tests for the forward-shadow persist step.

Workflow files are owner-hand-authored, so an agent session cannot fix a
persist-glob gap directly — it can only prepare a replacement and prove it
correct. That separation already cost this project four days of daily-refresh
evidence: `selections_delta_*` was written on every dispatch from 2026-09-22
and silently discarded at job end because no `-name` in the persist step
matched it.

These tests guard both sides of the handover:

* the prepared file at ``docs/owner_paste/forward_shadow.yml`` must cover
  every artifact the driver declares, so pasting it is known to close the
  gap; and
* it must differ from the live workflow *only* in the persist globs — same
  trigger, same permissions, same pinned action SHAs, same timeout. A
  "fix" that quietly widened permissions or unpinned an action would be a
  far worse bug than the one being fixed.
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
PROPOSED = Path("docs/owner_paste/forward_shadow.yml")


@pytest.fixture(scope="module")
def live_text() -> str:
    return LIVE.read_text()


@pytest.fixture(scope="module")
def proposed_text() -> str:
    return PROPOSED.read_text()


class TestProposedWorkflowClosesTheGap:
    def test_the_prepared_file_exists(self):
        assert PROPOSED.is_file(), (
            "the owner paste must be a complete, reviewable file, not a "
            "fragment quoted in prose")

    def test_it_covers_every_declared_artifact(self, proposed_text):
        missing = uncovered_evidence(proposed_text, _load_evidence())
        assert missing == [], (
            "pasting this file would still discard: "
            + ", ".join(f"{r}/{f}" for r, f in missing))

    def test_the_refresh_deltas_are_the_point_of_the_fix(self, proposed_text):
        rules = parse_persist_rules(proposed_text)
        patterns = {pattern for _, pattern in rules}
        assert "selections_delta_*.json" in patterns
        assert "settlement_delta_*.json" in patterns

    def test_the_event_day_tree_is_persisted(self, proposed_text):
        roots = {root for root, _ in parse_persist_rules(proposed_text)}
        assert "data/reports/shadow_event_day" in roots

    def test_the_live_workflow_still_has_the_gap(self, live_text):
        # If this ever fails, the owner has pasted the fix — delete the
        # prepared file and this test rather than leaving a stale handover.
        missing = uncovered_evidence(live_text, _load_evidence())
        assert missing, (
            "the live workflow now covers everything; the pending owner "
            "paste in docs/owner_paste/ is obsolete and should be removed")


class TestProposedWorkflowChangesNothingElse:
    """A persist-glob fix must not become a security or pinning change."""

    def _significant(self, text: str) -> list[str]:
        return [
            line for line in text.splitlines()
            if "find data/reports/shadow" not in line
            and "find data/settlement_evidence" not in line
            and not line.strip().startswith("#")
        ]

    def test_only_the_persist_globs_differ(self, live_text, proposed_text):
        assert self._significant(live_text) == self._significant(proposed_text)

    def test_the_trigger_is_still_dispatch_only(self, proposed_text):
        assert "workflow_dispatch: {}" in proposed_text
        assert "schedule:" not in proposed_text
        assert "pull_request:" not in proposed_text

    def test_permissions_are_unchanged(self, live_text, proposed_text):
        for block in ("contents: write", "actions: read"):
            assert proposed_text.count(block) == live_text.count(block)

    def test_actions_remain_pinned_to_the_same_shas(self, live_text,
                                                    proposed_text):
        def uses(text: str) -> list[str]:
            return sorted(line.strip() for line in text.splitlines()
                          if line.strip().startswith("uses:"))

        pinned = uses(proposed_text)
        assert pinned == uses(live_text)
        for line in pinned:
            ref = line.split("@", 1)[1]
            assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref), (
                f"action is not pinned to a full SHA: {line}")

    def test_raw_bodies_and_archives_are_still_never_committed(self,
                                                              proposed_text):
        # The AGENTS.md waiver covers small JSON/text evidence only.
        patterns = {pattern for _, pattern in parse_persist_rules(proposed_text)}
        assert "*.txt" not in patterns
        assert "*.tar.gz" not in patterns
