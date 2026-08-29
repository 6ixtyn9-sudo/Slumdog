"""Milestone 7B — Verifiable full-payload shadow bundle (create + verify).

This tool packages a *completed* Milestone 7 shadow run and every input
needed to audit/reproduce it into a deterministic, independently
verifiable compressed archive, plus an external bundle receipt and a
``sha256sum``-compatible checksum marker.

Post-decision preservation only. This module NEVER reruns R2, changes
ranking/selections, alters the payload or manifest, attaches outcomes,
reads settlement results to modify the bundle, uses odds, invokes
Forebet/collection/production/training, or accesses the network. It only
reads the exact bytes the completed run already committed to and writes
new bundle artifacts into an explicitly supplied output directory.

Standard library only (no third-party dependencies). The module imports
no other Slumdog submodule, so verification can run on an independent
machine with nothing but the archive, the bundle receipt, and Python.

Bounded-memory design
---------------------
Small metadata files (payload, manifest, the two configs, the capture
receipt, sidecars, inventory, README) are read into memory only under an
explicit metadata byte cap. Potentially large evidence files (raw capture
bodies, history inputs) and the archive itself are **streamed in bounded
chunks** — never materialized whole — both when the archive is created and
when it is verified. Verification never extracts files to disk: each tar
member is hashed and counted through chunked reads, and only the small
metadata members that must be parsed are buffered (again, under the
metadata cap). Explicit limits (see constants below) reject oversized or
malicious archives well before any multi-gigabyte allocation.

CLI:

    python -m slumdog.shadow_bundle create \\
        --run-dir <completed-run-dir> --output-dir <dir> --root <repo-root>
    python -m slumdog.shadow_bundle verify \\
        --bundle <archive.tar.gz> --receipt <archive.bundle.json>
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import io
import json
import os
import stat
import sys
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


# ---------------------------------------------------------------------------
# Frozen constants
# ---------------------------------------------------------------------------

BUNDLE_SCHEMA_VERSION = "slumdog_shadow_bundle_v1"
RUN_SCHEMA_VERSION = "shadow_evaluator_v1"

# Canonical (parsed-JSON) SHA-256 of the frozen 6B baseline config. This
# is the frozen R2 rule source and MUST NOT drift. It is duplicated here
# (rather than imported from ``shadow_evaluator``) so this module stays
# stdlib-only and independently runnable.
FROZEN_BASELINE_CONFIG_SHA256 = (
    "666dabe7ea21e11867cf4816f4c2edcd771247646c6c9d7726c22611cda700a1"
)
FROZEN_BASELINE_CONFIG_REPO_PATH = Path("config") / "research_baselines_v1.json"
SHADOW_DECLARATION_REPO_PATH = Path("config") / "shadow_evaluator_v1.json"

DURABILITY_STATUS = "LOCAL_EXPORT_READY_FOR_INDEPENDENT_COPY"

_COMPLETED_RUN_STATUSES = frozenset({
    "SHADOW_SELECTIONS_EMITTED",
    "SHADOW_NO_SELECTION",
})

# Logical archive layout.
RUN_DIR = "bundle/run"
CONFIG_DIR = "bundle/config"
CAPTURE_RECEIPT_MEMBER = "bundle/capture/receipt.json"
SIDECAR_DIR = "bundle/capture/sidecars"
BODY_DIR = "bundle/capture/bodies"
HISTORY_DIR = "bundle/history"
INVENTORY_MEMBER = "bundle/inventory.json"
README_MEMBER = "bundle/README.txt"
PAYLOAD_MEMBER = "bundle/run/shadow_selections.json"
MANIFEST_MEMBER = "bundle/run/manifest.json"
FROZEN_CONFIG_MEMBER = "bundle/config/research_baselines_v1.json"
DECLARATION_MEMBER = "bundle/config/shadow_evaluator_v1.json"

# Roles that must be JSON-parsed during verification (small metadata).
_METADATA_ROLES = frozenset({
    "run_payload", "run_manifest", "frozen_baseline_config",
    "shadow_declaration", "bundle_inventory",
})

# Deterministic tar metadata.
_TAR_MTIME = 0
_TAR_MODE = 0o644
_TAR_UID = 0
_TAR_GID = 0
_TAR_UNAME = ""
_TAR_GNAME = ""
_TAR_COMPRESSLEVEL = 9

# Conservative output permissions.
_OUTPUT_MODE = 0o600

# ---------------------------------------------------------------------------
# Bounded-memory limits (documented in docs/MILESTONE7B_SHADOW_BUNDLE.md).
#
# Chosen comfortably above the current retained dataset (~53 MB total
# across relevant data dirs) but far below any accidental multi-gigabyte
# allocation. There is deliberately no "unlimited" override.
# ---------------------------------------------------------------------------
STREAM_CHUNK = 1024 * 1024            # 1 MiB hash/copy chunk
MAX_COMPRESSED_ARCHIVE_BYTES = 512 * 1024 * 1024     # 512 MiB .tar.gz
MAX_EVIDENCE_MEMBER_BYTES = 256 * 1024 * 1024        # 256 MiB one body/history
MAX_TOTAL_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024    # 1 GiB decompressed
MAX_METADATA_BYTES = 16 * 1024 * 1024                # 16 MiB any JSON member
MAX_MEMBER_COUNT = 10_000
MAX_PATH_BYTES = 512

_AUTH_FLAGS = (
    "production_authorized",
    "shortlist_policy_authorized",
    "training_authorized",
    "threshold_optimization_authorized",
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class BundleError(Exception):
    """Base class for all bundle failures."""


class BundleSourceError(BundleError):
    """A source run/input is missing, unsafe, or fails integrity (create)."""


class BundleIntegrityError(BundleError):
    """An archive/receipt fails independent verification (verify)."""


# ---------------------------------------------------------------------------
# Small deterministic helpers
# ---------------------------------------------------------------------------


def _canonical_json_bytes(obj: Any) -> bytes:
    """UTF-8 bytes of canonical JSON: keys sorted recursively, compact."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_sha256(obj: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(obj)).hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _repo_rel(path: Path, root: Path) -> str:
    """Return the forward-slash repo-relative path of ``path`` under root."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as e:
        raise BundleSourceError(f"path is not within approved root: {path}") from e


def _safe_resolve_within_root(path: str | Path, root: Path, *, what: str) -> Path:
    """Resolve ``path`` and require it to live inside ``root``.

    Rejects absolute-path escape, ``..`` traversal escape, and any symlink
    along the resolved relative chain (defense in depth).
    """
    root = root.resolve()
    p = Path(path)
    candidate = p if p.is_absolute() else (root / p)
    try:
        resolved = candidate.resolve(strict=False)
    except OSError as e:
        raise BundleSourceError(f"{what}: path resolution failed: {path!r}: {e}") from e
    try:
        rel = resolved.relative_to(root)
    except ValueError as e:
        raise BundleSourceError(
            f"{what}: path escapes approved repository root: {path!r}"
        ) from e
    cur = root
    for part in rel.parts:
        cur = cur / part
        try:
            if cur.is_symlink():
                raise BundleSourceError(
                    f"{what}: symlink is not permitted in an evidence path: {cur}"
                )
        except OSError as e:
            raise BundleSourceError(f"{what}: cannot stat path component: {cur}: {e}") from e
    return resolved


# ---------------------------------------------------------------------------
# Chunked streaming primitives
# ---------------------------------------------------------------------------


def _hash_stream(fileobj, *, limit: int) -> tuple[str, int]:
    """Hash a stream in bounded chunks up to ``limit`` bytes.

    Returns ``(sha256_hex, bytes_read)``. Reads at most ``limit`` bytes; a
    stream longer than ``limit`` reports ``bytes_read == limit + 1``-style
    overflow only by reading one extra byte — callers detect overflow when
    ``bytes_read`` exceeds the expected/allowed size.
    """
    h = hashlib.sha256()
    total = 0
    while True:
        chunk = fileobj.read(STREAM_CHUNK)
        if not chunk:
            break
        h.update(chunk)
        total += len(chunk)
        if total > limit:
            # Stop reading; the caller compares total against the cap.
            return h.hexdigest(), total
    return h.hexdigest(), total


def _safe_archive_member_name(name: str) -> bool:
    """Reject absolute paths, '..', backslashes, and non-bundle paths."""
    if not name or name in (".", "/"):
        return False
    if len(name.encode("utf-8")) > MAX_PATH_BYTES:
        return False
    if name.startswith("/") or name.startswith("\\"):
        return False
    if "\\" in name:
        return False
    parts = name.split("/")
    if parts[0] != "bundle":
        return False
    for part in parts:
        if part in ("", ".", ".."):
            return False
    return True


class _CountingHashReader:
    """Wrap a binary file object: count bytes and hash them on read.

    Used to measure/hash the compressed archive as tarfile streams it,
    without ever holding the whole archive in memory.
    """

    def __init__(self, fileobj, *, limit: int, what: str) -> None:
        self._f = fileobj
        self._limit = limit
        self._what = what
        self.count = 0
        self.sha = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = STREAM_CHUNK
        want = min(size, STREAM_CHUNK)
        chunk = self._f.read(want)
        if chunk:
            self.count += len(chunk)
            self.sha.update(chunk)
            if self.count > self._limit:
                raise BundleIntegrityError(
                    f"{self._what} exceeds size limit ({self._limit} bytes)"
                )
        return chunk

    def close(self) -> None:
        self._f.close()


# ---------------------------------------------------------------------------
# Member specs
# ---------------------------------------------------------------------------


@dataclass
class _Member:
    """One archive member.

    Either ``data`` (small metadata/generated bytes, bounded by the
    metadata cap) or ``source`` (a disk evidence file that is streamed
    into the tar and never materialized) is set — never both for large
    evidence.
    """

    archive_member: str
    role: str
    size: int
    sha256: str
    source_paths: list[str] = field(default_factory=list)
    data: bytes | None = None
    source: Path | None = None


def _add_member(members: dict[str, _Member], member: _Member) -> None:
    existing = members.get(member.archive_member)
    if existing is not None:
        if existing.sha256 != member.sha256:
            raise BundleSourceError(
                f"archive member collision on {member.archive_member!r} with "
                f"different content"
            )
        existing.source_paths = sorted(set(existing.source_paths) | set(member.source_paths))
        return
    members[member.archive_member] = member


# ---------------------------------------------------------------------------
# Source verification
# ---------------------------------------------------------------------------


def _read_metadata_file(
    path: str | Path, root: Path, *, what: str,
    expected_sha256: str | None = None, limit: int = MAX_METADATA_BYTES,
) -> tuple[Path, bytes]:
    """Read a small metadata file in bounded fashion and verify its hash.

    Used for manifest, payload, configs, declaration, capture receipt, and
    sidecars. Reads at most ``limit + 1`` bytes and fails closed if the
    file is larger than the metadata cap.
    """
    resolved = _safe_resolve_within_root(path, root, what=what)
    if not resolved.is_file():
        raise BundleSourceError(f"{what}: missing or not a regular file: {path!r}")
    with resolved.open("rb") as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise BundleSourceError(
            f"{what}: metadata file exceeds {limit} bytes for {path!r}"
        )
    actual = _sha256_bytes(data)
    if expected_sha256 is not None and actual != expected_sha256:
        raise BundleSourceError(
            f"{what}: exact-byte SHA-256 mismatch for {path!r}: "
            f"expected {expected_sha256} actual {actual}"
        )
    return resolved, data


def _verify_evidence_file(
    path: str | Path, root: Path, *, expected_sha256: str,
    expected_size: int | None, what: str,
) -> tuple[Path, str, int]:
    """Stream-verify a potentially large evidence file WITHOUT buffering it.

    Opens the file once, fstats it (regular file + size), streams a
    chunked SHA-256 (capped at the evidence-member limit), and checks the
    hash against the run manifest. Returns ``(resolved_path, sha, size)``.
    No file bytes are retained.
    """
    resolved = _safe_resolve_within_root(path, root, what=what)
    if not resolved.is_file():
        raise BundleSourceError(f"{what}: missing or not a regular file: {path!r}")
    with resolved.open("rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise BundleSourceError(f"{what}: not a regular file: {path!r}")
        size = st.st_size
        if expected_size is not None and size != expected_size:
            raise BundleSourceError(
                f"{what}: size mismatch for {path!r}: expected {expected_size} actual {size}"
            )
        if size > MAX_EVIDENCE_MEMBER_BYTES:
            raise BundleSourceError(
                f"{what}: evidence file exceeds {MAX_EVIDENCE_MEMBER_BYTES} bytes: {path!r}"
            )
        sha, read = _hash_stream(f, limit=MAX_EVIDENCE_MEMBER_BYTES)
    if read != size:
        raise BundleSourceError(
            f"{what}: size changed or file truncated while hashing: {path!r}"
        )
    if sha != expected_sha256:
        raise BundleSourceError(
            f"{what}: exact-byte SHA-256 mismatch for {path!r}: "
            f"expected {expected_sha256} actual {sha}"
        )
    return resolved, sha, size


@contextlib.contextmanager
def _verified_source(path: Path, expected_sha256: str, expected_size: int, *,
                     what: str) -> Iterator[Any]:
    """Open an evidence file and stream it into tar while re-verifying.

    Check/use-race safe pattern:
      1. open the source once and fstat it;
      2. hash the bytes in chunks (must match the manifest hash/size);
      3. rewind the same descriptor;
      4. yield a bounded read proxy that re-hashes and counts while tar
         streams from it;
      5. after tar finishes, require the re-hash to match and fstat to be
         unchanged (size/mtime/inode/device), so the bytes that entered
         the archive are exactly the bytes that were verified.

    Raises BundleSourceError on any mismatch.
    """
    f = path.open("rb")
    try:
        st0 = os.fstat(f.fileno())
        if not stat.S_ISREG(st0.st_mode):
            raise BundleSourceError(f"{what}: not a regular file: {path!r}")
        if st0.st_size != expected_size:
            raise BundleSourceError(
                f"{what}: size changed before archiving: {path!r}: "
                f"expected {expected_size} found {st0.st_size}"
            )
        pre_sha, pre_read = _hash_stream(f, limit=MAX_EVIDENCE_MEMBER_BYTES)
        if pre_read != expected_size or pre_sha != expected_sha256:
            raise BundleSourceError(
                f"{what}: evidence verification failed before archiving: {path!r}"
            )
        f.seek(0)

        rehash = hashlib.sha256()
        state = {"count": 0}

        class _Proxy:
            """Bounded read view tarfile copies from; re-hashes every byte."""

            def read(self, size=-1):
                if size is None or size < 0:
                    size = STREAM_CHUNK
                chunk = f.read(min(size, STREAM_CHUNK))
                if chunk:
                    rehash.update(chunk)
                    state["count"] += len(chunk)
                    if state["count"] > expected_size:
                        raise BundleSourceError(
                            f"{what}: evidence grew during archiving: {path!r}"
                        )
                return chunk

            def close(self):
                pass

        yield _Proxy()

        if state["count"] != expected_size or rehash.hexdigest() != expected_sha256:
            raise BundleSourceError(
                f"{what}: streamed bytes do not match verified hash/size: {path!r} "
                f"(read {state['count']} of {expected_size})"
            )
        # The held descriptor still points at the bytes we streamed, so fstat on
        # the descriptor catches growth/truncation. But a same-content atomic
        # replacement (new inode at the same path) leaves the descriptor stale;
        # re-stat the path itself to detect an inode/identity swap.
        st_fd = os.fstat(f.fileno())
        try:
            st_path = os.stat(path)
        except OSError as e:
            raise BundleSourceError(
                f"{what}: source file vanished before archiving completed: {path!r}"
            ) from e
        identity0 = (st0.st_size, st0.st_mtime_ns, st0.st_ino, st0.st_dev)
        identity_path = (st_path.st_size, st_path.st_mtime_ns,
                         st_path.st_ino, st_path.st_dev)
        if identity0 != identity_path:
            raise BundleSourceError(
                f"{what}: source file changed between verification and archiving: {path!r}"
            )
        if st_fd.st_size != expected_size:
            raise BundleSourceError(
                f"{what}: source file size changed while streaming: {path!r}"
            )
    finally:
        f.close()


# ---------------------------------------------------------------------------
# Member collection
# ---------------------------------------------------------------------------


def _safe_extension(name: str, *, allowed: tuple[str, ...]) -> str:
    suffix = Path(name).suffix.lower()
    if suffix not in allowed:
        raise BundleSourceError(
            f"unsupported evidence file extension {suffix!r} for {name!r}; "
            f"allowed: {', '.join(allowed)}"
        )
    return suffix


def _collect_members(
    *, manifest_bytes: bytes, payload_bytes: bytes, root: Path,
) -> tuple[dict[str, Any], dict[str, _Member]]:
    """Validate the completed run and build every evidence member spec.

    Metadata members carry bounded in-memory bytes; capture bodies and
    history inputs are verified by streaming and reference their on-disk
    file (they are streamed again into the tar at archive build time).
    """
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise BundleSourceError(f"manifest is corrupt / not valid JSON: {e}") from e
    if not isinstance(manifest, dict):
        raise BundleSourceError("manifest must be a JSON object")

    if manifest.get("version") != RUN_SCHEMA_VERSION:
        raise BundleSourceError(
            f"unsupported run schema/version: {manifest.get('version')!r} "
            f"(expected {RUN_SCHEMA_VERSION!r})"
        )
    run_status = manifest.get("run_status")
    run_id = manifest.get("run_id")
    if run_status == "SHADOW_RUN_BLOCKED" or run_id == "BLOCKED":
        raise BundleSourceError(
            "refusing to bundle a blocked run receipt "
            "(run_status=SHADOW_RUN_BLOCKED / run_id=BLOCKED)"
        )
    if run_status not in _COMPLETED_RUN_STATUSES:
        raise BundleSourceError(
            f"refusing to bundle run with non-completed run_status={run_status!r}"
        )
    if not isinstance(run_id, str) or not run_id:
        raise BundleSourceError("manifest missing valid run_id")
    target_date = manifest.get("target_date")
    if not isinstance(target_date, str) or not target_date:
        raise BundleSourceError("manifest missing valid target_date")
    if not manifest.get("decision_committed_at"):
        raise BundleSourceError("manifest missing decision_committed_at")

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise BundleSourceError(f"payload is corrupt / not valid JSON: {e}") from e
    if not isinstance(payload, dict):
        raise BundleSourceError("payload must be a JSON object")
    payload_sha = _sha256_bytes(payload_bytes)
    if payload_sha != manifest.get("payload_file_sha256"):
        raise BundleSourceError(
            f"payload exact-byte SHA-256 does not match manifest: "
            f"actual {payload_sha} manifest {manifest.get('payload_file_sha256')}"
        )
    if payload.get("run_id") != run_id:
        raise BundleSourceError("payload run_id does not match manifest run_id")
    if payload.get("target_date") != target_date:
        raise BundleSourceError("payload target_date does not match manifest target_date")

    members: dict[str, _Member] = {}

    members[PAYLOAD_MEMBER] = _Member(
        archive_member=PAYLOAD_MEMBER, role="run_payload",
        size=len(payload_bytes), sha256=payload_sha, data=payload_bytes,
    )
    members[MANIFEST_MEMBER] = _Member(
        archive_member=MANIFEST_MEMBER, role="run_manifest",
        size=len(manifest_bytes), sha256=_sha256_bytes(manifest_bytes),
        data=manifest_bytes,
    )

    # --- frozen configs (metadata; parsed to verify canonical hashes) ------
    frozen_abs, frozen_bytes = _read_metadata_file(
        root / FROZEN_BASELINE_CONFIG_REPO_PATH, root, what="frozen baseline config"
    )
    try:
        frozen_obj = json.loads(frozen_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise BundleSourceError(f"frozen baseline config is not valid JSON: {e}") from e
    if _canonical_sha256(frozen_obj) != FROZEN_BASELINE_CONFIG_SHA256:
        raise BundleSourceError(
            f"frozen baseline config canonical SHA-256 mismatch: "
            f"actual {_canonical_sha256(frozen_obj)} expected {FROZEN_BASELINE_CONFIG_SHA256}"
        )
    if manifest.get("frozen_baseline_config_sha256") != FROZEN_BASELINE_CONFIG_SHA256:
        raise BundleSourceError(
            "manifest frozen_baseline_config_sha256 does not match the frozen config"
        )
    _add_member(members, _Member(
        archive_member=FROZEN_CONFIG_MEMBER, role="frozen_baseline_config",
        size=len(frozen_bytes), sha256=_sha256_bytes(frozen_bytes),
        source_paths=[_repo_rel(frozen_abs, root)], data=frozen_bytes,
    ))

    decl_abs, decl_bytes = _read_metadata_file(
        root / SHADOW_DECLARATION_REPO_PATH, root, what="shadow declaration"
    )
    try:
        decl_obj = json.loads(decl_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise BundleSourceError(f"shadow declaration is not valid JSON: {e}") from e
    decl_canonical = _canonical_sha256(decl_obj)
    if decl_canonical != manifest.get("declaration_sha256"):
        raise BundleSourceError(
            f"shadow declaration canonical SHA-256 mismatch: actual "
            f"{decl_canonical} manifest {manifest.get('declaration_sha256')}"
        )
    decl_auth = decl_obj.get("authorizations", {}) if isinstance(decl_obj, dict) else {}
    for flag in _AUTH_FLAGS:
        if decl_auth.get(flag) is not False:
            raise BundleSourceError(
                f"shadow declaration authorizations.{flag} must be false (fail closed)"
            )
    _add_member(members, _Member(
        archive_member=DECLARATION_MEMBER, role="shadow_declaration",
        size=len(decl_bytes), sha256=_sha256_bytes(decl_bytes),
        source_paths=[_repo_rel(decl_abs, root)], data=decl_bytes,
    ))

    # --- capture provenance ------------------------------------------------
    cp = manifest.get("capture_provenance")
    if not isinstance(cp, dict):
        raise BundleSourceError("manifest missing capture_provenance")
    receipt_path = cp.get("receipt_path")
    receipt_expected = cp.get("receipt_sha256")
    if not receipt_path or not receipt_expected:
        raise BundleSourceError("capture_provenance missing receipt path/hash")
    receipt_abs, receipt_bytes = _read_metadata_file(
        receipt_path, root, expected_sha256=receipt_expected, what="capture receipt"
    )
    ip = manifest.get("input_provenance")
    if isinstance(ip, dict) and ip.get("capture_receipt_sha256") not in (None, receipt_expected):
        raise BundleSourceError(
            "input_provenance.capture_receipt_sha256 disagrees with capture_provenance"
        )
    _add_member(members, _Member(
        archive_member=CAPTURE_RECEIPT_MEMBER, role="capture_receipt",
        size=len(receipt_bytes), sha256=_sha256_bytes(receipt_bytes),
        source_paths=[_repo_rel(receipt_abs, root)], data=receipt_bytes,
    ))

    raw_inputs = cp.get("raw_input_sha256")
    if not isinstance(raw_inputs, dict):
        raise BundleSourceError("capture_provenance missing raw_input_sha256")
    for raw_path in sorted(raw_inputs.keys()):
        expected = raw_inputs[raw_path]
        name = Path(raw_path).name.lower()
        if name.endswith(".json"):
            # Sidecar: small JSON metadata.
            abs_path, data = _read_metadata_file(
                raw_path, root, expected_sha256=expected, what="capture sidecar"
            )
            ext = _safe_extension(name, allowed=(".json",))
            member_path = f"{SIDECAR_DIR}/{expected}{ext}"
            _add_member(members, _Member(
                archive_member=member_path, role="capture_sidecar",
                size=len(data), sha256=_sha256_bytes(data),
                source_paths=[_repo_rel(abs_path, root)], data=data,
            ))
        elif name.endswith(".txt"):
            # Raw capture body: potentially large -> stream, never buffer.
            abs_path, sha, size = _verify_evidence_file(
                raw_path, root, expected_sha256=expected,
                expected_size=None, what="capture body",
            )
            ext = _safe_extension(name, allowed=(".txt",))
            member_path = f"{BODY_DIR}/{expected}{ext}"
            _add_member(members, _Member(
                archive_member=member_path, role="capture_body",
                size=size, sha256=sha,
                source_paths=[_repo_rel(abs_path, root)], source=abs_path,
            ))
        else:
            raise BundleSourceError(
                f"unsupported capture input type for {raw_path!r} "
                f"(expected sidecar .json or body .txt)"
            )

    # --- history provenance ------------------------------------------------
    hp = manifest.get("history_provenance")
    if not isinstance(hp, dict):
        raise BundleSourceError("manifest missing history_provenance")
    hist_hashes = hp.get("history_input_sha256", {})
    hist_bytes = hp.get("history_input_bytes", {})
    if not isinstance(hist_hashes, dict):
        raise BundleSourceError("history_provenance.history_input_sha256 must be an object")
    for hist_path in sorted(hist_hashes.keys()):
        expected = hist_hashes[hist_path]
        expected_size = hist_bytes.get(hist_path) if isinstance(hist_bytes, dict) else None
        name = Path(hist_path).name.lower()
        if name.endswith(".gz"):
            ext = _safe_extension(name, allowed=(".gz",))
        elif name == "settled_history.json":
            ext = ".json"
        else:
            raise BundleSourceError(
                f"unsupported history input format for {hist_path!r} "
                f"(supported: .jsonl.gz, settled_history.json)"
            )
        # History inputs are potentially large -> stream, never buffer.
        abs_path, sha, size = _verify_evidence_file(
            hist_path, root, expected_sha256=expected,
            expected_size=expected_size, what="history input",
        )
        member_path = f"{HISTORY_DIR}/{expected}{ext}"
        _add_member(members, _Member(
            archive_member=member_path, role="history_input",
            size=size, sha256=sha,
            source_paths=[_repo_rel(abs_path, root)], source=abs_path,
        ))

    return manifest, members


# ---------------------------------------------------------------------------
# Inventory + README
# ---------------------------------------------------------------------------


def _build_inventory(manifest: dict[str, Any], members: dict[str, _Member]) -> bytes:
    member_rows = []
    for archive_member in sorted(members.keys()):
        m = members[archive_member]
        member_rows.append({
            "archive_member": m.archive_member,
            "role": m.role,
            "sha256": m.sha256,
            "bytes": m.size,
            "source_paths": m.source_paths,
        })
    inventory = {
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "run_schema_version": RUN_SCHEMA_VERSION,
        "run_id": manifest["run_id"],
        "target_date": manifest["target_date"],
        "content_member_count": len(member_rows),
        "members": member_rows,
    }
    return _canonical_json_bytes(inventory)


_README_TEMPLATE = """\
SLUMDOG SHADOW RUN - VERIFIABLE FULL-PAYLOAD BUNDLE
====================================================

