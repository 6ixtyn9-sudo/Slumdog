# Slumdog Living Handoff

**Last updated:** 2026-08-24 (UTC) — Milestone 4: COMPLETE — pending real-data receipt execution, Current phase: Milestone 5 readiness review
**Branch:** `arena/01a033af-slumdog`
**HEAD SHA:** 625888a (Milestone 3) → now + Milestone 4 architecture + 4E hardening (pending commit)
**Phase:** Milestone 4: COMPLETE — pending real-data receipt execution (Milestone 0 COMPLETE, Milestone 1 COMPLETE reference audit, Milestone 2 COMPLETE including 2E hardening, Milestone 3 COMPLETE feature timing contract, Milestone 4 architecture + 4E hardening COMPLETE)
**Mission:** Slumdog identifies a small daily shortlist of participants that Forebet considers underdogs but whose available pre-event evidence indicates a credible outright-win upset.
**PR:** #6 https://github.com/6ixtyn9-sudo/Slumdog/pull/6 — OPEN, do not merge until user authorizes after Milestone 4 approval
**Training:** FROZEN (`feature_contracts.py: MODEL_TRAINING_ALLOWED=False`)

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

## Milestone 4 — COMPLETE (Architecture + 4E Hardening)

**Core principle:** Evaluate every eligible settled event, not only legacy Robber candidates. Flow: settled event → Forebet participant probabilities → price-free favorite/underdog identity → prior-only pre-event evidence → price-free feature snapshot → UNDERDOG_WIN label. Never through legacy odds-first candidate, displayed odds, market implied probability, price availability, legacy Robber score, ROI gate.

### Files Changed

- **Hardened module:** `src/slumdog/dataset.py` (~900 lines, 4E)
  - `FEATURE_CONTRACT_VERSION = "price-free-v1-minimal-2026-08-24"`, `LABEL_CONTRACT_VERSION = "price-free-v1"`
  - `ALLOWED_FEATURES` = identity 5 + prior 12 = 17 minimal safe set per FEATURE_TIMING_CONTRACT.md ALLOWED
  - `PROHIBITED_KEYS` = odds_1, odds_2, price, overround, fair_market_probability, value_edge, ROI, legacy_robber_score, period_values, score_1/2, etc.
  - `PriceFreeUnderdogExample` frozen: event_id, sport, event_date, favorite_index, underdog_index, favorite_probability, underdog_probability, draw_probability, probability_gap, label 0/1, features (ALLOWED only, None preserved), missingness (1 missing 0 present), source_url, raw_sha256, feature_contract_version, label_contract_version, exclusion_reason, legacy_provenance_missing, validation no index 0 as underdog, no prohibited keys, deterministic to_dict() sorted keys
  - `PriceFreeDatasetReceipt` frozen with raw vs canonical accounting: raw_input_rows, schema_excluded_rows, valid_loaded_rows, exact_duplicates_collapsed, canonical_input_rows, eligible_examples, builder_excluded_rows, input_rows alias canonical, positive_underdog_wins, negative_favorite_wins, negative_draws, excluded_void, excluded_source_conflict, excluded_equal_probability, excluded_missing_probability, excluded_non_finite_probability, excluded_out_of_range_probability, excluded_unknown_sport, excluded_unexpected_two_way_draw, excluded_invalid_winner, excluded_other, provenance_present, provenance_missing, provenance_invalid, positive_rate, canonical_date_min/max, eligible_date_min/max, date_min/max alias eligible, feature_contract_version, label_contract_version, input_digest, per_sport breakdown sorted
  - Invariants: raw = schema_excluded + valid_loaded, valid = exact_duplicates_collapsed + canonical, canonical = eligible + builder_excluded
  - `_validate_settled_dict` — no fabricated defaults: missing winner → SCHEMA_MISSING_WINNER_INDEX never defaults to 1, missing disposition → SCHEMA_MISSING_DISPOSITION never defaults to SETTLED, missing date/sport/participants/probabilities counted as schema exclusion, never infers outcome from score, never invents raw_sha256, rejects unknown schema_version, only documented schema fields accepted
  - `SchemaLoadResult` — raw_input_rows, schema_excluded_rows, valid_loaded_rows, schema_exclusion_reasons Counter, file_errors — malformed rows counted by reason not silently skipped
  - `_canonical_event_repr` — versioned JSON of event_id, sport, event_date, participant_1, participant_2, winner_index, disposition, probability_1, probability_2, draw_probability, score_1/2 (used by prior-history), league, source_url, raw_sha256 from facets, version canonical-v1; excludes odds_1/2 deliberately documented; stable under input reordering via sorted (event_date,sport,event_id)
  - `_compute_input_digest` — strengthened digest hashing canonical repr, stable under reordering
  - `_is_valid_sha256` — 64 hex chars required for provenance_present else missing/invalid
  - `build_price_free_examples` — deterministic ordering, deduplication composite key (sport,event_id,event_date) matching settlement.py seen key, same event_id in different sports does not collapse, conflicting content (winner/probabilities/participants/scores/disposition/league) fails loudly ValueError, exact duplicate collapses even if source_url/raw_sha256 differs per existing integrity policy, provenance change handled as exact collapse
  - `build_dataset_with_raw_accounting` — combines schema + builder stages with invariants
  - `load_settled_events_from_dicts` — tested adapter, no broad alias guessing host/guest/prob1/prob2

