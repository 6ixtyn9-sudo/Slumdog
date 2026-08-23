from slumdog.contracts import EventSnapshot, H2HStats, RecentForm, SettledEvent, TimingClass
from slumdog.facets import build_numeric_features
from slumdog.mma import (
    calculate_overround_mma,
    detect_mma_robber,
    devig_probabilities_mma,
    extract_mma_features,
)
from slumdog.training import build_training_rows, validation_summary
from slumdog.ml_meta import walk_forward_predict


def test_overround_and_devig_mma():
    overround = calculate_overround_mma(2.35, 1.62)
    assert overround is not None
    assert round(overround, 4) == 0.0428

    p1, p2 = devig_probabilities_mma(2.35, 1.62)
    assert p1 is not None and p2 is not None
    assert round(p1 + p2, 6) == 1.0
    assert round(p1, 4) == 0.4081
    assert round(p2, 4) == 0.5919


def test_extract_mma_features():
    event = EventSnapshot(
        event_id="mma:1201",
        sport="mma",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/mma/matches/fighter1-fighter2-1201",
        participant_1="Underdog Striker",
        participant_2="Favorite Grappler",
        probability_1=0.42,
        probability_2=0.58,
        forebet_pick=2,
        odds_1=2.30,
        odds_2=1.65,
        facets={
            "fighter_1_height": 185.0,
            "fighter_2_height": 178.0,
            "fighter_1_reach": 195.0,
            "fighter_2_reach": 182.0,
            "fighter_1_stance": "Southpaw",
            "fighter_2_stance": "Orthodox",
            "predicted_method": "KO/TKO",
            "strikes_1": 4.5,
            "takedowns_1": 1.2,
        },
        facet_timing={k: TimingClass.PRE_EVENT for k in [
            "fighter_1_height", "fighter_2_height",
            "fighter_1_reach", "fighter_2_reach",
            "fighter_1_stance", "fighter_2_stance",
            "predicted_method", "strikes_1", "takedowns_1",
        ]},
    )
    h2h = H2HStats(total_games=1, participant_1_wins=1, participant_2_wins=0)
    recent_1 = RecentForm(wins=4, games=5)
    recent_2 = RecentForm(wins=5, games=5)

    candidate = detect_mma_robber(event, h2h, recent_1, recent_2)
    assert candidate is not None
    assert candidate.participant_index == 1

    mf = extract_mma_features(event, candidate, h2h, recent_1, recent_2)
    assert mf.dog_price == 2.30
    assert mf.favorite_price == 1.65
    assert mf.dog_height_cm == 185.0
    assert mf.fav_height_cm == 178.0
    assert mf.height_gap_cm == 7.0
    assert mf.dog_reach_cm == 195.0
    assert mf.fav_reach_cm == 182.0
    assert mf.reach_gap_cm == 13.0
    assert mf.has_reach_advantage_dog == 1.0
    assert mf.is_southpaw_dog == 1.0
    assert mf.ko_finish_potential == 1.0
    assert mf.sub_finish_potential == 0.0

    feat_dict = mf.to_dict()
    assert feat_dict["mma_has_reach_advantage_dog"] == 1.0
    assert feat_dict["mma_is_southpaw_dog"] == 1.0
    assert feat_dict["mma_ko_finish_potential"] == 1.0
    assert feat_dict["mma_takedown_differential_missing"] == 1.0


def test_extract_mma_features_with_detail_differentials():
    event = EventSnapshot(
        event_id="mma:1205",
        sport="mma",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/mma/matches/fighter-a-b-1205",
        participant_1="Challenger",
        participant_2="Champion",
        probability_1=0.42,
        probability_2=0.58,
        forebet_pick=2,
        odds_1=2.40,
        odds_2=1.58,
        facets={
            "fighter_1_stance": "Southpaw",
            "fighter_2_stance": "Orthodox",
            "fighter_1_reach": 190.0,
            "fighter_2_reach": 182.0,
            "takedowns_1": 2.5,
            "takedowns_2": 1.0,
            "strikes_1": 4.8,
            "strikes_2": 3.2,
        },
        facet_timing={k: TimingClass.PRE_EVENT for k in [
            "fighter_1_stance", "fighter_2_stance", "fighter_1_reach", "fighter_2_reach",
            "takedowns_1", "takedowns_2", "strikes_1", "strikes_2",
        ]},
    )
    candidate = detect_mma_robber(event)
    assert candidate is not None
    assert any("Southpaw stance advantage" in r for r in candidate.reasons)

    mf = extract_mma_features(event, candidate)
    assert mf.is_southpaw_dog == 1.0
    assert mf.is_southpaw_fav == 0.0
    assert mf.takedown_avg_dog == 2.5
    assert mf.takedown_avg_fav == 1.0
    assert round(mf.takedown_differential, 1) == 1.5
    assert mf.sig_strikes_landed_dog == 4.8
    assert mf.sig_strikes_landed_fav == 3.2
    assert round(mf.sig_strikes_differential, 1) == 1.6



