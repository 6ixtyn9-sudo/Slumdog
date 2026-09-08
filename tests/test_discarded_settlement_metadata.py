"""Regression tests for the 2026-09-07 discard audit.

The settlement lineage collected considerably more from the post-event page
than it kept. Two discards are closed here:

1. **The draw price survived nowhere in the settled record.**
   ``parsers._participant_odds`` returns ``(home, away, raw_values)`` — for a
   three-way board it reads ``parsed[1]``, the draw price, and drops it — and
   ``settlement.parse_football_settled`` only ever read ``best_odd_1`` /
   ``best_odd_2``, never ``best_odd_X``. The *pre-event* path did keep
   ``odds_draw`` (``parsers.py`` reads ``best_odd_X`` into
   ``facets["odds_draw"]``), so the same number existed before kickoff and
   vanished afterwards. That asymmetry is what made the loss invisible.

2. **``SettledEvent.facets`` was built and then dropped.** Forebet's own pick,
   the league, weather, HT/ET/penalty scores, odds movement and cup flags were
   assembled by the parser, attached to the event, and then discarded by
   ``write_settlement_artifact``, which serialised a fixed list of grade fields.

Everything recovered here is **metadata, never signal**. See
``shadow_settle._settled_context`` and the ``metadata_policy`` block written
into the settlement artifact: odds are display-only (AGENTS.md invariant 11),
never model features or gates (invariants 8-9), and nothing recorded here may
feed an EV / de-vigging / Kelly / staking calculation (invariant 10).

Retention is the default and withholding is the exception. An earlier version of
this change withheld Forebet's ``kelly`` facet from the artifact; the owner
overruled that on 2026-09-07 — collected data is not to be discarded, since the
mission is to predict the underdog and any tool that gets us there should be
retained. ``kelly`` is therefore persisted as inert metadata, and the invariant-10
bar lives in the *feature* layer (``dataset.PROHIBITED_KEYS``) instead of at the
point of recording. Recording a number and staking on it are different acts: the
first is required, the second is forbidden.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from slumdog.contracts import SettledEvent
from slumdog.settlement import _draw_odds, parse_football_settled, parse_html_settled
from slumdog.shadow_settle import (
    GRADE_FAILURE,
    GRADE_SUCCESS,
    GRADE_UNRESOLVED,
    RANK_BANDS,
    WITHHELD_FACET_KEYS,
    SettlementGrade,
    _build_event_index,
    _rank_band,
    _settled_context,
    compute_rolling_summary,
    grade_all_entries,
    write_settlement_artifact,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TARGET_DATE = "2026-08-23"

# Three-way board: home 2.10, draw 3.40, away 3.20. Before the fix the 3.40
# was parsed and then thrown away.
FOOTBALL_THREE_WAY_HTML = b"""
<html><body><div class='rcnt'>
<span class='shortTag'>ES1</span>
<a class='tnmscn' href='/en/football/matches/laliga/real-madrid-barcelona/201'>
<span class='homeTeam'>Real Madrid</span><span class='awayTeam'>Barcelona</span>
<span class='date_bah'>23/08/2026 15:00</span></a>
<div class='fprc'><span>45</span><span>25</span><span>30</span></div>
<div class='predict_no'><span class='forepr'><span>1</span></span></div>
<div class='haodd'><span>2.10</span><span>3.40</span><span>3.20</span></div>
<div class='scoreLnk'><span>FT</span></div>
<div class='lscr_td'><span>2</span><span>1</span></div>
</div></body></html>
"""

# Two-way board: there is no draw price to recover, and inventing one would be
# worse than recording None.
BASKETBALL_TWO_WAY_HTML = b"""
<html><body><div class='rcnt'>
<span class='shortTag'>WNB</span>
<a class='tnmscn' href='/en/basketball/matches/test/alpha-beta/1'>
<span class='homeTeam'>Alpha</span><span class='awayTeam'>Beta</span>
<span class='date_bah'>23/08/2026 03:00</span></a>
<div class='fprc'><span>36</span><span>64</span></div>
<div class='predict_no'><span class='forepr'><span>2</span></span></div>
<div class='haodd'><span>+150</span><span>-200</span></div>
<div class='scoreLnk'><span>FT</span></div>
<div class='lscr_td'><span>93</span><span>86</span></div>
</div></body></html>
"""


def _cricket_html(middle_cell: str) -> bytes:
    """Cricket is draw-capable but Forebet leaves the draw cell blank/dashed."""
    return f"""
