"""Milestone 4 — price-free historical example builder tests.

Required tests per Milestone 4H:
- Price independence
- Draw semantics
- Timing
- Integrity
- Serialization
"""

import pytest

from slumdog.contracts import SettledEvent
from slumdog.dataset import (
    ALLOWED_FEATURES,
    FEATURE_CONTRACT_VERSION,
    LABEL_CONTRACT_VERSION,
    PROHIBITED_KEYS,
    PriceFreeUnderdogExample,
    PriceFreeDatasetReceipt,
    build_price_free_examples,
)


def make_settled(
    event_id="football:1",
    sport="football",
    event_date="2026-01-01",
    p1="Team A",
    p2="Team B",
    prob1=0.6,
    prob2=0.3,
    draw_prob=0.1,
    winner=2,
    score1=0,
    score2=1,
    disposition="SETTLED",
    odds1=None,
    odds2=None,
    source_url="https://www.forebet.com/en/football/matches/a-b/1",
):
    return SettledEvent(
        event_id=event_id,
        sport=sport,
        event_date=event_date,
        participant_1=p1,
        participant_2=p2,
        winner_index=winner,
        score_1=score1,
        score_2=score2,
        probability_1=prob1,
        probability_2=prob2,
        draw_probability=draw_prob,
        forebet_pick=1 if (prob1 or 0) > (prob2 or 0) else 2,
        odds_1=odds1,
        odds_2=odds2,
        league="TST",
        period_scores_1=(),
        period_scores_2=(),
        source_url=source_url,
        disposition=disposition,
    )


# ---------------------------------------------------------------------------
# Price independence
# ---------------------------------------------------------------------------

def test_odds_present_vs_absent_identical_identity():
    e_with_odds = make_settled(odds1=2.5, odds2=1.5)
    e_without_odds = make_settled(odds1=None, odds2=None)

    examples_with, _ = build_price_free_examples([e_with_odds])
    examples_without, _ = build_price_free_examples([e_without_odds])

    assert len(examples_with) == 1
    assert len(examples_without) == 1
    assert examples_with[0].favorite_index == examples_without[0].favorite_index
    assert examples_with[0].underdog_index == examples_without[0].underdog_index
    assert examples_with[0].favorite_probability == examples_without[0].favorite_probability
    assert examples_with[0].underdog_probability == examples_without[0].underdog_probability


def test_odds_present_vs_absent_identical_features():
    e_with = make_settled(odds1=2.5, odds2=1.5, event_date="2026-01-02")
    e_without = make_settled(odds1=None, odds2=None, event_date="2026-01-02")

    # Need prior history to test features — add same prior event
    prior = make_settled(event_id="football:prior", event_date="2026-01-01", winner=1, score1=2, score2=0)

    ex_with, _ = build_price_free_examples([prior, e_with])
    ex_without, _ = build_price_free_examples([prior, e_without])

    # Find the second event (2026-01-02)
    ex_with_target = [ex for ex in ex_with if ex.event_date == "2026-01-02"][0]
    ex_without_target = [ex for ex in ex_without if ex.event_date == "2026-01-02"][0]

    assert ex_with_target.features == ex_without_target.features
    assert ex_with_target.missingness == ex_without_target.missingness


def test_odds_present_vs_absent_identical_label():
    e_with = make_settled(odds1=5.0, odds2=1.2, winner=2)
    e_without = make_settled(odds1=None, odds2=None, winner=2)

    ex_with, _ = build_price_free_examples([e_with])
    ex_without, _ = build_price_free_examples([e_without])

    assert ex_with[0].label == ex_without[0].label == 1


def test_odds_present_vs_absent_identical_eligibility():
    e_with = make_settled(prob1=0.6, prob2=0.3, odds1=10.0, odds2=1.1)
    e_without = make_settled(prob1=0.6, prob2=0.3, odds1=None, odds2=None)

    ex_with, receipt_with = build_price_free_examples([e_with])
    ex_without, receipt_without = build_price_free_examples([e_without])

    assert receipt_with.eligible_examples == receipt_without.eligible_examples == 1
    assert len(ex_with) == len(ex_without) == 1


def test_extreme_odds_do_not_alter_example():
    e_normal = make_settled(odds1=2.0, odds2=2.0)
    e_extreme = make_settled(odds1=100.0, odds2=1.01)

    ex_normal, _ = build_price_free_examples([e_normal])
    ex_extreme, _ = build_price_free_examples([e_extreme])

    assert ex_normal[0].label == ex_extreme[0].label
    assert ex_normal[0].favorite_index == ex_extreme[0].favorite_index
    assert ex_normal[0].features["forebet_favorite_probability"] == ex_extreme[0].features["forebet_favorite_probability"]


