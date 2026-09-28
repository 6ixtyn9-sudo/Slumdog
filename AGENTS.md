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

The Arena sandbox has no *general* outbound network (`curl https://example.com` / `https://raw.githubusercontent.com/...` / `https://www.forebet.com/...` → `000`, exit 35, verified 2026-09-28). The GitHub runner does. Every external (Forebet) fact in this project since 2026-09-26 was obtained by shipping code to the runner and reading it back — no agent may claim a site *scraping* fact it did not measure this way.

**Correction (2026-09-28, verified twice — the first correction attempt below was itself wrong; do not repeat either mistake, full account in `HANDOFF.md`'s 2026-09-28 entry):** `api.github.com` *metadata* endpoints are NOT behind the general-egress block — `curl https://api.github.com` returns `200`, and `gh api repos/.../actions/runs`, `gh api repos/.../actions/jobs/<id>`, and the check-runs `/annotations` endpoint all work directly from this sandbox, authenticated or not (the WEB FETCH tool gets the same public JSON with no credential at all). That covers run/job status, step start/end timestamps, and `::notice` annotations — enough to time phases and watch a run to completion. It does **not** cover the run's substantive output: an earlier note (HANDOFF.md, corrected) claimed blob storage is simply unreachable, which is closer to true than the first correction gave it credit for. Tested end to end on run 36426785929 (job 108942599581): `gh run download`, `gh run view --job <id> --log`, and a direct `curl` against the exact signed SAS URL `gh` obtained all fail with connection-level errors (`EOF` / exit 35) against `*.blob.core.windows.net` and `results-receiver.actions.githubusercontent.com` — those are different domains from `api.github.com` and are not allowlisted, job completed or not. The WEB FETCH tool fares no better: it returns `403 Must have admin rights to Repository` on the jobs `/logs` endpoint and `401 Requires authentication` on the artifacts `/zip` endpoint, because it carries no GitHub credential and GitHub gates both behind auth unconditionally. **Net effect: `::notice` annotations are the only channel this sandbox can read a run's actual findings through, full stop — not just mid-run.** A script that needs its results read back in-session (like the kickoff-timezone probe) must emit them as annotations; a script that only needs to persist evidence (like the forward-shadow batch) can rely on its git-committed output instead, but that output only exists if the persist step actually ran (see the persist-step cancellation gap in `HANDOFF.md`).

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

## The Column Route As A Production Path

Every non-football board answers a bot-check page to anything CI can send,
so `ForebetCollector._fetch` falls through to `relay_columns.capture_board`
when — and only when — `validate_capture_body` rejects the HTML body. Rules
that must not be relaxed:

- **Fallback, never default.** A board that validates as HTML is never
  re-fetched column-wise. The column route is strictly the weaker evidence
  path and is taken only when the stronger one is unavailable.
- **The frozen bytes are the extracts, not the events.** A capture is
  written as `columns_v1` JSON holding the per-column text exactly as the
  renderer returned it, so `parse_capture` can rebuild the same events from
  what was stored. `body_format="columns_v1"` and `route="relay_columns"`
  mark it; a column body is never mistaken for an HTML one, in either
  direction.
- **A gap is a failure.** `capture_board` returning anything but `CAPTURED`
  raises out of `_fetch`. Nothing thinner than a full board is ever filed.
- **Settlement uses the same machinery.** `SETTLEMENT_COLUMN_SELECTORS`
  adds `.lscr_td` (score) and `.scoreLnk` (status); `settled_rows` grades a
  row only when the status is one of `FT`, `AOT`, `AP`, `FINAL` and the
  row's own rendered day is the day being settled. A live or postponed row
  is skipped, never graded on whatever numbers are showing.

## Identity Across Routes

A column capture emits `"<sport>:<id>"`, the same identity
`parsers.parse_football_json` and `settlement._base_row` use. A bare id
looks correct in isolation and never joins to its own settlement row, so
the capture and the grade would silently describe different matches.

## Fixtures Longer Than A Day

Cricket's kickoff column came back 8 rows long on a 13-row board every
run, and a short required column discards the board. The five missing rows
were multi-day matches: a Test renders a RANGE (`.dtrange`,
`08/05 - 11/05/2026`) where a one-day fixture renders a start
(`.date_bah`). `SPORT_COLUMN_OVERRIDES` asks for both, `scoped()` scopes
every part of a selector list (scoping only the first would let the rest
match the whole document), and a range resolves to the day it **begins** —
a pick frozen 24h before the last day of a Test would be frozen three days
after the match started.

## The Production Path, Proven Live

Run 36423084168, volleyball 2026-09-29, through `ForebetCollector` — not
through a probe-shaped copy of it:

```
route=relay_columns  body_format=columns_v1  bytes=3533  body_on_disk=true
captures_verified=1  parser_emitted_snapshots=12  snapshots_unique_accepted=12
parsed_events=12   top: volleyball:109598 Uzbekistan vs Kazakhstan p2=0.69
verdict: capture -> disk -> parse produced events
```

The run before it is the reason this stage exists. It captured the same
board correctly, wrote 3,032 good bytes with the right route and format
— and parsed them as HTML to nothing, because
`looks_like_columns_body` searched the first 400 bytes for the format
marker and a real ten-row board puts it at byte 1406 (the JSON was
key-sorted; `columns` sorts before `format`). **A capture that is
written and unreadable is worse than one that fails: it looks like a
quiet day.** The detector now parses the body and reads the field, the
marker leads the document for a human, and the loader passes
`body_format` and `route` through from the receipt it already had.

## Settling Is Not Ranking

Proven live, run 36419041728: 22 volleyball rows for 2026-09-27, all
`FT`, **18 graded** in 22 seconds — `volleyball:109541 Iran vs Kyrgyzstan
3-0`. The four ungraded rows belong to the neighbouring day and are
skipped by the row's own-day check, which is the rule working.

Three things had to be separated from the ranking route before that
worked, and each had been misread as throttling:

- **Scope.** `ROW_SCOPE` demands `.fprc`, because a row without a
  prediction cannot become a pick. A finished match is not being picked.
  `SETTLEMENT_ROW_SCOPE` (`.rcnt:has(.lscr_td)`) asks for a row with a
  score. With the wrong scope the board returned 42,670 bytes of real
  content and every column returned 422.
- **Columns.** Settlement reads five: link, home, away, score, status. It
  was fetching eight. A column is a request.
- **Verdict.** A settlement capture has no probabilities and therefore
  can never produce events, so "no events" cannot mean "no fixtures on
  this date". Those captures are judged by how many rows carry the target
  date.

## Market Shape Is Not Outcome Space

`SportSpec.draw_possible` says how many outcomes the board **prices**.
`SportSpec.draw_settles` (backed by `draw_outcome_possible`) says how many
outcomes can actually **happen**. MMA prices two and produces three: a fight
can end in a unanimous, majority or split draw, and separately in a no
contest.

- A draw in a sport that can end level grades as label 0 — a failed
  underdog win, per invariant 3 — rather than being excluded. Excluding it
  would quietly delete the cases where the pick did not come off.
- A no contest is `VOID`: there is no result to grade.
- A draw is `SETTLED_DRAW`, not `VOID`. The distinction matters because
  void rows leave the record and settled draws stay in it as failures.
- Method of victory (KO, TKO, submission, decision, disqualification) is
  recorded in `SettledEvent.facets["method"]` as evidence only. It is never
  a model feature and never gates a candidate.
- A sport that cannot end level still excludes an unexpected draw
  (`UNEXPECTED_DRAW_FOR_TWO_WAY`): there, a level result means the parse is
  wrong, not that the match tied.

## A Stage Reports As It Finishes

Run 36386778571 was cancelled at the workflow's 15-minute wall and reported
nothing whatsoever, because every annotation was emitted after the final
stage. Two rules now hold:

- `emit_section(key, value)` publishes a stage's result the moment the
  stage returns; `emit_annotations` skips whatever is already published.
  An overrun costs only the stages that had not run.
- Every network call inside a capture takes a `before_request` callback,
  and the probe passes `check_budget`. Without it the column route answered
  to nobody: eight columns times three attempts times a minute-long timeout
  outlives any job, whatever the probe's own deadline says. A stage that
  runs out catches `BudgetExhausted` and records `stopped: ...` rather than
  taking the run down with it.

## Recovering The Renderer's Clock

`UTC_KICKOFF_PROVEN_SPORTS` held football alone because football is
captured from `getrs.php?...&tz=0`, which pins the timezone, while every
other sport comes from a rendered listing whose times follow the relay's
egress IP — measured at five hours out on 2026-09-26 (match 2468143
rendered `09/25/2026 9:00 PM` against a `2026-09-26 02:00:00` JSON
instant). That hold, not capture ability, is why thirteen sports produce
nothing.

`slumdog/render_clock.py` performs the per-capture calibration that
constant's note asks for. Football is the one sport visible through **both**
channels in the same run, and the same renderer serves every other sport
from the same egress, so the offset measured on football is a measurement
of the renderer — not an assumption about hockey.

**Measured live, run 36404825813 (2026-09-28):** offset **-120 minutes**
(UTC-2), 40 joined matches, 10 distinct rendered hours, no scatter. The
red-team finding two days earlier measured **-300** (UTC-5) on the same
endpoint. The renderer's clock moved three hours between runs, which is
the whole argument for never carrying a calibration forward: a cached
offset would have placed every kickoff three hours from where it is, and
a pick would have been admitted as "pre-event" against a match already
under way.

It refuses unless all of the following hold, because a wrong offset does
not look wrong: it yields a kickoff that parses, sorts and prints
perfectly while admitting a match that has already started.

- at least `MIN_CALIBRATION_SAMPLES` (20) matches joined **by the site's own
  match id**, never by name;
- every joined match showing the *same* offset — scatter means a per-league
  timezone or a DST boundary, and no single number is right for all of them;
- at least `MIN_DISTINCT_RENDERED_HOURS` (3) distinct rendered hours, so an
  offset cannot be confused with a coincidence;
- an offset inside [UTC-12, UTC+14];
- the calibration belonging to **this** date and run. The egress IP can
  change between runs, so a calibration is never carried forward.

Every refusal path and `load_render_clock` failure returns `None`, which is
byte-identical to having no calibration at all: football only. A broken
calibration must never be more permissive than no calibration.

**A converted kickoff owes a margin.** The offset is measured on
football's board and applied to every other sport's. One renderer, one
egress, one clock is a sound inference and is not a measurement, and the
error it would make is one-directional: a match looks LATER than it is,
and a pick is admitted against a match already under way.
`CONVERTED_KICKOFF_MARGIN_MINUTES` (90) is demanded on top of the declared
lead for any kickoff that was converted rather than read, and rejections
land in their own bucket
(`INSUFFICIENT_LEAD_FOR_CONVERTED_KICKOFF`). The margin covers a clock
that shifts mid-run; a clock that is simply unknown is refused outright,
not margined.

When a clock is present the event-day track converts a rendered kickoff and
records the whole basis in the payload — `timing_contract.render_clock` and
`kickoff_timezone_basis` — and commits `render_clock_offset_minutes` to the
input digest, because a run that converted kickoffs made a different timing
claim from one that refused them.

### How the event-day stage uses it

`calibrate_event_day_clock` in `scripts/forward_shadow_batch.py` runs before
the capture and decides what the capture may fetch:

1. capture football (tz=0 JSON) and read its instants;
2. fetch **one** column — `.tnms`, the cell carrying the link and the
   rendered time — for the same board. One request, because the measurement
   cannot use the rest, and the source throttles;
3. measure; write `data/reports/render_clock_<date>_<stamp>.json` only if it
   proves out;
4. proven → capture every sport and pass `--render-clock` to the evaluator;
   refused → capture football alone, exactly as before.

If there are no instants the rendered column is never requested: a request
whose answer cannot be used is not worth making. The stage records the
outcome in `entry["render_clock"]`, so "why is basketball missing today" is
answerable from the receipt.

## Documentation Governance

- `docs/STATE.md` is canonical current truth, not append-only diary. Git history is history.
- `HANDOFF.md` is session continuation record.
- `AGENTS.md` is permanent mission + operating constitution.
- Every substantive PR must update when applicable: `docs/STATE.md`, `HANDOFF.md`, `docs/README.md`, relevant audit doc.
- PR incomplete if code changes make durable docs stale.
- Before push: `python -m pytest -q`, `python3 -m py_compile scripts/*.py src/slumdog/*.py tests/*.py`, `python -m pyflakes scripts src/slumdog tests`, `git diff --check`, `git status --short`. If Arena lacks deps, run what is possible, give user exact Codespace commands. Never claim skipped test passed. **Why this suite run is the real gate (owner, 2026-09-14):** the Monday Census job runs `pytest` *before* its `depth-sweep`, and a failed suite causes the sweep to be skipped — so a red suite pushed to `main` means that week's board census is never collected, and the only recovery is a same-day manual `workflow_dispatch` (Depth Build #33 was exactly that recovery after #32). Code reaches `main` only through agent sessions; keep the suite green locally before every push. A separate PR-triggered verify workflow was deliberately rejected on 2026-09-14 — do not re-propose one (rationale in `docs/STATE.md` → Verification).
