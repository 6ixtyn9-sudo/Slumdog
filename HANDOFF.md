# Slumdog Living Handoff

**Last updated:** 2026-08-24 (UTC) — Milestone 2 COMPLETE (including 2E hardening) + Milestone 3 feature timing audit CURRENT
**Branch:** `arena/01a033af-slumdog`
**HEAD SHA:** fee5d78 (Milestone 2E) → now + Milestone 3 docs (pending commit)
**Phase:** Milestone 3 — feature timing and leakage audit, read-only analysis, training FROZEN (Milestone 0 COMPLETE, Milestone 1 COMPLETE approved as REFERENCE, Milestone 2 COMPLETE)
**Mission:** Slumdog identifies a small daily shortlist of participants that Forebet considers underdogs but whose available pre-event evidence indicates a credible outright-win upset.
**PR:** #6 https://github.com/6ixtyn9-sudo/Slumdog/pull/6 — OPEN, do not merge until user authorizes after Milestone 3 approval
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
4. Operational README preserved
5. Verify move: test ! -e STATE.md PASS, test -e docs/STATE.md PASS

## Milestone 1 Audit — COMPLETE (Approved as Evidence Record with 8 Refinements) — Now REFERENCE

**Deliverable:** `docs/MILESTONE1_AUDIT.md` (885 lines + 143 lines corrections) — read-only audit, no code changes. Now classified REFERENCE after gaps resolved for identity/label.

Central problem exposed: system can operate without odds but underdog identity, scoring, feature vectors, thresholds, research approval still materially odds-first.

10 gaps documented. Approved plan corrections (8 refinements) added as annotation.

Classification: MILESTONE1_AUDIT.md = REFERENCE after Milestone 2 COMPLETE, STATE.md and FEATURE_TIMING_CONTRACT.md are concise authorities.

## Milestone 2 — Price-Free Identity, Label, and Contract Foundation — COMPLETE (Including 2E Hardening)

**Scope:** Implement only Milestone 2A–2E, do NOT proceed into feature-vector changes, training, ranking thresholds, or daily production yet. Training remains frozen. Compatibility boundary explicit — do not integrate into legacy training.py yet.

### Files Changed

- **New module:** `src/slumdog/underdog.py` (hardened, ~718 lines, commit fee5d78)
  - UnderdogAssessmentStatus enum: STRONG_UNDERDOG, WATCHLIST, INSUFFICIENT_EVIDENCE, REJECTED_SOURCE_CONFLICT, INELIGIBLE
  - DailyShortlistStatus enum: CANDIDATES_FOUND, NO_STRONG_UNDERDOG, SOURCE_FAILURE
  - ForebetUnderdogIdentity dataclass: favorite_index, underdog_index, favorite_probability, underdog_probability, probability_gap, eligible, ineligibility_reason, draw_probability
  - Pure function `identify_forebet_underdog(probability_1, probability_2, draw_probability=None)` — validates present/finite/[0,1], higher=favorite lower=underdog, draw does not determine identity, equal→EQUAL_PROBABILITY, missing→MISSING_PROBABILITY, non-finite→NON_FINITE, out-of-range→OUT_OF_RANGE, no fallback
  - UnderdogLabelResult dataclass: label 1/0/None, eligible, exclusion_reason, is_draw, is_void, is_source_conflict, winner_index, favorite_index, underdog_index, identity_ineligibility_reason, sport, draw_possible (2E additions)
  - Private helper `_label_from_indices(*, sport, favorite_index, underdog_index, winner_index, disposition, draw_possible, source_conflict, identity_ineligibility_reason, has_eligible_identity)` — low-level raw-indices, internal use only
  - Public function `label_underdog_outcome(sport: str, identity: ForebetUnderdogIdentity, winner_index: int|None, disposition="SETTLED", source_conflict=False) -> UnderdogLabelResult` — Milestone 2E hardening: derives favorite_index/underdog_index/eligible/reason from identity, derives draw_possible from SPORTS[sport].draw_possible, unknown sport→exclusion UNKNOWN_SPORT, preserves exact identity reason EQUAL_PROBABILITY/MISSING_PROBABILITY/NON_FINITE/OUT_OF_RANGE etc., not collapsed to NO_ELIGIBLE_IDENTITY unless original retained separately in identity_ineligibility_reason, caller cannot reverse fav/dog or override sport semantics
  - StrongUnderdogAssessment dataclass: event_id, sport, event_date, participant_1/2, favorite_index/underdog_index, favorite_name/underdog_name, favorite_probability/underdog_probability, draw_probability, probability_gap, status, supporting/contradicting/missing evidence, source_url, raw_sha256, captured_at, assessment_version, reserved slumdog_underdog_probability/probability_lift/baseline_strength_score must remain None until approved, isolated optional_price_context dict may be absent
  - DailyUnderdogShortlist dataclass: target_date ISO, generated_at ISO timezone-aware, status DailyShortlistStatus, assessments_considered, strong_candidates tuple, watchlist_candidates tuple, rejection_counts dict, assessment_version, source_receipt, validation NO_STRONG_UNDERDOG must have zero strong_candidates, no draw selection
  - Helper `build_assessment_from_identity()` — builds assessment from identity, returns None if not eligible

