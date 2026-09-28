import json

import pytest

from slumdog.forebet import (
    CONTENT_MARKER,
    ForebetCollector,
    source_url,
    unwrap_reader,
    validate_html_body,
)
from slumdog.sports import SPORTS


def test_source_urls_are_date_addressable():
    for spec in SPORTS.values():
        url = source_url(spec, "2026-08-22")
        assert url.startswith("https://www.forebet.com/")
        if not spec.current_only:
            assert "2026-08-22" in url


def test_reader_wrapper_requires_exact_provenance():
    source = "https://www.forebet.com/en/tennis/predictions/2026-08-22"
    body = b"real body data that is long enough"
    raw = f"Title: Tennis\n\nURL Source: {source}\n\n{CONTENT_MARKER}".encode() + body
    assert unwrap_reader(raw, source) == body
    with pytest.raises(ValueError, match="source URL mismatch"):
        unwrap_reader(raw, source + "?other=1")


def test_reader_rejects_forebet_404_content():
    source = "https://www.forebet.com/missing"
    raw = (
        f"URL Source: {source}\n\n{CONTENT_MARKER}"
        "## Not what you were looking for?\nForebet 404 Error"
    ).encode()
    with pytest.raises(ValueError, match="404 content"):
        unwrap_reader(raw, source)


def test_html_validation_rejects_false_success_and_accepts_sport_date():
    valid = b"<html>Basketball predictions for 22/08/2026" + b"x" * 100
    validate_html_body(valid, "basketball", "2026-08-22")
    with pytest.raises(ValueError, match="404"):
        validate_html_body(b"<html>Not what you were looking for?" + b"x" * 100,
                           "basketball", "2026-08-22")


def test_capture_all_is_fail_soft_and_writes_receipt(tmp_path, monkeypatch):
    collector = ForebetCollector(tmp_path, workers=2)

    def fake_fetch(sport, target_date):
        if sport == "mma":
            raise RuntimeError("temporary")
        directory = tmp_path / "data" / "raw" / sport / target_date
        directory.mkdir(parents=True, exist_ok=True)
        body = directory / "body.txt"
        meta = directory / "body.json"
        body.write_text("raw")
        from slumdog.forebet import RawCapture
        return RawCapture(sport, target_date, "2026-08-22T00:00:00+00:00", "u", "r", "html", "abc", 3,
                          str(body.relative_to(tmp_path)), str(meta.relative_to(tmp_path)))

    monkeypatch.setattr(collector, "_fetch", fake_fetch)
    rows = collector.capture_all("2026-08-22")
    assert len(rows) == len(SPORTS) - 1
    receipt = json.loads((tmp_path / "data" / "reports" / "capture_2026-08-22.json").read_text())
    assert receipt["failures"] == ["mma:RuntimeError:temporary"]


def test_football_validation_parses_html_wrapped_payload():
    from slumdog.forebet import validate_football_json_body
    body = b'<html><body>[[{"id":"1","Pred_1":"50","Pred_2":"50"}]]</body></html>'
    validate_football_json_body(body)


def test_football_validation_rejects_truncated_html_wrapped_json():
    from slumdog.forebet import validate_football_json_body
    body = b'<html><body>[[{"id":"1","Pred_1":"50"},{"id":"2"'
    with pytest.raises(ValueError, match="football JSON body missing"):
        validate_football_json_body(body)


def test_football_fetch_retries_truncated_then_succeeds(tmp_path, monkeypatch):
    from slumdog import forebet as forebet_mod
    good = b'<html><body>[[{"id":"1","Pred_1":"50","Pred_2":"50"}]]</body></html>'
    bad = b'<html><body>[[{"id":"1","Pred_1":"50"},{"id":"2"'
    responses = [bad, good]

    def fake_relay_markdown(relay, target, timeout):
        return responses.pop(0)

    monkeypatch.setattr(forebet_mod, "relay_get_markdown", fake_relay_markdown)
    monkeypatch.setattr(forebet_mod, "_sleep_with_jitter", lambda *a, **k: None)
    cap = forebet_mod.ForebetCollector(tmp_path)._fetch("football", "2025-09-21")
    assert cap.bytes == len(good)
    assert (tmp_path / cap.body_path).read_bytes() == good


