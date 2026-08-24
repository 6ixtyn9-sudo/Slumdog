# docs/ Index — Slumdog Documentation Governance

**Last verified:** 2026-08-24 (UTC)
**Canonical truth:** `docs/STATE.md`
**Read order:** `AGENTS.md` → `README.md` → `docs/STATE.md` → `HANDOFF.md` → `docs/FEATURE_TIMING_CONTRACT.md` → `docs/FOREBET_DEPTH_AUDIT.md` → source/tests

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
| `FEATURE_TIMING_CONTRACT.md` | Feature timing and leakage audit: period_values 10-point investigation, full feature inventory with columns Feature|Family|Sport|Source|Raw|Timing|Evidence|Missing|Indicator|Odds-dependent|Legacy|New-path|Action, missingness audit GENUINE_ZERO/UNKNOWN_ENCODED_AS_ZERO/SAFE_MATHEMATICAL_DEFAULT/UNRESOLVED, new-path eligibility ALLOWED/PROHIBITED/PARKED, odds-dependent prohibited, RESULT_ONLY prohibited, UNKNOWN prohibited until verified. | **CURRENT** | 2026-08-24 | Current operating contract for Milestone 3 — governs which features may enter price-free vector. |
| `MILESTONE1_AUDIT.md` | Price-free machinery audit: 10 gaps, staged plan, 8 refinements. | **REFERENCE** (was CURRENT during migration) | 2026-08-24 | Evidence record for Milestone 1, approved. Gaps 1-2 partially resolved by Milestone 2 identity/label/contracts; remaining gaps addressed by FEATURE_TIMING_CONTRACT.md. Do not let 885-line audit become permanent current truth — STATE.md and FEATURE_TIMING_CONTRACT.md are now concise authorities. |
| `FOREBET_DEPTH_AUDIT.md` | Training freeze receipt, common listing surface, match-detail surface by sport, historical depth contract, 3-page detail coverage table, representative price coverage, verified no-odds gaps, duplicate audits. | **CURRENT** | 2026-08-24 | Current operating evidence; complements STATE.md and FEATURE_TIMING_CONTRACT.md. Price coverage reference only. |
| `FOREBET_ARCHIVE_DEPTH.json` | Annual archive probe matrix proving availability lower bounds; conservative backfill start dates. | **REFERENCE** | 2026-08-24 | Stable reference; source for depth audit. |
| `FOREBET_DETAIL_COVERAGE.json` | 3 live detail pages per sport checked for factor availability. | **REFERENCE** | 2026-08-24 | Reference; superseded for missingness by future full census but retained as justification. |
| `FOREBET_PRICE_COVERAGE.json` | Representative displayed-price snapshot: one active-season date per sport. | **REFERENCE** | 2026-08-24 | **Reference evidence only. Price coverage is not a Slumdog candidate-readiness gate.** |
| `FOREBET_FACET_ANALYSIS_PLAN.md` | Timing class definitions, common fields catalogue, 10-step analysis order. | **REFERENCE** | 2026-08-24 | Stable technical plan; still valid but step 5 calibration must not use ROI-primary; step 6 lift is over Forebet underdog probability (price-free). |
| `MA_GOLIDE_ROBBER_FORENSIC.md` | Forensic spec of legacy Ma Golide Robber: odds-first cascade, defects. | **HISTORICAL** | 2026-08-24 | Historical evidence — useful to understand magolide.py but NOT current operating truth. New mission supersedes with Forebet-probability underdog. |
| `README.md` (this file) | Doc index with purpose/status/last-verified/canonical relationships. | **CURRENT** | 2026-08-24 | Canonical index — required by Milestone 0, updated for Milestone 3. |

## Classification Report (Milestone 0-3)

