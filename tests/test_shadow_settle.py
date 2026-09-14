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
    complete_settlement,
    compute_rolling_summary,
    grade_all_entries,
    grade_underdog_win,
    load_prediction_run,
    load_settlement_supplements,
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

    def test_unregistered_sport_is_not_a_crash(self):
        # An unknown sport string must still grade, not raise: SPORTS.get()
        # returns None and the draw branch treats it as draw-incapable.
        assert grade_underdog_win(
            underdog_index=1, winner_index=1,
            disposition="SETTLED", sport="not_a_real_sport",
        ) == GRADE_SUCCESS

    # -- guards added after the rank-4+ sentinel audit -------------------
    # A missing underdog identity used to be defaulted to ``0`` by the caller.
    # ``0`` is the draw sentinel, so ``winner_index == underdog_index`` could
    # never fire for a real winner: every such row graded FAILURE no matter
    # what actually happened. The contract now refuses to guess.

    def test_missing_underdog_index_with_a_winner_is_unresolved(self):
        assert grade_underdog_win(
            underdog_index=None, winner_index=2,
            disposition="SETTLED", sport="football",
        ) == GRADE_UNRESOLVED

    def test_zero_underdog_index_is_never_success(self):
        # The historical defect: 0 can only ever "match" a draw, and a draw is
        # intercepted earlier. Exhaustively, 0 must never yield SUCCESS.
        for winner in (0, 1, 2, None):
            for disp in ("SETTLED", "SETTLED_DRAW", "", None):
                for sport in ("football", "basketball", "unknown"):
                    assert grade_underdog_win(
                        underdog_index=0, winner_index=winner,
                        disposition=disp, sport=sport,
                    ) != GRADE_SUCCESS

    def test_missing_underdog_index_still_fails_a_draw(self):
        # A draw is a failed UNDERDOG_WIN regardless of which side was the
        # underdog, so identity is not needed to grade it. This is the one
        # decided outcome still available without an identity.
        assert grade_underdog_win(
            underdog_index=None, winner_index=0,
            disposition="SETTLED", sport="football",
        ) == GRADE_FAILURE

    def test_missing_winner_index_is_unresolved_not_failure(self):
        # ``None == 0`` and ``None == underdog_index`` are both False, so this
        # used to fall through to FAILURE — manufacturing a decided loss from a
        # missing result.
        assert grade_underdog_win(
            underdog_index=2, winner_index=None,
            disposition="SETTLED", sport="football",
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
        # PRODUCTION SCHEMA FIDELITY. Historically this fixture hand-supplied
        # ``underdog_index``/``favorite_index``/probabilities, which
        # ``shadow_evaluator.py`` did not emit for pool entries — so the suite
        # validated a schema production never produced and every rank-4+ row
        # silently defaulted to the ``0`` draw sentinel, making SUCCESS
        # unreachable. These keys must stay in sync with
        # ``CONSIDERED_POOL_ELIGIBLE_KEYS``; ``test_fixture_pool_entry_matches_
        # production_schema`` fails if they drift. Identity now arrives via
        # ``capture_record_tuples`` below (football:12347 → 0.62/0.18 →
        # underdog_index 2), exactly as it does in production.
        considered_pool = [
            {
                "sport": "football",
                "event_id": "football:12347",
                "event_date": target_date,
                "considered_status": "ELIGIBLE_RANKED_BEYOND_TOP3",
                "eligible": True,
                "rank_within_sport_day": 4,
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

    def test_fixture_pool_entry_never_exceeds_production_schema(self, tmp_path):
        """SCHEMA-DIVERGENCE GUARD.

        The rank-4+ grading defect survived because this file's fixture
        hand-supplied ``underdog_index`` while ``shadow_evaluator.py`` never
        emitted it — the suite tested a shape production did not produce, so
        settlement silently defaulted to the ``0`` draw sentinel and a rank-4+
        SUCCESS became unreachable.

        The fixture deliberately models the LEGACY 6-key shape (next test) so
        the recovery path stays exercised for the manifests already committed.
        What must never happen again is the fixture carrying a key production
        does not emit — so the guard is subset, not equality.
        ``tests/test_shadow_evaluator.py`` pins the other side by asserting
        production emits exactly ``CONSIDERED_POOL_ELIGIBLE_KEYS``.
        """
        from slumdog.shadow_evaluator import CONSIDERED_POOL_ELIGIBLE_KEYS

        _sel, man = _make_prediction_run(tmp_path)
        eligible = [p for p in man["considered_pool"] if p.get("eligible")]
        assert eligible, "fixture must contain an eligible rank-4+ pool entry"
        for entry in eligible:
            extra = set(entry) - set(CONSIDERED_POOL_ELIGIBLE_KEYS)
            assert not extra, (
                "fixture pool entry carries keys production never emits "
                f"{sorted(extra)} — this is the divergence that hid the "
                "rank-4+ grading defect"
            )

    def test_pool_entry_carries_no_underdog_index_by_default(self, tmp_path):
        """The fixture must model the *legacy* production shape too.

        Rank-4+ identity has to be recoverable from ``capture_record_tuples``
        because every manifest already committed lacks the field. This asserts
        the fixture really does omit it, so the recovery path is exercised
        rather than bypassed.
        """
        _sel, man = _make_prediction_run(tmp_path)
        pool = [p for p in man["considered_pool"] if p.get("eligible")]
        assert pool, "fixture must contain an eligible rank-4+ pool entry"
        for entry in pool:
            assert "underdog_index" not in entry or entry["underdog_index"] is None


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

    # -- rank-4+ identity recovery (regression for the sentinel defect) ----

    def test_r4plus_underdog_win_graded_success(self, tmp_path):
        """The assertion that was missing: a rank-4+ row CAN grade SUCCESS.

        football:12347 is a considered_pool entry with no ``underdog_index``.
        Its committed pre-event probabilities are 0.62 / 0.18, so the underdog
        is participant 2 — and the settled result has ``winner_index == 2``
        (East FC 0-3 West SC). Before the fix this graded FAILURE, because the
        missing identity defaulted to the ``0`` draw sentinel.
        """
        sel, man = _make_prediction_run(tmp_path)
        receipt = _make_settlement_receipt(tmp_path)
        from slumdog.contracts import SettledEvent
        settled = [SettledEvent(**e) for e in receipt["_settled_events"]]
        index = _build_event_index(sel, man)
        grades = grade_all_entries(index, settled, sel, man)
        g = next(g for g in grades if g.event_id == "football:12347")
        assert g.source == "considered_pool"
        assert g.underdog_index == 2
        assert g.grade == GRADE_SUCCESS

    def test_r4plus_identity_recovered_from_capture_tuples(self, tmp_path):
        sel, man = _make_prediction_run(tmp_path)
        receipt = _make_settlement_receipt(tmp_path)
        from slumdog.contracts import SettledEvent
        settled = [SettledEvent(**e) for e in receipt["_settled_events"]]
        grades = grade_all_entries(_build_event_index(sel, man), settled, sel, man)
        by_id = {g.event_id: g for g in grades}
        # selections[] carry the identity directly
        assert by_id["football:12345"].underdog_index_provenance == "entry"
        # considered_pool[] entries are re-derived from committed pre-event
        # probabilities, and the recovered probabilities are usable downstream
        r4 = by_id["football:12347"]
        assert r4.underdog_index_provenance == "capture_record_tuples"
        assert r4.underdog_probability == pytest.approx(0.18)
        assert r4.favorite_probability == pytest.approx(0.62)
        assert r4.favorite_index == 1

    def test_no_graded_row_uses_the_draw_sentinel_as_underdog(self, tmp_path):
        """``underdog_index == 0`` must never appear in a settlement row.

        ``0`` means "draw" in this schema, so an underdog_index of 0 is not a
        participant — it is the absence of one. Rows with no resolvable
        identity must record ``None``.
        """
        sel, man = _make_prediction_run(tmp_path)
        receipt = _make_settlement_receipt(tmp_path)
        from slumdog.contracts import SettledEvent
        settled = [SettledEvent(**e) for e in receipt["_settled_events"]]
        grades = grade_all_entries(_build_event_index(sel, man), settled, sel, man)
        for g in grades:
            assert g.underdog_index != 0, (
                f"{g.event_id} carries the draw sentinel as its underdog_index"
            )
            assert g.underdog_index in (1, 2, None)

    def test_unresolvable_identity_is_unresolved_not_failure(self, tmp_path):
        """No probabilities anywhere ⇒ no fabricated FAILURE."""
        sel, man = _make_prediction_run(tmp_path)
        # Drop the capture tuples so identity is genuinely unrecoverable
        man["input_provenance"]["capture_record_tuples"] = []
        receipt = _make_settlement_receipt(tmp_path)
        from slumdog.contracts import SettledEvent
        settled = [SettledEvent(**e) for e in receipt["_settled_events"]]
        grades = grade_all_entries(_build_event_index(sel, man), settled, sel, man)
        g = next(g for g in grades if g.event_id == "football:12347")
        assert g.underdog_index is None
        assert g.underdog_index_provenance == "unavailable"
        assert g.grade == GRADE_UNRESOLVED

    def test_written_artifact_records_identity_provenance(self, tmp_path):
        sel, man = _make_prediction_run(tmp_path)
        receipt = _make_settlement_receipt(tmp_path)
        from slumdog.contracts import SettledEvent
        settled = [SettledEvent(**e) for e in receipt["_settled_events"]]
        grades = grade_all_entries(_build_event_index(sel, man), settled, sel, man)
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
        rows = {r["event_id"]: r for r in payload["grades"]}
        assert rows["football:12347"]["underdog_index_provenance"] == (
            "capture_record_tuples"
        )
        assert rows["football:12345"]["underdog_index_provenance"] == "entry"
        # no row may persist the sentinel
        assert all(r["underdog_index"] != 0 for r in payload["grades"])
        assert payload["grading_contract"][
            "missing_underdog_identity_is_unresolved"
        ] is True


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
        assert payload["settlement_schema_version"] == "shadow_settlement"
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


# ---------------------------------------------------------------------------
# Settlement completion pass (append-only supplements, 2026-09-14)
# ---------------------------------------------------------------------------


TARGET = "2026-09-05"
RUN = "abcd1234efgh5678"


def _se(
    event_id: str,
    sport: str,
    *,
    winner_index,
    disposition: str = "SETTLED",
    event_date: str = TARGET,
    p1: float = 0.6,
    p2: float = 0.2,
    draw: float | None = 0.2,
    participant_1: str = "Home FC",
    participant_2: str = "Away United",
    score_1=None,
    score_2=None,
):
    from slumdog.contracts import SettledEvent
    return SettledEvent(
        event_id=event_id, sport=sport, event_date=event_date,
        participant_1=participant_1, participant_2=participant_2,
        winner_index=winner_index, score_1=score_1, score_2=score_2,
        probability_1=p1, probability_2=p2, draw_probability=draw,
        forebet_pick=1, disposition=disposition,
    )


def _settled_fixture(tmp_path, settled_events, *, target_date=TARGET, run_id=RUN,
                     selections=None, considered_pool=None):
    """Create a run + a real D+1 settlement.json graded against ``settled_events``."""
    sel, man = _make_prediction_run(
        tmp_path, target_date=target_date, run_id=run_id,
        selections=selections, considered_pool=considered_pool,
    )
    grades = grade_all_entries(_build_event_index(sel, man), settled_events, sel, man)
    result = write_settlement_artifact(
        target_date=target_date, run_id=run_id, grades=grades,
        summary=compute_rolling_summary(grades),
        settlement_receipt={
            "target_date": target_date, "captured": [], "failures": [],
        },
        repo_root=tmp_path, settled_at="2026-09-06T08:00:00Z",
    )
    run_dir = Path(result.settlement_artifact_path).parent
    return sel, man, run_dir


def _supplements(run_dir: Path):
    return sorted(run_dir.glob("settlement_supplement_*.json"))


class TestSettlementCompletion:
    def test_resolves_unsettled_rows_into_supplement(self, tmp_path):
        # D+1 capture only had football:12345 decided; the other three rows
        # froze UNSETTLED. The completion capture now carries all results.
        _settled_fixture(tmp_path, [_se("football:12345", "football", winner_index=2)])
        run_dir = tmp_path / "data/reports/shadow" / TARGET / RUN
        original_bytes = (run_dir / "settlement.json").read_bytes()

        fresh = [
            _se("football:12345", "football", winner_index=2),
            _se("football:12346", "football", participant_1="City FC",
                participant_2="Town SC", p1=0.55, p2=0.25,
                winner_index=0, score_1=1.0, score_2=1.0),
            _se("basketball:99001", "basketball", draw=None, p1=0.65, p2=0.35,
                participant_1="Team Alpha", participant_2="Team Beta",
                winner_index=1, score_1=100.0, score_2=95.0),
            _se("football:12347", "football", participant_1="East FC",
                participant_2="West SC", p1=0.62, p2=0.18,
                winner_index=2, score_1=0.0, score_2=3.0),
        ]
        import datetime as _dt
        out = complete_settlement(
            target_date=TARGET, run_id=RUN, repo_root=tmp_path,
            settled_rows_override=fresh, as_of=_dt.date(2026, 9, 8),
        )
        assert out.status == "SUPPLEMENT_WRITTEN"
        assert out.resolved_successes == 1   # rank-4+ football:12347
        assert out.resolved_failures == 2    # draw + favorite win
        assert out.still_pending == 0
        assert len(_supplements(run_dir)) == 1
        # the original frozen artifact is byte-identical
        assert (run_dir / "settlement.json").read_bytes() == original_bytes

        payload = json.loads(_supplements(run_dir)[0].read_text())
        ids = {r["event_id"] for r in payload["rows"]}
        assert ids == {"football:12346", "basketball:99001", "football:12347"}
        # already-decided rows are never recomputed, even into a fresh file
        assert "football:12345" not in ids
        assert all(r["previous_grade"] == GRADE_UNSETTLED for r in payload["rows"])

    def test_fresh_capture_contradicting_a_decided_grade_is_ignored(self, tmp_path):
        # football:12345 decided SUCCESS at D+1; a later capture would grade it
        # FAILURE. The completion pass must never touch the decided row.
        _settled_fixture(tmp_path, [_se("football:12345", "football", winner_index=2)])
        run_dir = tmp_path / "data/reports/shadow" / TARGET / RUN
        original = json.loads((run_dir / "settlement.json").read_text())
        original_grade = {r["event_id"]: r["grade"] for r in original["grades"]}

        fresh = [
            _se("football:12345", "football", winner_index=1),  # contradiction
            _se("football:12347", "football", participant_1="East FC",
                participant_2="West SC", p1=0.62, p2=0.18, winner_index=2),
        ]
        import datetime as _dt
        out = complete_settlement(
            target_date=TARGET, run_id=RUN, repo_root=tmp_path,
            settled_rows_override=fresh, as_of=_dt.date(2026, 9, 8),
        )
        assert out.status == "SUPPLEMENT_WRITTEN"
        assert out.resolved_successes == 1  # 12347 only
        payload = json.loads(_supplements(run_dir)[0].read_text())
        assert [r["event_id"] for r in payload["rows"]] == ["football:12347"]
        # original artifact untouched and still records SUCCESS
        reread = json.loads((run_dir / "settlement.json").read_text())
        grades = {r["event_id"]: r["grade"] for r in reread["grades"]}
        assert grades == original_grade
        assert grades["football:12345"] == GRADE_SUCCESS

    def test_nothing_pending_writes_nothing(self, tmp_path):
        fresh = [
            _se("football:12345", "football", winner_index=2),
            _se("football:12346", "football", participant_1="City FC",
                participant_2="Town SC", p1=0.55, p2=0.25, winner_index=0),
            _se("basketball:99001", "basketball", draw=None, p1=0.65, p2=0.35,
                participant_1="Team Alpha", participant_2="Team Beta",
                winner_index=1),
            _se("football:12347", "football", participant_1="East FC",
                participant_2="West SC", p1=0.62, p2=0.18, winner_index=2),
        ]
        _settled_fixture(tmp_path, fresh)
        run_dir = tmp_path / "data/reports/shadow" / TARGET / RUN
        import datetime as _dt
        out = complete_settlement(
            target_date=TARGET, run_id=RUN, repo_root=tmp_path,
            settled_rows_override=fresh, as_of=_dt.date(2026, 9, 8),
        )
        assert out.status == "NOTHING_PENDING"
        assert _supplements(run_dir) == []

    def test_no_new_resolutions_writes_nothing_and_is_idempotent(self, tmp_path):
        # Everything UNSETTLED at D+1; completion capture still has nothing.
        _settled_fixture(tmp_path, [])
        run_dir = tmp_path / "data/reports/shadow" / TARGET / RUN
        import datetime as _dt
        for _ in range(2):
            out = complete_settlement(
                target_date=TARGET, run_id=RUN, repo_root=tmp_path,
                settled_rows_override=[], as_of=_dt.date(2026, 9, 8),
            )
            assert out.status == "NO_NEW_RESOLUTIONS"
            assert out.still_pending == 4
        assert _supplements(run_dir) == []

    def test_second_supplement_closes_remaining_rows(self, tmp_path):
        # Append-only sequence: supplement 1 closes one row, supplement 2 (a
        # later dispatch) closes the rest.
        _settled_fixture(tmp_path, [])
        run_dir = tmp_path / "data/reports/shadow" / TARGET / RUN
        import datetime as _dt
        out1 = complete_settlement(
            target_date=TARGET, run_id=RUN, repo_root=tmp_path,
            settled_rows_override=[_se("football:12345", "football", winner_index=2)],
            as_of=_dt.date(2026, 9, 7), generated_at="2026-09-07T09:00:00Z",
        )
        assert out1.status == "SUPPLEMENT_WRITTEN"
        out2 = complete_settlement(
            target_date=TARGET, run_id=RUN, repo_root=tmp_path,
            settled_rows_override=[
                _se("football:12345", "football", winner_index=2),
                _se("football:12347", "football", participant_1="East FC",
                    participant_2="West SC", p1=0.62, p2=0.18, winner_index=2),
            ],
            as_of=_dt.date(2026, 9, 8), generated_at="2026-09-08T09:00:00Z",
        )
        assert out2.status == "SUPPLEMENT_WRITTEN"
        files = _supplements(run_dir)
        assert len(files) == 2
        # both load and hash-verify; covered keys never reappear
        loaded = load_settlement_supplements(run_dir)
        first_ids = {r["event_id"] for r in loaded[0]["rows"]}
        second_ids = {r["event_id"] for r in loaded[1]["rows"]}
        assert first_ids == {"football:12345"}
        assert second_ids == {"football:12347"}
        assert not (first_ids & second_ids)

    def test_unresolved_from_an_older_artifact_upgrades_to_success(self, tmp_path):
        # The parser contract always carries a winner for matched events, so
        # today's pipeline produces recoverable rows as UNSETTLED (no match);
        # but an UNRESOLVED row committed by ANY code version must still be
        # retried, and a later capture with a clean identity + result must
        # upgrade it. Hand-write the legacy artifact to model that state.
        import hashlib
        _make_prediction_run(tmp_path, target_date=TARGET, run_id=RUN)
        run_dir = tmp_path / "data/reports/shadow" / TARGET / RUN
        grades = [
            {"sport": "football", "event_id": "football:12345",
             "event_date": TARGET, "source": "selections",
             "considered_status": "PRIMARY_SHADOW_SELECTION",
             "rank_within_sport_day": 1, "underdog_index": 2,
             "underdog_probability": 0.2, "favorite_index": 1,
             "favorite_probability": 0.6, "grade": GRADE_SUCCESS,
             "winner_index": 2, "disposition": "SETTLED", "score_1": 1.0,
             "score_2": 2.0, "settled_participant_1": "Home FC",
             "settled_participant_2": "Away United", "match_method":
             "exact_event_id", "underdog_index_provenance": "entry",
             "settled_context": {}},
            {"sport": "football", "event_id": "football:12347",
             "event_date": TARGET, "source": "considered_pool",
             "considered_status": "ELIGIBLE_RANKED_BEYOND_TOP3",
             "rank_within_sport_day": 4, "underdog_index": None,
             "underdog_probability": None, "favorite_index": None,
             "favorite_probability": None, "grade": GRADE_UNRESOLVED,
             "winner_index": None, "disposition": None, "score_1": None,
             "score_2": None, "settled_participant_1": "",
             "settled_participant_2": "", "match_method": "no_match",
             "underdog_index_provenance": "unavailable", "settled_context": {}},
        ]
        artifact = {
            "settlement_schema_version": "shadow_settlement",
            "target_date": TARGET, "run_id": RUN,
            "settled_at": "2026-09-06T08:00:00Z",
            "grading_contract": {"target": "UNDERDOG_WIN"},
            "metadata_policy": {}, "grades": grades, "summary": {},
            "settlement_capture_receipt": {},
        }
        data = json.dumps(artifact, indent=2, sort_keys=True).encode()
        (run_dir / "settlement.json").write_bytes(data)
        (run_dir / "settlement.json.sha256").write_text(
            f"{hashlib.sha256(data).hexdigest()}  settlement.json\n"
        )

        import datetime as _dt
        out = complete_settlement(
            target_date=TARGET, run_id=RUN, repo_root=tmp_path,
            settled_rows_override=[
                _se("football:12347", "football", participant_1="East FC",
                    participant_2="West SC", p1=0.62, p2=0.18,
                    winner_index=2, score_1=0.0, score_2=3.0),
            ],
            as_of=_dt.date(2026, 9, 8),
        )
        assert out.status == "SUPPLEMENT_WRITTEN"
        row = json.loads(_supplements(run_dir)[0].read_text())["rows"][0]
        assert row["event_id"] == "football:12347"
        assert row["grade"] == GRADE_SUCCESS
        assert row["previous_grade"] == GRADE_UNRESOLVED
        assert row["resolution_kind"] == "decided"

    def test_void_match_closes_as_terminal_unresolved(self, tmp_path):
        _settled_fixture(tmp_path, [])
        run_dir = tmp_path / "data/reports/shadow" / TARGET / RUN
        import datetime as _dt
        out = complete_settlement(
            target_date=TARGET, run_id=RUN, repo_root=tmp_path,
            settled_rows_override=[
                _se("football:12346", "football", participant_1="City FC",
                    participant_2="Town SC", p1=0.55, p2=0.25,
                    winner_index=0, disposition="VOID"),
            ],
            as_of=_dt.date(2026, 9, 8),
        )
        assert out.status == "SUPPLEMENT_WRITTEN"
        assert out.terminal_unresolved == 1
        row = json.loads(_supplements(run_dir)[0].read_text())["rows"][0]
        assert row["grade"] == GRADE_UNRESOLVED
        assert row["resolution_kind"] == "terminal_unresolved"
        assert row["terminal_reason"] == "void_no_contest_or_cancelled"
        # the terminal row is no longer retried on the next dispatch
        again = complete_settlement(
            target_date=TARGET, run_id=RUN, repo_root=tmp_path,
            settled_rows_override=[
                _se("football:12346", "football", participant_1="City FC",
                    participant_2="Town SC", p1=0.55, p2=0.25,
                    winner_index=0, disposition="VOID"),
            ],
            as_of=_dt.date(2026, 9, 9),
        )
        assert again.status == "NO_NEW_RESOLUTIONS"
        assert len(_supplements(run_dir)) == 1

    def test_anomalous_two_way_draw_is_terminal(self, tmp_path):
        _settled_fixture(tmp_path, [])
        run_dir = tmp_path / "data/reports/shadow" / TARGET / RUN
        import datetime as _dt
        out = complete_settlement(
            target_date=TARGET, run_id=RUN, repo_root=tmp_path,
            settled_rows_override=[
                _se("basketball:99001", "basketball", draw=None, p1=0.65, p2=0.35,
                    participant_1="Team Alpha", participant_2="Team Beta",
                    winner_index=0),
            ],
            as_of=_dt.date(2026, 9, 8),
        )
        assert out.terminal_unresolved == 1
        row = json.loads(_supplements(run_dir)[0].read_text())["rows"][0]
        assert row["event_id"] == "basketball:99001"
        assert row["terminal_reason"] == "anomalous_draw_two_way_sport"

    def test_unrecoverable_identity_is_terminal_not_refetched_forever(self, tmp_path):
        # Identity comes from FROZEN pre-event data: if the manifest carries
        # no probabilities for an event, a later post-event capture cannot
        # manufacture an underdog, so the matched loss closes as terminal.
        sel, man = _make_prediction_run(tmp_path, target_date=TARGET, run_id=RUN)
        man["input_provenance"]["capture_record_tuples"] = [
            t for t in man["input_provenance"]["capture_record_tuples"]
            if t[1] != "football:12347"
        ]
        # completion reloads the run from disk, so persist the redacted manifest
        run_dir = tmp_path / "data/reports/shadow" / TARGET / RUN
        (run_dir / "manifest.json").write_text(json.dumps(man, sort_keys=True))
        grades = grade_all_entries(_build_event_index(sel, man), [
            _se("football:12347", "football", participant_1="East FC",
                participant_2="West SC", p1=0.62, p2=0.18, winner_index=1),
        ], sel, man)
        r4 = next(g for g in grades if g.event_id == "football:12347")
        assert r4.grade == GRADE_UNRESOLVED
        write_settlement_artifact(
            target_date=TARGET, run_id=RUN, grades=grades,
            summary=compute_rolling_summary(grades),
            settlement_receipt={"target_date": TARGET, "captured": [], "failures": []},
            repo_root=tmp_path, settled_at="2026-09-06T08:00:00Z",
        )
        run_dir = tmp_path / "data/reports/shadow" / TARGET / RUN
        import datetime as _dt
        out = complete_settlement(
            target_date=TARGET, run_id=RUN, repo_root=tmp_path,
            settled_rows_override=[
                _se("football:12347", "football", participant_1="East FC",
                    participant_2="West SC", p1=0.62, p2=0.18, winner_index=1),
            ],
            as_of=_dt.date(2026, 9, 8),
        )
        assert out.terminal_unresolved == 1
        row = json.loads(_supplements(run_dir)[0].read_text())["rows"][0]
        assert row["terminal_reason"] == "underdog_identity_unavailable"

    def test_retry_window_expiry_fetches_no_more(self, tmp_path, monkeypatch):
        _settled_fixture(tmp_path, [])
        run_dir = tmp_path / "data/reports/shadow" / TARGET / RUN

        def _boom(*a, **k):
            raise AssertionError("the window is closed; no capture may run")
        monkeypatch.setattr(
            "slumdog.shadow_settle.fetch_settlement_capture", _boom,
        )
        import datetime as _dt
        out = complete_settlement(
            target_date=TARGET, run_id=RUN, repo_root=tmp_path,
            as_of=_dt.date(2026, 9, 25),  # 20 days old > 14-day window
        )
        assert out.status == "RETRY_WINDOW_EXPIRED"
        assert out.still_pending == 4
        assert _supplements(run_dir) == []

    def test_within_window_without_result_is_not_expired(self, tmp_path):
        # Exactly at the 14-day boundary the row still retries (and, with no
        # result, reports NO_NEW_RESOLUTIONS rather than expiring).
        _settled_fixture(tmp_path, [])
        import datetime as _dt
        out = complete_settlement(
            target_date=TARGET, run_id=RUN, repo_root=tmp_path,
            settled_rows_override=[], as_of=_dt.date(2026, 9, 19),
        )
        assert out.status == "NO_NEW_RESOLUTIONS"

    def test_tampered_settlement_fails_closed(self, tmp_path):
        _settled_fixture(tmp_path, [])
        run_dir = tmp_path / "data/reports/shadow" / TARGET / RUN
        (run_dir / "settlement.json").write_bytes(b'{"grades": []}')
        import datetime as _dt
        out = complete_settlement(
            target_date=TARGET, run_id=RUN, repo_root=tmp_path,
            settled_rows_override=[], as_of=_dt.date(2026, 9, 8),
        )
        assert out.status == "COMPLETION_FAILED"
        assert "marker" in (out.error or "")
        assert _supplements(run_dir) == []

    def test_missing_settlement_fails_closed(self, tmp_path):
        # Run exists but was never settled (the D+1 pass owns that).
        _make_prediction_run(tmp_path, target_date=TARGET, run_id=RUN)
        import datetime as _dt
        out = complete_settlement(
            target_date=TARGET, run_id=RUN, repo_root=tmp_path,
            settled_rows_override=[], as_of=_dt.date(2026, 9, 8),
        )
        assert out.status == "COMPLETION_FAILED"

    def test_supplement_schema_marker_and_original_hash(self, tmp_path):
        import hashlib
        _settled_fixture(tmp_path, [_se("football:12345", "football", winner_index=2)])
        run_dir = tmp_path / "data/reports/shadow" / TARGET / RUN
        original_sha = hashlib.sha256(
            (run_dir / "settlement.json").read_bytes()
        ).hexdigest()
        import datetime as _dt
        complete_settlement(
            target_date=TARGET, run_id=RUN, repo_root=tmp_path,
            settled_rows_override=[
                _se("football:12345", "football", winner_index=2),
                _se("football:12347", "football", participant_1="East FC",
                    participant_2="West SC", p1=0.62, p2=0.18, winner_index=2),
            ],
            as_of=_dt.date(2026, 9, 8),
        )
        path = _supplements(run_dir)[0]
        payload = json.loads(path.read_text())
        assert payload["settlement_supplement_schema_version"] == (
            "shadow_settlement_supplement"
        )
        assert payload["completes"]["artifact"] == "settlement.json"
        assert payload["completes"]["artifact_sha256"] == original_sha
        assert payload["grading_contract"]["target"] == "UNDERDOG_WIN"
        assert payload["metadata_policy"][
            "completion_never_modifies_decided_grades"
        ] is True
        # marker verifies
        marker = Path(str(path) + ".sha256").read_text().split()[0]
        assert marker == hashlib.sha256(path.read_bytes()).hexdigest()
        # loader agrees
        assert len(load_settlement_supplements(run_dir)) == 1

    def test_same_second_dispatch_never_overwrites(self, tmp_path):
        _settled_fixture(tmp_path, [])
        import datetime as _dt
        kwargs = dict(
            target_date=TARGET, run_id=RUN, repo_root=tmp_path,
            as_of=_dt.date(2026, 9, 8), generated_at="2026-09-08T09:00:00Z",
        )
        complete_settlement(
            **kwargs,
            settled_rows_override=[_se("football:12345", "football", winner_index=2)],
        )
        complete_settlement(
            **kwargs,
            settled_rows_override=[_se("football:12346", "football",
                                      participant_1="City FC", participant_2="Town SC",
                                      p1=0.55, p2=0.25, winner_index=0)],
        )
        files = _supplements(tmp_path / "data/reports/shadow" / TARGET / RUN)
        assert len(files) == 2
        assert all(f.suffix == ".json" for f in files)


class TestCompletionCLI:
    def test_complete_offline_without_newer_receipt_is_failure_exit(self, tmp_path):
        _settled_fixture(tmp_path, [])
        result = subprocess.run(
            [sys.executable, "-m", "slumdog.shadow_settle",
             "--date", TARGET, "--run-id", RUN, "--root", str(tmp_path),
             "--complete", "--offline"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 2
        assert "COMPLETION_FAILED" in result.stdout
