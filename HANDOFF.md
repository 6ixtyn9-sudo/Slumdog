# Slumdog Living Handoff

**Last updated:** 2026-08-24 (UTC) — Milestone 2 implementation (price-free identity, label, contracts)
**Branch:** `arena/01a033af-slumdog`
**HEAD SHA:** `8f38647` (Milestone 1 audit) → now `+ Milestone 2` (pending commit)
**Phase:** Milestone 2 — price-free identity, label, and contract foundation (Milestone 0 COMPLETE, Milestone 1 COMPLETE approved)
**Mission:** Slumdog identifies a small daily shortlist of participants that Forebet considers underdogs but whose available pre-event evidence indicates a credible outright-win upset.
**PR:** #6 https://github.com/6ixtyn9-sudo/Slumdog/pull/6 — OPEN, do not merge until user authorizes after Milestone 2 approval
**Training:** FROZEN (`feature_contracts.py: MODEL_TRAINING_ALLOWED=False`)

## Product Invariants (from AGENTS.md)

- UNDERDOG_WIN outright only, never selects draws, draw=failed in draw-capable
- Odds optional metadata only — not required, not feature, not gate, missing must not lower confidence
- Never force pick; NO_STRONG_UNDERDOG is daily result not candidate state; never promise profit/guaranteed wins
- Training frozen until user approves dataset/target/timing/validation
- Main only permanent branch; Arena delivery only; never force-push main
- Assessment vs selection separated, odds outside core assessment, no ranking by model prob before approval

## Milestone 0 — COMPLETE (Approved with 5 Corrections, No Deletions)

User approved 2026-08-24: move STATE.md to docs/STATE.md, add AGENTS.md, canonical read order, classifying docs, retaining forensic as historical, main only permanent branch, locking price-free UNDERDOG_WIN mission, leaving training frozen. No deletions authorized.

Corrections applied (commit 259495c):
1. Advance phase to Milestone 1 audit, COMPLETE, FROZEN
2. Historical banner to MA_GOLIDE_ROBBER_FORENSIC.md, CURRENT/REFERENCE banners to FOREBET_DEPTH_AUDIT.md and FOREBET_FACET_ANALYSIS_PLAN.md
3. Price docs: FOREBET_PRICE_COVERAGE.json reference evidence only, not readiness gate; sparse prices parked observations not blockers
4. Operational README preserved (install, pytest, capture, backfill, depth-sweep, backfill-sport, analyze, parse, details, enrich)
5. Verify move: test ! -e STATE.md PASS, test -e docs/STATE.md PASS, grep shows only docs/STATE.md refs

## Milestone 1 Audit — COMPLETE (Approved as Evidence Record with 8 Refinements)

**Deliverable:** `docs/MILESTONE1_AUDIT.md` (885 lines + 143 lines corrections annotation) — read-only audit, no code changes.

Central problem exposed: system can operate without odds but underdog identity, scoring, feature vectors, thresholds, research approval still materially odds-first.

10 gaps documented with Finding/Evidence/Current/Required/Smallest change/Tests/Risk:
1. Odds-first underdog identity (magolide.py:48-73, all sport detectors)
2. Missing odds lowers threshold 20→11
3. Price-derived features in model (facets.py:80-82, sport to_dict)
4. Missing evidence zero-filled (facets.py:70-78 or 0.0)
5. Equal prob silent assignment via recent form / p1 default
6. No explicit NO_STRONG_UNDERDOG and small shortlist policy (no 1-3 cap)
7. ROI and price in approval gates (training.py:148-166)
8. Incomplete immutable receipt vs Milestone 6 (ledger.py)
9. Only supporting evidence, no contradicting/missing
10. Detail facet timing unverified (period_values, shots, passes, etc.)

