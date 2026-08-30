# docs/ Index — Slumdog Documentation Governance

**Last verified:** 2026-08-26 (UTC)
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
| `STATE.md` | Canonical current truth: phase, merged work, blockers, training status, data limitations, next milestone, parked work, unresolved evidence, links. | **CURRENT** | 2026-08-24 | Canonical — replaces root `STATE.md` (moved via `git mv`). Milestone 4: COMPLETE — pending real-data receipt execution, Milestone 5 readiness review. |
| `PRICE_FREE_DATASET_CONTRACT.md` | Price-free dataset contract: PriceFreeUnderdogExample contract, receipt with raw vs canonical accounting, builder with hardening (no unsafe defaults, no silent swallowing, strengthened digest, duplicate identity validated, provenance validated, date semantics explicit), minimal safe feature set ALLOWED only, missingness policy, timing guarantees, eligibility rules, receipt invariants, adapter schemas, hardened Codespace command, Milestone 6A research-only mode (explicit opt-in, census-before-collapse, whole-key conflict exclusion, deterministic /tmp artifacts, simplified receipt). | **CURRENT** | 2026-08-26 | Current operating contract — Milestone 4 COMPLETE (4E hardening verified), real-data census executed, Milestone 6A research mode added. No model training. |
| `FEATURE_TIMING_CONTRACT.md` | Feature timing and leakage audit: period_values 10-point investigation (UNKNOWN PROHIBITED), full feature inventory with required columns, missingness audit, new-path eligibility ALLOWED/PROHIBITED/PARKED. | **CURRENT** (Milestone 3 COMPLETE, still governing ALLOWED for Milestone 4 COMPLETE) | 2026-08-24 | Governing contract for allowed features — Milestone 3 COMPLETE, remains CURRENT as input to Milestone 4 COMPLETE. |
| `HISTORICAL_INTEGRITY_AUDIT.md` | Verified historical ledger conflicts, malformed-row evidence, provenance limitations, and unresolved integrity policy. | **CURRENT** | 2026-08-26 | Canonical evidence record for the Milestone 5 historical-integrity investigation: hockey duplicate mechanism documented, source-body origin layer UNKNOWN, provenance-free research policy unapproved. |
| `MILESTONE1_AUDIT.md` | Price-free machinery audit: 10 gaps, staged plan, 8 refinements. | **REFERENCE** | 2026-08-24 | Evidence record for Milestone 1, approved. Gaps resolved for identity/label/contracts/timing. |
| `FOREBET_DEPTH_AUDIT.md` | Training freeze receipt, common listing surface, match-detail surface by sport, historical depth contract, 3-page detail coverage table, representative price coverage, verified no-odds gaps, duplicate audits. | **CURRENT** | 2026-08-24 | Current operating evidence; complements STATE.md and dataset contract. Price coverage reference only. |
| `FOREBET_ARCHIVE_DEPTH.json` | Annual archive probe matrix proving availability lower bounds. | **REFERENCE** | 2026-08-24 | Stable reference; source for depth audit. |
| `FOREBET_DETAIL_COVERAGE.json` | 3 live detail pages per sport checked for factor availability. | **REFERENCE** | 2026-08-24 | Reference; superseded for missingness by future census but retained as justification. |
| `FOREBET_PRICE_COVERAGE.json` | Representative displayed-price snapshot: one active-season date per sport. | **REFERENCE** | 2026-08-24 | **Reference evidence only. Price coverage is not a Slumdog candidate-readiness gate.** |
| `FOREBET_FACET_ANALYSIS_PLAN.md` | Timing class definitions, common fields catalogue, 10-step analysis order. | **REFERENCE** | 2026-08-24 | Stable technical plan; still valid but step 5 calibration must not use ROI-primary; step 6 lift is over Forebet underdog probability (price-free). |
| `MA_GOLIDE_ROBBER_FORENSIC.md` | Forensic spec of legacy Ma Golide Robber: odds-first cascade, defects. | **HISTORICAL** | 2026-08-24 | Historical evidence — useful to understand magolide.py but NOT current operating truth. |
| `MILESTONE7_SHADOW_PICKS_PLAN.md` | Milestone 7 shadow pick evaluator plan. | **REFERENCE** | 2026-08-28 | Plan record for the merged Milestone 7 evaluator work. |
| `MILESTONE7B_SHADOW_BUNDLE.md` | Verifiable full-payload shadow bundle (create + verify, deterministic archives, bounded-memory streaming). | **CURRENT** | 2026-08-29 | Operating doc for the merged (PR #12) Milestone 7B bundle tooling. |
| `MILESTONE7D_CLOUD_BACKUP.md` | Cloud-only second-copy procedure: manual-dispatch workflow, synthetic fixture generator, artifact retention (30 days, NOT permanent), verification receipt, honest status distinctions. | **CURRENT** | 2026-08-30 | Operating doc for the Milestone 7D cloud backup PR (opened, NOT merged; workflow NOT yet dispatched). |
| `README.md` (this file) | Doc index with purpose/status/last-verified/canonical relationships. | **CURRENT** | 2026-08-24 | Canonical index — required by Milestone 0, updated for Milestone 4 COMPLETE (4E hardening). |

## Classification Report (Milestone 0-4E)

- **Files moved:** `STATE.md` → `docs/STATE.md` via `git mv`
- **Milestone 0: COMPLETE**, no deletions authorized
- **Milestone 1: COMPLETE — reference audit** — 10 gaps, 8 refinements, now REFERENCE
- **Milestone 2: COMPLETE — price-free identity, label and contracts (including 2E hardening)** — identity-bound label, SPORTS registry draw capability, exact reason preservation, 40 tests, 232 total passed. MILESTONE1_AUDIT.md → REFERENCE.
- **Milestone 3: COMPLETE — feature timing contract** — `docs/FEATURE_TIMING_CONTRACT.md` with period_values UNKNOWN PROHIBITED (does not block future progress, stays outside new path), full inventory, missingness audit. Remains CURRENT as governing ALLOWED for Milestone 4.
- **Milestone 4: COMPLETE — pending real-data receipt execution** — `docs/PRICE_FREE_DATASET_CONTRACT.md` is now CURRENT hardened — governs price-free historical example builder, minimal safe feature set ALLOWED only, missingness policy None+indicator, timing guarantees history_event_date < current_event_date same-date excluded, eligibility rules, receipt accounting with invariants raw=schema+valid valid=exact+canonical canonical=eligible+builder, deterministic output, price-independence tests, raw vs canonical accounting, strengthened digest (versioned fields excluding odds deliberately, stable under reordering), duplicate identity composite key (sport,event_id,event_date) exact collapse vs conflict fail loudly, provenance 64-hex validation, date semantics canonical vs eligible explicit, no unsafe defaults, no silent swallowing, adapter schemas settled_history.json and history_*.jsonl.gz tested, hardened Codespace command `python -m slumdog.dataset_audit --root data --receipt /tmp/slumdog_price_free/receipt.json --sample /tmp/slumdog_price_free/examples_sample.json --sample-size 5`, 30 tests + 34 hardening + 9 audit = 305 total passed, training frozen, no integration into legacy training.py yet.
- **Stale/duplicate docs:** None. `MA_GOLIDE_ROBBER_FORENSIC.md` is HISTORICAL but not STALE.
- **Proposed removals:** None.
- **Final canonical read order:** `AGENTS.md` → `README.md` → `docs/STATE.md` → `HANDOFF.md` → `docs/PRICE_FREE_DATASET_CONTRACT.md` → `docs/FEATURE_TIMING_CONTRACT.md` → `docs/FOREBET_DEPTH_AUDIT.md` → source/tests
- **Freshness lock:** Every substantive PR must update when applicable: `docs/STATE.md`, `HANDOFF.md`, `docs/README.md`, relevant audit doc.

## Cross-References Proof

Run:
```bash
grep -Rni --exclude-dir=.git 'STATE\\.md' .
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
- Milestone 4: COMPLETE — pending real-data receipt execution — Build leak-safe, price-free historical example foundation — every eligible settled event, not only legacy Robber candidates, flow settled→probs→identity→prior-only evidence→feature snapshot→label, never through odds. Hardened _validate_settled_dict no defaults, canonical repr versioned, digest stable under reordering odds excluded deliberately, duplicate identity composite key exact collapse vs conflict fail loudly, provenance 64-hex validation, date semantics canonical vs eligible explicit, minimal safe feature set ALLOWED only, missingness policy, timing guarantees, eligibility rules, receipt invariants, adapter schemas tested, hardened command `python -m slumdog.dataset_audit --root data --receipt /tmp/slumdog_price_free/receipt.json --sample /tmp/slumdog_price_free/examples_sample.json --sample-size 5`, 30 + 34 + 9 = 73 new tests, 305 total passed, training frozen.
- `feature_contracts.py` currently `MODEL_TRAINING_ALLOWED = False` — remains frozen until user approves dataset/target/timing/validation after Milestone 4 real-data receipt execution.
- Next after Milestone 4 approval: Milestone 5 readiness review — transparent baselines (Forebet underdog prob, gap, recent-form differential, Ma Golide heuristic, simple interpretable model) with walk-forward validation, never random splits.