- **New tests:** `tests/test_price_free.py` (40 tests after 2E hardening)
  - Identity (10): p1 favorite, p2 favorite, equal, missing, non-finite, out-of-range, draw larger not selected, odds disagree no effect, pick disagrees no effect, form disagrees no effect
  - Labels via identity-bound API (11): draw-capable underdog win, fav win, draw=0, two-way underdog win, fav win, two-way draw excluded, void excluded, equal prob excluded, missing prob excluded, invalid winner excluded, source conflict excluded
  - Hardening (10 new): label uses indices from identity, cannot reverse fav/dog via public API, draw capability from SPORTS registry, football draw→0, basketball draw→excluded, unknown sport explicit UNKNOWN_SPORT, equal/missing/non-finite/out-of-range reasons survive
  - Contracts (9): round-trip, no price field required, missing optional evidence accepted, no-pick serializes zero candidates, cannot contain fake draw, provenance optional only where legacy lacks, reserved fields None, no fake candidate for no-pick, optional_price_context isolated

- **Updated docs for Milestone 2 COMPLETE:**
  - `docs/STATE.md` — phase advanced to Milestone 3, Milestone 2 marked COMPLETE including 2E, blockers updated, links include FEATURE_TIMING_CONTRACT.md as CURRENT, MILESTONE1_AUDIT.md as REFERENCE
  - `docs/README.md` — inventory adds FEATURE_TIMING_CONTRACT.md as CURRENT, MILESTONE1_AUDIT.md as REFERENCE, notes for Milestone 3
  - `README.md` — Status updated to Milestone 2 COMPLETE / Milestone 3 CURRENT, training FROZEN

### New Contracts (Milestone 2)

- UnderdogAssessmentStatus, DailyShortlistStatus, ForebetUnderdogIdentity, UnderdogLabelResult (with identity_ineligibility_reason, sport, draw_possible), StrongUnderdogAssessment, DailyUnderdogShortlist
- Private _label_from_indices for internal focused use, public label_underdog_outcome identity-bound

### Identity Policy (2A)

- Validate present/finite/[0,1], higher=favorite lower=underdog, draw does NOT determine identity, equal→EQUAL_PROBABILITY, missing/invalid→MISSING/NON_FINITE/OUT_OF_RANGE, no fallback to odds/pick/form, no gap threshold, explicit reason

### Label Policy (2B + 2E Hardening)

- Public API accepts ForebetUnderdogIdentity directly, derives fav/dog/eligible/reason from identity, no caller repetition
- Derives draw capability from SPORTS[sport].draw_possible, not caller-provided draw_possible, caller cannot override sport semantics, unknown sport→explicit exclusion UNKNOWN_SPORT
- Preserves identity exclusion reason — if identity.eligible false, preserves exact reason EQUAL_PROBABILITY/MISSING_PROBABILITY/NON_FINITE/OUT_OF_RANGE etc., not collapsed to NO_ELIGIBLE_IDENTITY unless result also retains original reason separately in identity_ineligibility_reason field for audit counts/missing diagnosis/no-pick explanations/rejection summaries
- Draw-capable: underdog win 1, fav win 0, draw 0, void excluded
- Two-way: underdog win 1, fav win 0, unexpected draw excluded UNEXPECTED_DRAW_FOR_TWO_WAY, void excluded VOID
- Also excluded: INVALID_WINNER_INDEX, SOURCE_CONFLICT, WINNER_MISMATCH, UNKNOWN_SPORT
- Returns explicit exclusion reason

