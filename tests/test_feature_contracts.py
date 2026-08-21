from slumdog.feature_contracts import CONTRACTS, MODEL_TRAINING_ALLOWED
from slumdog.sports import SPORTS


def test_every_sport_has_distinct_feature_contract():
    assert set(CONTRACTS) == set(SPORTS)
    required_sets = {sport: contract.required for sport, contract in CONTRACTS.items()}
    assert len(set(required_sets.values())) == len(SPORTS)


def test_model_training_is_frozen_during_depth_audit():
    assert MODEL_TRAINING_ALLOWED is False
    for contract in CONTRACTS.values():
        assert "final_score" in contract.blocked
        assert "unknown_timing_field" in contract.blocked
