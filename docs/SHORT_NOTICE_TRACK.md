# SHORT_NOTICE track — one R1 per sport, per day

**Status:** IMPLEMENTED AND TESTED LOCALLY / NOT YET DISPATCHED IN CI / NO REAL
SHORT-NOTICE RUN EXISTS. Training remains FROZEN, production NOT AUTHORIZED,
shortlist policy NOT AUTHORIZED.
**Owner decision:** 2026-09-26 — "separate, clearly-labelled short-notice
track" (option B of the coverage/lead-time question).
**Last verified:** 2026-09-26, local suite `1168 passed` (Python 3.11 venv;
CI is 3.13 — the usual divergence caveat stands).

---

## 1. The problem this exists to solve

Slumdog is supposed to shortlist an underdog per sport. It has not been able
to. Rank-1 (R1) coverage by date, read off the committed evidence:

| Dates | Sports producing an R1 |
| --- | --- |
| 2026-09-10 … 09-20 | 5–7 per day (baseball, basketball, hockey, rugby, handball, volleyball, cricket, american_football) |
| 2026-09-21 … 09-26 | **football only** (plus handball on 09-23 and 09-25) |

The cause is not the model, the rule or the ranking. It is capture timing
against the frozen gate:

- The frozen contract requires `captured_at` **and**
  `decision_committed_at` to be at or before `target_date 00:00 UTC − 24h`.
- Forebet does not publish basketball / hockey / baseball / tennis / rugby /
  american-football boards that far ahead. Both the D+6 forward capture and
  the T+1/T+2 daily refresh return
  `ValueError: target date missing from HTML` for them (see
  `data/reports/capture_refresh_2026-09-27_20260926T043304Z.json`: captured
  `afl, esoccer, football`; failures `basketball, tennis, hockey, baseball,
  american_football, rugby`).

So those sports are *structurally* unreachable under the date-anchored gate,
and no amount of re-running the existing pipeline changes that.

## 2. What the track does

It decides on the event day and proves pre-event status **per event** against
the published kickoff, instead of against a date anchor.

An event is admitted only if all five hold:

1. `captured_at <= decision_committed_at` (no capture from the future);
2. the sport's capture source **proves the kickoff timezone is UTC**
   (`UTC_KICKOFF_PROVEN_SPORTS` — football's JSON endpoint pins `tz=0`; HTML
   boards render in the relay's local timezone and are refused, see §6.1);
3. the listing carries a **parseable** scheduled start (Forebet `date_bah` /
   `DATE_BAH`);
4. that start falls on the target date; and
5. that start is at least `min_lead_minutes_before_kickoff` (**120**, frozen
   in the declaration) after the decision instant.

An event whose start time cannot be parsed is **refused**, never assumed to be
far away. Every refusal is counted by reason in the manifest
(`short_notice_timing_rejections`), so "why did basketball produce nothing
today" is answerable from the artifact alone:

```
CAPTURED_AT_UNPARSEABLE
CAPTURED_AFTER_DECISION
KICKOFF_TIMEZONE_NOT_PROVEN_UTC
KICKOFF_MISSING_OR_UNPARSEABLE
KICKOFF_NOT_ON_TARGET_DATE
INSUFFICIENT_LEAD_BEFORE_KICKOFF
```

Everything else is identical to the frozen track — the same
`R2_CONSERVATIVE_FIXED_RULE` eligibility, the same
`R1_ALWAYS_RANK_COMPARATOR` ranking, the same features, the same
one-primary-per-sport-day cohort policy, the same `UNDERDOG_WIN`-only target.
No thresholds were added, moved or tuned.

## 3. What keeps it separate from the 24h record

This is a **weaker-lead-time experiment**, not a relaxation of the frozen
contract, and the separation is enforced in six places:

| Surface | Standard | Short notice |
| --- | --- | --- |
| Declaration | `config/shadow_evaluator.json` | `config/shadow_evaluator_short_notice.json` |
| `declaration_version` | `shadow_evaluator` | `shadow_evaluator_short_notice` |
| Artifact tree | `data/reports/shadow/` | `data/reports/shadow_short_notice/` |
| Payload/manifest label | *(absent — schema unchanged)* | `track: "SHORT_NOTICE"` + `timing_contract` block with `satisfies_frozen_24h_contract: false` |
| Capture receipt | `capture_<date>.json` | `capture_short_notice_<date>_<stamp>.json` |
| Settlement | `settle_run(...)` | `settle_run(..., shadow_subdir="shadow_short_notice")` |

Fail-closed guards, all covered by tests:

- a short-notice declaration may **not** carry `safe_cutoff_offset_hours_utc`
  (it must not be able to claim the frozen contract);
- it must declare `track: "SHORT_NOTICE"`, a lead of 30–1440 minutes, and
  `require_parsed_kickoff` / `refuse_event_without_parsed_kickoff` /
  `never_pooled_with_standard_track` all `true`;
- its `artifact_path.root` must be the short-notice tree, and a *standard*
  declaration is refused if it points at that tree;
- `shadow_run_dir()` refuses any evidence tree outside the two known ones;
- the standard track's payload, manifest and input digest are byte-identical
  to before this change (regression-tested: no `track`, no `timing_contract`,
  no `kickoff_utc`, no new digest keys).

**The two hit rates are never summed.** Batch-receipt counters are namespaced
(`short_notice_*`) precisely so a future reader cannot accidentally pool them.

## 4. Operational flow

The stage runs inside the existing daily dispatch, before the forward pass
(today's picks are the time-critical ones), and is fully isolated — any
failure is recorded and the frozen pipeline continues:

```
D+1 settlement (frozen tree)
  → completion pass → delta settlement
  → daily refresh (T+1..T+2)
  → SHORT_NOTICE settlement (D+1, short-notice tree)
  → SHORT_NOTICE capture + evaluate (today)
  → forward capture D+2..D+6 (frozen tree)
```

Flags: `--skip-short-notice` disables both short-notice sub-stages;
`--skip-settlement` also suppresses its settlement pass.

Lead-time reality check at the current 04:00 UTC dispatch: with a 120-minute
declared lead, any fixture kicking off before ~06:00 UTC is refused. A second
dispatch later in the day (the owner controls dispatch timing; the workflow is
`workflow_dispatch`-only) would widen coverage further. **No workflow schedule
was changed by this work.**

## 5. Owner paste — workflow persist step

Workflow files are owner-hand-authored; this session did not modify
`.github/workflows/forward_shadow.yml`. Two evidence gaps need the paste
below:

1. **Pre-existing bug:** `selections_delta_*.json` /
   `settlement_delta_*.json` (the daily refresh, live since 2026-09-22) are
   written on the runner and **never committed** — the persist step's `find`
   list does not name them. Four days of refresh evidence has been discarded.
2. **New:** the `data/reports/shadow_short_notice/` tree is not covered by
   the existing `find data/reports/shadow ...` root.

Replace the four commands in the **"Persist small evidence to git"** step
(between the `git config` lines and the `if git diff --cached --quiet` line)
with exactly:

```yaml
          find data/reports/shadow -type f \( -name 'shadow_selections.json' -o -name 'manifest.json' -o -name 'settlement.json' -o -name 'settlement.json.sha256' -o -name 'settlement_supplement_*.json' -o -name 'settlement_supplement_*.sha256' -o -name 'selections_delta_*.json' -o -name 'selections_delta_*.sha256' -o -name 'settlement_delta_*.json' -o -name 'settlement_delta_*.sha256' -o -name 'forward_batch_receipt.json' -o -name '*.settlement.json' -o -name '*.settlement.json.sha256' -o -name '*.bundle.json' -o -name '*.tar.gz.sha256' -o -name 'status.tsv' \) | xargs -r git add -f
          find data/reports/shadow_short_notice -type f \( -name 'shadow_selections.json' -o -name 'manifest.json' -o -name 'settlement.json' -o -name 'settlement.json.sha256' -o -name 'settlement_supplement_*.json' -o -name 'settlement_supplement_*.sha256' -o -name 'selections_delta_*.json' -o -name 'selections_delta_*.sha256' -o -name 'settlement_delta_*.json' -o -name 'settlement_delta_*.sha256' -o -name '*.bundle.json' -o -name '*.tar.gz.sha256' \) 2>/dev/null | xargs -r git add -f
          find data/settlement_evidence -type f \( -name 'settlement_capture_receipt.json' -o -name 'settlement_capture_receipt_completion_*.json' \) 2>/dev/null | xargs -r git add -f
          git add -f data/reports/capture_*.json 2>/dev/null || true
```

Unchanged by that paste: the trigger (`workflow_dispatch` only), permissions,
`timeout-minutes: 350`, pinned action SHAs, the seed step, the upload step,
and the commit/rebase/push lines. Raw bodies (`*.txt`) and `*.tar.gz`
archives are still never added — the scoped waiver in `AGENTS.md` covers small
JSON/text evidence only. `capture_short_notice_*.json` needs no new rule: the
existing `git add -f data/reports/capture_*.json` already matches it.

Verify before and after pasting:

```bash
python scripts/check_workflow_evidence_globs.py     # exit 0 == nothing discarded
```

Today it exits 1 and lists 10 uncovered artifact types. After the paste it
exits 0. The suite asserts only that the gap is a **subset** of
`KNOWN_UNCOVERED_PENDING_OWNER_PASTE`, so it is green both before and after —
but it fails loudly if a new artifact type is ever added without coverage.

## 6. Red-team review (2026-09-26) — what was wrong and what is now enforced

The track was adversarially reviewed before any real dispatch. Three defects
were found; all three are fixed in code and pinned by tests.

### 6.1 BLOCKER — kickoff timezone is NOT UTC outside football

The per-event gate's whole claim is "this pick was frozen at least 120 minutes
before kickoff". That claim is only as good as the timezone of the kickoff we
read off the listing.

Measured against the live site on 2026-09-26:

| Source | Same board, same date (2026-09-26) | Kickoff shown for match `2468143` |
| --- | --- | --- |
| Football JSON `getrs.php?...&tz=0` (what we capture for football) | pinned UTC | `2026-09-26 02:00:00` |
| HTML 1X2 board `/en/football-predictions/predictions-1x2/2026-09-26` | client-local | `09/25/2026 9:00 PM` |
| Same HTML board with `?tz=0&tzs=&tze=` appended | **unchanged** | `09/25/2026 9:00 PM` |

Two conclusions:

1. HTML listing pages render kickoff in a timezone derived from the
   **requesting client** (our relay's egress IP), five hours off UTC in this
   sample, and
2. the `tz=0` parameter that pins the football JSON is **ignored** on HTML
   pages, so the timezone cannot be forced from the URL.

Every sport except football is captured from an HTML board. If the relay ever
renders east of UTC, an event looks *later* than it is, and a "120 minutes of
lead" proof could be sold for an event that has already kicked off — the exact
leakage the gate exists to prevent, arriving silently and in the direction that
flatters the record.

**Enforcement.** `shadow_evaluator.UTC_KICKOFF_PROVEN_SPORTS` lists the sports
whose captured kickoff is provably UTC. It currently contains `football` only.
Any record from any other sport is refused by the gate with the reason
`KICKOFF_TIMEZONE_NOT_PROVEN_UTC`, counted in the manifest like every other
rejection, and the daily stage does not even fetch those boards (a
guaranteed-rejected request is wasted politeness budget); the held-back sports
are listed in the stage receipt as `timezone_hold_sports`.

**Consequence, stated plainly:** until the hold is lifted, this track cannot
deliver the "R1 in every sport, every day" goal. It runs football-only.

**How to lift the hold honestly (not yet implemented).** Calibrate the offset
from evidence instead of assuming it: on the same pass, capture the football
JSON (true UTC) *and* the football HTML board through the same relay, join the
two on the match id in the row href, and take the modal difference. That is the
relay's rendering offset for HTML for that capture; record it in the manifest,
apply it to the other sports' HTML kickoffs, and refuse the whole track if the
join is too small or the offsets disagree. One extra request per day, and the
offset becomes an auditable artifact rather than an assumption.

### 6.2 Settlement evidence collision

`settle_run` wrote every D+1 settlement capture to
`data/settlement_evidence/<date>/settlement_capture_receipt.json`. Both tracks
can hold a run for the same date and the driver settles both on the same
morning, so the short-notice capture would have **overwritten the committed
evidence file that the frozen track's `settlement.json` points at**.

Fixed: `shadow_settle.settlement_receipt_name(shadow_subdir)` gives each
non-standard tree its own file
(`settlement_capture_receipt_shadow_short_notice.json`); the standard name is
untouched so no existing artifact's pointer goes stale. The per-track capture
is still labelled `capture_purpose: settlement` (it is a D+1 capture, not a
completion re-capture).

### 6.3 Request pacing

`capture_selected` fetched every sport of a date in one burst. The short-notice
stage would have added a third such burst per day. `capture_selected` now takes
`pause_seconds` and, when set, fetches serially with that gap — the same 62s
spacing the settlement capture uses. The stage passes the driver's
`--pause-seconds` through.

### 6.4 Checked and found already safe

- **Finished / in-play rows never enter the track.** `parse_football_json`
  drops rows with a score or `comment` in `{FT, LIVE}`; `parse_html_events`
  drops any row whose result cell contains a digit. Both are now pinned by
  tests rather than left as reading comprehension.
- **Lead is measured from the decision instant**, which is later than the
  capture instant — the conservative direction.
- **One decision per date** (`find_short_notice_run`) stops a second, better
  informed run from replacing the day's pick.
- **Tree selection is allowlisted** (`shadow_run_dir` rejects unknown subdirs,
  so `--shadow-subdir` cannot traverse).
- **Digest separation**: `timing_track` is part of the input digest, so a
  short-notice run can never collide with a frozen run's digest.
- **Stage isolation**: every failure mode returns a status dict; the stage
  cannot abort the 24h pipeline.

## 7. Honest status and limits

- **The track is football-only right now** because of the kickoff-timezone
  hold in §6.1. Every other sport is refused, by design, until the offset can
  be proven from evidence.
- **No real short-notice run exists yet.** Nothing here may be reported as a
  hit rate until the stage has dispatched and settled real dates.
- The track's picks have **hours**, not a day, of lead time. That is a weaker
  evidentiary claim and is labelled as such in every artifact.
- Kickoff comes from Forebet's own listing field. If Forebet publishes a wrong
  or later-revised start time, the lead guard inherits that error; the capture
  body is retained so the claim is auditable after the fact.
- Sports whose boards Forebet publishes only minutes before start will still
  produce nothing — correctly. `NO_STRONG_UNDERDOG` / no-pick remains a valid
  outcome on this track too; it never forces a pick to fill a sport.
- Bundling (`slumdog.shadow_bundle`) is **not** wired to this tree yet; the
  short-notice artifacts are committed JSON evidence only.
- The completion pass (append-only supplements for UNSETTLED rows) currently
  runs on the frozen tree only.

## 8. Where the code lives

| Concern | File |
| --- | --- |
| Track policy, kickoff parsing, per-event gate | `src/slumdog/shadow_evaluator.py` (`TrackPolicy`, `track_policy`, `parse_kickoff_utc`, `_timing_classify_short_notice`, `UTC_KICKOFF_PROVEN_SPORTS`) |
| Record-level kickoff | `src/slumdog/shadow_contracts.py` (`PreEventRecord.kickoff`) |
| Declaration | `config/shadow_evaluator_short_notice.json` |
| Evidence-tree selection for settlement | `src/slumdog/shadow_settle.py` (`shadow_run_dir`, `settlement_receipt_name`, `shadow_subdir=` on every entry point, CLI `--shadow-subdir`) |
| Daily stage | `scripts/forward_shadow_batch.py` (`run_short_notice_for_date`, `find_short_notice_run`, `summarise_short_notice_run`, `--skip-short-notice`) |
| Evidence-coverage checker | `scripts/check_workflow_evidence_globs.py` |
| Tests | `tests/test_short_notice_track.py` (74), `tests/test_short_notice_batch.py` (36) |