- **New module:** `src/slumdog/dataset_audit.py` entry point `python -m slumdog.dataset_audit --root data --receipt /tmp/slumdog_price_free/receipt.json --sample /tmp/slumdog_price_free/examples_sample.json --sample-size 5`
  - No network, modifies no ledgers, writes only under /tmp, uses tested adapters for settled_history.json (list of SettledEvent dicts) and history_*.jsonl.gz (JSONL gz with facets raw_sha256)
  - Explicit NO_SUPPORTED_INPUT_FILES status (exit 0) vs fail on unreadable/corrupt/unknown schema/conflicting duplicates (exit 1)
  - Malformed rows visible by reason, never defaults missing winner, never silently skips, prints summary counts only

- **New tests:** 
  - `tests/test_price_free_dataset.py` (30 tests) — price independence (odds present vs absent identical identity/features/label/eligibility, extreme odds, reversed odds), draw semantics (football draw eligible label 0, basketball draw excluded, no index 0 as underdog), timing (future cannot affect past, same-date cannot affect each other, prior affects later, input order does not affect output, adding future row does not change prior, H2H only prior-date, sport isolation), integrity (exact duplicate collapse, conflicting fail loudly, void excluded, equal excluded, missing excluded, receipt accounting balances, unknown sport excluded), serialization (round-trip stable, receipt round-trip stable, feature ordering deterministic, no prohibited price key, allowed features only, missingness genuine zero vs missing, contract versions)
  - `tests/test_dataset_hardening.py` (34 tests) — missing winner excluded never participant 1, missing disposition excluded, malformed row counted, unreadable/corrupt JSON fails, corrupt gzip fails, unknown schema version fails, raw/canonical invariants, exact duplicate accounting, conflicting duplicate failure, digest changes when probability/winner/score changes, digest stable on reordering, odds not changing digest, malformed SHA counted, all-excluded date semantics, duplicate identity same event_id different sports, same participants/date different leagues, provenance change handling, no network imports
  - `tests/test_dataset_audit.py` (9 tests) — no-supported-input status, valid settled_history.json, valid history_jsonl_gz, unreadable JSON fails, corrupt gzip fails, unknown schema fails, conflicting duplicates fail, non-/tmp output rejected, malformed rows visible

- **Updated doc:** `docs/PRICE_FREE_DATASET_CONTRACT.md` (CURRENT, hardened, concise) — exact versions, allowed/prohibited, missingness, timing, receipt accounting with invariants, adapter schemas documented, Codespace command hardened `python -m slumdog.dataset_audit --root data --receipt /tmp/slumdog_price_free/receipt.json --sample /tmp/slumdog_price_free/examples_sample.json --sample-size 5`

- **Updated docs:**
  - `docs/STATE.md` — Milestone 4: COMPLETE — pending real-data receipt execution, Current phase: Milestone 5 readiness review, 4E hardening details, verification 305 passed
  - `docs/README.md` — inventory updated to Milestone 4 COMPLETE
  - `README.md` — Status updated to Milestone 4 COMPLETE pending real-data receipt

### Feature Contract Version

`price-free-v1-minimal-2026-08-24`

### Label Contract Version

`price-free-v1`

### Receipt Accounting Invariants

```
raw = schema_excluded + valid_loaded
valid = exact_duplicates_collapsed + canonical
canonical = eligible + builder_excluded
```

### Verification

