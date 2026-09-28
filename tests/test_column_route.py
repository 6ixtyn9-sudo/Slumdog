"""The column route as a production capture path.

Every non-football board currently answers a bot-check page to anything CI
can send, which is why rank-1 coverage collapsed to football alone. The
renderer will still hand over the board one field at a time. These tests
describe when the collector is allowed to use that route, and — more
importantly — when it must refuse rather than file something weaker than an
HTML capture under the same name.
"""
from __future__ import annotations

import json

import pytest

from slumdog import forebet
from slumdog.forebet import ForebetCollector
from slumdog.parsers import parse_capture
from slumdog.relay_columns import (
    BODY_FORMAT,
    CAPTURED,
    COVERAGE_GAP,
    BoardCapture,
    BoardColumns,
    serialise_columns,
)

BOT_CHECK = b"<html><body>Verifying you are human</body></html>"


def _board(sport: str = "hockey", target_date: str = "2026-09-29"):
    return BoardColumns(
        sport=sport, target_date=target_date,
        source_url="https://www.forebet.com/en/predictions-hockey",
        columns={
            "link": ["[Sportul Gyergyoi 29/09/2026 7:00 PM]"
                     "(https://f/m/sportul-gyergyoi/387418)"],
            "home": ["Sportul"], "away": ["Gyergyoi"],
            "kickoff": ["19:00"], "probabilities": ["3 97"], "pick": ["2"],
        },
        row_count=1)


def _reject_html(monkeypatch):
    monkeypatch.setattr(forebet, "fetch_with_fallback",
                        lambda *a, **k: (BOT_CHECK, "relay"))


def _column_capture(board):
    return BoardCapture(status=CAPTURED, sport=board.sport,
                        target_date=board.target_date,
                        source_url=board.source_url, events=(),
                        row_count=board.row_count, board=board)


class TestTheRouteIsAFallbackNotADefault:
    def test_a_good_html_board_never_reaches_the_column_route(
            self, monkeypatch, tmp_path):
        good = (b"<html>" + b"<div class='rcnt'>x</div>" * 400 + b"</html>")
        monkeypatch.setattr(forebet, "fetch_with_fallback",
                            lambda *a, **k: (good, "relay"))
        monkeypatch.setattr(forebet, "validate_capture_body",
                            lambda *a, **k: None)
        called = {"n": 0}

        def boom(*args, **kwargs):
            called["n"] += 1
            raise AssertionError("column route used for a valid board")

        monkeypatch.setattr("slumdog.relay_columns.capture_board", boom)
        capture = ForebetCollector(tmp_path)._fetch("hockey", "2026-09-29")
        assert capture.body_format == "html"
        assert called["n"] == 0

    def test_a_refused_html_board_falls_through_to_columns(
            self, monkeypatch, tmp_path):
        _reject_html(monkeypatch)
        board = _board()
        monkeypatch.setattr("slumdog.relay_columns.capture_board",
                            lambda *a, **k: _column_capture(board))
        capture = ForebetCollector(tmp_path)._fetch("hockey", "2026-09-29")
        assert capture.body_format == BODY_FORMAT
        assert capture.route == "relay_columns"
        stored = json.loads((tmp_path / capture.body_path).read_bytes())
        assert stored["columns"] == board.columns

    def test_a_column_gap_is_a_failure_not_an_empty_capture(
            self, monkeypatch, tmp_path):
        _reject_html(monkeypatch)
        monkeypatch.setattr(
            "slumdog.relay_columns.capture_board",
            lambda *a, **k: BoardCapture(
                status=COVERAGE_GAP, sport="hockey",
                target_date="2026-09-29", source_url="u", events=(),
                reason="columns disagree on row count"))
        with pytest.raises(ValueError, match="COVERAGE_GAP"):
            ForebetCollector(tmp_path)._fetch("hockey", "2026-09-29")

    def test_football_keeps_its_json_route(self, monkeypatch, tmp_path):
        payload = json.dumps([[{"DATE_BAH": "2026-09-29 19:00:00"}] * 40]
                             ).encode()
        monkeypatch.setattr(forebet, "relay_get_markdown",
                            lambda *a, **k: payload)
        monkeypatch.setattr(forebet, "validate_capture_body",
                            lambda *a, **k: None)
        capture = ForebetCollector(tmp_path)._fetch("football", "2026-09-29")
        assert capture.body_format == "html"
        assert capture.route == "relay_markdown"


class TestAColumnCaptureIsReadBackAsEvents:
    def _capture(self, tmp_path, body: bytes, **over):
        (tmp_path / "body.txt").write_bytes(body)
        return parse_capture(self._metadata(body_path="body.txt", **over),
                             root=tmp_path)

    def _metadata(self, **over):
        base = {"sport": "hockey", "target_date": "2026-09-29",
                "captured_at": "2026-09-29T06:00:00+00:00",
                "source_url": "https://f/", "sha256": "abc",
                "body_format": BODY_FORMAT}
        base.update(over)
        return base

    def test_stored_bytes_reproduce_the_events(self, tmp_path):
        events = self._capture(tmp_path, serialise_columns(_board()))
        assert len(events) == 1
        assert events[0].event_id.endswith("387418")
        assert events[0].participant_1 == "Sportul"
        assert events[0].raw_sha256 == "abc"

    def test_an_unmarked_column_body_is_still_recognised(self, tmp_path):
        """A body_format the writer forgot must not silently parse as HTML."""
        assert self._capture(tmp_path, serialise_columns(_board()),
                             body_format="")

    def test_an_html_capture_is_not_sent_to_the_column_parser(self, tmp_path):
        html = b"<html><body><div class='rcnt'></div></body></html>"
        assert self._capture(tmp_path, html, body_format="html") == []
