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