### Legacy Behavior Preserved

- Legacy CandidateState and RobberCandidate remain unchanged, marked legacy
- magolide.py and all sport detectors unchanged (still odds-first) — new path tested alongside, not replacing until approved
- facets.py, training.py, research.py, pipeline.py unchanged — no feature-vector changes per Milestone 2 scope, compatibility boundary explicit
- MODEL_TRAINING_ALLOWED=False remains frozen

### Tests Added

- tests/test_price_free.py: 40 tests (10 identity, 11 labels via identity, 10 hardening, 9 contracts)
- Full suite: 232 passed (192 legacy + 40 new) — pytest -q

### Full Verification (Milestone 2E)

```bash
python -m pytest -q tests/test_price_free.py  # 40 passed
python -m pytest -q  # 232 passed
python -m py_compile src/slumdog/underdog.py  # ok
python -m pyflakes src/slumdog/underdog.py  # ok
git diff --check  # ok
```

### Contract Notes for Later (from 2E)

- Nested mutability: frozen dataclass with mutable dicts optional_price_context/rejection_counts/source_receipt not deeply immutable, must be defensively copied/immutable structures/frozen by ledger before immutable receipts (Milestone 6), record only — do not add generalized freezing now
- Status semantics: STRONG_UNDERDOG must not imply approved Slumdog probability until scoring/thresholds approved; tests may construct status for serialization, operational code must not emit, reserved model probability/lift fields remain None, no report should present baseline strength as calibrated probability

## Milestone 3 — Feature Timing and Leakage Audit — CURRENT (Read-Only, No Code Change)

**Deliverable:** `docs/FEATURE_TIMING_CONTRACT.md` (CURRENT) — doc-only audit, no code changes, training FROZEN.

**Required columns:** Feature|Feature family|Sport|Source file/function|Raw source field|Timing|Evidence|Missing representation|Missing indicator|Odds-dependent|Legacy use|New-path eligibility|Action; Timing PRE_EVENT/RESULT_ONLY/UNKNOWN; New-path ALLOWED/PROHIBITED/PARKED; Rules RESULT_ONLY→prohibited, UNKNOWN→prohibited until verified, odds-dependent→prohibited, missing odds irrelevant, existing use does not prove eligibility, suggestive name does not prove timing, evidence must cite code or retained bytes.

**Priority investigation period_values 10 points:**
- DOM selector/JSON field: .predQ .fj_column span in parsers.py:170-173, JSON field period_values
- Listing/detail parser storing it: parse_html_events() parsers.py:122-223 storing facets["period_values"] with timing PRE_EVENT claim but not proven; settlement parser settlement.py:31-40 same selector for actual period scores
- Event contract field: EventSnapshot.facets["period_values"] list[list[str]], SettledEvent.period_scores_1/2 separate
- Feature-builder consuming: basketball.py:286-287, american_football.py:265, hockey.py:263, rugby.py:272, handball.py:275, volleyball.py:262, esports.py:257
- Sports used: basketball, american_football, hockey, rugby, handball, volleyball, esports
- Whether predicted/completed/schedule/ambiguous: AMBIGUOUS — same selector used for predicted (upcoming) and actual (settled); listing parser filters result rows but no retained bytes prove population for upcoming events
- Populated for upcoming events: UNKNOWN — no census in FOREBET_DEPTH_AUDIT.md or DETAIL_COVERAGE.json, no data/raw sample in repo, test fixture synthetic not proof
- Settlement output flow into same facet key: No direct flow into same key (settlement writes period_scores_1/2 not facets["period_values"]), but same DOM selector reused — risk of confusion
- Tests covering: test_parsers.py:47 synthetic upcoming row with period_values, basketball etc tests inject period_values, no test proves timing
- Final timing: UNKNOWN → new-path PROHIBITED until Jina probe proves upcoming population and sum vs predicted_score