Bundle schema version : {bundle_version}
Run schema version    : {run_version}
Run ID                : {run_id}
Target date           : {target_date}
Decision committed at : {committed_at}

CONTENTS (logical layout)
  bundle/run/shadow_selections.json  immutable decision payload (exact bytes)
  bundle/run/manifest.json           completion marker + provenance (exact bytes)
  bundle/config/research_baselines_v1.json   frozen R2 rule/config
  bundle/config/shadow_evaluator_v1.json     shadow declaration
  bundle/capture/receipt.json        capture receipt used by the run
  bundle/capture/sidecars/<sha>.json every referenced capture sidecar
  bundle/capture/bodies/<sha>.txt    every referenced raw capture body
  bundle/history/<sha>.<ext>         every referenced history input
  bundle/inventory.json              canonical member inventory
  bundle/README.txt                  this file

All file names under capture/ and history/ are content-addressed by the
exact-byte SHA-256 of the file. No absolute or host-specific paths are
stored in the archive; original repository-relative paths are recorded
in inventory.json.

BOUNDED-MEMORY VERIFICATION
  The verifier streams every member in fixed chunks and never extracts
  files to disk. Archive and member size limits are enforced before any
  large allocation (see docs/MILESTONE7B_SHADOW_BUNDLE.md).

