"""Repository-owned, tested audit entry point for price-free dataset — Milestone 4E.

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

Usage:
python -m slumdog.dataset_audit --root data --receipt /tmp/slumdog_price_free/receipt.json --sample /tmp/slumdog_price_free/examples_sample.json --sample-size 5
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from .dataset import (
    SchemaLoadResult,
    build_dataset_with_raw_accounting,
    load_settled_events_from_dicts,
)


def _load_json_file(path: Path) -> tuple[list[dict[str, Any]], list[str], Counter]:
    """Load settled_history.json — list of dicts. Returns (dicts, file_errors, schema_reasons)."""
    file_errors: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
        payload = json.loads(text)
    except Exception as e:
        # Corrupt JSON fails loudly
        file_errors.append(f"{path}:JSON_PARSE_ERROR:{type(e).__name__}:{e}")
        raise

    if not isinstance(payload, list):
        file_errors.append(f"{path}:SCHEMA_NOT_A_LIST")
        raise ValueError(f"{path} expected list, got {type(payload).__name__}")

    return payload, file_errors, Counter()


def _load_jsonl_gz_file(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Load history_*.jsonl.gz — each line json dict. Returns (dicts, file_errors)."""
    file_errors: list[str] = []
    dicts: list[dict[str, Any]] = []
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    if not isinstance(d, dict):
                        file_errors.append(f"{path}:{line_no}:NOT_A_DICT")
                        continue
                    dicts.append(d)
                except Exception as e:
                    # Malformed row counted, not silently skipped, but file still readable
                    file_errors.append(f"{path}:{line_no}:JSON_LINE_ERROR:{type(e).__name__}:{e}")
                    # Continue counting malformed rows via file_errors, but also count as schema exclusion later
                    # We add a sentinel malformed dict that will fail schema validation
                    dicts.append({"__malformed_line__": line_no, "__error__": str(e)})
    except Exception as e:
        # Corrupt gzip fails loudly
        file_errors.append(f"{path}:GZIP_READ_ERROR:{type(e).__name__}:{e}")
        raise

    return dicts, file_errors


