# docs/ Index — Slumdog Documentation Governance

**Last verified:** 2026-08-24 (UTC)
**Canonical truth:** `docs/STATE.md`
**Read order:** `AGENTS.md` → `README.md` → `docs/STATE.md` → `HANDOFF.md` → `docs/PRICE_FREE_DATASET_CONTRACT.md` → `docs/FEATURE_TIMING_CONTRACT.md` → `docs/FOREBET_DEPTH_AUDIT.md` → source/tests

This index classifies every file under `docs/` as:

- **CURRENT** — authoritative and maintained, reflects current contract
- **REFERENCE** — stable technical reference, accurate but not daily-changing
- **HISTORICAL** — useful evidence, not current operating truth
- **STALE** — superseded or inaccurate, candidate for update/consolidation/removal
- **UNKNOWN** — requires user review

## Inventory

| File | Purpose | Status | Last Verified | Canonical / Superseded |
|------|---------|--------|---------------|------------------------|
| `STATE.md` | Canonical current truth: phase, merged work, blockers, training status, data limitations, next milestone, parked work, unresolved evidence, links. | **CURRENT** | 2026-08-24 | Canonical — replaces root `STATE.md` (moved via `git mv`). |
| `PRICE_FREE_DATASET_CONTRACT.md` | Price-free dataset contract: PriceFreeUnderdogExample contract, receipt, builder with chronological evidence, minimal safe feature set ALLOWED only, missingness policy, timing guarantees, eligibility rules, receipt accounting, price-independence, Codespace command. | **CURRENT** | 2026-08-24 | Current operating contract for Milestone 4 — governs research dataset foundation, no model training. |
| `FEATURE_TIMING_CONTRACT.md` | Feature timing and leakage audit: period_values 10-point investigation (UNKNOWN PROHIBITED), full feature inventory with required columns, missingness audit, new-path eligibility ALLOWED/PROHIBITED/PARKED. | **CURRENT** (Milestone 3 COMPLETE, still governing ALLOWED) | 2026-08-24 | Governing contract for allowed features — Milestone 3 COMPLETE, remains CURRENT as input to Milestone 4. |
| `MILESTONE1_AUDIT.md` | Price-free machinery audit: 10 gaps, staged plan, 8 refinements. | **REFERENCE** | 2026-08-24 | Evidence record for Milestone 1, approved. Gaps resolved for identity/label/contracts/timing. |
| `FOREBET_DEPTH_AUDIT.md` | Training freeze receipt, common listing surface, match-detail surface by sport, historical depth contract, 3-page detail coverage table, representative price coverage, verified no-odds gaps, duplicate audits. | **CURRENT** | 2026-08-24 | Current operating evidence; complements STATE.md and dataset contract. Price coverage reference only. |
| `FOREBET_ARCHIVE_DEPTH.json` | Annual archive probe matrix proving availability lower bounds. | **REFERENCE** | 2026-08-24 | Stable reference; source for depth audit. |
| `FOREBET_DETAIL_COVERAGE.json` | 3 live detail pages per sport checked for factor availability. | **REFERENCE** | 2026-08-24 | Reference; superseded for missingness by future census but retained as justification. |
| `FOREBET_PRICE_COVERAGE.json` | Representative displayed-price snapshot: one active-season date per sport. | **REFERENCE** | 2026-08-24 | **Reference evidence only. Price coverage is not a Slumdog candidate-readiness gate.** |
| `FOREBET_FACET_ANALYSIS_PLAN.md` | Timing class definitions, common fields catalogue, 10-step analysis order. | **REFERENCE** | 2026-08-24 | Stable technical plan; still valid but step 5 calibration must not use ROI-primary; step 6 lift is over Forebet underdog probability (price-free). |
| `MA_GOLIDE_ROBBER_FORENSIC.md` | Forensic spec of legacy Ma Golide Robber: odds-first cascade, defects. | **HISTORICAL** | 2026-08-24 | Historical evidence — useful to understand magolide.py but NOT current operating truth. |
| `README.md` (this file) | Doc index with purpose/status/last-verified/canonical relationships. | **CURRENT** | 2026-08-24 | Canonical index — required by Milestone 0, updated for Milestone 4. |

## Classification Report (Milestone 0-4)

