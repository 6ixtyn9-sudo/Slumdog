import json

from slumdog.contracts import CandidateState, PriceState, RobberCandidate
from slumdog.ledger import freeze_candidates


def candidate(score):
    return RobberCandidate(
        event_id="e1", sport="tennis", participant_index=1,
        participant="A", opponent="B", score=score, reasons=["test"],
        raw_confidence=65, legacy_confidence=62, price=2.2,
        implied_probability=1 / 2.2, legacy_probability=0.55,
        legacy_expected_value=0.21, legacy_probability_advantage=0.095,
        price_state=PriceState.FOREBET_PRICED,
        state=CandidateState.SHADOW_PRICED,
        underdog_basis="displayed_odds",
    )


def test_first_frozen_payload_wins(tmp_path):
    path = freeze_candidates("2026-08-22", [candidate(30)], tmp_path)
    freeze_candidates("2026-08-22", [candidate(99)], tmp_path)
    rows = json.loads(path.read_text())
    assert len(rows) == 1
    assert rows[0]["score"] == 30
