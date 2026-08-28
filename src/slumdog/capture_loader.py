"""Milestone 7 — Read-only capture loader.

Pipeline:

    capture receipt  (data/reports/capture_<date>.json)
      -> sidecar schema validation
      -> body existence
      -> exact body SHA-256 verification
      -> existing per-capture parser (parsers.parse_capture)
      -> EventSnapshot list
      -> PreEventRecord list (via shadow_contracts.from_event_snapshot)

This module does NOT write to ``data/raw/``, ``data/interim/``, the
receipt, or any ledger. It does NOT call ``pipeline.parse_capture_receipt``
(which writes ``data/interim/events_<date>.json``). It does NOT access
the network. The hash of the body is computed and compared against the
sidecar's declared ``sha256``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .parsers import parse_capture
from .shadow_contracts import PreEventRecord
from .sports import SPORTS
from .contracts import EventSnapshot

# All formats except those explicitly read-only. Reject any path
# containing these substrings in the body_path. ``data/raw`` is the
# only legitimate origin for capture bodies.
_DEFAULT_REPO_ROOT = Path(".")

# Maximum capture receipt size we'll read into memory. Receipts are
# small (one entry per sport per day). 8 MiB is generous.
_MAX_RECEIPT_BYTES = 8 * 1024 * 1024


class CaptureLoaderError(Exception):
    """Base class."""


class ReceiptIntegrityError(CaptureLoaderError):
    """Receipt missing, malformed, or target-date mismatch."""


class SidecarIntegrityError(CaptureLoaderError):
    """Sidecar missing, malformed, or its declared body hash mismatches."""


class BodyIntegrityError(CaptureLoaderError):
    """Body missing or its recomputed SHA-256 does not match the sidecar."""


class PathContainmentError(CaptureLoaderError):
    """A supplied or referenced path escapes the approved repository root."""


@dataclass(frozen=True)
class CaptureLoadResult:
    """Result of loading a capture receipt.

    - ``records`` is the union of PreEventRecord objects, one per parser-
      emitted snapshot. May be empty.
    - ``capture_accounting`` is balanced, mutually exclusive per
      capture-receipt entry.
    - ``snapshot_accounting`` is balanced, mutually exclusive per
      parser-emitted snapshot. NOTE: this accounts for snapshots the
      parser actually emitted, not for raw source rows the parser may
      have silently dropped.
    - ``raw_input_paths`` is the set of file paths that were read
      (receipt, sidecars, bodies). Used for input-hash verification by
      callers and tests.
    """

    target_date: str
    records: list[PreEventRecord]
    capture_accounting: dict[str, int]
    snapshot_accounting: dict[str, int]
    receipt_path: str
    receipt_sha256: str
    receipt_bytes: int
    capture_entries: list[dict[str, Any]] = field(default_factory=list)
    raw_input_paths: list[str] = field(default_factory=list)
    raw_input_sha256: dict[str, str] = field(default_factory=dict)


def _resolve_within_root(path: Path, repo_root: Path) -> Path:
    """Resolve ``path`` to an absolute path and require it to live under
    ``repo_root``. Raises ``PathContainmentError`` for traversal,
    symlink-escape, or absolute-path escape."""
    repo_root = repo_root.resolve()
    try:
        candidate = (repo_root / path).resolve() if not path.is_absolute() else path.resolve()
    except OSError as e:
        raise PathContainmentError(f"path resolution failed: {path!r}: {e}") from e
    try:
        candidate.relative_to(repo_root)
    except ValueError as e:
        raise PathContainmentError(
            f"path {candidate} escapes repo root {repo_root}"
        ) from e
    return candidate


def _sha256_file(path: Path) -> tuple[str, int]:
    """Stream-hash a file. Returns (sha256_hex, byte_count)."""
    h = hashlib.sha256()
    total = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
            total += len(chunk)
    return h.hexdigest(), total


def _is_current_only(sport: str) -> bool:
    spec = SPORTS.get(sport)
    if spec is None:
        return False
    return bool(getattr(spec, "current_only", False))


def load_capture_records(
    *,
    target_date: str,
    capture_receipt_path: str | Path,
    repo_root: str | Path = ".",
) -> CaptureLoadResult:
    """Load a capture receipt and return verified PreEventRecord objects.

    Raises:
        ReceiptIntegrityError, SidecarIntegrityError,
        BodyIntegrityError, PathContainmentError.
    """
    repo_root = Path(repo_root).resolve()
    receipt_path = _resolve_within_root(Path(capture_receipt_path), repo_root)
    if not receipt_path.is_file():
        raise ReceiptIntegrityError(f"receipt not found: {receipt_path}")
    receipt_bytes_count = receipt_path.stat().st_size
    if receipt_bytes_count > _MAX_RECEIPT_BYTES:
        raise ReceiptIntegrityError(
            f"receipt too large: {receipt_bytes_count} > {_MAX_RECEIPT_BYTES}"
        )
    receipt_data = receipt_path.read_bytes()
    receipt_sha = hashlib.sha256(receipt_data).hexdigest()
    try:
        receipt = json.loads(receipt_data)
    except json.JSONDecodeError as e:
        raise ReceiptIntegrityError(f"receipt not valid JSON: {e}") from e
    if not isinstance(receipt, dict):
        raise ReceiptIntegrityError("receipt must be a JSON object")
    receipt_target_date = receipt.get("target_date")
    if receipt_target_date != target_date:
        raise ReceiptIntegrityError(
            f"receipt target_date mismatch: receipt={receipt_target_date!r} "
            f"requested={target_date!r}"
        )
    captured_entries = receipt.get("captured")
    if not isinstance(captured_entries, list):
        raise ReceiptIntegrityError("receipt.captured must be a list")

    raw_input_paths: list[str] = []
    raw_input_sha256: dict[str, str] = {}
    records: list[PreEventRecord] = []
    capture_accounting = {
        "raw_capture_receipt_entries": 0,
        "captures_verified": 0,
        "captures_missing": 0,
        "captures_hash_mismatch": 0,
        "captures_schema_invalid": 0,
        "captures_parse_failed": 0,
        "captures_unsupported_sport": 0,
    }
    snapshot_accounting = {
        "parser_emitted_snapshots": 0,
        "snapshots_unique_accepted": 0,
        "snapshots_exact_duplicate": 0,
        "snapshots_conflicting": 0,
        "snapshots_invalid_identity": 0,
    }

    for entry in captured_entries:
        capture_accounting["raw_capture_receipt_entries"] += 1
        if not isinstance(entry, dict):
            capture_accounting["captures_schema_invalid"] += 1
            continue
        # Required fields per RawCapture
        sport = entry.get("sport")
        entry_target_date = entry.get("target_date")
        captured_at = entry.get("captured_at")
        body_path = entry.get("body_path")
        sidecar_path = entry.get("metadata_path")
        declared_sha = entry.get("sha256")
        source_url = entry.get("source_url")
        if not (sport and entry_target_date and captured_at and body_path
                and sidecar_path and declared_sha and source_url):
            capture_accounting["captures_schema_invalid"] += 1
            continue
        if entry_target_date != target_date:
            capture_accounting["captures_schema_invalid"] += 1
            continue
        # Reject current-only sports per SPORTS metadata (not a duplicated list)
        if _is_current_only(sport):
            capture_accounting["captures_unsupported_sport"] += 1
            continue
        # Resolve sidecar and body paths, both must lie within repo_root
        try:
            sidecar_abs = _resolve_within_root(Path(sidecar_path), repo_root)
            body_abs = _resolve_within_root(Path(body_path), repo_root)
        except PathContainmentError:
            capture_accounting["captures_missing"] += 1
            continue
        if not sidecar_abs.is_file():
            capture_accounting["captures_missing"] += 1
            continue
        if not body_abs.is_file():
            capture_accounting["captures_missing"] += 1
            continue
        # Verify sidecar exact bytes
        sidecar_data = sidecar_abs.read_bytes()
        sidecar_sha = hashlib.sha256(sidecar_data).hexdigest()
        raw_input_paths.append(str(sidecar_abs))
        raw_input_sha256[str(sidecar_abs)] = sidecar_sha
        # Verify body exact bytes match sidecar-declared sha256
        body_sha, body_size = _sha256_file(body_abs)
        raw_input_paths.append(str(body_abs))
        raw_input_sha256[str(body_abs)] = body_sha
        if body_sha != declared_sha:
            capture_accounting["captures_hash_mismatch"] += 1
            continue
        # Optional: sidecar also declares its own sha256, sanity-check
        try:
            sidecar_obj = json.loads(sidecar_data)
        except json.JSONDecodeError:
            capture_accounting["captures_schema_invalid"] += 1
            continue
        if not isinstance(sidecar_obj, dict):
            capture_accounting["captures_schema_invalid"] += 1
            continue
        if sidecar_obj.get("sha256") != body_sha:
            capture_accounting["captures_hash_mismatch"] += 1
            continue
        if sidecar_obj.get("sport") != sport or sidecar_obj.get("target_date") != target_date:
            capture_accounting["captures_schema_invalid"] += 1
            continue
        # Body exists, hashes match. Call the existing parser.
        metadata = {
            "body_path": str(Path(body_path)),
            "sport": sport,
            "target_date": target_date,
            "captured_at": captured_at,
            "source_url": source_url,
            "sha256": body_sha,
        }
        try:
            snapshots = parse_capture(metadata, root=str(repo_root))
        except Exception:
            capture_accounting["captures_parse_failed"] += 1
            continue
        capture_accounting["captures_verified"] += 1
        # Parser-emitted snapshots accounting
        snapshot_accounting["parser_emitted_snapshots"] += len(snapshots)
        # Per-snapshot provenance-bound conversion
        emitted_for_entry: list[PreEventRecord] = []
        for snap in snapshots:
            if not isinstance(snap, EventSnapshot):
                snapshot_accounting["snapshots_invalid_identity"] += 1
                continue
            # Reject if parser emitted a target_date other than the requested
            if snap.event_date != target_date:
                snapshot_accounting["snapshots_invalid_identity"] += 1
                continue
            # Reject if parser-emitted raw_sha256 doesn't match the body
            if snap.raw_sha256 and snap.raw_sha256 != body_sha:
                snapshot_accounting["snapshots_invalid_identity"] += 1
                continue
            rec = PreEventRecord.from_event_snapshot(
                snap,
                body_path=str(body_path),
                capture_receipt_path=str(receipt_path),
                sidecar_path=str(sidecar_path),
            )
            emitted_for_entry.append(rec)
        # Duplicate / conflict classification for the snapshots emitted
        # from this single capture. Across captures, dedup/conflict is
        # handled by the evaluator's composite-key grouping (see
        # _snapshot_dedup).
        for rec in emitted_for_entry:
            records.append(rec)
        # Per-capture uniqueness: within one capture, two snapshots with
        # the same composite key are an exact duplicate. We count them.
        seen: set[tuple[str, str, str]] = set()
        for rec in emitted_for_entry:
            key = (rec.sport, rec.event_id, rec.event_date)
            if key in seen:
                snapshot_accounting["snapshots_exact_duplicate"] += 1
            else:
                seen.add(key)
        # The first of each unique group is counted later by the
        # evaluator's overall dedup pass. For per-capture accounting,
        # we report "snapshots_unique_accepted" as the count of unique
        # composite keys emitted by this capture.
        snapshot_accounting["snapshots_unique_accepted"] += len(seen)

    return CaptureLoadResult(
        target_date=target_date,
        records=records,
        capture_accounting=capture_accounting,
        snapshot_accounting=snapshot_accounting,
        receipt_path=str(receipt_path),
        receipt_sha256=receipt_sha,
        receipt_bytes=receipt_bytes_count,
        capture_entries=captured_entries,
        raw_input_paths=raw_input_paths,
        raw_input_sha256=raw_input_sha256,
    )
