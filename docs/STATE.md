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

**Current phase: Milestone 3 — feature timing and leakage audit (read-only analysis)**
**Milestone 0 documentation governance: COMPLETE**
**Milestone 1 audit: COMPLETE (approved, evidence record `docs/MILESTONE1_AUDIT.md`)**
**Milestone 2 price-free identity, label, contracts: COMPLETE (including 2E hardening)**
**Milestone 2E hardening: COMPLETE — identity-bound label, SPORTS registry draw capability, exact reason preservation**
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
- Produced `docs/MILESTONE1_AUDIT.md` with 10 gaps and staged plan, approved with refinements

Milestone 2 COMPLETE (including 2E hardening):
- 2A: Pure Forebet identity `identify_forebet_underdog(prob1, prob2, draw_prob)` — validates present/finite/[0,1], higher=favorite lower=underdog, draw does not determine identity, equal→EQUAL_PROBABILITY, missing→MISSING_PROBABILITY, non-finite→NON_FINITE, out-of-range→OUT_OF_RANGE, no fallback
- 2B: Historical label foundation — draw-capable: underdog win 1, fav win 0, draw 0, void excluded; two-way: underdog win 1, fav win 0, unexpected draw excluded UNEXPECTED_DRAW_FOR_TWO_WAY, void excluded
- 2C: Price-free contracts `StrongUnderdogAssessment`, `DailyUnderdogShortlist`, statuses `UnderdogAssessmentStatus` and `DailyShortlistStatus`, helper `build_assessment_from_identity()`
- 2D: Explicit tests 30 → 40 after 2E, full suite 222 → 232
- 2E hardening (new): UnderdogLabelResult adds identity_ineligibility_reason/sport/draw_possible, private _label_from_indices raw helper, public label_underdog_outcome(sport, identity: ForebetUnderdogIdentity, winner_index, disposition, source_conflict) derives fav/dog/eligible/reason from identity, derives draw_possible from SPORTS[sport].draw_possible, unknown sport→UNKNOWN_SPORT exclusion, preserves exact EQUAL_PROBABILITY/MISSING_PROBABILITY/NON_FINITE/OUT_OF_RANGE reasons, caller cannot reverse fav/dog or override draw capability, training frozen, no integration into legacy training.py yet

Milestone 3 CURRENT (read-only, no code change): Feature timing and leakage audit — inventory every potential feature, classify PRE_EVENT/RESULT_ONLY/UNKNOWN, odds excluded, missingness policy, produce `docs/FEATURE_TIMING_CONTRACT.md` with required columns, priority investigation period_values 10 points.

## Merged Work (main @ 2e3daa4) + PR #6 Branch (arena/01a033af-slumdog)

- PR #4: football truncated relay fix, H2H fabrication guard, MMA duplicate dedup, no-odds documentation (cricket 0% price verified, American football dash handling)
- PR #5: DOM-scoped football double-chance + scorer market facets (`detail_facets.py`), regression tests `test_football_dom_markets.py`
- Milestone 0 (branch): `STATE.md` → `docs/STATE.md` git mv, `AGENTS.md` constitution, `docs/STATE.md` rewrite to current truth, `README.md` rewrite to price-free mission, `docs/README.md` index, historical banners, price coverage reference-only clarification, 5 corrections verified, 192 tests passed
- Milestone 1 (branch): `docs/MILESTONE1_AUDIT.md` read-only audit (10 gaps, staged plan), approved with 8 refinements
- Milestone 2 (branch): `src/slumdog/underdog.py` new price-free module — pure Forebet identity, label with hardening, contracts, 40 tests in `tests/test_price_free.py`, full suite 232 passed
- Milestone 2E (branch, commit fee5d78): label contract hardening — identity-bound public API, SPORTS registry draw capability, exact reason preservation, 10 new hardening tests, 40 total price-free tests
- Milestone 3 (branch, current): `docs/FEATURE_TIMING_CONTRACT.md` doc-only audit — period_values 10-point investigation (DOM selector .predQ .fj_column, listing parser parsers.py:170-173, contract facets["period_values"], feature builders basketball.py:287 etc, sports used basketball/american_football/hockey/rugby/handball/volleyball/esports, ambiguous predicted vs completed, populated for upcoming unknown, settlement flow into period_scores_1/2 not same key, tests synthetic not proof, final timing UNKNOWN PROHIBITED), full feature inventory with columns Feature|Family|Sport|Source|Raw|Timing|Evidence|Missing|Indicator|Odds-dependent|Legacy|New-path|Action, missingness audit GENUINE_ZERO/UNKNOWN_ENCODED_AS_ZERO/SAFE_MATHEMATICAL_DEFAULT/UNRESOLVED
- Core pipeline (unchanged legacy): immutable captures, per-sport history ledgers, depth-sweep census, `forebet.py` relay handling, timing classes, sport-specific settlement contracts
- Feature contracts in `feature_contracts.py` (aspirational, not training inputs, MODEL_TRAINING_ALLOWED=False)
- Legacy Ma Golide Robber reproducer in `magolide.py` (odds-first cascade, legacy, superseded by new price-free path, preserved for forensic comparability)