**Required families audit:** Forebet probs, draw prob, gap/ratio, entropy/dominance, recent form, home/away, win rates, table position, H2H, goals/points scored/conceded, shots, shots on target, blocked/off-target, possession, passes accuracy, attacks, dangerous attacks, event-time, schedule difficulty, weather, venue, stable IDs, cup flags, trend text, double chance, goalscorer predictions, sport-specific physical/stat facets, every price/odds/overround/fair prob/value-edge field, every final/period/penalty/extra-time/disposition/settlement field — all inventoried in FEATURE_TIMING_CONTRACT.md with evidence citations.

**Missingness audit:** For every feature None/NaN/0/empty string/absent key/sentinel text and zero fallback classification GENUINE_ZERO/UNKNOWN_ENCODED_AS_ZERO/SAFE_MATHEMATICAL_DEFAULT/UNRESOLVED — documented in FEATURE_TIMING_CONTRACT.md. Example: facets.py _finite returns None not zero (good), football.py form_points_per_game returns 0.0 when empty list → UNKNOWN_ENCODED_AS_ZERO, basketball.py quarter_margins None → 0.0 fallback with missing flag → UNKNOWN_ENCODED_AS_ZERO, _entropy 0.0 when total<=0 → SAFE_MATHEMATICAL_DEFAULT, detail_facets.py _parse_pct returns None for "NAN%" literal → genuine missing handling.

**No code change during Milestone 3 audit — read-only analysis, training FROZEN.**

**Verification (doc-only):**
```bash
python -m pytest -q  # 232 passed
python -m pyflakes src/slumdog/underdog.py  # ok
git diff --check  # ok
```

**Next after Milestone 3 approval:** Milestone 4 — implement price-free feature vector based on FEATURE_TIMING_CONTRACT.md ALLOWED only, with missingness indicators, no odds, no RESULT_ONLY, no UNKNOWN.

## Open / Parked / Unresolved (Updated for Milestone 3)

**Open (Milestone 3 approval):**
- User review of FEATURE_TIMING_CONTRACT.md: period_values 10-point investigation, full feature inventory, missingness audit, new-path eligibility ALLOWED/PROHIBITED/PARKED, evidence citations
- Approval to proceed to Milestone 4 feature vector implementation (ALLOWED only)

**Parked:**
- American football odds probe `scripts/probe_american_football_odds.py` — do not run before ~2026-09-10
- Complex ensembles — baselines first after unlock
- Esoccer separate audit
- Dropped football getrs.php keys audit
- Sparse hockey/rugby/volleyball/handball pricing re-check on in-season top-league dates
- Auto-rewrite/compact legacy ledgers — prohibited without explicit authorization
- Feature-vector code changes, threshold changes, ranking, model approval, daily production — out of scope for Milestone 3, remain frozen

**Unresolved Evidence (preserved):**
- 4 cross-date identical pairs: basketball:198045, 198046, football:2041406, volleyball:96303
- Hockey 278977 conflict 1-6 vs 0-4
- MMA 11 void+priced rows
- Absent raw bytes for 7 suspicious dates
- Football DC token 21, scorer subtype unknown
- Football 963-date backfill gap quantification + replay feasibility
- Detail facet timing unverified (shots, passes, possession, attacks, next-fixture difficulty) — all PARKED in FEATURE_TIMING_CONTRACT.md, need Jina-HTML proof
- period_values timing UNKNOWN — needs live Jina probe for upcoming basketball date per FEATURE_TIMING_CONTRACT.md 10-point investigation, until then PROHIBITED

## PR State

- **Branch:** `arena/01a033af-slumdog`
- **Base:** `main` @ `2e3daa40b60ed520a0bcb2f178ef4219fad4d026`
- **PR:** #6 https://github.com/6ixtyn9-sudo/Slumdog/pull/6 — OPEN, do not merge until user authorizes after Milestone 3 approval
- **Commits:** f4d2946 Milestone 0 move+constitution, 259495c Milestone 0 final corrections, 8f38647 Milestone 1 audit, fee5d78 Milestone 2E hardening (40 tests, 232 total), + Milestone 3 docs (STATE.md, README.md, FEATURE_TIMING_CONTRACT.md, HANDOFF.md)
- **Mergeability:** No conflicts (doc-only + new module + tests, no legacy code changes)
- **User authorization:** Milestone 0 approved, Milestone 1 approved as REFERENCE, Milestone 2 COMPLETE (including 2E), Milestone 3 CURRENT pending approval — do not merge, do not change feature vectors/thresholds/ranking/model approval/daily production until approved