class TestCaptureTimingInstrumentation:
    """Priority 1 (2026-09-28): before this, ``forward_shadow_batch.py`` had
    zero internal timing (``grep -c 'time.time()\\|elapsed'`` was 0), so
    Forward Shadow #33 (run 36426785929) could only be reported as ">=1h56m
    on one undifferentiated step" — nobody could say which sport-date cost
    what. ``capture_timing`` is per-sport-date elapsed time, request count
    and outcome, the measurement Priority 1's actual fix needs to prove
    itself against."""

    def test_a_successful_capture_is_timed_and_labelled_by_route(
        self, tmp_path, monkeypatch
    ):
        from slumdog.forebet import ForebetCollector, RawCapture

        def fake_fetch(self, sport, target_date):
            self.before_request()  # the initial-attempt call every _fetch makes
            self.before_request()  # a simulated column-route attempt
            return RawCapture(
                sport, target_date, "2026-09-29T04:00:00+00:00", "u", "r",
                "columns_v1", "abc", 3, "p.txt", "p.json", route="relay_columns")

        monkeypatch.setattr(
            "slumdog.forebet.ForebetCollector._fetch", fake_fetch)
        collector = ForebetCollector(root=tmp_path, timeout=5, workers=1)
        collector.capture_selected(
            "2026-09-29", sports=["volleyball"], pause_seconds=0.01)
        receipt = json.loads(
            (tmp_path / "data" / "reports" / "capture_2026-09-29.json")
            .read_text())
        [timing] = receipt["capture_timing"]
        assert timing["sport"] == "volleyball"
        assert timing["outcome"] == "CAPTURED:relay_columns"
        assert timing["requests"] == 2
        assert timing["elapsed_seconds"] >= 0

    def test_a_column_route_gap_is_labelled_from_the_raised_message(
        self, tmp_path, monkeypatch
    ):
        def fake_fetch(self, sport, target_date):
            raise ValueError(
                f"{sport} {target_date}: html capture rejected and column "
                f"capture returned COVERAGE_GAP: missing required column(s)")

        monkeypatch.setattr(
            "slumdog.forebet.ForebetCollector._fetch", fake_fetch)
        from slumdog.forebet import ForebetCollector
        collector = ForebetCollector(root=tmp_path, timeout=5, workers=1)
        collector.capture_selected(
            "2026-09-29", sports=["rugby"], pause_seconds=0.01)
        receipt = json.loads(
            (tmp_path / "data" / "reports" / "capture_2026-09-29.json")
            .read_text())
        [timing] = receipt["capture_timing"]
        assert timing["outcome"] == "COVERAGE_GAP"
        assert receipt["failures"] == [
            "rugby:ValueError:rugby 2026-09-29: html capture rejected and "
            "column capture returned COVERAGE_GAP: missing required "
            "column(s)"]

    def test_an_unclassified_exception_is_labelled_raised_not_silently_dropped(
        self, tmp_path, monkeypatch
    ):
        def fake_fetch(self, sport, target_date):
            raise RuntimeError("connection reset")

        monkeypatch.setattr(
            "slumdog.forebet.ForebetCollector._fetch", fake_fetch)
        from slumdog.forebet import ForebetCollector
        collector = ForebetCollector(root=tmp_path, timeout=5, workers=1)
        collector.capture_selected(
            "2026-09-29", sports=["mma"], pause_seconds=0.01)
        receipt = json.loads(
            (tmp_path / "data" / "reports" / "capture_2026-09-29.json")
            .read_text())
        [timing] = receipt["capture_timing"]
        assert timing["outcome"] == "RAISED"

    def test_the_parallel_path_is_not_timed(self, tmp_path, monkeypatch):
        # capture_timing is a serial-path (pause_seconds>0) instrument only;
        # the parallel path (workers>1, used for historical backfill, not
        # the timing-sensitive forward pass) is left as it was.
        from slumdog.forebet import ForebetCollector, RawCapture

        def fake_fetch(self, sport, target_date):
            return RawCapture(
                sport, target_date, "2026-09-29T04:00:00+00:00", "u", "r",
                "html", "abc", 3, "p.txt", "p.json", route="direct")

        monkeypatch.setattr(
            "slumdog.forebet.ForebetCollector._fetch", fake_fetch)
        collector = ForebetCollector(root=tmp_path, timeout=5, workers=2)
        collector.capture_selected("2026-09-29", sports=["football"])
        receipt = json.loads(
            (tmp_path / "data" / "reports" / "capture_2026-09-29.json")
            .read_text())
        assert receipt["capture_timing"] == []

    def test_serial_true_times_a_single_sport_even_at_pause_zero(
        self, tmp_path, monkeypatch
    ):
        """The probe used to select the timed path with pause_seconds=0.001
        — small enough to be free (a single-sport call never sleeps) but
        truthy enough to pick the branch, which nothing marked as
        load-bearing and a future refactor could silently undo. serial=True
        must select the same path outright, at pause_seconds=0."""
        from slumdog.forebet import ForebetCollector, RawCapture

        def fake_fetch(self, sport, target_date):
            self.before_request()
            return RawCapture(
                sport, target_date, "2026-09-29T04:00:00+00:00", "u", "r",
                "columns_v1", "abc", 3, "p.txt", "p.json",
                route="relay_columns")

        monkeypatch.setattr(
            "slumdog.forebet.ForebetCollector._fetch", fake_fetch)
        collector = ForebetCollector(root=tmp_path, timeout=5, workers=1)
        collector.capture_selected(
            "2026-09-29", sports=["volleyball"], pause_seconds=0,
            serial=True)
        receipt = json.loads(
            (tmp_path / "data" / "reports" / "capture_2026-09-29.json")
            .read_text())
        [timing] = receipt["capture_timing"]
        assert timing["sport"] == "volleyball"
        assert timing["requests"] == 1

    def test_on_capture_timing_without_serial_is_refused(
        self, tmp_path, monkeypatch
    ):
        """A callback that can only ever fire on the serial path must be
        refused outright when the caller did not select that path, not
        silently ignored — an empty capture_timing would look identical to
        a callback that fired zero times because nothing failed."""
        from slumdog.forebet import ForebetCollector

        collector = ForebetCollector(root=tmp_path, timeout=5, workers=2)
        with pytest.raises(ValueError, match="on_capture_timing requires"):
            collector.capture_selected(
                "2026-09-29", sports=["football"], pause_seconds=0,
                on_capture_timing=lambda entry: None)

    def test_a_caller_supplied_before_request_still_runs_under_the_counter(
        self, tmp_path, monkeypatch
    ):
        # The counting wrapper must delegate to whatever before_request the
        # caller already installed (e.g. a request budget), not replace it.
        from slumdog.forebet import ForebetCollector, RawCapture

        outer_calls = {"n": 0}

        def fake_fetch(self, sport, target_date):
            self.before_request()
            return RawCapture(
                sport, target_date, "2026-09-29T04:00:00+00:00", "u", "r",
                "html", "abc", 3, "p.txt", "p.json", route="direct")

        monkeypatch.setattr(
            "slumdog.forebet.ForebetCollector._fetch", fake_fetch)
        collector = ForebetCollector(
            root=tmp_path, timeout=5, workers=1,
            before_request=lambda: outer_calls.__setitem__(
                "n", outer_calls["n"] + 1))
        collector.capture_selected(
            "2026-09-29", sports=["hockey"], pause_seconds=0.01)
        assert outer_calls["n"] == 1
        # And the caller's own before_request is restored afterwards.
        assert collector.before_request is not None


