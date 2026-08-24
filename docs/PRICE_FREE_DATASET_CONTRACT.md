# Price-Free Dataset Contract — Milestone 4 COMPLETE (4E + Final Integrity Hardened)

**Last verified:** 2026-08-24 (UTC)
**Branch:** arena/01a033af-slumdog
**Status:** CURRENT — Milestone 4 COMPLETE (4E + final integrity hardened, 323 tests), pending real-data receipt execution in Codespace with actual ledgers
**Training:** FROZEN
**Feature contract version:** `price-free-v1-minimal-2026-08-24`
**Label contract version:** `price-free-v1`

## Mission

Build leak-safe, price-free historical examples from **every eligible settled event**, not only legacy Robber candidates.

Flow:
```
settled event
    ↓
Forebet participant probabilities
    ↓
price-free favorite/underdog identity (identify_forebet_underdog)
    ↓
prior-only pre-event evidence (HistoryIndex, date < current)
    ↓
price-free feature snapshot (ALLOWED only)
    ↓
UNDERDOG_WIN label (label_underdog_outcome, SPORTS registry)
```

Never flows through: legacy odds-first candidate, displayed odds, market implied prob, price availability, legacy Robber score, ROI gate.

## Hardening (4E + Final Integrity)

- **No fabricated defaults:** missing `winner_index` → `SCHEMA_MISSING_WINNER_INDEX` excluded, never defaults to 1. Missing `disposition` → `SCHEMA_MISSING_DISPOSITION` excluded, never defaults to SETTLED. Empty disposition → same. Boolean `winner_index=True/False` rejected even though `True==1` in Python (`type is bool` check). Float `1.0` and string `"1"` rejected (`type is int` required).
- **Disposition vocabulary explicit:** settlement.py produces `SETTLED, SETTLED_CUP, SETTLED_DRAW, VOID` (verified via source inspection: FT/AOT/AP/FINAL → SETTLED; extra_time/penalty → SETTLED_CUP; cricket draw → SETTLED_DRAW; no result/abandon/cancel → VOID). `NO_CONTEST` is explicitly supported compatibility alias for VOID, not a value produced by settlement.py. `SUPPORTED_DISPOSITIONS = {SETTLED, SETTLED_CUP, SETTLED_DRAW, VOID} ∪ {NO_CONTEST alias}`. Unknown dispositions such as `PENDING, LIVE, ABANDONED, CANCELLED, POSTPONED, FT, arbitrary` → `SCHEMA_UNKNOWN_DISPOSITION` schema-excluded unless canonical contract explicitly supports it. Supported settled → eligible for labeling; supported void/no-contest → loaded then explicitly excluded by label contract (`excluded_void`).
- **No silent swallowing:** malformed rows counted by reason (`schema_exclusion_reasons` Counter), not `except: continue`. Unreadable/corrupt files fail loudly (non-zero exit). Unknown `schema_version` → `UNKNOWN_SCHEMA_VERSION` fails.
- **Raw vs canonical accounting with invariants:**
  - `raw_input_rows` = total dicts read
  - `schema_excluded_rows` + `valid_loaded_rows` = raw
  - `valid_loaded_rows` = `exact_duplicates_collapsed` + `canonical_input_rows`
  - `canonical_input_rows` = `eligible_examples` + `builder_excluded_rows`
  - `input_rows` alias `canonical_input_rows` for backward compat
- **Strengthened digest:** `_canonical_event_repr` hashes versioned JSON of `event_id, sport, event_date, participant_1, participant_2, winner_index, disposition, probability_1, probability_2, draw_probability, score_1/2 (used by prior-history), league, source_url, raw_sha256` + `version=canonical-v1`. **Odds deliberately excluded** (documented). Stable under input reordering via sorted `(event_date,sport,event_id)`. Source-conflict limitation documented (see below).
- **Duplicate identity validated:** composite key `(sport, event_id, event_date)` matching `settlement.py` seen key. Same `event_id` in different sports does not collapse. Changed probability/winner/disposition/league under same key → conflicting → fail loudly `ValueError`.
- **Deterministic provenance merge (final integrity):** 
  - Same domain fields + same provenance (identical raw_sha256 and source_url, both missing or both equal) → exact duplicate collapses
  - Same domain + missing provenance on one copy → deterministic merge preserving present (present wins regardless of input order, independent of input order)
  - Same domain + different non-empty raw_sha256 → conflict fail loudly `ValueError: conflicting provenance raw_sha256`
  - Same domain + different non-empty source_url → conflict fail loudly `ValueError: conflicting provenance source_url`
  - Do not silently choose one based on input order. Valid provenance must not be discarded in favor of missing. Canonical selection independent of input order. Digest represents complete provenance set after deterministic normalization. Receipt provenance counts have documented semantics: counts based on canonical rows only after deduplication and deterministic merge.
