# Milestone 1 Audit — Price-Free Underdog Machinery

**Date:** 2026-08-24 UTC
**Branch:** `arena/01a033af-slumdog`
**Mode:** Read-only audit, no model/candidate/label/feature code changes
**Authority:** `AGENTS.md`, `README.md`, `docs/STATE.md` (current), `HANDOFF.md`
**Training:** FROZEN (`feature_contracts.py: MODEL_TRAINING_ALLOWED=False`)

This audit inspects existing implementation against the price-free candidate contract required by the permanent mission:

> Slumdog identifies a small daily shortlist of participants that Forebet considers underdogs but whose available pre-event evidence indicates a credible outright-win upset.

Required candidate contract (from prompt):

```
event identity
sport
event date/time
Forebet favorite
Forebet underdog
Forebet favorite probability
Forebet underdog probability
draw probability where applicable
model underdog-win probability
lift over Forebet underdog probability
strength score
supporting evidence
contradicting evidence
missing evidence
candidate status
model/version identifier
source/provenance identifiers
optional price context
```

## 1. Candidate Definition

### How current code determines favorite and underdog

**Evidence:**
- `src/slumdog/magolide.py:identify_underdog()` lines 48-73
- Sport-specific detectors: `football.py:detect_football_robber()` 628-640, `basketball.py:439-450`, `tennis.py:348-359`, `hockey.py:407-418`, `baseball.py:356-367`, `american_football.py:406-417`, `rugby.py:410-421`, `handball.py:414-425`, `volleyball.py:394-405`, `cricket.py:380-391`, `mma.py:320-331`, `esports.py:382-393`
- `src/slumdog/training.py:_proxy_candidate()` 46 uses same `identify_underdog`

**Current behavior:**
Cascade:
1. If both prices exist and differ, higher decimal odds = underdog (`displayed_odds`)
2. Else if `forebet_pick` in (1,2), opposite = underdog (`opposite_forebet_pick`)
3. Else if at least one Forebet probability exists and differs, lower probability = underdog (`lower_forebet_probability`)
4. Else weaker recent form, tie defaults to participant 1 (`weaker_recent_form`)

**Required behavior:**
- Favorite = participant with higher Forebet probability
- Underdog = participant with lower Forebet probability
- Unambiguous favorite/underdog from Forebet probabilities only
- Odds must NOT determine underdog strength, not required input, not model feature, not eligibility gate (invariants 5-11)
- Equal-probability rows require explicit policy, must not be silently assigned

**Finding: Odds-first underdog identity**

```
Finding: Underdog identity is odds-first, not Forebet-probability-first
Evidence: magolide.py:54-58, football.py:631-632, basketball.py:443, etc.
Current behavior: Higher odds → underdog basis = displayed_odds; only falls through to probability if odds missing or equal
Required behavior: Forebet probability determines favorite/underdog; odds optional metadata only
Smallest proposed change: Rewrite identify_underdog() to use probability_1 vs probability_2 only; remove odds branch; handle equal prob with explicit INSUFFICIENT_EVIDENCE or REJECTED policy; update all sport detectors to call shared function; add test for equal prob
Tests required: test_underdog_identity_uses_forebet_prob_only, test_equal_prob_explicit_policy, test_odds_do_not_affect_underdog_basis
Risk if unchanged: Violates invariants 5-11, turns project into price-first system, misaligns with mission
```

### Does it require odds?

**Evidence:** `magolide.py:detect_robber()` 120-145, 175-195; threshold `config.min_score if odds_available else max(10, round(min_score*0.55))` line 194; `price_state` in contracts

**Current behavior:**
- Does NOT require odds to create candidate: if odds missing, threshold lowered to 11 (from 20) and state = SHADOW_UNPRICED, reason "No displayed price (percentage-defined upset)" or "Unpriced..."
- However odds affect score (favorite strength factor only when odds_available, odds value factor 15/10/8/4 points) and lower threshold increases output volume when unpriced (defect noted in forensic doc)

**Required:** Odds must not be required, must not lower confidence, must not affect eligibility. Current lowers threshold when missing → increases volume, violates "missing odds must not lower candidate confidence" (it does opposite: lowers bar).

**Finding:**

```
Finding: Missing odds lowers threshold, increasing volume, not confidence-neutral
Evidence: magolide.py:194 threshold = max(10, round(min_score*0.55)) when odds missing; football.py:630 threshold same
Current: Unpriced threshold 11 vs priced 20, more candidates emitted
Required: Same threshold regardless of odds; missing odds has no effect on eligibility or confidence
Smallest change: Remove odds-dependent threshold; use single min_score; remove odds value and favorite strength factors from score (or keep only if using Forebet prob); ensure price_state not used for eligibility
Tests: test_missing_odds_does_not_lower_threshold, test_price_state_not_used_for_eligibility
Risk: Fabricates weak candidates on no-price days to fill quota, violates invariant 7
```

### Does it reject candidates when prices absent?

**Current:** No, it emits SHADOW_UNPRICED. So not rejecting, but threshold lowering is issue. Meets "do not gate on odds availability" partially, but confidence effect violates.

### Does it support a no-pick state?

**Evidence:** `pipeline.py:build_shadow_robbers()` 106-118 filters by raw_confidence<65 and h2h<3 and recent<5 when no model; `reports.py:29` renders "NO QUALIFYING ROBBERS" when rows empty.

