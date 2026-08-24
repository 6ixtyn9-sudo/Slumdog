# Slumdog Living Handoff

**Last updated:** 2026-08-24 (UTC) — Milestone 0 corrections + Milestone 1 audit
**Branch:** `arena/01a033af-slumdog`
**HEAD SHA:** `259495c` (after Milestone 0 corrections), now `+ audit doc`
**Phase:** Milestone 1 — price-free underdog machinery audit (Milestone 0 COMPLETE, approved)
**Mission:** Slumdog identifies a small daily shortlist of participants that Forebet considers underdogs but whose available pre-event evidence indicates a credible outright-win upset.
**PR:** #6 open (https://github.com/6ixtyn9-sudo/Slumdog/pull/6) — do not merge until user authorizes after Milestone 1 approval
**Training:** FROZEN

## Product Invariants (from AGENTS.md)

- UNDERDOG_WIN outright only, never selects draws, draw=failed in draw-capable
- Odds optional metadata only — not required, not feature, not gate, missing must not lower confidence
- Never force pick; NO_STRONG_UNDERDOG valid; never promise profit/guaranteed wins
- Training frozen until user approves dataset/target/timing/validation
- Main only permanent branch; Arena delivery only; never force-push main

## Milestone 0 — COMPLETE (Approved with 5 Corrections, No Deletions)

User approved 2026-08-24: moving STATE.md to docs/STATE.md, adding AGENTS.md, canonical read order, classifying docs, retaining forensic as historical, main only permanent branch, locking price-free UNDERDOG_WIN mission, leaving training frozen. No deletions authorized.

### Corrections Applied (commit 259495c)

1. **Advance current phase:** `docs/STATE.md` phase → Milestone 1 audit, Milestone 0 COMPLETE, training FROZEN
2. **Historical banner:** Top of `docs/MA_GOLIDE_ROBBER_FORENSIC.md` added:
   > Status: HISTORICAL — legacy odds-first Robber, not current contract, authority AGENTS.md/README.md/docs/STATE.md
   Also added CURRENT/REFERENCE banners to `FOREBET_DEPTH_AUDIT.md` and `FOREBET_FACET_ANALYSIS_PLAN.md` clarifying price coverage reference only, ROI not primary
3. **Price docs clarification:** `docs/README.md` explicit: `FOREBET_PRICE_COVERAGE.json is reference evidence only. Price coverage is not a Slumdog candidate-readiness gate.` Sparse/missing prices in `docs/STATE.md` reclassified as parked data-quality observations, not blockers. Real blockers listed as 6 items (odds-first machinery, label contract unverified, draw settlement unverified, feature timing/leakage, validation/shortlist not approved, training frozen)
4. **Operational README preserved:** `README.md` retains install (`pip install -e '.[dev]'`), tests (`pytest`), capture (`slumdog capture`), backfill (`slumdog backfill`), depth-sweep (`slumdog depth-sweep --per-sport 1000000`), backfill-sport (`slumdog backfill-sport --sport basketball --start 2023-01-01`), analyze (`slumdog analyze`), parser dev (`slumdog parse`, `details`, `enrich`), model override note, dates from runner clock TZ Africa/Johannesburg, pipeline schedule, doc policy, read order
5. **Verify move precisely:**
   ```bash
   test ! -e STATE.md && echo PASS
   test -e docs/STATE.md && echo PASS
   grep -Rni --exclude-dir=.git --exclude='*.pyc' 'STATE\.md' .
   ```
   PASS both, grep shows only `docs/STATE.md` refs plus historical mention of move (acceptable, no active root link)

Verification after corrections: `py_compile` ok, `pyflakes` ok, `diff-check` ok, `pytest` 192 passed previously, doc-only changes.

## Milestone 1 Audit — Read-Only, No Code Changes

**Deliverable:** `docs/MILESTONE1_AUDIT.md` (new, 400+ lines) — comprehensive gap analysis against price-free contract.

### Candidate Definition Findings

