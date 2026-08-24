# Slumdog

> **Slumdog identifies a small daily shortlist of participants that Forebet considers underdogs but whose available pre-event evidence indicates a credible outright-win upset.**

Slumdog is a standalone multi-sport research system that:

- captures every supported Forebet sport as immutable raw HTML/JSON,
- identifies Forebet underdogs (lower Forebet probability),
- evaluates pre-event evidence for credible outright-win upsets,
- emits a small daily shortlist (1–3) ranked by upset strength, or `NO_STRONG_UNDERDOG` when evidence insufficient,
- freezes selections before kickoff and settles afterward, counting draws as failed `UNDERDOG_WIN`.

This is **not** a value-betting, odds-first, EV, de-vigging, Kelly, or bookmaker-coverage system. Prices are optional context only.

## Product Invariants

- Target is `UNDERDOG_WIN` outright win only. Slumdog never selects draws.
- In draw-capable sports, draw = failed prediction.
- Odds optional metadata only — not required to create candidate, not a model feature, not eligibility gate, missing odds must not lower confidence.
- Never force daily pick; no-pick day valid.
- Never claim guaranteed wins/income/life-changing results.
- Training frozen until user approves dataset, target, timing, validation contract.

## Architecture

```
Forebet sport pages
        |
        v
immutable sport captures (Satellites) — raw HTML/JSON + SHA-256
        |
        v
facet catalogue + timing provenance (PRE_EVENT / LIVE_ONLY / RESULT_ONLY / UNKNOWN)
        |
        v
price-free candidate contract (event identity, Forebet fav/underdog, probs, draw prob, model prob, lift, strength, evidence, missingness, status, version, provenance, optional price)
        |
        v
transparent baselines → interpretable model → daily shortlist (STRONG_UNDERDOG / WATCHLIST / INSUFFICIENT_EVIDENCE / REJECTED_SOURCE_CONFLICT / NO_STRONG_UNDERDOG)
        |
        v
immutable shadow receipts (frozen before kickoff) → settlement → honest forward tracking
```

## Sports

Football, Basketball, Tennis, Hockey, Baseball, American Football, Rugby, Handball, Volleyball, Cricket, MMA, Esoccer. Each sport has its own outcome and settlement contract; probabilities and thresholds never pooled blindly.

## Candidate Contract (price-free)

- event identity, sport, event date/time
- Forebet favorite + underdog (by Forebet participant probabilities)
- Forebet favorite probability, Forebet underdog probability, draw probability where applicable
- model underdog-win probability, lift over Forebet underdog probability, strength score
- supporting evidence, contradicting evidence, missing evidence (explicit)
- candidate status, model/version identifier, source/provenance identifiers
- optional price context (if present, displayed separately, not determining strength)

## Settlement Rules

- Two-way: label=1 only if underdog wins outright; 0 if favorite wins; void/no-contest/cancelled excluded.
- Draw-capable: label=1 only if selected underdog wins outright; 0 if favorite wins OR drawn; draw never selected.
- Equal-probability rows require explicit policy, must not be silently assigned.
- Voids/cancelled/abandoned excluded; conflicting source facts fail loudly.

## Status

- Phase: Milestone 3 — feature timing and leakage audit (read-only, no code change). Milestone 0 COMPLETE, Milestone 1 COMPLETE now REFERENCE, Milestone 2 COMPLETE including 2E hardening (identity-bound label, SPORTS registry draw capability, exact reason preservation, 40 tests, 232 total). `docs/STATE.md` is canonical current truth, `docs/FEATURE_TIMING_CONTRACT.md` is CURRENT feature timing contract (period_values UNKNOWN PROHIBITED until verified, full inventory with required columns, missingness audit).
- Model training: FROZEN. See `docs/STATE.md` for blockers (missing prices NOT blockers), data limitations (reference observations), unresolved evidence. No feature-vector/threshold/ranking/model approval/daily production changes until Milestone 3 approved.
- Slumdog emits shadow research only; no CERTIFIED output.

## Quick Start

```bash
python -m pip install -e '.[dev]'
pytest

# Dates from runner clock TZ Africa/Johannesburg; --date/--start/--end are optional overrides, omit for "now"

# Freeze all Forebet sport surfaces for today (or --date YYYY-MM-DD)
slumdog capture

# Bounded historical probe: trailing 7 days (or --start/--end)
slumdog backfill

# Full current-board census: all listings + every available match detail
slumdog depth-sweep --per-sport 1000000

# Full dated history for one sport; rolling/resumable ledger, repeat fetches only new dates; --end defaults to yesterday
slumdog backfill-sport --sport basketball --start 2023-01-01

# Research report (field missingness, coverage by season/league, priced vs unpriced) once data exists
slumdog analyze

# Lower-level parser dev
slumdog parse
slumdog details --events data/interim/events_$(date +%F).json --max-events 18
slumdog enrich --events data/interim/events_$(date +%F).json

# Model commands behind explicit research override only
```

GitHub pipeline (`.github/workflows/pipeline.yml`):
- daily 03:00 UTC (Sun, Tue–Sat) history accumulation through yesterday
- Mon 02:00 UTC full census + consolidated receipt (covers Monday history)
- manual dispatch (no inputs) runs both

## Documentation Policy

- `AGENTS.md` — permanent mission + operating constitution (read first)
- `docs/STATE.md` — canonical current truth (not append-only diary; Git history is history)
- `HANDOFF.md` — session continuation record
- `docs/README.md` — doc index with purpose/status/last-verified
- `docs/FOREBET_DEPTH_AUDIT.md` — depth freeze receipt + coverage + duplicate audits
- Every substantive PR must update when applicable: `docs/STATE.md`, `HANDOFF.md`, `docs/README.md`, relevant audit doc. PR incomplete if docs stale.

## Documentation Governance

Canonical read order for agents:

1. `AGENTS.md`
2. `README.md`
3. `docs/STATE.md`
4. `HANDOFF.md`
5. `docs/FOREBET_DEPTH_AUDIT.md`
6. relevant source/tests

Main is only permanent branch. Arena `arena/...` branch is delivery only. Do not force-push main.
