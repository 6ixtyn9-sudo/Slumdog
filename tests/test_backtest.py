"""Tests for the R1 rule backtest over the historical settlement corpus.

All fixtures are synthetic ledgers written directly to tmp_path in the
exact schema backfill.py writes (gzipped JSONL of asdict(SettledEvent)).
No network calls anywhere in this module.
"""
import datetime as dt
import gzip
import json
from dataclasses import asdict

import pytest

from slumdog.backtest import (
    KNOWN_LIMITATIONS,
    _cluster_bootstrap_surplus,
    _draw_model_discrimination,
    _all_outcome_probability_recalibration,
    _draw_probability_recalibration,
    _handball_draw_diagnostics,
    _low_draw_tail_analysis,
    r1_backtest,
)
from slumdog.contracts import SettledEvent


def _write_shadow_run(root, track_dir_name, event_date, run_id, selections):
    """A minimal shadow-track run directory: just enough for
    analyze._iter_track_runs to yield it (manifest.json + settlement.json
    present) and for backtest._pre_event_r1_selections to read the
    genuinely pre-event PRIMARY_SHADOW_SELECTION rows out of it."""
    run_dir = root / "data" / "reports" / track_dir_name / event_date / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(json.dumps({"run_id": run_id}))
    (run_dir / "settlement.json").write_text(json.dumps({"grades": []}))
    (run_dir / "shadow_selections.json").write_text(json.dumps({"selections": selections}))
    return run_dir


def _pre_event_selection(event_id, sport, event_date, *, favorite_index, underdog_index,
                          favorite_probability, underdog_probability, draw_probability=None,
                          status="PRIMARY_SHADOW_SELECTION"):
    return {
        "status": status,
        "event_id": event_id,
        "sport": sport,
        "event_date": event_date,
        "favorite_index": favorite_index,
        "favorite_probability": favorite_probability,
        "underdog_index": underdog_index,
        "underdog_probability": underdog_probability,
        "draw_probability": draw_probability,
    }


def _ev(event_id, sport, event_date, p1, p2, winner_index, *,
        probability_1=0.6, probability_2=0.4, draw_probability=None,
        forebet_pick=1, disposition="SETTLED", reconstruction="HISTORICAL_PAGE",
        league=""):
    return SettledEvent(
        event_id=event_id, sport=sport, event_date=event_date,
        participant_1=p1, participant_2=p2, winner_index=winner_index,
        score_1=1.0, score_2=0.0,
        probability_1=probability_1, probability_2=probability_2,
        draw_probability=draw_probability, forebet_pick=forebet_pick,
        disposition=disposition, reconstruction=reconstruction, league=league,
    )


def _write_ledger(root, sport, events):
    reports = root / "data" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / f"history_{sport}.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(asdict(ev), sort_keys=True) + "\n")
    return path


def _date(offset_days):
    base = dt.date(2025, 1, 1)
    return (base + dt.timedelta(days=offset_days)).isoformat()


def _priming_games(sport, team, opponents_prefix, start_offset, count=5):
    """``team`` beats a fresh filler opponent in each of ``count`` games,
    strictly before the test event, giving it exactly ``count`` prior
    games (>= the R2 eligibility floor of 5)."""
    return [
        _ev(f"{team}-prime-{i}", sport, _date(start_offset + i),
            team, f"{opponents_prefix}{i}", winner_index=1)
        for i in range(count)
    ]


def _build_eligible_scenario(sport="football", *, winner_index, test_event_date=_date(100)):
    """A minimal, fully R2-eligible sport-day: TeamA (underdog) vs TeamB
    (favourite), each with exactly 5 prior games and one prior h2h."""
    events = []
    events += _priming_games(sport, "TeamA", "FillerA", 0)
    events += _priming_games(sport, "TeamB", "FillerB", 10)
    # One prior h2h meeting, well before the test date.
    events.append(_ev("h2h-1", sport, _date(50), "TeamA", "TeamB", winner_index=1))
    # The test event itself: TeamB is favourite (p=0.6), TeamA underdog (p=0.4).
    events.append(_ev(
        "test-event", sport, test_event_date, "TeamB", "TeamA",
        winner_index=winner_index, probability_1=0.6, probability_2=0.4,
        forebet_pick=1,
    ))
    return events


class TestNoLedgerPresent:
    def test_no_ledgers_reports_plainly(self, tmp_path):
        path = r1_backtest(tmp_path, target_date="2026-01-01")
        analysis = json.loads(path.read_text())
        assert analysis["corpus_inventory"]["sports_with_a_ledger_in_this_checkout"] == 0
        assert "nothing to score" in analysis["headline"]
        for sport, info in analysis["corpus_inventory"]["per_sport"].items():
            assert info["available"] is False

    def test_markdown_is_generated_too(self, tmp_path):
        path = r1_backtest(tmp_path, target_date="2026-01-01")
        md_path = path.with_suffix(".md")
        assert md_path.is_file()
        text = md_path.read_text()
        assert "R1 rule backtest" in text
        assert "caveat" in text.lower()


