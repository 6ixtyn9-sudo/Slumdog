# Historical Integrity Audit

**Status:** Investigation Executed — hockey double-write mechanism resolved as far as retained evidence allows; source-body origin layer UNKNOWN

## 1. Exclusions (SCHEMA_MISSING_PARTICIPANT_1)
- **Target:** 6 schema exclusions due to missing `participant_1`.
- **Method:** Collected via `--schema-exclusion-report` using tested adapters.
- **Findings:**
  - All six schema-excluded rows are in: `data/reports/history_american_football.jsonl.gz`
  - All use `reconstruction=HISTORICAL_PAGE`.
  - All contain `participant_1` and `participant_2` as explicit empty strings.
  - No alternative participant-like fields are present.
  - All source URLs contain an empty team slug, `/-/`:
    - line 1415: `american_football:15799` — 2024-08-31 — `/en/american-football/matches/ncaa/-/15799`
    - line 1907: `american_football:16798` — 2024-09-21 — `/en/american-football/matches/ncaa/-/16798`
    - line 2157: `american_football:16965` — 2024-09-28 — `/en/american-football/matches/ncaa/-/16965`
    - line 3665: `american_football:18713` — 2024-11-09 — `/en/american-football/matches/ncaa/-/18713`
    - line 4945: `american_football:20775` — 2025-09-20 — `/en/american-football/matches/ncaa/-/20775`
    - line 5669: `american_football:21787` — 2025-10-11 — `/en/american-football/matches/ncaa/-/21787`
  - **Classification:** `MALFORMED_HISTORICAL_RECONSTRUCTION_WITH_EMPTY_PARTICIPANTS`
  - **Origin layer:** `UNKNOWN` — the ledger contains empty participant values and `/-/` URLs, and the parser can produce this shape from an empty-name row; whether the empty names originated in Forebet's original HTML, the relay, or another reconstruction step cannot be distinguished because no raw capture survives for these dates. Consistent with, but not proven to be, Forebet-side placeholder listings.
  - The receipt reason `SCHEMA_MISSING_PARTICIPANT_1` reflects first-failure validation. Both participants are empty in every affected row.
- **Decision:** No names were inferred, no aliases accepted, no ledgers modified, and no network request made. The six rows remain excluded.
- **System Behavior:** The schema report was emitted successfully while overall status remained `DATA_CONFLICTS`. Receipt and conflict report were emitted; examples/sample were not emitted.

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

### Hockey conflict mechanism

The retained hockey ledger contains exactly six rows for 2023-08-20,
matching the manifest's `settled_rows=6`. The first and last rows for that date
share composite key `hockey:278977`. They agree on all audited fields except
`score_1`, `score_2`, `period_scores_1` and `period_scores_2`.

- Line 62: 1–6, periods 0,1,0 / 2,1,3
- Line 67: 0–4, periods 0,0,0 / 1,2,1

Line 68 begins 2023-08-21. This rules out a later append of a second full
2023-08-20 batch. The conflicting rows were emitted in one six-row parse/write
batch by the pre-hardening writer.

The next date contains a related exact-duplicate pattern:
`hockey:278980` appears at both lines 68 and 71 with identical displayed outcome
values. This indicates a recurring first-row-repeated-at-end pattern in the
historical source body or reconstruction.

The surviving evidence cannot distinguish original Forebet HTML from relay or
reconstruction behavior because no raw capture or row provenance survives.
Origin layer remains `UNKNOWN`.

No record was selected, repaired, deleted or rewritten. Current dataset
construction remains fail-closed.

## 3. Provenance Policy
- **Context:** 0 eligible rows have provenance present / 654,029 are missing `raw_sha256` and `captured_at`.
- **Policy:** Use of provenance-free history for research remains an unapproved policy decision. Training remains frozen. We **DO NOT** invent `raw_sha256` for historical ledgers.
- **Constraint:** The `_canonical_event_repr` continues to explicitly document the source-conflict limitation.

## 4. Next Steps
Transparent walk-forward baselines remain deferred until historical integrity and provenance policy are reviewed by the maintainer. Whether provenance-free ledgers may be used for research (and under what exclusions) is a user-level policy decision that has not been made; no training or baseline evaluation may proceed without it.

## 5. Research-Only Dataset Mode (Milestone 6A)

- `dataset_audit.py` gains an explicit `--research-exclude-conflicts` opt-in (plus research-only `--examples`, `/tmp`-enforced). Strict mode is unchanged: conflicts still fail loudly with no examples.
- Research-mode data flow: conflict census over all valid rows → exclude every conflicting composite key → collapse exact duplicates among the remainder → strict price-free builder → readiness receipt. A conflicting key never reaches a "pick one variant" path.
- Research mode is authorized for dataset construction and descriptive measurement only. It is **not** authorized for training, calibrated probabilities, ranking, shortlists, shadow picks, production, or wagering. `MODEL_TRAINING_ALLOWED` remains `False`.
- The policy decision on wider provenance-free use (walk-forward baselines, then models) remains open and belongs to the maintainer.