HOW TO VERIFY (independent machine, no extraction required)

  1. Confirm the archive checksum matches the bundle receipt / checksum:

       sha256sum {archive_name}

     The printed SHA-256 must equal the "archive_sha256" value in
     {receipt_name} (and the value in {archive_name}.sha256 if present).

  2. If the Slumdog package is available, run full in-memory verification:

       python -m slumdog.shadow_bundle verify \\
         --bundle {archive_name} \\
         --receipt {receipt_name}

     A successful verification exits 0 and prints BUNDLE_VERIFIED.
     Any integrity, path, schema, hash, or authorization failure exits
     non-zero WITHOUT creating or modifying any files.

WHAT VERIFICATION CHECKS
  - archive exact-byte SHA-256 against the receipt
  - safe archive member paths (no absolute paths, no '..', no symlinks,
    no devices/FIFOs/hard links, no duplicate or unexpected members)
  - every inventory member SHA-256 and byte size
  - payload SHA-256 against the run manifest
  - frozen baseline config canonical SHA-256 (R2 rule source)
  - shadow declaration canonical SHA-256 and fail-closed authorization flags
  - recomputed input digest, decision digest, and run ID from bundled provenance

NON-PRODUCTION STATUS
  This bundle is post-decision preservation only. Production publication,
  shortlist policy, training, threshold optimization, real-money use, and
  any outcome attachment are NOT authorized and NOT performed. The bundle
  never reruns the rule or changes selections.

