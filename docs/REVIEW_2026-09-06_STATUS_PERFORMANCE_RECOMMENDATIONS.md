# Slumdog Status, Performance & Recommendations Review — 2026-09-06

> **Provenance (added 2026-09-06 by the collaborating Arena session, branch
> `arena/01a07741-slumdog`):** this document was authored by the prior Arena
> session — whose GitHub write access closed with PR #16's merge — and was
> committed to the repo at that session's explicit request. The content below
> is reproduced verbatim, including its own header note about the authoring
> session's limits (historical context). The documentation staleness flagged
> in §7 is addressed by the STATE.md / HANDOFF.md refresh accompanying the
> commit that added this file.

> **Status: REVIEW / REFERENCE ONLY — not a milestone doc, not canonical.**
> Canonical current truth remains `docs/STATE.md`; permanent mission/invariants
> remain `AGENTS.md`. This document is a point-in-time analysis produced at
> the user's request ("the word" to proceed with the broader review), written
> read-only against the real repository state on `main`. It makes no code
> changes and authorizes nothing. Training remains FROZEN.
>
> **A note on this session's limits:** this Arena session's GitHub write
> access (git push, `gh`, authenticated API) is permanently closed as of the
> PR #16 merge. This file exists locally only; it has not been and cannot be
> pushed by this agent. If it should live in the repo, copy it in from your
> own Codespace/agent and commit it there.

## 1. Where things actually stand

**Infrastructure is solid; the prediction system itself is still early.**

- Capture, immutable receipts, timing-safety gate (24h pre-event cutoff),
  price-free dataset/eligibility rules, shadow evaluator (R1/R2 baseline
  rules), shadow bundle packaging + verification, and now **automated D+1
  settlement** are all implemented, tested, and — as of this session —
  **proven against real production data, twice**, including a real root-cause
  fix for a blocking bug (see §2).
- Only **1 of 12 registered sports (football)** has a feature-extraction
  module wired into the live decision path in any depth. The other 11
  sports (basketball, tennis, hockey, baseball, american_football, rugby,
  handball, volleyball, cricket, mma, esoccer) are captured and have
  per-sport feature extractors written in code (`src/slumdog/basketball.py`,
  `tennis.py`, etc., all wired via `facets.py`), but the *shadow evaluator's*
  actual decision features (see §3) are the same 17-field, sport-agnostic
  vector for every sport — none of the richer per-sport feature modules
  currently feed the live R1/R2 shadow decision.
- Model training is still frozen (per `AGENTS.md` invariant 15) — no dataset,
  target, timing, or validation contract has been approved. Nothing in this
  review changes that.

## 2. Root-cause fix, closed this session

`SHADOW_RUN_BLOCKED` on the forward-capture dates 2026-09-10/11/12 was
traced to `scripts/forward_shadow_batch.py::run_evaluator()` passing
backfill *manifest* files (`history_<sport>.json`) to `--history`, which the
evaluator's loader rejects (it only accepts `history_*.jsonl.gz` ledgers and
`settled_history.json`). Fixed, tested (5 new regression tests), delivered,
applied by the user via `git am`, pushed to `main` as commit `972b79a`, and
**confirmed resolved in production**: a re-dispatch of `forward_shadow.yml`
completed successfully and 2026-09-10/11/12 now have real run + bundle
artifacts on `main`. This item is closed.

## 3. The feature-usage gap (the central finding of this review)

Football data is collected across **three separate, non-overlapping
vocabularies**, but the live shadow decision only uses a small, generic
slice of it.

**What's collected for football:**

| Layer | Source | Fields |
|---|---|---|
| `sports.py` `known_facets` | listing page facet catalogue | standings, form, home_form, away_form, h2h, streaks, weather, predicted_score, average_goals, btts, totals |
| `forebet.py` `FOOTBALL_MARKET_KEYS` | 5 cheap per-day JSON market endpoints | `uo` (over/under), `bts` (both-teams-score), `ht` (half-time), `ah` (Asian handicap), `cards` |
| `football.py` `FOOTBALL_MARKET_KEYS` (detail surface) | match-detail page | the above plus `corners`, `doublechance`, `goalscorer` — deliberately excluded from bulk capture because the bulk JSON echoes the 1X2 payload; real numbers only exist on the detail page |

**What actually reaches the live shadow decision** (`build_pre_event_features`
in `src/slumdog/dataset.py`, used by both the research builder and
`shadow_evaluator.py`) is a fixed **17-field, sport-agnostic** vector:

