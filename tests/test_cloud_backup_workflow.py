"""Milestone 7D — Cloud backup workflow contract tests.

These tests statically enforce the security/durability contract of
``.github/workflows/shadow_bundle_cloud_backup.yml`` so it cannot silently
drift:

- manual ``workflow_dispatch`` ONLY (no schedule, no push, no PR trigger);
- minimal permissions (``contents: read``);
- concurrency control (no simultaneous duplicate runs);
- a timeout on every job;
- every ``uses:`` pinned to an immutable full commit SHA (no floating tags);
- no cache usage of any kind;
- explicit artifact retention (documented exact days);
- fail-closed upload settings;
- the verification job runs on a fresh runner and depends ONLY on the
  downloaded artifact from the creation job.

DELIVERY NOTE: the Arena delivery bot's GitHub App token does not carry
the ``workflows`` permission, so the workflow FILE itself cannot be pushed
by the agent; it is delivered in the pull-request body for the owner to
commit from the data-bearing Codespace (one short step). Until that file
lands, this module SKIPS with an explicit reason — never silently passes —
and activates automatically once the file exists.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "shadow_bundle_cloud_backup.yml"

if not WORKFLOW.is_file():
    pytest.skip(
        "shadow_bundle_cloud_backup.yml is not present yet: the Arena bot "
        "token lacks the 'workflows' permission, so the owner must commit "
        "the workflow file (delivered in the Milestone 7D PR body) from "
        "the Codespace; these contract tests activate once it lands",
        allow_module_level=True,
    )

PINNED_USE_RE = re.compile(r"^actions/[a-z-]+@[0-9a-f]{40}$")
RETENTION_DAYS = 30


def _load() -> dict:
    assert WORKFLOW.is_file(), f"workflow missing: {WORKFLOW}"
    return yaml.safe_load(WORKFLOW.read_text())


def _jobs(doc: dict) -> dict:
    jobs = doc.get("jobs", {})
    assert isinstance(jobs, dict) and jobs, "workflow must define jobs"
    return jobs


def test_manual_dispatch_only():
    triggers = _load()["on"] if "on" in _load() else _load()[True]
    # YAML 1.1 parses bare `on:` as boolean True; accept both spellings.
    assert triggers == {"workflow_dispatch": {}}, (
        f"only workflow_dispatch may trigger this workflow, got: {triggers}"
    )


def test_minimal_permissions():
    permissions = _load().get("permissions")
    assert permissions == {"contents": "read"}


def test_concurrency_prevents_simultaneous_duplicates():
    concurrency = _load().get("concurrency")
    assert isinstance(concurrency, dict)
    assert concurrency.get("group"), "concurrency group must be named"
    # cancel-in-progress is deliberately false: a dispatched run is never
    # silently cancelled; extra dispatches queue behind the in-flight run.
    assert concurrency.get("cancel-in-progress") is False


def test_every_job_has_a_timeout():
    jobs = _jobs(_load())
    for job_id, job in jobs.items():
        timeout = job.get("timeout-minutes")
        assert isinstance(timeout, int) and 0 < timeout <= 30, (
            f"job {job_id!r} must set a sane timeout-minutes, got {timeout}"
        )


def test_all_actions_pinned_to_immutable_commit_shas():
    jobs = _jobs(_load())
    uses = []
    for job_id, job in jobs.items():
        for step in job.get("steps", []):
            if "uses" in step:
                uses.append((job_id, step["uses"]))
    assert uses, "workflow must use official actions"
    for job_id, ref in uses:
        assert PINNED_USE_RE.match(ref), (
            f"job {job_id!r} action not pinned to a full commit SHA: {ref}"
        )


def test_no_caches_anywhere():
    doc = _load()
    jobs = _jobs(doc)
    for job_id, job in jobs.items():
        for step in job.get("steps", []):
            uses = str(step.get("uses", ""))
            # No caching action may be used at all.
            assert "cache" not in uses, f"job {job_id!r} must not use a cache action"
            # setup-python must not enable its pip cache.
            if "setup-python" in uses:
                assert step.get("with", {}).get("cache") in (None, "none", False), (
                    f"job {job_id!r} must not enable the pip cache"
                )
    # The pip invocations themselves must run with caching disabled.
    text = WORKFLOW.read_text()
    assert text.count("pip install --no-cache-dir .") >= 2, (
        "both jobs must install with --no-cache-dir"
    )


def test_artifact_retention_is_explicit_and_short():
    jobs = _jobs(_load())
    uploads = []
    for job_id, job in jobs.items():
        for step in job.get("steps", []):
            if "upload-artifact" in str(step.get("uses", "")):
                uploads.append((job_id, step.get("with", {})))
    assert uploads, "workflow must upload artifacts"
    for job_id, with_ in uploads:
        assert with_.get("retention-days") == RETENTION_DAYS, (
            f"job {job_id!r} upload must pin retention-days={RETENTION_DAYS}"
        )
        assert with_.get("if-no-files-found") == "error", (
            f"job {job_id!r} upload must fail when files are missing"
        )


def test_verify_job_downloads_artifact_and_needs_create_job():
    jobs = _jobs(_load())
    assert set(jobs) == {"bundle-create", "bundle-verify"}
    verify = jobs["bundle-verify"]
    assert verify.get("needs") == "bundle-create", (
        "the verify job must depend on the create job"
    )
    downloads = [
        step for step in verify.get("steps", [])
        if "download-artifact" in str(step.get("uses", ""))
    ]
    assert len(downloads) == 1, "verify job must download exactly one artifact"
    # It must download the bundle artifact produced by the create job.
    name = downloads[0].get("with", {}).get("name", "")
    assert "needs.bundle-create.outputs" in str(name)


def test_artifact_name_carries_date_runid_and_hash_prefix():
    # The artifact name is assembled in a step that composes target date,
    # run id, and the archive SHA-256 prefix into the name.
    text = WORKFLOW.read_text()
    for fragment in (
        "steps.bundle.outputs.target_date",
        "steps.bundle.outputs.run_id",
        "steps.bundle.outputs.archive_sha256",
    ):
        assert fragment in text, f"artifact name must use {fragment}"


def test_workflow_never_runs_tests_or_evaluator_on_real_data():
    # Structural guard: the workflow must not invoke the shadow evaluator,
    # Forebet collectors, or the repository's data/ directory as an input.
    text = WORKFLOW.read_text()
    for forbidden in (
        "slumdog.shadow_evaluator",
        "forebet",
        "data/raw",
        "data/reports/shadow",
        "--root .",
    ):
        assert forbidden not in text, (
            f"workflow must not reference {forbidden!r}"
        )


def test_embedded_python_blocks_complete_and_verification_receipt_uploaded():
    """Embedded Python must compile and the verification receipt must upload."""
    jobs = _jobs(_load())

    embedded_blocks = []

    for job_id, job in jobs.items():
        for step in job.get("steps", []):
            run = step.get("run")
            if not isinstance(run, str):
                continue

            lines = run.splitlines()
            index = 0

            while index < len(lines):
                if "<<'PY'" not in lines[index]:
                    index += 1
                    continue

                start = index + 1
                end = start
                while end < len(lines) and lines[end].strip() != "PY":
                    end += 1

                assert end < len(lines), (
                    f"unterminated Python heredoc in job {job_id!r}, "
                    f"step {step.get('name')!r}"
                )

                body = "\n".join(lines[start:end]) + "\n"
                compile(
                    body,
                    f"<workflow:{job_id}:{step.get('name', 'unnamed')}>",
                    "exec",
                )
                embedded_blocks.append((job_id, step.get("name"), body))
                index = end + 1

    assert embedded_blocks, "workflow must contain embedded Python blocks"

    verify_steps = jobs["bundle-verify"].get("steps", [])
    receipt_uploads = [
        step
        for step in verify_steps
        if "upload-artifact@" in str(step.get("uses", ""))
        and str(step.get("with", {}).get("name", "")).endswith("-verification")
    ]

    assert len(receipt_uploads) == 1, (
        "verify job must upload exactly one compact verification receipt"
    )

    upload = receipt_uploads[0]
    with_ = upload.get("with", {})

    assert with_.get("path") == "cloud-bundle-verification.json"
    assert with_.get("retention-days") == RETENTION_DAYS
    assert with_.get("if-no-files-found") == "error"

    assert verify_steps[-1] is upload, (
        "verification receipt upload must be the final verify-job step"
    )

