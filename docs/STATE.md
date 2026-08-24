# Slumdog State — Canonical Current Truth

**Last verified:** 2026-08-24 (UTC)
**Branch:** `arena/01a033af-slumdog` (delivery), `main` is only permanent branch
**Doc canonical path:** `docs/STATE.md` (moved from root `STATE.md` via `git mv`)

## Permanent Product Mission

> **Slumdog identifies a small daily shortlist of participants that Forebet considers underdogs but whose available pre-event evidence indicates a credible outright-win upset.**

Invariants (from `AGENTS.md`):
- Target is `UNDERDOG_WIN` outright win only; draws never count as success.
- Slumdog never selects draws; in draw-capable sports, draw = failed `UNDERDOG_WIN`.
- Odds are optional metadata only — not required input, not a model feature, not eligibility gate.
- Missing odds must not lower confidence.
- Never force daily pick; `NO_STRONG_UNDERDOG` is valid.
- Never promise profit, guaranteed wins, life-changing results.
- Training frozen until user approves dataset, target, timing, validation contract.

## Current Phase

**Current phase: Milestone 4 — price-free historical example builder (research dataset foundation, no model training)**
**Milestone 0 documentation governance: COMPLETE**
**Milestone 1 audit: COMPLETE — reference audit**
**Milestone 2 price-free identity, label, contracts: COMPLETE (including 2E hardening)**
**Milestone 3 feature timing contract: COMPLETE (period_values UNKNOWN PROHIBITED)**
**Model training: FROZEN**

Milestone 0 completed: STATE.md → docs/STATE.md move, AGENTS.md constitution, docs audit, README rewrite to price-free mission, freshness lock, 5 corrections verified.

Milestone 1 completed (read-only audit, now REFERENCE): 10 gaps, staged plan, 8 refinements, central problem odds-first identity/scoring/features.

Milestone 2 COMPLETE (including 2E hardening):
- 2A: Pure Forebet identity `identify_forebet_underdog(prob1, prob2, draw_prob)` — validates present/finite/[0,1], higher=favorite, draw does not determine identity, equal→EQUAL_PROBABILITY, missing→MISSING, non-finite→NON_FINITE, out-of-range→OUT_OF_RANGE
- 2B: Historical label foundation
- 2C: Price-free contracts `StrongUnderdogAssessment`, `DailyUnderdogShortlist`
- 2D+2E: 40 tests, hardening — identity-bound public API `label_underdog_outcome(sport, identity, ...)`, derives draw_possible from SPORTS registry, preserves exact reasons, UNKNOWN_SPORT explicit, caller cannot reverse or override

Milestone 3 COMPLETE (read-only, doc-only):
- `docs/FEATURE_TIMING_CONTRACT.md` CURRENT → now REFERENCE? Actually after Milestone 3 approval, it becomes REFERENCE, but per task after approval documentation should state Milestone 3 COMPLETE — feature timing contract. For Milestone 4, FEATURE_TIMING_CONTRACT.md remains CURRENT as governing contract, but STATE says Milestone 3 COMPLETE.
- period_values 10-point investigation: DOM .predQ .fj_column, listing parser parsers.py:170-173, contract facets["period_values"], builders basketball.py:287 etc, sports used, ambiguous predicted vs completed, upcoming population unknown, settlement flow into period_scores_1/2 not same key, tests synthetic, final timing UNKNOWN PROHIBITED — does not block future progress, stays outside new path
- Full feature inventory with required columns, missingness audit GENUINE_ZERO/UNKNOWN_ENCODED_AS_ZERO/SAFE_MATHEMATICAL_DEFAULT/UNRESOLVED, rules RESULT_ONLY→prohibited, UNKNOWN→prohibited, odds-dependent→prohibited

Milestone 4 CURRENT (approved, no model training): Build leak-safe, price-free historical example foundation — every eligible settled event, not only legacy Robber candidates, flow settled→probs→identity→prior-only evidence→feature snapshot→label, never through odds.