<html><body><div class='rcnt'>
<span class='shortTag'>CRICT</span>
<a class='tnmscn' href='/en/cricket/matches/test/alpha-beta/7'>
<span class='homeTeam'>Alpha</span><span class='awayTeam'>Beta</span>
<span class='date_bah'>23/08/2026 03:00</span></a>
<div class='fprc'><span>36</span><span>64</span></div>
<div class='predict_no'><span class='forepr'><span>2</span></span></div>
<div class='haodd'><span>1.90</span><span>{middle_cell}</span><span>2.00</span></div>
<div class='scoreLnk'><span>FT</span></div>
<div class='lscr_td'><span>93</span><span>86</span></div>
</div></body></html>
""".encode()


def _football_json_row(**overrides) -> dict:
    row = {
        "id": "201",
        "DATE_BAH": "2026-08-23 15:00",
        "HOST_NAME": "Real Madrid",
        "GUEST_NAME": "Barcelona",
        "Host_SC": "2",
        "Guest_SC": "1",
        "Host_SC_HT": "1",
        "Guest_SC_HT": "0",
        "Pred_1": "45",
        "Pred_X": "25",
        "Pred_2": "30",
        "best_odd_1": "2.10",
        "best_odd_X": "3.40",
        "best_odd_2": "3.20",
        "best_odd_1_am": "+110",
        "best_odd_X_am": "+240",
        "best_odd_2_am": "+220",
        "short_tag": "ES1",
        "comment": "FT",
        "kelly": "0.94",
        "host_stadium": "Santiago Bernabeu",
        "weather_temp_f": "78",
        "move_X": "-0.10",
        "some_future_facet": "not-in-the-persisted-list",
    }
    row.update(overrides)
    return row


def _settled_event(**overrides) -> SettledEvent:
    kwargs: dict = {
        "event_id": "football:201",
        "sport": "football",
        "event_date": TARGET_DATE,
        "participant_1": "Real Madrid",
        "participant_2": "Barcelona",
        "winner_index": 1,
        "score_1": 2.0,
        "score_2": 1.0,
        "probability_1": 0.45,
        "probability_2": 0.30,
        "draw_probability": 0.25,
        "forebet_pick": 1,
        "odds_1": 2.10,
        "odds_2": 3.20,
        "odds_draw": 3.40,
        "league": "La Liga",
        "league_id": "ES1",
        "period_scores_1": (1.0, 2.0),
        "period_scores_2": (0.0, 1.0),
        "source_url": "/en/football/matches/laliga/real-madrid-barcelona/201",
        "participant_1_id": "rm",
        "participant_2_id": "fcb",
        "facets": {
            "host_stadium": "Santiago Bernabeu",
            "weather_temp_f": "78",
            "move_1": "+0.05",
            "move_X": "-0.10",
            "move_2": "+0.05",
            "Host_SC_HT": "1",
            "Guest_SC_HT": "0",
            "isCup": False,
            "trend_en": "Real Madrid won 4 of last 5",
            "kelly": "0.94",
            "some_future_facet": "not-in-the-persisted-list",
        },
    }
    kwargs.update(overrides)
    return SettledEvent(**kwargs)


# ---------------------------------------------------------------------------
# 1. The draw price is recovered, and only where one genuinely exists
# ---------------------------------------------------------------------------


class TestDrawOddsRecovery:
    def test_three_way_html_board_yields_draw_price(self):
        rows = parse_html_settled(FOOTBALL_THREE_WAY_HTML, "football", TARGET_DATE)
        assert len(rows) == 1
        row = rows[0]
        # All three prices, in board order: home / draw / away.
        assert (row.odds_1, row.odds_draw, row.odds_2) == (2.10, 3.40, 3.20)

    def test_two_way_board_has_no_draw_price(self):
        rows = parse_html_settled(BASKETBALL_TWO_WAY_HTML, "basketball", TARGET_DATE)
        assert len(rows) == 1
        assert rows[0].odds_draw is None
        # The two participant prices are unaffected by the change.
        assert rows[0].odds_1 == 2.5
        assert rows[0].odds_2 == pytest.approx(1.5)

    @pytest.mark.parametrize("middle_cell", ["", "-", "N/A"])
    def test_draw_capable_sport_with_blank_draw_cell_is_none(self, middle_cell):
        """Cricket/handball are draw-capable but Forebet leaves the cell blank.

        ``_participant_odds`` still returns home/away for these rows, so the
        draw price must be ``None`` rather than something positional — reading
        ``parsed[1]`` blindly would have returned the *away* price as a draw.
        """
        rows = parse_html_settled(_cricket_html(middle_cell), "cricket", TARGET_DATE)
        assert len(rows) == 1
        assert rows[0].odds_draw is None
        assert rows[0].odds_1 == pytest.approx(1.90)
        assert rows[0].odds_2 == pytest.approx(2.00)

    def test_football_json_reads_best_odd_x(self):
        """The JSON settlement path only ever read best_odd_1/best_odd_2."""
        payload = json.dumps([[_football_json_row()]]).encode()
        settled = parse_football_settled(payload, TARGET_DATE)
        assert len(settled) == 1
        event = settled[0]
        assert event.odds_draw == 3.40
        assert (event.odds_1, event.odds_2) == (2.10, 3.20)

    def test_football_json_absent_best_odd_x_is_none(self):
        payload = json.dumps([[_football_json_row(best_odd_X=None)]]).encode()
        settled = parse_football_settled(payload, TARGET_DATE)
        assert settled[0].odds_draw is None

    def test_american_format_prices_are_kept_in_facets(self):
        """parsers.py retains these pre-event; settlement used to drop them."""
        payload = json.dumps([[_football_json_row()]]).encode()
        event = parse_football_settled(payload, TARGET_DATE)[0]
        assert event.facets["best_odd_1_am"] == "+110"
        assert event.facets["best_odd_X_am"] == "+240"
        assert event.facets["best_odd_2_am"] == "+220"

    def test_draw_odds_helper_returns_none_for_two_way(self):
        """``_draw_odds`` must not fabricate a price for a two-way sport."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(BASKETBALL_TWO_WAY_HTML, "html.parser")
        row = soup.select_one("div.rcnt")
        assert _draw_odds(row, draw_possible=False) is None
        assert _draw_odds(row, draw_possible=True) is None  # only 2 cells


