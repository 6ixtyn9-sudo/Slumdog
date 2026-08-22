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
A scheduled GitHub workflow (.github/workflows/pipeline.yml) runs the full
current-board census weekly and accumulates every sport's dated archive
daily; a manual dispatch (no inputs) runs both immediately. A final job
merges all receipts into one Job Summary.
Detail enrichment writes numeric facets with timing classification and a
per-sport missingness receipt.
Annual archive and representative price/detail coverage reports are stored in
docs/.
Training is blocked unless an explicit research override is passed.
Dates are runtime-derived, not typed at dispatch: the workflow takes no
dispatch inputs, and every CLI date argument is an optional override that
defaults to the runner clock in TZ Africa/Johannesburg (src/slumdog/clock.py).
Heavy compute (research gate: per-sport model cards + feature ablations) runs
on GitHub-hosted runners (4 vCPU / 16 GB, Azure), not the local Codespace;
the pipeline's research job downloads the history ledgers, runs
`slumdog research --research-override`, and uploads the report as an artifact.
The Codespace is for code changes only.
History ledgers are rolling and resumable per sport (history_<sport>.jsonl.gz +
manifest), so repeat runs only fetch new dates. The pipeline persists each
sport's ledger and the census detail cache via actions/cache between runs
(per-sport keys + run_id), so the daily history job is genuinely incremental
instead of re-fetching every date from scratch. Relay access uses bounded
retry with backoff (transient 4xx/5xx/timeouts), and backfill batches are
gentle (max 6 in parallel) to stay under the public relay's shared-IP budget;
a valid page with zero rows counts as covered, not failed. Football capture
uses the relay's Markdown reader mode first (Edge-Factory-validated path for
the JSON endpoint — no X-Return-Format, unwrap the "Markdown Content:" body),
then the html-forced relay, then direct Forebet access (AJAX headers +
optional curl_cffi) for local runs; on GitHub runners a deterministic relay
failure fails fast instead of stalling. 2026-08-21 first run: football listing
401s on the runner, so its 963 dates remain to backfill (retried with the
Markdown-mode path on the next run).
Next gates
Review the first census + history receipts (from the scheduled pipeline), then
measure detail-field missingness from the census, not from three-page samples.
Implement each sport's approved detail fields and ablations.
Review separate model cards, then deliberately unlock training.
Specialized MMA/Cricket/Esoccer settlement and void handling is implemented
(SettledEvent.disposition, void exclusion from history and training rows) and
covered by fixtures. Esoccer remains prospective-only until a reliable dated
archive route exists.
