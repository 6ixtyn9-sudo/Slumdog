import json

from slumdog.parsers import decimal_odds, parse_football_json, parse_html_events


HTML = b"""
<html><body>
<div class='rcnt'>
  <span class='shortTag'>TST</span>
  <a class='tnmscn' href='/en/basketball/matches/test/alpha-beta/123'>
    <span class='homeTeam'><span>Alpha</span></span>
    <span class='awayTeam'><span>Beta</span></span>
    <span class='date_bah'>22/08/2026 14:00</span>
  </a>
  <div class='fprc'><span>35</span><span>65</span></div>
  <div class='predict'><span class='forepr'><span>2</span></span><span class='scrmobpred'>78-84</span></div>
  <div class='avg_sc'>162.5</div>
  <div class='bigOnly'><span class='lscrsp'>-154</span><div class='haodd'><span>+120</span><span>-154</span></div></div>
  <div class='predQ'><div class='fj_column'><span>18</span><span>22</span></div></div>
  <div class='lscr_td'></div>
</div>
</body></html>
"""


def test_decimal_odds_converts_american_and_decimal():
    assert decimal_odds("+150") == 2.5
    assert round(decimal_odds("-200"), 2) == 1.5
    assert decimal_odds("2.30") == 2.3
    assert decimal_odds("-") is None


def test_generic_html_parser_builds_named_event_and_all_core_fields():
    events = parse_html_events(
        HTML, "basketball", "2026-08-22", "2026-08-22T06:00:00+00:00",
        "https://www.forebet.com/en/basketball/predictions/2026-08-22", "abc",
    )
    assert len(events) == 1
    event = events[0]
    assert (event.participant_1, event.participant_2) == ("Alpha", "Beta")
    assert (event.probability_1, event.probability_2) == (0.35, 0.65)
    assert event.forebet_pick == 2
    assert event.predicted_score == "78-84"
    assert event.predicted_total == 162.5
    assert (event.odds_1, round(event.odds_2, 2)) == (2.2, 1.65)
    assert event.facets["period_values"] == [["18", "22"]]


def test_football_json_parser_keeps_prematch_and_drops_results():
    row = {
        "id": "1", "HOST_NAME": "Home", "GUEST_NAME": "Away",
        "Pred_1": "30", "Pred_X": "20", "Pred_2": "50",
        "best_odd_1": "2.60", "best_odd_2": "1.60", "short_tag": "TST",
        "DATE_BAH": "2026-08-22 18:00", "host_sc_pr": "1", "guest_sc_pr": "2",
        "goalsavg": "3.1", "Host_SC": None, "Guest_SC": None, "comment": "",
    }
    finished = dict(row, id="2", Host_SC="1", Guest_SC="2", comment="FT")
    body = ("<html><body>" + json.dumps([[row, finished], {}]) + "</body></html>").encode()
    events = parse_football_json(
        body, "2026-08-22", "2026-08-22T06:00:00+00:00", "u", "abc"
    )
    assert len(events) == 1
    assert events[0].forebet_pick == 2
    assert events[0].draw_probability == 0.20
    assert events[0].odds_1 == 2.6
    assert events[0].source_url.endswith("/home-away-1")


def test_football_listing_promotes_form_standings_weather_and_draw_odds():
    row = {
        "id": "9", "HOST_NAME": "Home", "GUEST_NAME": "Away",
        "Pred_1": "20", "Pred_X": "30", "Pred_2": "50",
        "best_odd_1": "4.00", "best_odd_2": "1.70", "best_odd_X": "3.60",
        "short_tag": "Pt1", "DATE_BAH": "2026-08-23 18:00",
        "host_sc_pr": "1", "guest_sc_pr": "2", "goalsavg": "2.8",
        "Host_SC": None, "Guest_SC": None, "comment": "",
        "host_form": ["l", "d", "d", "l", "w", "l"],
        "guest_form": ["w", "w", "d", "d", "w", "w"],
        "host_pos": "15th", "guest_pos": "7th",
        "weather_high": 14, "weather_low": 14, "weather_code": 31,
        "kelly": 0.39, "Round": 3,
    }
    body = json.dumps([[row]]).encode()
    events = parse_football_json(
        body, "2026-08-23", "2026-08-23T06:00:00+00:00", "u", "abc",
    )
    event = events[0]
    assert event.round_name == "3"
    assert event.facets["recent_1_wins"] == 1
    assert event.facets["recent_1_games"] == 6
    assert event.facets["recent_2_wins"] == 4
    assert event.facets["standings_1"] == 15
    assert event.facets["standings_2"] == 7
    assert event.facets["standings_gap"] == 8
    assert event.facets["odds_draw"] == 3.6
    assert event.facets["weather_high"] == 14
    assert event.facet_timing["recent_1_wins"].value == "PRE_EVENT"
