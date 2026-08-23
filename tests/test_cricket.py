from slumdog.contracts import EventSnapshot, H2HStats, RecentForm, SettledEvent, TimingClass
from slumdog.facets import build_numeric_features
from slumdog.cricket import (
    calculate_overround_cricket,
    detect_cricket_robber,
    devig_probabilities_cricket,
    extract_cricket_features,
)
from slumdog.training import build_training_rows, validation_summary
from slumdog.ml_meta import walk_forward_predict


def test_overround_and_devig_cricket():
    overround = calculate_overround_cricket(2.20, None, 1.70)
    assert overround is not None
    assert round(overround, 4) == 0.0428

    p1, px, p2 = devig_probabilities_cricket(2.20, None, 1.70)
    assert p1 is not None and px is None and p2 is not None
    assert round(p1 + p2, 6) == 1.0
    assert round(p1, 4) == 0.4359
    assert round(p2, 4) == 0.5641


def test_extract_cricket_features():
    event = EventSnapshot(
        event_id="cricket:1101",
        sport="cricket",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/cricket/matches/csk-mi-1101",
        participant_1="CSK",
        participant_2="MI",
        probability_1=0.45,
        probability_2=0.55,
        forebet_pick=2,
        odds_1=2.25,
        odds_2=1.68,
        predicted_score="175-182",
        predicted_total=357.0,
        facets={
            "match_format": "T20",
            "standings_1_rank": 3.0,
            "standings_2_rank": 1.0,
            "standings_1_pts": 14.0,
            "standings_2_pts": 18.0,
            "standings_1_nrr": 0.35,
            "standings_2_nrr": 0.85,
        },
        facet_timing={k: TimingClass.PRE_EVENT for k in [
            "match_format", "standings_1_rank", "standings_2_rank",
            "standings_1_pts", "standings_2_pts", "standings_1_nrr", "standings_2_nrr",
        ]},
    )
    h2h = H2HStats(total_games=6, participant_1_wins=3, participant_2_wins=3)
    recent_1 = RecentForm(wins=4, games=5)
    recent_2 = RecentForm(wins=5, games=5)

    candidate = detect_cricket_robber(event, h2h, recent_1, recent_2)
    assert candidate is not None
    assert candidate.participant_index == 1

    cf = extract_cricket_features(event, candidate, h2h, recent_1, recent_2)
    assert cf.is_home_dog == 1.0
    assert cf.is_t20_format == 1.0
    assert cf.is_test_format == 0.0
    assert cf.dog_price == 2.25
    assert cf.favorite_price == 1.68
    assert cf.predicted_total_runs == 357.0
    assert cf.predicted_run_margin_dog == -7.0  # 175 - 182
    assert cf.dog_rank == 3.0
    assert cf.favorite_rank == 1.0
    assert cf.rank_gap == -2.0
    assert cf.standings_pts_gap == -4.0  # 14 - 18
    assert round(cf.standings_nrr_gap, 2) == -0.50 # 0.35 - 0.85

    feat_dict = cf.to_dict()
    assert feat_dict["cr_is_home_dog"] == 1.0
    assert feat_dict["cr_is_t20_format"] == 1.0
    assert feat_dict["cr_predicted_total_runs"] == 357.0


def test_detect_cricket_robber_t20_bonus():
    event = EventSnapshot(
        event_id="cricket:1102",
        sport="cricket",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/cricket/matches/a-b-1102",
        participant_1="Home Dog",
        participant_2="Away Fav",
        probability_1=0.42,
        probability_2=0.58,
        forebet_pick=2,
        odds_1=2.40,
        odds_2=1.58,
        facets={"match_format": "T20 International"},
        facet_timing={"match_format": TimingClass.PRE_EVENT},
    )
    h2h = H2HStats(total_games=4, participant_1_wins=2, participant_2_wins=2)
    recent_1 = RecentForm(wins=3, games=5)
    recent_2 = RecentForm(wins=4, games=5)

    candidate = detect_cricket_robber(event, h2h, recent_1, recent_2)
    assert candidate is not None
    assert any("Home Ground Underdog" in r for r in candidate.reasons)
    assert any("T20 high-volatility format" in r for r in candidate.reasons)
    assert candidate.score >= 20.0


def test_build_numeric_features_includes_cricket_features():
    event = EventSnapshot(
        event_id="cricket:1103",
        sport="cricket",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/cricket/matches/a-b-1103",
        participant_1="India",
        participant_2="Australia",
        probability_1=0.40,
        probability_2=0.60,
        forebet_pick=2,
        odds_1=2.50,
        odds_2=1.55,
    )
    candidate = detect_cricket_robber(event)
    assert candidate is not None
    features = build_numeric_features(event, candidate)
    assert "cr_is_home_dog" in features
    assert "cr_forebet_dog_prob" in features
    assert "cr_favorite_dominance_ratio" in features


def test_cricket_walk_forward_training_pipeline():
    settled_rows = []
    for i in range(35):
        day = f"2026-01-{(i // 3) + 1:02d}"
        is_home_dog = (i % 2 == 1)
        dog_won = (i % 3 == 0)

        score_1 = 180.0 if (dog_won if is_home_dog else not dog_won) else 170.0
        score_2 = 170.0 if score_1 == 180.0 else 180.0
        winner = 1 if score_1 > score_2 else 2

        p1 = 0.40 if is_home_dog else 0.60
        p2 = 0.60 if is_home_dog else 0.40
        o1 = 2.45 if is_home_dog else 1.55
        o2 = 1.55 if is_home_dog else 2.45

        settled_rows.append(SettledEvent(
            event_id=f"cricket:game_{i}",
            sport="cricket",
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
            league="IPL",
            period_scores_1=(180.0,),
            period_scores_2=(170.0,),
            source_url="",
            disposition="SETTLED",
        ))

    training_rows = build_training_rows(settled_rows)
    assert len(training_rows) == 35
    assert all("cr_is_home_dog" in r.features for r in training_rows)

    predictions = walk_forward_predict(training_rows, min_train=10)
    assert len(predictions) > 0
    summary = validation_summary(predictions)
    assert "brier" in summary
    assert summary["brier"] is not None
    assert summary["brier"] < 1.0
