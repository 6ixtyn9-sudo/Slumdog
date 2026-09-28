"""Tests for column-wise board capture.

The channel these tests describe has already fooled this project once: a
5.8KB bot-check page was validated and frozen as a genuine capture for two
sports. So the emphasis here is on refusing bad data, not on parsing good
data.
"""
from __future__ import annotations

import urllib.error

import pytest

from slumdog.relay_columns import (
    CAPTURED,
    deserialise_columns,
    looks_like_columns_body,
    serialise_columns,
    settled_rows,
    COLUMN_SELECTORS,
    COVERAGE_GAP,
    NO_ROWS_FOR_DATE,
    DAY_FIRST,
    MONTH_FIRST,
    capture_board,
    infer_date_order,
    ROW_SCOPE,
    scoped,
    _consensus,
    align_columns,
    event_day_from_kickoff,
    event_id_from_url,
    match_url,
    rows_to_events,
    BoardColumns,
    ColumnAlignmentError,
    ColumnFetchError,
    fetch_board_columns,
    fetch_column,
    parse_column,
    strip_wrapper,
)

BOARD = "https://www.forebet.com/en/basketball/predictions/2026-09-27"


class _Response:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(bodies: dict[str, bytes], seen: list[str] | None = None):
    def _open(request, timeout=None):
        selector = request.headers.get("X-target-selector") or \
            request.headers.get("X-Target-Selector")
        if seen is not None:
            seen.append(selector)
        body = bodies.get(selector)
        if body is None:
            raise urllib.error.HTTPError(
                request.full_url, 422, "Unprocessable", {}, None)
        return _Response(body)

    return _open


def _full_board(rows: int = 3) -> dict[str, bytes]:
    teams = [f"Team{i}" for i in range(rows)]
    raw = {
        ".tnms": "\n".join(
            f"[Team{i} Rival{i} 09/27/2026 2:00 AM]"
            f"(https://www.forebet.com/en/basketball/matches/t{i}-250000{i})"
            for i in range(rows)).encode(),
        ".homeTeam": "\n".join(teams).encode(),
        ".awayTeam": "\n".join(f"Rival{i}" for i in range(rows)).encode(),
        ".date_bah": "\n".join("27/09/2026 19:30" for _ in range(rows)).encode(),
        ".fprc": "\n".join("71 29" for _ in range(rows)).encode(),
        ".forepr": "\n".join("1" for _ in range(rows)).encode(),
        ".ex_sc": "\n".join("92-78" for _ in range(rows)).encode(),
        ".avg_sc": "\n".join("167.4" for _ in range(rows)).encode(),
    }
    return {scoped(selector): body for selector, body in raw.items()}


class TestWrapperAndHeadings:
    def test_the_relay_envelope_is_removed(self):
        body = (b"Title: Basketball\n\nURL Source: https://x\n\n"
                b"Markdown Content:\nLakers\nCeltics\n")
        assert strip_wrapper(body) == "Lakers\nCeltics"

    def test_a_name_column_keeps_its_heading_until_alignment(self):
        # A team-name column has no shape that distinguishes "Host" from a
        # team, so its heading is removed later, against the row count the
        # shaped columns agree on — never guessed at here.
        body = b"Home team\nLakers\nCeltics"
        assert parse_column(body, selector=".homeTeam", column="home") == [
            "Home team", "Lakers", "Celtics"]

    def test_blank_lines_are_dropped(self):
        body = b"Lakers\n\n\nCeltics\n"
        assert parse_column(body, selector=".homeTeam") == ["Lakers", "Celtics"]


class TestChallengeRejection:
    """A bot-check page is a 200 with a body. Counting it as an empty board
    is how an interstitial got frozen as evidence before."""

    def test_an_interstitial_is_not_an_empty_board(self):
        body = (b"<html><head><title>Just a moment...</title></head>"
                b"<body>checking</body></html>")
        with pytest.raises(ColumnFetchError, match="bot-check"):
            parse_column(body, selector=".homeTeam")

    def test_a_challenge_during_capture_fails_the_board(self):
        bodies = _full_board()
        bodies[scoped(".homeTeam")] = b"<title>Just a moment...</title>"
        with pytest.raises(ColumnFetchError, match="missing required column"):
            fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                sleep=lambda _s: None, opener=_opener(bodies))


