# Slumdog State — Canonical Current Truth

**Last verified:** 2026-09-03 (UTC) — **PR #15 MERGED INTO `main` (`main` @ `3b57464`)** / **FORWARD SHADOW WORKFLOW LIVE** (`.github/workflows/forward_shadow.yml`, `contents: write`, `actions: read`) / **SHADOW EVIDENCE IN GIT** (27 files) / **CONTRACT AMENDED** (owner directive 2026-09-03 "no ledgers in Codespace") / **CODESPACE DEV-ONLY** / **PRODUCTION NOT AUTHORIZED** / **SHORTLIST POLICY NOT AUTHORIZED**. 694 tests pass (zero skips).

**Branch:** `arena/01a066f2-slumdog` (from `main` @ `3b57464`); `main` is only permanent branch
**Doc canonical path:** `docs/STATE.md`
**Base commit:** `3b57464ef36181519f26c34ad7018aca561a5c99` (PR #15 merged)
## Permanent Product Mission

> **Slumdog identifies a small daily shortlist of participants that Forebet considers underdogs but whose available pre-event evidence indicates a credible outright-win upset.**

Invariants:
- UNDERDOG_WIN outright only, draw=failed
- Odds optional metadata only
- Never force pick; NO_STRONG_UNDERDOG valid
- Training frozen until dataset contract approved

## Milestones

**Milestones 0–7B COMPLETE AND MERGED (`main` @ `41b345f`); Milestone 7D (cloud-only bundle backup) IMPLEMENTED/TESTED LOCALLY with PR #13 open (NOT merged; workflow COMMITTED on the PR branch, contract tests 12/12 zero skips; workflow NOT dispatched on GitHub) — first real shadow run BLOCKED on (1) one successful end-to-end manual dispatch of the cloud backup workflow and (2) an authorized gentle future-date capture**

- Milestone 0: Governance — STATE.md → docs/STATE.md, AGENTS.md, README, 5 corrections
- Milestone 1: Audit — docs/MILESTONE1_AUDIT.md REFERENCE, 10 gaps, 8 refinements
- Milestone 2: Price-free identity/label — src/slumdog/underdog.py, 40 tests, SPORTS registry draw capability
- Milestone 3: Feature timing — docs/FEATURE_TIMING_CONTRACT.md, period_values UNKNOWN PROHIBITED
- Milestone 4: Architecture — src/slumdog/dataset.py price-free examples, receipt with raw/canonical accounting, deterministic dedup composite key (sport,event_id,event_date), 30 tests
- Milestone 4E: Hardening — no unsafe defaults, no silent swallowing, strengthened digest, provenance validation, disposition vocabulary SETTLED/SETTLED_CUP/SETTLED_DRAW/VOID/NO_CONTEST, winner_index strict bool/float/string rejected, deterministic provenance merge
- Milestone 4F: Conflict census — ValidEventWithSource audit-only source tracking (file, line/index), ConflictGroup compact report, classification DOMAIN/OUTCOME/PROBABILITY/DISPOSITION/PROVENANCE/MULTIPLE, census mode --conflict-report, status DATA_CONFLICTS, nonzero exit, receipt with conflicting_composite_keys, conflicting_rows, conflicts_by_sport, conflicts_by_field, conflicts_with_valid_raw_sha256, conflicts_without_valid_raw_sha256, examples not emitted, normal builder still fail-closed
- Milestone 5: Historical integrity — schema-exclusion diagnostics; six malformed American-football rows classified (origin layer UNKNOWN); hockey:278977 double-write mechanism resolved as far as retained evidence allows (one six-row parse/write batch by the pre-hardening writer; recurring first-row-repeated-at-end pattern; origin layer UNKNOWN); provenance policy recorded as unapproved for training/production
- Milestone 6A: Research dataset readiness — bounded-memory v2 incremental builder (`src/slumdog/research_dataset.py`, `src/slumdog/research_builder.py`); explicit `--research-exclude-conflicts` opt-in; census-before-collapse ordering; whole-key conflict exclusion; deterministic `/tmp`-only examples; simplified receipt `RESEARCH_DATASET_READY_WITH_LIMITATIONS`; COMPLETE AND MERGED in PR #9
- Milestone 6B: Transparent non-trained walk-forward baselines — `src/slumdog/baseline_analyzer.py` and `src/slumdog/research_baselines.py`; two-pass architecture; frozen configuration verification against canonical SHA-256 `666dabe7ea21e11867cf4816f4c2edcd771247646c6c9d7726c22611cda700a1`; Pass 1 streaming integrity checks; Pass 2 missingness, 7 pre-declared signals with precedence, rules R0/R1/R2, streaks, quota vs non-quota selections, hit rates; atomic `/tmp` output writing; 27 focused tests; training remains frozen; production unauthorized
- Milestone 7 (IMPLEMENTED LOCALLY / NO REAL SHADOW RUN PERFORMED / FIRST REAL RUN BLOCKED ON FULL-PAYLOAD BACKUP AND AUTHORIZED FUTURE CAPTURE): shadow pick evaluator refactored into four modules — `slumdog.shadow_contracts` (lowest-layer `PreEventRecord` + `from_event_snapshot`, no upward dependency), `slumdog.capture_loader` (read-only capture receipt → `PreEventRecord`, derives `current_only` rejection from `SPORTS`), `slumdog.history_loader` (read-only history loader for `settled_history.json` and `history_*.jsonl.gz`, balanced accounting, streamed gzip, bounded size, v2 validity), and a rewritten `slumdog.shadow_evaluator` (orchestration + R2/R1 via `baseline_analyzer` with no duplicated thresholds, atomic write, no-overwrite, BLOCKED receipts in separate path, 24h timing gate, per-sport-day primary + rank-2/3 cohort, ranks-4+ in `considered_pool[]` with `considered_status = ELIGIBLE_RANKED_BEYOND_TOP3` and never in `selections[]`, `decision_digest` independent of per-snapshot source fields but committed to BOTH arrays, history memory bound tightened to 256 MiB default with explicit per-call override). Tests are focused (89 new behavioral tests; no test-count target; no Git dependency). CLI: `python -m slumdog.shadow_evaluator --help` returns 0; CLI also exposes `--history-max-interim-bytes`. **NO REAL SHADOW RUN PERFORMED — first real run BLOCKED on full-payload backup and authorized future capture.**

- Milestone 7B (IMPLEMENTED/TESTED LOCALLY / PR opened NOT merged / NO REAL BUNDLE, CAPTURE, OR RUN): verifiable full-payload shadow bundle — `slumdog.shadow_bundle` (stdlib-only, imports no other Slumdog module). `create` packages a completed run's exact `shadow_selections.json` + `manifest.json` plus both frozen configs, the capture receipt, every referenced sidecar/raw body, and every history input into a deterministic content-addressed `bundle/` tar.gz (sorted members, uid/gid 0, mode 0644, mtime 0, gzip mtime 0, regular files only) with a canonical `inventory.json`, human `README.txt`, external `*.bundle.json` receipt, and `*.sha256` marker; refuses partial/blocked runs, corrupt payloads, hash mismatches, missing inputs, unsupported schema/version, out-of-root/traversal/symlink paths, and pre-existing outputs (no force); temp-sibling + atomic-rename finalization with the checksum marker last. `verify` runs fully in memory (no extraction): archive SHA-256, receipt/declaration authorization flags (all false), safe members (no absolute/`..`/symlink/device/FIFO/duplicate/unexpected), inventory schema + per-member hash/size, payload-vs-manifest, recomputed frozen-config + declaration canonical hashes, and recomputed input/decision digests + run id; exit 0 with `BUNDLE_VERIFIED` or exit 2 with a clean `BUNDLE_VERIFY_FAILED`. Determinism proven by two-out-dir byte-identical archives via the CLI. 67 focused synthetic tests (incl. bounded-memory streaming); full suite 601 passed. Durability status `LOCAL_EXPORT_READY_FOR_INDEPENDENT_COPY`. Training FROZEN; production/shortlist/threshold NOT authorized. Docs: `docs/MILESTONE7B_SHADOW_BUNDLE.md`.

- Milestone 7D (IMPLEMENTED/TESTED LOCALLY / PR opened NOT merged / WORKFLOW NOT DISPATCHED ON GITHUB): cloud-only second-copy procedure — `.github/workflows/shadow_bundle_cloud_backup.yml` (manual `workflow_dispatch` ONLY; `permissions: contents: read`; actions/checkout, actions/setup-python, actions/upload-artifact, actions/download-artifact pinned to immutable full commit SHAs; 15-minute timeout on every job; no caches of any kind; concurrency-queued, never simultaneous; EXPLICIT 30-day artifact retention — Actions artifacts are NOT permanent storage; fail-closed on any hash/verify mismatch; logs carry filenames/hashes/ids only, never bundle contents or participants) plus `scripts/synthetic_shadow_fixture.py` (deterministic, network-free generator of an entirely synthetic completed run: `SHADOW_SELECTIONS_EMITTED` with 1 primary + 2 top-3 cohort, synthetic participants only, injected decision clock 36h before the frozen 24h cutoff, reads only the two frozen configs after canonical-hash verification, never writes inside the repository, refuses existing roots) and 21 focused tests (9 fixture + 12 workflow-contract, incl. owner-added regressions: required workflow presence, embedded-heredoc compilation, verification-receipt upload). Create job: fixture → `python -m slumdog.shadow_bundle create` → local verify (`BUNDLE_VERIFIED` required) → `sha256sum -c` → ONE artifact `shadow-bundle-<target_date>-<run_id>-<sha256-prefix-8>` holding the exact three-file triplet. Verify job on a FRESH runner depending only on the downloaded artifact: recompute archive SHA-256 (exact match vs creation job required) → `shadow_bundle verify` (`BUNDLE_VERIFIED` required) → marker re-check → compact JSON receipt `slumdog_cloud_bundle_verification_v1` (workflow run id/attempt, artifact name, target date, run id, archive SHA-256, creation/verification job identity, UTC timestamp, retention days, authorization flags all false) published to the job summary and as a separate artifact, never auto-committed. No change to the shadow evaluator, R2, ranking, configs, or production code. Status distinctions: implemented YES (committed on PR #13) / syntax validated YES (PyYAML + 12 contract tests, ZERO skips; a missing workflow file now FAILS the suite) / executed on GitHub NO / artifact uploaded NO / independent verification passed NO — cloud backup must NOT be claimed working until a real manual dispatch succeeds. Docs: `docs/MILESTONE7D_CLOUD_BACKUP.md`.

## Real-Data Census (Codespace retained ledgers)

```
Status: DATA_CONFLICTS
Files found: 11
Files empty: 0
Files unreadable: 0
Raw input rows: 655,394
Schema excluded: 6 (SCHEMA_MISSING_PARTICIPANT_1=6)
Valid loaded rows: 655,388
Exact duplicates collapsed: 279
Canonical non-conflicting rows: 655,107
Eligible examples before conflict gate: 654,029
Builder exclusions: 1,078
Conflicting composite keys: 1
Conflicting rows: 2
Conflict sport: hockey=1
Conflict fields: score_1, score_2, period_scores_1, period_scores_2
Conflict valid SHA: 0, missing SHA: 1
Provenance present: 0, missing: 654,029
Date range: 2023-02-12 through 2026-08-21
```

Accounting:
```
655,394 = 6 + 655,388
655,388 = 279 + 655,107 + 2
655,107 = 654,029 + 1,078
```

Known conflict:
```
hockey / hockey:278977 / 2023-08-20
Netherlands W vs Denmark W
Variant A: 1–6 periods 0,1,0 / 2,1,3
Variant B: 0–4 periods 0,0,0 / 1,2,1
File: data/reports/history_hockey.jsonl.gz SHA 36eec2b1493aca1b52f92d843485794255db02d246e9d5b6ced4a18b4c371542 lines 62/67
Classification: OUTCOME_CONFLICT
Both lack raw_sha256/captured_at, no selection made
```

Diagnostic:
```
history_hockey.json: dict container, keys [daily_receipts, dates_completed, dates_requested, end, failures, history_file, priced_rows, settled_rows, sport, start, void_rows], size 426204
```

Temporary artifacts (not committed):
```
/tmp/slumdog_price_free/receipt.json
/tmp/slumdog_price_free/conflicts.json
```

## Current Status

```
Milestones 0–7D: COMPLETE AND MERGED (PR #13 merged at b086eae, 622 tests)
Settlement module (P1): IMPLEMENTED — shadow_settle.py + 41 focused tests
Forward batch workflow (P3): CREATED — forward_shadow.yml + driver script + 20 contract tests
Draw-avoidance analysis (P7): CREATED — draw_analysis.py + 11 focused tests
Timing-V2 proposal (P6): DRAFTED — docs/TIMING_V2_PROPOSAL.md (not implemented)
Real shadow runs: EXIST IN CODESPACE (5 forward dates 2026-09-05..09, all BUNDLE_VERIFIED)
Real settlement: 2026-09-02 settled 2026-09-03 (primary SUCCESS, top-3 1/3)
Canonical config SHA-256: 666dabe7ea21e11867cf4816f4c2edcd771247646c6c9d7726c22611cda700a1 (VERIFIED)
New shadow declaration canonical SHA-256: dd08976a262e7a1882a4e29846612094c20447faf587c01a42608d57f4f4d597 (VERIFIED)
Tests: 694 passed (622 + 41 + 20 + 11)
Training: FROZEN
Production: NOT AUTHORIZED
Shortlist policy: NOT AUTHORIZED
Selection width: 1 primary + rank-2/3 cohort (FROZEN; grading all ranks 1..N is authorized)
Next: settle 2026-09-05..09 as each date's matches complete (P1 continuation)
```

## System Maturity: EARLY STAGE (~10% complete)

**Infrastructure: production-grade.** Workflow, git persistence, artifacts, settlement grading — all working.

**Prediction system: infancy.** Minimal viable instrument, not a validated predictor.

**What hasn't been done:**
- **Multi-sport:** 14 sports defined in `sports.py`, only football collected
- **Facet validation:** Football has 11 defined facets but only 17 derived stats captured; no Forebet HTML audit
- **Model training:** `MODEL_TRAINING_ALLOWED=False` — frozen heuristics, not trained models
- **Feature engineering:** Only basic statistics; no use of full facet set

**The long road ahead:**
1. Audit Forebet for all 14 sports (availability + facets)
2. Validate facet extraction from HTML/JSON
3. Decide sport prioritization (data density)
4. Enable training once facets validated
5. Multi-sport rollout (one sport at a time)
6. Feature engineering using full facet set
7. Model development and validation

**Current experiment:** Can frozen heuristics using 17 stats pick football underdogs?
**Future experiment:** Can trained models using full facets pick underdogs across sports?

## Training / Production

- **Training:** FROZEN (MODEL_TRAINING_ALLOWED=False)
- **Production:** NOT AUTHORIZED
- **Research dataset measurement (Milestone 6A): COMPLETE**
- **Research baseline evaluation (Milestone 6B): IMPLEMENTED** — non-model descriptive statistics, non-trained comparator rules (R0, R1, R2), pre-declared signal buckets. Not authorized: fitted models, threshold optimization, calibrated probabilities, ranking, daily shortlist, shadow picks, production, wagering.
- **Shadow pick evaluation (Milestone 7): IMPLEMENTED LOCALLY / NO REAL SHADOW RUN PERFORMED / FIRST REAL RUN BLOCKED ON FULL-PAYLOAD BACKUP AND AUTHORIZED FUTURE CAPTURE** — frozen R2 eligibility + R1 ranking, per-sport-day primary + rank-2/3 cohort (rank-4+ tracked in `considered_pool[]` with `considered_status = ELIGIBLE_RANKED_BEYOND_TOP3` and never in `selections[]`), no global cap, 24h pre-event timing gate (frozen in declaration), 4-ID split (run_id / input_digest / decision_digest / decision_committed_at), `decision_digest` independent of per-snapshot source fields (odds-only differences produce the same `decision_digest`), atomic no-overwrite artifact under `data/reports/shadow/<target_date>/<run_id>/`, BLOCKED receipts under `data/reports/shadow/<target_date>/BLOCKED/`, history memory bound 256 MiB default with explicit `--history-max-interim-bytes` override. Not authorized: production, training, threshold optimization, calibrated probabilities, real-data run (no real run performed).
- **Dataset builder strict mode:** fail-closed, correctly refuses corrupted ledger, does not guess, delete, or silently quarantine
- **period_values:** UNKNOWN and PROHIBITED per FEATURE_TIMING_CONTRACT.md
- **Source-conflict limitation:** SettledEvent does not represent source conflict; not in digest; builder assumes no conflict; documented

## Next Milestone

**Milestone 7 — IMPLEMENTED LOCALLY (89 focused tests, 515 total; NO REAL SHADOW RUN PERFORMED; first real shadow run BLOCKED on full-payload backup and authorized future capture; no commit, push, or PR for M7 in this session)**

- Authorizations: `shadow_evaluation_authorized=true`,
  `production_authorized=false`, `shortlist_policy_authorized=false`,
  `training_authorized=false`, `threshold_optimization_authorized=false`
  (fail-closed at declaration load).
- Frozen rule source: R2 read from
  `config/research_baselines_v1.json:rules.R2_CONSERVATIVE_FIXED_RULE`
  (canonical SHA-256
  `666dabe7ea21e11867cf4816f4c2edcd771247646c6c9d7726c22611cda700a1`).
- Frozen 24h pre-event timing gate: `captured_at` AND
  `decision_committed_at` ≤ `target_date 00:00 UTC − 24h`; both
  tz-aware UTC; boundary is `≤`. Margin frozen in declaration
  (`timing_safety.safe_cutoff_offset_hours_utc = 24`).
- Per-sport-day cohort: 1 PRIMARY + 2 COHORT + ranks 4+ recorded. No
  global cap. Sport-day zero-eligible → `SHADOW_NO_SELECTION`.
- 4-ID split: `run_id` (16 hex chars from
  `sha256(version + input_digest + decision_digest + decision_committed_at)`),
  `input_digest` (canonical sorted record tuples), `decision_digest`
  (canonical selections with `run_id` cross-link stripped),
  `decision_committed_at` (ISO UTC at start of finalization).
- Staged accounting: capture-level + parse-level + decision-level
  equations asserted before manifest write.
- Atomic write: payload + manifest written via temp + `os.replace`;
  manifest last; partial runs preserved untouched. No overwrite; second
  run with same `run_id` raises `ArtifactExistsError`.
- Durability: `LOCAL_CODESPACE_ONLY_NOT_BACKED_UP`. No compact-digest
  writer, no external storage, no Git-tracked exception, no force.
- Unresolved before first real run: real-data durability, `current_only`
  sports (esoccer, afl), capture-level / parse-level staging fields,
  history-input SHA-256 manifest, conflict-reporting history loader.

**Recovery implementation status (2026-08-28, this session):**
- The four-module structure is in place: `shadow_contracts` (lowest
  layer, no upward dependency), `capture_loader` (read-only capture
  pipeline), `history_loader` (read-only history pipeline), and a
  rewritten `shadow_evaluator` (orchestration only; no duplicated R2
  or R1 logic; uses `baseline_analyzer.is_r2_eligible` and a thin
  adapter to `baseline_analyzer.r1_sort_key`).
- Golden regression test: `test_shared_feature_golden_regression`
  asserts that `build_price_free_examples` on a fixed synthetic
  fixture produces a canonical SHA-256 of
  `1a97cb81fc6521a99f1055a873975d562cae33fefce7468ceca929739f8fca0d`
  (21430 bytes, 15 examples). The test asserts digest + byte count +
  example count — three independent axes. No Git import; no
  second-copy dataset import at test runtime. The hardcoded value
  was obtained by exporting base-commit source to
  `/tmp/golden_audit/base_pkg/`, running a separate Python subprocess
  against that base package, and comparing canonical bytes against
  the current implementation. The two outputs are byte-for-byte
  identical. Full audit procedure in `/tmp/golden_audit/README.md`.
- All 512 tests pass (`python -m pytest`).
- `python -m pyflakes src/slumdog scripts tests/test_shadow_evaluator.py` is clean.
- `git diff --check` is clean.
- `python -m py_compile` is clean for the new and modified files.
- `python -m slumdog.shadow_evaluator --help` exits 0.
- End-to-end twice with identical inputs: decisions match
  (`decision_digest` identical), `input_digest` matches, `run_id`
  differs only because commit timestamp differs, neither overwrote
  the other.
- No commit, push, PR, network access, or real Forebet request made
  in this session.


- Frozen configuration verified: `config/research_baselines_v1.json` with canonical SHA-256 `666dabe7ea21e11867cf4816f4c2edcd771247646c6c9d7726c22611cda700a1`
- Pass 1: streaming integrity checks over decompressed JSONL bytes, row count matching `receipt.accounting.eligible_examples`, date coverage within P1..P4 union, fail-closed on non-finite values and prohibited keys
- Pass 2: streaming metrics computation:
  - Missingness reporting for every analyzed feature (global & per sport per period)
  - 7 pre-declared signal bucket tables with precedence rules (conceding_rate_gap, evidence_availability, h2h_underdog_win_rate, probability_gap, recent_win_rate_gap, scoring_rate_gap, underdog_probability)
  - Rule ranking and evaluation for R0 (Forebet-only, quota-forced), R1 (Always-rank, quota-forced), R2 (Conservative fixed rule, eligibility-gated, non-quota-forced)
  - Selected-day vs all-opportunity-day hit rates (no-pick days count as no hit on all-opportunity days)
  - Candidate-level and daily top-1 losing streaks (per sport; global is max; no-pick days neither increment nor reset streak)
- Safe atomic output finalization under `/tmp` (`baselines.json` with embedded config and recomputation match, `summary.md` human-readable summary)
- Real-data 6B execution pending Codespace run against the 654,011 research dataset.

## Settlement Evidence Git Policy

`data/settlement_evidence/` holds post-event Forebet captures used for
settlement grading. Policy:

- **Small receipts** (`settlement_capture_receipt.json`): commit to git.
- **Large raw bodies** (HTML/JSON capture files): keep out of git; back up
  via GitHub Actions artifacts (30-day retention) or durable object storage.
- Settlement artifacts (`settlement.json` + `.sha256`) live inside the
  prediction run directory and are committed to git (they are small JSON).

This mirrors the existing raw-capture policy: raw bytes are large and
immutable; receipts and summaries are small and valuable for
reproducibility.

## Verification

- pytest → 694 passed (622 + 41 settlement + 20 forward batch + 11 draw analysis)
- pyflakes src/slumdog scripts tests → clean on all new/changed files
- py_compile scripts/*.py src/slumdog/*.py tests/*.py → ok
- git diff --check → ok
- Frozen baseline config SHA-256 → `666dabe7ea21e11867cf4816f4c2edcd771247646c6c9d7726c22611cda700a1` MATCH
- New shadow declaration canonical SHA-256 → `dd08976a262e7a1882a4e29846612094c20447faf587c01a42608d57f4f4d597` (unchanged)
- Golden regression → `1a97cb81fc6521a99f1055a873975d562cae33fefce7468ceca929739f8fca0d` (unchanged)
- CLI: `python -m slumdog.shadow_evaluator --help` → exit 0
- CLI: `python -m slumdog.shadow_settle --help` → exit 0

## Links

- AGENTS.md — constitution
- README.md — overview
- HANDOFF.md — living handoff with full census evidence
- docs/PRICE_FREE_DATASET_CONTRACT.md — dataset contract CURRENT
- docs/FEATURE_TIMING_CONTRACT.md — timing contract CURRENT (period_values UNKNOWN)
- docs/MILESTONE1_AUDIT.md — audit REFERENCE
