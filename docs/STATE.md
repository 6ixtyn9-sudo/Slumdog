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

**Current phase: Milestone 2 — price-free identity, label, and contract foundation**
**Milestone 0 documentation governance: COMPLETE**
**Milestone 1 audit: COMPLETE (approved, evidence record `docs/MILESTONE1_AUDIT.md`)**
**Model training: FROZEN**

Milestone 0 completed:
- `STATE.md` → `docs/STATE.md` move + reference cleanup (git mv)
- `AGENTS.md` entrypoint with permanent mission + invariants
- Docs audit/classification + `docs/README.md` index
- README.md rewrite to price-free UNDERDOG_WIN mission
- Freshness workflow lock
- 5 final corrections: phase advanced, historical banners, price coverage reference-only, operational README preserved, move verified

Milestone 1 completed (read-only audit, no code changes):
- Audited candidate generation, fav/underdog assignment, features, labels, training, calibration, ranking, reporting, scheduling, settlement, model cards, shadow outputs
- Exposed central problem: system can operate without odds but identity, scoring, feature vectors, thresholds, research approval still materially odds-first
- Produced `docs/MILESTONE1_AUDIT.md` with 10 gaps and staged plan, approved with refinements (NO_STRONG_UNDERDOG daily result not candidate state, preserve legacy CandidateState, separate assessment from selection, odds outside core assessment, no ranking by model prob before approval, leakage as priority blocker, missing values remain unknown, daily success needs several metrics)

Milestone 2 (current, approved next work only): Implement price-free identity, label, and contract foundation — do NOT proceed into feature-vector changes, training, ranking thresholds, or daily production yet. Implemented in `src/slumdog/underdog.py` with 30 tests.

Active: Milestone 2A–2D implementation — pure Forebet identity function, historical label function, new price-free contracts, explicit tests.

## Merged Work (main @ 2e3daa4) + PR #6 Branch (arena/01a033af-slumdog)

- PR #4: football truncated relay fix, H2H fabrication guard, MMA duplicate dedup, no-odds documentation (cricket 0% price verified, American football dash handling)
- PR #5: DOM-scoped football double-chance + scorer market facets (`detail_facets.py`), regression tests `test_football_dom_markets.py`
- Milestone 0 (branch): `STATE.md` → `docs/STATE.md` git mv, `AGENTS.md` constitution, `docs/STATE.md` rewrite to current truth, `README.md` rewrite to price-free mission, `docs/README.md` index, historical banners, price coverage reference-only clarification, 5 corrections verified, 192 tests passed
- Milestone 1 (branch): `docs/MILESTONE1_AUDIT.md` read-only audit (10 gaps, staged plan), approved with 8 refinements (NO_STRONG_UNDERDOG daily vs candidate, preserve legacy CandidateState, separate assessment from selection, odds outside core, no ranking by model prob before approval, leakage as priority blocker, missing values remain unknown, daily success metrics)
- Milestone 2 (branch, current): `src/slumdog/underdog.py` new price-free module — pure Forebet identity `identify_forebet_underdog()`, historical label `label_underdog_outcome()`, contracts `StrongUnderdogAssessment`, `DailyUnderdogShortlist`, statuses `UnderdogAssessmentStatus` (STRONG_UNDERDOG/WATCHLIST/INSUFFICIENT_EVIDENCE/REJECTED_SOURCE_CONFLICT/INELIGIBLE) and `DailyShortlistStatus` (CANDIDATES_FOUND/NO_STRONG_UNDERDOG/SOURCE_FAILURE), helper `build_assessment_from_identity()`, 30 new tests in `tests/test_price_free.py`, full suite 222 passed
- Core pipeline (unchanged legacy): immutable captures, per-sport history ledgers (`history_<sport>.jsonl.gz` + manifest), depth-sweep census, `forebet.py` relay handling (Markdown reader mode → html relay → direct), timing classes (`PRE_EVENT`/`LIVE_ONLY`/`RESULT_ONLY`/`UNKNOWN`), sport-specific settlement contracts
- Feature contracts in `feature_contracts.py` (currently aspirational, not training inputs, MODEL_TRAINING_ALLOWED=False)
- Legacy Ma Golide Robber reproducer in `magolide.py` (odds-first cascade, now marked legacy, superseded by new price-free path, preserved for forensic comparability)

## Active Blockers

**Milestone 0 governance: COMPLETE. Milestone 1 audit: COMPLETE.** Missing prices are NOT blockers — odds are optional context only (see invariants). Price coverage is reference evidence only, not readiness gate.

Real blockers (per user correction, updated for Milestone 2 progress):