def test_reversed_odds_do_not_alter_favorite_underdog():
    # Forebet says p1=0.6 fav, p2=0.3 dog, but odds reversed (p1 high odds)
    e_reversed_odds = make_settled(prob1=0.6, prob2=0.3, odds1=5.0, odds2=1.2)
    e_normal_odds = make_settled(prob1=0.6, prob2=0.3, odds1=1.2, odds2=5.0)

    ex_rev, _ = build_price_free_examples([e_reversed_odds])
    ex_norm, _ = build_price_free_examples([e_normal_odds])

    assert ex_rev[0].favorite_index == 1
    assert ex_norm[0].favorite_index == 1
    assert ex_rev[0].underdog_index == 2
    assert ex_norm[0].underdog_index == 2


# ---------------------------------------------------------------------------
# Draw semantics
# ---------------------------------------------------------------------------

def test_football_draw_eligible_label_zero():
    e_draw = make_settled(sport="football", winner=0, score1=1, score2=1, prob1=0.6, prob2=0.3, draw_prob=0.1)
    examples, receipt = build_price_free_examples([e_draw])

    assert len(examples) == 1
    assert examples[0].label == 0
    assert receipt.negative_draws == 1
    assert receipt.eligible_examples == 1


def test_basketball_draw_excluded():
    e_draw = make_settled(sport="basketball", winner=0, score1=100, score2=100, prob1=0.6, prob2=0.4, draw_prob=None)
    examples, receipt = build_price_free_examples([e_draw])

    assert len(examples) == 0
    assert receipt.excluded_unexpected_two_way_draw == 1
    assert receipt.input_rows == 1


def test_no_example_uses_index_zero_as_underdog():
    events = [
        make_settled(event_id=f"football:{i}", winner=1 if i % 2 == 0 else 2, event_date=f"2026-01-{i:02d}")
        for i in range(1, 10)
    ]
    examples, _ = build_price_free_examples(events)
    for ex in examples:
        assert ex.underdog_index != 0
        assert ex.favorite_index != 0
        assert ex.underdog_index in (1, 2)


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------

def test_future_rows_cannot_affect_past():
    past = make_settled(event_id="football:past", event_date="2026-01-01", p1="Alpha", p2="Beta", winner=1, score1=2, score2=0)
    future = make_settled(event_id="football:future", event_date="2026-01-10", p1="Alpha", p2="Beta", winner=2, score1=0, score2=1)

    # Build only past
    ex_past_only, _ = build_price_free_examples([past])
    # Build past + future
    ex_past_future, _ = build_price_free_examples([past, future])

    # Past example should be identical regardless of future presence
    past_only = [e for e in ex_past_only if e.event_id == "football:past"][0]
    past_with_future = [e for e in ex_past_future if e.event_id == "football:past"][0]

    assert past_only.features == past_with_future.features


def test_same_date_rows_cannot_affect_each_other():
    e1 = make_settled(event_id="football:e1", event_date="2026-01-05", p1="Alpha", p2="Beta", winner=1, score1=1, score2=0)
    e2 = make_settled(event_id="football:e2", event_date="2026-01-05", p1="Alpha", p2="Beta", winner=2, score1=0, score2=1)

    ex_both, _ = build_price_free_examples([e1, e2])
    ex_e1_only, _ = build_price_free_examples([e1])
    ex_e2_only, _ = build_price_free_examples([e2])

    # Same-date events should not inform each other — so features for e1 alone vs with e2 should be identical
    # Both have no prior history, so prior games 0
    e1_both = [e for e in ex_both if e.event_id == "football:e1"][0]
    e1_only = ex_e1_only[0]
    assert e1_both.features["underdog_prior_games"] == e1_only.features["underdog_prior_games"] == 0.0
    assert e1_both.features["h2h_prior_games"] == e1_only.features["h2h_prior_games"] == 0.0


def test_prior_rows_affect_later_rows():
    prior = make_settled(event_id="football:prior", event_date="2026-01-01", p1="Alpha", p2="Beta", winner=1, score1=2, score2=0)
    later = make_settled(event_id="football:later", event_date="2026-01-02", p1="Alpha", p2="Beta", winner=2, score1=0, score2=1)

    ex_prior_only, _ = build_price_free_examples([prior])
    ex_both, _ = build_price_free_examples([prior, later])

    later_ex = [e for e in ex_both if e.event_id == "football:later"][0]
    # Later should have prior games >0
    assert later_ex.features["h2h_prior_games"] == 1.0
    assert later_ex.features["underdog_prior_games"] is not None


