"""Repository-owned, tested audit entry point for price-free dataset — Milestone 4E + 4F.

Requirements (from task):
- No network requests
- Modify no ledgers
- Write only under /tmp
- Use tested schema adapters
- Print summary counts, not all examples
- Exit nonzero on unreadable files, unknown schema, or conflicting duplicates
- Make malformed-row counts visible
- Never default a missing winner
- Never silently skip rows
- If no supported ledger files, exit successfully with NO_SUPPORTED_INPUT_FILES status
- If files exist but cannot be parsed, must fail
- Milestone 4F: --conflict-report census mode collects all conflicts, status DATA_CONFLICTS, nonzero exit, receipt emitted with conflict counts, conflict report under /tmp containing only composite_key, source_file, line/index, conflicting fields, classification, raw_sha256, source_url, no full records, examples not emitted

Usage:
python -m slumdog.dataset_audit --root data --receipt /tmp/slumdog_price_free/receipt.json --sample /tmp/slumdog_price_free/examples_sample.json --sample-size 5
python -m slumdog.dataset_audit --root data --conflict-report /tmp/slumdog_price_free/conflicts.json --receipt /tmp/slumdog_price_free/receipt.json --sample /tmp/slumdog_price_free/examples_sample.json --sample-size 5
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .dataset import (
    SchemaLoadResult,
    ValidEventWithSource,
    build_conflict_census,
    build_dataset_with_raw_accounting,
    load_settled_events_from_dicts,
    _validate_settled_dict,
)


@dataclass
class RawWithSource:
    raw_dict: dict[str, Any]
    source_file: str
    source_location: str  # e.g., line:62 or index:5


def _load_json_file_with_source(path: Path) -> tuple[list[RawWithSource], list[str]]:
    """Load settled_history.json — list of dicts with source tracking."""
    file_errors: list[str] = []
    raws: list[RawWithSource] = []
    try:
        text = path.read_text(encoding="utf-8")
        payload = json.loads(text)
    except Exception as e:
        file_errors.append(f"{path}:JSON_PARSE_ERROR:{type(e).__name__}:{e}")
        raise

    if not isinstance(payload, list):
        file_errors.append(f"{path}:SCHEMA_NOT_A_LIST")
        raise ValueError(f"{path} expected list, got {type(payload).__name__}")

    for idx, item in enumerate(payload):
        loc = f"index:{idx}"
        if not isinstance(item, dict):
            # Will be counted as schema exclusion later, but preserve source
            raws.append(RawWithSource(raw_dict={"__not_a_dict__": True, "__index__": idx}, source_file=str(path), source_location=loc))
        else:
            raws.append(RawWithSource(raw_dict=item, source_file=str(path), source_location=loc))

    return raws, file_errors


def _load_jsonl_gz_file_with_source(path: Path) -> tuple[list[RawWithSource], list[str]]:
    """Load history_*.jsonl.gz with source tracking."""
    file_errors: list[str] = []
    raws: list[RawWithSource] = []
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                stripped = line.strip()
                if not stripped:
                    continue
                loc = f"line:{line_no}"
                try:
                    d = json.loads(stripped)
                    if not isinstance(d, dict):
                        file_errors.append(f"{path}:{line_no}:NOT_A_DICT")
                        raws.append(RawWithSource(raw_dict={"__not_a_dict__": True}, source_file=str(path), source_location=loc))
                        continue
                    raws.append(RawWithSource(raw_dict=d, source_file=str(path), source_location=loc))
                except Exception as e:
                    file_errors.append(f"{path}:{line_no}:JSON_LINE_ERROR:{type(e).__name__}:{e}")
                    # Preserve malformed as sentinel that will fail schema validation, but with source
                    raws.append(RawWithSource(raw_dict={"__malformed_line__": line_no, "__error__": str(e)}, source_file=str(path), source_location=loc))
    except Exception as e:
        file_errors.append(f"{path}:GZIP_READ_ERROR:{type(e).__name__}:{e}")
        raise

    return raws, file_errors


def audit_dataset(
    root: Path,
    receipt_path: Path,
    sample_path: Path,
    sample_size: int = 5,
    conflict_report_path: Path | None = None,
) -> int:
    root = Path(root)
    receipt_path = Path(receipt_path)
    sample_path = Path(sample_path)
    conflict_path = Path(conflict_report_path) if conflict_report_path is not None else None

    # Safety checks for /tmp
    if not str(receipt_path).startswith("/tmp"):
        print(f"ERROR: receipt_path must be under /tmp for safety, got {receipt_path}", file=sys.stderr)
        return 1
    if not str(sample_path).startswith("/tmp"):
        print(f"ERROR: sample_path must be under /tmp for safety, got {sample_path}", file=sys.stderr)
        return 1
    if conflict_path is not None and not str(conflict_path).startswith("/tmp"):
        print(f"ERROR: conflict_report_path must be under /tmp for safety, got {conflict_path}", file=sys.stderr)
        return 1

    # Discover supported ledger files
    candidates_with_source: list[RawWithSource] = []
    all_file_errors: list[str] = []
    files_found = 0
    files_empty = 0
    files_unreadable = 0

    def try_load_interim(p: Path):
        nonlocal files_found, files_empty, files_unreadable
        if not p.exists():
            return
        files_found += 1
        try:
            raws, file_errors = _load_json_file_with_source(p)
            all_file_errors.extend(file_errors)
            if len(raws) == 0:
                files_empty += 1
            candidates_with_source.extend(raws)
        except Exception as e:
            files_unreadable += 1
            all_file_errors.append(f"{p}:FAILED:{e}")
            raise

    def try_load_gz(p: Path):
        nonlocal files_found, files_empty, files_unreadable
        if not p.exists():
            return
        files_found += 1
        try:
            raws, file_errors = _load_jsonl_gz_file_with_source(p)
            all_file_errors.extend(file_errors)
            if len(raws) == 0:
                files_empty += 1
            candidates_with_source.extend(raws)
        except Exception as e:
            files_unreadable += 1
            all_file_errors.append(f"{p}:FAILED:{e}")
            raise

    search_roots = [root]
    if (root / "data").exists():
        search_roots.append(root / "data")
    if root == Path(".") or str(root) == ".":
        search_roots.append(Path("data"))

    interim_candidates: list[Path] = []
    gz_candidates: list[Path] = []

    for sr in search_roots:
        ip = sr / "interim" / "settled_history.json"
        if ip not in interim_candidates:
            interim_candidates.append(ip)
        rp = sr / "reports"
        if rp.exists():
            for gz in sorted(rp.glob("history_*.jsonl.gz")):
                if gz not in gz_candidates:
                    gz_candidates.append(gz)
            # Also support uncompressed history_*.json for diagnostic (not part of original spec but safe)
            for js in sorted(rp.glob("history_*.json")):
                # Only include if corresponding gz not present? Include anyway but avoid double counting same logical file
                # For diagnostic metadata shape inspection, we allow json files as well
                if js not in interim_candidates and js.suffix == ".json":
                    # Treat json files similarly to interim (list)?? But history_*.json might be dict container, not list.
                    # We will handle later via safe diagnostic, not as primary ledger.
                    # So do not auto-load json here unless explicitly requested? For now skip to keep original behavior.
                    pass

    interim_candidates = sorted(set(interim_candidates))
    gz_candidates = sorted(set(gz_candidates))

    # Safe diagnostic: inspect data/reports/history_hockey.json metadata shape if present (container type, top-level keys)
    # This is read-only, no ledger modification, no network, for understanding legacy JSON shape vs jsonl.gz
    for sr in search_roots:
        hj = sr / "reports" / "history_hockey.json"
        if hj.exists():
            try:
                # Read first 2MB to avoid huge files, inspect container type
                text = hj.read_text(encoding="utf-8")[:2_000_000]
                # Try to parse as JSON but handle large file — only peek
                try:
                    payload = json.loads(text) if len(text) < 1_900_000 else None
                    # If truncated, we still report file size and first char
                    if payload is None:
                        print(f"DIAGNOSTIC history_hockey.json exists: {hj} size {hj.stat().st_size} bytes, first char {text[:1]!r} (too large for full parse in diagnostic)")
                    else:
                        ctype = type(payload).__name__
                        if isinstance(payload, dict):
                            keys = sorted(payload.keys())[:50]
                            print(f"DIAGNOSTIC history_hockey.json: container dict, top-level keys {keys}, file {hj} size {hj.stat().st_size}")
                        elif isinstance(payload, list):
                            first_keys = sorted(payload[0].keys())[:50] if payload and isinstance(payload[0], dict) else []
                            print(f"DIAGNOSTIC history_hockey.json: container list len {len(payload)}, first element keys {first_keys}, file {hj} size {hj.stat().st_size}")
                        else:
                            print(f"DIAGNOSTIC history_hockey.json: container {ctype}, file {hj} size {hj.stat().st_size}")
                except Exception as je:
                    print(f"DIAGNOSTIC history_hockey.json exists but JSON parse failed (expected for large/truncated): {hj} size {hj.stat().st_size} error {je}")
            except Exception as e:
                print(f"DIAGNOSTIC history_hockey.json inspection failed: {hj} error {e}", file=sys.stderr)
            break  # only first found

    try:
        for ip in interim_candidates:
            try_load_interim(ip)
        for gz in gz_candidates:
            try_load_gz(gz)
    except Exception as e:
        print(f"ERROR: unreadable ledger file: {e}", file=sys.stderr)
        for fe in all_file_errors:
            print(f"  file_error: {fe}", file=sys.stderr)
        return 1

    if files_found == 0:
        print("NO_SUPPORTED_INPUT_FILES: no files matching data/interim/settled_history.json or data/reports/history_*.jsonl.gz found")
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps({
            "status": "NO_SUPPORTED_INPUT_FILES",
            "files_found": 0,
            "files_empty": 0,
            "files_unreadable": 0,
            "raw_input_rows": 0,
            "schema_excluded_rows": 0,
            "valid_loaded_rows": 0,
            "exact_duplicates_collapsed": 0,
            "canonical_input_rows": 0,
            "eligible_examples": 0,
            "builder_excluded_rows": 0,
            "input_rows": 0,
            "positive_underdog_wins": 0,
            "negative_favorite_wins": 0,
            "negative_draws": 0,
            "excluded_void": 0,
            "excluded_source_conflict": 0,
            "excluded_equal_probability": 0,
            "excluded_missing_probability": 0,
            "excluded_non_finite_probability": 0,
            "excluded_out_of_range_probability": 0,
            "excluded_unknown_sport": 0,
            "excluded_unexpected_two_way_draw": 0,
            "excluded_invalid_winner": 0,
            "excluded_other": 0,
            "provenance_present": 0,
            "provenance_missing": 0,
            "provenance_invalid": 0,
            "positive_rate": None,
            "canonical_date_min": None,
            "canonical_date_max": None,
            "eligible_date_min": None,
            "eligible_date_max": None,
            "date_min": None,
            "date_max": None,
            "feature_contract_version": "price-free-v1-minimal-2026-08-24",
            "label_contract_version": "price-free-v1",
            "input_digest": "no-input",
            "per_sport": {},
            "conflicting_composite_keys": 0,
            "conflicting_rows": 0,
            "conflicts_by_sport": {},
            "conflicts_by_field": {},
            "conflicts_with_valid_raw_sha256": 0,
            "conflicts_without_valid_raw_sha256": 0,
            "file_errors": [],
            "schema_exclusion_reasons": {},
        }, indent=2, sort_keys=True))
        if str(sample_path).startswith("/tmp"):
            sample_path.parent.mkdir(parents=True, exist_ok=True)
            sample_path.write_text(json.dumps([], indent=2, sort_keys=True))
        if conflict_path is not None:
            conflict_path.parent.mkdir(parents=True, exist_ok=True)
            conflict_path.write_text(json.dumps([], indent=2, sort_keys=True))
        return 0

    # Schema validation with source tracking
    raw_input_rows = len(candidates_with_source)
    valid_with_source: list[ValidEventWithSource] = []
    schema_excluded_rows = 0
    schema_reasons: Counter = Counter()

    for rws in candidates_with_source:
        d = rws.raw_dict
        if not isinstance(d, dict):
            schema_excluded_rows += 1
            schema_reasons["SCHEMA_NOT_A_DICT"] += 1
            continue
        try:
            ev = _validate_settled_dict(d)
            valid_with_source.append(ValidEventWithSource(event=ev, source_file=rws.source_file, source_location=rws.source_location))
        except ValueError as ve:
            schema_excluded_rows += 1
            msg = str(ve)
            reason = msg.split(":")[0] if ":" in msg else msg
            schema_reasons[reason] += 1
        except Exception as e:
            schema_excluded_rows += 1
            schema_reasons[f"SCHEMA_UNEXPECTED_{type(e).__name__}"] += 1

    unknown_schema = schema_reasons.get("UNKNOWN_SCHEMA_VERSION", 0)
    if unknown_schema > 0:
        print(f"ERROR: unknown schema version detected: {unknown_schema} rows", file=sys.stderr)
        for k, v in schema_reasons.items():
            if "UNKNOWN_SCHEMA" in k:
                print(f"  {k}: {v}", file=sys.stderr)
        return 1

    # If conflict-report mode, do census
    if conflict_path is not None:
        # Build conflict census — must continue after first conflict
        conflict_groups, census_receipt, debug_info = build_conflict_census(valid_with_source)

        # Fill raw accounting into receipt
        # census_receipt currently has raw_input_rows=0 placeholder, we override
        # We need to create a new receipt with correct raw/schema counts
        # But keep other fields from census_receipt
        final_receipt_dict = census_receipt.to_dict()
        final_receipt_dict["raw_input_rows"] = raw_input_rows
        final_receipt_dict["schema_excluded_rows"] = schema_excluded_rows
        # valid_loaded_rows already correct from census (len valid_with_source)
        # Ensure input_rows alias consistent
        # Add file accounting
        final_receipt_dict["files_found"] = files_found
        final_receipt_dict["files_empty"] = files_empty
        final_receipt_dict["files_unreadable"] = files_unreadable
        final_receipt_dict["file_errors"] = all_file_errors
        final_receipt_dict["schema_exclusion_reasons"] = dict(schema_reasons)

        # Determine status
        if census_receipt.conflicting_composite_keys > 0:
            final_receipt_dict["status"] = "DATA_CONFLICTS"
            # Write receipt
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            receipt_path.write_text(json.dumps(final_receipt_dict, indent=2, sort_keys=True))

            # Build conflict report — compact only, no full records
            conflict_report_entries = []
            for g in conflict_groups:
                # composite_key as list for JSON
                entry = {
                    "composite_key": list(g.composite_key),
                    "sport": g.sport,
                    "conflicting_fields": g.conflicting_fields,
                    "classification": g.classification,
                    "raw_sha256_values": g.raw_sha256_values,
                    "source_url_values": g.source_url_values,
                    "source_entries": g.source_entries,
                }
                conflict_report_entries.append(entry)

            # Deterministic order already sorted in build_conflict_census
            conflict_path.parent.mkdir(parents=True, exist_ok=True)
            conflict_path.write_text(json.dumps(conflict_report_entries, indent=2, sort_keys=True))

            # Print summary
            print(f"Files found: {files_found} (empty: {files_empty}, unreadable: {files_unreadable})")
            print(f"Raw input rows: {raw_input_rows}")
            print(f"Schema excluded rows: {schema_excluded_rows}")
            for reason, count in sorted(schema_reasons.items()):
                print(f"  schema_excluded {reason}: {count}")
            print(f"Valid loaded rows: {final_receipt_dict['valid_loaded_rows']}")
            print(f"Exact duplicates collapsed: {final_receipt_dict['exact_duplicates_collapsed']}")
            print(f"Canonical input rows (non-conflicting): {final_receipt_dict['canonical_input_rows']}")
            print(f"Conflicting composite keys: {final_receipt_dict['conflicting_composite_keys']}")
            print(f"Conflicting rows: {final_receipt_dict['conflicting_rows']}")
            print(f"Conflicts by sport: {final_receipt_dict['conflicts_by_sport']}")
            print(f"Conflicts by field: {final_receipt_dict['conflicts_by_field']}")
            print(f"Conflicts with valid raw_sha256: {final_receipt_dict['conflicts_with_valid_raw_sha256']}")
            print(f"Conflicts without valid raw_sha256: {final_receipt_dict['conflicts_without_valid_raw_sha256']}")
            print(f"Status: DATA_CONFLICTS — {final_receipt_dict['conflicting_composite_keys']} keys, {final_receipt_dict['conflicting_rows']} rows")
            print(f"Wrote receipt to {receipt_path}")
            print(f"Wrote conflict report ({len(conflict_report_entries)} groups) to {conflict_path}")
            print("Examples not emitted due to DATA_CONFLICTS")

            if all_file_errors:
                print("File errors / malformed rows (visible, not silent):", file=sys.stderr)
                for fe in all_file_errors[:20]:
                    print(f"  {fe}", file=sys.stderr)
                if len(all_file_errors) > 20:
                    print(f"  ... and {len(all_file_errors)-20} more", file=sys.stderr)

            return 1
        else:
            # No conflicts — proceed as normal success, but also write empty conflict report
            # Build examples from canonical (valid_with_source)
            # For simplicity, use build_dataset_with_raw_accounting on raw dicts (original path) to get same receipt as normal
            # But we already have census receipt with no conflicts, which should match normal builder
            # We'll reuse census receipt for consistency
            final_receipt_dict["status"] = "OK"

            # Build examples from valid events (non-conflicting)
            # Use valid_with_source events list
            valid_events = [v.event for v in valid_with_source]
            # Re-use builder that fails loudly (should not fail now)
            try:
                from .dataset import build_price_free_examples
                examples, builder_receipt = build_price_free_examples(valid_events)
            except ValueError as ve:
                if "conflicting" in str(ve):
                    # Should not happen because we already checked no conflicts
                    print(f"ERROR: unexpected conflicting duplicates in no-conflict path: {ve}", file=sys.stderr)
                    return 1
                else:
                    print(f"ERROR: builder failed: {ve}", file=sys.stderr)
                    return 1

            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            # Use builder receipt but with raw accounting filled
            # For consistency with normal mode, use build_dataset_with_raw_accounting result
            raw_dicts = [rws.raw_dict for rws in candidates_with_source]
            examples_full, receipt_full, _ = build_dataset_with_raw_accounting(raw_dicts)
            receipt_full_dict = receipt_full.to_dict()
            receipt_full_dict["files_found"] = files_found
            receipt_full_dict["files_empty"] = files_empty
            receipt_full_dict["files_unreadable"] = files_unreadable
            receipt_full_dict["file_errors"] = all_file_errors
            receipt_full_dict["schema_exclusion_reasons"] = dict(schema_reasons)
            receipt_full_dict["status"] = "OK"
            receipt_full_dict["conflicting_composite_keys"] = 0
            receipt_full_dict["conflicting_rows"] = 0
            receipt_full_dict["conflicts_by_sport"] = {}
            receipt_full_dict["conflicts_by_field"] = {}
            receipt_full_dict["conflicts_with_valid_raw_sha256"] = 0
            receipt_full_dict["conflicts_without_valid_raw_sha256"] = 0
            receipt_path.write_text(json.dumps(receipt_full_dict, indent=2, sort_keys=True))

            sample_path.parent.mkdir(parents=True, exist_ok=True)
            sample_examples = examples_full[:sample_size]
            sample_path.write_text(json.dumps([e.to_dict() for e in sample_examples], indent=2, sort_keys=True))

            conflict_path.parent.mkdir(parents=True, exist_ok=True)
            conflict_path.write_text(json.dumps([], indent=2, sort_keys=True))

            print(f"Files found: {files_found} (empty: {files_empty}, unreadable: {files_unreadable})")
            print(f"Raw input rows: {receipt_full.raw_input_rows}")
            print(f"Schema excluded rows: {receipt_full.schema_excluded_rows}")
            print(f"Valid loaded rows: {receipt_full.valid_loaded_rows}")
            print(f"Canonical input rows: {receipt_full.canonical_input_rows}")
            print(f"Eligible examples: {receipt_full.eligible_examples}")
            print(f"Status: OK — no conflicts")
            print(f"Wrote receipt to {receipt_path}")
            print(f"Wrote sample ({len(sample_examples)} examples) to {sample_path}")
            print(f"Wrote empty conflict report to {conflict_path}")

            return 0

    # Normal mode (no conflict-report): fail loudly on conflicts
    raw_dicts = [rws.raw_dict for rws in candidates_with_source]
    try:
        examples, receipt, _ = build_dataset_with_raw_accounting(raw_dicts)
    except ValueError as ve:
        if "conflicting" in str(ve):
            print(f"ERROR: conflicting duplicates: {ve}", file=sys.stderr)
            return 1
        else:
            print(f"ERROR: builder failed: {ve}", file=sys.stderr)
            return 1
    except Exception as e:
        print(f"ERROR: builder unexpected failure: {type(e).__name__}:{e}", file=sys.stderr)
        return 1

    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    sample_path.parent.mkdir(parents=True, exist_ok=True)

    receipt_dict = receipt.to_dict()
    receipt_dict["files_found"] = files_found
    receipt_dict["files_empty"] = files_empty
    receipt_dict["files_unreadable"] = files_unreadable
    receipt_dict["file_errors"] = all_file_errors
    receipt_dict["schema_exclusion_reasons"] = dict(schema_reasons)
    receipt_dict["status"] = "OK"

    receipt_path.write_text(json.dumps(receipt_dict, indent=2, sort_keys=True))

    sample_examples = examples[:sample_size]
    sample_path.write_text(json.dumps([e.to_dict() for e in sample_examples], indent=2, sort_keys=True))

    print(f"Files found: {files_found} (empty: {files_empty}, unreadable: {files_unreadable})")
    print(f"Raw input rows: {receipt.raw_input_rows}")
    print(f"Schema excluded rows: {receipt.schema_excluded_rows}")
    for reason, count in sorted(schema_reasons.items()):
        print(f"  schema_excluded {reason}: {count}")
    print(f"Valid loaded rows: {receipt.valid_loaded_rows}")
    print(f"Exact duplicates collapsed: {receipt.exact_duplicates_collapsed}")
    print(f"Canonical input rows: {receipt.canonical_input_rows}")
    print(f"Eligible examples: {receipt.eligible_examples}")
    print(f"Builder excluded rows: {receipt.builder_excluded_rows}")
    print(f"  excluded_void: {receipt.excluded_void}")
    print(f"  excluded_equal_probability: {receipt.excluded_equal_probability}")
    print(f"  excluded_missing_probability: {receipt.excluded_missing_probability}")
    print(f"  excluded_non_finite_probability: {receipt.excluded_non_finite_probability}")
    print(f"  excluded_out_of_range_probability: {receipt.excluded_out_of_range_probability}")
    print(f"  excluded_unknown_sport: {receipt.excluded_unknown_sport}")
    print(f"  excluded_unexpected_two_way_draw: {receipt.excluded_unexpected_two_way_draw}")
    print(f"  excluded_invalid_winner: {receipt.excluded_invalid_winner}")
    print(f"  excluded_other: {receipt.excluded_other}")
    print(f"Positive underdog wins: {receipt.positive_underdog_wins}")
    print(f"Negative favorite wins: {receipt.negative_favorite_wins}")
    print(f"Negative draws: {receipt.negative_draws}")
    print(f"Positive rate: {receipt.positive_rate}")
    print(f"Canonical date min/max: {receipt.canonical_date_min} / {receipt.canonical_date_max}")
    print(f"Eligible date min/max: {receipt.eligible_date_min} / {receipt.eligible_date_max}")
    print(f"Provenance present/missing/invalid: {receipt.provenance_present} / {receipt.provenance_missing} / {receipt.provenance_invalid}")
    print(f"Input digest: {receipt.input_digest}")
    print(f"Feature contract: {receipt.feature_contract_version}, Label contract: {receipt.label_contract_version}")
    print(f"Wrote receipt to {receipt_path}")
    print(f"Wrote sample ({len(sample_examples)} examples) to {sample_path}")

    if all_file_errors:
        print("File errors / malformed rows (visible, not silent):", file=sys.stderr)
        for fe in all_file_errors[:20]:
            print(f"  {fe}", file=sys.stderr)
        if len(all_file_errors) > 20:
            print(f"  ... and {len(all_file_errors)-20} more", file=sys.stderr)

    return 0


# ---------------------------------------------------------------------------
# Backward compatibility wrappers for older tests (Milestone 4E)
# ---------------------------------------------------------------------------

def _load_json_file(path: Path) -> tuple[list[dict[str, Any]], list[str], Counter]:
    """Legacy wrapper: returns (dicts, file_errors, Counter) as older tests expect."""
    raws, file_errors = _load_json_file_with_source(Path(path))
    dicts = [r.raw_dict for r in raws]
    return dicts, file_errors, Counter()


def _load_jsonl_gz_file(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Legacy wrapper: returns (dicts, file_errors) as older tests expect."""
    raws, file_errors = _load_jsonl_gz_file_with_source(Path(path))
    dicts = [r.raw_dict for r in raws]
    return dicts, file_errors


def main():
    parser = argparse.ArgumentParser(description="Price-free dataset audit — no network, writes only under /tmp")
    parser.add_argument("--root", default="data", help="root containing interim/ and reports/ (default: data)")
    parser.add_argument("--receipt", default="/tmp/slumdog_price_free/receipt.json", help="receipt output path (must be under /tmp)")
    parser.add_argument("--sample", default="/tmp/slumdog_price_free/examples_sample.json", help="sample output path (must be under /tmp)")
    parser.add_argument("--sample-size", type=int, default=5, help="number of examples in sample")
    parser.add_argument("--conflict-report", default=None, help="conflict report output path (must be under /tmp) — enables census mode")
    args = parser.parse_args()

    code = audit_dataset(Path(args.root), Path(args.receipt), Path(args.sample), args.sample_size, Path(args.conflict_report) if args.conflict_report else None)
    sys.exit(code)


if __name__ == "__main__":
    main()
