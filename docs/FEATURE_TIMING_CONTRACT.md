# Feature Timing Contract — Milestone 3 Audit

**Last verified:** 2026-08-24 (UTC)
**Branch:** arena/01a033af-slumdog
**Status:** CURRENT — Milestone 3 feature timing and leakage audit
**Training:** FROZEN (MODEL_TRAINING_ALLOWED=False)
**Doc type:** CURRENT (replaces ad-hoc timing claims, governs new-path eligibility)

## Mission and Rules

Per `AGENTS.md` invariants:
- Target is UNDERDOG_WIN outright only; draws never count as success.
- Odds are optional metadata only — not required, not feature, not gate.
- Missing odds must not lower confidence.
- Training frozen until dataset/target/timing/validation contract approved.

**Timing definitions (from `contracts.py` TimingClass):**
- PRE_EVENT — demonstrably available before kickoff on listing/detail page for upcoming events, retained in frozen capture before result known.
- RESULT_ONLY — final score, period scores, winner, disposition, extra-time/penalty, halftime score, settlement comment — prohibited as feature.
- UNKNOWN — timing not proven by code + retained bytes or live Jina probe — prohibited until verified.
- LIVE_ONLY — in-event score — prohibited.

**New-path eligibility:**
- ALLOWED — PRE_EVENT proven, not odds-dependent, missingness handled.
- PROHIBITED — RESULT_ONLY, UNKNOWN until verified, odds-dependent, settlement/price leakage.
- PARKED — plausible PRE_EVENT but needs sport-specific validation or evidence not yet captured (e.g., detail facets not in census).

**Rules (from task):**
- RESULT_ONLY → prohibited
- UNKNOWN → prohibited until verified
- odds-dependent → prohibited (odds are optional metadata only)
- missing odds irrelevant (must not gate, must not lower confidence)
- existing legacy use does NOT prove eligibility
- suggestive name does NOT prove timing
- evidence must cite code file/function or retained bytes path (not inference)
- No code change during Milestone 3 audit (read-only)

---

## Priority Investigation: period_values

Required 10-point trace per task. Until resolved, period_values = UNKNOWN, new-path PROHIBITED.

### 1. DOM selector / JSON field

- **Selector:** `.predQ .fj_column span` — listing page quarter/period cells
- **File:** `src/slumdog/parsers.py:170-173`
```python
period_values = [
    [_text(span) for span in cell.select("span")]
    for cell in row.select(".predQ .fj_column")
]
```
- **Settlement counterpart:** `src/slumdog/settlement.py:36-40` uses same selector `.predQ .fj_column` to extract numeric period scores for settled rows:
```python
for cell in row.select(".predQ .fj_column"):
    values = [_number(_text(span)) for span in cell.select("span")]
```
- **JSON field name:** `period_values` stored in `EventSnapshot.facets["period_values"]` (list of list of strings) — `parsers.py:180`

### 2. Listing / detail parser storing it

- **Listing parser:** `parse_html_events()` in `src/slumdog/parsers.py:122-223` — builds `facets["period_values"]`, timing `TimingClass.PRE_EVENT` at line 191
- **Filtering:** listing parser drops rows where `result_text` contains digit (`parsers.py:177-178`):
```python
if result_text and re.search(r"\d", result_text):
    continue
```
So only rows without final score enter pre-event set. period_values extracted from those rows.
- **Detail parser:** `detail_facets.py` does NOT parse period_values; detail facets are separate (shots, passes, etc.)
- **Football JSON:** football `getrs.php` does NOT emit period_values; football uses `Host_SC_HT`/`Guest_SC_HT` for halftime (RESULT_ONLY)

### 3. Event contract field

- **Field:** `EventSnapshot.facets["period_values"]` — `contracts.py: EventSnapshot.facets dict`
- **Type:** `list[list[str]]` e.g. `[["18","22"], ["20","20"]]` per `tests/test_parsers.py:47`
- **Timing map:** `EventSnapshot.facet_timing["period_values"] = PRE_EVENT` (parsers.py:191)
- **SettledEvent counterpart:** `SettledEvent.period_scores_1/2` tuple[float] — `contracts.py: SettledEvent` and `settlement.py: parse_html_settled` — NOT same key, but same DOM source for result rows

### 4. Feature-builder consuming

- **Basketball:** `src/slumdog/basketball.py:286-287` — `periods = facets.get("period_values") or []` → quarter margins `q1_margin_dog` etc.
- **American football:** `src/slumdog/american_football.py:265` — `quarter_values = facets.get("quarter_values") or facets.get("quarters") or facets.get("period_values")`
- **Hockey:** `src/slumdog/hockey.py:263` — `periods = facets.get("period_values") or []`
- **Rugby:** `src/slumdog/rugby.py:272-277`
- **Handball:** `src/slumdog/handball.py:275-281`
- **Volleyball:** `src/slumdog/volleyball.py:262-268`
- **Esports:** `src/slumdog/esports.py:257-263`
- **Football:** NOT consuming period_values (football uses `Host_SC_HT` which is RESULT_ONLY, blocked)

### 5. Sports used

- Basketball, American football, Hockey, Rugby, Handball, Volleyball, Esports, (also AFL if shares basketball logic) — see grep above.
- Football explicitly NOT using period_values as feature (uses halftime scores as RESULT_ONLY)

### 6. Whether predicted / completed / schedule / ambiguous