def test_input_order_does_not_affect_output():
    e1 = make_settled(event_id="football:1", event_date="2026-01-01", winner=1)
    e2 = make_settled(event_id="football:2", event_date="2026-01-02", winner=2)
    e3 = make_settled(event_id="football:3", event_date="2026-01-03", winner=1)

    ex_ordered, receipt_ordered = build_price_free_examples([e1, e2, e3])
    ex_reversed, receipt_reversed = build_price_free_examples([e3, e2, e1])
    ex_shuffled, receipt_shuffled = build_price_free_examples([e2, e3, e1])

    assert [e.event_id for e in ex_ordered] == [e.event_id for e in ex_reversed] == [e.event_id for e in ex_shuffled]
    assert ex_ordered[0].features == ex_reversed[0].features == ex_shuffled[0].features
    assert receipt_ordered.input_digest == receipt_reversed.input_digest == receipt_shuffled.input_digest


def test_adding_future_row_does_not_change_prior_examples():
    e1 = make_settled(event_id="football:1", event_date="2026-01-01", winner=1)
    e2 = make_settled(event_id="football:2", event_date="2026-01-02", winner=2)

    ex_before, _ = build_price_free_examples([e1])
    ex_after, _ = build_price_free_examples([e1, e2])

    before = ex_before[0]
    after = [e for e in ex_after if e.event_id == "football:1"][0]
    assert before.features == after.features
    assert before.label == after.label


def test_h2h_only_uses_prior_date_meetings():
    # Two meetings same teams, one future should not count
    e_past = make_settled(event_id="football:past", event_date="2026-01-01", p1="Alpha", p2="Beta", winner=1, score1=1, score2=0)
    e_target = make_settled(event_id="football:target", event_date="2026-01-05", p1="Alpha", p2="Beta", winner=2, score1=0, score2=1)
    e_future = make_settled(event_id="football:future", event_date="2026-01-10", p1="Alpha", p2="Beta", winner=1, score1=2, score2=0)

    ex_with_future, _ = build_price_free_examples([e_past, e_target, e_future])
    target_ex = [e for e in ex_with_future if e.event_id == "football:target"][0]

    # Only past meeting should count, not future
    assert target_ex.features["h2h_prior_games"] == 1.0


def test_prior_history_for_one_sport_cannot_enter_another_sport():
    football_prior = make_settled(event_id="football:prior", sport="football", event_date="2026-01-01", p1="Alpha", p2="Beta", winner=1, score1=2, score2=0)
    basketball_target = make_settled(event_id="basketball:target", sport="basketball", event_date="2026-01-02", p1="Alpha", p2="Beta", winner=2, prob1=0.6, prob2=0.4, draw_prob=None, score1=90, score2=100)

    examples, _ = build_price_free_examples([football_prior, basketball_target])
    target = [e for e in examples if e.event_id == "basketball:target"][0]

    # Football history must not affect basketball
    assert target.features["h2h_prior_games"] == 0.0
    assert target.features["underdog_prior_games"] == 0.0


# ---------------------------------------------------------------------------
# Integrity
# ---------------------------------------------------------------------------

def test_exact_duplicate_composite_key_does_not_create_duplicate():
    e1 = make_settled(event_id="football:dup", event_date="2026-01-01", winner=1)
    e2 = make_settled(event_id="football:dup", event_date="2026-01-01", winner=1)  # exact duplicate

    examples, receipt = build_price_free_examples([e1, e2])

    assert receipt.input_rows == 1  # deduped
    assert len(examples) == 1


def test_conflicting_composite_key_fails_loudly():
    e1 = make_settled(event_id="football:conflict", event_date="2026-01-01", winner=1, score1=1, score2=0)
    e2 = make_settled(event_id="football:conflict", event_date="2026-01-01", winner=2, score1=0, score2=1)  # conflicting winner

    with pytest.raises(ValueError, match="conflicting composite key"):
        build_price_free_examples([e1, e2])


def test_void_excluded():
    e_void = make_settled(winner=1, disposition="VOID")
    examples, receipt = build_price_free_examples([e_void])

    assert len(examples) == 0
    assert receipt.excluded_void == 1


def test_equal_probabilities_excluded():
    e_equal = make_settled(prob1=0.4, prob2=0.4, draw_prob=0.2)
    examples, receipt = build_price_free_examples([e_equal])

    assert len(examples) == 0
    assert receipt.excluded_equal_probability == 1


def test_missing_probabilities_excluded():
    e_missing = make_settled(prob1=None, prob2=0.4)
    examples, receipt = build_price_free_examples([e_missing])

    assert len(examples) == 0
    assert receipt.excluded_missing_probability == 1


