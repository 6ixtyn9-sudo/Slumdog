"""Milestone 4E hardening tests — no unsafe defaults, raw/canonical accounting, digest, provenance, duplicate identity, adapter."""

import tempfile
from pathlib import Path

import pytest

from slumdog.dataset import (
    _compute_input_digest,
    _validate_settled_dict,
    build_dataset_with_raw_accounting,
    load_settled_events_from_dicts,
)
from slumdog.dataset_audit import _load_json_file, _load_jsonl_gz_file


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
    odds1=None,
    odds2=None,
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
        "odds_1": odds1,
        "odds_2": odds2,
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
# 1. Remove fabricated outcome defaults
# ---------------------------------------------------------------------------

def test_missing_winner_is_excluded_never_participant_1():
    d = make_settled_dict()
    del d["winner_index"]
    result = load_settled_events_from_dicts([d])
    assert result.schema_excluded_rows == 1
    assert "SCHEMA_MISSING_WINNER_INDEX" in result.schema_exclusion_reasons
    # Builder should not see it as participant 1 win
    examples, receipt, _ = build_dataset_with_raw_accounting([d])
    assert receipt.eligible_examples == 0
    assert receipt.schema_excluded_rows == 1


def test_missing_disposition_is_excluded():
    d = make_settled_dict()
    del d["disposition"]
    result = load_settled_events_from_dicts([d])
    assert result.schema_excluded_rows == 1
    assert "SCHEMA_MISSING_DISPOSITION" in result.schema_exclusion_reasons


def test_missing_date_excluded():
    d = make_settled_dict()
    del d["event_date"]
    result = load_settled_events_from_dicts([d])
    assert result.schema_excluded_rows == 1
    assert "SCHEMA_MISSING_EVENT_DATE" in result.schema_exclusion_reasons


def test_missing_sport_excluded():
    d = make_settled_dict()
    del d["sport"]
    result = load_settled_events_from_dicts([d])
    assert result.schema_excluded_rows == 1


def test_missing_participants_excluded():
    d = make_settled_dict()
    del d["participant_1"]
    result = load_settled_events_from_dicts([d])
    assert result.schema_excluded_rows == 1


def test_missing_probability_key_excluded_as_schema():
    d = make_settled_dict()
    del d["probability_1"]
    result = load_settled_events_from_dicts([d])
    assert result.schema_excluded_rows == 1
    assert "SCHEMA_MISSING_PROBABILITY_1" in result.schema_exclusion_reasons


# ---------------------------------------------------------------------------
# 2. Stop silently swallowing malformed records
# ---------------------------------------------------------------------------

def test_malformed_row_is_counted():
    good = make_settled_dict(event_id="football:good")
    bad = {"not": "a valid dict for settled event"}  # missing required fields
    result = load_settled_events_from_dicts([good, bad])
    assert result.raw_input_rows == 2
    assert result.schema_excluded_rows == 1
    assert len(result.valid_events) == 1
    # Malformed counted by reason
    assert sum(result.schema_exclusion_reasons.values()) == 1


def test_unreadable_json_fails_loudly():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "bad.json"
        p.write_text("{ invalid json")
        with pytest.raises(Exception):
            _load_json_file(p)


def test_corrupt_gzip_fails_loudly():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "bad.jsonl.gz"
        p.write_bytes(b"not gzip")
        with pytest.raises(Exception):
            _load_jsonl_gz_file(p)


def test_unknown_schema_version_fails():
    d = make_settled_dict(extra={"schema_version": "v999-unknown"})
    with pytest.raises(ValueError, match="UNKNOWN_SCHEMA_VERSION"):
        _validate_settled_dict(d)
    result = load_settled_events_from_dicts([d])
    assert result.schema_exclusion_reasons["UNKNOWN_SCHEMA_VERSION"] == 1


# ---------------------------------------------------------------------------
# 3. Raw vs canonical accounting
# ---------------------------------------------------------------------------

