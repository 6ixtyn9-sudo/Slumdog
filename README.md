# Slumdog

Slumdog is a standalone multi-sport research system for reproducing and testing
Ma Golide **Robber** decisions against Forebet's complete pre-event matchup
surface.

It captures every supported sport, identifies the Forebet percentage underdog,
reproduces the legacy Ma Golide Robber score, and learns sport-specific
conditions under which that underdog wins.

## Principles

- Forebet is the sole external prediction and displayed-price source.
- Every raw page is frozen before parsing.
- Every visible Forebet factor is catalogued with timing provenance.
- Result/live fields cannot enter pre-event features.
- Missing prices are allowed for upset learning, but expected value remains
  unknown and the output is not actionable.
- Outputs are not capped. Every candidate passing the configured confidence
  threshold is emitted.
- No candidate is called certain or guaranteed.
- v0.1 emits shadow research only.

## Architecture

```text
Forebet sport pages
        |
        v
immutable sport captures (Satellites)
        |
        v
facet catalogue + sport contracts
        |
        v
Ma Golide Robber reproducer (Assayer baseline)
        |
        v
sport-specific ML-meta probability
        |
        v
all qualifying shadow Robbers (Mothership output)
```

## Sports

- Football
- Basketball
- Tennis
- Hockey
- Baseball
- American Football
- Rugby
- Handball
- Volleyball
- Cricket
- MMA
- Esoccer

Each sport has its own outcome and settlement contract. Probabilities and
thresholds are never pooled blindly across sports.

## Robber definition

A Robber is a named participant selected as the upset side.

When both Forebet prices exist, the participant with the longer price is the
market underdog. Without prices, the participant opposite Forebet's predicted
winner is the percentage underdog; if needed, the lower participant probability
breaks the tie.

The legacy Ma Golide score then considers:

- favorite strength;
- underdog H2H upset rate;
- period/quarter dominance;
- half performance;
- recent momentum;
- displayed price band, when present.

Legacy confidence is reproduced for audit comparability. It is not accepted as
a trained probability. The ML-meta layer learns the actual underdog-win target
from out-of-time data.

## Model freeze

Training is disabled while the Forebet depth audit is in progress. A preliminary
14-day experiment demonstrated that separate sport estimators are not enough
when their feature vector is still mostly generic. That experiment and its
models were discarded rather than shipped.

Retraining is allowed only after each sport has a documented listing/detail
facet contract, historical depth receipt, timing classification, settlement
coverage and price-availability profile.

## Status lanes

```text
SHADOW_UNPRICED  probability-defined Robber; Forebet price missing
SHADOW_PRICED    Forebet price exists; model not certified
CERTIFIED        reserved for later sport-specific prospective proof
```

v0.1 never emits `CERTIFIED`.

## Quick start

```bash
python -m pip install -e '.[dev]'
pytest

# Dates come from the runner clock in TZ Africa/Johannesburg. Every --date /
# --start / --end argument is an optional override; omit it to mean "now".

# Freeze all Forebet sport surfaces for today (or --date YYYY-MM-DD).
slumdog capture

# Bounded historical probe: trailing 7 days by default (or --start/--end).
slumdog backfill

# Full current-board census: all listings and every available match detail.
slumdog depth-sweep --per-sport 1000000

# Full dated history for one sport; the ledger is rolling and resumable, so
# repeat runs only fetch days not yet covered (the GitHub pipeline fans all
# sports out in parallel). --end defaults to yesterday.
slumdog backfill-sport --sport basketball --start 2023-01-01

# Turn the pipeline receipts into a research report (field missingness,
# coverage by season/league, priced vs unpriced) once data exists.
slumdog analyze

# The GitHub pipeline (Slumdog · Forebet Depth Pipeline) runs automatically:
#   daily 03:00 UTC  history accumulation (through yesterday)
#   Mon    02:00 UTC full census + consolidated receipt
#   manual dispatch (no inputs) runs both immediately

# Lower-level commands remain available for parser development.
slumdog parse
slumdog details --events data/interim/events_$(date +%F).json --max-events 18
slumdog enrich --events data/interim/events_$(date +%F).json

# Model commands remain available only behind an explicit research override.
# Production training is frozen until parser/missingness gates are complete.
```

Listing parsers normalize the common Forebet event surface across all sports.
Raw HTML remains the source of truth for deeper match-detail facets developed in
the next parser phase.

## Documentation policy

`STATE.md` is kept concise and describes only the current contract and next
build gate. Detailed findings belong in dated, bounded research reports rather
than an ever-growing handover file.
