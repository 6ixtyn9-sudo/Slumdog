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


class TestTheCollectorAnswersToItsCaller:
    """Run 36409134160: one board cost 500 seconds inside a stage holding
    a 110-second budget. A first HTML attempt plus eight column requests
    with retries outlives any cap the caller thinks it has, unless the
    caller is asked."""

    def test_the_hook_runs_before_a_board_is_fetched(self, monkeypatch,
                                                     tmp_path):
        seen = {"n": 0}
        monkeypatch.setattr(forebet, "fetch_with_fallback",
                            lambda *a, **k: (b"x" * 400, "relay"))
        monkeypatch.setattr(forebet, "validate_capture_body",
                            lambda *a, **k: None)
        collector = ForebetCollector(
            tmp_path, before_request=lambda: seen.__setitem__("n", seen["n"] + 1))
        collector._fetch("hockey", "2026-09-29")
        assert seen["n"] == 1

    def test_a_raising_hook_stops_the_capture(self, tmp_path, monkeypatch):
        class OutOfTime(RuntimeError):
            pass

        def guard():
            raise OutOfTime("slice spent")

        called = {"n": 0}
        monkeypatch.setattr(
            forebet, "fetch_with_fallback",
            lambda *a, **k: (called.__setitem__("n", called["n"] + 1),
                             (b"", "relay"))[1])
        with pytest.raises(OutOfTime):
            ForebetCollector(tmp_path, before_request=guard)._fetch(
                "hockey", "2026-09-29")
        assert called["n"] == 0

    def test_the_hook_reaches_the_column_fallback(self, monkeypatch,
                                                  tmp_path):
        _reject_html(monkeypatch)
        seen = {}

        def fake_capture(*args, **kwargs):
            seen["before_request"] = kwargs.get("before_request")
            return _column_capture(_board())

        monkeypatch.setattr("slumdog.relay_columns.capture_board",
                            fake_capture)
        guard = lambda: None  # noqa: E731
        ForebetCollector(tmp_path, before_request=guard)._fetch(
            "hockey", "2026-09-29")
        assert seen["before_request"] is guard

    def test_production_passes_nothing_and_is_unchanged(self, tmp_path):
        assert ForebetCollector(tmp_path).before_request is None


class TestTheHtmlAttemptIsOneAttempt:
    """Since 2026-09-22 the non-football board has answered a bot-check to
    everything CI can send. Three retries of a 42KB page buy nothing, and
    in run 36419041728 the column requests that followed them came back
    403 while the same relay served a settlement capture seconds later."""

    def test_the_html_probe_does_not_retry(self, monkeypatch, tmp_path):
        seen = {}

        def fake_fetch(relay, target, timeout=45, max_retries=3):
            seen["max_retries"] = max_retries
            return BOT_CHECK, "relay"

        monkeypatch.setattr(forebet, "fetch_with_fallback", fake_fetch)
        board = _board()
        monkeypatch.setattr("slumdog.relay_columns.capture_board",
                            lambda *a, **k: _column_capture(board))
        ForebetCollector(tmp_path)._fetch("hockey", "2026-09-29")
        assert seen["max_retries"] == 1

    def test_football_keeps_its_retries(self, monkeypatch, tmp_path):
        """Football's JSON route does succeed, and intermittently — its
        retries are the reason it produces picks at all."""
        seen = {"n": 0}

        def fake_markdown(*a, **k):
            seen["n"] += 1
            raise RuntimeError("challenge page")

        monkeypatch.setattr(forebet, "relay_get_markdown", fake_markdown)
        monkeypatch.setattr(
            forebet, "fetch_with_fallback",
            lambda *a, **k: (json.dumps([[{"DATE_BAH": "x"}] * 40]).encode(),
                             "relay"))
        monkeypatch.setattr(forebet, "validate_capture_body",
                            lambda *a, **k: None)
        ForebetCollector(tmp_path)._fetch("football", "2026-09-29")
        assert seen["n"] == 1  # markdown first, then the fallback succeeded


class TestAWrittenCaptureMustBeReadableAtAnySize:
    """Run 36421154844 captured a volleyball board correctly through the
    production collector, wrote 3,032 good bytes to disk, and parsed it
    as HTML to nothing. The detector searched the first 400 bytes for the
    format marker; a real ten-row board puts it at byte 1406, because the
    JSON was key-sorted and 'columns' sorts before 'format'.

    A capture that is written and unreadable is worse than one that
    fails: it looks like a quiet day."""

    def _board_of(self, rows):
        from slumdog.relay_columns import BoardColumns

        return BoardColumns(
            sport="volleyball", target_date="2026-09-29", source_url="u",
            columns={
                "link": [f"[A{i} B{i} 29/09/2026 7:00 PM]"
                         f"(https://f/m/a{i}/{109540 + i})"
                         for i in range(rows)],
                "home": [f"A{i}" for i in range(rows)],
                "away": [f"B{i}" for i in range(rows)],
                "kickoff": ["19:00"] * rows,
                "probabilities": ["62 38"] * rows,
                "pick": ["1"] * rows,
            }, row_count=rows)

    @pytest.mark.parametrize("rows", [1, 10, 64, 200])
    def test_a_board_of_any_size_is_recognised(self, rows):
        from slumdog.relay_columns import (
            looks_like_columns_body,
            serialise_columns,
        )

        assert looks_like_columns_body(serialise_columns(self._board_of(rows)))

    def test_a_big_board_still_parses_to_events(self, tmp_path):
        from slumdog.relay_columns import serialise_columns

        (tmp_path / "body.txt").write_bytes(
            serialise_columns(self._board_of(64)))
        events = parse_capture({
            "body_path": "body.txt", "sport": "volleyball",
            "target_date": "2026-09-29",
            "captured_at": "2026-09-29T06:00:00+00:00",
            "source_url": "u", "sha256": "abc"}, root=tmp_path)
        assert len(events) == 64

    def test_the_marker_still_leads_the_body_for_a_human(self):
        from slumdog.relay_columns import serialise_columns

        body = serialise_columns(self._board_of(64))
        assert b"columns_v1" in body[:120]

    def test_html_and_json_are_still_told_apart(self):
        from slumdog.relay_columns import looks_like_columns_body

        assert not looks_like_columns_body(
            b"<html><body><div class='rcnt'></div></body></html>")
        assert not looks_like_columns_body(b'[[{"id": 1}]]')
        assert not looks_like_columns_body(b'{"format": "something_else"}')
        assert not looks_like_columns_body(b"{ truncated")