def test_receipt_accounting_balances():
    events = [
        make_settled(event_id="football:1", winner=1, event_date="2026-01-01"),
        make_settled(event_id="football:2", winner=0, event_date="2026-01-02", score1=1, score2=1),  # draw -> eligible 0
        make_settled(event_id="basketball:1", sport="basketball", winner=0, event_date="2026-01-03", prob1=0.6, prob2=0.4, draw_prob=None, score1=100, score2=100),  # two-way draw excluded
        make_settled(event_id="football:3", winner=1, disposition="VOID", event_date="2026-01-04"),
        make_settled(event_id="football:4", prob1=0.5, prob2=0.5, event_date="2026-01-05"),
        make_settled(event_id="football:5", prob1=None, prob2=0.3, event_date="2026-01-06"),
    ]

    examples, receipt = build_price_free_examples(events)

    total_excluded = (
        receipt.excluded_void
        + receipt.excluded_source_conflict
        + receipt.excluded_equal_probability
        + receipt.excluded_missing_probability
        + receipt.excluded_non_finite_probability
        + receipt.excluded_out_of_range_probability
        + receipt.excluded_unknown_sport
        + receipt.excluded_unexpected_two_way_draw
        + receipt.excluded_invalid_winner
        + receipt.excluded_other
    )

    assert receipt.input_rows == receipt.eligible_examples + total_excluded


def test_unknown_sport_excluded():
    e_unknown = make_settled(sport="unknown_sport_xyz", event_id="unknown_sport_xyz:1")
    examples, receipt = build_price_free_examples([e_unknown])

    assert len(examples) == 0
    assert receipt.excluded_unknown_sport == 1


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def test_example_round_trip_stable():
    e = make_settled()
    examples, _ = build_price_free_examples([e])
    ex = examples[0]

    d = ex.to_dict()
    restored = PriceFreeUnderdogExample.from_dict(d)

    assert restored.event_id == ex.event_id
    assert restored.label == ex.label
    assert restored.features == ex.features
    assert restored.missingness == ex.missingness
    # Second round-trip
    d2 = restored.to_dict()
    assert d == d2


def test_receipt_round_trip_stable():
    events = [make_settled(event_id=f"football:{i}", event_date=f"2026-01-{i:02d}") for i in range(1, 5)]
    _, receipt = build_price_free_examples(events)

    d = receipt.to_dict()
    restored = PriceFreeDatasetReceipt.from_dict(d)

    assert restored.input_rows == receipt.input_rows
    assert restored.eligible_examples == receipt.eligible_examples
    assert restored.input_digest == receipt.input_digest
    assert restored.to_dict() == d


def test_feature_ordering_deterministic():
    e = make_settled()
    examples, _ = build_price_free_examples([e])
    ex = examples[0]

    d = ex.to_dict()
    feature_keys = list(d["features"].keys())
    assert feature_keys == sorted(feature_keys)
    missing_keys = list(d["missingness"].keys())
    assert missing_keys == sorted(missing_keys)


def test_no_prohibited_price_key_in_serialized_output():
    e = make_settled(odds1=2.5, odds2=1.5)
    examples, _ = build_price_free_examples([e])
    ex = examples[0]
    d = ex.to_dict()

    for prohibited in PROHIBITED_KEYS:
        assert prohibited not in d["features"]
        assert prohibited not in d
        # Also check not in top-level fields
        assert prohibited not in d or d.get(prohibited) is None


def test_allowed_features_only():
    e = make_settled()
    examples, _ = build_price_free_examples([e])
    ex = examples[0]

    for key in ex.features:
        assert key in ALLOWED_FEATURES, f"feature {key} not in allowed list"


def test_missingness_policy_genuine_zero_vs_missing():
    # No prior history -> prior games 0 with missing 0 (genuine zero)
    e = make_settled(event_id="football:target", event_date="2026-01-10", p1="Alpha", p2="Beta")
    examples, _ = build_price_free_examples([e])
    ex = examples[0]

    assert ex.features["underdog_prior_games"] == 0.0
    assert ex.missingness["underdog_prior_games"] == 0
    assert ex.features["h2h_prior_games"] == 0.0
    assert ex.missingness["h2h_prior_games"] == 0

    # Win rate with no games -> None with missing 1
    assert ex.features["underdog_prior_win_rate"] is None
    assert ex.missingness["underdog_prior_win_rate"] == 1


def test_feature_contract_and_label_versions():
    e = make_settled()
    examples, receipt = build_price_free_examples([e])

    assert examples[0].feature_contract_version == FEATURE_CONTRACT_VERSION
    assert examples[0].label_contract_version == LABEL_CONTRACT_VERSION
    assert receipt.feature_contract_version == FEATURE_CONTRACT_VERSION
    assert receipt.label_contract_version == LABEL_CONTRACT_VERSION
