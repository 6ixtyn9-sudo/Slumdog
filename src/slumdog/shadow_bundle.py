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

CLI:

    python -m slumdog.shadow_bundle create \\
        --run-dir <completed-run-dir> --output-dir <dir> --root <repo-root>
    python -m slumdog.shadow_bundle verify \\
        --bundle <archive.tar.gz> --receipt <archive.bundle.json>

Verification never extracts files to disk: every archive member is read
and hashed in memory.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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

# Durability status recorded on a successfully created bundle. The archive
# is a portable, independently copyable export; it is NOT a second backup
# until an independently downloaded copy verifies.
DURABILITY_STATUS = "LOCAL_EXPORT_READY_FOR_INDEPENDENT_COPY"

# Completed (non-blocked) run statuses emitted by Milestone 7.
_COMPLETED_RUN_STATUSES = frozenset({
    "SHADOW_SELECTIONS_EMITTED",
    "SHADOW_NO_SELECTION",
})

# Logical archive layout.
ARCHIVE_PREFIX = "bundle/"
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

# Verification reads a (potentially untrusted) archive into memory. Bound both
# the compressed archive and the decompressed total so a malformed or
# decompression-bomb archive is rejected instead of exhausting memory. These
# are far above any genuine shadow bundle (a few tens of MB).
_MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024        # 2 GiB compressed
_MAX_UNCOMPRESSED_TOTAL = 4 * 1024 * 1024 * 1024   # 4 GiB decompressed

# Authorization flags that MUST be false on every bundle (the bundle tool
# is post-decision preservation; it never authorizes anything).
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
    # Reject symlinks anywhere along the path inside the root.
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


def _read_evidence_file(
    path: str | Path, root: Path, *, expected_sha256: str | None, what: str
) -> tuple[Path, bytes]:
    """Read a referenced evidence file, requiring regular-file + exact hash."""
    resolved = _safe_resolve_within_root(path, root, what=what)
    if not resolved.is_file():
        raise BundleSourceError(f"{what}: missing or not a regular file: {path!r}")
    data = resolved.read_bytes()
    actual = _sha256_bytes(data)
    if expected_sha256 is not None and actual != expected_sha256:
        raise BundleSourceError(
            f"{what}: exact-byte SHA-256 mismatch for {path!r}: "
            f"expected {expected_sha256} actual {actual}"
        )
    return resolved, data


# ---------------------------------------------------------------------------
# Member collection
# ---------------------------------------------------------------------------


class _Member:
    __slots__ = ("archive_member", "role", "sha256", "size", "data", "source_paths")

    def __init__(
        self,
        *,
        archive_member: str,
        role: str,
        data: bytes,
        source_paths: list[str],
    ) -> None:
        self.archive_member = archive_member
        self.role = role
        self.data = data
        self.sha256 = _sha256_bytes(data)
        self.size = len(data)
        self.source_paths = sorted(set(source_paths))


def _safe_extension(name: str, *, allowed: tuple[str, ...]) -> str:
    """Return a lowercase, whitelisted extension for a content-addressed name."""
    suffix = Path(name).suffix.lower()
    if suffix not in allowed:
        raise BundleSourceError(
            f"unsupported evidence file extension {suffix!r} for {name!r}; "
            f"allowed: {', '.join(allowed)}"
        )
    return suffix


