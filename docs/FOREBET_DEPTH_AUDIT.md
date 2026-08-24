# Forebet Depth Audit — Training Freeze Receipt

## Finding

The preliminary model used one estimator per sport, but too many features were
still generic: Forebet participant probabilities, probability gap/entropy,
displayed odds, legacy Robber score, generic H2H counts and generic recent form.
That is not a sufficient sport-specific representation. Training is frozen and
the preliminary seed/models were removed.

## Common listing surface

Most sports expose participant identities, competition code, date/time,
participant probabilities, predicted participant, predicted score/sets/points,
expected total and optional participant coefficients. Period result columns are
sport-specific. Raw pages also contain live/final fields that must be excluded
from pre-event modeling.

## Match-detail surface observed by sport

### Football

- three-way home/draw/away probabilities and predicted outcome;
- predicted score and average goals;
- displayed 1X2 coefficients;
- weather;
- league position/standings;
- home/away and last-six form;
- H2H;
- half-time/full-time probabilities;
- goals scored/conceded;
- BTTS and scoring/no-scoring rates;
- corners and cards averages;
- streak/trend statements;
- first-half and second-half splits.

Planned feature families: draw pressure, probability entropy, favourite gap,
venue splits, standings gap, goals attack/defence gaps, BTTS/totals profile,
HT/FT profile, corners/cards context, weather, H2H and form trajectory.

### Basketball

- two-way probabilities and predicted winner;
- predicted final score and average points;
- moneyline coefficients;
- Q1–Q4/OT scores;
- conference and overall standings;
- last-six form;
- home/away splits;
- H2H and next fixtures.

Planned features: predicted margin/total, standings and win-rate gap, pace/total
proxy, quarter consistency, home/away split, H2H margin, rest/schedule where
provable, recent scoring and conceding form.

### Tennis

- two-way probabilities and predicted winner;
- predicted sets and average games per set;
- coefficients;
- tournament and round;
- player height where displayed;
- last-six matches;
- surface-specific career win/loss JSON for clay/hard/grass;
- H2H where available.

Planned features: surface win-rate gap and sample, tournament round, predicted
set margin, games-per-set expectation, recent form, ranking/height when present,
H2H, favourite probability concentration and price.

### Hockey

- two-way probabilities and predicted winner;
- predicted score and average goals;
- coefficients;
- P1–P3, OT and penalty result structure;
- standings;
- last-six, home/away form and H2H.

Planned features: regulation/overtime contract, predicted margin/total,
standings gap, period dominance/variance, H2H, recent scoring margins and venue
splits.

### Baseball

- two-way probabilities and predicted winner;
- predicted score, average runs and hits;
- coefficients;
- inning result structure;
- competition/round;
- last-six, home/away form and H2H;
- narrative form summaries where displayed.

Planned features: predicted run margin/total, hitting/run environment, inning
profile, recent win and run-differential form, H2H, venue split and competition
context. Pitcher information is not assumed unless timing-proven on the page.

### American Football

- two-way probabilities and predicted winner;
- predicted final score and total;
- coefficients;
- Q1–Q4/OT results;
- last-six, home/away form and H2H.

Planned features: predicted margin/total, quarter scoring balance, H2H margin,
recent scoring/defence, home/away performance and competition context.

### Rugby

- two-way probabilities and predicted winner;
- predicted score and average points;
- coefficients;
- round/stage;
- H2H, last-six, home/away form and next fixtures.

Planned features: predicted margin/total, H2H margin, recent points for/against,
venue split, competition/round and available half/period structure.

### Handball

- home/draw/away probabilities;
- predicted score and average goals;
- coefficients;
- half results;
- standings where available;
- H2H, last-six and home/away form.

Planned features: draw pressure, predicted margin/total, standings gap, half
performance, H2H, recent goal difference and venue split.

### Volleyball

- two-way probabilities and predicted winner;
- predicted set score and average points per set;
- coefficients;
- S1–S5 structure;
- group standings where available;
- H2H, last-six and home/away form.

Planned features: predicted set margin, points-per-set environment, group rank,
set-by-set stability, H2H, recent set differential and venue split.

### Cricket

- participant and optional draw probabilities;
- predicted winner and prediction runs;
- average runs;
- match format/tour/competition;
- multi-day date span;
- innings and result narratives;
- H2H and last-six/home/away form;
- D/L and DLS outcomes, no-result and draw semantics.

Planned features: format-specific model only (T20/ODI/Test separated), draw/no-
result probability, expected runs, innings profile, H2H, recent format-specific
form, chase/bat-first context only when pre-event and venue/tour context.

### MMA

- fighter probabilities and predicted winner;
- predicted finish method;
- coefficients;
- division;
- fighter record;
- KO and submission wins;
- height, weight, reach and stance;
- recent fights;
- average strikes per fight;
- significant strikes by target;
- takedown attempts/landed;
- submissions and control time.

Planned features: record strength/opposition-adjusted form, physical differences,
stance matchup, striking/takedown/submission/control gaps, predicted method,
division and scheduled rounds. No team-sport H2H/form template is reused.

### Esoccer

- three-way probabilities, predicted score and average goals;
- player handles embedded in team names;
- rapid repeated matchups;
- HT/FT, corner/card and scored/conceded surfaces on detail pages;
- H2H and last-six pages.

Planned features: strict player-handle identity, game format/duration, repeat
frequency, handle-pair H2H, short-horizon drift and score environment. Team club
names are presentation skins and cannot be treated as physical clubs. Esoccer
has no confirmed reliable dated archive route and remains research-only.

## Historical depth contract

Active-season annual probes were run from 2018 through 2026. Zero can still
mean an off-day, so Slumdog uses conservative backfill start dates at or before
the earliest proven non-empty year rather than claiming an exact first fixture.

