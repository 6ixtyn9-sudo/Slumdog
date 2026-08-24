# Slumdog Living Handoff

**Last updated:** 2026-08-24 (UTC) — Milestones 0–4F COMPLETE, real-data census executed, PR #6 MERGE READY
**Branch:** `arena/01a034f6-slumdog`
**HEAD SHA:** (pending commit) — Milestone 5 Investigation Script added
**Phase:** Milestones 0–4F COMPLETE, price-free foundation MERGE READY, historical dataset generation FAIL-CLOSED, real-data readiness BLOCKED by historical conflicts, training FROZEN, production NOT AUTHORIZED
**Mission:** Slumdog identifies a small daily shortlist of participants that Forebet considers underdogs but whose available pre-event evidence indicates a credible outright-win upset.
**PR:** #6 https://github.com/6ixtyn9-sudo/Slumdog/pull/6 — OPEN, MERGE READY after documentation commit
**Training:** FROZEN (`feature_contracts.py: MODEL_TRAINING_ALLOWED=False`)
**Tests:** 337 passed (verified 2026-08-24)

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
- **Price-free foundation:** MERGE READY
- **Historical dataset generation:** FAIL-CLOSED (correctly refuses corrupted ledger)
- **Real-data readiness:** BLOCKED by 1 outcome conflict and 6 schema exclusions + absent provenance (0 present)
- **No conflict may be guessed, deleted, or silently quarantined**
- **period_values remains UNKNOWN and PROHIBITED** per FEATURE_TIMING_CONTRACT.md 10-point investigation
- **Source-conflict visibility limitation:** SettledEvent contract does not represent source conflict; not in digest; builder assumes no conflict; receipt excluded_source_conflict=0 for current schemas; documented in _canonical_event_repr

## Next Milestone

**Milestone 5: historical integrity investigation**

- *Action:* Added `scripts/investigate_m5.py` and `docs/MILESTONE5_INVESTIGATION.md` to safely inspect exclusions and provenance without modifying ledgers or querying the network.
- Investigate historical reconstruction conflicts (hockey 278977)
- Provenance/reconstruction investigation: why same composite key has two settled scores in same file, no raw_sha256/captured_at
- Establish provenance for historical ledgers (currently 0 present for 654,029 eligible)
- Do not query Forebet until provenance established
- Do not choose more plausible score, average, or infer correctness
- No deletion/dedup of ledger rows
- After provenance established, transparent baselines with walk-forward validation

## PR State

- **Branch:** `arena/01a034f6-slumdog`
- **Base:** `main` @ `c48d5dc`
- **PR:** (pending)
- **Final head:** (pending commit)
- **Merge approves only:** governance documentation; price-free identity and label contracts; safe historical example contracts; strict adapters and receipts; conflict detection and census tooling; tests
- **Merge does NOT approve:** excluding the hockey conflict; training a model; ranking candidates; thresholds; production integration; daily selections; legacy Robber removal

## Evidence Language Compliance

- Verified from code: file paths, function names, disposition vocabulary SETTLED/SETTLED_CUP/SETTLED_DRAW/VOID/NO_CONTEST, winner_index strict, provenance merge deterministic, composite key (sport,event_id,event_date), conflict classification, receipt fields, accounting equations
- Verified from executed census: 11 files, 655,394 raw, 6 schema excluded SCHEMA_MISSING_PARTICIPANT_1, 279 exact duplicates, 1 conflicting key hockey 278977 OUTCOME_CONFLICT, 2 conflicting rows, 0 valid SHA, 1 missing SHA, 654,029 eligible, 1,078 builder exclusions, 0 provenance present
- Verified from tests: 337 passed, pyflakes clean, py_compile ok, diff-check ok
- Unresolved: hockey 278977 conflicting scores — retained both, no selection
- Parked: period_values UNKNOWN PROHIBITED, detail facets UNKNOWN/PARKED, American football odds probe, esoccer audit
