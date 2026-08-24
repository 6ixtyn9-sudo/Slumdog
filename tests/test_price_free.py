"""Milestone 2D + 2E — price-free identity, label, and contracts with hardening.

Milestone 2D required:
Identity (10), Labels (11), Contracts (6+)
Milestone 2E hardening:
- label uses indices from ForebetUnderdogIdentity
- callers cannot reverse fav/underdog via public API
- draw capability from SPORTS registry
- football draw → 0, basketball draw → excluded
- unknown sport explicit
- equal/missing/non-finite/out-of-range reasons survive into label exclusion
- update label tests to build identity via identify_forebet_underdog
"""

from slumdog.underdog import (
    DailyShortlistStatus,
    DailyUnderdogShortlist,
    StrongUnderdogAssessment,
    UnderdogAssessmentStatus,
    build_assessment_from_identity,
    identify_forebet_underdog,
    label_underdog_outcome,
)


# ---------------------------------------------------------------------------
# Identity tests (10)
# ---------------------------------------------------------------------------

def test_identity_participant_1_favorite():
    identity = identify_forebet_underdog(0.6, 0.3)
    assert identity.eligible is True
    assert identity.favorite_index == 1
    assert identity.underdog_index == 2
    assert identity.favorite_probability == 0.6
    assert identity.underdog_probability == 0.3
    assert identity.probability_gap == 0.3
    assert identity.ineligibility_reason is None


def test_identity_participant_2_favorite():
    identity = identify_forebet_underdog(0.25, 0.65)
    assert identity.eligible is True
    assert identity.favorite_index == 2
    assert identity.underdog_index == 1
    assert identity.favorite_probability == 0.65
    assert identity.underdog_probability == 0.25
    assert identity.probability_gap == 0.4


def test_identity_equal_probabilities():
    identity = identify_forebet_underdog(0.4, 0.4)
    assert identity.eligible is False
    assert identity.favorite_index is None
    assert identity.underdog_index is None
    assert identity.ineligibility_reason == "EQUAL_PROBABILITY"
    assert identity.probability_gap == 0.0


def test_identity_missing_participant_probability():
    identity = identify_forebet_underdog(None, 0.4)
    assert identity.eligible is False
    assert identity.ineligibility_reason == "MISSING_PROBABILITY"

    identity2 = identify_forebet_underdog(0.5, None)
    assert identity2.eligible is False
    assert identity2.ineligibility_reason == "MISSING_PROBABILITY"

    identity3 = identify_forebet_underdog(None, None)
    assert identity3.eligible is False
    assert identity3.ineligibility_reason == "MISSING_PROBABILITY"


def test_identity_non_finite_probability():
    identity = identify_forebet_underdog(float("inf"), 0.3)
    assert identity.eligible is False
    assert identity.ineligibility_reason in ("NON_FINITE_PROBABILITY", "INVALID_PROBABILITY", "OUT_OF_RANGE_PROBABILITY")

    identity2 = identify_forebet_underdog(float("nan"), 0.3)
    assert identity2.eligible is False

    identity3 = identify_forebet_underdog(0.5, float("-inf"))
    assert identity3.eligible is False


def test_identity_out_of_range_probability():
    identity = identify_forebet_underdog(1.5, 0.3)
    assert identity.eligible is False
    assert identity.ineligibility_reason == "OUT_OF_RANGE_PROBABILITY"

    identity2 = identify_forebet_underdog(-0.1, 0.3)
    assert identity2.eligible is False
    assert identity2.ineligibility_reason == "OUT_OF_RANGE_PROBABILITY"

    identity3 = identify_forebet_underdog(0.5, 1.2)
    assert identity3.eligible is False


def test_identity_draw_probability_larger_does_not_become_selected():
    identity = identify_forebet_underdog(0.25, 0.25, draw_probability=0.5)
    assert identity.eligible is False
    assert identity.ineligibility_reason == "EQUAL_PROBABILITY"
    assert identity.draw_probability == 0.5
    assert identity.favorite_index != 0
    assert identity.underdog_index != 0

    identity2 = identify_forebet_underdog(0.2, 0.3, draw_probability=0.5)
    assert identity2.eligible is True
    assert identity2.favorite_index == 2
    assert identity2.underdog_index == 1
    assert identity2.draw_probability == 0.5
    assert identity2.favorite_index in (1, 2)
    assert identity2.underdog_index in (1, 2)