# ---------------------------------------------------------------------------
# 2. settled_context curation — and the Kelly exclusion
# ---------------------------------------------------------------------------


class TestSettledContext:
    def test_recovers_the_fields_that_used_to_vanish(self):
        ctx = _settled_context(_settled_event())
        assert ctx["forebet_pick"] == 1
        assert ctx["league"] == "La Liga"
        assert ctx["league_id"] == "ES1"
        assert ctx["participant_1_id"] == "rm"
        assert ctx["participant_2_id"] == "fcb"
        assert ctx["odds_draw"] == 3.40
        assert ctx["period_scores_1"] == [1.0, 2.0]
        assert ctx["period_scores_2"] == [0.0, 1.0]
        assert ctx["probability_1"] == 0.45
        assert ctx["draw_probability"] == 0.25
        assert "real-madrid-barcelona" in ctx["source_url"]

    def test_kelly_is_retained_as_inert_metadata(self):
        """Owner directive 2026-09-07: collected data is not to be discarded.

        Kelly fractions are recorded, because the mission is to predict the
        underdog and any tool that gets us there should be retained. What
        invariant 10 forbids is *staking* on them, so that bar sits in the
        feature layer — see test_kelly_is_barred_from_the_feature_layer.
        """
        assert "kelly" not in WITHHELD_FACET_KEYS

        event = _settled_event()
        assert "kelly" in event.facets
        ctx = _settled_context(event)
        assert ctx["facets"]["kelly"] == "0.94"  # retained, not dropped
        assert ctx["facets_withheld"] == []

    def test_kelly_is_barred_from_the_feature_layer(self):
        """Retained as data, banned as signal — that is where invariant 10 bites."""
        from slumdog.dataset import PROHIBITED_KEYS

        assert "kelly" in PROHIBITED_KEYS

    def test_nothing_is_withheld_under_the_current_policy(self):
        assert WITHHELD_FACET_KEYS == ()

    def test_every_facet_the_parser_collected_is_retained(self):
        """Retention by default — including facets no one has listed yet."""
        event = _settled_event()
        ctx = _settled_context(event)
        assert set(ctx["facets"]) == set(event.facets)
        assert ctx["facets"]["some_future_facet"] == "not-in-the-persisted-list"
        assert ctx["facets"]["host_stadium"] == "Santiago Bernabeu"
        assert ctx["facets"]["Host_SC_HT"] == "1"
        assert ctx["facets"]["move_X"] == "-0.10"
        assert ctx["facets"]["trend_en"].startswith("Real Madrid")

    def test_withholding_mechanism_still_records_what_it_drops(self, monkeypatch):
        """The denylist is empty, not absent: a future withholding stays visible."""
        import slumdog.shadow_settle as mod

        monkeypatch.setattr(mod, "WITHHELD_FACET_KEYS", ("kelly",))
        ctx = mod._settled_context(_settled_event())
        assert "kelly" not in ctx["facets"]
        assert ctx["facets_withheld"] == ["kelly"]

    def test_no_settled_event_yields_empty_context(self):
        assert _settled_context(None) == {}

    def test_post_event_facts_are_kept_out_of_pre_event_records(self):
        """HT/ET/penalty/weather are post-event — bar them from PreEventRecord.

        They are legitimate in the settlement artifact (it is written after the
        match) and illegitimate as pre-event features.
        """
        from slumdog.shadow_contracts import _FORBIDDEN_RECORD_FIELDS

        # Odds and post-event outcomes can never be PreEventRecord fields.
        for barred in (
            "odds_1", "odds_2", "odds_draw",
            "score_1", "score_2", "winner_index", "disposition",
            "extra_time_score", "penalty_score",
        ):
            assert barred in _FORBIDDEN_RECORD_FIELDS, barred


