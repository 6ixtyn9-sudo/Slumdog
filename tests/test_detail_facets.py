from slumdog.detail_facets import parse_detail


def test_tennis_surface_records_are_sport_specific():
    body = b'''<html><body><h2>Last 6 matches</h2><div class="prformcont"><i class="form_w"></i></div>
    {"clay":{"win":20,"lost":10,"total":30},"hard":{"win":10,"lost":10,"total":20},"grass":{"win":2,"lost":3,"total":5}}
    {"clay":{"win":5,"lost":15,"total":20},"hard":{"win":8,"lost":12,"total":20},"grass":{"win":1,"lost":4,"total":5}}
    Height</body></html>'''
    facets = parse_detail(body, "tennis")
    assert facets.sport_specific["p1_clay_win_rate"] == 20/30
    assert facets.sport_specific["p2_clay_win_rate"] == 5/20
    assert facets.sport_specific["height_present"] is True
    assert "p1_hard_sample" not in facets.missing


def test_mma_parser_does_not_reuse_team_sport_contract():
    body = b'''<html><body><h2>Fighters info</h2>
    Fighter A brings a record of 14-2-0 at 6'2" and 248 lbs.
    Fighter B boasts a record of 18-6-0 at 6'3" and 251 lbs.
    Reach Stance Orthodox Southpaw Average Strikes Per Fight Takedowns Submissions Control time
    </body></html>'''
    facets = parse_detail(body, "mma")
    assert facets.sport_specific["p1_record_win_rate"] == 14/16
    assert facets.sport_specific["p2_record_win_rate"] == 18/24
    assert facets.sport_specific["p1_height_inches"] == 74
    assert facets.sport_specific["strikes_present"] is True
    assert facets.common["h2h_present"] is False


def test_football_detail_contract_keeps_football_only_fields():
    body = b'''<html><body><h2>Head to head</h2><h3>Team Last 6 matches</h3>
    Weather conditions HT/FT Probability Avg. corners Cards score Both teams scored
    Straight line distance 510 km Fouls Tackles Ball possession
    </body></html>'''
    facets = parse_detail(body, "football")
    assert facets.sport_specific["weather_present"] is True
    assert facets.sport_specific["htft_present"] is True
    assert facets.sport_specific["corners_present"] is True
    assert facets.sport_specific["cards_present"] is True
    assert facets.sport_specific["btts_present"] is True
    assert facets.sport_specific["distance_present"] is True
    assert facets.sport_specific["fouls_present"] is True
    assert facets.sport_specific["tackles_present"] is True
    assert facets.sport_specific["possession_present"] is True
    assert facets.common["travel_distance_km"] == 510.0


def test_detail_form_and_h2h_become_pipeline_keys():
    body = b'''<html><body>
    <h2>Head to head</h2>
    <div>Alpha 4 wins Beta 1 wins 2 draws</div>
    <h3>Last 6 matches</h3>
    <div class="prformcont"><i class="form_w"></i><i class="form_w"></i><i class="form_l"></i></div>
    <div class="prformcont"><i class="form_w"></i><i class="form_l"></i><i class="form_d"></i></div>
    </body></html>'''
    facets = parse_detail(body, "basketball")
    assert facets.common["recent_1_wins"] == 2
    assert facets.common["recent_1_games"] == 3
    assert facets.common["recent_2_wins"] == 1
    assert facets.common["recent_2_games"] == 3
    assert facets.common["h2h_participant_1_wins"] == 4
    assert facets.common["h2h_participant_2_wins"] == 1
    assert facets.common["h2h_total_games"] == 7