- **Ambiguous.** Two hypotheses:
  - **Predicted:** Forebet shows predicted quarter breakdown that sums to predicted_score (e.g., predicted 78-84 broken into Q1 18-22 etc.) — would be PRE_EVENT
  - **Completed:** Same DOM `.predQ` for settled rows holds actual quarter scores — settlement parser treats as RESULT_ONLY actuals
  - **Evidence gap:** Listing parser filters result rows, so period_values for upcoming events *could* be predicted, but no retained raw HTML in repo proves population for upcoming events vs settled. Test fixture `tests/test_parsers.py:11` has upcoming row (lscr_td empty) with period_values `[["18","22"]]` — that fixture was hand-crafted, not from frozen capture. No `data/raw/*/2026-08-22/*.txt` retained in repo to prove.
  - **Conclusion:** AMBIGUOUS until live Jina probe or frozen capture shows upcoming event with period_values populated before result, and proves it sums to predicted_score not final score.

### 7. Populated for upcoming events

- **Unknown.** `parsers.py` does extract period_values for upcoming rows, but we have no census of `depth-sweep` showing fill rate for upcoming vs settled.
- `docs/FOREBET_DETAIL_COVERAGE.json` (3-page sample) does not include listing period_values fill rate.
- `docs/FOREBET_DEPTH_AUDIT.md` does not quantify period_values.
- No `data/raw` sample in repo for basketball upcoming date.
- Need: Jina relay fetch of `https://www.forebet.com/en/basketball/predictions/<date>` for future date, inspect `.predQ` presence before FT, compare to predicted_score sum.

### 8. Settlement output flow into same facet key

- **No direct flow into same key.** Settlement writes `SettledEvent.period_scores_1/2` (numeric tuples) — `settlement.py:31-32`, not `facets["period_values"]`.
- However same DOM selector is reused for both pre-event and result parsing — risk of confusion if settlement period_scores were ever merged back into `facets["period_values"]` (currently not, but legacy `history.py` could conflate).
- `ledger.py` not examined — must verify no merging of settled period scores into training facets.

### 9. Tests covering

- `tests/test_parsers.py:47` asserts `event.facets["period_values"] == [["18","22"]]` for synthetic upcoming row
- `tests/test_basketball.py:65` provides `period_values` as input to feature extractor
- `tests/test_american_football.py:42`, `test_handball.py:45`, `test_hockey.py:42`, `test_rugby.py:45`, `test_volleyball.py:42`, `test_esports.py:42` — all inject period_values as facet and assert feature extraction
- No test proves timing (PRE_EVENT vs RESULT_ONLY) — tests use synthetic data, not frozen captures

### 10. Final timing

- **TIMING = UNKNOWN**
- **Evidence:** same DOM selector used for both predicted (upcoming) and actual (settled) period scores; listing parser claims PRE_EVENT but no retained bytes prove population for upcoming events; synthetic tests not proof; no Jina probe receipt.
- **Action:** Until live Jina HTML probe for basketball (or other sport) upcoming date shows `.predQ` populated and matches predicted_score sum (not final), keep UNKNOWN. New-path eligibility = PROHIBITED. Add to `FOREBET_DEPTH_AUDIT.md` as priority blocker. Do not use as feature in new path.

---

## Feature Inventory — Full Audit

Columns: Feature | Feature family | Sport | Source file/function | Raw source field | Timing | Evidence | Missing representation | Missing indicator | Odds-dependent | Legacy use | New-path eligibility | Action

> Note: Odds-dependent = YES → PROHIBITED per invariants regardless of timing. Missing odds irrelevant — must not gate.

### Forebet probabilities (core identity)

| Feature | Family | Sport | Source file/function | Raw source field | Timing | Evidence | Missing | Missing indicator | Odds-dependent | Legacy use | New-path | Action |
|---|---|---|---|---|---|---|---|---|---|---|---|
| probability_1 | Forebet probs | all | parsers.py: parse_html_events .fprc span, football.py: Pred_1 | .fprc span[0] / Pred_1 | PRE_EVENT | parsers.py:130-145, football JSON Pred_1; contracts.py EventSnapshot.probability_1 | None (row dropped if missing) | N/A (row filtered) | NO | Yes (facets.py, underdog.py) | ALLOWED | Keep as core identity |
| probability_2 | Forebet probs | all | parsers.py .fprc span[2] or Pred_2 | .fprc span[2] / Pred_2 | PRE_EVENT | same as above | None → row dropped | N/A | NO | Yes | ALLOWED | Keep |
| draw_probability | draw prob | football, handball, cricket, esoccer | parsers.py .fprc span[1], football Pred_X | .fprc span[1] / Pred_X | PRE_EVENT | parsers.py:132-136, football JSON Pred_X | None where sport two-way | draw_probability_missing flag in facets.py:192 | NO | Yes | ALLOWED | Keep as context, never selected |
| forebet_pick | Forebet probs | all | parsers.py .forepr span, football JSON max prob | .forepr span / max(Pred_1,Pred_2) | PRE_EVENT | parsers.py:147-148, parse_football_json: best | None | forebet_calls_dog boolean | NO | Yes (legacy underdog basis) | PARKED | Pick is prediction, not probability; use only as boolean indicator, not as identity; document |
| probability_gap | gap/ratio | all | underdog.py identify_forebet_underdog, facets.py | derived fav-underdog | PRE_EVENT (derived) | underdog.py: gap = fav_prob - dog_prob; facets.py: fav_prob - dog_prob | 0.0 if equal (ineligible) | None | NO | Yes | ALLOWED | Derived, no odds |
| probability_ratio | gap/ratio | all | facets.py build_numeric_features | dog_prob / fav_prob | PRE_EVENT derived | facets.py:74-75 | 0.0 if fav_prob 0 | None | NO | Yes | ALLOWED | Derived |
| entropy/dominance | entropy/dominance | all | facets.py _entropy, football.py shannon_entropy_3way, basketball.py shannon_entropy_2way | probabilities list | PRE_EVENT derived | facets.py:46-57, football.py:40-48 | 0.0 if missing | None | NO | Yes | ALLOWED | Derived |
| draw_pressure_ratio | gap/ratio | football | football.py extract_football_features | draw/(fav+dog) | PRE_EVENT derived | football.py:215 | 0.0 if denominator 0 | missing flag not needed (derived) | NO | Yes | ALLOWED | Context only |
| favorite_dominance_ratio | gap/ratio | all | football.py, basketball.py | fav / dog | PRE_EVENT derived | football.py:216, basketball.py:122 | 0.0 if dog 0 | None | NO | Yes | ALLOWED | Derived |