1. **Underdog machinery still odds-first in legacy path** — `contracts.py`/`magolide.py`/`feature_contracts.py` and all sport detectors encode odds-first cascade; new price-free path `underdog.py` implemented but legacy not yet replaced (preserved per migration strategy). Needs integration in later stage after tests.
2. **Underdog-win label contract foundation done, integration pending** — pure label `label_underdog_outcome()` implemented with explicit exclusion reasons (VOID, EQUAL_PROBABILITY, MISSING, INVALID_WINNER, SOURCE_CONFLICT, UNEXPECTED_DRAW_FOR_TWO_WAY), draw-capable draw=0, two-way draw excluded, void excluded. Needs integration into training orchestration and settlement verification in later milestone (currently training.py still uses legacy odds-first candidate).
3. **Draw settlement behavior verified in pure function, needs pipeline integration** — pure label counts draw as failed for draw-capable, excluded for two-way; pipeline settlement must enforce same.
4. **Feature timing and leakage controls need auditing (priority blocker)** — `period_values` timing=UNKNOWN, prohibited as feature until proven pre-event; football detail fields (shots, passes, possession, attacks, next-fixture difficulty) need timing evidence from retained bytes or live Jina probe; odds must not be features (still present in legacy feature vectors, not yet removed per Milestone 2 scope).
5. **Model validation and daily shortlist behavior not yet approved** — walk-forward exists, but ROI gate still in legacy validation, required daily metrics (top_1_daily_hit_rate, top_3_daily_any_hit_rate, days_with_at_least_one_selected_winner, selected_candidates_per_day, no_pick_day_rate, candidate_precision) not yet implemented; shortlist cap 1–3, explicit NO_STRONG_UNDERDOG daily status, assessment vs selection separation defined in new contracts but not yet integrated into pipeline.
6. **Training remains frozen** — `MODEL_TRAINING_ALLOWED=False`, explicit user unlock required after dataset/target/timing/validation contract approved. No feature-vector, threshold, ranking, model approval, or daily production changes in Milestone 2.

Data limitations below are parked observations, not candidate-readiness gates.

## Current Model-Training Status

**FROZEN.** From `feature_contracts.py`: `MODEL_TRAINING_ALLOWED = False`.

- 14-day preliminary experiment discarded (generic vector, insufficient sport-specific representation).
- No retraining allowed until each sport has: listing/detail facet contract, historical depth receipt, timing classification, settlement coverage, price-availability profile, AND user approves dataset/target/timing/validation contract.
- When unlocked, first implement transparent baselines (Forebet underdog prob, gap, recent-form differential, Ma Golide heuristic, simple interpretable model) with walk-forward validation, never random splits.

## Current Data Limitations (Reference Observations, Not Readiness Gates)

- **Football backfill gap:** 963 dates failed on runner (401 relay) — Markdown reader path added but needs next pipeline probe.
- **Relay egress:** Direct `forebet.com` from Azure (Codespaces + GH runners) behind Cloudflare JS challenge (403). Jina relay Markdown mode works locally (714 rows, 2.4MB) but GH Actions IP 401 status still to be verified.
- **Price coverage — reference evidence only, NOT a candidate-readiness gate:** `FOREBET_PRICE_COVERAGE.json` is snapshot (one active-season date per sport), not global. Football 77.3% (778/1006), Basketball 60.6%, Tennis 96%, Baseball 68.4%, Cricket 0% (6643 settled rows, zero priced, verified no `.haodd`), Handball 0% but actually 2-price American format fixed, Hockey 0% on sampled WHL/AHL date (99 dashes) — needs in-season NHL/KHL/SHL re-check, Rugby/Volleyball 0%, American football 0% (7447 archived rows zero priced, pending 2026-09-10 probe), MMA 153 priced / 757 unique, Esoccer rolling board no reliable dated archive. Sparse/missing prices do NOT block strong-underdog generation — odds are optional context only.
- **Detail coverage:** Three-page sample per sport in `FOREBET_DETAIL_COVERAGE.json` justified parser families, but full census missingness not yet measured from `depth-sweep`.
- **Legacy ledger integrity:** 279 byte-identical extra rows across 278 repeated same-date keys; 4 cross-date identical pairs after removing `event_date` (`basketball:198045`, `basketball:198046`, `football:2041406`, `volleyball:96303`); hockey `278977` same-key conflicting results (1-6 vs 0-4); MMA 11 rows both void+priced (7 raw captures absent, plausible pre-scratch); all 759 legacy MMA rows lacked `raw_sha256`/`captured_at` (predates provenance retention).
- **Missing raw bytes:** 7 sampled suspicious dates had manifest hashes but no local `data/raw` files; hash cannot reconstruct bytes, refetch ≠ historical capture.
- **Football markets:** 5 distinct JSON endpoints (uo, bts, ht, ah, cards) — one req/date each; htft byte-identical to ht; corners/doublechance/goalscorer echo 1X2 JSON, so price only from detail HTML (with `Coef. -` often).