## Evidence Language Compliance

- Verified from code: file paths, function names, line numbers, grep results, test names, DOM selectors, facet timing maps
- Verified from executed probe: pytest 232 passed, py_compile ok, pyflakes ok, diff-check ok
- Plausible but unverified: detail facet timing — marked PARKED/UNKNOWN, not claimed as PRE_EVENT proven — per rules suggestive name does not prove timing, evidence must cite code or retained bytes
- Unresolved conflict: retained competing facts (duplicate audits, period_values ambiguous) without silently choosing one — marked UNKNOWN PROHIBITED until verified

## After Merge: Next Session Starts Here (Updated for Milestone 4)

Read `AGENTS.md` first, then `README.md`, then `docs/STATE.md`, then `HANDOFF.md`, then `docs/FEATURE_TIMING_CONTRACT.md`, then `docs/FOREBET_DEPTH_AUDIT.md`, then `src/slumdog/underdog.py`, then `tests/test_price_free.py`, then relevant source/tests.

**Exact next task (Milestone 4 — implement price-free feature vector):**

After Milestone 3 approval, implement price-free feature vector based on FEATURE_TIMING_CONTRACT.md ALLOWED only:
- Forebet participant probabilities, draw prob context, gap/ratio/entropy/dominance
- Recent wins/draws/losses, win rates, PPG from football JSON host_form/guest_form (pre-event proven)
- Table position from host_pos/guest_pos (pre-event)
- Predicted scores/totals, goalsavg, host_sc_pr/guest_sc_pr
- Weather, venue, stable IDs, cup flags, league_code, round_number, move direction, etc. per ALLOWED list
- Explicit missingness indicators for every optional numeric feature
- No odds fields (odds_1, odds_2, odds_draw, am variants, best_odd_*, haodd, lscrsp, overround, fair prob, value edge) — PROHIBITED per invariants
- No RESULT_ONLY (score, winner, period_scores, extra_time, penalty, HT, disposition)
- No UNKNOWN/PARKED until verified (period_values, detail shots/passes/possession/attacks/event-time/next difficulty, surface splits, MMA tale-of-the-tape, double-chance prob/pick, goalscorer prob/name, etc.) — PROHIBITED/PARKED until Jina proof
- Missing evidence is not zero — use None/NaN + indicator + imputation inside pipeline, not global zero-fill now (Milestone 6 defensive copy requirement)
- Text trends require deliberate auditable representation — do not casually add opaque embeddings
- Do not use feature merely because it exists

**Required evidence for next session:**
- `docs/FEATURE_TIMING_CONTRACT.md` approved (period_values investigation, full inventory, missingness audit, eligibility)
- `docs/STATE.md` Milestone 2 COMPLETE, Milestone 3 CURRENT, training FROZEN
- `src/slumdog/underdog.py` identity-bound label hardened, 40 tests passing, 232 total
- Training remains frozen until feature vector approved

**Safe commands:**
- `git status --short`, `git diff --check`, `python3 -m py_compile ...`, `pyflakes`, `pytest -q`, `grep -Rni ...`, read-only audits
- Doc-only updates to `docs/STATE.md`, `HANDOFF.md`, `docs/README.md`, `docs/FEATURE_TIMING_CONTRACT.md`

**Prohibited:**
- Do not run American football odds probe before ~2026-09-10
- Do not fetch aggressively; at most 6 workers, 62s pauses
- Do not train models (frozen) — no research-override without explicit user unlock
- Do not change feature vectors, thresholds, ranking, model approval, daily production until Milestone 3 approved
- Do not auto-rewrite legacy ledgers
- Do not infer undocumented market semantics

**Unresolved facts to preserve:**
- Four cross-date identical pairs, hockey 278977 conflict, MMA 11 void+priced, absent raw bytes, DC token 21, scorer semantic uncertainty, detail timing unverified (period_values = UNKNOWN PROHIBITED), missingness zero-fill classification