### Recent form / win rates

| Feature | Family | Sport | Source file/function | Raw source field | Timing | Evidence | Missing | Indicator | Odds-dep | Legacy | New-path | Action |
|---|---|---|---|---|---|---|---|---|---|---|---|
| recent_1_wins, recent_1_draws, recent_1_losses, recent_1_games | recent form | football | parsers.py promote_football_listing host_form | host_form array ['w','d','l'] | PRE_EVENT | parsers.py:235-248, _form_record | {} if not list | recent_1_games 0 | NO | Yes | ALLOWED | Pre-event L6 |
| recent_2_wins etc | recent form | football | same | guest_form | PRE_EVENT | same | {} | recent_2_games 0 | NO | Yes | ALLOWED |
| p1_form_wins, p1_form_losses, p1_form_draws | recent form | all (detail) | detail_facets.py _form_counts .prformcont | .prformcont .form_w/l/d count | PRE_EVENT | detail_facets.py:127-139 | 0 counts if missing | None (0 genuine?) | NO | Yes (common) | PARKED | Detail page — need timing proof that form on detail page is pre-event (likely yes, but verify) |
| p2_form_* | recent form | all | same | same | PRE_EVENT | same | 0 | None | NO | Yes | PARKED |
| dog_recent_win_rate, favorite_recent_win_rate | win rates | all | facets.py RecentForm.win_rate | wins/games | PRE_EVENT derived | contracts.py RecentForm.win_rate, basketball.py 312-315 | 0.0 if games 0 | dog_recent_games | NO | Yes | ALLOWED | Derived from recent form |
| dog_ppg, favorite_ppg, ppg_gap | recent form | football | football.py form_points_per_game | form_list ['w','d','l'] → 3*W+D / games | PRE_EVENT derived | football.py:55-71 | 0.0 if empty | dog_recent_games | NO | Yes | ALLOWED |
| l6_all_wins, l6_league_wins etc | recent form | football | detail_facets.py _football_lg_form | JSON lg_-1_6, lg_1_6 arrays | PRE_EVENT? | detail_facets.py:352-389 | missing if not found | sample missing flag | NO | Yes | UNKNOWN | Needs timing proof — embedded JSON on detail page, likely pre-event but verify |
| surface win rates | recent form | tennis | detail_facets.py _surface_records | {"clay":{"win":...}} JSON | PRE_EVENT? | detail_facets.py:141-162 | 0 sample | p1_clay_sample | NO | Yes | UNKNOWN | Needs timing proof |

### Table position / standings

| Feature | Family | Sport | Source file/function | Raw source field | Timing | Evidence | Missing | Indicator | Odds | Legacy | New-path | Action |
|---|---|---|---|---|---|---|---|---|---|---|---|
| standings_1, standings_2, standings_gap | table position | football | parsers.py promote_football_listing host_pos/guest_pos | host_pos, guest_pos strings "15th" | PRE_EVENT | parsers.py:249-255 | None if not parsed | missing flag in football.py | NO | Yes | ALLOWED | League position pre-event |
| standings_1_rank, standings_2_rank, standings_1_pts, gd, gp, wins, draws, losses, gf, ga | table position | all (detail) | detail_facets.py _standings_from_page table with PTS/GP headers | table row with team name + numbers | PRE_EVENT | detail_facets.py:100-147 | {} if not found | _missing in detail numeric | NO | Yes | PARKED | Detail page table — likely pre-event but need Jina proof that table is current standings not post-match |
| rank_gap, pts_gap, gd_gap, ppg_gap | table position | football/basketball | football.py _safe_float facets standings_* | derived | PRE_EVENT derived | football.py:294-317 | None if missing | _missing flag | NO | Yes | ALLOWED if base ALLOWED else PARKED |
| ladder_gap | table position | afl | feature_contracts.py | ladder | UNKNOWN | not yet parsed | UNKNOWN | UNKNOWN | NO | No | PARKED | Not implemented |

### H2H

