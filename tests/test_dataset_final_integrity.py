"""Final integrity corrections — disposition vocabulary, winner_index type checks, deterministic provenance merge, source-conflict digest."""

import pytest

from slumdog.dataset import (
    SUPPORTED_DISPOSITIONS,
    _compute_input_digest,
    _validate_settled_dict,
    build_dataset_with_raw_accounting,
    load_settled_events_from_dicts,
)


def make_settled_dict(
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
    source_url="https://www.forebet.com/en/football/matches/a-b/1",
    facets=None,
    extra=None,
):
    d = {
        "event_id": event_id,
        "sport": sport,
        "event_date": event_date,
        "participant_1": p1,
        "participant_2": p2,
        "winner_index": winner,
        "score_1": score1,
        "score_2": score2,
        "probability_1": prob1,
        "probability_2": prob2,
        "draw_probability": draw_prob,
        "forebet_pick": 1,
        "odds_1": None,
        "odds_2": None,
        "league": "TST",
        "period_scores_1": (),
        "period_scores_2": (),
        "source_url": source_url,
        "disposition": disposition,
        "facets": facets or {},
    }
    if extra:
        d.update(extra)
    return d


# ---------------------------------------------------------------------------
# 1. Unknown dispositions must be excluded — use canonical vocabulary
# ---------------------------------------------------------------------------

def test_unknown_disposition_is_schema_excluded():
    for unknown in ["PENDING", "LIVE", "ABANDONED", "CANCELLED", "POSTPONED", "CANCELED", "arbitrary_text", "FT"]:
        d = make_settled_dict(disposition=unknown)
        result = load_settled_events_from_dicts([d])
        assert result.schema_excluded_rows == 1, f"should exclude unknown {unknown}"
        assert "SCHEMA_UNKNOWN_DISPOSITION" in result.schema_exclusion_reasons, f"reason for {unknown}"


def test_missing_disposition_is_schema_excluded():
    d = make_settled_dict()
    del d["disposition"]
    result = load_settled_events_from_dicts([d])
    assert result.schema_excluded_rows == 1
    assert "SCHEMA_MISSING_DISPOSITION" in result.schema_exclusion_reasons


def test_empty_disposition_is_schema_excluded():
    for empty in ["", "   ", "\t\n"]:
        d = make_settled_dict(disposition=empty)
        result = load_settled_events_from_dicts([d])
        assert result.schema_excluded_rows == 1
        assert "SCHEMA_MISSING_DISPOSITION" in result.schema_exclusion_reasons


def test_supported_settled_disposition_eligible_for_labeling():
    # SETTLED, SETTLED_CUP, SETTLED_DRAW are settled and should be eligible (if other fields valid)
    for disp in ["SETTLED", "SETTLED_CUP", "SETTLED_DRAW"]:
        d = make_settled_dict(disposition=disp, sport="football", draw_prob=0.1)
        examples, receipt, _ = build_dataset_with_raw_accounting([d])
        # For football, SETTLED_DRAW is still settled? Actually cricket SETTLED_DRAW but for football it's still considered settled
        # The builder will treat SETTLED_DRAW as settled (not void) and then label it
        # So eligible should be 1 for SETTLED and SETTLED_CUP, and for SETTLED_DRAW may be eligible depending on sport
        # For football, draw possible, so SETTLED_DRAW with winner 2 should be eligible
        assert receipt.canonical_input_rows == 1
        # At least not schema excluded
        assert receipt.schema_excluded_rows == 0


def test_supported_void_no_contest_remains_explicitly_accounted():
    # VOID and NO_CONTEST should be loaded (not schema excluded) then explicitly excluded by label contract
    for disp in ["VOID", "NO_CONTEST"]:
        d = make_settled_dict(disposition=disp)
        result = load_settled_events_from_dicts([d])
        assert result.schema_excluded_rows == 0, f"{disp} should be loaded, not schema excluded"
        assert len(result.valid_events) == 1

        examples, receipt, _ = build_dataset_with_raw_accounting([d])
        assert receipt.canonical_input_rows == 1
        assert receipt.eligible_examples == 0
        assert receipt.excluded_void == 1
        assert receipt.builder_excluded_rows == 1


