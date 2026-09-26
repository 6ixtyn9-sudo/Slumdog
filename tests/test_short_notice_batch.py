"""Batch-driver contract for the SHORT_NOTICE stage and for evidence
persistence coverage.

Two separate concerns live here:

1. **The stage itself** — capture today's board, evaluate it under the
   SHORT_NOTICE declaration, and settle it in its own tree. Every failure
   mode must stay inside the stage (the driver's isolation contract) so a
   short-notice problem can never cost the frozen 24h pipeline a dispatch.

2. **Evidence persistence coverage** — the driver declares every small
   artifact it can write in ``PERSISTED_EVIDENCE``; the workflow's persist
   step decides what actually survives the runner. Workflow files are
   owner-authored, so the check here is a subset assertion: the current gap
   may only shrink, and a NEW uncovered artifact type fails the suite. The
   gap is real today — ``selections_delta_*`` has been written on every
   dispatch since 2026-09-22 and never committed.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

import scripts.forward_shadow_batch as fsb
from scripts.check_workflow_evidence_globs import (
    is_covered,
    parse_persist_rules,
    uncovered_evidence,
)

WORKFLOW_PATH = Path(".github/workflows/forward_shadow.yml")
TARGET_DATE = "2026-09-26"
RUN_ID = "0123456789abcdef"


def _write_run(root: Path, subdir: str, target_date: str, run_id: str,
               selections: list[dict], *, settled: bool = False) -> Path:
    run_dir = root / "data" / "reports" / subdir / target_date / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "shadow_selections.json").write_text(json.dumps({
        "run_id": run_id, "target_date": target_date,
        "run_status": "SHADOW_SELECTIONS_EMITTED", "selections": selections,
    }))
    (run_dir / "manifest.json").write_text(json.dumps({
        "run_id": run_id, "target_date": target_date, "considered_pool": [],
    }))
    if settled:
        (run_dir / "settlement.json").write_text(json.dumps({"grades": []}))
    return run_dir


def _selection(sport: str, rank: int) -> dict:
    return {"sport": sport, "event_date": TARGET_DATE,
            "event_id": f"{sport}:{rank}", "rank_within_sport_day": rank,
            "status": ("PRIMARY_SHADOW_SELECTION" if rank == 1
                       else "TOP3_EVALUATION_COHORT")}


# ===========================================================================
# Group 1: stage plumbing
# ===========================================================================


class TestShortNoticeStage:
    def test_constants_point_at_the_separate_tree_and_declaration(self):
        assert fsb.SHORT_NOTICE_SHADOW_SUBDIR == "shadow_short_notice"
        assert fsb.STANDARD_SHADOW_SUBDIR == "shadow"
        assert fsb.SHORT_NOTICE_CONFIG == (
            "config/shadow_evaluator_short_notice.json")
        assert Path(fsb.SHORT_NOTICE_CONFIG).is_file()

    def test_find_short_notice_run_ignores_the_frozen_tree(self, tmp_path):
        _write_run(tmp_path, "shadow", TARGET_DATE, RUN_ID, [])
        assert fsb.find_short_notice_run(TARGET_DATE, tmp_path) is None
        _write_run(tmp_path, "shadow_short_notice", TARGET_DATE, "abc123", [])
        assert fsb.find_short_notice_run(TARGET_DATE, tmp_path) == "abc123"

    def test_find_short_notice_run_skips_blocked_and_incomplete(self, tmp_path):
        day = tmp_path / "data" / "reports" / "shadow_short_notice" / TARGET_DATE
        (day / "BLOCKED").mkdir(parents=True)
        (day / "BLOCKED" / "shadow_selections.json").write_text("{}")
        (day / "partial").mkdir()
        assert fsb.find_short_notice_run(TARGET_DATE, tmp_path) is None

    def test_summary_counts_one_r1_per_sport(self, tmp_path):
        run_dir = _write_run(
            tmp_path, "shadow_short_notice", TARGET_DATE, RUN_ID,
            [_selection("football", 1), _selection("football", 2),
             _selection("basketball", 1), _selection("hockey", 1),
             _selection("hockey", 3)])
        summary = fsb.summarise_short_notice_run(run_dir)
        assert summary["sports_with_r1"] == ["basketball", "football", "hockey"]
        assert summary["r1_count"] == 3
        assert summary["selection_count"] == 5

    def test_summary_of_unreadable_run_is_zeroed_not_raised(self, tmp_path):
        assert fsb.summarise_short_notice_run(tmp_path / "nope") == {
            "sports_with_r1": [], "r1_count": 0, "selection_count": 0}

    def test_existing_run_is_not_re_decided(self, tmp_path, monkeypatch):
        _write_run(tmp_path, "shadow_short_notice", TARGET_DATE, RUN_ID, [])

        def _boom(*a, **k):
            raise AssertionError("must not capture when a run already exists")

        monkeypatch.setattr("slumdog.forebet.ForebetCollector", _boom)
        entry = fsb.run_short_notice_for_date(TARGET_DATE, tmp_path)
        assert entry["status"] == "ALREADY_RUN"
        assert entry["run_id"] == RUN_ID

    def test_dry_run_touches_nothing(self, tmp_path, monkeypatch):
        def _boom(*a, **k):
            raise AssertionError("dry run must not capture")

        monkeypatch.setattr("slumdog.forebet.ForebetCollector", _boom)
        entry = fsb.run_short_notice_for_date(
            TARGET_DATE, tmp_path, dry_run=True)
        assert entry["status"] == "DRY_RUN"
        assert not (tmp_path / "data" / "reports").exists()

    def test_capture_failure_is_isolated_never_raised(self, tmp_path, monkeypatch):
        class _Collector:
            def __init__(self, **kwargs):
                pass

            def capture_selected(self, *a, **k):
                raise RuntimeError("relay down")

        monkeypatch.setattr("slumdog.forebet.ForebetCollector", _Collector)
        entry = fsb.run_short_notice_for_date(TARGET_DATE, tmp_path)
        assert entry["status"] == "SHORT_NOTICE_FAILED"
        assert "relay down" in entry["error"]

    def _stub_collector(self, tmp_path, monkeypatch, sports: list[str],
                        failures: int = 0):
        reports = tmp_path / "data" / "reports"
        reports.mkdir(parents=True, exist_ok=True)

        class _Collector:
            def __init__(self, **kwargs):
                pass

            def capture_selected(self, target_date, force=False,
                                 receipt_name=None):
                (reports / receipt_name).write_text(json.dumps({
                    "target_date": target_date,
                    "captured": [{"sport": s} for s in sports],
                    "failures": [f"x{i}" for i in range(failures)],
                }))
                return []

        monkeypatch.setattr("slumdog.forebet.ForebetCollector", _Collector)

    def test_empty_board_reports_no_captures_and_skips_evaluation(
            self, tmp_path, monkeypatch):
        self._stub_collector(tmp_path, monkeypatch, [])
        monkeypatch.setattr(fsb, "run_evaluator", lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("must not evaluate an empty capture")))
        entry = fsb.run_short_notice_for_date(TARGET_DATE, tmp_path)
        assert entry["status"] == "NO_CAPTURES"

    def _run_stage_with_fake_evaluator(self, tmp_path, monkeypatch, *,
                                       selections, rejections=None,
                                       run_status="SHADOW_SELECTIONS_EMITTED"):
        """Run the stage end to end with the network and evaluator stubbed.

        The evaluator's artifact dir is written OUTSIDE the short-notice tree
        so the stage's "one decision per date" guard does not short-circuit
        before the evaluator is reached.
        """
        self._stub_collector(
            tmp_path, monkeypatch, ["football", "basketball", "hockey"])
        run_dir = _write_run(
            tmp_path, "eval_out", TARGET_DATE, RUN_ID, selections)
        (run_dir / "manifest.json").write_text(json.dumps({
            "run_id": RUN_ID, "considered_pool": [],
            "short_notice_timing_rejections": rejections or {},
        }))
        seen: dict = {}

        def _fake_eval(target_date, repo_root, **kwargs):
            seen.update(kwargs)
            seen["target_date"] = target_date
            return {"run_id": RUN_ID, "run_status": run_status,
                    "artifact_dir": str(run_dir)}

        monkeypatch.setattr(fsb, "run_evaluator", _fake_eval)
        return fsb.run_short_notice_for_date(TARGET_DATE, tmp_path), seen

    def test_successful_stage_reports_one_r1_per_sport(
            self, tmp_path, monkeypatch):
        entry, _ = self._run_stage_with_fake_evaluator(
            tmp_path, monkeypatch,
            selections=[_selection("basketball", 1), _selection("hockey", 1),
                        _selection("hockey", 2)])
        assert entry["status"] == "SELECTIONS_EMITTED"
        assert entry["track"] == "SHORT_NOTICE"
        assert entry["sports_with_r1"] == ["basketball", "hockey"]
        assert entry["r1_count"] == 2
        assert entry["selection_count"] == 3
        assert entry["captured_sports"] == 3

    def test_stage_uses_the_short_notice_declaration_and_its_own_receipt(
            self, tmp_path, monkeypatch):
        entry, seen = self._run_stage_with_fake_evaluator(
            tmp_path, monkeypatch, selections=[_selection("hockey", 1)])
        assert seen["config_rel"] == fsb.SHORT_NOTICE_CONFIG
        assert seen["target_date"] == TARGET_DATE
        assert seen["receipt_name"].startswith(
            f"capture_short_notice_{TARGET_DATE}_")
        assert seen["receipt_name"].endswith(".json")
        # Never reuses or overwrites the frozen track's daily receipt.
        assert seen["receipt_name"] != f"capture_{TARGET_DATE}.json"
        assert entry["capture_receipt"] == seen["receipt_name"]

    def test_stage_surfaces_why_events_were_refused(self, tmp_path, monkeypatch):
        entry, _ = self._run_stage_with_fake_evaluator(
            tmp_path, monkeypatch, selections=[_selection("hockey", 1)],
            rejections={"INSUFFICIENT_LEAD_BEFORE_KICKOFF": 4,
                        "KICKOFF_MISSING_OR_UNPARSEABLE": 1})
        assert entry["timing_rejections"] == {
            "INSUFFICIENT_LEAD_BEFORE_KICKOFF": 4,
            "KICKOFF_MISSING_OR_UNPARSEABLE": 1}

    def test_no_qualifying_pick_is_a_valid_outcome(self, tmp_path, monkeypatch):
        entry, _ = self._run_stage_with_fake_evaluator(
            tmp_path, monkeypatch, selections=[],
            run_status="SHADOW_NO_SELECTION")
        assert entry["status"] == "NO_SELECTION"
        assert entry["r1_count"] == 0

    def test_blocked_evaluator_is_reported_not_counted_as_picks(
            self, tmp_path, monkeypatch):
        self._stub_collector(tmp_path, monkeypatch, ["football"])
        monkeypatch.setattr(fsb, "run_evaluator", lambda *a, **k: {
            "run_id": "BLOCKED", "run_status": "SHADOW_RUN_BLOCKED",
            "artifact_dir": str(tmp_path)})
        entry = fsb.run_short_notice_for_date(TARGET_DATE, tmp_path)
        assert entry["status"] == "BLOCKED"
        assert entry["r1_count"] == 0


# ===========================================================================
# Group 2: settlement stays inside its own tree
# ===========================================================================


class TestSettlementTreeScoping:
    as_of = dt.date(2026, 9, 28)

    def test_backlog_scans_only_the_requested_tree(self, tmp_path):
        _write_run(tmp_path, "shadow", TARGET_DATE, "aaaa", [])
        _write_run(tmp_path, "shadow_short_notice", TARGET_DATE, "bbbb", [])
        standard = fsb.find_settleable_dates(tmp_path, as_of=self.as_of)
        short = fsb.find_settleable_dates(
            tmp_path, as_of=self.as_of,
            shadow_subdir=fsb.SHORT_NOTICE_SHADOW_SUBDIR)
        assert standard == [(TARGET_DATE, "aaaa")]
        assert short == [(TARGET_DATE, "bbbb")]

    def test_settled_short_notice_run_is_not_re_settled(self, tmp_path):
        _write_run(tmp_path, "shadow_short_notice", TARGET_DATE, "bbbb", [],
                   settled=True)
        assert fsb.find_settleable_dates(
            tmp_path, as_of=self.as_of,
            shadow_subdir=fsb.SHORT_NOTICE_SHADOW_SUBDIR) == []

    def test_d_plus_1_rule_holds_on_the_short_notice_tree(self, tmp_path):
        _write_run(tmp_path, "shadow_short_notice", TARGET_DATE, "bbbb", [])
        assert fsb.find_settleable_dates(
            tmp_path, as_of=dt.date(2026, 9, 26),
            shadow_subdir=fsb.SHORT_NOTICE_SHADOW_SUBDIR) == []

    def test_sports_are_read_from_the_requested_tree(self, tmp_path):
        _write_run(tmp_path, "shadow_short_notice", TARGET_DATE, "bbbb",
                   [_selection("basketball", 1), _selection("hockey", 1)])
        assert fsb._sports_in_run(
            TARGET_DATE, "bbbb", tmp_path,
            shadow_subdir=fsb.SHORT_NOTICE_SHADOW_SUBDIR) == [
                "basketball", "hockey"]
        assert fsb._sports_in_run(TARGET_DATE, "bbbb", tmp_path) == []

    def test_settle_run_receives_the_subdir(self, tmp_path, monkeypatch):
        _write_run(tmp_path, "shadow_short_notice", TARGET_DATE, "bbbb",
                   [_selection("hockey", 1)])
        seen: dict = {}

        class _Result:
            settlement_artifact_path = "p"
            settlement_artifact_sha256 = "s"
            summary = {"primary_hit_rate": None}

        def _fake_settle(**kwargs):
            seen.update(kwargs)
            return _Result()

        monkeypatch.setattr("slumdog.shadow_settle.settle_run", _fake_settle)
        result = fsb.run_settlement_for_date(
            TARGET_DATE, "bbbb", tmp_path,
            shadow_subdir=fsb.SHORT_NOTICE_SHADOW_SUBDIR)
        assert result["status"] == "SETTLED"
        assert seen["shadow_subdir"] == fsb.SHORT_NOTICE_SHADOW_SUBDIR

    def test_default_callers_still_get_the_frozen_tree(self, tmp_path, monkeypatch):
        _write_run(tmp_path, "shadow", TARGET_DATE, "aaaa",
                   [_selection("football", 1)])
        seen: dict = {}

        class _Result:
            settlement_artifact_path = "p"
            settlement_artifact_sha256 = "s"
            summary = {"primary_hit_rate": None}

        def _fake_settle(**kwargs):
            seen.update(kwargs)
            return _Result()

        monkeypatch.setattr("slumdog.shadow_settle.settle_run", _fake_settle)
        fsb.run_settlement_for_date(TARGET_DATE, "aaaa", tmp_path)
        assert seen["shadow_subdir"] == "shadow"


# ===========================================================================
# Group 3: evidence persistence coverage
# ===========================================================================


class TestPersistedEvidenceDeclaration:
    def test_declaration_is_non_empty_and_well_formed(self):
        assert fsb.PERSISTED_EVIDENCE
        for entry in fsb.PERSISTED_EVIDENCE:
            root, filename = entry
            assert root.startswith("data/")
            assert filename and "/" not in filename

    def test_every_artifact_the_short_notice_stage_writes_is_declared(self):
        declared = set(fsb.PERSISTED_EVIDENCE)
        for filename in ("shadow_selections.json", "manifest.json",
                         "settlement.json", "settlement.json.sha256"):
            assert ("data/reports/shadow_short_notice", filename) in declared

    def test_refresh_delta_artifacts_are_declared(self):
        roots = {r for r, _ in fsb.PERSISTED_EVIDENCE}
        names = [f for r, f in fsb.PERSISTED_EVIDENCE
                 if r == "data/reports/shadow"]
        assert "data/reports/shadow" in roots
        assert any(n.startswith("selections_delta_") for n in names)
        assert any(n.startswith("settlement_delta_") for n in names)

    def test_no_raw_bodies_or_archives_are_ever_declared(self):
        """The scoped git waiver covers small JSON/text only."""
        for _, filename in fsb.PERSISTED_EVIDENCE:
            assert not filename.endswith(".tar.gz")
            assert not filename.endswith(".txt")
            assert not filename.startswith("history_")


class TestWorkflowGlobCoverage:
    def test_parser_reads_find_name_groups(self):
        text = (
            "          find data/reports/shadow -type f \\( -name 'a.json' "
            "-o -name 'b_*.json' \\) | xargs -r git add -f\n")
        assert parse_persist_rules(text) == [
            ("data/reports/shadow", "a.json"),
            ("data/reports/shadow", "b_*.json"),
        ]

    def test_parser_reads_direct_git_add_globs(self):
        text = "          git add -f data/reports/capture_*.json 2>/dev/null\n"
        assert parse_persist_rules(text) == [
            ("data/reports", "capture_*.json")]

    def test_coverage_requires_both_root_and_name_to_match(self):
        rules = [("data/reports/shadow", "settlement_*.json")]
        assert is_covered("data/reports/shadow", "settlement_x.json", rules)
        assert is_covered(
            "data/reports/shadow/2026-09-26", "settlement_x.json", rules)
        assert not is_covered(
            "data/reports/shadow_short_notice", "settlement_x.json", rules)
        assert not is_covered("data/reports/shadow", "other.json", rules)

    def test_sibling_tree_is_not_covered_by_a_prefix_name_collision(self):
        """``find data/reports/shadow`` must NOT be read as covering
        ``data/reports/shadow_short_notice`` — string-prefix matching without
        a path separator would silently claim coverage that does not exist."""
        rules = parse_persist_rules(
            "find data/reports/shadow -type f \\( -name 'manifest.json' \\) "
            "| xargs -r git add -f")
        assert not is_covered(
            "data/reports/shadow_short_notice", "manifest.json", rules)

    @pytest.mark.skipif(not WORKFLOW_PATH.is_file(), reason="workflow absent")
    def test_current_gap_is_a_subset_of_the_documented_gap(self):
        """The workflow is owner-authored, so this asserts the gap may only
        SHRINK. It stays green when the owner pastes the fix, and fails when
        a new artifact type is added without declaring it as pending."""
        missing = set(uncovered_evidence(
            WORKFLOW_PATH.read_text(),
            [tuple(e) for e in fsb.PERSISTED_EVIDENCE]))
        unexpected = missing - fsb.KNOWN_UNCOVERED_PENDING_OWNER_PASTE
        assert not unexpected, (
            "new evidence artifacts would be discarded by the workflow "
            f"persist step: {sorted(unexpected)}")

    def test_documented_gap_only_lists_declared_artifacts(self):
        declared = set(fsb.PERSISTED_EVIDENCE)
        assert fsb.KNOWN_UNCOVERED_PENDING_OWNER_PASTE <= declared

    @pytest.mark.skipif(not WORKFLOW_PATH.is_file(), reason="workflow absent")
    def test_frozen_track_evidence_is_already_covered(self):
        """Regression guard for the artifacts that ARE committed today."""
        rules = parse_persist_rules(WORKFLOW_PATH.read_text())
        for filename in ("shadow_selections.json", "manifest.json",
                         "settlement.json", "settlement.json.sha256"):
            assert is_covered("data/reports/shadow", filename, rules)
        assert is_covered("data/reports", "capture_2026-09-26.json", rules)