**Current:** Can output zero candidates (no-pick). But status is implicit empty list, not explicit `NO_STRONG_UNDERDOG` status. Required statuses: STRONG_UNDERDOG, WATCHLIST, INSUFFICIENT_EVIDENCE, REJECTED_SOURCE_CONFLICT, NO_STRONG_UNDERDOG. Current statuses: SHADOW_UNPRICED, SHADOW_PRICED, CERTIFIED, REJECTED.

**Finding:**

```
Finding: No explicit NO_STRONG_UNDERDOG status; empty list is only signal
Evidence: contracts.py:26-30 CandidateState, reports.py:29, pipeline.py:build_shadow_robbers
Current: SHADOW_* states, empty list = no pick
Required: Explicit daily status NO_STRONG_UNDERDOG + per-candidate STRONG_UNDERDOG/WATCHLIST/INSUFFICIENT_EVIDENCE/REJECTED_SOURCE_CONFLICT
Smallest change: Redefine CandidateState to new statuses, add daily wrapper with status field, update reports to emit NO_STRONG_UNDERDOG receipt
Tests: test_no_pick_day_explicit_status, test_daily_shortlist_capped_1_3, test_never_forces_pick
Risk: Operational ambiguity, cannot measure no-pick rate, cannot enforce small shortlist
```

### Does it ever select or positively score draws?

**Evidence:** `magolide.py:detect_robber()` returns RobberCandidate with participant_index 1 or 2 only; draw never selected. `football.py` underdog identity never returns 0. `contracts.py` EventSnapshot forebet_pick only 1/2/None. However legacy score does not penalize draw probability? Football planned feature families mention draw pressure but not scoring.

**Current:** Never selects draws (good). Does it positively score draws? No, draw not candidate. But does it treat draw as supporting evidence? In football features, draw_pressure_ratio exists but not used as negative? Need check.

**Required:** Draw never selected, draw = failed UNDERDOG_WIN prediction.

**Finding:** Compliant on selection, but settlement must enforce draw = failed (see label section).

### Is underdog based on Forebet prob or price?

**Current:** Price-first, as above. Gap.

## 2. Historical Label

### Where is target constructed?

**Evidence:**
- `training.py:build_training_rows()` 76-103
- `training.py:76 underdog_won = int(row.winner_index == candidate.participant_index)`
- `ml_meta.py:TrainingRow` underdog_won field
- `history.py:HistoryIndex` filters

**Current behavior:**
- Target = 1 if settled winner_index == candidate.participant_index (underdog wins), else 0
- Candidate determined via odds-first `identify_underdog` (so label is odds-dependent indirectly)

**Required:** Label = 1 only if underdog (by Forebet prob) wins outright; 0 if favorite wins OR draw in draw-capable sports; void excluded; favorite/underdog identity frozen from pre-event Forebet probs.

**Finding:**

```
Finding: Label uses odds-first candidate, not Forebet-prob underdog
Evidence: training.py:46 dog = identify_underdog(event).index, 76 underdog_won compares to that candidate
Current: Label depends on price
Required: Label must use Forebet-prob underdog, frozen from pre-event snapshot
Smallest change: New function identify_underdog_by_forebet_prob() using only probability_1 vs probability_2; use it in build_training_rows; add test that label independent of odds
Tests: test_label_uses_forebet_prob_not_odds, test_label_frozen_from_pre_event
Risk: Training on price-defined underdog leaks bookmaker info, violates price-free contract
```

### Draw handling in draw-capable sports

**Evidence:** `training.py:80-92`, `history.py:30-45`, `sports.py:draw_possible`

**Current:**
- Two-way sports (draw_possible=False): if winner_index==0, row excluded (quarantine as contract violation) — `training.py:84-88`, `history.py:32-38`
- Draw-capable (football, handball, cricket, esoccer): winner_index==0 kept, underdog_won = 0 (since 0 != 1/2), so draw = failed underdog win. This matches required for draw-capable.
- SETTLED_DRAW disposition also handled: `training.py:90-91` excludes SETTLED_DRAW if draw not possible; keeps if draw possible.

**Required:** For draw-capable, label=1 only if underdog wins outright, 0 if favorite wins OR drawn, void excluded. For two-way, label=1 only if underdog wins, 0 if favorite wins, void excluded. So current draw handling for draw-capable is correct (draw=0), but for two-way, draw excluded rather than counted as failure — which is arguably correct as contract violation, but prompt says for two-way label=0 if favorite wins, void excluded, doesn't mention draw. Since two-way shouldn't have draws, quarantine is reasonable. Need explicit policy.

**Finding:**

```
Finding: Draw handling mostly correct for draw-capable (draw=failed), but two-way draw quarantine needs explicit documented policy
Evidence: training.py:80-92, settlement.py parse_football_settled winner logic
Current: Draw-capable draw → underdog_won=0 (failed), two-way draw → excluded
Required: Document explicit policy; ensure settlement reproducible; add test for football draw = failed
Tests: test_draw_is_failed_underdog_win_for_football, test_two_way_draw_excluded_or_explicit_policy
Risk: Ambiguous settlement if two-way draw occurs (overtime missing)
```

### Void handling

**Evidence:** `training.py:77-79` if disposition==VOID continue; `history.py:29` filters VOID; `settlement.py` parses comment tokens CANCL, POSTP, ABAND, etc. as VOID.