class TestKnownLimitationsAlwaysPresent:
    def test_historical_page_caveat_and_void_caveat_both_present(self, tmp_path):
        path = r1_backtest(tmp_path, target_date="2026-01-01")
        analysis = json.loads(path.read_text())
        assert len(analysis["known_limitations"]) == 2
        assert any("HISTORICAL_PAGE" in note for note in analysis["known_limitations"])
        assert any("VOID" in note for note in analysis["known_limitations"])
        assert analysis["known_limitations"] == list(KNOWN_LIMITATIONS)


class TestReconstructionAndGrading:
    def test_underdog_win_is_reconstructed_and_graded_success(self, tmp_path):
        events = _build_eligible_scenario(winner_index=2)  # TeamA (underdog=p2) wins
        _write_ledger(tmp_path, "football", events)
        path = r1_backtest(tmp_path, target_date="2026-01-01")
        analysis = json.loads(path.read_text())
        assert analysis["reconstruction_populations_present"] == ["HISTORICAL_PAGE"]
        pop = analysis["populations"]["HISTORICAL_PAGE"]
        assert pop["overall"]["n"] == 1
        assert pop["overall"]["successes"] == 1

    def test_favourite_win_is_graded_failure(self, tmp_path):
        events = _build_eligible_scenario(winner_index=1)  # TeamB (favourite) wins
        _write_ledger(tmp_path, "football", events)
        path = r1_backtest(tmp_path, target_date="2026-01-01")
        analysis = json.loads(path.read_text())
        pop = analysis["populations"]["HISTORICAL_PAGE"]
        assert pop["overall"]["n"] == 1
        assert pop["overall"]["successes"] == 0

    def test_settled_draw_counts_as_a_loss_and_is_flagged(self, tmp_path):
        events = _build_eligible_scenario(winner_index=1)
        events[-1] = _ev(
            "test-event", "football", _date(100), "TeamB", "TeamA",
            winner_index=0, probability_1=0.6, probability_2=0.4,
            forebet_pick=1, disposition="SETTLED_DRAW",
        )
        _write_ledger(tmp_path, "football", events)
        path = r1_backtest(tmp_path, target_date="2026-01-01")
        analysis = json.loads(path.read_text())
        pop = analysis["populations"]["HISTORICAL_PAGE"]
        assert pop["overall"]["n"] == 1
        assert pop["overall"]["successes"] == 0
        assert pop["overall"]["settled_draws"] == 1

    def test_void_row_is_excluded_from_the_candidate_pool_entirely(self, tmp_path):
        events = _build_eligible_scenario(winner_index=2)
        events[-1] = _ev(
            "test-event", "football", _date(100), "TeamB", "TeamA",
            winner_index=0, probability_1=0.6, probability_2=0.4,
            forebet_pick=1, disposition="VOID",
        )
        _write_ledger(tmp_path, "football", events)
        path = r1_backtest(tmp_path, target_date="2026-01-01")
        analysis = json.loads(path.read_text())
        # No HISTORICAL_PAGE rows at all: the VOID test event never enters
        # the ranked pool (load_valid_history drops VOID upstream) and the
        # priming/h2h games are never R2-eligible on their own (thin prior
        # history for each other).
        assert analysis["reconstruction_populations_present"] == []

    def test_thin_history_is_not_eligible(self, tmp_path):
        # Only 2 prior games each -- below the underdog/favourite_prior_games
        # >= 5 floor -- so the test event must never be ranked.
        events = []
        events += _priming_games("football", "TeamA", "FillerA", 0, count=2)
        events += _priming_games("football", "TeamB", "FillerB", 10, count=2)
        events.append(_ev("h2h-1", "football", _date(50), "TeamA", "TeamB", winner_index=1))
        events.append(_ev(
            "test-event", "football", _date(100), "TeamB", "TeamA",
            winner_index=2, probability_1=0.6, probability_2=0.4, forebet_pick=1,
        ))
        _write_ledger(tmp_path, "football", events)
        path = r1_backtest(tmp_path, target_date="2026-01-01")
        analysis = json.loads(path.read_text())
        assert analysis["reconstruction_populations_present"] == []
        assert "nothing to score" in analysis["headline"]

    def test_wide_probability_gap_is_not_eligible(self, tmp_path):
        events = _build_eligible_scenario(winner_index=2)
        # gap = 0.9-0.1 = 0.8 > the 0.2 R2 ceiling.
        events[-1] = _ev(
            "test-event", "football", _date(100), "TeamB", "TeamA",
            winner_index=2, probability_1=0.9, probability_2=0.1, forebet_pick=1,
        )
        _write_ledger(tmp_path, "football", events)
        path = r1_backtest(tmp_path, target_date="2026-01-01")
        analysis = json.loads(path.read_text())
        assert analysis["reconstruction_populations_present"] == []