def test_raw_canonical_accounting_invariants():
    raw_dicts = [
        make_settled_dict(event_id="football:1", event_date="2026-01-01"),
        make_settled_dict(event_id="football:2", event_date="2026-01-02"),
        {"bad": "row"},  # schema excluded
        make_settled_dict(event_id="football:1", event_date="2026-01-01"),  # exact duplicate
    ]

    examples, receipt, schema_result = build_dataset_with_raw_accounting(raw_dicts)

    # Invariant 1: raw = schema_excluded + valid_loaded
    assert receipt.raw_input_rows == receipt.schema_excluded_rows + receipt.valid_loaded_rows

    # Invariant 2: valid = exact_duplicates_collapsed + canonical
    assert receipt.valid_loaded_rows == receipt.exact_duplicates_collapsed + receipt.canonical_input_rows

    # Invariant 3: canonical = eligible + builder_excluded
    assert receipt.canonical_input_rows == receipt.eligible_examples + receipt.builder_excluded_rows

    # Also check raw counts
    assert receipt.raw_input_rows == 4
    assert receipt.schema_excluded_rows == 1
    assert receipt.valid_loaded_rows == 3
    assert receipt.exact_duplicates_collapsed == 1
    assert receipt.canonical_input_rows == 2


def test_exact_duplicate_accounting():
    d1 = make_settled_dict(event_id="football:dup", event_date="2026-01-01", winner=1, score1=1, score2=0)
    d2 = make_settled_dict(event_id="football:dup", event_date="2026-01-01", winner=1, score1=1, score2=0)

    examples, receipt, _ = build_dataset_with_raw_accounting([d1, d2])

    assert receipt.raw_input_rows == 2
    assert receipt.valid_loaded_rows == 2
    assert receipt.exact_duplicates_collapsed == 1
    assert receipt.canonical_input_rows == 1
    assert receipt.eligible_examples == 1


def test_conflicting_duplicate_failure():
    d1 = make_settled_dict(event_id="football:conflict", event_date="2026-01-01", winner=1, score1=1, score2=0)
    d2 = make_settled_dict(event_id="football:conflict", event_date="2026-01-01", winner=2, score1=0, score2=1)

    with pytest.raises(ValueError, match="conflicting composite key"):
        build_dataset_with_raw_accounting([d1, d2])


# ---------------------------------------------------------------------------
# 4. Strengthened input digest
# ---------------------------------------------------------------------------

def test_digest_changes_when_probability_changes():
    d1 = make_settled_dict(event_id="football:1", prob1=0.6, prob2=0.3)
    d2 = make_settled_dict(event_id="football:1", prob1=0.7, prob2=0.2)

    # Need to convert to SettledEvent for digest
    ev1 = _validate_settled_dict(d1)
    ev2 = _validate_settled_dict(d2)

    digest1 = _compute_input_digest([ev1])
    digest2 = _compute_input_digest([ev2])

    assert digest1 != digest2


def test_digest_changes_when_winner_changes():
    d1 = make_settled_dict(event_id="football:1", winner=1)
    d2 = make_settled_dict(event_id="football:1", winner=2)

    ev1 = _validate_settled_dict(d1)
    ev2 = _validate_settled_dict(d2)

    assert _compute_input_digest([ev1]) != _compute_input_digest([ev2])


def test_digest_changes_when_score_changes():
    d1 = make_settled_dict(event_id="football:1", score1=1, score2=0)
    d2 = make_settled_dict(event_id="football:1", score1=2, score2=0)

    ev1 = _validate_settled_dict(d1)
    ev2 = _validate_settled_dict(d2)

    assert _compute_input_digest([ev1]) != _compute_input_digest([ev2])


def test_digest_does_not_change_when_input_order_changes():
    d1 = make_settled_dict(event_id="football:1", event_date="2026-01-01")
    d2 = make_settled_dict(event_id="football:2", event_date="2026-01-02")

    ev1 = _validate_settled_dict(d1)
    ev2 = _validate_settled_dict(d2)

    digest_ordered = _compute_input_digest([ev1, ev2])
    digest_reversed = _compute_input_digest([ev2, ev1])

    assert digest_ordered == digest_reversed


