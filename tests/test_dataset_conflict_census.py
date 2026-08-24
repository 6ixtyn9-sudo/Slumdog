"""Milestone 4F conflict census — read-only diagnostic for genuine ledger conflicts.

Tests required:
- census continues after first conflict
- multiple conflicts counted/grouped by sport
- OUTCOME_CONFLICT for score differences
- PROBABILITY_CONFLICT
- DOMAIN_CONFLICT
- DISPOSITION_CONFLICT
- MULTIPLE
- source location preserved (source_file, source_location)
- no full serialization (no score_1, participant_1 etc in report)
- normal mode still fails loudly
- census emits no examples (audit path)
- deterministic under reordering
- JSON only under /tmp (enforced by audit_dataset)
"""

import json
import gzip
import tempfile
from pathlib import Path

import pytest

from slumdog.dataset import (
    ValidEventWithSource,
    build_conflict_census,
    build_dataset_with_raw_accounting,
    _validate_settled_dict,
)
from slumdog.dataset_audit import audit_dataset


def make_settled_dict(
    event_id="hockey:278977",
    sport="hockey",
    event_date="2023-08-20",
    p1="Netherlands W",
    p2="Denmark W",
    prob1=0.54,
    prob2=0.46,
    draw_prob=None,
    winner=2,
    score1=1,
    score2=6,
    disposition="SETTLED",
    source_url="/en/hockey/matches/wch-ia-women/netherlands-w-denmark-w/278977",
    facets=None,
    league="WCH IA Women",
    period1=(0, 1, 0),
    period2=(2, 1, 3),
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
        "forebet_pick": 2,
        "odds_1": None,
        "odds_2": None,
        "league": league,
        "period_scores_1": period1,
        "period_scores_2": period2,
        "source_url": source_url,
        "disposition": disposition,
        "facets": facets or {},
    }
    return d


def make_valid_with_source(d, source_file, source_location):
    ev = _validate_settled_dict(d)
    return ValidEventWithSource(event=ev, source_file=source_file, source_location=source_location)


def test_census_continues_after_first_conflict():
    # Two different conflicting keys
    d1a = make_settled_dict(event_id="hockey:1", event_date="2023-08-20", score1=1, score2=6)
    d1b = make_settled_dict(event_id="hockey:1", event_date="2023-08-20", score1=0, score2=4, period1=(0, 0, 0), period2=(1, 2, 1))
    d2a = make_settled_dict(event_id="hockey:2", event_date="2023-08-21", score1=2, score2=3)
    d2b = make_settled_dict(event_id="hockey:2", event_date="2023-08-21", score1=5, score2=0)

    valid = [
        make_valid_with_source(d1a, "data/reports/history_hockey.jsonl.gz", "line:62"),
        make_valid_with_source(d1b, "data/reports/history_hockey.jsonl.gz", "line:67"),
        make_valid_with_source(d2a, "data/reports/history_hockey.jsonl.gz", "line:100"),
        make_valid_with_source(d2b, "data/reports/history_hockey.jsonl.gz", "line:101"),
    ]

    groups, receipt, _ = build_conflict_census(valid)
    assert len(groups) == 2
    assert receipt.conflicting_composite_keys == 2
    assert receipt.conflicting_rows == 4


def test_multiple_conflicts_grouped_by_sport():
    d_h1a = make_settled_dict(sport="hockey", event_id="hockey:1", event_date="2023-08-20", score1=1, score2=2)
    d_h1b = make_settled_dict(sport="hockey", event_id="hockey:1", event_date="2023-08-20", score1=3, score2=4)
    d_f1a = make_settled_dict(sport="football", event_id="football:1", event_date="2023-08-20", score1=0, score2=1)
    d_f1b = make_settled_dict(sport="football", event_id="football:1", event_date="2023-08-20", score1=2, score2=2)

    valid = [
        make_valid_with_source(d_h1a, "a.gz", "line:1"),
        make_valid_with_source(d_h1b, "a.gz", "line:2"),
        make_valid_with_source(d_f1a, "b.gz", "line:1"),
        make_valid_with_source(d_f1b, "b.gz", "line:2"),
    ]
    groups, receipt, _ = build_conflict_census(valid)
    assert receipt.conflicts_by_sport["hockey"] == 1
    assert receipt.conflicts_by_sport["football"] == 1
    assert receipt.conflicting_composite_keys == 2


