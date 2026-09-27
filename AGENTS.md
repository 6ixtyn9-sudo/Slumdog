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

## Remote Probing From a Sandbox With No Egress

The Arena sandbox has no outbound network (`curl` → `000`, exit 35). The GitHub runner does. Every external fact in this project since 2026-09-26 was obtained by shipping code to the runner and reading it back — no agent may claim a site fact it did not measure this way.

**The loop.** `.github/workflows/probe_kickoff_timezone.yml` (owner-authored, `contents: read`, `continue-on-error: true`) runs `scripts/probe_kickoff_timezone.py` and triggers on pushes that touch the script. Pushing the script alone re-runs the probe — there is nothing to dispatch by hand.

- **Never edit the workflow.** All probe logic goes in the script. Workflow files are owner-authored; a needed workflow change is a paste prepared under `docs/owner_paste/` plus a contract test (see `tests/test_workflow_persist_contract.py`).
- Read results back with `gh`, not by opening logs:
  ```bash
  gh api repos/6ixtyn9-sudo/Slumdog/actions/runs?per_page=1 --jq '.workflow_runs[]|"\(.id) \(.status)"'
  CR=$(gh api repos/6ixtyn9-sudo/Slumdog/actions/runs/<id>/jobs --jq '.jobs[0].check_run_url')
  gh api "${CR#https://api.github.com/}/annotations" --jq '.[] | "== \(.title)\n\(.message)"'
  ```
- **Emit one `::notice` annotation per report section**, each JSON blob capped ~2600 chars. A single combined blob truncates at ~3000 and the answer you want is usually the last thing written — this cost two wasted runs.
- **The job is capped at 15 minutes and the cap is silent.** An overrun is not a lost stage: the runner is killed before annotations are emitted and the run reports *nothing* (run 36294292356). The script arms `set_deadline()`, every network primitive calls `check_budget()`, and sleeps go through `pace()`. Put the stage that answers the current question FIRST.
- One run ≈ 10–20 relay requests. Budget the question accordingly; prefer one request that carries a whole row over six that must be joined.
- A run takes ~10–15 min wall clock. Poll with a bounded loop, then read annotations; do not re-push while a run is in flight.

**How to reason about results.**

- **One falsifiable hypothesis per run**, with the measurement that would disprove it. "Team names are absent from the DOM" survived days because nobody scoped a selector at the name element; it was false.
- **Record negative results in `docs/STATE.md`** with the exact URL/header/route tried. The archive, `all.js`, headed Chromium, sitemaps, Save Page Now, `X-Return-Format: html`, and `getjson.php` are all closed — do not retry them without new evidence.
- **The relay throttles, and throttled replies imitate data**: 422 for a selector that worked minutes earlier, a 591 B stub, an 18-row render of a 126-row board, and a bot-check page served as HTTP 200. Several recorded "dead ends" were throttled replies. Re-test under pacing before declaring anything dead, retry the transport with backoff, and never treat a short body as an empty board.
- **A mismatch between measurements is the finding.** Every board returning exactly one fewer `.fprc` than `.homeTeam` was not a parsing bug; it meant some rows have no probability cell, which makes index-joining unsafe. The fix was `:has()` scoping, not a smarter parser.
- Prefer a fix that removes a failure mode by construction over one that detects it afterwards.

**Capture route currently in use** (`src/slumdog/relay_columns.py`, not yet wired into the collector): one relay GET per field to `https://r.jina.ai/<board url>` with `X-Target-Selector: .rcnt:has(.fprc):has(.tnms) <field>`, headers `User-Agent`, `Accept: text/plain`, `X-No-Cache: true`, `X-Timeout: 25`. Columns zip by index. Fail closed on a missing required column, disagreeing row counts, a bot-check body, or a board under `minimum_rows`. Headings are detected by shape, never by matching heading text (only `.fprc`'s heading matched an allowlist, which silently mis-aligned all four boards).

**Discipline that applies to probe code too.** Every probe stage lands with tests that pin its refusals — throttle, partial render, interstitial — not just its happy path. Run the full suite and pyflakes before every push; a piped `pytest | tail` exits 0 even when red, so never chain `&& git push` off it. Expect "both added" conflicts on the two probe files during rebase (`git checkout --theirs`, then continue). The GitHub token can expire mid-session (`gh: Bad credentials`) — ask the user to reconnect; do not work around it.

## Documentation Governance

- `docs/STATE.md` is canonical current truth, not append-only diary. Git history is history.
- `HANDOFF.md` is session continuation record.
- `AGENTS.md` is permanent mission + operating constitution.
- Every substantive PR must update when applicable: `docs/STATE.md`, `HANDOFF.md`, `docs/README.md`, relevant audit doc.
- PR incomplete if code changes make durable docs stale.
- Before push: `python -m pytest -q`, `python3 -m py_compile scripts/*.py src/slumdog/*.py tests/*.py`, `python -m pyflakes scripts src/slumdog tests`, `git diff --check`, `git status --short`. If Arena lacks deps, run what is possible, give user exact Codespace commands. Never claim skipped test passed. **Why this suite run is the real gate (owner, 2026-09-14):** the Monday Census job runs `pytest` *before* its `depth-sweep`, and a failed suite causes the sweep to be skipped — so a red suite pushed to `main` means that week's board census is never collected, and the only recovery is a same-day manual `workflow_dispatch` (Depth Build #33 was exactly that recovery after #32). Code reaches `main` only through agent sessions; keep the suite green locally before every push. A separate PR-triggered verify workflow was deliberately rejected on 2026-09-14 — do not re-propose one (rationale in `docs/STATE.md` → Verification).
