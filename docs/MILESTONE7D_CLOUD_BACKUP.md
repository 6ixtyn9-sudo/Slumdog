# Milestone 7D — Cloud-Only Shadow Bundle Backup (synthetic)

**Status:** IMPLEMENTED / TESTED LOCALLY — **workflow NOT yet dispatched on GitHub**
**PR:** opened into `main` from `arena/01a0512f-slumdog` (NOT merged)
**Scope:** manually-triggered second-copy *procedure* for shadow bundles, using
only GitHub Actions artifact storage and the automatic `GITHUB_TOKEN`.

## What was added

| File | Purpose |
|---|---|
| `.github/workflows/shadow_bundle_cloud_backup.yml` | Manual `workflow_dispatch` workflow: build synthetic fixture → bundle → local verify → upload artifact → fresh-runner download → independent verify → verification receipt. **Committed on PR #13** (owner push `ba7d554` from the Codespace; provenance note below). |
| `scripts/synthetic_shadow_fixture.py` | Deterministic, network-free generator of an entirely synthetic completed shadow run (`SHADOW_SELECTIONS_EMITTED`, 1 primary + 2 cohort, synthetic participants only, injected decision clock safely before the frozen 24h cutoff). |
| `tests/test_synthetic_shadow_fixture.py` | 9 focused tests: selection shape, root-independent decision digest + deterministic history bytes, same-root reproducibility, synthetic-only participants, no network/collector references, fail-closed on existing root, bundle create+verify roundtrip, CLI behavior. |
| `tests/test_cloud_backup_workflow.py` | 12 contract tests over the workflow YAML: dispatch-only trigger, `contents: read`, concurrency, per-job timeouts, immutable-SHA pinning, no caches, explicit 30-day retention, fail-closed upload, verify job isolation — plus owner-added regressions: the workflow file is REQUIRED (missing file fails the suite, no skip), every embedded Python heredoc must COMPILE and be terminated, and the compact verification-receipt upload (name/path/retention/final-step) is asserted. |

No change to the shadow evaluator, R2, ranking, configs, or production code.

## Workflow file provenance (resolved)

GitHub refuses to let a GitHub App create or modify `.github/workflows/*`
without the `workflows` permission, and the Arena delivery token does not
carry it. The workflow file was therefore committed to the PR branch by the
**owner** from the data-bearing Codespace (commit `ba7d554`, refined in
`9113116`), together with owner-added regression tests (`1cd9a14`,
`c35a8d1`) that make a missing workflow file a hard test FAILURE (no skip),
compile every embedded Python heredoc, and assert the verification-receipt
upload. All 12 workflow-contract tests now pass with zero skips.

## Workflow contract

- **Trigger:** `workflow_dispatch` ONLY (no schedule, no push, no PR).
- **Permissions:** `contents: read` (top level; nothing else requested).
- **Actions pinned to immutable commit SHAs** (verified against the tags at
  implementation time; re-verify before re-pinning):
  - `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1` (v7.0.1)
  - `actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97` (v7.0.0)
  - `actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` (v7.0.1)
  - `actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c` (v8.0.1)
- **Timeouts:** 15 minutes on every job.
- **Caches:** none (no cache action, setup-python cache off, `pip --no-cache-dir`).
- **Concurrency:** group `shadow-bundle-cloud-backup`, queued — never two
  simultaneous runs; a dispatched run is never cancelled mid-flight.
- **Jobs:**
  1. `bundle-create` — installs the package (`pip install --no-cache-dir .`),
     builds the synthetic run under `$RUNNER_TEMP`, bundles it with
     `python -m slumdog.shadow_bundle create`, verifies locally
     (`BUNDLE_VERIFIED` required), runs `sha256sum -c` against the marker,
     uploads the **three-file triplet** (`.tar.gz`, `.bundle.json`,
     `.tar.gz.sha256`) as ONE artifact named
     `shadow-bundle-<target_date>-<run_id>-<sha256-prefix-8>`.
  2. `bundle-verify` — fresh runner, depends ONLY on the downloaded artifact:
     recomputes the archive SHA-256 and requires an exact match with the
     creation job's value, re-runs `python -m slumdog.shadow_bundle verify`
     (must print `BUNDLE_VERIFIED`), re-checks the checksum marker, then
     publishes a compact JSON verification receipt to the job summary and as a
     separate artifact.
- **Fail-closed:** any hash mismatch, missing file, or non-`BUNDLE_VERIFIED`
  result fails the job; upload steps use `if-no-files-found: error`.
- **Logs:** filenames, hashes, ids, and digests only — no bundle contents, no
  participant data.
- **No** prediction-source access, real capture, real shadow run, production
  publication, training, or external credentials.

## Retention — exact configuration

Every artifact upload sets `retention-days: 30` explicitly. **GitHub Actions
artifacts are NOT permanent storage**: they expire (30 days here) and must not
be treated as the durable second copy long-term. Per the owner's decision this
procedure is the *interim* second copy for the first shadow runs; if the
experiment continues, migrate bundles to durable object storage or a private
release asset (explicitly a separate approval).

## Verification receipt fields

`slumdog_cloud_bundle_verification_v1`: workflow run id + attempt, repository,
artifact name, target date, run id, archive SHA-256, `bundle_verified: true`,
creation job (`bundle-create`), verification job (`bundle-verify`), UTC
timestamp, retention days, and fail-closed authorization flags (production /
shortlist policy / training / threshold optimization — all `false`).
The receipt is uploaded as an artifact and appended to the job summary; it is
never committed automatically.

## How to dispatch (owner)

```bash
gh workflow run shadow_bundle_cloud_backup.yml --ref main
gh run watch            # then attach to the run
```

Or: GitHub → Actions → "Slumdog · Shadow Bundle Cloud Backup (synthetic)" →
Run workflow. On success, download the `shadow-bundle-receipt-*` artifact as
the verification evidence.

## Honest status distinctions

- Workflow **implemented**: YES — committed on PR #13 (owner push; see provenance above).
- Workflow **syntax validated locally**: YES (PyYAML parse + 12 structural
  contract tests, zero skips; embedded Python heredocs compile).
- Workflow **executed on GitHub**: **NO** — not dispatched yet.
- Artifact **uploaded / downloaded in a separate job / independent verification
  passed**: **NO** — all pending the first real dispatch.
- **Do not claim cloud backup works until a manually dispatched run succeeds
  end-to-end.**
