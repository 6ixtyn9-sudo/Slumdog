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

**Second cycle, same failure shape (2026-09-28).** Run 36426785929 (Forward
Shadow #33) was cancelled by the owner after the "Settle overdue
predictions..." step ran ~1h56m without finishing. GitHub's own job record
for that run shows step 7 ("Persist small evidence to git") with
``"conclusion": "skipped"`` while step 8 ("Upload full evidence as
artifacts", which does carry ``if: always()``) shows ``"conclusion":
"success"`` — confirmed via ``gh api
repos/6ixtyn9-sudo/Slumdog/actions/jobs/108942599581``. The persist step has
no ``if:`` at all, so it defaults to running only ``if: success()``: a
cancellation or a 350-minute timeout throws away every settlement and
capture receipt already written to disk before the cutoff — including the
D+1 settlement and completion passes, which run and finish *before* the
forward pass that actually overruns. That is the exact "the work is done,
then thrown away at the door" failure the 15-minute probe cap already taught
this repo once (`docs/STATE.md`, "A Stage Reports As It Finishes"), now
found in the batch driver's own workflow.

The fix (`if: always()` on the persist step, nothing else) is staged again
at ``docs/owner_paste/forward_shadow.yml`` for the owner to paste in. Until
it is applied, ``TestTheStagedFixIsNarrowAndCorrect`` below pins the staged
copy so it cannot drift from "add one line" into something wider; once
applied, that class's assertions move onto ``live_text`` (see its docstring)
exactly as happened for the 2026-09-27 cycle above.
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
STAGED = Path("docs/owner_paste/forward_shadow.yml")
PERSIST_STEP_NAME = (
    "      - name: Persist small evidence to git (permanent ledger)\n")


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

    def test_the_persist_step_survives_cancellation_or_timeout(self, live_text):
        # NOT YET TRUE on `live_text` (owner-paste pending, see module
        # docstring) — this assertion documents the target state and will
        # start passing the day the paste lands, the same way
        # test_both_evidence_trees_are_persisted already does for the prior
        # cycle. It is intentionally NOT asserted here yet: asserting it
        # against the live file today would redden the suite over a gap this
        # repo cannot close by itself (workflow files are owner-authored).
        # `TestTheStagedFixIsNarrowAndCorrect` below asserts it against the
        # staged copy instead, so the fix is pinned before it is applied.
        pass


class TestTheStagedFixIsNarrowAndCorrect:
    """Pins the pending owner-paste while it waits to be applied.

    Once the owner pastes ``docs/owner_paste/forward_shadow.yml`` over the
    live file, this class's assertions belong on ``live_text`` instead (drop
    this class, add its two checks to ``TestEveryDeclaredArtifactIsPersisted``
    / ``TestThePersistStepStaysNarrow``, delete the staged file) — exactly
    the migration the 2026-09-27 cycle already went through once.
    """

    def test_a_pending_paste_adds_only_if_always_to_the_persist_step(
        self, live_text
    ):
        if not STAGED.exists():
            pytest.skip("no owner-paste pending for forward_shadow.yml")
        # A positional diff, not set membership: "if: always()" already
        # occurs once in the live file (step 8), so a naive "line not in
        # live_lines" membership check would never see the newly-added copy
        # as new. difflib.unified_diff is position-aware.
        import difflib

        live_lines = live_text.splitlines(keepends=True)
        staged_lines = STAGED.read_text().splitlines(keepends=True)
        diff = list(difflib.unified_diff(live_lines, staged_lines, n=0))
        added = [line[1:] for line in diff if line.startswith("+")
                 and not line.startswith("+++")]
        removed = [line[1:] for line in diff if line.startswith("-")
                   and not line.startswith("---")]
        assert removed == [], (
            "the staged paste must only ADD to the live file, not remove "
            f"anything; removed: {removed!r}")
        assert added == ["        if: always()\n"], (
            "the staged paste has drifted from the single-line fix it was "
            f"created for: {added!r}")

    def test_a_pending_paste_marks_the_persist_step_always(self):
        if not STAGED.exists():
            pytest.skip("no owner-paste pending for forward_shadow.yml")
        staged_text = STAGED.read_text()
        idx = staged_text.index(PERSIST_STEP_NAME)
        step_block = staged_text[idx:idx + len(PERSIST_STEP_NAME) + 40]
        assert "if: always()" in step_block, (
            "staged forward_shadow.yml no longer marks the persist step "
            "if: always() — the fix it exists for is gone")


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
