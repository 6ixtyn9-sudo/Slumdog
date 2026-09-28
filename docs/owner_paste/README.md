# Owner paste — files an agent token cannot push

GitHub App tokens are refused when a push adds or edits anything under
`.github/workflows/`:

```
! [remote rejected] arena/01a0dd7a-slumdog -> arena/01a0dd7a-slumdog
  (refusing to allow a GitHub App to create or update workflow
   `.github/workflows/probe_kickoff_timezone.yml` without `workflows` permission)
```

So workflow files that need to run are staged here as ordinary files and the
owner copies them across in the GitHub web UI. No terminal, no Codespace.

## `forward_shadow.yml` — APPLIED 2026-09-27, file removed

The persist step never named `selections_delta_*` or `settlement_delta_*`, so
the daily refresh was written on every dispatch from 2026-09-22 and discarded
at job end, and the `shadow_event_day/` tree had no rule at all. The owner
pasted the replacement; `main` now carries it byte-identical to what was
staged here, and `python scripts/check_workflow_evidence_globs.py` reports
24/24 declared artifacts covered, exit 0.

The staged copy is deleted rather than kept as a record — git history is the
record. `tests/test_workflow_persist_contract.py` now guards the live file
directly, so the gap cannot silently reopen.

## `forward_shadow.yml` — pending, staged 2026-09-28: persist step must survive cancellation

**Proven, not inferred:** Forward Shadow #33 (run 36426785929) was dispatched
2026-09-28T13:10:59Z and cancelled by the owner at 15:07:37Z after its one
real step ran 1h56m without finishing (the run this session was told to read
end-to-end — see `HANDOFF.md`). GitHub's own record for that job
(`gh api repos/6ixtyn9-sudo/Slumdog/actions/jobs/108942599581`) shows:

```
step 6 "Settle overdue predictions ... forward batch ..." conclusion=cancelled
step 7 "Persist small evidence to git (permanent ledger)"  conclusion=skipped
step 8 "Upload full evidence as artifacts (30d retention)" conclusion=success
```

Step 8 carries `if: always()`; step 7 carries no `if:` at all, so it defaults
to `if: success()` and was skipped. The D+1 settlement pass and the
completion pass both run — and finish, and write their small-evidence files
to disk — *before* the forward capture pass that is the part actually
overrunning (Priority 1 in `HANDOFF.md`). Cancelling or timing out therefore
discards graded settlements and receipts that had already finished, not just
whatever the forward pass had in flight. The only copy of that evidence is
now the 30-day artifact `forward-shadow-36426785929`, which nothing in this
sandbox — `gh`, `curl`, nor the web-fetch tool — can currently read (see
`HANDOFF.md`, "network reachability, corrected again").

**The fix is one line:** `if: always()` on the persist step, nothing else.
Small-evidence writes are already isolated per date and already the narrow,
vetted glob list from the 2026-09-27 cycle above — running that step
unconditionally does not widen what gets committed, only whether it runs
when an earlier step failed or the job was cancelled/timed out. Staged at
`docs/owner_paste/forward_shadow.yml`; `tests/test_workflow_persist_contract.py`
(`TestTheStagedFixIsNarrowAndCorrect`) pins that the staged copy differs from
the live file by exactly that one added line — nothing else — so pasting it
cannot smuggle in a wider change.

**To apply:** open `docs/owner_paste/forward_shadow.yml` on this branch,
copy the whole file, paste it over `.github/workflows/forward_shadow.yml` on
`main` in the GitHub web UI, commit. Then delete the staged copy (git history
is the record, same as the 2026-09-27 paste) and move
`TestTheStagedFixIsNarrowAndCorrect`'s two checks onto `live_text` in
`TestEveryDeclaredArtifactIsPersisted` / `TestThePersistStepStaysNarrow`.

## `probe_kickoff_timezone.yml` — APPLIED 2026-09-28, file removed

**Why it exists:** the EVENT_DAY track currently refuses every sport except
football because only the football capture URL pins `tz=0`; HTML boards
render kickoff in the requesting client's timezone and ignore `?tz=0` (see
`docs/EVENT_DAY_TRACK.md` §6.1). This job gathers the evidence that would
lift that hold, and also carries the Priority 1 circuit-breaker measurement
(item iii) as a normal stage inside `run_probe()` itself — no separate CLI
flag or `workflow_dispatch` input was ever needed for that part.

**What was staged and applied:** only the `push` trigger's branch list. It
used to name one specific past session's branch (`arena/01a0dd7a-slumdog`)
and nothing else, so a push from any other branch silently triggered
nothing — the workflow only ever ran via manual `workflow_dispatch`. The
owner applied the fix directly to `main` (commit `1348ded`:
`branches: [main, 'arena/**']`, byte-similar to what was staged here). This
branch picked it up via `git merge origin/main` rather than an authored
diff — merging in an already owner-committed workflow-file change is
accepted by the restricted push token even though authoring a fresh diff to
that path directly is refused; use this pattern for any future
`docs/owner_paste/*.yml` fix once the owner applies it to `main`.

Consequence: any push touching `scripts/probe_kickoff_timezone.py` or the
workflow file itself, from `main` or any `arena/**` session branch, now
triggers the probe automatically — dispatch is no longer required for
future probe measurements, just a push.

