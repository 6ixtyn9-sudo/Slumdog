# Forebet Full-Facet Analysis Plan

> **Status: REFERENCE — Analysis Plan**
>
> Timing classes (PRE_EVENT / LIVE_ONLY / RESULT_ONLY / UNKNOWN) remain authoritative.
> Steps mentioning price / value / ROI are historical planning; per current mission,
> ROI is not primary metric and price coverage is reference only, not a readiness gate.
> Current authority: `AGENTS.md`, `README.md`, `docs/STATE.md`.

## Coverage

Slumdog captures Football, Basketball, Tennis, Hockey, Baseball, American
Football, Rugby, Handball, Volleyball, Cricket, MMA and Esoccer.

Every source body is frozen before parsing and addressed by SHA-256. Parser
changes can therefore be replayed without rewriting what was observed.

## Timing classes

Every field must be assigned one class:

```text
PRE_EVENT    eligible for Robber detection and ML
LIVE_ONLY    retained, never a feature
RESULT_ONLY  settlement/audit only
UNKNOWN      retained, blocked from models
```

## Common fields to catalogue

- source event identity and URL;
- participant names/order and aliases;
- league, tournament, stage and round;
- event date, kickoff and timezone;
- Forebet participant probabilities and draw probability;
- Forebet selected outcome;
- predicted score/sets/points and expected total;
- every displayed participant coefficient;
- standings/rank, recent form and home/away splits;
- H2H history;
- streaks/trends;
- scoring/conceding averages and margins;
- weather/conditions where displayed;
- cancellation/postponement/live/final indicators;
- final result and period results.

## Sport-specific catalogue

- Football: three-way probability, draw, BTTS, totals, weather, standings.
- Basketball: quarter data, predicted score/total, moneyline, pace proxies.
- Tennis: surface, round, player rank, sets, game total, player form.
- Hockey: regulation/overtime semantics, period data, total.
- Baseball: innings, hits, runs, result settlement.
- American Football: quarter data, total, predicted margin.
- Rugby: halves, margin and total.
- Handball: three-way/draw structure, halves and total.
- Volleyball: set scores, predicted sets, total points.
- Cricket: match format, multi-day span, draw/no-result, innings and tours.
- MMA: division, record, height, weight, reach, stance, strikes, takedowns,
  submissions, control time and predicted method.
- Esoccer: player handle identity, match format, repeat frequency and aliases.

## Analysis order

For each sport and each facet:

1. availability and missingness;
2. first known pre-event timestamp;
3. parser reliability against frozen fixtures;
4. univariate upset rate;
5. calibration by value band;
6. marginal lift over Forebet underdog probability;
7. interaction with the Ma Golide legacy factors;
8. stability across leagues/tournaments and time;
9. contribution to out-of-time log loss, Brier score and underdog hit rate;
10. price-available versus price-missing coverage bias.

A field is not retained merely because it is visible. It must be leak-safe and
show stable out-of-time contribution or serve an audit/settlement purpose.