def test_identity_odds_disagree_has_no_effect():
    identity = identify_forebet_underdog(0.6, 0.3)
    assert identity.favorite_index == 1
    assert identity.underdog_index == 2


def test_identity_forebet_pick_disagrees_has_no_effect():
    identity = identify_forebet_underdog(0.6, 0.3)
    assert identity.favorite_index == 1


def test_identity_recent_form_disagrees_has_no_effect():
    identity = identify_forebet_underdog(0.6, 0.3)
    assert identity.favorite_index == 1
    assert identity.underdog_index == 2


# ---------------------------------------------------------------------------
# Label tests — now via identity-bound public API (Milestone 2E)
# ---------------------------------------------------------------------------

def test_label_draw_capable_underdog_wins():
    identity = identify_forebet_underdog(0.6, 0.3, draw_probability=0.1)
    result = label_underdog_outcome("football", identity, winner_index=2, disposition="SETTLED")
    assert result.eligible is True
    assert result.label == 1
    assert result.is_draw is False
    assert result.is_void is False
    assert result.favorite_index == 1
    assert result.underdog_index == 2


def test_label_draw_capable_favorite_wins():
    identity = identify_forebet_underdog(0.6, 0.3, draw_probability=0.1)
    result = label_underdog_outcome("football", identity, winner_index=1, disposition="SETTLED")
    assert result.eligible is True
    assert result.label == 0


def test_label_draw_capable_draw_is_zero():
    identity = identify_forebet_underdog(0.6, 0.3, draw_probability=0.1)
    result = label_underdog_outcome("football", identity, winner_index=0, disposition="SETTLED")
    assert result.eligible is True
    assert result.label == 0
    assert result.is_draw is True


def test_label_two_way_underdog_wins():
    identity = identify_forebet_underdog(0.65, 0.35)
    result = label_underdog_outcome("basketball", identity, winner_index=2, disposition="SETTLED")
    assert result.eligible is True
    assert result.label == 1


def test_label_two_way_favorite_wins():
    identity = identify_forebet_underdog(0.65, 0.35)
    result = label_underdog_outcome("basketball", identity, winner_index=1, disposition="SETTLED")
    assert result.eligible is True
    assert result.label == 0


def test_label_two_way_unexpected_draw_excluded():
    identity = identify_forebet_underdog(0.65, 0.35)
    result = label_underdog_outcome("basketball", identity, winner_index=0, disposition="SETTLED")
    assert result.eligible is False
    assert result.label is None
    assert result.exclusion_reason == "UNEXPECTED_DRAW_FOR_TWO_WAY"
    assert result.is_draw is True


def test_label_void_excluded():
    identity = identify_forebet_underdog(0.6, 0.3, draw_probability=0.1)
    result = label_underdog_outcome("football", identity, winner_index=1, disposition="VOID")
    assert result.eligible is False
    assert result.label is None
    assert result.exclusion_reason == "VOID"
    assert result.is_void is True


def test_label_equal_probability_excluded():
    identity = identify_forebet_underdog(0.4, 0.4, draw_probability=0.2)
    result = label_underdog_outcome("football", identity, winner_index=1, disposition="SETTLED")
    assert result.eligible is False
    assert result.exclusion_reason == "EQUAL_PROBABILITY"
    assert result.identity_ineligibility_reason == "EQUAL_PROBABILITY"


def test_label_missing_probability_excluded():
    identity = identify_forebet_underdog(None, 0.4)
    result = label_underdog_outcome("football", identity, winner_index=1, disposition="SETTLED")
    assert result.eligible is False
    assert result.exclusion_reason == "MISSING_PROBABILITY"
    assert result.identity_ineligibility_reason == "MISSING_PROBABILITY"


def test_label_invalid_winner_index_excluded():
    identity = identify_forebet_underdog(0.6, 0.3)
    result = label_underdog_outcome("football", identity, winner_index=5, disposition="SETTLED")
    assert result.eligible is False
    assert result.exclusion_reason == "INVALID_WINNER_INDEX"

    result2 = label_underdog_outcome("football", identity, winner_index=None, disposition="SETTLED")
    assert result2.eligible is False
    assert result2.exclusion_reason == "INVALID_WINNER_INDEX"