**Current:** Voids excluded — compliant with required "void/no-contest/cancelled excluded".

### Tied participant probabilities

**Evidence:** `magolide.py:60-73` if p1==p2, falls through to recent form, then participant 1 if tie.

**Current:** Silent assignment via weaker_recent_form or participant 1 default. No explicit INSUFFICIENT_EVIDENCE.

**Required:** Explicit policy, must not be silently assigned.

**Finding:**

```
Finding: Equal Forebet probabilities silently assigned via recent form / p1 default
Evidence: magolide.py:65-73, football.py:635-639
Current: v1 <= v2 defaults to participant 1 as underdog when equal
Required: Explicit REJECTED_SOURCE_CONFLICT or INSUFFICIENT_EVIDENCE when probabilities equal and no unambiguous favorite
Smallest change: In identify_underdog_by_forebet_prob, if abs(p1-p2) < epsilon or both None, return None or special status; candidate builder emits INSUFFICIENT_EVIDENCE; add missing evidence field
Tests: test_equal_prob_not_silently_assigned, test_missing_prob_explicit_policy
Risk: Fabricates underdog when Forebet has no favorite, violates "unambiguous favorite and underdog"
```

### Is favorite/underdog identity frozen from pre-event?

**Current:** Identity recomputed at training time from settled snapshot (which uses same probabilities as pre-event? `settled_event_snapshot` copies probability_1/2 from settled row). Settled row probabilities come from historical listing (pre-event). So frozen in sense of using stored prob, but candidate uses odds-first, not pre-event prob only. Also `HistoryIndex.context` uses strictly earlier dates (bisect_left) — good, no leakage from future.

**Required:** Identity frozen from pre-event Forebet probabilities, not recomputed from result.

**Finding:** Partially compliant (uses stored probs, prior-only history), but odds-first breaks frozen-from-prob requirement.

## 3. Features

### Table — Current Feature Families

| Feature family | Source | Timing | Odds-dependent? | Missingness policy | Used by |
|---|---|---|---|---|---|
| Forebet probabilities | `EventSnapshot.probability_1/2`, `draw_probability` | PRE_EVENT | No | `draw_probability_missing` flag, but p1/p2 no missing flag (0.0 fallback) | `facets.py`, all sport extractors |
| Probability gap / ratio / entropy / favorite dominance | Computed from p1/p2/draw | PRE_EVENT | No | No missing flag (0.0 if missing) | `facets.py:70-78`, `football.py`, `basketball.py` etc |
| Recent form wins/games, win_rate, PPG, draw_rate | `RecentForm`, `H2HStats`, facets `host_form`/`guest_form` | PRE_EVENT (history index prior-only) | No | No explicit missing flag for win_rate (0.0 fallback), `dog_recent_games` numeric | `facets.py:86-90`, `football.py:form_points_per_game` |
| Table position / standings gap | facets `standings_1`, `host_pos`, `standings_1_pts`, etc | PRE_EVENT | No | `_safe_float` → None → 0.0 + `_missing` flag | `football.py`, `basketball.py` etc |
| H2H total, win rate, draw rate, undefeated, period win rates | `H2HStats`, facets `h2h_total_games` | PRE_EVENT (prior-only) | No | 0.0 fallback, no missing flag for some | `facets.py:83-85`, sport extractors |
| Shots total/avg/blocked/on-off-target %/inside-box % | detail_facets `p1_total_shots_avg` etc, label-anchored regex | PRE_EVENT (detail page pre-event? needs verification) | No | `_safe_float` → missing flag | `football.py:_facet_pair` |
| Shots on target | same as above | PRE_EVENT? | No | missing flag | football |
| Possession / passes total/avg/accurate/accuracy % | detail_facets | PRE_EVENT? | No | missing flag | football |
| Attacks total+dangerous | detail_facets | PRE_EVENT? | No | missing flag | football |
| Goals/points scored and conceded avg, clean sheets, net efficiency | `p1_scored_avg`, `p1_conceded_avg`, etc | PRE_EVENT (detail) | No | missing flag | football, basketball |
| Home/away context (is_home_dog, venue splits) | candidate index, facets `home_split`/`away_split` | PRE_EVENT | No | No missing flag | all sports |
| Schedule difficulty / next-fixture difficulty 1-5 avg | facets `next_fixture_difficulty` | PRE_EVENT? | No | missing flag? | `detail_facets.py` Phase B |
| Sport-specific facets: weather, height, weight, reach, stance, strikes, takedowns, submissions, control, surface records, quarter data, period data, BTTS, totals, HT/FT, corners, cards, double chance, etc | `detail_facets.py`, sport modules | PRE_EVENT (weather, standings) / UNKNOWN for some | No (except price) | missing flag for most | sport modules |
| Odds and price-derived: displayed_odds, implied_probability, price_available, dog_price, favorite_price, draw_price, market_overround, dog_fair_implied_prob, favorite_fair_implied_prob, price_value_edge, legacy_robber_score, legacy_raw_confidence | `EventSnapshot.odds_1/2`, `RobberCandidate.price`, facets `best_odd_*` | PRE_EVENT (but prohibited as feature) | **Yes** | missing flag for price fields | `facets.py:80-82`, all sport extractors |
| Predicted total / score / sets / points | `predicted_total`, `predicted_score` | PRE_EVENT | No | `predicted_total_missing` flag | facets, sport |
| Streaks/trends text | facets `trend_en` | UNKNOWN (text, needs auditable representation, not opaque embeddings) | No | No numeric | not yet used as numeric, retained as facet |
| Result-only: final score, live_score, extra_time_score, penalty_score, status after start, post-event form | `SettledEvent.score_1/2`, `period_scores`, `detail_facets` tiebreakers | RESULT_ONLY | No | N/A — must be blocked | `settlement.py`, `feature_contracts.py:BLOCKED_COMMON` |