## Merged Work (main @ 2e3daa4) + PR #6 Branch (arena/01a033af-slumdog)

- PR #4: football truncated relay fix, H2H fabrication guard, MMA duplicate dedup, no-odds documentation
- PR #5: DOM-scoped football double-chance + scorer market facets
- Milestone 0 (branch): STATE.md → docs/STATE.md git mv, AGENTS.md constitution, docs/STATE.md rewrite, README rewrite, docs/README.md index, 5 corrections verified, 192 tests passed
- Milestone 1 (branch): `docs/MILESTONE1_AUDIT.md` read-only audit, approved with 8 refinements, now REFERENCE
- Milestone 2 (branch): `src/slumdog/underdog.py` new price-free module — identity, label with 2E hardening, contracts, 40 tests, full suite 232 passed
- Milestone 2E (branch, commit fee5d78): label hardening — identity-bound API, SPORTS registry draw capability, exact reason preservation, 10 hardening tests
- Milestone 3 (branch, commit 625888a): `docs/FEATURE_TIMING_CONTRACT.md` doc-only audit — period_values UNKNOWN PROHIBITED, full inventory, missingness audit, no code change, 232 tests passed
- Milestone 4 (branch, current): `src/slumdog/dataset.py` new — `PriceFreeUnderdogExample` contract (event_id, sport, event_date, favorite_index, underdog_index, favorite_probability, underdog_probability, draw_probability, probability_gap, label 0/1, features, missingness, source_url, raw_sha256, feature_contract_version, label_contract_version, exclusion_reason, legacy_provenance_missing), `PriceFreeDatasetReceipt` with required counts globally and per sport, builder `build_price_free_examples` with chronological evidence history_event_date < current_event_date, same-date excluded, deterministic ordering, duplicate handling exact collapse conflicting fail loudly, minimal safe feature set ALLOWED only (forebet_favorite_probability, forebet_underdog_probability, forebet_probability_gap, forebet_draw_probability, forebet_draw_probability_missing, underdog_prior_games, favorite_prior_games, underdog_prior_win_rate, favorite_prior_win_rate, recent_win_rate_gap, h2h_prior_games, h2h_underdog_win_rate, h2h_draw_rate, underdog_prior_draw_rate, favorite_prior_draw_rate, prior_scoring_rate_gap, prior_conceding_rate_gap), missingness policy None preserved + boolean indicator, no imputation, timing guarantees, eligibility rules draw-capable draw=0 two-way draw excluded void excluded equal/missing/non-finite/out-of-range excluded odds availability no effect, receipt accounting input_rows = eligible + exclusions, price-independence tests, 30 new tests in `tests/test_price_free_dataset.py`, full suite 262 passed, training frozen, no integration into legacy training.py yet
- Core pipeline (unchanged legacy): immutable captures, per-sport history ledgers, depth-sweep census, forebet.py relay handling, timing classes, sport-specific settlement contracts
- Feature contracts in `feature_contracts.py` (aspirational, not training inputs, MODEL_TRAINING_ALLOWED=False)
- Legacy Ma Golide Robber reproducer in `magolide.py` (odds-first cascade, legacy, preserved)

## Active Blockers

**Milestone 0: COMPLETE. Milestone 1: COMPLETE reference audit. Milestone 2: COMPLETE. Milestone 3: COMPLETE. Current phase: Milestone 4.**

Real blockers (updated for Milestone 4):

