# Addendum — Sport Eligibility Findings (2026-09-07)

> **Provenance (added 2026-09-07 by the collaborating Arena session, branch
> `arena/01a07741-slumdog`):** this document was authored by the prior Arena
> session — whose GitHub write access is closed — and was committed to the
> repo at that session's explicit request. The content below is reproduced
> verbatim, including its own session note.

> **Status: REVIEW / REFERENCE ONLY — not a milestone doc, not canonical.**
> Canonical current truth remains `docs/STATE.md`. This is a short addendum
> to `docs/REVIEW_2026-09-06_STATUS_PERFORMANCE_RECOMMENDATIONS.md`,
> produced by a follow-up investigation into "why do only some sports
> produce picks, and would backfill help." No code changes, no config
> changes, no backfill was run. Training remains FROZEN.
>
> **Session note:** this document was authored in an Arena session whose
> GitHub write access is already closed (same situation as the original
> review doc it extends). It exists locally only and must be committed by
> whoever has write access (Codespace or a collaborating agent's session).

## Finding: the "only 3 picks/day" ceiling was already resolving itself

Contrary to the initial assumption in the 2026-09-06 review (§1, "only
football has depth"), real production runs on `main` show the picture had
already improved by the time of writing:

| Target date | Primary picks | Top-3 cohort | Sports contributing a primary pick |
|---|---|---|---|
| 2026-09-02 … 09-09 | 1 | 2 | football only |
| 2026-09-10 | 5 | 7 | basketball, football, hockey, rugby, volleyball |
| 2026-09-11 | 6 | 11 | baseball, basketball, football, handball, hockey, rugby |
| 2026-09-12 | 7 | 14 | american_football, baseball, football, handball, hockey, rugby, volleyball |

(Verified directly against `shadow_selections.json`'s `decision_accounting`
block for each run on `main`.) The cohort size per sport-day (1 primary + 2
top-3) is fixed and frozen under `config/shadow_evaluator_v1.json`
(`cohort_policy.primary_selection_per_sport_day == 1`,
`top3_cohort_per_sport_day == 2`, both hard-validated) — but there is
`"no_global_cap": true`, so total daily pick volume grows automatically as
more sports independently clear the R2 eligibility gate:

```
underdog_prior_games >= 5 AND favorite_prior_games >= 5
AND h2h_prior_games >= 1 AND forebet_probability_gap <= 0.2
```//`src/slumdog/baseline_analyzer.py::is_r2_eligible`

This is not a config change and required none — it is the existing frozen
rule naturally admitting more sport-days as each sport's settled-history
ledger (accumulated by the daily forward pipeline) deepens over time.

## Finding: three sports have never produced a primary pick — and backfill would not help two of them

As of the runs examined (through 2026-09-12), **tennis, cricket, and mma**
have never produced a `PRIMARY_SHADOW_SELECTION` (esoccer is excluded
entirely — `current_only=True`, no dated archive exists to backfill).

The initial hypothesis was thin ledger volume. Actual row counts (pulled
directly from the Codespace's `data/reports/history_<sport>.jsonl.gz`
ledgers, 2026-09-07) disprove that:

| Sport | Rows | Date range | Unique participant pairs | Pairs with ≥2 meetings | Repeat rate |
|---|---|---|---|---|---|
| tennis | 38,712 | 2024-05-16 → 2026-08-21 | 33,682 | 4,014 | **12%** |
| cricket | 6,546 | 2025-02-10 → 2026-08-21 | 2,968 | 1,543 | **52%** |
| mma | 759 | 2025-01-11 → 2026-08-16 | 718 | 38 | **5%** |

Row volume is not the binding constraint for any of the three — even mma's
759 rows comfortably exceeds the `prior_games >= 5` threshold for
*individual* participants many times over; that threshold is per-participant,
not sport-wide. The real gate is `h2h_prior_games >= 1`, which requires the
*same two* participants to have met before, in Slumdog's own settled
history.

**Tennis and MMA are structurally H2H-gated, not depth-gated.** Both draw
from a large pool of individual competitors (thousands of players/fighters)
where most pairings are one-off; only 12% (tennis) and 5% (mma) of
historical pairings have ever repeated even once. Backfilling further
history for either sport would add more one-off matches, not more rematches
— it is very unlikely to materially increase the number of pairings that
satisfy `h2h_prior_games >= 1` for a later meeting. **No backfill
recommended for tennis or mma on this basis.**

**Cricket is structurally different — high repeat rate (52%) — but is not a
backfill candidate either, for an unrelated reason:** its ledger already
starts (2025-02-10) almost exactly at the sport's own conservative
`HISTORY_STARTS["cricket"] = "2025-01-01"` contract value in
`src/slumdog/sports.py`. There is barely any earlier history to backfill
*to* under the existing frozen contract. Cricket's picture will improve
organically as the daily forward pipeline keeps accumulating more recent
rows — the same "let it accrue" conclusion as the original review's #2
recommendation, just for a different underlying reason (young archive
window, not sparse pairs).

## Conclusion — no backfill action taken or recommended

- No `backfill-sport` command was run for any sport as a result of this
  investigation.
- The apparent "only 3 picks/day" limitation observed early on was a
  temporary artifact of early-stage ledger depth, not a permanent ceiling —
  it has already substantially resolved itself via the existing daily
  forward pipeline (8 sports had contributed a primary pick across
  2026-09-10 through 09-12, football through american_football).
- Tennis, mma, and cricket are expected to remain rare-to-never
  contributors for structural reasons (sparse repeat pairings for
  tennis/mma; a young archive window for cricket) rather than a
  data-collection gap that more backfill effort would close. This is a
  legitimate, evidence-backed structural finding — not something to
  "fix" by loosening the frozen R2 thresholds (that would be a
  result-driven amendment, prohibited under `AGENTS.md`/
  `config/shadow_evaluator_v1.json`'s anti-tuning rule).
- Recommended next check-in: re-pull the per-sport primary-pick tally
  after another 1–2 weeks of forward runs to confirm cricket's
  contribution is in fact rising as its ledger matures, and that
  tennis/mma remain at or near zero as predicted.
