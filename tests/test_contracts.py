from datetime import datetime, timezone

import pytest

from slumdog.contracts import EventSnapshot, PriceState, TimingClass


def make_event(**overrides):
    payload = dict(
        event_id="tennis:x-y",
        sport="tennis",
        event_date="2026-08-22",
        captured_at=datetime.now(timezone.utc).isoformat(),
        source_url="https://www.forebet.com/en/tennis/example",
        participant_1="X",
        participant_2="Y",
        probability_1=0.40,
        probability_2=0.60,
        forebet_pick=2,
        facets={"rank_gap": 12, "live_score": "1-0", "mystery": 99},
        facet_timing={
            "rank_gap": TimingClass.PRE_EVENT,
            "live_score": TimingClass.LIVE_ONLY,
            "mystery": TimingClass.UNKNOWN,
        },
    )
    payload.update(overrides)
    return EventSnapshot(**payload)


def test_only_explicit_pre_event_facets_are_model_eligible():
    event = make_event()
    assert event.pre_event_facets() == {"rank_gap": 12}


def test_missing_odds_is_explicit():
    assert make_event().price_state == PriceState.PRICE_MISSING
    assert make_event(odds_1=2.2, odds_2=1.7).price_state == PriceState.FOREBET_PRICED


def test_invalid_probability_and_naive_timestamp_fail():
    with pytest.raises(ValueError, match="outside"):
        make_event(probability_1=1.2)
    with pytest.raises(ValueError, match="timezone"):
        make_event(captured_at="2026-08-22T08:00:00")