Approved plan corrections (user refinement, added as annotation without rewriting evidence):
1. NO_STRONG_UNDERDOG is daily result not candidate state — separate UnderdogAssessmentStatus (STRONG_UNDERDOG/WATCHLIST/INSUFFICIENT_EVIDENCE/REJECTED_SOURCE_CONFLICT/INELIGIBLE) vs DailyShortlistStatus (CANDIDATES_FOUND/NO_STRONG_UNDERDOG/SOURCE_FAILURE), daily report with zero candidates explicit JSON status=NO_STRONG_UNDERDOG candidates=[]
2. Do not destructively repurpose legacy CandidateState yet — introduce clearly named price-free contracts, preserve legacy while new path tested, mark old as legacy, remove only after equivalence tests. Names: UnderdogAssessmentStatus, StrongUnderdogAssessment, DailyUnderdogShortlist, DailyShortlistStatus, avoid ambiguous RobberCandidate reuse
3. Separate assessment from selection — assessment contains fav/underdog identity, Forebet probs, evidence completeness, strength/prob estimate when available, supporting/contradicting/missing evidence, event-level status; daily shortlist contains generation date/time, model/heuristic version, source receipt, ordered selected assessments, explicit daily status, number considered/rejected, rejection summary
4. Odds outside core assessment — do not include price-derived fields in identity/eligibility/strength/features/confidence/ranking/missing score; optional_price_context isolated block may be absent, no other field depends on it
5. Do not rank by model prob before model approved — training frozen, no approved probability yet; before approval produce contract fixtures, historical labels, transparent baselines, shadow output marked baseline/research, no operational probability claim; use neutral baseline_strength_score for deterministic baselines, not slumdog_underdog_probability unless genuinely calibrated
6. Treat suspected leakage as priority blocker — period_values timing=UNKNOWN, prohibited as feature until proven pre-event; trace where enters event, whether predicted or completed scoring, where basketball consumes it, whether available before kickoff, whether settlement-derived period data flows back; football detail fields need timing evidence
7. Missing values remain unknown — classify each zero as genuine zero vs legacy unknown encoded as zero vs safe default vs leakage fallback; preferred representation: missing None/NaN + explicit indicator + imputation inside pipeline; do not change globally in one patch
8. Daily success needs several metrics — top_1_daily_hit_rate, top_3_daily_any_hit_rate, days_with_at_least_one_selected_winner, selected_candidates_per_day, no_pick_day_rate, candidate_precision together; top_3 alone inflates with more candidates, must include number selected; draw remains failed UNDERDOG_WIN

Classification: MILESTONE1_AUDIT.md = CURRENT during migration, REFERENCE after gaps resolved, STATE.md remains concise authority.

## Milestone 2 — Price-Free Identity, Label, and Contract Foundation (Current, Approved Next Work Only)

**Scope:** Implement only Milestone 2A–2D, do NOT proceed into feature-vector changes, training, ranking thresholds, or daily production yet. Training remains frozen.

### Files Changed

