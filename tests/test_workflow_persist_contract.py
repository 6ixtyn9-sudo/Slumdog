"""Contract tests for the forward-shadow persist step.

Workflow files are owner-hand-authored: an agent token is refused when a push
touches ``.github/workflows/``. That separation has a failure mode with real
cost — the driver gains an artifact type, the persist globs are not extended,
and the artifact is written on the runner and discarded at job end. It
happened: ``selections_delta_*`` was produced on every dispatch from
2026-09-22 and never committed.

The replacement was staged under ``docs/workflow_staging/`` and applied by the
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

The fix (`if: always()` on the persist step, nothing else) was staged at
``docs/workflow_staging/forward_shadow.yml`` and applied by the owner on
2026-09-29 (`main` commit ``28fc073``, "Add condition to persist evidence to
git") — a single added line, nothing else, confirmed by diffing the staged
copy against the applied commit byte-for-byte before this file was deleted.
This branch picked the change up via a merge of ``origin/main`` (merge
commit ``5c51b8b``) rather than an authored diff, the same pattern used for
every other owner-applied workflow fix in this repo. ``TestTheStagedFixIs
NarrowAndCorrect`` (which pinned the staged copy while it was pending) is
gone; its two checks now live on ``live_text`` directly, in
``TestEveryDeclaredArtifactIsPersisted`` and ``TestThePersistStepStaysNarrow``
below — the same migration the 2026-09-27 cycle already went through once.
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
        # Applied 2026-09-29 (main commit 28fc073): the persist step now
        # carries `if: always()`, so a cancellation or timeout after the
        # D+1 settlement/completion passes have already finished (but
        # before the overrunning forward pass completes) no longer throws
        # away evidence already written to disk. Positional, not membership:
        # "if: always()" already occurs once elsewhere in this file (the
        # upload-artifact step), so this checks it appears specifically
        # within the persist step's own block.
        idx = live_text.index(PERSIST_STEP_NAME)
        step_block = live_text[idx:idx + len(PERSIST_STEP_NAME) + 60]
        assert "if: always()" in step_block, (
            "the persist step no longer carries if: always() — a "
            "cancellation or timeout would silently discard already-"
            "finished evidence again")


class TestAppliedPipefailCorrection:
    def test_owner_applied_staged_replacement_byte_for_byte(self, live_text):
        # Owner-authored commit cf376e4 applied the grouped-find repair.
        assert STAGED.read_text() == live_text

    def test_all_three_optional_finds_neutralize_exit_before_pipefail(self):
        persist = STAGED.read_text().split(PERSIST_STEP_NAME, 1)[1]
        persist = persist.split("      - name: Upload full evidence", 1)[0]
        find_lines = [line.strip() for line in persist.splitlines()
                      if line.strip().startswith("{ find ")]
        assert len(find_lines) == 3
        assert all("2>/dev/null || true; } | xargs -r git add -f" in line
                   for line in find_lines)


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