The staged copy is deleted rather than kept as a record — git history is
the record, same as the 2026-09-27 `forward_shadow.yml` cycle above.
`tests/test_probe_workflow_persist_contract.py` now guards the live file's
branch list directly, so the gap cannot silently reopen.

**Running the probe:** Actions → Probe kickoff timezone → Run workflow →
pick the branch → Run workflow (still works, independent of the push
trigger) — or simply push a change touching the probe script. The last
lines of the log are the verdict; the `kickoff-timezone-probe` artifact
holds the full JSON report, including `circuit_breaker_comparison`. Note
(2026-09-28, owner-verified): reading that artifact or the raw job log
requires **repo-admin** credentials even on a public repo (anonymous
`GET /actions/runs/<id>/logs` → 403 "Must have admin rights to
Repository"); the probe's own `emit_section` **check-run annotations** are
anonymous-readable with no credential at all
(`/check-runs/<job_id>/annotations`) and are the reliable way to read a run
without an owner paste.

**Delete the live workflow file entirely once the timezone hold and the
Priority 1 breaker measurement are both settled for good.** It is a
diagnostic, not part of the pipeline.

## `probe_canary_cron.yml` — staged 2026-09-28, UN-PAUSED, dual-path: continuous availability sampling

**Un-paused (2026-09-28, same day it was first paused).** It was held
because a server-side fetch got a REAL response direct from `forebet.com`
at the exact moment a relay (`r.jina.ai`) fetch of the identical URL
returned a challenge page, and this job's sample — like
`sample_canary`/`_canary_state` on a GitHub runner at the time — went via
the relay only. The runner-side follow-up (`--direct-vs-relay-only`, run
`36470920157`) then showed the pause's own premise was incomplete: direct
FAILED OUTRIGHT from a GitHub runner (no response at all), relay at least
got a real, challenged, response — neither path alone was the "true"
availability signal. `sample_canary`/`_canary_state` were made **dual-path**
instead (relay first, direct fallback attempted once only if relay fails,
the fact of which path served recorded rather than assumed), and this
staged cron inherits that behaviour for free via `--canary-only`. A red
cron run now means both paths were blocked this sample; a green run
records which one served. See also `_CANARY_PATH_BLOCKED_PREFIX` /
`_mark_canary_path_blocked` in `src/slumdog/forebet.py` for the matching
relabeling in the production canary code path, and `HANDOFF.md`'s
"MEASURED ROUTING REPLACES HARDCODED ROUTING" entry for the full change.

**Why it exists:** two consecutive site-wide WAF blocks this session
(`36455080098`, `36461512749`), the same afternoon that had served cleanly
that morning (run `36419041728` graded 18 rows; run a5e5720 captured 13
events) — same code, same relay, same runner provider. The owner's
reframe: "we've been optimising how much we ask, when the binding
constraint may be when we ask." You cannot catch a healthy window by luck
with a handful of 13-minute probe runs; you need continuous, cheap
sampling. `scripts/probe_kickoff_timezone.py --canary-only` (added this
session, made dual-path this pass) answers in one or two requests —
football's tz=0 JSON via the relay, then direct only if the relay leg
failed, classified, one annotation, exit — instead of sharing a budget
scheduler with the full multi-stage sweep.

**What is staged:** a brand-new, separate workflow file
(`docs/owner_paste/probe_canary_cron.yml`), not an edit to
`probe_kickoff_timezone.yml`. Running the full sweep on a cron would cost
13 minutes per sample and defeat the point; this job costs one HTTP
request. It runs on `schedule: cron: '17 */2 * * *'` (every two hours) plus
`workflow_dispatch` for manual testing, `permissions: contents: read`, a
3-minute timeout, and fails the job (`exit 1`) on an unhealthy sample —
deliberately, so Actions' own green/red run history becomes a readable
availability map with no owner paste and no annotation fetch required
(the `probe:canary` annotation still carries the machine-readable reason
for anyone who wants it).

**Why this one cannot simply be pushed like the probe script itself:**
`schedule` triggers only fire from the repository's **default branch**
(`main`) — pasted onto any session branch, it would parse correctly and
simply never run. It must go through the same owner-paste-onto-`main` path
as `probe_kickoff_timezone.yml`'s trigger fix, not a session-branch push.

**To apply:** open `docs/owner_paste/probe_canary_cron.yml` on this
branch, create a new file at the same path under `.github/workflows/` on
`main` in the GitHub web UI, paste the contents, commit. No merge needed
into this branch beyond deleting the staged copy afterward (same as the
`probe_kickoff_timezone.yml` cycle above) —
`tests/test_probe_canary_cron_contract.py` guards the staged file's safety
properties now and should migrate onto the live file the same way
`test_probe_workflow_persist_contract.py` did once applied.

**This is optional, not mandatory** — the owner asked whether a recurring
job was wanted at all before it was added; staging it here does not paste
it anywhere. If the owner would rather not run a cron, this file can stay
staged indefinitely or be deleted, and the same `--canary-only` mode is
still available for opportunistic manual sampling (slower to build a full
map, but zero standing footprint).

**Delete this staged file (and, once applied, the live workflow) once the
availability map answers the scheduling question well enough to pick a
serving window for event-day capture, or once Forebet's block clears for
good.** It is a measurement job, not part of the pipeline.