- **Provenance validation:** `raw_sha256` must be 64 hex chars to count as `provenance_present`; missing → `provenance_missing`; malformed → `provenance_invalid`.
- **Date semantics explicit:** `canonical_date_min/max` = min/max over all canonical inputs (including later excluded), `eligible_date_min/max` = min/max over eligible only. `date_min/max` alias eligible for backward compat. All-excluded dataset → canonical dates present, eligible dates None.
- **Source-conflict limitation documented:** `SettledEvent` contract from supported ledgers (`data/interim/settled_history.json`, `data/reports/history_*.jsonl.gz`) does NOT represent source conflict. Source conflict is a label-time flag in `underdog.py label_underdog_outcome(source_conflict=True)`, not a field in `SettledEvent`. Therefore not included in `_canonical_event_repr` digest. Builder assumes `source_conflict=False`. Receipt `excluded_source_conflict` remains 0 for current schemas. If future ledger adds source_conflict field, it must be included in duplicate comparison, canonical digest, builder eligibility, and receipt. This limitation is documented and not claimed as audited.

## Example Contract

`PriceFreeUnderdogExample` in `src/slumdog/dataset.py`

Required:
```
event_id, sport, event_date, favorite_index (1/2), underdog_index (1/2, never 0),
favorite_probability (0-1), underdog_probability (0-1), draw_probability (None or 0-1, context only),
probability_gap, label (0/1), features (ALLOWED only, None preserved), missingness (1 missing 0 present),
source_url, raw_sha256, feature_contract_version, label_contract_version
```

Optional audit:
```
exclusion_reason, legacy_provenance_missing
```

Prohibited (never in features):
```
odds_1, odds_2, price, overround, fair_market_probability, value_edge, ROI, legacy_robber_score,
period_values, score_1/2, period_scores, extra-time/penalty, disposition, live_score, result text
```

Eligible label 0/1 only, exclusions separate receipt.

## Allowed Features (Minimal Safe Set)

Per `docs/FEATURE_TIMING_CONTRACT.md` ALLOWED.

**Identity (always present):**
- forebet_favorite_probability, forebet_underdog_probability, forebet_probability_gap,
  forebet_draw_probability (None allowed), forebet_draw_probability_missing

**Prior-history (computed strictly from events with date < current, same-date excluded):**
- underdog_prior_games, favorite_prior_games, underdog_prior_win_rate, favorite_prior_win_rate,
  recent_win_rate_gap, h2h_prior_games, h2h_underdog_win_rate, h2h_draw_rate,
  underdog_prior_draw_rate, favorite_prior_draw_rate, prior_scoring_rate_gap, prior_conceding_rate_gap

Subset supported by `HistoryIndex`: `_earlier` uses bisect_left on `(event_date, "")` — same-date excluded. `prior_rows(sport, date)` returns only earlier. `context()` gives H2HStats, RecentForm.

## Prohibited Features (First Version)

All odds/price/overround/fair/value/legacy score, period_values (UNKNOWN timing), final/period/penalty/extra-time/disposition/settlement/live score/result text/unknown-timing trends/details.

## Missingness Policy

- Preserve None in features, add missingness boolean/numeric (1 missing 0 present)
- Do not convert missing to meaningful zero, no imputation during construction
- Genuine zero `0 + missing 0`, missing `None + missing 1`

## Timing Guarantees

- Rule: `history_event_date < current_event_date` enforced via `HistoryIndex._earlier`
- Same-date excluded unless timestamp ordering verified — safe default date-strict
- Future does not affect past, input order invariant, adding future row does not change prior, H2H only prior-date, sport isolation

## Eligibility Rules

Eligible only when: sport known, disposition in `SETTLED_DISPOSITIONS` (SETTLED, SETTLED_CUP, SETTLED_DRAW), no source conflict (assumed False for current schemas), eligible identity, labelable outcome, required identity valid.

