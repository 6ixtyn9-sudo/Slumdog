# Historical Integrity Audit

**Status:** Investigation Planned

## 1. Exclusions (SCHEMA_MISSING_PARTICIPANT_1)
- **Target:** 6 schema exclusions due to missing `participant_1`.
- **Method:** Collected via `--schema-exclusion-report` using tested adapters.
- **Goals:**
  - Which files/lines?
  - Which sports?
  - Why is the participant missing?
  - Is it `settlement.py` or `backfill.py` shape?

## 2. Hockey Double-Write (hockey:278977)
- **Known Conflict:** Netherlands W vs Denmark W, 2023-08-20.
  - Variant A: 1–6 periods 0,1,0 / 2,1,3
  - Variant B: 0–4 periods 0,0,0 / 1,2,1
  - File: `data/reports/history_hockey.jsonl.gz` lines 62/67.
- **Goals:**
  - Check if `history_hockey.json` (dict container) has separate accounting.
  - Determine why the same file has two conflicting scores.
- **Constraints:** Do not choose a variant, delete, deduplicate, guess a plausible score, or average. Do not query Forebet until provenance is established.

## 3. Provenance Policy
- **Context:** 0 eligible rows have provenance present / 654,029 are missing `raw_sha256` and `captured_at`.
- **Policy:** Research use **CAN PROCEED** with this explicit limitation. We **DO NOT** invent `raw_sha256` for historical ledgers.
- **Constraint:** The `_canonical_event_repr` continues to explicitly document the source-conflict limitation.

## 4. Next Steps
Transparent walk-forward baselines remain deferred until historical integrity and provenance policy are reviewed.