from datetime import datetime, timezone

from slumdog.contracts import CandidateState, EventSnapshot, H2HStats, RecentForm, TimingClass
from slumdog.magolide import RobberConfig, detect_robber, identify_underdog
from slumdog.pipeline import build_shadow_robbers


def event(**overrides):
    payload = dict(
        event_id="basketball:test:a-b",
        sport="basketball",
        event_date="2026-08-22",
        captured_at=datetime.now(timezone.utc).isoformat(),
        source_url="https://www.forebet.com/example",
        participant_1="Alpha",
        participant_2="Beta",
        probability_1=0.35,
        probability_2=0.65,
        forebet_pick=2,
        odds_1=2.10,
        odds_2=1.40,
    )
    payload.update(overrides)
    return EventSnapshot(**payload)


def test_odds_define_underdog_before_forebet_pick():
    e = event(forebet_pick=1)  # source pick conflicts with market prices
    identity = identify_underdog(e)
    assert identity.index == 1
    assert identity.basis == "displayed_odds"


def test_no_odds_uses_opposite_forebet_pick():
    e = event(odds_1=None, odds_2=None, forebet_pick=2)
    identity = identify_underdog(e)
    assert identity.index == 1
    assert identity.basis == "opposite_forebet_pick"


def test_legacy_factors_reproduce_high_confidence_priced_robber():
    e = event()
    h2h = H2HStats(
        total_games=4,
        participant_1_wins=2,
        period_win_rates_1=(0.60, 0.55, 0.40, 0.30),
        half_1_rate_1=0.60,
        half_2_rate_1=0.70,
    )
    candidate = detect_robber(e, h2h, RecentForm(4, 5), RecentForm(4, 5))
    assert candidate is not None
    assert candidate.participant == "Alpha"
    assert candidate.score == 79
    assert candidate.state == CandidateState.SHADOW_PRICED
    assert candidate.legacy_confidence >= 60
    assert candidate.legacy_calibration_forensic is True
    assert candidate.legacy_probability_advantage >= 0.08
    assert any("H2H" in reason for reason in candidate.reasons)
    assert any("Period" in reason for reason in candidate.reasons)
    assert any("Hot" in reason for reason in candidate.reasons)


def test_unpriced_robber_can_be_learned_but_has_no_ev():
    e = event(odds_1=None, odds_2=None)
    h2h = H2HStats(total_games=4, participant_1_wins=2)
    candidate = detect_robber(e, h2h, RecentForm(4, 5), RecentForm(2, 5))
    assert candidate is not None
    assert candidate.state == CandidateState.SHADOW_UNPRICED
    assert candidate.price is None
    assert candidate.legacy_expected_value is None
    assert candidate.legacy_probability_advantage is None


def test_no_output_count_cap():
    facets = {
        "h2h_total_games": 4,
        "h2h_participant_1_wins": 2,
        "period_win_rates_1": [0.6, 0.55],
        "recent_1_wins": 4,
        "recent_1_games": 5,
    }
    timing = {key: TimingClass.PRE_EVENT for key in facets}
    events = [
        event(
            event_id=f"basketball:test:{i}", odds_1=2.1, odds_2=1.35,
            facets=facets, facet_timing=timing,
        )
        for i in range(8)
    ]
    candidates = build_shadow_robbers(
        events,
        RobberConfig(emit_min_confidence=50),
    )
    assert len(candidates) == 8
