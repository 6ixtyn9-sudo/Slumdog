# Slumdog R1 Scorecard — generated for 2026-09-28

Offline measurement from already-committed shadow evidence only (`data/reports/shadow/**`). No network access. See `method` in the JSON receipt for the exact grading contract and significance floor.


## STANDARD track

Scope: **23** settled target date(s), 2026-09-02 to 2026-09-26. only target dates with a committed settlement.json are scored; 7 additional date(s) have selections but no settlement yet and are excluded from every rate below (not zero-filled, not guessed).

**Overall R1 hit rate: 19/44 (43.2%) [95% CI 29.7%–57.8%]** (of which 6 settled as a draw, counted as a loss per the grading contract)

### Coverage — did an R1 exist at all?

| Sport | Sport-days attempted | Sport-days with an R1 | Coverage |
|---|---:|---:|---:|
| american_football | 3 | 2 | 67% |
| baseball | 8 | 8 | 100% |
| basketball | 8 | 5 | 62% |
| cricket | 4 | 2 | 50% |
| football | 29 | 28 | 97% |
| handball | 13 | 10 | 77% |
| hockey | 7 | 5 | 71% |
| mma | 1 | 0 | 0% |
| rugby | 5 | 4 | 80% |
| volleyball | 5 | 3 | 60% |
| **all sports** | **83** | **67** | 81% |

_'attempted' means this sport appeared in that date's sport_day_summary at all — a sport that failed to fetch entirely that day (WAF block, timing hold, etc.) never appears here and is not counted as an attempt; see HANDOFF.md / docs/STATE.md for the fetch-failure history this coverage table cannot see._

### R1 hit rate by sport

| Sport | R1 selections recorded | Settled hit rate |
|---|---:|---|
| american_football | 2 | 1/1 (100.0%) [95% CI 20.6%–100.0%] ⚠️ n<30 |
| baseball | 8 | 4/4 (100.0%) [95% CI 51.0%–100.0%] ⚠️ n<30 |
| basketball | 5 | 2/3 (66.7%) [95% CI 20.8%–93.8%] ⚠️ n<30 |
| cricket | 2 | 0/1 (0.0%) [95% CI 0.0%–79.3%] ⚠️ n<30 |
| football | 23 | 5/19 (26.3%) [95% CI 11.8%–48.8%] ⚠️ n<30 |
| handball | 10 | 3/7 (42.9%) [95% CI 15.8%–75.0%] ⚠️ n<30 |
| hockey | 5 | 1/3 (33.3%) [95% CI 6.2%–79.2%] ⚠️ n<30 |
| rugby | 4 | 2/3 (66.7%) [95% CI 20.8%–93.8%] ⚠️ n<30 |
| volleyball | 3 | 1/3 (33.3%) [95% CI 6.2%–79.2%] ⚠️ n<30 |

### R1 hit rate by underdog-probability band

| Band | R1 selections recorded | Settled hit rate |
|---|---:|---|
| <0.20 | 0 | n=0 (no settled rows) |
| 0.20-0.30 | 11 | 2/8 (25.0%) [95% CI 7.1%–59.1%] ⚠️ n<30 |
| 0.30-0.40 | 13 | 3/12 (25.0%) [95% CI 8.9%–53.2%] ⚠️ n<30 |
| 0.40+ | 38 | 14/24 (58.3%) [95% CI 38.8%–75.5%] ⚠️ n<30 |

### Baselines, computed on the SAME R1 rows

| Strategy | Settled hit rate |
|---|---|
| Our R1 pick | 19/44 (43.2%) [95% CI 29.7%–57.8%] |
| Always favourite (opposite pick, same match) | 19/44 (43.2%) [95% CI 29.7%–57.8%] |
| Follow Forebet's own pick, same match (24 row(s) missing forebet_pick, excluded) | 14/36 (38.9%) [95% CI 24.8%–55.1%] |
| Always underdog (same rows — identical to our R1 pick by construction) | 19/44 (43.2%) [95% CI 29.7%–57.8%] |

### Supplementary: full eligible-cohort underdog rate (not one of the three required baselines)

Betting the underdog on EVERY R2-eligible candidate the pipeline surfaced that day (not just the rank-1 pick actually selected): 307/1703 (18.0%) [95% CI 16.3%–19.9%]

_supplementary, NOT one of the three required same-row baselines — a different, larger row set (every R2-eligible candidate any rank, not just the rank-1 pick actually selected)._

## EVENT_DAY track

**Not available.** no data/reports/shadow_event_day directory exists in this checkout — no real run has ever been committed on this track. This is the finding, reported plainly, not papered over.