1. **Underdog machinery still odds-first in legacy path** — `contracts.py`/`magolide.py`/`feature_contracts.py` and sport detectors encode odds-first cascade; new price-free path `underdog.py` + `dataset.py` implemented but legacy not yet replaced (preserved per migration strategy). Needs integration after Milestone 4 approval.
2. **Label contract hardened and complete, dataset builder complete, integration pending** — public label and dataset builder derive draw capability from SPORTS registry, preserve exact reasons, chronological evidence proven, price independence proven. Needs integration into training orchestration and settlement verification in later milestone (training.py still uses legacy odds-first candidate — intentionally not integrated per compatibility boundary).
3. **Detail facets still UNKNOWN/PARKED per FEATURE_TIMING_CONTRACT.md** — need Jina probes for shots, passes, possession, attacks, next-fixture difficulty, surface splits, MMA tale-of-the-tape, double-chance prob/pick, goalscorer prob/name before adding to feature contract v2. First version proves pipeline with smallest safe set.
4. **period_values remains UNKNOWN PROHIBITED** — same selector used pre-event and settlement, no retained bytes prove upcoming population. Does not block future progress, stays outside new path per Milestone 3 conclusion.
5. **Model validation and daily shortlist behavior not yet approved** — walk-forward exists, but ROI gate still in legacy validation, required daily metrics not yet implemented; shortlist cap 1–3, explicit NO_STRONG_UNDERDOG daily status, assessment vs selection separation defined in new contracts but not yet integrated.
6. **Training remains frozen** — `MODEL_TRAINING_ALLOWED=False`, explicit user unlock required after dataset/target/timing/validation contract approved. No feature-vector threshold, ranking, model approval, or daily production changes until Milestone 4 approved. Milestone 4 produces tested research dataset foundation only.
7. **Nested mutability and status semantics contract notes for later** — frozen dataclass with mutable dicts optional_price_context/rejection_counts/source_receipt not deeply immutable, must be defensively copied/immutable/frozen by ledger before immutable receipts (Milestone 6), record only; STRONG_UNDERDOG must not imply approved probability until scoring/thresholds approved.

Data limitations below are parked observations, not candidate-readiness gates.

## Current Model-Training Status

**FROZEN.** From `feature_contracts.py`: `MODEL_TRAINING_ALLOWED = False`.

- 14-day preliminary experiment discarded.
- No retraining allowed until each sport has: listing/detail facet contract, historical depth receipt, timing classification, settlement coverage, price-availability profile, AND user approves dataset/target/timing/validation contract.
- When unlocked, first implement transparent baselines with walk-forward validation, never random splits.
- Milestone 4 is research dataset foundation only — no LogisticRegression, no model training, no production integration.

## Current Data Limitations (Reference Observations, Not Readiness Gates)

- **Football backfill gap:** 963 dates failed on runner (401 relay) — Markdown reader path added but needs next pipeline probe.
- **Relay egress:** Direct forebet.com from Azure behind Cloudflare JS challenge (403). Jina relay Markdown mode works locally but GH Actions IP 401.
- **Price coverage — reference evidence only, NOT a candidate-readiness gate:** Snapshot one active-season date per sport. Football 77.3%, Basketball 60.6%, Tennis 96%, Baseball 68.4%, Cricket 0% (6643 settled rows zero priced), Handball 0% but actually 2-price American format fixed, Hockey 0% on sampled date, Rugby/Volleyball 0%, American football 0% (7447 archived rows zero priced, pending 2026-09-10 probe), MMA 153 priced / 757 unique, Esoccer rolling board no reliable dated archive. Sparse/missing prices do NOT block strong-underdog generation — odds are optional context only.
- **Detail coverage:** Three-page sample per sport in `FOREBET_DETAIL_COVERAGE.json` justified parser families, but full census missingness not yet measured from `depth-sweep`.
- **Legacy ledger integrity:** 279 byte-identical extra rows across 278 repeated same-date keys; 4 cross-date identical pairs after removing event_date; hockey 278977 same-key conflicting results; MMA 11 rows both void+priced; all 759 legacy MMA rows lacked raw_sha256/captured_at (predates provenance retention).
- **Missing raw bytes:** 7 sampled suspicious dates had manifest hashes but no local data/raw files.
- **Football markets:** 5 distinct JSON endpoints (uo, bts, ht, ah, cards) — one req/date each; htft byte-identical to ht; corners/doublechance/goalscorer echo 1X2 JSON, so price only from detail HTML.

## Next Approved Milestone