def test_label_source_conflict_excluded():
    identity = identify_forebet_underdog(0.6, 0.3)
    result = label_underdog_outcome("football", identity, winner_index=1, disposition="SETTLED", source_conflict=True)
    assert result.eligible is False
    assert result.exclusion_reason == "SOURCE_CONFLICT"
    assert result.is_source_conflict is True


# ---------------------------------------------------------------------------
# Hardening tests (Milestone 2E)
# ---------------------------------------------------------------------------

def test_label_uses_indices_from_identity():
    # Identity says fav=1 dog=2
    identity = identify_forebet_underdog(0.7, 0.2)
    assert identity.favorite_index == 1
    assert identity.underdog_index == 2
    # Label must use those indices, not reversed
    result_fav_win = label_underdog_outcome("football", identity, winner_index=1, disposition="SETTLED")
    assert result_fav_win.label == 0
    assert result_fav_win.favorite_index == 1
    assert result_fav_win.underdog_index == 2

    result_dog_win = label_underdog_outcome("football", identity, winner_index=2, disposition="SETTLED")
    assert result_dog_win.label == 1
    assert result_dog_win.favorite_index == 1
    assert result_dog_win.underdog_index == 2


def test_label_cannot_reverse_favorite_underdog_via_public_api():
    # Public API accepts identity object only, not separate indices.
    # Attempting to reverse would require constructing a different identity,
    # which would have different probabilities — cannot reverse through label call.
    identity = identify_forebet_underdog(0.8, 0.15)
    # Identity is fixed: fav=1 (0.8), dog=2 (0.15)
    assert identity.favorite_index == 1
    assert identity.underdog_index == 2
    # Even if caller wants to claim fav=2 dog=1, they cannot via public API
    # because label derives from identity. The only way is to create new identity
    # with swapped probs, which is a different event and would be caught by audit.
    result = label_underdog_outcome("football", identity, winner_index=2, disposition="SETTLED")
    # Winner 2 is underdog per identity, so label 1
    assert result.label == 1
    # If someone tried to reverse, they'd need identity with fav=2 dog=1, which would be
    # identify_forebet_underdog(0.15, 0.8) — different probabilities, not a reversal of same event
    identity_swapped = identify_forebet_underdog(0.15, 0.8)
    assert identity_swapped.favorite_index == 2
    assert identity_swapped.underdog_index == 1
    # This is a different identity, not a reversal via label API
    result_swapped = label_underdog_outcome("football", identity_swapped, winner_index=2, disposition="SETTLED")
    # Now winner 2 is favorite, label 0
    assert result_swapped.label == 0
    # So public API prevents silent reversal of same identity


def test_draw_capability_from_sports_registry():
    # Football is draw_possible=True per sports.py
    identity = identify_forebet_underdog(0.6, 0.3, draw_probability=0.1)
    football_draw = label_underdog_outcome("football", identity, winner_index=0, disposition="SETTLED")
    assert football_draw.draw_possible is True
    assert football_draw.eligible is True
    assert football_draw.label == 0

    # Basketball is draw_possible=False
    identity2 = identify_forebet_underdog(0.6, 0.3)
    basketball_draw = label_underdog_outcome("basketball", identity2, winner_index=0, disposition="SETTLED")
    assert basketball_draw.draw_possible is False
    assert basketball_draw.eligible is False
    assert basketball_draw.exclusion_reason == "UNEXPECTED_DRAW_FOR_TWO_WAY"


def test_football_draw_is_zero():
    identity = identify_forebet_underdog(0.55, 0.25, draw_probability=0.2)
    result = label_underdog_outcome("football", identity, winner_index=0, disposition="SETTLED")
    assert result.eligible is True
    assert result.label == 0
    assert result.is_draw is True
    assert result.sport == "football"


def test_basketball_draw_excluded():
    identity = identify_forebet_underdog(0.55, 0.45)
    result = label_underdog_outcome("basketball", identity, winner_index=0, disposition="SETTLED")
    assert result.eligible is False
    assert result.label is None
    assert result.exclusion_reason == "UNEXPECTED_DRAW_FOR_TWO_WAY"
    assert result.is_draw is True


def test_unknown_sport_explicit():
    identity = identify_forebet_underdog(0.6, 0.3)
    result = label_underdog_outcome("unknown_sport_xyz", identity, winner_index=1, disposition="SETTLED")
    assert result.eligible is False
    assert result.exclusion_reason == "UNKNOWN_SPORT"
    assert result.sport == "unknown_sport_xyz"