| Feature | Family | Sport | Source | Raw | Timing | Evidence | Missing | Indicator | Odds | Legacy | New-path | Action |
|---|---|---|---|---|---|---|---|---|---|---|---|
| h2h_total_games, h2h_participant_1_wins, h2h_participant_2_wins, h2h_draws | H2H | all | detail_facets.py _h2h_from_page head to head text or .h2h table | "head to head" section text | PRE_EVENT | detail_facets.py:56-108 | {} if no H2H section | h2h_present boolean | NO | Yes | PARKED | H2H on detail page — likely pre-event but need proof not including current match |
| h2h_dog_win_rate, h2h_draw_rate, h2h_dog_undefeated_rate, h2h_has_dog_win | H2H | all | facets.py H2HStats.wins/total, football.py | derived | PRE_EVENT derived | facets.py, football.py:328-333 | 0.0 if no games | h2h_total_games | NO | Yes | PARKED if base PARKED else ALLOWED | Depends on base H2H timing |
| period_win_rates, half_rates | H2H | basketball etc | contracts.py H2HStats period_win_rates | detail? | UNKNOWN | contracts.py H2HStats has period_win_rates but no parser fills it? Search parsers | 0.0 | None | NO | Yes (basketball uses period_rates) | UNKNOWN | Trace where period_win_rates populated — likely from period_values which is UNKNOWN → PROHIBITED until resolved |

### Goals / points scored / conceded

| Feature | Family | Sport | Source | Raw | Timing | Evidence | Missing | Indicator | Odds | Legacy | New-path | Action |
|---|---|---|---|---|---|---|---|---|---|---|---|
| host_sc_pr, guest_sc_pr, goalsavg | goals/points scored/conceded | football | parsers.py promote_football_listing | host_sc_pr, guest_sc_pr, goalsavg from JSON | PRE_EVENT | parsers.py:257-262 | None | predicted_total_missing | NO | Yes | ALLOWED | Predicted scores, pre-event |
| predicted_score, predicted_total | goals/points | all | parsers.py .scrmobpred, .avg_sc, football JSON host_sc_pr+guest_sc_pr | .scrmobpred text, .avg_sc number | PRE_EVENT | parsers.py:149-150, football.py 248-252 | None | predicted_total_missing | NO | Yes | ALLOWED | Forebet prediction |
| p1_scored_avg, p1_conceded_avg, p2_... | goals/points | football/basketball (detail) | detail_facets.py _goal_avgs .os_goals_section1_child | 8 numbers: p1 scored, avg, conceded, avg, p2... | PRE_EVENT? | detail_facets.py:189-209 | {} if len<8 | missing flag | NO | Yes | PARKED | Detail page overall-statistics — likely pre-event season averages but need timing proof |
| p1_scored_avg, p2_scored_avg etc | goals/points | basketball | basketball.py facets p1_scored_avg detail | detail_p1_scored_avg | PRE_EVENT? | basketball.py:355-360 | None | _missing | NO | Yes | PARKED | Same as above |

### Shots / shots on target / blocked / off-target

| Feature | Family | Sport | Source | Raw | Timing | Evidence | Missing | Indicator | Odds | Legacy | New-path | Action |
|---|---|---|---|---|---|---|---|---|---|---|---|
| total_shots, blocked, on_target_pct, off_target_pct, inside_box_pct, avg | shots | football (detail) | detail_facets.py _football_shots | text "Total shots <n> <avg> Blocked <n> ... OFF target ... ON target ... Inside box" | PRE_EVENT? | detail_facets.py:271-312 | missing if NAN% → None per _parse_pct | _missing flag | NO | Yes | PARKED | Detail page stats — likely pre-event season averages but need Jina proof that it's not live/post |
| shots_on_target_pct | shots on target | football | same | same | PRE_EVENT? | same | None if NAN literal | _missing | NO | Yes | PARKED |
| blocked/off-target | blocked/off-target | football | same | Blocked counts | PRE_EVENT? | same | None | _missing | NO | Yes | PARKED |

### Possession

| Feature | Family | Sport | Source | Raw | Timing | Evidence | Missing | Indicator | Odds | Legacy | New-path | Action |
|---|---|---|---|---|---|---|---|---|---|---|---|
| possession_pct | possession | football (detail) | detail_facets.py _football_passes | "Ball Possession <pct>%" | PRE_EVENT? | detail_facets.py:314-341 | None | _missing | NO | Yes | PARKED | Detail season avg possession — need timing proof |

### Passes accuracy

| Feature | Family | Sport | Source | Raw | Timing | Evidence | Missing | Indicator | Odds | Legacy | New-path | Action |
|---|---|---|---|---|---|---|---|---|---|---|---|
| passes_total, passes_avg, passes_accurate, passes_accuracy_pct | passes accuracy | football (detail) | detail_facets.py _football_passes | "Total <n> Avg. per game <avg> Accurate <n> <pct>%" | PRE_EVENT? | same | None | _missing | NO | Yes | PARKED |

### Attacks / dangerous attacks

| Feature | Family | Sport | Source | Raw | Timing | Evidence | Missing | Indicator | Odds | Legacy | New-path | Action |
|---|---|---|---|---|---|---|---|---|---|---|---|
| total_attacks_total/avg, dangerous_attacks_total/avg | attacks | football (detail) | detail_facets.py _football_attacks | "Total attacks" section | PRE_EVENT? | detail_facets.py:343-371 | None | _missing | NO | Yes | PARKED |

### Event-time

| Feature | Family | Sport | Source | Raw | Timing | Evidence | Missing | Indicator | Odds | Legacy | New-path | Action |
|---|---|---|---|---|---|---|---|---|---|---|---|
| first_goal_min, first_corner_min, first_card_min | event-time | football (detail) | detail_facets.py _football_event_times | "Avg. event time" block | PRE_EVENT? | detail_facets.py:373-390 | None | _missing | NO | Yes | PARKED | Avg minute of first goal — season average, likely pre-event but verify not post |

### Schedule difficulty

