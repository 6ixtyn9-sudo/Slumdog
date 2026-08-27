# Slumdog Living Handoff

**Last updated:** 2026-08-27 (UTC) — Milestone 6B: IMPLEMENTED and locally verified (423 tests passed); canonical config SHA-256 verified; real-data Codespace run pending
**Branch:** `arena/01a04198-slumdog`
**Base commit:** `09a56dcae4daff4014f79beca220cefb67edfe9d` (`main` after PR #9 merge)
**Phase:** Milestones 0–6A COMPLETE AND MERGED; Milestone 6B IMPLEMENTED; training FROZEN; production NOT AUTHORIZED; shortlist policy NOT AUTHORIZED
**Mission:** Slumdog identifies a small daily shortlist of participants that Forebet considers underdogs but whose available pre-event evidence indicates a credible outright-win upset.
**Training:** FROZEN (`feature_contracts.py: MODEL_TRAINING_ALLOWED=False`)
**Tests:** 423 passed (verified 2026-08-27)

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

## Milestone 6B — Non-Trained Baseline Analyzer (IMPLEMENTED, 423 tests passed)

- **Frozen Pre-declaration:** `config/research_baselines_v1.json` verified against canonical SHA-256 `666dabe7ea21e11867cf4816f4c2edcd771247646c6c9d7726c22611cda700a1`. Anti-tuning guarantees preserved: tuning periods empty, result-driven amendments prohibited, shortlist policy not authorized, training frozen.
- **Two-Pass Architecture:**
  - `src/slumdog/baseline_analyzer.py` and `src/slumdog/research_baselines.py`.
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
- **Tests:** `tests/test_baseline_analyzer.py` (27 focused tests). Total suite: 423 passed.
- **Codespace execution command:**
  ```bash
  python -m slumdog.baseline_analyzer \
    --config config/research_baselines_v1.json \
    --examples /tmp/slumdog_research/examples.jsonl.gz \
    --receipt /tmp/slumdog_research/receipt.json \
    --baselines-json /tmp/slumdog_6b/baselines.json \
    --summary-md /tmp/slumdog_6b/summary.md
  ```

## Next Milestone

**Milestone 6B — Real-Data Codespace Execution:**

Run `python -m slumdog.baseline_analyzer` against the Codespace retained 654,011 research dataset to generate the canonical Milestone 6B baseline results. Model training remains FROZEN; production remains NOT AUTHORIZED.

## PR State

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
