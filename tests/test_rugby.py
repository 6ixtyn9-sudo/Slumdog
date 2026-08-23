from slumdog.contracts import EventSnapshot, H2HStats, RecentForm, SettledEvent, TimingClass
from slumdog.facets import build_numeric_features
from slumdog.rugby import (
    calculate_overround_rugby,
    detect_rugby_robber,
    devig_probabilities_rugby,
    extract_rugby_features,
)
from slumdog.training import build_training_rows, validation_summary
from slumdog.ml_meta import walk_forward_predict


def test_overround_and_devig_rugby():
    overround = calculate_overround_rugby(2.40, 21.0, 1.60)
    assert overround is not None
    assert round(overround, 4) == 0.0893

    p1, px, p2 = devig_probabilities_rugby(2.40, 21.0, 1.60)
    assert p1 is not None and px is not None and p2 is not None
    assert round(p1 + px + p2, 6) == 1.0
    assert round(p1, 4) == 0.3825
    assert round(px, 4) == 0.0437
    assert round(p2, 4) == 0.5738


def test_extract_rugby_features():
    event = EventSnapshot(
        event_id="rugby:701",
        sport="rugby",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/rugby/matches/springboks-allblacks-701",
        participant_1="Springboks",
        participant_2="All Blacks",
        probability_1=0.45,
        draw_probability=0.05,
        probability_2=0.50,
        forebet_pick=2,
        odds_1=2.35,
        odds_2=1.65,
        predicted_score="22-25",
        predicted_total=47.0,
        facets={
            "odds_draw": 22.0,
            "period_values": [["10", "12"], ["12", "13"]],
            "standings_1_rank": 2.0,
            "standings_2_rank": 1.0,
            "standings_1_pts": 14.0,
            "standings_2_pts": 15.0,
            "standings_1_gd": 20.0,
            "standings_2_gd": 28.0,
        },
        facet_timing={k: TimingClass.PRE_EVENT for k in [
            "odds_draw", "period_values", "standings_1_rank", "standings_2_rank",
            "standings_1_pts", "standings_2_pts", "standings_1_gd", "standings_2_gd",
        ]},
    )
    h2h = H2HStats(total_games=6, participant_1_wins=3, participant_2_wins=3)
    recent_1 = RecentForm(wins=4, games=5)
    recent_2 = RecentForm(wins=4, games=5)

    candidate = detect_rugby_robber(event, h2h, recent_1, recent_2)
    assert candidate is not None
    assert candidate.participant_index == 1

    rf = extract_rugby_features(event, candidate, h2h, recent_1, recent_2)
    assert rf.is_home_dog == 1.0
    assert rf.dog_price == 2.35
    assert rf.draw_price == 22.0
    assert rf.favorite_price == 1.65
    assert rf.predicted_total_points == 47.0
    assert rf.predicted_point_margin_dog == -3.0  # 22 - 25
    assert rf.try_margin_expectation == 1.0
    assert rf.penalty_margin_expectation == 1.0
    assert rf.h1_margin_dog == -2.0  # 10 - 12
    assert rf.h2_margin_dog == -1.0  # 12 - 13
    assert rf.dog_rank == 2.0
    assert rf.favorite_rank == 1.0
    assert rf.rank_gap == -1.0
    assert rf.standings_pts_gap == -1.0  # 14 - 15
    assert rf.standings_gd_gap == -8.0   # 20 - 28

    feat_dict = rf.to_dict()
    assert feat_dict["rg_is_home_dog"] == 1.0
    assert feat_dict["rg_try_margin_expectation"] == 1.0
    assert feat_dict["rg_penalty_margin_expectation"] == 1.0
    assert feat_dict["rg_predicted_point_margin_dog"] == -3.0


def test_detect_rugby_robber_try_margin_bonus():
    event = EventSnapshot(
        event_id="rugby:702",
        sport="rugby",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/rugby/matches/a-b-702",
        participant_1="Home Dog",
        participant_2="Away Fav",
        probability_1=0.42,
        draw_probability=0.04,
        probability_2=0.54,
        forebet_pick=2,
        odds_1=2.50,
        odds_2=1.58,
        predicted_score="18-23",
        predicted_total=41.0,
    )
    h2h = H2HStats(total_games=4, participant_1_wins=2, participant_2_wins=2)
    recent_1 = RecentForm(wins=3, games=5)
    recent_2 = RecentForm(wins=4, games=5)

    candidate = detect_rugby_robber(event, h2h, recent_1, recent_2)
    assert candidate is not None
    assert any("Home Ground Underdog" in r for r in candidate.reasons)
    assert any("Converted try game expectation" in r for r in candidate.reasons)
    assert candidate.score >= 20.0


def test_build_numeric_features_includes_rugby_features():
    event = EventSnapshot(
        event_id="rugby:703",
        sport="rugby",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/rugby/matches/a-b-703",
        participant_1="Leinster",
        participant_2="Toulouse",
        probability_1=0.40,
        draw_probability=0.04,
        probability_2=0.56,
        forebet_pick=2,
        odds_1=2.60,
        odds_2=1.52,
    )
    candidate = detect_rugby_robber(event)
    assert candidate is not None
    features = build_numeric_features(event, candidate)
    assert "rg_is_home_dog" in features
    assert "rg_forebet_dog_prob" in features
    assert "rg_favorite_dominance_ratio" in features


def test_rugby_walk_forward_training_pipeline():
    settled_rows = []
    for i in range(35):
        day = f"2026-01-{(i // 3) + 1:02d}"
        is_home_dog = (i % 2 == 1)
        dog_won = (i % 3 == 0)

        score_1 = 25.0 if (dog_won if is_home_dog else not dog_won) else 20.0
        score_2 = 20.0 if score_1 == 25.0 else 25.0
        winner = 1 if score_1 > score_2 else 2

        p1 = 0.40 if is_home_dog else 0.56
        p2 = 0.56 if is_home_dog else 0.40
        o1 = 2.50 if is_home_dog else 1.55
        o2 = 1.55 if is_home_dog else 2.50

        settled_rows.append(SettledEvent(
            event_id=f"rugby:game_{i}",
            sport="rugby",
            event_date=day,
            participant_1=f"Team_{i % 6}",
            participant_2=f"Team_{(i + 1) % 6}",
            winner_index=winner,
            score_1=score_1,
            score_2=score_2,
            probability_1=p1,
            draw_probability=0.04,
            probability_2=p2,
            forebet_pick=2 if is_home_dog else 1,
            odds_1=o1,
            odds_2=o2,
            league="URC",
            period_scores_1=(12.0, 13.0),
            period_scores_2=(10.0, 10.0),
            source_url="",
            disposition="SETTLED",
        ))

    training_rows = build_training_rows(settled_rows)
    assert len(training_rows) == 35
    assert all("rg_is_home_dog" in r.features for r in training_rows)

    predictions = walk_forward_predict(training_rows, min_train=10)
    assert len(predictions) > 0
    summary = validation_summary(predictions)
    assert "brier" in summary
    assert summary["brier"] is not None
    assert summary["brier"] < 1.0
