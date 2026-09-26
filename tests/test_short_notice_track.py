"""SHORT_NOTICE evidence track (owner decision 2026-09-26).

The frozen 24h track anchors its timing gate to ``target_date 00:00 UTC``.
Forebet does not publish basketball / hockey / baseball / tennis / rugby
boards that far ahead, so those sports cannot produce a rank-1 (R1) pick at
all under it — 2026-09-21..26 were football-only. The SHORT_NOTICE track
decides on the event day and proves pre-event status PER EVENT against the
published kickoff.

These tests pin the three properties that make the track trustworthy:

1. it is strictly separate from the 24h record (declaration, artifact tree,
   labels, settlement) and cannot be pooled with it by accident;
2. its per-event gate is fail-closed — no parseable kickoff, no pick; and
3. the 24h track is byte-for-byte unaffected by its existence.
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import io
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from slumdog.shadow_contracts import PreEventRecord
from slumdog.shadow_evaluator import (
    MIN_SHORT_NOTICE_LEAD_MINUTES,
    SHORT_NOTICE_ARTIFACT_ROOT,
    SHORT_NOTICE_DECLARATION_VERSION,
    SHORT_NOTICE_TRACK,
    STANDARD_TRACK,
    ShadowEvaluatorError,
    _timing_classify_short_notice,
    evaluate_from_disk,
    load_shadow_declaration,
    parse_kickoff_utc,
    track_policy,
)
from slumdog.shadow_settle import (
    SHORT_NOTICE_SHADOW_SUBDIR,
    STANDARD_SHADOW_SUBDIR,
    SettlementError,
    load_prediction_run,
    shadow_run_dir,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
STANDARD_DECL = REPO_ROOT / "config" / "shadow_evaluator.json"
SHORT_NOTICE_DECL = REPO_ROOT / "config" / "shadow_evaluator_short_notice.json"
FROZEN_CONFIG = REPO_ROOT / "config" / "research_baselines.json"

TARGET_DATE = "2026-09-26"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_root():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "config").mkdir()
        (root / "data" / "reports").mkdir(parents=True)
        shutil.copy(FROZEN_CONFIG, root / "config" / "research_baselines.json")
        shutil.copy(STANDARD_DECL, root / "config" / "shadow_evaluator.json")
        shutil.copy(
            SHORT_NOTICE_DECL,
            root / "config" / "shadow_evaluator_short_notice.json",
        )
        yield root


def _decl(tmp_root: Path, **timing_overrides) -> Path:
    """Write a short-notice declaration with patched timing fields."""
    obj = json.loads(SHORT_NOTICE_DECL.read_text())
    for key, value in timing_overrides.items():
        if value is None:
            obj["timing_safety"].pop(key, None)
        else:
            obj["timing_safety"][key] = value
    path = tmp_root / "config" / "patched.json"
    path.write_text(json.dumps(obj, indent=2, sort_keys=True))
    return path


def _record(
    event_id: str = "football:1",
    *,
    kickoff: str = f"{TARGET_DATE} 18:00",
    captured_at: str = f"{TARGET_DATE}T04:00:00Z",
    event_date: str = TARGET_DATE,
    p1: str = "Arsenal",
    p2: str = "Liverpool",
) -> PreEventRecord:
    return PreEventRecord(
        event_id=event_id, sport="football", event_date=event_date,
        participant_1=p1, participant_2=p2,
        probability_1=0.50, probability_2=0.40, draw_probability=0.10,
        source_url=f"https://example.invalid/{event_id}",
        raw_sha256=hashlib.sha256(event_id.encode()).hexdigest(),
        captured_at=captured_at,
        body_path=f"data/raw/football/{event_date}/{event_id}.txt",
        route="snapshot", kickoff=kickoff,
    )


def _write_capture(
    tmp_root: Path,
    rows: list[dict],
    *,
    target_date: str = TARGET_DATE,
    captured_at: str = f"{TARGET_DATE}T04:00:00+00:00",
    receipt_name: str | None = None,
) -> Path:
    """Write a football JSON capture body + sidecar + receipt.

    Mirrors the real capture layout (``parsers.parse_football_json`` reads
    ``DATE_BAH`` as the scheduled start), so the kickoff the evaluator gates
    on is the one Forebet actually publishes.
    """
    body = ("<html><body>" + json.dumps([rows, {}]) + "</body></html>").encode()
    body_sha = hashlib.sha256(body).hexdigest()
    body_dir = tmp_root / "data" / "raw" / "football" / target_date
    body_dir.mkdir(parents=True, exist_ok=True)
    stamp = captured_at.replace("-", "").replace(":", "")[:15] + "Z"
    body_path = body_dir / f"{stamp}_{body_sha[:12]}.txt"
    sidecar_path = body_dir / f"{stamp}_{body_sha[:12]}.json"
    body_path.write_bytes(body)
    sidecar = {
        "sport": "football", "target_date": target_date,
        "captured_at": captured_at,
        "source_url": f"https://example.invalid/football/{target_date}",
        "relay_url": f"https://relay.invalid/football/{target_date}",
        "body_format": "json", "sha256": body_sha, "bytes": len(body),
        "body_path": str(body_path.relative_to(tmp_root)),
        "metadata_path": str(sidecar_path.relative_to(tmp_root)),
        "route": "direct",
    }
    sidecar_path.write_text(json.dumps(sidecar, indent=2, sort_keys=True))
    receipt = {
        "target_date": target_date, "generated_at": captured_at,
        "captured": [sidecar], "failures": [], "reused": 0,
        "football_markets": None,
    }
    receipt_path = (tmp_root / "data" / "reports"
                    / (receipt_name or f"capture_{target_date}.json"))
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True))
    return receipt_path


def _write_history(tmp_root: Path) -> Path:
    """Prior settled rows so the frozen R2 rule can actually qualify a pick.

    Six Arsenal, six Liverpool and three Arsenal-vs-Liverpool H2H matches:
    the exact minimum the frozen eligibility spec demands
    (underdog_prior_games >= 5, favorite_prior_games >= 5, h2h_prior_games >= 1).
    """
    rows = []
    for i in range(6):
        rows.append({
            "event_id": f"a{i}", "sport": "football",
            "event_date": f"2024-01-{i + 1:02d}",
            "participant_1": "Arsenal", "participant_2": "Chelsea",
            "winner_index": 1, "score_1": 2.0, "score_2": 1.0,
            "probability_1": 0.55, "probability_2": 0.30,
            "draw_probability": 0.15, "forebet_pick": None,
            "disposition": "SETTLED",
        })
    for i in range(6):
        rows.append({
            "event_id": f"l{i}", "sport": "football",
            "event_date": f"2024-02-{i + 1:02d}",
            "participant_1": "Liverpool", "participant_2": "ManU",
            "winner_index": 1, "score_1": 2.0, "score_2": 0.0,
            "probability_1": 0.50, "probability_2": 0.30,
            "draw_probability": 0.20, "forebet_pick": None,
            "disposition": "SETTLED",
        })
    for i in range(3):
        rows.append({
            "event_id": f"h{i}", "sport": "football",
            "event_date": f"2024-03-{i + 1:02d}",
            "participant_1": "Arsenal", "participant_2": "Liverpool",
            "winner_index": 2, "score_1": 0.0, "score_2": 2.0,
            "probability_1": 0.45, "probability_2": 0.35,
            "draw_probability": 0.20, "forebet_pick": None,
            "disposition": "SETTLED",
        })
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        for row in rows:
            gz.write((json.dumps(row) + "\n").encode("utf-8"))
    path = tmp_root / "data" / "reports" / "history_football.jsonl.gz"
    path.write_bytes(buf.getvalue())
    return path


def _row(event_id: str, kickoff_hhmm: str, *, host="Arsenal", guest="Liverpool"):
    return {
        "id": event_id, "HOST_NAME": host, "GUEST_NAME": guest,
        "Pred_1": "50", "Pred_X": "10", "Pred_2": "40",
        "best_odd_1": None, "best_odd_2": None, "best_odd_X": None,
        "short_tag": "EPL", "DATE_BAH": f"{TARGET_DATE} {kickoff_hhmm}",
        "host_sc_pr": "1", "guest_sc_pr": "1", "goalsavg": "2.5",
        "Host_SC": None, "Guest_SC": None, "comment": "",
    }


# ===========================================================================
# Group 1: declaration contract
# ===========================================================================


class TestShortNoticeDeclaration:
    def test_shipped_declaration_loads_and_resolves_to_the_track(self):
        policy = track_policy(load_shadow_declaration(SHORT_NOTICE_DECL))
        assert policy.name == SHORT_NOTICE_TRACK
        assert policy.is_short_notice
        assert policy.safe_cutoff_offset_hours is None
        assert policy.min_lead_minutes >= MIN_SHORT_NOTICE_LEAD_MINUTES
        assert policy.artifact_root == SHORT_NOTICE_ARTIFACT_ROOT

    def test_standard_declaration_still_resolves_to_the_frozen_track(self):
        policy = track_policy(load_shadow_declaration(STANDARD_DECL))
        assert policy.name == STANDARD_TRACK
        assert policy.safe_cutoff_offset_hours == 24
        assert policy.min_lead_minutes is None
        assert policy.artifact_root == "data/reports/shadow"

    def test_declaration_version_is_distinct(self):
        assert (json.loads(SHORT_NOTICE_DECL.read_text())["declaration_version"]
                == SHORT_NOTICE_DECLARATION_VERSION)

    def test_track_field_must_say_short_notice(self, tmp_root):
        with pytest.raises(ShadowEvaluatorError, match="timing_safety.track"):
            load_shadow_declaration(_decl(tmp_root, track="STANDARD"))

    @pytest.mark.parametrize(
        "field", ["safe_cutoff_offset_hours_utc", "safe_cutoff_offset_hours"])
    def test_cannot_claim_the_frozen_24h_contract(self, tmp_root, field):
        with pytest.raises(ShadowEvaluatorError, match="must be absent"):
            load_shadow_declaration(_decl(tmp_root, **{field: 24}))

    @pytest.mark.parametrize("lead", [29, 0, -120, 1441])
    def test_lead_outside_bounds_is_refused(self, tmp_root, lead):
        with pytest.raises(ShadowEvaluatorError, match="min_lead_minutes"):
            load_shadow_declaration(
                _decl(tmp_root, min_lead_minutes_before_kickoff=lead))

    @pytest.mark.parametrize("lead", ["120", 120.0, True, None])
    def test_non_integer_lead_is_refused(self, tmp_root, lead):
        with pytest.raises(ShadowEvaluatorError, match="min_lead_minutes"):
            load_shadow_declaration(
                _decl(tmp_root, min_lead_minutes_before_kickoff=lead))

    @pytest.mark.parametrize("flag", [
        "require_parsed_kickoff",
        "refuse_event_without_parsed_kickoff",
        "never_pooled_with_standard_track",
    ])
    def test_safety_flags_must_be_true(self, tmp_root, flag):
        with pytest.raises(ShadowEvaluatorError, match=flag):
            load_shadow_declaration(_decl(tmp_root, **{flag: False}))

    def test_artifact_root_must_be_the_short_notice_tree(self, tmp_root):
        obj = json.loads(SHORT_NOTICE_DECL.read_text())
        obj["artifact_path"]["root"] = "data/reports/shadow"
        path = tmp_root / "config" / "wrong_root.json"
        path.write_text(json.dumps(obj))
        with pytest.raises(ShadowEvaluatorError, match="artifact_path.root"):
            load_shadow_declaration(path)

    def test_standard_declaration_may_not_write_into_the_short_notice_tree(
            self, tmp_root):
        obj = json.loads(STANDARD_DECL.read_text())
        obj["artifact_path"]["root"] = SHORT_NOTICE_ARTIFACT_ROOT
        path = tmp_root / "config" / "cross.json"
        path.write_text(json.dumps(obj))
        with pytest.raises(ShadowEvaluatorError, match="reserved"):
            load_shadow_declaration(path)

    def test_authorizations_remain_fail_closed(self):
        auth = load_shadow_declaration(SHORT_NOTICE_DECL)["authorizations"]
        assert auth["shadow_evaluation_authorized"] is True
        for gate in ("production_authorized", "shortlist_policy_authorized",
                     "training_authorized", "threshold_optimization_authorized"):
            assert auth[gate] is False

    def test_rule_source_is_the_same_frozen_r2_and_r1(self):
        decl = load_shadow_declaration(SHORT_NOTICE_DECL)
        standard = load_shadow_declaration(STANDARD_DECL)
        assert decl["rule"] == standard["rule"]
        assert decl["anti_tuning"] == standard["anti_tuning"]
        assert decl["cohort_policy"] == standard["cohort_policy"]


# ===========================================================================
# Group 2: kickoff parsing (fail-closed)
# ===========================================================================


class TestParseKickoffUtc:
    @pytest.mark.parametrize("text,expected", [
        ("26/09/2026 18:00", dt.datetime(2026, 9, 26, 18, 0, tzinfo=dt.timezone.utc)),
        ("2026-09-26 18:00", dt.datetime(2026, 9, 26, 18, 0, tzinfo=dt.timezone.utc)),
        ("2026-09-26T18:30", dt.datetime(2026, 9, 26, 18, 30, tzinfo=dt.timezone.utc)),
        ("26/09/2026 00:05 extra text", dt.datetime(2026, 9, 26, 0, 5, tzinfo=dt.timezone.utc)),
    ])
    def test_both_published_shapes_parse_as_utc(self, text, expected):
        assert parse_kickoff_utc(text) == expected

    @pytest.mark.parametrize("text", [
        "", "   ", None, "2026-09-26", "26/09/2026", "tomorrow",
        "32/09/2026 18:00", "2026-13-26 18:00", "2026-09-26 25:00",
    ])
    def test_unusable_values_return_none_never_a_guess(self, text):
        assert parse_kickoff_utc(text) is None


# ===========================================================================
# Group 3: the per-event timing gate
# ===========================================================================


class TestShortNoticeTimingGate:
    decision = dt.datetime(2026, 9, 26, 4, 0, tzinfo=dt.timezone.utc)

    def _classify(self, records, lead=120):
        return _timing_classify_short_notice(
            records, target_date=TARGET_DATE,
            decision_dt=self.decision, min_lead_minutes=lead,
        )

    def test_event_with_enough_lead_is_admitted(self):
        timed, rejected, malformed, reasons = self._classify([_record()])
        assert len(timed) == 1 and rejected == 0 and malformed == 0
        assert sum(reasons.values()) == 0

    def test_exactly_at_the_lead_boundary_is_admitted(self):
        rec = _record(kickoff=f"{TARGET_DATE} 06:00")  # decision + 120 min
        timed, rejected, _, _ = self._classify([rec])
        assert len(timed) == 1 and rejected == 0

    def test_one_minute_short_of_the_lead_is_refused(self):
        rec = _record(kickoff=f"{TARGET_DATE} 05:59")
        timed, rejected, _, reasons = self._classify([rec])
        assert timed == [] and rejected == 1
        assert reasons["INSUFFICIENT_LEAD_BEFORE_KICKOFF"] == 1

    def test_event_already_started_is_refused(self):
        rec = _record(kickoff=f"{TARGET_DATE} 03:00")
        timed, rejected, _, reasons = self._classify([rec])
        assert timed == [] and rejected == 1
        assert reasons["INSUFFICIENT_LEAD_BEFORE_KICKOFF"] == 1

    def test_missing_kickoff_is_refused_not_assumed_distant(self):
        timed, rejected, _, reasons = self._classify([_record(kickoff="")])
        assert timed == [] and rejected == 1
        assert reasons["KICKOFF_MISSING_OR_UNPARSEABLE"] == 1

    def test_unparseable_kickoff_is_refused(self):
        timed, _, _, reasons = self._classify([_record(kickoff="later today")])
        assert timed == []
        assert reasons["KICKOFF_MISSING_OR_UNPARSEABLE"] == 1

    def test_kickoff_on_another_date_is_refused(self):
        rec = _record(kickoff="2026-09-27 18:00")
        timed, _, _, reasons = self._classify([rec])
        assert timed == []
        assert reasons["KICKOFF_NOT_ON_TARGET_DATE"] == 1

    def test_capture_from_after_the_decision_is_refused(self):
        rec = _record(captured_at=f"{TARGET_DATE}T05:00:00Z")
        timed, _, _, reasons = self._classify([rec])
        assert timed == []
        assert reasons["CAPTURED_AFTER_DECISION"] == 1

    def test_unparseable_captured_at_is_refused(self):
        rec = _record(captured_at="not-a-timestamp")
        timed, _, _, reasons = self._classify([rec])
        assert timed == []
        assert reasons["CAPTURED_AT_UNPARSEABLE"] == 1

    def test_accounting_balances_across_every_bucket(self):
        records = [
            _record("football:ok"),
            _record("football:late", kickoff=f"{TARGET_DATE} 04:30"),
            _record("football:nokick", kickoff=""),
            _record("football:otherday", kickoff="2026-09-27 18:00"),
            _record("football:future", captured_at=f"{TARGET_DATE}T09:00:00Z"),
        ]
        timed, rejected, malformed, reasons = self._classify(records)
        assert len(timed) + rejected + malformed == len(records)
        assert rejected == sum(reasons.values()) == 4

    def test_a_longer_declared_lead_admits_strictly_less(self):
        records = [_record("football:a", kickoff=f"{TARGET_DATE} 07:00")]
        assert len(self._classify(records, lead=120)[0]) == 1
        assert len(self._classify(records, lead=240)[0]) == 0


# ===========================================================================
# Group 4: end-to-end runs
# ===========================================================================


class TestShortNoticeEndToEnd:
    decision = dt.datetime(2026, 9, 26, 4, 0, tzinfo=dt.timezone.utc)

    def _run(self, tmp_root, rows, *, config="shadow_evaluator_short_notice.json",
             decision=None, receipt_name=None, captured_at=None):
        receipt = _write_capture(
            tmp_root, rows, receipt_name=receipt_name,
            captured_at=captured_at or f"{TARGET_DATE}T04:00:00+00:00")
        history = _write_history(tmp_root)
        return evaluate_from_disk(
            target_date=TARGET_DATE,
            capture_receipt_path=receipt,
            declaration_path=tmp_root / "config" / config,
            repo_root=tmp_root,
            history_paths=[history],
            decision_clock=decision or self.decision,
        )

    def test_same_day_decision_is_blocked_on_the_frozen_track(self, tmp_root):
        """The gap being closed: a 2026-09-26 decision for 2026-09-26 is
        already past the frozen cutoff (2026-09-25 00:00 UTC)."""
        result = self._run(
            tmp_root, [_row("1", "18:00")], config="shadow_evaluator.json")
        assert result.run_status == "SHADOW_RUN_BLOCKED"
        assert result.manifest["block_reason"] == (
            "DECISION_COMMITTED_AT_AFTER_SAFE_CUTOFF")

    def test_same_capture_produces_a_real_pick_on_the_short_notice_track(
            self, tmp_root):
        result = self._run(tmp_root, [_row("1", "18:00")])
        assert result.run_status == "SHADOW_SELECTIONS_EMITTED"
        assert result.payload["track"] == SHORT_NOTICE_TRACK
        primaries = [s for s in result.payload["selections"]
                     if s["rank_within_sport_day"] == 1]
        assert len(primaries) == 1
        assert primaries[0]["status"] == "PRIMARY_SHADOW_SELECTION"
        # Same frozen rule as the 24h track: Forebet's underdog, not the
        # favourite, and never a draw.
        assert primaries[0]["underdog_index"] == 2

    def test_artifacts_land_in_the_separate_tree_only(self, tmp_root):
        result = self._run(tmp_root, [_row("1", "18:00")])
        artifact_dir = Path(result.artifact_dir)
        assert SHORT_NOTICE_ARTIFACT_ROOT.split("/")[-1] in artifact_dir.parts
        assert not (tmp_root / "data" / "reports" / "shadow"
                    / TARGET_DATE).exists()

    def test_payload_and_manifest_are_loudly_labelled(self, tmp_root):
        result = self._run(tmp_root, [_row("1", "18:00")])
        contract = result.payload["timing_contract"]
        assert contract["track"] == SHORT_NOTICE_TRACK
        assert contract["satisfies_frozen_24h_contract"] is False
        assert contract["never_pooled_with_standard_track"] is True
        assert contract["min_lead_minutes_before_kickoff"] == 120
        assert result.manifest["track"] == SHORT_NOTICE_TRACK
        assert result.manifest["timing_contract"] == contract

    def test_manifest_reports_why_events_were_refused(self, tmp_root):
        rows = [
            _row("1", "18:00"),
            _row("2", "04:30", host="Chelsea", guest="Spurs"),
            _row("3", "05:00", host="Leeds", guest="Everton"),
        ]
        result = self._run(tmp_root, rows)
        rejections = result.manifest["short_notice_timing_rejections"]
        assert rejections["INSUFFICIENT_LEAD_BEFORE_KICKOFF"] == 2
        assert rejections["KICKOFF_MISSING_OR_UNPARSEABLE"] == 0

    def test_a_board_of_imminent_kickoffs_yields_no_selection(self, tmp_root):
        rows = [_row("1", "04:30"), _row("2", "05:00", host="Chelsea", guest="Spurs")]
        result = self._run(tmp_root, rows)
        assert result.run_status == "SHADOW_NO_SELECTION"
        assert result.payload["selections"] == []

    def test_selection_rows_carry_the_kickoff_and_realised_lead(self, tmp_root):
        result = self._run(tmp_root, [_row("1", "18:00")])
        assert result.payload["selections"], "fixture must produce a pick"
        for sel in result.payload["selections"]:
            assert sel["kickoff_utc"] == "2026-09-26T18:00:00Z"
            assert sel["lead_minutes_at_decision"] == 840
            assert sel["lead_minutes_at_decision"] >= 120

    def test_run_safe_cutoff_is_the_earliest_admissible_kickoff(self, tmp_root):
        result = self._run(tmp_root, [_row("1", "18:00")])
        assert result.payload["safe_cutoff_utc"] == "2026-09-26T06:00:00Z"
        assert (result.payload["timing_contract"][
            "earliest_admissible_kickoff_utc"] == "2026-09-26T06:00:00Z")

    def test_decision_after_the_target_day_ends_is_blocked(self, tmp_root):
        result = self._run(
            tmp_root, [_row("1", "18:00")],
            decision=dt.datetime(2026, 9, 27, 1, 0, tzinfo=dt.timezone.utc))
        assert result.run_status == "SHADOW_RUN_BLOCKED"
        assert result.manifest["block_reason"] == (
            "DECISION_COMMITTED_AT_AFTER_TARGET_DATE")
        assert result.manifest["track"] == SHORT_NOTICE_TRACK

    def test_blocked_receipt_states_its_cutoff_semantics(self, tmp_root):
        result = self._run(
            tmp_root, [_row("1", "18:00")],
            decision=dt.datetime(2026, 9, 27, 1, 0, tzinfo=dt.timezone.utc))
        assert result.manifest["safe_cutoff_semantics"] == (
            "earliest_admissible_kickoff_utc")

    def test_digests_differ_from_a_standard_run_over_the_same_capture(
            self, tmp_root):
        """A standard run of the same board (with a legal early decision) and
        a short-notice run must never collide on run_id/input_digest."""
        short = self._run(tmp_root, [_row("1", "18:00")])
        standard = self._run(
            tmp_root, [_row("1", "18:00")],
            config="shadow_evaluator.json",
            decision=dt.datetime(2026, 9, 24, 12, 0, tzinfo=dt.timezone.utc),
            captured_at="2026-09-24T10:00:00+00:00",
            receipt_name=f"capture_std_{TARGET_DATE}.json")
        assert standard.run_status != "SHADOW_RUN_BLOCKED"
        assert short.run_id != standard.run_id
        assert short.manifest["input_digest"] != standard.manifest["input_digest"]

    def test_standard_track_payload_keeps_its_historical_schema(self, tmp_root):
        standard = self._run(
            tmp_root, [_row("1", "18:00")],
            config="shadow_evaluator.json",
            decision=dt.datetime(2026, 9, 24, 12, 0, tzinfo=dt.timezone.utc),
            captured_at="2026-09-24T10:00:00+00:00")
        assert standard.payload["selections"], "fixture must produce a pick"
        assert "track" not in standard.payload
        assert "timing_contract" not in standard.payload
        assert "track" not in standard.manifest
        assert "short_notice_timing_rejections" not in standard.manifest
        for sel in standard.payload["selections"]:
            assert "kickoff_utc" not in sel
            assert "lead_minutes_at_decision" not in sel
        assert standard.payload["safe_cutoff_utc"] == "2026-09-25T00:00:00Z"

    def test_standard_track_input_digest_has_no_track_keys(self, tmp_root):
        standard = self._run(
            tmp_root, [_row("1", "18:00")],
            config="shadow_evaluator.json",
            decision=dt.datetime(2026, 9, 24, 12, 0, tzinfo=dt.timezone.utc),
            captured_at="2026-09-24T10:00:00+00:00")
        provenance = standard.manifest["input_provenance"]
        assert "timing_track" not in provenance
        assert "min_lead_minutes_before_kickoff" not in provenance


# ===========================================================================
# Group 5: settlement tree separation
# ===========================================================================


class TestSettlementTreeSeparation:
    def test_run_dir_resolves_per_tree(self, tmp_root):
        standard = shadow_run_dir(tmp_root, TARGET_DATE, "abc")
        short = shadow_run_dir(
            tmp_root, TARGET_DATE, "abc", SHORT_NOTICE_SHADOW_SUBDIR)
        assert standard.parts[-3] == STANDARD_SHADOW_SUBDIR
        assert short.parts[-3] == SHORT_NOTICE_SHADOW_SUBDIR
        assert standard != short

    def test_unknown_tree_is_refused(self, tmp_root):
        with pytest.raises(SettlementError, match="unknown shadow evidence tree"):
            shadow_run_dir(tmp_root, TARGET_DATE, "abc", "shadow_experimental")

    def test_loader_reads_the_requested_tree_only(self, tmp_root):
        run_id = "0123456789abcdef"
        run_dir = shadow_run_dir(
            tmp_root, TARGET_DATE, run_id, SHORT_NOTICE_SHADOW_SUBDIR)
        run_dir.mkdir(parents=True)
        payload = {"run_id": run_id, "target_date": TARGET_DATE,
                   "track": SHORT_NOTICE_TRACK, "selections": []}
        (run_dir / "shadow_selections.json").write_text(json.dumps(payload))
        (run_dir / "manifest.json").write_text(
            json.dumps({"run_id": run_id, "considered_pool": []}))

        selections, _ = load_prediction_run(
            TARGET_DATE, run_id, tmp_root,
            shadow_subdir=SHORT_NOTICE_SHADOW_SUBDIR)
        assert selections["track"] == SHORT_NOTICE_TRACK

        with pytest.raises(SettlementError, match="not found"):
            load_prediction_run(TARGET_DATE, run_id, tmp_root)