| Feature | Family | Sport | Source | Raw | Timing | Evidence | Missing | Indicator | Odds | Legacy | New-path | Action |
|---|---|---|---|---|---|---|---|---|---|---|---|
| next_fixtures_count, next_difficulty_avg | schedule difficulty | football (detail) | detail_facets.py _football_next_difficulty | "Next matches" section difficulty ratings 1-5 | PRE_EVENT | detail_facets.py:416-445 | None | _missing | NO | Yes | PARKED | Upcoming fixtures difficulty — plausible PRE_EVENT but needs proof it's future not past |
| travel_distance_km | schedule difficulty | all | detail_facets.py _distance_and_weather | "(\d+) km" text | PRE_EVENT? | detail_facets.py:195-211 | None | _missing | NO | Yes | PARKED | Distance from prose — likely pre-event |
| rest_days, schedule_density | schedule difficulty | basketball etc | feature_contracts.py mentions but not parsed | UNKNOWN | UNKNOWN | not in parsers | UNKNOWN | UNKNOWN | NO | No | PARKED | Not implemented |

### Weather / venue

| Feature | Family | Sport | Source | Raw | Timing | Evidence | Missing | Indicator | Odds | Legacy | New-path | Action |
|---|---|---|---|---|---|---|---|---|---|---|---|
| weather_temperature_c, weather_high, low, code | weather | football | parsers.py promote_football_listing weather_* + detail_facets _distance_and_weather | weather_high/low/code, temp_f | PRE_EVENT | parsers.py:249-262, detail_facets.py:195-211 | None | _missing | NO | Yes | ALLOWED (high/low/code/temp_f) | Weather pre-event |
| stadium | venue | football | parsers.py host_stadium | host_stadium string | PRE_EVENT | parsers.py:277-279 | "" if missing | None | NO | Yes | ALLOWED | Venue pre-event |
| weather_present, distance_present | weather/venue | football detail | detail_facets.py sections presence booleans | lower text contains "weather", "distance" | PRE_EVENT | detail_facets.py:230-240 | false if not present | boolean itself | NO | Yes | PARKED | Presence flag — likely pre-event |

### Stable IDs

| Feature | Family | Sport | Source | Raw | Timing | Evidence | Missing | Indicator | Odds | Legacy | New-path | Action |
|---|---|---|---|---|---|---|---|---|---|---|---|
| participant_1_id, participant_2_id, league_id | stable IDs | football | parsers.py parse_football_json host_id, guest_id, league_id | host_id, guest_id, league_id | PRE_EVENT | parsers.py:393-403, test_parsers.py:134-136 | "" if missing | None | NO | Yes | ALLOWED | Stable IDs pre-event |
| league_code, shortTag | stable IDs | all | parsers.py league | .shortTag text | PRE_EVENT | parsers.py:163 | "" | None | NO | Yes | ALLOWED |

### Cup flags

| Feature | Family | Sport | Source | Raw | Timing | Evidence | Missing | Indicator | Odds | Legacy | New-path | Action |
|---|---|---|---|---|---|---|---|---|---|---|---|
| isCup, is_international_club_cup, is_nationalteam_cup | cup flags | football | parsers.py promote_football_listing isCup | isCup "0"/"1" | PRE_EVENT | parsers.py:287-291 | None | 0.0/1.0 | NO | Yes | ALLOWED | Cup flag pre-event |
| code, host_short, guest_short | cup flags / venue | football | parsers.py code | code, host_short | PRE_EVENT | parsers.py:285 | "" | None | NO | Yes | ALLOWED |

### Trend text

| Feature | Family | Sport | Source | Raw | Timing | Evidence | Missing | Indicator | Odds | Legacy | New-path | Action |
|---|---|---|---|---|---|---|---|---|---|---|---|
| trend_en | trend text | football | parsers.py trend dict en | trend.en English sentence | PRE_EVENT | parsers.py:308-314 | None if missing | missing_evidence list | NO | Yes | PARKED | Text trend requires deliberate auditable representation; do not casually add opaque embeddings — per Milestone 3 rules, park until representation approved |
| trend_raw | trend text | football | same | full dict | PRE_EVENT | same | None | same | NO | Yes | PROHIBITED | Raw multilingual dict — opaque, prohibited |

### Double chance

| Feature | Family | Sport | Source | Raw | Timing | Evidence | Missing | Indicator | Odds | Legacy | New-path | Action |
|---|---|---|---|---|---|---|---|---|---|---|---|
| doublechance_prob, doublechance_pick, doublechance_pick_raw, doublechance_pick_price_am | double chance | football detail | detail_facets.py _football_dom_tab_markets #dbc_table .rcnt | .fprc .fpr, .predict .forepr, .prmod .lscrsp | PRE_EVENT | detail_facets.py:488-524 | None if not single row | _missing | YES? price_am is odds-dependent | Yes (detail_facets) | PROHIBITED for price_am, PARKED for prob/pick | doublechance_pick_price_am is American odds → odds-dependent → PROHIBITED; prob/pick is Forebet prediction, plausible PRE_EVENT but needs DOM proof and should be parked until double-chance semantics validated |

### Goalscorer predictions

| Feature | Family | Sport | Source | Raw | Timing | Evidence | Missing | Indicator | Odds | Legacy | New-path | Action |
|---|---|---|---|---|---|---|---|---|---|---|---|
| goalscorer_1_name, _prob, _price_am etc | goalscorer predictions | football detail | detail_facets.py _football_dom_tab_markets #gscr_table .rcnt | .fprc > .playerPred, .predict .forepr .playerPred, .prmod .lscrsp | PRE_EVENT | detail_facets.py:526-565 | None if not single row | _missing | price_am YES odds-dependent | Yes | PROHIBITED for price, PARKED for name/prob | Goalscorer price is odds → PROHIBITED; name/prob is Forebet prediction, plausible PRE_EVENT but needs validation, park |

