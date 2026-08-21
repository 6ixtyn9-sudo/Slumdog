Slumdog State
Phase
Forebet depth audit — model training frozen.

Current contract
Forebet is the sole external prediction/displayed-price source.
All supported sports are captured as immutable raw HTML/JSON.
No output-count cap is planned.
No model, suggestion or schedule is production-authorized yet.
No other repository is imported.
Why training was frozen
A 14-day preliminary experiment used separate estimators per sport but still
shared a mostly generic probability/price/history vector. That does not satisfy
the requirement to analyse every Forebet facet. The seed, generated models and
example suggestions were removed from the candidate repository.

Proven listing depth samples
A same-calendar-date probe established these lower bounds, not exact earliest
dates:

Football: non-empty 2024 sample; sampled 2023 date empty.
Basketball and Hockey: non-empty 2023 samples.
Tennis, Baseball, Handball and Volleyball: non-empty 2024 samples.
Rugby and Cricket: non-empty 2025 samples.
American Football and MMA: current-period samples only in this probe.
Esoccer: rolling board; no reliable dated archive route confirmed.
Seasonality means an empty sampled date does not prove the archive starts there.
A systematic earliest-date search remains required.

Depth implementation
Sport-specific feature and settlement contracts are machine-readable.
One manual GitHub dispatch captures all current listings and every current
match detail, while parallel sport jobs stream each dated archive from its
own start date. A final job merges all receipts into one Job Summary.
Detail enrichment writes numeric facets with timing classification and a
per-sport missingness receipt.
Annual archive and representative price/detail coverage reports are stored in
docs/.
Training is blocked unless an explicit research override is passed.
Dates are runtime-derived, not typed at dispatch: the workflow takes no
dispatch inputs, and every CLI date argument is an optional override that
defaults to the runner clock in TZ Africa/Johannesburg (src/slumdog/clock.py).
History ledgers are rolling and resumable per sport (history_<sport>.jsonl.gz +
manifest), so repeat runs only fetch new dates.
Next gates
Dispatch the full Forebet build (one manual GitHub dispatch) so the current
all-detail census and each sport's full dated backfill stream to compressed
receipts, then measure detail-field missingness from the census, not from
three-page samples.
Implement each sport's approved detail fields and ablations.
Review separate model cards, then deliberately unlock training.
Specialized MMA/Cricket/Esoccer settlement and void handling is implemented
(SettledEvent.disposition, void exclusion from history and training rows) and
covered by fixtures. Esoccer remains prospective-only until a reliable dated
archive route exists.