# ---------------------------------------------------------------------------
# 3. Governance: display-only, never a feature, never a gate
# ---------------------------------------------------------------------------


class TestOddsGovernance:
    def test_odds_draw_is_a_prohibited_feature_key(self):
        from slumdog.dataset import PROHIBITED_KEYS

        assert {"odds_1", "odds_2", "odds_draw"} <= PROHIBITED_KEYS

    def test_odds_draw_is_declared_pre_event_display_metadata(self):
        from slumdog.facets import COMMON_FACETS, TimingClass

        by_name = {f.name: f for f in COMMON_FACETS}
        assert "odds_draw" in by_name
        assert by_name["odds_draw"].timing == TimingClass.PRE_EVENT

    def test_odds_do_not_change_a_grade(self):
        """Invariant 11: a price must never decide, gate or re-grade anything."""
        cheap = _settled_event(odds_1=1.05, odds_2=41.0, odds_draw=19.0)
        dear = _settled_event(odds_1=9.50, odds_2=1.10, odds_draw=2.00)
        no_price = _settled_event(odds_1=None, odds_2=None, odds_draw=None)

        from slumdog.shadow_settle import grade_underdog_win

        grades = [
            grade_underdog_win(
                underdog_index=2,
                winner_index=event.winner_index,
                disposition=event.disposition,
                sport=event.sport,
            )
            for event in (cheap, dear, no_price)
        ]
        assert grades[0] == grades[1] == grades[2]

    def test_artifact_metadata_policy_states_the_rules(self, tmp_path):
        artifact = _write_artifact(tmp_path, settled=[_settled_event()])
        policy = artifact["metadata_policy"]
        assert policy["settled_context_is_metadata_only"] is True
        assert policy["odds_used_in_grading"] is False
        assert policy["odds_used_as_model_features"] is False
        assert policy["odds_gate_candidates"] is False
        assert policy["missing_odds_lower_confidence"] is False
        # Retention (owner directive 2026-09-07): data is kept, staking is not.
        assert policy["facets_retained_by_default"] is True
        assert policy["withheld_facet_keys"] == []
        assert policy["kelly_retained_as_inert_metadata"] is True
        assert policy["kelly_used_for_ev_devig_or_staking"] is False
        assert "invariant 10" in policy["invariants"]["no_ev_devig_kelly_or_staking"]


