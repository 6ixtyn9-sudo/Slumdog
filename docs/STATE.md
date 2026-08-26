# Slumdog State — Canonical Current Truth

**Last verified:** 2026-08-26 (UTC) — Milestone 6A implemented, local verification complete, real-data execution pending
**Branch:** `arena/01a03dc4-slumdog` (delivery), `main` is only permanent branch
**Doc canonical path:** `docs/STATE.md`
**PR:** #8 OPEN — "Historical integrity evidence and research dataset readiness" — 361 tests passed
**HEAD:** a7de11e

## Permanent Product Mission

> **Slumdog identifies a small daily shortlist of participants that Forebet considers underdogs but whose available pre-event evidence indicates a credible outright-win upset.**

Invariants:
- UNDERDOG_WIN outright only, draw=failed
- Odds optional metadata only
- Never force pick; NO_STRONG_UNDERDOG valid
- Training frozen until dataset contract approved

## Milestones

**Milestones 0–5: COMPLETE, Milestone 6A: COMPLETE (local verification; real-data execution pending)**

- Milestone 0: Governance — STATE.md → docs/STATE.md, AGENTS.md, README, 5 corrections
- Milestone 1: Audit — docs/MILESTONE1_AUDIT.md REFERENCE, 10 gaps, 8 refinements
- Milestone 2: Price-free identity/label — src/slumdog/underdog.py, 40 tests, SPORTS registry draw capability
- Milestone 3: Feature timing — docs/FEATURE_TIMING_CONTRACT.md, period_values UNKNOWN PROHIBITED
- Milestone 4: Architecture — src/slumdog/dataset.py price-free examples, receipt with raw/canonical accounting, deterministic dedup composite key (sport,event_id,event_date), 30 tests
- Milestone 4E: Hardening — no unsafe defaults, no silent swallowing, strengthened digest, provenance validation, disposition vocabulary SETTLED/SETTLED_CUP/SETTLED_DRAW/VOID/NO_CONTEST, winner_index strict bool/float/string rejected, deterministic provenance merge
- Milestone 4F: Conflict census — ValidEventWithSource audit-only source tracking (file, line/index), ConflictGroup compact report, classification DOMAIN/OUTCOME/PROBABILITY/DISPOSITION/PROVENANCE/MULTIPLE, census mode --conflict-report, status DATA_CONFLICTS, nonzero exit, receipt with conflicting_composite_keys, conflicting_rows, conflicts_by_sport, conflicts_by_field, conflicts_with_valid_raw_sha256, conflicts_without_valid_raw_sha256, examples not emitted, normal builder still fail-closed
- Milestone 5: Historical integrity — schema-exclusion diagnostics; six malformed American-football rows classified (origin layer UNKNOWN); hockey:278977 double-write mechanism resolved as far as retained evidence allows (one six-row parse/write batch by the pre-hardening writer; recurring first-row-repeated-at-end pattern; origin layer UNKNOWN); provenance policy recorded as unapproved for training/production
- Milestone 6A: Research dataset readiness — src/slumdog/research_dataset.py; explicit `--research-exclude-conflicts` opt-in; census-before-collapse ordering; whole-key conflict exclusion; deterministic `/tmp`-only examples; simplified receipt `RESEARCH_DATASET_READY_WITH_LIMITATIONS`; focused tests (21 new); strict mode unchanged

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
Milestones 0–5: COMPLETE
Milestone 6A (research dataset readiness): COMPLETE — implementation + real-data verification (head 3898103, 396 tests)
Price-free foundation: v2 implementation on arena/01a03e7a-slumdog; replacement PR open against main (supersedes PR #8, closed not-merged)
Historical dataset generation: FAIL-CLOSED (strict); RESEARCH mode opt-in with whole-key conflict exclusion
Research builder: v2 incremental — bounded-memory, linear-time, bit-identical to strict on valid canonical events
Real-data verification: exit 0 in 192.61 s, peak RSS 2,284.2 MiB, 654,011 eligible, 1,096 builder exclusions (fully explicit reasons)
Training: FROZEN
Production: NOT AUTHORIZED
Milestone 6B: NOT STARTED / NOT AUTHORIZED
Next: maintainer scope review of the replacement PR
```

## Training / Production

- **Training:** FROZEN (MODEL_TRAINING_ALLOWED=False)
- **Production:** NOT AUTHORIZED
- **Research dataset measurement (Milestone 6A): AUTHORIZED** — dataset construction, receipt measurement, non-model descriptive statistics, research-only artifacts. Not authorized: fitted models, threshold optimization, calibrated probabilities, ranking, daily shortlist, shadow picks, production, wagering.
- **Dataset builder strict mode:** fail-closed, correctly refuses corrupted ledger, does not guess, delete, or silently quarantine
- **period_values:** UNKNOWN and PROHIBITED per FEATURE_TIMING_CONTRACT.md
- **Source-conflict limitation:** SettledEvent does not represent source conflict; not in digest; builder assumes no conflict; documented

## Next Milestone

**Milestone 6A — COMPLETE (final real-data verification at head `3898103`)**

Final Codespace run passed every gate: audit exit 0, elapsed 192.61 s, peak RSS 2,284.2 MiB. Accounting: 655,394 raw → 6 schema excluded → 655,388 valid → 279 exact duplicates + 2 conflicting rows (1 key) + 655,107 canonical → **654,011 eligible + 1,096 builder exclusions** (equal-probability 180, out-of-range 7, self-pair 18, unexpected two-way draw 588, void 303). Outcomes: 191,238 underdog wins / 380,212 favorite wins / 82,561 draw negatives; positive rate 0.29240792586057424. Provenance 0/654,011/0; 17-field feature missingness; price independence, global + per-sport outcome accounting, and exclusion accounting all passed; input digest `30cb96ffd2ee8193ecf0786df1b6a45aca3a26a8c8457d85c0135c512685c1c7`; examples digest `ac84325d281c1808765fbcb18028efb193dbbdd2affc806ba459bb9d8a09a228` (deterministic; unchanged by the receipt-only correction); compressed artifact 45,439,763 bytes; source ledger hashes unchanged.

**Next:** maintainer scope review of the replacement PR ("Add bounded research-only price-free dataset generation") → merge decision.

**Milestone 6B (not started, not authorized):** transparent non-trained walk-forward baselines. Requires explicit approval; training stays frozen.

## Verification

- pytest → 396 passed (340 prior + 21 research-mode + 35 incremental-builder tests)
- pyflakes src/slumdog → clean
- py_compile scripts/*.py src/slumdog/*.py tests/*.py → ok
- git diff --check → ok
- git status --short → clean after commit
- Research-mode smoke test on synthetic ledger: census-before-normalization ordering verified, accounting balanced, deterministic gzip, price-independence passed, status RESEARCH_DATASET_READY_WITH_LIMITATIONS
- v2 equivalence: bit-identical examples + matching counters to the strict builder on valid canonical settled events (single-sport, multi-sport, same-date isolation, input reordering, provenance duplicates); intentional v2-vs-legacy divergences (NO_CONTEST alias, SETTLED_CUP winner-0, self-pair) covered as separate tests
- Streaming: mid-stream gzip failure and mid-commit rename failure leave no final artifacts (verified by injected faults); sample bounded to first N emitted; no-preexist refusal; diagnostic receipt only on internal inconsistency
- Outcome subtypes: global readiness == top-level outcomes; per-sport `eligible = positive + favorite_wins + draws`; draws never collapsed into favorite wins
- Receipt auditability: `accounting.builder_exclusion_reasons` (all builder reason keys, sorted; sum == builder_excluded_rows; stable serialization for READY and NOT_READY receipts)
- Real-data census (historical, 2026-08-24): DATA_CONFLICTS, 1 conflicting key (hockey), 2 conflicting rows, 6 schema exclusions, 654,029 eligible before gate (legacy membership; v2 differs by design), 0 provenance present
- Real-data final run (2026-08-26, head 3898103): exit 0, 192.61 s, peak RSS 2,284.2 MiB, 654,011 eligible, 1,096 builder exclusions with fully explicit reasons, positive rate 0.29240792586057424, deterministic examples digest unchanged by receipt-only correction, ledger hashes unchanged

## Links

- AGENTS.md — constitution
- README.md — overview
- HANDOFF.md — living handoff with full census evidence
- docs/PRICE_FREE_DATASET_CONTRACT.md — dataset contract CURRENT
- docs/FEATURE_TIMING_CONTRACT.md — timing contract CURRENT (period_values UNKNOWN)
- docs/MILESTONE1_AUDIT.md — audit REFERENCE