DURABILITY BOUNDARY
  Durability status: {durability}
  This export is a portable, independently copyable artifact, but a hash
  alone is NOT a second copy. The first real shadow prediction is backed
  up ONLY when an independently downloaded full archive verifies on a
  separate machine.
"""


def _build_readme(*, manifest: dict[str, Any], archive_name: str, receipt_name: str) -> bytes:
    text = _README_TEMPLATE.format(
        bundle_version=BUNDLE_SCHEMA_VERSION,
        run_version=RUN_SCHEMA_VERSION,
        run_id=manifest["run_id"],
        target_date=manifest["target_date"],
        committed_at=manifest.get("decision_committed_at", ""),
        archive_name=archive_name,
        receipt_name=receipt_name,
        durability=DURABILITY_STATUS,
    )
    return text.encode("utf-8")


# ---------------------------------------------------------------------------
# Deterministic streaming archive creation
# ---------------------------------------------------------------------------


def _write_archive_stream(target_path: Path, all_members: list[_Member]) -> None:
    """Write members to a deterministic gzip-compressed tar, streaming.

    Metadata members (small) are written from in-memory bytes; evidence
    members (capture bodies, history) are streamed directly from their
    verified on-disk descriptor via :func:`_verified_source`, so large
    files are never materialized. Fixed tar metadata keeps the archive
    deterministic.
    """
    ordered = sorted(all_members, key=lambda m: m.archive_member)
    with target_path.open("wb") as out:
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=out,
            mtime=0, compresslevel=_TAR_COMPRESSLEVEL,
        ) as gz:
            # "w|" streams an uncompressed ustar tar into the gzip wrapper.
            with tarfile.open(fileobj=gz, mode="w|", format=tarfile.USTAR_FORMAT) as tar:
                for m in ordered:
                    info = tarfile.TarInfo(name=m.archive_member)
                    info.size = m.size
                    info.mtime = _TAR_MTIME
                    info.mode = _TAR_MODE
                    info.uid = _TAR_UID
                    info.gid = _TAR_GID
                    info.uname = _TAR_UNAME
                    info.gname = _TAR_GNAME
                    info.type = tarfile.REGTYPE
                    if m.data is not None:
                        tar.addfile(info, io.BytesIO(m.data))
                    else:
                        assert m.source is not None
                        with _verified_source(
                            m.source, m.sha256, m.size,
                            what=f"archive member {m.archive_member}",
                        ) as proxy:
                            tar.addfile(info, proxy)


def _stream_file_sha256(path: Path) -> tuple[str, int]:
    """Stream-hash a completed archive file without loading it."""
    h = hashlib.sha256()
    total = 0
    with path.open("rb") as f:
        while True:
            chunk = f.read(STREAM_CHUNK)
            if not chunk:
                break
            h.update(chunk)
            total += len(chunk)
    return h.hexdigest(), total


# ---------------------------------------------------------------------------
# Atomic finalization
# ---------------------------------------------------------------------------


def _atomic_write(final_path: Path, data: bytes) -> None:
    """Write ``data`` to a temp sibling then atomically rename into place."""
    final_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=final_path.name + ".", suffix=".tmp", dir=str(final_path.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_path, _OUTPUT_MODE)
        os.replace(tmp_path, final_path)
        os.chmod(final_path, _OUTPUT_MODE)
    except Exception:
        with contextlib.suppress(OSError):
            if tmp_path.exists():
                tmp_path.unlink()
        raise


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def _stem(target_date: str, run_id: str) -> str:
    return f"slumdog-shadow-{target_date}-{run_id}"


def create_bundle(*, run_dir: str | Path, output_dir: str | Path, root: str | Path) -> dict[str, Any]:
    """Create a deterministic full-payload bundle from a completed run."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise BundleSourceError(f"repository root not found or not a directory: {root}")

    run_dir_resolved = _safe_resolve_within_root(
        run_dir, root, what="run directory"
    )
    if not run_dir_resolved.is_dir():
        raise BundleSourceError(f"run directory not found: {run_dir!r}")
    manifest_path = run_dir_resolved / "manifest.json"
    payload_path = run_dir_resolved / "shadow_selections.json"
    if not manifest_path.is_file():
        raise BundleSourceError(
            f"partial run: completion marker manifest.json missing in {run_dir_resolved}"
        )
    if not payload_path.is_file():
        raise BundleSourceError(
            f"partial run: shadow_selections.json missing in {run_dir_resolved}"
        )

    manifest_abs, manifest_bytes = _read_metadata_file(
        manifest_path, root, what="run manifest"
    )
    payload_abs, payload_bytes = _read_metadata_file(
        payload_path, root, what="run payload"
    )

    manifest, member_map = _collect_members(
        manifest_bytes=manifest_bytes, payload_bytes=payload_bytes, root=root
    )
    run_id = manifest["run_id"]
    target_date = manifest["target_date"]

    member_map[PAYLOAD_MEMBER].source_paths = [_repo_rel(payload_abs, root)]
    member_map[MANIFEST_MEMBER].source_paths = [_repo_rel(manifest_abs, root)]

    stem = _stem(target_date, run_id)
    archive_name = f"{stem}.tar.gz"
    receipt_name = f"{stem}.bundle.json"

    readme_bytes = _build_readme(
        manifest=manifest, archive_name=archive_name, receipt_name=receipt_name
    )
    member_map[README_MEMBER] = _Member(
        archive_member=README_MEMBER, role="bundle_readme",
        size=len(readme_bytes), sha256=_sha256_bytes(readme_bytes),
        data=readme_bytes,
    )

    inventory_bytes = _build_inventory(manifest, member_map)
    inventory_member = _Member(
        archive_member=INVENTORY_MEMBER, role="bundle_inventory",
        size=len(inventory_bytes), sha256=_sha256_bytes(inventory_bytes),
        data=inventory_bytes,
    )
    all_members = list(member_map.values()) + [inventory_member]

    if len(all_members) > MAX_MEMBER_COUNT:
        raise BundleSourceError(
            f"bundle member count {len(all_members)} exceeds limit {MAX_MEMBER_COUNT}"
        )
    total_uncompressed = sum(m.size for m in all_members)
    if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES:
        raise BundleSourceError(
            f"bundle uncompressed total {total_uncompressed} exceeds limit "
            f"{MAX_TOTAL_UNCOMPRESSED_BYTES}"
        )

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    archive_path = out / archive_name
    receipt_path = out / receipt_name
    checksum_path = out / f"{archive_name}.sha256"
    for final in (archive_path, receipt_path, checksum_path):
        if final.exists() or final.is_symlink():
            raise BundleSourceError(
                f"refusing to overwrite existing output path: {final} "
                f"(no force/overwrite option; remove it explicitly)"
            )

    # 1) Write the archive to a temporary sibling (streamed).
    fd, tmp_arch_name = tempfile.mkstemp(
        prefix=archive_name + ".", suffix=".tmp", dir=str(out)
    )
    os.close(fd)
    tmp_arch = Path(tmp_arch_name)
    try:
        os.chmod(tmp_arch, _OUTPUT_MODE)
        _write_archive_stream(tmp_arch, all_members)

        # 2) Compute exact archive SHA-256/size from the finalized bytes.
        archive_sha, archive_size = _stream_file_sha256(tmp_arch)

        # 3) Build the receipt contract (archive bytes are now fixed).
        receipt = {
            "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
            "run_schema_version": RUN_SCHEMA_VERSION,
            "target_date": target_date,
            "run_id": run_id,
            "run_status": manifest.get("run_status"),
            "archive_filename": archive_name,
            "archive_sha256": archive_sha,
            "archive_bytes": archive_size,
            "archive_format": "tar.gz (deterministic: ustar, mtime=0, uid/gid=0, gzip mtime=0)",
            "source_manifest_sha256": _sha256_bytes(manifest_bytes),
            "source_payload_sha256": _sha256_bytes(payload_bytes),
            "payload_file_sha256": manifest.get("payload_file_sha256"),
            "frozen_baseline_config_canonical_sha256": FROZEN_BASELINE_CONFIG_SHA256,
            "shadow_declaration_canonical_sha256": manifest.get("declaration_sha256"),
            "input_digest": manifest.get("input_digest"),
            "decision_digest": manifest.get("decision_digest"),
            "decision_committed_at": manifest.get("decision_committed_at"),
            "safe_cutoff_utc": manifest.get("safe_cutoff_utc"),
            "bundle_created_at": _now_utc_iso(),
            "inventory_sha256": inventory_member.sha256,
            "inventory_bytes": inventory_member.size,
            "member_count": len(all_members),
            "content_member_count": len(member_map),
            "total_uncompressed_bytes": total_uncompressed,
            "durability_status": DURABILITY_STATUS,
            "authorizations": {
                "production_authorized": False,
                "shortlist_policy_authorized": False,
                "training_authorized": False,
                "threshold_optimization_authorized": False,
            },
        }

        # 4) SELF-VERIFICATION: run the full streaming verifier against the
        #    completed temp archive before anything is finalized. Do not
        #    trust successful tar writing alone.
        verify_archive_file(tmp_arch, receipt)

        # 5) Finalize in order: archive rename -> receipt -> marker last.
        os.replace(tmp_arch, archive_path)
        os.chmod(archive_path, _OUTPUT_MODE)
        tmp_arch = None
        receipt_bytes = json.dumps(receipt, indent=2, sort_keys=True).encode("utf-8")
        _atomic_write(receipt_path, receipt_bytes)
        _atomic_write(checksum_path, f"{archive_sha}  {archive_name}\n".encode("utf-8"))
    except Exception:
        # Partial output stays visibly incomplete; never masquerade as done.
        with contextlib.suppress(OSError):
            if tmp_arch is not None and tmp_arch.exists():
                tmp_arch.unlink()
        raise

    return {
        "archive_path": str(archive_path),
        "receipt_path": str(receipt_path),
        "checksum_path": str(checksum_path),
        "archive_filename": archive_name,
        "archive_sha256": archive_sha,
        "archive_bytes": archive_size,
        "run_id": run_id,
        "target_date": target_date,
        "member_count": len(all_members),
        "durability_status": DURABILITY_STATUS,
    }