class TestClusterBootstrap:
    def test_calendar_day_primary_and_block_sensitivity_are_deterministic(self):
        records = []
        for day in range(1, 11):
            date = f"2026-01-{day:02d}"
            for sport in ("football", "handball"):
                # Identical same-day errors across sports: exactly the
                # cross-sport dependence calendar-day blocks preserve.
                records.append((sport, date, "<0.20", 0.10,
                                1 if day % 2 == 0 else 0))
        first = _cluster_bootstrap_surplus(
            records, ["<0.20"], replicates=200, seed=7)
        second = _cluster_bootstrap_surplus(
            records, ["<0.20"], replicates=200, seed=7)
        assert first == second
        assert first["primary_block"] == "calendar_day_PRIMARY"
        schemes = first["schemes"]
        assert schemes["calendar_day_PRIMARY"]["blocks"] == 10
        assert schemes["sport_day"]["blocks"] == 20
        assert schemes["calendar_month"]["blocks"] == 1
        assert schemes["calendar_day_PRIMARY"]["buckets"]["<0.20"][
            "valid_replicates"] == 200
        assert "cross-sport dependence" in first["primary_block_reason"]


class TestBaselinesAndBands:
    def test_calibration_compares_observed_to_assigned_probability_same_rows(
            self, tmp_path):
        events = _build_eligible_scenario(winner_index=2)
        _write_ledger(tmp_path, "football", events)
        path = r1_backtest(tmp_path, target_date="2026-01-01")
        analysis = json.loads(path.read_text())
        scorecard = analysis["populations"]["HISTORICAL_PAGE"]
        calibration = scorecard["calibration"]
        dog = calibration["overall"]["r1_underdog"]
        assert dog["n"] == 1
        assert dog["successes"] == 1
        assert dog["mean_predicted_probability"] == pytest.approx(0.4)
        assert dog["hit_rate"] == pytest.approx(1.0)
        assert dog["observed_minus_predicted"] == pytest.approx(0.6)
        favourite = calibration["overall"]["favourite_control"]
        assert favourite["n"] == 1
        assert favourite["mean_predicted_probability"] == pytest.approx(0.6)
        assert favourite["hit_rate"] == pytest.approx(0.0)
        assert favourite["observed_minus_predicted"] == pytest.approx(-0.6)
        assert calibration["by_underdog_probability_band"]["0.40+"][
            "r1_underdog"] == dog
        assert calibration["by_sport"]["football"]["r1_underdog"] == dog
        assert "PRIMARY MERIT METRIC" in calibration["interpretation"]
        assert "NOT THE MERIT TEST" in scorecard["raw_hit_rate_note"]

    def test_calibration_section_precedes_raw_hit_rates_in_markdown(self, tmp_path):
        events = _build_eligible_scenario(winner_index=2)
        _write_ledger(tmp_path, "football", events)
        path = r1_backtest(tmp_path, target_date="2026-01-01")
        text = path.with_suffix(".md").read_text()
        calibration_pos = text.find("PRIMARY MERIT METRIC")
        raw_pos = text.find("Raw hit rates (descriptive baselines")
        assert calibration_pos != -1
        assert raw_pos != -1
        assert calibration_pos < raw_pos
        assert "Observed - predicted" in text
        assert "Favourite control" in text

    def test_draw_space_split_keeps_two_way_clean_and_draw_differential(self,
                                                                        tmp_path):
        football = _build_eligible_scenario(sport="football", winner_index=2)
        basketball = _build_eligible_scenario(
            sport="basketball", winner_index=2,
            test_event_date=_date(101))
        hockey = _build_eligible_scenario(
            sport="hockey", winner_index=2,
            test_event_date=_date(102))
        _write_ledger(tmp_path, "football", football)
        _write_ledger(tmp_path, "basketball", basketball)
        _write_ledger(tmp_path, "hockey", hockey)
        path = r1_backtest(tmp_path, target_date="2026-01-01")
        scorecard = json.loads(path.read_text())["populations"]["HISTORICAL_PAGE"]
        split = scorecard["draw_space_split"]
        assert split["two_way_sports"]["sports"] == ["basketball", "hockey"]
        assert split["draw_capable_sports"]["sports"] == ["football"]
        pooled = split["two_way_sports"]["pooled"]
        assert pooled["n"] == 2
        assert pooled["mean_predicted_probability"] == pytest.approx(0.4)
        assert pooled["hit_rate"] == pytest.approx(1.0)
        assert pooled["observed_minus_predicted"] == pytest.approx(0.6)
        assert pooled["surplus_wilson_95_lo"] == pytest.approx(
            pooled["wilson_95_lo"] - 0.4)
        assert pooled["indicative_only_n_lt_500"] is True
        draw = split["draw_capable_sports"]["pooled"]
        assert draw["n"] == 1
        assert draw["differential_surplus"] == pytest.approx(1.2)
        assert "paired" in draw["differential_interval_method"]
        coverage = scorecard["coverage"]
        assert coverage["distinct_sport_days"] == 3
        assert coverage["mean_r1_picks_per_sport_day"] == pytest.approx(1.0)

    def test_temporal_holdout_is_strictly_after_predeclared_cutoff(self, tmp_path):
        events = _build_eligible_scenario(sport="tennis", winner_index=2)
        events.append(_ev(
            "holdout-event", "tennis", "2026-07-15", "TeamB", "TeamA",
            winner_index=1, probability_1=0.6, probability_2=0.4,
            forebet_pick=1,
        ))
        _write_ledger(tmp_path, "tennis", events)
        path = r1_backtest(tmp_path, target_date="2026-08-01")
        holdout = json.loads(path.read_text())["populations"]["HISTORICAL_PAGE"][
            "temporal_holdout"]
        assert holdout["cutoff"] == "2026-06-30"
        tennis = holdout["per_sport"]["tennis"]
        assert tennis["development_through_cutoff"]["n"] == 1
        assert tennis["development_through_cutoff"]["observed_minus_predicted"] == pytest.approx(0.6)
        assert tennis["holdout_after_cutoff"]["n"] == 1
        assert tennis["holdout_after_cutoff"]["observed_minus_predicted"] == pytest.approx(-0.4)
        assert tennis["indicative_only_holdout_n_lt_500"] is True
        assert "multiple sports" in holdout["multiplicity_warning"]

    def test_signal_wide_analysis_uses_every_eligible_row_and_clusters_by_day(
            self, tmp_path):
        events = _build_eligible_scenario(sport="tennis", winner_index=2)
        events.append(_ev(
            "second-eligible", "tennis", _date(100), "TeamB", "TeamA",
            winner_index=2, probability_1=0.58, probability_2=0.42,
            forebet_pick=1,
        ))
        _write_ledger(tmp_path, "tennis", events)
        path = r1_backtest(tmp_path, target_date="2026-01-01")
        analysis = json.loads(path.read_text())
        # Only one row is daily R1, but both eligible rows enter the signal.
        assert analysis["populations"]["HISTORICAL_PAGE"]["overall"]["n"] == 1
        signal = analysis["eligible_underdog_signal"]
        tennis = signal["per_sport"]["tennis"]["all"]
        assert tennis["calibration"]["n"] == 2
        assert tennis["candidate_frequency"]["candidate_rows"] == 2
        assert tennis["candidate_frequency"]["active_days"] == 1
        assert tennis["candidate_frequency"]["mean_candidates_per_active_day"] == 2
        bootstrap = tennis["calendar_day_cluster_bootstrap"]
        assert bootstrap["primary_block"] == "calendar_day_PRIMARY"
        assert bootstrap["schemes"]["calendar_day_PRIMARY"]["blocks"] == 1
        favourite = tennis["favourite_control"]
        assert favourite["n"] == 2
        assert favourite["observed_minus_predicted"] == pytest.approx(-0.59)
        differential = tennis["differential"]
        assert differential["n"] == 2
        assert differential["observed_minus_predicted"] == pytest.approx(1.18)
        differential_bootstrap = tennis[
            "differential_calendar_day_cluster_bootstrap"]
        differential_bucket = differential_bootstrap["schemes"][
            "calendar_day_PRIMARY"]["buckets"]["all"]
        assert differential_bucket["bootstrap_95_lo"] == pytest.approx(1.18)
        assert differential_bucket["bootstrap_95_hi"] == pytest.approx(1.18)
        assert tennis["favourite_calendar_day_cluster_bootstrap"]["schemes"][
            "calendar_day_PRIMARY"]["blocks"] == 1
        assert signal["cutoff"] == "2026-06-30"
        assert "shared across sports" in signal["block_reason"]
        variant = analysis["negative_sport_gate_variant"]
        assert variant["status"] == "RETIRED_FAILED_DEVELOPMENT_OUTCOME_SPACE_TEST"
        assert variant["excluded_sports_selected_on_development_only"] == []
        development_variant = variant["development_through_cutoff"]
        assert development_variant[
            "pooled_raw_surplus_prohibited_due_to_outcome_space_mix_shift"] is True
        assert development_variant["two_way"]["rows_removed"] == 0
        assert development_variant["draw_capable"]["rows_removed"] == 0
        assert "outcome-space mix" in variant["warning"]

    def test_three_outcome_map_uses_all_draw_capable_settled_rows(self, tmp_path):
        event = _ev(
            "draw-1", "football", _date(1), "Home", "Away", winner_index=0,
            probability_1=0.4, probability_2=0.3, draw_probability=0.3,
            disposition="SETTLED_DRAW",
        )
        _write_ledger(tmp_path, "football", [event])
        path = r1_backtest(tmp_path, target_date="2026-01-01")
        outcome_map = json.loads(path.read_text())["three_outcome_calibration_map"]
        assert outcome_map["sports"] == ["football"]
        assert outcome_map["settled_rows_in_draw_capable_sports"] == 1
        draw = outcome_map["pooled"]["draw"]["buckets"]["0.30-0.35"]
        assert draw["n"] == 1
        assert draw["mean_predicted_probability"] == pytest.approx(0.3)
        assert draw["hit_rate"] == pytest.approx(1.0)
        assert draw["observed_minus_predicted"] == pytest.approx(0.7)
        assert draw["indicative_only_n_lt_500"] is True
        home = outcome_map["pooled"]["home"]["buckets"]["0.35+"]
        assert home["hit_rate"] == pytest.approx(0.0)
        assert home["observed_minus_predicted"] == pytest.approx(-0.4)

    def test_low_draw_tail_reports_relative_surplus_frequency_and_two_blocks(
            self, tmp_path):
        events = [
            _ev(f"draw-tail-{index}", "football", f"2026-01-0{index}",
                "Home", "Away", winner_index=0,
                probability_1=0.60, probability_2=0.36 - probability,
                draw_probability=probability, disposition="SETTLED_DRAW")
            for index, probability in enumerate((0.04, 0.08, 0.12, 0.18), start=1)
        ]
        _write_ledger(tmp_path, "football", events)
        analysis = json.loads(r1_backtest(
            tmp_path, target_date="2026-02-01").read_text())[
                "low_draw_tail_analysis"]
        assert analysis["bucket_contract"] == [
            "<0.05", "0.05-0.10", "0.10-0.15", "0.15-0.20"]
        development = analysis["pooled"]["development_through_cutoff"]
        assert "ledger-valid settled rows" in development["population"]
        assert "0.005 genuine-forecast floor" in development["population"]
        assert development["parent_lt_0_20_n"] == 4
        assert development["frozen_shape_verdict"] == (
            "mixed or unresolved; do not call it a power play")
        bucket = development["buckets"]["<0.05"]
        assert bucket["n"] == 1
        assert bucket["mean_predicted_probability"] == pytest.approx(0.04)
        assert bucket["observed_hit_rate"] == pytest.approx(1.0)
        assert bucket["absolute_surplus"] == pytest.approx(0.96)
        assert bucket[
            "relative_surplus_observed_divided_by_predicted"] == pytest.approx(25.0)
        assert bucket["candidate_rows_per_active_day"] == pytest.approx(1.0)
        bootstrap = analysis["per_sport"]["football"][
            "development_through_cutoff"]["cluster_bootstrap"]
        assert set(bootstrap["schemes"]) == {
            "calendar_day_PRIMARY", "calendar_month"}
        assert "extreme_tail_power_play_shape" in analysis[
            "predeclared_shape_interpretation"]
        assert "no-result" in analysis["draw_outcome_semantics"]["cricket"]
        assert "split fight draw" in analysis["draw_outcome_semantics"]["mma"]
        assert analysis["pooled"]["validated_shape_verdict"] == "NOT VALIDATED"
        composition = analysis["retained_lt_0_05_composition"][
            "development_through_cutoff"]
        assert composition["pooled_retained_lt_0_05_n"] == 1
        assert composition["sports"]["football"]["n"] == 1
        assert composition["sports"]["football"][
            "share_of_pooled_retained_lt_0_05"] == pytest.approx(1.0)

    def test_low_draw_tail_excludes_zero_like_and_two_outcome_board_rows(
            self, tmp_path):
        cricket = _ev(
            "cricket-zero-like", "cricket", "2026-01-01", "A", "B",
            winner_index=0, probability_1=0.60, probability_2=0.3999,
            draw_probability=0.0001, disposition="SETTLED_DRAW")
        mma = _ev(
            "mma-two-outcome", "mma", "2026-01-01", "A", "B",
            winner_index=0, probability_1=0.60, probability_2=0.37,
            draw_probability=0.03, disposition="SETTLED_DRAW")
        analysis = _low_draw_tail_analysis([cricket, mma])
        cricket_audit = analysis["per_sport"]["cricket"][
            "development_through_cutoff"]["forecast_exclusion_audit"]
        assert cricket_audit["raw_numeric_lt_0_20_n_before_forecast_filter"] == 1
        assert cricket_audit["excluded"][
            "below_0_005_no_forecast_floor"] == 1
        assert cricket_audit["retained_lt_0_20_n"] == 0
        mma_audit = analysis["per_sport"]["mma"][
            "development_through_cutoff"]["forecast_exclusion_audit"]
        assert mma_audit["excluded"][
            "sport_board_does_not_publish_draw_probability"] == 1
        assert mma_audit["retained_lt_0_20_n"] == 0
        assert analysis["genuine_draw_forecast_contract"][
            "minimum_probability"] == pytest.approx(0.005)

    def test_handball_walk_forward_uses_fixed_quarters_and_predeclared_rule(self):
        events = []
        index = 0
        for year in (2024, 2025, 2026):
            for quarter in (1, 2, 3, 4):
                if year == 2026 and quarter == 4:
                    continue
                month = (quarter - 1) * 3 + 1
                events.append(_ev(
                    f"hb-{index}", "handball", f"{year}-{month:02d}-15",
                    "Home", "Away", winner_index=0,
                    probability_1=0.60, probability_2=0.37,
                    draw_probability=0.03, disposition="SETTLED_DRAW",
                    league="Test League"))
                index += 1
        result = _handball_draw_diagnostics(events)
        assert len(result["walk_forward_folds"]) == 11
        assert result["walk_forward_folds"][0]["fold"] == "2024-Q1"
        assert result["walk_forward_folds"][-1]["fold"] == "2026-Q3"
        assert result["persistence_summary"] == {
            "total_fixed_folds": 11,
            "nonempty_folds": 11,
            "empty_folds": [],
            "positive_sign_folds": 11,
            "month_interval_excludes_zero_positive_folds": 11,
            "final_two_nonempty_positive": True,
            "persistence_rule_met": True,
        }
        league = result["league_concentration"]["leagues"][0]
        assert league["league"] == "Test League"
        assert league["share_of_handball_tail"] == pytest.approx(1.0)
        assert result["full_draw_calibration_curve"]["<0.05"]["n"] == 11
        assert "75%" in result["predeclared_persistence_rule"]

    def test_draw_discrimination_reports_curve_range_and_month_bootstrap(self):
        football = [
            _ev(f"f-{index}", "football", f"2026-01-{index + 1:02d}",
                "Home", "Away", winner_index=(0 if index >= 2 else 1),
                probability_1=0.60, probability_2=0.39 - probability,
                draw_probability=probability,
                disposition=("SETTLED_DRAW" if index >= 2 else "SETTLED"))
            for index, probability in enumerate((0.01, 0.08, 0.18, 0.28))
        ]
        mma = [_ev(
            "mma", "mma", "2026-01-01", "A", "B", winner_index=0,
            probability_1=0.6, probability_2=0.37, draw_probability=0.03,
            disposition="SETTLED_DRAW")]
        result = _draw_model_discrimination(football + mma)
        assert set(result["per_sport"]) == {"football"}
        football_result = result["per_sport"]["football"]
        assert football_result["n"] == 4
        assert football_result[
            "spearman_rank_correlation_predicted_draw_vs_realised_draw"] > 0
        assert football_result["spearman_valid_replicates"] == 1000
        observed_range = football_result[
            "observed_rate_range_across_nonempty_bands"]
        assert observed_range == {"minimum": 0.0, "maximum": 1.0, "range": 1.0}
        assert football_result["full_draw_calibration_curve"]["0.15-0.20"][
            "n"] == 1

    def test_one_parameter_recalibration_fits_development_only_and_scores_holdout(self):
        events = []
        for sport in ("football", "handball"):
            for index, (day, probability, draw) in enumerate((
                ("2025-01-10", 0.05, False),
                ("2025-04-10", 0.15, False),
                ("2025-07-10", 0.35, True),
                ("2026-01-10", 0.45, True),
                ("2026-07-10", 0.10, False),
                ("2026-08-10", 0.40, True),
            )):
                events.append(_ev(
                    f"{sport}-{index}", sport, day, "Home", "Away",
                    winner_index=0 if draw else 1,
                    probability_1=0.55,
                    probability_2=0.45 - probability,
                    draw_probability=probability,
                    disposition="SETTLED_DRAW" if draw else "SETTLED"))
        result = _draw_probability_recalibration(events)
        assert set(result["per_sport"]) == {"football", "handball"}
        for sport_result in result["per_sport"].values():
            fit = sport_result["development_fit"]
            assert fit["n"] == 4
            assert 0 <= fit["shrink_coefficient"] <= 1
            holdout = sport_result["holdout_evaluation"]
            assert holdout["n"] == 2
            assert "brier_before" in holdout and "log_loss_after" in holdout
            assert "brier_base_rate_only" in holdout
            assert "brier_information_gain_base_minus_shrink" in holdout
            assert "log_loss_base_rate_only" in holdout
            assert "information_verdict" in sport_result
            assert len(sport_result["sequential_quarterly_folds"]) == 11
            assert "<0.05" in sport_result["holdout_recalibrated_curve"]
        assert "both sports pass" in result["predeclared_real_improvement_rule"]
        outcomes = _all_outcome_probability_recalibration(events, result)
        assert set(outcomes["outcomes"]) == {"home_win", "away_win", "draw"}
        assert outcomes["outcomes"]["draw"] is result
        assert outcomes["outcomes"]["home_win"]["per_sport"]["football"][
            "development_fit"]["n"] == 4

    def test_baselines_computed_on_the_same_rows(self, tmp_path):
        events = _build_eligible_scenario(winner_index=2)
        _write_ledger(tmp_path, "football", events)
        path = r1_backtest(tmp_path, target_date="2026-01-01")
        analysis = json.loads(path.read_text())
        pop = analysis["populations"]["HISTORICAL_PAGE"]
        baselines = pop["baselines_same_rows"]
        assert baselines["our_r1_pick"]["n"] == 1
        assert baselines["always_favourite_same_rows"]["n"] == 1
        # Favourite (TeamB) did NOT win (underdog TeamA won) -> favourite bet fails.
        assert baselines["always_favourite_same_rows"]["successes"] == 0
        assert baselines["forebet_pick_same_rows"]["n"] == 1

    def test_probability_band_bucketing(self, tmp_path):
        events = _build_eligible_scenario(winner_index=2)  # underdog prob 0.4 -> "0.40+"
        _write_ledger(tmp_path, "football", events)
        path = r1_backtest(tmp_path, target_date="2026-01-01")
        analysis = json.loads(path.read_text())
        pop = analysis["populations"]["HISTORICAL_PAGE"]
        assert pop["by_underdog_probability_band"]["0.40+"]["n"] == 1
        assert pop["by_underdog_probability_band"]["<0.20"]["n"] == 0

    def test_small_n_is_flagged_not_significant(self, tmp_path):
        events = _build_eligible_scenario(winner_index=2)
        _write_ledger(tmp_path, "football", events)
        path = r1_backtest(tmp_path, target_date="2026-01-01")
        analysis = json.loads(path.read_text())
        pop = analysis["populations"]["HISTORICAL_PAGE"]
        assert pop["overall"]["significant_n"] is False
        assert "n too small" in pop["overall"]["note"]