## Active Blockers

**Milestone 0 governance: COMPLETE. Milestone 1 audit: COMPLETE. Milestone 2 COMPLETE (including 2E).** Missing prices are NOT blockers — odds are optional context only. Price coverage is reference evidence only, not readiness gate.

Real blockers (updated for Milestone 3):

1. **Underdog machinery still odds-first in legacy path** — `contracts.py`/`magolide.py`/`feature_contracts.py` and all sport detectors encode odds-first cascade; new price-free path `underdog.py` implemented with hardening but legacy not yet replaced (preserved per migration strategy). Needs integration after Milestone 3 approval.
2. **Label contract hardened and complete, integration pending** — public `label_underdog_outcome(sport, identity, ...)` derives draw capability from SPORTS registry, preserves exact identity reasons, unknown sport explicit UNKNOWN_SPORT, draw-capable draw=0, two-way draw excluded. Needs integration into training orchestration and settlement verification in later milestone (training.py still uses legacy odds-first candidate — intentionally not integrated per compatibility boundary).
3. **Feature timing and leakage controls — Milestone 3 audit CURRENT** — `period_values` timing=UNKNOWN, prohibited as feature until proven pre-event (10-point investigation in FEATURE_TIMING_CONTRACT.md); football detail fields (shots, passes, possession, attacks, next-fixture difficulty) need timing evidence from retained bytes or live Jina probe; odds must not be features (still present in legacy feature vectors, not yet removed per Milestone 2 scope — now explicitly prohibited in FEATURE_TIMING_CONTRACT.md). No code change during Milestone 3 audit.
4. **Model validation and daily shortlist behavior not yet approved** — walk-forward exists, but ROI gate still in legacy validation, required daily metrics not yet implemented; shortlist cap 1–3, explicit NO_STRONG_UNDERDOG daily status, assessment vs selection separation defined in new contracts but not yet integrated.
5. **Training remains frozen** — `MODEL_TRAINING_ALLOWED=False`, explicit user unlock required after dataset/target/timing/validation contract approved. No feature-vector, threshold, ranking, model approval, or daily production changes until Milestone 3 approved.
6. **Nested mutability and status semantics contract notes for later** — frozen dataclass with mutable dicts optional_price_context/rejection_counts/source_receipt not deeply immutable, must be defensively copied/immutable/frozen by ledger before immutable receipts (Milestone 6), record only; STRONG_UNDERDOG must not imply approved probability until scoring/thresholds approved, tests may construct status for serialization, operational code must not emit, reserved fields remain None.

Data limitations below are parked observations, not candidate-readiness gates.

## Current Model-Training Status

**FROZEN.** From `feature_contracts.py`: `MODEL_TRAINING_ALLOWED = False`.

- 14-day preliminary experiment discarded (generic vector, insufficient sport-specific representation).
- No retraining allowed until each sport has: listing/detail facet contract, historical depth receipt, timing classification, settlement coverage, price-availability profile, AND user approves dataset/target/timing/validation contract.
- When unlocked, first implement transparent baselines (Forebet underdog prob, gap, recent-form differential, Ma Golide heuristic, simple interpretable model) with walk-forward validation, never random splits.
- Milestone 3 is read-only analysis — no code change, no training.

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

**Milestone 1: COMPLETE (approved as evidence record `docs/MILESTONE1_AUDIT.md`, with 8 refinements).**

**Milestone 2: COMPLETE (including 2E hardening, approved).** Deliverables: `src/slumdog/underdog.py` with identity-bound label, SPORTS registry draw capability, exact reason preservation, 40 tests, 232 total passed, training frozen, compatibility boundary explicit (no integration into legacy training.py yet).

**Milestone 3 CURRENT (approved next work, read-only, no code change):** Feature timing and leakage audit — inventory every potential feature, classify PRE_EVENT/RESULT_ONLY/UNKNOWN with proof, odds excluded, missingness policy, produce `docs/FEATURE_TIMING_CONTRACT.md`.

Milestone 3 deliverable `docs/FEATURE_TIMING_CONTRACT.md` must have columns: Feature|Feature family|Sport|Source file/function|Raw source field|Timing|Evidence|Missing representation|Missing indicator|Odds-dependent|Legacy use|New-path eligibility|Action; Timing PRE_EVENT/RESULT_ONLY/UNKNOWN; New-path ALLOWED/PROHIBITED/PARKED; Rules RESULT_ONLY→prohibited, UNKNOWN→prohibited until verified, odds-dependent→prohibited, missing odds irrelevant, existing use does not prove eligibility, suggestive name does not prove timing, evidence must cite code or retained bytes.