def test_odds_changes_do_not_change_digest():
    d1 = make_settled_dict(event_id="football:1", odds1=2.0, odds2=2.0)
    d2 = make_settled_dict(event_id="football:1", odds1=100.0, odds2=1.01)

    ev1 = _validate_settled_dict(d1)
    ev2 = _validate_settled_dict(d2)

    assert _compute_input_digest([ev1]) == _compute_input_digest([ev2])


# ---------------------------------------------------------------------------
# 5. Validate duplicate identity
# ---------------------------------------------------------------------------

def test_identical_duplicate_collapses():
    d1 = make_settled_dict(event_id="football:dup", sport="football", event_date="2026-01-01")
    d2 = make_settled_dict(event_id="football:dup", sport="football", event_date="2026-01-01")

    examples, receipt, _ = build_dataset_with_raw_accounting([d1, d2])
    assert receipt.canonical_input_rows == 1
    assert receipt.exact_duplicates_collapsed == 1


def test_same_event_id_different_sports_does_not_collapse():
    d1 = make_settled_dict(event_id="common:1", sport="football", event_date="2026-01-01")
    d2 = make_settled_dict(event_id="common:1", sport="basketball", event_date="2026-01-01", draw_prob=None, prob1=0.6, prob2=0.4)

    examples, receipt, _ = build_dataset_with_raw_accounting([d1, d2])
    # Composite key includes sport, so should NOT collapse
    assert receipt.canonical_input_rows == 2
    assert receipt.exact_duplicates_collapsed == 0


def test_same_participants_date_different_leagues_not_conflated_when_league_part_of_identity():
    # Our current composite key is (sport, event_id, event_date) matching settlement.py
    # Same participants/date but different event_id should not collapse (different matches)
    # Same event_id but different leagues with same sport/date — if event_id same, it would collapse per current key
    # This test documents that league is NOT part of current key, but we ensure different event_ids do not conflate
    d1 = make_settled_dict(event_id="football:match1", sport="football", event_date="2026-01-01", p1="Alpha", p2="Beta", extra={"league": "League A"})
    d2 = make_settled_dict(event_id="football:match2", sport="football", event_date="2026-01-01", p1="Alpha", p2="Beta", extra={"league": "League B"})

    examples, receipt, _ = build_dataset_with_raw_accounting([d1, d2])
    assert receipt.canonical_input_rows == 2


def test_changed_probability_same_composite_key_is_conflict():
    d1 = make_settled_dict(event_id="football:1", event_date="2026-01-01", prob1=0.6, prob2=0.3)
    d2 = make_settled_dict(event_id="football:1", event_date="2026-01-01", prob1=0.7, prob2=0.2)

    with pytest.raises(ValueError, match="conflicting composite key"):
        build_dataset_with_raw_accounting([d1, d2])


def test_changed_winner_disposition_same_composite_key_is_conflict():
    d1 = make_settled_dict(event_id="football:1", event_date="2026-01-01", winner=1, disposition="SETTLED")
    d2 = make_settled_dict(event_id="football:1", event_date="2026-01-01", winner=2, disposition="SETTLED")

    with pytest.raises(ValueError, match="conflicting composite key"):
        build_dataset_with_raw_accounting([d1, d2])

    d3 = make_settled_dict(event_id="football:1", event_date="2026-01-01", winner=1, disposition="SETTLED")
    d4 = make_settled_dict(event_id="football:1", event_date="2026-01-01", winner=1, disposition="VOID")

    with pytest.raises(ValueError, match="conflicting composite key"):
        build_dataset_with_raw_accounting([d3, d4])


