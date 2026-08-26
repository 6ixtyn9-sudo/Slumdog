"""Milestone 6A — research-only dataset construction and readiness.

Research mode only; strict mode is unchanged. build_research_dataset:
lightweight conflict census over ALL valid rows -> exclude every
conflicting composite key -> content/provenance duplicate normalization ->
incremental v2 builder (research_builder.py) -> readiness from bounded
aggregates -> streaming deterministic artifacts (examples gz streamed as
produced, bounded sample = first N emitted, receipt last, safe
no-overwrite finalization).

Feature contract price-free-v2-incremental-valid-history on every emitted
example, sample, and receipt; label contract unchanged (price-free-v1).
NOT authorized here: models, ranking, production, wagering. Must never be
imported by production pipeline modules (pipeline, training, backfill,
depth_sweep, research, forebet, cli).
"""


from __future__ import annotations


import gzip


import hashlib


import json


import os


import sys


import uuid


from collections import Counter


from dataclasses import dataclass


from pathlib import Path


from typing import Any, Iterable


from .contracts import SettledEvent


from .dataset import (
    ALLOWED_FEATURES,
    LABEL_CONTRACT_VERSION,
    ValidEventWithSource,
    PriceFreeUnderdogExample,
    census_conflicts_only,
)


from .research_builder import (
    RESEARCH_FEATURE_CONTRACT_VERSION,
    _ReadinessAgg,
    _collect_prohibited_keys,
    _composite_key,
    _IncrementalBuilder,
    _compute_research_input_digest,
    _normalize_duplicates,
    research_history_eligible,
)


__all__ = [
    "NOT_READY_STATUS",
    "RESEARCH_MODE",
    "RESEARCH_STATUS",
    "ResearchDatasetResult",
    "ResearchExampleEmitter",
    "_collect_prohibited_keys",  # re-exported from research_builder for tests
    "build_research_dataset",
    "run_research_mode",
]




RESEARCH_MODE = "RESEARCH_EXCLUDE_CONFLICTS"


RESEARCH_STATUS = "RESEARCH_DATASET_READY_WITH_LIMITATIONS"


NOT_READY_STATUS = "RESEARCH_DATASET_NOT_READY"


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