- **Files moved:** `STATE.md` → `docs/STATE.md` via `git mv`
- **Milestone 0: COMPLETE**, no deletions authorized
- **Milestone 1: COMPLETE — reference audit** — 10 gaps, 8 refinements, now REFERENCE
- **Milestone 2: COMPLETE — price-free identity, label and contracts (including 2E hardening)** — identity-bound label, SPORTS registry draw capability, exact reason preservation, 40 tests, 232 total passed. MILESTONE1_AUDIT.md → REFERENCE.
- **Milestone 3: COMPLETE — feature timing contract** — `docs/FEATURE_TIMING_CONTRACT.md` with period_values UNKNOWN PROHIBITED (does not block future progress, stays outside new path), full inventory, missingness audit. Remains CURRENT as governing ALLOWED for Milestone 4.
- **Milestone 4 CURRENT:** `docs/PRICE_FREE_DATASET_CONTRACT.md` is now CURRENT — governs price-free historical example builder, minimal safe feature set ALLOWED only, missingness policy None+indicator, timing guarantees history_event_date < current_event_date same-date excluded, eligibility rules, receipt accounting, price-independence tests, no model training, no integration into legacy training.py yet.
- **Stale/duplicate docs:** None. `MA_GOLIDE_ROBBER_FORENSIC.md` is HISTORICAL but not STALE.
- **Proposed removals:** None.
- **Final canonical read order:** `AGENTS.md` → `README.md` → `docs/STATE.md` → `HANDOFF.md` → `docs/PRICE_FREE_DATASET_CONTRACT.md` → `docs/FEATURE_TIMING_CONTRACT.md` → `docs/FOREBET_DEPTH_AUDIT.md` → source/tests
- **Freshness lock:** Every substantive PR must update when applicable: `docs/STATE.md`, `HANDOFF.md`, `docs/README.md`, relevant audit doc.

## Cross-References Proof

Run:
```bash
grep -Rni --exclude-dir=.git 'STATE\.md' .
```

Expected:
- `README.md` should reference `docs/STATE.md`
- `AGENTS.md` should reference `docs/STATE.md`
- `docs/README.md` and `docs/STATE.md` self-reference canonical path

## Price Coverage Clarification

```text
FOREBET_PRICE_COVERAGE.json is reference evidence only.
Price coverage is not a Slumdog candidate-readiness gate.
```

- Odds are optional metadata per `AGENTS.md` invariants.
- Missing odds must not lower confidence, must not gate candidates, must not be model features.
- Sparse or missing prices are parked observations, not blockers.
- ROI is not primary metric.

## Notes for Next Milestone

- Milestone 0: COMPLETE
- Milestone 1: COMPLETE — reference audit
- Milestone 2: COMPLETE — price-free identity, label and contracts (including 2E hardening: identity-bound public API, SPORTS registry draw capability, exact reason preservation, 40 tests)
- Milestone 3: COMPLETE — feature timing contract (period_values UNKNOWN PROHIBITED, does not block future progress, stays outside new path, full inventory, missingness audit)
- Milestone 4 CURRENT (research dataset foundation, no model training): Build leak-safe, price-free historical example foundation — every eligible settled event, not only legacy Robber candidates, flow settled→probs→identity→prior-only evidence→feature snapshot→label, never through odds. Contract PriceFreeUnderdogExample (event_id, sport, event_date, favorite_index, underdog_index, favorite_probability, underdog_probability, draw_probability, probability_gap, label 0/1, features, missingness, source_url, raw_sha256, feature_contract_version, label_contract_version, exclusion_reason, legacy_provenance_missing), minimal safe feature set ALLOWED only (forebet_favorite_probability, forebet_underdog_probability, forebet_probability_gap, forebet_draw_probability, forebet_draw_probability_missing, underdog_prior_games, favorite_prior_games, underdog_prior_win_rate, favorite_prior_win_rate, recent_win_rate_gap, h2h_prior_games, h2h_underdog_win_rate, h2h_draw_rate, underdog_prior_draw_rate, favorite_prior_draw_rate, prior_scoring_rate_gap, prior_conceding_rate_gap), missingness policy None preserved + indicator, timing guarantees history_event_date < current_event_date same-date excluded, eligibility rules draw-capable draw=0 two-way draw excluded void excluded equal/missing/non-finite/out-of-range excluded odds availability no effect, receipt accounting input_rows = eligible + exclusions, deterministic output, price-independence tests, 30 new tests, full suite 262 passed, training frozen, no integration into legacy training.py yet.
- `feature_contracts.py` currently `MODEL_TRAINING_ALLOWED = False` — remains frozen until user approves dataset/target/timing/validation after Milestone 4.
- Next after Milestone 4 approval: Milestone 5 — transparent baselines (Forebet underdog prob, gap, recent-form differential, Ma Golide heuristic, simple interpretable model) with walk-forward validation, never random splits.
