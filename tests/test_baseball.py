from slumdog.contracts import EventSnapshot, H2HStats, RecentForm, SettledEvent, TimingClass
from slumdog.facets import build_numeric_features
from slumdog.baseball import (
    calculate_overround_baseball,
    detect_baseball_robber,
    devig_probabilities_baseball,
    extract_baseball_features,
)
from slumdog.training import build_training_rows, validation_summary
from slumdog.ml_meta import walk_forward_predict


def test_overround_and_devig_baseball():
    overround = calculate_overround_baseball(1.80, 2.05)
    assert overround is not None
    assert round(overround, 4) == 0.0434

    p1, p2 = devig_probabilities_baseball(1.80, 2.05)
    assert p1 is not None and p2 is not None
    assert round(p1 + p2, 6) == 1.0
    assert round(p1, 4) == 0.5325
    assert round(p2, 4) == 0.4675


def test_extract_baseball_features():
    event = EventSnapshot(
        event_id="baseball:501",
        sport="baseball",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/baseball/matches/red-sox-yankees-501",
        participant_1="Red Sox",
        participant_2="Yankees",
        probability_1=0.42,
        probability_2=0.58,
        forebet_pick=2,
        odds_1=2.35,
        odds_2=1.62,
        predicted_score="3-4",
        predicted_total=7.0,
        facets={
            "standings_1_rank": 8.0,
            "standings_2_rank": 2.0,
            "standings_1_wins": 50.0,
            "standings_1_losses": 45.0,
            "standings_2_wins": 65.0,
            "standings_2_losses": 30.0,
        },
        facet_timing={k: TimingClass.PRE_EVENT for k in [
            "standings_1_rank", "standings_2_rank",
            "standings_1_wins", "standings_1_losses",
            "standings_2_wins", "standings_2_losses",
        ]},
    )
    h2h = H2HStats(total_games=6, participant_1_wins=3, participant_2_wins=3)
    recent_1 = RecentForm(wins=4, games=6)
    recent_2 = RecentForm(wins=4, games=6)

    candidate = detect_baseball_robber(event, h2h, recent_1, recent_2)
    assert candidate is not None
    assert candidate.participant_index == 1

    bf = extract_baseball_features(event, candidate, h2h, recent_1, recent_2)
    assert bf.is_home_dog == 1.0
    assert bf.dog_price == 2.35
    assert bf.favorite_price == 1.62
    assert bf.predicted_total_runs == 7.0
    assert bf.low_total_environment == 1.0
    assert bf.high_total_environment == 0.0
    assert bf.predicted_run_margin_dog == -1.0  # 3 - 4
    assert bf.dog_rank == 8.0
    assert bf.favorite_rank == 2.0
    assert bf.rank_gap == -6.0
    assert bf.standings_win_pct_gap is not None
    assert round(bf.standings_win_pct_gap, 4) < 0.0

    feat_dict = bf.to_dict()
    assert feat_dict["ba_is_home_dog"] == 1.0
    assert feat_dict["ba_low_total_environment"] == 1.0
    assert feat_dict["ba_high_total_environment"] == 0.0
    assert feat_dict["ba_predicted_total_runs"] == 7.0
    assert feat_dict["ba_travel_distance_km_missing"] == 1.0


