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

## `probe_kickoff_timezone.yml` — one-shot kickoff-timezone probe

**Why:** the EVENT_DAY track currently refuses every sport except football
because only the football capture URL pins `tz=0`; HTML boards render kickoff
in the requesting client's timezone and ignore `?tz=0` (see
`docs/EVENT_DAY_TRACK.md` §6.1). This job gathers the evidence that would lift
that hold. Running it on a GitHub runner is the point: that is the exact relay
and IP combination production captures from, so the answer is about the real
pipeline rather than some other machine's geolocation.

**Safety:** `permissions: contents: read`. It commits nothing, touches no
evidence tree, freezes no capture, and writes only a build artifact. Two HTTP
requests to the source plus one extra board, spaced by `--pause 20`.
`timeout-minutes: 15`. Action SHAs are the same pins `forward_shadow.yml`
already uses.

**How to run it, in the browser:**

1. Open `docs/owner_paste/probe_kickoff_timezone.yml` on branch
   `arena/01a0dd7a-slumdog` and copy the whole file.
2. Go to **Add file → Create new file** on that same branch, name it
   `.github/workflows/probe_kickoff_timezone.yml`, paste, and commit to the
   branch (not to `main`).
3. That commit triggers the run by itself — the workflow's `push` trigger is
   scoped to this branch and to these two paths. **Actions → Probe kickoff
   timezone** shows the log; the `kickoff-timezone-probe` artifact holds the
   full JSON report.

The last lines of the log are the verdict. Exit 0 means a definite answer
(either a machine-readable start instant exists in the raw HTML, or the relay's
offset was unanimous across at least 20 joined matches). Exit 1 means the probe
was inconclusive and the hold stands — an ambiguous probe is not permission.

**Delete it afterwards.** It is a diagnostic, not part of the pipeline.
