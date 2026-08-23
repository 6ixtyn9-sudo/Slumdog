from slumdog.contracts import EventSnapshot, H2HStats, RecentForm, SettledEvent, TimingClass
from slumdog.facets import build_numeric_features
from slumdog.hockey import (
    calculate_overround_hockey,
    detect_hockey_robber,
    devig_probabilities_hockey,
    extract_hockey_features,
)
from slumdog.training import build_training_rows, validation_summary
from slumdog.ml_meta import walk_forward_predict


def test_overround_and_devig_hockey():
    overround = calculate_overround_hockey(1.65, 2.30)
    assert overround is not None
    assert round(overround, 4) == 0.0408

    p1, p2 = devig_probabilities_hockey(1.65, 2.30)
    assert p1 is not None and p2 is not None
    assert round(p1 + p2, 6) == 1.0
    assert round(p1, 4) == 0.5823
    assert round(p2, 4) == 0.4177


def test_extract_hockey_features_with_periods():
    event = EventSnapshot(
        event_id="hockey:401",
        sport="hockey",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/hockey/matches/oilers-panthers-401",
        participant_1="Oilers",
        participant_2="Panthers",
        probability_1=0.42,
        probability_2=0.58,
        forebet_pick=2,
        odds_1=2.45,
        odds_2=1.58,
        predicted_score="2-3",
        predicted_total=5.0,
        facets={
            "period_values": [["1", "1"], ["1", "1"], ["0", "1"]],
            "standings_1_rank": 7.0,
            "standings_2_rank": 3.0,
            "standings_1_pts": 75.0,
            "standings_2_pts": 88.0,
            "standings_1_gd": 15.0,
            "standings_2_gd": 35.0,
        },
        facet_timing={k: TimingClass.PRE_EVENT for k in [
            "period_values", "standings_1_rank", "standings_2_rank",
            "standings_1_pts", "standings_2_pts", "standings_1_gd", "standings_2_gd",
        ]},
    )
    h2h = H2HStats(
        total_games=5,
        participant_1_wins=2,
        participant_2_wins=3,
        period_win_rates_1=(0.4, 0.4, 0.4),
        period_win_rates_2=(0.4, 0.6, 0.6),
    )
    recent_1 = RecentForm(wins=3, games=5)
    recent_2 = RecentForm(wins=4, games=5)

    candidate = detect_hockey_robber(event, h2h, recent_1, recent_2)
    assert candidate is not None
    assert candidate.participant_index == 1

    hf = extract_hockey_features(event, candidate, h2h, recent_1, recent_2)
    assert hf.is_home_dog == 1.0
    assert hf.dog_price == 2.45
    assert hf.favorite_price == 1.58
    assert hf.predicted_total_goals == 5.0
    assert hf.low_total_environment == 1.0
    assert hf.predicted_goal_margin_dog == -1.0  # 2 - 3
    assert hf.p1_margin_dog == 0.0  # 1 - 1
    assert hf.p2_margin_dog == 0.0  # 1 - 1
    assert hf.p3_margin_dog == -1.0 # 0 - 1
    assert hf.periods_projected_won == 2.0  # P1 & P2 tied >= 0
    assert hf.dog_rank == 7.0
    assert hf.favorite_rank == 3.0
    assert hf.rank_gap == -4.0
    assert hf.standings_pts_gap == -13.0  # 75 - 88

    feat_dict = hf.to_dict()
    assert feat_dict["hk_is_home_dog"] == 1.0
    assert feat_dict["hk_low_total_environment"] == 1.0
    assert feat_dict["hk_predicted_total_goals"] == 5.0


def test_detect_hockey_robber_tight_total_bonus():
    event = EventSnapshot(
        event_id="hockey:402",
        sport="hockey",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/hockey/matches/a-b-402",
        participant_1="Home Dog",
        participant_2="Away Fav",
        probability_1=0.40,
        probability_2=0.60,
        forebet_pick=2,
        odds_1=2.50,
        odds_2=1.55,
        predicted_total=4.5,
    )
    h2h = H2HStats(total_games=4, participant_1_wins=2, participant_2_wins=2)
    recent_1 = RecentForm(wins=3, games=5)
    recent_2 = RecentForm(wins=4, games=5)

    candidate = detect_hockey_robber(event, h2h, recent_1, recent_2)
    assert candidate is not None
    assert any("Home Ice Underdog" in r for r in candidate.reasons)
    assert any("Tight game expectation" in r for r in candidate.reasons)
    assert candidate.score >= 20.0


def test_build_numeric_features_includes_hockey_features():
    event = EventSnapshot(
        event_id="hockey:403",
        sport="hockey",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/hockey/matches/a-b-403",
        participant_1="Rangers",
        participant_2="Bruins",
        probability_1=0.38,
        probability_2=0.62,
        forebet_pick=2,
        odds_1=2.65,
        odds_2=1.50,
    )
    candidate = detect_hockey_robber(event)
    assert candidate is not None
    features = build_numeric_features(event, candidate)
    assert "hk_is_home_dog" in features
    assert "hk_forebet_dog_prob" in features
    assert "hk_favorite_dominance_ratio" in features


def test_hockey_walk_forward_training_pipeline():
    settled_rows = []
    for i in range(35):
        day = f"2026-01-{(i // 3) + 1:02d}"
        is_home_dog = (i % 2 == 1)
        dog_won = (i % 3 == 0)

        score_1 = 3.0 if (dog_won if is_home_dog else not dog_won) else 2.0
        score_2 = 2.0 if score_1 == 3.0 else 3.0
        winner = 1 if score_1 > score_2 else 2

        p1 = 0.38 if is_home_dog else 0.62
        p2 = 0.62 if is_home_dog else 0.38
        o1 = 2.55 if is_home_dog else 1.52
        o2 = 1.52 if is_home_dog else 2.55

        settled_rows.append(SettledEvent(
            event_id=f"hockey:game_{i}",
            sport="hockey",
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
            league="NHL",
            period_scores_1=(1.0, 1.0, 1.0),
            period_scores_2=(0.0, 1.0, 1.0),
            source_url="",
            disposition="SETTLED",
        ))

    training_rows = build_training_rows(settled_rows)
    assert len(training_rows) == 35
    assert all("hk_is_home_dog" in r.features for r in training_rows)

    predictions = walk_forward_predict(training_rows, min_train=10)
    assert len(predictions) > 0
    summary = validation_summary(predictions)
    assert "brier" in summary
    assert summary["brier"] is not None
    assert summary["brier"] < 1.0
