# Historical Integrity Audit

**Status:** Investigation Executed

## 1. Exclusions (SCHEMA_MISSING_PARTICIPANT_1)
- **Target:** 6 schema exclusions due to missing `participant_1`.
- **Method:** Collected via `--schema-exclusion-report` using tested adapters.
- **Findings:**
  - All 6 rows manifest a strict `SCHEMA_MISSING_PARTICIPANT_1` error because the primary `participant_1` key is null or empty.
  - The rows are captured, but further evaluation is gated by Forebet identity matching.
  - No legacy aliases (`team`, `home`, `player`, `fighter`) have been mapped dynamically to bypass the exclusion.

## 2. Hockey Double-Write (hockey:278977)
- **Known Conflict:** Netherlands W vs Denmark W, 2023-08-20.
  - Variant A: 1–6 periods 0,1,0 / 2,1,3
  - Variant B: 0–4 periods 0,0,0 / 1,2,1
  - File: `data/reports/history_hockey.jsonl.gz` lines 62/67.
- **Findings:**
  - The deterministic conflict census caught the two rows as an `OUTCOME_CONFLICT` directly on the composite key (`sport`, `event_id`, `event_date`) exactly at lines 62 and 67 in the `.jsonl.gz` ledger.
  - `history_hockey.json` functions as a legacy container dictionary containing accounting totals rather than row entries (`'daily_receipts', 'dates_completed', 'dates_requested', 'end', 'failures', 'history_file', 'priced_rows', 'settled_rows', 'sport', 'start', 'void_rows'`).
  - Neither conflicting record has valid `raw_sha256` or `captured_at` fields, blocking algorithmic tie-breaking.
- **Decision:** The conflicting rows remain in the ledger untouched. The dataset builder correctly fails closed. No tie-breaking logic (e.g., score averaging) is implemented.

## 3. Provenance Policy
- **Context:** 0 eligible rows have provenance present / 654,029 are missing `raw_sha256` and `captured_at`.
- **Policy:** Research use **CAN PROCEED** with this explicit limitation. We **DO NOT** invent `raw_sha256` for historical ledgers.
- **Constraint:** The `_canonical_event_repr` continues to explicitly document the source-conflict limitation.

## 4. Next Steps
Transparent walk-forward baselines remain deferred until historical integrity and provenance policy are reviewed by the maintainer.