- **How determined:** `magolide.py:identify_underdog()` 48-73 cascade: odds → pick → prob → recent form. All sport detectors (football 628-640, basketball 439-450, tennis 348-359, hockey 407-418, baseball 356-367, american_football 406-417, rugby 410-421, handball 414-425, volleyball 394-405, cricket 380-391, mma 320-331, esports 382-393) use same odds-first pattern.
- **Requires odds?** No, but lowers threshold when missing (20→11) increasing volume — violates invariant 7.
- **Rejects when prices absent?** No, emits SHADOW_UNPRICED.
- **No-pick state?** Can output zero (reports.py "NO QUALIFYING ROBBERS"), but no explicit NO_STRONG_UNDERDOG status. Current CandidateState = SHADOW_UNPRICED/PRICED/CERTIFIED/REJECTED, not STRONG_UNDERDOG/WATCHLIST/INSUFFICIENT_EVIDENCE/REJECTED_SOURCE_CONFLICT/NO_STRONG_UNDERDOG.
- **Selects draws?** Never selects draws (good), but settlement must enforce draw=failed.
- **Underdog basis:** Currently `displayed_odds` when odds present, else `lower_forebet_probability` — price-first, not Forebet prob.

**Gap 1:** Odds-first identity → must rewrite to Forebet prob only.
**Gap 2:** Missing odds lowers threshold → must use single threshold, remove odds value factors.

### Historical Label Findings

- **Where constructed:** `training.py:build_training_rows()` 76 `underdog_won = int(winner_index == candidate.participant_index)` using odds-first candidate.
- **Draw-capable:** Draw kept, underdog_won=0 → draw=failed (compliant). Two-way: draw excluded as contract violation (quarantine) — needs explicit documented policy.
- **Voids:** Excluded (VOID disposition) — compliant.
- **Tied probs:** Silent assignment via weaker_recent_form / participant 1 default — violates explicit policy requirement.
- **Frozen identity:** Uses stored prob from settled snapshot (prior-only HistoryIndex via bisect_left — good, no future leakage), but odds-first breaks frozen-from-prob.

**Gap 5:** Equal prob silent assignment → explicit INSUFFICIENT_EVIDENCE.
**Gap:** Label uses odds-first candidate → must use Forebet-prob underdog.

### Features Findings

Table in audit doc details 15 families. Key:

- **Forebet probs:** Used but 0.0 fallback when missing, no missing flag for p1/p2 — violates "missing is not zero"
- **Gap/ratio/entropy/dominance:** No missing flag
- **Recent form:** Prior-only HistoryIndex (good), but win_rate 0.0 fallback
- **Table position:** Has missing flag via _safe_float
- **H2H:** Prior-only, good, 0.0 fallback
- **Shots, shots on target, possession, passing, attacks, dangerous attacks, goals scored/conceded, schedule difficulty:** From detail_facets Phase B label-anchored regex, timing claimed PRE_EVENT but MUST be verified against real Jina-HTML capture per docs/STATE.md — currently UNKNOWN risk
- **Home/away:** is_home_dog ok
- **Sport-specific:** weather PRE_EVENT, height/weight/reach/stance PRE_EVENT for MMA, surface records PRE_EVENT, quarter data RESULT_ONLY risk (period_values used in basketball from facets — if from settled, leakage)
- **Odds and price-derived:** Present as features (displayed_odds, implied_probability, price_available, overround, fair probs, value edge, legacy score) — violates invariant 8
- **Result-only:** result, live_score, extra_time_score, penalty_score correctly blocked via pre_event_facets filter, but period_values risk
- **Text trends:** trend_en retained, not numeric — good, needs deliberate representation not opaque embeddings

**Gap 3:** Odds are model features → remove all price_* keys.
**Gap 4:** Missing evidence zero-filled for core fields → add missing flags.
**Gap 10:** Detail facet timing unverified → audit against retained bytes or live Jina probe, move unverified to UNKNOWN and block.

### Training and Validation Findings

