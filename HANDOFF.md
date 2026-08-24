# Slumdog Living Handoff

**Last updated:** 2026-08-24 (UTC) — Milestone 4 price-free historical example builder CURRENT (research dataset foundation, no model training)
**Branch:** `arena/01a033af-slumdog`
**HEAD SHA:** 625888a (Milestone 3) → now + Milestone 4 dataset builder (pending commit)
**Phase:** Milestone 4 — price-free historical example builder (Milestone 0 COMPLETE, Milestone 1 COMPLETE reference audit, Milestone 2 COMPLETE including 2E hardening, Milestone 3 COMPLETE feature timing contract)
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
- `tests/test_price_free.py` (40 tests after 2E) — identity 10, labels via identity 11, hardening 10 (indices from identity, cannot reverse, draw from SPORTS, football draw 0, basketball draw excluded, unknown sport explicit, equal/missing/non-finite/out-of-range survive), contracts 9
- Full suite 232 passed after 2E, training frozen, compatibility boundary explicit (no integration into legacy training.py)

**Contract notes for later:**
- Nested mutability: frozen dataclass with mutable dicts optional_price_context/rejection_counts/source_receipt not deeply immutable, must be defensively copied/immutable/frozen by ledger before immutable receipts (Milestone 6), record only
- Status semantics: STRONG_UNDERDOG must not imply approved probability until scoring/thresholds approved; tests may construct status for serialization, operational code must not emit, reserved fields remain None

## Milestone 3 — COMPLETE (Feature Timing Contract)

**Deliverable:** `docs/FEATURE_TIMING_CONTRACT.md` (CURRENT as governing ALLOWED, Milestone 3 COMPLETE) — doc-only audit, no code change, training FROZEN.

**Required columns:** Feature|Family|Sport|Source file/function|Raw source field|Timing|Evidence|Missing representation|Missing indicator|Odds-dependent|Legacy use|New-path eligibility|Action; Timing PRE_EVENT/RESULT_ONLY/UNKNOWN; New-path ALLOWED/PROHIBITED/PARKED; Rules RESULT_ONLY→prohibited, UNKNOWN→prohibited until verified, odds-dependent→prohibited.

**Priority investigation period_values 10 points:**
- DOM selector .predQ .fj_column span parsers.py:170-173, JSON field period_values
- Listing parser parse_html_events() stores facets["period_values"] with timing PRE_EVENT claim but not proven; settlement parser settlement.py:31-40 same selector for actual period scores
- Contract facets["period_values"] list[list[str]], SettledEvent.period_scores_1/2 separate
- Builders consuming: basketball.py:286-287, american_football.py:265, hockey.py:263, rugby.py:272, handball.py:275, volleyball.py:262, esports.py:257
- Sports: basketball, american_football, hockey, rugby, handball, volleyball, esports
- Predicted/completed/ambiguous: AMBIGUOUS — same selector used for predicted and actual
- Populated for upcoming: UNKNOWN — no census, no retained raw bytes, synthetic tests not proof
- Settlement flow: No direct flow into same key, but same DOM selector reused
- Tests: test_parsers.py:47 synthetic, basketball etc inject values — no timing proof
- Final timing: UNKNOWN → PROHIBITED new-path until Jina probe proves upcoming population

**Conclusion:** period_values remains UNKNOWN and prohibited. Does not need to block all future progress. Stays outside new path.

**Full inventory:** Forebet probs, draw prob, gap/ratio, entropy/dominance, recent form, home/away, win rates, table position, H2H, goals/points, shots, shots on target, blocked/off-target, possession, passes accuracy, attacks, dangerous attacks, event-time, schedule difficulty, weather, venue, stable IDs, cup flags, trend text, double chance, goalscorer, sport-specific physical/stat, every price/odds/overround/fair prob/value-edge, every final/period/penalty/extra-time/disposition/settlement — all inventoried with evidence citations.

**Missingness audit:** None/NaN/0/empty/absent/sentinel and zero fallback classification GENUINE_ZERO/UNKNOWN_ENCODED_AS_ZERO/SAFE_MATHEMATICAL_DEFAULT/UNRESOLVED.

