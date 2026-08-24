# Milestone 5: Historical Integrity Investigation

**Status:** Investigation Planned

## 1. Inspect 6 `SCHEMA_MISSING_PARTICIPANT_1` Exclusions
We must inspect the 6 exclusions for missing `participant_1`. A script `scripts/investigate_m5.py` has been provided to extract these rows without deleting or guessing.
- Which files/lines?
- Which sports?
- Why is the participant missing?
- Is it `settlement.py` or `backfill.py` shape?

## 2. Investigate hockey:278977 Double-Write
We have a known conflict for hockey event 278977 (Netherlands W vs Denmark W, 2023-08-20).
Variant A: 1–6 periods 0,1,0 / 2,1,3
Variant B: 0–4 periods 0,0,0 / 1,2,1
File: `data/reports/history_hockey.jsonl.gz` lines 62/67.

**Rules for investigation:**
- Do not choose a variant.
- Check if both lines appear in the same gz.
- Check if `history_hockey.json` (dict container) has separate accounting.
- Check if reconstruction `HISTORICAL_PAGE` has a known double-write.
- Do not delete, deduplicate, guess a plausible score, or average.
- Do not query Forebet until provenance is established.

## 3. Provenance Policy Decision
**Context:** 0 eligible rows have provenance present / 654,029 are missing provenance (`raw_sha256` and `captured_at`).
**Policy:** 
- Research use **CAN PROCEED** with this explicit limitation documented.
- We **DO NOT** invent `raw_sha256` for historical ledgers.
- The `_canonical_event_repr` continues to explicitly document the source-conflict limitation.

## 4. Retained Strict Rules
- Keep `period_values` `UNKNOWN` and **PROHIBITED** per `FEATURE_TIMING_CONTRACT.md`.
- Keep source-conflict limitation explicitly documented in `_canonical_event_repr`.

## 5. Transparent Baselines Plan
After integrity findings are resolved, we plan transparent baselines with **walk-forward validation** (never random splits). 
Planned baselines:
- **Forebet underdog probability:** Naive baseline using Forebet's assigned probability.
- **Probability gap:** Favorite probability minus Underdog probability.
- **Recent-form differential:** Form points comparison.
- **Ma Golide heuristic (price-free):** Existing deterministic rule-based selection.
- **Simple interpretable model:** A transparent linear or tree-based model strictly restricted to `ALLOWED` features only (17 features: 5 identity + 12 prior).

No deep learning black-boxes or odds-based features are permitted.
