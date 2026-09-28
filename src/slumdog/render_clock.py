"""Recovering the renderer's clock from evidence.

Forebet's football JSON is requested with ``tz=0``, so its ``DATE_BAH`` is
UTC. Every other sport is captured from a rendered listing, whose times come
out in a timezone derived from the requesting client — our relay's egress IP
— and the ``tz=0`` parameter is ignored there. The red-team finding of
2026-09-26 measured the gap directly: match 2468143 rendered as
``09/25/2026 9:00 PM`` while the tz=0 JSON gave ``2026-09-26 02:00:00`` for
the same match, a five-hour offset.

That is why ``UTC_KICKOFF_PROVEN_SPORTS`` holds football alone: a rendered
kickoff cannot be turned into an instant, so the event-day track refuses it
rather than guess, and thirteen sports produce nothing. The note beside that
constant says what would change it — "a per-capture calibration that recovers
the offset from evidence". This module is that calibration.

The method, and why it is sound:

    Football is the one sport visible through **both** channels in the same
    run. The JSON gives the instant; the rendered board gives the same
    matches' local text; the same renderer serves every other sport in the
    same run from the same egress. So the offset measured on football is the
    offset applied to hockey — not an assumption about hockey, a measurement
    of the renderer.

What it refuses, and why:

    * **Too few matches.** One agreeing pair could be a coincidence of
      rounding; a day's football board is hundreds of matches, so demanding
      a real sample costs nothing.
    * **Scatter.** Every match must show the *same* offset. Two different
      offsets on one board means the times are not a single clock — a
      per-league timezone, or a DST boundary inside the day — and a single
      number would be wrong for some of them.
    * **One rendered hour.** A board where every match kicks off at the same
      displayed time cannot distinguish a clock offset from a coincidence,
      so distinct hours are required.
    * **Staleness.** A calibration belongs to the capture that produced it.
      The egress IP can change between runs and the offset with it, so a
      calibration is never carried over to another day or another run.

Refusal is the default: every failure path returns a reason and no offset,
and the caller is expected to fall back to today's behaviour — football only.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

# A calibration must rest on a real sample of the board, not on whichever
# handful of matches happened to parse.
MIN_CALIBRATION_SAMPLES = 20

# Distinct rendered hours required. A board that shows one kickoff time
# cannot separate a clock offset from a coincidence.
MIN_DISTINCT_RENDERED_HOURS = 3

# Offsets are whole minutes; real-world zones are whole quarter-hours, but
# minutes are measured rather than assumed so a broken parse shows up as
# scatter instead of being rounded into agreement.
_RENDERED = re.compile(
    r"(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})\s*([AaPp][Mm])?")

# What the ISO instant from the football JSON looks like.
_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})")

#: Reasons a calibration was refused. Every one of them leaves the caller
#: exactly where it was: football only.
TOO_FEW_SAMPLES = "TOO_FEW_SAMPLES"
OFFSETS_DISAGREE = "OFFSETS_DISAGREE"
TOO_FEW_DISTINCT_HOURS = "TOO_FEW_DISTINCT_HOURS"
NO_OVERLAPPING_MATCHES = "NO_OVERLAPPING_MATCHES"
IMPLAUSIBLE_OFFSET = "IMPLAUSIBLE_OFFSET"

# No real timezone lies outside this range. An offset beyond it means the
# join matched the wrong matches, not that the renderer is in Kiribati plus
# a day.
_MAX_OFFSET_MINUTES = 14 * 60
_MIN_OFFSET_MINUTES = -12 * 60


@dataclass(frozen=True)
class RenderClock:
    """A measured relationship between rendered text and real instants.

    ``offset_minutes`` is ``rendered - utc``: the renderer showing 9:00 PM
    for an 02:00 UTC kickoff is -300.
    """

    offset_minutes: int
    samples: int
    distinct_hours: int
    target_date: str
    measured_at: str
    source_run: str = ""
    json_sha256: str = ""
    render_sha256: str = ""
    sample_event_ids: tuple[str, ...] = ()

    def to_utc(self, rendered: str) -> dt.datetime | None:
        """The UTC instant a rendered listing time refers to."""
        naive = parse_rendered(rendered)
        if naive is None:
            return None
        return (naive - dt.timedelta(minutes=self.offset_minutes)
                ).replace(tzinfo=dt.timezone.utc)

    def as_dict(self) -> dict:
        return {
            "offset_minutes": self.offset_minutes,
            "samples": self.samples,
            "distinct_hours": self.distinct_hours,
            "target_date": self.target_date,
            "measured_at": self.measured_at,
            "source_run": self.source_run,
            "json_sha256": self.json_sha256,
            "render_sha256": self.render_sha256,
            "sample_event_ids": list(self.sample_event_ids),
        }


@dataclass(frozen=True)
class Calibration:
    """Outcome of a calibration attempt: a clock, or the reason there isn't
    one. Never both, and never a clock with caveats attached."""

    clock: RenderClock | None = None
    reason: str = ""
    detail: str = ""
    observed_offsets: dict[int, int] = field(default_factory=dict)

    @property
    def proven(self) -> bool:
        return self.clock is not None


def parse_rendered(value: str) -> dt.datetime | None:
    """A naive datetime from rendered listing text.

    Both orders are accepted because the renderer uses whichever the locale
    implies, and the reading is resolved by the caller's date-order
    inference before it gets here — but a value that is ambiguous on its own
    (03/04) is left to the offset agreement check to catch: a wrong reading
    produces a day-sized disagreement, which is exactly what scatter means.
    """
    found = _RENDERED.search(value or "")
    if not found:
        return None
    first, second, year, hour, minute, meridiem = found.groups()
    first_i, second_i, hour_i = int(first), int(second), int(hour)
    if meridiem:
        upper = meridiem.upper()
        if upper == "PM" and hour_i != 12:
            hour_i += 12
        elif upper == "AM" and hour_i == 12:
            hour_i = 0
    # Day-first when the first component cannot be a month; month-first
    # otherwise. An ambiguous pair that reads wrongly shows up as scatter.
    day, month = (first_i, second_i) if first_i > 12 else (second_i, first_i)
    if first_i <= 12 and second_i <= 12:
        month, day = first_i, second_i
    try:
        return dt.datetime(int(year), month, day, hour_i, int(minute))
    except ValueError:
        return None


def parse_instant(value: str) -> dt.datetime | None:
    """A naive UTC datetime from the football JSON's ``DATE_BAH``."""
    found = _ISO.search(value or "")
    if not found:
        return None
    year, month, day, hour, minute = (int(part) for part in found.groups())
    try:
        return dt.datetime(year, month, day, hour, minute)
    except ValueError:
        return None