class TestAlignment:
    """A throttled render can return a partial board that looks genuine."""

    def test_columns_that_disagree_raise(self):
        bodies = _full_board(3)
        bodies[scoped(".avg_sc")] = b"167.4\n168.9"  # 2 where the rest have 3
        with pytest.raises(ColumnAlignmentError, match="disagree"):
            fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                sleep=lambda _s: None, opener=_opener(bodies))

    def test_the_error_names_the_counts(self):
        bodies = _full_board(3)
        bodies[scoped(".fprc")] = b"71 29"
        with pytest.raises(ColumnAlignmentError) as caught:
            fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                sleep=lambda _s: None, opener=_opener(bodies))
        assert "'probabilities': 1" in str(caught.value)

    def test_a_short_board_is_rejected_when_a_minimum_is_set(self):
        with pytest.raises(ColumnFetchError, match="expected at least"):
            fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                sleep=lambda _s: None, opener=_opener(_full_board(3)),
                                minimum_rows=20)


class TestRequiredColumns:
    def test_identity_and_kickoff_are_mandatory(self):
        bodies = _full_board()
        del bodies[scoped(".date_bah")]
        with pytest.raises(ColumnFetchError, match="kickoff"):
            fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                sleep=lambda _s: None, opener=_opener(bodies))

    def test_an_optional_column_failing_marks_the_board_partial(self):
        bodies = _full_board()
        del bodies[scoped(".avg_sc")]
        board = fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                    sleep=lambda _s: None, opener=_opener(bodies))
        # The board is still usable — identity and kickoff survived — but it
        # must not pass as complete, because the missing average could equally
        # be a throttled response as an absent field.
        assert board.partial is True
        assert "average" not in board.columns
        assert board.row_count == 3

    def test_a_422_is_reported_as_a_fetch_failure(self):
        with pytest.raises(ColumnFetchError, match="HTTP 422"):
            fetch_column(BOARD, ".nope", opener=_opener({}),
                         sleep=lambda _s: None)


class TestRows:
    def test_columns_zip_back_into_matches(self):
        board = fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                    sleep=lambda _s: None, opener=_opener(_full_board(3)))
        rows = board.rows()
        assert board.row_count == 3 and len(rows) == 3
        assert rows[0]["home"] == "Team0" and rows[0]["away"] == "Rival0"
        assert rows[0]["kickoff"] == "27/09/2026 19:30"
        assert rows[2]["predicted_score"] == "92-78"

    def test_every_field_is_requested_once(self):
        seen: list[str] = []
        fetch_board_columns(BOARD, "basketball", "2026-09-27",
                            sleep=lambda _s: None, opener=_opener(_full_board(), seen))
        assert seen == [scoped(sel) for sel in COLUMN_SELECTORS.values()]

    def test_a_clean_board_is_not_marked_partial(self):
        board = fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                    sleep=lambda _s: None, opener=_opener(_full_board(4)))
        assert board.partial is False
        assert isinstance(board, BoardColumns)


class TestMatchIdentity:
    """Columns carry no id except inside the .tnms link, and an event
    without identity cannot be settled later."""

    def test_the_site_match_id_is_reused(self):
        assert event_id_from_url(
            "https://www.forebet.com/en/basketball/matches/lakers-heat-2500123"
        ) == "2500123"

    def test_an_id_less_url_still_yields_a_stable_identity(self):
        url = "https://www.forebet.com/en/basketball/matches/lakers-heat"
        first, second = event_id_from_url(url), event_id_from_url(url)
        assert first == second and len(first) == 16

    def test_the_url_is_read_out_of_the_markdown_link(self):
        cell = "[Abejas Santos 09/27/2026 2:00 AM](https://x/m/abejas-9)"
        assert match_url(cell) == "https://x/m/abejas-9"

    def test_a_cell_without_a_link_has_no_url(self):
        assert match_url("Abejas Santos 09/27/2026 2:00 AM") is None

    def test_the_board_renders_the_month_first(self):
        # 09/27/2026 is 27 September, not an invalid 9th of month 27.
        assert event_day_from_kickoff("09/27/2026 2:00 AM") == "2026-09-27"

    def test_missing_identity_is_required_before_a_board_is_accepted(self):
        bodies = _full_board()
        del bodies[scoped(".tnms")]
        with pytest.raises(ColumnFetchError, match="link"):
            fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                sleep=lambda _s: None, opener=_opener(bodies))