def test_outcome_conflict_for_score_differences():
    # Real hockey:278977 case — differing score_1, score_2, period_scores
    d_a = make_settled_dict(score1=1, score2=6, period1=(0, 1, 0), period2=(2, 1, 3))
    d_b = make_settled_dict(score1=0, score2=4, period1=(0, 0, 0), period2=(1, 2, 1))

    valid = [
        make_valid_with_source(d_a, "data/reports/history_hockey.jsonl.gz", "line:62"),
        make_valid_with_source(d_b, "data/reports/history_hockey.jsonl.gz", "line:67"),
    ]
    groups, receipt, _ = build_conflict_census(valid)
    assert len(groups) == 1
    g = groups[0]
    assert g.classification == "OUTCOME_CONFLICT"
    assert "score_1" in g.conflicting_fields
    assert "score_2" in g.conflicting_fields
    assert "period_scores_1" in g.conflicting_fields
    assert "period_scores_2" in g.conflicting_fields


def test_probability_conflict():
    d_a = make_settled_dict(prob1=0.54, prob2=0.46)
    d_b = make_settled_dict(prob1=0.60, prob2=0.40)

    valid = [
        make_valid_with_source(d_a, "a.gz", "line:1"),
        make_valid_with_source(d_b, "a.gz", "line:2"),
    ]
    groups, receipt, _ = build_conflict_census(valid)
    assert groups[0].classification == "PROBABILITY_CONFLICT"
    assert "probability_1" in groups[0].conflicting_fields or "probability_2" in groups[0].conflicting_fields


def test_domain_conflict():
    d_a = make_settled_dict(p1="Team A", p2="Team B")
    d_b = make_settled_dict(p1="Team A", p2="Team C")  # different participant_2

    valid = [
        make_valid_with_source(d_a, "a.gz", "line:1"),
        make_valid_with_source(d_b, "a.gz", "line:2"),
    ]
    groups, receipt, _ = build_conflict_census(valid)
    assert groups[0].classification == "DOMAIN_CONFLICT"
    assert "participant_2" in groups[0].conflicting_fields


def test_disposition_conflict():
    d_a = make_settled_dict(disposition="SETTLED")
    d_b = make_settled_dict(disposition="SETTLED_CUP")

    valid = [
        make_valid_with_source(d_a, "a.gz", "line:1"),
        make_valid_with_source(d_b, "a.gz", "line:2"),
    ]
    groups, receipt, _ = build_conflict_census(valid)
    assert groups[0].classification == "DISPOSITION_CONFLICT"
    assert "disposition" in groups[0].conflicting_fields


def test_multiple_classification():
    d_a = make_settled_dict(p1="A", score1=1, prob1=0.54)
    d_b = make_settled_dict(p1="B", score1=2, prob1=0.60)

    valid = [
        make_valid_with_source(d_a, "a.gz", "line:1"),
        make_valid_with_source(d_b, "a.gz", "line:2"),
    ]
    groups, receipt, _ = build_conflict_census(valid)
    assert groups[0].classification == "MULTIPLE"
    # Should have fields from multiple categories
    assert len(groups[0].conflicting_fields) >= 2


def test_source_location_preserved():
    d_a = make_settled_dict(score1=1)
    d_b = make_settled_dict(score1=2)

    valid = [
        make_valid_with_source(d_a, "data/reports/history_hockey.jsonl.gz", "line:62"),
        make_valid_with_source(d_b, "data/reports/history_hockey.jsonl.gz", "line:67"),
    ]
    groups, receipt, _ = build_conflict_census(valid)
    g = groups[0]
    assert len(g.source_entries) == 2
    locs = {e["source_location"] for e in g.source_entries}
    assert "line:62" in locs
    assert "line:67" in locs
    files = {e["source_file"] for e in g.source_entries}
    assert "data/reports/history_hockey.jsonl.gz" in files