- **Models:** LogisticRegression median imputer + StandardScaler C=0.5 balanced liblinear max_iter 2000, one per sport, feature contract sorted union
- **Callable despite freeze?** Yes via allow_research / --research-override bypass — compliant with freeze but bypass exists, used on GH runners
- **Split method:** Walk-forward expanding-date via _iter_walk_forward_splits groups by event_date, prior.copy() — chronological, no random splits. Grep for train_test_split/KFold/ShuffleSplit none in src.
- **Random splits:** None
- **Walk-forward implemented:** Yes
- **Primary metrics:** Brier, hit_rate, Wilson lower 90, threshold, plus priced ROI gate. Eligible requires n>=20, hit_rate>=0.45, wilson>=0.35, (priced_n<10 or ROI>0). Best ranked by (ROI>0, Wilson, threshold) — ROI embedded.
- **Required metrics per Milestone 4:** top-1 daily hit rate, top-3 shortlist hit rate, % days at least one correct, precision, recall, lift, precision@K, Brier, calibration by band, coverage, no-pick rate, longest losing streak, by sport/league/gap band — not implemented, ROI not primary.
- **Calibration:** Logistic outputs some calibration, Brier computed, but calibration by predicted upset band missing
- **Per-sport approval:** Registry per-sport status INSUFFICIENT/SHADOW_MODEL/OBSERVE_MODEL, but no explicit user approval gate per sport as required by Milestone 7

**Gap 7:** ROI and price in approval gate → remove, implement required daily metrics, keep ROI separate retrospective only.

### Daily Output Findings

- **Produces candidates?** Yes, build_shadow_robbers emits every high-confidence legacy Robber, sorted by ml_prob or legacy_confidence, no count cap but filters raw_conf<65 and h2h<3+recent<5 when no model — can be many, not 1-3.
- **How many?** 0 to many
- **No candidates?** Yes, empty list → reports.py "NO QUALIFYING ROBBERS" but no explicit NO_STRONG_UNDERDOG daily status
- **Immutable?** ledger.py freeze_candidates append-only preserves first payload, frozen_at timestamp — immutable per date, but receipt incomplete vs Milestone 6 (missing feature snapshot, missingness snapshot, model/version id, source SHA, draw prob, lift, supporting/contradicting/missing separation, explicit new status, generation timestamp ok, optional price context)
- **Provenance?** EventSnapshot has raw_sha256, source_url, captured_at, but candidate to_dict doesn't include it. Model has contract_hash but candidate only has ml_train_rows etc.
- **Supporting/contradicting/missing?** Only supporting reasons list
- **Settlement reproducible?** settlement.py parses frozen captures, appends unique (sport, event_id, event_date), sorts, new backfills have write guard and fail loudly on conflicting same-key payloads — reproducible for new, but legacy has 279 byte-identical extras, 4 cross-date identical pairs, hockey 278977 conflict unresolved
- **Draw settles as failure?** For draw-capable yes (underdog_won=0), for two-way excluded — needs test and doc

**Gap 6:** No 1-3 cap, no STRONG_UNDERDOG/WATCHLIST, no explicit NO_STRONG_UNDERDOG
**Gap 8:** Incomplete immutable receipt vs Milestone 6
**Gap 9:** Only supporting evidence

## Exact Final Audit Format — Gaps Summary (10 gaps)

See `docs/MILESTONE1_AUDIT.md` for full Finding/Evidence/Current/Required/Smallest change/Tests/Risk per gap:

1. Odds-first underdog identity
2. Missing odds lowers threshold
3. Price-derived features in model
4. Missing evidence zero-filled
5. Equal prob silent assignment
6. No explicit NO_STRONG_UNDERDOG and small shortlist policy
7. ROI and price in approval gates
8. Incomplete immutable receipt
9. Only supporting evidence, no contradicting/missing
10. Detail facet timing unverified

## Staged Implementation Plan (No Code Yet — Approval Required)

### Stage 1: Define Price-Free Candidate Contract (Milestone 2)

Propose exact dataclass for new candidate with all required fields, label contract for two-way vs draw-capable, eligibility without odds, edge-case tests before implementation.

### Stage 2: Fix Underdog Identity

New `identify_underdog_by_forebet_prob()` using only prob1/2, draw as context, explicit INSUFFICIENT_EVIDENCE when equal/missing, replace old cascade.

### Stage 3: Remove Odds from Features and Scoring

Remove price_* from build_numeric_features and sport to_dict, remove odds-dependent threshold and odds value factors, ensure missing odds no effect.

