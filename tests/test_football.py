import math

from slumdog.contracts import EventSnapshot, H2HStats, RecentForm, TimingClass
from slumdog.facets import build_numeric_features
from slumdog.football import (
    calculate_overround,
    detect_football_robber,
    devig_probabilities_3way,
    extract_football_features,
    form_points_per_game,
    shannon_entropy_3way,
)


def test_overround_and_devig_probabilities():
    # Odds: 2.00, 3.40, 3.80
    # Implied raw: 0.50, 0.294118, 0.263158 -> sum = 1.057276 -> overround ~ 5.73%
    o1, ox, o2 = 2.00, 3.40, 3.80
    overround = calculate_overround(o1, ox, o2)
    assert overround is not None
    assert round(overround, 4) == 0.0573

    p1, px, p2 = devig_probabilities_3way(o1, ox, o2)
    assert p1 is not None and px is not None and p2 is not None
    assert round(p1 + px + p2, 6) == 1.0
    assert round(p1, 4) == 0.4729
    assert round(px, 4) == 0.2782
    assert round(p2, 4) == 0.2489


def test_overround_handles_missing_or_invalid_odds():
    assert calculate_overround(None, 3.0, 2.0) is None
    assert calculate_overround(2.0, None, 2.0) is None
    assert calculate_overround(2.0, 3.0, 0.5) is None
    assert devig_probabilities_3way(None, 3.0, 2.0) == (None, None, None)


def test_shannon_entropy_3way():
    # Uniform 1/3, 1/3, 1/3 has maximum entropy = ln(3) ~ 1.0986
    max_ent = shannon_entropy_3way(1/3, 1/3, 1/3)
    assert round(max_ent, 4) == round(math.log(3), 4)

    # Skewed distribution has lower entropy
    skewed_ent = shannon_entropy_3way(0.70, 0.20, 0.10)
    assert skewed_ent < max_ent
    assert skewed_ent > 0.0

    # Degenerate case
    assert shannon_entropy_3way(0, 0, 0) == 0.0


def test_form_points_per_game():
    ppg, wr, dr, lr, n = form_points_per_game(["w", "w", "w"])
    assert ppg == 3.0
    assert wr == 1.0
    assert dr == 0.0
    assert lr == 0.0
    assert n == 3.0

    ppg, wr, dr, lr, n = form_points_per_game(["w", "d", "l", "w"])
    assert ppg == 7.0 / 4.0
    assert wr == 0.5
    assert dr == 0.25
    assert lr == 0.25
    assert n == 4.0

    assert form_points_per_game([]) == (0.0, 0.0, 0.0, 0.0, 0.0)


def test_extract_football_features_full():
    event = EventSnapshot(
        event_id="football:100",
        sport="football",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/football/matches/home-away-100",
        participant_1="Arsenal",
        participant_2="Chelsea",
        probability_1=0.25,
        draw_probability=0.30,
        probability_2=0.45,
        forebet_pick=2,
        odds_1=3.80,
        odds_2=1.95,
        facets={
            "odds_draw": 3.40,
            "host_pos": 12,
            "guest_pos": 3,
            "host_form": ["w", "d", "l", "w", "w"],
            "guest_form": ["w", "w", "w", "d", "l"],
            "goalsavg": 2.7,
            "host_sc_pr": 1.0,
            "guest_sc_pr": 2.0,
            "market_uo_pr_over": 55.0,
            "market_bts_Pred_gg": 60.0,
            "market_ht_Pred_X_HT": 45.0,
            "weather_high": 22.0,
        },
        facet_timing={k: TimingClass.PRE_EVENT for k in [
            "odds_draw", "host_pos", "guest_pos", "host_form", "guest_form",
            "goalsavg", "host_sc_pr", "guest_sc_pr", "market_uo_pr_over",
            "market_bts_Pred_gg", "market_ht_Pred_X_HT", "weather_high",
        ]},
    )
    h2h = H2HStats(total_games=6, participant_1_wins=2, participant_2_wins=3)
    recent_1 = RecentForm(wins=3, games=5)
    recent_2 = RecentForm(wins=3, games=5)

    candidate = detect_football_robber(event, h2h, recent_1, recent_2)
    assert candidate is not None
    assert candidate.participant_index == 1  # Arsenal is the home dog

    fb = extract_football_features(event, candidate, h2h, recent_1, recent_2)
    assert fb.is_home_dog == 1.0
    assert fb.forebet_dog_prob == 0.25
    assert fb.forebet_draw_prob == 0.30
    assert fb.forebet_favorite_prob == 0.45
    assert fb.dog_price == 3.80
    assert fb.favorite_price == 1.95
    assert fb.draw_price == 3.40
    assert fb.dog_rank == 12.0
    assert fb.favorite_rank == 3.0
    assert fb.rank_gap == -9.0  # Favorite rank (3) - Dog rank (12)
    assert fb.over_25_prob == 0.55
    assert fb.btts_yes_prob == 0.60
    assert fb.ht_draw_prob == 0.45
    assert fb.h2h_total_games == 6.0
    assert round(fb.h2h_dog_win_rate, 3) == round(2 / 6, 3)

    feat_dict = fb.to_dict()
    assert feat_dict["fb_is_home_dog"] == 1.0
    assert feat_dict["fb_over_25_prob_missing"] == 0.0
    assert feat_dict["fb_over_25_prob"] == 0.55
    assert feat_dict["fb_travel_distance_km_missing"] == 1.0


