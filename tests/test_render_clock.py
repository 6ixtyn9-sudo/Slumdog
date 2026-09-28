"""Tests for recovering the renderer's clock from evidence.

The measurement these tests describe is the only thing standing between
seven capturable sports and a daily rank-1 pick, so the emphasis is on when
it must REFUSE. An offset that is wrong by hours does not look wrong: it
produces a kickoff that parses, sorts and prints perfectly, and admits an
event that has already started.
"""
from __future__ import annotations

import datetime as dt

from slumdog.render_clock import (
    IMPLAUSIBLE_OFFSET,
    MIN_CALIBRATION_SAMPLES,
    NO_OVERLAPPING_MATCHES,
    OFFSETS_DISAGREE,
    TOO_FEW_DISTINCT_HOURS,
    TOO_FEW_SAMPLES,
    Calibration,
    RenderClock,
    clock_for_capture,
    measure_render_clock,
    parse_instant,
    parse_rendered,
)


def _pair(count=MIN_CALIBRATION_SAMPLES, offset_hours=-5, hours=(14, 17, 19)):
    """A football board seen both ways, with a known offset applied."""
    instants: dict[str, str] = {}
    rendered: dict[str, str] = {}
    for index in range(count):
        hour = hours[index % len(hours)]
        utc = dt.datetime(2026, 9, 29, hour, 0)
        shown = utc + dt.timedelta(hours=offset_hours)
        key = f"football:{1000 + index}"
        instants[key] = utc.strftime("%Y-%m-%d %H:%M:%S")
        rendered[key] = shown.strftime("%d/%m/%Y %H:%M")
    return instants, rendered


def _measure(instants, rendered, **over):
    kwargs = {"target_date": "2026-09-29",
              "measured_at": "2026-09-29T06:00:00+00:00"}
    kwargs.update(over)
    return measure_render_clock(instants, rendered, **kwargs)


class TestTheOffsetIsMeasuredNotAssumed:
    def test_a_clean_board_yields_the_offset(self):
        result = _measure(*_pair())
        assert result.proven
        assert result.clock.offset_minutes == -300
        assert result.clock.samples == MIN_CALIBRATION_SAMPLES
        assert result.clock.distinct_hours == 3

    def test_the_2026_09_26_finding_reproduces(self):
        """Match 2468143 rendered 09/25/2026 9:00 PM for a 02:00 UTC
        kickoff — the five-hour gap that froze twelve sports out."""
        instants, rendered = _pair(offset_hours=-5)
        instants["football:2468143"] = "2026-09-26 02:00:00"
        rendered["football:2468143"] = "09/25/2026 9:00 PM"
        result = _measure(instants, rendered)
        assert result.proven
        assert result.clock.offset_minutes == -300

    def test_a_renderer_already_in_utc_is_reported_as_zero(self):
        result = _measure(*_pair(offset_hours=0))
        assert result.proven
        assert result.clock.offset_minutes == 0

    def test_the_clock_converts_a_rendered_time_back(self):
        clock = _measure(*_pair()).clock
        assert clock.to_utc("29/09/2026 14:00") == dt.datetime(
            2026, 9, 29, 19, 0, tzinfo=dt.timezone.utc)

    def test_an_unreadable_rendered_time_converts_to_nothing(self):
        clock = _measure(*_pair()).clock
        assert clock.to_utc("TBD") is None
        assert clock.to_utc("") is None


class TestItRefusesRatherThanGuess:
    def test_disagreeing_offsets_are_refused_whole(self):
        """One board, two clocks — a per-league timezone or a DST
        boundary. Which matches are wrong is unknowable, so none are used."""
        instants, rendered = _pair()
        first = sorted(rendered)[0]
        rendered[first] = "29/09/2026 23:30"
        result = _measure(instants, rendered)
        assert not result.proven
        assert result.reason == OFFSETS_DISAGREE
        assert len(result.observed_offsets) == 2

    def test_a_thin_sample_is_refused(self):
        result = _measure(*_pair(count=5))
        assert not result.proven
        assert result.reason == TOO_FEW_SAMPLES

    def test_a_board_with_one_kickoff_hour_is_refused(self):
        result = _measure(*_pair(hours=(19,)))
        assert not result.proven
        assert result.reason == TOO_FEW_DISTINCT_HOURS

    def test_no_shared_matches_is_refused(self):
        instants, rendered = _pair()
        result = _measure(instants, {f"other:{k}": v
                                     for k, v in rendered.items()})
        assert not result.proven
        assert result.reason == NO_OVERLAPPING_MATCHES

    def test_an_offset_no_timezone_has_is_refused(self):
        """A day-sized offset means the join matched the wrong matches."""
        result = _measure(*_pair(offset_hours=-30))
        assert not result.proven
        assert result.reason == IMPLAUSIBLE_OFFSET

    def test_unparseable_rows_are_skipped_not_counted(self):
        instants, rendered = _pair(count=MIN_CALIBRATION_SAMPLES + 4)
        for key in sorted(rendered)[:3]:
            rendered[key] = "Postponed"
        result = _measure(instants, rendered)
        assert result.proven
        assert result.clock.samples == MIN_CALIBRATION_SAMPLES + 1


