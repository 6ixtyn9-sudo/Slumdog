from slumdog.contracts import EventSnapshot, H2HStats, RecentForm, SettledEvent, TimingClass
from slumdog.facets import build_numeric_features
from slumdog.esports import (
    calculate_overround_esports,
    detect_esports_robber,
    devig_probabilities_esports,
    extract_esports_features,
)
from slumdog.training import build_training_rows, validation_summary
from slumdog.ml_meta import walk_forward_predict


def test_overround_and_devig_esports():
    overround = calculate_overround_esports(1.80, 2.05)
    assert overround is not None
    assert round(overround, 4) == 0.0434

    p1, p2 = devig_probabilities_esports(1.80, 2.05)
    assert p1 is not None and p2 is not None
    assert round(p1 + p2, 6) == 1.0
    assert round(p1, 4) == 0.5325
    assert round(p2, 4) == 0.4675


def test_extract_esports_features():
    event = EventSnapshot(
        event_id="esports:1001",
        sport="esports",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/esports/matches/navi-faze-1001",
        participant_1="NaVi",
        participant_2="FaZe",
        probability_1=0.45,
        probability_2=0.55,
        forebet_pick=2,
        odds_1=2.30,
        odds_2=1.65,
        predicted_score="1-2",
        predicted_total=3.0,
        facets={
            "period_values": [["13", "16"], ["16", "11"], ["10", "16"]],
            "standings_1_rank": 5.0,
            "standings_2_rank": 2.0,
            "standings_1_pts": 12.0,
            "standings_2_pts": 18.0,
            "standings_1_gd": 4.0,
            "standings_2_gd": 12.0,
        },
        facet_timing={k: TimingClass.PRE_EVENT for k in [
            "period_values", "standings_1_rank", "standings_2_rank",
            "standings_1_pts", "standings_2_pts", "standings_1_gd", "standings_2_gd",
        ]},
    )
    h2h = H2HStats(total_games=6, participant_1_wins=3, participant_2_wins=3)
    recent_1 = RecentForm(wins=4, games=5)
    recent_2 = RecentForm(wins=5, games=5)

    candidate = detect_esports_robber(event, h2h, recent_1, recent_2)
    assert candidate is not None
    assert candidate.participant_index == 1

    ef = extract_esports_features(event, candidate, h2h, recent_1, recent_2)
    assert ef.is_home_dog == 1.0
    assert ef.dog_price == 2.30
    assert ef.favorite_price == 1.65
    assert ef.predicted_total_maps == 3.0
    assert ef.predicted_map_margin_dog == -1.0  # 1 - 2
    assert ef.decider_map_expectation == 1.0   # 1-2 in Bo3 is decider
    assert ef.sweep_map_expectation == 0.0
    assert ef.m1_margin_dog == -3.0  # 13 - 16
    assert ef.m2_margin_dog == 5.0   # 16 - 11
    assert ef.m3_margin_dog == -6.0  # 10 - 16
    assert ef.maps_projected_won == 1.0  # M2 > 0
    assert ef.dog_rank == 5.0
    assert ef.favorite_rank == 2.0
    assert ef.rank_gap == -3.0
    assert ef.standings_pts_gap == -6.0   # 12 - 18
    assert ef.standings_map_diff_gap == -8.0 # 4 - 12

    feat_dict = ef.to_dict()
    assert feat_dict["es_is_home_dog"] == 1.0
    assert feat_dict["es_decider_map_expectation"] == 1.0
    assert feat_dict["es_predicted_total_maps"] == 3.0
    assert feat_dict["es_net_map_differential_gap_missing"] == 1.0


def test_extract_esports_features_with_detail_differentials():
    event = EventSnapshot(
        event_id="esports:1005",
        sport="esports",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/esports/matches/home-away-1005",
        participant_1="Complexity",
        participant_2="MOUZ",
        probability_1=0.42,
        probability_2=0.58,
        forebet_pick=2,
        odds_1=2.40,
        odds_2=1.58,
        predicted_score="1-2",
        predicted_total=3.0,
        facets={
            "p1_scored_avg": 1.6,
            "p1_conceded_avg": 1.1,
            "p2_scored_avg": 1.9,
            "p2_conceded_avg": 0.8,
        },
        facet_timing={k: TimingClass.PRE_EVENT for k in [
            "p1_scored_avg", "p1_conceded_avg", "p2_scored_avg", "p2_conceded_avg",
        ]},
    )
    candidate = detect_esports_robber(event)
    assert candidate is not None

    ef = extract_esports_features(event, candidate)
    assert ef.dog_scored_avg == 1.6
    assert ef.fav_scored_avg == 1.9
    # net map diff: (1.6 - 1.1) - (1.9 - 0.8) = 0.5 - 1.1 = -0.6
    assert round(ef.net_map_differential_gap, 1) == -0.6



def test_detect_esports_robber_decider_bonus():
    event = EventSnapshot(
        event_id="esports:1002",
        sport="esports",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/esports/matches/a-b-1002",
        participant_1="Team Liquid",
        participant_2="Vitality",
        probability_1=0.42,
        probability_2=0.58,
        forebet_pick=2,
        odds_1=2.40,
        odds_2=1.58,
        predicted_score="1-2",
        predicted_total=3.0,
    )
    h2h = H2HStats(total_games=4, participant_1_wins=2, participant_2_wins=2)
    recent_1 = RecentForm(wins=3, games=5)
    recent_2 = RecentForm(wins=4, games=5)

    candidate = detect_esports_robber(event, h2h, recent_1, recent_2)
    assert candidate is not None
    assert any("decider map expectation" in r for r in candidate.reasons)
    assert candidate.score >= 20.0


def test_build_numeric_features_includes_esports_features():
    event = EventSnapshot(
        event_id="esports:1003",
        sport="esports",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/esports/matches/a-b-1003",
        participant_1="G2",
        participant_2="Spirit",
        probability_1=0.40,
        probability_2=0.60,
        forebet_pick=2,
        odds_1=2.50,
        odds_2=1.55,
    )
    candidate = detect_esports_robber(event)
    assert candidate is not None
    features = build_numeric_features(event, candidate)
    assert "es_is_home_dog" in features
    assert "es_forebet_dog_prob" in features
    assert "es_favorite_dominance_ratio" in features


def test_esports_walk_forward_training_pipeline():
    settled_rows = []
    for i in range(35):
        day = f"2026-01-{(i // 3) + 1:02d}"
        is_home_dog = (i % 2 == 1)
        dog_won = (i % 3 == 0)

        score_1 = 2.0 if (dog_won if is_home_dog else not dog_won) else 1.0
        score_2 = 1.0 if score_1 == 2.0 else 2.0
        winner = 1 if score_1 > score_2 else 2

        p1 = 0.40 if is_home_dog else 0.60
        p2 = 0.60 if is_home_dog else 0.40
        o1 = 2.45 if is_home_dog else 1.55
        o2 = 1.55 if is_home_dog else 2.45

        settled_rows.append(SettledEvent(
            event_id=f"esports:game_{i}",
            sport="esports",
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
            league="ESL Pro League",
            period_scores_1=(16.0, 11.0, 16.0),
            period_scores_2=(14.0, 16.0, 10.0),
            source_url="",
            disposition="SETTLED",
        ))

    training_rows = build_training_rows(settled_rows)
    assert len(training_rows) == 35
    assert all("es_is_home_dog" in r.features for r in training_rows)

    predictions = walk_forward_predict(training_rows, min_train=10)
    assert len(predictions) > 0
    summary = validation_summary(predictions)
    assert "brier" in summary
    assert summary["brier"] is not None
    assert summary["brier"] < 1.0