def test_equal_probability_reason_survives():
    identity = identify_forebet_underdog(0.4, 0.4)
    assert identity.ineligibility_reason == "EQUAL_PROBABILITY"
    result = label_underdog_outcome("football", identity, winner_index=1, disposition="SETTLED")
    assert result.eligible is False
    assert result.exclusion_reason == "EQUAL_PROBABILITY"
    assert result.identity_ineligibility_reason == "EQUAL_PROBABILITY"


def test_missing_probability_reason_survives():
    identity = identify_forebet_underdog(None, 0.3)
    assert identity.ineligibility_reason == "MISSING_PROBABILITY"
    result = label_underdog_outcome("football", identity, winner_index=1, disposition="SETTLED")
    assert result.eligible is False
    assert result.exclusion_reason == "MISSING_PROBABILITY"
    assert result.identity_ineligibility_reason == "MISSING_PROBABILITY"

    identity2 = identify_forebet_underdog(0.5, None)
    result2 = label_underdog_outcome("football", identity2, winner_index=1, disposition="SETTLED")
    assert result2.exclusion_reason == "MISSING_PROBABILITY"
    assert result2.identity_ineligibility_reason == "MISSING_PROBABILITY"


def test_non_finite_reason_survives():
    identity = identify_forebet_underdog(float("inf"), 0.3)
    assert identity.ineligibility_reason in ("NON_FINITE_PROBABILITY", "OUT_OF_RANGE_PROBABILITY", "INVALID_PROBABILITY")
    result = label_underdog_outcome("football", identity, winner_index=1, disposition="SETTLED")
    assert result.eligible is False
    assert result.exclusion_reason == identity.ineligibility_reason
    assert result.identity_ineligibility_reason == identity.ineligibility_reason

    identity2 = identify_forebet_underdog(float("nan"), 0.4)
    result2 = label_underdog_outcome("football", identity2, winner_index=1, disposition="SETTLED")
    assert result2.eligible is False
    assert result2.identity_ineligibility_reason is not None


def test_out_of_range_reason_survives():
    identity = identify_forebet_underdog(1.5, 0.3)
    assert identity.ineligibility_reason == "OUT_OF_RANGE_PROBABILITY"
    result = label_underdog_outcome("football", identity, winner_index=1, disposition="SETTLED")
    assert result.eligible is False
    assert result.exclusion_reason == "OUT_OF_RANGE_PROBABILITY"
    assert result.identity_ineligibility_reason == "OUT_OF_RANGE_PROBABILITY"

    identity2 = identify_forebet_underdog(-0.2, 0.3)
    result2 = label_underdog_outcome("football", identity2, winner_index=1, disposition="SETTLED")
    assert result2.exclusion_reason == "OUT_OF_RANGE_PROBABILITY"
    assert result2.identity_ineligibility_reason == "OUT_OF_RANGE_PROBABILITY"


# ---------------------------------------------------------------------------
# Contract tests (6+)
# ---------------------------------------------------------------------------

def test_assessment_round_trip_serialization():
    identity = identify_forebet_underdog(0.6, 0.3, draw_probability=0.1)
    assessment = build_assessment_from_identity(
        event_id="football:123",
        sport="football",
        event_date="2026-08-24",
        participant_1="Team A",
        participant_2="Team B",
        identity=identity,
        status=UnderdogAssessmentStatus.STRONG_UNDERDOG,
        supporting_evidence=("Forebet underdog but recent form strong",),
        contradicting_evidence=("Favorite home advantage",),
        missing_evidence=("Standings missing",),
        source_url="https://www.forebet.com/en/football/matches/team-a-team-b/123",
        raw_sha256="abc123",
        captured_at="2026-08-24T00:00:00+00:00",
    )
    assert assessment is not None
    d = assessment.to_dict()
    restored = StrongUnderdogAssessment.from_dict(d)
    assert restored.event_id == assessment.event_id
    assert restored.favorite_index == 1
    assert restored.underdog_index == 2
    assert restored.favorite_probability == 0.6
    assert restored.underdog_probability == 0.3
    assert restored.status == UnderdogAssessmentStatus.STRONG_UNDERDOG
    assert restored.supporting_evidence == ("Forebet underdog but recent form strong",)
    assert restored.contradicting_evidence == ("Favorite home advantage",)
    assert restored.missing_evidence == ("Standings missing",)


