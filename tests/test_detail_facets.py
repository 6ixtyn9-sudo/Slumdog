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
    </body></html>'''
    facets = parse_detail(body, "football")
    assert facets.sport_specific == {
        "weather_present": True,
        "htft_present": True,
        "corners_present": True,
        "cards_present": True,
        "btts_present": True,
    }


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
