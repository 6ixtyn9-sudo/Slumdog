from slumdog.contracts import EventSnapshot, H2HStats, RecentForm, SettledEvent, TimingClass
from slumdog.facets import build_numeric_features
from slumdog.tennis import (
    calculate_overround_tennis,
    detect_tennis_robber,
    devig_probabilities_tennis,
    extract_tennis_features,
)
from slumdog.training import build_training_rows, validation_summary
from slumdog.ml_meta import walk_forward_predict


def test_overround_and_devig_tennis():
    overround = calculate_overround_tennis(1.40, 3.00)
    assert overround is not None
    assert round(overround, 4) == 0.0476

    p1, p2 = devig_probabilities_tennis(1.40, 3.00)
    assert p1 is not None and p2 is not None
    assert round(p1 + p2, 6) == 1.0
    assert round(p1, 4) == 0.6818
    assert round(p2, 4) == 0.3182


def test_extract_tennis_features_with_surface_specialist():
    event = EventSnapshot(
        event_id="tennis:301",
        sport="tennis",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/tennis/matches/alcaraz-sinner-301",
        participant_1="Alcaraz",
        participant_2="Sinner",
        probability_1=0.40,
        probability_2=0.60,
        forebet_pick=2,
        odds_1=2.60,
        odds_2=1.52,
        predicted_score="1-2",
        predicted_total=23.5,
        facets={
            "surface": "clay",
            "p1_clay_win_rate": 0.82,
            "p1_clay_sample": 50.0,
            "p2_clay_win_rate": 0.65,
            "p2_clay_sample": 40.0,
            "p1_height_inches": 72.0,
            "p2_height_inches": 75.0,
        },
        facet_timing={k: TimingClass.PRE_EVENT for k in [
            "surface", "p1_clay_win_rate", "p1_clay_sample",
            "p2_clay_win_rate", "p2_clay_sample", "p1_height_inches", "p2_height_inches",
        ]},
    )
    h2h = H2HStats(total_games=6, participant_1_wins=3, participant_2_wins=3)
    recent_1 = RecentForm(wins=4, games=5)
    recent_2 = RecentForm(wins=5, games=5)

    candidate = detect_tennis_robber(event, h2h, recent_1, recent_2)
    assert candidate is not None
    assert candidate.participant_index == 1

    tf = extract_tennis_features(event, candidate, h2h, recent_1, recent_2)
    assert tf.dog_price == 2.60
    assert tf.favorite_price == 1.52
    assert tf.surface_dog_win_rate == 0.82
    assert tf.surface_fav_win_rate == 0.65
    assert round(tf.surface_win_rate_gap, 2) == 0.17
    assert tf.surface_specialist_dog == 1.0
    assert tf.predicted_set_margin_dog == -1.0  # 1 - 2
    assert tf.dog_height_inches == 72.0
    assert tf.fav_height_inches == 75.0
    assert tf.height_gap_inches == -3.0

    feat_dict = tf.to_dict()
    assert feat_dict["ten_surface_specialist_dog"] == 1.0
    assert feat_dict["ten_surface_win_rate_gap_missing"] == 0.0
    assert round(feat_dict["ten_clay_win_rate_gap"], 2) == 0.17
    assert feat_dict["ten_hard_win_rate_gap_missing"] == 1.0


def test_tennis_robber_detector_height_advantage():
    event = EventSnapshot(
        event_id="tennis:305",
        sport="tennis",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/tennis/matches/a-b-305",
        participant_1="Big Server",
        participant_2="Short Returner",
        probability_1=0.38,
        probability_2=0.62,
        forebet_pick=2,
        odds_1=2.60,
        odds_2=1.52,
        facets={
            "p1_height_inches": 80.0,
            "p2_height_inches": 71.0,
        },
        facet_timing={"p1_height_inches": TimingClass.PRE_EVENT, "p2_height_inches": TimingClass.PRE_EVENT},
    )
    candidate = detect_tennis_robber(event)
    assert candidate is not None
    assert any("Height / Serve Edge" in r for r in candidate.reasons)



def test_tennis_robber_detector_surface_bonus():
    event = EventSnapshot(
        event_id="tennis:302",
        sport="tennis",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/tennis/matches/a-b-302",
        participant_1="Clay Specialist",
        participant_2="Top Seed",
        probability_1=0.36,
        probability_2=0.64,
        forebet_pick=2,
        odds_1=2.75,
        odds_2=1.45,
        facets={
            "surface": "clay",
            "p1_clay_win_rate": 0.75,
            "p1_clay_sample": 30.0,
        },
        facet_timing={"surface": TimingClass.PRE_EVENT, "p1_clay_win_rate": TimingClass.PRE_EVENT, "p1_clay_sample": TimingClass.PRE_EVENT},
    )
    h2h = H2HStats(total_games=3, participant_1_wins=1, participant_2_wins=2)
    recent_1 = RecentForm(wins=3, games=5)
    recent_2 = RecentForm(wins=4, games=5)

    candidate = detect_tennis_robber(event, h2h, recent_1, recent_2)
    assert candidate is not None
    assert any("Surface Specialist" in r for r in candidate.reasons)
    assert candidate.score >= 20.0


def test_build_numeric_features_includes_tennis_features():
    event = EventSnapshot(
        event_id="tennis:303",
        sport="tennis",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/tennis/matches/a-b-303",
        participant_1="Player A",
        participant_2="Player B",
        probability_1=0.30,
        probability_2=0.70,
        forebet_pick=2,
        odds_1=3.20,
        odds_2=1.35,
    )
    candidate = detect_tennis_robber(event)
    assert candidate is not None
    features = build_numeric_features(event, candidate)
    assert "ten_forebet_dog_prob" in features
    assert "ten_surface_specialist_dog" in features
    assert "ten_favorite_dominance_ratio" in features


def test_tennis_walk_forward_training_pipeline():
    settled_rows = []
    for i in range(35):
        day = f"2026-01-{(i // 3) + 1:02d}"
        is_dog_p1 = (i % 2 == 1)
        dog_won = (i % 3 == 0)

        score_1 = 2.0 if (dog_won if is_dog_p1 else not dog_won) else 1.0
        score_2 = 1.0 if score_1 == 2.0 else 2.0
        winner = 1 if score_1 > score_2 else 2

        p1 = 0.35 if is_dog_p1 else 0.65
        p2 = 0.65 if is_dog_p1 else 0.35
        o1 = 2.80 if is_dog_p1 else 1.45
        o2 = 1.45 if is_dog_p1 else 2.80

        settled_rows.append(SettledEvent(
            event_id=f"tennis:match_{i}",
            sport="tennis",
            event_date=day,
            participant_1=f"Player_{i % 8}",
            participant_2=f"Player_{(i + 1) % 8}",
            winner_index=winner,
            score_1=score_1,
            score_2=score_2,
            probability_1=p1,
            draw_probability=None,
            probability_2=p2,
            forebet_pick=2 if is_dog_p1 else 1,
            odds_1=o1,
            odds_2=o2,
            league="ATP",
            period_scores_1=(6.0, 4.0, 6.0),
            period_scores_2=(4.0, 6.0, 3.0),
            source_url="",
            disposition="SETTLED",
        ))

    training_rows = build_training_rows(settled_rows)
    assert len(training_rows) == 35
    assert all("ten_forebet_dog_prob" in r.features for r in training_rows)

    predictions = walk_forward_predict(training_rows, min_train=10)
    assert len(predictions) > 0
    summary = validation_summary(predictions)
    assert "brier" in summary
    assert summary["brier"] is not None
    assert summary["brier"] < 1.0