### Stage 4: Feature Timing Audit

Inventory every feature from detail_facets, sport modules, forebet parsers, classify PRE_EVENT/RESULT_ONLY/UNKNOWN with proof, ensure pre_event_facets blocks leakage, add missingness indicators, produce table with source/timing/missingness/sport.

### Stage 5: Label and Settlement Verification

Use new underdog identity, draw=failed for draw-capable, void excluded, equal-prob explicit, verify HistoryIndex prior-only, settlement reproducible, test draw settlement.

### Stage 6: Daily Shortlist Policy

Redefine CandidateState to STRONG_UNDERDOG/WATCHLIST/INSUFFICIENT_EVIDENCE/REJECTED_SOURCE_CONFLICT/NO_STRONG_UNDERDOG, ranking by model prob + lift + evidence, cap 1-3, never fill quota, explicit daily status, update reports and ledger.

### Stage 7: Baselines Before Complex Models (Training remains frozen until unlock)

When unlocked: Forebet underdog-prob baseline, probability-gap baseline, recent-form differential baseline, Ma Golide heuristic baseline, simple interpretable model, walk-forward, required metrics (top-1/top-3 hit rate, % days at least one correct, precision, recall, lift, precision@K, Brier, calibration by band, coverage, no-pick rate, losing streak, by sport/league/gap), ROI not primary.

### Stage 8: Immutable Shadow Selections

Freeze before kickoff: event key, date/time, participants, fav/underdog, Forebet probs, feature snapshot, missingness snapshot, score/prob, evidence codes, model version, source refs, generation timestamp, optional odds context. After settlement append outcome, never edit original, count draws as failed, exclude voids, report daily and cumulative.

### Stage 9: Readiness Gate

Price-free contract implemented, labels tested, timing/leakage enforced, at least one sport passes walk-forward, thresholds out-of-sample, no-pick supported, immutable receipts exist, settlement reproducible, forward shadow tracking, docs accurate, user authorizes per sport.

## Verification Completed (Milestone 1 Audit)

- Read-only: no model, candidate, label, feature code changes
- Files read: contracts.py, magolide.py, training.py, research.py, pipeline.py, history.py, feature_contracts.py, football.py, basketball.py, tennis.py, hockey.py, baseball.py, american_football.py, rugby.py, handball.py, volleyball.py, cricket.py, mma.py, esports.py, settlement.py, sports.py, facets.py, ml_meta.py, reports.py, ledger.py, docs/STATE.md, HANDOFF.md, FOREBET_DEPTH_AUDIT.md, etc.
- Grep: `grep -Rni "identify_underdog|displayed_odds|CandidateState"` etc.
- No network fetches, no training runs
- Tests not run for audit (read-only), but prior verification 192 passed remains valid for unchanged code
- New doc `docs/MILESTONE1_AUDIT.md` created, no deletions

## Changed Files (This Session — Milestone 0 corrections + Milestone 1 audit)

- `docs/STATE.md` — phase advanced to Milestone 1, blockers corrected (missing prices NOT blockers), price coverage clarified as reference only
- `docs/MA_GOLIDE_ROBBER_FORENSIC.md` — HISTORICAL banner
- `docs/FOREBET_DEPTH_AUDIT.md` — CURRENT banner clarifying price reference only
- `docs/FOREBET_FACET_ANALYSIS_PLAN.md` — REFERENCE banner clarifying ROI not primary
- `docs/README.md` — explicit price coverage reference only, not readiness gate, parked observations not blockers, no deletions authorized
- `README.md` — Status phase Milestone 1, training FROZEN, blockers note
- `docs/MILESTONE1_AUDIT.md` — new comprehensive audit (10 gaps, staged plan)
- `HANDOFF.md` — this file, updated for corrections + audit

## Open / Parked / Unresolved (Updated)

**Open (Milestone 1 approval):**
- User review of `docs/MILESTONE1_AUDIT.md` and staged plan before any code changes
- Explicit approval for Stage 1 (candidate contract) and Stage 2 (underdog identity fix)
- Training remains frozen until dataset/target/timing/validation approved