### Sport-specific physical / stat facets

| Feature | Family | Sport | Source | Raw | Timing | Evidence | Missing | Indicator | Odds | Legacy | New-path | Action |
|---|---|---|---|---|---|---|---|---|---|---|---|
| height, weight, reach, stance, strikes, takedowns, submissions, control_time, record wins/losses/draws, win_rate | sport-specific physical/stat | mma | detail_facets.py _mma_fields | regex record of (\d+)-(\d+)-(\d+), (\d+)' (\d+)", (\d+) lbs, Reach, stance | PRE_EVENT? | detail_facets.py:164-193 | 0.0 if not found | _sample flags | NO | Yes | PARKED | Tale-of-the-tape — likely pre-event but need Jina proof |
| surface splits clay/hard/grass win_rate/sample | sport-specific | tennis | detail_facets.py _surface_records | JSON {"clay":{"win":...}} | PRE_EVENT? | detail_facets.py:141-162 | 0 | sample flag | NO | Yes | UNKNOWN → PROHIBITED until verified | Needs timing proof |
| quarter_data_present, period_data_present, overtime_present, hits_present, innings_present, set_data_present, etc | sport-specific | various detail | detail_facets.py presence booleans | lower text contains "q1" etc | PRE_EVENT? | detail_facets.py:242-268 | false | boolean | NO | Yes | PARKED | Presence flags — likely pre-event but park |

### Price / odds / overround / fair prob / value-edge

| Feature | Family | Sport | Source | Raw | Timing | Evidence | Missing | Indicator | Odds | Legacy | New-path | Action |
|---|---|---|---|---|---|---|---|---|---|---|---|
| odds_1, odds_2, odds_draw, odds_1_am, odds_draw_am, odds_2_am, best_odd_1, best_odd_X, best_odd_2, haodd, lscrsp, selected_odds_raw | price/odds | all | parsers.py _participant_odds .haodd span, .lscrsp | .haodd span American/decimal | PRE_EVENT (displayed) | parsers.py:71-108 | None/"-" → None | price_available flag facets.py:77 | YES | Yes | PROHIBITED | Odds are optional metadata only per invariants, must not be model feature, not eligibility gate, missing must not lower confidence — see AGENTS.md. Explicitly prohibited in new path. |
| market_overround, dog_fair_implied_prob, favorite_fair_implied_prob, draw_fair_implied_prob, price_value_edge, displayed_odds, implied_probability, overround, devig probabilities | price/odds | all | football.py calculate_overround, devig_probabilities_3way, basketball.py calculate_overround_2way | derived from odds | PRE_EVENT derived but odds-dependent | football.py:27-49, basketball.py:28-46 | None if odds missing | _missing flag | YES | Yes | PROHIBITED | Derived from odds → odds-dependent → PROHIBITED |
| market_uo_pr_over, pr_under, odds_under_over, best_over/under, market_bts_Pred_gg, market_ht_Pred_1_HT, market_ah_AH_type, market_cards_avg_cards, market_corners_avg_corners etc | price/odds | football | forebet.py FOOTBALL_MARKETS + parsers.py _merge_football_markets | JSON markets uo/bts/ht/ah/cards distinct endpoints | PRE_EVENT | forebet.py:107-138, parsers.py:350-380 | None if missing | _missing | Mixed: pr_over etc are Forebet prob predictions (ALLOWED if not odds), odds_* are odds (PROHIBITED) | Yes | SPLIT | pr_over, Pred_gg, Pred_1_HT etc are Forebet probabilities → ALLOWED as prediction context; odds_under_over, best_over, odds_gg_y etc are odds → PROHIBITED. Need per-key split. |
| corners_p1_prob, cards_p1_prob etc (from text) | price/odds? | football detail | detail_facets.py _football_tab_markets | "(\d+) (\d+) Over <pred> <line> Corners" | PRE_EVENT? | detail_facets.py:430-482 | None | _missing | NO? Actually prob is Forebet prob, not odds | Yes | PARKED | Corners/cards prob is Forebet prediction, plausible PRE_EVENT but needs DOM proof, park until verified |

### Final / period / penalty / extra-time / disposition / settlement

| Feature | Family | Sport | Source | Raw | Timing | Evidence | Missing | Indicator | Odds | Legacy | New-path | Action |
|---|---|---|---|---|---|---|---|---|---|---|---|
| score_1, score_2, winner_index, period_scores_1/2, extra_time_score, penalty_score, Host_SC_HT, Guest_SC_HT, disposition, status, comment, lscr_td, FT/AOT/AP/FINAL | final/period/penalty/extra-time/disposition/settlement | all | settlement.py parse_html_settled, parse_football_settled | .lscr_td span scores, comment FT etc, Host_SC, Guest_SC, Host_SC_HT | RESULT_ONLY | settlement.py:12-40, 60-120, contracts.py SettledEvent | None if void | N/A | NO | Yes (settlement) | PROHIBITED | RESULT_ONLY → prohibited as feature, only for label |
| result, live_score, status (generic) | final | all | facets.py COMMON_FACETS | result, live_score | RESULT_ONLY/LIVE_ONLY | facets.py:26-33 | None | N/A | NO | Yes | PROHIBITED |
| period_values (settled usage) | final/period | all | settlement.py parse_html_settled period_values as actual quarter scores | .predQ .fj_column numeric | RESULT_ONLY when from settled rows | settlement.py:31-40 | [] | N/A | NO | Yes | PROHIBITED | Same selector as pre-event ambiguous — settled usage is RESULT_ONLY |

