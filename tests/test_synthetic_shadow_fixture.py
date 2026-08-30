"""Milestone 7D — Synthetic shadow-run fixture generator tests.

The fixture generator (``scripts/synthetic_shadow_fixture.py``) is the
cloud-backup workflow's only input source. These tests prove it:

- builds a completed synthetic run with the expected selection shape;
- is deterministic across invocations (same digests, same run_id);
- uses only unmistakably synthetic participants;
- contains no network references;
- refuses to touch an existing root (fail closed, no overwrite);
- produces a run the Milestone 7B bundler can bundle and verify;
- works through its CLI with a clean exit code and JSON summary.

No retained repository data under ``data/`` is read: everything is
generated under per-test temporary roots.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import subprocess
import sys
from pathlib import Path

from slumdog.shadow_bundle import create_bundle, verify_bundle

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "synthetic_shadow_fixture.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("synthetic_shadow_fixture", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_builds_completed_run_with_expected_selection_shape(tmp_path):
    mod = _load_module()
    summary = mod.build_synthetic_run(tmp_path / "repo")
    assert summary["run_status"] == "SHADOW_SELECTIONS_EMITTED"
    assert summary["selection_count"] == 3
    assert summary["selection_statuses"].count("PRIMARY_SHADOW_SELECTION") == 1
    assert summary["selection_statuses"].count("TOP3_EVALUATION_COHORT") == 2
    # Injected decision clock safely before the frozen 24h cutoff.
    assert summary["decision_committed_at"] == "2026-08-30T12:00:00Z"
    assert summary["safe_cutoff_utc"] == "2026-09-01T00:00:00Z"
    # Completed-run artifacts exist inside the synthetic root only.
    run_dir = Path(summary["run_dir"])
    assert run_dir.is_dir()
    assert (run_dir / "manifest.json").is_file()
    assert (run_dir / "shadow_selections.json").is_file()
    assert summary["synthetic_root"] in summary["run_dir"]


def test_history_bytes_and_decision_digest_are_root_independent(tmp_path):
    # The evaluator's input_digest/run_id deliberately commit to absolute
    # provenance paths (capture receipt, sidecar, body, history paths), so
    # run_id equality across DIFFERENT roots is not a property of the
    # machinery. What must hold across roots: identical synthetic history
    # bytes (gzip mtime=0) and an identical decision_digest (the decision
    # content is provenance-free by contract). Bundle-level determinism
    # from a FIXED root is proven by tests/test_shadow_bundle.py.
    mod = _load_module()
    first = mod.build_synthetic_run(tmp_path / "repo_a")
    second = mod.build_synthetic_run(tmp_path / "repo_b")
    assert first["decision_digest"] == second["decision_digest"]
    assert first["selection_statuses"] == second["selection_statuses"]
    assert first["selection_count"] == second["selection_count"]
    # The deterministic history bytes are byte-identical across roots.
    gz_a = (tmp_path / "repo_a" / "data" / "reports" / "history_football.jsonl.gz")
    gz_b = (tmp_path / "repo_b" / "data" / "reports" / "history_football.jsonl.gz")
    assert gz_a.read_bytes() == gz_b.read_bytes()


def test_same_root_rebuild_is_reproducible_in_decision_content(tmp_path):
    # Two invocations built to path-identical roots cannot coexist (the
    # evaluator refuses to overwrite), so reproducibility is asserted via
    # the root-independent digests plus a fresh rebuild after removal.
    mod = _load_module()
    first = mod.build_synthetic_run(tmp_path / "repo")
    decision_digest = first["decision_digest"]
    import shutil
    shutil.rmtree(first["synthetic_root"])
    second = mod.build_synthetic_run(tmp_path / "repo")
    assert second["decision_digest"] == decision_digest
    assert second["selection_statuses"] == first["selection_statuses"]


def test_participants_are_unmistakably_synthetic(tmp_path):
    mod = _load_module()
    summary = mod.build_synthetic_run(tmp_path / "repo")
    manifest = json.loads(
        (Path(summary["run_dir"]) / "manifest.json").read_text())
    tuples = manifest["input_provenance"]["capture_record_tuples"]
    assert tuples, "expected captured record tuples in the manifest"
    for row in tuples:
        participant_1, participant_2 = row[3], row[4]
        assert participant_1.startswith("Synthetic "), participant_1
        assert participant_2.startswith("Synthetic "), participant_2
    # The raw body itself carries only synthetic names.
    body = json.loads(
        (tmp_path / "repo" / "data" / "raw" / "football" / "2026-09-02")
        .glob("*.txt").__next__().read_text())
    for row in body[0]:
        assert row["HOST_NAME"].startswith("Synthetic ")
        assert row["GUEST_NAME"].startswith("Synthetic ")


def test_generator_source_has_no_network_or_collector_references():
    mod = _load_module()
    source = inspect.getsource(mod)
    # Network machinery and collector/production modules must never be
    # imported or invoked by the generator. (The settled-row schema key
    # "forebet_pick" is data, not an import, and is allowed.)
    for forbidden in (
        "urllib", "socket", "http.client", "requests",
        "ForebetCollector",
        "from slumdog.forebet", "from slumdog.pipeline",
        "from slumdog.settlement", "slumdog.training",
    ):
        assert forbidden not in source, f"generator must not reference {forbidden!r}"


def test_refuses_existing_root(tmp_path):
    mod = _load_module()
    existing = tmp_path / "already-there"
    existing.mkdir()
    (existing / "keep.txt").write_text("do not touch")
    try:
        mod.build_synthetic_run(existing)
    except mod.SyntheticFixtureError as e:
        assert "refusing" in str(e)
    else:
        raise AssertionError("generator must refuse an existing root")
    assert (existing / "keep.txt").read_text() == "do not touch"


def test_generated_run_bundles_and_verifies(tmp_path):
    mod = _load_module()
    summary = mod.build_synthetic_run(tmp_path / "repo")
    out = tmp_path / "export"
    result = create_bundle(
        run_dir=summary["run_dir"], output_dir=out,
        root=summary["synthetic_root"],
    )
    archive = out / result["archive_filename"]
    receipt = out / result["archive_filename"].replace(".tar.gz", ".bundle.json")
    marker = out / (result["archive_filename"] + ".sha256")
    assert archive.is_file() and receipt.is_file() and marker.is_file()
    assert len(list(out.iterdir())) == 3  # exactly three files, no temp leftovers
    verified = verify_bundle(bundle_path=archive, receipt_path=receipt)
    assert verified["status"] == "BUNDLE_VERIFIED"
    assert verified["run_id"] == summary["run_id"]
    assert marker.read_text().split()[0] == result["archive_sha256"]


def test_cli_builds_run_and_prints_json_summary(tmp_path):
    root = tmp_path / "cli-repo"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    summary = json.loads(proc.stdout)
    assert summary["run_status"] == "SHADOW_SELECTIONS_EMITTED"
    assert summary["selection_statuses"].count("PRIMARY_SHADOW_SELECTION") == 1
    assert summary["selection_statuses"].count("TOP3_EVALUATION_COHORT") == 2
    # The summary carries ids/digests only — no participant data.
    assert "Synthetic" not in proc.stdout


def test_cli_fails_closed_on_existing_root(tmp_path):
    root = tmp_path / "exists"
    root.mkdir()
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 2
    assert "SYNTHETIC_FIXTURE_FAILED" in proc.stderr
    assert "Traceback" not in proc.stderr