def _collect_members(
    *, manifest_bytes: bytes, payload_bytes: bytes, root: Path
) -> tuple[dict[str, Any], dict[str, _Member]]:
    """Validate the completed run and collect every evidence member.

    Returns ``(manifest, member_map)`` where ``member_map`` is keyed by
    archive member path.
    """
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise BundleSourceError(f"manifest is corrupt / not valid JSON: {e}") from e
    if not isinstance(manifest, dict):
        raise BundleSourceError("manifest must be a JSON object")

    # --- run schema / version / completion status -------------------------
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

    # --- payload -----------------------------------------------------------
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise BundleSourceError(f"payload is corrupt / not valid JSON: {e}") from e
    if not isinstance(payload, dict):
        raise BundleSourceError("payload must be a JSON object")
    payload_sha = _sha256_bytes(payload_bytes)
    expected_payload_sha = manifest.get("payload_file_sha256")
    if payload_sha != expected_payload_sha:
        raise BundleSourceError(
            f"payload exact-byte SHA-256 does not match manifest: "
            f"actual {payload_sha} manifest {expected_payload_sha}"
        )
    if payload.get("run_id") != run_id:
        raise BundleSourceError("payload run_id does not match manifest run_id")
    if payload.get("target_date") != target_date:
        raise BundleSourceError("payload target_date does not match manifest target_date")

    members: dict[str, _Member] = {}

    def _add(member: _Member) -> None:
        existing = members.get(member.archive_member)
        if existing is not None:
            # Content-addressed dedup: identical bytes map to one member;
            # every original reference is still represented in source_paths.
            if existing.sha256 != member.sha256:
                raise BundleSourceError(
                    f"archive member collision on {member.archive_member!r} with "
                    f"different content"
                )
            existing.source_paths = sorted(set(existing.source_paths) | set(member.source_paths))
            return
        members[member.archive_member] = member

    # Run payload + manifest (exact original bytes preserved).
    _add(_Member(
        archive_member=PAYLOAD_MEMBER, role="run_payload",
        data=payload_bytes, source_paths=[],  # source path set by caller
    ))
    _add(_Member(
        archive_member=MANIFEST_MEMBER, role="run_manifest",
        data=manifest_bytes, source_paths=[],
    ))

    # --- frozen configs ----------------------------------------------------
    frozen_path = root / FROZEN_BASELINE_CONFIG_REPO_PATH
    frozen_abs, frozen_bytes = _read_evidence_file(
        frozen_path, root, expected_sha256=None, what="frozen baseline config"
    )
    try:
        frozen_obj = json.loads(frozen_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise BundleSourceError(f"frozen baseline config is not valid JSON: {e}") from e
    frozen_canonical = _canonical_sha256(frozen_obj)
    if frozen_canonical != FROZEN_BASELINE_CONFIG_SHA256:
        raise BundleSourceError(
            f"frozen baseline config canonical SHA-256 mismatch: "
            f"actual {frozen_canonical} expected {FROZEN_BASELINE_CONFIG_SHA256}"
        )
    if manifest.get("frozen_baseline_config_sha256") != FROZEN_BASELINE_CONFIG_SHA256:
        raise BundleSourceError(
            "manifest frozen_baseline_config_sha256 does not match the frozen config"
        )
    _add(_Member(
        archive_member=FROZEN_CONFIG_MEMBER, role="frozen_baseline_config",
        data=frozen_bytes, source_paths=[_repo_rel(frozen_abs, root)],
    ))

    decl_path = root / SHADOW_DECLARATION_REPO_PATH
    decl_abs, decl_bytes = _read_evidence_file(
        decl_path, root, expected_sha256=None, what="shadow declaration"
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
    # Fail-closed authorization gates on the declaration that produced the run.
    decl_auth = decl_obj.get("authorizations", {}) if isinstance(decl_obj, dict) else {}
    for flag in _AUTH_FLAGS:
        if decl_auth.get(flag) is not False:
            raise BundleSourceError(
                f"shadow declaration authorizations.{flag} must be false (fail closed)"
            )
    _add(_Member(
        archive_member=DECLARATION_MEMBER, role="shadow_declaration",
        data=decl_bytes, source_paths=[_repo_rel(decl_abs, root)],
    ))

    # --- capture provenance ------------------------------------------------
    cp = manifest.get("capture_provenance")
    if not isinstance(cp, dict):
        raise BundleSourceError("manifest missing capture_provenance")
    receipt_path = cp.get("receipt_path")
    receipt_expected = cp.get("receipt_sha256")
    if not receipt_path or not receipt_expected:
        raise BundleSourceError("capture_provenance missing receipt path/hash")
    receipt_abs, receipt_bytes = _read_evidence_file(
        receipt_path, root, expected_sha256=receipt_expected,
        what="capture receipt",
    )
    # Cross-anchor the receipt hash to the input provenance block as well.
    ip = manifest.get("input_provenance")
    if isinstance(ip, dict) and ip.get("capture_receipt_sha256") not in (None, receipt_expected):
        raise BundleSourceError(
            "input_provenance.capture_receipt_sha256 disagrees with capture_provenance"
        )
    _add(_Member(
        archive_member=CAPTURE_RECEIPT_MEMBER, role="capture_receipt",
        data=receipt_bytes, source_paths=[_repo_rel(receipt_abs, root)],
    ))

    raw_inputs = cp.get("raw_input_sha256")
    if not isinstance(raw_inputs, dict):
        raise BundleSourceError("capture_provenance missing raw_input_sha256")
    for raw_path in sorted(raw_inputs.keys()):
        expected = raw_inputs[raw_path]
        abs_path, data = _read_evidence_file(
            raw_path, root, expected_sha256=expected, what="capture input"
        )
        rel = _repo_rel(abs_path, root)
        name = Path(raw_path).name.lower()
        if name.endswith(".json"):
            ext = _safe_extension(name, allowed=(".json",))
            member_path = f"{SIDECAR_DIR}/{expected}{ext}"
            role = "capture_sidecar"
        elif name.endswith(".txt"):
            ext = _safe_extension(name, allowed=(".txt",))
            member_path = f"{BODY_DIR}/{expected}{ext}"
            role = "capture_body"
        else:
            raise BundleSourceError(
                f"unsupported capture input type for {raw_path!r} "
                f"(expected sidecar .json or body .txt)"
            )
        _add(_Member(
            archive_member=member_path, role=role, data=data,
            source_paths=[rel],
        ))

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
        abs_path, data = _read_evidence_file(
            hist_path, root, expected_sha256=expected, what="history input"
        )
        if hist_path in hist_bytes and hist_bytes[hist_path] != len(data):
            raise BundleSourceError(
                f"history input size mismatch for {hist_path!r}: "
                f"actual {len(data)} manifest {hist_bytes[hist_path]}"
            )
        rel = _repo_rel(abs_path, root)
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
        member_path = f"{HISTORY_DIR}/{expected}{ext}"
        _add(_Member(
            archive_member=member_path, role="history_input", data=data,
            source_paths=[rel],
        ))

    return manifest, members


# ---------------------------------------------------------------------------
# Inventory + README
# ---------------------------------------------------------------------------


def _build_inventory(
    *, manifest: dict[str, Any], members: dict[str, _Member]
) -> bytes:
    """Build the deterministic canonical inventory JSON bytes.

    Lists every content member (including README) but NOT inventory.json
    itself (an inventory cannot record its own hash). The external bundle
    receipt records the inventory hash and the total member count.
    """
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
# Deterministic archive
# ---------------------------------------------------------------------------


def _build_archive_bytes(all_members: list[_Member]) -> bytes:
    """Serialize members to a deterministic gzip-compressed tar.

    Fixed: member order (sorted logical path), UID/GID 0, empty
    owner/group, mode 0644, mtime 0, regular files only, ustar format,
    gzip mtime 0 and fixed compression level. Same input bytes always
    produce the same archive bytes and SHA-256.
    """
    ordered = sorted(all_members, key=lambda m: m.archive_member)
    buf = io.BytesIO()
    with gzip.GzipFile(
        filename="", mode="wb", fileobj=buf,
        mtime=0, compresslevel=_TAR_COMPRESSLEVEL,
    ) as gz:
        # "w|" streams an uncompressed ustar tar into the gzip wrapper
        # (a plain "w" tar into a gzip fileobj would emit an invalid stream).
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
                tar.addfile(info, io.BytesIO(m.data))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Atomic finalization
# ---------------------------------------------------------------------------


def _atomic_write(final_path: Path, data: bytes) -> None:
    """Write ``data`` to a temp sibling then atomically rename into place.

    The temp file is created with mode 0600; the final file is chmod 0600.
    Never overwrites an existing final path (the caller pre-checks).
    """
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
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
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

    # The run directory must live inside the approved root.
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

    manifest_bytes = manifest_path.read_bytes()
    payload_bytes = payload_path.read_bytes()

    manifest, member_map = _collect_members(
        manifest_bytes=manifest_bytes, payload_bytes=payload_bytes, root=root
    )
    run_id = manifest["run_id"]
    target_date = manifest["target_date"]

    # Record the run file source paths (exact original bytes preserved).
    member_map[PAYLOAD_MEMBER].source_paths = [_repo_rel(payload_path, root)]
    member_map[MANIFEST_MEMBER].source_paths = [_repo_rel(manifest_path, root)]

    stem = _stem(target_date, run_id)
    archive_name = f"{stem}.tar.gz"
    receipt_name = f"{stem}.bundle.json"

    # README first (content member, listed in inventory).
    readme_bytes = _build_readme(
        manifest=manifest, archive_name=archive_name, receipt_name=receipt_name
    )
    member_map[README_MEMBER] = _Member(
        archive_member=README_MEMBER, role="bundle_readme",
        data=readme_bytes, source_paths=[],
    )

    # Inventory lists every content member except inventory.json itself.
    inventory_bytes = _build_inventory(manifest=manifest, members=member_map)
    inventory_member = _Member(
        archive_member=INVENTORY_MEMBER, role="bundle_inventory",
        data=inventory_bytes, source_paths=[],
    )

    all_members = list(member_map.values()) + [inventory_member]
    archive_bytes = _build_archive_bytes(all_members)
    archive_sha = _sha256_bytes(archive_bytes)

    member_count = len(all_members)
    total_uncompressed = sum(m.size for m in all_members)

    receipt = {
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "run_schema_version": RUN_SCHEMA_VERSION,
        "target_date": target_date,
        "run_id": run_id,
        "run_status": manifest.get("run_status"),
        "archive_filename": archive_name,
        "archive_sha256": archive_sha,
        "archive_bytes": len(archive_bytes),
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
        "member_count": member_count,
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
    receipt_bytes = json.dumps(receipt, indent=2, sort_keys=True).encode("utf-8")
    checksum_bytes = f"{archive_sha}  {archive_name}\n".encode("utf-8")

    # Output directory is explicit; refuse any pre-existing final path.
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

    # Finalize in order: archive, then receipt, then checksum marker last.
    # An interruption before the checksum leaves no valid completion marker.
    _atomic_write(archive_path, archive_bytes)
    try:
        _atomic_write(receipt_path, receipt_bytes)
        _atomic_write(checksum_path, checksum_bytes)
    except Exception:
        # Partial output stays visibly incomplete; never masquerade as done.
        raise

    return {
        "archive_path": str(archive_path),
        "receipt_path": str(receipt_path),
        "checksum_path": str(checksum_path),
        "archive_filename": archive_name,
        "archive_sha256": archive_sha,
        "archive_bytes": len(archive_bytes),
        "run_id": run_id,
        "target_date": target_date,
        "member_count": member_count,
        "durability_status": DURABILITY_STATUS,
    }


# ---------------------------------------------------------------------------
# Verify (pure, in-memory; never extracts)
# ---------------------------------------------------------------------------


def _safe_archive_member_name(name: str) -> bool:
    """Reject absolute paths, '..', backslashes, and non-bundle paths."""
    if not name or name in (".", "/"):
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


def _read_archive_members(archive_bytes: bytes) -> dict[str, bytes]:
    """Read every regular archive member into memory.

    Never extracts to disk. Rejects unsafe names, duplicate names, and any
    non-regular member type (symlinks, hard links, devices, FIFOs, dirs).
    """
    try:
        tar = tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz")
    except (tarfile.TarError, OSError, EOFError, gzip.BadGzipFile) as e:
        raise BundleIntegrityError(f"corrupt or unreadable archive: {e}") from e
    members: dict[str, bytes] = {}
    total = 0
    with tar:
        for info in tar.getmembers():
            if info.type not in (tarfile.REGTYPE, tarfile.AREGTYPE):
                raise BundleIntegrityError(
                    f"unsupported archive member type for {info.name!r}: "
                    f"only regular files are permitted (no symlinks, hard links, "
                    f"devices, FIFOs, directories)"
                )
            if info.size < 0 or info.size > _MAX_UNCOMPRESSED_TOTAL:
                raise BundleIntegrityError(
                    f"archive member {info.name!r} has unsafe declared size {info.size}"
                )
            name = info.name
            if not _safe_archive_member_name(name):
                raise BundleIntegrityError(f"unsafe archive member path: {name!r}")
            if name in members:
                raise BundleIntegrityError(f"duplicate archive member: {name!r}")
            extracted = tar.extractfile(info)
            if extracted is None:
                raise BundleIntegrityError(f"cannot read archive member: {name!r}")
            # Read exactly the declared (already bounded) byte count, then
            # confirm no extra data follows — the tar header size is
            # authoritative, so this never allocates more than the real member.
            data = extracted.read(info.size)
            if len(data) != info.size or extracted.read(1):
                raise BundleIntegrityError(
                    f"archive member size mismatch: {name!r} header={info.size} "
                    f"actual={len(data)}"
                )
            total += len(data)
            if total > _MAX_UNCOMPRESSED_TOTAL:
                raise BundleIntegrityError(
                    f"archive decompressed total exceeds safety limit "
                    f"({_MAX_UNCOMPRESSED_TOTAL} bytes)"
                )
            members[name] = data
    return members


def _require_member(members: dict[str, bytes], name: str) -> bytes:
    if name not in members:
        raise BundleIntegrityError(f"required archive member missing: {name}")
    return members[name]


def _parse_json_member(name: str, data: bytes) -> Any:
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise BundleIntegrityError(f"archive member {name!r} is not valid JSON: {e}") from e


def verify_bundle(*, bundle_path: str | Path, receipt_path: str | Path) -> dict[str, Any]:
    """Independently verify a bundle archive against its receipt in memory."""
    bundle_path = Path(bundle_path)
    receipt_path = Path(receipt_path)
    if not bundle_path.is_file():
        raise BundleIntegrityError(f"bundle archive not found: {bundle_path}")
    if not receipt_path.is_file():
        raise BundleIntegrityError(f"bundle receipt not found: {receipt_path}")

    archive_bytes = bundle_path.read_bytes()
    if len(archive_bytes) > _MAX_ARCHIVE_BYTES:
        raise BundleIntegrityError(
            f"archive exceeds maximum compressed size ({_MAX_ARCHIVE_BYTES} bytes)"
        )
    archive_sha = _sha256_bytes(archive_bytes)
    try:
        receipt = json.loads(receipt_path.read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise BundleIntegrityError(f"receipt is not valid JSON: {e}") from e
    if not isinstance(receipt, dict):
        raise BundleIntegrityError("receipt must be a JSON object")
    claimed_total = receipt.get("total_uncompressed_bytes")
    if isinstance(claimed_total, int) and claimed_total > _MAX_UNCOMPRESSED_TOTAL:
        raise BundleIntegrityError(
            f"receipt claims uncompressed size above the safety limit "
            f"({_MAX_UNCOMPRESSED_TOTAL} bytes)"
        )

    # --- receipt schema / authorization flags ------------------------------
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

    # --- archive exact-byte hash + size against receipt --------------------
    if archive_sha != receipt.get("archive_sha256"):
        raise BundleIntegrityError(
            f"archive SHA-256 mismatch: actual {archive_sha} "
            f"receipt {receipt.get('archive_sha256')}"
        )
    if len(archive_bytes) != receipt.get("archive_bytes"):
        raise BundleIntegrityError(
            f"archive size mismatch: actual {len(archive_bytes)} "
            f"receipt {receipt.get('archive_bytes')}"
        )

    # Optional sibling checksum marker (sha256sum format).
    sibling_checksum = bundle_path.with_name(bundle_path.name + ".sha256")
    if sibling_checksum.is_file():
        marker = sibling_checksum.read_text().strip().split()[0]
        if marker != archive_sha:
            raise BundleIntegrityError(
                f"sibling .sha256 marker {marker} does not match archive {archive_sha}"
            )

    # --- read + structurally validate archive members ----------------------
    members = _read_archive_members(archive_bytes)

    inventory_bytes = _require_member(members, INVENTORY_MEMBER)
    if _sha256_bytes(inventory_bytes) != receipt.get("inventory_sha256"):
        raise BundleIntegrityError("inventory SHA-256 does not match receipt")
    if len(inventory_bytes) != receipt.get("inventory_bytes"):
        raise BundleIntegrityError("inventory byte size does not match receipt")
    inventory = _parse_json_member(INVENTORY_MEMBER, inventory_bytes)
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
        data = members.get(name)
        if data is None:
            raise BundleIntegrityError(f"missing archive member listed in inventory: {name}")
        if _sha256_bytes(data) != row["sha256"]:
            raise BundleIntegrityError(f"member SHA-256 mismatch: {name}")
        if len(data) != row["bytes"]:
            raise BundleIntegrityError(f"member byte size mismatch: {name}")

    unexpected = set(members.keys()) - expected_names
    if unexpected:
        raise BundleIntegrityError(
            f"unexpected archive members not listed in inventory: {sorted(unexpected)}"
        )
    if receipt.get("member_count") != len(members):
        raise BundleIntegrityError(
            f"receipt member_count {receipt.get('member_count')} != "
            f"actual {len(members)}"
        )
    if inventory.get("content_member_count") != len(inv_members):
        raise BundleIntegrityError("inventory content_member_count mismatch")
    total_uncompressed = sum(len(d) for d in members.values())
    if receipt.get("total_uncompressed_bytes") != total_uncompressed:
        raise BundleIntegrityError(
            f"total_uncompressed_bytes mismatch: receipt "
            f"{receipt.get('total_uncompressed_bytes')} actual {total_uncompressed}"
        )

    # --- run manifest / payload -------------------------------------------
    manifest = _parse_json_member(MANIFEST_MEMBER, _require_member(members, MANIFEST_MEMBER))
    payload = _parse_json_member(PAYLOAD_MEMBER, _require_member(members, PAYLOAD_MEMBER))
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
    # Payload exact-byte hash against the manifest.
    payload_actual_sha = _sha256_bytes(members[PAYLOAD_MEMBER])
    if payload_actual_sha != manifest.get("payload_file_sha256"):
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

    # --- frozen configs: recompute canonical hashes from bundled bytes ------
    frozen_data = _require_member(members, FROZEN_CONFIG_MEMBER)
    frozen_obj = _parse_json_member(FROZEN_CONFIG_MEMBER, frozen_data)
    if _canonical_sha256(frozen_obj) != FROZEN_BASELINE_CONFIG_SHA256:
        raise BundleIntegrityError(
            "bundled frozen baseline config canonical SHA-256 mismatch"
        )
    if manifest.get("frozen_baseline_config_sha256") != FROZEN_BASELINE_CONFIG_SHA256:
        raise BundleIntegrityError("manifest frozen_baseline_config_sha256 mismatch")
    if receipt.get("frozen_baseline_config_canonical_sha256") != FROZEN_BASELINE_CONFIG_SHA256:
        raise BundleIntegrityError("receipt frozen baseline config hash mismatch")

    decl_data = _require_member(members, DECLARATION_MEMBER)
    decl_obj = _parse_json_member(DECLARATION_MEMBER, decl_data)
    decl_canonical = _canonical_sha256(decl_obj)
    if decl_canonical != manifest.get("declaration_sha256"):
        raise BundleIntegrityError(
            "bundled shadow declaration canonical SHA-256 does not match manifest"
        )
    if receipt.get("shadow_declaration_canonical_sha256") != decl_canonical:
        raise BundleIntegrityError(
            "receipt shadow declaration canonical hash does not match bundled bytes"
        )
    decl_auth = decl_obj.get("authorizations", {}) if isinstance(decl_obj, dict) else {}
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
        "member_count": len(members),
        "total_uncompressed_bytes": total_uncompressed,
        "input_digest": input_digest,
        "decision_digest": decision_digest,
        "durability_status": receipt.get("durability_status"),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m slumdog.shadow_bundle",
        description=(
            "Milestone 7B: package a completed shadow run and every input "
            "needed to audit/reproduce it into a deterministic, independently "
            "verifiable full-payload bundle (post-decision preservation only)."
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
        help="Independently verify a bundle archive against its receipt (in memory).",
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