- **New module:** `src/slumdog/underdog.py` (new, ~450 lines)
  - `UnderdogAssessmentStatus` enum: STRONG_UNDERDOG, WATCHLIST, INSUFFICIENT_EVIDENCE, REJECTED_SOURCE_CONFLICT, INELIGIBLE (event-level)
  - `DailyShortlistStatus` enum: CANDIDATES_FOUND, NO_STRONG_UNDERDOG, SOURCE_FAILURE (daily-level)
  - `ForebetUnderdogIdentity` dataclass: favorite_index, underdog_index, favorite_probability, underdog_probability, probability_gap, eligible, ineligibility_reason, draw_probability, to_dict()
  - Pure function `identify_forebet_underdog(probability_1, probability_2, draw_probability=None)` — validates present/finite/[0,1], higher=favorite lower=underdog, draw does not determine identity, equal→no underdog EQUAL_PROBABILITY, missing→MISSING_PROBABILITY, non-finite→NON_FINITE, out-of-range→OUT_OF_RANGE, no fallback to odds/pick/form, no arbitrary gap threshold
  - `UnderdogLabelResult` dataclass: label 1/0/None, eligible, exclusion_reason, is_draw, is_void, is_source_conflict, winner_index, favorite_index, underdog_index
  - Pure function `label_underdog_outcome(*, sport, favorite_index, underdog_index, winner_index, disposition, draw_possible, source_conflict, has_eligible_identity)` — draw-capable: underdog win 1, fav win 0, draw 0, void excluded; two-way: underdog win 1, fav win 0, unexpected draw excluded UNEXPECTED_DRAW_FOR_TWO_WAY, void excluded VOID; also excludes NO_ELIGIBLE_IDENTITY, EQUAL_PROBABILITY, INVALID_WINNER_INDEX, SOURCE_CONFLICT, WINNER_MISMATCH, explicit reason
  - `StrongUnderdogAssessment` dataclass: event_id, sport, event_date, participant_1/2, favorite_index/underdog_index, favorite_name/underdog_name, favorite_probability/underdog_probability, draw_probability, probability_gap, status, supporting_evidence tuple, contradicting_evidence tuple, missing_evidence tuple, source_url, raw_sha256, captured_at, assessment_version price-free-v1, reserved optional slumdog_underdog_probability/probability_lift/baseline_strength_score must remain None until approved, isolated optional_price_context dict may be absent, no price fields in core, validation fav prob > dog prob, fav/dog differ, 1/2 only (draw never selected)
  - `DailyUnderdogShortlist` dataclass: target_date ISO, generated_at ISO timezone-aware, status DailyShortlistStatus, assessments_considered int, strong_candidates tuple, watchlist_candidates tuple, rejection_counts dict, assessment_version, source_receipt, validation NO_STRONG_UNDERDOG must have zero strong_candidates, no draw selection
  - Helper `build_assessment_from_identity()` — builds assessment from identity, returns None if not eligible

- **New tests:** `tests/test_price_free.py` (new, 30 tests)
  - Identity (10): p1 favorite, p2 favorite, equal prob, missing prob, non-finite, out-of-range, draw larger not selected, odds disagree no effect, pick disagrees no effect, form disagrees no effect
  - Labels (11): draw-capable underdog win, fav win, draw=0, two-way underdog win, fav win, two-way draw excluded, void excluded, equal prob excluded, missing prob excluded, invalid winner excluded, source conflict excluded
  - Contracts (9+): round-trip serialization, no price field required, missing optional evidence accepted, no-pick serializes zero candidates, cannot contain fake draw, provenance optional only where legacy lacks, reserved fields None until approved, no fake candidate for no-pick status, optional_price_context isolated

- **Updated docs:**
  - `docs/STATE.md` — phase advanced to Milestone 2, Milestone 1 COMPLETE, merged work includes PR #6 branch, blockers updated (odds-first still in legacy path, new price-free path implemented but not yet integrated, label foundation done integration pending, draw settlement verified in pure function, feature timing leakage priority blocker, validation/shortlist not yet approved, training frozen), next milestone Milestone 2 complete then Milestone 3 feature timing, links include MILESTONE1_AUDIT.md, verification receipt updated to 222 tests
  - `docs/README.md` — inventory adds MILESTONE1_AUDIT.md as CURRENT during migration REFERENCE after gaps resolved, Notes for Next Milestone updated with 8 refinements and Milestone 2 scope
  - `docs/MILESTONE1_AUDIT.md` — added Approved Plan Corrections section (8 refinements) without rewriting evidence, 143 lines added, blank EOF fixed
  - `README.md` — Status updated to Milestone 1 then Milestone 2, training FROZEN

### New Contracts

- `UnderdogAssessmentStatus`: STRONG_UNDERDOG, WATCHLIST, INSUFFICIENT_EVIDENCE, REJECTED_SOURCE_CONFLICT, INELIGIBLE
- `DailyShortlistStatus`: CANDIDATES_FOUND, NO_STRONG_UNDERDOG, SOURCE_FAILURE
- `ForebetUnderdogIdentity`: favorite_index, underdog_index, favorite_probability, underdog_probability, probability_gap, eligible, ineligibility_reason, draw_probability
- `UnderdogLabelResult`: label, eligible, exclusion_reason, is_draw, is_void, is_source_conflict, winner_index, favorite_index, underdog_index
- `StrongUnderdogAssessment`: event_id, sport, event_date, participant_1/2, favorite_index/underdog_index, favorite_name/underdog_name, favorite_probability/underdog_probability, draw_probability, probability_gap, status, supporting/contradicting/missing evidence, source_url, raw_sha256, captured_at, assessment_version, reserved slumdog_underdog_probability/probability_lift/baseline_strength_score (None until approved), optional_price_context isolated
- `DailyUnderdogShortlist`: target_date, generated_at, status, assessments_considered, strong_candidates tuple, watchlist_candidates tuple, rejection_counts, assessment_version, source_receipt