---

## Missingness Audit

Per task: for every feature audit None/NaN/0/empty string/absent key/sentinel text and zero fallback classification.

**Classification:**
- GENUINE_ZERO — true zero (e.g., 0 wins, 0 goals, 0 dangerous attacks when team truly had 0)
- UNKNOWN_ENCODED_AS_ZERO — missing encoded as 0.0 fallback (legacy facets.py:70-78 or 0.0 in to_dict)
- SAFE_MATHEMATICAL_DEFAULT — safe default for math (e.g., ratio denominator guard 0.01, entropy 0.0 when no probs)
- UNRESOLVED — not yet classified

**Findings from code:**

- `facets.py:70-78` — `event.pre_event_facets().items()` → `_finite(value)` → if None → `feature_key_missing=1.0` and no numeric feature emitted (good), but sport-specific extractors (football.py, basketball.py) do `float(val) if val is not None else 0.0` with `_missing` flag = 1.0 — this is UNKNOWN_ENCODED_AS_ZERO (0.0 fallback with missing indicator). Must be preserved as pattern: missing indicator + 0.0 fallback, but new path should use None/NaN + indicator + imputation inside pipeline, not global 0.0 change now.
- `detail_facets.py` — missing stays missing (returns {} if not found, never zero-fill) — good, but `_football_shots` treats literal "NAN%" as missing via `_parse_pct` returning None — correctly not zero-filled (GENUINE missing handling)
- `parsers.py: _number()` returns None if no number — good, not zero
- `parsers.py: promote_football_listing` only sets facet if `_number` not None — good
- `football.py: form_points_per_game` returns 0.0,0.0... if empty list — this is UNKNOWN_ENCODED_AS_ZERO (0 ppg when no form) — should be GENUINE_ZERO? No, 0 ppg when no games is not genuine, it's missing. Should be classified as UNKNOWN_ENCODED_AS_ZERO.
- `basketball.py: quarter_margins` — if period_values empty, margins stay None → missing flag 1.0 + 0.0 fallback → UNKNOWN_ENCODED_AS_ZERO
- `facets.py: _entropy` — returns 0.0 if total<=0 — SAFE_MATHEMATICAL_DEFAULT
- `football.py: draw_pressure_ratio` — 0.0 if denominator 0 — SAFE_MATHEMATICAL_DEFAULT
- `football.py: dominance` — fav / max(0.01, dog) — SAFE_MATHEMATICAL_DEFAULT (0.01 guard)
- Odds: `decimal_odds` returns None for "-" — good, missing stays None, not zero
- Price fields in feature dicts: `price_available` 0.0/1.0 flag + 0.0 fallback for missing price — UNKNOWN_ENCODED_AS_ZERO (but prohibited anyway)
- Settlement: score_1/score_2 None if void — good, not zero

**Required action for new path:**
- Keep missing representation as currently: None/0.0 + explicit `_missing` flag where already implemented
- Do not globally change zero-fill in this audit (per Milestone 6 requirement, defensive copy/immutable structures before receipts)
- For future pipeline: convert to None/NaN + missing indicator + imputation inside pipeline (not at extraction), per Milestone 1 audit gap 4 correction
- Classify each 0.0 fallback in feature table above as UNKNOWN_ENCODED_AS_ZERO unless proven genuine (e.g., h2h_dog_win_rate 0.0 when h2h_total_games 0 is UNKNOWN_ENCODED_AS_ZERO, not genuine 0% win rate)

**Missingness table excerpt:**

| Feature | Raw missing | Code fallback | Classification |
|---|---|---|---|
| probability_1 | row dropped | N/A | N/A (row filtered) |
| draw_probability | None (two-way) | 0.0 + missing flag in facets.py | UNKNOWN_ENCODED_AS_ZERO (but allowed as context) |
| recent form counts | {} | 0 wins/games | UNKNOWN_ENCODED_AS_ZERO (should be missing indicator) |
| p1_form_wins | no .prformcont | 0 | UNKNOWN_ENCODED_AS_ZERO |
| standings rank | no table | None → missing flag 1.0 + 0.0 | UNKNOWN_ENCODED_AS_ZERO |
| h2h wins | no H2H section | 0 + present boolean | UNKNOWN_ENCODED_AS_ZERO (presence flag helps) |
| total_shots | no "Total shots" label | None → missing flag | GENUINE missing handled (not zero-filled) |
| possession | no label | None | GENUINE missing (not zero-filled) |
| period_values | no .predQ | [] → margins None → 0.0 fallback | UNKNOWN_ENCODED_AS_ZERO + UNKNOWN timing → PROHIBITED |
| odds | "-" or missing | None | GENUINE missing (not zero) but PROHIBITED as feature |
| score_1 | void | None | GENUINE missing (void) |

---

## New-Path Eligibility Summary