class TestEventConversion:
    def _board(self, rows: int = 3):
        return fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                   opener=_opener(_full_board(rows)))

    def test_rows_become_rankable_events(self):
        events = rows_to_events(self._board(3), captured_at="2026-09-27T04:00:00Z")
        assert len(events) == 3
        first = events[0]
        # "<sport>:<id>" — the identity the rest of the system joins on.
        assert first.event_id == "basketball:2500000"
        assert (first.participant_1, first.participant_2) == ("Team0", "Rival0")
        assert first.sport == "basketball" and first.event_date == "2026-09-27"
        assert first.probability_1 == 0.71 and first.probability_2 == 0.29
        assert first.forebet_pick == 1
        assert first.predicted_total == 167.4

    def test_a_sport_without_draws_gets_no_draw_probability(self):
        events = rows_to_events(self._board(1), captured_at="2026-09-27T04:00:00Z")
        assert events[0].draw_probability is None

    def test_rows_for_a_neighbouring_day_are_dropped(self):
        bodies = _full_board(2)
        bodies[scoped(".tnms")] = (
            b"[A B 09/27/2026 2:00 AM](https://f/m/a-1111)\n"
            b"[C D 09/28/2026 2:00 AM](https://f/m/c-2222)")
        board = fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                    sleep=lambda _s: None, opener=_opener(bodies))
        events = rows_to_events(board, captured_at="2026-09-27T04:00:00Z")
        assert [e.event_id for e in events] == ["basketball:1111"]

    def test_an_unreadable_probability_drops_the_row_rather_than_guessing(self):
        bodies = _full_board(2)
        bodies[scoped(".fprc")] = b"71 29\n-"
        board = fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                    sleep=lambda _s: None, opener=_opener(bodies))
        assert len(rows_to_events(board, captured_at="2026-09-27T04:00:00Z")) == 1

    def test_the_rendered_clock_is_kept_as_text_not_as_an_instant(self):
        events = rows_to_events(self._board(1), captured_at="2026-09-27T04:00:00Z")
        # The renderer emits local-to-the-relay time; it is carried verbatim
        # so no downstream consumer can mistake it for UTC.
        assert events[0].kickoff == "27/09/2026 19:30"


class TestHeadingsAreFoundByShape:
    """Matching a list of known heading strings was tried first and silently
    mis-aligned every board: on live data only '.fprc' matched its heading,
    so probabilities came back exactly one row short and all four sports
    were refused. Headings are shape-detected instead."""

    def test_a_probability_heading_is_dropped_whatever_it_says(self):
        rows = parse_column(b"Prob. %\n71 29\n64 36", selector=".fprc",
                            column="probabilities")
        assert rows == ["71 29", "64 36"]

    def test_a_localised_heading_is_still_dropped(self):
        rows = parse_column("Wahrscheinlichkeit\n71 29".encode(),
                            selector=".fprc", column="probabilities")
        assert rows == ["71 29"]

    def test_a_kickoff_heading_is_dropped_by_shape(self):
        rows = parse_column(b"Date\n27/09/2026 19:30", selector=".date_bah",
                            column="kickoff")
        assert rows == ["27/09/2026 19:30"]

    def test_a_link_heading_is_dropped_by_shape(self):
        rows = parse_column(b"Match\n[A B](https://f/m/a-1)", selector=".tnms",
                            column="link")
        assert rows == ["[A B](https://f/m/a-1)"]

    def test_only_leading_lines_are_dropped(self):
        # A blank value mid-board is a real row; dropping it would shift
        # every later row against the other columns.
        rows = parse_column(b"Prob. %\n71 29\n-\n64 36", selector=".fprc",
                            column="probabilities")
        assert rows == ["71 29", "-", "64 36"]

    def test_a_name_column_heading_is_trimmed_to_the_consensus(self):
        aligned = align_columns({
            "probabilities": ["71 29", "64 36"],
            "kickoff": ["27/09/2026", "27/09/2026"],
            "home": ["Host", "Lakers", "Heat"],
        })
        assert aligned["home"] == ["Lakers", "Heat"]

    def test_a_short_name_column_is_never_padded(self):
        aligned = align_columns({
            "probabilities": ["71 29", "64 36"],
            "home": ["Lakers"],
        })
        assert aligned["home"] == ["Lakers"]  # left short, so alignment fails

    def test_a_badly_wrong_name_column_is_not_trimmed_into_agreement(self):
        aligned = align_columns({
            "probabilities": ["71 29"],
            "home": ["Host", "Lakers", "Heat", "Bulls"],
        })
        assert len(aligned["home"]) == 4

    def test_the_off_by_one_board_now_converts(self):
        # Exactly the live shape from run 36295483404: every column 20 rows,
        # probabilities 19, because only that heading was recognised.
        bodies = _full_board(3)
        bodies[scoped(".homeTeam")] = b"Host\nTeam0\nTeam1\nTeam2"
        bodies[scoped(".awayTeam")] = b"Guest\nRival0\nRival1\nRival2"
        board = fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                    sleep=lambda _s: None, opener=_opener(bodies))
        assert board.row_count == 3
        events = rows_to_events(board, captured_at="2026-09-27T04:00:00Z")
        assert [e.participant_1 for e in events] == ["Team0", "Team1", "Team2"]