```
forebet_favorite_probability, forebet_underdog_probability,
forebet_probability_gap, forebet_draw_probability,
forebet_draw_probability_missing,
underdog_prior_games, favorite_prior_games,
underdog_prior_win_rate, favorite_prior_win_rate, recent_win_rate_gap,
h2h_prior_games, h2h_underdog_win_rate, h2h_draw_rate,
underdog_prior_draw_rate, favorite_prior_draw_rate,
prior_scoring_rate_gap, prior_conceding_rate_gap
```

None of `uo`/`bts`/`ht`/`ah`/`cards`, and none of the listing-page
`known_facets` (standings, weather, btts, totals, streaks, predicted_score,
average_goals) feed this vector, and this vector is what the shadow
evaluator's R1/R2 rules actually rank on.

**Important nuance:** a *separate*, much richer football feature module
already exists and is tested — `src/slumdog/football.py::extract_football_features`
(60+ fields: standings gaps, PPG, H2H undefeated rate, over/under, BTTS,
half-time splits, Asian handicap line, card/corner intensity, weather,
travel distance, shots/tackles/dangerous-attacks averages). It is wired
into `facets.py::build_numeric_features`, which is used by
`pipeline.py` and `training.py` — but **not** by `dataset.py`'s
`build_pre_event_features`, which is the function the frozen shadow
evaluator and the research-dataset builder both call. In other words: the
richer feature-extraction code already exists in the repo, has tests, and is
simply not on the path the live/shadow decision uses. This is very likely
by design (the 17-field vector is what's frozen under
`config/shadow_evaluator_v1.json`'s `R2_CONSERVATIVE_FIXED_RULE`, and
`AGENTS.md`'s anti-tuning rule forbids amending the frozen rule based on
observed results) — but it means today's shadow picks are not using most of
what's already sitting in the captured data, and that richer vector is the
natural on-ramp for the eventual (still-frozen) training work, not a new
build.

## 4. Forebet data-completeness (what's collected vs. what's collectible)

This review did not re-crawl live Forebet pages (no new network probes were
made — see `AGENTS.md`'s probe-discipline rule); it compared what
`docs/FOREBET_DEPTH_AUDIT.md`, `docs/FOREBET_DETAIL_COVERAGE.json`, and
`docs/FOREBET_PRICE_COVERAGE.json` (frozen 2026-08-21/24 receipts) already
document against the intentional scope decisions baked into the collector:

- Football's listing/JSON coverage is good: `both_price_pct=0.7734` (778/1006
  events with both participant prices) on the 2026-08-15 sample date, and the
  five market-group JSON endpoints (`uo`/`bts`/`ht`/`ah`/`cards`) are already
  being fetched cheaply (5 requests/day) per `docs/FOREBET_DEPTH_AUDIT.md`.