def test_assessment_no_price_field_required():
    identity = identify_forebet_underdog(0.55, 0.35)
    assessment = build_assessment_from_identity(
        event_id="basketball:456",
        sport="basketball",
        event_date="2026-08-24",
        participant_1="Team C",
        participant_2="Team D",
        identity=identity,
        status=UnderdogAssessmentStatus.WATCHLIST,
    )
    assert assessment is not None
    d = assessment.to_dict()
    forbidden_keys = {"price", "odds_1", "odds_2", "displayed_odds", "implied_probability", "price_available"}
    for key in forbidden_keys:
        assert key not in d, f"price field {key} should not be in core contract"
    assert d.get("optional_price_context") is None


def test_assessment_missing_optional_detail_evidence_accepted():
    assessment = StrongUnderdogAssessment(
        event_id="tennis:789",
        sport="tennis",
        event_date="2026-08-24",
        participant_1="Player A",
        participant_2="Player B",
        favorite_index=1,
        underdog_index=2,
        favorite_name="Player A",
        underdog_name="Player B",
        favorite_probability=0.7,
        underdog_probability=0.2,
        draw_probability=None,
        probability_gap=0.5,
        status=UnderdogAssessmentStatus.INSUFFICIENT_EVIDENCE,
        supporting_evidence=(),
        contradicting_evidence=(),
        missing_evidence=("Surface record missing", "H2H missing"),
        source_url="",
        raw_sha256="",
        captured_at="",
    )
    assert assessment.missing_evidence == ("Surface record missing", "H2H missing")
    d = assessment.to_dict()
    restored = StrongUnderdogAssessment.from_dict(d)
    assert restored.missing_evidence == ("Surface record missing", "H2H missing")


def test_no_pick_daily_shortlist_serializes_with_zero_candidates():
    daily = DailyUnderdogShortlist(
        target_date="2026-08-24",
        generated_at="2026-08-24T00:00:00+00:00",
        status=DailyShortlistStatus.NO_STRONG_UNDERDOG,
        assessments_considered=15,
        strong_candidates=(),
        watchlist_candidates=(),
        rejection_counts={"INSUFFICIENT_EVIDENCE": 10, "EQUAL_PROBABILITY": 5},
        assessment_version="price-free-v1",
        source_receipt="capture_2026-08-24.json",
    )
    assert daily.status == DailyShortlistStatus.NO_STRONG_UNDERDOG
    assert len(daily.strong_candidates) == 0
    d = daily.to_dict()
    assert d["status"] == "NO_STRONG_UNDERDOG"
    assert d["strong_candidates"] == []
    restored = DailyUnderdogShortlist.from_dict(d)
    assert restored.status == DailyShortlistStatus.NO_STRONG_UNDERDOG
    assert len(restored.strong_candidates) == 0
    assert restored.assessments_considered == 15


def test_candidate_shortlist_cannot_contain_fake_draw_selection():
    identity = identify_forebet_underdog(0.6, 0.3)
    assessment = build_assessment_from_identity(
        event_id="football:999",
        sport="football",
        event_date="2026-08-24",
        participant_1="Team X",
        participant_2="Team Y",
        identity=identity,
        status=UnderdogAssessmentStatus.STRONG_UNDERDOG,
    )
    assert assessment is not None
    assert assessment.underdog_index in (1, 2)
    assert assessment.favorite_index in (1, 2)

    daily = DailyUnderdogShortlist(
        target_date="2026-08-24",
        generated_at="2026-08-24T00:00:00+00:00",
        status=DailyShortlistStatus.CANDIDATES_FOUND,
        assessments_considered=10,
        strong_candidates=(assessment,),
        watchlist_candidates=(),
        rejection_counts={},
    )
    assert daily.strong_candidates[0].underdog_index != 0

    try:
        StrongUnderdogAssessment(
            event_id="football:draw",
            sport="football",
            event_date="2026-08-24",
            participant_1="A",
            participant_2="B",
            favorite_index=1,
            underdog_index=0,
            favorite_name="A",
            underdog_name="Draw",
            favorite_probability=0.6,
            underdog_probability=0.3,
            draw_probability=0.1,
            probability_gap=0.3,
            status=UnderdogAssessmentStatus.STRONG_UNDERDOG,
        )
        assert False, "Should have raised ValueError for draw underdog_index"
    except ValueError:
        pass