No code change during Milestone 3 audit.

## Milestone 4 — Price-Free Historical Example Builder — CURRENT (Research Dataset Foundation, No Model Training)

**Core principle:** Evaluate every eligible settled event, not only legacy Robber candidates. Flow: settled event → Forebet participant probabilities → price-free favorite/underdog identity → prior-only pre-event evidence → price-free feature snapshot → UNDERDOG_WIN label. Never flows through legacy odds-first candidate, displayed odds, market implied probability, price availability, legacy Robber score, ROI gate.

### Files Changed

- **New module:** `src/slumdog/dataset.py` (~400 lines)
  - `FEATURE_CONTRACT_VERSION = "price-free-v1-minimal-2026-08-24"`, `LABEL_CONTRACT_VERSION = "price-free-v1"`
  - `ALLOWED_FEATURES` = identity 5 + prior 12 = 17 minimal safe set per FEATURE_TIMING_CONTRACT.md ALLOWED
  - `PROHIBITED_KEYS` = odds_1, odds_2, price, overround, fair_market_probability, value_edge, ROI, legacy_robber_score, period_values, score_1/2, period_scores_1/2, extra_time_score, penalty_score, disposition, live_score, result, etc.
  - `PriceFreeUnderdogExample` dataclass frozen: event_id, sport, event_date, favorite_index, underdog_index, favorite_probability, underdog_probability, draw_probability, probability_gap, label 0/1, features dict (ALLOWED only, None preserved), missingness dict (1 missing 0 present), source_url, raw_sha256, feature_contract_version, label_contract_version, exclusion_reason, legacy_provenance_missing, validation no index 0 as underdog, no prohibited keys, deterministic to_dict() sorted keys
  - `PriceFreeDatasetReceipt` dataclass frozen: input_rows, eligible_examples, positive_underdog_wins, negative_favorite_wins, negative_draws, excluded_void, excluded_source_conflict, excluded_equal_probability, excluded_missing_probability, excluded_non_finite_probability, excluded_out_of_range_probability, excluded_unknown_sport, excluded_unexpected_two_way_draw, excluded_invalid_winner, excluded_other, provenance_present, provenance_missing, positive_rate, date_min, date_max, feature_contract_version, label_contract_version, input_digest (sha256 of sorted event_id|sport|date|winner), per_sport breakdown sorted
  - `build_price_free_examples(settled_events, ...)` — deterministic ordering by (event_date, sport, event_id), deduplication exact composite keys collapse conflicting fail loudly (ValueError), HistoryIndex built from all rows but query via _earlier (date < current, same-date excluded), identity via identify_forebet_underdog, label via label_underdog_outcome SPORTS registry, features: identity features + prior history via HistoryIndex.context() + extended prior scoring/draw rates via _prior_scoring_stats() from prior_rows where scores available, missingness policy None preserved + indicator, no imputation, timing guarantees, eligibility rules draw-capable draw=0 two-way draw excluded void excluded equal/missing/non-finite/out-of-range excluded odds availability no effect, receipt accounting input_rows = eligible + exclusions, deterministic output, no dependence on dict insertion or input order, does not rewrite ledgers, training frozen, no integration into legacy training.py

- **New tests:** `tests/test_price_free_dataset.py` (30 tests)
  - Price independence (6): odds present vs absent identical identity, identical features, identical label, identical eligibility, extreme odds do not alter, reversed odds do not alter favorite/underdog
  - Draw semantics (3): football draw eligible label 0, basketball draw excluded, no example ever uses index 0 as underdog
  - Timing (7): future rows cannot affect past, same-date rows cannot affect each other, prior rows affect later rows, input order does not affect output, adding future row does not change prior examples, H2H only uses prior-date meetings, prior history for one sport cannot enter another sport
  - Integrity (7): exact duplicate composite key does not create duplicate, conflicting composite key fails loudly, void excluded, equal probabilities excluded, missing probabilities excluded, receipt accounting balances, unknown sport excluded
  - Serialization (7): example round-trip stable, receipt round-trip stable, feature ordering deterministic, no prohibited price key in serialized output, allowed features only, missingness genuine zero vs missing, feature contract and label versions

