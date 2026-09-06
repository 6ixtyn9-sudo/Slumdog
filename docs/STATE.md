# Slumdog State — Canonical Current Truth

**Last verified:** 2026-09-06 (UTC) — **PR #16 MERGED INTO `main` (`main` @ `3f6608b`)** / **AUTOMATED D+1 SETTLEMENT LIVE AND PROVEN IN PRODUCTION** (first real dispatch settled 2026-09-02 and 2026-09-05; artifacts on `main`) / **FORWARD SHADOW WORKFLOW LIVE** (`.github/workflows/forward_shadow.yml`, `contents: write`, `actions: read`) / **SHADOW EVIDENCE IN GIT** (runs 2026-09-05..12 + settlements + bundles) / **CONTRACT AMENDED** (owner directive 2026-09-03 "no ledgers in Codespace") / **CODESPACE DEV-ONLY** / **PRODUCTION NOT AUTHORIZED** / **SHORTLIST POLICY NOT AUTHORIZED**. 718 tests pass, 12 deselected (pre-existing `test_cloud_backup_workflow.py` failures — that workflow file was never committed to `main`).

**Branch:** `arena/01a07741-slumdog` (docs-refresh session, from `main` @ `3f6608b`); `main` is only permanent branch
**Doc canonical path:** `docs/STATE.md`
**Base commit:** `3f6608bf13f7aea8f463be0249201a9165241970` (main tip at this verification)
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
Settlement module (P1): IMPLEMENTED — shadow_settle.py + 45 focused tests
  (41 original + 4 sport-scoping, 2026-09-06)
Automated D+1 settlement (P1 continuation, 2026-09-06): MERGED (PR #16,
  merge commit c06659c) AND PROVEN IN PRODUCTION — forward_shadow.yml's
  existing daily job (dispatched
  externally via cron-job.org at 06:00 SAST/04:00 UTC; NOT a native GitHub
  `schedule:` trigger — the workflow remains workflow_dispatch-only per its
  pinned contract test) now settles any overdue prediction run BEFORE its
  forward-capture pass. A date is eligible once `target_date <= today - 1
  day` (D+1 rule, owner-confirmed 2026-09-06) and has a completed run with
  no settlement.json yet. Oldest-eligible-date-first; each date isolated
  (one date's SettlementError or unexpected exception is recorded and the
  loop continues — never blocks another date or the forward-capture pass
  that follows). Settlement capture now fetches only the sport(s) actually
  present in that run's selections + considered_pool (currently always
  just football) instead of unconditionally polling all 12 sports —
  fetch_settlement_capture() and settle_run() both gained an optional
  `sports` parameter, backward-compatible (omitted = original
  fetch-everything behavior). New: find_settleable_run,
  find_settleable_dates, _sports_in_run, run_settlement_for_date,
  run_settlement_backlog in scripts/forward_shadow_batch.py (27 new
  tests, including 4 regression tests added after independent review for
  a malformed-run-file edge case — see the note below). Workflow job
  timeout-minutes raised 15 -> 350 (owner decision
  2026-09-06, mirrors the existing pipeline.yml precedent) specifically so
  clearing a multi-date backlog, or settling additional sports once they
  go live, is never time-constrained; the workflow's own contract test
  updated to match (ceiling only, not the requirement that every job
  declare an explicit timeout). No new GitHub Actions workflow file was
  added — this rides entirely on the existing forward_shadow.yml job,
  per owner instruction. The first real dispatch (2026-09-06) settled the
  backlog eligible as of that date — 2026-09-02 (run acd78872019300ff) and
  2026-09-05 (run 4353ca88e825fd6a) — writing real settlement.json artifacts
  (schema shadow_settlement_v1) and settlement_capture_receipt.json files to
  git on main; remaining dates settle automatically as their D+1 arrives on
  each subsequent daily dispatch.
  IMPORTANT: the settlement.json this module writes lives at
  data/reports/shadow/<date>/<run_id>/settlement.json with schema
  "settlement_schema_version": "shadow_settlement_v1". This is DIFFERENT
  from the pre-existing data/reports/shadow/settlements/2026-09-02/
  acd78872019300ff.settlement.json, whose schema is
  "version": "shadow_settlement_v1_manual_binding" — that file was a
  manual/ad-hoc grading, not produced by shadow_settle.py. Once the
  automated job runs, 2026-09-02 will additionally get a real
  shadow_settle.py-produced settlement.json at its own run directory;
  the two are not the same artifact and must not be conflated.
Forward batch workflow (P3): CREATED — forward_shadow.yml + driver script + 20 contract tests
  (now 52 tests in tests/test_forward_shadow_batch.py incl. D+1 settlement
  and history-selection coverage). run_evaluator() history-selection bug
  fixed by 972b79a (2026-09-06, +5 regression tests): it was passing
  backfill manifest files (history_<sport>.json) to the evaluator's
  --history, which only accepts history_*.jsonl.gz ledgers and
  settled_history.json — the root cause of SHADOW_RUN_BLOCKED on
  2026-09-10/11/12; resolved, confirmed by a successful production
  re-dispatch (real runs + bundles for those dates now on main).