# ---------------------------------------------------------------------------
# 4. Persistence end-to-end
# ---------------------------------------------------------------------------


def _prediction_run(
    tmp_path: Path,
    *,
    target_date: str = TARGET_DATE,
    run_id: str = "abcd1234efgh5678",
    selections: list[dict] | None = None,
    considered_pool: list[dict] | None = None,
    capture_record_tuples: list[list] | None = None,
) -> tuple[dict, dict]:
    run_dir = tmp_path / "data" / "reports" / "shadow" / target_date / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    if selections is None:
        selections = [
            {
                "sport": "football",
                "event_id": "football:201",
                "event_date": target_date,
                "rank_within_sport_day": 1,
                "status": "PRIMARY_SHADOW_SELECTION",
                "favorite_index": 1,
                "underdog_index": 2,
                "favorite_probability": 0.45,
                "underdog_probability": 0.30,
                "draw_probability": 0.25,
                "features": {},
                "missingness": {},
                "run_id": run_id,
            }
        ]
    manifest = {
        "run_id": run_id,
        "target_date": target_date,
        "considered_pool": considered_pool or [],
        "input_provenance": {
            "capture_record_tuples": capture_record_tuples or [],
        },
    }
    (run_dir / "shadow_selections.json").write_text(
        json.dumps({"selections": selections}, indent=2, sort_keys=True)
    )
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True)
    )
    return {"selections": selections}, manifest


def _write_artifact(
    tmp_path: Path,
    *,
    settled: list[SettledEvent],
    selections: list[dict] | None = None,
    considered_pool: list[dict] | None = None,
    capture_record_tuples: list[list] | None = None,
    run_id: str = "abcd1234efgh5678",
) -> dict:
    selections, manifest = _prediction_run(
        tmp_path,
        selections=selections,
        considered_pool=considered_pool,
        capture_record_tuples=capture_record_tuples,
        run_id=run_id,
    )
    index = _build_event_index(selections, manifest)
    grades = grade_all_entries(index, settled, selections, manifest)
    summary = compute_rolling_summary(grades)
    result = write_settlement_artifact(
        target_date=TARGET_DATE,
        run_id=run_id,
        grades=grades,
        summary=summary,
        settlement_receipt={"_settled_events": []},
        repo_root=tmp_path,
        settled_at="2026-08-24T08:00:00Z",
    )
    return json.loads(Path(result.settlement_artifact_path).read_text())


class TestArtifactPersistence:
    def test_grade_rows_carry_settled_context(self, tmp_path):
        artifact = _write_artifact(tmp_path, settled=[_settled_event()])
        rows = artifact["grades"]
        assert len(rows) == 1
        ctx = rows[0]["settled_context"]
        assert ctx["odds_draw"] == 3.40
        assert ctx["forebet_pick"] == 1
        assert ctx["league"] == "La Liga"
        assert ctx["facets"]["host_stadium"] == "Santiago Bernabeu"
        assert ctx["facets"]["kelly"] == "0.94"  # retained, not withheld
        assert ctx["facets_withheld"] == []

    def test_kelly_reaches_the_written_bytes(self, tmp_path):
        """Retention is the whole point: the value must survive to the evidence."""
        artifact = _write_artifact(tmp_path, settled=[_settled_event()])
        assert artifact["grades"][0]["settled_context"]["facets"]["kelly"] == "0.94"
        assert artifact["metadata_policy"]["withheld_facet_keys"] == []

    def test_unsettled_rows_have_empty_context_not_a_crash(self, tmp_path):
        artifact = _write_artifact(tmp_path, settled=[])
        rows = artifact["grades"]
        assert len(rows) == 1
        assert rows[0]["settled_context"] == {}

    def test_context_survives_canonical_json_roundtrip(self, tmp_path):
        """canonical_json_bytes must serialise the nested dict deterministically."""
        first = _write_artifact(tmp_path / "a", settled=[_settled_event()])
        second = _write_artifact(tmp_path / "b", settled=[_settled_event()])
        assert first["grades"] == second["grades"]


