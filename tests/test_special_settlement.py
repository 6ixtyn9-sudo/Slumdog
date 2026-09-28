from slumdog.history import HistoryIndex
from slumdog.settlement import parse_cricket_settled, parse_esoccer_settled, parse_mma_settled


MMA = b'''<html><body><div class="rcnt"><span class="shortTag">UFC</span>
<a class="tnmscn" href="/en/mma/matches/ufc/a-b/1"><span class="homeTeam">A</span><span class="awayTeam">B</span><span class="date_bah">16/08/2026 04:00</span></a>
<div class="fprc"><span>40</span><span>60</span></div><span class="forepr"><span>2</span></span>
<div class="haodd"><span>+150</span><span>-200</span></div>
<div class="lscr_td"><span class="oltrpy">A</span><span>KO</span></div></div></body></html>'''

CRICKET = b'''<html><body><div class="rcnt"><span class="shortTag">Test</span>
<a class="tnmscn" href="/en/cricket/matches/test/alpha-beta/2"><span class="homeTeam">Alpha</span><span class="awayTeam">Beta</span><span class="dtrange">08/05 - 11/05/2026</span></a>
<div class="fprc"><span>40</span><span>20</span><span>40</span></div><span class="forepr"><span>X</span></span>
<div class="lscr_td"><span><b>500</b></span><span>490</span></div><div class="crftcomm">Match drawn</div></div></body></html>'''

ESOCCER = b'''<html><body><div class="rcnt"><span class="shortTag">E12</span>
<a class="tnmscn" href="/en/esoccer/football/matches/a-b-3"><span class="homeTeam">Club (PlayerA)</span><span class="awayTeam">Club (PlayerB)</span><span class="date_bah">20/08/2026 10:00</span></a>
<div class="fprc"><span>30</span><span>20</span><span>50</span></div><span class="forepr"><span>2</span></span>
<div class="scoreLnk"><span>FT</span></div><div class="lscr_td"><b class="l_scr">4 - 2</b></div></div></body></html>'''


def test_mma_winner_name_settlement():
    rows = parse_mma_settled(MMA, "2026-08-16")
    assert len(rows) == 1
    assert rows[0].winner_index == 1
    assert rows[0].score_1 is None


def test_mma_duplicate_listing_rows_are_settled_once():
    duplicate = MMA.replace(b"</body></html>", b"").replace(
        b"<html><body>", b""
    )
    body = MMA.replace(b"</body></html>", duplicate + b"</body></html>")
    rows = parse_mma_settled(body, "2026-08-16")
    assert [row.event_id for row in rows] == ["mma:1"]


def test_cricket_draw_is_not_an_underdog_loss_or_void():
    rows = parse_cricket_settled(CRICKET, "2026-05-11")
    assert len(rows) == 1
    assert rows[0].winner_index == 0
    assert rows[0].disposition == "SETTLED_DRAW"


def test_esoccer_uses_handle_match_final_score():
    rows = parse_esoccer_settled(ESOCCER, "2026-08-20")
    assert len(rows) == 1
    assert rows[0].winner_index == 1


def test_void_rows_are_excluded_from_history():
    row = parse_mma_settled(MMA.replace(b'<span class="oltrpy">A</span><span>KO</span>', b'<span>NO CONTEST</span>'), "2026-08-16")[0]
    assert row.disposition == "VOID"
    assert HistoryIndex([row]).rows == []


# ---------------------------------------------------------------------------
# MMA decides a winner differently: KO, TKO, submission, decision — or nobody.
# ---------------------------------------------------------------------------

def _mma_result(inner: bytes) -> bytes:
    return MMA.replace(b'<span class="oltrpy">A</span><span>KO</span>', inner)


def test_mma_records_how_the_fight_was_won():
    assert parse_mma_settled(MMA, "2026-08-16")[0].facets["method"] == "KO"
    for rendered, method in (
        (b'<span class="oltrpy">A</span><span>TKO (punches) R2</span>', "TKO"),
        (b'<span class="oltrpy">B</span><span>Submission (RNC)</span>',
         "SUBMISSION"),
        (b'<span class="oltrpy">A</span><span>Unanimous decision</span>',
         "DECISION"),
        (b'<span class="oltrpy">B</span><span>DQ</span>',
         "DISQUALIFICATION"),
    ):
        row = parse_mma_settled(_mma_result(rendered), "2026-08-16")[0]
        assert row.facets["method"] == method, rendered


def test_mma_draw_is_settled_not_dropped():
    """A draw is a real result: the pick failed, and the record says so."""
    row = parse_mma_settled(
        _mma_result(b"<span>Split Draw</span>"), "2026-08-16")[0]
    assert row.winner_index == 0
    assert row.disposition == "SETTLED_DRAW"


def test_mma_no_contest_is_void_not_a_draw():
    row = parse_mma_settled(
        _mma_result(b"<span>NC</span>"), "2026-08-16")[0]
    assert row.disposition == "VOID"


def test_mma_unnamed_winner_is_still_refused():
    """No draw, no void, no named winner — nothing to grade."""
    assert parse_mma_settled(
        _mma_result(b"<span>KO</span>"), "2026-08-16") == []


def test_mma_draw_grades_as_a_failed_underdog_win():
    from slumdog.underdog import identify_forebet_underdog, label_underdog_outcome

    identity = identify_forebet_underdog(0.7, 0.3)
    result = label_underdog_outcome("mma", identity, winner_index=0,
                                    disposition="SETTLED_DRAW")
    assert result.eligible is True
    assert result.label == 0
    assert result.is_draw is True


def test_two_priced_outcomes_is_not_the_same_question_as_a_possible_draw():
    from slumdog.sports import SPORTS

    assert SPORTS["mma"].draw_possible is False   # board prices two
    assert SPORTS["mma"].draw_settles is True     # three can happen
    assert SPORTS["basketball"].draw_settles is False
    assert SPORTS["football"].draw_settles is True