def test_extract_football_features_with_tactical_details():
    event = EventSnapshot(
        event_id="football:105",
        sport="football",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/football/matches/porto-benfica-105",
        participant_1="FC Porto",
        participant_2="SL Benfica",
        probability_1=0.28,
        draw_probability=0.32,
        probability_2=0.40,
        forebet_pick=2,
        odds_1=3.40,
        odds_2=2.10,
        facets={
            "travel_distance_km": 310.0,
            "p1_clean_sheets_avg": 0.45,
            "p2_clean_sheets_avg": 0.35,
            "p1_corners_avg": 6.2,
            "p2_corners_avg": 4.8,
            "p1_total_shots_avg": 14.5,
            "p2_total_shots_avg": 12.0,
            "p1_scored_avg": 2.1,
            "p1_conceded_avg": 0.9,
            "p2_scored_avg": 1.8,
            "p2_conceded_avg": 1.1,
        },
        facet_timing={k: TimingClass.PRE_EVENT for k in [
            "travel_distance_km", "p1_clean_sheets_avg", "p2_clean_sheets_avg",
            "p1_corners_avg", "p2_corners_avg", "p1_total_shots_avg",
            "p2_total_shots_avg", "p1_scored_avg", "p1_conceded_avg",
            "p2_scored_avg", "p2_conceded_avg",
        ]},
    )
    candidate = detect_football_robber(event)
    assert candidate is not None
    fb = extract_football_features(event, candidate)

    assert fb.travel_distance_km == 310.0
    assert fb.dog_clean_sheets_avg == 0.45
    assert fb.fav_clean_sheets_avg == 0.35
    assert round(fb.clean_sheets_avg_gap, 2) == 0.10
    assert fb.dog_corners_avg == 6.2
    assert round(fb.corners_avg_gap, 1) == 1.4
    assert fb.dog_total_shots_avg == 14.5
    assert round(fb.total_shots_avg_gap, 1) == 2.5
    # net_eff: (2.1 - 0.9) - (1.8 - 1.1) = 1.2 - 0.7 = 0.5
    assert round(fb.net_goal_efficiency_gap, 2) == 0.50

    feat_dict = fb.to_dict()
    assert feat_dict["fb_travel_distance_km"] == 310.0
    assert feat_dict["fb_travel_distance_km_missing"] == 0.0
    assert round(feat_dict["fb_clean_sheets_avg_gap"], 2) == 0.10



def test_detect_football_robber_home_advantage():
    event = EventSnapshot(
        event_id="football:101",
        sport="football",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/football/matches/home-away-101",
        participant_1="Underdog FC",
        participant_2="Favorite FC",
        probability_1=0.25,
        draw_probability=0.25,
        probability_2=0.50,
        forebet_pick=2,
        odds_1=3.50,
        odds_2=1.90,
    )
    h2h = H2HStats(total_games=4, participant_1_wins=2, participant_2_wins=1)
    recent_1 = RecentForm(wins=3, games=5)
    recent_2 = RecentForm(wins=3, games=5)

    candidate = detect_football_robber(event, h2h, recent_1, recent_2)
    assert candidate is not None
    assert candidate.participant_index == 1
    assert any("Home Underdog Advantage" in r for r in candidate.reasons)
    assert candidate.score >= 20.0