```bash
python -m pytest -q  # 305 passed
python -m pytest -q tests/test_price_free.py tests/test_price_free_dataset.py tests/test_dataset_hardening.py tests/test_dataset_audit.py  # 113 passed
python3 -m py_compile src/slumdog/*.py tests/*.py  # ok
python -m pyflakes src/slumdog/dataset.py src/slumdog/dataset_audit.py src/slumdog/underdog.py  # ok
git diff --check  # ok
python -m slumdog.dataset_audit --root data --receipt /tmp/slumdog_price_free/receipt.json --sample /tmp/slumdog_price_free/examples_sample.json --sample-size 5  # NO_SUPPORTED_INPUT_FILES when no ledgers, summary only
```

### Codespace Data Audit Command (Hardened)

```bash
python -m slumdog.dataset_audit --root data --receipt /tmp/slumdog_price_free/receipt.json --sample /tmp/slumdog_price_free/examples_sample.json --sample-size 5
```

Read-only, no network, writes under /tmp, prints receipt summary only, fails loudly on unreadable/corrupt/unknown schema/conflicting duplicates, never defaults missing winner, never silently skips.

## Open / Parked / Unresolved (Updated for Milestone 4 COMPLETE)

**Open (Milestone 4 approval pending real-data receipt):**
- Execute real-data receipt via hardened audit command in Codespace with actual ledgers under data/interim/settled_history.json or data/reports/history_*.jsonl.gz
- User review of PRICE_FREE_DATASET_CONTRACT.md hardened: feature contract version, label contract version, allowed/prohibited lists, missingness policy, timing rule, receipt accounting with invariants, adapter schemas, hardened command
- Approval to proceed to Milestone 5 transparent baselines with walk-forward validation

**Parked:**
- American football odds probe scripts/probe_american_football_odds.py — do not run before ~2026-09-10
- Complex ensembles — baselines first after unlock
- Esoccer separate audit
- Dropped football getrs.php keys audit
- Sparse hockey/rugby/volleyball/handball pricing re-check on in-season top-league dates
- Auto-rewrite/compact legacy ledgers — prohibited without explicit authorization
- Detail facets still UNKNOWN/PARKED per FEATURE_TIMING_CONTRACT.md — need Jina probes for shots, passes, possession, attacks, etc. before adding to feature contract v2
- Model training, ranking thresholds, daily production — out of scope for Milestone 4, remain frozen

**Unresolved Evidence (preserved):**
- 4 cross-date identical pairs: basketball:198045, 198046, football:2041406, volleyball:96303
- Hockey 278977 conflict 1-6 vs 0-4
- MMA 11 void+priced rows
- Absent raw bytes for 7 suspicious dates
- Football DC token 21, scorer subtype unknown
- Football 963-date backfill gap quantification + replay feasibility
- Detail facet timing unverified (shots, passes, possession, attacks, next-fixture difficulty) — all PARKED in FEATURE_TIMING_CONTRACT.md, need Jina-HTML proof
- period_values timing UNKNOWN — needs live Jina probe for upcoming basketball date per FEATURE_TIMING_CONTRACT.md 10-point investigation, stays PROHIBITED outside new path, does not block future progress

## PR State

- **Branch:** `arena/01a033af-slumdog`
- **Base:** `main` @ `2e3daa40b60ed520a0bcb2f178ef4219fad4d026`
- **PR:** #6 https://github.com/6ixtyn9-sudo/Slumdog/pull/6 — OPEN, do not merge until user authorizes after Milestone 4 approval
- **Commits:** f4d2946 Milestone 0, 259495c Milestone 0 corrections, 8f38647 Milestone 1 audit, fee5d78 Milestone 2E hardening (40 tests), 625888a Milestone 3 feature timing audit (232 total), + Milestone 4 architecture (262 total) + Milestone 4E hardening (305 total, pending commit)
- **Mergeability:** No conflicts (doc-only + new modules + tests, no legacy code changes)
- **User authorization:** Milestone 0 COMPLETE, Milestone 1 COMPLETE reference audit, Milestone 2 COMPLETE including 2E, Milestone 3 COMPLETE feature timing contract, Milestone 4: COMPLETE — pending real-data receipt execution — do not merge, do not change feature vectors/thresholds/ranking/model approval/daily production until approved, do not train model

## Evidence Language Compliance