### Identity Policy (Milestone 2A)

- Validate both participant probabilities present, finite, in [0,1]
- Higher prob = favorite, lower = underdog
- Draw prob does NOT determine identity, never selected (index never 0)
- Exact equal → no underdog, ineligibility_reason=EQUAL_PROBABILITY
- Missing/invalid → no underdog, MISSING_PROBABILITY / NON_FINITE / OUT_OF_RANGE / INVALID
- No fallback to odds, forebet_pick, recent form — pure function signature only prob1, prob2, optional draw
- No arbitrary gap threshold — minimum strength/gap belongs to later selection policy
- Explicit reason for ineligibility

### Label Policy (Milestone 2B)

- Draw-capable (football, handball, cricket, esoccer): underdog win 1, favorite win 0, draw 0, void excluded
- Two-way (basketball, tennis, hockey, baseball, american_football, rugby, volleyball, mma, esports): underdog win 1, favorite win 0, unexpected draw excluded UNEXPECTED_DRAW_FOR_TWO_WAY, void excluded VOID
- Also excluded: no eligible identity NO_ELIGIBLE_IDENTITY, equal prob EQUAL_PROBABILITY, missing prob via NO_ELIGIBLE_IDENTITY, invalid winner INVALID_WINNER_INDEX, source conflict SOURCE_CONFLICT, winner mismatch WINNER_MISMATCH_IDENTITY
- Returns explicit exclusion reason, not only None
- Separate from training orchestration — pure function

### Legacy Behavior Preserved

- Legacy `CandidateState` (SHADOW_UNPRICED/PRICED/CERTIFIED/REJECTED) and `RobberCandidate` remain in `contracts.py` unchanged, marked as legacy in `underdog.py` header comment, not destructively repurposed
- `magolide.py` and all sport detectors unchanged (still odds-first) — new path tested alongside, not replacing until explicitly approved cleanup after equivalence/migration tests
- `facets.py`, `training.py`, `research.py`, `pipeline.py` unchanged — no feature-vector, threshold, ranking, model approval, daily production changes per Milestone 2 scope
- `MODEL_TRAINING_ALLOWED=False` remains frozen

### Tests Added

- `tests/test_price_free.py`: 30 tests covering identity (10), labels (11), contracts (9)
  - Identity: test_identity_participant_1_favorite, test_identity_participant_2_favorite, test_identity_equal_probabilities, test_identity_missing_participant_probability, test_identity_non_finite_probability, test_identity_out_of_range_probability, test_identity_draw_probability_larger_does_not_become_selected, test_identity_odds_disagree_has_no_effect, test_identity_forebet_pick_disagrees_has_no_effect, test_identity_recent_form_disagrees_has_no_effect
  - Labels: test_label_draw_capable_underdog_wins, test_label_draw_capable_favorite_wins, test_label_draw_capable_draw_is_zero, test_label_two_way_underdog_wins, test_label_two_way_favorite_wins, test_label_two_way_unexpected_draw_excluded, test_label_void_excluded, test_label_equal_probability_excluded, test_label_missing_probability_excluded, test_label_invalid_winner_index_excluded, test_label_source_conflict_excluded
  - Contracts: test_assessment_round_trip_serialization, test_assessment_no_price_field_required, test_assessment_missing_optional_detail_evidence_accepted, test_no_pick_daily_shortlist_serializes_with_zero_candidates, test_candidate_shortlist_cannot_contain_fake_draw_selection, test_provenance_optional_only_where_legacy_lacks, test_assessment_reserved_fields_remain_none_until_approved, test_daily_shortlist_no_fake_candidate_for_no_pick_status, test_optional_price_context_isolated