**Milestone 0: COMPLETE**
**Milestone 1: COMPLETE — reference audit**
**Milestone 2: COMPLETE — price-free identity, label and contracts (including 2E hardening)**
**Milestone 3: COMPLETE — feature timing contract (period_values UNKNOWN PROHIBITED, does not block future progress)**
**Current phase: Milestone 4 — price-free historical example builder (research dataset foundation, no model training)**
**Model training: FROZEN**

Milestone 4 deliverables (this branch, current):
- `src/slumdog/dataset.py` — `PriceFreeUnderdogExample` contract, `PriceFreeDatasetReceipt`, builder `build_price_free_examples` with chronological evidence history_event_date < current_event_date, same-date excluded, deterministic ordering, duplicate handling, minimal safe feature set ALLOWED only, missingness policy None preserved + indicator, timing guarantees, eligibility rules, receipt accounting, price-independence, training frozen, no integration into legacy training.py yet
- `tests/test_price_free_dataset.py` — 30 tests covering price independence (odds present vs absent identical identity/features/label/eligibility, extreme odds, reversed odds), draw semantics (football draw eligible label 0, basketball draw excluded, no index 0 as underdog), timing (future cannot affect past, same-date cannot affect each other, prior affects later, input order does not affect output, adding future row does not change prior, H2H only prior-date, sport isolation), integrity (exact duplicate collapse, conflicting fail loudly, void excluded, equal excluded, missing excluded, receipt accounting balances, unknown sport excluded), serialization (round-trip stable, receipt round-trip stable, feature ordering deterministic, no prohibited price key, allowed features only, missingness genuine zero vs missing, contract versions)
- `docs/PRICE_FREE_DATASET_CONTRACT.md` — concise contract doc with exact feature contract version `price-free-v1-minimal-2026-08-24`, label contract version `price-free-v1`, allowed/prohibited lists, missingness policy, timing rule, receipt accounting, remaining blockers, Codespace command

Next after Milestone 4 approval: Milestone 5 — transparent baselines (Forebet underdog prob, gap, recent-form differential, Ma Golide heuristic, simple interpretable model) with walk-forward validation, never random splits, after user approves dataset contract.

## Parked Work

- American football odds probe (`scripts/probe_american_football_odds.py`) — do not run before ~2026-09-10
- Complex ensembles — baselines first after unlock
- Esoccer separate audit
- Dropped football `getrs.php` keys audit
- Sparse hockey/rugby/volleyball/handball pricing re-check on in-season top-league dates
- Auto-rewrite/compact legacy ledgers — prohibited without explicit authorization

## Unresolved Evidence

- 4 cross-date normalized-identical pairs (basketball:198045/198046, football:2041406, volleyball:96303) — rescheduled vs date-boundary? Unresolved, not auto-deleted.
- Hockey `278977` (2023-08-20) conflicting results — needs source bytes, neither selectable honestly.
- MMA 11 void+priced rows — plausible pre-scratch but cannot verify without raw captures.
- Absent sampled raw bytes for 7 suspicious dates — manifest retains URL/byte-count/SHA256 only.
- Football DC token `21` observed — raw, unnormalized, preserved.
- Scorer market subtype unknown; display order preserved but not ranking; empty rows emit nothing; sample fill-rate unknown.
- Football 963-date backfill gap quantification + replay feasibility from retained captures.
- HT/FT as single combined price, not 9-cell matrix — verified distinctness but need DOM selector proof.
- period_values timing UNKNOWN — needs live Jina probe for upcoming basketball date, per FEATURE_TIMING_CONTRACT.md 10-point investigation, stays PROHIBITED outside new path, does not block future progress.

## Links to Deeper Documents

