"""Contract test for the probe-kickoff-timezone workflow's push trigger.

Workflow files are owner-hand-authored: an agent token is refused when a
push touches ``.github/workflows/`` — confirmed again 2026-09-28:

    ! [remote rejected] arena/01a0e863-slumdog -> arena/01a0e863-slumdog
      (refusing to allow a GitHub App to create or update workflow
       `.github/workflows/probe_kickoff_timezone.yml` without `workflows`
       permission)

The live file's ``on.push.branches`` used to name exactly one branch,
``arena/01a0dd7a-slumdog`` — a specific prior session's branch, long gone —
so nothing that pushed from anywhere else silently triggered the workflow
at all. This was exactly the failure shape ``test_workflow_persist_contract
.py`` already documents for ``forward_shadow.yml``'s persist step: an
owner-authored file quietly stops doing what an agent assumed it still did.

The fix (``branches: [main, 'arena/**']``, nothing else) was staged at
``docs/workflow_staging/probe_kickoff_timezone.yml`` and applied by the owner
directly to `main` (commit ``1348ded``, picked up onto this branch via a
merge of ``origin/main`` rather than an authored diff — merging in an
already owner-committed workflow change is accepted by the restricted push
token even though authoring a fresh one is not). These tests now guard the
live file directly, the same migration ``test_workflow_persist_contract.py``
went through for ``forward_shadow.yml`` on 2026-09-27: the staged copy and
its "not yet applied" tests are gone, replaced by a permanent guard that the
branch list stays wide and never regresses to a single hardcoded session
branch.
"""
from __future__ import annotations

from pathlib import Path

import yaml

LIVE = Path(".github/workflows/probe_kickoff_timezone.yml")


def _live_text() -> str:
    return LIVE.read_text()


def _push_branches() -> list:
    # YAML parses the bare `on:` key as the boolean True unless quoted, so
    # look it up defensively under both spellings.
    parsed = yaml.safe_load(_live_text())
    on = parsed.get("on", parsed.get(True))
    return on["push"]["branches"]


class TestThePushTriggerStaysDurable:
    """Permanent guard: the push trigger must keep firing for `main` and
    for any session branch, and must never again narrow to one hardcoded
    session branch (the exact regression this file was created to catch).
    """

    def test_the_branch_list_includes_main_and_every_session_branch(self):
        branches = _push_branches()
        assert "main" in branches
        assert "arena/**" in branches

    def test_no_single_hardcoded_session_branch_has_reappeared(self):
        branches = _push_branches()
        # The specific regression this test pins against: an
        # owner-pasted, one-off session branch (of any shape) sitting
        # alone in on.push.branches, silently dead the moment that
        # session ends. `arena/**` covers every session branch already, so
        # a literal per-session entry (like the old `arena/01a0dd7a-
        # slumdog`) should never need to exist in the parsed branch list
        # again. Checked against the PARSED list, not raw text, since the
        # old branch name legitimately still appears in this file's own
        # explanatory comment.
        assert not any(b != "arena/**" and b.startswith("arena/")
                      for b in branches), branches
        assert len(branches) == 2, branches

    def test_dispatch_is_still_available_independent_of_the_push_trigger(
            self):
        # workflow_dispatch has its own inputs block, entirely independent
        # of on.push.branches — this is the path that already worked from
        # any branch even while the push trigger was stale (run a5e5720,
        # 2026-09-28), and must keep working regardless of push-trigger
        # changes.
        live_text = _live_text()
        assert "workflow_dispatch:" in live_text
        assert "probe_date:" in live_text


class TestThePermissionsAndPinsAreUnchanged:
    """The branch-list fix was scoped to widen ``on.push.branches`` only —
    pin the surrounding safety properties so a future edit to this file
    cannot quietly widen permissions or drop a pin alongside an unrelated
    trigger change.
    """

    def test_read_only_permissions_and_pins_are_present(self):
        live_text = _live_text()
        for needle in (
            "permissions:",
            "contents: read",
            "timeout-minutes: 15",
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
            "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
        ):
            assert needle in live_text, needle

    def test_no_new_circuit_breaker_cli_flag_was_smuggled_in(self):
        # The circuit-breaker measurement (Priority 1, item iii) is a
        # normal stage inside run_probe() itself, not a new CLI flag —
        # the existing hardcoded command line already exercises it.
        live_text = _live_text()
        assert "--circuit-breaker-probe" not in live_text