class TestTracksAndSportsNeverPooled:
    def test_two_sports_are_reported_separately_in_by_sport(self, tmp_path):
        football_events = _build_eligible_scenario(sport="football", winner_index=2)
        _write_ledger(tmp_path, "football", football_events)
        handball_events = _build_eligible_scenario(sport="handball", winner_index=1)
        _write_ledger(tmp_path, "handball", handball_events)
        path = r1_backtest(tmp_path, target_date="2026-01-01")
        analysis = json.loads(path.read_text())
        pop = analysis["populations"]["HISTORICAL_PAGE"]
        assert set(pop["by_sport"]) == {"football", "handball"}
        assert pop["by_sport"]["football"]["successes"] == 1
        assert pop["by_sport"]["handball"]["successes"] == 0
        # Combined overall still reflects both, but never silently merges
        # a sport's identity away -- by_sport keeps them addressable.
        assert pop["overall"]["n"] == 2

    def test_sport_without_a_ledger_is_reported_unavailable_not_zero(self, tmp_path):
        football_events = _build_eligible_scenario(sport="football", winner_index=2)
        _write_ledger(tmp_path, "football", football_events)
        path = r1_backtest(tmp_path, target_date="2026-01-01")
        analysis = json.loads(path.read_text())
        per_sport = analysis["corpus_inventory"]["per_sport"]
        assert per_sport["rugby"]["available"] is False
        assert "does not exist" in per_sport["rugby"]["note"]
        assert per_sport["football"]["available"] is True

    def test_current_only_sports_are_never_checked(self, tmp_path):
        path = r1_backtest(tmp_path, target_date="2026-01-01")
        analysis = json.loads(path.read_text())
        per_sport = analysis["corpus_inventory"]["per_sport"]
        assert "esoccer" not in per_sport
        assert "afl" not in per_sport