Priority investigation period_values 10 points: DOM selector/JSON field, listing/detail parser storing it, event contract field, feature-builder consuming, sports used, whether predicted/completed/schedule/ambiguous, populated for upcoming events, settlement output flow into same facet key, tests covering, final timing; until resolved period_values=UNKNOWN new path PROHIBITED.

Required families audit list includes Forebet probs, draw prob, gap/ratio, entropy/dominance, recent form, home/away, win rates, table position, H2H, goals/points scored/conceded, shots, shots on target, blocked/off-target, possession, passes accuracy, attacks, dangerous attacks, event-time, schedule difficulty, weather, venue, stable IDs, cup flags, trend text, double chance, goalscorer predictions, sport-specific physical/stat facets, every price/odds/overround/fair prob/value-edge field, every final/period/penalty/extra-time/disposition/settlement field.

Missingness audit for every feature None/NaN/0/empty string/absent key/sentinel text and zero fallback classification GENUINE_ZERO/UNKNOWN_ENCODED_AS_ZERO/SAFE_MATHEMATICAL_DEFAULT/UNRESOLVED.

No code change during Milestone 3 audit. Documentation lifecycle: MILESTONE1_AUDIT.md → REFERENCE, FEATURE_TIMING_CONTRACT.md → CURRENT, STATE.md concise truth, HANDOFF records functions/tests/blocker, README reflects transition.

Next after Milestone 3 approval: Milestone 4 — implement price-free feature vector based on approved FEATURE_TIMING_CONTRACT.md ALLOWED only, with missingness indicators, no odds, no RESULT_ONLY, no UNKNOWN.

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
- period_values timing UNKNOWN — needs live Jina probe for upcoming basketball date, check .predQ presence and sum vs predicted_score, per FEATURE_TIMING_CONTRACT.md 10-point investigation.

## Links to Deeper Documents

- `AGENTS.md` — permanent mission + operating constitution
- `README.md` — product overview (price-free mission, operational commands)
- `HANDOFF.md` — session continuation record
- `docs/STATE.md` — this file, canonical current truth
- `docs/README.md` — doc index with purpose/status/last-verified
- `docs/MILESTONE1_AUDIT.md` — price-free machinery audit: 10 gaps, staged plan, now REFERENCE (gaps resolved for identity/label/contracts, timing audit now CURRENT)
- `docs/FEATURE_TIMING_CONTRACT.md` — **NEW CURRENT** — feature timing and leakage audit with period_values 10-point investigation, full feature inventory, missingness audit, new-path eligibility
- `docs/FOREBET_DEPTH_AUDIT.md` — training freeze receipt, facet inventory, historical depth contract, price coverage snapshots (reference only)
- `docs/FOREBET_ARCHIVE_DEPTH.json` — annual archive probe matrix
- `docs/FOREBET_DETAIL_COVERAGE.json` — 3-page-per-sport detail factor sample
- `docs/FOREBET_PRICE_COVERAGE.json` — representative price snapshot per sport (reference only)
- `docs/FOREBET_FACET_ANALYSIS_PLAN.md` — timing classes + analysis order
- `docs/MA_GOLIDE_ROBBER_FORENSIC.md` — legacy Robber forensic spec (HISTORICAL)

## Verification Receipt (Milestone 0-3)

- `git mv STATE.md docs/STATE.md` executed, verified
- Milestone 1 audit: `docs/MILESTONE1_AUDIT.md` 885 lines + corrections, read-only
- Milestone 2: `src/slumdog/underdog.py` new module, `tests/test_price_free.py` 40 tests (10 hardening), full suite 232 passed
- Milestone 2E: hardening verified — identity-bound public API, SPORTS registry draw capability, exact reason preservation, tests: label uses indices from identity, cannot reverse via public API, draw from SPORTS, football draw 0, basketball draw excluded, unknown sport explicit, equal/missing/non-finite/out-of-range survive
- Milestone 3: `docs/FEATURE_TIMING_CONTRACT.md` created doc-only, period_values 10-point trace, feature inventory with required columns, missingness audit, no code change
- Tests: `python3 -m pytest -q` 232 passed, `python3 -m pytest -q tests/test_price_free.py` 40 passed, `python3 -m py_compile src/slumdog/underdog.py` ok, `python3 -m pyflakes src/slumdog/underdog.py` ok, `git diff --check` ok
- Training remains frozen: `MODEL_TRAINING_ALLOWED=False`

## After Milestone 3 (Now)

- Milestone 0 COMPLETE, Milestone 1 COMPLETE (now REFERENCE), Milestone 2 COMPLETE (including 2E), Milestone 3 CURRENT (read-only audit)
- PR #6 open unmerged, branch arena/01a033af-slumdog
- Next: Awaiting user approval of FEATURE_TIMING_CONTRACT.md (period_values investigation, feature inventory, missingness audit, new-path eligibility) before proceeding to Milestone 4 feature vector implementation (ALLOWED only, no odds, no RESULT_ONLY, no UNKNOWN)
- Do not proceed into feature-vector code changes, training, ranking thresholds, or daily production until Milestone 3 approved