def test_build_numeric_features_includes_football_features():
    event = EventSnapshot(
        event_id="football:102",
        sport="football",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/football/matches/home-away-102",
        participant_1="Team A",
        participant_2="Team B",
        probability_1=0.20,
        draw_probability=0.25,
        probability_2=0.55,
        forebet_pick=2,
        odds_1=4.20,
        odds_2=1.70,
    )
    candidate = detect_football_robber(event)
    assert candidate is not None
    features = build_numeric_features(event, candidate)
    assert "fb_is_home_dog" in features
    assert "fb_forebet_draw_prob" in features
    assert "fb_favorite_dominance_ratio" in features
    assert features["fb_is_home_dog"] == 1.0


def test_parse_football_settled_with_ht_scores_and_disposition():
    import json
    from slumdog.settlement import parse_football_settled

    row_settled = {
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
        "best_odd_2": "3.20",
        "short_tag": "ES1",
        "comment": "FT",
    }
    row_void = {
        "id": "202",
        "DATE_BAH": "2026-08-23 17:00",
        "HOST_NAME": "Sevilla",
        "GUEST_NAME": "Betis",
        "Host_SC": None,
        "Guest_SC": None,
        "Pred_1": "40",
        "Pred_X": "30",
        "Pred_2": "30",
        "best_odd_1": "2.40",
        "best_odd_2": "2.90",
        "short_tag": "ES1",
        "comment": "POSTP",
    }
    payload = json.dumps([[row_settled, row_void]]).encode()
    settled = parse_football_settled(payload, "2026-08-23")
    assert len(settled) == 2

    # Match 1: Settled with HT scores
    m1 = settled[0]
    assert m1.winner_index == 1
    assert m1.score_1 == 2.0
    assert m1.score_2 == 1.0
    assert m1.period_scores_1 == (1.0, 2.0)
    assert m1.period_scores_2 == (0.0, 1.0)
    assert m1.disposition == "SETTLED"
    assert "/real-madrid-barcelona-201" in m1.source_url

    # Match 2: Void / Postponed
    m2 = settled[1]
    assert m2.disposition == "VOID"
    assert m2.score_1 is None


def test_football_walk_forward_training_pipeline():
    from slumdog.contracts import SettledEvent
    from slumdog.training import build_training_rows, validation_summary
    from slumdog.ml_meta import walk_forward_predict

    # Create 35 dated settled events across multiple dates
    settled_rows = []
    for i in range(35):
        day = f"2026-01-{(i // 3) + 1:02d}"
        # Host is underdog in odd matches, Guest in even
        is_home_dog = (i % 2 == 1)
        dog_won = (i % 3 == 0)  # ~33% underdog win rate
        
        score_1 = 2.0 if (dog_won if is_home_dog else not dog_won) else 1.0
        score_2 = 1.0 if score_1 == 2.0 else 2.0
        winner = 1 if score_1 > score_2 else 2
        
        p1 = 0.25 if is_home_dog else 0.50
        p2 = 0.50 if is_home_dog else 0.25
        o1 = 3.50 if is_home_dog else 1.80
        o2 = 1.80 if is_home_dog else 3.50

        settled_rows.append(SettledEvent(
            event_id=f"football:match_{i}",
            sport="football",
            event_date=day,
            participant_1=f"Team_{i % 8}",
            participant_2=f"Team_{(i + 1) % 8}",
            winner_index=winner,
            score_1=score_1,
            score_2=score_2,
            probability_1=p1,
            draw_probability=0.25,
            probability_2=p2,
            forebet_pick=2 if is_home_dog else 1,
            odds_1=o1,
            odds_2=o2,
            league="EPL",
            period_scores_1=(1.0, score_1),
            period_scores_2=(0.0, score_2),
            source_url="",
            disposition="SETTLED",
        ))

    training_rows = build_training_rows(settled_rows)
    assert len(training_rows) == 35
    assert all("fb_is_home_dog" in r.features for r in training_rows)

    predictions = walk_forward_predict(training_rows, min_train=10)
    assert len(predictions) > 0
    summary = validation_summary(predictions)
    assert "brier" in summary
    assert summary["brier"] is not None
    assert summary["brier"] < 1.0