- **Files moved:** `STATE.md` → `docs/STATE.md` via `git mv`
- **Links updated:** `README.md` → `docs/STATE.md`; `AGENTS.md` read order points to `docs/STATE.md`; `docs/STATE.md` self-documents canonical path.
- **Milestone 2 COMPLETE:** `src/slumdog/underdog.py` with identity-bound label hardening (2E), 40 tests, 232 total passed. MILESTONE1_AUDIT.md → REFERENCE.
- **Milestone 3 CURRENT:** `docs/FEATURE_TIMING_CONTRACT.md` is now CURRENT — governs feature timing, leakage, odds exclusion, missingness policy. It contains period_values 10-point investigation (UNKNOWN → PROHIBITED), full inventory with required columns, missingness audit.
- **Stale/duplicate docs:** None proven obsolete yet. `MA_GOLIDE_ROBBER_FORENSIC.md` is HISTORICAL but not STALE — retains forensic value.
- **Proposed removals:** None. Do not delete evidence to make tree look clean.
- **UNKNOWN requiring user review:** None currently; all files have clear purpose.
- **Final canonical read order:** `AGENTS.md` → `README.md` → `docs/STATE.md` → `HANDOFF.md` → `docs/FEATURE_TIMING_CONTRACT.md` → `docs/FOREBET_DEPTH_AUDIT.md` → `FOREBET_ARCHIVE_DEPTH.json` / `FOREBET_DETAIL_COVERAGE.json` / `FOREBET_PRICE_COVERAGE.json` / `FOREBET_FACET_ANALYSIS_PLAN.md` / `MA_GOLIDE_ROBBER_FORENSIC.md` → source/tests
- **Freshness lock:** Every substantive PR must update when applicable: `docs/STATE.md`, `HANDOFF.md`, `docs/README.md`, relevant audit doc. PR incomplete if docs stale.

## Cross-References Proof

Run:
```bash
grep -Rni --exclude-dir=.git 'STATE\.md' .
```

Expected:
- `README.md` should reference `docs/STATE.md` (not root)
- `AGENTS.md` should reference `docs/STATE.md`
- `docs/README.md` and `docs/STATE.md` self-reference canonical path
- No stale root `STATE.md` references

## Price Coverage Clarification (Required Correction)

```text
FOREBET_PRICE_COVERAGE.json is reference evidence only.
Price coverage is not a Slumdog candidate-readiness gate.
```

- Odds are optional metadata per `AGENTS.md` invariants.
- Missing odds must not lower confidence, must not gate candidates, must not be model features.
- Sparse or missing prices are parked observations, not blockers.
- ROI is not primary metric.

## Notes for Next Milestone

- Milestone 0: COMPLETE, no deletions authorized.
- Milestone 1: COMPLETE, now REFERENCE — 10 gaps, 8 refinements. Gaps 1-2 partially resolved by Milestone 2.
- Milestone 2: COMPLETE (including 2E hardening) — pure Forebet identity, identity-bound label with SPORTS registry draw capability, exact reason preservation, price-free contracts, 40 tests, 232 total passed, training frozen, compatibility boundary explicit (no integration into legacy training.py yet). Contract notes for later: nested mutability (frozen dataclass with mutable dicts must be defensively copied before immutable receipts, Milestone 6), status semantics (STRONG_UNDERDOG must not imply approved probability until scoring/thresholds approved, reserved fields None).
- Milestone 3 CURRENT (read-only, no code change): Feature timing and leakage audit — `docs/FEATURE_TIMING_CONTRACT.md` with required columns, period_values 10-point investigation (DOM selector .predQ .fj_column, listing parser parsers.py:170-173, contract facets["period_values"], feature builders basketball.py:287 etc, sports used, ambiguous predicted vs completed, populated for upcoming unknown, settlement flow into period_scores_1/2 not same key, tests synthetic, final timing UNKNOWN PROHIBITED), full feature inventory (Forebet probs, draw prob, gap/ratio, entropy/dominance, recent form, home/away, win rates, table position, H2H, goals/points, shots, shots on target, blocked/off-target, possession, passes accuracy, attacks, dangerous attacks, event-time, schedule difficulty, weather, venue, stable IDs, cup flags, trend text, double chance, goalscorer, sport-specific physical/stat, every price/odds/overround/fair prob/value-edge, every final/period/penalty/extra-time/disposition/settlement), missingness audit None/NaN/0/empty/absent/sentinel and zero fallback classification GENUINE_ZERO/UNKNOWN_ENCODED_AS_ZERO/SAFE_MATHEMATICAL_DEFAULT/UNRESOLVED, rules RESULT_ONLY→prohibited, UNKNOWN→prohibited until verified, odds-dependent→prohibited, missing odds irrelevant, existing use does not prove eligibility, suggestive name does not prove timing, evidence must cite code or retained bytes.
- `feature_contracts.py` currently `MODEL_TRAINING_ALLOWED = False` — remains frozen until user approves dataset/target/timing/validation after Milestone 3.
- Next after Milestone 3 approval: Milestone 4 — implement price-free feature vector based on FEATURE_TIMING_CONTRACT.md ALLOWED only, with missingness indicators, no odds, no RESULT_ONLY, no UNKNOWN.
