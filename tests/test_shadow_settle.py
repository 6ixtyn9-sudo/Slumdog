"""Focused tests for the shadow settlement module (P1).

Tests are entirely synthetic — no network, no real Forebet access, no
real prediction runs. Each test constructs minimal fixtures in a temp
directory and verifies the settlement pipeline end-to-end.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from slumdog.shadow_settle import (
    GRADE_FAILURE,
    GRADE_SUCCESS,
    GRADE_UNRESOLVED,
    GRADE_UNSETTLED,
    SettlementError,
    _build_event_index,
    compute_rolling_summary,
    grade_all_entries,
    grade_underdog_win,
    load_prediction_run,
    settle_run,
    write_settlement_artifact,
)


# ---------------------------------------------------------------------------
# Grading contract (frozen — these tests are the contract)
# ---------------------------------------------------------------------------


class TestGradeUnderdogWin:
    """Verify the frozen grading contract."""

    def test_underdog_wins_football(self):
        assert grade_underdog_win(
            underdog_index=2, winner_index=2,
            disposition="SETTLED", sport="football",
        ) == GRADE_SUCCESS

    def test_favorite_wins_football(self):
        assert grade_underdog_win(
            underdog_index=2, winner_index=1,
            disposition="SETTLED", sport="football",
        ) == GRADE_FAILURE

    def test_draw_football_is_failure(self):
        assert grade_underdog_win(
            underdog_index=1, winner_index=0,
            disposition="SETTLED", sport="football",
        ) == GRADE_FAILURE

    def test_draw_basketball_is_unresolved(self):
        # Basketball is two-way; draw is anomalous.
        assert grade_underdog_win(
            underdog_index=1, winner_index=0,
            disposition="SETTLED", sport="basketball",
        ) == GRADE_UNRESOLVED

    def test_void_is_unresolved(self):
        assert grade_underdog_win(
            underdog_index=1, winner_index=0,
            disposition="VOID", sport="football",
        ) == GRADE_UNRESOLVED

    def test_no_contest_is_unresolved(self):
        assert grade_underdog_win(
            underdog_index=2, winner_index=1,
            disposition="NO_CONTEST", sport="tennis",
        ) == GRADE_UNRESOLVED

    def test_cancelled_is_unresolved(self):
        assert grade_underdog_win(
            underdog_index=1, winner_index=2,
            disposition="CANCELLED", sport="football",
        ) == GRADE_UNRESOLVED

    def test_settled_draw_disposition_is_failure(self):
        assert grade_underdog_win(
            underdog_index=1, winner_index=0,
            disposition="SETTLED_DRAW", sport="cricket",
        ) == GRADE_FAILURE

    def test_underdog_wins_mma(self):
        assert grade_underdog_win(
            underdog_index=1, winner_index=1,
            disposition="SETTLED", sport="mma",
        ) == GRADE_SUCCESS

    def test_underdog_index_2_wins(self):
        assert grade_underdog_win(
            underdog_index=2, winner_index=2,
            disposition="SETTLED", sport="hockey",
        ) == GRADE_SUCCESS

    def test_empty_disposition_defaults_to_settled(self):
        assert grade_underdog_win(
            underdog_index=1, winner_index=1,
            disposition="", sport="football",
        ) == GRADE_SUCCESS

    def test_none_disposition_defaults_to_settled(self):
        assert grade_underdog_win(
            underdog_index=2, winner_index=1,
            disposition=None, sport="tennis",
        ) == GRADE_FAILURE

    def test_handball_draw_is_failure(self):
        assert grade_underdog_win(
            underdog_index=1, winner_index=0,
            disposition="SETTLED", sport="handball",
        ) == GRADE_FAILURE

    def test_cricket_void_is_unresolved(self):
        assert grade_underdog_win(
            underdog_index=2, winner_index=0,
            disposition="VOID", sport="cricket",
        ) == GRADE_UNRESOLVED


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_prediction_run(
    tmpdir: Path,
    target_date: str = "2026-09-05",
    run_id: str = "abcd1234efgh5678",
    *,
    selections: list[dict] | None = None,
    considered_pool: list[dict] | None = None,
) -> tuple[dict, dict]:
    """Create a minimal synthetic prediction run."""
    run_dir = tmpdir / "data" / "reports" / "shadow" / target_date / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    if selections is None:
        selections = [
            {
                "sport": "football",
                "event_id": "football:12345",
                "event_date": target_date,
                "rank_within_sport_day": 1,
                "status": "PRIMARY_SHADOW_SELECTION",
                "favorite_index": 1,
                "underdog_index": 2,
                "favorite_probability": 0.60,
                "underdog_probability": 0.20,
                "probability_gap": 0.40,
                "draw_probability": 0.20,
                "features": {},
                "missingness": {},
                "run_id": run_id,
            },
            {
                "sport": "football",
                "event_id": "football:12346",
                "event_date": target_date,
                "rank_within_sport_day": 2,
                "status": "TOP3_EVALUATION_COHORT",
                "favorite_index": 1,
                "underdog_index": 2,
                "favorite_probability": 0.55,
                "underdog_probability": 0.25,
                "probability_gap": 0.30,
                "draw_probability": 0.20,
                "features": {},
                "missingness": {},
                "run_id": run_id,
            },
            {
                "sport": "basketball",
                "event_id": "basketball:99001",
                "event_date": target_date,
                "rank_within_sport_day": 1,
                "status": "PRIMARY_SHADOW_SELECTION",
                "favorite_index": 1,
                "underdog_index": 2,
                "favorite_probability": 0.65,
                "underdog_probability": 0.35,
                "probability_gap": 0.30,
                "draw_probability": None,
                "features": {},
                "missingness": {},
                "run_id": run_id,
            },
        ]
    if considered_pool is None:
        considered_pool = [
            {
                "sport": "football",
                "event_id": "football:12347",
                "event_date": target_date,
                "considered_status": "ELIGIBLE_RANKED_BEYOND_TOP3",
                "eligible": True,
                "rank_within_sport_day": 4,
                "underdog_index": 2,
                "underdog_probability": 0.18,
                "favorite_index": 1,
                "favorite_probability": 0.62,
            },
        ]

    capture_record_tuples = [
        ("football", "football:12345", target_date, "Home FC", "Away United",
         "0.60", "0.20", "0.20", "abc123", "2026-09-03T10:00:00Z",
         "data/raw/football/2026-09-05/body.txt",
         "https://forebet.com/en/football/12345", "relay"),
        ("football", "football:12346", target_date, "City FC", "Town SC",
         "0.55", "0.25", "0.20", "def456", "2026-09-03T10:00:00Z",
         "data/raw/football/2026-09-05/body.txt",
         "https://forebet.com/en/football/12346", "relay"),
        ("basketball", "basketball:99001", target_date, "Team Alpha", "Team Beta",
         "0.65", "0.35", "None", "ghi789", "2026-09-03T10:00:00Z",
         "data/raw/basketball/2026-09-05/body.txt",
         "https://forebet.com/en/basketball/99001", "relay"),
        ("football", "football:12347", target_date, "East FC", "West SC",
         "0.62", "0.18", "0.20", "jkl012", "2026-09-03T10:00:00Z",
         "data/raw/football/2026-09-05/body.txt",
         "https://forebet.com/en/football/12347", "relay"),
    ]

    payload = {
        "run_id": run_id,
        "target_date": target_date,
        "selections": selections,
        "sport_day_summary": [],
    }
    manifest = {
        "run_id": run_id,
        "target_date": target_date,
        "considered_pool": considered_pool,
        "input_provenance": {
            "capture_record_tuples": capture_record_tuples,
        },
    }
    (run_dir / "shadow_selections.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True)
    )
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True)
    )
    return payload, manifest


def _make_settlement_receipt(
    tmpdir: Path,
    target_date: str = "2026-09-05",
    settled_events: list[dict] | None = None,
) -> dict:
    """Create a synthetic settlement capture receipt."""
    evidence_dir = tmpdir / "data" / "settlement_evidence" / target_date
    evidence_dir.mkdir(parents=True, exist_ok=True)

    if settled_events is None:
        settled_events = [
            {
                "event_id": "football:12345",
                "sport": "football",
                "event_date": target_date,
                "participant_1": "Home FC",
                "participant_2": "Away United",
                "winner_index": 2,  # underdog wins!
                "score_1": 1.0,
                "score_2": 2.0,
                "probability_1": 0.60,
                "probability_2": 0.20,
                "draw_probability": 0.20,
                "forebet_pick": 1,
                "disposition": "SETTLED",
            },
            {
                "event_id": "football:12346",
                "sport": "football",
                "event_date": target_date,
                "participant_1": "City FC",
                "participant_2": "Town SC",
                "winner_index": 0,  # draw
                "score_1": 1.0,
                "score_2": 1.0,
                "probability_1": 0.55,
                "probability_2": 0.25,
                "draw_probability": 0.20,
                "forebet_pick": 1,
                "disposition": "SETTLED",
            },
            {
                "event_id": "basketball:99001",
                "sport": "basketball",
                "event_date": target_date,
                "participant_1": "Team Alpha",
                "participant_2": "Team Beta",
                "winner_index": 1,  # favorite wins
                "score_1": 100.0,
                "score_2": 95.0,
                "probability_1": 0.65,
                "probability_2": 0.35,
                "draw_probability": None,
                "forebet_pick": 1,
                "disposition": "SETTLED",
            },
            {
                "event_id": "football:12347",
                "sport": "football",
                "event_date": target_date,
                "participant_1": "East FC",
                "participant_2": "West SC",
                "winner_index": 2,  # underdog wins!
                "score_1": 0.0,
                "score_2": 3.0,
                "probability_1": 0.62,
                "probability_2": 0.18,
                "draw_probability": 0.20,
                "forebet_pick": 1,
                "disposition": "SETTLED",
            },
        ]

    receipt = {
        "target_date": target_date,
        "generated_at": "2026-09-06T08:00:00Z",
        "capture_type": "settlement_evidence",
        "captured": [],
        "failures": [],
        "_settled_events": settled_events,
    }
    receipt_path = evidence_dir / "settlement_capture_receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True))
    return receipt


# ---------------------------------------------------------------------------
# Prediction run loading
# ---------------------------------------------------------------------------


class TestLoadPredictionRun:
    def test_loads_valid_run(self, tmp_path):
        _make_prediction_run(tmp_path)
        sel, man = load_prediction_run("2026-09-05", "abcd1234efgh5678", tmp_path)
        assert sel["run_id"] == "abcd1234efgh5678"
        assert man["run_id"] == "abcd1234efgh5678"
        assert len(sel["selections"]) == 3

    def test_missing_run_dir_raises(self, tmp_path):
        with pytest.raises(SettlementError, match="not found"):
            load_prediction_run("2026-09-05", "nonexistent", tmp_path)

    def test_run_id_mismatch_raises(self, tmp_path):
        _make_prediction_run(tmp_path)
        with pytest.raises(SettlementError, match="mismatch"):
            load_prediction_run("2026-09-05", "wrong_id_here_00", tmp_path)


# ---------------------------------------------------------------------------
# Event index
# ---------------------------------------------------------------------------


class TestBuildEventIndex:
    def test_includes_selections_and_pool(self, tmp_path):
        sel, man = _make_prediction_run(tmp_path)
        index = _build_event_index(sel, man)
        assert "football:football:12345:2026-09-05" in index
        assert "basketball:basketball:99001:2026-09-05" in index
        assert "football:football:12347:2026-09-05" in index

    def test_r4plus_from_pool(self, tmp_path):
        sel, man = _make_prediction_run(tmp_path)
        index = _build_event_index(sel, man)
        r4 = index.get("football:football:12347:2026-09-05")
        assert r4 is not None
        assert r4["_source"] == "considered_pool"

    def test_selections_not_duplicated(self, tmp_path):
        sel, man = _make_prediction_run(tmp_path)
        index = _build_event_index(sel, man)
        # Selections appear as "selections" source
        for key, entry in index.items():
            if entry["_source"] == "selections":
                assert entry["status"] in (
                    "PRIMARY_SHADOW_SELECTION",
                    "TOP3_EVALUATION_COHORT",
                )


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------


class TestGradeAllEntries:
    def test_grades_all_entries(self, tmp_path):
        sel, man = _make_prediction_run(tmp_path)
        receipt = _make_settlement_receipt(tmp_path)
        from slumdog.contracts import SettledEvent
        settled = [SettledEvent(**e) for e in receipt["_settled_events"]]
        index = _build_event_index(sel, man)
        grades = grade_all_entries(index, settled, sel, man)
        assert len(grades) == 4  # 3 selections + 1 pool

    def test_underdog_win_graded_success(self, tmp_path):
        sel, man = _make_prediction_run(tmp_path)
        receipt = _make_settlement_receipt(tmp_path)
        from slumdog.contracts import SettledEvent
        settled = [SettledEvent(**e) for e in receipt["_settled_events"]]
        index = _build_event_index(sel, man)
        grades = grade_all_entries(index, settled, sel, man)
        # football:12345 — underdog (index=2) won → SUCCESS
        g = next(g for g in grades if g.event_id == "football:12345")
        assert g.grade == GRADE_SUCCESS

    def test_draw_graded_failure(self, tmp_path):
        sel, man = _make_prediction_run(tmp_path)
        receipt = _make_settlement_receipt(tmp_path)
        from slumdog.contracts import SettledEvent
        settled = [SettledEvent(**e) for e in receipt["_settled_events"]]
        index = _build_event_index(sel, man)
        grades = grade_all_entries(index, settled, sel, man)
        # football:12346 — draw → FAILURE
        g = next(g for g in grades if g.event_id == "football:12346")
        assert g.grade == GRADE_FAILURE

    def test_favorite_wins_graded_failure(self, tmp_path):
        sel, man = _make_prediction_run(tmp_path)
        receipt = _make_settlement_receipt(tmp_path)
        from slumdog.contracts import SettledEvent
        settled = [SettledEvent(**e) for e in receipt["_settled_events"]]
        index = _build_event_index(sel, man)
        grades = grade_all_entries(index, settled, sel, man)
        # basketball:99001 — favorite (index=1) won, underdog is index=2
        g = next(g for g in grades if g.event_id == "basketball:99001")
        assert g.grade == GRADE_FAILURE

    def test_unsettled_when_not_found(self, tmp_path):
        sel, man = _make_prediction_run(tmp_path)
        # Empty settled list
        index = _build_event_index(sel, man)
        grades = grade_all_entries(index, [], sel, man)
        for g in grades:
            assert g.grade == GRADE_UNSETTLED


# ---------------------------------------------------------------------------
# Rolling summary
# ---------------------------------------------------------------------------


class TestRollingSummary:
    def test_primary_hit_rate(self, tmp_path):
        sel, man = _make_prediction_run(tmp_path)
        receipt = _make_settlement_receipt(tmp_path)
        from slumdog.contracts import SettledEvent
        settled = [SettledEvent(**e) for e in receipt["_settled_events"]]
        index = _build_event_index(sel, man)
        grades = grade_all_entries(index, settled, sel, man)
        summary = compute_rolling_summary(grades)
        # Primary (rank 1): football:12345 SUCCESS, basketball:99001 FAILURE
        primary = summary["primary_hit_rate"]
        assert primary["n"] == 2
        assert primary["successes"] == 1
        assert primary["hit_rate"] == 0.5

    def test_per_rank_breakdown(self, tmp_path):
        sel, man = _make_prediction_run(tmp_path)
        receipt = _make_settlement_receipt(tmp_path)
        from slumdog.contracts import SettledEvent
        settled = [SettledEvent(**e) for e in receipt["_settled_events"]]
        index = _build_event_index(sel, man)
        grades = grade_all_entries(index, settled, sel, man)
        summary = compute_rolling_summary(grades)
        assert "1" in summary["per_rank"]
        assert "2" in summary["per_rank"]
        assert "4" in summary["per_rank"]

    def test_by_underdog_probability_band(self, tmp_path):
        sel, man = _make_prediction_run(tmp_path)
        receipt = _make_settlement_receipt(tmp_path)
        from slumdog.contracts import SettledEvent
        settled = [SettledEvent(**e) for e in receipt["_settled_events"]]
        index = _build_event_index(sel, man)
        grades = grade_all_entries(index, settled, sel, man)
        summary = compute_rolling_summary(grades)
        bands = summary["by_underdog_probability_band"]
        # Underdog probability 0.20 falls in "0.20-0.25" band
        assert "0.20-0.25" in bands
        assert bands["0.20-0.25"]["n"] >= 1

    def test_per_sport_breakdown(self, tmp_path):
        sel, man = _make_prediction_run(tmp_path)
        receipt = _make_settlement_receipt(tmp_path)
        from slumdog.contracts import SettledEvent
        settled = [SettledEvent(**e) for e in receipt["_settled_events"]]
        index = _build_event_index(sel, man)
        grades = grade_all_entries(index, settled, sel, man)
        summary = compute_rolling_summary(grades)
        assert "football" in summary["per_sport"]
        assert "basketball" in summary["per_sport"]

    def test_n_per_cell_always_present(self, tmp_path):
        sel, man = _make_prediction_run(tmp_path)
        receipt = _make_settlement_receipt(tmp_path)
        from slumdog.contracts import SettledEvent
        settled = [SettledEvent(**e) for e in receipt["_settled_events"]]
        index = _build_event_index(sel, man)
        grades = grade_all_entries(index, settled, sel, man)
        summary = compute_rolling_summary(grades)
        for band_label, band in summary["by_underdog_probability_band"].items():
            assert "n" in band, f"band {band_label} missing n"

    def test_cohort_cumulative(self, tmp_path):
        sel, man = _make_prediction_run(tmp_path)
        receipt = _make_settlement_receipt(tmp_path)
        from slumdog.contracts import SettledEvent
        settled = [SettledEvent(**e) for e in receipt["_settled_events"]]
        index = _build_event_index(sel, man)
        grades = grade_all_entries(index, settled, sel, man)
        summary = compute_rolling_summary(grades)
        cohort = summary["cohort_cumulative"]
        assert cohort["sport_days_with_top3"] >= 1


# ---------------------------------------------------------------------------
# Artifact writing
# ---------------------------------------------------------------------------


class TestWriteSettlementArtifact:
    def test_writes_artifact_and_marker(self, tmp_path):
        sel, man = _make_prediction_run(tmp_path)
        receipt = _make_settlement_receipt(tmp_path)
        from slumdog.contracts import SettledEvent
        settled = [SettledEvent(**e) for e in receipt["_settled_events"]]
        index = _build_event_index(sel, man)
        grades = grade_all_entries(index, settled, sel, man)
        summary = compute_rolling_summary(grades)
        result = write_settlement_artifact(
            target_date="2026-09-05",
            run_id="abcd1234efgh5678",
            grades=grades,
            summary=summary,
            settlement_receipt=receipt,
            repo_root=tmp_path,
            settled_at="2026-09-06T08:00:00Z",
        )
        assert Path(result.settlement_artifact_path).is_file()
        assert Path(result.settlement_marker_path).is_file()
        assert result.settlement_artifact_sha256

    def test_no_overwrite(self, tmp_path):
        sel, man = _make_prediction_run(tmp_path)
        receipt = _make_settlement_receipt(tmp_path)
        from slumdog.contracts import SettledEvent
        settled = [SettledEvent(**e) for e in receipt["_settled_events"]]
        index = _build_event_index(sel, man)
        grades = grade_all_entries(index, settled, sel, man)
        summary = compute_rolling_summary(grades)
        write_settlement_artifact(
            target_date="2026-09-05",
            run_id="abcd1234efgh5678",
            grades=grades,
            summary=summary,
            settlement_receipt=receipt,
            repo_root=tmp_path,
        )
        with pytest.raises(SettlementError, match="refusing to overwrite"):
            write_settlement_artifact(
                target_date="2026-09-05",
                run_id="abcd1234efgh5678",
                grades=grades,
                summary=summary,
                settlement_receipt=receipt,
                repo_root=tmp_path,
            )

    def test_marker_sha256_matches(self, tmp_path):
        sel, man = _make_prediction_run(tmp_path)
        receipt = _make_settlement_receipt(tmp_path)
        from slumdog.contracts import SettledEvent
        settled = [SettledEvent(**e) for e in receipt["_settled_events"]]
        index = _build_event_index(sel, man)
        grades = grade_all_entries(index, settled, sel, man)
        summary = compute_rolling_summary(grades)
        result = write_settlement_artifact(
            target_date="2026-09-05",
            run_id="abcd1234efgh5678",
            grades=grades,
            summary=summary,
            settlement_receipt=receipt,
            repo_root=tmp_path,
        )
        marker = Path(result.settlement_marker_path).read_text()
        assert result.settlement_artifact_sha256 in marker

    def test_artifact_schema_version(self, tmp_path):
        sel, man = _make_prediction_run(tmp_path)
        receipt = _make_settlement_receipt(tmp_path)
        from slumdog.contracts import SettledEvent
        settled = [SettledEvent(**e) for e in receipt["_settled_events"]]
        index = _build_event_index(sel, man)
        grades = grade_all_entries(index, settled, sel, man)
        summary = compute_rolling_summary(grades)
        result = write_settlement_artifact(
            target_date="2026-09-05",
            run_id="abcd1234efgh5678",
            grades=grades,
            summary=summary,
            settlement_receipt=receipt,
            repo_root=tmp_path,
        )
        payload = json.loads(Path(result.settlement_artifact_path).read_text())
        assert payload["settlement_schema_version"] == "shadow_settlement_v1"
        assert payload["grading_contract"]["target"] == "UNDERDOG_WIN"

    def test_prediction_run_unmodified(self, tmp_path):
        sel, man = _make_prediction_run(tmp_path)
        run_dir = tmp_path / "data" / "reports" / "shadow" / "2026-09-05" / "abcd1234efgh5678"
        selections_before = (run_dir / "shadow_selections.json").read_bytes()
        manifest_before = (run_dir / "manifest.json").read_bytes()

        receipt = _make_settlement_receipt(tmp_path)
        from slumdog.contracts import SettledEvent
        settled = [SettledEvent(**e) for e in receipt["_settled_events"]]
        index = _build_event_index(sel, man)
        grades = grade_all_entries(index, settled, sel, man)
        summary = compute_rolling_summary(grades)
        write_settlement_artifact(
            target_date="2026-09-05",
            run_id="abcd1234efgh5678",
            grades=grades,
            summary=summary,
            settlement_receipt=receipt,
            repo_root=tmp_path,
        )

        assert (run_dir / "shadow_selections.json").read_bytes() == selections_before
        assert (run_dir / "manifest.json").read_bytes() == manifest_before


# ---------------------------------------------------------------------------
# End-to-end with offline mode
# ---------------------------------------------------------------------------


class TestSettleRunOffline:
    """End-to-end settlement using the offline path (no network)."""

    def _prepare_offline(self, tmp_path):
        """Create prediction run + a settlement receipt that parse_settled_from_receipt can read."""
        _make_prediction_run(tmp_path)
        evidence_dir = tmp_path / "data" / "settlement_evidence" / "2026-09-05"
        evidence_dir.mkdir(parents=True, exist_ok=True)

        # Create a minimal football body that parse_football_settled can parse
        football_body = json.dumps([[
            {
                "id": "12345",
                "DATE_BAH": "2026-09-05 15:00:00",
                "HOST_NAME": "Home FC",
                "GUEST_NAME": "Away United",
                "Host_SC": 1,
                "Guest_SC": 2,
                "Pred_1": 60,
                "Pred_X": 20,
                "Pred_2": 20,
                "host_id": "100",
                "guest_id": "101",
                "short_tag": "TestLeague",
            },
            {
                "id": "12346",
                "DATE_BAH": "2026-09-05 15:00:00",
                "HOST_NAME": "City FC",
                "GUEST_NAME": "Town SC",
                "Host_SC": 1,
                "Guest_SC": 1,
                "Pred_1": 55,
                "Pred_X": 20,
                "Pred_2": 25,
                "host_id": "102",
                "guest_id": "103",
                "short_tag": "TestLeague",
            },
            {
                "id": "12347",
                "DATE_BAH": "2026-09-05 15:00:00",
                "HOST_NAME": "East FC",
                "GUEST_NAME": "West SC",
                "Host_SC": 0,
                "Guest_SC": 3,
                "Pred_1": 62,
                "Pred_X": 20,
                "Pred_2": 18,
                "host_id": "104",
                "guest_id": "105",
                "short_tag": "TestLeague",
            },
        ]]).encode()
        football_dir = evidence_dir / "football"
        football_dir.mkdir(parents=True, exist_ok=True)
        body_path = football_dir / "settlement_body.txt"
        body_path.write_bytes(football_body)

        receipt = {
            "target_date": "2026-09-05",
            "generated_at": "2026-09-06T08:00:00Z",
            "capture_type": "settlement_evidence",
            "captured": [
                {
                    "sport": "football",
                    "target_date": "2026-09-05",
                    "body_path": str(body_path.relative_to(tmp_path)),
                },
            ],
            "failures": [],
        }
        receipt_path = evidence_dir / "settlement_capture_receipt.json"
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True))
        return receipt_path

    def test_settle_offline_succeeds(self, tmp_path):
        receipt_path = self._prepare_offline(tmp_path)
        result = settle_run(
            target_date="2026-09-05",
            run_id="abcd1234efgh5678",
            repo_root=tmp_path,
            offline=True,
            settlement_receipt_path=receipt_path,
            settled_at="2026-09-06T08:00:00Z",
        )
        assert result.target_date == "2026-09-05"
        assert result.run_id == "abcd1234efgh5678"
        assert result.settlement_artifact_sha256
        assert Path(result.settlement_artifact_path).is_file()
        # Verify grades exist
        assert len(result.grades) > 0

    def test_settle_offline_grades_correct(self, tmp_path):
        receipt_path = self._prepare_offline(tmp_path)
        result = settle_run(
            target_date="2026-09-05",
            run_id="abcd1234efgh5678",
            repo_root=tmp_path,
            offline=True,
            settlement_receipt_path=receipt_path,
            settled_at="2026-09-06T08:00:00Z",
        )
        # football:12345 — away wins (winner_index=2, underdog_index=2) → SUCCESS
        g12345 = next(
            (g for g in result.grades if g.event_id == "football:12345"), None,
        )
        assert g12345 is not None
        assert g12345.grade == GRADE_SUCCESS

    def test_settle_refuses_existing(self, tmp_path):
        receipt_path = self._prepare_offline(tmp_path)
        settle_run(
            target_date="2026-09-05",
            run_id="abcd1234efgh5678",
            repo_root=tmp_path,
            offline=True,
            settlement_receipt_path=receipt_path,
        )
        with pytest.raises(SettlementError, match="already exists"):
            settle_run(
                target_date="2026-09-05",
                run_id="abcd1234efgh5678",
                repo_root=tmp_path,
                offline=True,
                settlement_receipt_path=receipt_path,
            )


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------


class TestCLI:
    def test_help_exits_zero(self):
        result = subprocess.run(
            [sys.executable, "-m", "slumdog.shadow_settle", "--help"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "shadow_settle" in result.stdout or "settlement" in result.stdout


# ---------------------------------------------------------------------------
# Void and special dispositions end-to-end
# ---------------------------------------------------------------------------


class TestVoidAndSpecialDispositions:
    def test_void_event_is_unresolved(self, tmp_path):
        sel_payload = [
            {
                "sport": "football",
                "event_id": "football:55555",
                "event_date": "2026-09-05",
                "rank_within_sport_day": 1,
                "status": "PRIMARY_SHADOW_SELECTION",
                "favorite_index": 1,
                "underdog_index": 2,
                "favorite_probability": 0.60,
                "underdog_probability": 0.20,
                "probability_gap": 0.40,
                "draw_probability": 0.20,
                "features": {},
                "missingness": {},
                "run_id": "voidtest12345678",
            },
        ]
        pool = []
        _make_prediction_run(
            tmp_path, run_id="voidtest12345678",
            selections=sel_payload, considered_pool=pool,
        )
        # Build a receipt with a VOID event
        from slumdog.contracts import SettledEvent
        settled = [SettledEvent(
            event_id="football:55555", sport="football",
            event_date="2026-09-05",
            participant_1="A", participant_2="B",
            winner_index=0, score_1=None, score_2=None,
            probability_1=0.6, probability_2=0.2,
            draw_probability=0.2, forebet_pick=1,
            disposition="VOID",
        )]
        sel, man = load_prediction_run("2026-09-05", "voidtest12345678", tmp_path)
        index = _build_event_index(sel, man)
        grades = grade_all_entries(index, settled, sel, man)
        g = grades[0]
        assert g.grade == GRADE_UNRESOLVED
        assert g.disposition == "VOID"


# ---------------------------------------------------------------------------
# Sport-scoped settlement capture (D+1 automation support, 2026-09-06)
# ---------------------------------------------------------------------------


class TestFetchSettlementCaptureSportScoping:
    """``fetch_settlement_capture(sports=...)`` restricts which sports are
    fetched during the network settlement pass. Grading itself is
    unaffected — it only ever grades sports present in the prediction
    run's own entries; this parameter is purely a fetch-cost control so
    an automated D+1 job does not pay the ``pause_seconds`` politeness
    delay for sports with nothing to grade.
    """

    def test_default_fetches_all_non_current_only_sports(self, tmp_path, monkeypatch):
        from slumdog.shadow_settle import fetch_settlement_capture
        from slumdog.sports import SPORTS
        import slumdog.forebet as forebet_mod

        monkeypatch.setattr(
            forebet_mod, "relay_get_markdown",
            lambda relay, target, *, timeout: b"<html>ok</html>",
        )
        monkeypatch.setattr(
            forebet_mod, "fetch_with_fallback",
            lambda relay, target, *, timeout, max_retries: (b"<html>ok</html>", "fake_route"),
        )
        monkeypatch.setattr(forebet_mod, "validate_capture_body", lambda *a, **k: None)

        receipt = fetch_settlement_capture(
            "2026-09-05", tmp_path, pause_seconds=0, timeout=1,
        )
        expected = [s for s in SPORTS if not SPORTS[s].current_only]
        assert len(receipt["captured"]) == len(expected)

    def test_sports_subset_only_fetches_requested(self, tmp_path, monkeypatch):
        from slumdog.shadow_settle import fetch_settlement_capture
        import slumdog.forebet as forebet_mod

        def _relay_get_markdown(relay, target, *, timeout):
            return b"<html>ok</html>"

        def _fetch_with_fallback(relay, target, *, timeout, max_retries):
            return b"<html>ok</html>", "fake_route"

        monkeypatch.setattr(forebet_mod, "relay_get_markdown", _relay_get_markdown)
        monkeypatch.setattr(forebet_mod, "fetch_with_fallback", _fetch_with_fallback)
        monkeypatch.setattr(forebet_mod, "validate_capture_body", lambda *a, **k: None)

        receipt = fetch_settlement_capture(
            "2026-09-05", tmp_path, pause_seconds=0, timeout=1,
            sports=["football"],
        )
        assert len(receipt["captured"]) == 1
        assert receipt["captured"][0]["sport"] == "football"

    def test_sports_subset_drops_unknown_and_current_only(self, tmp_path, monkeypatch):
        from slumdog.shadow_settle import fetch_settlement_capture
        import slumdog.forebet as forebet_mod

        monkeypatch.setattr(
            forebet_mod, "relay_get_markdown",
            lambda relay, target, *, timeout: b"<html>ok</html>",
        )
        monkeypatch.setattr(
            forebet_mod, "fetch_with_fallback",
            lambda relay, target, *, timeout, max_retries: (b"<html>ok</html>", "fake_route"),
        )
        monkeypatch.setattr(forebet_mod, "validate_capture_body", lambda *a, **k: None)

        receipt = fetch_settlement_capture(
            "2026-09-05", tmp_path, pause_seconds=0, timeout=1,
            sports=["football", "not_a_real_sport", "esoccer"],
        )
        sports_fetched = {c["sport"] for c in receipt["captured"]}
        assert "not_a_real_sport" not in sports_fetched
        # esoccer is current_only in SPORTS — must never be fetched via
        # the settlement path even if explicitly requested.
        from slumdog.sports import SPORTS
        if "esoccer" in SPORTS and SPORTS["esoccer"].current_only:
            assert "esoccer" not in sports_fetched

    def test_empty_sports_list_fetches_nothing(self, tmp_path, monkeypatch):
        from slumdog.shadow_settle import fetch_settlement_capture
        import slumdog.forebet as forebet_mod

        calls = []
        monkeypatch.setattr(
            forebet_mod, "fetch_with_fallback",
            lambda *a, **k: calls.append(1) or (b"x", "r"),
        )
        monkeypatch.setattr(
            forebet_mod, "relay_get_markdown",
            lambda *a, **k: calls.append(1) or "x",
        )
        receipt = fetch_settlement_capture(
            "2026-09-05", tmp_path, pause_seconds=0, timeout=1, sports=[],
        )
        assert receipt["captured"] == []
        assert calls == []