# ---------------------------------------------------------------------------
# Streaming verification (never extracts)
# ---------------------------------------------------------------------------


def _read_member_bounded(exfile, info, *, limit: int, what: str) -> bytes:
    """Read at most ``limit`` bytes of a tar member via bounded chunks."""
    buf = io.BytesIO()
    remaining = limit + 1
    while remaining > 0:
        chunk = exfile.read(min(STREAM_CHUNK, remaining))
        if not chunk:
            break
        buf.write(chunk)
        remaining -= len(chunk)
    data = buf.getvalue()
    if len(data) > limit:
        raise BundleIntegrityError(
            f"{what}: member {info.name!r} exceeds {limit} byte metadata limit"
        )
    return data


def _pass_over_members(archive_path: Path):
    """Yield ``(tar, info, extractfile)`` over a freshly streamed tar open.

    The compressed archive is read through a counting/hashing wrapper so
    the archive SHA-256 and compressed size accumulate as tarfile streams
    the gzip data — the archive is never held in memory.
    """
    raw = archive_path.open("rb")
    counter = _CountingHashReader(
        raw, limit=MAX_COMPRESSED_ARCHIVE_BYTES, what="compressed archive"
    )
    try:
        tar = tarfile.open(fileobj=counter, mode="r:gz")
    except (tarfile.TarError, OSError, EOFError, gzip.BadGzipFile) as e:
        counter.close()
        raise BundleIntegrityError(f"corrupt or unreadable archive: {e}") from e
    return counter, tar


