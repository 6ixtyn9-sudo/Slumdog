# AGENTS.md — Slumdog Permanent Operating Constitution

Every future agent MUST read in this order before changing anything:

1. `AGENTS.md` (this file — mission + invariants)
2. `README.md` (current product overview)
3. `docs/STATE.md` (canonical current truth)
4. `HANDOFF.md` (session continuation record)
5. `docs/FOREBET_DEPTH_AUDIT.md` (depth freeze receipt + coverage)
6. Relevant source and tests for the active task

## Permanent Product Mission

> **Slumdog identifies a small daily shortlist of participants that Forebet considers underdogs but whose available pre-event evidence indicates a credible outright-win upset.**

This is NOT a value-betting, odds-first, EV, de-vigging, Kelly, or bookmaker-coverage system.

## Product Invariants — DO NOT DIVERGE

1. Target is `UNDERDOG_WIN` — outright win only.
2. Slumdog never selects draws.
3. In draw-capable sports, a draw is a failed `UNDERDOG_WIN` prediction.
4. Do not silently convert target to "underdog avoids defeat."
5. Odds are optional metadata only.
6. Odds must not be required to create a candidate.
7. Missing odds must not lower candidate confidence.
8. Odds must not be model features.
9. Do not gate candidates on odds availability.
10. Do not turn project into EV / de-vigging / Kelly / staking / bookmaker-coverage.
11. If odds exist, display separately as optional context, but they do not determine underdog strength.
12. Never force a pick merely to satisfy a daily quota.
13. Valid no-pick day (`NO_STRONG_UNDERDOG`) is better than weak/fabricated candidate.
14. Never claim guaranteed wins, guaranteed income, or life-changing financial outcomes.
15. Model training remains frozen until user approves dataset, target, timing, validation contract.

## Desired End Product

- Small daily shortlist, preferably 1–3 candidates.
- Each candidate is outright underdog-win selection.
- Ranked by credible upset strength.
- Every candidate explains supporting + contradicting + missing evidence.
- System can output `NO_STRONG_UNDERDOG` when nothing qualifies.
- Results frozen before events and settled afterward.
- Forward performance measured honestly (hit rate, calibration, not ROI-first).

## Repository Workflow

- `main` is the ONLY permanent branch.
- Arena assigns temporary `arena/...` branch — use as delivery mechanism only.
- Never create another permanent branch. Never work directly on other long-lived branch.
- Do not merge until user explicitly authorizes.
- Before merge, update durable docs and verification receipt.
- After merge, user returns Codespace to `main` and deletes temporary branch locally + remotely.
- Never force-push `main`. Never delete unmerged work.

## Filesystem Separation

- Arena checkout: `/home/user/Slumdog`
- User Codespace checkout: `/workspaces/Slumdog`
- Separate filesystems. Uncommitted files, ignored files, captures, ledgers do not cross.
- Tracked Git changes transfer only via commit/push/pull.
- Never commit raw captures, ledgers, temporary archives, or secrets.

## Change Control

- Discuss findings before coding unless pre-authorized.
- Forebet is sole external prediction source. Preserve immutable captures.
- Missing stays missing; never zero-fill.
- Result, final score, settlement status, post-event facts cannot enter features.
- Every parser change needs minimal fixture-based regression test.
- Prefer retained bytes before network. Use existing collector/relay code, at most 6 workers, small batches, 62s pauses. One-off probes sequential/minimal, record URL/date/route/result.
- Do not run American-football odds probe before ~2026-09-10.

## Documentation Governance

- `docs/STATE.md` is canonical current truth, not append-only diary. Git history is history.
- `HANDOFF.md` is session continuation record.
- `AGENTS.md` is permanent mission + operating constitution.
- Every substantive PR must update when applicable: `docs/STATE.md`, `HANDOFF.md`, `docs/README.md`, relevant audit doc.
- PR incomplete if code changes make durable docs stale.
- Before push: `python -m pytest -q`, `python3 -m py_compile scripts/*.py src/slumdog/*.py tests/*.py`, `python -m pyflakes scripts src/slumdog tests`, `git diff --check`, `git status --short`. If Arena lacks deps, run what is possible, give user exact Codespace commands. Never claim skipped test passed.