### Exact Test Count

- **Full suite:** 222 passed (192 legacy + 30 new) — `python3 -m pytest -v` 52.89s
- **Focused:** `tests/test_price_free.py` 30 passed 0.09s
- **Collection:** 222 tests collected

### Full Verification

```bash
python3 -m pytest -q                    # 222 passed
python3 -m py_compile scripts/*.py src/slumdog/*.py tests/*.py  # ok
python3 -m pyflakes src/slumdog/underdog.py tests/test_price_free.py  # ok (after cleanup of unused imports)
python3 -m pyflakes scripts src/slumdog tests  # ok (no findings after cleanup)
git diff --check                        # ok (fixed blank EOF in MILESTONE1_AUDIT.md)
test ! -e STATE.md && test -e docs/STATE.md  # PASS
grep -Rni --exclude-dir=.git --exclude='*.pyc' 'STATE\.md' .  # only docs/STATE.md refs
```

### Remaining Gaps (After Milestone 2)

From `MILESTONE1_AUDIT.md` 10 gaps, 2 partially addressed by foundation (identity/label/contracts foundation done but integration pending):

- **Gap 1 & 5 partially:** Pure identity implemented but legacy odds-first still in pipeline — needs integration in later stage after tests
- **Gap 2:** Missing odds lowers threshold — still in legacy magolide, not yet removed (out of scope for Milestone 2)
- **Gap 3:** Price-derived features — still in legacy facets, not yet removed (out of scope)
- **Gap 4:** Missing zero-fill — still in legacy facets, not yet removed (out of scope, will be staged per family)
- **Gap 6:** No 1-3 cap, no explicit NO_STRONG_UNDERDOG daily status — new contracts define daily status but pipeline not yet using them
- **Gap 7:** ROI gate — still in legacy validation, not yet removed
- **Gap 8 & 9:** Incomplete receipt and only supporting evidence — new contracts define full fields but ledger/reports not yet using them
- **Gap 10:** Detail timing unverified — period_values timing=UNKNOWN, prohibited as feature until proven, still priority blocker

Next approved: Milestone 3 — feature and timing contract (inventory every potential feature, classify PRE_EVENT/RESULT_ONLY/UNKNOWN with proof, odds excluded, missingness policy, feature table). Do not proceed into training/ranking/daily production until Milestone 2 approved.

## Open / Parked / Unresolved (Updated)

**Open (Milestone 2 approval):**
- User review of Milestone 2 implementation: Finding addressed, Files changed, New contracts, Identity policy, Label policy, Legacy preserved, Tests added, Exact test count, Full verification, Remaining gaps
- Approval to proceed to Milestone 3 feature timing contract

**Parked:**
- American football odds probe `scripts/probe_american_football_odds.py` — do not run before ~2026-09-10
- Complex ensembles — baselines first after unlock
- Esoccer separate audit
- Dropped football getrs.php keys audit
- Sparse hockey/rugby/volleyball/handball pricing re-check on in-season top-league dates
- Auto-rewrite/compact legacy ledgers — prohibited without explicit authorization
- Feature-vector changes, threshold changes, ranking, model approval, daily production — out of scope for Milestone 2, remain frozen

**Unresolved Evidence (preserved):**
- 4 cross-date identical pairs: basketball:198045, 198046, football:2041406, volleyball:96303
- Hockey 278977 conflict 1-6 vs 0-4
- MMA 11 void+priced rows
- Absent raw bytes for 7 suspicious dates
- Football DC token 21, scorer subtype unknown
- Football 963-date backfill gap quantification + replay feasibility
- Detail facet timing unverified (shots, passes, possession, attacks, next-fixture difficulty, period_values) — needs Jina-HTML proof, period_values = UNKNOWN, prohibited as feature until proven

## PR State

