# Slumdog State — Canonical Current Truth

**Last verified:** 2026-08-27 (UTC) — Milestone 6B two-pass non-trained baseline analyzer implemented; canonical config SHA-256 verified; all 426 tests passed
**Branch:** `arena/01a04198-slumdog` (delivery), `main` is only permanent branch
**Doc canonical path:** `docs/STATE.md`
**PR:** #10 OPEN — "Implement two-pass non-trained baseline analyzer (Milestone 6B)" — 426 tests passed
**Base commit:** `09a56dcae4daff4014f79beca220cefb67edfe9d` (PR #9 merged)

## Permanent Product Mission

> **Slumdog identifies a small daily shortlist of participants that Forebet considers underdogs but whose available pre-event evidence indicates a credible outright-win upset.**

Invariants:
- UNDERDOG_WIN outright only, draw=failed
- Odds optional metadata only
- Never force pick; NO_STRONG_UNDERDOG valid
- Training frozen until dataset contract approved

## Milestones

**Milestones 0–6A: COMPLETE AND MERGED, Milestone 6B: IMPLEMENTED (local verification complete; real-data execution pending in Codespace)**

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
Milestones 0–6A: COMPLETE AND MERGED (PR #9 merged at 09a56dc, 396 tests)
Milestone 6B (baseline analyzer): IMPLEMENTED — two-pass non-trained baseline analyzer
Canonical config SHA-256: 666dabe7ea21e11867cf4816f4c2edcd771247646c6c9d7726c22611cda700a1 (VERIFIED)
Tests: 426 passed (396 prior + 30 Milestone 6B tests)
Training: FROZEN
Production: NOT AUTHORIZED
Shortlist policy: NOT AUTHORIZED
Next: Codespace execution of baseline analyzer on real 654,011 dataset
```

## Training / Production

- **Training:** FROZEN (MODEL_TRAINING_ALLOWED=False)
- **Production:** NOT AUTHORIZED
- **Research dataset measurement (Milestone 6A): COMPLETE**
- **Research baseline evaluation (Milestone 6B): IMPLEMENTED** — non-model descriptive statistics, non-trained comparator rules (R0, R1, R2), pre-declared signal buckets. Not authorized: fitted models, threshold optimization, calibrated probabilities, ranking, daily shortlist, shadow picks, production, wagering.
- **Dataset builder strict mode:** fail-closed, correctly refuses corrupted ledger, does not guess, delete, or silently quarantine
- **period_values:** UNKNOWN and PROHIBITED per FEATURE_TIMING_CONTRACT.md
- **Source-conflict limitation:** SettledEvent does not represent source conflict; not in digest; builder assumes no conflict; documented

## Next Milestone

**Milestone 6B — IMPLEMENTED (local verification complete with 27 focused tests; real-data Codespace run pending)**

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

## Verification

- pytest → 426 passed (396 prior + 30 baseline analyzer tests)
- pyflakes src/slumdog → clean
- py_compile scripts/*.py src/slumdog/*.py tests/*.py → ok
- git diff --check → ok

## Links

- AGENTS.md — constitution
- README.md — overview
- HANDOFF.md — living handoff with full census evidence
- docs/PRICE_FREE_DATASET_CONTRACT.md — dataset contract CURRENT
- docs/FEATURE_TIMING_CONTRACT.md — timing contract CURRENT (period_values UNKNOWN)
- docs/MILESTONE1_AUDIT.md — audit REFERENCE
