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

    def test_the_heading_row_is_not_a_match(self):
        body = b"Home team\nLakers\nCeltics"
        assert parse_column(body, selector=".homeTeam") == ["Lakers", "Celtics"]

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
