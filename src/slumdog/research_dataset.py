"""Milestone 6A — research-only dataset construction and readiness measurement.

Authorized (Milestone 6A): dataset construction, receipt measurement,
non-model descriptive statistics, research-only artifact generation.

NOT authorized here: fitted models, threshold optimization, calibrated
probabilities, ranking, daily shortlist, shadow picks, production, wagering.
``MODEL_TRAINING_ALLOWED`` stays False (feature_contracts.py).

Data flow (research mode only — strict mode is unchanged):
raw rows
    -> schema validation (dataset.py, strict, no defaults)
    -> conflict census over ALL valid rows (build_conflict_census)
    -> exclude every conflicting composite key — never choose a variant
    -> collapse exact duplicates among remaining non-conflicting rows
    -> strict price-free example builder (build_price_free_examples)
    -> readiness measurement
    -> deterministic research artifacts

A conflicting key must never reach a "pick one representative" normalization
path: exclusion happens at the composite-key level before any collapse or
building step.

This module imports only the standard library and ``slumdog.dataset``. It
reuses the existing schema adapters, identity, label, history, census, and
builder implementations. It must never be imported by production pipeline
modules (pipeline, training, backfill, depth_sweep, research, forebet, cli).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .dataset import (
    ALLOWED_FEATURES,
    FEATURE_CONTRACT_VERSION,
    LABEL_CONTRACT_VERSION,
    PROHIBITED_KEYS,
    ValidEventWithSource,
    PriceFreeUnderdogExample,
    build_conflict_census,
    build_price_free_examples,
)

RESEARCH_MODE = "RESEARCH_EXCLUDE_CONFLICTS"
RESEARCH_STATUS = "RESEARCH_DATASET_READY_WITH_LIMITATIONS"
NOT_READY_STATUS = "RESEARCH_DATASET_NOT_READY"

# Machine-readable limitation codes (canonical order).
LIMITATION_RESEARCH_ONLY = "RESEARCH_ONLY"
LIMITATION_LEGACY_PROVENANCE_ABSENT = "LEGACY_PROVENANCE_ABSENT"
LIMITATION_CONFLICTING_KEYS_EXCLUDED = "CONFLICTING_KEYS_EXCLUDED"
LIMITATION_SCHEMA_INVALID_ROWS_EXCLUDED = "SCHEMA_INVALID_ROWS_EXCLUDED"
LIMITATION_SOURCE_CONFLICT_VISIBILITY_UNAVAILABLE = "SOURCE_CONFLICT_VISIBILITY_UNAVAILABLE"
LIMITATION_PERIOD_VALUES_PROHIBITED = "PERIOD_VALUES_PROHIBITED"
LIMITATION_CODES = (
    LIMITATION_RESEARCH_ONLY,
    LIMITATION_LEGACY_PROVENANCE_ABSENT,
    LIMITATION_CONFLICTING_KEYS_EXCLUDED,
    LIMITATION_SCHEMA_INVALID_ROWS_EXCLUDED,
    LIMITATION_SOURCE_CONFLICT_VISIBILITY_UNAVAILABLE,
    LIMITATION_PERIOD_VALUES_PROHIBITED,
)


def _composite_key(event) -> tuple[str, str, str]:
    return (event.sport, event.event_id, event.event_date)


def _collect_prohibited_keys(obj: Any, found: set[str]) -> None:
    """Recursively collect exact prohibited key names from an example payload.

    Exact key matching only — values are never scanned, so receipt prose that
    legitimately mentions e.g. ROI is not flagged.
    """
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(key, str) and key in PROHIBITED_KEYS:
                found.add(key)
            _collect_prohibited_keys(value, found)
    elif isinstance(obj, list):
        for item in obj:
            _collect_prohibited_keys(item, found)


def _examples_digest(examples: list[PriceFreeUnderdogExample]) -> str:
    lines = [
        json.dumps(e.to_dict(), sort_keys=True, separators=(",", ":"))
        for e in examples
    ]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16]


def _examples_per_date_stats(examples: list[PriceFreeUnderdogExample]) -> dict[str, Any]:
    per_date: Counter = Counter(e.event_date for e in examples)
    counts = list(per_date.values())
    return {
        "covered_dates": len(counts),
        "examples_per_date_min": min(counts) if counts else 0,
        "examples_per_date_mean": (sum(counts) / len(counts)) if counts else 0.0,
        "examples_per_date_max": max(counts) if counts else 0,
    }


def _readiness_stats(examples: list[PriceFreeUnderdogExample]) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "eligible_examples": len(examples),
        "positive_examples": sum(1 for e in examples if e.label == 1),
        "negative_favorite_wins": sum(1 for e in examples if e.label == 0),
        "negative_draws": 0,  # draws are labeled 0 alongside favorite wins in examples; see outcomes
    }
    stats["positive_rate"] = (
        (stats["positive_examples"] / len(examples)) if examples else None
    )
    stats["date_min"] = min((e.event_date for e in examples), default=None)
    stats["date_max"] = max((e.event_date for e in examples), default=None)
    stats.update(_examples_per_date_stats(examples))
    return stats


def _feature_missingness(examples: list[PriceFreeUnderdogExample]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for field in ALLOWED_FEATURES:
        missing = sum(1 for e in examples if e.features.get(field) is None)
        present = len(examples) - missing
        result[field] = {
            "present_count": present,
            "missing_count": missing,
            "missing_rate": (missing / len(examples)) if examples else 0.0,
        }
    return result


def _history_coverage(examples: list[PriceFreeUnderdogExample]) -> dict[str, Any]:
    def available_count(field: str) -> int:
        return sum(1 for e in examples if (e.features.get(field) or 0) > 0)

    denominator = len(examples)
    def entry(count: int) -> dict[str, Any]:
        return {"count": count, "rate": (count / denominator) if denominator else 0.0}

    underdog = available_count("underdog_prior_games")
    favorite = available_count("favorite_prior_games")
    return {
        "eligible_examples": denominator,
        "underdog_prior_history_available": entry(underdog),
        "favorite_prior_history_available": entry(favorite),
        "both_participants_prior_history_available": entry(
            sum(
                1
                for e in examples
                if (e.features.get("underdog_prior_games") or 0) > 0
                and (e.features.get("favorite_prior_games") or 0) > 0
            )
        ),
        "h2h_prior_history_available": entry(available_count("h2h_prior_games")),
    }


@dataclass(frozen=True)
class ResearchDatasetResult:
    """Result of research-only dataset construction (no artifacts written here)."""

    examples: list[PriceFreeUnderdogExample]
    receipt: dict[str, Any]
    ready: bool
    errors: tuple[str, ...]


def build_research_dataset(
    valid_with_source: list[ValidEventWithSource],
    *,
    raw_dicts: list[dict[str, Any]],
    raw_input_rows: int,
    schema_excluded_rows: int,
) -> ResearchDatasetResult:
    """Build the research-only dataset with explicit conflict-key exclusion.

    Required order (enforced here, verified by tests):
    census over ALL valid rows -> exclude conflicting keys -> collapse exact
    duplicates -> strict builder -> readiness.

    ``valid_with_source`` must be the schema-validated events with source
    tracking (produced by the audit loaders). ``raw_dicts`` is the raw input
    used only to count malformed empty-participant rows (a schema-exclusion
    subset).
    """
    errors: list[str] = []

    # 1. Conflict census over ALL valid rows (before any collapse).
    conflict_groups, census_receipt, _debug = build_conflict_census(valid_with_source)
    conflicting_keys: set[tuple[str, str, str]] = {g.composite_key for g in conflict_groups}
    conflicting_composite_keys_excluded = len(conflicting_keys)
    conflicting_rows_excluded = sum(
        1 for v in valid_with_source if _composite_key(v.event) in conflicting_keys
    )

    # 2. Exclude every row belonging to a conflicting composite key.
    non_conflicting = [
        v for v in valid_with_source if _composite_key(v.event) not in conflicting_keys
    ]
    non_conflicting_events = [v.event for v in non_conflicting]

    # 3. Strict builder — collapses exact duplicates, cannot raise now because
    #    conflicting keys were removed; a raise is a defensive failure.
    try:
        examples, builder_receipt = build_price_free_examples(non_conflicting_events)
    except ValueError as exc:
        errors.append(f"builder_failed_after_conflict_exclusion:{exc}")
        examples = []
        builder_receipt = None

    if builder_receipt is not None:
        # Cross-check: census and builder must agree on non-conflicting
        # duplicate collapses under identical merge policy.
        if census_receipt.exact_duplicates_collapsed != builder_receipt.exact_duplicates_collapsed:
            errors.append(
                "internal_mismatch_exact_duplicates:"
                f"census={census_receipt.exact_duplicates_collapsed} "
                f"builder={builder_receipt.exact_duplicates_collapsed}"
            )

    exact_duplicates_collapsed = (
        builder_receipt.exact_duplicates_collapsed if builder_receipt is not None else 0
    )
    canonical_non_conflicting_rows = (
        builder_receipt.canonical_input_rows if builder_receipt is not None else 0
    )
    eligible_examples = builder_receipt.eligible_examples if builder_receipt is not None else 0
    builder_excluded_rows = builder_receipt.builder_excluded_rows if builder_receipt is not None else 0

    # 4. Malformed empty-participant rows — a schema-exclusion subset.
    malformed_empty_participant_rows = sum(
        1
        for d in raw_dicts
        if isinstance(d, dict)
        and str(d.get("participant_1") or "") == ""
        and str(d.get("participant_2") or "") == ""
    )

    # 5. Accounting invariants.
    valid_loaded_rows = raw_input_rows - schema_excluded_rows
    accounting_balanced = (
        (raw_input_rows == schema_excluded_rows + valid_loaded_rows)
        and (
            valid_loaded_rows
            == exact_duplicates_collapsed
            + conflicting_rows_excluded
            + canonical_non_conflicting_rows
        )
        and (canonical_non_conflicting_rows == eligible_examples + builder_excluded_rows)
        and (malformed_empty_participant_rows <= schema_excluded_rows)
    )
    if not accounting_balanced:
        errors.append("accounting_invariants_unbalanced")

    # 6. No example may come from an excluded key.
    leaked_keys = [
        (e.sport, e.event_id, e.event_date)
        for e in examples
        if (e.sport, e.event_id, e.event_date) in conflicting_keys
    ]
    if leaked_keys:
        errors.append(f"excluded_key_leakage:{len(leaked_keys)}")

    # 7. Price independence — exact prohibited key scan over emitted examples.
    found: set[str] = set()
    for e in examples:
        _collect_prohibited_keys(e.to_dict(), found)
    prohibited_found = sorted(found)
    price_passed = not prohibited_found
    if not price_passed:
        errors.append(f"prohibited_example_keys_found:{prohibited_found}")

    # 8. Outcomes.
    positive = builder_receipt.positive_underdog_wins if builder_receipt is not None else 0
    negative_fav = builder_receipt.negative_favorite_wins if builder_receipt is not None else 0
    negative_draw = builder_receipt.negative_draws if builder_receipt is not None else 0
    positive_rate = builder_receipt.positive_rate if builder_receipt is not None else None

    # 9. Readiness.
    global_stats = _readiness_stats(examples)
    global_stats["canonical_rows"] = canonical_non_conflicting_rows
    global_stats["provenance"] = {
        "present": builder_receipt.provenance_present if builder_receipt is not None else 0,
        "missing": builder_receipt.provenance_missing if builder_receipt is not None else 0,
        "invalid": builder_receipt.provenance_invalid if builder_receipt is not None else 0,
    }

    unique_non_conflicting_keys = {_composite_key(v.event) for v in non_conflicting}
    canonical_keys_by_sport = Counter(key[0] for key in unique_non_conflicting_keys)

    by_sport: dict[str, dict[str, Any]] = {}
    sport_keys = sorted(set(canonical_keys_by_sport) | {e.sport for e in examples})
    for sport in sport_keys:
        sport_examples = [e for e in examples if e.sport == sport]
        sport_stats = _readiness_stats(sport_examples)
        sport_stats["canonical_rows"] = canonical_keys_by_sport.get(sport, 0)
        by_sport[sport] = sport_stats

    # 10. Limitations.
    limitations: list[str] = [LIMITATION_RESEARCH_ONLY]
    provenance = global_stats["provenance"]
    if (provenance["missing"] + provenance["invalid"]) > 0:
        limitations.append(LIMITATION_LEGACY_PROVENANCE_ABSENT)
    if conflicting_composite_keys_excluded > 0:
        limitations.append(LIMITATION_CONFLICTING_KEYS_EXCLUDED)
    if schema_excluded_rows > 0:
        limitations.append(LIMITATION_SCHEMA_INVALID_ROWS_EXCLUDED)
    limitations.extend(
        [
            LIMITATION_SOURCE_CONFLICT_VISIBILITY_UNAVAILABLE,
            LIMITATION_PERIOD_VALUES_PROHIBITED,
        ]
    )
    limitations = [code for code in LIMITATION_CODES if code in limitations]

    # 11. Digests.
    input_digest = builder_receipt.input_digest if builder_receipt is not None else "not-built"
    examples_digest = _examples_digest(examples)

    ready = (
        accounting_balanced
        and price_passed
        and not leaked_keys
        and not errors
    )

    receipt: dict[str, Any] = {
        "status": RESEARCH_STATUS if ready else NOT_READY_STATUS,
        "mode": RESEARCH_MODE,
        "research_only": True,
        "training_allowed": False,
        "production_allowed": False,
        "feature_contract_version": FEATURE_CONTRACT_VERSION,
        "label_contract_version": LABEL_CONTRACT_VERSION,
        "accounting": {
            "raw_input_rows": raw_input_rows,
            "schema_excluded_rows": schema_excluded_rows,
            "malformed_empty_participant_rows": malformed_empty_participant_rows,
            "valid_loaded_rows": valid_loaded_rows,
            "exact_duplicates_collapsed": exact_duplicates_collapsed,
            "conflicting_composite_keys_excluded": conflicting_composite_keys_excluded,
            "conflicting_rows_excluded": conflicting_rows_excluded,
            "canonical_non_conflicting_rows": canonical_non_conflicting_rows,
            "eligible_examples": eligible_examples,
            "builder_excluded_rows": builder_excluded_rows,
            "accounting_balanced": accounting_balanced,
        },
        "outcomes": {
            "positive_underdog_wins": positive,
            "negative_favorite_wins": negative_fav,
            "negative_draws": negative_draw,
            "positive_rate": positive_rate,
        },
        "readiness": {
            "global": global_stats,
            "by_sport": by_sport,
            "feature_missingness": _feature_missingness(examples),
            "history_coverage": _history_coverage(examples),
        },
        "limitations": limitations,
        "price_independence": {
            "example_keys_checked": True,
            "prohibited_example_keys_found": prohibited_found,
            "passed": price_passed,
        },
        "input_digest": input_digest,
        "examples_digest": examples_digest,
        "errors": errors,
    }

    return ResearchDatasetResult(
        examples=examples,
        receipt=receipt,
        ready=ready,
        errors=tuple(errors),
    )


def run_research_mode(
    *,
    valid_with_source: list[ValidEventWithSource],
    raw_dicts: list[dict[str, Any]],
    raw_input_rows: int,
    schema_excluded_rows: int,
    schema_exclusion_reasons: Counter,
    receipt_path: Path,
    sample_path: Path,
    examples_path: Path | None,
    sample_size: int,
    files_found: int,
    files_empty: int,
    files_unreadable: int,
    file_errors: list[str],
) -> int:
    """Orchestrate research-mode artifact emission; returns process exit code.

    Writes only under /tmp (enforced by the caller in dataset_audit). Never
    modifies source ledgers. Examples are emitted only when the build is
    internally consistent.
    """
    result = build_research_dataset(
        valid_with_source,
        raw_dicts=raw_dicts,
        raw_input_rows=raw_input_rows,
        schema_excluded_rows=schema_excluded_rows,
    )

    receipt_dict = dict(result.receipt)
    receipt_dict["files_found"] = files_found
    receipt_dict["files_empty"] = files_empty
    receipt_dict["files_unreadable"] = files_unreadable
    receipt_dict["file_errors"] = list(file_errors)
    receipt_dict["schema_exclusion_reasons"] = dict(schema_exclusion_reasons)

    emit_examples = result.ready and examples_path is not None

    try:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(receipt_dict, indent=2, sort_keys=True))

        # Sample envelope — concise research-only marker, no authorization prose.
        sample_payload: dict[str, Any] = {
            "research_only": True,
            "mode": RESEARCH_MODE,
            "feature_contract_version": FEATURE_CONTRACT_VERSION,
            "label_contract_version": LABEL_CONTRACT_VERSION,
            "examples": [e.to_dict() for e in result.examples[:sample_size]],
        }
        sample_path.parent.mkdir(parents=True, exist_ok=True)
        sample_path.write_text(json.dumps(sample_payload, indent=2, sort_keys=True))

        if emit_examples and examples_path is not None:
            lines = [
                json.dumps(e.to_dict(), sort_keys=True, separators=(",", ":"))
                for e in result.examples
            ]
            payload = "\n".join(lines) + "\n"
            # mtime=0 keeps the gzip stream deterministic across runs.
            gz_bytes = gzip.compress(payload.encode("utf-8"), mtime=0)
            examples_path.parent.mkdir(parents=True, exist_ok=True)
            examples_path.write_bytes(gz_bytes)
    except Exception as exc:
        print(f"ERROR: research artifact emission failed: {exc}", file=sys.stderr)
        return 1

    acc = receipt_dict["accounting"]
    print(f"Files found: {files_found} (empty: {files_empty}, unreadable: {files_unreadable})")
    print(f"Mode: {RESEARCH_MODE} (research-only, explicit opt-in)")
    print(f"Raw input rows: {acc['raw_input_rows']}")
    print(f"Schema excluded rows: {acc['schema_excluded_rows']} (malformed empty-participant: {acc['malformed_empty_participant_rows']})")
    for reason, count in sorted(schema_exclusion_reasons.items()):
        print(f"  schema_excluded {reason}: {count}")
    print(f"Valid loaded rows: {acc['valid_loaded_rows']}")
    print(f"Conflicting composite keys excluded: {acc['conflicting_composite_keys_excluded']}")
    print(f"Conflicting rows excluded: {acc['conflicting_rows_excluded']}")
    print(f"Exact duplicates collapsed: {acc['exact_duplicates_collapsed']}")
    print(f"Canonical non-conflicting rows: {acc['canonical_non_conflicting_rows']}")
    print(f"Eligible examples: {acc['eligible_examples']}")
    print(f"Builder excluded rows: {acc['builder_excluded_rows']}")
    print(f"Accounting balanced: {acc['accounting_balanced']}")
    print(f"Positive underdog wins: {receipt_dict['outcomes']['positive_underdog_wins']}")
    print(f"Negative favorite wins: {receipt_dict['outcomes']['negative_favorite_wins']}")
    print(f"Negative draws: {receipt_dict['outcomes']['negative_draws']}")
    print(f"Positive rate: {receipt_dict['outcomes']['positive_rate']}")
    print(f"Provenance present/missing/invalid: {receipt_dict['readiness']['global']['provenance']}")
    print(f"Price independence passed: {receipt_dict['price_independence']['passed']}")
    print(f"Limitations: {receipt_dict['limitations']}")
    print(f"Input digest: {receipt_dict['input_digest']}, examples digest: {receipt_dict['examples_digest']}")
    print(f"Wrote receipt to {receipt_path}")
    print(f"Wrote research sample ({min(sample_size, len(result.examples))} examples) to {sample_path}")
    if emit_examples and examples_path is not None:
        print(f"Wrote research examples ({len(result.examples)} rows) to {examples_path}")
    elif examples_path is not None:
        print("Examples not emitted: dataset not ready or inconsistent")
    if result.errors:
        print("Research dataset errors (visible, not silent):")
        for err in result.errors:
            print(f"  {err}")
    return 0 if result.ready else 1