class TestContaminationCheck:
    def test_no_pre_event_evidence_is_insufficient_data(self, tmp_path):
        events = _build_eligible_scenario(winner_index=2)
        _write_ledger(tmp_path, "football", events)
        path = r1_backtest(tmp_path, target_date="2026-01-01")
        analysis = json.loads(path.read_text())
        verdict = analysis["provenance_verdict"]
        assert verdict["STANDARD"]["verdict"] == "INSUFFICIENT_DATA"
        assert verdict["EVENT_DAY"]["verdict"] == "INSUFFICIENT_DATA"
        assert verdict["STANDARD"]["pre_event_picks_available"] == 0

    def test_matching_pre_event_capture_is_identical(self, tmp_path):
        events = _build_eligible_scenario(winner_index=2)
        _write_ledger(tmp_path, "football", events)
        _write_shadow_run(
            tmp_path, "shadow", _date(100), "run-1",
            [_pre_event_selection(
                "test-event", "football", _date(100),
                favorite_index=1, underdog_index=2,
                favorite_probability=0.6, underdog_probability=0.4,
            )],
        )
        path = r1_backtest(tmp_path, target_date="2026-01-01")
        analysis = json.loads(path.read_text())
        check = analysis["provenance_verdict"]["STANDARD"]
        assert check["verdict"] == "IDENTICAL"
        assert check["matched_to_historical_ledger"] == 1
        assert check["differing_count"] == 0
        assert check["underdog_identity_flipped_count"] == 0
        # The headline must say the rates are a real measurement, not a
        # footnoted upper bound, when the check comes back clean.
        assert "measurement, not just an upper bound" in analysis["headline"]

    def test_recomputed_probability_is_flagged_differing(self, tmp_path):
        events = _build_eligible_scenario(winner_index=2)
        _write_ledger(tmp_path, "football", events)
        _write_shadow_run(
            tmp_path, "shadow", _date(100), "run-1",
            [_pre_event_selection(
                "test-event", "football", _date(100),
                favorite_index=1, underdog_index=2,
                favorite_probability=0.5, underdog_probability=0.5,  # differs from ledger's 0.6/0.4
            )],
        )
        path = r1_backtest(tmp_path, target_date="2026-01-01")
        analysis = json.loads(path.read_text())
        check = analysis["provenance_verdict"]["STANDARD"]
        assert check["verdict"] == "DIFFERING"
        assert check["differing_count"] == 1
        assert check["max_absolute_probability_delta_seen"] is not None
        assert check["max_absolute_probability_delta_seen"] > 0.005
        # The headline must say EVERY rate is an upper bound, loudly, not in
        # a footnote, exactly per the owner directive.
        assert "EVERY rate below is an upper bound" in analysis["headline"]

    def test_underdog_side_flip_is_flagged_even_if_probabilities_are_close(self, tmp_path):
        events = _build_eligible_scenario(winner_index=2)
        _write_ledger(tmp_path, "football", events)
        _write_shadow_run(
            tmp_path, "shadow", _date(100), "run-1",
            [_pre_event_selection(
                "test-event", "football", _date(100),
                favorite_index=2, underdog_index=1,  # flipped vs. ledger's identity
                favorite_probability=0.6, underdog_probability=0.4,
            )],
        )
        path = r1_backtest(tmp_path, target_date="2026-01-01")
        analysis = json.loads(path.read_text())
        check = analysis["provenance_verdict"]["STANDARD"]
        assert check["verdict"] == "DIFFERING"
        assert check["underdog_identity_flipped_count"] == 1

    def test_tracks_are_never_pooled_in_the_verdict(self, tmp_path):
        events = _build_eligible_scenario(winner_index=2)
        _write_ledger(tmp_path, "football", events)
        _write_shadow_run(
            tmp_path, "shadow", _date(100), "run-1",
            [_pre_event_selection(
                "test-event", "football", _date(100),
                favorite_index=1, underdog_index=2,
                favorite_probability=0.6, underdog_probability=0.4,
            )],
        )
        path = r1_backtest(tmp_path, target_date="2026-01-01")
        analysis = json.loads(path.read_text())
        verdict = analysis["provenance_verdict"]
        assert verdict["STANDARD"]["verdict"] == "IDENTICAL"
        assert verdict["EVENT_DAY"]["verdict"] == "INSUFFICIENT_DATA"
        assert verdict["EVENT_DAY"]["pre_event_picks_available"] == 0

    def test_statistical_power_note_distinguishes_gross_vs_small_bias(self, tmp_path):
        events = _build_eligible_scenario(winner_index=2)
        _write_ledger(tmp_path, "football", events)
        _write_shadow_run(
            tmp_path, "shadow", _date(100), "run-1",
            [_pre_event_selection(
                "test-event", "football", _date(100),
                favorite_index=1, underdog_index=2,
                favorite_probability=0.6, underdog_probability=0.4,
            )],
        )
        path = r1_backtest(tmp_path, target_date="2026-01-01")
        analysis = json.loads(path.read_text())
        note = analysis["provenance_verdict"]["STANDARD"]["statistical_power_note"]
        assert "n=1" in note
        assert "gross, systematic recomputation" in note
        assert "NOT enough to bound a small" in note

    def test_provenance_verdict_is_rendered_at_the_top_of_the_markdown(self, tmp_path):
        events = _build_eligible_scenario(winner_index=2)
        _write_ledger(tmp_path, "football", events)
        path = r1_backtest(tmp_path, target_date="2026-01-01")
        text = path.with_suffix(".md").read_text()
        verdict_pos = text.find("VERDICT FIRST")
        caveat_pos = text.find("The caveat that must not be buried")
        assert verdict_pos != -1
        assert caveat_pos != -1
        assert verdict_pos < caveat_pos