class TestRowScope:
    """Measured on 2026-09-27 every blocked board returned one fewer .fprc
    than .homeTeam — some rows carry no probability cell. A single missing
    element silently pairs every later row with the wrong match, so the
    scope removes those rows instead of detecting them afterwards."""

    def test_columns_are_scoped_to_complete_rows(self):
        assert scoped(".homeTeam") == ".rcnt:has(.fprc):has(.tnms) .homeTeam"

    def test_the_scope_requires_both_identity_and_probability(self):
        assert ":has(.fprc)" in ROW_SCOPE and ":has(.tnms)" in ROW_SCOPE

    def test_every_request_carries_the_scope(self):
        seen: list[str] = []
        fetch_board_columns(BOARD, "basketball", "2026-09-27",
                            sleep=lambda _s: None, opener=_opener(_full_board(2), seen))
        assert all(s.startswith(ROW_SCOPE) for s in seen)

    def test_an_unpredicted_row_never_reaches_the_join(self):
        # The board holds three matches; the renderer returns two because
        # the third has no probability cell. Nothing is mis-paired.
        board = fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                    sleep=lambda _s: None, opener=_opener(_full_board(2)))
        events = rows_to_events(board, captured_at="2026-09-27T04:00:00Z")
        assert [e.participant_1 for e in events] == ["Team0", "Team1"]


class TestThrottleRetry:
    """Throttling is the last obstacle to daily coverage. On 2026-09-27 a
    board would return five clean columns and one 422, and that single
    refusal discarded the whole board — tennis was lost while its other
    five columns agreed on 43 rows."""

    def test_a_refusal_is_retried(self):
        calls = {"n": 0}

        def flaky(request, timeout=None):
            calls["n"] += 1
            if calls["n"] < 3:
                raise urllib.error.HTTPError("u", 422, "no", {}, None)
            return _Response(b"Lakers\nCeltics")

        rows = fetch_column(BOARD, ".homeTeam", opener=flaky, column="home",
                            sleep=lambda _s: None)
        assert rows == ["Lakers", "Celtics"] and calls["n"] == 3

    def test_retries_back_off_rather_than_hammer(self):
        slept: list[float] = []

        def always_422(request, timeout=None):
            raise urllib.error.HTTPError("u", 422, "no", {}, None)

        with pytest.raises(ColumnFetchError):
            fetch_column(BOARD, ".homeTeam", opener=always_422,
                         backoff=5.0, sleep=slept.append)
        assert slept == [5.0, 10.0]

    def test_it_gives_up_and_reports_rather_than_looping(self):
        def always_down(request, timeout=None):
            raise OSError("connection reset")

        with pytest.raises(ColumnFetchError, match="OSError"):
            fetch_column(BOARD, ".homeTeam", opener=always_down,
                         sleep=lambda _s: None)

    def test_a_bot_check_is_never_retried_into_data(self):
        calls = {"n": 0}

        def challenge(request, timeout=None):
            calls["n"] += 1
            return _Response(b"<title>Just a moment...</title>")

        with pytest.raises(ColumnFetchError, match="bot-check"):
            fetch_column(BOARD, ".homeTeam", opener=challenge,
                         sleep=lambda _s: None)
        assert calls["n"] == 1, "a challenge is an answer, not a transient"