- **New doc:** `docs/PRICE_FREE_DATASET_CONTRACT.md` (CURRENT) — concise contract doc with exact feature contract version, label contract version, allowed/prohibited lists, missingness policy, timing rule, receipt accounting, remaining blockers, Codespace command

- **Updated docs:**
  - `docs/STATE.md` — phase advanced to Milestone 4, Milestone 0 COMPLETE, Milestone 1 COMPLETE reference audit, Milestone 2 COMPLETE, Milestone 3 COMPLETE feature timing contract, Current phase Milestone 4 price-free historical example builder, Model training FROZEN, merged work includes dataset.py, blockers updated, links include PRICE_FREE_DATASET_CONTRACT.md as CURRENT
  - `docs/FEATURE_TIMING_CONTRACT.md` — remains CURRENT as governing ALLOWED for Milestone 4, but Milestone 3 marked COMPLETE in STATE.md
  - `docs/README.md` — inventory adds PRICE_FREE_DATASET_CONTRACT.md as CURRENT, FEATURE_TIMING_CONTRACT.md as CURRENT (Milestone 3 COMPLETE still governing), MILESTONE1_AUDIT.md as REFERENCE, notes for Milestone 4
  - `README.md` — Status updated to Milestone 4 CURRENT, training FROZEN, dataset builder produces tested research foundation only

### Example Contract

`PriceFreeUnderdogExample` — event_id, sport, event_date, favorite_index, underdog_index, favorite_probability, underdog_probability, draw_probability, probability_gap, label 0/1, features (ALLOWED only), missingness, source_url, raw_sha256, feature_contract_version, label_contract_version, exclusion_reason, legacy_provenance_missing. Do NOT include odds_1, odds_2, price, overround, fair_market_probability, value_edge, ROI, legacy_robber_score. Eligible example must have label 0 or 1. Excluded events not disguised as eligible rows, recorded in separate audit receipt.

### Feature Contract Version

`price-free-v1-minimal-2026-08-24`

### Label Contract Version

`price-free-v1`

### Allowed Features (Minimal Safe Set)

Per FEATURE_TIMING_CONTRACT.md ALLOWED:
- forebet_favorite_probability, forebet_underdog_probability, forebet_probability_gap, forebet_draw_probability, forebet_draw_probability_missing
- underdog_prior_games, favorite_prior_games, underdog_prior_win_rate, favorite_prior_win_rate, recent_win_rate_gap, h2h_prior_games, h2h_underdog_win_rate, h2h_draw_rate, underdog_prior_draw_rate, favorite_prior_draw_rate, prior_scoring_rate_gap, prior_conceding_rate_gap

Subset reliably supported by HistoryIndex (history.py): HistoryIndex._earlier uses bisect_left on (event_date, "") — same-date excluded, prior_rows(sport, date) returns only earlier dates, context() gives H2HStats and RecentForm, extended scoring/draw rates computed from prior_rows where scores available. If HistoryIndex cannot distinguish no history from zero games: documented limitation — games=0 means no history, win_rate=None with missing=1, games missing=0 (genuine zero prior games). Do not fabricate distinction.

### Prohibited Features (First Version)

Regardless of legacy use: all odds and price fields, overround, fair implied probability, value edge, legacy Robber score, period_values (UNKNOWN), final scores, period scores, penalty scores, extra-time scores, disposition, settlement fields, live score, result text, unknown-timing text trends, unknown-timing detail fields (shots, possession, passes, attacks, etc.) — PARKED, may be added only in later feature-contract version with retained evidence.

### Missingness Policy

For every optional feature: preserve None in example contract, add corresponding boolean/numeric missingness field, do not convert missing evidence to meaningful zero, do not impute during example construction, leave imputation to later approved model pipeline. Example h2h_prior_games=None + missing 1, genuine zero 0 + missing 0. If current HistoryIndex cannot distinguish no history from zero games, document limitation and do not fabricate distinction.

