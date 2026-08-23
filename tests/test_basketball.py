import math

from slumdog.contracts import EventSnapshot, H2HStats, RecentForm, TimingClass, SettledEvent
from slumdog.facets import build_numeric_features
from slumdog.basketball import (
    calculate_overround_2way,
    detect_basketball_robber,
    devig_probabilities_2way,
    extract_basketball_features,
    parse_score_string,
    shannon_entropy_2way,
)
from slumdog.training import build_training_rows, validation_summary
from slumdog.ml_meta import walk_forward_predict


def test_overround_and_devig_probabilities_2way():
    # Odds: 1.50, 2.70
    # Implied: 1/1.50 = 0.666667, 1/2.70 = 0.370370 -> sum = 1.037037 -> overround = 3.70%
    overround = calculate_overround_2way(1.50, 2.70)
    assert overround is not None
    assert round(overround, 4) == 0.0370

    p1, p2 = devig_probabilities_2way(1.50, 2.70)
    assert p1 is not None and p2 is not None
    assert round(p1 + p2, 6) == 1.0
    assert round(p1, 4) == 0.6429
    assert round(p2, 4) == 0.3571


def test_shannon_entropy_2way():
    # 50/50 split has maximum entropy = ln(2) ~ 0.6931
    max_ent = shannon_entropy_2way(0.50, 0.50)
    assert round(max_ent, 4) == round(math.log(2), 4)

    # Skewed split 90/10 has lower entropy
    skewed_ent = shannon_entropy_2way(0.90, 0.10)
    assert skewed_ent < max_ent
    assert skewed_ent > 0.0


def test_parse_score_string():
    assert parse_score_string("108-115") == (108.0, 115.0)
    assert parse_score_string("95:88") == (95.0, 88.0)
    assert parse_score_string("") == (None, None)


def test_extract_basketball_features_full():
    event = EventSnapshot(
        event_id="basketball:201",
        sport="basketball",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/basketball/matches/lakers-celtics-201",
        participant_1="Lakers",
        participant_2="Celtics",
        probability_1=0.35,
        probability_2=0.65,
        forebet_pick=2,
        odds_1=2.80,
        odds_2=1.45,
        predicted_score="105-112",
        predicted_total=217.0,
        facets={
            "period_values": [["26", "28"], ["25", "30"], ["28", "26"], ["26", "28"]],
            "standings_1_rank": 9.0,
            "standings_2_rank": 2.0,
            "standings_1_wins": 30.0,
            "standings_1_losses": 25.0,
            "standings_2_wins": 42.0,
            "standings_2_losses": 14.0,
        },
        facet_timing={k: TimingClass.PRE_EVENT for k in [
            "period_values", "standings_1_rank", "standings_2_rank",
            "standings_1_wins", "standings_1_losses", "standings_2_wins", "standings_2_losses",
        ]},
    )
    h2h = H2HStats(
        total_games=5,
        participant_1_wins=2,
        participant_2_wins=3,
        period_win_rates_1=(0.4, 0.6, 0.4, 0.4),
        period_win_rates_2=(0.6, 0.4, 0.6, 0.6),
    )
    recent_1 = RecentForm(wins=3, games=5)
    recent_2 = RecentForm(wins=4, games=5)

    candidate = detect_basketball_robber(event, h2h, recent_1, recent_2)
    assert candidate is not None
    assert candidate.participant_index == 1  # Lakers are the home dog

    bb = extract_basketball_features(event, candidate, h2h, recent_1, recent_2)
    assert bb.is_home_dog == 1.0
    assert bb.forebet_dog_prob == 0.35
    assert bb.forebet_favorite_prob == 0.65
    assert bb.dog_price == 2.80
    assert bb.favorite_price == 1.45
    assert bb.predicted_total_points == 217.0
    assert bb.high_pace_environment == 1.0
    assert bb.predicted_point_margin_dog == -7.0  # 105 - 112
    assert bb.q1_margin_dog == -2.0  # 26 - 28
    assert bb.q2_margin_dog == -5.0  # 25 - 30
    assert bb.q3_margin_dog == 2.0   # 28 - 26
    assert bb.first_half_margin_dog == -7.0  # -2 + -5
    assert bb.second_half_margin_dog == 0.0  # +2 + -2
    assert bb.quarters_projected_won == 1.0  # Q3 won
    assert bb.quarter_consistency_rate == 0.25
    assert bb.h2h_total_games == 5.0
    assert bb.h2h_dog_win_rate == 2 / 5
    assert bb.dog_rank == 9.0
    assert bb.favorite_rank == 2.0
    assert bb.rank_gap == -7.0

    feat_dict = bb.to_dict()
    assert feat_dict["bb_is_home_dog"] == 1.0
    assert feat_dict["bb_high_pace_environment"] == 1.0
    assert feat_dict["bb_predicted_total_points"] == 217.0
    assert feat_dict["bb_travel_distance_km_missing"] == 1.0


