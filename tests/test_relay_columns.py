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
    COLUMN_SELECTORS,
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
    return {
        ".rcnt .tnms": "\n".join(
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
        bodies[".homeTeam"] = b"<title>Just a moment...</title>"
        with pytest.raises(ColumnFetchError, match="missing required column"):
            fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                opener=_opener(bodies))


class TestAlignment:
    """A throttled render can return a partial board that looks genuine."""

    def test_columns_that_disagree_raise(self):
        bodies = _full_board(3)
        bodies[".avg_sc"] = b"167.4\n168.9"  # 2 where the rest have 3
        with pytest.raises(ColumnAlignmentError, match="disagree"):
            fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                opener=_opener(bodies))

    def test_the_error_names_the_counts(self):
        bodies = _full_board(3)
        bodies[".fprc"] = b"71 29"
        with pytest.raises(ColumnAlignmentError) as caught:
            fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                opener=_opener(bodies))
        assert "'probabilities': 1" in str(caught.value)

    def test_a_short_board_is_rejected_when_a_minimum_is_set(self):
        with pytest.raises(ColumnFetchError, match="expected at least"):
            fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                opener=_opener(_full_board(3)),
                                minimum_rows=20)


class TestRequiredColumns:
    def test_identity_and_kickoff_are_mandatory(self):
        bodies = _full_board()
        del bodies[".date_bah"]
        with pytest.raises(ColumnFetchError, match="kickoff"):
            fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                opener=_opener(bodies))

    def test_an_optional_column_failing_marks_the_board_partial(self):
        bodies = _full_board()
        del bodies[".avg_sc"]
        board = fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                    opener=_opener(bodies))
        # The board is still usable — identity and kickoff survived — but it
        # must not pass as complete, because the missing average could equally
        # be a throttled response as an absent field.
        assert board.partial is True
        assert "average" not in board.columns
        assert board.row_count == 3

    def test_a_422_is_reported_as_a_fetch_failure(self):
        with pytest.raises(ColumnFetchError, match="HTTP 422"):
            fetch_column(BOARD, ".nope", opener=_opener({}))


class TestRows:
    def test_columns_zip_back_into_matches(self):
        board = fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                    opener=_opener(_full_board(3)))
        rows = board.rows()
        assert board.row_count == 3 and len(rows) == 3
        assert rows[0]["home"] == "Team0" and rows[0]["away"] == "Rival0"
        assert rows[0]["kickoff"] == "27/09/2026 19:30"
        assert rows[2]["predicted_score"] == "92-78"

    def test_every_field_is_requested_once(self):
        seen: list[str] = []
        fetch_board_columns(BOARD, "basketball", "2026-09-27",
                            opener=_opener(_full_board(), seen))
        assert seen == list(COLUMN_SELECTORS.values())

    def test_a_clean_board_is_not_marked_partial(self):
        board = fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                    opener=_opener(_full_board(4)))
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
        del bodies[".rcnt .tnms"]
        with pytest.raises(ColumnFetchError, match="link"):
            fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                opener=_opener(bodies))


class TestEventConversion:
    def _board(self, rows: int = 3):
        return fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                   opener=_opener(_full_board(rows)))

    def test_rows_become_rankable_events(self):
        events = rows_to_events(self._board(3), captured_at="2026-09-27T04:00:00Z")
        assert len(events) == 3
        first = events[0]
        assert first.event_id == "2500000"
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
        bodies[".rcnt .tnms"] = (
            b"[A B 09/27/2026 2:00 AM](https://f/m/a-1111)\n"
            b"[C D 09/28/2026 2:00 AM](https://f/m/c-2222)")
        board = fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                    opener=_opener(bodies))
        events = rows_to_events(board, captured_at="2026-09-27T04:00:00Z")
        assert [e.event_id for e in events] == ["1111"]

    def test_an_unreadable_probability_drops_the_row_rather_than_guessing(self):
        bodies = _full_board(2)
        bodies[".fprc"] = b"71 29\n-"
        board = fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                    opener=_opener(bodies))
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
        bodies[".homeTeam"] = b"Host\nTeam0\nTeam1\nTeam2"
        bodies[".awayTeam"] = b"Guest\nRival0\nRival1\nRival2"
        board = fetch_board_columns(BOARD, "basketball", "2026-09-27",
                                    opener=_opener(bodies))
        assert board.row_count == 3
        events = rows_to_events(board, captured_at="2026-09-27T04:00:00Z")
        assert [e.participant_1 for e in events] == ["Team0", "Team1", "Team2"]