- **Branch:** `arena/01a033af-slumdog`
- **Base:** `main` @ `2e3daa40b60ed520a0bcb2f178ef4219fad4d026`
- **PR:** #6 https://github.com/6ixtyn9-sudo/Slumdog/pull/6 — OPEN, do not merge until user authorizes after Milestone 2 approval
- **Commits:** f4d2946 Milestone 0 move+constitution, 259495c Milestone 0 final corrections, 8f38647 Milestone 1 audit, + Milestone 2 (pending commit: underdog.py + test_price_free.py + docs updates)
- **Mergeability:** No conflicts (doc-only + new module + tests, no legacy code changes)
- **User authorization:** Milestone 0 approved, Milestone 1 approved as evidence record with 8 refinements, Milestone 2 pending approval — do not merge, do not change feature vectors/thresholds/ranking/model approval/daily production until approved

## Evidence Language Compliance

- Verified from code: file paths, function names, line numbers, grep results, test names
- Verified from executed probe: pytest 222 passed, py_compile ok, pyflakes ok, diff-check ok, move verification PASS
- Plausible but unverified: detail facet timing — marked UNKNOWN, not claimed as PRE_EVENT proven
- Unresolved conflict: retained competing facts (duplicate audits) without silently choosing one

## After Merge: Next Session Starts Here (Updated for Milestone 3)

Read `AGENTS.md` first, then `README.md`, then `docs/STATE.md`, then `HANDOFF.md`, then `docs/FOREBET_DEPTH_AUDIT.md`, then `docs/MILESTONE1_AUDIT.md`, then `src/slumdog/underdog.py`, then `tests/test_price_free.py`, then relevant source/tests.

**Exact next task (Milestone 3 — define feature and timing contract):**

DISCUSS BEFORE CODING. Inventory every potential feature and classify it:

- PRE_EVENT — allowed
- RESULT_ONLY — prohibited
- UNKNOWN — prohibited until verified

Model should use what Forebet provides, including where available:

- Forebet participant probabilities
- probability gap
- draw probability as context, never as selected outcome
- recent wins/draws/losses
- recent win rate
- home/away form
- table position
- goals or points scored and conceded
- H2H, only after validated scoping
- shots
- shots on target
- blocked/off-target rates
- possession
- passes and accuracy
- attacks and dangerous attacks
- scoring/conceding trends
- opponent/schedule difficulty
- sport-specific facets
- weather/venue where genuinely pre-event
- detail-page facets
- explicit missingness indicators

Rules:

- Odds are excluded from features
- Missing odds have no effect on eligibility or confidence
- Missing evidence is not zero
- Every optional numeric feature needs missingness indicator or explicit missing-value policy
- Result, final score, settlement status and post-event facts cannot enter features
- Text trends require deliberate, auditable representation; do not casually add opaque text embeddings
- Do not use a feature merely because it exists

Produce feature table with source, timing, missingness and sport applicability.

**Required evidence for next session:**
- `docs/MILESTONE1_AUDIT.md` approved, `underdog.py` identity/label/contracts implemented, 30 tests passed
- `period_values` tracing: where enters event, whether predicted or completed scoring, where basketball consumes it, whether available before kickoff, whether settlement-derived period data can flow back — until proven pre-event, timing=UNKNOWN, prohibited as feature
- Inventory of all potential features from `detail_facets.py`, sport modules, `forebet.py` parsers
- Missing values classification: genuine zero vs legacy unknown encoded as zero vs safe default vs leakage fallback
- Training remains frozen

**Safe commands:**
- `git status --short`, `git diff --check`, `python3 -m py_compile ...`, `pyflakes`, `pytest -q` (Codespace after install), `grep -Rni ...`, read-only audits of `src/slumdog/*.py`

**Prohibited:**
- Do not run American football odds probe before ~2026-09-10
- Do not fetch aggressively; at most 6 workers, 62s pauses
- Do not train models (frozen) — no research-override without explicit user unlock
- Do not change feature vectors, thresholds, ranking, model approval, daily production until Milestone 2 approved
- Do not auto-rewrite legacy ledgers
- Do not infer undocumented market semantics

**Unresolved facts to preserve:**
- Four cross-date identical pairs, hockey 278977 conflict, MMA 11 void+priced, absent raw bytes, DC token 21, scorer semantic uncertainty, detail timing unverified (period_values = UNKNOWN)
