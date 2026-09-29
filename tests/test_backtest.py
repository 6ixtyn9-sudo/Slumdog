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

from slumdog.backtest import r1_backtest, KNOWN_LIMITATIONS
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
        forebet_pick=1, disposition="SETTLED", reconstruction="HISTORICAL_PAGE"):
    return SettledEvent(
        event_id=event_id, sport=sport, event_date=event_date,
        participant_1=p1, participant_2=p2, winner_index=winner_index,
        score_1=1.0, score_2=0.0,
        probability_1=probability_1, probability_2=probability_2,
        draw_probability=draw_probability, forebet_pick=forebet_pick,
        disposition=disposition, reconstruction=reconstruction,
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
