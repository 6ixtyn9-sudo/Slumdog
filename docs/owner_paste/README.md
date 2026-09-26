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
