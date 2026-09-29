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

**Correction (2026-09-28, third pass — the two before this were each wrong in opposite directions; this is the one to trust, full account in `HANDOFF.md`'s 2026-09-28 entries):** the mistake both earlier corrections made was promoting "the specific method I tried failed" into "this is unreachable, full stop." State it per method instead, because that is what was actually tested:

| Method | Target | Result |
|---|---|---|
| `curl` / `gh run download` / `gh run view --log` from the sandbox | `*.blob.core.windows.net`, `results-receiver.actions.githubusercontent.com` | **FAILS** — no general egress from this sandbox (`EOF` / exit 35); not allowlisted, credential or not |
| WEB FETCH tool | `api.github.com/repos/.../actions/runs\|jobs\|...` (JSON) | **WORKS** — public repo, no credential needed: run/job status, steps, conclusions, artifact *metadata* (not bytes) |
| WEB FETCH tool | `raw.githubusercontent.com/...` | **WORKS** — same reasoning, public repo |
| WEB FETCH tool | `.../actions/runs/<id>/logs` or `.../actions/artifacts/<id>/zip` (unsigned) | **FAILS** — these require an `Authorization` header; the tool sends none, hence the `401`/`403` this repo saw twice |
| WEB FETCH tool | a **pre-signed** blob URL (the one behind the run page's "Download log" button, or the one `gh`/`curl` obtain and then fail to fetch themselves) | **WORKS** — the `sig=`/`se=` query params in a signed URL ARE the credential, so no header is needed; confirmed against a real job log by the owner pasting one in chat. The window is short (`se=` is ~10 minutes from issue) — ask for a fresh paste, don't reuse an old one. |

So: full run logs and artifacts ARE readable in this sandbox, for the cost of one owner paste of a signed URL — they are not gated on annotations at all. What actually cannot be automated end-to-end from inside a session is *obtaining* that signed URL without the owner's authenticated browser (the run page's Download-log button, or GitHub's UI); every automated path (`gh`, `curl`, unsigned WEB FETCH) that could mint one itself is blocked by the sandbox's own egress, not by GitHub. Ask for the paste **before** declaring a run's findings unreachable.

**The general rule this leaves standing:** when a read method fails, report which method failed and under what condition, not a conclusion about every possible method. "I could not authenticate to the artifacts endpoint with tool X" is a finding. "Run findings are unreadable" is a claim about methods nobody tried yet.

**Sharper still (2026-09-28, owner-verified): the raw-log/artifact gate is specifically repo-ADMIN rights, not "any credential," and it is why run 36426785929/job 108942599581's log was never readable by this session at all — annotations were the only thing that could have carried its findings, and it emitted none.** Two anonymous, unauthenticated probes settled this:

| Method | Target | Result |
|---|---|---|
| Anonymous `GET` (no credential at all) | `.../actions/runs/<id>/logs` | **FAILS** — `403 "Must have admin rights to Repository"`, on a public repo. Repo visibility never mattered; only who can mint the request matters, and that requires repo-admin. |
| Anonymous `GET` (no credential at all) | `.../check-runs/<job_id>/annotations` | **WORKS** — returns JSON with no auth of any kind. |

So the earlier owner-pasted signed-blob-URL reads that worked in this session worked *because the owner has admin rights on this repo* and used their own authenticated browser session to mint that URL — not because the
signed URL itself sidesteps the admin check for anyone. Nothing in this sandbox (no tool, no token this session holds) can mint that URL itself, ever, regardless of the repo being public. **Consequence, now a standing
design rule for every script in this repo:** if a run's findings need to be readable by an agent (or anyone) without an owner pasting a fresh signed URL from their own browser, those findings must be emitted as
check-run annotations — incrementally, per phase, the moment each phase finishes, never batched at the end, because a killed/cancelled run must still leave partial findings behind. `probe_kickoff_timezone.py` already
does this (`emit_section`, below); `scripts/forward_shadow_batch.py` now does too (`emit_notice`, one per settlement/completion/refresh/event-day phase plus one per forward-pass target date, each carrying a
`summarize_capture_timing()` roll-up) — added specifically because it was the one script in this repo with none, which is the literal reason run #33 was a dead end.

**Also confirmed the same day: a pasted "successful" log is only as good as confirming which job it actually came from.** Two earlier pastes in this session's history were both quietly the `probe` job's log (running
`probe_kickoff_timezone.py`), never Forward Shadow's `forward-batch` job (running `forward_shadow_batch.py --root .`) — GitHub hands back whichever job's signed URL matches the page the owner is looking at, and it is
easy to grab the wrong one without noticing. Before treating a pasted log as answering a question about a specific workflow/job, check the job name printed at its top.


**The loop — correction (2026-09-28):** `.github/workflows/probe_kickoff_timezone.yml` (owner-authored, `contents: read`, `continue-on-error: true`) runs `scripts/probe_kickoff_timezone.py` on pushes that touch the script, but **only from the exact branches named in its `on.push.branches` list** — it does not fire for every branch that touches the script, contradicting the line this replaces. Verified 2026-09-28: the list only named a prior session's branch, so every push to `arena/01a0e863-slumdog` touching the script this session triggered nothing, silently.

**A new, separate, hard capability wall found the same day, while trying to fix that:** this session's GitHub identity cannot write to `.github/workflows/**` at all, and cannot dispatch ANY workflow, regardless of tool:

| Method | Target | Result |
|---|---|---|
| `git push` (a commit that edits `.github/workflows/probe_kickoff_timezone.yml`, alongside unrelated files) | GitHub's push-time workflow-file check | **FAILS**, the whole push — `refusing to allow a GitHub App to create or update workflow .github/workflows/probe_kickoff_timezone.yml without \`workflows\` permission`. Unrelated files in the same commit are blocked too; the fix is to split the workflow-file edit into its own commit and drop it before pushing. |
| `gh workflow run <name> --ref <branch>` | `POST .../actions/workflows/<id>/dispatches` | **FAILS** — `HTTP 403: Resource not accessible by integration`. `gh auth status` confirms `git` and `gh` share the same bot token (`arena-ai-coding-agent[bot]`), so this is not a tool choice — no dispatch path exists from this identity. |

Net effect: a workflow's trigger list and its `workflow_dispatch` inputs are edit-once, owner-only surfaces from this sandbox. An agent can write and test the *script* a workflow calls (ordinary `contents: write`, unaffected), but cannot add itself to a push trigger, add a new `workflow_dispatch` input, or fire one by hand — any of those needs the owner's own GitHub write access, either to apply a diff or to dispatch directly.

- **Never edit the workflow.** All probe logic goes in the script. Workflow files are owner-authored; a needed workflow change is a paste prepared under `docs/workflow_staging/` plus a contract test (see `tests/test_workflow_persist_contract.py`).
- **Once the owner applies a staged `docs/workflow_staging/*.yml` fix directly to `main`, pull it in with `git merge origin/main` and push the merge — do not leave the branch permanently stale relative to `main`'s workflow files.** Confirmed 2026-09-28: pushing a merge commit that brings in a `.github/workflows/*.yml` change *already committed by the owner on `main`* succeeds under this bot's restricted token, even though authoring a fresh diff to that same path directly is rejected (the `workflows` permission error above). The distinction GitHub's check is making is "who authored this content," not "does this push touch this path" — a merge just carries someone else's already-authorized commit forward.
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

## Multiplicity And Temporal Holdout Warning

A per-sport effect discovered on the same period used to inspect every sport is
a lead, not a gate. The standing counterexample is handball calibration from the
2026-09-29 offline backtest: development surplus **+8.96 percentage points**
with a 95% interval **[+1.82, +16.09]**, followed by holdout surplus **-30.20
points** with **[-56.38, -4.01]** — statistically separated from zero in opposite
directions. Had the development period been used to authorize a sport gate, the
system would have shipped a confident mistake.

Rules:

- Never mutate the frozen R1 rule from a full-period or development-only sport,
  band, facet, or outcome effect.
- Multiple sports/bands/facets inspected together require explicit multiplicity
  treatment; an isolated 95% interval is not sufficient.
- Preserve a genuinely later holdout, report its n, and label n<500 indicative.
- When many rows share a sport-day, row-wise Wilson intervals alone may
  overstate effective information; use sport-day-cluster-aware uncertainty for
  signal-wide analyses.
- A parallel selector remains a research track until its direction and useful
  firing frequency survive out-of-sample evaluation. Rare alone is never a
  "power play"; positive held-out calibration surplus with adequate evidence is.
- Tennis is the second worked warning: its earlier R1-level positive lead did
  not survive signal-wide calendar-day clustering in run `36550899550`
  (development and holdout intervals both included zero). Treat it as retired,
  not as a gate. Together with handball's sign reversal, this demonstrates why
  inspected sport leads need clustered temporal validation and multiplicity
  discipline before product use.
- Football is the third worked warning. Run `36550899550` gave an apparently
  decisive held-out underdog surplus of **+3.751 points**
  **[+2.979,+4.507]**, but the same-row favourite control was **+3.368**
  **[+2.505,+4.204]** and the underdog-minus-favourite differential was only
  **+0.383** **[-1.091,+1.787]**. Development differential was likewise null:
  **-0.386** **[-0.869,+0.100]**. Both win sides rose because draws were
  over-predicted; football has no demonstrated underdog edge.
- Pattern rule: on this corpus, assume every positive underdog surplus in a
  draw-capable sport is draw miscalibration until its same-row
  underdog-minus-favourite differential survives cluster-aware temporal
  evaluation. Never headline the underdog leg by itself.
- The negative-sport gate supplies the fourth and fifth worked warnings.
  First, excluding basketball, hockey and volleyball (two-way) plus handball
  (draw-capable) changed the outcome-space mix; its pooled holdout raw surplus
  rose to +7.83 points partly because the remainder became more draw-capable and
  therefore more exposed to draw over-prediction. Second, after correcting that
  mix, the gate failed on the development period that selected it: two-way
  interval `[-0.0152,+0.0328]`; draw-capable differential
  `[-0.0535,+0.0483]`, with point estimate moving from `+0.0290` frozen to
  `-0.0040` gated. The `n=184` holdout two-way result cannot rescue a gate that
  failed development. The gate is retired. Never compare pooled raw surplus
  across a changed outcome-space mixture; two-way merit is underdog surplus and
  draw-capable merit is the same-row underdog-minus-favourite differential.

## Retracted Pooled Low-Draw Shape — Sixth Worked Warning

The pooled `<0.05` shape passed its predeclared statistical criterion and was
then **retracted as mostly an encoding artifact** when the required per-sport
audit exposed that the criterion never asked whether a forecast existed.
Cricket development had `n=4995`, mean predicted `0.000146`, observed `0.005205`
and an artificial `35.6x` ratio; holdout was `80x` on `n=854`. Forebet was
functionally not modelling that draw outcome. MMA can settle fight draws but has
a two-outcome board, so it likewise cannot supply a genuine draw forecast.

This is the sixth worked warning and the most important: predeclaration prevents
post-hoc storytelling but cannot repair a misspecified population contract.
Before calibration, require that the sport's board publishes draw probability
and apply the predeclared `0.005` plausibility floor; report exclusions per
sport. The **unfiltered** pooled claim remains retracted.

The filtered population subsequently revalidated the shape, but composition
made it narrow: handball was `14473/14614` development rows (99.0%) and
`639/658` holdout rows (97.1%). Required wording is: **Forebet under-forecasts
low-probability handball draws** — development `2.19x`, `n=14473`, month
`[+0.0233,+0.0308]`; holdout `1.76x`, `n=639`, month
`[-0.0199,+0.0351]`, which includes zero. Never call this a general property of
Forebet's draw model. The evidentiary bar is higher because handball is also the
standing sport whose underdog effect reversed from `+8.96` points in development
to `-30.20` in holdout. That does not prove another reversal, but forbids
promoting the pooled development estimate without sequential persistence.

Handball persistence is predeclared before execution: fixed calendar quarters
2024-Q1 through 2026-Q3; at least 75% of nonempty folds positive, at least 50%
with month-block lower bound above zero, and the final two nonempty folds both
positive. League slices are multiplicity-exposed and `n<500` indicative.

Football remains only an indicative lead: development predicted `0.0348`,
observed `0.11`, relative `3.16x`, but `n=100` and 1.25 rows/active day; holdout
`n=13`. Under the standing evidence rule it needs at least `n=500` graded rows
(and enough independent calendar days for clustered uncertainty) before it can
advance beyond indicative. No selector exists; frozen R1 is unchanged.

## Documentation Governance

- `docs/STATE.md` is canonical current truth, not append-only diary. Git history is history.
- `HANDOFF.md` is session continuation record.
- `AGENTS.md` is permanent mission + operating constitution.
- Every substantive PR must update when applicable: `docs/STATE.md`, `HANDOFF.md`, `docs/README.md`, relevant audit doc.
- PR incomplete if code changes make durable docs stale.
- Before push: `python -m pytest -q`, `python3 -m py_compile scripts/*.py src/slumdog/*.py tests/*.py`, `python -m pyflakes scripts src/slumdog tests`, `git diff --check`, `git status --short`. If Arena lacks deps, run what is possible, give user exact Codespace commands. Never claim skipped test passed. **Why this suite run is the real gate (owner, 2026-09-14):** the Monday Census job runs `pytest` *before* its `depth-sweep`, and a failed suite causes the sweep to be skipped — so a red suite pushed to `main` means that week's board census is never collected, and the only recovery is a same-day manual `workflow_dispatch` (Depth Build #33 was exactly that recovery after #32). Code reaches `main` only through agent sessions; keep the suite green locally before every push. A separate PR-triggered verify workflow was deliberately rejected on 2026-09-14 — do not re-propose one (rationale in `docs/STATE.md` → Verification).
