# Slumdog Living Handoff

**Last updated:** 2026-09-06 (UTC) — **Milestone 7E (automated D+1 settlement) IMPLEMENTED, COMMITTED, AND PUSHED to `arena/01a0770a-slumdog` (not merged to `main`, no PR opened), NOT YET DISPATCHED ON GITHUB** — see that section below for full detail. Independently reviewed post-push; one real robustness gap found and fixed (see Milestone 7E note). Prior state: **PR #15 MERGED** (`main` @ `3b57464`, superseded by `5cd962d` on `main` as of this session) / **FORWARD WORKFLOW LIVE** / **EVIDENCE IN GIT** (27 files) / **CONTRACT AMENDED** / **CODESPACE DEV-ONLY** / **PRODUCTION NOT AUTHORIZED**. 713 tests passed, 12 deselected (pre-existing `test_cloud_backup_workflow.py` failures, unrelated), zero skips otherwise.

**Branch:** `arena/01a0770a-slumdog` (from `main` @ `5cd962d`)
**Base commit:** `5cd962d4cb97cf3c122b9f040af5c260baea71d5`
**Tests:** 713 passed, 12 deselected (`--deselect tests/test_cloud_backup_workflow.py`)
**Working tree:** committed and pushed to `origin/arena/01a0770a-slumdog` across three commits (`09f14e7` code/tests/docs — pushed by the Arena agent; `c0b006f` the `.github/workflows/forward_shadow.yml` edit — pushed by the owner from Codespace, since the Arena delivery App token cannot push changes under `.github/workflows/*`, same limitation as Milestone 7D; `dc0e51b` a post-review robustness fix, no workflow files touched, pushed directly by the Arena agent). Files touched across all three: `src/slumdog/shadow_settle.py`, `scripts/forward_shadow_batch.py`, `.github/workflows/forward_shadow.yml`, `tests/test_shadow_settle.py`, `tests/test_forward_shadow_batch.py`, `docs/STATE.md`, `HANDOFF.md`. Not merged to `main` yet. Two independent review passes completed (one found the branch initially unreachable due to session isolation before push; one found and fixed a real `_sports_in_run()` robustness gap, `dc0e51b`) — both are now resolved and the branch is considered ready for a PR pending owner sign-off.

## Cloud-Native Evidence Architecture

Forward workflow is single source of truth. Codespace is dev-only.
Dispatch: `gh workflow run forward_shadow.yml --ref main`

Settlement is now automated within this same workflow (Milestone 7E, D+1 rule) — see that section below. Cadence itself is still driven externally by cron-job.org (daily 06:00 SAST / 04:00 UTC dispatch calls), not a native GitHub `schedule:` trigger; the workflow file remains `workflow_dispatch`-only by design.

Pending: (1) first real scheduled dispatch proving the D+1 settlement automation end-to-end, (2) durable storage beyond 30d artifacts.

## System Maturity: EARLY STAGE (~10% complete)

**Infrastructure: production-grade.** Prediction system: infancy.

**Not done:** multi-sport collection (1 of 14 sports), facet validation, model training, feature engineering.

**Road ahead:** audit Forebet for 14 sports → validate facets → enable training → multi-sport rollout → model development.

**Current:** frozen heuristics + 17 stats + football only. **Future:** trained models + full facets + multi-sport.

## Milestone 7B — COMPLETE LOCALLY (shadow bundle; PR opened, NOT merged)

**Deliverable:** `src/slumdog/shadow_bundle.py` + `tests/test_shadow_bundle.py` (67 focused tests, incl. bounded-memory streaming) + `docs/MILESTONE7B_SHADOW_BUNDLE.md`. Standard-library-only module; imports no other Slumdog submodule, so verification runs on an independent machine with only the archive + receipt + Python.

**Commands:**
- Create: `python -m slumdog.shadow_bundle create --run-dir <completed-run-dir> --output-dir <explicit-dir> --root <repo-root>` → writes `slumdog-shadow-<date>-<run>.tar.gz`, `*.bundle.json`, `*.tar.gz.sha256` (all `0600`).
- Verify: `python -m slumdog.shadow_bundle verify --bundle <archive> --receipt <bundle.json>` → `BUNDLE_VERIFIED` (exit 0) or `BUNDLE_VERIFY_FAILED` (exit 2, no traceback). Never extracts to disk.

**Behavior:** packages a completed run's exact `shadow_selections.json` + `manifest.json` plus the two frozen configs, the capture receipt, every referenced sidecar/raw body, and every referenced history input; content-addressed logical `bundle/` layout with a canonical `inventory.json` and `README.txt`; deterministic tar.gz (sorted members, uid/gid 0, mode 0644, mtime 0, gzip mtime 0, regular files only) — same source bytes produce byte-identical archives (proven via two-out-dir exact-byte comparison through the CLI). Create refuses partial/blocked runs, corrupt payloads, hash mismatches, missing inputs, unsupported schema/version, out-of-root/traversal/symlink paths, and any pre-existing output (no force/overwrite); temp-sibling + atomic-rename finalization, checksum marker last, so interruptions leave no valid completion marker. **Bounded-memory streaming:** raw bodies/history are hashed and tar-streamed in 1 MiB chunks (never `read_bytes`-ed); the archive is stream-hashed and then **self-verified with the full streaming verifier before finalization**; verification hashes every member in bounded chunks over two streaming passes (never extracts, never holds an archive member whole), with explicit caps (512 MiB compressed / 256 MiB member / 1 GiB total uncompressed / 16 MiB metadata / 10k members / 512-byte paths; no `--unlimited`), and a check/use-race guard that re-hashes and re-stats each streamed source. Verifier checks archive hash, receipt/declaration authorization flags (all false), safe members (no absolute/`..`/symlink/device/FIFO/duplicate/unexpected), inventory schema + per-member hash/size, payload-vs-manifest, recomputed frozen config + declaration canonical hashes, and recomputed input/decision digests + run id. 67 focused tests.

**Authorization state:** Training NOT AUTHORIZED, Production NOT AUTHORIZED, Shortlist policy NOT AUTHORIZED, Threshold optimization NOT AUTHORIZED; no real Forebet capture and no real shadow run authorized in this milestone.

**Milestone 7B implemented/tested locally; subsequently merged into `main` via PR #12 (merge commit `41b345f`). No real shadow bundle created. No real Forebet capture. No real shadow run. Production not authorized. Shortlist policy not authorized.**

## Milestone 7D — Cloud-Only Shadow Bundle Backup (IMPLEMENTED LOCALLY / PR opened, NOT merged / WORKFLOW NOT DISPATCHED)

**Owner decision (2026-08-30):** the slow local computer is OUT of the backup loop. Second copies go to GitHub Actions artifact storage, verified from a second short-lived cloud runner. Actions artifacts are NOT permanent storage; 30-day retention configured explicitly; migrate to durable object storage or a private release asset if the experiment continues (separate approval).