### Timing Guarantees

Rule: history_event_date < current_event_date. Same-date events must not inform one another unless event-level timestamp ordering both available and explicitly verified. Safe default is date-strict exclusion. Tests: future does not affect earlier, same-date does not affect each other, earlier affects later, input order does not change output, adding future row does not change prior examples, H2H only uses prior-date meetings, prior history for one sport cannot enter another sport.

### Eligibility Rules

Eligible only when: sport known, disposition settled and supported, no source conflict, participant probabilities produce eligible identity, outcome can be labeled under sport contract, required identity features valid.

Draw-capable: underdog win→1, favorite win→0, draw→0, void→excluded
Two-way: underdog win→1, favorite win→0, draw→excluded, void/no-contest→excluded
Identity: equal→excluded, missing→excluded, non-finite→excluded, out-of-range→excluded
Odds availability has no effect.

### Receipt Accounting

Every build produces deterministic receipt with required counts globally and per sport: input_rows, eligible_examples, positive_underdog_wins, negative_favorite_wins, negative_draws, excluded_void, excluded_source_conflict, excluded_equal_probability, excluded_missing_probability, excluded_non_finite_probability, excluded_out_of_range_probability, excluded_unknown_sport, excluded_unexpected_two_way_draw, excluded_invalid_winner, provenance_present, provenance_missing, positive_rate, date_min, date_max, feature_contract_version, label_contract_version, input_digest, per_sport breakdown. Invariant: input_rows = eligible_examples + sum(all exclusion categories). Test receipt_accounting_balances proves.

Do not report ROI or price coverage as dataset-readiness metrics.

### Deterministic Output

Stable event ordering, stable feature-key ordering, stable receipt ordering, duplicate exact composite keys collapse only according to existing integrity contract, conflicting composite keys fail loudly, no dependence on dictionary insertion accidents, no dependence on input row order. Do not automatically rewrite source ledgers.

### Price-Independence Tests

Odds present vs absent identical identity, identical features, identical label, identical eligibility, extreme odds do not alter, reversed odds do not alter favorite/underdog — all in test_price_free_dataset.py.

### Exact Test Count

- Full suite: 262 passed (192 legacy + 40 price-free identity/label + 30 price-free dataset)
- Focused: tests/test_price_free.py 40 passed, tests/test_price_free_dataset.py 30 passed
- Collection: 262 tests

### Full Verification

```bash
python -m pytest -q  # 262 passed
python -m pytest -q tests/test_price_free.py tests/test_price_free_dataset.py  # 70 passed
python3 -m py_compile src/slumdog/*.py tests/*.py  # ok
python -m pyflakes src/slumdog/dataset.py src/slumdog/underdog.py  # ok
git diff --check  # ok
git status --short  # ok
```

### Codespace Data Audit Command

Read-only, no network, writes under /tmp, prints receipt summary only — see `docs/PRICE_FREE_DATASET_CONTRACT.md` for exact command. It tries data/interim/settled_history.json and data/reports/history_*.jsonl.gz, builds examples via build_price_free_examples, writes receipt to /tmp/slumdog_price_free/receipt.json and sample to examples_sample.json, prints receipt JSON. Does not alter ledgers, does not fetch network, avoids dumping hundreds of thousands examples into chat. Do not claim real dataset counts until Codespace command runs.

## Open / Parked / Unresolved (Updated for Milestone 4)

**Open (Milestone 4 approval):**
- User review of PRICE_FREE_DATASET_CONTRACT.md: feature contract version, label contract version, allowed/prohibited lists, missingness policy, timing rule, receipt accounting, Codespace command
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
- **Commits:** f4d2946 Milestone 0, 259495c Milestone 0 corrections, 8f38647 Milestone 1 audit, fee5d78 Milestone 2E hardening (40 tests), 625888a Milestone 3 feature timing audit (232 total), + Milestone 4 dataset builder (262 total, pending commit)
- **Mergeability:** No conflicts (doc-only + new modules + tests, no legacy code changes)
- **User authorization:** Milestone 0 COMPLETE, Milestone 1 COMPLETE reference audit, Milestone 2 COMPLETE including 2E, Milestone 3 COMPLETE feature timing contract, Milestone 4 CURRENT pending approval — do not merge, do not change feature vectors/thresholds/ranking/model approval/daily production until approved, do not train model