- `AGENTS.md` — permanent mission + operating constitution
- `README.md` — product overview (price-free mission, operational commands)
- `HANDOFF.md` — session continuation record
- `docs/STATE.md` — this file, canonical current truth
- `docs/README.md` — doc index
- `docs/MILESTONE1_AUDIT.md` — price-free machinery audit, now REFERENCE
- `docs/FEATURE_TIMING_CONTRACT.md` — feature timing and leakage audit, now REFERENCE? Actually CURRENT during Milestone 4 as governing contract, but per task after approval Milestone 3 COMPLETE — feature timing contract. For Milestone 4, it remains CURRENT governing ALLOWED.
- `docs/PRICE_FREE_DATASET_CONTRACT.md` — **NEW CURRENT** — price-free dataset contract with exact versions, allowed/prohibited lists, missingness policy, timing rule, receipt accounting, Codespace command
- `docs/FOREBET_DEPTH_AUDIT.md` — training freeze receipt, facet inventory, historical depth contract, price coverage snapshots
- `docs/FOREBET_ARCHIVE_DEPTH.json` — annual archive probe matrix
- `docs/FOREBET_DETAIL_COVERAGE.json` — 3-page-per-sport detail factor sample
- `docs/FOREBET_PRICE_COVERAGE.json` — representative price snapshot per sport (reference only)
- `docs/FOREBET_FACET_ANALYSIS_PLAN.md` — timing classes + analysis order
- `docs/MA_GOLIDE_ROBBER_FORENSIC.md` — legacy Robber forensic spec (HISTORICAL)

## Verification Receipt (Milestone 0-4)

- `git mv STATE.md docs/STATE.md` executed, verified
- Milestone 1 audit: `docs/MILESTONE1_AUDIT.md` 885 lines + corrections, read-only, now REFERENCE
- Milestone 2: `src/slumdog/underdog.py` new module, `tests/test_price_free.py` 40 tests (10 hardening), full suite 232 passed
- Milestone 2E: hardening verified — identity-bound public API, SPORTS registry draw capability, exact reason preservation, tests: label uses indices from identity, cannot reverse via public API, draw from SPORTS, football draw 0, basketball draw excluded, unknown sport explicit, equal/missing/non-finite/out-of-range survive
- Milestone 3: `docs/FEATURE_TIMING_CONTRACT.md` created doc-only, period_values 10-point trace, feature inventory, missingness audit, no code change, 232 tests passed
- Milestone 4: `src/slumdog/dataset.py` new — PriceFreeUnderdogExample contract, receipt, builder with chronological evidence, minimal safe feature set ALLOWED only, missingness policy, timing guarantees, eligibility rules, receipt accounting, price-independence, 30 new tests in `tests/test_price_free_dataset.py`, full suite 262 passed, training frozen, no integration into legacy training.py yet, no LogisticRegression, no MODEL_TRAINING_ALLOWED unlock
- Tests: `python3 -m pytest -q` 262 passed, `python3 -m pytest -q tests/test_price_free.py tests/test_price_free_dataset.py` 70 passed, `python3 -m py_compile src/slumdog/*.py tests/*.py` ok, `python3 -m pyflakes src/slumdog/dataset.py src/slumdog/underdog.py` ok, `git diff --check` ok
- Training remains frozen: `MODEL_TRAINING_ALLOWED=False`

## After Milestone 4 (Now)

- Milestone 0 COMPLETE, Milestone 1 COMPLETE reference audit, Milestone 2 COMPLETE price-free identity/label/contracts, Milestone 3 COMPLETE feature timing contract, Current phase Milestone 4 price-free historical example builder, Model training FROZEN
- PR #6 open unmerged, branch arena/01a033af-slumdog
- Next: Awaiting user approval of Milestone 4 dataset contract (feature contract version, label contract version, allowed/prohibited lists, missingness policy, timing guarantees, receipt accounting) before proceeding to Milestone 5 transparent baselines with walk-forward validation
- Do not proceed into model training, ranking thresholds, or daily production until Milestone 4 approved
- Codespace data audit command provided in `docs/PRICE_FREE_DATASET_CONTRACT.md` — writes output under /tmp, does not alter ledgers, does not fetch network, prints receipt summary only