def test_detail_standings_goals_and_named_h2h_are_numeric():
    body = b'''<html><body>
    <h2>Head to head</h2>
    <table><tr><td>04/25 2026 SL Benfica 4 - 1 (2 - 1) Moreirense FC Pt1</td></tr>
    <tr><td>12/14 2025 Moreirense FC 0 - 4 (0 - 1) SL Benfica Pt1</td></tr>
    <tr><td>02/08 2025 SL Benfica 3 - 2 (3 - 1) Moreirense FC Pt1</td></tr>
    <tr><td>08/30 2024 Moreirense FC 1 - 1 (0 - 0) SL Benfica Pt1</td></tr></table>
    <table>
      <tr><th>REGULAR SEASON</th><th>PTS</th><th>GP</th><th>W</th><th>D</th><th>L</th><th>GF</th><th>GA</th><th>+/-</th></tr>
      <tr><td>7</td><td>SL Benfica</td><td>4</td><td>2</td><td>1</td><td>1</td><td>0</td><td>9</td><td>2</td><td>7</td></tr>
      <tr><td>15</td><td>Moreirense FC</td><td>1</td><td>2</td><td>0</td><td>1</td><td>1</td><td>2</td><td>6</td><td>-4</td></tr>
    </table>
    <table>
      <tr><td>3.83</td><td>23</td><td>Corners</td><td>55</td><td>7.86</td></tr>
      <tr><td>3.17</td><td>19</td><td>Yellow cards</td><td>7</td><td>1</td></tr>
    </table>
    <div class="os_goals_section1_child">Scored 6</div>
    <div class="os_goals_section1_child">Avg. per game 1</div>
    <div class="os_goals_section1_child">Conceded 14</div>
    <div class="os_goals_section1_child">Avg. per game 2.33</div>
    <div class="os_goals_section1_child">Scored 25</div>
    <div class="os_goals_section1_child">Avg. per game 3.57</div>
    <div class="os_goals_section1_child">Conceded 7</div>
    <div class="os_goals_section1_child">Avg. per game 1</div>
    </body></html>'''
    facets = parse_detail(body, "football", "Moreirense FC", "SL Benfica")
    assert facets.common["h2h_participant_1_wins"] == 0
    assert facets.common["h2h_participant_2_wins"] == 3
    assert facets.common["h2h_draws"] == 1
    assert facets.common["h2h_total_games"] == 4
    assert facets.common["standings_1_rank"] == 15
    assert facets.common["standings_2_pts"] == 4
    assert facets.common["standings_gap"] == 8
    assert facets.common["p1_corners_avg"] == 3.83
    assert facets.common["p2_yellow_cards_avg"] == 1
    assert facets.common["p1_scored_avg"] == 1
    assert facets.common["p2_scored_avg"] == 3.57


def test_football_matches_real_page_labels_not_just_prose():
    # Real Forebet detail pages label these as "ht/ft btts" menus and
    # "avg. corners"; the parser must not require "probability"/"scored".
    body = b'''<html><body><h2>Match info</h2>
    <div>1X2 Under/Over 2.5 Half time HT/FT BTTS Double handicap Corners Cards</div>
    <div>Prediction Correct score Avg. goals Weather conditions Coef.</div>
    </body></html>'''
    facets = parse_detail(body, "football")
    assert facets.sport_specific["htft_present"] is True
    assert facets.sport_specific["btts_present"] is True
    assert facets.sport_specific["corners_present"] is True
    assert facets.sport_specific["cards_present"] is True
    assert facets.sport_specific["weather_present"] is True
    assert facets.missing == []