def test_no_full_serialization_in_conflict_report():
    d_a = make_settled_dict(score1=1)
    d_b = make_settled_dict(score1=2)

    valid = [
        make_valid_with_source(d_a, "a.gz", "line:1"),
        make_valid_with_source(d_b, "a.gz", "line:2"),
    ]
    groups, receipt, _ = build_conflict_census(valid)
    g = groups[0]
    # ConflictGroup should not contain full event dicts
    # Ensure no prohibited full record fields in group itself
    # The report JSON should only have allowed keys
    entry = {
        "composite_key": list(g.composite_key),
        "sport": g.sport,
        "conflicting_fields": g.conflicting_fields,
        "classification": g.classification,
        "raw_sha256_values": g.raw_sha256_values,
        "source_url_values": g.source_url_values,
        "source_entries": g.source_entries,
    }
    json.dumps(entry)  # ensure serializable
    # Should not contain full participant names as top-level keys? But source_url may contain them
    # Check that no full event serialization like participant_1, score_1 etc as top-level
    assert "participant_1" not in entry
    assert "score_1" not in entry or "score_1" in entry["conflicting_fields"]  # score_1 allowed only as conflicting field name, not value
    # Ensure source_entries only have allowed keys
    for se in entry["source_entries"]:
        assert set(se.keys()) == {"source_file", "source_location", "raw_sha256", "source_url"}


def test_normal_mode_still_fails_loudly():
    d_a = make_settled_dict(score1=1)
    d_b = make_settled_dict(score1=2)
    with pytest.raises(ValueError, match="conflicting composite key"):
        build_dataset_with_raw_accounting([d_a, d_b] if False else [make_settled_dict(score1=1), make_settled_dict(score1=2)])
    # Use actual dicts
    raw = [make_settled_dict(score1=1), make_settled_dict(score1=2)]
    with pytest.raises(ValueError, match="conflicting composite key"):
        build_dataset_with_raw_accounting(raw)


def test_deterministic_under_reordering():
    d_a = make_settled_dict(event_id="hockey:1", score1=1)
    d_b = make_settled_dict(event_id="hockey:1", score1=2)
    d_c = make_settled_dict(event_id="hockey:2", score1=3)
    d_d = make_settled_dict(event_id="hockey:2", score1=4)

    valid1 = [
        make_valid_with_source(d_a, "a.gz", "line:1"),
        make_valid_with_source(d_b, "a.gz", "line:2"),
        make_valid_with_source(d_c, "a.gz", "line:3"),
        make_valid_with_source(d_d, "a.gz", "line:4"),
    ]
    valid2 = [
        make_valid_with_source(d_d, "a.gz", "line:4"),
        make_valid_with_source(d_c, "a.gz", "line:3"),
        make_valid_with_source(d_b, "a.gz", "line:2"),
        make_valid_with_source(d_a, "a.gz", "line:1"),
    ]

    groups1, receipt1, _ = build_conflict_census(valid1)
    groups2, receipt2, _ = build_conflict_census(valid2)

    assert receipt1.conflicting_composite_keys == receipt2.conflicting_composite_keys
    assert receipt1.conflicts_by_sport == receipt2.conflicts_by_sport
    # Groups sorted deterministically by composite key, not input order
    assert [g.composite_key for g in groups1] == [g.composite_key for g in groups2]
    assert groups1[0].classification == groups2[0].classification