def test_changed_provenance_same_content_collapses_per_integrity_policy():
    # Updated policy (final integrity): identical provenance collapses, missing vs present preserves present,
    # different non-empty hashes or source URLs fail loudly (deterministic)
    # Identical provenance should collapse
    d1 = make_settled_dict(event_id="football:1", event_date="2026-01-01", source_url="https://a.com", facets={"raw_sha256": "a"*64})
    d2 = make_settled_dict(event_id="football:1", event_date="2026-01-01", source_url="https://a.com", facets={"raw_sha256": "a"*64})

    examples, receipt, _ = build_dataset_with_raw_accounting([d1, d2])
    assert receipt.exact_duplicates_collapsed == 1
    assert receipt.canonical_input_rows == 1

    # Missing vs present provenance deterministically preserves present
    d_missing = make_settled_dict(event_id="football:2", event_date="2026-01-01", source_url="", facets={})
    d_present = make_settled_dict(event_id="football:2", event_date="2026-01-01", source_url="https://a.com", facets={"raw_sha256": "a"*64})

    examples2, receipt2, _ = build_dataset_with_raw_accounting([d_missing, d_present])
    assert receipt2.canonical_input_rows == 1
    assert receipt2.provenance_present == 1  # present preserved
    # Reverse order should also preserve present and be stable
    examples2_rev, receipt2_rev, _ = build_dataset_with_raw_accounting([d_present, d_missing])
    assert receipt2_rev.canonical_input_rows == 1
    assert receipt2_rev.provenance_present == 1
    assert examples2[0].raw_sha256 == examples2_rev[0].raw_sha256 == "a"*64

    # Different non-empty hashes should fail loudly
    d_hash_a = make_settled_dict(event_id="football:3", event_date="2026-01-01", source_url="https://a.com", facets={"raw_sha256": "a"*64})
    d_hash_b = make_settled_dict(event_id="football:3", event_date="2026-01-01", source_url="https://a.com", facets={"raw_sha256": "b"*64})
    with pytest.raises(ValueError, match="conflicting provenance raw_sha256"):
        build_dataset_with_raw_accounting([d_hash_a, d_hash_b])

    # Different non-empty source URLs should fail loudly
    d_url_a = make_settled_dict(event_id="football:4", event_date="2026-01-01", source_url="https://a.com", facets={"raw_sha256": "a"*64})
    d_url_b = make_settled_dict(event_id="football:4", event_date="2026-01-01", source_url="https://b.com", facets={"raw_sha256": "a"*64})
    with pytest.raises(ValueError, match="conflicting provenance source_url"):
        build_dataset_with_raw_accounting([d_url_a, d_url_b])


# ---------------------------------------------------------------------------
# 6. Validate real ledger adapter separately
# ---------------------------------------------------------------------------

def test_adapter_supports_settled_history_json_shape():
    # Shape produced by settlement.py append_settled_from_capture: asdict(SettledEvent)
    settled_dict = make_settled_dict()
    # settled_history.json is list of such dicts
    result = load_settled_events_from_dicts([settled_dict])
    assert result.raw_input_rows == 1
    assert result.schema_excluded_rows == 0
    assert len(result.valid_events) == 1


def test_adapter_supports_history_jsonl_gz_shape():
    # Shape produced by backfill.py: asdict(SettledEvent) + facets raw_sha256
    settled_dict = make_settled_dict(facets={"raw_sha256": "a"*64})
    result = load_settled_events_from_dicts([settled_dict])
    assert len(result.valid_events) == 1


def test_adapter_rejects_unknown_schema_version():
    d = make_settled_dict(extra={"schema_version": "v2-future"})
    result = load_settled_events_from_dicts([d])
    assert result.schema_excluded_rows == 1
    assert result.schema_exclusion_reasons["UNKNOWN_SCHEMA_VERSION"] == 1


def test_adapter_does_not_guess_aliases():
    # Old unsafe code guessed host/guest/prob1/prob2/date — new adapter should reject
    bad_alias = {
        "host": "Team A",
        "guest": "Team B",
        "prob1": 0.6,
        "prob2": 0.3,
        "date": "2026-01-01",
    }
    result = load_settled_events_from_dicts([bad_alias])
    assert result.schema_excluded_rows == 1
    # Should not be parsed as valid
    assert len(result.valid_events) == 0


# ---------------------------------------------------------------------------
# 7. Correct receipt date semantics
# ---------------------------------------------------------------------------