class TestCaptureSelectedRefreshParams:
    def test_receipt_name_validation(self, tmp_path):
        from slumdog.forebet import ForebetCollector
        collector = ForebetCollector(root=tmp_path, timeout=5, workers=1)
        with pytest.raises(ValueError, match="bad receipt_name"):
            collector.capture_selected(
                "2026-09-23", sports=["hockey"],
                receipt_name="capture_refresh_2026-09-23/evil.json")
        with pytest.raises(ValueError, match="bad receipt_name"):
            collector.capture_selected(
                "2026-09-23", sports=["hockey"],
                receipt_name="capture_refresh_2026-09-23.txt")

    def test_force_skips_raw_dir_reuse(self, tmp_path, monkeypatch):
        """force=True must re-fetch even when a same-date raw capture dir
        exists (the refresh needs a genuinely fresh snapshot)."""
        from slumdog.forebet import ForebetCollector
        collector = ForebetCollector(root=tmp_path, timeout=5, workers=1)
        raw = tmp_path / "data" / "raw" / "hockey" / "2026-09-23"
        raw.mkdir(parents=True, exist_ok=True)
        # A valid prior capture pair (sidecar metadata is what reuses).
        (raw / "prior.json").write_text(json.dumps({
            "sport": "hockey", "target_date": "2026-09-23",
            "captured_at": "2026-09-20T04:00:00Z",
            "source_url": "https://example.invalid/x",
            "relay_url": "https://relay.invalid/x",
            "body_format": "html", "sha256": "0" * 64, "bytes": 3,
            "body_path": "data/raw/hockey/2026-09-23/prior.txt",
            "metadata_path": "data/raw/hockey/2026-09-23/prior.json",
            "route": "direct",
        }))

        calls = []

        def _fake_fetch(self, sport, target_date):
            calls.append((sport, target_date))
            raise RuntimeError("offline test: no network")

        monkeypatch.setattr(
            "slumdog.forebet.ForebetCollector._fetch", _fake_fetch)

        # Without force: reuse — the fetch seam stays untouched.
        captures = collector.capture_selected("2026-09-23", sports=["hockey"])
        assert len(captures) == 1 and captures[0].sport == "hockey"
        assert calls == []
        # With force: the raw dir no longer shields the sport from a fetch.
        collector.capture_selected("2026-09-23", sports=["hockey"], force=True)
        assert calls == [("hockey", "2026-09-23")]


