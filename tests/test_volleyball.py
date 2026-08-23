from slumdog.contracts import EventSnapshot, H2HStats, RecentForm, SettledEvent, TimingClass
from slumdog.facets import build_numeric_features
from slumdog.volleyball import (
    calculate_overround_volleyball,
    detect_volleyball_robber,
    devig_probabilities_volleyball,
    extract_volleyball_features,
)
from slumdog.training import build_training_rows, validation_summary
from slumdog.ml_meta import walk_forward_predict


def test_overround_and_devig_volleyball():
    overround = calculate_overround_volleyball(1.75, 2.15)
    assert overround is not None
    assert round(overround, 4) == 0.0365

    p1, p2 = devig_probabilities_volleyball(1.75, 2.15)
    assert p1 is not None and p2 is not None
    assert round(p1 + p2, 6) == 1.0
    assert round(p1, 4) == 0.5513
    assert round(p2, 4) == 0.4487


def test_extract_volleyball_features():
    event = EventSnapshot(
        event_id="volleyball:901",
        sport="volleyball",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/volleyball/matches/trentino-perugia-901",
        participant_1="Trentino",
        participant_2="Perugia",
        probability_1=0.45,
        probability_2=0.55,
        forebet_pick=2,
        odds_1=2.30,
        odds_2=1.65,
        predicted_score="2-3",
        predicted_total=5.0,
        facets={
            "period_values": [["25", "23"], ["22", "25"], ["25", "21"]],
            "standings_1_rank": 3.0,
            "standings_2_rank": 1.0,
            "standings_1_pts": 45.0,
            "standings_2_pts": 52.0,
            "standings_1_gd": 12.0,
            "standings_2_gd": 25.0,
        },
        facet_timing={k: TimingClass.PRE_EVENT for k in [
            "period_values", "standings_1_rank", "standings_2_rank",
            "standings_1_pts", "standings_2_pts", "standings_1_gd", "standings_2_gd",
        ]},
    )
    h2h = H2HStats(total_games=6, participant_1_wins=3, participant_2_wins=3)
    recent_1 = RecentForm(wins=4, games=5)
    recent_2 = RecentForm(wins=5, games=5)

    candidate = detect_volleyball_robber(event, h2h, recent_1, recent_2)
    assert candidate is not None
    assert candidate.participant_index == 1

    vf = extract_volleyball_features(event, candidate, h2h, recent_1, recent_2)
    assert vf.is_home_dog == 1.0
    assert vf.dog_price == 2.30
    assert vf.favorite_price == 1.65
    assert vf.predicted_total_sets == 5.0
    assert vf.predicted_set_margin_dog == -1.0  # 2 - 3
    assert vf.decider_match_expectation == 1.0  # 2-3 score is 5-set decider
    assert vf.sweep_match_expectation == 0.0
    assert vf.s1_margin_dog == 2.0  # 25 - 23
    assert vf.s2_margin_dog == -3.0 # 22 - 25
    assert vf.s3_margin_dog == 4.0  # 25 - 21
    assert vf.sets_projected_won == 2.0  # S1 and S3 > 0
    assert vf.dog_rank == 3.0
    assert vf.favorite_rank == 1.0
    assert vf.rank_gap == -2.0
    assert vf.standings_pts_gap == -7.0   # 45 - 52
    assert vf.standings_set_diff_gap == -13.0 # 12 - 25

    feat_dict = vf.to_dict()
    assert feat_dict["vb_is_home_dog"] == 1.0
    assert feat_dict["vb_decider_match_expectation"] == 1.0
    assert feat_dict["vb_predicted_total_sets"] == 5.0


def test_detect_volleyball_robber_decider_bonus():
    event = EventSnapshot(
        event_id="volleyball:902",
        sport="volleyball",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/volleyball/matches/a-b-902",
        participant_1="Home Dog",
        participant_2="Away Fav",
        probability_1=0.42,
        probability_2=0.58,
        forebet_pick=2,
        odds_1=2.40,
        odds_2=1.58,
        predicted_score="2-3",
        predicted_total=5.0,
    )
    h2h = H2HStats(total_games=4, participant_1_wins=2, participant_2_wins=2)
    recent_1 = RecentForm(wins=3, games=5)
    recent_2 = RecentForm(wins=4, games=5)

    candidate = detect_volleyball_robber(event, h2h, recent_1, recent_2)
    assert candidate is not None
    assert any("Home Court Underdog" in r for r in candidate.reasons)
    assert any("5-set decider expectation" in r for r in candidate.reasons)
    assert candidate.score >= 20.0


def test_build_numeric_features_includes_volleyball_features():
    event = EventSnapshot(
        event_id="volleyball:903",
        sport="volleyball",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/volleyball/matches/a-b-903",
        participant_1="Modena",
        participant_2="Lube Civitanova",
        probability_1=0.40,
        probability_2=0.60,
        forebet_pick=2,
        odds_1=2.50,
        odds_2=1.55,
    )
    candidate = detect_volleyball_robber(event)
    assert candidate is not None
    features = build_numeric_features(event, candidate)
    assert "vb_is_home_dog" in features
    assert "vb_forebet_dog_prob" in features
    assert "vb_favorite_dominance_ratio" in features


def test_volleyball_walk_forward_training_pipeline():
    settled_rows = []
    for i in range(35):
        day = f"2026-01-{(i // 3) + 1:02d}"
        is_home_dog = (i % 2 == 1)
        dog_won = (i % 3 == 0)

        score_1 = 3.0 if (dog_won if is_home_dog else not dog_won) else 2.0
        score_2 = 2.0 if score_1 == 3.0 else 3.0
        winner = 1 if score_1 > score_2 else 2

        p1 = 0.40 if is_home_dog else 0.60
        p2 = 0.60 if is_home_dog else 0.40
        o1 = 2.45 if is_home_dog else 1.55
        o2 = 1.55 if is_home_dog else 2.45

        settled_rows.append(SettledEvent(
            event_id=f"volleyball:game_{i}",
            sport="volleyball",
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
            league="SuperLega",
            period_scores_1=(25.0, 22.0, 25.0, 23.0, 15.0),
            period_scores_2=(23.0, 25.0, 21.0, 25.0, 12.0),
            source_url="",
            disposition="SETTLED",
        ))

    training_rows = build_training_rows(settled_rows)
    assert len(training_rows) == 35
    assert all("vb_is_home_dog" in r.features for r in training_rows)

    predictions = walk_forward_predict(training_rows, min_train=10)
    assert len(predictions) > 0
    summary = validation_summary(predictions)
    assert "brier" in summary
    assert summary["brier"] is not None
    assert summary["brier"] < 1.0