Draw-capable: underdog win 1, fav win 0, draw 0, void excluded
Two-way: underdog win 1, fav win 0, draw excluded, void/no-contest excluded (VOID, NO_CONTEST → `excluded_void`)
Equal/missing/non-finite/out-of-range probabilities excluded
Odds availability no effect
Unknown disposition → schema-excluded (`SCHEMA_UNKNOWN_DISPOSITION`)

## Receipt Accounting

`PriceFreeDatasetReceipt` deterministic, counts globally and per sport:

```
raw_input_rows, schema_excluded_rows, valid_loaded_rows, exact_duplicates_collapsed,
canonical_input_rows, eligible_examples, builder_excluded_rows,
input_rows (=canonical), positive_underdog_wins, negative_favorite_wins, negative_draws,
excluded_void, excluded_source_conflict (0 for current schemas, limitation documented),
excluded_equal_probability, excluded_missing_probability,
excluded_non_finite_probability, excluded_out_of_range_probability, excluded_unknown_sport,
excluded_unexpected_two_way_draw, excluded_invalid_winner, excluded_other,
provenance_present, provenance_missing, provenance_invalid,
positive_rate, canonical_date_min/max, eligible_date_min/max, date_min/max alias eligible,
feature_contract_version, label_contract_version, input_digest, per_sport breakdown
```

Invariants:
```
raw = schema_excluded + valid_loaded
valid = exact_duplicates_collapsed + canonical
canonical = eligible + builder_excluded
```

## Deterministic Output

- Stable ordering: (event_date, sport, event_id) sorted, feature-key sorted, receipt per_sport sorted
- Exact duplicate composite keys collapse per deterministic provenance merge policy, conflicting (domain or provenance) fail loudly
- No dict insertion accident, no input order dependence, no auto-rewrite ledgers
- Digest stable after duplicate normalization, stable under reversed input order, provenance merge stable

## Adapter Schemas (Tested)

Supported, documented, no broad alias guessing:

- `data/interim/settled_history.json` — list of SettledEvent dicts (from settlement.py, produces SETTLED, SETTLED_CUP, SETTLED_DRAW, VOID)
- `data/reports/history_*.jsonl.gz` — JSONL gz, each line SettledEvent dict + facets `raw_sha256` (from backfill.py)

Both parsed only via `_validate_settled_dict` requiring `event_id, sport, event_date ISO, participant_1/2, winner_index int 0/1/2 (bool/float/string rejected), disposition in SUPPORTED_DISPOSITIONS (SETTLED, SETTLED_CUP, SETTLED_DRAW, VOID, NO_CONTEST), probability_1/2 key present (None allowed then builder excludes as missing_probability)`. Never infers outcome from score, never invents raw_sha256, rejects unknown `schema_version`. No `host/guest/prob1/prob2/date` aliases.

If files exist but unreadable/corrupt/unknown schema/conflicting duplicates (including provenance conflicts) → fail loudly non-zero.
If no supported files → `NO_SUPPORTED_INPUT_FILES` status, exit 0, full receipt with zeros.

If real files produce `UNKNOWN_SCHEMA`, do not broaden aliases speculatively. Inspect compact schema information—field names, schema version, and a redacted representative record shape—then update the adapter deliberately.

## Verification

```bash
python -m pytest -q  # 323 tests
python -m pytest -q tests/test_price_free.py tests/test_price_free_dataset.py tests/test_dataset_hardening.py tests/test_dataset_audit.py tests/test_dataset_final_integrity.py  # 131 tests
python3 -m py_compile src/slumdog/*.py tests/*.py
python -m pyflakes src/slumdog/dataset.py src/slumdog/dataset_audit.py src/slumdog/underdog.py
git diff --check
```

## Codespace Command (Hardened, Read-Only, No Network, Writes Only Under /tmp)

```bash
python -m slumdog.dataset_audit --root data --receipt /tmp/slumdog_price_free/receipt.json --sample /tmp/slumdog_price_free/examples_sample.json --sample-size 5
```

- No network requests
- Modifies no ledgers
- Writes only under /tmp
- Uses tested adapters for `settled_history.json` and `history_*.jsonl.gz`
- Counts raw/schema/valid/canonical/eligible/builder, malformed rows visible by reason
- Fails loudly on unreadable/corrupt/unknown schema/conflicting duplicates (including provenance conflicts)
- Never defaults missing winner, never silently skips
- Prints summary counts, not all examples
- If no supported ledger files → prints `NO_SUPPORTED_INPUT_FILES`, exits 0, full receipt with zeros
- If files exist but cannot be parsed → fails non-zero
```