# ---------------------------------------------------------------------------
# 5. per_rank_band — the n=1 explosion gets a reportable view
# ---------------------------------------------------------------------------


def _grade(rank: int | None, grade: str = GRADE_SUCCESS) -> SettlementGrade:
    return SettlementGrade(
        sport="football",
        event_id=f"football:{rank}",
        event_date=TARGET_DATE,
        source="considered_pool" if rank and rank > 3 else "selections",
        considered_status="ELIGIBLE_RANKED_BEYOND_TOP3",
        rank_within_sport_day=rank,
        underdog_index=2,
        underdog_probability=0.30,
        favorite_index=1,
        favorite_probability=0.45,
        grade=grade,
        winner_index=2,
        disposition="SETTLED",
        score_1=1.0,
        score_2=2.0,
        settled_participant_1="A",
        settled_participant_2="B",
        match_method="exact_event_id",
    )


class TestRankBanding:
    @pytest.mark.parametrize(
        "rank,expected",
        [
            (1, "1"),
            (2, "2-3"),
            (3, "2-3"),
            (4, "4-10"),
            (10, "4-10"),
            (11, "11-25"),
            (25, "11-25"),
            (26, "26-50"),
            (50, "26-50"),
            (51, "51+"),
            (507, "51+"),
            (None, "none"),
            (0, "none"),
            (-1, "none"),
        ],
    )
    def test_rank_band_edges(self, rank, expected):
        assert _rank_band(rank) == expected

    def test_bands_are_contiguous_and_ordered(self):
        labels = [label for label, _, _ in RANK_BANDS]
        assert labels == ["1", "2-3", "4-10", "11-25", "26-50", "51+"]
        # No gap or overlap between consecutive bands.
        for (_, _l1, h1), (_, l2, _h2) in zip(RANK_BANDS, RANK_BANDS[1:]):
            assert h1 is not None and l2 == h1 + 1

    def test_bands_pool_the_n1_cells_and_preserve_totals(self):
        grades = [_grade(r, GRADE_SUCCESS) for r in range(1, 61)]
        grades += [_grade(200, GRADE_UNRESOLVED)]
        summary = compute_rolling_summary(grades)

        per_rank = summary["per_rank"]
        banded = summary["per_rank_band"]

        # The audit view is untouched: one cell per distinct rank, mostly n=1.
        assert len(per_rank) == 61
        assert sum(cell["n"] for cell in per_rank.values()) == 61

        # The reportable view pools them, and conserves every row.
        assert sum(cell["n"] for cell in banded.values()) == 61
        assert banded["1"]["n"] == 1
        assert banded["2-3"]["n"] == 2
        assert banded["4-10"]["n"] == 7
        assert banded["11-25"]["n"] == 15
        assert banded["26-50"]["n"] == 25
        assert banded["51+"]["n"] == 11  # ranks 51-60 plus 200

    def test_bands_only_report_n30_when_n30(self):
        """A pooled cell that still has n<30 must show its n, not hide it."""
        grades = [_grade(r) for r in range(1, 8)]
        banded = compute_rolling_summary(grades)["per_rank_band"]
        assert banded["4-10"]["n"] == 4
        assert banded["4-10"]["n"] < 30  # explicitly below the reporting bar

    def test_per_rank_keys_sort_numerically_not_lexicographically(self):
        grades = [_grade(r) for r in (1, 2, 10, 11, 100, None)]
        keys = list(compute_rolling_summary(grades)["per_rank"])
        assert keys == ["1", "2", "10", "11", "100", "none"]

    def test_hit_rate_within_a_band_is_computed_on_pooled_rows(self):
        grades = [_grade(r, GRADE_SUCCESS) for r in range(4, 9)]
        grades += [_grade(r, GRADE_UNRESOLVED) for r in range(9, 11)]
        banded = compute_rolling_summary(grades)["per_rank_band"]
        assert banded["4-10"]["n"] == 7
        assert banded["4-10"]["successes"] == 5
        assert banded["4-10"]["unresolved"] == 2
        assert banded["4-10"]["hit_rate"] == pytest.approx(5 / 7)


