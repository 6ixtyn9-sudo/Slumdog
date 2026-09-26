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


class TestSportCodeHunt:
    """The basketball bundle builds the same getrs.php URL football uses,
    differing only in tp=. Finding that code turns a blocked sport into a
    JSON capture."""

    def test_archive_rewriting_is_stripped_from_refs(self):
        from scripts.probe_kickoff_timezone import normalise_ref

        assert normalise_ref(
            "https://web.archive.org/web/20240601052009/"
            "https://www.forebet.com/scripts/getrs.php?ln="
        ) == "https://www.forebet.com/scripts/getrs.php?ln="

    def test_scheme_less_reference_is_repaired(self):
        from scripts.probe_kickoff_timezone import normalise_ref

        assert normalise_ref(
            "https://www.forebet.com/en/basketball/forebet.com/scripts/"
            "getjson.php?gdt=") == "https://forebet.com/scripts/getjson.php?gdt="

    def test_tp_literals_are_recovered_from_the_page(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe

        html = b'<script>var q="&tp=" ; loadr({tp:"bsk"}); go("tp=xx")</script>'
        monkeypatch.setattr(probe, "archived_html",
                            lambda url, *, timeout: ("snap", html))
        out = probe.hunt_endpoints("https://www.forebet.com/en/basketball/p",
                                   timeout=1, pause=0)
        assert "bsk" in out["tp_literals"]
        assert out["inline_script_context"]

    def test_a_code_returning_rows_is_declared_the_capture_route(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe
        from scripts.probe_kickoff_timezone import summarise_offsets, verdict

        def _relay(url, headers, *, timeout):
            if "tp=bsk" in url:
                return b'<html><body>[[{"host":"A","away":"B"}]]</body></html>'
            return b"<html>no</html>"

        monkeypatch.setattr(probe, "relay_request", _relay)
        out = probe.test_tp_candidates(
            "2026-09-27", ["bas", "bsk"], timeout=1, pause=0)
        assert out["bsk"]["json_like"] and not out["bas"]["json_like"]

        _, lines = verdict({"fetch_errors": [], "offset": summarise_offsets([]),
                            "tp_candidates": out})
        assert any("SPORT CODE FOUND: tp=bsk" in l for l in lines)

    def test_the_known_football_code_is_not_retried(self, monkeypatch):
        """tp=1x2 is football's; testing it would prove nothing about the
        blocked sports and would waste a rate-limited request."""
        import scripts.probe_kickoff_timezone as probe

        calls: list[str] = []
        monkeypatch.setattr(probe, "relay_request",
                            lambda url, headers, *, timeout: calls.append(url) or b"")
        monkeypatch.setattr(probe, "hunt_endpoints",
                            lambda page, *, timeout, pause: {
                                "tp_literals": ["1x2", "bsk"], "php_refs": []})
        monkeypatch.setattr(probe, "fetch", lambda *a, **k: b"")
        monkeypatch.setattr(probe, "test_tp_candidates",
                            lambda date, values, *, timeout, pause:
                                {"_values": values})
        monkeypatch.setattr(probe, "markdown_modes", lambda *a, **k: {})
        monkeypatch.setattr(probe, "fetch_matrix", lambda *a, **k: {})
        monkeypatch.setattr(probe, "api_endpoint_sweep", lambda *a, **k: {})
        monkeypatch.setattr(probe, "save_page_now", lambda *a, **k: {})
        report = probe.run_probe("2026-09-27", sport="basketball",
                                 timeout=1, pause=0, run_hunt=True)
        assert "1x2" not in report["tp_candidates"]["_values"]
        assert "bsk" in report["tp_candidates"]["_values"]


class TestFetchMatrix:
    """No JSON twin exists for the blocked sports, so the remaining question
    is whether any host or fetcher returns their board HTML at all."""

    def _matrix(self, monkeypatch, responder):
        import scripts.probe_kickoff_timezone as probe

        monkeypatch.setattr(probe, "direct_fetch",
                            lambda url, *, timeout, attempts=3: responder(url))
        monkeypatch.setattr(probe, "relay_request",
                            lambda url, headers, *, timeout: responder(url))
        return probe.fetch_matrix("2026-09-27", "basketball",
                                  timeout=1, pause=0)

    def test_the_mobile_host_is_tried(self, monkeypatch):
        seen: list[str] = []

        def _respond(url):
            seen.append(url)
            return b"<html></html>"

        out = self._matrix(monkeypatch, _respond)
        assert any("m.forebet.com" in u for u in seen)
        assert set(out) >= {"www_direct", "mobile_direct",
                            "mobile_relay_markdown", "codetabs_proxy"}

    def test_a_route_with_rows_is_named_as_the_capture_route(self, monkeypatch):
        from scripts.probe_kickoff_timezone import summarise_offsets, verdict

        def _respond(url):
            if "m.forebet.com" in url and "r.jina" not in url:
                return (b'<div class="rcnt" itemprop="x">A v B</div>' * 3)
            return b"<html><title>Just a moment...</title></html>"

        out = self._matrix(monkeypatch, _respond)
        assert out["mobile_direct"]["rows"] == 3
        assert out["www_direct"]["looks_like"] == "challenge_page"

        _, lines = verdict({"fetch_errors": [], "offset": summarise_offsets([]),
                            "fetch_matrix": out})
        assert any("BOARD HTML RECOVERED VIA: mobile_direct" in l for l in lines)

    def test_every_failure_is_isolated(self, monkeypatch):
        out = self._matrix(monkeypatch, _boom("blocked"))
        assert len(out) == 6 and all("error" in fp for fp in out.values())


class TestMarkdownModes:
    """Markdown is the only route that clears the bot check, so coverage now
    depends on whether Markdown carries a match identity and a kickoff time —
    the plain variant has the numbers but names no teams."""

    def _run(self, monkeypatch, responder):
        import scripts.probe_kickoff_timezone as probe

        monkeypatch.setattr(probe, "relay_request",
                            lambda url, headers, *, timeout: responder(headers))
        return probe.markdown_modes("2026-09-27", "basketball",
                                    timeout=1, pause=0)

    def test_every_variant_is_asked_for(self, monkeypatch):
        seen: list[dict] = []
        out = self._run(monkeypatch, lambda h: seen.append(h) or b"x" * 500)
        assert set(out) == {"plain", "text_format", "links_summary",
                            "target_selector"}
        assert {"text", None} & {h.get("X-Return-Format") for h in seen}
        assert any(h.get("X-With-Links-Summary") == "true" for h in seen)

    def test_row_links_are_counted_and_deduplicated(self, monkeypatch):
        link = (b"https://www.forebet.com/en/basketball/predictions/"
                b"lakers-vs-heat-1234567")
        out = self._run(monkeypatch, lambda h: b"pad" * 200 + link + b" " + link)
        assert out["plain"]["match_links"] == 1
        assert out["plain"]["link_sample"][0].endswith("1234567")

    def test_match_pages_live_under_matches_not_predictions(self, monkeypatch):
        """The original regex demanded /predictions/, so it scored 124 real
        match links as 1 and produced a false 'no identity' verdict."""
        link = (b"https://www.forebet.com/en/football/matches/"
                b"real-salt-lake-new-england-revolution-2426911")
        out = self._run(monkeypatch, lambda h: b"pad" * 200 + link)
        assert out["plain"]["match_links"] == 1
        sport, slug, mid = out["plain"]["link_sample"][0].split("|")
        assert sport == "football"
        assert slug == "real-salt-lake-new-england-revolution"
        assert mid == "2426911"

    def test_the_row_text_around_a_kickoff_is_kept(self, monkeypatch):
        out = self._run(monkeypatch,
                        lambda h: b"x" * 300 + b"Lakers Heat 19:30 done")
        assert "19:30" in out["plain"]["row_context"]
        assert "Lakers" in out["plain"]["row_context"]

    def test_kickoff_clocks_are_counted(self, monkeypatch):
        out = self._run(monkeypatch, lambda h: b"x" * 400 + b" 14:00 19:30 ")
        assert out["plain"]["clocks"] == 2

    def test_a_variant_with_links_is_declared_usable(self, monkeypatch):
        from scripts.probe_kickoff_timezone import summarise_offsets, verdict

        links = b" ".join(
            b"https://www.forebet.com/en/basketball/predictions/a-vs-b-%d"
            % (1000000 + i) for i in range(6))
        out = self._run(monkeypatch,
                        lambda h: b"pad" * 200 + links
                        if h.get("X-With-Links-Summary") else b"pad" * 200)
        _, lines = verdict({"fetch_errors": [], "offset": summarise_offsets([]),
                            "markdown_modes": out})
        assert any("MATCH IDENTITY RECOVERABLE VIA: links_summary" in l
                   for l in lines)

    def test_no_identity_anywhere_is_stated_plainly(self, monkeypatch):
        from scripts.probe_kickoff_timezone import summarise_offsets, verdict

        out = self._run(monkeypatch, lambda h: b"36 64 2 85-86 " * 40)
        _, lines = verdict({"fetch_errors": [], "offset": summarise_offsets([]),
                            "markdown_modes": out})
        assert any("NO MARKDOWN VARIANT CARRIES MATCH IDENTITY" in l
                   for l in lines)

    def test_the_answered_hunt_is_off_by_default(self, monkeypatch):
        """The archive hunt is finished and costs rate-limited requests."""
        import scripts.probe_kickoff_timezone as probe

        monkeypatch.setattr(probe, "fetch", lambda *a, **k: b"")
        monkeypatch.setattr(probe, "probe_routes",
                            lambda *a, **k: {})
        monkeypatch.setattr(probe, "markdown_modes", lambda *a, **k: {"ok": {}})
        monkeypatch.setattr(probe, "hunt_endpoints", _boom("must not run"))
        monkeypatch.setattr(probe, "fetch_matrix", _boom("must not run"))
        report = probe.run_probe("2026-09-27", sport="basketball",
                                 timeout=1, pause=0)
        assert "fetch_matrix" not in report
        assert report["markdown_modes"] == {"ok": {}}


class TestBrowserProbe:
    """Last route standing: every HTTP fetcher is either refused or fed the
    interstitial, and Markdown drops the teams and the clock. A real browser
    is the only thing left that can satisfy the check like a visitor."""

    class _Element:
        def __init__(self, text):
            self._text = text

        def inner_text(self):
            return self._text

        def inner_html(self):
            return f"<span>{self._text}</span>"

    class _Page:
        def __init__(self, html, rows, raises=False):
            self._html, self._rows, self._raises = html, rows, raises
            self.waited = False

        def goto(self, url, **kw):
            self.url = url
            self.visited = getattr(self, "visited", []) + [url]

        def wait_for_timeout(self, ms):
            pass

        def wait_for_selector(self, selector, **kw):
            self.waited = True
            if self._raises:
                raise TimeoutError("no rows")

        def content(self):
            return self._html

        def title(self):
            return "Basketball predictions"

        def query_selector_all(self, selector):
            return self._rows

    def _launcher(self, page):
        class _Browser:
            def new_page(self, **kw):
                return page

            def close(self):
                page.closed = True

        class _Chromium:
            def launch(self, **kw):
                return _Browser()

        class _Play:
            chromium = _Chromium()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return lambda: _Play()

    def test_rendered_rows_are_reported(self):
        from scripts.probe_kickoff_timezone import playwright_fetch

        page = self._Page(
            '<div class="rcnt">x</div><div class="rcnt">y</div>',
            [self._Element("Lakers Heat 27/09/2026 19:30")])
        out = playwright_fetch("https://x.invalid",
                               launcher=self._launcher(page))
        assert out["rows"] == 2
        assert "Lakers Heat" in out["first_row_text"]
        assert page.waited

    def test_a_timeout_still_returns_what_rendered(self):
        from scripts.probe_kickoff_timezone import playwright_fetch

        page = self._Page("<html><title>Just a moment...</title></html>", [],
                          raises=True)
        out = playwright_fetch("https://x.invalid",
                               launcher=self._launcher(page))
        assert out["rows"] == 0
        assert out["looks_like"] == "challenge_page"
        assert "wait_error" in out

    def test_a_failed_install_short_circuits(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe

        monkeypatch.setattr(probe, "install_playwright",
                            lambda: "install failed: chromium: boom")
        monkeypatch.setattr(probe, "playwright_fetch", _boom("must not run"))
        out = probe.browser_probe("2026-09-27", "basketball")
        assert out["install"].startswith("install failed")
        assert "rows" not in out

    def test_install_reports_a_nonzero_exit(self):
        from scripts.probe_kickoff_timezone import install_playwright

        class _Done:
            returncode = 1
            stderr = b"no space left on device"

        assert "no space left" in install_playwright(runner=lambda *a, **k: _Done())

    def test_a_browser_crash_is_recorded_not_raised(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe

        monkeypatch.setattr(probe, "install_playwright", lambda: "ok")
        monkeypatch.setattr(probe, "playwright_fetch", _boom("browser died"))
        out = probe.browser_probe("2026-09-27", "basketball")
        assert "browser died" in out["error"]

    def test_the_verdict_calls_a_working_browser_the_fix(self):
        from scripts.probe_kickoff_timezone import summarise_offsets, verdict

        _, lines = verdict({
            "fetch_errors": [], "offset": summarise_offsets([]),
            "browser_probe": {"rows": 82, "bytes": 231543,
                              "title": "Basketball predictions",
                              "first_row_text": "Lakers Heat 19:30"}})
        assert any("REAL BROWSER GETS THE BOARD: 82 rows" in l for l in lines)
        assert any("Lakers Heat" in l for l in lines)


class TestBrowserWarmup:
    """Chromium ran the challenge JS and still sat on the interstitial, so
    the remaining hypothesis is that clearance is granted on the site root
    and carried into the board by cookie — the way a visitor arrives."""

    def test_the_root_is_visited_before_the_board(self):
        from scripts.probe_kickoff_timezone import playwright_fetch

        page = TestBrowserProbe._Page('<div class="rcnt">x</div>', [])
        out = playwright_fetch(
            "https://www.forebet.com/en/basketball/predictions/2026-09-27",
            launcher=TestBrowserProbe()._launcher(page))
        assert page.visited[0] == "https://www.forebet.com/en/"
        assert page.visited[1].endswith("2026-09-27")
        assert out["warmup"]

    def test_the_verdict_no_longer_hides_the_fingerprint(self):
        from scripts.probe_kickoff_timezone import summarise_offsets, verdict

        _, lines = verdict({
            "fetch_errors": [], "offset": summarise_offsets([]),
            "browser_probe": {"install": "ok", "rows": 0, "bytes": 28664,
                              "looks_like": "challenge_page",
                              "title": "Just a moment...",
                              "wait_error": "TimeoutError"}})
        line = next(l for l in lines if "BROWSER PROBE FOUND NO ROWS" in l)
        assert "28664B" in line and "challenge_page" in line
        assert "TimeoutError" in line


class TestApiSweep:
    """The bundle named six sibling endpoints and the earlier sweep tested
    none of them — it only varied getrs.php's tp value. The bot check is on
    the site, not its APIs, which is the whole reason football survives."""

    def _sweep(self, monkeypatch, direct, relayed=b""):
        import scripts.probe_kickoff_timezone as probe

        monkeypatch.setattr(probe, "direct_fetch",
                            lambda url, *, timeout, attempts=3: direct(url))
        monkeypatch.setattr(probe, "relay_request",
                            lambda url, headers, *, timeout: relayed)
        return probe.api_endpoint_sweep("2026-09-27", timeout=1, pause=0)

    def test_the_untested_endpoints_are_all_tried(self, monkeypatch):
        seen: list[str] = []
        out = self._sweep(monkeypatch, lambda u: seen.append(u) or b"")
        assert set(out) >= {"getjson_gdt", "getftr_int", "get_live_r",
                            "get_menu", "getrs_control"}
        assert any("getjson.php?gdt=2026-09-27" in u for u in seen)

    def test_a_json_answer_is_flagged_and_sampled(self, monkeypatch):
        from scripts.probe_kickoff_timezone import summarise_offsets, verdict

        out = self._sweep(
            monkeypatch,
            lambda u: b'[[{"host":"Lakers","away":"Heat"}]]'
            if "getjson.php" in u else b"<html>no</html>")
        assert out["getjson_gdt"]["direct"]["json_like"]
        _, lines = verdict({"fetch_errors": [], "offset": summarise_offsets([]),
                            "api_sweep": out})
        assert any("API RETURNS DATA: getjson_gdt [direct]" in l for l in lines)
        assert any("Lakers" in l for l in lines)

    def test_the_control_is_not_reported_as_a_discovery(self, monkeypatch):
        """getrs.php answering proves the APIs are open, not that a blocked
        sport was solved."""
        from scripts.probe_kickoff_timezone import summarise_offsets, verdict

        out = self._sweep(monkeypatch,
                          lambda u: b'[[{"x":1}]]' if "getrs.php" in u else b"")
        _, lines = verdict({"fetch_errors": [], "offset": summarise_offsets([]),
                            "api_sweep": out})
        assert not any("API RETURNS DATA" in l for l in lines)
        assert any("not behind the bot check" in l for l in lines)

    def test_relay_is_only_used_when_direct_fails(self, monkeypatch):
        out = self._sweep(monkeypatch, lambda u: b'[[{"x":1}]]')
        assert all("relayed" not in e for e in out.values())

    def test_a_challenged_api_is_labelled(self, monkeypatch):
        out = self._sweep(monkeypatch,
                          lambda u: b"<title>Just a moment...</title>")
        assert out["get_menu"]["direct"]["challenge"] is True


class TestSavePageNow:
    """Archive.org crawls from its own infrastructure and the runner can
    already read snapshots, so if their crawler is allowed through it is a
    free rendering proxy."""

    def test_a_fresh_snapshot_with_rows_is_declared_a_route(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe
        from scripts.probe_kickoff_timezone import summarise_offsets, verdict

        monkeypatch.setattr(probe, "direct_fetch",
                            lambda url, *, timeout, attempts=3: b"saved")
        monkeypatch.setattr(probe, "cdx_snapshots",
                            lambda p, *, limit, timeout:
                                [("20260927120000", "https://x/board")])
        monkeypatch.setattr(probe, "archived_bytes",
                            lambda ts, u, *, timeout, kind="id_":
                                b'<div class="rcnt">A v B</div>' * 40)
        out = probe.save_page_now("https://x/board", timeout=1)
        assert out["rows"] == 40 and out["newest"] == "20260927120000"

        _, lines = verdict({"fetch_errors": [], "offset": summarise_offsets([]),
                            "save_page_now": out})
        assert any("ARCHIVE CRAWLER GETS THE BOARD" in l for l in lines)

    def test_an_archived_challenge_is_not_mistaken_for_success(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe

        monkeypatch.setattr(probe, "direct_fetch",
                            lambda url, *, timeout, attempts=3: b"saved")
        monkeypatch.setattr(probe, "cdx_snapshots",
                            lambda p, *, limit, timeout:
                                [("20260927120000", "https://x/board")])
        monkeypatch.setattr(probe, "archived_bytes",
                            lambda ts, u, *, timeout, kind="id_":
                                b"<title>Just a moment...</title>")
        out = probe.save_page_now("https://x/board", timeout=1)
        assert out["rows"] == 0 and out["challenge"] is True

    def test_a_refused_save_still_reads_the_index(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe

        monkeypatch.setattr(probe, "direct_fetch", _boom("429"))
        monkeypatch.setattr(probe, "cdx_snapshots",
                            lambda p, *, limit, timeout: [])
        out = probe.save_page_now("https://x/board", timeout=1)
        assert "429" in out["save"] and out["snapshots"] == 0

    def test_the_answered_sweeps_are_off_by_default(self, monkeypatch):
        """api_sweep, the getjson crack and Save Page Now are answered; the
        browser attempt is the live question, so it runs by default."""
        import scripts.probe_kickoff_timezone as probe

        monkeypatch.setattr(probe, "fetch", lambda *a, **k: b"")
        monkeypatch.setattr(probe, "probe_routes", lambda *a, **k: {})
        monkeypatch.setattr(probe, "markdown_modes", lambda *a, **k: {})
        monkeypatch.setattr(probe, "api_endpoint_sweep", _boom("must not run"))
        monkeypatch.setattr(probe, "crack_getjson", _boom("must not run"))
        monkeypatch.setattr(probe, "save_page_now", _boom("must not run"))
        monkeypatch.setattr(probe, "browser_probe", lambda *a, **k: {"rows": 0})
        monkeypatch.setattr(probe, "discover_sitemaps", lambda **k: {})
        monkeypatch.setattr(probe, "live_dom_selectors", lambda *a, **k: {})
        report = probe.run_probe("2026-09-27", sport="basketball",
                                 timeout=1, pause=0)
        assert "save_page_now" not in report
        # The browser question is answered; it no longer runs unasked.
        assert "browser_probe" not in report

        asked = probe.run_probe("2026-09-27", sport="basketball",
                                timeout=1, pause=0, run_browser=True)
        assert asked["browser_probe"] == {"rows": 0}


class TestGetJsonCrack:
    """getjson.php answers [] rather than 404 or an interstitial, so the
    endpoint is live and unchallenged and only the arguments are wrong."""

    def _crack(self, monkeypatch, responder):
        import scripts.probe_kickoff_timezone as probe

        monkeypatch.setattr(probe, "relay_request",
                            lambda url, headers, *, timeout: responder(url))
        return probe.crack_getjson("2026-09-27", timeout=1, pause=0)

    def test_date_formats_and_extra_params_are_varied(self, monkeypatch):
        seen: list[str] = []
        out = self._crack(monkeypatch, lambda u: seen.append(u) or b"[]")
        joined = " ".join(seen)
        assert "gdt=27-09-2026" in joined and "gdt=20260927" in joined
        assert "gdt=2026-09-26" in joined  # a past date, in case future is empty
        assert all(fp["empty"] for fp in out.values())

    def test_the_relay_wrapper_is_stripped_before_judging(self, monkeypatch):
        """A wrapped [] must still read as empty, not as 90 bytes of data."""
        out = self._crack(
            monkeypatch,
            lambda u: b"Title: \n\nURL Source: x\n\nMarkdown Content:\n[]\n")
        assert all(fp["empty"] for fp in out.values())
        assert all(fp["bytes"] <= 4 for fp in out.values())

    def test_rows_are_reported_as_a_hit(self, monkeypatch):
        from scripts.probe_kickoff_timezone import summarise_offsets, verdict

        out = self._crack(
            monkeypatch,
            lambda u: b"Markdown Content:\n" + b'[{"id":"1","DATE_BAH":"x"}]'
            if "sp=2" in u else b"[]")
        assert out["with_sport"]["empty"] is False
        _, lines = verdict({"fetch_errors": [], "offset": summarise_offsets([]),
                            "getjson_crack": out})
        assert any("GETJSON RETURNS DATA FOR: with_sport" in l for l in lines)


class TestJsCallSites:
    """The call site in the bundle names the parameters the endpoint wants."""

    def test_contexts_are_extracted_for_each_endpoint(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe

        js = (b"x" * 300 + b'u="/scripts/getjson.php?gdt="+d+"&sp="+s;'
              + b"y" * 300 + b'v="/scripts/getjson_t.php?mid="+m;')
        monkeypatch.setattr(probe, "cdx_snapshots",
                            lambda p, *, limit, timeout: [("2026", "u")])
        monkeypatch.setattr(probe, "archived_bytes",
                            lambda ts, u, *, timeout, kind="id_": js)
        out = probe.mine_js_contexts(timeout=1)
        assert "&sp=" in out["getjson.php"][0]
        assert "getjson_t" in out
        assert out["bytes"] == len(js)

    def test_a_missing_bundle_is_reported_not_raised(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe

        monkeypatch.setattr(probe, "cdx_snapshots",
                            lambda p, *, limit, timeout: [])
        assert "not archived" in probe.mine_js_contexts(timeout=1)["error"]

    def test_a_fetch_failure_is_reported_not_raised(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe

        monkeypatch.setattr(probe, "cdx_snapshots",
                            lambda p, *, limit, timeout: [("2026", "u")])
        monkeypatch.setattr(probe, "archived_bytes", _boom("410"))
        assert "410" in probe.mine_js_contexts(timeout=1)["error"]


class TestHeadedBrowser:
    """Headless Chrome is the most heavily fingerprinted signal there is, and
    the relay's own renderer clears this check from a datacenter address — so
    the block is unlikely to be purely about the IP. Run headed under Xvfb
    with the automation tells removed."""

    def test_xvfb_is_installed_before_the_browser(self):
        from scripts.probe_kickoff_timezone import install_playwright

        calls: list[list[str]] = []

        class _Done:
            returncode = 0
            stderr = b""

        install_playwright(runner=lambda a, **k: calls.append(a) or _Done())
        assert any("xvfb" in " ".join(a) for a in calls)
        assert any("playwright" in " ".join(a) for a in calls)

    def test_a_display_makes_the_browser_headed(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe

        seen: dict = {}

        def _fetch(url, *, headless=True, **kw):
            seen["headless"] = headless
            return {"rows": 0}

        monkeypatch.setattr(probe, "install_playwright", lambda: "ok")
        monkeypatch.setattr(probe, "start_virtual_display", lambda: ":99")
        monkeypatch.setattr(probe, "playwright_fetch", _fetch)
        probe.browser_probe("2026-09-27", "basketball")
        assert seen["headless"] is False

    def test_a_failed_display_falls_back_to_headless(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe

        seen: dict = {}
        monkeypatch.setattr(probe, "install_playwright", lambda: "ok")
        monkeypatch.setattr(probe, "start_virtual_display",
                            lambda: "FileNotFoundError: Xvfb")
        monkeypatch.setattr(
            probe, "playwright_fetch",
            lambda url, *, headless=True, **kw: seen.update(headless=headless)
            or {"rows": 0})
        out = probe.browser_probe("2026-09-27", "basketball")
        assert seen["headless"] is True
        assert "Xvfb" in out["display"]

    def test_the_automation_tell_is_patched_out(self):
        from scripts.probe_kickoff_timezone import playwright_fetch

        scripts: list[str] = []
        page = TestBrowserProbe._Page('<div class="rcnt">x</div>', [])
        page.add_init_script = scripts.append
        playwright_fetch("https://x.invalid",
                         launcher=TestBrowserProbe()._launcher(page))
        assert scripts and "navigator" in scripts[0]
        assert "webdriver" in scripts[0]


class TestSitemapRoute:
    """getjson.php wants a match id, which I called circular because ids live
    on the board. Sitemaps are static XML published for crawlers — if they
    list match pages, the circle opens."""

    ROBOTS = (b"User-agent: *\nDisallow: /cgi-bin/\n"
              b"Sitemap: https://www.forebet.com/sitemap_index.xml\n")
    INDEX = (b"<sitemapindex><sitemap><loc>"
             b"https://www.forebet.com/sitemap_matches.xml</loc></sitemap>"
             b"</sitemapindex>")
    MATCHES = (b"<urlset><url><loc>https://www.forebet.com/en/basketball/"
               b"predictions/lakers-vs-heat-2476264</loc></url>"
               b"<url><loc>https://www.forebet.com/en/tennis/predictions/"
               b"alcaraz-vs-sinner-9911223</loc></url></urlset>")

    def test_sitemaps_are_read_from_robots(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe

        def _fetch(url, *, timeout, attempts=3):
            if url.endswith("robots.txt"):
                return self.ROBOTS
            return self.INDEX

        monkeypatch.setattr(probe, "direct_fetch", _fetch)
        out = probe.discover_sitemaps(timeout=1, pause=0)
        assert out["sitemaps_listed"] == [
            "https://www.forebet.com/sitemap_index.xml"]
        child = out["children"]["https://www.forebet.com/sitemap_index.xml"]
        assert child["is_index"] and child["locs"] == 1

    def test_relay_is_used_when_a_direct_fetch_is_refused(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe

        monkeypatch.setattr(probe, "direct_fetch", _boom("403"))
        monkeypatch.setattr(probe, "relay_request",
                            lambda url, headers, *, timeout: self.ROBOTS)
        out = probe.discover_sitemaps(timeout=1, pause=0)
        assert out["sitemaps_listed"]
        assert out["direct_errors"]

    def test_match_ids_are_harvested_per_sport(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe

        monkeypatch.setattr(probe, "direct_fetch",
                            lambda url, *, timeout, attempts=3: self.MATCHES)
        out = probe.harvest_match_ids("https://x/sitemap.xml", timeout=1)
        assert out["by_sport"]["basketball"][0] == ("lakers-vs-heat", "2476264")
        assert out["by_sport"]["tennis"][0][1] == "9911223"

    def test_a_working_per_match_endpoint_is_declared_a_route(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe
        from scripts.probe_kickoff_timezone import summarise_offsets, verdict

        monkeypatch.setattr(
            probe, "relay_request",
            lambda url, headers, *, timeout:
                b'Markdown Content:\n[{"id":"2476264",'
                b'"DATE_BAH":"2026-09-27 19:30:00","host":"Lakers",'
                b'"guest":"Heat","Pred_1":"71","Pred_2":"29"}]')
        out = probe.test_match_json("lakers-vs-heat", "2476264", timeout=1)
        assert out["empty"] is False and out["has_date_bah"]

        _, lines = verdict({"fetch_errors": [], "offset": summarise_offsets([]),
                            "match_json": out})
        assert any("PER-MATCH JSON WORKS" in l for l in lines)

    def test_an_empty_per_match_answer_is_not_a_win(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe
        from scripts.probe_kickoff_timezone import summarise_offsets, verdict

        monkeypatch.setattr(probe, "relay_request",
                            lambda url, headers, *, timeout: b"[]")
        out = probe.test_match_json("x", "1", timeout=1)
        _, lines = verdict({"fetch_errors": [], "offset": summarise_offsets([]),
                            "match_json": out})
        assert out["empty"] and not any("PER-MATCH JSON WORKS" in l
                                        for l in lines)


class TestLiveDomSelectors:
    """A target-selector request answers 422 when nothing matches, which
    reads the live DOM's shape without ever seeing the page."""

    def test_a_422_is_recorded_as_absent(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe

        class _Err(Exception):
            code = 422

        def _relay(url, headers, *, timeout):
            if headers.get("X-Target-Selector") in (".rcnt", "div.rcnt"):
                raise _Err("unprocessable")
            return b"<table>rows</table>"

        monkeypatch.setattr(probe, "relay_request", _relay)
        out = probe.live_dom_selectors("2026-09-27", "basketball",
                                       timeout=1, pause=0)
        assert out[".rcnt"]["found"] is False
        assert out["table"]["found"] is True

    def test_a_missing_rcnt_is_called_out_as_a_parser_problem(self):
        from scripts.probe_kickoff_timezone import summarise_offsets, verdict

        _, lines = verdict({
            "fetch_errors": [], "offset": summarise_offsets([]),
            "dom_selectors": {".rcnt": {"found": False, "error": "HTTPError:422"},
                              "table": {"found": True, "bytes": 900}}})
        assert any("THE LIVE BOARD HAS NO .rcnt" in l for l in lines)

    def test_an_unrelated_error_is_not_read_as_absence(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe

        monkeypatch.setattr(probe, "relay_request", _boom("timeout"))
        out = probe.live_dom_selectors("2026-09-27", "basketball",
                                       timeout=1, pause=0)
        assert all(fp["found"] is None for fp in out.values())


class TestHarvestedIdentity:
    """A match slug names both teams and the id keys the per-match endpoint,
    so a link is a complete identity — no HTML board required."""

    def test_a_link_feeds_the_per_match_endpoint(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe

        called: dict = {}
        monkeypatch.setattr(probe, "fetch", lambda *a, **k: b"")
        monkeypatch.setattr(probe, "probe_routes", lambda *a, **k: {})
        monkeypatch.setattr(probe, "markdown_modes", lambda *a, **k: {
            "links_summary": {"link_sample": [
                "football|a-vs-b-old|111111",
                "basketball|lakers-vs-heat|2476264"]}})
        monkeypatch.setattr(probe, "discover_sitemaps", lambda **k: {})
        monkeypatch.setattr(probe, "live_dom_selectors", lambda *a, **k: {})

        def _match_json(slug, mid, *, timeout):
            called.update(slug=slug, mid=mid)
            return {"empty": False}

        monkeypatch.setattr(probe, "test_match_json", _match_json)
        report = probe.run_probe("2026-09-27", sport="basketball",
                                 timeout=1, pause=0)
        # The blocked sport is the one worth testing, not football.
        assert called == {"slug": "lakers-vs-heat", "mid": "2476264"}
        assert len(report["harvested_links"]) == 2

    def test_no_links_means_no_call(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe

        monkeypatch.setattr(probe, "fetch", lambda *a, **k: b"")
        monkeypatch.setattr(probe, "probe_routes", lambda *a, **k: {})
        monkeypatch.setattr(probe, "markdown_modes", lambda *a, **k: {
            "plain": {"link_sample": []}})
        monkeypatch.setattr(probe, "discover_sitemaps", lambda **k: {})
        monkeypatch.setattr(probe, "live_dom_selectors", lambda *a, **k: {})
        monkeypatch.setattr(probe, "test_match_json", _boom("must not run"))
        report = probe.run_probe("2026-09-27", sport="basketball",
                                 timeout=1, pause=0)
        assert report["harvested_links"] == []
        assert "match_json" not in report


class TestListingSlice:
    """Sampling around the first clock kept landing in the navigation, which
    says nothing about whether a row names its teams."""

    def test_the_slice_starts_at_the_listing(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe

        body = (b"nav nav nav " * 40 + b"Basketball predictions for 27/09/2026"
                b" Home team Away team Lakers Heat 19:30 71 29")
        monkeypatch.setattr(probe, "relay_request",
                            lambda url, headers, *, timeout: body)
        out = probe.markdown_modes("2026-09-27", "basketball",
                                   timeout=1, pause=0)
        slice_ = out["plain"]["table_slice"]
        assert slice_.startswith("predictions for")
        assert "Lakers Heat" in slice_

    def test_a_tiny_payload_is_not_called_data(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe
        from scripts.probe_kickoff_timezone import summarise_offsets, verdict

        monkeypatch.setattr(probe, "relay_request",
                            lambda url, headers, *, timeout: b'[{"a":1}]')
        out = probe.test_match_json("slug", "123", timeout=1)
        assert out["empty"] is True

        _, lines = verdict({"fetch_errors": [], "offset": summarise_offsets([]),
                            "match_json": out})
        assert not any("PER-MATCH JSON WORKS" in l for l in lines)

    def test_a_real_payload_still_counts(self, monkeypatch):
        import scripts.probe_kickoff_timezone as probe

        payload = b'[{"id":"2476264","DATE_BAH":"2026-09-27 19:30:00",' \
                  b'"host":"Lakers","guest":"Heat","Pred_1":"71"}]'
        monkeypatch.setattr(probe, "relay_request",
                            lambda url, headers, *, timeout: payload)
        out = probe.test_match_json("slug", "123", timeout=1)
        assert out["empty"] is False and out["has_date_bah"]