**Deliverables:** `.github/workflows/shadow_bundle_cloud_backup.yml` (committed on PR #13 by the owner from the Codespace — the Arena delivery App token cannot push `.github/workflows/*`; historical note only) + `scripts/synthetic_shadow_fixture.py` + `tests/test_synthetic_shadow_fixture.py` (9 tests) + `tests/test_cloud_backup_workflow.py` (12 tests: the 10 original contract tests plus owner-added regressions requiring the workflow file to exist, compiling every embedded Python heredoc, and asserting the compact verification-receipt upload) + `docs/MILESTONE7D_CLOUD_BACKUP.md`. No change to the shadow evaluator, R2, ranking, configs, or production code.

**Workflow contract:** manual `workflow_dispatch` ONLY; `permissions: contents: read`; concurrency group `shadow-bundle-cloud-backup` (queued, never simultaneous, never cancels in-flight); 15-minute timeout per job; NO caches (no cache action, setup-python cache off, `pip install --no-cache-dir .`); all four actions pinned to immutable full commit SHAs (checkout v7.0.1 `3d3c42e5…`, setup-python v7.0.0 `5fda3b95…`, upload-artifact v7.0.1 `043fb46d…`, download-artifact v8.0.1 `3e5f45b2…`); retention-days 30 explicit on every artifact; `if-no-files-found: error`; logs carry filenames/hashes/ids only.

**Job 1 `bundle-create`:** build the entirely synthetic completed run under `$RUNNER_TEMP` (generator below) → `python -m slumdog.shadow_bundle create` → local verify requiring `BUNDLE_VERIFIED` → `sha256sum -c` marker check → upload the exact three-file triplet as ONE artifact named `shadow-bundle-<target_date>-<run_id>-<sha256-prefix-8>`. Exposes artifact name/target date/run id/archive SHA-256 as job outputs.

**Job 2 `bundle-verify` (fresh runner, needs only the artifact):** download-artifact → recompute archive SHA-256, require exact match with job 1 → `python -m slumdog.shadow_bundle verify` requiring `BUNDLE_VERIFIED` → marker re-check → compact JSON receipt `slumdog_cloud_bundle_verification_v1` (workflow run id/attempt, repository, artifact name, target date, run id, archive SHA-256, `bundle_verified: true`, creation job `bundle-create`, verification job from `GITHUB_JOB`, UTC timestamp, retention days, authorization flags all false) appended to the job summary AND uploaded as a separate artifact. Receipts are never auto-committed.

**Synthetic fixture generator (`scripts/synthetic_shadow_fixture.py`):** deterministic and network-free; verifies both frozen config canonical hashes before anything else; copies ONLY those two configs into the synthetic root; synthetic participants (Synthetic Alpha/Beta, Gamma/Delta, Epsilon/Zeta — 3 football events, 42 synthetic settled history rows = 6+6+2 per pairing, direct-route raw-JSON body, gzip mtime=0); injected decision clock `2026-08-30T12:00:00Z` vs cutoff `2026-09-01T00:00:00Z`; drives the REAL evaluator; fails closed unless `SHADOW_SELECTIONS_EMITTED` with exactly 1 primary + 2 cohort; refuses existing roots; never writes inside the repository. Known property (documented in tests): `input_digest`/`run_id` commit to absolute provenance paths, so they are root-dependent BY DESIGN; `decision_digest` and history bytes are root-independent; bundle determinism from a fixed root is proven by the M7B suite.

**Validation (Arena sandbox, 2026-08-30):** full suite 622 passed (601 on `main` + 9 fixture + 12 workflow-contract, ZERO skips); workflow tests 12/12 with `-rs` (no skip reasons); fixture tests 9/9; `py_compile` all files OK; `git diff --check` clean; pyflakes clean on every new/changed file (13 pre-existing warnings in old test files untouched); workflow YAML parsed with PyYAML. Owner commits `ba7d554`..`c35a8d1` added the workflow file and the required-presence / embedded-heredoc / receipt-upload regression tests.

**Honest status:** implemented YES (committed on PR #13) / contract tests 12/12 with zero skips / syntax validated YES / executed on GitHub NO / artifact uploaded NO / downloaded-in-separate-job NO / independent verification passed NO. **Do not claim cloud backup works until a manual dispatch succeeds end-to-end.** Dispatch: `gh workflow run shadow_bundle_cloud_backup.yml --ref main` (after merge) and confirm both jobs green; the receipt artifact is the evidence.

**Authorization state:** Training NOT AUTHORIZED, Production NOT AUTHORIZED, Shortlist policy NOT AUTHORIZED, Threshold optimization NOT AUTHORIZED; the workflow performs no capture, no real run, no production activity, and uses no credentials beyond the automatic `GITHUB_TOKEN`.


## Milestone 7E — Automated D+1 Settlement (IMPLEMENTED LOCALLY / NOT DISPATCHED / NO NEW WORKFLOW FILE, 2026-09-06)

**Owner decision (2026-09-06):** settlement of forward shadow picks must run without manual intervention. No new GitHub Actions workflow file — automation rides entirely on the existing `forward_shadow.yml` job, which is already dispatched daily by an external cron-job.org trigger at 06:00 SAST / 04:00 UTC (confirmed via `gh run list`: 4 prior runs landing ~24h apart). The workflow's trigger stays `workflow_dispatch`-only; `test_manual_dispatch_only` is untouched and green — cron-job.org calling the dispatch API is not the same as adding a native `schedule:` trigger, and the owner did not ask for one.

**Timing rule:** D+1 after the target date — a date becomes settlement-eligible once `target_date <= as_of - 1 day`. Verified exactly via direct testing: `as_of=2026-09-02 -> []`; `as_of=2026-09-03 -> [2026-09-02]`; `as_of=2026-09-06` adds `2026-09-05`; `as_of=2026-09-10` returns the full 6-date backlog, oldest first.

**Deliverables:**
- `src/slumdog/shadow_settle.py`: `fetch_settlement_capture()` and `settle_run()` both gained an optional `sports: list[str] | None` parameter (backward-compatible; omitted = original behavior of fetching all 12 non-`current_only` sports). When given, results are filtered to the `SPORTS` registry order and unknown/`current_only` names are silently dropped. New `--sports` CLI flag (comma-separated). This exists because every real run to date only contains `football` selections, so unconditionally polling all 12 sports per settlement (each with a 62s pause) wastes ~11 minutes per date for no benefit today.
- `scripts/forward_shadow_batch.py`: new orchestration — `find_settleable_run()` (one unsettled completed run per date dir), `find_settleable_dates()` (D+1 cutoff, oldest-first, skips non-date sibling dirs like `settlements/`), `_sports_in_run()` (derives sport set from a run's `selections[]` and manifest `considered_pool[]`, used to scope the settlement fetch via the new `sports` param), `run_settlement_for_date()` (never raises — returns `SETTLED` / `SETTLEMENT_FAILED` / `NO_SPORTS_RESOLVED`, so one bad date can't take down another or the forward-capture pass), `run_settlement_backlog()` (loops oldest-first, isolates failures, respects `--dry-run`). `main()` now runs the settlement backlog pass **before** the forward-capture loop; new `--skip-settlement` CLI flag; `forward_batch_receipt.json` gained `settlement_backlog` (per-date detail) and `settlement_backlog_total/settled/failed` summary fields (this receipt itself is NOT added to the workflow's git-persist allowlist — same as before this change).
- `.github/workflows/forward_shadow.yml`: two changes, both scoped narrowly:
  1. **Real pre-existing bug fixed:** the git-persist step's `find ... -name '*.settlement.json'` glob never matched `write_settlement_artifact()`'s actual output filename (bare `settlement.json`, no prefix before the dot) — settlement artifacts could never have been committed, even by a human running this workflow manually, until this fix. Added explicit `-name 'settlement.json'` / `-name 'settlement.json.sha256'` patterns, and added staging of `data/settlement_evidence/**/settlement_capture_receipt.json` (small receipts are supposed to be git-tracked per `.gitignore`/STATE.md policy but were never actually staged by any pattern in this workflow before now).
  2. **`timeout-minutes` raised 15 -> 350** (owner decision 2026-09-06, mirrors the existing `pipeline.yml` precedent — all three of its jobs already use 350, just under GitHub's ~360-minute practical ceiling for hosted runners): a multi-date D+1 backlog plus the existing forward-capture step needs headroom, and the owner explicitly wants time removed as a constraint since more sports will be settled in the future. The workflow's own pinned contract test (`test_timeout_on_every_job` in `tests/test_forward_shadow_batch.py`) was updated to assert `<= 350` instead of `<= 15` — this is a deliberate, owner-authorized change to a previously-pinned test value, not a workaround.
- Tests: `tests/test_shadow_settle.py` gained `TestFetchSettlementCaptureSportScoping` (4 tests: default-fetches-all, subset-only, drops-unknown/`current_only`, empty-list-fetches-nothing) — 45 total, all passing. `tests/test_forward_shadow_batch.py` gained `TestFindSettleableRun`, `TestFindSettleableDates`, `TestSportsInRun`, `TestRunSettlementForDate`, `TestRunSettlementBacklog` (27 new tests covering the D+1 boundary, ordering, non-date siblings, already-settled exclusion, failure isolation, dry-run safety, and — added after independent review, see note below — malformed-run-file robustness) — 47 total in that file, all passing. Full suite: 713 passed, 12 deselected (the pre-existing `test_cloud_backup_workflow.py` failures from Milestone 7D, unrelated to this change — that workflow file was never committed to `main`).

**Independent review fix (2026-09-06, post-push):** a review of the pushed commit found that `_sports_in_run()` only caught `(OSError, json.JSONDecodeError)` around its two file reads, so a `shadow_selections.json`/`manifest.json` that parses as valid JSON but has the wrong top-level shape (e.g. a bare list instead of a dict, or a non-dict entry inside `selections`/`considered_pool`) would raise `AttributeError`/`TypeError` uncaught — violating `run_settlement_for_date`'s documented "never raises" contract and, worse, would have propagated out of `run_settlement_backlog()` and aborted `main()` before the forward-capture pass ran. Fixed by widening both except clauses to `(OSError, json.JSONDecodeError, AttributeError, TypeError)`, with 4 new regression tests (`TestSportsInRun`: list-not-dict, non-dict selection entry, non-dict manifest; `TestRunSettlementForDate`: end-to-end malformed-file case resolves to `NO_SPORTS_RESOLVED` rather than raising). Real-world likelihood was assessed as low (run files are always dicts written by the frozen evaluator with atomic writes) but the fix was one line per except clause, so it was applied rather than left as a documented risk.

**IMPORTANT — do not conflate two different settlement artifacts:** the pre-existing `data/reports/shadow/settlements/2026-09-02/acd78872019300ff.settlement.json` (schema `"version": "shadow_settlement_v1_manual_binding"`) was a **manual/ad-hoc grading**, not produced by `shadow_settle.py` (confirmed: that string appears nowhere in the module; its path convention — a shared `settlements/<date>/<run_id>.settlement.json` — doesn't match what `write_settlement_artifact()` actually writes). The automation in this milestone writes a fresh, correctly-schemed settlement (`"settlement_schema_version": "shadow_settlement_v1"`) to `data/reports/shadow/<date>/<run_id>/settlement.json` the first time it runs for 2026-09-02 — this is intended, not a duplicate-avoidance bug.

**Honest status:** implemented YES / unit-tested YES (68 new/modified tests, full suite green) / manual dry-run smoke-tested YES (`scripts/forward_shadow_batch.py --dry-run --pause-seconds 1 --dates 2` confirmed the settlement pass runs before the forward loop and the receipt fields populate correctly) / executed on GitHub NO / real automated settlement of the 2026-09-02..09 backlog NO. **Do not claim the automation "works" end-to-end until the next scheduled `forward_shadow.yml` dispatch actually clears the backlog and a real `settlement.json` (schema `shadow_settlement_v1`) appears under `data/reports/shadow/2026-09-02/<run_id>/`.** Not yet committed/pushed/PR'd — left on the branch for review per owner instruction ("measure twice, cut once... I will then rope in another agent that will work hand in hand with you").

**Authorization state:** Training NOT AUTHORIZED, Production NOT AUTHORIZED, Shortlist policy NOT AUTHORIZED, Threshold optimization NOT AUTHORIZED; this milestone touches only settlement scheduling/scoping and workflow timeout/staging config — no change to the evaluator, ranking, features, or any credential surface beyond the automatic `GITHUB_TOKEN` already used by the workflow.

## Product Invariants (from AGENTS.md)

- UNDERDOG_WIN outright only, never selects draws, draw=failed in draw-capable
- Odds optional metadata only — not required, not feature, not gate, missing must not lower confidence
- Never force pick; NO_STRONG_UNDERDOG is daily result not candidate state; never promise profit/guaranteed wins
- Training frozen until user approves dataset/target/timing/validation
- Main only permanent branch; Arena delivery only; never force-push main
- Assessment vs selection separated, odds outside core assessment, no ranking by model prob before approval

## Milestone 0 — COMPLETE

User approved 2026-08-24: move STATE.md to docs/STATE.md, add AGENTS.md, canonical read order, classifying docs, retaining forensic as historical, main only permanent branch, locking price-free UNDERDOG_WIN mission, leaving training frozen. No deletions authorized.

## Milestone 1 Audit — COMPLETE (Now REFERENCE)

**Deliverable:** `docs/MILESTONE1_AUDIT.md` (885 lines + corrections) — read-only audit, no code changes. Now REFERENCE after gaps resolved for identity/label/timing.

Central problem exposed: system can operate without odds but underdog identity, scoring, feature vectors, thresholds, research approval still materially odds-first. 10 gaps documented, 8 refinements approved.

## Milestone 2 — COMPLETE (Including 2E Hardening)

**Files:**
- `src/slumdog/underdog.py` (~718 lines, commit fee5d78) — pure Forebet identity `identify_forebet_underdog()`, historical label with hardening: `UnderdogLabelResult` adds identity_ineligibility_reason/sport/draw_possible, private `_label_from_indices` raw helper, public `label_underdog_outcome(sport, identity: ForebetUnderdogIdentity, winner_index, disposition, source_conflict)` derives fav/dog/eligible/reason from identity, derives draw_possible from SPORTS[sport].draw_possible, unknown sport→UNKNOWN_SPORT exclusion, preserves exact EQUAL_PROBABILITY/MISSING_PROBABILITY/NON_FINITE/OUT_OF_RANGE reasons, caller cannot reverse fav/dog or override sport semantics
- `tests/test_price_free.py` (40 tests after 2E) — identity 10, labels via identity 11, hardening 10, contracts 9
- Full suite 232 passed after 2E, training frozen, compatibility boundary explicit

## Milestone 3 — COMPLETE (Feature Timing Contract)

**Deliverable:** `docs/FEATURE_TIMING_CONTRACT.md` (CURRENT as governing ALLOWED, Milestone 3 COMPLETE) — doc-only audit, no code change, training FROZEN. period_values 10-point investigation UNKNOWN PROHIBITED, full inventory, missingness audit.

## Milestone 4 — COMPLETE (Architecture + 4E Hardening + 4F Conflict Census)

**Core principle:** Evaluate every eligible settled event, not only legacy Robber candidates. Flow: settled event → Forebet participant probabilities → price-free favorite/underdog identity → prior-only pre-event evidence → price-free feature snapshot → UNDERDOG_WIN label. Never through legacy odds-first candidate, displayed odds, market implied probability, price availability, legacy Robber score, ROI gate.

### Files Changed (4F)

- **Hardened module:** `src/slumdog/dataset.py` (~1400 lines, 4F)
  - `FEATURE_CONTRACT_VERSION = "price-free-v1-minimal-2026-08-24"`, `LABEL_CONTRACT_VERSION = "price-free-v1"`
  - `ALLOWED_FEATURES` = identity 5 + prior 12 = 17 minimal safe set
  - `PROHIBITED_KEYS` = odds_1, odds_2, price, overround, fair_market_probability, value_edge, ROI, legacy_robber_score, period_values, score_1/2, etc.
  - `PriceFreeUnderdogExample` frozen, `PriceFreeDatasetReceipt` with raw vs canonical + conflict census fields: conflicting_composite_keys, conflicting_rows, conflicts_by_sport, conflicts_by_field, conflicts_with_valid_raw_sha256, conflicts_without_valid_raw_sha256
  - `_validate_settled_dict` strict: winner_index int 0/1/2 bool rejected, disposition vocabulary SUPPORTED_DISPOSITIONS = {SETTLED, SETTLED_CUP, SETTLED_DRAW, VOID, NO_CONTEST} from settlement.py, unknown dispositions schema-excluded
  - Deterministic provenance merge: identical collapses, missing vs present preserves present, different non-empty hashes/URLs fail loudly, stable under reversed order
  - `_canonical_event_repr` versioned, odds excluded deliberately, source-conflict limitation documented (SettledEvent does not represent source conflict, not in digest)
  - `build_price_free_examples` composite key (sport,event_id,event_date), exact duplicate collapse vs conflict fail loudly
  - **NEW:** `ValidEventWithSource` (event + audit-only source_file, source_location), `ConflictGroup`, `_compare_events_for_conflict`, `_classify_conflict`, `build_conflict_census` — deterministic grouping, classification DOMAIN/OUTCOME/PROBABILITY/DISPOSITION/PROVENANCE/MULTIPLE, continues after first conflict, compact report no full serialization

- **Audit module:** `src/slumdog/dataset_audit.py` (4F)
  - Entry points: normal `python -m slumdog.dataset_audit --root data --receipt /tmp/.../receipt.json --sample /tmp/.../examples_sample.json --sample-size 5` and census `... --conflict-report /tmp/.../conflicts.json`
  - Source-tracked loaders: `line:N` for jsonl.gz, `index:N` for json
  - Census mode: status DATA_CONFLICTS, exit 1, receipt emitted with conflict totals, conflict report compact under /tmp, examples NOT emitted
  - Normal mode still fails loudly on conflicting duplicates
  - Safe diagnostic: inspects `data/reports/history_hockey.json` container type, top-level keys (dict with daily_receipts, dates_completed, etc, size 426204)
  - Writes only under /tmp, no network, no ledger modification

- **Tests:**
  - `tests/test_price_free_dataset.py` (30)
  - `tests/test_dataset_hardening.py` (34)
  - `tests/test_dataset_audit.py` (9)
  - `tests/test_dataset_final_integrity.py` (18)
  - `tests/test_dataset_conflict_census.py` (14) — census continues after first conflict, multiple conflicts grouped by sport, OUTCOME_CONFLICT for score differences, PROBABILITY/DOMAIN/DISPOSITION/MULTIPLE, source location preserved, no full serialization, normal mode still fails, census emits no examples, deterministic under reordering, JSON only under /tmp, provenance counts
  - Full suite **337 passed**

## REAL-DATA CENSUS — Codespace retained ledgers (2026-08-24)

Executed on `arena/01a033af-slumdog` HEAD `4b1546f` in data-bearing Codespace with 11 ledger files.

Command:
```
rm -rf /tmp/slumdog_price_free && mkdir -p /tmp/slumdog_price_free
python -m slumdog.dataset_audit --root data --conflict-report /tmp/slumdog_price_free/conflicts.json --receipt /tmp/slumdog_price_free/receipt.json --sample /tmp/slumdog_price_free/examples_sample.json --sample-size 5
# exit 1 expected
```

Summary (exact from receipt):
```
Status: DATA_CONFLICTS
Files found: 11
Files empty: 0
Files unreadable: 0
Raw input rows: 655,394
Schema excluded: 6
Schema reason: SCHEMA_MISSING_PARTICIPANT_1 = 6
Valid loaded rows: 655,388
Exact duplicates collapsed: 279
Canonical non-conflicting rows: 655,107
Eligible examples before conflict gate: 654,029
Builder exclusions: 1,078
Conflicting composite keys: 1
Conflicting rows: 2
Conflict sport: hockey = 1
Conflict fields: score_1, score_2, period_scores_1, period_scores_2
Conflict valid raw SHA-256: 0
Conflict missing raw SHA-256: 1
Eligible provenance present: 0
Eligible provenance missing: 654,029
Canonical date range: 2023-02-12 through 2026-08-21
Examples emitted: no
Training: frozen
Production: unauthorized
```

Accounting balances:
```
655,394 = 6 + 655,388
655,388 = 279 + 655,107 + 2
655,107 = 654,029 + 1,078
```

Known conflict:
```
Composite key: hockey / hockey:278977 / 2023-08-20
Participants: Netherlands W / Denmark W
Variant A: 1–6; periods 0,1,0 / 2,1,3
Variant B: 0–4; periods 0,0,0 / 1,2,1
File: data/reports/history_hockey.jsonl.gz SHA 36eec2b1493aca1b52f92d843485794255db02d246e9d5b6ced4a18b4c371542 lines 62 and 67
Classification: OUTCOME_CONFLICT
Both records lack raw_sha256 and captured_at.
No variant has been selected, rewritten, deleted, or quarantined.
```

Diagnostic:
```
DIAGNOSTIC history_hockey.json: container dict, top-level keys ['daily_receipts', 'dates_completed', 'dates_requested', 'end', 'failures', 'history_file', 'priced_rows', 'settled_rows', 'sport', 'start', 'void_rows'], file data/reports/history_hockey.json size 426204
```

Temporary artifacts (not committed):
```
/tmp/slumdog_price_free/receipt.json
/tmp/slumdog_price_free/conflicts.json
```

## Verification Receipt (Milestones 0–4F)

- `python -m pytest -q` → 337 passed
- `python -m pyflakes scripts src/slumdog tests` → clean
- `python3 -m py_compile scripts/*.py src/slumdog/*.py tests/*.py` → ok
- `git diff --check` → ok
- `git status --short` → clean after commit
- Real-data census: 655,394 raw, 6 schema excluded, 655,388 valid, 279 exact duplicates collapsed, 655,107 canonical non-conflicting, 654,029 eligible before gate, 1,078 builder exclusions, 1 conflicting key (hockey outcome), 2 conflicting rows, 0 provenance present, 654,029 missing
- Census behavior: deterministic, conflict report emitted, no examples emitted, all readable rows accounted, conflicts reported not silently removed

## Training / Production Status

- **Training:** FROZEN (`MODEL_TRAINING_ALLOWED=False`)
- **Production:** NOT AUTHORIZED
- **Research dataset measurement (Milestone 6A): AUTHORIZED** — dataset construction, receipt measurement, non-model descriptive statistics, research-only artifacts. Legacy provenance-free history may be used for these measurements only.
- **Not authorized (6A boundary):** fitted models, threshold optimization, calibrated probabilities, ranking, daily shortlist, shadow picks, production, wagering
- **Historical dataset generation (strict):** FAIL-CLOSED (correctly refuses corrupted ledger)
- **Real-data research readiness:** run research mode in Codespace to regenerate accounting; 1 hockey conflict key + 6 schema exclusions are excluded explicitly and counted
- **period_values** remains UNKNOWN and PROHIBITED per FEATURE_TIMING_CONTRACT.md
- **Source-conflict visibility limitation:** SettledEvent contract does not represent source conflict; not in digest; builder assumes no conflict; receipt excluded_source_conflict=0 for current schemas; documented in _canonical_event_repr

## Milestone 6A — Research Dataset Readiness (v2 implementation — COMPLETE, real-data verified)

The original 6A implementation was found not to scale (it materialized all examples in memory and rebuilt full history per example). The corrected implementation below is bounded-memory and linear-time. Full contract details live in docs/PRICE_FREE_DATASET_CONTRACT.md (6A section) — not repeated here.

- **Modules:** `src/slumdog/research_dataset.py` (orchestration, streaming emitter, bounded views, safe finalization) + new `src/slumdog/research_builder.py` (v2 eligibility, duplicate normalization, incremental builder core). Both research-only; neither is imported by production pipeline modules (pipeline, training, backfill, depth_sweep, research, forebet, cli).
- **`build_price_free_examples` (strict) is unchanged** and serves as the byte-level reference for equivalence tests.
- **v2 feature contract:** `price-free-v2-incremental-valid-history` on every example/sample/receipt; label contract unchanged. The only semantic change vs legacy is history membership: `research_history_eligible` (coherent disposition/winner, known sport, distinct participants) replaces the implicit legacy `HistoryIndex` filter — intentional divergences: unknown-sport rows, `NO_CONTEST` aliases, and incoherent rows (e.g. `SETTLED_CUP` winner-0) no longer feed history.
- **Data flow:** raw → schema validation → lightweight census (`census_conflicts_only`, O(rows)) → whole-key conflict exclusion → content/provenance-separated duplicate normalization (content equality, then deterministic provenance representative; no input-order selection) → incremental builder (one sport, one event-date batch at a time, same-date isolation, bounded participant/H2H state) → bounded readiness aggregates → streaming artifacts.
- **Bounded by construction:** examples stream to the temp gzip as produced (never held as a list); sample = first `sample_size` emitted; ready receipts carry `research_ready: true`; internal inconsistencies produce a diagnostic receipt only (`RESEARCH_DATASET_NOT_READY`, never coexisting with final artifacts).
- **Safe finalization:** no pre-existing output is ever overwritten (no `--force`); temps validated then renamed examples → sample → receipt last; failures before the receipt rename remove this run's temps and finals.
- **Guards unchanged:** explicit `--research-exclude-conflicts` opt-in; `/tmp`-only examples path; no combination with `--conflict-report`.
- **Tests:** `tests/test_dataset_research_mode.py` (21: flags, accounting, determinism, ledger immutability, pipeline behavior — the brittle import-state assertion was removed per review) + `tests/test_research_incremental_builder.py` (36: strict-equivalence incl. same-date isolation, reordering, multi-sport ordering, provenance duplicates; intentional divergences; exact-byte v2 input digest; emitter/sample/digest; mid-stream and mid-commit failure leaving no finals; no-preexist refusal; diagnostic receipt; one-shot iterator; empty ledger; eligibility matrix; representative tie-breaks; outcome-subtype aggregation incl. per-sport invariants; self-pair no-emit/no-history/explicit reason; `builder_exclusion_reasons` receipt exposure + serialization stability). Suite total: 396.
- **Final real-data verification (Codespace, head `3898103`, 2026-08-26):** audit exit 0, `RESEARCH_DATASET_READY_WITH_LIMITATIONS`, elapsed 192.61 s, peak RSS 2,284.2 MiB. Accounting: raw 655,394 → schema exclusions 6 → valid 655,388 → exact duplicates 279 + conflicting rows 2 (1 conflicting key) + canonical 655,107 → eligible 654,011 + builder exclusions 1,096. Exclusion reasons (fully explicit): equal probability 180, out-of-range probability 7, self-pair 18, unexpected two-way draw 588, void 303. Outcomes: underdog wins 191,238 / favorite wins 380,212 / draw negatives 82,561; positive rate 0.29240792586057424. Provenance present/missing/invalid = 0 / 654,011 / 0; 17-field feature missingness; price independence passed; global + per-sport outcome accounting passed; exclusion accounting passed; input digest `30cb96ffd2ee8193ecf0786df1b6a45aca3a26a8c8457d85c0135c512685c1c7`; examples digest `ac84325d281c1808765fbcb18028efb193dbbdd2affc806ba459bb9d8a09a228` (deterministic, unchanged by the receipt-only correction); compressed artifact 45,439,763 bytes; source ledger hashes unchanged; training FROZEN; production NOT AUTHORIZED.
- **Docs updated:** docs/PRICE_FREE_DATASET_CONTRACT.md (6A section), docs/STATE.md, HANDOFF.md.

## Milestone 6B — Non-Trained Baseline Analyzer (COMPLETE, 426 tests passed, Real Run Verified)

- **Frozen Pre-declaration:** `config/research_baselines_v1.json` verified against canonical SHA-256 `666dabe7ea21e11867cf4816f4c2edcd771247646c6c9d7726c22611cda700a1`. Anti-tuning guarantees preserved: tuning periods empty, result-driven amendments prohibited, shortlist policy not authorized, training frozen.
- **Two-Pass Architecture:**
  - `src/slumdog/baseline_analyzer.py` (implementation) and `src/slumdog/research_baselines.py` (re-export/CLI shim only, zero logic).
  - **Pass 1 (Streaming integrity):** SHA-256 over decompressed JSONL bytes verified against `receipt.examples_digest`; row count verified against `receipt.accounting.eligible_examples`; every row's `event_date` must fall within P1..P4 union; non-finite floats (NaN/Inf/-Inf) fail closed; prohibited keys fail closed; any failure exits nonzero immediately with no outputs written.
  - **Pass 2 (Streaming analysis & aggregation):**
    - Missingness reporting for every analyzed feature (global and per sport per period).
    - 7 pre-declared signal bucket tables with exact precedence rules: `conceding_rate_gap`, `evidence_availability`, `h2h_underdog_win_rate`, `probability_gap`, `recent_win_rate_gap`, `scoring_rate_gap`, `underdog_probability`.
    - Empty buckets reported as empty, never omitted.
    - Insufficient threshold = 30 examples.
    - Rule comparators: R0 (Forebet-only, quota-forced), R1 (Always-rank, quota-forced), R2 (Conservative fixed rule, eligibility-gated, non-quota-forced).
    - Selected-day vs all-opportunity-day hit rates (no-pick days count as no hit in all-opportunity rates).
    - Losing streaks: candidate-level within sport (global = max across sports; sports never concatenated); daily top-1 within sport (global = max across sports; no-pick days neither increment nor reset streak).
  - **Safe Atomic Outputs:** `/tmp/slumdog_6b/baselines.json` (embedded config with recomputed hash match) and `/tmp/slumdog_6b/summary.md` (Markdown summary tables). Atomic write via `.tmp-{uuid}` rename.
- **Reporting Corrections (Verified):**
  1. Top-level `shortlist_policy_authorized: false` sourced and validated from embedded config.
  2. Markdown comparator table renders actual formatted percentages (`46.63%`, `80.05%`, `9.8%`) while JSON metric values remain decimal rates.
- **Real-Data Execution Evidence (Codespace retained dataset):**
  - Rows analyzed: 654,011
  - Input examples digest: `ac84325d281c1808765fbcb18028efb193dbbdd2affc806ba459bb9d8a09a228`
  - Frozen config hash: `666dabe7ea21e11867cf4816f4c2edcd771247646c6c9d7726c22611cda700a1` (verified)
  - Ledger hashes: unchanged
  - Analyzer exit: 0
  - Period totals balance:
    - P1: 24,203
    - P2: 230,129
    - P3: 250,700
    - P4: 148,979
    - Total: 654,011
- **Descriptive P4 Results (Non-Trained Baselines):**
  - Comparators:
    - R0 Forebet-only top-1: 46.63%
    - R1 evidence-order top-1: 42.84% (R1 underperformed R0 in every period; evidence order alone does not improve on Forebet probability)
    - R2 conservative selected-day top-1: 46.10% (with 9.8% no-pick days; does not beat R0)
  - Strong descriptive signals:
    - Underdog probability 0.40–0.50: precision 44.07%, lift 1.4802
    - Probability gap 0–0.05: precision 39.44%, lift 1.3247
    - Recent win-rate gap >= +0.30: precision 38.17%, lift 1.2822
    - Scoring-rate gap >= +2: precision 42.22%, lift 1.4180
    - Conceding-rate gap < -2: precision 40.81%, lift 1.3709
    - H2H underdog win rate (0.50, 1): precision 38.09%, lift 1.2794
  - Weak descriptive signals:
    - Evidence quantity alone is weak: 3–4 components precision 33.32% (lift 1.1190); 5–6 components precision 28.91% (lift 0.9709)
- **Policy Invariants:**
  - Training: FROZEN (`MODEL_TRAINING_ALLOWED=False`).
  - Production: NOT AUTHORIZED.
  - Shortlist policy: NOT AUTHORIZED.
  - Do not derive or implement a shortlist threshold from these results. Next milestone requires separate design discussion and explicit authorization.
- **Tests:** `tests/test_baseline_analyzer.py` (30 focused tests). Total suite: 426 passed.

## PR State

- **Active branch:** `arena/01a0512f-slumdog` — Milestone 7D cloud-only bundle backup (base `main` @ `41b345f`).
- **Pull request:** #13 opened into `main` (NOT merged) — "Add cloud-only shadow bundle backup workflow (Milestone 7D)". Contents: the committed workflow file (owner push `ba7d554` from the Codespace, after the Arena delivery App was refused `.github/workflows/*` writes for lacking the `workflows` permission — resolved, historical note) + synthetic fixture generator + 9 fixture tests + 12 workflow-contract tests passing with ZERO skips (missing workflow file now FAILS the suite) + docs (STATE/HANDOFF/docs README/MILESTONE7D).
- **Merge approves only:** the manual-dispatch synthetic-bundle cloud backup procedure (fixture generator, workflow, contract tests, docs).
- **Merge does NOT approve:** model training, threshold optimization, production publication, shortlist policy activation, real Forebet capture, real shadow runs, permanent-storage claims, auto-committed receipts.
- **Next:** (1) merge PR #13; (2) owner manually dispatches the workflow once (`gh workflow run shadow_bundle_cloud_backup.yml --ref main`) and confirms both jobs green (receipt artifact) — only then is the cloud second-copy path proven; (3) schedule the first real future-date capture/run.

- **Active branch:** `arena/01a04198-slumdog` — head `8977cab` (and handoff commit).
- **Pull Request:** #10 https://github.com/6ixtyn9-sudo/Slumdog/pull/10 — "Implement two-pass non-trained baseline analyzer (Milestone 6B)" (OPEN, mergeable, against `main`).
- **Merge approves only:** two-pass non-trained baseline analyzer, anti-tuning verification, descriptive baseline evaluation reporting.
- **Merge does NOT approve:** model training, threshold optimization, calibrated probabilities, ranking for production, shortlist policy activation, shadow picks, production integration, wagering.

- **Active branch:** `arena/01a03e7a-slumdog` — v2 implementation, final tested head `3898103` (base `main` @ `efb4c90`, built on the preserved PR #8 commits via `bc5dd3c`). Replacement PR opened from here against `main`.
- **Replacement PR:** #9 https://github.com/6ixtyn9-sudo/Slumdog/pull/9 — "Add bounded research-only price-free dataset generation" (OPEN, mergeable/CLEAN, against `main` from `arena/01a03e7a-slumdog`) — supersedes PR #8.
- **PR #8 (superseded):** https://github.com/6ixtyn9-sudo/Slumdog/pull/8 — closed **not-merged** as superseded once the replacement PR was verified. Original `arena/01a03dc4-slumdog` branch is NOT deleted from this Arena session.
- **Original #8 contents:** integrity-evidence docs (hockey mechanism, UNKNOWN origin layers, corrected provenance policy, docs index) + original Milestone 6A research mode (replaced by the v2 incremental builder).
- **Merge approves only:** historical-integrity documentation; research-only dataset construction, receipt measurement, descriptive statistics, research artifact generation
- **Merge does NOT approve:** model training, threshold optimization, calibrated probabilities, ranking, candidate shortlists, shadow picks, production integration, daily selections, legacy Robber removal, provenance fabrication

## Evidence Language Compliance

- Verified from code: file paths, function names, disposition vocabulary SETTLED/SETTLED_CUP/SETTLED_DRAW/VOID/NO_CONTEST, winner_index strict, provenance merge deterministic, composite key (sport,event_id,event_date), conflict classification, receipt fields, accounting equations
- Verified from executed census (historical, 2026-08-24): 11 files, 655,394 raw, 6 schema excluded SCHEMA_MISSING_PARTICIPANT_1, 279 exact duplicates, 1 conflicting key hockey 278977 OUTCOME_CONFLICT, 2 conflicting rows, 0 valid SHA, 1 missing SHA, 654,029 eligible (legacy v1 membership), 1,078 builder exclusions, 0 provenance present
- Verified from final real-data run (2026-08-26, head 3898103): audit exit 0, 192.61 s, peak RSS 2,284.2 MiB, eligible 654,011, builder exclusions 1,096 (equal-probability 180, out-of-range 7, self-pair 18, unexpected two-way draw 588, void 303), underdog wins 191,238 / favorite wins 380,212 / draw negatives 82,561, positive rate 0.29240792586057424, provenance 0/654,011/0, input digest 30cb96ff…c1c7, examples digest ac84325d…a228, artifact 45,439,763 bytes, ledger hashes unchanged
- Verified from tests: 396 passed, pyflakes clean, py_compile ok, diff-check ok
- Unresolved: hockey 278977 conflicting scores — retained both, no selection
- Parked: period_values UNKNOWN PROHIBITED, detail facets UNKNOWN/PARKED, American football odds probe, esoccer audit

## Milestone 7 — Shadow Pick Evaluator (IMPLEMENTED LOCALLY / NO REAL SHADOW RUN PERFORMED / FIRST REAL RUN BLOCKED ON FULL-PAYLOAD BACKUP AND AUTHORIZED FUTURE CAPTURE, 515 tests)

**Status:** Milestone 7 implementation has been re-built per the
approved recovery plan (Option A: reduce, then complete local
ingestion vertical slice). All required modules and the CLI exist.
No commit, push, PR, network access, or real shadow run has been
performed. Tests are green. Documentation updated to "IMPLEMENTED
LOCALLY" status.

**Floating-point boundary observation (recorded limitation, not a
fix):** The frozen R2 implementation compares binary floats
directly. Some decimal source combinations mathematically equal to
`0.20` can parse to a float slightly above `0.20` and therefore
be ineligible (e.g. `0.55 - 0.35 == 0.20000000000000007`). The
frozen implementation is preserved unchanged; no tolerance or
rounding adjustment is authorized. Synthetic test fixtures use an
interior gap (e.g. `0.18` from `0.54` vs `0.36`) when the test
needs the third event to be R2-eligible. See
`docs/MILESTONE7_SHADOW_PICKS_PLAN.md` §8 for the full note.

**Conflict detection must precede R2 eligibility and ranking
(processing order, owner integrity review):** The shadow
evaluator's pipeline is a strict five-stage flow. Conflict
detection runs on EVERY timed-valid record BEFORE identity
validation, feature construction, R2 eligibility, and R1
ranking. An R2-ineligible observation whose decision
content differs from an R2-eligible observation is therefore
classified as a genuine decision conflict and the entire
group is excluded — the R2-ineligible observation is NOT
silently filtered out before conflict detection. The
previous design ran dedup on the R2-filtered set, which was
an integrity blocker (reviewed and corrected in the second
post-PR integrity pass).

Pipeline stages, in order:

1. **Keyability** — every verified parser snapshot is run
   through `_extract_decision_fingerprint`. Records too
   malformed to form a fingerprint (unknown sport, empty
   event_id/date/participant, self-pair after normalize,
   None or out-of-range probability) are bucketed as
   `malformed_or_unkeyable`. They do NOT participate in
   conflict detection and are NOT silently attached to an
   unrelated event.
2. **Timing gate** — every keyable record's `captured_at`
   is checked against the 24h-before-target safe cutoff.
   Records past the cutoff (or with an unparseable
   timestamp) are bucketed as `timing_rejected`.
3. **Conflict classification** — every timed-valid record
   is grouped by composite key `(sport, event_id,
   event_date)` and its price-free decision fingerprint
   is compared against the other records in the group:
   - one fingerprint, N observations → one admitted
     canonical record; N-1 extras counted as
     `exact_decision_duplicate_extra_rows`; all N source
     observations preserved as provenance;
   - multiple fingerprints, any number of observations →
     entire group excluded; `conflict_groups` += 1,
     `conflicting_rows` += N; both fingerprints retained in
     `decision_conflicts` for forensic review.
4. **Identity + features + R2 + R1** — runs ONLY on the
   admitted canonical records (NOT on the conflict-
   excluded group, NOT on malformed or timing-rejected
   records).
5. **Rank + select** — per-sport-day ranking, primary + top-3
   cohort assignment, selection emission.

Staged accounting (three separate non-overlapping
equations, asserted before the manifest is written):
- Stage 1 — `verified_parser_snapshots = malformed_or_unkeyable + timing_rejected + timed_keyable_snapshots`
- Stage 2 — `timed_keyable_snapshots = admitted_canonical_records + exact_decision_duplicate_extra_rows + conflicting_rows`
- Stage 3 — `admitted_canonical_records = identity_ineligible + feature_incomplete_or_r2_ineligible + primary_selected + top3_cohort_selected + eligible_ranked_beyond_top3`

Participant normalization in the fingerprint reuses the
existing v2 identity helper `key_of` from
`slumdog.shadow_contracts` (casefold + alphanumeric only,
digit order preserved). Empty normalized participants are
rejected as malformed; self-pair after normalize is
rejected as malformed; the DC token `21` is NOT rewritten
to `12`. The fingerprint excludes provenance fields
(`raw_sha256`, `source_url`, `captured_at`, `route`, body /
sidecar / receipt paths); they are committed to by
`input_digest` and recorded in the per-record
`_provenance_observations` list on the canonical record.

`draw_probability` may legitimately be `None` (the
price-free contract explicitly supports
`forebet_draw_probability_missing` and two-way sports
commonly have no draw). `None` is a valid fingerprint value
and is distinguished from `0.0` in the fingerprint tuple so
two observations of the same event that differ only in the
presence/absence of a draw probability are NOT collapsed.
If `draw_probability` is present it must be finite and in
`[0, 1]`; a two-way sport carrying a non-zero draw is
malformed.

Canonical-record selection is deterministic: for a single
fingerprint bucket the canonical is the observation with
the lexicographically smallest `raw_sha256` of its body
(with `captured_at`, `body_path`, and `source_url` as
tiebreakers). This makes the canonical choice
order-independent at the receipt level: two runs whose
capture receipts differ only in the byte order of two
decision-equivalent observations select the same canonical
record, the same features, the same R1 rank, and produce
the same `decision_digest`. `input_digest` legitimately
differs when the receipt byte order differs (the receipt's
SHA-256 is committed to by `input_digest`). ALL source
observations are still retained as
`provenance_observations` on the canonical record; no
source is silently lost.

Digests are distinct:
- `input_digest` commits to every source observation AND
  to the conflict fingerprint trail.
- `decision_digest` commits to the conflict-resolved
  considered pool, exclusions, primary/cohort selections,
  and the staged accounting. It intentionally includes the
  duplicate-source accounting
  (`admitted_canonical_records`,
  `exact_decision_duplicate_extra_rows`,
  `conflict_groups`, `conflicting_rows`) so two runs with
  the same decision content but a different number of
  observations produce different decision_digests — this is
  the documented choice (the accounting IS the reproducible
  record of how many observations were collapsed). Two
  runs differing only in odds-only provenance produce
  different `input_digest` (because the per-snapshot
  sidecar/body SHA-256 differ) but the same
  `decision_digest` (because odds-only differences do not
  change the price-free decision content).

Asymmetric conflict tests (owner spec):
- A: same composite key, observation A R2-eligible (gap
  0.10), observation B R2-INELIGIBLE (gap 0.30) → 1
  conflict group, 2 conflicting rows, 0 admitted, both
  fingerprints + provenance retained.
- B: same composite key, observation A identity-eligible,
  observation B identity-INELIGIBLE (equal participant
  probabilities on a two-way sport) → 1 conflict group, 2
  conflicting rows, 0 admitted, `identity_ineligible`
  count must be 0 (conflict runs before identity).
- C (kept from prior commit): same composite key, two
  observations of the same event with different odds
  metadata but identical decision content → 1 admitted
  canonical, 1 exact-decision-duplicate extra, 0 conflicts.

See `docs/MILESTONE7_SHADOW_PICKS_PLAN.md` §10 for the
full ordering spec.

**Decision content vs source provenance (dedup semantics):**
The shadow evaluator's across-capture dedup uses a
**decision fingerprint** that contains ONLY price-free decision
content (sport, event_date, event_id, participants, and the three
forebet probabilities). Provenance fields — `raw_sha256`,
`sidecar_sha256`, `captured_at`, `source_url`, `route`, body
paths — are EXCLUDED from the fingerprint. Two observations of
the same event that differ only in odds/provenance are
**decision-equivalent duplicate observations**: one is admitted
as the canonical record, the extras are counted in
`exact_decision_duplicate_extra_rows`, and all source
observations are preserved as provenance. Two observations
whose decision fingerprints differ are a **genuine decision
conflict**: the entire group is excluded from decision
evaluation (`conflict_groups` / `conflicting_rows`), both
fingerprints are retained in the manifest's `decision_conflicts`
list for forensic review, and no arbitrary winner is chosen. The
previous implementation that included `source_url`, `raw_sha256`,
and `captured_at` in the fingerprint was an integrity blocker
(reviewed and corrected in the post-PR integrity pass). The
decision_digest is independent of per-snapshot source fields so
odds-only differences (whether between separate runs or between
observations in the same run) do not affect decision
reproducibility. See
`docs/MILESTONE7_SHADOW_PICKS_PLAN.md` §10 for the full
semantics.

**Scope:** A pre-event forward shadow evaluator that consumes an
already-captured Forebet snapshot, applies the **frozen R2 eligibility
rule** read from `config/research_baselines_v1.json` (never
duplicated), applies **R1 ranking** (the same comparator used by the
6B analyzer), and emits an immutable per-sport-day payload + manifest
under `data/reports/shadow/<target_date>/<run_id>/`.

**Files (uncommitted — to be reviewed before commit):**
- `src/slumdog/shadow_contracts.py` — NEW (~125 lines). Owns
  `PreEventRecord` and `from_event_snapshot`. Imports from
  `slumdog.contracts` + `slumdog.sports` only. No dependency on
  `shadow_evaluator` or `capture_loader`. The
  `__post_init__` defensively checks forbidden outcome/odds field
  names. Establishes the typed boundary for the forward evaluator.
- `src/slumdog/capture_loader.py` — NEW (~290 lines).
  `load_capture_records(target_date, capture_receipt_path, repo_root)
  -> CaptureLoadResult`. Verifies receipt SHA-256, sidecar exact-byte
  SHA-256, body exact-byte SHA-256 against sidecar-declared SHA-256.
  Uses `parsers.parse_capture`. Path-containment via
  `_resolve_within_root`. Derives `current_only` rejection from
  `SPORTS[sport].current_only` (esoccer, afl are rejected
  automatically). Returns balanced `capture_accounting` and
  `snapshot_accounting`.
- `src/slumdog/history_loader.py` — NEW (~270 lines).
  `load_valid_history(target_date, repo_root, history_paths=None,
  max_interim_bytes=256MiB) -> HistoryLoadResult`. Supports both formats
  actually used in the repo:
  `data/interim/settled_history.json` and
  `data/reports/history_<sport>.jsonl.gz`. Streams gzipped JSONL
  without decompressing-into-memory. Uses
  `dataset._validate_settled_dict` and `dataset._census_grouping`.
  Asserts two non-overlap equations in the function body. Uses
  `RESEARCH_FEATURE_CONTRACT_VERSION` from `research_builder`. The
  in-memory bound for non-gz interim ledgers was tightened from
  1 GiB to 256 MiB; gz inputs are still streamed with no
  whole-file in-memory bound. The CLI exposes
  `--history-max-interim-bytes` for tests.
- `src/slumdog/shadow_evaluator.py` — REWRITTEN (~640 lines, down
  from 1109). Three layers: `_evaluate_record` (pure per-record),
  `_emit_run` / `_blocked_run` (decision engine), `evaluate_from_disk`
  (orchestration), `main` (CLI). Single `ShadowEvaluatorError` base.
  Imports `is_r2_eligible` and `canonical_json_bytes` from
  `baseline_analyzer` (no duplicated thresholds). `r1_sort_key` is
  adapted from `baseline_analyzer` via a thin wrapper that maps
  features+event_id into the event-dict shape it expects. Uses
  `safe_cutoff_utc(target_date)` which subtracts 24h. Atomic write
  via `tempfile.mkstemp` + `os.replace`. Refuses overwrite of
  existing artifact dir. BLOCKED runs go to a separate
  `BLOCKED/BLOCKED_<stamp>_<reason>.json` so they cannot be mistaken
  for completed artifacts.
- `src/slumdog/dataset.py` — UNCHANGED in this recovery (the
  `build_pre_event_features` extraction is from the prior session;
  the new helpers delegate to it).
- `config/shadow_evaluator_v1.json` — UNCHANGED from the prior
  session. Canonical SHA-256 (sorted keys, compact separators,
  UTF-8): `dd08976a262e7a1882a4e29846612094c20447faf587c01a42608d57f4f4d597`.
  Verified after refactor.
- `tests/test_shadow_evaluator.py` — REWRITTEN. 86 focused behavioral
  tests (up from the prior 60 — additional tests added for v2 history
  validity matrix, capture accounting, BLOCKED receipt semantics, and
  digest content). No test-count target. Tests do not import Git, do
  not load a second copy of `dataset.py`, do not parse source text.
  The shared feature extraction has a true golden-regression test
  (`test_shared_feature_golden_regression`) whose expected digest
  `1a97cb81fc6521a99f1055a873975d562cae33fefce7468ceca929739f8fca0d`
  is hardcoded and recorded with a comment naming the base commit
  `b87784fdb590c17b55d4fa1c2bd6c3275dce0f6d` and the audit procedure
  that produced it (separate subprocess against
  `/tmp/golden_audit/base_pkg/slumdog/`). The base and current
  canonical outputs are byte-for-byte identical
  (`diff -q` reports no difference).
- `docs/MILESTONE7_SHADOW_PICKS_PLAN.md` — UNCHANGED from prior
  session.
- `HANDOFF.md` (this file), `docs/STATE.md` — UPDATED to "MILESTONE 7
  IMPLEMENTED LOCALLY / NO REAL SHADOW RUN PERFORMED / FIRST REAL
  RUN BLOCKED ON FULL-PAYLOAD BACKUP AND AUTHORIZED FUTURE CAPTURE"
  status. Do NOT say complete, merged, production-ready, or
  shortlist-authorized.

**Frozen rule source (not duplicated):**
- R2 eligibility is the single `bool` returned by
  `baseline_analyzer.is_r2_eligible(features)`. No second
  implementation exists. The local `is_r2_eligible` re-exports the
  baseline one. The shadow declaration's
  `anti_tuning.rule_source_frozen_config_sha256` equals the frozen
  6B config SHA-256 and is verified at load. Any drift in the 6B
  config raises a `ShadowEvaluatorError` and the evaluator refuses
  to read input or write output.
- R1 ranking is `baseline_analyzer.r1_sort_key` invoked through a
  thin adapter that maps the local features+event_id shape into the
  event-dict shape the baseline expects. There is no duplicate
  ranking tuple.

**Authorization gates (fail-closed at load):**
- `production_authorized`, `shortlist_policy_authorized`,
  `training_authorized`, `threshold_optimization_authorized` must
  all be explicitly `false`. `shadow_evaluation_authorized` must be
  `true`. Tested via parametrized `test_authorization_gate_rejected`.

**Typed boundary (PreEventRecord) at the lowest sensible layer:**
- The forward record has 15 fields, all required for identity,
  features, ranking, and provenance. No `score_*`, `winner_index`,
  `disposition`, `period_scores_*`, or `odds_*` field exists by
  construction. The `__post_init__` enforces the field-name boundary
  defensively. The `from_event_snapshot` adapter drops forbidden
  fields from any source `EventSnapshot`.

**Circular-import avoidance:**
- `shadow_contracts` imports only from `slumdog.contracts` +
  `slumdog.sports`.
- `capture_loader` imports from `slumdog.parsers`, `slumdog.sports`,
  `slumdog.contracts`, `slumdog.shadow_contracts`. NOT from
  `slumdog.shadow_evaluator` or `slumdog.history_loader`.
- `history_loader` imports from `slumdog.contracts`,
  `slumdog.history`, `slumdog.research_builder`. NOT from
  `slumdog.shadow_evaluator` or `slumdog.shadow_contracts`. (Late
  imports to `slumdog.dataset` and `slumdog.sports` only to avoid
  module-load order coupling.)
- `shadow_evaluator` imports from `slumdog.baseline_analyzer`,
  `slumdog.capture_loader`, `slumdog.history_loader`,
  `slumdog.shadow_contracts`. (No circular imports in either
  direction.)

**Frozen 24h timing gate:**
- `safe_cutoff_utc(target_date) = target_date 00:00 UTC − 24h`. The
  gate is enforced on each record's `captured_at` and on the run's
  `decision_committed_at`. Both must be tz-aware UTC. A violation is
  recorded as `PRE_EVENT_TIMING_UNVERIFIED` (and at run level as
  `SHADOW_RUN_BLOCKED` if the run itself commits after the cutoff).
  The gate is described in the manifest as a *conservative pre-event
  timing gate*; it is not proof of exact kickoff time.

**Per-sport-day cohort (no global cap):**
- R1-sorted eligible events per `(sport, event_date)` are assigned
  `PRIMARY_SHADOW_SELECTION` (rank 1), `TOP3_EVALUATION_COHORT`
  (ranks 2–3), and `ELIGIBLE_RANKED_BEYOND_TOP3` (ranks 4+).
  Sport-days with zero eligible events get `SHADOW_NO_SELECTION`.

**4-ID split (run_id / input_digest / decision_digest /
decision_committed_at):**
- `input_digest` is canonical SHA-256 over
  `{declaration_sha256, target_date, capture_receipt_sha256,
  history_input_sha256 (per-file map), sorted record tuples}`.
- `decision_digest` is canonical SHA-256 over
  `{sorted selections without run_id, decision_accounting}`.
- `run_id = sha256({version, input_digest, decision_digest,
  decision_committed_at})[:16]`.
- `decision_committed_at` captured at the very start of
  `evaluate_from_disk`, ISO 8601 UTC, second precision, suffixed `Z`.

**No-overwrite invariant:**
- The artifact directory is created with `mkdir(exist_ok=False)`. A
  second run with the same `input_digest` and same
  `decision_committed_at` (i.e. same `run_id`) is rejected with
  `ShadowEvaluatorError("refusing to overwrite...")`. Changing the
  input yields a different `run_id` and a sibling directory. The
  same call repeated with a bumped commit timestamp produces a
  different `run_id` and the same `decision_digest` /
  `input_digest` (verified end-to-end).

**Staged accounting (real observations, not zero placeholders):**
- capture-level: `captures_verified + captures_missing +
  captures_hash_mismatch + captures_schema_invalid +
  captures_parse_failed + captures_unsupported =
  raw_capture_receipt_entries`.
- snapshot-level: `snapshots_unique_accepted +
  snapshots_exact_duplicate + snapshots_conflicting +
  snapshots_invalid_identity = parser_emitted_snapshots`.
- history-level: `history_decoded_rows == history_schema_invalid +
  history_schema_valid_candidate_rows`; and
  `history_schema_valid_candidate_rows == v2_excluded +
  unique_valid_rows + exact_duplicate_rows + conflicting_rows`.
  Asserted in the function body.
- decision-level: `timing_rejected + identity_ineligible +
  feature_incomplete_or_r2_ineligible + primary_selected +
  top3_cohort_selected + eligible_ranked_beyond_top3 =
  unique_nonconflicting_rows`.

**Durability limitation:**
- The manifest records
  `durability_status = LOCAL_CODESPACE_ONLY_NOT_BACKED_UP`. A
  second-copy procedure is **not** part of M7 and is deferred to a
  separately approved PR.

**Production-isolation evidence:**
- The shadow_evaluator module imports only from `slumdog.baseline_analyzer`,
  `slumdog.capture_loader`, `slumdog.history_loader`,
  `slumdog.shadow_contracts`. NOT from `pipeline`, `forebet`,
  `settlement`, `research_dataset`, `dataset_audit`, `cli`, or
  `training`. The test
  `test_production_isolation_no_settlement_or_collectors` monkeypatches
  `forebet.ForebetCollector` and
  `settlement.append_settled_from_capture` to raise AssertionError if
  called; the test passes.
- The test `test_production_isolation_no_network` monkeypatches
  `urllib.request.urlopen` to raise AssertionError if called; the
  test passes.
- No raw capture or new fixture was added. No write into
  `data/raw/`, `data/interim/`, `data/ledgers/`, or any pre-existing
  final path. New outputs only under
  `data/reports/shadow/<target_date>/<run_id>/` (success) or
  `data/reports/shadow/<target_date>/BLOCKED/` (failure receipts).

**CLI:**
- `python -m slumdog.shadow_evaluator --help` works without loading
  configs or touching the filesystem.
- `python -m slumdog.shadow_evaluator --date <YYYY-MM-DD>
  --capture-receipt <path> --config <path> [--root <root>]
  [--history <path>...]` returns 0 on success and a non-zero exit on
  any integrity failure with a concise stderr line that does not
  include a Python traceback.

**Real-run-id uniqueness proof (verified end-to-end):**
- Run A (clock 2026-08-26T12:00:00Z): `run_id=8bb932e3c9d28bd0`,
  `decision_digest=77d56bef797d3c78…`, `input_digest=aea7f565…47ba8b`,
  `status=SHADOW_NO_SELECTION`.
- Run B (clock 2026-08-26T12:00:01Z, same on-disk inputs):
  `run_id=6f16dbb2698e7954`, same `decision_digest`, same
  `input_digest`, same `status`. Both artifact directories exist;
  neither overwrote the other.

**No-first-real-run confirmation:**
- No real Forebet network request was made during M7 development or
  recovery.
- No real shadow evaluation run was performed.
- The evaluator was verified against synthetic / in-memory input
  only (per-test `tempfile.TemporaryDirectory()` root).
- No commit / push / PR was made for the M7 work in this session.