def measure_render_clock(
    instants: dict[str, str],
    rendered: dict[str, str],
    *,
    target_date: str,
    measured_at: str,
    source_run: str = "",
    json_sha256: str = "",
    render_sha256: str = "",
    min_samples: int = MIN_CALIBRATION_SAMPLES,
    min_distinct_hours: int = MIN_DISTINCT_RENDERED_HOURS,
) -> Calibration:
    """Measure the renderer's offset by joining one sport seen both ways.

    ``instants`` maps event id to the trusted UTC timestamp (football's
    ``DATE_BAH``); ``rendered`` maps the same ids to the listing text the
    renderer produced for those matches in the same run.
    """
    offsets: dict[int, int] = {}
    hours: set[int] = set()
    used: list[str] = []
    for event_id, raw_instant in sorted(instants.items()):
        raw_rendered = rendered.get(event_id)
        if not raw_rendered:
            continue
        utc = parse_instant(raw_instant)
        shown = parse_rendered(raw_rendered)
        if utc is None or shown is None:
            continue
        delta = int((shown - utc).total_seconds() // 60)
        offsets[delta] = offsets.get(delta, 0) + 1
        hours.add(shown.hour)
        used.append(event_id)

    if not used:
        return Calibration(reason=NO_OVERLAPPING_MATCHES,
                           detail="no event id appeared in both channels")
    if len(offsets) > 1:
        # Two clocks on one board. Which matches are wrong is unknowable
        # from here, so none of them are used.
        return Calibration(
            reason=OFFSETS_DISAGREE, observed_offsets=dict(offsets),
            detail=f"{len(offsets)} distinct offsets across {len(used)} "
                   f"matches: {sorted(offsets)}")
    offset = next(iter(offsets))
    if not _MIN_OFFSET_MINUTES <= offset <= _MAX_OFFSET_MINUTES:
        return Calibration(reason=IMPLAUSIBLE_OFFSET,
                           observed_offsets=dict(offsets),
                           detail=f"{offset} minutes is not a timezone")
    if len(used) < min_samples:
        return Calibration(
            reason=TOO_FEW_SAMPLES, observed_offsets=dict(offsets),
            detail=f"{len(used)} matches joined, {min_samples} required")
    if len(hours) < min_distinct_hours:
        return Calibration(
            reason=TOO_FEW_DISTINCT_HOURS, observed_offsets=dict(offsets),
            detail=f"{len(hours)} distinct rendered hour(s), "
                   f"{min_distinct_hours} required")
    return Calibration(clock=RenderClock(
        offset_minutes=offset,
        samples=len(used),
        distinct_hours=len(hours),
        target_date=target_date,
        measured_at=measured_at,
        source_run=source_run,
        json_sha256=json_sha256,
        render_sha256=render_sha256,
        sample_event_ids=tuple(used[:25]),
    ))


def calibrate_capture(
    *,
    target_date: str,
    fetch_instants,
    fetch_rendered,
    measured_at: str,
    source_run: str = "",
) -> Calibration:
    """Run one calibration against a live capture.

    The two fetchers are injected so this stays a measurement rather than a
    network client: the caller decides where the tz=0 instants and the
    rendered board come from, and a failure in either is a refusal, never a
    partial calibration built from whatever did arrive.
    """
    try:
        instants = fetch_instants()
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return Calibration(reason=NO_OVERLAPPING_MATCHES,
                           detail=f"instants unavailable: "
                                  f"{type(exc).__name__}: {exc}"[:200])
    if not instants:
        # No trusted instants means no calibration is possible, so the
        # rendered board is not fetched at all. A request whose answer
        # cannot be used is not worth making of a source that throttles.
        return Calibration(reason=NO_OVERLAPPING_MATCHES,
                           detail="no tz=0 instants in this capture")
    try:
        rendered = fetch_rendered()
    except Exception as exc:  # noqa: BLE001
        return Calibration(reason=NO_OVERLAPPING_MATCHES,
                           detail=f"rendered board unavailable: "
                                  f"{type(exc).__name__}: {exc}"[:200])
    return measure_render_clock(
        instants or {}, rendered or {}, target_date=target_date,
        measured_at=measured_at, source_run=source_run)


def instants_from_records(records, sport: str = "football") -> dict[str, str]:
    """Event id -> trusted UTC timestamp, from the channel that has one.

    Football is captured from ``getrs.php?...&tz=0``, which pins the
    rendering timezone, so its ``DATE_BAH`` is the only kickoff in the
    system that is an instant rather than a local reading.
    """
    return {
        str(record.event_id): str(record.kickoff)
        for record in records
        if getattr(record, "sport", "") == sport and record.kickoff
    }


def load_render_clock(path, target_date: str):
    """Read a calibration from disk for one date, or return ``None``.

    Every failure mode — no path, no file, unreadable JSON, a missing
    field, a different date — returns ``None``, which puts the caller
    exactly where it would be without a calibration at all. That symmetry
    is deliberate: a broken calibration must never be more permissive than
    no calibration, and the only way to guarantee that is for both to look
    identical from here.
    """
    if not path:
        return None
    try:
        import json as _json
        from pathlib import Path as _Path

        payload = _json.loads(_Path(path).read_text())
        clock = RenderClock(
            offset_minutes=int(payload["offset_minutes"]),
            samples=int(payload["samples"]),
            distinct_hours=int(payload["distinct_hours"]),
            target_date=str(payload["target_date"]),
            measured_at=str(payload["measured_at"]),
            source_run=str(payload.get("source_run") or ""),
            json_sha256=str(payload.get("json_sha256") or ""),
            render_sha256=str(payload.get("render_sha256") or ""),
            sample_event_ids=tuple(payload.get("sample_event_ids") or ()),
        )
    except Exception:
        return None
    if clock.target_date != target_date:
        return None
    if clock.samples < MIN_CALIBRATION_SAMPLES:
        return None
    if clock.distinct_hours < MIN_DISTINCT_RENDERED_HOURS:
        return None
    if not _MIN_OFFSET_MINUTES <= clock.offset_minutes <= _MAX_OFFSET_MINUTES:
        return None
    return clock


def clock_for_capture(calibration: Calibration | None, *, target_date: str,
                      source_run: str = "") -> RenderClock | None:
    """The clock a capture is allowed to use, or ``None``.

    A calibration is evidence about one run's renderer, not a standing fact.
    The egress IP that decides the offset can change between runs, and a
    stale offset applied to a fresh board would move every kickoff by hours
    in silence — the precise failure the lead gate exists to prevent. So a
    calibration from another date, or from another run, is not used.
    """
    if calibration is None or calibration.clock is None:
        return None
    clock = calibration.clock
    if clock.target_date != target_date:
        return None
    if source_run and clock.source_run and clock.source_run != source_run:
        return None
    return clock