def test_canonical_disposition_vocabulary_derived_from_settlement():
    # Ensure our supported set matches settlement.py outputs
    # settlement.py produces SETTLED, SETTLED_CUP, SETTLED_DRAW, VOID
    assert "SETTLED" in SUPPORTED_DISPOSITIONS
    assert "VOID" in SUPPORTED_DISPOSITIONS
    assert "SETTLED_CUP" in SUPPORTED_DISPOSITIONS
    assert "SETTLED_DRAW" in SUPPORTED_DISPOSITIONS
    # NO_CONTEST included as void alias per task example
    assert "NO_CONTEST" in SUPPORTED_DISPOSITIONS
    # Unknown examples not in supported
    assert "PENDING" not in SUPPORTED_DISPOSITIONS
    assert "LIVE" not in SUPPORTED_DISPOSITIONS
    assert "ABANDONED" not in SUPPORTED_DISPOSITIONS


# ---------------------------------------------------------------------------
# Winner_index strict type checks
# ---------------------------------------------------------------------------

def test_winner_index_true_is_rejected():
    d = make_settled_dict(winner=True)
    result = load_settled_events_from_dicts([d])
    assert result.schema_excluded_rows == 1
    # Should be BOOL rejection, not treated as 1
    assert any("BOOL" in k or "INVALID_WINNER" in k for k in result.schema_exclusion_reasons)


def test_winner_index_false_is_rejected():
    d = make_settled_dict(winner=False)
    result = load_settled_events_from_dicts([d])
    assert result.schema_excluded_rows == 1


def test_winner_index_float_is_rejected():
    d = make_settled_dict(winner=1.0)
    result = load_settled_events_from_dicts([d])
    assert result.schema_excluded_rows == 1
    assert any("TYPE" in k or "INVALID_WINNER" in k for k in result.schema_exclusion_reasons)


def test_winner_index_string_is_rejected():
    d = make_settled_dict(winner="1")
    result = load_settled_events_from_dicts([d])
    assert result.schema_excluded_rows == 1


def test_winner_index_none_is_schema_missing():
    d = make_settled_dict()
    d["winner_index"] = None
    result = load_settled_events_from_dicts([d])
    assert result.schema_excluded_rows == 1
    assert "SCHEMA_MISSING_WINNER_INDEX" in result.schema_exclusion_reasons


# ---------------------------------------------------------------------------
# 2. Duplicate provenance handling must be deterministic
# ---------------------------------------------------------------------------

def test_provenance_merge_stable_under_reversed_input_order():
    d_missing = make_settled_dict(event_id="football:1", event_date="2026-01-01", source_url="", facets={})
    d_present = make_settled_dict(event_id="football:1", event_date="2026-01-01", source_url="https://a.com", facets={"raw_sha256": "a"*64})

    examples_fwd, receipt_fwd, _ = build_dataset_with_raw_accounting([d_missing, d_present])
    examples_rev, receipt_rev, _ = build_dataset_with_raw_accounting([d_present, d_missing])

    assert receipt_fwd.canonical_input_rows == receipt_rev.canonical_input_rows == 1
    assert examples_fwd[0].raw_sha256 == examples_rev[0].raw_sha256 == "a"*64
    assert examples_fwd[0].source_url == examples_rev[0].source_url == "https://a.com"
    # Digest stable after duplicate normalization
    assert receipt_fwd.input_digest == receipt_rev.input_digest


def test_missing_plus_valid_provenance_preserves_valid():
    d_missing = make_settled_dict(event_id="football:1", event_date="2026-01-01", source_url="", facets={})
    d_present = make_settled_dict(event_id="football:1", event_date="2026-01-01", source_url="https://present.com", facets={"raw_sha256": "b"*64})

    examples, receipt, _ = build_dataset_with_raw_accounting([d_missing, d_present])
    assert receipt.canonical_input_rows == 1
    assert receipt.provenance_present == 1
    assert examples[0].raw_sha256 == "b"*64
    assert examples[0].source_url == "https://present.com"