def test_football_numeric_detail_stats_from_observed_page():
    # Text mirrors the verified Brentford v Tottenham detail page (22/08/2026)
    # and the UNAM Pumas v Necaxa top-of-page tabs (23/08/2026).
    body = b"""<html><body>
    Shots BRE Total shots 68 11.33 Blocked 17 2.83 49% OFF target 31% ON target 74% Inside box 26% Outside box
    TOT Total shots 79 13.17 Blocked 23 3.83 56% OFF target 27% ON target 73% Inside box 27% Outside box
    Passes Total 2305 Avg. per game 384.17 Accurate 1856 81% Ball Possession 48%
    Total 2577 Avg. per game 429.5 Accurate 2172 84% Ball Possession 53%
    Avg. event time First goal 68' First corner 30' First card 0'
    Total attacks Brentford 565 Avg. 94.17 Tottenham 563 Avg. 93.83
    Dangerous attacks Brentford 303 Avg. 50.5 Tottenham 269 Avg. 44.83
    Under/Over 1 5 17% 83% 1.5 Goals Under/Over 2 4 33% 67% 2.5 Goals Under/Over 3 3 50% 50% 3.5 Goals
    Both scored Yes 3 50% 50% No 3
    Both scored Yes 4 67% 33% No 2
    next matches Easy 1 Severe 5 BRE 2 2 3 2 4 3 TOT 3 2 3 2 2 4
    {"lg_-1":[1,3,2,6],"lg_-1_6":[1,3,2,6],"lg_1":[1,3,2,6],"lg_1_6":[1,3,2,6]}
    {"lg_-1":[3,2,1,6],"lg_-1_6":[3,2,1,6],"lg_1":[3,2,1,6],"lg_1_6":[3,2,1,6]}
    46 54 Over 5-6 5 - 6 9.57 Corners 44 56 Over 1-4 1 - 4 5.04 Cards
    71% 12 2-0
    </body></html>"""
    facets = parse_detail(body, "football", "Brentford", "Tottenham")
    s = facets.sport_specific
    assert s["p1_shots_total"] == 68
    assert s["p1_shots_avg"] == 11.33
    assert s["p1_shots_on_target_pct"] == 31
    assert s["p2_shots_inside_box_pct"] == 73
    assert s["p1_passes_total"] == 2305
    assert s["p1_passes_accuracy_pct"] == 81
    assert s["p2_possession_pct"] == 53
    assert s["p1_total_attacks_total"] == 565
    assert s["p2_dangerous_attacks_avg"] == 44.83
    assert s["first_goal_min"] == 68
    assert s["first_corner_min"] == 30
    assert s["recent_uo_2.5_under_pct"] == 33
    assert s["recent_uo_2.5_over_pct"] == 67
    assert s["p1_btts_yes"] == 3
    assert s["p2_btts_yes_pct"] == 67
    assert s["p1_l6_all_win_rate"] == 1 / 6
    assert s["p2_l6_all_wins"] == 3
    assert s["corners_avg_line"] == 9.57
    assert s["cards_pred_low"] == 1
    assert s["doublechance_prob"] == 71
    assert s["doublechance_pick"] == "12"
    # Numeric values surface with football prefix via numeric()
    numeric = facets.numeric()
    assert numeric["football_p1_shots_total"] == 68
    assert numeric["football_corners_avg_line"] == 9.57


def test_football_shots_nan_percentages_still_extracts_totals():
    # Forebet renders no-data shot-direction splits as literal "NAN%" on
    # tiny-sample amateur fixtures (Kutjevo v Svacic, 2026-08-23). That must
    # not discard the total/blocked counts present on the same block, and
    # NAN must surface as missing -- never zero-filled.
    from slumdog.detail_facets import _football_shots

    text = (
        "Total shots 0 0 Blocked 0 0 NAN% OFF target NAN% ON target 0% Inside "
        "box 0% Outside box SVA Avg. per game Total shots 0 0 Blocked 0 0 NAN% "
        "OFF target NAN% ON target 0% Inside box 0% Outside box KUT"
    )
    out = _football_shots(text)
    assert out["p1_shots_total"] == 0
    assert out["p1_shots_blocked"] == 0
    assert "p1_shots_on_target_pct" not in out
    assert "p1_shots_off_target_pct" not in out
    # The 0% inside-box value is a real (zero) figure, not NAN.
    assert out["p1_shots_inside_box_pct"] == 0
    assert out["p2_shots_total"] == 0