## Evidence Language Compliance

- Verified from code: file paths, function names, line numbers, grep results, test names, DOM selectors, facet timing maps, feature contract versions, receipt fields
- Verified from executed probe: pytest 262 passed, py_compile ok, pyflakes ok, diff-check ok
- Plausible but unverified: detail facet timing — marked PARKED/UNKNOWN, not claimed as PRE_EVENT proven
- Unresolved conflict: retained competing facts without silently choosing one — period_values ambiguous marked UNKNOWN PROHIBITED, does not block future progress

## After Merge: Next Session Starts Here (Updated for Milestone 5)

Read AGENTS.md first, then README.md, then docs/STATE.md, then HANDOFF.md, then docs/PRICE_FREE_DATASET_CONTRACT.md, then docs/FEATURE_TIMING_CONTRACT.md, then docs/FOREBET_DEPTH_AUDIT.md, then src/slumdog/underdog.py, src/slumdog/dataset.py, then tests/test_price_free.py, tests/test_price_free_dataset.py, then relevant source/tests.

**Exact next task (Milestone 5 — transparent baselines with walk-forward validation):**

After Milestone 4 approval, implement transparent baselines:
- Forebet underdog probability baseline
- Probability gap baseline
- Recent-form differential baseline
- Ma Golide heuristic baseline (price-free version)
- Simple interpretable model (e.g., logistic regression with ALLOWED features only) with walk-forward validation, never random splits
- Metrics: top_1_daily_hit_rate, top_3_daily_any_hit_rate, days_with_at_least_one_selected_winner, selected_candidates_per_day, no_pick_day_rate, candidate_precision together, not ROI-primary
- Training remains frozen until dataset contract approved — now approved, but model training still needs explicit unlock? Actually Milestone 5 will require MODEL_TRAINING_ALLOWED remains False until user approves baselines? Per original plan, first implement transparent baselines with walk-forward validation, never random splits, after dataset/target/timing/validation contract approved. So Milestone 5 can proceed after Milestone 4 approval.

**Required evidence for next session:**
- docs/PRICE_FREE_DATASET_CONTRACT.md approved (feature contract version, label contract version, allowed/prohibited lists, missingness policy, timing rule, receipt accounting, Codespace command)
- docs/STATE.md Milestone 0 COMPLETE, Milestone 1 COMPLETE reference audit, Milestone 2 COMPLETE, Milestone 3 COMPLETE, Current phase Milestone 4, Model training FROZEN
- src/slumdog/dataset.py builder with chronological evidence, minimal safe feature set ALLOWED only, missingness policy, timing guarantees, eligibility rules, receipt accounting, price-independence, 30 tests passing, 262 total
- Training remains frozen until baselines approved — no model registry approval, no production pipeline change until Milestone 5 approved

**Safe commands:**
- git status --short, git diff --check, python3 -m py_compile ..., pyflakes, pytest -q, grep -Rni ..., read-only audits

**Prohibited:**
- Do not run American football odds probe before ~2026-09-10
- Do not fetch aggressively; at most 6 workers, 62s pauses
- Do not train models (frozen) — no research-override without explicit user unlock until Milestone 5 approved (even then, only transparent baselines with walk-forward, never random splits)
- Do not change production pipeline output, shortlist thresholds, emit operational STRONG_UNDERDOG until Milestone 5 approved
- Do not auto-rewrite legacy ledgers
- Do not infer undocumented market semantics

**Unresolved facts to preserve:**
- Four cross-date identical pairs, hockey 278977 conflict, MMA 11 void+priced, absent raw bytes, DC token 21, scorer semantic uncertainty, detail timing unverified (period_values = UNKNOWN PROHIBITED, does not block future progress), missingness zero-fill classification
