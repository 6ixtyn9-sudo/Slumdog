"""Offline tests for the kickoff-timezone probe.

The probe is the evidence-gathering step that could lift the EVENT_DAY
track's timezone hold, so its reasoning must be conservative: an
inconclusive sample has to read as "not proven", never as permission.
No test here touches the network.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from scripts.probe_kickoff_timezone import (
    html_board_rows,
    machine_readable_candidates,
    offset_minutes,
    parse_display_time,
    summarise_offsets,
    verdict,
)


def _boom(message: str):
    def _raise(*args, **kwargs):
        raise RuntimeError(message)
    return _raise


class TestDisplayTimeParsing:
    def test_parses_both_published_display_shapes(self):
        assert parse_display_time("25/09/2026 21:00") == dt.datetime(
            2026, 9, 25, 21, 0)
        assert parse_display_time("09/25/2026 9:00 PM") == dt.datetime(
            2026, 9, 25, 21, 0)

    def test_unparseable_text_is_none_not_a_guess(self):
        assert parse_display_time("") is None
        assert parse_display_time("tomorrow") is None


class TestOffset:
    def test_offset_is_positive_when_the_board_runs_ahead_of_utc(self):
        # Board says 21:00, true instant is 19:00 UTC: the board makes the
        # event look two hours later than it is — the dangerous direction.
        assert offset_minutes(
            dt.datetime(2026, 9, 26, 21, 0),
            dt.datetime(2026, 9, 26, 19, 0)) == 120

    def test_offset_measured_in_the_2026_09_26_sample(self):
        # Board showed 09/25 9:00 PM, tz=0 JSON gave 2026-09-26 02:00.
        assert offset_minutes(
            dt.datetime(2026, 9, 25, 21, 0),
            dt.datetime(2026, 9, 26, 2, 0)) == -300

    def test_summary_reports_unanimity(self):
        s = summarise_offsets([120, 120, 120])
        assert s["modal_offset_minutes"] == 120
        assert s["unanimous"] is True
        assert s["agreement"] == 1.0
        assert s["joined"] == 3

    def test_summary_exposes_disagreement(self):
        s = summarise_offsets([120, 120, 60])
        assert s["modal_offset_minutes"] == 120
        assert s["unanimous"] is False
        assert s["agreement"] < 1.0
        assert s["distribution"] == {"60": 1, "120": 2}

    def test_empty_sample_is_not_an_answer(self):
        s = summarise_offsets([])
        assert s["modal_offset_minutes"] is None
        assert s["unanimous"] is False


class TestMachineReadableDetection:
    def test_finds_a_time_element(self):
        hits = machine_readable_candidates(
            '<div><time datetime="2026-09-26T18:00:00Z">18:00</time></div>')
        assert any(h["attribute"] == "datetime" for h in hits)

    def test_finds_an_epoch_data_attribute(self):
        hits = machine_readable_candidates(
            '<span class="date_bah" data-ts="1790000000">18:00</span>')
        assert any(h["sample"] == "1790000000" for h in hits)

    def test_finds_json_ld_start_date(self):
        hits = machine_readable_candidates(
            '<script type="application/ld+json">'
            '{"startDate": "2026-09-26T18:00:00+00:00"}</script>')
        assert any(h["kind"] == "json_ld" for h in hits)

    def test_plain_rendered_text_yields_nothing(self):
        # The real boards look like this: a human-rendered string and no
        # machine-readable instant anywhere.
        hits = machine_readable_candidates(
            '<div class="rcnt"><span class="date_bah">25/09/2026 21:00</span>'
            '<a class="tnmscn" href="/en/football/matches/a-b-123">x</a></div>')
        assert hits == []


class TestBoardRowExtraction:
    def test_extracts_match_id_and_rendered_time(self):
        rows = html_board_rows(
            b'<div class="rcnt">'
            b'<a class="tnmscn" href="/en/football/matches/atlante-fc-cf-monterrey-2468143">x</a>'
            b'<span class="date_bah">09/25/2026 9:00 PM</span></div>'
            b'<div class="rcnt"><span class="date_bah">no link</span></div>')
        assert rows == [
            {"match_id": "2468143", "displayed": "09/25/2026 9:00 PM"}]


class TestVerdict:
    def _report(self, **over):
        report = {
            "football_html_candidates": [],
            "extra_sport_candidates": [],
            "offset": summarise_offsets([]),
        }
        report.update(over)
        return report

    def test_thin_join_is_not_proven(self):
        resolved, lines = verdict(
            self._report(offset=summarise_offsets([0] * 5)))
        assert resolved is False
        assert any("NOT RESOLVED" in line for line in lines)

    def test_disagreeing_offsets_are_not_proven(self):
        offsets = [0] * 30 + [120] * 10
        resolved, _ = verdict(self._report(offset=summarise_offsets(offsets)))
        assert resolved is False

    def test_unanimous_large_sample_resolves(self):
        resolved, lines = verdict(
            self._report(offset=summarise_offsets([-300] * 40)))
        assert resolved is True
        assert any("-300 min" in line for line in lines)

    def test_zero_offset_is_reported_as_one_observation_not_a_guarantee(self):
        _, lines = verdict(self._report(offset=summarise_offsets([0] * 40)))
        assert any("not a guarantee" in line for line in lines)

    def test_machine_readable_field_resolves_on_its_own(self):
        resolved, lines = verdict(self._report(
            football_html_candidates=[
                {"kind": "attribute", "attribute": "data-ts",
                 "sample": "1790000000"}]))
        assert resolved is True
        assert any("MACHINE-READABLE START FOUND" in line for line in lines)


class TestProbeSurvivesFetchFailure:
    """Run 36242469136 died in under a second and wrote nothing, because an
    unhandled fetch error went to stderr. A probe that cannot report is
    worse than no probe: it burns a round trip and teaches nothing."""

    def test_fetch_records_the_error_instead_of_raising(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe

        monkeypatch.setattr(probe, "FETCH_ERRORS", [])
        monkeypatch.setattr(probe, "relay_get_markdown", _boom("relay down"))
        monkeypatch.setattr(probe, "fetch_with_fallback", _boom("403"))

        assert probe.fetch("https://x.invalid", timeout=1) is None
        assert len(probe.FETCH_ERRORS) == 2
        assert {e["route"] for e in probe.FETCH_ERRORS} == {
            "relay_or_direct", "relay_markdown"}

    def test_json_endpoint_tries_markdown_route_first(self, monkeypatch):
        """Production's order, because the html-forced relay mode 401s on
        cloud IPs for the JSON endpoint."""
        import scripts.probe_kickoff_timezone as probe

        order: list[str] = []
        monkeypatch.setattr(probe, "FETCH_ERRORS", [])
        monkeypatch.setattr(
            probe, "relay_get_markdown",
            lambda *a, **k: order.append("markdown") or b"ok")
        monkeypatch.setattr(
            probe, "fetch_with_fallback",
            lambda *a, **k: (order.append("fallback") or b"ok", "relay"))

        probe.fetch("https://x.invalid", timeout=1, json_endpoint=True)
        assert order == ["markdown"]
        order.clear()
        probe.fetch("https://x.invalid", timeout=1)
        assert order == ["fallback"]

    def test_probe_reports_a_total_outage_rather_than_crashing(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe

        monkeypatch.setattr(probe, "FETCH_ERRORS", [])
        monkeypatch.setattr(probe, "relay_get_markdown", _boom("relay down"))
        monkeypatch.setattr(probe, "fetch_with_fallback", _boom("403"))

        report = probe.run_probe(
            "2026-09-27", sport="basketball", timeout=1, pause=0)
        resolved, lines = probe.verdict(report)

        assert resolved is False
        assert report["fetch_errors"]
        assert any("FETCH FAILURES" in line for line in lines)
        assert any("NOT RESOLVED" in line for line in lines)

    def test_crash_is_reported_in_the_verdict(self):
        from scripts.probe_kickoff_timezone import summarise_offsets, verdict

        resolved, lines = verdict({
            "crashed": "RuntimeError: boom",
            "traceback": "Traceback...\nRuntimeError: boom",
            "fetch_errors": [],
            "offset": summarise_offsets([]),
        })
        assert resolved is False
        assert lines[0].startswith("PROBE CRASHED: RuntimeError: boom")


class TestAnnotations:
    """Annotations come back through api.github.com; run logs and artifacts
    come from blob storage. Only the former can be read back automatically."""

    def test_nothing_is_emitted_outside_actions(self, monkeypatch):
        from scripts.probe_kickoff_timezone import emit_annotations

        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
        assert emit_annotations({"a": 1}, ["verdict"]) == []

    def test_verdict_and_report_are_emitted_inside_actions(self, monkeypatch):
        from scripts.probe_kickoff_timezone import emit_annotations

        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        emitted = emit_annotations(
            {"target_date": "2026-09-27", "offset_samples": ["dropped"]},
            ["line one", "line two"])
        assert len(emitted) == 3
        assert emitted[0].startswith("::notice title=Kickoff timezone verdict::")
        # Newlines must be escaped or the annotation is truncated at line one.
        assert "%0A" in emitted[0]
        assert "\n" not in emitted[0]
        assert "2026-09-27" in emitted[1]
        assert "dropped" not in emitted[1]

    def test_escaping_protects_the_workflow_command_syntax(self):
        from scripts.probe_kickoff_timezone import _annotation_escape

        assert _annotation_escape("a::b") == "a%3A%3Ab"
        assert _annotation_escape("50%") == "50%25"


class TestBodyFingerprint:
    """A board that parses to zero rows has three very different causes and
    the probe must distinguish them, or the next run is another guess."""

    def test_identifies_a_real_board(self):
        from scripts.probe_kickoff_timezone import body_fingerprint

        fp = body_fingerprint(b'<div class="rcnt">...</div>')
        assert fp["has_rcnt"] is True
        assert fp["looks_like"] == "board_html"

    def test_identifies_a_relay_markdown_wrapper(self):
        from scripts.probe_kickoff_timezone import body_fingerprint

        fp = body_fingerprint(b"Title: Forebet\n\nMarkdown Content:\n| a | b |")
        assert fp["has_rcnt"] is False
        assert fp["looks_like"] == "relay_markdown_wrapper"

    def test_identifies_a_challenge_page(self):
        from scripts.probe_kickoff_timezone import body_fingerprint

        fp = body_fingerprint(b"<html><title>Just a moment...</title></html>")
        assert fp["looks_like"] == "challenge_page"

    def test_empty_body_is_not_mistaken_for_a_board(self):
        from scripts.probe_kickoff_timezone import body_fingerprint

        assert body_fingerprint(None)["looks_like"] == "empty"

    def test_verdict_calls_out_a_board_that_never_arrived(self):
        from scripts.probe_kickoff_timezone import summarise_offsets, verdict

        resolved, lines = verdict({
            "fetch_errors": [],
            "offset": summarise_offsets([]),
            "football_board_body": {
                "bytes": 5994, "has_rcnt": False,
                "looks_like": "challenge_page", "sample": "Just a moment"},
        })
        assert resolved is False
        assert any("RETURNED NO LISTING ROWS" in line for line in lines)


class TestRouteDiagnostic:
    """The capture path asks the relay for `X-Return-Format: html` and got a
    challenge page. If another mode returns a real board, that is the fix for
    the coverage collapse itself."""

    def test_every_mode_is_tried_and_fingerprinted(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe

        seen: list[dict] = []

        def _fake(url, headers, *, timeout):
            seen.append(headers)
            if headers.get("X-Respond-With") == "html" and headers.get("X-Engine"):
                return b'<div class="rcnt">real board</div>'
            return b"<html><title>Just a moment...</title></html>"

        monkeypatch.setattr(probe, "relay_request", _fake)
        out = probe.probe_routes("https://x.invalid", timeout=1, pause=0)

        assert set(out) == {
            "return_format_html", "html_browser_engine", "html_cf_engine",
            "respond_with_html_browser", "markdown_reader"}
        assert out["respond_with_html_browser"]["has_rcnt"] is True
        assert out["return_format_html"]["looks_like"] == "challenge_page"
        assert len(seen) == 5
        # The engine variants must actually differ from the failing baseline,
        # or the comparison proves nothing.
        engines = {h.get("X-Engine") for h in seen}
        assert {"browser", "cf-browser-rendering"} <= engines

    def test_a_failing_mode_is_recorded_not_raised(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe

        monkeypatch.setattr(probe, "relay_request", _boom("401"))
        out = probe.probe_routes("https://x.invalid", timeout=1, pause=0)
        assert all("error" in fp for fp in out.values())

    def test_verdict_names_a_working_route(self):
        from scripts.probe_kickoff_timezone import summarise_offsets, verdict

        _, lines = verdict({
            "fetch_errors": [], "offset": summarise_offsets([]),
            "route_diagnostic_url": "https://x.invalid",
            "route_diagnostic": {
                "return_format_html": {"bytes": 5931, "has_rcnt": False,
                                       "looks_like": "challenge_page"},
                "respond_with_html": {"bytes": 150000, "has_rcnt": True,
                                      "looks_like": "board_html"},
            },
        })
        assert any("WORKING CAPTURE ROUTE(S): respond_with_html" in l for l in lines)

    def test_verdict_says_so_when_no_route_works(self):
        from scripts.probe_kickoff_timezone import summarise_offsets, verdict

        _, lines = verdict({
            "fetch_errors": [], "offset": summarise_offsets([]),
            "route_diagnostic": {
                "return_format_html": {"bytes": 5931, "has_rcnt": False,
                                       "looks_like": "challenge_page"}},
        })
        assert any("NO RELAY MODE RETURNED A BOARD" in l for l in lines)


class TestChallengePageRejection:
    """Challenge pages were being stored as genuine captures for the two
    current_only sports, because only the lenient label check applied."""

    def test_challenge_page_is_rejected_for_a_current_only_sport(self):
        import pytest as _pytest

        from slumdog.forebet import validate_html_body

        body = (b'<html lang="en-US"><head><title>Just a moment...</title>'
                b'</head><body>esoccer</body></html>' + b"x" * 200)
        with _pytest.raises(ValueError, match="challenge page"):
            validate_html_body(body, "esoccer", "2026-09-27")

    def test_challenge_page_is_rejected_for_a_dated_sport(self):
        import pytest as _pytest

        from slumdog.forebet import validate_html_body

        body = (b'<html><body><div class="challenge-platform">basketball '
                b'27/09/2026</div></body></html>' + b"x" * 200)
        with _pytest.raises(ValueError, match="challenge page"):
            validate_html_body(body, "basketball", "2026-09-27")

    def test_a_real_board_still_validates(self):
        from slumdog.forebet import validate_html_body

        body = (b'<html><body>basketball <div class="rcnt">'
                b'<span class="date_bah">27/09/2026 18:00</span></div>'
                b'</body></html>' + b"x" * 200)
        validate_html_body(body, "basketball", "2026-09-27")

    def test_detector_is_exposed_for_reuse(self):
        from slumdog.forebet import looks_like_challenge_page

        assert looks_like_challenge_page(b"<title>Just a moment...</title>")
        assert not looks_like_challenge_page(b'<div class="rcnt">ok</div>')


class TestEndpointHunt:
    """Football still captures because it uses a JSON endpoint that is not
    bot-checked. The hunt asks whether the blocked sports have one too."""

    ARCHIVED = (
        b'<html><head><script src="/js/bsk.js"></script></head><body>'
        b'<div class="rcnt" id="r1"><span class="date_bah">27/09/2026 '
        b'18:00</span></div><div class="rcnt" id="r2"></div>'
        b'<script>xmlhttp.open("GET","/scripts/getrs_bsk.php?ln=en&in="+d);'
        b'</script></body></html>'
    )

    def test_php_endpoints_are_recovered_from_markup(self):
        from scripts.probe_kickoff_timezone import endpoint_candidates

        found = endpoint_candidates(
            self.ARCHIVED, "https://www.forebet.com/en/basketball/predictions")
        assert found
        assert found[0].startswith("https://www.forebet.com/scripts/getrs_bsk.php")

    def test_offsite_references_are_ignored(self):
        from scripts.probe_kickoff_timezone import endpoint_candidates

        found = endpoint_candidates(
            b'<script src="https://ads.example.com/x.php"></script>',
            "https://www.forebet.com/en/basketball/predictions")
        assert found == []

    def test_hunt_reports_rows_and_markup_samples(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe

        monkeypatch.setattr(probe, "archived_html",
                            lambda url, *, timeout: ("snap", self.ARCHIVED))
        out = probe.hunt_endpoints("https://www.forebet.com/en/basketball/"
                                   "predictions", timeout=1, pause=0)
        assert out["rcnt_rows"] == 2
        assert "date_bah" in out["markup_samples"]
        assert any("getrs_bsk.php" in u for u in out["php_refs"])

    def test_hunt_failure_is_recorded_not_raised(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe

        monkeypatch.setattr(probe, "archived_html", _boom("no snapshot"))
        out = probe.hunt_endpoints("https://x.invalid", timeout=1, pause=0)
        assert "error" in out and "no snapshot" in out["error"]

    def test_a_json_returning_candidate_is_called_out(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe
        from scripts.probe_kickoff_timezone import summarise_offsets, verdict

        monkeypatch.setattr(probe, "relay_request",
                            lambda url, headers, *, timeout: b'[[{"id":1}]]')
        tests = probe.test_candidates(
            ["https://www.forebet.com/scripts/getrs_bsk.php"],
            timeout=1, pause=0)
        assert all(fp["json_like"] for fp in tests.values())

        _, lines = verdict({"fetch_errors": [], "offset": summarise_offsets([]),
                            "endpoint_tests": tests})
        assert any("JSON ENDPOINT WORKS FOR THIS SPORT" in l for l in lines)

    def test_a_challenged_candidate_is_not_called_a_win(self):
        from scripts.probe_kickoff_timezone import summarise_offsets, verdict

        _, lines = verdict({
            "fetch_errors": [], "offset": summarise_offsets([]),
            "endpoint_tests": {"https://x/getrs_bsk.php": {
                "bytes": 5931, "looks_like": "challenge_page",
                "json_like": False}}})
        assert not any("JSON ENDPOINT WORKS" in l for l in lines)


class TestArchiveLookup:
    """The availability API only answers for an exact URL, and the sport
    boards' exact paths are not archived. The CDX index does prefix search."""

    ROWS = json.dumps([
        ["urlkey", "timestamp", "original", "mimetype", "statuscode"],
        ["com,forebet)/en/basketball", "20250101000000",
         "https://www.forebet.com/en/basketball", "text/html", "200"],
        ["com,forebet)/en/basketball/predictions/2025-01-02", "20250102000000",
         "https://www.forebet.com/en/basketball/predictions/2025-01-02",
         "text/html", "200"],
    ]).encode()

    def test_prefix_search_finds_a_sibling_board(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe

        calls: list[str] = []

        def _fetch(url, *, timeout):
            calls.append(url)
            if "cdx" in url:
                return self.ROWS
            return b"<html>archived</html>"

        monkeypatch.setattr(probe, "direct_fetch", _fetch)
        url, body = probe.archived_html(
            "https://www.forebet.com/en/basketball/predictions", timeout=1)
        assert "id_/" in url and body == b"<html>archived</html>"
        assert any("matchType=prefix" in c for c in calls)

    def test_empty_index_falls_back_to_the_parent_path(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe

        patterns: list[str] = []

        def _fetch(url, *, timeout):
            if "cdx" in url:
                patterns.append(url)
                return b"[]"
            return b""

        monkeypatch.setattr(probe, "direct_fetch", _fetch)
        with pytest.raises(RuntimeError, match="no archived snapshot"):
            probe.archived_html(
                "https://www.forebet.com/en/basketball/predictions", timeout=1)
        assert len(patterns) == 2
        assert "predictions" not in patterns[1].split("url=")[1].split("&")[0]

    def test_javascript_is_mined_for_endpoints(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe

        page = b'<html><script src="/js/bsk.js"></script></html>'
        monkeypatch.setattr(
            probe, "archived_html",
            lambda url, *, timeout: (
                "https://web.archive.org/web/20250101000000id_/x", page))
        monkeypatch.setattr(
            probe, "archived_bytes",
            lambda ts, url, *, timeout, kind="id_":
                b'xhr.open("GET","/scripts/getrs_bsk.php?ln=en")')

        out = probe.hunt_endpoints(
            "https://www.forebet.com/en/basketball/predictions",
            timeout=1, pause=0)
        assert any("getrs_bsk.php" in ref for ref in out["js_php_refs"])
        assert any("getrs_bsk.php" in ref for ref in out["php_refs"])

    def test_a_broken_script_fetch_does_not_sink_the_hunt(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe

        monkeypatch.setattr(
            probe, "archived_html",
            lambda url, *, timeout: (
                "https://web.archive.org/web/20250101000000id_/x",
                b'<html><script src="/js/bsk.js"></script></html>'))
        monkeypatch.setattr(probe, "archived_bytes", _boom("410 gone"))

        out = probe.hunt_endpoints("https://www.forebet.com/en/basketball/p",
                                   timeout=1, pause=0)
        assert out["js_php_refs"] == ["! https://www.forebet.com/js/bsk.js: RuntimeError"]

    def test_control_absence_is_reported_as_a_miner_fault(self):
        from scripts.probe_kickoff_timezone import summarise_offsets, verdict

        _, lines = verdict({
            "fetch_errors": [], "offset": summarise_offsets([]),
            "endpoint_hunt_control": {"php_refs": []}})
        assert any("the miner itself found nothing" in l for l in lines)


class TestJavascriptMiningDetails:
    """The first hunt skipped `/includes/js/all.js?v=378` — the very file the
    board's fetch lives in — because the filter matched on the whole URL."""

    PAGE = (b'<html><script src="/includes/js/all.js?v=378"></script>'
            b'<script src="https://ads.example.com/a.js"></script></html>')

    def _hunt(self, monkeypatch, js: bytes):
        import scripts.probe_kickoff_timezone as probe

        fetched: list[str] = []

        def _bytes(ts, url, *, timeout, kind="id_"):
            fetched.append(url)
            return js

        monkeypatch.setattr(
            probe, "archived_html",
            lambda url, *, timeout: (
                "https://web.archive.org/web/20240522214706id_/x", self.PAGE))
        monkeypatch.setattr(probe, "archived_bytes", _bytes)
        out = probe.hunt_endpoints(
            "https://www.forebet.com/en/basketball/predictions",
            timeout=1, pause=0)
        return out, fetched

    def test_a_versioned_script_is_still_fetched(self, monkeypatch):
        out, fetched = self._hunt(
            monkeypatch, b'open("GET","/scripts/getrs.php?ln=en&sp=2")')
        assert fetched == ["https://www.forebet.com/includes/js/all.js?v=378"]
        assert any("getrs.php" in ref for ref in out["php_refs"])

    def test_the_surrounding_javascript_is_captured(self, monkeypatch):
        out, _ = self._hunt(
            monkeypatch, b'var u="/scripts/getrs.php?ln="+ln+"&sp="+sport;')
        assert "sp=" in out["js_context"]

    def test_retries_survive_a_refused_connection(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe

        calls = {"n": 0}

        class _FakeResponse:
            def read(self):
                return b"ok"

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def _urlopen(request, timeout):
            calls["n"] += 1
            if calls["n"] < 3:
                raise OSError("[Errno 111] Connection refused")
            return _FakeResponse()

        monkeypatch.setattr(probe.time, "sleep", lambda s: None)
        import urllib.request

        monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
        assert probe.direct_fetch("https://web.archive.org/x", timeout=1) == b"ok"
        assert calls["n"] == 3

    def test_exhausted_retries_raise_the_last_error(self, monkeypatch):
        import urllib.request

        import scripts.probe_kickoff_timezone as probe

        monkeypatch.setattr(probe.time, "sleep", lambda s: None)
        monkeypatch.setattr(urllib.request, "urlopen", _boom("refused"))
        with pytest.raises(RuntimeError, match="refused"):
            probe.direct_fetch("https://web.archive.org/x", timeout=1)
