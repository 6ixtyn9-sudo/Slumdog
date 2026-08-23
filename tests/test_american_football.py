from slumdog.contracts import EventSnapshot, H2HStats, RecentForm, SettledEvent, TimingClass
from slumdog.facets import build_numeric_features
from slumdog.american_football import (
    calculate_overround_af,
    detect_american_football_robber,
    devig_probabilities_af,
    extract_american_football_features,
)
from slumdog.training import build_training_rows, validation_summary
from slumdog.ml_meta import walk_forward_predict


def test_overround_and_devig_af():
    overround = calculate_overround_af(1.70, 2.20)
    assert overround is not None
    assert round(overround, 4) == 0.0428

    p1, p2 = devig_probabilities_af(1.70, 2.20)
    assert p1 is not None and p2 is not None
    assert round(p1 + p2, 6) == 1.0
    assert round(p1, 4) == 0.5641
    assert round(p2, 4) == 0.4359


def test_extract_american_football_features():
    event = EventSnapshot(
        event_id="af:601",
        sport="american_football",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/american-football/matches/chiefs-eagles-601",
        participant_1="Chiefs",
        participant_2="Eagles",
        probability_1=0.44,
        probability_2=0.56,
        forebet_pick=2,
        odds_1=2.30,
        odds_2=1.65,
        predicted_score="21-24",
        predicted_total=45.0,
        facets={
            "period_values": [["7", "7"], ["7", "10"], ["0", "7"], ["7", "0"]],
            "standings_1_rank": 4.0,
            "standings_2_rank": 1.0,
            "standings_1_pts": 10.0,
            "standings_2_pts": 12.0,
        },
        facet_timing={k: TimingClass.PRE_EVENT for k in [
            "period_values", "standings_1_rank", "standings_2_rank",
            "standings_1_pts", "standings_2_pts",
        ]},
    )
    h2h = H2HStats(total_games=4, participant_1_wins=2, participant_2_wins=2)
    recent_1 = RecentForm(wins=4, games=5)
    recent_2 = RecentForm(wins=5, games=5)

    candidate = detect_american_football_robber(event, h2h, recent_1, recent_2)
    assert candidate is not None
    assert candidate.participant_index == 1

    aff = extract_american_football_features(event, candidate, h2h, recent_1, recent_2)
    assert aff.is_home_dog == 1.0
    assert aff.dog_price == 2.30
    assert aff.favorite_price == 1.65
    assert aff.predicted_total_points == 45.0
    assert aff.predicted_point_margin_dog == -3.0  # 21 - 24
    assert aff.one_score_game_expectation == 1.0
    assert aff.field_goal_game_expectation == 1.0
    assert aff.low_total_environment == 0.0
    assert aff.q1_margin_dog == 0.0  # 7 - 7
    assert aff.q2_margin_dog == -3.0 # 7 - 10
    assert aff.q3_margin_dog == -7.0 # 0 - 7
    assert aff.q4_margin_dog == 7.0  # 7 - 0
    assert aff.h1_margin_dog == -3.0 # (0 + -3)
    assert aff.quarters_projected_won == 2.0  # Q1 tied (>=0), Q4 won (>=0)

    feat_dict = aff.to_dict()
    assert feat_dict["af_is_home_dog"] == 1.0
    assert feat_dict["af_one_score_game_expectation"] == 1.0
    assert feat_dict["af_field_goal_game_expectation"] == 1.0
    assert feat_dict["af_predicted_point_margin_dog"] == -3.0


def test_detect_american_football_robber_one_score_bonus():
    event = EventSnapshot(
        event_id="af:602",
        sport="american_football",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/american-football/matches/a-b-602",
        participant_1="Home Dog",
        participant_2="Away Fav",
        probability_1=0.42,
        probability_2=0.58,
        forebet_pick=2,
        odds_1=2.45,
        odds_2=1.58,
        predicted_score="20-23",
        predicted_total=43.0,
    )
    h2h = H2HStats(total_games=4, participant_1_wins=2, participant_2_wins=2)
    recent_1 = RecentForm(wins=3, games=5)
    recent_2 = RecentForm(wins=4, games=5)

    candidate = detect_american_football_robber(event, h2h, recent_1, recent_2)
    assert candidate is not None
    assert any("Home Field Underdog" in r for r in candidate.reasons)
    assert any("Field goal game expectation" in r for r in candidate.reasons)
    assert candidate.score >= 20.0


def test_build_numeric_features_includes_american_football_features():
    event = EventSnapshot(
        event_id="af:603",
        sport="american_football",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/american-football/matches/a-b-603",
        participant_1="Packers",
        participant_2="Vikings",
        probability_1=0.40,
        probability_2=0.60,
        forebet_pick=2,
        odds_1=2.60,
        odds_2=1.52,
    )
    candidate = detect_american_football_robber(event)
    assert candidate is not None
    features = build_numeric_features(event, candidate)
    assert "af_is_home_dog" in features
    assert "af_forebet_dog_prob" in features
    assert "af_favorite_dominance_ratio" in features


def test_american_football_walk_forward_training_pipeline():
    settled_rows = []
    for i in range(35):
        day = f"2026-01-{(i // 3) + 1:02d}"
        is_home_dog = (i % 2 == 1)
        dog_won = (i % 3 == 0)

        score_1 = 24.0 if (dog_won if is_home_dog else not dog_won) else 20.0
        score_2 = 20.0 if score_1 == 24.0 else 24.0
        winner = 1 if score_1 > score_2 else 2

        p1 = 0.40 if is_home_dog else 0.60
        p2 = 0.60 if is_home_dog else 0.40
        o1 = 2.50 if is_home_dog else 1.55
        o2 = 1.55 if is_home_dog else 2.50

        settled_rows.append(SettledEvent(
            event_id=f"american_football:game_{i}",
            sport="american_football",
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
            league="NFL",
            period_scores_1=(7.0, 7.0, 3.0, 7.0),
            period_scores_2=(3.0, 7.0, 7.0, 3.0),
            source_url="",
            disposition="SETTLED",
        ))

    training_rows = build_training_rows(settled_rows)
    assert len(training_rows) == 35
    assert all("af_is_home_dog" in r.features for r in training_rows)

    predictions = walk_forward_predict(training_rows, min_train=10)
    assert len(predictions) > 0
    summary = validation_summary(predictions)
    assert "brier" in summary
    assert summary["brier"] is not None
    assert summary["brier"] < 1.0
