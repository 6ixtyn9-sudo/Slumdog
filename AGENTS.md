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
- **Scoped waiver (2026-09-03, owner directive "no ledgers in Codespace"):**
  small shadow evidence IS committed to git: `shadow_selections.json`,
  `manifest.json`, `capture_*.json` receipts, `*.settlement.json` +
  `.sha256` markers, `*.bundle.json` receipts, `*.tar.gz.sha256` markers,
  and `status.tsv` under `data/reports/shadow/`. This waiver covers ONLY
  these small JSON/text files — never raw capture bodies (HTML/JSON),
  never `*.tar.gz` bundle archives, never history ledgers. Those remain
  in Actions artifacts (30-day retention) or durable object storage.
- **Waiver extension (2026-09-08, owner directive "resolve the errata waiver
  scope before adding more surface area"):** `*.rank4_erratum.json` +
  `.sha256` markers under `data/reports/shadow/errata/` are added to the
  scoped waiver above. These are append-only corrections to already-committed
  settlement evidence (measured 2026-09-08: 5.2–86 KiB JSON across the three
  published errata, same size class as the `*.settlement.json` files already
  covered). They never restate or overwrite
  the originals, which remain byte-identical and authoritative. All other
  exclusions in the waiver above are unchanged: still never raw capture
  bodies, never `*.tar.gz` archives, never history ledgers.
- **Waiver extension (2026-09-14, owner directive `build_include_unresolved`):**
  `settlement_supplement_*.json` + `.sha256` markers under
  `data/reports/shadow/<date>/<run>/` and
  `settlement_capture_receipt_completion_*.json` receipts under
  `data/settlement_evidence/<date>/` are added to the scoped waiver. These
  are the settlement completion pass's append-only records: they close rows
  the one-shot D+1 settlement left UNSETTLED/UNRESOLVED (retry window D+1
  through D+14), and never modify or overwrite the original
  `settlement.json` — its SHA-256 marker is re-verified before a supplement
  is written, decided SUCCESS/FAILURE grades are immutable and never
  re-graded, and every supplement carries its own marker. They are the same
  small-JSON size class as the `*.settlement.json` files already covered.
  Raw completion capture bodies (HTML/JSON) remain outside git (30-day
  Actions artifacts); all other exclusions in the waiver above are
  unchanged.
- **Waiver extension (2026-09-22, owner directive "near-term re-capture
  coverage fix"):** `selections_delta_<stamp>.json` (+ `.sha256`) and their
  `selections_delta_<stamp>.manifest.json` (+ `.sha256`) copies under
  `data/reports/shadow/<date>/<run>/`, `settlement_delta_<stamp>.json` +
  `.sha256` markers in the same run dirs, `capture_refresh_<date>_<stamp>.json`
  receipts under `data/reports/`, and `settlement_capture_receipt_delta_*.json`
  receipts under `data/settlement_evidence/<date>/` are added to the scoped
  waiver. These are the daily-refresh (near-term re-capture) pass's
  append-only records: the T+1/T+2 dates are re-captured so late-publishing
  leagues (baseball, basketball, tennis, mma, esports — previously "target
  date missing from HTML" at D+5) still enter the shadow pipeline; the
  refresh evaluates only events the original run never admitted
  (--exclude-events over the frozen considered set) and appends its picks as
  `selections_delta_*` INSIDE the original run dir. The original
  `shadow_selections.json`, `manifest.json`, `settlement.json` and bundle
  stay byte-frozen (one-run-per-date intact — the refresh evaluator's own
  run dir is deleted after relocation), and every delta artifact carries its
  own SHA-256 marker, re-verified fail-closed before delta settlement.
  Same small-JSON size class as the files already covered. Raw refresh
  capture bodies (HTML/JSON) remain outside git; all other exclusions in
  the waiver above are unchanged.

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
- Before push: `python -m pytest -q`, `python3 -m py_compile scripts/*.py src/slumdog/*.py tests/*.py`, `python -m pyflakes scripts src/slumdog tests`, `git diff --check`, `git status --short`. If Arena lacks deps, run what is possible, give user exact Codespace commands. Never claim skipped test passed. **Why this suite run is the real gate (owner, 2026-09-14):** the Monday Census job runs `pytest` *before* its `depth-sweep`, and a failed suite causes the sweep to be skipped — so a red suite pushed to `main` means that week's board census is never collected, and the only recovery is a same-day manual `workflow_dispatch` (Depth Build #33 was exactly that recovery after #32). Code reaches `main` only through agent sessions; keep the suite green locally before every push. A separate PR-triggered verify workflow was deliberately rejected on 2026-09-14 — do not re-propose one (rationale in `docs/STATE.md` → Verification).