- `corners`, `doublechance`, `goalscorer` are **intentionally** excluded from
  bulk capture — documented reason: the bulk JSON for these echoes the 1X2
  payload; real values only exist on the per-match detail page, which is far
  more expensive to fetch at scale. This is a scope decision already made and
  recorded, not an oversight — but it does mean corners/cards/doublechance
  detail data is deeper on Forebet than what Slumdog captures in bulk.
  `docs/FOREBET_DETAIL_COVERAGE.json` shows a 3-per-sport sample of detail
  pages was fetched and its own inventory of factor availability recorded
  per URL (`corners: false, cards: false` for those basketball samples — the
  sampling didn't cover football specifically in the excerpt reviewed here).
- No new gaps were found beyond the one the user already suspected
  (facet-usage, §3): the collection side is already broader than the
  consumption side for football specifically.

## 5. Real early performance data (all sports pooled where noted; football-specific where noted)

Two dates have real `settlement.json` payloads available on `main` so far
(remember: settlement only fires D+1, so 2026-09-06 through 09-09 exist as
run directories but have **not yet been settled** — their `settlements/`
subfolder is empty as of this review; 09-10/11/12 are even newer and also
unsettled):

| Date | Run ID | Football entries (settled) | Primary hit rate | Top-3 combined hit rate | Ranks 4+ hit rate | Overall football hit rate |
|---|---|---|---|---|---|---|
| 2026-09-02 | `acd78872019300ff` | 27 | **1.000** (1/1) | 0.333 (1/3) | 0.000 (0/24, 2 unresolved) | 0.037 (1/27) |
| 2026-09-05 | `4353ca88e825fd6a` | 507 | **0.000** (0/1) | 0.667 (2/3) | 0.000 (0/504, 5 unresolved, 19 unsettled) | ≈0.0039 (2/507) |

Reading this honestly:

- **Sample size is far too small to draw any real conclusion** — two
  primary picks total (1 win, 1 loss) is not a rate, it's two coin flips.
  Anyone who tells you "primary hit rate is 50%" from this data is
  overstating precision by orders of magnitude.
- The **top-3 cohort** (rank 1–3 per sport-day) is doing directionally
  better than the deep pool (`ranks_4_plus`, which is essentially at 0% both
  days) — consistent with the R2 rule doing *some* useful ranking work, but
  again, n=6 across two days is not evidence, just a direction worth
  continuing to watch as more days settle.
- The very large gap in volume between the two dates (27 vs. 507 football
  entries) reflects how many matches Forebet listed those particular days,
  not a system change.
- **This system is explicitly not being graded on ROI/EV** (per `AGENTS.md`);
  hit rate and calibration are the right lens, and even by that honest lens
  there simply isn't enough settled history yet to say anything quantitative.
  The right next step is not "improve the model" yet — it's "let 09-06
  through 09-12 settle" so there are ~9 days of real primary-pick outcomes
  instead of 2.

**Action for the user:** once 09-06 through 09-12 have gone through another
D+1 settlement pass (which needs another authenticated dispatch — this
agent cannot trigger it), re-pull this table. Nine real dates would still be
a small sample, but it's 4-5x more primary-pick evidence than exists today.

## 6. Accuracy-improvement recommendations (ranked by leverage, all training-neutral)

None of these require lifting the training freeze; they are about
data/decision-path hygiene ahead of any future training decision.

1. **Reconcile the two football feature paths (§3).** Decide explicitly
   whether `dataset.py::build_pre_event_features`'s 17-field vector stays
   the permanent frozen decision surface (in which case, document *why* the
   60+-field `football.py::extract_football_features` module exists
   alongside it and is not used by the shadow path — likely just "reserved
   for the eventual training feature set"), or whether an intentional,
   explicitly-authorized amendment is warranted to the frozen rule to widen
   it. This is a decision for the user/owner, not something to silently
   change — `AGENTS.md`'s anti-tuning rule requires this kind of change to
   be pre-authorized and not result-driven.
2. **Let the settlement backlog actually accumulate before judging
   anything.** Two settled primary picks is not enough to reason about
   direction. This is the single highest-leverage non-code action right
   now — more than any feature work.
3. **If/when a facet audit for the other 11 sports happens** (already the
   documented owner priority in `docs/STATE.md`'s "Road ahead"), prioritize
   basketball and tennis next — they have the largest real-data footprints
   after football per `docs/FOREBET_ARCHIVE_DEPTH.json` (basketball: 99–158
   rows/season-sample; tennis: 30–75) and their feature-extraction modules
   (`basketball.py`, `tennis.py`) already exist and are wired into
   `facets.py`, just like football's — the same "collected but not on the
   shadow decision path" gap likely exists there too and is worth
   confirming before assuming football is a special case.
4. **Track calibration, not just hit rate, once volume exists.** The R2 rule
   already reports summary stats per cohort; extending that to a
   probability-band breakdown (Forebet underdog-probability decile vs.
   observed win rate) would surface whether the system is *systematically*
   miscalibrated in one direction long before there's enough data for a
   trained model to be worth training.
5. **Corners/cards/doublechance detail-page capture remains a deliberate
   gap, not a bug** — leave it alone unless a specific feature hypothesis
   needs it; the per-match detail fetch cost is real and undocumented
   expansion here would violate the "small batches, 62s pauses" collection
   discipline in `AGENTS.md`.

## 7. Documentation staleness

`docs/STATE.md` is dated 2026-09-03 and does not yet reflect: the D+1
settlement automation going live in production (proven working twice),
the `run_evaluator()` history-selection fix (`972b79a`), or the successful
09-10/11/12 forward runs. `HANDOFF.md` is dated 2026-09-06 but was written
*before* the fix delivery and confirmation in this session. Both need a
refresh pass — this is a docs housekeeping task for whoever picks this up
next (this agent or the collaborating agent), not something this
read-only review changes.

## 8. Training-start timeline

No date is being proposed. Training remains frozen per `AGENTS.md` invariant
15 until the user explicitly approves dataset, target, timing, and
validation contract. The one relevant new fact from this review: real
settled outcomes are only just starting to accumulate (2 primary picks
total across 2 settled dates), so even if a training decision were made
today, there would not yet be enough forward-settled shadow history to use
as an honest out-of-time validation set for it. A reasonable trigger
condition to watch for (not a date, a condition) — enough settled shadow
days have accumulated to see a stable direction in top-3 hit rate — hasn't
been reached yet.