class TestACalibrationBelongsToItsCapture:
    """The egress IP that decides the offset can change between runs, so a
    calibration is evidence about one capture, not a standing fact."""

    def _calibration(self, **over):
        fields = {"offset_minutes": -300, "samples": 40, "distinct_hours": 6,
                  "target_date": "2026-09-29",
                  "measured_at": "2026-09-29T06:00:00+00:00",
                  "source_run": "run-1"}
        fields.update(over)
        return Calibration(clock=RenderClock(**fields))

    def test_it_is_used_for_its_own_capture(self):
        clock = clock_for_capture(self._calibration(),
                                  target_date="2026-09-29",
                                  source_run="run-1")
        assert clock is not None and clock.offset_minutes == -300

    def test_yesterdays_calibration_is_not_reused(self):
        assert clock_for_capture(self._calibration(),
                                 target_date="2026-09-30") is None

    def test_another_runs_calibration_is_not_reused(self):
        assert clock_for_capture(self._calibration(),
                                 target_date="2026-09-29",
                                 source_run="run-2") is None

    def test_a_refused_calibration_offers_no_clock(self):
        assert clock_for_capture(Calibration(reason=TOO_FEW_SAMPLES),
                                 target_date="2026-09-29") is None
        assert clock_for_capture(None, target_date="2026-09-29") is None


class TestReadingTheTwoChannels:
    def test_rendered_text_in_both_orders(self):
        assert parse_rendered("29/09/2026 19:00") == dt.datetime(
            2026, 9, 29, 19, 0)
        assert parse_rendered("09/29/2026 7:00 PM") == dt.datetime(
            2026, 9, 29, 19, 0)

    def test_midnight_and_noon_meridiem(self):
        assert parse_rendered("09/29/2026 12:00 AM").hour == 0
        assert parse_rendered("09/29/2026 12:00 PM").hour == 12

    def test_an_impossible_date_is_not_invented(self):
        assert parse_rendered("31/02/2026 19:00") is None

    def test_the_json_instant(self):
        assert parse_instant("2026-09-29 19:00:00") == dt.datetime(
            2026, 9, 29, 19, 0)
        assert parse_instant("") is None


class TestJoiningTheTwoChannels:
    """The join is on the site's own match id, not on names: two boards can
    spell a team differently, and a name join that silently misses would
    look exactly like a thin sample."""

    def _board(self, count=3):
        from slumdog.relay_columns import BoardColumns

        return BoardColumns(
            sport="football", target_date="2026-09-29", source_url="u",
            columns={
                "link": [f"[A{i} B{i} 29/09/2026 {14 + i}:00]"
                         f"(https://f/m/a{i}-b{i}-{2468140 + i})"
                         for i in range(count)],
                "home": [f"A{i}" for i in range(count)],
                "away": [f"B{i}" for i in range(count)],
                "kickoff": ["x"] * count,
                "probabilities": ["50 20 30"] * count,
                "pick": ["1"] * count,
            }, row_count=count)

    def test_rendered_text_is_keyed_by_match_id(self):
        from slumdog.relay_columns import rendered_kickoffs

        rendered = rendered_kickoffs(self._board())
        assert set(rendered) == {"football:2468140", "football:2468141",
                                 "football:2468142"}
        assert "14:00" in rendered["football:2468140"]

    def test_instants_come_only_from_the_tz0_channel(self):
        from slumdog.render_clock import instants_from_records

        class _Record:
            def __init__(self, event_id, sport, kickoff):
                self.event_id, self.sport, self.kickoff = (
                    event_id, sport, kickoff)

        records = [_Record("football:1", "football", "2026-09-29 19:00:00"),
                   _Record("hockey:2", "hockey", "29/09/2026 19:00"),
                   _Record("football:3", "football", "")]
        assert instants_from_records(records) == {
            "football:1": "2026-09-29 19:00:00"}

    def test_the_two_halves_measure_an_offset_end_to_end(self):
        from slumdog.relay_columns import rendered_kickoffs

        board = self._board(count=3)
        rendered = rendered_kickoffs(board)
        instants = {
            "football:2468140": "2026-09-29 19:00:00",
            "football:2468141": "2026-09-29 20:00:00",
            "football:2468142": "2026-09-29 21:00:00",
        }
        result = measure_render_clock(
            instants, rendered, target_date="2026-09-29",
            measured_at="2026-09-29T06:00:00Z", min_samples=3)
        assert result.proven
        assert result.clock.offset_minutes == -300