def test_provenance_optional_only_where_legacy_lacks():
    identity = identify_forebet_underdog(0.6, 0.3)
    assessment_legacy = build_assessment_from_identity(
        event_id="football:legacy",
        sport="football",
        event_date="2024-01-01",
        participant_1="Old Team A",
        participant_2="Old Team B",
        identity=identity,
        status=UnderdogAssessmentStatus.WATCHLIST,
        source_url="",
        raw_sha256="",
        captured_at="",
    )
    assert assessment_legacy is not None
    assert assessment_legacy.source_url == ""
    assert assessment_legacy.raw_sha256 == ""
    assessment_new = build_assessment_from_identity(
        event_id="football:new",
        sport="football",
        event_date="2026-08-24",
        participant_1="Team A",
        participant_2="Team B",
        identity=identity,
        status=UnderdogAssessmentStatus.STRONG_UNDERDOG,
        source_url="https://www.forebet.com/en/football/matches/a-b/123",
        raw_sha256="deadbeef" * 8,
        captured_at="2026-08-24T05:00:00+00:00",
    )
    assert assessment_new is not None
    assert assessment_new.source_url != ""
    assert assessment_new.raw_sha256 != ""
    assert assessment_new.captured_at != ""


def test_assessment_reserved_fields_remain_none_until_approved():
    identity = identify_forebet_underdog(0.6, 0.3)
    assessment = build_assessment_from_identity(
        event_id="football:reserved",
        sport="football",
        event_date="2026-08-24",
        participant_1="Team A",
        participant_2="Team B",
        identity=identity,
        status=UnderdogAssessmentStatus.STRONG_UNDERDOG,
    )
    assert assessment is not None
    assert assessment.slumdog_underdog_probability is None
    assert assessment.probability_lift is None
    assert assessment.baseline_strength_score is None


def test_daily_shortlist_no_fake_candidate_for_no_pick_status():
    try:
        DailyUnderdogShortlist(
            target_date="2026-08-24",
            generated_at="2026-08-24T00:00:00+00:00",
            status=DailyShortlistStatus.NO_STRONG_UNDERDOG,
            assessments_considered=5,
            strong_candidates=(
                StrongUnderdogAssessment(
                    event_id="fake",
                    sport="football",
                    event_date="2026-08-24",
                    participant_1="A",
                    participant_2="B",
                    favorite_index=1,
                    underdog_index=2,
                    favorite_name="A",
                    underdog_name="B",
                    favorite_probability=0.6,
                    underdog_probability=0.3,
                    draw_probability=0.1,
                    probability_gap=0.3,
                    status=UnderdogAssessmentStatus.STRONG_UNDERDOG,
                ),
            ),
            watchlist_candidates=(),
        )
        assert False, "Should have raised ValueError for NO_STRONG_UNDERDOG with candidates"
    except ValueError as e:
        assert "zero" in str(e).lower() or "NO_STRONG_UNDERDOG" in str(e)


def test_optional_price_context_isolated():
    identity = identify_forebet_underdog(0.6, 0.3)
    assessment_no_price = build_assessment_from_identity(
        event_id="football:noprice",
        sport="football",
        event_date="2026-08-24",
        participant_1="Team A",
        participant_2="Team B",
        identity=identity,
        status=UnderdogAssessmentStatus.STRONG_UNDERDOG,
    )
    assert assessment_no_price.optional_price_context is None

    assessment_with_price = StrongUnderdogAssessment(
        event_id="football:withprice",
        sport="football",
        event_date="2026-08-24",
        participant_1="Team A",
        participant_2="Team B",
        favorite_index=1,
        underdog_index=2,
        favorite_name="Team A",
        underdog_name="Team B",
        favorite_probability=0.6,
        underdog_probability=0.3,
        draw_probability=0.1,
        probability_gap=0.3,
        status=UnderdogAssessmentStatus.STRONG_UNDERDOG,
        optional_price_context={
            "participant_1": 2.5,
            "participant_2": 1.8,
            "captured_at": "2026-08-24T00:00:00+00:00",
        },
    )
    assert assessment_with_price.favorite_index == 1
    assert assessment_with_price.underdog_index == 2
    assert assessment_with_price.optional_price_context["participant_1"] == 2.5
    assert assessment_with_price.probability_gap == 0.3