## Next Approved Milestone

**Milestone 0: COMPLETE (approved with 5 documentation corrections, no deletions authorized).**

**Milestone 1: COMPLETE (approved as evidence record `docs/MILESTONE1_AUDIT.md`, with 8 refinements: NO_STRONG_UNDERDOG daily vs candidate, preserve legacy CandidateState, separate assessment from selection, odds outside core, no ranking by model prob before approval, leakage as priority blocker, missing values remain unknown, daily success metrics).**

Milestone 1 completed:
- Audited candidate generation, fav/underdog assignment, features, labels, training, calibration, ranking, reporting, scheduling, settlement, model cards, shadow outputs
- Produced 10 gaps with Finding/Evidence/Current/Required/Smallest change/Tests/Risk and staged plan
- Central problem: system can operate without odds but identity, scoring, feature vectors, thresholds, research approval still materially odds-first

**Milestone 2 (current, approved next work only):** Implement price-free identity, label, and contract foundation — do NOT proceed into feature-vector changes, training, ranking thresholds, or daily production yet.

Milestone 2A–2D implemented in `src/slumdog/underdog.py`:
- 2A: Pure Forebet identity `identify_forebet_underdog(prob1, prob2, draw_prob)` — validates present/finite/[0,1], higher= favorite, lower=underdog, draw does not determine identity, equal → no underdog with EQUAL_PROBABILITY reason, missing/invalid → MISSING_PROBABILITY etc., no fallback to odds/pick/form, no gap threshold
- 2B: Historical label `label_underdog_outcome()` — draw-capable: underdog win 1, fav win 0, draw 0, void excluded; two-way: underdog win 1, fav win 0, unexpected draw excluded, void excluded; also excludes no eligible identity, invalid winner, source conflict, missing probs, explicit exclusion reason
- 2C: New price-free contracts `StrongUnderdogAssessment` (event_id, sport, date, participants, fav/underdog index/name/prob, draw prob, gap, status, supporting/contradicting/missing evidence, source_url, raw_sha256, captured_at, assessment_version, reserved optional slumdog_underdog_probability/probability_lift/baseline_strength_score must remain None until approved, isolated optional_price_context block may be absent, no other field depends on it) and `DailyUnderdogShortlist` (target_date, generated_at, status CANDIDATES_FOUND/NO_STRONG_UNDERDOG/SOURCE_FAILURE, assessments_considered, strong_candidates, watchlist_candidates, rejection_counts, assessment_version, source_receipt, no-pick example status=NO_STRONG_UNDERDOG with empty tuple, no fake candidate, no draw selection)
- 2D: Explicit tests — identity (10 cases: p1 fav, p2 fav, equal, missing, non-finite, out-of-range, draw larger not selected, odds disagree no effect, pick disagrees no effect, form disagrees no effect), labels (11 cases: draw-capable underdog/fav win, draw=0, two-way underdog/fav win, two-way draw excluded, void excluded, equal prob excluded, missing prob excluded, invalid winner excluded, source conflict excluded), contracts (6 cases: round-trip, no price field required, missing optional evidence accepted, no-pick serializes zero candidates, cannot contain fake draw, provenance optional only where legacy lacks, reserved fields None, optional price context isolated, no fake candidate for no-pick status)

Next after Milestone 2 approval: Milestone 3 — define feature and timing contract (inventory every potential feature, classify PRE_EVENT/RESULT_ONLY/UNKNOWN, odds excluded, missingness policy, feature table).

## Parked Work

- American football odds probe (`scripts/probe_american_football_odds.py`) — do not run before ~2026-09-10 (regular season)
- Complex ensembles — baselines first after unlock
- Esoccer separate audit (player-handle identity, no dated archive route)
- Dropped football `getrs.php` keys audit
- Sparse hockey/rugby/volleyball/handball pricing re-check on in-season top-league dates
- Auto-rewrite/compact legacy ledgers — prohibited without explicit authorization

## Unresolved Evidence