**Parked (unchanged):**
- American football odds probe `scripts/probe_american_football_odds.py` — do not run before ~2026-09-10
- Complex ensembles — baselines first after unlock
- Esoccer separate audit
- Dropped football getrs.php keys audit
- Sparse hockey/rugby/volleyball/handball pricing re-check on in-season top-league dates
- Auto-rewrite/compact legacy ledgers — prohibited without explicit authorization

**Unresolved Evidence (preserved):**
- 4 cross-date identical pairs: basketball:198045, 198046, football:2041406, volleyball:96303
- Hockey 278977 conflict 1-6 vs 0-4
- MMA 11 void+priced rows
- Absent raw bytes for 7 suspicious dates
- Football DC token 21, scorer subtype unknown
- Football 963-date backfill gap quantification + replay feasibility
- Detail facet timing unverified (shots, passes, possession, attacks, next-fixture difficulty, period_values) — needs Jina-HTML proof

## PR State

- **Branch:** `arena/01a033af-slumdog`
- **Base:** `main` @ `2e3daa40b60ed520a0bcb2f178ef4219fad4d026`
- **PR:** #6 https://github.com/6ixtyn9-sudo/Slumdog/pull/6 — open, do not merge until user authorizes after Milestone 1 approval
- **Commits:** f4d2946 Milestone 0 move + constitution, 259495c Milestone 0 final corrections, + audit doc (next commit)
- **Mergeability:** No conflicts (doc-only + audit doc, no code changes)
- **User authorization:** Milestone 0 approved, Milestone 1 audit pending approval — do not merge, do not change model/candidate/label/feature code until approved

## Evidence Language Compliance

- Verified from code: file paths, function names, line numbers, grep results
- Verified from executed probe: prior pytest 192 passed, py_compile/pyflakes/diff-check ok, move verification PASS
- Plausible but unverified: detail facet timing (shots etc.) — marked as needing verification, not claimed as PRE_EVENT proven
- Unresolved conflict: retained competing facts (duplicate audits) without silently choosing one

## After Merge: Next Session Starts Here (Updated)

Read `AGENTS.md` first, then `README.md`, then `docs/STATE.md`, then `HANDOFF.md`, then `docs/FOREBET_DEPTH_AUDIT.md`, then `docs/MILESTONE1_AUDIT.md`, then relevant source/tests.

**Exact next task (Milestone 2 — define label and eligibility contract):**

DISCUSS BEFORE CODING. Propose exact, testable historical label:

- Two-way: Forebet favorite = higher prob, underdog = lower prob, label=1 only if underdog wins, 0 if favorite wins, void excluded
- Draw-capable: participant probs identify fav/underdog, draw never selected, label=1 only if selected underdog wins outright, 0 if favorite wins OR drawn, void excluded, ambiguous equal-prob explicit policy must not be silently assigned
- Define candidate eligibility without odds: valid settled outcome, usable Forebet probs, unambiguous fav/underdog, sufficient pre-event evidence, no source conflict, no result leakage, no odds requirement
- Present exact edge-case treatment and tests before implementation

**Required evidence for next session:**
- `docs/MILESTONE1_AUDIT.md` 10 gaps and staged plan approved by user
- Current `magolide.py` odds-first cascade vs new Forebet-prob identity
- `training.py` label construction and void/draw handling
- `facets.py` price features and missingness policy
- `pipeline.py` shortlist cap and no-pick handling
- `ledger.py` immutable receipt completeness

**Safe commands:**
- `git status --short`, `git diff --check`, `python3 -m py_compile ...`, `pyflakes`, `pytest -q` (Codespace after install), `grep -Rni ...`, read-only audits

**Prohibited:**
- Do not run American football odds probe before ~2026-09-10
- Do not fetch aggressively; at most 6 workers, 62s pauses
- Do not train models (frozen) — no `slumdog research --research-override` without explicit user unlock
- Do not change candidate/label/feature code until Milestone 1 approved
- Do not auto-rewrite legacy ledgers
- Do not infer undocumented market semantics

**Unresolved facts to preserve:**
- Four cross-date identical pairs, hockey 278977 conflict, MMA 11 void+priced, absent raw bytes, DC token 21, scorer semantic uncertainty, detail timing unverified
