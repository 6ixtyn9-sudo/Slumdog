"""Contract test for the probe-kickoff-timezone workflow's push trigger.

Workflow files are owner-hand-authored: an agent token is refused when a
push touches ``.github/workflows/`` — confirmed again 2026-09-28:

    ! [remote rejected] arena/01a0e863-slumdog -> arena/01a0e863-slumdog
      (refusing to allow a GitHub App to create or update workflow
       `.github/workflows/probe_kickoff_timezone.yml` without `workflows`
       permission)

The live file's ``on.push.branches`` names exactly one branch,
``arena/01a0dd7a-slumdog`` — a specific prior session's branch. That branch
is gone; nothing that pushes from anywhere else (this branch included,
verified 2026-09-28) triggers the workflow at all, silently. This does NOT
block running the probe — ``workflow_dispatch`` already works from the
Actions UI against any branch regardless of the push trigger's branch list
(run a5e5720, 2026-09-28, dispatched against ``arena/01a0e863-slumdog`` with
no paste applied) — but the push trigger going dead the moment a session
ends is exactly the same failure shape ``test_workflow_persist_contract.py``
already documents for ``forward_shadow.yml``'s persist step: an
owner-authored file quietly stops doing what an agent assumed it still did.

The fix (``branches: [main, 'arena/**']``, nothing else) is staged at
``docs/owner_paste/probe_kickoff_timezone.yml`` for the owner to paste in
whenever convenient — it is not blocking, since dispatch already works.
Until it is applied, this file pins the staged copy so it cannot drift into
a wider change; once applied, move its two assertions onto ``live_text``
and delete the staged copy, the same migration ``forward_shadow.yml``'s
paste went through.
"""
from __future__ import annotations

import difflib
from pathlib import Path

import pytest

LIVE = Path(".github/workflows/probe_kickoff_timezone.yml")
STAGED = Path("docs/owner_paste/probe_kickoff_timezone.yml")


@pytest.fixture(scope="module")
def live_text() -> str:
    return LIVE.read_text()


class TestTheLiveTriggerIsStillNarrow:
    """Documents the gap directly against the live file, the same way
    test_workflow_persist_contract.py's TestEveryDeclaredArtifactIsPersisted
    documents forward_shadow.yml's pre-paste state — so the assertion this
    class exists to eventually make (a durable trigger) is visible as a
    target, not silently absent."""

    def test_the_branch_list_is_not_yet_durable(self, live_text):
        # NOT a bug being asserted against; a record of the gap the staged
        # paste below closes. Flip this the day the paste lands (see
        # TestTheStagedFixIsNarrowAndCorrect's docstring for the migration).
        assert "arena/01a0dd7a-slumdog" in live_text
        assert "'arena/**'" not in live_text

    def test_dispatch_is_unaffected_by_the_stale_trigger(self, live_text):
        # workflow_dispatch has its own inputs block, entirely independent
        # of on.push.branches — the stale push trigger cannot silently
        # break the one path that already works from any branch.
        assert "workflow_dispatch:" in live_text
        assert "probe_date:" in live_text


class TestTheStagedFixIsNarrowAndCorrect:
    """Pins the pending owner-paste while it waits to be applied.

    Once the owner pastes ``docs/owner_paste/probe_kickoff_timezone.yml``
    over the live file, drop this class, add
    ``test_the_branch_list_is_durable`` to ``TestTheLiveTriggerIsStillNarrow``
    (renamed), and delete the staged file — the migration
    ``test_workflow_persist_contract.py`` already went through once for
    ``forward_shadow.yml``.
    """

    def test_the_staged_copy_only_widens_the_branch_list(self, live_text):
        if not STAGED.exists():
            pytest.skip("no owner-paste pending for probe_kickoff_timezone.yml")
        live_lines = live_text.splitlines(keepends=True)
        staged_lines = STAGED.read_text().splitlines(keepends=True)
        diff = list(difflib.unified_diff(live_lines, staged_lines, n=0))
        added_code = [line[1:] for line in diff if line.startswith("+")
                     and not line.startswith("+++")
                     and line[1:].strip() and not line[1:].strip().startswith("#")]
        removed_code = [line[1:] for line in diff if line.startswith("-")
                        and not line.startswith("---")
                        and line[1:].strip() and not line[1:].strip().startswith("#")]
        # Comment-only lines are allowed to change freely (the staged copy
        # explains itself); only the CODE lines are pinned to exactly this
        # one substitution — the old single branch entry replaced by two.
        assert removed_code == ["      - arena/01a0dd7a-slumdog\n"], (
            "the staged paste has drifted from the branch-list-only fix "
            f"it was created for; removed code lines: {removed_code!r}")
        assert added_code == ["      - main\n", "      - 'arena/**'\n"], (
            "the staged paste has drifted from the branch-list-only fix "
            f"it was created for; added code lines: {added_code!r}")

    def test_the_staged_copy_keeps_workflow_dispatch_probe_date(self):
        if not STAGED.exists():
            pytest.skip("no owner-paste pending for probe_kickoff_timezone.yml")
        staged_text = STAGED.read_text()
        assert "workflow_dispatch:" in staged_text
        assert "probe_date:" in staged_text
        # No new CLI flag plumbing needed in the workflow itself — the
        # circuit-breaker measurement (Priority 1, item iii) is now a
        # normal stage inside run_probe(), so the existing hardcoded
        # command line already exercises it.
        assert "--circuit-breaker-probe" not in staged_text

    def test_the_staged_copy_does_not_touch_permissions_or_pins(
        self, live_text
    ):
        if not STAGED.exists():
            pytest.skip("no owner-paste pending for probe_kickoff_timezone.yml")
        staged_text = STAGED.read_text()
        for needle in ("permissions:", "contents: read",
                      "timeout-minutes: 15",
                      "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
                      "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
                      "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"):
            assert needle in live_text and needle in staged_text, needle