class TestCapturePolicy:
    """The three decisions that had to be settled before this route could
    feed anything, each pinned so a later change has to argue with a test
    rather than quietly reverse a policy."""

    def _capture(self, bodies, **kwargs):
        # Retry backoff is real time; tests exercise the policy, not the wait.
        kwargs.setdefault("sleep", lambda _s: None)
        return capture_board(BOARD, "basketball", "2026-09-27",
                             captured_at="2026-09-27T04:00:00Z",
                             opener=_opener(bodies), **kwargs)

    def test_a_clean_board_is_captured(self):
        result = self._capture(_full_board(3))
        assert result.status == CAPTURED and result.usable
        assert len(result.events) == 3

    def test_an_unreadable_board_is_a_gap_not_a_quiet_day(self):
        bodies = _full_board(3)
        del bodies[scoped(".date_bah")]
        result = self._capture(bodies)
        assert result.status == COVERAGE_GAP
        assert result.events == [] and "kickoff" in result.reason
        assert not result.usable

    def test_a_gap_is_distinguishable_from_a_day_without_fixtures(self):
        other_day = _full_board(2)
        other_day[scoped(".tnms")] = (
            b"[A B 09/28/2026 2:00 AM](https://f/m/a-1111)\n"
            b"[C D 09/28/2026 4:00 AM](https://f/m/c-2222)")
        assert self._capture(other_day).status == NO_ROWS_FOR_DATE
        assert self._capture({}).status == COVERAGE_GAP

    def test_silence_is_never_read_as_a_day_without_fixtures(self):
        # An empty render and a throttled blank cannot be told apart from
        # here, and this route has already served short bodies that looked
        # like data. NO_ROWS_FOR_DATE needs positive evidence of other dates.
        result = self._capture(_full_board(0))
        assert result.status == COVERAGE_GAP

    def test_a_board_is_never_walked_forward_to_another_date(self):
        # Exactly the live baseball case: the board is readable and full,
        # but every row belongs to the next day.
        bodies = _full_board(2)
        bodies[scoped(".tnms")] = (
            b"[A B 09/28/2026 2:00 AM](https://f/m/a-1111)\n"
            b"[C D 09/28/2026 4:00 AM](https://f/m/c-2222)")
        result = self._capture(bodies)
        assert result.status == NO_ROWS_FOR_DATE
        assert result.events == []
        assert result.target_date == "2026-09-27"

    def test_the_dates_the_board_did_show_are_recorded(self):
        bodies = _full_board(2)
        bodies[scoped(".tnms")] = (
            b"[A B 09/28/2026 2:00 AM](https://f/m/a-1111)\n"
            b"[C D 09/29/2026 4:00 AM](https://f/m/c-2222)")
        result = self._capture(bodies)
        # This measures how far ahead the sport publishes, which is the
        # real input to scheduling the late-publishing sports.
        assert result.observed_dates == ("2026-09-28", "2026-09-29")

    def test_a_quiet_day_is_not_rejected_for_being_small(self):
        # No absolute floor: manufacturing absence is as wrong as
        # manufacturing data.
        result = self._capture(_full_board(1))
        assert result.status == CAPTURED and len(result.events) == 1

    def test_a_count_far_below_normal_is_flagged_not_rejected(self):
        result = self._capture(_full_board(2), expected_rows=40)
        assert result.status == CAPTURED
        assert result.suspect_short is True
        assert result.usable is True

    def test_a_normal_count_is_not_flagged(self):
        result = self._capture(_full_board(20), expected_rows=22)
        assert result.suspect_short is False

    def test_capture_does_not_raise_for_any_of_these(self):
        for bodies in ({}, _full_board(0), _full_board(3)):
            self._capture(bodies)  # must not raise


class TestDateOrderIsMeasuredNotAssumed:
    """Assuming month-first was a live bug waiting to happen. Basketball
    renders 09/27/2026, which can only be month-first; the cricket sweep on
    2026-09-28 returned 30/09/2026, which can only be day-first. Reading a
    board in the wrong order files matches under a day they do not belong
    to, and the 24h proof is a claim about that day."""

    def test_a_day_over_twelve_proves_month_first(self):
        assert infer_date_order(["[A B 09/27/2026 2:00 AM](u)"]) == MONTH_FIRST

    def test_a_first_component_over_twelve_proves_day_first(self):
        assert infer_date_order(["[A B 30/09/2026 2:00 PM](u)"]) == DAY_FIRST

    def test_an_all_ambiguous_board_yields_no_order(self):
        # Every date could be read either way; guessing is not allowed.
        assert infer_date_order(["[A B 05/06/2026](u)",
                                 "[C D 07/08/2026](u)"]) is None

    def test_a_self_contradicting_board_yields_no_order(self):
        assert infer_date_order(["[A B 09/27/2026](u)",
                                 "[C D 30/09/2026](u)"]) is None

    def test_the_day_is_read_in_the_boards_own_order(self):
        assert event_day_from_kickoff("01/02/2026", MONTH_FIRST) == "2026-01-02"
        assert event_day_from_kickoff("01/02/2026", DAY_FIRST) == "2026-02-01"

    def test_an_impossible_date_is_rejected(self):
        assert event_day_from_kickoff("30/09/2026", MONTH_FIRST) is None

    def test_a_board_with_no_readable_order_is_refused(self):
        # Every date reads both ways AND the anchor is symmetric (05/05),
        # so neither the board nor the URL settles the order.
        bodies = _full_board(2)
        bodies[scoped(".tnms")] = (
            b"[A B 05/05/2026 2:00 AM](https://f/m/a-1)\n"
            b"[C D 07/08/2026 2:00 AM](https://f/m/c-2)")
        result = capture_board(BOARD, "basketball", "2026-05-05",
                               captured_at="2026-05-04T04:00:00Z",
                               opener=_opener(bodies),
                               sleep=lambda _s: None)
        assert result.status == COVERAGE_GAP
        assert "month-first or day-first" in result.reason

    def test_a_day_first_board_still_converts(self):
        bodies = _full_board(2)
        bodies[scoped(".tnms")] = (
            b"[A B 28/09/2026 2:00 AM](https://f/m/a-1111)\n"
            b"[C D 30/09/2026 2:00 AM](https://f/m/c-2222)")
        result = capture_board(BOARD, "basketball", "2026-09-28",
                               captured_at="2026-09-27T04:00:00Z",
                               opener=_opener(bodies),
                               sleep=lambda _s: None)
        assert result.status == CAPTURED
        assert [e.event_id for e in result.events] == ["basketball:1111"]
        assert result.observed_dates == ("2026-09-28", "2026-09-30")