class ResearchExampleEmitter:
    """Streams examples to an optional gzip artifact as they are produced.

    Keeps only O(1) state per example: a running SHA-256 over the exact
    emitted bytes, a bounded sample of the first ``sample_size`` examples,
    and a counter. With ``final_path=None`` (receipt-only runs) the same
    line/digest/sample logic applies with no file. The final gzip path only
    appears after close + rename by the caller — a mid-stream failure
    leaves no final artifacts.
    """

    def __init__(self, final_path: Path | None, *, sample_size: int) -> None:
        self.final_path = final_path
        self.sample_size = sample_size
        self.emitted = 0
        self.sample: list[PriceFreeUnderdogExample] = []
        self._hasher = hashlib.sha256()
        self.tmp_path: Path | None = None
        self._fh: Any = None
        self._gz: Any = None
        self.closed = False

    def emit(self, example: PriceFreeUnderdogExample) -> None:
        """Consume one example incrementally (never a sequence)."""
        line = (
            json.dumps(example.to_dict(), sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        self._hasher.update(line)
        if self.final_path is not None:
            if self._gz is None:
                self._open_stream()
            self._gz.write(line)
        if len(self.sample) < self.sample_size:
            self.sample.append(example)
        self.emitted += 1

    def _open_stream(self) -> None:
        assert self.final_path is not None
        self.final_path.parent.mkdir(parents=True, exist_ok=True)
        self.tmp_path = self.final_path.with_name(
            f".{self.final_path.name}.tmp-{uuid.uuid4().hex}"
        )
        self._fh = open(self.tmp_path, "wb")
        # mtime=0 keeps the gzip stream deterministic across runs.
        self._gz = gzip.GzipFile(filename="", mode="wb", fileobj=self._fh, mtime=0)

    def ensure_opened(self) -> None:
        """Open the temp stream even if nothing was emitted (empty gz)."""
        if self.final_path is not None and self._gz is None:
            self._open_stream()

    def close(self) -> None:
        if self._gz is not None:
            self._gz.close()
            self._fh.close()
            self._gz = None
            self._fh = None
        self.closed = True

    def cleanup(self) -> None:
        """Discard a partially written stream (called on failure)."""
        try:
            self.close()
        finally:
            if self.tmp_path is not None:
                if self.tmp_path.exists():
                    self.tmp_path.unlink()
                self.tmp_path = None

    @property
    def digest(self) -> str:
        """Full 64-hex SHA-256 over the exact emitted bytes."""
        return self._hasher.hexdigest()


@dataclass(frozen=True)
class ResearchDatasetResult:
    """Result of research-only v2 dataset construction.

    Bounded by design: no full example list is ever held — only the emitted
    count, the bounded sample, and the digests over the exact emitted bytes.
    """

    receipt: dict[str, Any]
    ready: bool
    errors: tuple[str, ...]
    emitted_examples: int
    sample: tuple[PriceFreeUnderdogExample, ...]
    examples_digest: str
    input_digest: str


def build_research_dataset(
    valid_with_source: Iterable[ValidEventWithSource],
    *,
    raw_input_rows: int,
    schema_excluded_rows: int,
    malformed_empty_participant_rows: int,
    emitter: ResearchExampleEmitter,
) -> ResearchDatasetResult:
    """Build the research-only v2 dataset incrementally (bounded memory).

    Enforced order: lightweight census over ALL valid rows -> exclude every
    conflicting composite key -> content/provenance duplicate normalization
    -> incremental per-sport, per-event-date build with same-date isolation
    -> readiness from bounded aggregates. Examples stream into ``emitter``
    as they are produced and are never held as a full list.
    ``valid_with_source`` may be any iterable; a one-shot iterator is
    consumed exactly once.
    """
    rows = (
        valid_with_source if isinstance(valid_with_source, list) else list(valid_with_source)
    )
    errors: list[str] = []

    # 1. Lightweight conflict census over ALL valid rows (before any collapse).
    conflict_groups, _census_counts = census_conflicts_only(rows)
    conflicting_keys: set[tuple[str, str, str]] = {g.composite_key for g in conflict_groups}
    conflicting_rows_excluded = sum(1 for v in rows if _composite_key(v.event) in conflicting_keys)
    candidates = [v for v in rows if _composite_key(v.event) not in conflicting_keys]

    # 2. Content/provenance-separated duplicate normalization.
    canonical, exact_duplicates_collapsed, norm_errors = _normalize_duplicates(candidates)
    errors.extend(norm_errors)

    # 3. Per-sport canonical rows in build order (event_date, event_id).
    rows_by_sport: dict[str, list[SettledEvent]] = {}
    for ev in canonical:
        rows_by_sport.setdefault(ev.sport, []).append(ev)
    for sport in rows_by_sport:
        rows_by_sport[sport].sort(key=lambda r: (r.event_date, r.event_id))

    input_digest, _sport_digests = _compute_research_input_digest(rows_by_sport)

    # 4. Incremental build: one sport at a time, one complete event-date
    #    batch at a time; state holds only dates < D during D's read phase.
    builder = _IncrementalBuilder()
    global_agg = _ReadinessAgg()
    by_sport_agg: dict[str, _ReadinessAgg] = {sport: _ReadinessAgg() for sport in rows_by_sport}
    for sport in sorted(rows_by_sport):
        sport_rows = rows_by_sport[sport]
        sport_agg = by_sport_agg[sport]
        i, n = 0, len(sport_rows)
        while i < n:
            date = sport_rows[i].event_date
            j = i
            while j < n and sport_rows[j].event_date == date:
                j += 1
            batch = sport_rows[i:j]
            for row in batch:
                builder.process_row(row, conflicting_keys, emitter, global_agg, sport_agg)
            for row in batch:
                if research_history_eligible(row):
                    builder.update_state(row)
            i = j

    # 5. Accounting invariants.
    valid_loaded_rows = raw_input_rows - schema_excluded_rows
    canonical_non_conflicting_rows = len(canonical)
    eligible_examples = emitter.emitted
    builder_excluded_rows = canonical_non_conflicting_rows - eligible_examples
    excluded_sum = sum(builder.excluded.values())
    if excluded_sum != builder_excluded_rows:
        errors.append(
            "internal_mismatch_exclusion_counter:"
            f"counters={excluded_sum} derived={builder_excluded_rows}"
        )
    accounting_balanced = (
        (raw_input_rows == schema_excluded_rows + valid_loaded_rows)
        and (
            valid_loaded_rows
            == exact_duplicates_collapsed
            + conflicting_rows_excluded
            + canonical_non_conflicting_rows
        )
        and (canonical_non_conflicting_rows == eligible_examples + builder_excluded_rows)
        and (builder_excluded_rows >= 0)
        and (malformed_empty_participant_rows <= schema_excluded_rows)
    )
    if not accounting_balanced:
        errors.append("accounting_invariants_unbalanced")

    prohibited_found = sorted(builder.prohibited_found)
    price_passed = not prohibited_found
    if not price_passed:
        errors.append(f"prohibited_example_keys_found:{prohibited_found}")
    if builder.leaked_keys:
        errors.append(f"excluded_key_leakage:{builder.leaked_keys}")

    positive_rate = (builder.positive / eligible_examples) if eligible_examples else None

    # 6. Readiness views from bounded aggregates.
    provenance_global = {
        "present": global_agg.provenance_present,
        "missing": global_agg.provenance_missing,
        "invalid": global_agg.provenance_invalid,
    }
    limitations: list[str] = [LIMITATION_RESEARCH_ONLY]
    if (provenance_global["missing"] + provenance_global["invalid"]) > 0:
        limitations.append(LIMITATION_LEGACY_PROVENANCE_ABSENT)
    if conflicting_keys:
        limitations.append(LIMITATION_CONFLICTING_KEYS_EXCLUDED)
    if schema_excluded_rows > 0:
        limitations.append(LIMITATION_SCHEMA_INVALID_ROWS_EXCLUDED)
    limitations.extend(
        (LIMITATION_SOURCE_CONFLICT_VISIBILITY_UNAVAILABLE, LIMITATION_PERIOD_VALUES_PROHIBITED)
    )
    limitations = [code for code in LIMITATION_CODES if code in limitations]

    ready = accounting_balanced and price_passed and builder.leaked_keys == 0 and not errors

    receipt: dict[str, Any] = {
        "status": RESEARCH_STATUS if ready else NOT_READY_STATUS,
        "mode": RESEARCH_MODE,
        "research_only": True,
        "training_allowed": False,
        "production_allowed": False,
        "research_ready": ready,
        "feature_contract_version": RESEARCH_FEATURE_CONTRACT_VERSION,
        "label_contract_version": LABEL_CONTRACT_VERSION,
        "accounting": {
            "raw_input_rows": raw_input_rows,
            "schema_excluded_rows": schema_excluded_rows,
            "malformed_empty_participant_rows": malformed_empty_participant_rows,
            "valid_loaded_rows": valid_loaded_rows,
            "exact_duplicates_collapsed": exact_duplicates_collapsed,
            "conflicting_composite_keys_excluded": len(conflicting_keys),
            "conflicting_rows_excluded": conflicting_rows_excluded,
            "canonical_non_conflicting_rows": canonical_non_conflicting_rows,
            "eligible_examples": eligible_examples,
            "builder_excluded_rows": builder_excluded_rows,
            "accounting_balanced": accounting_balanced,
        },
        "outcomes": {
            "positive_underdog_wins": builder.positive,
            "negative_favorite_wins": builder.negative_fav,
            "negative_draws": builder.negative_draw,
            "positive_rate": positive_rate,
        },
        "readiness": {
            "global": _readiness_view(
                global_agg, canonical_non_conflicting_rows, include_provenance=True
            ),
            "by_sport": {
                sport: _readiness_view(
                    by_sport_agg[sport], len(rows_by_sport[sport]), include_provenance=False
                )
                for sport in sorted(rows_by_sport)
            },
            "feature_missingness": _feature_missingness_view(global_agg),
            "history_coverage": _history_coverage_view(global_agg),
        },
        "limitations": limitations,
        "price_independence": {
            "example_keys_checked": True,
            "prohibited_example_keys_found": prohibited_found,
            "passed": price_passed,
        },
        "input_digest": input_digest,
        "examples_digest": emitter.digest,
        "errors": errors,
    }

    return ResearchDatasetResult(
        receipt=receipt,
        ready=ready,
        errors=tuple(errors),
        emitted_examples=eligible_examples,
        sample=tuple(emitter.sample),
        examples_digest=emitter.digest,
        input_digest=input_digest,
    )


def _readiness_view(
    agg: _ReadinessAgg, canonical_rows: int, *, include_provenance: bool
) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "eligible_examples": agg.eligible,
        "positive_examples": agg.positive,
        "negative_favorite_wins": agg.negative_fav,
        "negative_draws": agg.negative_draw,
    }
    stats["positive_rate"] = (agg.positive / agg.eligible) if agg.eligible else None
    stats["date_min"] = agg.date_min
    stats["date_max"] = agg.date_max
    counts = list(agg.per_date.values())
    stats["covered_dates"] = len(counts)
    stats["examples_per_date_min"] = min(counts) if counts else 0
    stats["examples_per_date_mean"] = (sum(counts) / len(counts)) if counts else 0.0
    stats["examples_per_date_max"] = max(counts) if counts else 0
    stats["canonical_rows"] = canonical_rows
    if include_provenance:
        stats["provenance"] = {
            "present": agg.provenance_present,
            "missing": agg.provenance_missing,
            "invalid": agg.provenance_invalid,
        }
    return stats


def _feature_missingness_view(agg: _ReadinessAgg) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for field_name in ALLOWED_FEATURES:
        missing = agg.feat_missing.get(field_name, 0)
        present = agg.eligible - missing
        result[field_name] = {
            "present_count": present,
            "missing_count": missing,
            "missing_rate": (missing / agg.eligible) if agg.eligible else 0.0,
        }
    return result


def _history_coverage_view(agg: _ReadinessAgg) -> dict[str, Any]:
    denominator = agg.eligible

    def entry(count: int) -> dict[str, Any]:
        return {"count": count, "rate": (count / denominator) if denominator else 0.0}

    return {
        "eligible_examples": denominator,
        "underdog_prior_history_available": entry(agg.hist_underdog_ok),
        "favorite_prior_history_available": entry(agg.hist_favorite_ok),
        "both_participants_prior_history_available": entry(agg.hist_both_ok),
        "h2h_prior_history_available": entry(agg.hist_h2h_ok),
    }


def _sample_payload(result: ResearchDatasetResult) -> dict[str, Any]:
    """Sample envelope — concise research-only marker, from the bounded
    first-N emitted examples (never reread from the gzip)."""
    return {
        "research_only": True,
        "mode": RESEARCH_MODE,
        "feature_contract_version": RESEARCH_FEATURE_CONTRACT_VERSION,
        "label_contract_version": LABEL_CONTRACT_VERSION,
        "examples": [e.to_dict() for e in result.sample],
    }


def _write_temp_json(final_path: Path, payload: dict[str, Any]) -> Path:
    final_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = final_path.with_name(f".{final_path.name}.tmp-{uuid.uuid4().hex}")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return tmp


def _print_summary(
    receipt_dict: dict[str, Any],
    result: ResearchDatasetResult,
    *,
    receipt_path: Path,
    sample_path: Path,
    examples_path: Path | None,
) -> None:
    acc = receipt_dict["accounting"]
    print(f"Files found: {receipt_dict['files_found']} (empty: {receipt_dict['files_empty']}, unreadable: {receipt_dict['files_unreadable']})")
    print(f"Mode: {RESEARCH_MODE} (research-only, explicit opt-in)")
    print(f"Raw input rows: {acc['raw_input_rows']}")
    print(f"Schema excluded rows: {acc['schema_excluded_rows']} (malformed empty-participant: {acc['malformed_empty_participant_rows']})")
    for reason, count in sorted(receipt_dict["schema_exclusion_reasons"].items()):
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
    if result.ready:
        print(f"Wrote research sample ({len(result.sample)} examples) to {sample_path}")
        if examples_path is not None:
            print(f"Wrote research examples ({result.emitted_examples} rows) to {examples_path}")
    else:
        if examples_path is not None:
            print("Examples not emitted: dataset not ready or inconsistent")
    if result.errors:
        print("Research dataset errors (visible, not silent):")
        for err in result.errors:
            print(f"  {err}")


def run_research_mode(
    *,
    valid_with_source: Iterable[ValidEventWithSource],
    raw_input_rows: int,
    schema_excluded_rows: int,
    malformed_empty_participant_rows: int,
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
    """Orchestrate research-mode artifact emission; returns the exit code.

    Safe no-overwrite finalization:
    0. refuse to run if any final output path already exists (no --force)
    1. stream examples to a temp gzip as they are produced
    2. write the sample temp from the bounded first-N emitted (no reread)
    3. write the receipt temp from bounded aggregates
    4. validate invariants, digests, and counts
    5. rename examples -> sample -> receipt (receipt last)
    6. failure before the receipt rename removes this run's temps and
       finals — no ready receipt, ledgers untouched. An internal
       inconsistency writes a diagnostic receipt only
       (status=RESEARCH_DATASET_NOT_READY, research_ready=false) and never
       coexists with final examples/sample artifacts.
    """
    receipt_path = Path(receipt_path)
    sample_path = Path(sample_path)
    examples_path = Path(examples_path) if examples_path is not None else None

    preexisting = [
        p for p in (receipt_path, sample_path, examples_path) if p is not None and p.exists()
    ]
    if preexisting:
        print(
            "ERROR: research output path(s) already exist; refusing to overwrite: "
            + ", ".join(str(p) for p in preexisting),
            file=sys.stderr,
        )
        return 1

    emitter = ResearchExampleEmitter(examples_path, sample_size=sample_size)
    sample_tmp: Path | None = None
    receipt_tmp: Path | None = None
    finals: list[Path] = []
    try:
        result = build_research_dataset(
            valid_with_source,
            raw_input_rows=raw_input_rows,
            schema_excluded_rows=schema_excluded_rows,
            malformed_empty_participant_rows=malformed_empty_participant_rows,
            emitter=emitter,
        )

        receipt_dict = dict(result.receipt)
        receipt_dict["files_found"] = files_found
        receipt_dict["files_empty"] = files_empty
        receipt_dict["files_unreadable"] = files_unreadable
        receipt_dict["file_errors"] = list(file_errors)
        receipt_dict["schema_exclusion_reasons"] = dict(schema_exclusion_reasons)

        # Validate invariants/digests/counts before any rename.
        validated = (
            receipt_dict["accounting"]["accounting_balanced"] is True
            and receipt_dict["accounting"]["eligible_examples"] == result.emitted_examples
            == emitter.emitted
            and len(result.examples_digest) == 64
            and len(result.input_digest) == 64
            and len(result.sample) == min(sample_size, result.emitted_examples)
        )
        if not result.ready or not validated:
            if validated:
                receipt_dict["errors"] = list(result.receipt["errors"]) + [
                    "finalization_validation_failed"
                ]
            receipt_dict["status"] = NOT_READY_STATUS
            receipt_dict["research_ready"] = False
            # Internal inconsistency: diagnostic receipt only — never
            # coexisting with final examples/sample artifacts.
            emitter.cleanup()
            receipt_tmp = _write_temp_json(receipt_path, receipt_dict)
            os.replace(receipt_tmp, receipt_path)
            receipt_tmp = None
            _print_summary(
                receipt_dict,
                result,
                receipt_path=receipt_path,
                sample_path=sample_path,
                examples_path=examples_path,
            )
            return 1

        if examples_path is not None:
            emitter.ensure_opened()  # guarantees a (possibly empty) temp stream
        sample_tmp = _write_temp_json(sample_path, _sample_payload(result))
        receipt_tmp = _write_temp_json(receipt_path, receipt_dict)

        # (5) rename examples -> sample -> receipt (receipt last)
        if examples_path is not None and emitter.tmp_path is not None:
            os.replace(emitter.tmp_path, examples_path)
            finals.append(examples_path)
            emitter.tmp_path = None
        os.replace(sample_tmp, sample_path)
        sample_tmp = None
        finals.append(sample_path)
        os.replace(receipt_tmp, receipt_path)
        receipt_tmp = None
        finals.append(receipt_path)
        emitter.close()

        _print_summary(
            receipt_dict,
            result,
            receipt_path=receipt_path,
            sample_path=sample_path,
            examples_path=examples_path,
        )
        return 0
    except Exception as exc:
        # I/O or unexpected failure before finalization: remove this run's
        # temps and finals — no ready receipt, ledgers untouched.
        emitter.cleanup()
        for tmp in (sample_tmp, receipt_tmp):
            if tmp is not None and tmp.exists():
                tmp.unlink()
        for final in finals:
            if final.exists():
                final.unlink()
        print(f"ERROR: research artifact emission failed: {exc}", file=sys.stderr)
        return 1


RESEARCH_MODE = "RESEARCH_EXCLUDE_CONFLICTS"


RESEARCH_STATUS = "RESEARCH_DATASET_READY_WITH_LIMITATIONS"


NOT_READY_STATUS = "RESEARCH_DATASET_NOT_READY"


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
