# Slumdog State — Canonical Current Truth

**Last verified:** 2026-08-24 (UTC) — Real-data census executed
**Branch:** `arena/01a034f6-slumdog` (delivery), `main` is only permanent branch
**Doc canonical path:** `docs/STATE.md`
**PR:** #7 OPEN — 337 tests passed
**HEAD:** 2c0e0cf

## Permanent Product Mission

> **Slumdog identifies a small daily shortlist of participants that Forebet considers underdogs but whose available pre-event evidence indicates a credible outright-win upset.**

Invariants:
- UNDERDOG_WIN outright only, draw=failed
- Odds optional metadata only
- Never force pick; NO_STRONG_UNDERDOG valid
- Training frozen until dataset contract approved

## Milestones

**Milestones 0–4F: COMPLETE**

- Milestone 0: Governance — STATE.md → docs/STATE.md, AGENTS.md, README, 5 corrections
- Milestone 1: Audit — docs/MILESTONE1_AUDIT.md REFERENCE, 10 gaps, 8 refinements
- Milestone 2: Price-free identity/label — src/slumdog/underdog.py, 40 tests, SPORTS registry draw capability
- Milestone 3: Feature timing — docs/FEATURE_TIMING_CONTRACT.md, period_values UNKNOWN PROHIBITED
- Milestone 4: Architecture — src/slumdog/dataset.py price-free examples, receipt with raw/canonical accounting, deterministic dedup composite key (sport,event_id,event_date), 30 tests
- Milestone 4E: Hardening — no unsafe defaults, no silent swallowing, strengthened digest, provenance validation, disposition vocabulary SETTLED/SETTLED_CUP/SETTLED_DRAW/VOID/NO_CONTEST, winner_index strict bool/float/string rejected, deterministic provenance merge
- Milestone 4F: Conflict census — ValidEventWithSource audit-only source tracking (file, line/index), ConflictGroup compact report, classification DOMAIN/OUTCOME/PROBABILITY/DISPOSITION/PROVENANCE/MULTIPLE, census mode --conflict-report, status DATA_CONFLICTS, nonzero exit, receipt with conflicting_composite_keys, conflicting_rows, conflicts_by_sport, conflicts_by_field, conflicts_with_valid_raw_sha256, conflicts_without_valid_raw_sha256, examples not emitted, normal builder still fail-closed

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
Milestones 0–4F: COMPLETE
Price-free foundation: MERGE READY
Historical dataset generation: FAIL-CLOSED
Real-data readiness: BLOCKED by 1 outcome conflict and 6 schema exclusions, provenance absent
Training: FROZEN
Production: NOT AUTHORIZED
Next: historical conflict provenance/reconstruction investigation
```

## Training / Production

- **Training:** FROZEN (MODEL_TRAINING_ALLOWED=False)
- **Production:** NOT AUTHORIZED
- **Dataset builder:** fail-closed, correctly refuses corrupted ledger, does not guess, delete, or silently quarantine
- **period_values:** UNKNOWN and PROHIBITED per FEATURE_TIMING_CONTRACT.md
- **Source-conflict limitation:** SettledEvent does not represent source conflict; not in digest; builder assumes no conflict; documented

## Next Milestone

**Milestone 5: historical integrity investigation**

- *Action:* Extended `dataset_audit.py` with `--schema-exclusion-report` and added `docs/HISTORICAL_INTEGRITY_AUDIT.md` to safely document exclusions and provenance limits.
- Investigate why hockey:278977 has two settled scores in same file
- Establish provenance for historical ledgers (currently 0 present)
- Do not query Forebet until provenance established
- No plausibility choice, no averaging, no deletion/dedup
- After provenance, transparent baselines with walk-forward validation

## Verification

- pytest -q → 337 passed
- pyflakes scripts src/slumdog tests → clean
- py_compile scripts/*.py src/slumdog/*.py tests/*.py → ok
- git diff --check → ok
- git status --short → clean after commit
- Real-data census executed deterministically, conflict report emitted, no examples emitted, all rows accounted

## Links

- AGENTS.md — constitution
- README.md — overview
- HANDOFF.md — living handoff with full census evidence
- docs/PRICE_FREE_DATASET_CONTRACT.md — dataset contract CURRENT
- docs/FEATURE_TIMING_CONTRACT.md — timing contract CURRENT (period_values UNKNOWN)
- docs/MILESTONE1_AUDIT.md — audit REFERENCE