class TestColumnsThatRenderSeveralLinesPerRow:
    """Measured on 2026-09-28: every board returned exactly three times the
    row count for .ex_sc — the pair and each side's score — while nothing
    else disagreed. basketball 57 against 19, hockey 102 against 34,
    handball 36 against 12, volleyball 30 against 10."""

    def test_an_exact_multiple_collapses_to_one_value_per_row(self):
        aligned = align_columns({
            "link": ["a", "b"], "home": ["A", "B"], "away": ["C", "D"],
            "kickoff": ["k", "k"], "probabilities": ["71 29", "64 36"],
            "predicted_score": ["77-79", "77", "**79**",
                                "88-84", "88", "**84**"],
        })
        assert aligned["predicted_score"] == ["77-79", "88-84"]

    def test_the_row_count_comes_from_columns_that_can_state_it(self):
        # link, kickoff and probabilities are 1:1 with matches AND have
        # detectable headings, so a noisy optional column cannot outvote
        # them — and a name column carrying an undetectable heading does
        # not corrupt the count it is about to be measured against.
        assert _consensus({"link": ["a"], "kickoff": ["k"],
                           "probabilities": ["p"],
                           "home": ["Host", "A"],
                           "average": ["1", "2", "3"]}) == 1

    def test_those_columns_disagreeing_gives_no_consensus(self):
        assert _consensus({"link": ["a", "b"], "kickoff": ["k"],
                           "probabilities": ["p"]}) is None

    def test_a_ragged_column_is_still_a_mismatch(self):
        # 5 is not a multiple of 2: a partial render, not a known shape.
        aligned = align_columns({
            "link": ["a", "b"], "home": ["A", "B"], "away": ["C", "D"],
            "kickoff": ["k", "k"], "probabilities": ["p", "p"],
            "average": ["1", "2", "3", "4", "5"],
        })
        assert len(aligned["average"]) == 5

    def test_a_live_board_shape_converts_end_to_end(self):
        bodies = _full_board(3)
        bodies[scoped(".ex_sc")] = b"\n".join(
            [b"92-78", b"92", b"**78**"] * 3)
        board = fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                    sleep=lambda _s: None,
                                    opener=_opener(bodies))
        assert board.row_count == 3
        events = rows_to_events(board, captured_at="2026-09-27T04:00:00Z")
        assert [e.predicted_score for e in events] == ["92-78"] * 3


class TestCollapsingIsNarrow:
    def test_a_required_column_is_never_collapsed(self):
        # A name column that happens to be an exact multiple of the row
        # count is a broken capture, not a multi-line cell.
        aligned = align_columns({
            "link": ["a"], "kickoff": ["k"], "probabilities": ["p"],
            "home": ["Host", "Lakers", "Heat", "Bulls"],
        })
        assert len(aligned["home"]) == 4


class TestFieldsASportDoesNotHave:
    """MMA's board has no correct-score column: on 2026-10-03 its .ex_sc
    matched zero elements while every other column returned ten, and the
    whole board was refused as misaligned. An empty render is only safe to
    treat this way because a refusal raises — the two arrive by different
    paths and are never confused."""

    def test_an_empty_optional_column_is_dropped_not_fatal(self):
        bodies = _full_board(3)
        bodies[scoped(".ex_sc")] = b""
        board = fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                    sleep=lambda _s: None,
                                    opener=_opener(bodies))
        assert board.row_count == 3
        assert "predicted_score" not in board.columns
        assert board.partial is True

    def test_the_board_still_converts_without_it(self):
        bodies = _full_board(2)
        bodies[scoped(".ex_sc")] = b""
        board = fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                    sleep=lambda _s: None,
                                    opener=_opener(bodies))
        events = rows_to_events(board, captured_at="2026-09-27T04:00:00Z")
        assert len(events) == 2
        assert all(e.predicted_score == "" for e in events)

    def test_an_empty_required_column_is_still_fatal(self):
        # No probabilities means nothing to rank, whatever the cause.
        bodies = _full_board(3)
        bodies[scoped(".fprc")] = b""
        with pytest.raises((ColumnFetchError, ColumnAlignmentError)):
            fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                sleep=lambda _s: None, opener=_opener(bodies))

    def test_a_refused_column_is_not_mistaken_for_an_absent_field(self):
        # 422 raises and is recorded as a failure; it never looks like a
        # sport that simply lacks the field.
        bodies = _full_board(3)
        del bodies[scoped(".avg_sc")]
        board = fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                    sleep=lambda _s: None,
                                    opener=_opener(bodies))
        assert board.partial is True and "average" not in board.columns


