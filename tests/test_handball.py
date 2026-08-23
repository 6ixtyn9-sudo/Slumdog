from slumdog.contracts import EventSnapshot, H2HStats, RecentForm, SettledEvent, TimingClass
from slumdog.facets import build_numeric_features
from slumdog.handball import (
    calculate_overround_handball,
    detect_handball_robber,
    devig_probabilities_handball,
    extract_handball_features,
)
from slumdog.training import build_training_rows, validation_summary
from slumdog.ml_meta import walk_forward_predict


def test_overround_and_devig_handball():
    overround = calculate_overround_handball(2.30, 9.0, 1.70)
    assert overround is not None
    assert round(overround, 4) == 0.1341

    p1, px, p2 = devig_probabilities_handball(2.30, 9.0, 1.70)
    assert p1 is not None and px is not None and p2 is not None
    assert round(p1 + px + p2, 6) == 1.0
    assert round(p1, 4) == 0.3834
    assert round(px, 4) == 0.0980
    assert round(p2, 4) == 0.5187


def test_extract_handball_features():
    event = EventSnapshot(
        event_id="handball:801",
        sport="handball",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/handball/matches/kiel-barcelona-801",
        participant_1="Kiel",
        participant_2="Barcelona",
        probability_1=0.44,
        draw_probability=0.08,
        probability_2=0.48,
        forebet_pick=2,
        odds_1=2.35,
        odds_2=1.65,
        predicted_score="28-29",
        predicted_total=57.0,
        facets={
            "odds_draw": 9.50,
            "period_values": [["14", "14"], ["14", "15"]],
            "standings_1_rank": 3.0,
            "standings_2_rank": 1.0,
            "standings_1_pts": 22.0,
            "standings_2_pts": 26.0,
            "standings_1_gd": 35.0,
            "standings_2_gd": 55.0,
        },
        facet_timing={k: TimingClass.PRE_EVENT for k in [
            "odds_draw", "period_values", "standings_1_rank", "standings_2_rank",
            "standings_1_pts", "standings_2_pts", "standings_1_gd", "standings_2_gd",
        ]},
    )
    h2h = H2HStats(total_games=6, participant_1_wins=3, participant_2_wins=3)
    recent_1 = RecentForm(wins=4, games=5)
    recent_2 = RecentForm(wins=5, games=5)

    candidate = detect_handball_robber(event, h2h, recent_1, recent_2)
    assert candidate is not None
    assert candidate.participant_index == 1

    hf = extract_handball_features(event, candidate, h2h, recent_1, recent_2)
    assert hf.is_home_dog == 1.0
    assert hf.dog_price == 2.35
    assert hf.draw_price == 9.50
    assert hf.favorite_price == 1.65
    assert hf.predicted_total_goals == 57.0
    assert hf.predicted_goal_margin_dog == -1.0  # 28 - 29
    assert hf.close_game_expectation == 1.0
    assert hf.low_total_environment == 0.0
    assert hf.high_total_environment == 0.0
    assert hf.h1_margin_dog == 0.0   # 14 - 14
    assert hf.h2_margin_dog == -1.0  # 14 - 15
    assert hf.dog_rank == 3.0
    assert hf.favorite_rank == 1.0
    assert hf.rank_gap == -2.0
    assert hf.standings_pts_gap == -4.0   # 22 - 26
    assert hf.standings_gd_gap == -20.0   # 35 - 55

    feat_dict = hf.to_dict()
    assert feat_dict["hb_is_home_dog"] == 1.0
    assert feat_dict["hb_close_game_expectation"] == 1.0
    assert feat_dict["hb_predicted_goal_margin_dog"] == -1.0


def test_detect_handball_robber_close_game_bonus():
    event = EventSnapshot(
        event_id="handball:802",
        sport="handball",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/handball/matches/a-b-802",
        participant_1="Home Dog",
        participant_2="Away Fav",
        probability_1=0.42,
        draw_probability=0.08,
        probability_2=0.50,
        forebet_pick=2,
        odds_1=2.45,
        odds_2=1.60,
        predicted_score="26-27",
        predicted_total=53.0,
    )
    h2h = H2HStats(total_games=4, participant_1_wins=2, participant_2_wins=2)
    recent_1 = RecentForm(wins=3, games=5)
    recent_2 = RecentForm(wins=4, games=5)

    candidate = detect_handball_robber(event, h2h, recent_1, recent_2)
    assert candidate is not None
    assert any("Home Court Underdog" in r for r in candidate.reasons)
    assert any("Tight game expectation" in r for r in candidate.reasons)
    assert candidate.score >= 20.0


def test_build_numeric_features_includes_handball_features():
    event = EventSnapshot(
        event_id="handball:803",
        sport="handball",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/handball/matches/a-b-803",
        participant_1="Flensburg",
        participant_2="Magdeburg",
        probability_1=0.41,
        draw_probability=0.08,
        probability_2=0.51,
        forebet_pick=2,
        odds_1=2.50,
        odds_2=1.55,
    )
    candidate = detect_handball_robber(event)
    assert candidate is not None
    features = build_numeric_features(event, candidate)
    assert "hb_is_home_dog" in features
    assert "hb_forebet_dog_prob" in features
    assert "hb_favorite_dominance_ratio" in features


def test_handball_walk_forward_training_pipeline():
    settled_rows = []
    for i in range(35):
        day = f"2026-01-{(i // 3) + 1:02d}"
        is_home_dog = (i % 2 == 1)
        dog_won = (i % 3 == 0)

        score_1 = 30.0 if (dog_won if is_home_dog else not dog_won) else 27.0
        score_2 = 27.0 if score_1 == 30.0 else 30.0
        winner = 1 if score_1 > score_2 else 2

        p1 = 0.41 if is_home_dog else 0.51
        p2 = 0.51 if is_home_dog else 0.41
        o1 = 2.45 if is_home_dog else 1.55
        o2 = 1.55 if is_home_dog else 2.45

        settled_rows.append(SettledEvent(
            event_id=f"handball:game_{i}",
            sport="handball",
            event_date=day,
            participant_1=f"Team_{i % 6}",
            participant_2=f"Team_{(i + 1) % 6}",
            winner_index=winner,
            score_1=score_1,
            score_2=score_2,
            probability_1=p1,
            draw_probability=0.08,
            probability_2=p2,
            forebet_pick=2 if is_home_dog else 1,
            odds_1=o1,
            odds_2=o2,
            league="Bundesliga",
            period_scores_1=(15.0, 15.0),
            period_scores_2=(13.0, 14.0),
            source_url="",
            disposition="SETTLED",
        ))

    training_rows = build_training_rows(settled_rows)
    assert len(training_rows) == 35
    assert all("hb_is_home_dog" in r.features for r in training_rows)

    predictions = walk_forward_predict(training_rows, min_train=10)
    assert len(predictions) > 0
    summary = validation_summary(predictions)
    assert "brier" in summary
    assert summary["brier"] is not None
    assert summary["brier"] < 1.0