# ---------------------------------------------------------------------------
# 6. Conflicting capture records must not decide a grade
# ---------------------------------------------------------------------------


def _pool_entry(event_id: str, rank: int) -> dict:
    """A considered_pool row written before identity serialisation: no index."""
    return {
        "sport": "football",
        "event_id": event_id,
        "event_date": TARGET_DATE,
        "considered_status": "ELIGIBLE_RANKED_BEYOND_TOP3",
        "rank_within_sport_day": rank,
    }


class TestConflictingCaptureRecords:
    def test_identical_duplicates_still_resolve(self, tmp_path):
        """The real 09-12 case: same key twice, same probabilities. No refusal."""
        tuples = [
            ["football", "football:201", TARGET_DATE, "Real Madrid", "Barcelona",
             0.45, 0.30, 0.25, "sha", "t"],
            ["football", "football:201", TARGET_DATE, "Real Madrid", "Barcelona",
             0.45, 0.30, 0.25, "sha", "t"],
        ]
        artifact = _write_artifact(
            tmp_path,
            settled=[_settled_event()],
            # No selections: the pool row must be the only candidate for this
            # key, otherwise _build_event_index prefers the selection entry and
            # the re-derivation path under test is never reached.
            selections=[],
            considered_pool=[_pool_entry("football:201", 4)],
            capture_record_tuples=tuples,
        )
        rows = {r["event_id"]: r for r in artifact["grades"]}
        pool_row = rows["football:201"]
        assert pool_row["underdog_index_provenance"] == "capture_record_tuples"
        assert pool_row["underdog_index"] == 2
        # The re-derived identity produced a real decision. Away (index 2) is
        # the underdog at 0.30 vs 0.45, and home won, so this is a FAILURE —
        # the point is that it is a *grade*, not an UNRESOLVED refusal.
        assert pool_row["grade"] == GRADE_FAILURE

    def test_conflicting_duplicates_refuse_the_identity(self, tmp_path):
        """Two records disagree, so neither may decide — last-wins would have."""
        tuples = [
            ["football", "football:201", TARGET_DATE, "Real Madrid", "Barcelona",
             0.45, 0.30, 0.25, "sha", "t"],
            # Same key, different probabilities: away now looks like the dog.
            ["football", "football:201", TARGET_DATE, "Real Madrid", "Barcelona",
             0.20, 0.60, 0.20, "sha2", "t"],
        ]
        artifact = _write_artifact(
            tmp_path,
            settled=[_settled_event()],
            # No selections: the pool row must be the only candidate for this
            # key, otherwise _build_event_index prefers the selection entry and
            # the re-derivation path under test is never reached.
            selections=[],
            considered_pool=[_pool_entry("football:201", 4)],
            capture_record_tuples=tuples,
        )
        rows = {r["event_id"]: r for r in artifact["grades"]}
        pool_row = rows["football:201"]
        assert pool_row["underdog_index_provenance"] == (
            "unavailable_conflicting_capture"
        )
        assert pool_row["underdog_index"] is None
        # Refused, not guessed: UNRESOLVED rather than a SUCCESS/FAILURE.
        assert pool_row["grade"] == GRADE_UNRESOLVED

    def test_entry_carried_identity_is_unaffected_by_a_conflict(self, tmp_path):
        """A row that carries its own identity must not be penalised."""
        tuples = [
            ["football", "football:201", TARGET_DATE, "Real Madrid", "Barcelona",
             0.45, 0.30, 0.25, "sha", "t"],
            ["football", "football:201", TARGET_DATE, "Real Madrid", "Barcelona",
             0.20, 0.60, 0.20, "sha2", "t"],
        ]
        artifact = _write_artifact(
            tmp_path,
            settled=[_settled_event()],
            capture_record_tuples=tuples,
        )
        sel_row = artifact["grades"][0]
        assert sel_row["underdog_index_provenance"] == "entry"
        assert sel_row["underdog_index"] == 2
