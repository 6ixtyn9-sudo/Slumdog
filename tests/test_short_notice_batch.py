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

    def _stub_collector(self, tmp_path, monkeypatch, captured_sports: list[str],
                        failures: int = 0):
        reports = tmp_path / "data" / "reports"
        reports.mkdir(parents=True, exist_ok=True)

        class _Collector:
            def __init__(self, **kwargs):
                pass

            def capture_selected(self, target_date, sports=None, force=False,
                                 receipt_name=None, pause_seconds=0):
                (reports / receipt_name).write_text(json.dumps({
                    "target_date": target_date,
                    "captured": [{"sport": s} for s in captured_sports],
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


# ===========================================================================
# Group 4: settlement evidence separation (red-team finding 2026-09-26)
#
# Both tracks can hold a run for the SAME target date, and the driver settles
# both on the same morning. The settlement capture receipt is written under
# data/settlement_evidence/<date>/, so a shared filename would let the second
# track's capture overwrite the committed evidence the first track's
# settlement.json points at.
# ===========================================================================


class TestSettlementEvidenceSeparation:
    def test_receipt_name_is_per_track(self):
        from slumdog.shadow_settle import (
            SHORT_NOTICE_SHADOW_SUBDIR,
            STANDARD_SHADOW_SUBDIR,
            settlement_receipt_name,
        )

        standard = settlement_receipt_name(STANDARD_SHADOW_SUBDIR)
        short_notice = settlement_receipt_name(SHORT_NOTICE_SHADOW_SUBDIR)
        # The standard name is historical and already committed: it must not
        # change, or every prior settlement's pointer goes stale.
        assert standard == "settlement_capture_receipt.json"
        assert short_notice != standard
        assert SHORT_NOTICE_SHADOW_SUBDIR in short_notice

    def test_unknown_tree_is_refused(self):
        from slumdog.shadow_settle import SettlementError, settlement_receipt_name

        with pytest.raises(SettlementError, match="unknown shadow evidence tree"):
            settlement_receipt_name("../../etc")

    def test_short_notice_settlement_writes_its_own_receipt_file(
            self, tmp_path, monkeypatch):
        """The short-notice settlement must not reuse — and therefore must
        not clobber — the standard track's receipt for the same date."""
        import slumdog.shadow_settle as ss

        # Standard track already settled this date: its receipt is on disk.
        evidence = tmp_path / "data" / "settlement_evidence" / TARGET_DATE
        evidence.mkdir(parents=True)
        standard_receipt = evidence / "settlement_capture_receipt.json"
        standard_receipt.write_text(json.dumps({"marker": "standard"}))

        seen: dict = {}

        def _fake_fetch(target_date, repo_root, **kwargs):
            seen.update(kwargs)
            receipt = {
                "target_date": target_date,
                "generated_at": "2026-09-27T06:00:00Z",
                "capture_type": "settlement_evidence",
                "capture_purpose": kwargs.get("capture_purpose"),
                "captured": [], "failures": [],
            }
            name = kwargs.get("receipt_name") or "settlement_capture_receipt.json"
            (Path(repo_root) / "data" / "settlement_evidence" / target_date
             / name).write_text(json.dumps(receipt))
            return receipt

        monkeypatch.setattr(ss, "fetch_settlement_capture", _fake_fetch)
        _write_run(tmp_path, "shadow_short_notice", TARGET_DATE, RUN_ID,
                   [_selection("football", 1)])

        result = ss.settle_run(
            target_date=TARGET_DATE, run_id=RUN_ID, repo_root=tmp_path,
            shadow_subdir="shadow_short_notice",
            settled_at="2026-09-27T06:00:00Z",
        )

        assert seen["receipt_name"] == (
            "settlement_capture_receipt_shadow_short_notice.json")
        # A per-track D+1 capture is still a settlement, not a completion pass.
        assert seen["capture_purpose"] == "settlement"
        # The standard track's committed evidence is untouched.
        assert json.loads(standard_receipt.read_text()) == {"marker": "standard"}
        assert (evidence
                / "settlement_capture_receipt_shadow_short_notice.json").is_file()
        assert result.settlement_receipt_path.endswith(
            "settlement_capture_receipt_shadow_short_notice.json")


# ===========================================================================
# Group 5: request pacing and timezone hold on the same-day capture
# ===========================================================================


class TestShortNoticeCaptureIsPacedAndScoped:
    def test_capture_selected_paces_serial_fetches(self, tmp_path, monkeypatch):
        from slumdog import forebet

        sleeps: list[float] = []
        fetched: list[str] = []
        monkeypatch.setattr(forebet.time, "sleep", lambda s: sleeps.append(s))

        collector = forebet.ForebetCollector(root=tmp_path, workers=1)
        monkeypatch.setattr(
            collector, "_fetch",
            lambda sport, target_date: fetched.append(sport) or forebet.RawCapture(
                sport=sport, target_date=target_date,
                captured_at="2026-09-26T04:00:00Z",
                source_url="https://x.invalid", relay_url="https://r.invalid",
                body_format="html", sha256="0" * 64, bytes=1,
                body_path="data/raw/x.txt", metadata_path="data/raw/x.json",
                route="test"))

        # No football: its capture also triggers the markets fetch, whose
        # own retry backoff would pollute the recorded sleeps.
        collector.capture_selected(
            TARGET_DATE, ["handball", "rugby", "volleyball"],
            force=True, pause_seconds=62)

        assert fetched == ["handball", "rugby", "volleyball"]
        # One pause BETWEEN each pair of requests, never before the first.
        assert sleeps == [62, 62]

    def test_stage_only_captures_sports_it_may_decide_from(
            self, tmp_path, monkeypatch):
        from slumdog.shadow_evaluator import UTC_KICKOFF_PROVEN_SPORTS

        calls: dict = {}

        class _FakeCollector:
            def __init__(self, **kwargs):
                pass

            def capture_selected(self, target_date, sports=None, **kwargs):
                calls["sports"] = sports
                calls["pause_seconds"] = kwargs.get("pause_seconds")
                (tmp_path / "data" / "reports").mkdir(parents=True, exist_ok=True)
                (tmp_path / "data" / "reports" / kwargs["receipt_name"]).write_text(
                    json.dumps({"captured": [], "failures": []}))
                return []

        monkeypatch.setattr(
            "slumdog.forebet.ForebetCollector", _FakeCollector)
        entry = fsb.run_short_notice_for_date(
            TARGET_DATE, tmp_path, pause_seconds=62,
            base_date=dt.date.fromisoformat(TARGET_DATE))

        assert entry["status"] == "NO_CAPTURES"
        assert calls["sports"] == sorted(UTC_KICKOFF_PROVEN_SPORTS)
        assert calls["pause_seconds"] == 62
        # Every sport held back by the timezone finding is named in the receipt
        # entry, so "why is basketball missing" is answerable from evidence.
        assert "basketball" in entry["timezone_hold_sports"]
        assert "football" not in entry["timezone_hold_sports"]