- Verified from code: file paths, function names, line numbers, grep results, test names, DOM selectors, facet timing maps, feature contract versions, receipt fields, invariants raw=schema+valid etc., composite key (sport,event_id,event_date), canonical repr fields, digest stability, provenance validation, date semantics
- Verified from executed probe: pytest 305 passed, py_compile ok, pyflakes ok, diff-check ok, dataset_audit NO_SUPPORTED_INPUT_FILES when no ledgers, summary counts printed, fails on corrupt/unknown schema/conflicting duplicates
- Plausible but unverified: detail facet timing — marked PARKED/UNKNOWN, not claimed as PRE_EVENT proven
- Unresolved conflict: retained competing facts without silently choosing one — period_values ambiguous marked UNKNOWN PROHIBITED, does not block future progress

## After Merge: Next Session Starts Here (Updated for Milestone 5)

Read AGENTS.md first, then README.md, then docs/STATE.md, then HANDOFF.md, then docs/PRICE_FREE_DATASET_CONTRACT.md, then docs/FEATURE_TIMING_CONTRACT.md, then docs/FOREBET_DEPTH_AUDIT.md, then src/slumdog/underdog.py, src/slumdog/dataset.py, src/slumdog/dataset_audit.py, then tests/test_price_free.py, tests/test_price_free_dataset.py, tests/test_dataset_hardening.py, tests/test_dataset_audit.py, then relevant source/tests.

**Exact next task (Milestone 5 — transparent baselines with walk-forward validation):**

After Milestone 4 approval and real-data receipt execution, implement transparent baselines:
- Forebet underdog probability baseline
- Probability gap baseline
- Recent-form differential baseline
- Ma Golide heuristic baseline (price-free version)
- Simple interpretable model (e.g., logistic regression with ALLOWED features only) with walk-forward validation, never random splits
- Metrics: top_1_daily_hit_rate, top_3_daily_any_hit_rate, days_with_at_least_one_selected_winner, selected_candidates_per_day, no_pick_day_rate, candidate_precision together, not ROI-primary
- Training remains frozen until dataset contract approved — now approved pending receipt, but model training still needs explicit unlock for Milestone 5 baselines per original plan

**Required evidence for next session:**
- docs/PRICE_FREE_DATASET_CONTRACT.md approved (feature contract version, label contract version, allowed/prohibited lists, missingness policy, timing rule, receipt accounting with invariants, adapter schemas, hardened command)
- docs/STATE.md Milestone 0 COMPLETE, Milestone 1 COMPLETE reference audit, Milestone 2 COMPLETE, Milestone 3 COMPLETE, Milestone 4: COMPLETE — pending real-data receipt execution, Current phase: Milestone 5 readiness review, Model training FROZEN
- src/slumdog/dataset.py hardened with no unsafe defaults, no silent swallowing, raw vs canonical accounting, strengthened digest, duplicate identity validated, provenance validated, date semantics explicit, 305 tests passing
- src/slumdog/dataset_audit.py entry point with hardened command, no network, writes only under /tmp, tested adapters, explicit NO_SUPPORTED_INPUT_FILES vs fail loudly
- Real-data receipt executed in Codespace: python -m slumdog.dataset_audit --root data --receipt /tmp/slumdog_price_free/receipt.json --sample /tmp/slumdog_price_free/examples_sample.json --sample-size 5

**Safe commands:**
- git status --short, git diff --check, python3 -m py_compile ..., pyflakes, pytest -q, grep -Rni ..., read-only audits
- python -m slumdog.dataset_audit --root data --receipt /tmp/slumdog_price_free/receipt.json --sample /tmp/slumdog_price_free/examples_sample.json --sample-size 5

**Prohibited:**
- Do not run American football odds probe before ~2026-09-10
- Do not fetch aggressively; at most 6 workers, 62s pauses
- Do not train models (frozen) — no research-override without explicit user unlock until Milestone 5 approved (even then, only transparent baselines with walk-forward, never random splits)
- Do not change production pipeline output, shortlist thresholds, emit operational STRONG_UNDERDOG until Milestone 5 approved
- Do not auto-rewrite legacy ledgers
- Do not infer undocumented market semantics

**Unresolved facts to preserve:**
- Four cross-date identical pairs, hockey 278977 conflict, MMA 11 void+priced, absent raw bytes, DC token 21, scorer semantic uncertainty, detail timing unverified (period_values = UNKNOWN PROHIBITED, does not block future progress), missingness zero-fill classification