def test_the_second_challenge_wording_is_named_not_caught_by_luck():
    """Captured live on the tz=0 JSON endpoint (run 36401440850): 272 bytes
    that matched no marker and failed closed only because the JSON parse
    threw. An HTML board would have survived on its missing sport label."""
    from slumdog.forebet import looks_like_challenge_page

    live = (b"![Image 1: Icon for www.forebet.com](https://www.forebet.com/"
            b"favicon.ico) ## www.forebet.com ## Performing security "
            b"verification This website uses a security service to protect "
            b"against malicious bots. This page is displayed while the "
            b"website verifies you are not a bot.")
    assert looks_like_challenge_page(live)
    assert not looks_like_challenge_page(
        b'[[{"id": 1, "HOST_NAME": "Arsenal"}]]')


class TestTheBoardIsNotTheSource:
    """Run 36402990164 spent 108 seconds and three attempts rendering
    /en/football-tips-and-predictions/predictions/<date>, which does not
    exist. The renderer answers 422 for 'your selector matched nothing',
    which reads exactly like throttling — so a wrong URL looks like a site
    refusing you."""

    def test_footballs_board_is_not_its_json_endpoint(self):
        from slumdog.forebet import board_url, source_url
        from slumdog.sports import SPORTS

        board = board_url(SPORTS["football"], "2026-09-29")
        assert board == ("https://www.forebet.com/en/football-predictions/"
                         "predictions-1x2/2026-09-29")
        assert "getrs.php" in source_url(SPORTS["football"], "2026-09-29")
        assert "getrs.php" not in board

    def test_the_sport_path_slug_is_not_a_board_either(self):
        from slumdog.forebet import board_url
        from slumdog.sports import SPORTS

        assert SPORTS["football"].path not in board_url(
            SPORTS["football"], "2026-09-29")

    def test_every_other_sport_keeps_one_url(self):
        from slumdog.forebet import board_url, source_url
        from slumdog.sports import SPORTS

        for key, spec in SPORTS.items():
            if key == "football":
                continue
            assert board_url(spec, "2026-09-29") == source_url(
                spec, "2026-09-29")