| Sport | Earliest proven non-empty year | Conservative backfill start |
|---|---:|---:|
| Football | 2024 | 2024-01-01 |
| Basketball | 2023 | 2023-01-01 |
| Tennis | 2024 | 2024-01-01 |
| Hockey | 2022 | 2022-01-01 |
| Baseball | 2024 | 2024-01-01 |
| American Football | 2023 | 2023-01-01 |
| Rugby | 2024 | 2024-01-01 |
| Handball | 2024 | 2024-01-01 |
| Volleyball | 2024 | 2024-01-01 |
| Cricket | 2025 | 2025-01-01 |
| MMA | 2025 | 2025-01-01 |
| Esoccer | no reliable dated archive | prospective only |

The full annual row matrix is stored in `FOREBET_ARCHIVE_DEPTH.json`. Training
must use the sport-specific start above and may not force one shared time span.

## Depth gate status

Completed:

- annual archive probes and conservative per-sport start dates;
- three live detail pages per sport;
- listing/detail factor-family inventory;
- representative price-coverage snapshots;
- sport-specific outcome and feature contracts;
- timing classes and blocked-field contracts;
- explicit model-training freeze.

Still required before retraining (implementation, not further discovery):

- parser fixtures for every approved detail field;
- specialized settlement/void code where generic score settlement is invalid;
- full historical backfill from each sport's own start date;
- field missingness by season/league;
- sport-specific feature ablations and model cards.

## Preliminary three-detail-page coverage

Three live detail pages per sport were checked to qualify the field contracts.
This is no longer the final missingness gate: the full-build workflow now fetches
every current detail page and reports census coverage. The table remains the
pre-deployment receipt that justified each parser family.

| Sport | H2H | Last 6 | Venue splits | Standings | Core sport detail |
|---|---:|---:|---:|---:|---|
| Football | 3/3 | 3/3 | 3/3 | 0/3 sample | weather 3/3, HT/FT 3/3, corners 3/3, cards 3/3, BTTS 1/3 |
| Basketball | 3/3 | 3/3 | 3/3 | 1/3 | quarters 3/3, expected total 3/3 |
| Tennis | 2/3 | 3/3 | n/a | n/a | surface records 3/3, height 3/3, sets 3/3 |
| Hockey | 3/3 | 3/3 | 3/3 | 3/3 | period structure 3/3, expected total 3/3 |
| Baseball | 3/3 | 3/3 | 3/3 | 1/3 | narrative intro 3/3, expected runs 3/3 |
| American Football | 3/3 | 3/3 | 3/3 | 0/3 | quarter structure 3/3 |
| Rugby | 3/3 | 3/3 | 3/3 | 0/3 | expected points 3/3 |
| Handball | 3/3 | 3/3 | 3/3 | 0/3 | expected goals 3/3 |
| Volleyball | 3/3 | 3/3 | 3/3 | 0/3 sample | set structure 3/3, expected points/set 3/3 |
| Cricket | 3/3 | 3/3 | 3/3 | 0/3 | innings 3/3, DLS/DL evidence 2/3 |
| MMA | n/a | specialized recent fights | n/a | n/a | height/weight/reach/stance/strikes/takedowns/submissions/control 3/3 |
| Esoccer | 3/3 | 3/3 | 3/3 | n/a | HT/FT/corners/cards/BTTS/weather-like field 3/3 |

## Representative displayed-price coverage

One active-season date was sampled per sport. This is a snapshot, not a global
coverage estimate.

| Sport | Events | Both participant prices | Coverage |
|---|---:|---:|---:|
| Football | 1006 | 778 | 77.3% |
| Basketball | 99 | 60 | 60.6% |
| Tennis | 75 | 72 | 96.0% |
| Baseball | 19 | 13 | 68.4% |
| Cricket | 15 | 0 | 0% |
| Handball | 96 | 0 | 0% |
| Hockey | 77 | 0 | 0% |
| Rugby | 14 | 0 | 0% |
| Volleyball | 134 | 0 | 0% |
| American Football | sampled date had no events | — | — |
| MMA | sampled date had no events | — | — |
| Esoccer | rolling archive; separate audit required | — | — |

Price coverage must be measured across many dates and leagues before any value
or ROI conclusion. Unpriced events remain useful for upset learning but cannot
support expected-value certification.

## Verified no-odds coverage gaps (2026-08-24)

Live relay probes and settled ledgers found no bookmaker prices for cricket:
6,643 settled rows (6,119 SETTLED, 375 SETTLED_DRAW, 149 VOID), zero priced.
Cricket listings expose Forebet probabilities via `.fprc`, but no `.haodd` odds
container. This is a coverage limitation rather than a parser gap.

American-football upcoming rows expose `.haodd` with dashes. Four 2026
preseason fixtures and 7,447 archived settled rows contained zero prices. This
remains subject to a regular-season re-check on or after approximately
2026-09-10; no final claim should be made until that probe is executed.

### MMA legacy-ledger integrity

The 2026-08-24 audit of `history_mma.jsonl.gz` found 759 stored rows and 757
unique event IDs. Two settled, priced events (`mma:2638` and `mma:2721`) were
byte-identical duplicate writes from the 2026-06-15 listing. Deduplication gives
600 settled, 157 void, and 153 priced events; 11 events are both void and
priced. Thus the earlier `159 void == 159 priced` observation is neither
reproduced nor a structural settlement bug. Pre-event prices on a subsequently
void event are retained for audit.

All 759 legacy rows lack both `raw_sha256` and `captured_at`; they predate raw
provenance retention. Current backfill writes `facets.raw_sha256` for new rows.
Legacy provenance can only be restored by rebuilding from retained raw captures;
hashes must never be fabricated. The existing derived ledger is not rewritten
automatically.