class TestFrozenBytesAreTheExtracts:
    """A column capture must be re-parsable from what was stored."""

    def _board(self):
        return BoardColumns(
            sport="hockey", target_date="2026-09-29",
            source_url="https://www.forebet.com/en/predictions-hockey",
            columns={"link": ["29/09/2026 Sportul - Gyergyoi"],
                     "home": ["Sportul"], "away": ["Gyergyoi"]},
            row_count=1, partial=True)

    def test_round_trip_preserves_every_extract(self):
        board = self._board()
        back = deserialise_columns(
            serialise_columns(board))
        assert back.columns == board.columns
        assert back.sport == board.sport
        assert back.target_date == board.target_date
        assert back.row_count == board.row_count
        assert back.partial

    def test_foreign_body_is_refused_not_guessed_at(self):
        with pytest.raises(ValueError):
            deserialise_columns(b'{"format": "html"}')

    def test_html_body_is_not_mistaken_for_columns(self):
        assert not looks_like_columns_body(b"<html><body>x")
        assert looks_like_columns_body(serialise_columns(self._board()))


class TestSettlementNeedsAFinalScore:
    """D+1 grading reads the board's own result, and only when it is final."""

    def _board(self, statuses, scores):
        rows = len(statuses)
        return BoardColumns(
            sport="hockey", target_date="2026-09-29",
            source_url="https://www.forebet.com/en/predictions-hockey",
            columns={
                "link": [f"[A{i} B{i} 29/09/2026 7:00 PM]"
                         f"(https://f/m/a{i}/{387410 + i})"
                         for i in range(rows)],
                "home": [f"A{i}" for i in range(rows)],
                "away": [f"B{i}" for i in range(rows)],
                "probabilities": ["62 38"] * rows,
                "pick": ["1"] * rows,
                "score": list(scores), "status": list(statuses),
            }, row_count=rows)

    def test_final_rows_are_graded(self):
        rows = settled_rows(self._board(["FT"], ["3 - 1"]))
        assert len(rows) == 1
        assert rows[0]["event_id"] == "hockey:387410"
        assert (rows[0]["score_1"], rows[0]["score_2"]) == (3.0, 1.0)
        assert rows[0]["winner_index"] == 1

    def test_a_live_match_is_never_graded(self):
        assert settled_rows(self._board(["45'"], ["1 - 0"])) == []

    def test_a_postponed_match_is_never_graded(self):
        assert settled_rows(self._board(["Postp."], ["- -"])) == []

    def test_a_draw_is_recorded_as_a_draw(self):
        rows = settled_rows(self._board(["FT"], ["2 - 2"]))
        assert rows[0]["winner_index"] == 0

    def test_overtime_and_penalties_count_as_final(self):
        rows = settled_rows(self._board(["AOT", "AP"], ["2 - 3", "1 - 2"]))
        assert [row["winner_index"] for row in rows] == [2, 2]

    def test_the_settled_id_joins_to_the_id_the_pick_was_made_under(self):
        """The whole point of D+1 settlement: same match, same identity."""
        board = self._board(["FT"], ["3 - 1"])
        captured = rows_to_events(board, captured_at="2026-09-29T06:00:00Z")
        settled = settled_rows(board)
        assert [event.event_id for event in captured] == \
            [row["event_id"] for row in settled]
        assert captured[0].event_id.startswith("hockey:")

    def test_a_row_from_another_day_is_not_graded(self):
        board = self._board(["FT"], ["3 - 1"])
        board.columns["link"] = ["[A0 B0 30/09/2026 7:00 PM]"
                                 "(https://f/m/a0/387410)"]
        assert settled_rows(board) == []


class TestWhatCountsAsRequiredDependsOnTheJob:
    """A capture for settlement needs fields a capture for ranking does not."""

    def _columns(self, score):
        return {"link": ["l"], "home": ["A"], "away": ["B"],
                "kickoff": ["k"], "probabilities": ["62 38"], "pick": ["1"],
                "score": list(score), "status": ["FT"]}

    def test_an_empty_optional_column_is_dropped_as_before(self):
        aligned = align_columns(self._columns([]))
        assert "score" not in aligned
        assert aligned["home"] == ["A"]

    def test_an_empty_required_column_is_kept_so_it_fails(self):
        from slumdog.relay_columns import SETTLEMENT_REQUIRED_COLUMNS

        aligned = align_columns(self._columns([]),
                                required=SETTLEMENT_REQUIRED_COLUMNS)
        assert aligned["score"] == []