def test_census_emits_no_examples_and_status_data_conflicts():
    # Integration test for audit_dataset in census mode with real conflicting file
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_root = Path(tmpdir) / "data"
        reports = tmp_root / "reports"
        reports.mkdir(parents=True)
        # Create a gz file with conflicting duplicate
        gz_path = reports / "history_hockey.jsonl.gz"
        d_a = make_settled_dict(event_id="hockey:278977", event_date="2023-08-20", score1=1, score2=6, period1=(0, 1, 0), period2=(2, 1, 3))
        d_b = make_settled_dict(event_id="hockey:278977", event_date="2023-08-20", score1=0, score2=4, period1=(0, 0, 0), period2=(1, 2, 1))
        with gzip.open(gz_path, "wt", encoding="utf-8") as f:
            f.write(json.dumps(d_a) + "\n")
            f.write(json.dumps(d_b) + "\n")

        receipt_path = Path("/tmp") / "slumdog_test_receipt.json"
        sample_path = Path("/tmp") / "slumdog_test_sample.json"
        conflict_path = Path("/tmp") / "slumdog_test_conflicts.json"
        # Clean any previous
        for p in [receipt_path, sample_path, conflict_path]:
            if p.exists():
                p.unlink()

        code = audit_dataset(tmp_root, receipt_path, sample_path, sample_size=5, conflict_report_path=conflict_path)
        # Should be nonzero and DATA_CONFLICTS
        assert code != 0
        assert receipt_path.exists()
        receipt_data = json.loads(receipt_path.read_text())
        assert receipt_data["status"] == "DATA_CONFLICTS"
        assert receipt_data["conflicting_composite_keys"] == 1
        assert receipt_data["conflicting_rows"] == 2
        assert "hockey" in receipt_data["conflicts_by_sport"]
        assert receipt_data["conflicts_without_valid_raw_sha256"] == 1
        assert receipt_data["conflicts_with_valid_raw_sha256"] == 0

        assert conflict_path.exists()
        conflicts = json.loads(conflict_path.read_text())
        assert len(conflicts) == 1
        assert conflicts[0]["classification"] == "OUTCOME_CONFLICT"
        assert "score_1" in conflicts[0]["conflicting_fields"]

        # Examples not emitted — sample should not exist or be absent? Spec says not emitted
        # Our implementation does not write sample in conflict mode
        assert not sample_path.exists() or sample_path.read_text().strip() == "" or json.loads(sample_path.read_text()) == [] or True
        # Actually in our code we don't write sample in conflict path, so check not exists
        # The audit_dataset for conflict path returns before writing sample
        assert not sample_path.exists()

        # Ensure conflict report contains only allowed fields
        for entry in conflicts:
            assert set(entry.keys()) == {"composite_key", "sport", "conflicting_fields", "classification", "raw_sha256_values", "source_url_values", "source_entries"}
            for se in entry["source_entries"]:
                assert set(se.keys()) == {"source_file", "source_location", "raw_sha256", "source_url"}


def test_provenance_conflict_counts():
    # Conflict with valid raw_sha256 should be counted in conflicts_with_valid
    sha_a = "a" * 64
    sha_b = "b" * 64
    d_a = make_settled_dict(facets={"raw_sha256": sha_a}, source_url="https://a.com")
    d_b = make_settled_dict(facets={"raw_sha256": sha_b}, source_url="https://a.com")  # same url, different hash -> provenance conflict

    # But note: builder would fail on provenance conflict, but census should detect it as PROVENANCE_CONFLICT
    # Our _compare_events_for_conflict checks raw_sha256 conflict only when both non-empty and different
    # However build_dataset_with_raw_accounting would raise provenance conflict, not domain conflict
    # For census, we want it to be counted
    valid = [
        make_valid_with_source(d_a, "a.gz", "line:1"),
        make_valid_with_source(d_b, "a.gz", "line:2"),
    ]
    groups, receipt, _ = build_conflict_census(valid)
    assert len(groups) == 1
    assert groups[0].classification == "PROVENANCE_CONFLICT"
    assert receipt.conflicts_with_valid_raw_sha256 == 1
    assert receipt.conflicts_without_valid_raw_sha256 == 0


def test_json_only_under_tmp_enforced():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_root = Path(tmpdir) / "data"
        reports = tmp_root / "reports"
        reports.mkdir(parents=True)
        gz_path = reports / "history_hockey.jsonl.gz"
        d = make_settled_dict()
        with gzip.open(gz_path, "wt", encoding="utf-8") as f:
            f.write(json.dumps(d) + "\n")

        # Try to write receipt outside /tmp — should fail (use cwd which is not under /tmp)
        bad_receipt = Path.cwd() / "receipt_outside_tmp.json"
        good_sample = Path("/tmp/slumdog_test_sample2.json")
        code = audit_dataset(tmp_root, bad_receipt, good_sample, sample_size=1, conflict_report_path=None)
        assert code != 0
        assert not bad_receipt.exists()

        # Try conflict report outside /tmp
        good_receipt = Path("/tmp/slumdog_test_receipt2.json")
        bad_conflict = Path.cwd() / "conflicts_outside_tmp.json"
        code = audit_dataset(tmp_root, good_receipt, good_sample, sample_size=1, conflict_report_path=bad_conflict)
        assert code != 0
        assert not bad_conflict.exists()

        # Cleanup any accidental files
        for p in [bad_receipt, bad_conflict]:
            if p.exists():
                p.unlink()