def test_detect_mma_robber_reach_bonus():
    event = EventSnapshot(
        event_id="mma:1202",
        sport="mma",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/mma/matches/a-b-1202",
        participant_1="Dog Fighter",
        participant_2="Fav Fighter",
        probability_1=0.40,
        probability_2=0.60,
        forebet_pick=2,
        odds_1=2.45,
        odds_2=1.58,
        facets={
            "fighter_1_reach": 192.0,
            "fighter_2_reach": 184.0,
        },
        facet_timing={"fighter_1_reach": TimingClass.PRE_EVENT, "fighter_2_reach": TimingClass.PRE_EVENT},
    )
    h2h = H2HStats(total_games=1, participant_1_wins=1, participant_2_wins=0)
    recent_1 = RecentForm(wins=3, games=5)
    recent_2 = RecentForm(wins=4, games=5)

    candidate = detect_mma_robber(event, h2h, recent_1, recent_2)
    assert candidate is not None
    assert any("Reach advantage" in r for r in candidate.reasons)
    assert candidate.score >= 20.0


def test_build_numeric_features_includes_mma_features():
    event = EventSnapshot(
        event_id="mma:1203",
        sport="mma",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/mma/matches/a-b-1203",
        participant_1="Aspinall",
        participant_2="Jones",
        probability_1=0.40,
        probability_2=0.60,
        forebet_pick=2,
        odds_1=2.50,
        odds_2=1.55,
    )
    candidate = detect_mma_robber(event)
    assert candidate is not None
    features = build_numeric_features(event, candidate)
    assert "mma_forebet_dog_prob" in features
    assert "mma_has_reach_advantage_dog" in features
    assert "mma_favorite_dominance_ratio" in features


def test_mma_walk_forward_training_pipeline():
    settled_rows = []
    for i in range(35):
        day = f"2026-01-{(i // 3) + 1:02d}"
        is_home_dog = (i % 2 == 1)
        dog_won = (i % 3 == 0)

        score_1 = 1.0 if (dog_won if is_home_dog else not dog_won) else 0.0
        score_2 = 0.0 if score_1 == 1.0 else 1.0
        winner = 1 if score_1 > score_2 else 2

        p1 = 0.40 if is_home_dog else 0.60
        p2 = 0.60 if is_home_dog else 0.40
        o1 = 2.45 if is_home_dog else 1.55
        o2 = 1.55 if is_home_dog else 2.45

        settled_rows.append(SettledEvent(
            event_id=f"mma:fight_{i}",
            sport="mma",
            event_date=day,
            participant_1=f"Fighter_{i % 6}",
            participant_2=f"Fighter_{(i + 1) % 6}",
            winner_index=winner,
            score_1=score_1,
            score_2=score_2,
            probability_1=p1,
            draw_probability=None,
            probability_2=p2,
            forebet_pick=2 if is_home_dog else 1,
            odds_1=o1,
            odds_2=o2,
            league="UFC",
            period_scores_1=(1.0,),
            period_scores_2=(0.0,),
            source_url="",
            disposition="SETTLED",
        ))

    training_rows = build_training_rows(settled_rows)
    assert len(training_rows) == 35
    assert all("mma_forebet_dog_prob" in r.features for r in training_rows)

    predictions = walk_forward_predict(training_rows, min_train=10)
    assert len(predictions) > 0
    summary = validation_summary(predictions)
    assert "brier" in summary
    assert summary["brier"] is not None
    assert summary["brier"] < 1.0