Draw-avoidance analysis (P7): CREATED — draw_analysis.py + 11 focused tests
Timing-V2 proposal (P6): DRAFTED — docs/TIMING_V2_PROPOSAL.md (not implemented)
Real shadow runs: ON MAIN (8 forward dates 2026-09-05..12; 09-05..09
  BUNDLE_VERIFIED from the codespace era, 09-10/11/12 produced by
  production dispatches after the 972b79a fix, each with manifest +
  selections + bundle artifacts on main)
Real settlement (AUTOMATED, shadow_settle.py output, on main): 2026-09-02
  (run acd78872019300ff: primary 1/1, top-3 1/3) and 2026-09-05 (run
  4353ca88e825fd6a: primary 0/1, top-3 2/3), schema shadow_settlement_v1,
  settled 2026-09-06 by the first real production dispatch. Distinct from
  the pre-existing manual/ad-hoc file at data/reports/shadow/settlements/
  2026-09-02/acd78872019300ff.settlement.json (schema
  shadow_settlement_v1_manual_binding) — see IMPORTANT note above. Sample
  size (2 primary picks) is far too small for any performance conclusion —
  see docs/REVIEW_2026-09-06_STATUS_PERFORMANCE_RECOMMENDATIONS.md §5.
Canonical config SHA-256: 666dabe7ea21e11867cf4816f4c2edcd771247646c6c9d7726c22611cda700a1 (VERIFIED)
New shadow declaration canonical SHA-256: dd08976a262e7a1882a4e29846612094c20447faf587c01a42608d57f4f4d597 (VERIFIED)
Tests: 718 passed, 12 deselected (713 at PR #16 merge + 5 history-selection
  regression tests from 972b79a; 12 deselected are the pre-existing
  test_cloud_backup_workflow.py failures — that workflow file was never
  committed to main, unrelated)
Training: FROZEN
Production: NOT AUTHORIZED
Shortlist policy: NOT AUTHORIZED
Selection width: 1 primary + rank-2/3 cohort (FROZEN; grading all ranks 1..N is authorized)
Next: remaining forward runs settle automatically as their D+1 arrives on
  each daily dispatch (2026-09-06 eligible from 09-07, 2026-09-07 from
  09-08, ..., 2026-09-12 from 09-13); let the settled backlog accumulate
  before judging any direction (2 settled primary picks is not a rate).
  Owner decision pending: the feature-usage gap documented in
  docs/REVIEW_2026-09-06_STATUS_PERFORMANCE_RECOMMENDATIONS.md §3/§6.1
  (17-field frozen decision vector vs the 60+-field
  football.py::extract_football_features module) — no change without
  explicit owner authorization (touches the frozen R2 contract; AGENTS.md
  anti-tuning rule requires pre-authorization). Training remains FROZEN.
```

## System Maturity: EARLY STAGE (~10% complete)

**Infrastructure: production-grade.** Workflow, git persistence, artifacts, settlement grading — all working.

**Prediction system: infancy.** Minimal viable instrument, not a validated predictor.

**What hasn't been done:**
- **Multi-sport:** 14 sports defined in `sports.py`, only football collected
- **Facet validation:** Football has 11 defined facets but only 17 derived stats captured; no Forebet HTML audit
- **Model training:** `MODEL_TRAINING_ALLOWED=False` — frozen heuristics, not trained models
- **Feature engineering:** Only basic statistics; no use of full facet set

**The long road ahead (owner priority order 2026-09-03):**
1. **Football facet audit** - what is Forebet actually serving? Are all 11 facets extractable?
2. **ML training** - use the 655k row historical dataset + full facet set
3. **Better predictions** - trained models > frozen heuristics
4. **Wider net** - more picks per day once quality validated
5. **One sport at a time** - football first, then expand
6. **Reporting/UI** - only after predictions worth viewing (premature now)

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

- pytest → 718 passed, 12 deselected (verified 2026-09-06 at `main` @ `3f6608b`; deselects are the pre-existing `test_cloud_backup_workflow.py` failures, unrelated)
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
- docs/REVIEW_2026-09-06_STATUS_PERFORMANCE_RECOMMENDATIONS.md — 2026-09-06 status/performance review (REVIEW/REFERENCE ONLY, not canonical)
- docs/PRICE_FREE_DATASET_CONTRACT.md — dataset contract CURRENT
- docs/FEATURE_TIMING_CONTRACT.md — timing contract CURRENT (period_values UNKNOWN)
- docs/MILESTONE1_AUDIT.md — audit REFERENCE
