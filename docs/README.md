# docs/ Index — Slumdog Documentation Governance

**Last verified:** 2026-08-24 (UTC)
**Canonical truth:** `docs/STATE.md`
**Read order:** `AGENTS.md` → `README.md` → `docs/STATE.md` → `HANDOFF.md` → `docs/FOREBET_DEPTH_AUDIT.md` → source/tests

This index classifies every file under `docs/` as:

- **CURRENT** — authoritative and maintained, reflects current contract
- **REFERENCE** — stable technical reference, accurate but not daily-changing
- **HISTORICAL** — useful evidence, not current operating truth
- **STALE** — superseded or inaccurate, candidate for update/consolidation/removal
- **UNKNOWN** — requires user review

## Inventory

| File | Purpose | Status | Last Verified | Canonical / Superseded |
|------|---------|--------|---------------|------------------------|
| `STATE.md` | Canonical current truth: phase, merged work, blockers, training status, data limitations, next milestone, parked work, unresolved evidence, links. Git history is history, not append-only diary. | **CURRENT** | 2026-08-24 | Canonical — replaces root `STATE.md` (moved via `git mv`). |
| `MILESTONE1_AUDIT.md` | Price-free machinery audit: candidate definition (odds-first cascade), historical label (draw=failed for draw-capable, void excluded, equal prob silent), features (price-derived present, zero-fill, timing unverified), training/validation (walk-forward ok, ROI gate), daily output (no cap, incomplete receipt, only supporting evidence). 10 gaps with Finding/Evidence/Current/Required/Smallest change/Tests/Risk, staged plan. | **CURRENT during migration, REFERENCE after gaps resolved** | 2026-08-24 | Evidence record for Milestone 1, approved by user. Do not let 885-line audit become permanent current-state truth — `STATE.md` remains concise authority. |
| `FOREBET_DEPTH_AUDIT.md` | Training freeze receipt, common listing surface, match-detail surface by sport, historical depth contract (conservative backfill starts), 3-page detail coverage table, representative price coverage, verified no-odds gaps (cricket 0%, American football dash), MMA legacy integrity, cross-sport duplicate audit, cup settlement notes. | **CURRENT** | 2026-08-24 | Current operating evidence; complements `STATE.md`. Price coverage reference only, ROI not primary, draw=failed per new mission. |
| `FOREBET_ARCHIVE_DEPTH.json` | Annual archive probe matrix (one sport-season date per year, 2018-2026) proving availability lower bounds; conservative backfill start dates derived from it. Method: nonzero proves availability, zero may be off-season. | **REFERENCE** | 2026-08-24 (probe date 2026-08-15 sample) | Stable reference; source for `FOREBET_DEPTH_AUDIT.md` table. |
| `FOREBET_DETAIL_COVERAGE.json` | 3 live detail pages per sport checked for factor availability (H2H, Last6, venue splits, standings, quarters, surface, height, expected total, etc.). 1612 lines, pre-deployment receipt that justified parser families. | **REFERENCE** | 2026-08-24 | Reference; superseded for missingness by future full census (`depth-sweep`) but retained as justification. |
| `FOREBET_PRICE_COVERAGE.json` | Representative displayed-price snapshot: one active-season date per sport, events count, both participant prices, coverage %. Not global estimate. | **REFERENCE** | 2026-08-24 (dates: football 2026-08-15, basketball 2026-01-15, etc.) | **Reference evidence only. Price coverage is not a Slumdog candidate-readiness gate.** Complements depth audit price table. Must be measured across many dates before any value conclusion; unpriced events remain useful for upset learning (price optional per new mission). |
| `FOREBET_FACET_ANALYSIS_PLAN.md` | Timing class definitions (PRE_EVENT / LIVE_ONLY / RESULT_ONLY / UNKNOWN), common fields to catalogue, sport-specific catalogue, 10-step analysis order (availability, timestamp, parser reliability, univariate upset rate, calibration, lift over Forebet underdog prob, interaction with legacy factors, stability, out-of-time Brier/logloss/hit rate, priced vs unpriced bias). | **REFERENCE** | 2026-08-24 | Stable technical plan; still valid but step 5 calibration must not use ROI-primary; step 6 lift is over Forebet underdog probability (price-free). |
| `MA_GOLIDE_ROBBER_FORENSIC.md` | Forensic spec of legacy Ma Golide Robber: authoritative meaning (upset participant, not warning slice), source of truth (`Accumulator_Builder.gs`), underdog identity cascade (odds → pick → prob → form), legacy score table, raw confidence formula, price calibration warning (max 67%, min 30%, min 8pp advantage, manufactured advantage), defects (odds bounds only warned, missing odds lowered threshold, deterministic confidence, uncapped output). | **HISTORICAL** | 2026-08-24 | Historical evidence — useful to understand `magolide.py` reproducer but NOT current operating truth. New mission supersedes odds-first cascade with Forebet-probability underdog + pre-event evidence. Retain for audit comparability, do not delete. |
| `README.md` (this file) | Doc index with purpose/status/last-verified/canonical relationships. | **CURRENT** | 2026-08-24 | Canonical index — required by Milestone 0, updated for Milestone 1 audit classification. |