def test_extract_basketball_features_with_detail_differentials():
    event = EventSnapshot(
        event_id="basketball:205",
        sport="basketball",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/basketball/matches/home-away-205",
        participant_1="Denver Nuggets",
        participant_2="Boston Celtics",
        probability_1=0.42,
        probability_2=0.58,
        forebet_pick=2,
        odds_1=2.30,
        odds_2=1.65,
        predicted_score="112-115",
        predicted_total=227.0,
        facets={
            "travel_distance_km": 2850.0,
            "p1_scored_avg": 115.4,
            "p1_conceded_avg": 109.2,
            "p2_scored_avg": 118.0,
            "p2_conceded_avg": 110.0,
        },
        facet_timing={k: TimingClass.PRE_EVENT for k in [
            "travel_distance_km", "p1_scored_avg", "p1_conceded_avg", "p2_scored_avg", "p2_conceded_avg",
        ]},
    )
    candidate = detect_basketball_robber(event)
    assert candidate is not None
    assert any("Fav Away Road Fatigue" in r for r in candidate.reasons)

    bb = extract_basketball_features(event, candidate)
    assert bb.travel_distance_km == 2850.0
    assert bb.dog_travel_distance == 0.0  # Denver is home
    assert bb.fav_travel_distance == 2850.0  # Boston traveled
    assert bb.dog_scored_avg == 115.4
    assert bb.fav_scored_avg == 118.0
    # net diff: (115.4 - 109.2) - (118.0 - 110.0) = 6.2 - 8.0 = -1.8
    assert round(bb.net_points_differential_gap, 1) == -1.8

    feat_dict = bb.to_dict()
    assert feat_dict["bb_travel_distance_km"] == 2850.0
    assert feat_dict["bb_travel_distance_km_missing"] == 0.0
    assert round(feat_dict["bb_net_points_differential_gap"], 1) == -1.8



def test_detect_basketball_robber_scoring():
    event = EventSnapshot(
        event_id="basketball:202",
        sport="basketball",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/basketball/matches/home-away-202",
        participant_1="Underdog Team",
        participant_2="Favorite Team",
        probability_1=0.38,
        probability_2=0.62,
        forebet_pick=2,
        odds_1=2.60,
        odds_2=1.52,
    )
    h2h = H2HStats(
        total_games=4,
        participant_1_wins=2,
        participant_2_wins=2,
        period_win_rates_1=(0.5, 0.75, 0.5, 0.5),
        period_win_rates_2=(0.5, 0.25, 0.5, 0.5),
    )
    recent_1 = RecentForm(wins=3, games=5)
    recent_2 = RecentForm(wins=4, games=5)

    candidate = detect_basketball_robber(event, h2h, recent_1, recent_2)
    assert candidate is not None
    assert candidate.participant_index == 1
    assert any("Home Court Underdog" in r for r in candidate.reasons)
    assert candidate.score >= 20.0


def test_build_numeric_features_includes_basketball_features():
    event = EventSnapshot(
        event_id="basketball:203",
        sport="basketball",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/basketball/matches/a-b-203",
        participant_1="Hawks",
        participant_2="Heat",
        probability_1=0.30,
        probability_2=0.70,
        forebet_pick=2,
        odds_1=3.10,
        odds_2=1.38,
    )
    candidate = detect_basketball_robber(event)
    assert candidate is not None
    features = build_numeric_features(event, candidate)
    assert "bb_is_home_dog" in features
    assert "bb_high_pace_environment" in features
    assert "bb_favorite_dominance_ratio" in features
    assert features["bb_is_home_dog"] == 1.0


def test_basketball_walk_forward_training_pipeline():
    settled_rows = []
    for i in range(35):
        day = f"2026-01-{(i // 3) + 1:02d}"
        is_home_dog = (i % 2 == 1)
        dog_won = (i % 3 == 0)  # ~33% upset rate

        score_1 = 108.0 if (dog_won if is_home_dog else not dog_won) else 102.0
        score_2 = 102.0 if score_1 == 108.0 else 108.0
        winner = 1 if score_1 > score_2 else 2

        p1 = 0.35 if is_home_dog else 0.65
        p2 = 0.65 if is_home_dog else 0.35
        o1 = 2.70 if is_home_dog else 1.48
        o2 = 1.48 if is_home_dog else 2.70

        settled_rows.append(SettledEvent(
            event_id=f"basketball:game_{i}",
            sport="basketball",
            event_date=day,
            participant_1=f"Team_{i % 6}",
            participant_2=f"Team_{(i + 1) % 6}",
            winner_index=winner,
            score_1=score_1,
            score_2=score_2,
            probability_1=p1,
            draw_probability=None,
            probability_2=p2,
            forebet_pick=2 if is_home_dog else 1,
            odds_1=o1,
            odds_2=o2,
            league="NBA",
            period_scores_1=(25.0, 27.0, 28.0, 28.0),
            period_scores_2=(24.0, 26.0, 27.0, 25.0),
            source_url="",
            disposition="SETTLED",
        ))

    training_rows = build_training_rows(settled_rows)
    assert len(training_rows) == 35
    assert all("bb_is_home_dog" in r.features for r in training_rows)

    predictions = walk_forward_predict(training_rows, min_train=10)
    assert len(predictions) > 0
    summary = validation_summary(predictions)
    assert "brier" in summary
    assert summary["brier"] is not None
    assert summary["brier"] < 1.0