**Specific inspection:**

- Forebet probabilities: used, but fallback to 0.0 when missing, no missingness indicator for p1/p2 — violates "missing evidence is not zero"
- Probability gap: computed, no missing flag
- Recent form: uses prior-only HistoryIndex (good, no leakage), but win_rate 0.0 fallback when no games
- Win rates: same
- Table position: has missing flag
- H2H: prior-only, good, but 0.0 fallback
- Shots, shots on target, possession, passing, attacks, dangerous attacks: from detail_facets Phase B, label-anchored regex against flattened page text, claimed PRE_EVENT but MUST be verified against real Jina-HTML capture per `docs/STATE.md` — currently UNKNOWN timing risk
- Goals/points scored/conceded: from detail averages, PRE_EVENT? plausible
- Home/away: is_home_dog derived from candidate index, ok
- Schedule difficulty: next-fixture difficulty 1-5 avg, PRE_EVENT? needs verification
- Sport-specific: weather (PRE_EVENT), height/weight/reach/stance (PRE_EVENT for MMA), surface records (PRE_EVENT), quarter data (RESULT_ONLY? Q1-Q4 scores are live/result, must be excluded — but facets.py uses period_values from facets which could be result? Check: `basketball.py` uses `facets.get("period_values")` — if that's from pre-event? Actually period_values likely from detail? Need timing audit)
- Odds and price-derived: **Present as features, violates invariant 8**

**Result-only and unknown-timing fields identified:**

- `result`, `live_score` in COMMON_FACETS marked RESULT_ONLY/LIVE_ONLY — correctly blocked via `pre_event_facets()` filter
- `extra_time_score`, `penalty_score` in `settlement.py` retained as facets but marked RESULT_ONLY in docs/STATE.md — good, but need to ensure not entering `pre_event_facets`
- `period_values` (quarter scores) — if from settled page, RESULT_ONLY, must not enter features. Currently used in basketball extractor from pre_event_facets, so if that facet is marked PRE_EVENT incorrectly, leakage risk.
- `trend_en` text — UNKNOWN, needs deliberate representation, not opaque embeddings — currently not numeric, good
- `status` facet — UNKNOWN, must be classified per raw page state

**Finding summary:**

```
Finding: Odds are model features (displayed_odds, implied_probability, price_available, overround, fair probs, value edge)
Evidence: facets.py:80-82, football.py:149-151 optional_fields, basketball.py:116-122, etc.
Current: 15+ price-derived features per sport
Required: Odds excluded from features, missing odds has no effect
Smallest change: Remove all price_* features from build_numeric_features and sport extractors; keep price only as optional metadata in candidate receipt, not in vector; add test that feature dict has no price keys
Tests: test_no_price_features_in_vector, test_missing_odds_no_effect_on_features
Risk: Model learns bookmaker pricing, not Forebet evidence, violates mission, creates EV/Kelly drift

Finding: Missing evidence zero-filled for some core fields
Evidence: facets.py:70-78 dog_prob = event.probability(...) or 0.0, favorite_prob = max(...) or 0.0, probability_gap = fav-dog with 0 fallback
Current: 0.0 when missing, not explicit missingness
Required: Missing is not zero, needs indicator or explicit missing policy
Smallest change: Add missing flags for forebet_dog_probability, favorite_probability, probability_gap; keep 0.0 value but with flag; audit all _safe_float usages
Tests: test_missing_prob_not_zero_filled, test_missingness_indicator_present
Risk: Model misinterprets missing as strong favorite, biases underdog detection

Finding: Some detail facets timing unverified (shots, passes, possession, attacks, next-fixture difficulty, period_values)
Evidence: docs/STATE.md Phase B regex shapes built from observed page text but MUST be verified against real Jina-HTML capture; detail_facets.py label-anchored regex
Current: Marked PRE_EVENT but verification pending
Required: Each feature classified PRE_EVENT/RESULT_ONLY/UNKNOWN with proof from retained bytes or live probe
Smallest change: Audit detail_facets.py against retained detail HTML (if available) or run slumdog details --events ... --max-events 18 with Jina relay, record selectors, update facet_timing; move unverified to UNKNOWN and block from models
Tests: test_detail_facet_timing_proven, test_no_result_only_in_pre_event_facets
Risk: Result leakage, inflated backtest performance
```

## 4. Training and Validation

### What models exist?

**Evidence:** `ml_meta.py:ModelArtifact`, `training.py:train_registry`, `research.py:sport_model_card`, `feature_contracts.py:CONTRACTS`

- LogisticRegression with median imputer + StandardScaler, C=0.5, balanced class weight, liblinear, max_iter 2000
- One model per sport
- Feature contract = sorted union of keys from training rows

### Is training actually callable despite freeze?

**Evidence:** `training.py:195-202` checks `MODEL_TRAINING_ALLOWED` and `allow_research`; `research.py:244-247` same.

**Current:** Training frozen unless `--research-override` or `allow_research=True`. So callable via research gate on GH runners, but not in normal CLI. Compliant with freeze, but bypass exists.

### What split method is used?

**Evidence:** `ml_meta.py:_iter_walk_forward_splits` 84-108 groups by event_date, expanding prior, yields (prior.copy(), grouped[date]) for test dates.

**Current:** Walk-forward validation implemented, expanding-date, no random splits. Good.

**Required:** Chronological walk-forward, never random splits — compliant.

**Finding:** Walk-forward present, but need to ensure no random splits elsewhere. Grep for `train_test_split`, `KFold`, `ShuffleSplit` — none found in src (only sklearn Pipeline). So no random splits.

### Are random splits present?

**No**, only walk-forward.

### Is walk-forward validation implemented?

**Yes**, as above.

### What metrics are currently primary?

**Evidence:** `training.py:validation_summary` 116-170 computes brier, scans thresholds 0.35-0.60, requires n>=10, computes hit_rate, wilson_lower_90, priced_n, priced_roi. Eligible filter: n>=20, hit_rate>=0.45, wilson_lower>=0.35, (priced_n<10 or priced_roi>0). Best selected by (priced_n>=10 and ROI>0, wilson_lower, threshold).

**Current primary:** Brier, hit_rate, Wilson lower, threshold, plus priced ROI as gate. Also research markdown renders Brier, hit, Wilson, ROI.

**Required per Milestone 4:**
- top-1 daily underdog hit rate
- top-3 daily shortlist hit rate
- % days with at least one correct
- candidate-level precision
- recall of actual underdog wins
- lift over comparable Forebet underdogs
- precision@K
- Brier
- calibration by predicted upset band
- candidate coverage
- no-pick-day rate
- longest losing streak
- results by sport, league, probability-gap band
- Do NOT use ROI as primary

**Finding:**

```
Finding: ROI and price availability embedded in approval gate, metrics not aligned with required daily shortlist metrics
Evidence: training.py:148-166 eligible filter includes priced_roi>0 when priced_n>=10; best key includes priced_n>=10 and ROI>0 first
Current: Positive ROI required when 10+ priced rows, else hit-rate
Required: ROI not primary, optional odds retrospective only; required metrics include top-1/top-3 daily hit rate, % days at least one correct, precision, recall, lift, precision@K, Brier, calibration, coverage, no-pick rate, losing streak, by sport/league/gap band
Smallest change: Remove priced_n/priced_roi from eligibility and ranking; keep ROI only in separate retrospective report; implement required metrics in research.py and validation_summary; add calibration by predicted band
Tests: test_roi_not_in_approval_gate, test_required_metrics_present, test_calibration_by_band
Risk: Model selection biased to priced subset, turns into value-betting, violates invariants 10-11, misses operational objective (at least one correct per day)
```

### Are models calibrated?

**Current:** LogisticRegression outputs calibrated probabilities to some extent, but no explicit calibration (e.g., isotonic, Platt) or calibration-by-band report. Brier computed, but calibration by predicted upset band not reported.

**Required:** Brier + calibration by predicted upset band.

**Finding:** Partial — Brier present, calibration band missing.

### Is there per-sport approval?

**Current:** Registry has per-sport status (INSUFFICIENT, SHADOW_MODEL, OBSERVE_MODEL) and threshold, but no explicit user approval gate per sport as required by Milestone 7: "Approval is per sport. One sport passing does not authorize every sport."

**Finding:** Per-sport model exists, but approval process not documented as explicit user gate.

## 5. Daily Output

### Does pipeline produce candidates?

**Evidence:** `pipeline.py:build_shadow_robbers` 61-130, `cli.py`

**Current:** Yes, emits every high-confidence legacy Robber, sorted by ml_probability or legacy_confidence, no count cap originally but then filters raw_confidence<65 and h2h<3+recent<5 when no model. So can produce many, not limited to 1–3.

**Required:** Small daily shortlist preferably 1–3, at most 1–3 STRONG_UNDERDOG per day, fewer when weak, allow zero, never fill quota with weak.

**Finding:**

```
Finding: No 1-3 cap, no ranking by credible upset strength with evidence, no WATCHLIST/STRONG_UNDERDOG distinction
Evidence: pipeline.py:61 docstring "Emit every high-confidence legacy Robber; there is no count cap", sorted but not capped, reports.py renders all rows
Current: All qualifying candidates emitted
Required: Rank by strength, emit at most 1-3 STRONG_UNDERDOG, fewer when weak, allow zero, never fill quota
Smallest change: Add ranking function using model prob + lift + evidence, add shortlist policy with threshold selection out-of-sample, cap 1-3, add NO_STRONG_UNDERDOG daily status
Tests: test_daily_shortlist_max_3, test_never_fills_quota_with_weak, test_no_pick_day_valid
Risk: Operational overload, weak candidates dilute trust, violates daily objective
```

### How many? Can it output no candidates?

**Current:** Can be 0 to many (no cap). Yes can output none.

### Are outputs immutable?

**Evidence:** `ledger.py:freeze_candidates` preserves first frozen payload, append-only, frozen_at timestamp.

**Current:** Immutable ledger per date, but candidate receipt does not include full feature snapshot, missingness snapshot, candidate score/prob, evidence/reason codes, model version, source capture refs, generation timestamp, optional odds context as required by Milestone 6. It includes to_dict() which has score, reasons, legacy_confidence, price, implied, etc., plus frozen_at added, but not feature snapshot, missingness, model version, source SHA, etc.

**Required (Milestone 6):** Freeze before kickoff: event key, date/time, participants, favorite and underdog, Forebet probs, feature snapshot, missingness snapshot, candidate score and prob, evidence/reason codes, model version, source capture refs, generation timestamp, optional odds context.

**Finding:**

```
Finding: Immutable ledger exists but receipt incomplete vs Milestone 6
Evidence: ledger.py:freeze_candidates, contracts.py:RobberCandidate.to_dict()
Current: event_id, sport, participant_index, participant, opponent, score, reasons, raw_confidence, legacy_confidence, price, implied, legacy_prob, price_state, state, underdog_basis, forebet probs, ml fields, frozen_at
Missing: event date/time, kickoff, league, favorite/underdog explicit (only participant/opponent), draw prob, model underdog-win prob (ml_probability present but not lift), strength score (score present), supporting/contradicting/missing evidence (only reasons), candidate status new, model/version id, source/provenance identifiers (raw_sha256 not in candidate), feature snapshot, missingness snapshot, generation timestamp (frozen_at is generation, ok), optional price context separate
Smallest change: Define new price-free candidate dataclass with required fields, update freeze to include full snapshot, keep legacy for forensic comparability but add new contract
Tests: test_immutable_receipt_has_required_fields, test_feature_snapshot_frozen, test_provenance_present
Risk: Cannot reproduce settlement, cannot audit forward performance honestly
```

### Is model/version/source provenance recorded?

**Partial:** EventSnapshot has raw_sha256, source_url, captured_at, but candidate doesn't include it. Model artifact has contract_hash, trained_through, but candidate only has ml_train_rows etc., not model version identifier.

### Is supporting and contradicting evidence available?

**Current:** Only supporting reasons list (e.g., "Heavy fav @1.35", "H2H 30%"). No contradicting, no missing evidence explicit.

**Required:** Every candidate explains supporting and contradicting and missing evidence.

**Finding:**

```
Finding: Only supporting evidence, no contradicting or missing
Evidence: magolide.py:reasons list, football.py:reasons, reports.py:reasons rendering
Current: Reasons = supporting factors that added score
Required: Supporting + contradicting + missing evidence explicit
Smallest change: Add fields supporting_evidence, contradicting_evidence, missing_evidence to candidate; populate from feature gaps and opposing signals (e.g., favorite recent form strong = contradicting)
Tests: test_candidate_has_supporting_contradicting_missing
Risk: Cannot assess credible upset strength honestly, violates end product requirement
```

### Is settlement reproducible?

**Evidence:** `settlement.py:parse_*_settled` parses frozen captures, `append_settled_from_capture` appends unique facts with key (sport, event_id, event_date), sorts.

**Current:** Settlement from frozen captures, but legacy ledgers have duplicate issues (279 byte-identical extras, 4 cross-date identical pairs, hockey 278977 conflict). New backfills have write guard and fail loudly on conflicting same-key payloads. So reproducible for new data, but legacy needs audit.

**Required:** Settlement reproducible, draws count as failed UNDERDOG_WIN, voids excluded.

**Finding:** Mostly reproducible for new, but legacy conflicts unresolved, need explicit policy.

### Does a draw settle as failure for UNDERDOG_WIN?

**Current:** For draw-capable sports, yes (underdog_won=0 when winner_index==0). For two-way, excluded. So draw = failure for draw-capable, which matches required. But need to ensure pipeline that settles daily shadow selections counts draws as failed, not void.

**Finding:** Compliant for draw-capable, but needs test and doc.

## 6. Exact Final Audit Format — Gaps Summary

### Gap 1: Odds-first underdog identity

```
Finding: Underdog identity is odds-first, not Forebet-probability-first
Evidence: magolide.py:48-73 identify_underdog, all sport detectors 406-418 etc, training.py:46
Current behavior: Higher odds → underdog, fallback to pick then prob then form
Required behavior: Lower Forebet prob = underdog, odds optional metadata only, equal prob explicit policy
Smallest proposed change: New function identify_underdog_by_forebet_prob(prob1, prob2, draw_prob) returning (favorite, underdog, gap, status); remove odds branch; update magolide and all sport detectors to use it; add explicit INSUFFICIENT_EVIDENCE when equal or missing probs
Tests required: test_underdog_identity_uses_forebet_prob_only, test_equal_prob_explicit_policy, test_odds_do_not_affect_basis
Risk if unchanged: Violates invariants 5-11, price-first system, mission failure
```

### Gap 2: Missing odds lowers threshold

```
Finding: Missing odds lowers score threshold from 20 to 11, increasing volume
Evidence: magolide.py:194, football.py: threshold line
Current: Unpriced easier to qualify
Required: Same threshold regardless of odds, missing odds no effect
Smallest change: Single threshold, remove odds-dependent threshold, remove odds value factors from score
Tests: test_missing_odds_no_threshold_change, test_price_state_not_eligibility
Risk: Weak/fabricated candidates on no-price days, violates no-quota-fill
```

### Gap 3: Price-derived features in model

```
Finding: Odds are model features
Evidence: facets.py:80-82 displayed_odds, implied_probability, price_available; sport extractors fb_dog_price, market_overround, etc.
Current: 15+ price features per sport
Required: Odds excluded from features, not model inputs
Smallest change: Remove all price_* keys from build_numeric_features and sport to_dict; keep price only in receipt as optional context; update feature_contracts to exclude price; add test no price keys in vector
Tests: test_no_price_features_in_vector, test_missing_odds_no_effect
Risk: Model learns bookmaker, not upset evidence, violates invariant 8, turns into EV engine
```

### Gap 4: Missing evidence zero-filled

```
Finding: Forebet probabilities and gaps zero-filled when missing, no missingness indicator for core fields
Evidence: facets.py:70-78 dog_prob = or 0.0, favorite_prob = max(...) or 0.0
Current: 0.0 fallback
Required: Missing is not zero, needs indicator or explicit policy
Smallest change: Add missing flags for core prob fields, keep 0.0 value but with flag; audit all fallbacks
Tests: test_missing_prob_has_flag, test_missing_not_zero
Risk: Bias, misinterprets missing as strong favorite
```

### Gap 5: Equal prob silent assignment

```
Finding: Equal Forebet probabilities silently assigned via recent form / p1 default
Evidence: magolide.py:65-73
Current: Defaults to participant 1 when equal
Required: Explicit policy, INSUFFICIENT_EVIDENCE or REJECTED_SOURCE_CONFLICT, not silent
Smallest change: Detect equal prob (|p1-p2|<epsilon) and return None → candidate status INSUFFICIENT_EVIDENCE with missing evidence explicit
Tests: test_equal_prob_not_silently_assigned
Risk: Fabricates underdog when no favorite, violates unambiguous requirement
```

### Gap 6: No explicit NO_STRONG_UNDERDOG and small shortlist policy

```
Finding: No explicit daily status NO_STRONG_UNDERDOG, no 1-3 cap, no STRONG_UNDERDOG/WATCHLIST distinction
Evidence: contracts.py CandidateState, pipeline.py no cap, reports.py NO QUALIFYING ROBBERS only
Current: Empty list = no pick, all qualifying emitted
Required: Rank by strength, emit at most 1-3 STRONG_UNDERDOG, allow zero, explicit NO_STRONG_UNDERDOG, never fill quota
Smallest change: Redefine CandidateState to STRONG_UNDERDOG, WATCHLIST, INSUFFICIENT_EVIDENCE, REJECTED_SOURCE_CONFLICT, NO_STRONG_UNDERDOG; add daily shortlist policy function ranking by model prob + lift + evidence; cap 1-3; add daily receipt with status
Tests: test_shortlist_max_3, test_no_pick_explicit_status, test_never_fills_quota
Risk: Operational overload, weak candidates, cannot measure daily objective
```

### Gap 7: ROI and price in approval gates

```
Finding: ROI and priced_n embedded in model approval gate
Evidence: training.py:148-166 eligible filter priced_roi>0 when priced_n>=10, best key includes ROI first
Current: Positive ROI required when 10+ priced rows
Required: ROI not primary, optional retrospective only; required metrics include daily hit rates, precision, recall, lift, etc.
Smallest change: Remove priced_n/priced_roi from eligibility and ranking; implement required metrics in research.py; keep ROI only in separate retrospective report
Tests: test_roi_not_in_gate, test_required_metrics_present
Risk: Value-betting drift, violates invariants 10-11, misaligned with at least-one-correct-per-day objective
```

### Gap 8: Incomplete immutable receipt

```
Finding: Ledger immutable but receipt missing required fields per Milestone 6
Evidence: ledger.py, contracts.py to_dict
Current: Has score, reasons, legacy confidence, price, but missing feature snapshot, missingness, model/version id, source SHA, draw prob, lift, supporting/contradicting/missing evidence separation, explicit status
Required: Full freeze before kickoff: event key, date/time, participants, fav/underdog, Forebet probs, feature snapshot, missingness snapshot, candidate score/prob, evidence codes, model version, source refs, generation timestamp, optional odds context
Smallest change: Define new price-free candidate dataclass with all required fields; update freeze_candidates to include snapshots; keep legacy for forensic comparability
Tests: test_receipt_has_required_fields, test_feature_snapshot_frozen, test_provenance_present
Risk: Cannot settle reproducibly, cannot audit forward performance honestly
```

### Gap 9: Only supporting evidence, no contradicting/missing

```
Finding: Reasons list only supporting, no contradicting or missing evidence
Evidence: magolide.py reasons, football.py reasons
Current: Reasons = factors that added score
Required: Supporting + contradicting + missing evidence explicit per candidate
Smallest change: Add supporting_evidence, contradicting_evidence, missing_evidence fields; populate from opposing signals (e.g., favorite strong recent form = contradicting, missing standings = missing)
Tests: test_candidate_has_supporting_contradicting_missing
Risk: Violates end product requirement, cannot assess credible upset strength
```

### Gap 10: Detail facet timing unverified

```
Finding: Some detail facets (shots, passes, possession, attacks, next-fixture difficulty, period_values) timing unverified, potential RESULT_ONLY leakage
Evidence: docs/STATE.md Phase B regex shapes need verification against real Jina-HTML capture; basketball.py uses period_values from facets
Current: Marked PRE_EVENT but verification pending
Required: Each feature classified PRE_EVENT/RESULT_ONLY/UNKNOWN with proof
Smallest change: Audit detail_facets.py against retained detail HTML or live Jina relay capture, update facet_timing, move unverified to UNKNOWN and block from models
Tests: test_no_result_only_in_pre_event, test_detail_timing_proven
Risk: Result leakage, inflated backtest, violates timing contract
```

## 7. Staged Implementation Plan (No Code Yet — Approval Required)

### Stage 0: Documentation (Done, corrections applied)

- Milestone 0 COMPLETE, banners added, price coverage clarified as reference only, not gate.

### Stage 1: Define Price-Free Candidate Contract (Milestone 2)

- Propose exact dataclass for new candidate (price-free) with all required fields from prompt
- Define label contract: two-way vs draw-capable, void excluded, equal-prob explicit policy
- Define eligibility: valid settled outcome for training, usable Forebet probs, unambiguous fav/underdog, sufficient pre-event evidence, no source conflict, no result leakage, no odds requirement
- Present edge-case tests before implementation

### Stage 2: Fix Underdog Identity (Smallest contained replacement)

- Implement `identify_underdog_by_forebet_prob()` using only probability_1/2, draw_prob as context
- Replace `identify_underdog()` calls in `magolide.py` and all sport detectors with new function, or make old function delegate with deprecation
- Add explicit handling for equal/missing probs → INSUFFICIENT_EVIDENCE
- Update tests

### Stage 3: Remove Odds from Features and Scoring

- Remove price-derived features from `facets.py` and all sport `to_dict()` methods
- Remove odds-dependent threshold and odds value factors from score
- Ensure missing odds has no effect on eligibility/confidence
- Update `feature_contracts.py` to exclude price, keep `MODEL_TRAINING_ALLOWED=False` until approved
- Add tests for no price keys in vector

### Stage 4: Feature Timing Audit

- Inventory every potential feature from `detail_facets.py`, sport modules, `forebet.py` parsers
- Classify each as PRE_EVENT / RESULT_ONLY / UNKNOWN with evidence (retained bytes or live probe URL/date/route/result)
- Ensure `pre_event_facets()` filter blocks RESULT_ONLY/UNKNOWN
- Add missingness indicators for all optional numeric features
- Produce feature table with source, timing, missingness, sport applicability

### Stage 5: Label and Settlement Verification

- Verify `build_training_rows` uses new underdog identity, draw = failed for draw-capable, void excluded, equal-prob explicit
- Verify `HistoryIndex` prior-only (already good)
- Verify settlement reproducible and counts draws as failed
- Add tests for draw settlement as failure

### Stage 6: Daily Shortlist Policy

- Redefine `CandidateState` to STRONG_UNDERDOG, WATCHLIST, INSUFFICIENT_EVIDENCE, REJECTED_SOURCE_CONFLICT, NO_STRONG_UNDERDOG
- Implement ranking by model prob + lift + evidence strength
- Cap at 1-3 STRONG_UNDERDOG per day, fewer when weak, allow zero, never fill quota
- Update `reports.py` and `ledger.py` to emit explicit daily status and immutable receipt with full snapshots

### Stage 7: Baselines Before Complex Models (Training remains frozen until user unlocks)

When unlocked:
- Implement transparent baselines: Forebet underdog-prob baseline, probability-gap baseline, recent-form differential baseline, existing Ma Golide heuristic baseline, simple interpretable model
- Evaluate chronologically walk-forward, never random splits
- Required metrics: top-1 daily hit rate, top-3 shortlist hit rate, % days at least one correct, precision, recall, lift, precision@K, Brier, calibration by band, coverage, no-pick rate, longest losing streak, by sport/league/gap band
- Do not use ROI as primary; optional odds retrospective separate

### Stage 8: Immutable Shadow Selections

- Freeze before kickoff: event key, date/time, participants, fav/underdog, Forebet probs, feature snapshot, missingness snapshot, score/prob, evidence codes, model version, source refs, generation timestamp, optional odds context
- After settlement: append outcome, never edit original, count draws as failed, exclude voids, report daily and cumulative

### Stage 9: Readiness Gate

- Price-free candidate contract implemented
- Labels tested
- Timing/leakage enforced
- At least one sport passes walk-forward validation
- Shortlist thresholds selected out-of-sample
- Daily output supports no-pick
- Immutable receipts exist
- Settlement reproducible
- Forward shadow tracking
- docs/STATE.md and HANDOFF.md accurate
- User authorizes operational output per sport

## Risk If Not Fixed

- Remains odds-first Robber system, not price-free underdog-win system — violates permanent mission
- Model learns bookmaker prices, not credible upset evidence — drifts to EV/Kelly, violates do-not-do list
- Silent equal-prob assignment fabricates candidates — violates unambiguous requirement
- ROI gate biases to priced subset, misses operational objective (at least one correct per day)
- No small shortlist, no explicit no-pick, no supporting/contradicting/missing evidence — not trustworthy daily product
- Incomplete immutable receipts — cannot measure forward performance honestly

## Next Action Required

User approval of this audit and staged plan before any code changes. Training remains frozen. No files deleted. PR #6 remains unmerged pending Milestone 1 approval.

---
**Verification:** Read-only audit, no code changes, `grep` and file reads only, no network fetches, no training runs.
