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
| `FOREBET_DEPTH_AUDIT.md` | Training freeze receipt, common listing surface, match-detail surface by sport, historical depth contract (conservative backfill starts), 3-page detail coverage table, representative price coverage, verified no-odds gaps (cricket 0%, American football dash), MMA legacy integrity, cross-sport duplicate audit, cup settlement notes. | **CURRENT** | 2026-08-24 | Current operating evidence; complements `STATE.md`. Needs price-free mission alignment note in Milestone 1 (remove ROI-primary language, clarify draw=failed). |
| `FOREBET_ARCHIVE_DEPTH.json` | Annual archive probe matrix (one sport-season date per year, 2018-2026) proving availability lower bounds; conservative backfill start dates derived from it. Method: nonzero proves availability, zero may be off-season. | **REFERENCE** | 2026-08-24 (probe date 2026-08-15 sample) | Stable reference; source for `FOREBET_DEPTH_AUDIT.md` table. |
| `FOREBET_DETAIL_COVERAGE.json` | 3 live detail pages per sport checked for factor availability (H2H, Last6, venue splits, standings, quarters, surface, height, expected total, etc.). 1612 lines, pre-deployment receipt that justified parser families. | **REFERENCE** | 2026-08-24 | Reference; superseded for missingness by future full census (`depth-sweep`) but retained as justification. |
| `FOREBET_PRICE_COVERAGE.json` | Representative displayed-price snapshot: one active-season date per sport, events count, both participant prices, coverage %. Not global estimate. | **REFERENCE** | 2026-08-24 (dates: football 2026-08-15, basketball 2026-01-15, etc.) | Reference snapshot; complements depth audit price table. Must be measured across many dates before any value conclusion; unpriced events remain useful for upset learning (price optional per new mission). |
| `FOREBET_FACET_ANALYSIS_PLAN.md` | Timing class definitions (PRE_EVENT / LIVE_ONLY / RESULT_ONLY / UNKNOWN), common fields to catalogue, sport-specific catalogue, 10-step analysis order (availability, timestamp, parser reliability, univariate upset rate, calibration, lift over Forebet underdog prob, interaction with legacy factors, stability, out-of-time Brier/logloss/hit rate, priced vs unpriced bias). | **REFERENCE** | 2026-08-24 | Stable technical plan; still valid but step 5 calibration must not use ROI-primary; step 6 lift is over Forebet underdog probability (price-free). |
| `MA_GOLIDE_ROBBER_FORENSIC.md` | Forensic spec of legacy Ma Golide Robber: authoritative meaning (upset participant, not warning slice), source of truth (`Accumulator_Builder.gs`), underdog identity cascade (odds → pick → prob → form), legacy score table, raw confidence formula, price calibration warning (max 67%, min 30%, min 8pp advantage, manufactured advantage), defects (odds bounds only warned, missing odds lowered threshold, deterministic confidence, uncapped output). | **HISTORICAL** | 2026-08-24 | Historical evidence — useful to understand `magolide.py` reproducer but NOT current operating truth. New mission supersedes odds-first cascade with Forebet-probability underdog + pre-event evidence. Retain for audit comparability, do not delete. |
| `README.md` (this file) | Doc index with purpose/status/last-verified/canonical relationships. | **CURRENT** | 2026-08-24 | Canonical index — required by Milestone 0. |

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

## Notes for Next Milestone

- Milestone 1 will audit existing underdog machinery (`contracts.py`, `magolide.py`, `training.py`, `research.py`, `pipeline.py`, `history.py`, `feature_contracts.py`, sport modules, tests) vs price-free candidate contract.
- `FOREBET_DEPTH_AUDIT.md` needs note that odds are optional context, not required for upset learning, and that ROI is not primary metric (if optional odds used for retrospective context, report separately).
- `feature_contracts.py` currently `MODEL_TRAINING_ALLOWED = False` — remains frozen until user approves dataset/target/timing/validation.
- No doc deletions authorized until user reviews this classification.