class TestCliNeverFailsTheJob:
    def test_r1_backtest_cli_exits_zero_even_when_the_engine_raises(self, tmp_path, monkeypatch):
        import sys
        import slumdog.cli as cli

        def _boom(root, target_date=None):
            raise RuntimeError("simulated unexpected failure")

        monkeypatch.setattr(cli, "r1_backtest", _boom)
        monkeypatch.setattr(
            sys, "argv",
            ["slumdog", "r1-backtest", "--root", str(tmp_path), "--date", "2026-01-01"])
        rc = cli.main()
        assert rc == 0

    def test_r1_backtest_cli_exits_zero_on_the_happy_path(self, tmp_path, monkeypatch):
        import sys
        import slumdog.cli as cli

        monkeypatch.setattr(
            sys, "argv",
            ["slumdog", "r1-backtest", "--root", str(tmp_path), "--date", "2026-01-01"])
        rc = cli.main()
        assert rc == 0
        assert (tmp_path / "data" / "reports" / "r1_backtest_2026-01-01.json").is_file()


class TestCorpusInventory:
    def test_row_counts_and_date_range_reported_before_scoring(self, tmp_path):
        events = _build_eligible_scenario(winner_index=2)
        _write_ledger(tmp_path, "football", events)
        path = r1_backtest(tmp_path, target_date="2026-01-01")
        analysis = json.loads(path.read_text())
        info = analysis["corpus_inventory"]["per_sport"]["football"]
        assert info["settled_row_count"] == len(events)
        assert info["date_range"][0] == _date(0)
        assert info["date_range"][1] == _date(100)
