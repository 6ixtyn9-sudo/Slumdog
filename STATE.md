# Slumdog State

## Phase

Forebet depth audit — model training frozen.

## Current contract

- Forebet is the sole external prediction/displayed-price source.
- All supported sports are captured as immutable raw HTML/JSON.
- No output-count cap is planned.
- No model, suggestion or schedule is production-authorized yet.
- No other repository is imported.

## Why training was frozen

A 14-day preliminary experiment used separate estimators per sport but still
shared a mostly generic probability/price/history vector. That does not satisfy
the requirement to analyse every Forebet facet. The seed, generated models and
example suggestions were removed from the candidate repository.

## Proven listing depth samples

A same-calendar-date probe established these lower bounds, not exact earliest
dates:

- Football: non-empty 2024 sample; sampled 2023 date empty.
- Basketball and Hockey: non-empty 2023 samples.
- Tennis, Baseball, Handball and Volleyball: non-empty 2024 samples.
- Rugby and Cricket: non-empty 2025 samples.
- American Football and MMA: current-period samples only in this probe.
- Esoccer: rolling board; no reliable dated archive route confirmed.

Seasonality means an empty sampled date does not prove the archive starts there.
A systematic earliest-date search remains required.

## Depth implementation

- Sport-specific feature and settlement contracts are machine-readable.
- A manual GitHub depth worker captures one sport/date listing plus up to 18
  uncached detail pages per run and persists the detail cache.
- Detail enrichment writes numeric facets with timing classification and a
  per-sport missingness receipt.
- Annual archive and representative price/detail coverage reports are stored in
  `docs/`.
- Training is blocked unless an explicit research override is passed.

## Next gates

1. Run the depth worker across representative leagues/seasons per sport until
   required detail-field missingness is measured, not assumed.
2. Add specialized MMA/Cricket/Esoccer settlement and identity fixtures.
3. Backfill each sport from its own conservative history start.
4. Implement each sport's approved detail fields and ablations.
5. Review separate model cards, then deliberately unlock training.
