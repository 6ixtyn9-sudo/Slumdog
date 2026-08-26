"""Milestone 6A research-mode tests — explicit opt-in, conflict-key exclusion,
census-before-collapse ordering, deterministic artifacts, price independence,
and unchanged strict/pipeline behavior."""

import gzip
import json
import random
import tempfile
from pathlib import Path

from slumdog.dataset_audit import audit_dataset
from slumdog.research_dataset import (
    RESEARCH_MODE,
    RESEARCH_STATUS,
    _collect_prohibited_keys,
    build_research_dataset,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_settled_dict(
    event_id="hockey:1",
    sport="hockey",
    event_date="2023-08-20",
    p1="Netherlands W",
    p2="Denmark W",
    prob1=0.6,
    prob2=0.4,
    draw_prob=None,
    winner=2,
    score1=1,
    score2=6,
    disposition="SETTLED",
    source_url="/en/hockey/matches/x/y/1",
    facets=None,
    league="TST",
    period1=(0, 1, 0),
    period2=(2, 1, 3),
    odds1=None,
    odds2=None,
):
    return {
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
        "odds_1": odds1,
        "odds_2": odds2,
        "league": league,
        "period_scores_1": period1,
        "period_scores_2": period2,
        "source_url": source_url,
        "disposition": disposition,
        "facets": facets if facets is not None else {},
    }


def conflicting_pair_rows():
    """hockey:K variants A (1-6) and B (0-4) — OUTCOME_CONFLICT like the real ledger."""
    return [
        make_settled_dict(event_id="hockey:K", event_date="2023-08-20", winner=2, score1=1, score2=6),
        make_settled_dict(
            event_id="hockey:K", event_date="2023-08-20", winner=2, score1=0, score2=4,
            period1=(0, 0, 0), period2=(1, 2, 1),
        ),
    ]


def write_ledger(root: Path, rows: list[dict]) -> Path:
    interim = root / "interim"
    interim.mkdir(exist_ok=True)
    (root / "reports").mkdir(exist_ok=True)
    path = interim / "settled_history.json"
    path.write_text(json.dumps(rows))
    return path


def run_research(root: Path, out_dir: Path, rows, *, examples=True, sample_size=5, reorder_seed=None):
    ledger_path = write_ledger(root, rows)
    receipt = out_dir / "receipt.json"
    sample = out_dir / "sample.json"
    examples_path = out_dir / "examples.jsonl.gz" if examples else None
    code = audit_dataset(
        root, receipt, sample, sample_size,
        research_exclude_conflicts=True, examples_path=examples_path,
    )
    receipt_data = json.loads(receipt.read_text())
    sample_data = json.loads(sample.read_text())
    examples_bytes = examples_path.read_bytes() if examples else None
    return code, receipt_data, sample_data, examples_bytes, ledger_path


def ledger_bytes(path: Path) -> bytes:
    return path.read_bytes()


# ---------------------------------------------------------------------------
# 1. Strict mode still fails and emits no examples
# ---------------------------------------------------------------------------


def test_strict_mode_still_fails_on_conflicts_and_emits_nothing():
    with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_out:
        root, out = Path(tmp_root), Path(tmp_out)
        write_ledger(root, conflicting_pair_rows())
        receipt, sample = out / "receipt.json", out / "sample.json"
        code = audit_dataset(root, receipt, sample, sample_size=2)
        assert code == 1
        # Normal strict mode fails before writing artifacts.
        assert not receipt.exists()
        assert not sample.exists()


# ---------------------------------------------------------------------------
# 2-3. Research requires the explicit flag; --examples rejected without it
# ---------------------------------------------------------------------------


def test_examples_without_research_flag_rejected():
    with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_out:
        root, out = Path(tmp_root), Path(tmp_out)
        write_ledger(root, [make_settled_dict()])
        code = audit_dataset(
            root, out / "receipt.json", out / "sample.json", 5,
            research_exclude_conflicts=False, examples_path=out / "examples.jsonl.gz",
        )
        assert code == 1


def test_research_requires_explicit_flag_plain_run_still_strict():
    with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_out:
        root, out = Path(tmp_root), Path(tmp_out)
        write_ledger(root, conflicting_pair_rows())
        # Without the flag there is no research behavior at all — conflict still fails.
        code = audit_dataset(root, out / "receipt.json", out / "sample.json", 5)
        assert code == 1


# ---------------------------------------------------------------------------
# 4. Non-/tmp examples path rejected
# ---------------------------------------------------------------------------


def test_non_tmp_examples_path_rejected():
    with tempfile.TemporaryDirectory() as tmp_root:
        root = Path(tmp_root)
        write_ledger(root, [make_settled_dict()])
        code = audit_dataset(
            root,
            Path("/home/user/Slumdog") / "receipt.json",
            Path("/home/user/Slumdog") / "sample.json",
            5,
            research_exclude_conflicts=True,
            examples_path=Path("/home/user/Slumdog") / "examples.jsonl.gz",
        )
        assert code == 1


# ---------------------------------------------------------------------------
# 5. Conflict census runs before exact duplicate collapse
# 6. Every row under a conflicting key is excluded
# 7. No conflicting variant survives
# ---------------------------------------------------------------------------


def test_census_runs_before_duplicate_collapse():
    with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_out:
        root, out = Path(tmp_root), Path(tmp_out)
        rows = [
            conflicting_pair_rows()[0],                            # variant A
            conflicting_pair_rows()[0],                            # exact dup of A
            conflicting_pair_rows()[1],                            # variant B (conflict)
            make_settled_dict(event_id="hockey:L", event_date="2023-08-21"),
        ]
        code, receipt, _sample, examples_bytes, _ledger = run_research(root, out, rows)
        assert code == 0
        acc = receipt["accounting"]
        # Census-first: ALL 3 rows under hockey:K are excluded (including the
        # exact duplicate). If collapse ran first, conflicting_rows would be 2
        # and exact_duplicates_collapsed would be 1.
        assert acc["conflicting_composite_keys_excluded"] == 1
        assert acc["conflicting_rows_excluded"] == 3
        assert acc["exact_duplicates_collapsed"] == 0
        assert acc["accounting_balanced"] is True


def test_all_rows_under_conflicting_key_excluded_and_no_variant_survives():
    with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_out:
        root, out = Path(tmp_root), Path(tmp_out)
        rows = [
            *conflicting_pair_rows(),
            make_settled_dict(event_id="hockey:L", event_date="2023-08-21", winner=2, score1=2, score2=3),
        ]
        code, receipt, _sample, examples_bytes, _ledger = run_research(root, out, rows)
        assert code == 0
        lines = gzip.decompress(examples_bytes).decode().splitlines()
        emitted = [json.loads(line) for line in lines]
        assert len(emitted) == 1
        assert emitted[0]["event_id"] == "hockey:L"
        keys = {(e["sport"], e["event_id"], e["event_date"]) for e in emitted}
        assert ("hockey", "hockey:K", "2023-08-20") not in keys
        assert receipt["accounting"]["eligible_examples"] == 1


# ---------------------------------------------------------------------------
# 8. Exact duplicates outside conflicts collapse
# ---------------------------------------------------------------------------


def test_exact_duplicates_outside_conflicts_collapse():
    with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_out:
        root, out = Path(tmp_root), Path(tmp_out)
        rows = [
            *conflicting_pair_rows(),
            make_settled_dict(event_id="hockey:L", event_date="2023-08-21"),
            make_settled_dict(event_id="hockey:L", event_date="2023-08-21"),
        ]
        code, receipt, _sample, examples_bytes, _ledger = run_research(root, out, rows)
        assert code == 0
        acc = receipt["accounting"]
        assert acc["exact_duplicates_collapsed"] == 1
        assert acc["canonical_non_conflicting_rows"] == 1
        lines = gzip.decompress(examples_bytes).decode().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["event_id"] == "hockey:L"


# ---------------------------------------------------------------------------
# 9. Accounting invariants balance
# ---------------------------------------------------------------------------


def test_accounting_invariants_balance():
    with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_out:
        root, out = Path(tmp_root), Path(tmp_out)
        rows = [
            *conflicting_pair_rows(),
            make_settled_dict(event_id="hockey:L", event_date="2023-08-21"),
            make_settled_dict(event_id="hockey:L", event_date="2023-08-21"),
            {"bad": "row"},  # schema-excluded
        ]
        code, receipt, _sample, _examples, _ledger = run_research(root, out, rows)
        assert code == 0
        acc = receipt["accounting"]
        assert acc["raw_input_rows"] == 5
        assert acc["schema_excluded_rows"] == 1
        assert acc["valid_loaded_rows"] == 4
        assert acc["conflicting_rows_excluded"] == 2
        assert acc["exact_duplicates_collapsed"] == 1
        assert acc["canonical_non_conflicting_rows"] == 1
        assert acc["eligible_examples"] == 1
        assert acc["builder_excluded_rows"] == 0
        # Explicit equations:
        assert acc["raw_input_rows"] == acc["schema_excluded_rows"] + acc["valid_loaded_rows"]
        assert acc["valid_loaded_rows"] == (
            acc["exact_duplicates_collapsed"]
            + acc["conflicting_rows_excluded"]
            + acc["canonical_non_conflicting_rows"]
        )
        assert acc["canonical_non_conflicting_rows"] == (
            acc["eligible_examples"] + acc["builder_excluded_rows"]
        )
        assert acc["accounting_balanced"] is True


# ---------------------------------------------------------------------------
# 10. Empty-participant count is a schema-exclusion subset
# ---------------------------------------------------------------------------


def test_empty_participant_rows_are_schema_exclusion_subset():
    with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_out:
        root, out = Path(tmp_root), Path(tmp_out)
        malformed = make_settled_dict(
            event_id="american_football:15799", sport="american_football",
            event_date="2024-08-31", p1="", p2="",
        )
        rows = [
            malformed,
            make_settled_dict(event_id="hockey:L", event_date="2023-08-21"),
        ]
        code, receipt, _sample, examples_bytes, _ledger = run_research(root, out, rows)
        assert code == 0
        acc = receipt["accounting"]
        assert acc["malformed_empty_participant_rows"] == 1
        assert acc["schema_excluded_rows"] == 1
        assert acc["malformed_empty_participant_rows"] <= acc["schema_excluded_rows"]
        # The malformed row must not appear in examples.
        lines = gzip.decompress(examples_bytes).decode().splitlines()
        assert all(json.loads(line)["event_id"] == "hockey:L" for line in lines)


# ---------------------------------------------------------------------------
# 11. Provenance absence remains visible
# ---------------------------------------------------------------------------


def test_provenance_absence_visible():
    with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_out:
        root, out = Path(tmp_root), Path(tmp_out)
        code, receipt, _sample, _examples, _ledger = run_research(
            root, out, [make_settled_dict(event_id="hockey:L", event_date="2023-08-21")],
        )
        assert code == 0
        provenance = receipt["readiness"]["global"]["provenance"]
        assert provenance["present"] == 0
        assert provenance["missing"] == 1
        assert "LEGACY_PROVENANCE_ABSENT" in receipt["limitations"]


# ---------------------------------------------------------------------------
# 12. Prohibited example keys absent (exact key scan)
# ---------------------------------------------------------------------------


def test_prohibited_example_keys_absent():
    with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_out:
        root, out = Path(tmp_root), Path(tmp_out)
        code, receipt, _sample, _examples, _ledger = run_research(
            root, out, [make_settled_dict(event_id="hockey:L", event_date="2023-08-21")],
        )
        assert code == 0
        pi = receipt["price_independence"]
        assert pi["example_keys_checked"] is True
        assert pi["prohibited_example_keys_found"] == []
        assert pi["passed"] is True


def test_prohibited_key_scan_is_exact_and_recursive():
    found: set = set()
    _collect_prohibited_keys(
        {"nested": [{"odds_1": 2.0}], "text": "ROI is not a key here", "safe": 1},
        found,
    )
    assert found == {"odds_1"}


# ---------------------------------------------------------------------------
# 13. Odds mutation leaves canonical identity, features, label, eligibility
#     and the example digest unchanged
# ---------------------------------------------------------------------------


def test_odds_mutation_leaves_examples_and_digest_unchanged():
    base_row = make_settled_dict(event_id="hockey:L", event_date="2023-08-21")
    with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_out:
        root, out = Path(tmp_root), Path(tmp_out)
        code1, receipt1, _s1, examples1, _l1 = run_research(root, out, [dict(base_row)])
        assert code1 == 0
    with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_out:
        root, out = Path(tmp_root), Path(tmp_out)
        priced = dict(base_row, odds_1=2.5, odds_2=1.5)
        code2, receipt2, _s2, examples2, _l2 = run_research(root, out, [priced])
        assert code2 == 0
    assert examples1 == examples2
    assert receipt1["examples_digest"] == receipt2["examples_digest"]
    assert receipt1["input_digest"] == receipt2["input_digest"]
    assert receipt1["outcomes"] == receipt2["outcomes"]
    assert receipt1["accounting"]["eligible_examples"] == receipt2["accounting"]["eligible_examples"]


# ---------------------------------------------------------------------------
# 14. Input reordering leaves output and digest unchanged
# ---------------------------------------------------------------------------


def test_input_reordering_leaves_output_unchanged():
    rows = [
        make_settled_dict(event_id="hockey:1", event_date="2023-08-20"),
        make_settled_dict(event_id="hockey:2", event_date="2023-08-21", winner=1, score1=3, score2=2),
        make_settled_dict(event_id="hockey:3", event_date="2023-08-22"),
    ]
    shuffled = list(rows)
    random.Random(42).shuffle(shuffled)
    assert shuffled != rows

    with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_out:
        code1, receipt1, _s1, examples1, _l1 = run_research(Path(tmp_root), Path(tmp_out), rows)
        assert code1 == 0
    with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_out:
        code2, receipt2, _s2, examples2, _l2 = run_research(Path(tmp_root), Path(tmp_out), shuffled)
        assert code2 == 0

    assert examples1 == examples2
    assert receipt1["input_digest"] == receipt2["input_digest"]
    assert receipt1["examples_digest"] == receipt2["examples_digest"]
    assert receipt1["outcomes"] == receipt2["outcomes"]
    assert receipt1["accounting"] == receipt2["accounting"]


# ---------------------------------------------------------------------------
# 15. Sample is marked research-only
# ---------------------------------------------------------------------------


def test_sample_marked_research_only():
    with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_out:
        root, out = Path(tmp_root), Path(tmp_out)
        code, _receipt, sample_data, _examples, _ledger = run_research(
            root, out, [make_settled_dict(event_id="hockey:L", event_date="2023-08-21")],
        )
        assert code == 0
        assert sample_data["research_only"] is True
        assert sample_data["mode"] == RESEARCH_MODE
        assert "examples" in sample_data
        assert "feature_contract_version" in sample_data
        assert "label_contract_version" in sample_data


# ---------------------------------------------------------------------------
# 16. Gzip examples output is deterministic
# ---------------------------------------------------------------------------


def test_gzip_examples_deterministic():
    rows = [
        make_settled_dict(event_id="hockey:1", event_date="2023-08-20"),
        make_settled_dict(event_id="hockey:2", event_date="2023-08-21"),
    ]
    with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_out:
        _, _, _, examples1, _ = run_research(Path(tmp_root), Path(tmp_out), rows)
    with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_out:
        _, _, _, examples2, _ = run_research(Path(tmp_root), Path(tmp_out), rows)
    assert examples1 == examples2


# ---------------------------------------------------------------------------
# 17. Input ledger bytes remain unchanged
# ---------------------------------------------------------------------------


def test_input_ledger_bytes_unchanged():
    rows = [
        *conflicting_pair_rows(),
        make_settled_dict(event_id="hockey:L", event_date="2023-08-21"),
    ]
    with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_out:
        root, out = Path(tmp_root), Path(tmp_out)
        _code, _receipt, _sample, _examples, ledger = run_research(root, out, rows)
        # Ledger content identical to what we wrote (no repair, no rewrite).
        assert ledger.read_bytes() == json.dumps(rows).encode("utf-8")


# ---------------------------------------------------------------------------
# 18. Existing pipeline behavior and imports remain unchanged
# ---------------------------------------------------------------------------


def test_existing_pipeline_modules_unchanged():
    import slumdog.backfill
    import slumdog.depth_sweep
    import slumdog.forebet
    import slumdog.pipeline
    import slumdog.research
    import slumdog.training

    for module in (
        slumdog.pipeline,
        slumdog.training,
        slumdog.backfill,
        slumdog.depth_sweep,
        slumdog.research,
        slumdog.forebet,
    ):
        assert "research_dataset" not in vars(module)

    from slumdog.pipeline import event_from_dict

    snapshot = event_from_dict(
        {
            "event_id": "basketball:demo:1",
            "sport": "basketball",
            "event_date": "2026-08-20",
            "captured_at": "2026-08-20T06:00:00+00:00",
            "source_url": "https://www.forebet.com/en/basketball/example",
            "participant_1": "Alpha",
            "participant_2": "Beta",
            "probability_1": 0.35,
            "probability_2": 0.65,
            "forebet_pick": 2,
            "odds_1": 2.1,
            "odds_2": 1.4,
            "league": "DEMO",
            "facets": {},
        }
    )
    assert snapshot is not None
    assert snapshot.sport == "basketball"


# ---------------------------------------------------------------------------
# Combination guard + clean-mode status (extra focused coverage)
# ---------------------------------------------------------------------------


def test_research_conflict_report_combination_rejected():
    with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_out:
        root, out = Path(tmp_root), Path(tmp_out)
        write_ledger(root, [make_settled_dict()])
        code = audit_dataset(
            root, out / "receipt.json", out / "sample.json", 5,
            conflict_report_path=out / "conflicts.json",
            research_exclude_conflicts=True,
        )
        assert code == 1


def test_clean_research_mode_is_ready_without_conflict_limitation():
    with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_out:
        root, out = Path(tmp_root), Path(tmp_out)
        code, receipt, _sample, _examples, _ledger = run_research(
            root, out, [make_settled_dict(event_id="hockey:L", event_date="2023-08-21")],
        )
        assert code == 0
        assert receipt["status"] == RESEARCH_STATUS
        assert "CONFLICTING_KEYS_EXCLUDED" not in receipt["limitations"]
        assert "RESEARCH_ONLY" in receipt["limitations"]
        assert receipt["training_allowed"] is False
        assert receipt["production_allowed"] is False


def test_build_research_dataset_direct_rejects_nothing_and_counts_keys():
    """Direct builder path: census over valid events with source tracking."""
    from slumdog.dataset import ValidEventWithSource, _validate_settled_dict

    valid_with_source = [
        ValidEventWithSource(
            event=_validate_settled_dict(make_settled_dict(event_id="hockey:K", event_date="2023-08-20")),
            source_file="f.jsonl.gz",
            source_location="line:1",
        ),
        ValidEventWithSource(
            event=_validate_settled_dict(
                make_settled_dict(event_id="hockey:K", event_date="2023-08-20", winner=2, score1=0, score2=4, period1=(0, 0, 0), period2=(1, 2, 1)),
            ),
            source_file="f.jsonl.gz",
            source_location="line:2",
        ),
        ValidEventWithSource(
            event=_validate_settled_dict(make_settled_dict(event_id="hockey:L", event_date="2023-08-21")),
            source_file="f.jsonl.gz",
            source_location="line:3",
        ),
    ]
    raw_dicts = [make_settled_dict(event_id="hockey:K", event_date="2023-08-20")] * 2 + [
        make_settled_dict(event_id="hockey:L", event_date="2023-08-21"),
    ]
    result = build_research_dataset(
        valid_with_source,
        raw_dicts=raw_dicts,
        raw_input_rows=3,
        schema_excluded_rows=0,
    )
    assert result.ready is True
    assert result.receipt["accounting"]["conflicting_composite_keys_excluded"] == 1
    assert result.receipt["accounting"]["conflicting_rows_excluded"] == 2
    assert result.receipt["accounting"]["eligible_examples"] == 1
    assert len(result.examples) == 1
    assert result.examples[0].event_id == "hockey:L"
