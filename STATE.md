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
2026-08-23 depth audit (verified against live Forebet, not assumed)
- Direct forebet.com from Azure datacenter IPs (Codespaces AND GitHub runners)
  is behind a Cloudflare JS challenge ("Just a moment...", HTTP 403). Plain curl
  and curl_cffi TLS impersonation (safari17_0/chrome124) BOTH receive the
  challenge page. The `direct_get` fallback cannot succeed from those networks;
  it is only useful from a residential/local IP.
- The Jina relay (r.jina.ai) Markdown-reader mode WORKS from Codespace:
  200, 2.4 MB, 714 football rows for one date. It is the qualified free path;
  whether GitHub Actions IPs still 401 needs a live probe on the next run.
- Football 1X2 JSON row has 73 keys including stable identity
  (host_id/guest_id/league_id), venue (host_stadium), odds movement
  (move_1/move_X/move_2), cup flags (isCup, is_international_club_cup,
  is_nationalteam_cup), cup tiebreakers (penalty_score, extra_time_score),
  multilingual trend text and American odds (best_odd_*_am). These are now
  promoted to named PRE_EVENT facets; tiebreaker scores are RESULT_ONLY.
- Market endpoint distinctness verified on 2026-08-23:
  uo, bts, ht, ah, cards = distinct, ~714 rows each (cards 656).
  htft is BYTE-IDENTICAL to ht (single odds_ht_ft price, not a 9-cell matrix).
  corners, doublechance, goalscorer ECHO the 1X2 JSON payload exactly.
  Therefore FOOTBALL_MARKETS is (uo, bts, ht, ah, cards) and the other three
  are detail-page-only (FOOTBALL_DETAIL_ONLY_MARKETS). The prior code comment
  about corners/goalscorer echoing 1X2 is correct; football.py's corners/dc
  schema is aspirational and not endpoint-backed.
- The five distinct markets are one request per DATE each (covering all
  matches), so the "today only" guard was removed; they now backfill
  historically and are cached (markets.json reused).
- Non-football odds: handball DOES publish odds in `.haodd span` but as TWO
  American prices (home/away; draw cell blank). The draw_possible parser
  demanded three prices and dropped both -> falsely reported 0% coverage.
  Fixed to accept two valid prices (draw null). Hockey on 2026-01-15 is 99/99
  genuine dashes (WHL/AHL/women's) — not a parser bug for that sample; an
  in-season NHL/KHL/SHL date still needs checking.
- The per-match detail page embeds all market panels (1X2, UO, HT, HT/FT,
  BTTS, double chance, AH, corners, cards, goalscorers) in one HTML document,
  but corners/doublechance/goalscorers carry `Coef. -` there (no book price),
  so the JSON endpoints remain the price source for the markets they cover.
- Raw history retention is now the default (keep_raw=True). Ledger rows carry
  raw_sha256 and the workflow caches data/raw/<sport> between runs. Cup ties
  settled on penalties/ET are tagged SETTLED_CUP (regulation score decides
  winner_index; tiebreakers retained as facets).
- Phase B (football detail numeric extraction) added to detail_facets:
  shots (total/avg/blocked/on-off-target %/inside-box %), passes
  (total/avg/accurate/accuracy %/possession %), total+dangerous attacks,
  avg event times (first goal/corner/card), recent UO distributions at
  1.5/2.5/3.5, BTTS yes/no per side, next-fixture difficulty (1-5 avg),
  Forebet's embedded lg_-1_6/lg_1_6 W/D/L JSON, and the detail-only
  corners/cards/double-chance panels (their JSON endpoints echo 1X2). These
  are label-anchored regexes against the flattened page text; the "Others"
  pair table and disciplinary table remain parsed by _metric_pair_tables.
  The regex shapes were built from observed page text but MUST be verified
  against a real Jina-HTML detail capture (CLI: slumdog details + enrich)
  before being treated as production-accurate; a layout change leaves values
  missing rather than wrong.

Next gates
Review the first census + history receipts (from the scheduled pipeline), then
measure detail-field missingness from the census, not from three-page samples.
Probe GitHub Actions egress for the relay and the hockey price question on an
in-season top-league date.
Implement each sport's approved detail fields and ablations (numeric extraction
of shots/passes/possession/attacks/cards/fouls/tackles/standings/next-match
difficulty; corners/doublechance/goalscorers from detail HTML).
Review separate model cards, then deliberately unlock training.
Specialized MMA/Cricket/Esoccer settlement and void handling is implemented
(SettledEvent.disposition, void exclusion from history and training rows) and
covered by fixtures. Esoccer remains prospective-only until a reliable dated
archive route exists.
