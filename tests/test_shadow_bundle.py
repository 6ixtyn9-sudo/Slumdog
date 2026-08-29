"""Milestone 7B — Verifiable full-payload shadow bundle tests.

Every test uses ONLY temporary synthetic fixture data under
``tempfile`` roots. No retained real data under ``data/`` is ever read
or packaged; no Forebet/network/collector/settlement/production/training
call is made. The bundler is exercised against real, evaluator-produced
completed run artifacts (manifest + payload + receipt + sidecars +
bodies + history) built inside the temp root.

Test groups:
  A. Happy path: create + verify, determinism, inventory, exact bytes
  B. Create-time source/run refusals (manifest, blocked, hashes, paths)
  C. Verify-time integrity refusals (members, duplicates, symlinks, ...)
  D. Atomic finalization / no overwrite / permissions / no extraction
  E. Production isolation / CLI / static guarantees
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from slumdog import shadow_bundle as sb
from slumdog.shadow_bundle import (
    BUNDLE_SCHEMA_VERSION,
    DURABILITY_STATUS,
    FROZEN_BASELINE_CONFIG_SHA256,
    RUN_SCHEMA_VERSION,
    BundleError,
    create_bundle,
    verify_bundle,
)
from slumdog.shadow_evaluator import evaluate_from_disk


REPO_ROOT = Path(__file__).resolve().parents[1]
FROZEN_CONFIG = REPO_ROOT / "config" / "research_baselines_v1.json"
SHADOW_DECL = REPO_ROOT / "config" / "shadow_evaluator_v1.json"

TARGET_DATE = "2026-08-28"
# captured_at MUST be before the safe cutoff (target 00:00Z - 24h).
CAPTURED_AT = "2026-08-26T10:00:00Z"
STAMP = "20260826T100000Z"
DECISION_CLOCK = datetime(2026, 8, 26, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Synthetic fixture builders
# ---------------------------------------------------------------------------


def _settled_row(event_id, date, p1, p2, winner, prob1, prob2, draw):
    return {
        "event_id": event_id, "sport": "football", "event_date": date,
        "participant_1": p1, "participant_2": p2, "winner_index": winner,
        "score_1": 2.0, "score_2": 1.0, "probability_1": prob1,
        "probability_2": prob2, "draw_probability": draw,
        "forebet_pick": None, "disposition": "SETTLED",
    }


def _arsenal_chelsea_priors():
    """6 prior games per side + 2 H2H => R2-eligible Arsenal vs Chelsea."""
    rows = []
    for i in range(6):
        rows.append(_settled_row(
            f"ac_{i}", f"2024-01-{(i % 28) + 1:02d}",
            "Arsenal", "Chelsea", 1, 0.55, 0.30, 0.15))
    for i in range(6):
        rows.append(_settled_row(
            f"ca_{i}", f"2024-03-{(i % 28) + 1:02d}",
            "Chelsea", "Arsenal", 1, 0.50, 0.30, 0.20))
    for i in range(2):
        rows.append(_settled_row(
            f"h2h_{i}", f"2024-05-{(i % 28) + 1:02d}",
            "Arsenal", "Chelsea", 1, 0.55, 0.30, 0.15))
    return rows


def _forebet_rows():
    """A real Forebet-style parsed body row (Arsenal vs Chelsea, 0.50/0.40)."""
    return [{
        "id": "1001", "HOST_NAME": "Arsenal", "GUEST_NAME": "Chelsea",
        "Pred_1": "50", "Pred_X": "10", "Pred_2": "40",
        "best_odd_1": "2.00", "best_odd_2": "2.50", "best_odd_X": "10.00",
        "short_tag": "EPL", "DATE_BAH": f"{TARGET_DATE} 15:00",
        "host_sc_pr": "1", "guest_sc_pr": "1", "goalsavg": "2.5",
        "Host_SC": None, "Guest_SC": None, "comment": "",
    }]


def make_env(tmp_path: Path, *, primary: bool = True) -> dict:
    """Build a full synthetic repo root with a completed shadow run.

    Returns a dict of paths. With ``primary=True`` the history + body are
    wired so the evaluator emits a PRIMARY_SHADOW_SELECTION; with
    ``primary=False`` the history is empty so it emits SHADOW_NO_SELECTION.
    """
    root = tmp_path / "repo"
    (root / "config").mkdir(parents=True)
    (root / "data" / "reports" / "shadow").mkdir(parents=True)
    shutil.copy(FROZEN_CONFIG, root / "config" / "research_baselines_v1.json")
    shutil.copy(SHADOW_DECL, root / "config" / "shadow_evaluator_v1.json")

    # Raw body + sidecar (real Forebet row shape, json body_format).
    body = ("<html><body>" + json.dumps([_forebet_rows(), {}]) + "</body></html>").encode("utf-8")
    body_sha = hashlib.sha256(body).hexdigest()
    body_dir = root / "data" / "raw" / "football" / TARGET_DATE
    body_dir.mkdir(parents=True, exist_ok=True)
    body_path = body_dir / f"{STAMP}_{body_sha[:12]}.txt"
    sidecar_path = body_dir / f"{STAMP}_{body_sha[:12]}.json"
    body_path.write_bytes(body)
    sidecar = {
        "sport": "football", "target_date": TARGET_DATE, "captured_at": CAPTURED_AT,
        "source_url": f"https://example.invalid/football/{TARGET_DATE}",
        "body_format": "json", "sha256": body_sha, "bytes": len(body),
        "body_path": body_path.relative_to(root).as_posix(),
        "metadata_path": sidecar_path.relative_to(root).as_posix(),
        "route": "direct",
    }
    sidecar_path.write_text(json.dumps(sidecar, indent=2, sort_keys=True))
    receipt = {
        "target_date": TARGET_DATE, "generated_at": CAPTURED_AT,
        "captured": [sidecar], "failures": [], "reused": 0, "football_markets": None,
    }
    receipt_path = root / "data" / "reports" / f"capture_{TARGET_DATE}.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True))

    # History gz.
    gz_path = root / "data" / "reports" / "history_football.jsonl.gz"
    hist_rows = _arsenal_chelsea_priors() if primary else []
    with gzip.open(gz_path, "wb") as f:
        for row in hist_rows:
            f.write((json.dumps(row) + "\n").encode("utf-8"))

    result = evaluate_from_disk(
        target_date=TARGET_DATE,
        capture_receipt_path=receipt_path,
        declaration_path=root / "config" / "shadow_evaluator_v1.json",
        repo_root=root,
        history_paths=[gz_path],
        decision_clock=DECISION_CLOCK,
        history_max_interim_bytes=10 * 1024 * 1024,
    )
    assert result.run_status in ("SHADOW_SELECTIONS_EMITTED", "SHADOW_NO_SELECTION")
    run_dir = Path(result.artifact_dir)

    return {
        "root": root,
        "run_dir": run_dir,
        "manifest_path": run_dir / "manifest.json",
        "payload_path": run_dir / "shadow_selections.json",
        "receipt_path": receipt_path,
        "sidecar_path": sidecar_path,
        "body_path": body_path,
        "history_path": gz_path,
        "frozen_config_path": root / "config" / "research_baselines_v1.json",
        "decl_path": root / "config" / "shadow_evaluator_v1.json",
        "run_status": result.run_status,
        "run_id": result.run_id,
    }


def bundle_paths(env: dict, out: Path):
    """Create a bundle and return (result, archive_path, receipt_path, checksum_path)."""
    res = create_bundle(run_dir=env["run_dir"], output_dir=out, root=env["root"])
    outdir = Path(out)
    return (
        res,
        outdir / res["archive_filename"],
        outdir / (res["archive_filename"].replace(".tar.gz", ".bundle.json")),
        outdir / (res["archive_filename"] + ".sha256"),
    )


def read_archive(archive_path: Path) -> dict[str, bytes]:
    with tarfile.open(archive_path, mode="r:gz") as tar:
        return {m.name: tar.extractfile(m).read() for m in tar.getmembers()}


def write_archive(archive_path: Path, members: dict[str, bytes]) -> None:
    """Deterministically re-write an archive from in-memory members."""
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0, compresslevel=9) as gz:
        with tarfile.open(fileobj=gz, mode="w|", format=tarfile.USTAR_FORMAT) as tar:
            for name in sorted(members):
                data = members[name]
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                info.mtime = 0
                info.mode = 0o644
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                info.type = tarfile.REGTYPE
                tar.addfile(info, io.BytesIO(data))
    archive_path.write_bytes(buf.getvalue())


def tamper_bundle(archive_path: Path, receipt_path: Path, *, mutate_archive=None,
                  mutate_receipt=None, write_symlink_member=False,
                  write_dup_member=False, add_unexpected=False,
                  resync_inventory=False, drop_marker=False):
    """Rebuild an archive/receipt pair for verify-failure tests.

    The receipt archive_sha256 (and sibling marker) are recomputed to match
    the rebuilt archive so the verifier reaches the structural/content check
    under test. When ``resync_inventory`` is set, the in-archive inventory
    member hashes/sizes are refreshed to match the mutated content, so a
    semantic-level check (payload vs manifest, canonical config hash, ...)
    is reached rather than the inventory member-hash check.
    """
    members = read_archive(archive_path)
    if mutate_archive:
        mutate_archive(members)
    if add_unexpected:
        members["bundle/extra/secret.txt"] = b"unexpected file"
    if resync_inventory and "bundle/inventory.json" in members:
        inv = json.loads(members["bundle/inventory.json"])
        for row in inv.get("members", []):
            name = row["archive_member"]
            if name in members:
                row["sha256"] = hashlib.sha256(members[name]).hexdigest()
                row["bytes"] = len(members[name])
        members["bundle/inventory.json"] = json.dumps(
            inv, sort_keys=True, separators=(",", ":")).encode("utf-8")
    # Optional malicious member types are injected at raw-tar level.
    if write_symlink_member or write_dup_member:
        buf = io.BytesIO()
        with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0, compresslevel=9) as gz:
            with tarfile.open(fileobj=gz, mode="w|", format=tarfile.USTAR_FORMAT) as tar:
                seen = set()
                for name in sorted(members):
                    data = members[name]
                    info = tarfile.TarInfo(name=name)
                    info.size = len(data)
                    info.mtime = 0
                    info.mode = 0o644
                    info.uid = 0
                    info.gid = 0
                    info.type = tarfile.REGTYPE
                    tar.addfile(info, io.BytesIO(data))
                    seen.add(name)
                if write_symlink_member:
                    link = tarfile.TarInfo(name="bundle/run/evil-link")
                    link.type = tarfile.SYMTYPE
                    link.linkname = "../../../../etc/passwd"
                    link.mtime = 0
                    link.uid = 0
                    link.gid = 0
                    tar.addfile(link)
                if write_dup_member:
                    data = members["bundle/run/manifest.json"]
                    dup = tarfile.TarInfo(name="bundle/run/manifest.json")
                    dup.size = len(data)
                    dup.mtime = 0
                    dup.uid = 0
                    dup.gid = 0
                    dup.type = tarfile.REGTYPE
                    tar.addfile(dup, io.BytesIO(data + b" "))
        archive_path.write_bytes(buf.getvalue())
    else:
        write_archive(archive_path, members)

    receipt = json.loads(receipt_path.read_text())
    new_arch_bytes = archive_path.read_bytes()
    new_arch_sha = hashlib.sha256(new_arch_bytes).hexdigest()
    receipt["archive_sha256"] = new_arch_sha
    receipt["archive_bytes"] = len(new_arch_bytes)
    # If content/inventory was resynced, refresh the receipt's recorded
    # member hashes/sizes/totals so the verifier proceeds to the semantic
    # checks (which are what these tests target).
    if resync_inventory:
        rebuilt = read_archive(archive_path)
        inv_bytes = rebuilt["bundle/inventory.json"]
        receipt["inventory_sha256"] = hashlib.sha256(inv_bytes).hexdigest()
        receipt["inventory_bytes"] = len(inv_bytes)
        receipt["member_count"] = len(rebuilt)
        receipt["total_uncompressed_bytes"] = sum(len(d) for d in rebuilt.values())
        # Follow a deeply edited manifest so earlier digest/run-id checks pass
        # and the targeted declaration/config/payload check is reached.
        try:
            manifest = json.loads(rebuilt["bundle/run/manifest.json"])
        except (KeyError, ValueError):
            manifest = None
        if manifest is not None:
            receipt["run_id"] = manifest.get("run_id", receipt.get("run_id"))
            receipt["input_digest"] = manifest.get("input_digest", receipt.get("input_digest"))
            receipt["decision_digest"] = manifest.get("decision_digest", receipt.get("decision_digest"))
            receipt["shadow_declaration_canonical_sha256"] = manifest.get(
                "declaration_sha256", receipt.get("shadow_declaration_canonical_sha256"))
    if mutate_receipt:
        mutate_receipt(receipt)
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True))
    # Keep the sibling checksum marker consistent with the rebuilt archive so
    # the verifier reaches the structural/content check under test (the
    # marker is part of the bundle triplet and independently re-checked).
    marker_path = archive_path.with_name(archive_path.name + ".sha256")
    if marker_path.exists() and not drop_marker:
        marker_path.write_text(f"{new_arch_sha}  {archive_path.name}\n")


# ---------------------------------------------------------------------------
# Group A: happy path
# ---------------------------------------------------------------------------


def test_create_and_verify_roundtrip(tmp_path):
    env = make_env(tmp_path)
    out = tmp_path / "out"
    res, archive, receipt, checksum = bundle_paths(env, out)
    assert res["durability_status"] == DURABILITY_STATUS
    assert archive.is_file() and receipt.is_file() and checksum.is_file()
    verified = verify_bundle(bundle_path=archive, receipt_path=receipt)
    assert verified["status"] == "BUNDLE_VERIFIED"
    assert verified["run_id"] == env["run_id"]
    assert verified["target_date"] == TARGET_DATE


def test_deterministic_identical_archive_bytes(tmp_path):
    env = make_env(tmp_path)
    out1 = tmp_path / "o1"
    out2 = tmp_path / "o2"
    r1, a1, rc1, _ = bundle_paths(env, out1)
    r2, a2, rc2, _ = bundle_paths(env, out2)
    # Same verified source bytes produce identical archive bytes + hash.
    assert a1.read_bytes() == a2.read_bytes()
    assert r1["archive_sha256"] == r2["archive_sha256"]
    # Exact-byte comparison mirrors the owner's `cmp` / `sha256sum` procedure.
    assert hashlib.sha256(a1.read_bytes()).hexdigest() == r1["archive_sha256"]


def test_creation_timestamp_does_not_affect_archive(tmp_path, monkeypatch):
    env = make_env(tmp_path)
    import slumdog.shadow_bundle as mod
    calls = iter([
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2030, 12, 31, tzinfo=timezone.utc),
    ])
    monkeypatch.setattr(mod, "_now_utc_iso", lambda: next(calls).strftime("%Y-%m-%dT%H:%M:%SZ"))
    r1, a1, _, _ = bundle_paths(env, tmp_path / "o1")
    r2, a2, _, _ = bundle_paths(env, tmp_path / "o2")
    assert a1.read_bytes() == a2.read_bytes()
    # The receipts differ only in bundle_created_at; archives are identical.
    assert r1["archive_sha256"] == r2["archive_sha256"]


def test_inventory_covers_every_member_with_hashes(tmp_path):
    env = make_env(tmp_path)
    _, archive, receipt, _ = bundle_paths(env, tmp_path / "out")
    members = read_archive(archive)
    inv = json.loads(members["bundle/inventory.json"])
    assert inv["bundle_schema_version"] == BUNDLE_SCHEMA_VERSION
    assert inv["run_id"] == env["run_id"]
    # Inventory lists every content member except itself.
    listed = {row["archive_member"] for row in inv["members"]}
    expected = set(members) - {"bundle/inventory.json"}
    assert listed == expected
    assert inv["content_member_count"] == len(inv["members"])
    # Every listed member hash + size matches the archive bytes.
    for row in inv["members"]:
        data = members[row["archive_member"]]
        assert row["sha256"] == hashlib.sha256(data).hexdigest()
        assert row["bytes"] == len(data)
        assert row["role"]
        assert isinstance(row["source_paths"], list)
    # Receipt member count equals total archive members (incl inventory).
    rcpt = json.loads(receipt.read_text())
    assert rcpt["member_count"] == len(members)
    assert rcpt["total_uncompressed_bytes"] == sum(len(d) for d in members.values())


def test_exact_original_payload_and_manifest_bytes_preserved(tmp_path):
    env = make_env(tmp_path)
    _, archive, _, _ = bundle_paths(env, tmp_path / "out")
    members = read_archive(archive)
    assert members["bundle/run/shadow_selections.json"] == env["payload_path"].read_bytes()
    assert members["bundle/run/manifest.json"] == env["manifest_path"].read_bytes()
    # Manifest payload hash matches bundled payload.
    manifest = json.loads(members["bundle/run/manifest.json"])
    assert manifest["payload_file_sha256"] == hashlib.sha256(
        members["bundle/run/shadow_selections.json"]).hexdigest()


def test_bundle_contains_all_required_inputs(tmp_path):
    env = make_env(tmp_path)
    _, archive, _, _ = bundle_paths(env, tmp_path / "out")
    members = read_archive(archive)
    names = set(members)
    assert "bundle/run/shadow_selections.json" in names
    assert "bundle/run/manifest.json" in names
    assert "bundle/config/research_baselines_v1.json" in names
    assert "bundle/config/shadow_evaluator_v1.json" in names
    assert "bundle/capture/receipt.json" in names
    assert any(n.startswith("bundle/capture/sidecars/") for n in names)
    assert any(n.startswith("bundle/capture/bodies/") for n in names)
    assert any(n.startswith("bundle/history/") for n in names)
    assert "bundle/inventory.json" in names
    assert "bundle/README.txt" in names
    # Bundled capture/history bytes exactly equal the on-disk source bytes.
    assert members["bundle/capture/receipt.json"] == env["receipt_path"].read_bytes()
    assert members["bundle/config/research_baselines_v1.json"] == env["frozen_config_path"].read_bytes()
    assert members["bundle/config/shadow_evaluator_v1.json"] == env["decl_path"].read_bytes()


def test_archive_uses_safe_logical_paths_only(tmp_path):
    env = make_env(tmp_path)
    _, archive, _, _ = bundle_paths(env, tmp_path / "out")
    with tarfile.open(archive, mode="r:gz") as tar:
        for m in tar.getmembers():
            assert not m.name.startswith("/")
            assert ".." not in m.name.split("/")
            assert "\\" not in m.name
            assert m.issym() is False and m.islnk() is False
            assert m.isdev() is False and m.isfifo() is False
            assert m.isdir() is False
            assert m.uid == 0 and m.gid == 0 and m.uname == "" and m.gname == ""
            assert m.mode == 0o644
            assert m.mtime == 0


def test_receipt_records_required_fields_and_auth_flags(tmp_path):
    env = make_env(tmp_path)
    res, archive, receipt, _ = bundle_paths(env, tmp_path / "out")
    rcpt = json.loads(receipt.read_text())
    for key in (
        "bundle_schema_version", "run_schema_version", "target_date", "run_id",
        "archive_filename", "archive_sha256", "archive_bytes",
        "source_manifest_sha256", "source_payload_sha256",
        "frozen_baseline_config_canonical_sha256",
        "shadow_declaration_canonical_sha256", "input_digest", "decision_digest",
        "decision_committed_at", "bundle_created_at", "member_count",
        "total_uncompressed_bytes", "durability_status",
    ):
        assert key in rcpt, f"receipt missing {key}"
    assert rcpt["durability_status"] == DURABILITY_STATUS
    assert rcpt["frozen_baseline_config_canonical_sha256"] == FROZEN_BASELINE_CONFIG_SHA256
    auth = rcpt["authorizations"]
    assert auth == {
        "production_authorized": False,
        "shortlist_policy_authorized": False,
        "training_authorized": False,
        "threshold_optimization_authorized": False,
    }
    # Checksum marker content matches the archive.
    marker = (tmp_path / "out" / (res["archive_filename"] + ".sha256")).read_text()
    assert marker.split()[0] == res["archive_sha256"]


def test_bundle_works_for_no_selection_run(tmp_path):
    env = make_env(tmp_path, primary=False)
    assert env["run_status"] == "SHADOW_NO_SELECTION"
    _, archive, receipt, _ = bundle_paths(env, tmp_path / "out")
    verified = verify_bundle(bundle_path=archive, receipt_path=receipt)
    assert verified["status"] == "BUNDLE_VERIFIED"


# ---------------------------------------------------------------------------
# Group B: create-time refusals
# ---------------------------------------------------------------------------


def test_create_rejects_missing_manifest(tmp_path):
    env = make_env(tmp_path)
    env["manifest_path"].unlink()
    with pytest.raises(BundleError, match="manifest"):
        create_bundle(run_dir=env["run_dir"], output_dir=tmp_path / "out", root=env["root"])


def test_create_rejects_blocked_receipt(tmp_path):
    env = make_env(tmp_path)
    # Forge a completed-looking directory whose manifest is a blocked receipt.
    blocked = tmp_path / "blocked_run"
    blocked.mkdir()
    (blocked / "shadow_selections.json").write_bytes(b'{"run_id":"BLOCKED"}')
    (blocked / "manifest.json").write_text(json.dumps({
        "version": RUN_SCHEMA_VERSION,
        "run_id": "BLOCKED",
        "run_status": "SHADOW_RUN_BLOCKED",
        "target_date": TARGET_DATE,
        "payload_file_sha256": hashlib.sha256(b'{"run_id":"BLOCKED"}').hexdigest(),
    }))
    with pytest.raises(BundleError, match="[Bb]locked"):
        create_bundle(run_dir=blocked, output_dir=tmp_path / "out", root=env["root"])


def test_create_rejects_payload_hash_mismatch(tmp_path):
    env = make_env(tmp_path)
    env["payload_path"].write_bytes(env["payload_path"].read_bytes() + b" ")
    with pytest.raises(BundleError, match="payload"):
        create_bundle(run_dir=env["run_dir"], output_dir=tmp_path / "out", root=env["root"])


def test_create_rejects_missing_capture_receipt(tmp_path):
    env = make_env(tmp_path)
    env["receipt_path"].unlink()
    with pytest.raises(BundleError, match="capture receipt"):
        create_bundle(run_dir=env["run_dir"], output_dir=tmp_path / "out", root=env["root"])


def test_create_rejects_missing_sidecar(tmp_path):
    env = make_env(tmp_path)
    env["sidecar_path"].unlink()
    with pytest.raises(BundleError, match="sidecar"):
        create_bundle(run_dir=env["run_dir"], output_dir=tmp_path / "out", root=env["root"])


def test_create_rejects_sidecar_or_body_hash_mismatch(tmp_path):
    env = make_env(tmp_path)
    env["body_path"].write_bytes(b"tampered body bytes")
    with pytest.raises(BundleError, match="SHA-256 mismatch"):
        create_bundle(run_dir=env["run_dir"], output_dir=tmp_path / "out", root=env["root"])


def test_create_rejects_missing_history_input(tmp_path):
    env = make_env(tmp_path)
    env["history_path"].unlink()
    with pytest.raises(BundleError, match="history input"):
        create_bundle(run_dir=env["run_dir"], output_dir=tmp_path / "out", root=env["root"])


def test_create_rejects_history_hash_mismatch(tmp_path):
    env = make_env(tmp_path)
    # Flip a byte inside the gz without changing its size, so the recorded
    # size matches but the streamed SHA-256 must be detected as mismatched.
    hp = env["history_path"]
    data = bytearray(hp.read_bytes())
    data[-1] ^= 0xFF
    hp.write_bytes(bytes(data))
    with pytest.raises(BundleError, match="SHA-256 mismatch"):
        create_bundle(run_dir=env["run_dir"], output_dir=tmp_path / "out", root=env["root"])


def test_create_rejects_history_size_mismatch(tmp_path):
    env = make_env(tmp_path)
    with gzip.open(env["history_path"], "ab") as f:
        f.write(b'{"event_id":"extra","sport":"football"}\n')
    with pytest.raises(BundleError, match="size mismatch|SHA-256 mismatch"):
        create_bundle(run_dir=env["run_dir"], output_dir=tmp_path / "out", root=env["root"])


def test_create_rejects_frozen_config_hash_mismatch(tmp_path):
    env = make_env(tmp_path)
    cfg_path = env["frozen_config_path"]
    cfg = json.loads(cfg_path.read_text())
    cfg["__bundle_tamper__"] = True
    cfg_path.write_text(json.dumps(cfg))
    with pytest.raises(BundleError, match="frozen baseline config"):
        create_bundle(run_dir=env["run_dir"], output_dir=tmp_path / "out", root=env["root"])


def test_create_rejects_shadow_declaration_hash_mismatch(tmp_path):
    env = make_env(tmp_path)
    decl_path = env["decl_path"]
    decl = json.loads(decl_path.read_text())
    decl["__bundle_tamper__"] = True
    decl_path.write_text(json.dumps(decl))
    with pytest.raises(BundleError, match="shadow declaration"):
        create_bundle(run_dir=env["run_dir"], output_dir=tmp_path / "out", root=env["root"])


def test_create_rejects_unsupported_run_schema_version(tmp_path):
    env = make_env(tmp_path)
    # Rewrite only the version, keep payload hash intact (version check fires first).
    manifest = json.loads(env["manifest_path"].read_text())
    manifest["version"] = "shadow_evaluator_v999"
    env["manifest_path"].write_text(json.dumps(manifest))
    with pytest.raises(BundleError, match="schema/version"):
        create_bundle(run_dir=env["run_dir"], output_dir=tmp_path / "out", root=env["root"])


def test_create_rejects_unsafe_run_dir_outside_root(tmp_path):
    env = make_env(tmp_path)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(BundleError, match="root|escapes|within"):
        create_bundle(run_dir=outside, output_dir=tmp_path / "out", root=env["root"])


def test_create_rejects_run_dir_via_symlink_escape(tmp_path):
    env = make_env(tmp_path)
    # A symlink inside the root that points outside must be refused.
    outside = tmp_path / "outside"
    outside.mkdir()
    link = env["root"] / "data" / "reports" / "shadow" / "link_run"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(BundleError, match="symlink|escapes|root"):
        create_bundle(run_dir=link, output_dir=tmp_path / "out", root=env["root"])


def test_create_does_not_mutate_inputs(tmp_path):
    env = make_env(tmp_path)
    before = {
        "manifest": env["manifest_path"].read_bytes(),
        "payload": env["payload_path"].read_bytes(),
        "receipt": env["receipt_path"].read_bytes(),
        "sidecar": env["sidecar_path"].read_bytes(),
        "body": env["body_path"].read_bytes(),
        "history": env["history_path"].read_bytes(),
    }
    bundle_paths(env, tmp_path / "out")
    assert env["manifest_path"].read_bytes() == before["manifest"]
    assert env["payload_path"].read_bytes() == before["payload"]
    assert env["receipt_path"].read_bytes() == before["receipt"]
    assert env["sidecar_path"].read_bytes() == before["sidecar"]
    assert env["body_path"].read_bytes() == before["body"]
    assert env["history_path"].read_bytes() == before["history"]


# ---------------------------------------------------------------------------
# Group C: verify-time refusals
# ---------------------------------------------------------------------------


def _make_verified_bundle(tmp_path):
    env = make_env(tmp_path)
    res, archive, receipt, checksum = bundle_paths(env, tmp_path / "out")
    return env, res, archive, receipt, checksum


def test_verify_rejects_archive_receipt_sha_mismatch(tmp_path):
    _, _, archive, receipt, _ = _make_verified_bundle(tmp_path)
    # Flip the receipt hash without touching the archive.
    rcpt = json.loads(receipt.read_text())
    rcpt["archive_sha256"] = "0" * 64
    receipt.write_text(json.dumps(rcpt))
    with pytest.raises(BundleError, match="SHA-256 mismatch"):
        verify_bundle(bundle_path=archive, receipt_path=receipt)


def test_verify_rejects_corrupt_archive(tmp_path):
    _, _, archive, receipt, marker = _make_verified_bundle(tmp_path)
    # Corrupt the gzip stream (flip bytes) and align the receipt hash so the
    # archive-level checksum passes and the unreadable-stream check fires.
    data = bytearray(archive.read_bytes())
    for i in range(min(200, len(data))):
        data[i] = data[i] ^ 0xFF
    archive.write_bytes(bytes(data))
    rcpt = json.loads(receipt.read_text())
    rcpt["archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    receipt.write_text(json.dumps(rcpt))
    marker.unlink()  # marker describes the original archive; remove it
    with pytest.raises(BundleError, match="corrupt|unreadable"):
        verify_bundle(bundle_path=archive, receipt_path=receipt)


def test_verify_rejects_unsafe_archive_member(tmp_path):
    _, _, archive, receipt, _ = _make_verified_bundle(tmp_path)
    tamper_bundle(archive, receipt, mutate_archive=lambda m: m.__setitem__(
        "bundle/../../escape.txt", b"x"))
    with pytest.raises(BundleError, match="unsafe"):
        verify_bundle(bundle_path=archive, receipt_path=receipt)


def test_verify_rejects_duplicate_member(tmp_path):
    _, _, archive, receipt, _ = _make_verified_bundle(tmp_path)
    tamper_bundle(archive, receipt, write_dup_member=True)
    with pytest.raises(BundleError, match="duplicate"):
        verify_bundle(bundle_path=archive, receipt_path=receipt)


def test_verify_rejects_symlink_member(tmp_path):
    _, _, archive, receipt, _ = _make_verified_bundle(tmp_path)
    tamper_bundle(archive, receipt, write_symlink_member=True)
    with pytest.raises(BundleError, match="symlink|member type|regular files"):
        verify_bundle(bundle_path=archive, receipt_path=receipt)


def test_verify_rejects_unexpected_member(tmp_path):
    _, _, archive, receipt, _ = _make_verified_bundle(tmp_path)
    tamper_bundle(archive, receipt, add_unexpected=True)
    with pytest.raises(BundleError, match="unexpected"):
        verify_bundle(bundle_path=archive, receipt_path=receipt)


def test_verify_rejects_missing_member_listed_in_inventory(tmp_path):
    _, _, archive, receipt, _ = _make_verified_bundle(tmp_path)

    def drop(members):
        del members["bundle/capture/receipt.json"]
    tamper_bundle(archive, receipt, mutate_archive=drop)
    with pytest.raises(BundleError, match="missing archive member"):
        verify_bundle(bundle_path=archive, receipt_path=receipt)


def test_verify_rejects_member_hash_mismatch(tmp_path):
    _, _, archive, receipt, _ = _make_verified_bundle(tmp_path)

    def corrupt_body(members):
        for name in list(members):
            if name.startswith("bundle/capture/bodies/"):
                members[name] = members[name] + b"tampered"
    tamper_bundle(archive, receipt, mutate_archive=corrupt_body)
    with pytest.raises(BundleError, match="member SHA-256 mismatch"):
        verify_bundle(bundle_path=archive, receipt_path=receipt)


def test_verify_rejects_bundled_payload_vs_manifest_mismatch(tmp_path):
    _, _, archive, receipt, _ = _make_verified_bundle(tmp_path)

    def corrupt_payload(members):
        payload = json.loads(members["bundle/run/shadow_selections.json"])
        payload["__tampered__"] = True
        members["bundle/run/shadow_selections.json"] = json.dumps(payload).encode()
    tamper_bundle(archive, receipt, mutate_archive=corrupt_payload, resync_inventory=True)
    with pytest.raises(BundleError, match="payload SHA-256"):
        verify_bundle(bundle_path=archive, receipt_path=receipt)


def test_verify_rejects_frozen_config_canonical_mismatch(tmp_path):
    _, _, archive, receipt, _ = _make_verified_bundle(tmp_path)

    def corrupt_config(members):
        cfg = json.loads(members["bundle/config/research_baselines_v1.json"])
        cfg["__tampered__"] = True
        members["bundle/config/research_baselines_v1.json"] = json.dumps(cfg).encode()
    tamper_bundle(archive, receipt, mutate_archive=corrupt_config, resync_inventory=True)
    with pytest.raises(BundleError, match="frozen baseline config"):
        verify_bundle(bundle_path=archive, receipt_path=receipt)


def _reconsume_manifest_after_deep_edit(manifest, members):
    """Make deep-edited manifest+payload self-consistent except the tested property."""
    def canon_bytes(o):
        return json.dumps(o, sort_keys=True, separators=(",", ":")).encode("utf-8")

    ip = manifest.get("input_provenance")
    dp = manifest.get("decision_provenance")
    if isinstance(ip, dict):
        manifest["input_digest"] = hashlib.sha256(canon_bytes(ip)).hexdigest()
    if isinstance(dp, dict):
        manifest["decision_digest"] = hashlib.sha256(canon_bytes(dp)).hexdigest()
    run_id_payload = {
        "version": manifest.get("version", RUN_SCHEMA_VERSION),
        "input_digest": manifest["input_digest"],
        "decision_digest": manifest["decision_digest"],
        "decision_committed_at": manifest.get("decision_committed_at"),
    }
    manifest["run_id"] = hashlib.sha256(canon_bytes(run_id_payload)).hexdigest()[:16]
    members["bundle/run/manifest.json"] = canon_bytes(manifest)
    # Align the payload's run_id/digests so payload<->manifest consistency holds.
    if "bundle/run/shadow_selections.json" in members:
        payload = json.loads(members["bundle/run/shadow_selections.json"])
        payload["run_id"] = manifest["run_id"]
        payload["input_digest"] = manifest["input_digest"]
        payload["decision_digest"] = manifest["decision_digest"]
        payload_bytes = canon_bytes(payload)
        members["bundle/run/shadow_selections.json"] = payload_bytes
        manifest["payload_file_sha256"] = hashlib.sha256(payload_bytes).hexdigest()
        members["bundle/run/manifest.json"] = canon_bytes(manifest)
    return manifest


def test_verify_rejects_shadow_declaration_canonical_mismatch(tmp_path):
    _, _, archive, receipt, _ = _make_verified_bundle(tmp_path)

    def corrupt_decl(members):
        # Flip a fail-closed authorization gate inside the bundled
        # declaration, and keep every hash/digest self-consistent so the
        # verifier reaches the declaration authorization gate.
        decl = json.loads(members["bundle/config/shadow_evaluator_v1.json"])
        decl["authorizations"]["production_authorized"] = True
        decl_bytes = json.dumps(decl, sort_keys=True, separators=(",", ":")).encode("utf-8")
        members["bundle/config/shadow_evaluator_v1.json"] = decl_bytes
        new_decl_sha = hashlib.sha256(decl_bytes).hexdigest()
        manifest = json.loads(members["bundle/run/manifest.json"])
        manifest["declaration_sha256"] = new_decl_sha
        if isinstance(manifest.get("input_provenance"), dict):
            manifest["input_provenance"]["declaration_sha256"] = new_decl_sha
        _reconsume_manifest_after_deep_edit(manifest, members)
    tamper_bundle(archive, receipt, mutate_archive=corrupt_decl, resync_inventory=True)
    with pytest.raises(BundleError, match="production_authorized|authorizations"):
        verify_bundle(bundle_path=archive, receipt_path=receipt)


def test_verify_rejects_authorization_flag_true(tmp_path):
    _, _, archive, receipt, _ = _make_verified_bundle(tmp_path)

    def set_auth(rcpt):
        rcpt["authorizations"]["production_authorized"] = True
    tamper_bundle(archive, receipt, mutate_receipt=set_auth)
    with pytest.raises(BundleError, match="production_authorized|authorizations"):
        verify_bundle(bundle_path=archive, receipt_path=receipt)


def test_verify_rejects_unsupported_bundle_schema(tmp_path):
    _, _, archive, receipt, _ = _make_verified_bundle(tmp_path)

    def set_version(rcpt):
        rcpt["bundle_schema_version"] = "slumdog_shadow_bundle_v999"
    tamper_bundle(archive, receipt, mutate_receipt=set_version)
    with pytest.raises(BundleError, match="bundle schema version"):
        verify_bundle(bundle_path=archive, receipt_path=receipt)


def test_verify_rejects_run_id_mismatch(tmp_path):
    _, _, archive, receipt, _ = _make_verified_bundle(tmp_path)

    def corrupt(rcpt):
        rcpt["run_id"] = "deadbeefdeadbeef"
    tamper_bundle(archive, receipt, mutate_receipt=corrupt)
    with pytest.raises(BundleError, match="run_id"):
        verify_bundle(bundle_path=archive, receipt_path=receipt)


def test_verify_rejects_decision_digest_mismatch(tmp_path):
    _, _, archive, receipt, _ = _make_verified_bundle(tmp_path)

    def corrupt(members):
        manifest = json.loads(members["bundle/run/manifest.json"])
        manifest["decision_digest"] = "f" * 64
        members["bundle/run/manifest.json"] = json.dumps(manifest).encode()
    tamper_bundle(archive, receipt, mutate_archive=corrupt, resync_inventory=True)
    with pytest.raises(BundleError, match="decision_digest|run_id"):
        verify_bundle(bundle_path=archive, receipt_path=receipt)


def test_verify_rejects_bad_sibling_checksum_marker(tmp_path):
    _, _, archive, receipt, checksum = _make_verified_bundle(tmp_path)
    checksum.write_text("0" * 64 + "  " + archive.name + "\n")
    with pytest.raises(BundleError, match="sha256|checksum|marker"):
        verify_bundle(bundle_path=archive, receipt_path=receipt)


def test_verify_missing_files_raise_integrity_error(tmp_path):
    _, _, archive, receipt, _ = _make_verified_bundle(tmp_path)
    with pytest.raises(BundleError, match="not found"):
        verify_bundle(bundle_path=archive, receipt_path=tmp_path / "nope.json")
    with pytest.raises(BundleError, match="not found"):
        verify_bundle(bundle_path=tmp_path / "nope.tar.gz", receipt_path=receipt)


# ---------------------------------------------------------------------------
# Group D: atomic finalization / no overwrite / permissions / no extraction
# ---------------------------------------------------------------------------


def test_create_refuses_existing_output_no_overwrite(tmp_path):
    env = make_env(tmp_path)
    out = tmp_path / "out"
    bundle_paths(env, out)
    # Second create into the same output directory must fail (no force).
    with pytest.raises(BundleError, match="overwrite|existing"):
        create_bundle(run_dir=env["run_dir"], output_dir=out, root=env["root"])


def test_interrupted_finalization_leaves_no_valid_marker(tmp_path, monkeypatch):
    env = make_env(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    original_replace = sb.os.replace

    def flaky_replace(src, dst):
        # Fail before the receipt is finalized: the archive may land, but
        # there must be no valid receipt (the bundle's completion marker),
        # and therefore nothing that verifies as a complete bundle.
        if str(dst).endswith(".bundle.json"):
            raise OSError("simulated crash during receipt finalization")
        return original_replace(src, dst)

    monkeypatch.setattr(sb.os, "replace", flaky_replace)
    with pytest.raises(OSError):
        create_bundle(run_dir=env["run_dir"], output_dir=out, root=env["root"])
    monkeypatch.undo()

    # No valid receipt and no checksum marker after an interrupted finalization.
    receipts = list(out.glob("*.bundle.json"))
    checksums = list(out.glob("*.sha256"))
    assert receipts == [], "receipt completion marker must not exist after interruption"
    assert checksums == [], "checksum marker must not exist after interruption"
    # No leftover temp files masquerading as final artifacts.
    leftovers = [p for p in out.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []
    # A stray archive without a receipt can never verify.
    for archive in out.glob("*.tar.gz"):
        with pytest.raises(BundleError):
            verify_bundle(
                bundle_path=archive,
                receipt_path=out / (archive.name.replace(".tar.gz", ".bundle.json")),
            )


def test_bundle_outputs_have_conservative_permissions(tmp_path):
    env = make_env(tmp_path)
    res, archive, receipt, checksum = bundle_paths(env, tmp_path / "out")
    for p in (archive, receipt, checksum):
        mode = p.stat().st_mode & 0o777
        assert mode == 0o600, f"{p.name} mode is {oct(mode)}"


def test_verify_performs_no_extraction(tmp_path, monkeypatch):
    env = make_env(tmp_path)
    _, archive, receipt, _ = bundle_paths(env, tmp_path / "out")

    def boom(*a, **k):
        raise AssertionError("verifier must never extract to disk")

    monkeypatch.setattr(tarfile.TarFile, "extractall", boom)
    monkeypatch.setattr(tarfile.TarFile, "extract", boom)
    # Verification must succeed purely in memory.
    result = verify_bundle(bundle_path=archive, receipt_path=receipt)
    assert result["status"] == "BUNDLE_VERIFIED"
    # No files were created next to the bundle.
    outdir = archive.parent
    before = {p.name for p in outdir.iterdir()}
    verify_bundle(bundle_path=archive, receipt_path=receipt)
    after = {p.name for p in outdir.iterdir()}
    assert before == after


# ---------------------------------------------------------------------------
# Group E: production isolation / CLI
# ---------------------------------------------------------------------------


def test_module_is_stdlib_only_no_slumdog_imports():
    """A fresh import of shadow_bundle must not pull other Slumdog modules.

    This guarantees verification can run on an independent machine with only
    the archive, receipt, and the Python standard library.
    """
    code = (
        "import sys, importlib\n"
        "import slumdog.shadow_bundle  # noqa\n"
        "leaked = [m for m in sys.modules if m.startswith('slumdog.') and m != 'slumdog.shadow_bundle' and m != 'slumdog']\n"
        "forbidden = ['slumdog.forebet','slumdog.pipeline','slumdog.settlement',\n"
        "             'slumdog.training','slumdog.capture_loader','slumdog.history_loader',\n"
        "             'slumdog.shadow_evaluator','slumdog.dataset','urllib.request','http.client','socket']\n"
        "bad = [m for m in forbidden if m in sys.modules]\n"
        "assert not bad, f'forbidden modules imported: {bad}'\n"
        "print('OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT / "src"),
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_cli_help():
    for argv in (["--help"], ["create", "--help"], ["verify", "--help"]):
        proc = subprocess.run(
            [sys.executable, "-m", "slumdog.shadow_bundle", *argv],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert "usage" in proc.stdout.lower()


def test_cli_create_success(tmp_path):
    env = make_env(tmp_path)
    out = tmp_path / "cli_out"
    proc = subprocess.run(
        [sys.executable, "-m", "slumdog.shadow_bundle", "create",
         "--run-dir", str(env["run_dir"]),
         "--output-dir", str(out),
         "--root", str(env["root"])],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert out.glob("*.tar.gz")
    assert json.loads(proc.stdout)["durability_status"] == DURABILITY_STATUS


def test_cli_verify_success(tmp_path):
    env = make_env(tmp_path)
    res, archive, receipt, _ = bundle_paths(env, tmp_path / "out")
    proc = subprocess.run(
        [sys.executable, "-m", "slumdog.shadow_bundle", "verify",
         "--bundle", str(archive), "--receipt", str(receipt)],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["status"] == "BUNDLE_VERIFIED"


def test_cli_expected_failure_nonzero_without_traceback(tmp_path):
    env = make_env(tmp_path)
    # Missing manifest -> clean nonzero exit, no Python traceback.
    env["manifest_path"].unlink()
    proc = subprocess.run(
        [sys.executable, "-m", "slumdog.shadow_bundle", "create",
         "--run-dir", str(env["run_dir"]),
         "--output-dir", str(tmp_path / "out"),
         "--root", str(env["root"])],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr
    assert "BUNDLE_CREATE_FAILED" in proc.stderr


def test_cli_verify_failure_nonzero_without_traceback(tmp_path):
    _, _, archive, receipt, _ = _make_verified_bundle(tmp_path)
    rcpt = json.loads(receipt.read_text())
    rcpt["archive_sha256"] = "0" * 64
    receipt.write_text(json.dumps(rcpt))
    proc = subprocess.run(
        [sys.executable, "-m", "slumdog.shadow_bundle", "verify",
         "--bundle", str(archive), "--receipt", str(receipt)],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr
    assert "BUNDLE_VERIFY_FAILED" in proc.stderr


def test_no_real_data_touched():
    """The bundler module must default to touching nothing under data/.

    This is a structural guard: create_bundle requires an explicit run-dir
    and root and never scans or packages retained data on its own.
    """
    import inspect
    src = inspect.getsource(sb)
    # It must not contain hard-coded retained-data scanning or network calls.
    for forbidden in ("urllib", "requests", "http.client", "socket.",
                      "forebet capture", "subprocess", "eval(", "exec("):
        assert forbidden not in src, f"shadow_bundle must not reference {forbidden!r}"


# ---------------------------------------------------------------------------
# Group F: bounded-memory / streaming hardening (Milestone 7B review)
# ---------------------------------------------------------------------------


def test_limit_constants_are_conservative():
    """Bounds are documented, finite, and far below multi-GB allocations."""
    assert sb.STREAM_CHUNK == 1024 * 1024
    assert sb.MAX_COMPRESSED_ARCHIVE_BYTES == 512 * 1024 * 1024
    assert sb.MAX_EVIDENCE_MEMBER_BYTES == 256 * 1024 * 1024
    assert sb.MAX_TOTAL_UNCOMPRESSED_BYTES == 1024 * 1024 * 1024
    assert sb.MAX_METADATA_BYTES == 16 * 1024 * 1024
    assert sb.MAX_MEMBER_COUNT == 10_000
    assert sb.MAX_PATH_BYTES == 512
    # No unlimited escape hatch exists: no CLI flag of any kind.
    import inspect
    assert "--unlimited" not in inspect.getsource(sb)
    help_text = sb._build_arg_parser().format_help()
    assert "unlimited" not in help_text.lower()



def test_verify_streams_members_in_bounded_chunks(tmp_path, monkeypatch):
    """Verification must never issue an unbounded file .read().

    Replace the chunk size with a small value and instrument the tar member
    file objects so any read larger than the chunk (or read with no/negative
    size) fails the test. Genuine member bytes are far smaller than the chunk,
    so this proves the verifier only issues bounded chunk reads.
    """
    _, _, archive, receipt, _ = _make_verified_bundle(tmp_path)
    monkeypatch.setattr(sb, "STREAM_CHUNK", 64 * 1024)

    real_extract = tarfile.TarFile.extractfile

    def guarded_extract(self, member):
        fobj = real_extract(self, member)
        if fobj is None:
            return fobj
        orig_read = fobj.read

        def read(size=-1):
            assert size is not None and size >= 0, "unbounded member read"
            assert size <= 64 * 1024, f"read of {size} bytes exceeds chunk"
            return orig_read(size)

        fobj.read = read
        return fobj

    monkeypatch.setattr(tarfile.TarFile, "extractfile", guarded_extract)
    result = verify_bundle(bundle_path=archive, receipt_path=receipt)
    assert result["status"] == "BUNDLE_VERIFIED"


def test_create_streams_history_and_body_without_read_bytes(tmp_path, monkeypatch):
    """Creation must stream large evidence (history/body), never read_bytes()."""
    env = make_env(tmp_path)
    read_bytes = Path.read_bytes

    def no_large_read_bytes(self, *a, **k):
        name = self.name.lower()
        if name.endswith(".gz") or name.endswith(".txt"):
            raise AssertionError(
                f"create must stream evidence file, not read_bytes(): {self.name}"
            )
        return read_bytes(self, *a, **k)

    monkeypatch.setattr(Path, "read_bytes", no_large_read_bytes)
    res = create_bundle(
        run_dir=env["run_dir"], output_dir=tmp_path / "out", root=env["root"])
    assert res["member_count"] >= 1
    members = read_archive(tmp_path / "out" / res["archive_filename"])
    assert any(n.startswith("bundle/history/") for n in members)
    assert any(n.startswith("bundle/capture/bodies/") for n in members)


def test_create_rejects_evidence_member_over_size_limit(tmp_path, monkeypatch):
    """A raw body/history larger than the evidence cap fails closed."""
    env = make_env(tmp_path)
    monkeypatch.setattr(sb, "MAX_EVIDENCE_MEMBER_BYTES", 8)  # tiny
    with pytest.raises(BundleError, match="exceeds|limit"):
        create_bundle(run_dir=env["run_dir"], output_dir=tmp_path / "out", root=env["root"])


def _raw_ustar_member(name: str, declared_size: int, payload: bytes = b"") -> bytes:
    """Hand-craft a single ustar header block + payload + zero blocks.

    Lets us declare a header size independent of the payload length, so the
    verifier can be proven to reject an oversized header BEFORE allocating or
    reading content.
    """
    header = bytearray(tarfile.BLOCKSIZE)

    def put(field_off, value: bytes):
        header[field_off:field_off + len(value)] = value

    name_b = name.encode("utf-8")[:100]
    put(0, name_b)
    put(100, b"0000644\x00")
    put(108, b"0000000\x00")
    put(116, b"0000000\x00")
    put(124, b"%011o\x00" % declared_size)
    put(136, b"00000000000\x00")  # mtime
    put(148, b"        ")         # checksum placeholder (spaces)
    put(156, b"0")                # typeflag: regular file
    put(257, b"ustar\x0000")      # ustar magic
    put(263, b"00")               # version
    chk = sum(header)
    put(148, b"%06o\x00 " % chk)
    blocks = bytes(header)
    # Payload (deliberately short relative to declared_size).
    blocks += payload
    pad = (-len(payload)) % tarfile.BLOCKSIZE
    blocks += b"\x00" * pad
    blocks += b"\x00" * (tarfile.BLOCKSIZE * 2)  # archive end
    return blocks


def test_verify_rejects_oversized_declared_member_before_reading(tmp_path):
    """A member whose tar header declares > the member cap is rejected."""
    raw_tar = _raw_ustar_member(
        "bundle/run/manifest.json", sb.MAX_EVIDENCE_MEMBER_BYTES + 1, b"x")
    archive = tmp_path / "evil.tar.gz"
    with gzip.GzipFile(filename="", mode="wb", fileobj=archive.open("wb"),
                       mtime=0, compresslevel=9) as gz:
        gz.write(raw_tar)
    receipt = tmp_path / "evil.bundle.json"
    receipt.write_text(json.dumps({
        "bundle_schema_version": sb.BUNDLE_SCHEMA_VERSION,
        "authorizations": {f: False for f in (
            "production_authorized", "shortlist_policy_authorized",
            "training_authorized", "threshold_optimization_authorized")},
        "durability_status": sb.DURABILITY_STATUS,
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "archive_bytes": len(archive.read_bytes()),
    }))
    with pytest.raises(BundleError, match="unsafe declared size|declared size|exceeds"):
        verify_bundle(bundle_path=archive, receipt_path=receipt)


def test_verify_rejects_excessive_member_count(tmp_path, monkeypatch):
    """More than MAX_MEMBER_COUNT members is rejected."""
    _, _, archive, receipt, _ = _make_verified_bundle(tmp_path)
    monkeypatch.setattr(sb, "MAX_MEMBER_COUNT", 2)
    tamper_bundle(archive, receipt)  # keep marker consistent; archive valid
    with pytest.raises(BundleError, match="member count"):
        verify_bundle(bundle_path=archive, receipt_path=receipt)


def test_verify_rejects_cumulative_uncompressed_limit(tmp_path, monkeypatch):
    """Declared uncompressed total over the cap fails closed."""
    env = make_env(tmp_path)
    out = tmp_path / "out"
    res = create_bundle(run_dir=env["run_dir"], output_dir=out, root=env["root"])
    archive = out / res["archive_filename"]
    receipt = out / res["archive_filename"].replace(".tar.gz", ".bundle.json")
    monkeypatch.setattr(sb, "MAX_TOTAL_UNCOMPRESSED_BYTES", 10)  # below real total
    with pytest.raises(BundleError, match="uncompressed total|exceeds"):
        verify_bundle(bundle_path=archive, receipt_path=receipt)


def test_verify_rejects_oversized_metadata_member(tmp_path, monkeypatch):
    """A metadata member larger than the metadata cap is rejected."""
    _, _, archive, receipt, _ = _make_verified_bundle(tmp_path)
    # Set the metadata cap below the real manifest size.
    monkeypatch.setattr(sb, "MAX_METADATA_BYTES", 16)
    tamper_bundle(archive, receipt)
    with pytest.raises(BundleError, match="metadata limit|metadata member"):
        verify_bundle(bundle_path=archive, receipt_path=receipt)


def test_verify_rejects_overlong_member_path(tmp_path):
    """A member path longer than MAX_PATH_BYTES is unsafe.

    ustar cannot physically carry a >100-char name, so a too-long path is
    asserted at the guard (which also runs during verification for names
    injected via a hand-crafted/Pax archive).
    """
    assert sb._safe_archive_member_name("bundle/" + "a" * (sb.MAX_PATH_BYTES + 1)) is False
    assert sb._safe_archive_member_name("/etc/passwd") is False
    assert sb._safe_archive_member_name("bundle/../x.txt") is False
    assert sb._safe_archive_member_name("bundle\\x.txt") is False
    assert sb._safe_archive_member_name("other/x.txt") is False
    assert sb._safe_archive_member_name("bundle/run/manifest.json") is True
    # The verifier applies this same guard to every member name; names at or
    # beyond MAX_PATH_BYTES (which ustar cannot even carry in a header) are
    # rejected before any content is considered.
    overlong = "bundle/" + "a" * (sb.MAX_PATH_BYTES + 5) + ".txt"
    assert len(overlong.encode()) > sb.MAX_PATH_BYTES
    assert sb._safe_archive_member_name(overlong) is False


def test_verify_rejects_truncated_member(tmp_path):
    """A member whose actual bytes are shorter than its declared size fails."""
    _, _, archive, receipt, _ = _make_verified_bundle(tmp_path)
    # Build a genuinely valid bundle, then cut the tail off the compressed
    # archive: the final member/header becomes truncated mid-stream.
    data = archive.read_bytes()
    archive.write_bytes(data[: int(len(data) * 0.6)])
    rcpt = json.loads(receipt.read_text())
    rcpt["archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    rcpt["archive_bytes"] = len(archive.read_bytes())
    receipt.write_text(json.dumps(rcpt))
    marker = archive.with_name(archive.name + ".sha256")
    if marker.exists():
        marker.unlink()
    with pytest.raises(BundleError, match="corrupt|truncated|unreadable|SHA-256"):
        verify_bundle(bundle_path=archive, receipt_path=receipt)


def test_verified_source_streams_and_rehashes_exact_bytes(tmp_path):
    """A verified source streams the file and its on-stream hash matches."""
    import slumdog.shadow_bundle as mod
    f = tmp_path / "evidence.txt"
    payload = b"0123456789ABCDEF" * 64  # 1024 bytes (> one small read)
    f.write_bytes(payload)
    sha = hashlib.sha256(payload).hexdigest()
    with mod._verified_source(f, sha, len(payload), what="test") as proxy:
        out = b""
        while True:
            chunk = proxy.read(37)  # deliberately unaligned bounded reads
            if not chunk:
                break
            out += chunk
    assert out == payload


def test_verified_source_detects_growth_after_verification(tmp_path):
    """If the file grows after the pre-archive hash but before/during the
    tar streaming phase, the final streamed-size / identity check fails."""
    import slumdog.shadow_bundle as mod
    f = tmp_path / "evidence2.txt"
    payload = b"hello-streaming-world"
    f.write_bytes(payload)
    sha = hashlib.sha256(payload).hexdigest()

    # Make the pre-archive streaming phase see extra bytes by growing the file
    # between the two hashes: we do this by replacing the descriptor's read so
    # the SECOND pass (the tar stream) yields more than expected_size.
    with pytest.raises(mod.BundleSourceError, match="grew|do not match"):
        with mod._verified_source(f, sha, len(payload), what="test") as proxy:
            b""
            # Append after verification; the proxy/tar stream would now observe
            # growth. We emulate a concurrent writer mid-context.
            with open(f, "ab") as grow:
                grow.write(b"EXTRA")
            out = b""
            while True:
                c = proxy.read(64)
                if not c:
                    break
                out += c


def test_verified_source_detects_replacement_after_read(tmp_path):
    """If the source is replaced (inode change) during archiving, fail closed.

    The replacement keeps identical content/length so only the inode identity
    check can detect the check/use race.
    """
    import slumdog.shadow_bundle as mod
    f = tmp_path / "evidence3.txt"
    payload = b"abcdefghijklmnopqrstuvwxyz0123456789"
    f.write_bytes(payload)
    sha = hashlib.sha256(payload).hexdigest()
    ino_before = f.stat().st_ino
    with pytest.raises(mod.BundleSourceError, match="changed"):
        with mod._verified_source(f, sha, len(payload), what="test") as proxy:
            out = b""
            while True:
                c = proxy.read(64)
                if not c:
                    break
                out += c
            # Atomically replace with an identical-content new inode.
            tmp_swap = tmp_path / "swap.txt"
            tmp_swap.write_bytes(payload)
            os.replace(tmp_swap, f)
            assert f.stat().st_ino != ino_before


def test_create_no_completion_marker_when_self_verification_fails(tmp_path, monkeypatch):
    """If post-write self-verification fails, no receipt/marker is finalized."""
    env = make_env(tmp_path)
    out = tmp_path / "out"
    out.mkdir()

    def fail_self(archive_path, receipt_obj):
        # Force self-verification of the freshly written temp archive to fail.
        raise sb.BundleIntegrityError("simulated self-verification failure")

    monkeypatch.setattr(sb, "verify_archive_file", fail_self)
    with pytest.raises(BundleError, match="self-verification"):
        create_bundle(run_dir=env["run_dir"], output_dir=out, root=env["root"])
    assert list(out.glob("*.bundle.json")) == []
    assert list(out.glob("*.sha256")) == []
    assert list(out.glob("*.tar.gz")) == []  # temp archive removed on failure
    assert list(out.glob("*.tmp")) == []


def test_deterministic_archive_bytes_unchanged_by_streaming(tmp_path):
    """Streaming creation still yields byte-identical archives across runs."""
    env = make_env(tmp_path)
    r1, a1, _, _ = bundle_paths(env, tmp_path / "o1")
    r2, a2, _, _ = bundle_paths(env, tmp_path / "o2")
    assert a1.read_bytes() == a2.read_bytes()
    assert r1["archive_sha256"] == r2["archive_sha256"]
    # And the streamed archive verifies.
    assert verify_bundle(
        bundle_path=a1,
        receipt_path=tmp_path / "o1" / (r1["archive_filename"].replace(".tar.gz", ".bundle.json")),
    )["status"] == "BUNDLE_VERIFIED"