**ALLOWED (PRE_EVENT proven, not odds-dependent):**
- probability_1, probability_2, draw_probability (context, never selected), probability_gap, ratio, entropy, dominance, draw_pressure_ratio
- predicted_score, predicted_total, goalsavg, host_sc_pr, guest_sc_pr
- recent form L6 counts from football JSON host_form/guest_form (pre-event proven via getrs.php)
- standings_1/2/gap from host_pos/guest_pos (pre-event)
- weather_high/low/code/temp_f, stadium, participant IDs, league_id, isCup flags, code, host_short/guest_short, move_1/X/2 (odds movement direction? move is not odds value, it's direction "no/up/down" — ALLOWED as metadata, not odds), trend_en PARKED (text)
- league_code, round_number, kelly? kelly is? Kelly criterion? Might be odds-derived — need check: kelly in football JSON is likely betting metric → odds-dependent? Treat as PARKED until proven not odds-derived

**PARKED (plausible PRE_EVENT but needs Jina/bytes proof or representation decision):**
- All detail_facets: shots, possession, passes, attacks, event times, uo/btts counts, next difficulty, travel distance, h2h from detail, standings detail table, goal avgs, clean sheets, corners, cards, fouls, tackles, etc. — need Jina HTML probe proving section exists on upcoming detail page before kickoff
- Surface records tennis, MMA tale-of-the-tape, etc.
- Double-chance prob/pick, corners/cards prob, goalscorer prob/name
- l6_all/league wins from lg_-1_6 JSON (likely pre-event but verify)
- trend_en text (needs deliberate representation)
- kelly, round_or_stage if not odds-derived

**PROHIBITED:**
- period_values (UNKNOWN timing) — until 10-point proof, new-path PROHIBITED
- All odds fields: odds_1, odds_2, odds_draw, am variants, best_odd_*, haodd, lscrsp, selected_odds_raw, market odds_*, overround, fair implied prob, value edge, displayed_odds, implied_probability, price_available as feature (price_available is odds-dependent flag)
- All RESULT_ONLY: score_1/2, winner_index, period_scores, extra_time_score, penalty_score, Host_SC_HT, Guest_SC_HT, disposition, result, live_score, status, comment, FT/AOT/AP, etc.
- trend_raw (opaque dict)
- Any field with timing UNKNOWN per this doc

**Odds-dependent explicitly prohibited (even if PRE_EVENT):**
- See list above — odds are optional metadata only, never model feature, per AGENTS.md invariants. Missing odds irrelevant.

---

## Evidence Citations

- `src/slumdog/parsers.py:170-173` — period_values DOM selector
- `src/slumdog/parsers.py:191` — timing claim PRE_EVENT for period_values (but not proven by retained bytes)
- `src/slumdog/settlement.py:31-40` — same selector used for RESULT_ONLY actual period scores
- `src/slumdog/basketball.py:286-287` — feature builder consuming period_values
- `src/slumdog/football.py:27-49` — overround/devig odds-dependent calculations (prohibited)
- `src/slumdog/facets.py:70-78` — missingness handling _finite + _missing flag
- `src/slumdog/detail_facets.py:271-312` — shots extraction with NAN% handling (_parse_pct returns None, not zero)
- `src/slumdog/detail_facets.py:488-524` — double-chance DOM-scoped extraction #dbc_table .rcnt
- `src/slumdog/detail_facets.py:526-565` — goalscorer DOM-scoped extraction #gscr_table .rcnt
- `src/slumdog/contracts.py:EventSnapshot` — pre_event_facets() filters by facet_timing PRE_EVENT only
- `src/slumdog/sports.py` — draw_possible registry used for label contract (football True, basketball False etc.)
- `tests/test_parsers.py:47` — synthetic test for period_values (not proof of timing)
- `docs/FOREBET_DEPTH_AUDIT.md` — no period_values fill-rate census (gap)
- `docs/FOREBET_DETAIL_COVERAGE.json` — 3-page sample, no period_values timing proof

---

## Actions

1. **period_values:** Keep UNKNOWN, PROHIBITED new-path until Jina probe. Add probe task: fetch basketball predictions for future date via relay, inspect .predQ presence, compare sum to predicted_score vs final score, capture receipt in `data/raw/basketball/<date>/` and document.
2. **Detail facets:** For each PARKED facet, need Jina HTML detail page for upcoming match (before kickoff) — prove section text exists, extract via detail_facets.py, retain bytes, document timing PRE_EVENT. Prioritize football shots/passes/possession/attacks (most valuable per Milestone 3 rules).
3. **Odds:** Ensure new-path training never uses odds fields — add guard in `facets.py` build_numeric_features to exclude odds-dependent features when building price-free vector (future code change, not in this audit).
4. **Missingness:** Document classification per feature as above; future pipeline to use None/NaN + indicator + imputation inside pipeline, not global 0.0 change now (Milestone 6 requirement: defensive copy before immutable receipts).
5. **Training:** Remains FROZEN. No feature-vector changes until this contract approved.

---

## Verification

- `python -m pytest -q` — 232 tests passing (40 new price-free + 192 legacy) — training frozen, no code change in this doc-only milestone
- `python -m pyflakes src/slumdog/underdog.py` — ok
- `git diff --check` — ok
- `docs/STATE.md` updated to Milestone 2 COMPLETE, Milestone 3 CURRENT
- `docs/README.md` updated with FEATURE_TIMING_CONTRACT.md as CURRENT
- `HANDOFF.md` updated with Milestone 3 audit phase

---

## Contract Notes for Later (from Milestone 2E)

- **Nested mutability:** frozen dataclass with mutable dicts optional_price_context/rejection_counts/source_receipt not deeply immutable, must be defensively copied/immutable structures/frozen by ledger before immutable receipts (Milestone 6), record only — do not add generalized freezing now.
- **Status semantics:** STRONG_UNDERDOG must not imply approved probability until scoring/thresholds approved; tests may construct status for serialization, operational code must not emit, reserved fields remain None, no report presents baseline strength as calibrated probability.