## Classification Report (Milestone 0 Requirement)

- **Files moved:** `STATE.md` → `docs/STATE.md` via `git mv`
- **Links updated:** `README.md` reference `STATE.md` → `docs/STATE.md`; `AGENTS.md` read order points to `docs/STATE.md`; `docs/STATE.md` self-documents new canonical path.
- **Stale/duplicate docs found:** None proven obsolete yet. `MA_GOLIDE_ROBBER_FORENSIC.md` is HISTORICAL but not STALE — retains forensic value. `FOREBET_DETAIL_COVERAGE.json` 3-page sample will be superseded by census missingness but not yet stale.
- **Proposed removals:** None at this stage. Do not delete evidence or user work to make tree look clean. Ask before deleting UNKNOWN.
- **UNKNOWN requiring user review:** None currently; all files have clear purpose.
- **Final canonical read order:** `AGENTS.md` → `README.md` → `docs/STATE.md` → `HANDOFF.md` → `docs/FOREBET_DEPTH_AUDIT.md` → `FOREBET_ARCHIVE_DEPTH.json` / `FOREBET_DETAIL_COVERAGE.json` / `FOREBET_PRICE_COVERAGE.json` / `FOREBET_FACET_ANALYSIS_PLAN.md` / `MA_GOLIDE_ROBBER_FORENSIC.md` → source/tests
- **Freshness lock:** Every substantive PR must update when applicable: `docs/STATE.md`, `HANDOFF.md`, `docs/README.md`, relevant audit doc. PR incomplete if docs stale.

## Cross-References Proof

Run:

```bash
grep -Rni --exclude-dir=.git 'STATE\.md' .
```

Expected after Milestone 0:

- `README.md` should reference `docs/STATE.md` (not root)
- `AGENTS.md` should reference `docs/STATE.md`
- `docs/README.md` and `docs/STATE.md` self-reference canonical path
- No stale root `STATE.md` references

## Price Coverage Clarification (Required Correction)

```text
FOREBET_PRICE_COVERAGE.json is reference evidence only.
Price coverage is not a Slumdog candidate-readiness gate.
```

- Odds are optional metadata per `AGENTS.md` invariants 5-11.
- Missing odds must not lower confidence, must not gate candidates, must not be model features.
- Sparse or missing prices in `FOREBET_DEPTH_AUDIT.md` and `FOREBET_PRICE_COVERAGE.json` are parked data-quality observations, not current blockers to strong-underdog generation.
- If optional odds later used for retrospective context, report separately, never as training or eligibility requirement.
- ROI is not primary metric.

## Notes for Next Milestone

- Milestone 0: COMPLETE, no deletions authorized (user approval 2026-08-24).
- Milestone 1: Audit COMPLETE, approved as evidence record (`MILESTONE1_AUDIT.md` = CURRENT during migration, REFERENCE after gaps resolved). Audit exposed central problem: system can operate without odds but underdog identity, scoring, feature vectors, thresholds, research approval still materially odds-first.
- Milestone 2 (approved next work only): Implement price-free identity, label, and contract foundation — do NOT proceed into feature-vector changes, training, ranking thresholds, or daily production yet. Milestone 2A pure Forebet identity function, 2B historical label function, 2C new price-free contracts (StrongUnderdogAssessment, DailyUnderdogShortlist), 2D explicit tests. Training remains frozen.
- Corrections to staged plan (user refinement): NO_STRONG_UNDERDOG is daily result not candidate state (separate UnderdogAssessmentStatus vs DailyShortlistStatus), do not destructively repurpose legacy CandidateState yet (introduce clearly named price-free contracts, preserve legacy while new path tested), separate assessment from selection, odds outside core assessment (isolated optional_price_context block), do not rank by model probability before model approved (use baseline_strength_score neutral), treat suspected leakage (period_values) as priority blocker (timing=UNKNOWN, prohibited as feature until proven pre-event), missing values remain unknown (classify zero as genuine vs legacy unknown vs safe default vs leakage fallback, use None/NaN + missing indicator + imputation in pipeline), daily success needs several metrics (top_1_daily_hit_rate, top_3_daily_any_hit_rate, days_with_at_least_one_selected_winner, selected_candidates_per_day, no_pick_day_rate, candidate_precision, draw=failed).
- `feature_contracts.py` currently `MODEL_TRAINING_ALLOWED = False` — remains frozen until user approves dataset/target/timing/validation.
- Historical banner added to `MA_GOLIDE_ROBBER_FORENSIC.md`; CURRENT/REFERENCE banners added to `FOREBET_DEPTH_AUDIT.md` and `FOREBET_FACET_ANALYSIS_PLAN.md`; JSON files untouched, status in this index.