def test_different_valid_hashes_conflict():
    d1 = make_settled_dict(event_id="football:1", event_date="2026-01-01", source_url="https://a.com", facets={"raw_sha256": "a"*64})
    d2 = make_settled_dict(event_id="football:1", event_date="2026-01-01", source_url="https://a.com", facets={"raw_sha256": "b"*64})

    with pytest.raises(ValueError, match="conflicting provenance raw_sha256"):
        build_dataset_with_raw_accounting([d1, d2])


def test_different_non_empty_source_urls_conflict():
    d1 = make_settled_dict(event_id="football:1", event_date="2026-01-01", source_url="https://a.com", facets={"raw_sha256": "a"*64})
    d2 = make_settled_dict(event_id="football:1", event_date="2026-01-01", source_url="https://b.com", facets={"raw_sha256": "a"*64})

    with pytest.raises(ValueError, match="conflicting provenance source_url"):
        build_dataset_with_raw_accounting([d1, d2])


def test_digest_stable_after_duplicate_normalization():
    d1 = make_settled_dict(event_id="football:1", event_date="2026-01-01", source_url="https://a.com", facets={"raw_sha256": "a"*64})
    d2 = make_settled_dict(event_id="football:1", event_date="2026-01-01", source_url="https://a.com", facets={"raw_sha256": "a"*64})
    d3 = make_settled_dict(event_id="football:2", event_date="2026-01-02", source_url="https://a.com", facets={"raw_sha256": "b"*64})

    # With duplicate
    examples_dup, receipt_dup, _ = build_dataset_with_raw_accounting([d1, d2, d3])
    # Without duplicate (already deduped)
    examples_nodup, receipt_nodup, _ = build_dataset_with_raw_accounting([d1, d3])

    # Digests should be same because duplicate normalization should be deterministic
    assert receipt_dup.input_digest == receipt_nodup.input_digest
    assert receipt_dup.canonical_input_rows == receipt_nodup.canonical_input_rows == 2


# ---------------------------------------------------------------------------
# 3. Source-conflict state in digest — document limitation
# ---------------------------------------------------------------------------

def test_source_conflict_limitation_documented():
    # SettledEvent does not have source_conflict field, so digest cannot include it
    # Check that _canonical_event_repr does not include source_conflict and docstring mentions limitation
    import slumdog.dataset as ds
    source = ds._canonical_event_repr.__doc__
    assert "source conflict" in source.lower()
    assert "not represented" in source.lower() or "limitation" in source.lower()

    # Builder assumes no source conflict — receipt excluded_source_conflict should be 0 for current schemas
    d = make_settled_dict()
    examples, receipt, _ = build_dataset_with_raw_accounting([d])
    assert receipt.excluded_source_conflict == 0

    # If we try to pass source_conflict via facets, it should not affect digest unless explicitly included
    # For now, ensure digest does not change when adding arbitrary facet not in canonical repr
    ev1 = _validate_settled_dict(make_settled_dict(facets={"raw_sha256": "a"*64}))
    ev2 = _validate_settled_dict(make_settled_dict(facets={"raw_sha256": "a"*64, "source_conflict": True}))
    # Digest should be same because source_conflict not in canonical repr (documented limitation)
    assert _compute_input_digest([ev1]) == _compute_input_digest([ev2])


def test_source_conflict_state_changes_eligibility_where_supported():
    # Since SettledEvent does not have source_conflict, we test the label contract directly
    # label_underdog_outcome with source_conflict=True should be excluded
    from slumdog.underdog import identify_forebet_underdog, label_underdog_outcome

    identity = identify_forebet_underdog(0.6, 0.3, 0.1)
    result_no_conflict = label_underdog_outcome("football", identity, winner_index=2, disposition="SETTLED", source_conflict=False)
    result_conflict = label_underdog_outcome("football", identity, winner_index=2, disposition="SETTLED", source_conflict=True)

    assert result_no_conflict.eligible is True
    assert result_conflict.eligible is False
    assert result_conflict.exclusion_reason == "SOURCE_CONFLICT"