class TestTheCaptureAnswersToItsCaller:
    """Run 36386778571 was killed at the 15-minute wall with nothing
    reported: eight columns, three attempts each, none of them asking
    whether there was still time."""

    def _opener(self, calls):
        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b"[x](https://f/m/a/1111)"

        def opener(request, timeout=0):
            calls.append(timeout)
            return _Response()

        return opener

    def test_the_guard_runs_before_every_attempt(self):
        calls: list[int] = []
        seen = {"n": 0}

        def guard():
            seen["n"] += 1

        fetch_column("https://f/", ".tnms", opener=self._opener(calls),
                     column="link", before_request=guard)
        assert seen["n"] == 1

    def test_a_raising_guard_stops_the_capture_rather_than_retrying(self):
        class Stop(RuntimeError):
            pass

        def guard():
            raise Stop("out of time")

        with pytest.raises(Stop):
            fetch_column("https://f/", ".tnms", column="link",
                         before_request=guard,
                         opener=self._opener([]), sleep=lambda _s: None)

    def test_the_guard_reaches_every_column_of_a_board(self):
        calls: list[int] = []
        seen = {"n": 0}

        def guard():
            seen["n"] += 1

        with pytest.raises(Exception):
            # The fake body yields one row per column, which will not
            # satisfy alignment — the point is only that the guard ran.
            fetch_board_columns("https://f/", "hockey", "2026-09-29",
                                opener=self._opener(calls),
                                before_request=guard,
                                sleep=lambda _s: None)
        assert seen["n"] >= len(COLUMN_SELECTORS)


class TestAMatchThatLastsMoreThanADay:
    """Cricket returned a kickoff column 8 rows long on a 13-row board,
    every run, and a short required column discards the whole board. The
    five missing rows were multi-day matches, which render a date RANGE
    instead of a start time."""

    def test_both_date_markups_are_asked_for(self):
        from slumdog.relay_columns import selectors_for

        assert selectors_for("cricket")["kickoff"] == ".date_bah, .dtrange"
        assert selectors_for("hockey")["kickoff"] == ".date_bah"

    def test_every_part_of_a_selector_list_is_scoped(self):
        """Scoping only the first part would let the rest match the whole
        document — the exact leak the row scope exists to close."""
        out = scoped(".date_bah, .dtrange")
        assert out.count(ROW_SCOPE) == 2
        assert out == f"{ROW_SCOPE} .date_bah, {ROW_SCOPE} .dtrange"

    def test_a_range_resolves_to_the_day_it_begins(self):
        """A pick frozen 24h before the LAST day of a Test would be frozen
        three days after the match started."""
        assert event_day_from_kickoff("08/05 - 11/05/2026",
                                      DAY_FIRST) == "2026-05-08"
        assert event_day_from_kickoff("05/08 - 05/11/2026",
                                      MONTH_FIRST) == "2026-05-08"

    def test_an_en_dash_range_reads_the_same(self):
        assert event_day_from_kickoff("08/05 \u2013 11/05/2026",
                                      DAY_FIRST) == "2026-05-08"

    def test_an_impossible_range_start_is_refused(self):
        assert event_day_from_kickoff("40/05 - 11/05/2026", DAY_FIRST) is None

    def test_a_single_day_fixture_is_unaffected(self):
        assert event_day_from_kickoff("29/09/2026 19:00",
                                      DAY_FIRST) == "2026-09-29"

    def test_a_mixed_board_keeps_its_rows_aligned(self):
        """One row per match either way: a selector list returns matches in
        document order, so ranges and start times interleave correctly."""
        board = BoardColumns(
            sport="cricket", target_date="2026-05-08", source_url="u",
            columns={
                "link": ["[A B 08/05 - 11/05/2026](https://f/m/a-b/51396)",
                         "[C D 08/05/2026 10:00](https://f/m/c-d/51397)"],
                "home": ["A", "C"], "away": ["B", "D"],
                "kickoff": ["08/05 - 11/05/2026", "08/05/2026 10:00"],
                "probabilities": ["50 20 30", "40 25 35"],
                "pick": ["1", "2"],
            }, row_count=2)
        events = rows_to_events(board, captured_at="2026-05-07T00:00:00Z")
        assert [e.event_id for e in events] == ["cricket:51396",
                                                "cricket:51397"]
        assert {e.event_date for e in events} == {"2026-05-08"}
