"""Offline tests for the kickoff-timezone probe.

The probe is the evidence-gathering step that could lift the EVENT_DAY
track's timezone hold, so its reasoning must be conservative: an
inconclusive sample has to read as "not proven", never as permission.
No test here touches the network.
"""
from __future__ import annotations

import datetime as dt

from scripts.probe_kickoff_timezone import (
    html_board_rows,
    machine_readable_candidates,
    offset_minutes,
    parse_display_time,
    summarise_offsets,
    verdict,
)


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