- 4 cross-date normalized-identical pairs (basketball:198045/198046, football:2041406, volleyball:96303) — rescheduled vs date-boundary? Unresolved, not auto-deleted.
- Hockey `278977` (2023-08-20) conflicting results — needs source bytes, neither selectable honestly.
- MMA 11 void+priced rows — plausible pre-scratch but cannot verify without raw captures.
- Absent sampled raw bytes for 7 suspicious dates — manifest retains URL/byte-count/SHA256 only.
- Football DC token `21` observed (`85% / 12 / -1111` etc) — raw, unnormalized, preserved.
- Scorer market subtype unknown; display order preserved but not ranking; empty rows emit nothing; sample fill-rate unknown.
- Football 963-date backfill gap quantification + replay feasibility from retained captures.
- HT/FT as single combined price, not 9-cell matrix — verified distinctness but need DOM selector proof.

## Links to Deeper Documents

- `AGENTS.md` — permanent mission + operating constitution
- `README.md` — product overview (price-free mission, operational commands)
- `HANDOFF.md` — session continuation record (Milestone 0 corrections + Milestone 1 audit)
- `docs/STATE.md` — this file, canonical current truth (Milestone 1 audit phase)
- `docs/README.md` — doc index with purpose/status/last-verified, price coverage reference-only clarification
- `docs/MILESTONE1_AUDIT.md` — **NEW** price-free machinery audit: 10 gaps (odds-first identity, threshold, price features, zero-fill, equal prob, shortlist policy, ROI gate, incomplete receipt, evidence, timing), staged implementation plan
- `docs/FOREBET_DEPTH_AUDIT.md` — training freeze receipt, facet inventory, historical depth contract, price coverage snapshots (reference only), verified no-odds gaps, duplicate audits
- `docs/FOREBET_ARCHIVE_DEPTH.json` — annual archive probe matrix (conservative backfill starts)
- `docs/FOREBET_DETAIL_COVERAGE.json` — 3-page-per-sport detail factor sample
- `docs/FOREBET_PRICE_COVERAGE.json` — representative price snapshot per sport (reference evidence only, not readiness gate)
- `docs/FOREBET_FACET_ANALYSIS_PLAN.md` — timing classes + analysis order (10-step, ROI not primary)
- `docs/MA_GOLIDE_ROBBER_FORENSIC.md` — legacy Robber forensic spec (HISTORICAL, not current contract)

## Verification Receipt (Milestone 0 complete, corrections applied, Milestone 1 audit complete, Milestone 2 implemented)

- `git mv STATE.md docs/STATE.md` executed, verified `test ! -e STATE.md && test -e docs/STATE.md`
- `grep -Rni --exclude-dir=.git --exclude='*.pyc' 'STATE\.md' .` shows only `docs/STATE.md` refs (plus historical mention of move in docs/README.md — acceptable, no active root link)
- `AGENTS.md` created with invariants + read order
- `docs/STATE.md` rewritten from diary to current-truth, phase advanced to Milestone 1 audit then Milestone 2, Milestone 0 marked COMPLETE, missing prices clarified as NOT blockers, price coverage reference-only
- `README.md` rewritten to price-free mission, operational commands preserved, Status updated to Milestone 2
- `docs/README.md` index created, price coverage clarified as reference only not gate, MILESTONE1_AUDIT.md classified as CURRENT during migration REFERENCE after gaps resolved
- `docs/MA_GOLIDE_ROBBER_FORENSIC.md` banner added: HISTORICAL, not current contract; `FOREBET_DEPTH_AUDIT.md` and `FOREBET_FACET_ANALYSIS_PLAN.md` banners added
- Milestone 1 audit: `docs/MILESTONE1_AUDIT.md` 885 lines + 8 refinements annotation, read-only, no code changes
- Milestone 2: `src/slumdog/underdog.py` new module (pure identity, label, contracts), `tests/test_price_free.py` 30 tests, full suite 222 passed
- Tests: `python3 -m pytest -q` 222 passed (192 legacy + 30 new), `python3 -m py_compile scripts/*.py src/slumdog/*.py tests/*.py` ok, `python3 -m pyflakes scripts src/slumdog tests` ok, `git diff --check` ok
- Training remains frozen: `MODEL_TRAINING_ALLOWED=False`, no feature-vector/threshold/ranking/model approval/daily production changes in Milestone 2

## After Milestone 2 (Now)

- Milestone 0 approved, no deletions authorized, PR #6 open unmerged
- Milestone 1 audit approved as evidence record with 8 refinements
- Milestone 2 implemented: pure Forebet identity, label, price-free contracts, 30 tests, 222 total passed, training frozen, no feature-vector changes
- Next: Awaiting user approval of Milestone 2 implementation (Finding addressed, Files changed, New contracts, Identity policy, Label policy, Legacy preserved, Tests added, Exact test count, Full verification, Remaining gaps) before proceeding to Milestone 3 feature timing contract
- Do not proceed into feature-vector changes, training, ranking thresholds, or daily production until Milestone 2 approved