def audit_dataset(
    root: Path,
    receipt_path: Path,
    sample_path: Path,
    sample_size: int = 5,
) -> int:
    """Audit dataset from supported ledger files.

    Returns exit code: 0 success, 2 NO_SUPPORTED_INPUT_FILES, 1 failure (unreadable, unknown schema, conflicting duplicates)
    """
    root = Path(root)
    receipt_path = Path(receipt_path)
    sample_path = Path(sample_path)

    # Discover supported ledger files
    # Documented schemas:
    # - data/interim/settled_history.json (list of SettledEvent dicts) — produced by settlement.py
    # - data/reports/history_*.jsonl.gz (jsonl gz, each line SettledEvent dict + facets raw_sha256) — produced by backfill.py
    # If one format obsolete or unsupported, say explicitly instead of guessing aliases.

    candidates_raw: list[dict[str, Any]] = []
    all_file_errors: list[str] = []
    files_found = 0
    files_empty = 0
    files_unreadable = 0

    # Helper to attempt load
    def try_load_interim(p: Path):
        nonlocal files_found, files_empty, files_unreadable
        if not p.exists():
            return
        files_found += 1
        try:
            payload, file_errors, _ = _load_json_file(p)
            all_file_errors.extend(file_errors)
            if len(payload) == 0:
                files_empty += 1
            candidates_raw.extend(payload)
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
            dicts, file_errors = _load_jsonl_gz_file(p)
            all_file_errors.extend(file_errors)
            if len(dicts) == 0:
                files_empty += 1
            candidates_raw.extend(dicts)
        except Exception as e:
            files_unreadable += 1
            all_file_errors.append(f"{p}:FAILED:{e}")
            raise

    # Search locations
    # Root may be "data" or "." — try both patterns
    search_roots = [root]
    if (root / "data").exists():
        search_roots.append(root / "data")
    # Also if root is ".", check data/...
    if root == Path(".") or str(root) == ".":
        search_roots.append(Path("data"))

    interim_candidates = []
    gz_candidates = []

    for sr in search_roots:
        ip = sr / "interim" / "settled_history.json"
        if ip not in interim_candidates:
            interim_candidates.append(ip)
        # history_*.jsonl.gz in reports
        rp = sr / "reports"
        if rp.exists():
            for gz in sorted(rp.glob("history_*.jsonl.gz")):
                if gz not in gz_candidates:
                    gz_candidates.append(gz)

    # Deduplicate search paths
    interim_candidates = sorted(set(interim_candidates))
    gz_candidates = sorted(set(gz_candidates))

    # Load
    try:
        for ip in interim_candidates:
            try_load_interim(ip)
        for gz in gz_candidates:
            try_load_gz(gz)
    except Exception as e:
        # Unreadable/corrupt file must fail loudly
        print(f"ERROR: unreadable ledger file: {e}", file=sys.stderr)
        for fe in all_file_errors:
            print(f"  file_error: {fe}", file=sys.stderr)
        return 1

    if files_found == 0:
        # No supported ledger files — exit successfully with explicit status (0)
        print("NO_SUPPORTED_INPUT_FILES: no files matching data/interim/settled_history.json or data/reports/history_*.jsonl.gz found")
        if not str(receipt_path).startswith("/tmp"):
            print(f"ERROR: receipt_path must be under /tmp for safety, got {receipt_path}", file=sys.stderr)
            return 1
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        # Full receipt structure with zeros for consistent reporting
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
            "file_errors": [],
            "schema_exclusion_reasons": {},
        }, indent=2, sort_keys=True))
        # Also write empty sample
        if str(sample_path).startswith("/tmp"):
            sample_path.parent.mkdir(parents=True, exist_ok=True)
            sample_path.write_text(json.dumps([], indent=2, sort_keys=True))
        return 0

    # Now schema validation with explicit counting
    schema_result: SchemaLoadResult = load_settled_events_from_dicts(candidates_raw)

    # If unknown schema version found, fail loudly
    unknown_schema = schema_result.schema_exclusion_reasons.get("UNKNOWN_SCHEMA_VERSION", 0)
    if unknown_schema > 0:
        print(f"ERROR: unknown schema version detected: {unknown_schema} rows", file=sys.stderr)
        for k, v in schema_result.schema_exclusion_reasons.items():
            if "UNKNOWN_SCHEMA" in k:
                print(f"  {k}: {v}", file=sys.stderr)
        return 1

    # Build dataset with raw accounting
    try:
        examples, receipt, _ = build_dataset_with_raw_accounting(candidates_raw)
    except ValueError as ve:
        # Conflicting duplicates (including provenance conflicts) must fail loudly
        if "conflicting" in str(ve):
            print(f"ERROR: conflicting duplicates: {ve}", file=sys.stderr)
            return 1
        else:
            print(f"ERROR: builder failed: {ve}", file=sys.stderr)
            return 1
    except Exception as e:
        print(f"ERROR: builder unexpected failure: {type(e).__name__}:{e}", file=sys.stderr)
        return 1

    # Write receipt and sample only under /tmp
    if not str(receipt_path).startswith("/tmp"):
        print(f"ERROR: receipt_path must be under /tmp for safety, got {receipt_path}", file=sys.stderr)
        return 1
    if not str(sample_path).startswith("/tmp"):
        print(f"ERROR: sample_path must be under /tmp for safety, got {sample_path}", file=sys.stderr)
        return 1

    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    sample_path.parent.mkdir(parents=True, exist_ok=True)

    receipt_dict = receipt.to_dict()
    # Add file-level accounting
    receipt_dict["files_found"] = files_found
    receipt_dict["files_empty"] = files_empty
    receipt_dict["files_unreadable"] = files_unreadable
    receipt_dict["file_errors"] = all_file_errors
    receipt_dict["schema_exclusion_reasons"] = dict(schema_result.schema_exclusion_reasons)

    receipt_path.write_text(json.dumps(receipt_dict, indent=2, sort_keys=True))

    sample_examples = examples[:sample_size]
    sample_path.write_text(json.dumps([e.to_dict() for e in sample_examples], indent=2, sort_keys=True))

    # Print summary counts, not all examples
    print(f"Files found: {files_found} (empty: {files_empty}, unreadable: {files_unreadable})")
    print(f"Raw input rows: {receipt.raw_input_rows}")
    print(f"Schema excluded rows: {receipt.schema_excluded_rows}")
    for reason, count in sorted(schema_result.schema_exclusion_reasons.items()):
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

    # Exit nonzero if there were file errors that are unreadable? Already handled.
    # If files found but all rows schema excluded, that's not necessarily failure — it's an all-excluded dataset, which is valid
    # But if files unreadable, we already returned 1

    return 0


def main():
    parser = argparse.ArgumentParser(description="Price-free dataset audit — no network, writes only under /tmp")
    parser.add_argument("--root", default="data", help="root containing interim/ and reports/ (default: data)")
    parser.add_argument("--receipt", default="/tmp/slumdog_price_free/receipt.json", help="receipt output path (must be under /tmp)")
    parser.add_argument("--sample", default="/tmp/slumdog_price_free/examples_sample.json", help="sample output path (must be under /tmp)")
    parser.add_argument("--sample-size", type=int, default=5, help="number of examples in sample")
    args = parser.parse_args()

    code = audit_dataset(Path(args.root), Path(args.receipt), Path(args.sample), args.sample_size)
    sys.exit(code)


if __name__ == "__main__":
    main()
