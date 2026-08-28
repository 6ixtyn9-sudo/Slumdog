"""Milestone 7 — Read-only valid-history loader.

Pipeline:

    configured history paths
      -> exact-byte SHA-256 per input (streamed, bounded)
      -> bounded streaming or load
      -> schema validation (via dataset._validate_settled_dict)
      -> v2 validity filtering
      -> duplicate/conflict handling (via dataset._census_grouping)
      -> list[SettledEvent]
      -> HistoryIndex
      -> balanced history_manifest_section

Supported formats (the only ones the repository actually uses):

- ``data/interim/settled_history.json`` — JSON list of dicts. Bounded
  load: file size must not exceed the configured limit; the limit is
  recorded in the manifest.
- ``data/reports/history_<sport>.jsonl.gz`` — gzipped JSONL. Streamed
  one line at a time.

This module does NOT mutate any input file. It does NOT call settlement
append. It does NOT call the network. It does NOT use odds. It does NOT
emit labels. It does NOT write to any history output path.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .contracts import SettledEvent
from .history import HistoryIndex
from .research_builder import RESEARCH_FEATURE_CONTRACT_VERSION


# Default bound for the JSON-list interim ledger. The real
# settled_history.json in this repo is 654k rows (~150 MB). 1 GiB
# is a generous bound for synthetic and small real datasets. Larger
# inputs MUST use the gzipped history_<sport>.jsonl.gz files.
DEFAULT_MAX_INTERIM_BYTES = 256 * 1024 * 1024  # 256 MiB
# History memory bound (owner item 4): tightened from 1 GiB to 256
# MiB. The bound is the maximum in-memory size of a non-gz interim
# ledger (``settled_history.json``) that ``load_valid_history`` will
# accept. Gzipped JSONL inputs are streamed (``_STREAM_CHUNK``) and
# have NO whole-file in-memory bound: only one row is held at a
# time during parse. Rationale for 256 MiB: a settled ledger with
# ~150-200 bytes/row holds ~1.3-1.7M rows, which is far more than
# any single-sport history that the M7 decision process needs (we
# require 5 priors + 1 H2H per team; the cap is therefore well above
# the realistic working set). The bound is exposed via the
# ``--history-max-interim-bytes`` CLI flag (and the
# ``max_interim_bytes`` kwarg on ``load_valid_history``) so tests
# can pass a smaller value; the loader does NOT silently disable
# the cap.

# Stream chunk size for hashing and reading.
_STREAM_CHUNK = 1024 * 1024


class HistoryLoaderError(Exception):
    """Base class."""


class HistoryPathError(HistoryLoaderError):
    """A history path is missing, escapes repo root, or has wrong format."""


class HistorySizeLimitError(HistoryLoaderError):
    """A history file exceeds the documented limit."""


@dataclass(frozen=True)
class HistoryLoadResult:
    """Result of loading and validating prior-history files.

    - ``settled`` is the unique, valid, non-conflicting set of prior
      settled events. Empty list is valid.
    - ``history_index`` is a ``HistoryIndex`` built from ``settled``.
    - ``manifest_section`` is the manifest-ready dict. Every key is
      populated from real observations; no placeholders.
    """

    target_date: str
    cutoff_date: str
    history_index: HistoryIndex
    settled: list[SettledEvent]
    manifest_section: dict[str, Any] = field(default_factory=dict)


def _resolve_within_root(path: Path, repo_root: Path) -> Path:
    repo_root = repo_root.resolve()
    try:
        candidate = (repo_root / path).resolve() if not path.is_absolute() else path.resolve()
    except OSError as e:
        raise HistoryPathError(f"path resolution failed: {path!r}: {e}") from e
    try:
        candidate.relative_to(repo_root)
    except ValueError as e:
        raise HistoryPathError(
            f"path {candidate} escapes repo root {repo_root}"
        ) from e
    return candidate


def _validate_settled_dict(d: Any) -> SettledEvent:
    """Strict v2 settled-event validator. Raises ValueError with a
    ``SCHEMA_*`` reason prefix on invalid input; returns the
    ``SettledEvent`` on success.

    Imported lazily to avoid loading the entire dataset module at
    import time. The function is pure (no I/O, no side effects).
    """
    from .dataset import _validate_settled_dict as _impl
    return _impl(d)


def _emit_canonical(records: list[SettledEvent]) -> list[SettledEvent]:
    """Per-composite-key dedup that returns the canonical event per group
    plus a count of exact duplicates and conflicts. Pure."""
    from .dataset import _census_grouping, ValidEventWithSource
    if not records:
        return [], 0, 0, 0
    # Wrap as ValidEventWithSource with a synthetic source label
    with_source = [
        ValidEventWithSource(
            event=r, source_file="history_loader", source_location=f"row:{i}",
        )
        for i, r in enumerate(records)
    ]
    conflict_groups, counts, canonical_events = _census_grouping(with_source)
    return (
        canonical_events,
        int(counts.get("exact_duplicates_collapsed", 0)),
        int(counts.get("conflicting_composite_keys", 0)),
        int(counts.get("conflicting_rows", 0)),
    )


def _is_two_way(sport: str) -> bool:
    from .sports import SPORTS
    spec = SPORTS.get(sport)
    if spec is None:
        return True  # unknown sport is treated as two-way for safety
    return not bool(getattr(spec, "draw_possible", False))


def _v2_filter_one(
    ev: SettledEvent, *, cutoff_date: date
) -> tuple[bool, str | None]:
    """Apply v2 validity rules. Returns (ok, reason_if_not_ok)."""
    from .sports import SPORTS
    from .shadow_contracts import key_of
    # Sport recognition
    if ev.sport not in SPORTS:
        return False, "UNKNOWN_SPORT"
    # Participants nonempty
    if not ev.participant_1 or not ev.participant_2:
        return False, "EMPTY_PARTICIPANT"
    # No self-pairs (case-insensitive canonical key)
    if key_of(ev.participant_1) == key_of(ev.participant_2):
        return False, "SELF_PAIR"
    # Disposition vocabulary
    disp = (ev.disposition or "").strip().upper()
    if disp in ("VOID", "NO_CONTEST"):
        return False, f"VOID_DISPOSITION:{disp}"
    if disp not in ("SETTLED", "SETTLED_CUP", "SETTLED_DRAW"):
        return False, f"UNSUPPORTED_DISPOSITION:{disp}"
    # Coherent winner
    if ev.winner_index not in (0, 1, 2):
        return False, "INVALID_WINNER_INDEX"
    # Anomalous two-way draw
    if _is_two_way(ev.sport) and ev.winner_index == 0:
        return False, "ANOMALOUS_TWO_WAY_DRAW"
    # Strict event_date < target_date
    try:
        ev_date = date.fromisoformat(ev.event_date[:10])
    except (ValueError, TypeError):
        return False, "INVALID_EVENT_DATE"
    if ev_date >= cutoff_date:
        return False, "PRIOR_DATE_VIOLATION"
    return True, None


def _interim_path(repo_root: Path) -> Path:
    return repo_root / "data" / "interim" / "settled_history.json"


def _reports_dir(repo_root: Path) -> Path:
    return repo_root / "data" / "reports"


def load_valid_history(
    *,
    target_date: str,
    repo_root: str | Path,
    history_paths: list[str | Path] | None = None,
    max_interim_bytes: int = DEFAULT_MAX_INTERIM_BYTES,
) -> HistoryLoadResult:
    """Load prior settled events with exact v2 validity.

    If ``history_paths`` is None, the loader reads:

    - ``data/interim/settled_history.json`` (if it exists and is not
      larger than ``max_interim_bytes``), and
    - every ``data/reports/history_<sport>.jsonl.gz`` (streamed).

    If ``history_paths`` is given, only those paths are read. Each path
    must be a file (or symlink to a file) that lives within ``repo_root``
    and matches one of the two supported formats.
    """
    repo_root = Path(repo_root).resolve()
    try:
        target_d = date.fromisoformat(target_date)
    except ValueError as e:
        raise HistoryLoaderError(f"target_date not ISO YYYY-MM-DD: {target_date!r}") from e
    cutoff_date = target_d  # strict event_date < target_date

    # Discover paths
    if history_paths is None:
        candidates: list[Path] = []
        interim = _interim_path(repo_root)
        if interim.is_file():
            candidates.append(interim)
        reports_dir = _reports_dir(repo_root)
        if reports_dir.is_dir():
            candidates.extend(sorted(reports_dir.glob("history_*.jsonl.gz")))
        paths: list[Path] = candidates
    else:
        paths = [_resolve_within_root(Path(p), repo_root) for p in history_paths]

    manifest_section: dict[str, Any] = {
        "history_target_date": target_date,
        "history_cutoff_date": cutoff_date.isoformat(),
        "history_feature_contract": RESEARCH_FEATURE_CONTRACT_VERSION,
        "history_max_interim_bytes": max_interim_bytes,
        "history_input_paths": [],
        "history_input_sha256": {},
        "history_input_bytes": {},
        "history_input_format": {},
    }
    excluded_counts: Counter = Counter()
    decoded_rows = 0
    raw_settled: list[SettledEvent] = []

    for path in paths:
        path_str = str(path)
        manifest_section["history_input_paths"].append(path_str)
        if not path.is_file():
            raise HistoryPathError(f"history path not a file: {path}")
        if path.suffix == ".gz":
            manifest_section["history_input_format"][path_str] = "jsonl.gz"
            # Hash the exact gzipped bytes by streaming, then re-open
            # the file to parse the gz stream. This avoids the
            # decompressed-byte-size discrepancy.
            file_bytes = 0
            file_hash = hashlib.sha256()
            with path.open("rb") as f:
                for chunk in iter(lambda: f.read(_STREAM_CHUNK), b""):
                    file_hash.update(chunk)
                    file_bytes += len(chunk)
            manifest_section["history_input_sha256"][path_str] = file_hash.hexdigest()
            manifest_section["history_input_bytes"][path_str] = file_bytes
            with path.open("rb") as f:
                gz = gzip.GzipFile(fileobj=f)
                for raw_line in gz:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    decoded_rows += 1
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        excluded_counts["MALFORMED_JSONL"] += 1
                        continue
                    try:
                        ev = _validate_settled_dict(row)
                    except ValueError as e:
                        reason = str(e).split(":", 1)[0]
                        excluded_counts[f"SCHEMA_INVALID:{reason}"] += 1
                        continue
                    raw_settled.append(ev)
        elif path.name == "settled_history.json":
            manifest_section["history_input_format"][path_str] = "json-list"
            size = path.stat().st_size
            if size > max_interim_bytes:
                raise HistorySizeLimitError(
                    f"interim ledger {path} is {size} bytes, exceeds "
                    f"max_interim_bytes={max_interim_bytes}"
                )
            data = path.read_bytes()
            h = hashlib.sha256(data).hexdigest()
            try:
                parsed = json.loads(data)
            except json.JSONDecodeError:
                excluded_counts["MALFORMED_JSON"] += 1
                manifest_section["history_input_sha256"][path_str] = h
                manifest_section["history_input_bytes"][path_str] = size
                continue
            if not isinstance(parsed, list):
                excluded_counts["MALFORMED_JSON"] += 1
                manifest_section["history_input_sha256"][path_str] = h
                manifest_section["history_input_bytes"][path_str] = size
                continue
            for row in parsed:
                decoded_rows += 1
                try:
                    ev = _validate_settled_dict(row)
                except ValueError as e:
                    reason = str(e).split(":", 1)[0]
                    excluded_counts[f"SCHEMA_INVALID:{reason}"] += 1
                    continue
                raw_settled.append(ev)
            manifest_section["history_input_sha256"][path_str] = h
            manifest_section["history_input_bytes"][path_str] = size
        else:
            raise HistoryPathError(
                f"unsupported history format: {path} "
                f"(supported: .jsonl.gz, settled_history.json)"
            )

    # Stage 1: decoded rows
    schema_invalid = sum(v for k, v in excluded_counts.items() if k.startswith("SCHEMA_INVALID") or k.startswith("MALFORMED"))
    schema_valid_candidate_rows = decoded_rows - schema_invalid
    # Stage 2: v2 validity filter
    valid: list[SettledEvent] = []
    for ev in raw_settled:
        ok, reason = _v2_filter_one(ev, cutoff_date=cutoff_date)
        if ok:
            valid.append(ev)
        else:
            excluded_counts[reason or "V2_INVALID"] += 1
    # Stage 3: dedup + conflict (per _census_grouping)
    canonical, exact_duplicates, conflicting_keys, conflicting_rows = _emit_canonical(valid)
    # Admitted rows
    admitted = canonical

    manifest_section["history_decoded_rows"] = decoded_rows
    manifest_section["history_schema_invalid"] = schema_invalid
    manifest_section["history_schema_valid_candidate_rows"] = schema_valid_candidate_rows
    manifest_section["history_excluded_counts"] = dict(excluded_counts)
    manifest_section["history_unique_valid_rows"] = len(canonical)
    manifest_section["history_exact_duplicate_rows"] = exact_duplicates
    manifest_section["history_conflict_count_groups"] = conflicting_keys
    manifest_section["history_conflict_count_rows"] = conflicting_rows
    manifest_section["history_admitted_rows"] = len(admitted)

    # Verify the three equations that must hold for non-overlap:
    # 1. decoded_rows == schema_invalid + schema_valid_candidate_rows
    # 2. schema_valid_candidate_rows == history_v2_excluded + unique_valid_rows + exact_duplicate_rows + conflicting_rows
    #    where history_v2_excluded = sum of v2-exclusion counts
    v2_excluded = sum(v for k, v in excluded_counts.items()
                      if k not in ("MALFORMED_JSON", "MALFORMED_JSONL")
                      and not k.startswith("SCHEMA_INVALID"))
    assert decoded_rows == schema_invalid + schema_valid_candidate_rows, (
        f"decoded rows imbalance: {decoded_rows} vs {schema_invalid}+{schema_valid_candidate_rows}"
    )
    assert schema_valid_candidate_rows == v2_excluded + len(canonical) + exact_duplicates + conflicting_rows, (
        f"validity/dedup imbalance: {schema_valid_candidate_rows} vs "
        f"{v2_excluded}+{len(canonical)}+{exact_duplicates}+{conflicting_rows}"
    )

    return HistoryLoadResult(
        target_date=target_date,
        cutoff_date=cutoff_date.isoformat(),
        history_index=HistoryIndex(admitted),
        settled=admitted,
        manifest_section=manifest_section,
    )