def verify_archive_file(archive_path: str | Path, receipt: dict[str, Any]) -> dict[str, Any]:
    """Stream-verify an archive against a receipt object. Never extracts.

    Two streaming passes over the compressed file:
      pass 1 — structural checks + per-member SHA-256/size (all members
               hashed in chunks and discarded) + archive hash;
      pass 2 — buffered read (under the metadata cap) and parse of only
               the small metadata members needed for semantic checks.
    """
    archive_path = Path(archive_path)

    # --- receipt schema / authorization flags -----------------------------
    if not isinstance(receipt, dict):
        raise BundleIntegrityError("receipt must be a JSON object")
    if receipt.get("bundle_schema_version") != BUNDLE_SCHEMA_VERSION:
        raise BundleIntegrityError(
            f"unsupported bundle schema version: {receipt.get('bundle_schema_version')!r}"
        )
    auth = receipt.get("authorizations")
    if not isinstance(auth, dict):
        raise BundleIntegrityError("receipt missing authorizations object")
    for flag in _AUTH_FLAGS:
        if auth.get(flag) is not False:
            raise BundleIntegrityError(
                f"receipt authorizations.{flag} must be false; bundle must not "
                f"authorize production/shortlist/training/threshold activity"
            )
    if receipt.get("durability_status") != DURABILITY_STATUS:
        raise BundleIntegrityError(
            f"receipt durability_status must be {DURABILITY_STATUS!r}"
        )

    # --- pass 1: structure + per-member hashing (all streamed) -------------
    member_digests: dict[str, tuple[str, int]] = {}
    member_count = 0
    total_uncompressed = 0
    archive_sha = ""
    archive_compressed = 0

    counter, tar = _pass_over_members(archive_path)
    try:
        while True:
            try:
                info = tar.next()
            except (tarfile.TarError, OSError, EOFError) as e:
                raise BundleIntegrityError(f"corrupt or truncated archive: {e}") from e
            if info is None:
                break
            member_count += 1
            if member_count > MAX_MEMBER_COUNT:
                raise BundleIntegrityError(
                    f"member count exceeds limit {MAX_MEMBER_COUNT}"
                )
            if info.type not in (tarfile.REGTYPE, tarfile.AREGTYPE):
                raise BundleIntegrityError(
                    f"unsupported archive member type for {info.name!r}: only regular "
                    f"files are permitted (no symlinks, hard links, devices, FIFOs, "
                    f"directories, PAX headers)"
                )
            name = info.name
            if not _safe_archive_member_name(name):
                raise BundleIntegrityError(f"unsafe archive member path: {name!r}")
            if name in member_digests:
                raise BundleIntegrityError(f"duplicate archive member: {name!r}")
            if info.size < 0 or info.size > MAX_EVIDENCE_MEMBER_BYTES:
                raise BundleIntegrityError(
                    f"archive member {name!r} has unsafe declared size {info.size} "
                    f"(limit {MAX_EVIDENCE_MEMBER_BYTES})"
                )
            total_uncompressed += info.size
            if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES:
                raise BundleIntegrityError(
                    f"declared uncompressed total exceeds limit "
                    f"{MAX_TOTAL_UNCOMPRESSED_BYTES} bytes"
                )

            exfile = tar.extractfile(info)
            if exfile is None:
                raise BundleIntegrityError(f"cannot read archive member: {name!r}")
            h = hashlib.sha256()
            actual = 0
            try:
                while True:
                    chunk = exfile.read(STREAM_CHUNK)
                    if not chunk:
                        break
                    h.update(chunk)
                    actual += len(chunk)
                    if actual > info.size:
                        raise BundleIntegrityError(
                            f"archive member {name!r} longer than its declared size"
                        )
            except (tarfile.TarError, OSError, EOFError) as e:
                raise BundleIntegrityError(f"corrupt or truncated member {name!r}: {e}") from e
            if actual != info.size:
                raise BundleIntegrityError(
                    f"archive member size mismatch: {name!r} header={info.size} actual={actual}"
                )
            member_digests[name] = (h.hexdigest(), actual)
    finally:
        try:
            tar.close()
        finally:
            archive_sha = counter.sha.hexdigest()
            archive_compressed = counter.count
            counter.close()

    # --- archive exact-byte hash + size against receipt --------------------
    if archive_sha != receipt.get("archive_sha256"):
        raise BundleIntegrityError(
            f"archive SHA-256 mismatch: actual {archive_sha} "
            f"receipt {receipt.get('archive_sha256')}"
        )
    if archive_compressed != receipt.get("archive_bytes"):
        raise BundleIntegrityError(
            f"archive size mismatch: actual {archive_compressed} "
            f"receipt {receipt.get('archive_bytes')}"
        )
    if archive_compressed > MAX_COMPRESSED_ARCHIVE_BYTES:
        raise BundleIntegrityError("compressed archive exceeds size limit")

    # --- inventory: bounded re-read + parse (pass 2 limited) ---------------
    parsed: dict[str, Any] = {}
    needed = {
        MANIFEST_MEMBER: "run_manifest",
        PAYLOAD_MEMBER: "run_payload",
        FROZEN_CONFIG_MEMBER: "frozen_baseline_config",
        DECLARATION_MEMBER: "shadow_declaration",
        INVENTORY_MEMBER: "bundle_inventory",
    }
    counter2, tar2 = _pass_over_members(archive_path)
    try:
        while True:
            try:
                info = tar2.next()
            except (tarfile.TarError, OSError, EOFError) as e:
                raise BundleIntegrityError(f"corrupt or truncated archive: {e}") from e
            if info is None:
                break
            if info.name in needed:
                if info.size > MAX_METADATA_BYTES:
                    raise BundleIntegrityError(
                        f"metadata member {info.name!r} exceeds {MAX_METADATA_BYTES} "
                        f"byte metadata limit (declared {info.size})"
                    )
                exfile = tar2.extractfile(info)
                data = _read_member_bounded(
                    exfile, info, limit=MAX_METADATA_BYTES, what="metadata member"
                )
                try:
                    parsed[info.name] = json.loads(data.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as e:
                    raise BundleIntegrityError(
                        f"archive member {info.name!r} is not valid JSON: {e}"
                    ) from e
    finally:
        tar2.close()
        counter2.close()

    if INVENTORY_MEMBER not in parsed:
        raise BundleIntegrityError("required archive member missing: inventory")
    inventory = parsed[INVENTORY_MEMBER]
    if not isinstance(inventory, dict):
        raise BundleIntegrityError("inventory must be a JSON object")
    if inventory.get("bundle_schema_version") != BUNDLE_SCHEMA_VERSION:
        raise BundleIntegrityError("inventory bundle_schema_version mismatch")
    inv_members = inventory.get("members")
    if not isinstance(inv_members, list):
        raise BundleIntegrityError("inventory.members must be a list")

    expected_names = {INVENTORY_MEMBER}
    for row in inv_members:
        if not isinstance(row, dict):
            raise BundleIntegrityError("inventory member row must be an object")
        for key in ("archive_member", "role", "sha256", "bytes", "source_paths"):
            if key not in row:
                raise BundleIntegrityError(f"inventory member row missing {key!r}")
        name = row["archive_member"]
        if not isinstance(name, str) or not _safe_archive_member_name(name):
            raise BundleIntegrityError(f"inventory lists unsafe member: {name!r}")
        if name == INVENTORY_MEMBER:
            raise BundleIntegrityError("inventory must not list itself")
        if name in expected_names:
            raise BundleIntegrityError(f"inventory lists duplicate member: {name!r}")
        expected_names.add(name)
        digest = member_digests.get(name)
        if digest is None:
            raise BundleIntegrityError(f"missing archive member listed in inventory: {name}")
        if digest[0] != row["sha256"]:
            raise BundleIntegrityError(f"member SHA-256 mismatch: {name}")
        if digest[1] != row["bytes"]:
            raise BundleIntegrityError(f"member byte size mismatch: {name}")

    unexpected = set(member_digests.keys()) - expected_names
    if unexpected:
        raise BundleIntegrityError(
            f"unexpected archive members not listed in inventory: {sorted(unexpected)}"
        )
    if receipt.get("member_count") != member_count:
        raise BundleIntegrityError(
            f"receipt member_count {receipt.get('member_count')} != actual {member_count}"
        )
    if inventory.get("content_member_count") != len(inv_members):
        raise BundleIntegrityError("inventory content_member_count mismatch")
    if receipt.get("total_uncompressed_bytes") != total_uncompressed:
        raise BundleIntegrityError(
            f"total_uncompressed_bytes mismatch: receipt "
            f"{receipt.get('total_uncompressed_bytes')} actual {total_uncompressed}"
        )
    inv_sha, inv_size = member_digests[INVENTORY_MEMBER]
    if inv_sha != receipt.get("inventory_sha256"):
        raise BundleIntegrityError("inventory SHA-256 does not match receipt")
    if inv_size != receipt.get("inventory_bytes"):
        raise BundleIntegrityError("inventory byte size does not match receipt")

    # --- run manifest / payload -------------------------------------------
    manifest = parsed.get(MANIFEST_MEMBER)
    payload = parsed.get(PAYLOAD_MEMBER)
    if not isinstance(manifest, dict) or not isinstance(payload, dict):
        raise BundleIntegrityError("manifest and payload must be JSON objects")
    if manifest.get("version") != RUN_SCHEMA_VERSION:
        raise BundleIntegrityError(
            f"unsupported run schema/version: {manifest.get('version')!r}"
        )
    if manifest.get("run_status") not in _COMPLETED_RUN_STATUSES:
        raise BundleIntegrityError(
            f"bundled run is not a completed run: run_status={manifest.get('run_status')!r}"
        )
    payload_digest = member_digests[PAYLOAD_MEMBER]
    if payload_digest[0] != manifest.get("payload_file_sha256"):
        raise BundleIntegrityError("payload SHA-256 does not match manifest")
    run_id = manifest.get("run_id")
    target_date = manifest.get("target_date")
    if payload.get("run_id") != run_id or receipt.get("run_id") != run_id:
        raise BundleIntegrityError("run_id mismatch across payload/manifest/receipt")
    if payload.get("target_date") != target_date or receipt.get("target_date") != target_date:
        raise BundleIntegrityError("target_date mismatch across payload/manifest/receipt")

    # --- recompute digests + run id from bundled provenance ----------------
    input_digest = manifest.get("input_digest")
    decision_digest = manifest.get("decision_digest")
    ip = manifest.get("input_provenance")
    dp = manifest.get("decision_provenance")
    if isinstance(ip, dict) and _canonical_sha256(ip) != input_digest:
        raise BundleIntegrityError("recomputed input_digest does not match manifest")
    if isinstance(dp, dict) and _canonical_sha256(dp) != decision_digest:
        raise BundleIntegrityError("recomputed decision_digest does not match manifest")
    if receipt.get("input_digest") != input_digest:
        raise BundleIntegrityError("receipt input_digest does not match manifest")
    if receipt.get("decision_digest") != decision_digest:
        raise BundleIntegrityError("receipt decision_digest does not match manifest")
    committed_at = manifest.get("decision_committed_at")
    recomputed_run_id = _sha256_bytes(_canonical_json_bytes({
        "version": RUN_SCHEMA_VERSION,
        "input_digest": input_digest,
        "decision_digest": decision_digest,
        "decision_committed_at": committed_at,
    }))[:16]
    if recomputed_run_id != run_id:
        raise BundleIntegrityError(
            f"recomputed run_id {recomputed_run_id} does not match manifest {run_id}"
        )

    # --- frozen configs: recompute canonical hashes from bundled bytes -----
    frozen_obj = parsed.get(FROZEN_CONFIG_MEMBER)
    if not isinstance(frozen_obj, dict) or _canonical_sha256(frozen_obj) != FROZEN_BASELINE_CONFIG_SHA256:
        raise BundleIntegrityError("bundled frozen baseline config canonical SHA-256 mismatch")
    if manifest.get("frozen_baseline_config_sha256") != FROZEN_BASELINE_CONFIG_SHA256:
        raise BundleIntegrityError("manifest frozen_baseline_config_sha256 mismatch")
    if receipt.get("frozen_baseline_config_canonical_sha256") != FROZEN_BASELINE_CONFIG_SHA256:
        raise BundleIntegrityError("receipt frozen baseline config hash mismatch")

    decl_obj = parsed.get(DECLARATION_MEMBER)
    if not isinstance(decl_obj, dict):
        raise BundleIntegrityError("bundled shadow declaration is not a JSON object")
    decl_canonical = _canonical_sha256(decl_obj)
    if decl_canonical != manifest.get("declaration_sha256"):
        raise BundleIntegrityError(
            "bundled shadow declaration canonical SHA-256 does not match manifest"
        )
    if receipt.get("shadow_declaration_canonical_sha256") != decl_canonical:
        raise BundleIntegrityError(
            "receipt shadow declaration canonical hash does not match bundled bytes"
        )
    decl_auth = decl_obj.get("authorizations", {})
    for flag in _AUTH_FLAGS:
        if decl_auth.get(flag) is not False:
            raise BundleIntegrityError(
                f"bundled declaration authorizations.{flag} must be false"
            )

    return {
        "status": "BUNDLE_VERIFIED",
        "run_id": run_id,
        "target_date": target_date,
        "archive_sha256": archive_sha,
        "member_count": member_count,
        "total_uncompressed_bytes": total_uncompressed,
        "input_digest": input_digest,
        "decision_digest": decision_digest,
        "durability_status": receipt.get("durability_status"),
    }


def verify_bundle(*, bundle_path: str | Path, receipt_path: str | Path) -> dict[str, Any]:
    """Independently verify a bundle archive against its receipt in memory."""
    bundle_path = Path(bundle_path)
    receipt_path = Path(receipt_path)
    if not bundle_path.is_file():
        raise BundleIntegrityError(f"bundle archive not found: {bundle_path}")
    if not receipt_path.is_file():
        raise BundleIntegrityError(f"bundle receipt not found: {receipt_path}")
    with receipt_path.open("rb") as f:
        data = f.read(MAX_METADATA_BYTES + 1)
    if len(data) > MAX_METADATA_BYTES:
        raise BundleIntegrityError(
            f"bundle receipt exceeds {MAX_METADATA_BYTES} byte metadata limit"
        )
    try:
        receipt = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise BundleIntegrityError(f"receipt is not valid JSON: {e}") from e
    result = verify_archive_file(bundle_path, receipt)
    # Optional sibling checksum marker (sha256sum format), if present.
    sibling = bundle_path.with_name(bundle_path.name + ".sha256")
    if sibling.is_file():
        marker = sibling.read_text().strip().split()
        if not marker or marker[0] != result["archive_sha256"]:
            raise BundleIntegrityError(
                f"sibling .sha256 marker does not match archive "
                f"{result['archive_sha256']}"
            )
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m slumdog.shadow_bundle",
        description=(
            "Milestone 7B: package a completed shadow run and every input "
            "needed to audit/reproduce it into a deterministic, independently "
            "verifiable full-payload bundle (post-decision preservation only; "
            "bounded-memory streaming create and verify)."
        ),
    )
    sub = p.add_subparsers(dest="command", required=True)

    pc = sub.add_parser(
        "create",
        help="Create a deterministic full-payload bundle from a completed run.",
    )
    pc.add_argument("--run-dir", required=True, type=Path,
                    help="Completed run directory containing manifest.json and "
                         "shadow_selections.json")
    pc.add_argument("--output-dir", required=True, type=Path,
                    help="Explicit output directory for the archive/receipt/checksum "
                         "(created if absent; never overwrites existing outputs)")
    pc.add_argument("--root", required=True, type=Path,
                    help="Approved repository root containing config/ and data/")

    pv = sub.add_parser(
        "verify",
        help="Independently verify a bundle archive against its receipt (streamed, "
             "in memory; never extracts).",
    )
    pv.add_argument("--bundle", required=True, type=Path,
                    help="Path to the slumdog-shadow-<date>-<run>.tar.gz archive")
    pv.add_argument("--receipt", required=True, type=Path,
                    help="Path to the slumdog-shadow-<date>-<run>.bundle.json receipt")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    try:
        if args.command == "create":
            result = create_bundle(
                run_dir=args.run_dir, output_dir=args.output_dir, root=args.root
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        if args.command == "verify":
            result = verify_bundle(bundle_path=args.bundle, receipt_path=args.receipt)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
    except BundleError as e:
        if args.command == "verify":
            print(f"BUNDLE_VERIFY_FAILED: {e}", file=sys.stderr)
        else:
            print(f"BUNDLE_CREATE_FAILED: {e}", file=sys.stderr)
        return 2
    print(f"unknown command: {args.command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