def test_all_excluded_dataset_date_semantics():
    # All rows excluded — canonical dates should still be present, eligible dates None
    raw_dicts = [
        make_settled_dict(event_id="football:1", event_date="2026-01-01", prob1=0.5, prob2=0.5),  # equal -> excluded
        make_settled_dict(event_id="football:2", event_date="2026-01-10", disposition="VOID"),  # void excluded
    ]

    examples, receipt, _ = build_dataset_with_raw_accounting(raw_dicts)

    assert receipt.eligible_examples == 0
    assert receipt.canonical_date_min == "2026-01-01"
    assert receipt.canonical_date_max == "2026-01-10"
    assert receipt.eligible_date_min is None
    assert receipt.eligible_date_max is None
    # Backward compat date_min/max alias eligible dates should be None
    assert receipt.date_min is None
    assert receipt.date_max is None


def test_receipt_date_fields_explicit():
    raw_dicts = [
        make_settled_dict(event_id="football:1", event_date="2026-01-01"),
        make_settled_dict(event_id="football:2", event_date="2026-01-05"),
    ]

    examples, receipt, _ = build_dataset_with_raw_accounting(raw_dicts)

    assert receipt.canonical_date_min == "2026-01-01"
    assert receipt.canonical_date_max == "2026-01-05"
    assert receipt.eligible_date_min == "2026-01-01"
    assert receipt.eligible_date_max == "2026-01-05"


# ---------------------------------------------------------------------------
# 8. Provenance accounting
# ---------------------------------------------------------------------------

def test_malformed_sha256_counted_separately():
    valid_sha = "a" * 64
    invalid_sha = "not-a-valid-sha256"
    missing_sha = ""

    d_valid = make_settled_dict(event_id="football:1", facets={"raw_sha256": valid_sha})
    d_invalid = make_settled_dict(event_id="football:2", facets={"raw_sha256": invalid_sha})
    d_missing = make_settled_dict(event_id="football:3", facets={"raw_sha256": missing_sha})

    examples, receipt, _ = build_dataset_with_raw_accounting([d_valid, d_invalid, d_missing])

    assert receipt.provenance_present == 1
    assert receipt.provenance_invalid == 1
    assert receipt.provenance_missing == 1


def test_legacy_provenance_missing_marker():
    d = make_settled_dict(event_id="football:1", facets={})
    examples, receipt, _ = build_dataset_with_raw_accounting([d])

    # No raw_sha256 → provenance_missing, legacy_provenance_missing True
    assert receipt.provenance_missing == 1
    assert examples[0].legacy_provenance_missing is True
    assert examples[0].raw_sha256 == ""


# ---------------------------------------------------------------------------
# 9. No network or Jina probes yet — ensured by not importing forebet collector
# ---------------------------------------------------------------------------

def test_no_network_import_in_dataset_modules():
    # Ensure dataset modules do not import forebet collector or network
    import slumdog.dataset as ds
    import slumdog.dataset_audit as da

    # Check source code does not contain forbidden imports
    ds_source = Path(ds.__file__).read_text()
    da_source = Path(da.__file__).read_text()

    assert "ForebetCollector" not in ds_source
    assert "relay_get" not in ds_source
    assert "Jina" not in ds_source
    assert "ForebetCollector" not in da_source


# ---------------------------------------------------------------------------
# Additional integrity: odds changes do not affect digest already tested, but also ensure builder
# ---------------------------------------------------------------------------

def test_odds_present_vs_absent_does_not_change_digest_but_features_same():
    d_with_odds = make_settled_dict(event_id="football:1", odds1=2.5, odds2=1.5)
    d_without_odds = make_settled_dict(event_id="football:1", odds1=None, odds2=None)

    ev_with = _validate_settled_dict(d_with_odds)
    ev_without = _validate_settled_dict(d_without_odds)

    # Digest should be same (odds excluded deliberately)
    assert _compute_input_digest([ev_with]) == _compute_input_digest([ev_without])

    # Features should be same (price independence)
    examples_with, _, _ = build_dataset_with_raw_accounting([d_with_odds])
    examples_without, _, _ = build_dataset_with_raw_accounting([d_without_odds])
    assert examples_with[0].features == examples_without[0].features