def test_extract_baseball_features_with_detail_differentials():
    event = EventSnapshot(
        event_id="baseball:505",
        sport="baseball",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/baseball/matches/home-away-505",
        participant_1="San Francisco Giants",
        participant_2="New York Yankees",
        probability_1=0.41,
        probability_2=0.59,
        forebet_pick=2,
        odds_1=2.40,
        odds_2=1.58,
        predicted_score="3-4",
        predicted_total=7.0,
        facets={
            "travel_distance_km": 4100.0,
            "p1_scored_avg": 4.6,
            "p1_conceded_avg": 4.2,
            "p2_scored_avg": 5.1,
            "p2_conceded_avg": 4.0,
        },
        facet_timing={k: TimingClass.PRE_EVENT for k in [
            "travel_distance_km", "p1_scored_avg", "p1_conceded_avg", "p2_scored_avg", "p2_conceded_avg",
        ]},
    )
    candidate = detect_baseball_robber(event)
    assert candidate is not None
    assert any("Fav Away Road Fatigue" in r for r in candidate.reasons)

    bf = extract_baseball_features(event, candidate)
    assert bf.travel_distance_km == 4100.0
    assert bf.dog_travel_distance == 0.0
    assert bf.fav_travel_distance == 4100.0
    # net run diff: (4.6 - 4.2) - (5.1 - 4.0) = 0.4 - 1.1 = -0.7
    assert round(bf.net_run_differential_gap, 1) == -0.7



def test_detect_baseball_robber_batting_last_bonus():
    event = EventSnapshot(
        event_id="baseball:502",
        sport="baseball",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/baseball/matches/a-b-502",
        participant_1="Home Dog",
        participant_2="Away Fav",
        probability_1=0.41,
        probability_2=0.59,
        forebet_pick=2,
        odds_1=2.40,
        odds_2=1.58,
        predicted_total=6.5,
    )
    h2h = H2HStats(total_games=5, participant_1_wins=2, participant_2_wins=3)
    recent_1 = RecentForm(wins=3, games=5)
    recent_2 = RecentForm(wins=4, games=5)

    candidate = detect_baseball_robber(event, h2h, recent_1, recent_2)
    assert candidate is not None
    assert any("Home Underdog / Batting Last" in r for r in candidate.reasons)
    assert any("Pitcher's duel expectation" in r for r in candidate.reasons)
    assert candidate.score >= 20.0


def test_build_numeric_features_includes_baseball_features():
    event = EventSnapshot(
        event_id="baseball:503",
        sport="baseball",
        event_date="2026-08-23",
        captured_at="2026-08-23T10:00:00+00:00",
        source_url="https://www.forebet.com/en/baseball/matches/a-b-503",
        participant_1="Cubs",
        participant_2="Cardinals",
        probability_1=0.39,
        probability_2=0.61,
        forebet_pick=2,
        odds_1=2.55,
        odds_2=1.52,
    )
    candidate = detect_baseball_robber(event)
    assert candidate is not None
    features = build_numeric_features(event, candidate)
    assert "ba_is_home_dog" in features
    assert "ba_forebet_dog_prob" in features
    assert "ba_favorite_dominance_ratio" in features


def test_baseball_walk_forward_training_pipeline():
    settled_rows = []
    for i in range(35):
        day = f"2026-01-{(i // 3) + 1:02d}"
        is_home_dog = (i % 2 == 1)
        dog_won = (i % 3 == 0)

        score_1 = 5.0 if (dog_won if is_home_dog else not dog_won) else 3.0
        score_2 = 3.0 if score_1 == 5.0 else 5.0
        winner = 1 if score_1 > score_2 else 2

        p1 = 0.39 if is_home_dog else 0.61
        p2 = 0.61 if is_home_dog else 0.39
        o1 = 2.50 if is_home_dog else 1.54
        o2 = 1.54 if is_home_dog else 2.50

        settled_rows.append(SettledEvent(
            event_id=f"baseball:game_{i}",
            sport="baseball",
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
            league="MLB",
            period_scores_1=(1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0),
            period_scores_2=(0.0, 0.0, 1.0, 1.0, 0.0, 1.0, 0.0, 0.0, 0.0),
            source_url="",
            disposition="SETTLED",
        ))

    training_rows = build_training_rows(settled_rows)
    assert len(training_rows) == 35
    assert all("ba_is_home_dog" in r.features for r in training_rows)

    predictions = walk_forward_predict(training_rows, min_train=10)
    assert len(predictions) > 0
    summary = validation_summary(predictions)
    assert "brier" in summary
    assert summary["brier"] is not None
    assert summary["brier"] < 1.0
