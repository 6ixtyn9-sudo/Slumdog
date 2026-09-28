"""Column-wise board capture through the relay's rendering engine.

Why this exists
---------------
From 2026-09-22 the Forebet HTML boards stopped being fetchable from CI:
direct requests are refused, and every proxied fetch of the page returns a
bot-check interstitial. Only the relay's rendering engine gets through, and
its full-page Markdown render collapses the listing table and drops the
team-name column — which is why the boards looked like they carried no match
identity at all.

Selector-scoped extraction recovers it. Asking the renderer for one field at
a time returns that column as lines of text, and the columns can be zipped
back into rows by index.

What this module refuses to do
------------------------------
The channel is unreliable in ways that are easy to mistake for data:

* it throttles, answering 422 or a short stub for a selector that worked
  minutes earlier;
* a throttled render can return a *partial* board (17 rows where the page
  has 126);
* a bot-check page is a 200 response with a body.

Every one of those has already been mistaken for a real answer once in this
project's history, and an interstitial was frozen into the evidence record as
a genuine capture. So this module fails closed: columns must all be present,
equally long, and plausible, or it raises. A caller may retry later; it may
never silently record a short board as a complete one.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .contracts import EventSnapshot
from .forebet import RELAY_BASE, looks_like_challenge_page
from .sports import SPORTS

# One selector per field. The renderer returns each match's value on its own
# line, in board order.
# Every column is scoped to rows that actually carry the fields a decision
# needs. Measured on 2026-09-27, each of the four blocked boards returned one
# fewer ".fprc" than ".homeTeam" — basketball 19 against 20, tennis 44 against
# 45 — because some rows (live, postponed, or simply unpredicted) have no
# probability cell at all.
#
# That single missing element is fatal to an index join: every row after it
# is silently paired with the wrong match. Rather than detect the gap after
# the fact, the scope removes it — ":has()" restricts the match set to
# complete rows, so the columns are equal in length by construction and the
# row a board could not predict is never captured at all.
ROW_SCOPE = ".rcnt:has(.fprc):has(.tnms)"

# The scope for a board being SETTLED rather than ranked.
#
# ROW_SCOPE demands a prediction cell, because a row without one cannot
# become a pick. A finished match is not being picked, and requiring
# ".fprc" of it asks a results board to look like a fixtures board. Run
# 36417397896 showed the cost precisely: the volleyball board for
# 2026-09-27 rendered 42,670 bytes of real content - title "Volleyball
# predictions for 27/09/2026", no challenge page - while every scoped
# column returned 422, which reads as throttling and is not.
#
# A row that has a score is the row settlement is looking for, and it is
# a cheaper selector besides.
SETTLEMENT_ROW_SCOPE = ".rcnt:has(.lscr_td)"


def scoped(selector: str, scope: str = ROW_SCOPE) -> str:
    """A field selector restricted to complete rows.

    A comma-separated selector is scoped part by part. Scoping only the
    first part would leave the rest matching the whole document — which
    reads as a column longer than the board and fails alignment, or worse,
    quietly pulls in rows the scope exists to exclude.
    """
    if not scope:
        return selector
    return ", ".join(f"{scope} {part.strip()}"
                     for part in selector.split(",") if part.strip())


COLUMN_SELECTORS: dict[str, str] = {
    # .tnms renders as a Markdown link, which is the only place the match
    # URL survives the render — and the URL is the only real match identity
    # available through this route.
    "link": ".tnms",
    "home": ".homeTeam",
    "away": ".awayTeam",
    "kickoff": ".date_bah",
    "probabilities": ".fprc",
    "pick": ".forepr",
    "predicted_score": ".ex_sc",
    "average": ".avg_sc",
}

# Per-sport selector differences, measured rather than guessed.
#
# Cricket returned kickoff for 8 of 13 rows on every run while every other
# column returned 13, and a required column that short is fatal — so the
# whole board was discarded. The five missing rows are multi-day matches:
# a Test spans four days, so the board renders a RANGE (".dtrange",
# "08/05 - 11/05/2026") where a one-day fixture renders a start
# (".date_bah"). Both are the row's date; only the markup differs, which
# settlement already knew (see settlement._base_row). A selector list
# preserves document order, so the rows still line up one-to-one.
SPORT_COLUMN_OVERRIDES: dict[str, dict[str, str]] = {
    "cricket": {"kickoff": ".date_bah, .dtrange"},
}


def selectors_for(sport: str,
                  base: dict[str, str] | None = None) -> dict[str, str]:
    """The column selectors to use for one sport."""
    merged = dict(base if base is not None else COLUMN_SELECTORS)
    merged.update(SPORT_COLUMN_OVERRIDES.get(sport, {}))
    return merged


# Fields without which a row cannot be a pick: who is playing and when.
# Without any one of these a row cannot become a ranked pick, so losing one
# is a capture failure and must be reported as such. Probabilities belong
# here: handball once returned eleven complete rows and a throttled
# probability column, and the result read as "no events for this date" — a
# fetch failure wearing the face of an empty board.
REQUIRED_COLUMNS: tuple[str, ...] = (
    "link", "home", "away", "kickoff", "probabilities")

# Every column extract begins with the board's heading for that column, but
# the heading text varies by sport and locale. Matching a list of known
# heading strings was tried first and silently mis-aligned every board: only
# ".fprc" ("Prob. %") matched, so probabilities came back exactly one row
# shorter than every other column and all four sports were refused.
#
# So headings are identified by SHAPE instead. A heading is a leading line
# that cannot be a value of its own column, which is locale-independent.
_SHAPES: dict[str, "re.Pattern[str]"] = {
    "link": re.compile(r"\]\("),                      # a Markdown link
    "kickoff": re.compile(r"\d{1,2}[/.-]\d{1,2}"),     # a date
    "probabilities": re.compile(r"\d"),                # at least one number
    "pick": re.compile(r"^\**[12X]\**$"),              # 1, 2 or X
    "predicted_score": re.compile(r"\d"),
    "average": re.compile(r"\d"),
}

# Team-name columns have no distinguishing shape — a heading like "Host" is
# as name-like as a team. At most this many leading lines may be trimmed from
# them to reach the row count the shaped columns agree on.
_MAX_HEADING_LINES = 1

_WRAPPER_MARKER = "Markdown Content:"


class ColumnFetchError(RuntimeError):
    """A column could not be retrieved, or what came back was not data."""


class ColumnAlignmentError(RuntimeError):
    """Columns disagree about how many matches the board holds."""


@dataclass
class BoardColumns:
    """Columns for one board, already validated as mutually consistent."""

    sport: str
    target_date: str
    source_url: str
    columns: dict[str, list[str]]
    row_count: int = 0
    partial: bool = field(default=False)

    def rows(self) -> list[dict[str, str]]:
        """Zip the columns back into per-match rows, in board order."""
        return [
            {name: values[index] for name, values in self.columns.items()}
            for index in range(self.row_count)
        ]


def strip_wrapper(body: bytes) -> str:
    """Drop the relay's Markdown envelope, keeping the extract itself."""
    text = body.decode("utf-8", "replace")
    if _WRAPPER_MARKER in text:
        text = text.split(_WRAPPER_MARKER, 1)[1]
    return text.strip()


def parse_column(body: bytes, *, selector: str, column: str = "") -> list[str]:
    """Lines of one column extract, without its heading row.

    A bot-check page is a 200 with a body, so it is rejected here rather
    than counted as a zero-row board.
    """
    if looks_like_challenge_page(body):
        raise ColumnFetchError(
            f"{selector}: relay returned a bot-check page ({len(body)} bytes)")
    rows = [line.strip() for line in strip_wrapper(body).splitlines()
            if line.strip()]
    shape = _SHAPES.get(column)
    if shape is not None:
        # Only leading lines are dropped: a malformed row in the middle of a
        # board is a real row with a missing value, and losing it would shift
        # every column after it against the others.
        while rows and not shape.search(rows[0]):
            rows.pop(0)
    return rows


#: Columns that are both 1:1 with matches and heading-detectable by shape,
#: so their length is the row count with nothing to subtract. Team-name
#: columns are 1:1 too, but a heading like "Host" is indistinguishable from
#: a team, so they are measured against these rather than voting.
_CONSENSUS_COLUMNS: tuple[str, ...] = ("link", "kickoff", "probabilities")


def _consensus(columns: dict[str, list[str]]) -> int | None:
    """How many matches the board holds, per the columns that can say so."""
    counts = [len(columns[name]) for name in _CONSENSUS_COLUMNS
              if name in columns]
    if not counts or len(set(counts)) != 1:
        return None
    return counts[0]


def align_columns(columns: dict[str, list[str]], *,
                  required: tuple[str, ...] | None = None
                  ) -> dict[str, list[str]]:
    """Reconcile columns that render more than one line per row, or a
    heading the shape filter could not see. Anything else is left alone to
    fail as a mismatch.

    Two real shapes, both measured on live boards:

    * ``.ex_sc`` emits three lines per match — the pair and each side's
      score ("77-79", "77", "**79**"). Every board on 2026-09-28 returned
      exactly three times the row count there, and nothing else disagreed.
      A column that is an exact multiple is collapsed to its first line
      per group, which is the combined value.
    * a team-name column carries a heading that has no shape to detect it
      ("Host" is as name-like as a team), leaving it longer by one.

    A column that is neither an exact multiple nor a single heading longer
    is a partial render and must stay a mismatch, not be trimmed into
    looking consistent.
    """
    required = required or REQUIRED_COLUMNS
    expected = _consensus(columns)
    if not expected:
        return columns
    aligned: dict[str, list[str]] = {}
    for name, rows in columns.items():
        count = len(rows)
        # An optional column that renders nothing, on a board whose
        # required columns are complete, is a field this sport does not
        # have — mma has no correct score, and its .ex_sc matched zero
        # elements while every other column returned ten. This is only
        # safe because a refusal raises: an empty render and a throttled
        # one are not confused here, they arrive by different paths.
        if count == 0 and name not in required:
            continue
        excess = count - expected
        # Only columns that are not 1:1 with matches may be collapsed. A
        # team-name column that happens to be an exact multiple of the row
        # count is a broken capture, not a multi-line cell, and must stay a
        # mismatch.
        if (name not in required and count > expected
                and count % expected == 0):
            group = count // expected
            rows = [rows[index * group] for index in range(expected)]
        elif name not in _SHAPES and 0 < excess <= _MAX_HEADING_LINES:
            rows = rows[excess:]
        aligned[name] = rows
    return aligned


def fetch_column(board_url: str, selector: str, *, timeout: int = 60,
                 opener=None, column: str = "", attempts: int = 3,
                 backoff: float = 8.0, sleep=time.sleep,
                 before_request=None) -> list[str]:
    """Fetch a single column through the renderer, retrying a refusal.

    Throttling is the last thing standing between this route and daily
    coverage. On 2026-09-27 a board would return five clean columns and one
    422, and that single refusal discarded the whole board — tennis was lost
    to a throttled probability column while its other five agreed on 43
    rows. The refusals move around between runs, so a retry recovers them.

    Only the transport is retried. A bot-check body is not a transient
    failure and is never retried into looking like data.

    ``before_request`` is called before every attempt and may raise to stop
    the capture. A caller under a wall-clock cap needs a say here: eight
    columns times three attempts times a minute-long timeout outlives any
    job, and run 36386778571 was killed at the 15-minute wall with nothing
    reported because this loop answered to no one.
    """
    request = urllib.request.Request(
        RELAY_BASE + board_url,
        headers={
            "User-Agent": "EdgeFactory/1.0",
            "Accept": "text/plain",
            "X-No-Cache": "true",
            # Without a wait the render can snapshot before the board fills.
            "X-Timeout": "25",
            "X-Target-Selector": selector,
        },
    )
    open_url = opener or urllib.request.urlopen
    last: ColumnFetchError | None = None
    for attempt in range(max(1, attempts)):
        if before_request is not None:
            before_request()
        if attempt:
            sleep(backoff * attempt)
        try:
            with open_url(request, timeout=timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            # 422 is what the renderer answers when it matches nothing:
            # both a genuinely absent selector and a throttled request.
            last = ColumnFetchError(f"{selector}: HTTP {exc.code}")
            continue
        except Exception as exc:  # noqa: BLE001 - one failure type out
            last = ColumnFetchError(f"{selector}: {type(exc).__name__}")
            continue
        return parse_column(body, selector=selector, column=column)
    raise last or ColumnFetchError(f"{selector}: no attempt was made")


def fetch_board_columns(board_url: str, sport: str, target_date: str, *,
                        timeout: int = 60, opener=None, minimum_rows: int = 1,
                        attempts: int = 3, backoff: float = 8.0,
                        sleep=time.sleep,
                        selectors: dict[str, str] | None = None,
                        required: tuple[str, ...] | None = None,
                        before_request=None,
                        scope: str = ROW_SCOPE,
                        circuit_breaker_columns: int = 2,
                        circuit_breaker_attempts: int = 1) -> BoardColumns:
    """Fetch every column for a board and validate that they agree.

    Raises rather than returning a half-built board: a throttled render can
    hand back a genuine-looking but partial listing, and a short board
    frozen as a complete one is indistinguishable from a quiet fixture day.

    **Circuit breaker (Priority 1, 2026-09-28):** the forward pass captures
    D+2..D+6 x 14 sports, and most sport-dates that far out simply have no
    board yet — every column then 422s, and paying the full
    ``len(selectors) * attempts`` requests (8 columns x 3 attempts by
    default = 24) to learn that is exactly the cost regression Forward
    Shadow #33 measured (>=1h56m on one undifferentiated step, run
    36426785929). So the first ``circuit_breaker_columns`` columns (in
    ``selectors`` order — ``link``, ``home`` by default, both in
    ``REQUIRED_COLUMNS``) are tried first with only
    ``circuit_breaker_attempts`` attempt(s) each. If *all* of them refuse,
    the board is treated as not coming and nothing else is fetched — 2-3
    requests instead of 24. If *any* of them answers, the board is alive
    (a genuinely dead board does not selectively answer two arbitrary
    fields), so any probed column that did fail gets retried with the full
    ``attempts``/``backoff`` policy — the existing "a throttled column
    recovers on retry" guarantee (see ``fetch_column``) is not weakened for
    a board that actually exists. Pass ``circuit_breaker_columns=0`` to
    disable and restore the pre-2026-09-28 behaviour exactly.
    """
    selectors = selectors_for(sport, selectors)
    required = required or REQUIRED_COLUMNS
    columns: dict[str, list[str]] = {}
    failures: list[str] = []
    names = list(selectors)
    probe_names = names[:max(0, circuit_breaker_columns)]
    remaining_names = names[len(probe_names):]

    def _attempt(name: str, use_attempts: int) -> ColumnFetchError | None:
        selector = selectors[name]
        try:
            columns[name] = fetch_column(board_url, scoped(selector, scope),
                                         timeout=timeout, opener=opener,
                                         column=name, attempts=use_attempts,
                                         backoff=backoff, sleep=sleep,
                                         before_request=before_request)
            return None
        except ColumnFetchError as exc:
            return exc

    probe_failures: dict[str, ColumnFetchError] = {}
    for name in probe_names:
        exc = _attempt(name, circuit_breaker_attempts)
        if exc is not None:
            probe_failures[name] = exc

    if probe_names and len(probe_failures) == len(probe_names):
        failures.extend(str(exc) for exc in probe_failures.values())
        raise ColumnFetchError(
            f"{sport} {target_date}: circuit breaker tripped — the first "
            f"{len(probe_names)} column(s) ({', '.join(probe_names)}) all "
            f"refused on {circuit_breaker_attempts} attempt(s) each; board "
            f"not attempted further. failures: {'; '.join(failures)}")

    # The board answered at least one probed column, so it is alive: give
    # any probed column that failed the REST of the full attempts budget —
    # not another full `attempts` on top of the probe, which would spend
    # more requests on this one column than a column that was never probed.
    retry_attempts = max(0, attempts - circuit_breaker_attempts)
    for name, exc in probe_failures.items():
        if retry_attempts <= 0:
            failures.append(str(exc))
            continue
        retry_exc = _attempt(name, retry_attempts)
        if retry_exc is not None:
            failures.append(str(retry_exc))
    for name in remaining_names:
        exc = _attempt(name, attempts)
        if exc is not None:
            failures.append(str(exc))

    missing = [name for name in required if name not in columns]
    if missing:
        raise ColumnFetchError(
            f"{sport} {target_date}: missing required column(s) "
            f"{', '.join(missing)}; failures: {'; '.join(failures) or 'none'}")

    columns = align_columns(columns, required=required)
    counts = {name: len(values) for name, values in columns.items()}
    absent = [name for name in selectors
              if name not in columns and name not in required]
    distinct = set(counts.values())
    if len(distinct) != 1:
        raise ColumnAlignmentError(
            f"{sport} {target_date}: columns disagree on row count {counts}")

    row_count = distinct.pop()
    if row_count < minimum_rows:
        raise ColumnFetchError(
            f"{sport} {target_date}: board returned {row_count} rows, "
            f"expected at least {minimum_rows}")

    return BoardColumns(
        sport=sport,
        target_date=target_date,
        source_url=board_url,
        columns=columns,
        row_count=row_count,
        # A thinner row than ideal, whether the field failed or the sport
        # simply does not have it.
        partial=bool(failures) or bool(absent),
    )


_MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\((?P<url>[^)]+)\)")
_EVENT_DAY = re.compile(r"(\d{2})/(\d{2})/(\d{4})")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


MONTH_FIRST = "MDY"
DAY_FIRST = "DMY"


def infer_date_order(cells: list[str],
                     target_date: str | None = None) -> str | None:
    """Work out whether a board renders month-first or day-first.

    Assuming month-first was a live bug waiting to happen. Basketball
    renders ``09/27/2026``, which can only be month-first; the cricket
    sweep on 2026-09-28 returned ``30/09/2026``, which can only be
    day-first. A board read in the wrong order files matches under a day
    they do not belong to, and the 24h timing proof is a claim about that
    day — so the order is measured per board, never assumed.

    Returns ``None`` when the board gives no disambiguating date (every
    value has both components under 13) or when it contradicts itself.
    Callers must refuse such a board rather than pick an order.
    """
    month_first = day_first = False
    for cell in cells:
        match = _EVENT_DAY.search(cell)
        if not match:
            continue
        first, second = int(match.group(1)), int(match.group(2))
        if first > 12:
            day_first = True
        if second > 12:
            month_first = True
    if month_first and day_first:
        return None  # the board disagrees with itself; trust neither
    if month_first:
        return MONTH_FIRST
    if day_first:
        return DAY_FIRST

    # Nothing on the board settles it — every date reads both ways, which
    # is the normal case in the first twelve days of a month. Rugby was
    # lost exactly here: its board held 10/01 and 10/02 and was refused as
    # unreadable, though it was perfectly reachable.
    #
    # The URL is the anchor. A board fetched for a given date is mostly
    # that date's matches, so whichever reading reproduces the date we
    # asked for is the board's order. If both readings can produce it
    # (2026-05-05) or neither does, nothing has been settled and the
    # caller must still refuse.
    if not target_date:
        return None
    matches = {order for order in (MONTH_FIRST, DAY_FIRST)
               if any(event_day_from_kickoff(cell, order) == target_date
                      for cell in cells)}
    return matches.pop() if len(matches) == 1 else None


# A multi-day fixture renders as a range: "08/05 - 11/05/2026". Only the
# end carries the year, so a plain search finds the LAST day.
_DATE_RANGE = re.compile(
    r"(\d{1,2})/(\d{1,2})\s*[-\u2013]\s*(\d{1,2})/(\d{1,2})/(\d{4})")


def event_day_from_kickoff(value: str, order: str = MONTH_FIRST) -> str | None:
    """ISO day from a rendered kickoff such as ``09/27/2026 2:00 AM``.

    ``order`` must come from :func:`infer_date_order` for the board being
    read. Only the day is taken; the clock is deliberately ignored, because
    the renderer emits it in a timezone derived from the relay's egress IP
    rather than UTC.

    A multi-day fixture (a cricket Test spans four days) resolves to the
    day it BEGINS. The end date is the wrong answer in the way that
    matters: a pick frozen "24 hours before" the last day of a Test would
    be frozen three days after the match started, which is not a
    prediction. Taking the start makes the freeze claim true or the row
    excluded, never silently late.
    """
    ranged = _DATE_RANGE.search(value)
    if ranged:
        first, second, _, _, year = ranged.groups()
        month, day = ((first, second) if order == MONTH_FIRST
                      else (second, first))
        if 1 <= int(month) <= 12 and 1 <= int(day) <= 31:
            return f"{year}-{int(month):02d}-{int(day):02d}"
        return None
    match = _EVENT_DAY.search(value)
    if not match:
        return None
    first, second = match.group(1), match.group(2)
    month, day = (first, second) if order == MONTH_FIRST else (second, first)
    if not (1 <= int(month) <= 12 and 1 <= int(day) <= 31):
        return None
    return f"{match.group(3)}-{month}-{day}"


def match_url(cell: str) -> str | None:
    """The match URL from a rendered ``.tnms`` cell."""
    found = _MARKDOWN_LINK.search(cell)
    return found.group("url").strip() if found else None


def event_id_from_url(url: str) -> str:
    """Forebet's own match id, or a stable digest when the URL has none.

    The trailing numeric segment of a match URL is the site's match id, and
    reusing it keeps a pick joinable to the same match at settlement. When a
    URL carries no id the digest is deterministic, so the same row yields the
    same identity on a later capture.
    """
    tail = url.rstrip("/").split("/")[-1]
    digits = re.search(r"(\d{4,})$", tail)
    if digits:
        return digits.group(1)
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def _probabilities(cell: str, *, draw_possible: bool
                   ) -> tuple[float | None, float | None, float | None]:
    """Home / draw / away probabilities from a rendered ``.fprc`` cell."""
    values = [float(token) for token in _NUMBER.findall(cell)]
    if draw_possible and len(values) >= 3:
        return values[0] / 100.0, values[1] / 100.0, values[2] / 100.0
    if not draw_possible and len(values) >= 2:
        return values[0] / 100.0, None, values[1] / 100.0
    return None, None, None


def rows_to_events(board: BoardColumns, *, captured_at: str,
                   raw_sha256: str = "") -> list[EventSnapshot]:
    """Turn validated columns into events the evaluator can already rank.

    Rows are skipped, never guessed at, when identity or probabilities are
    unreadable, and when the row's own rendered day is not the day being
    captured — boards carry neighbouring days, and the renderer's clock is
    not trustworthy enough to reassign one.
    """
    spec = SPORTS[board.sport]
    order = infer_date_order(board.columns.get("link", []),
                             board.target_date)
    if order is None:
        raise ColumnAlignmentError(
            f"{board.sport} {board.target_date}: cannot tell whether this "
            f"board renders month-first or day-first; refusing rather than "
            f"filing matches under a day they may not belong to")
    events: list[EventSnapshot] = []
    for row in board.rows():
        url = match_url(row.get("link", ""))
        if not url:
            continue
        if event_day_from_kickoff(row.get("link", ""), order) != board.target_date:
            continue
        home, away = row.get("home", "").strip(), row.get("away", "").strip()
        if not home or not away:
            continue
        probability_1, draw_probability, probability_2 = _probabilities(
            row.get("probabilities", ""), draw_possible=spec.draw_possible)
        if probability_1 is None or probability_2 is None:
            continue
        pick = row.get("pick", "").strip()
        totals = _NUMBER.findall(row.get("average", ""))
        events.append(EventSnapshot(
            # "<sport>:<id>", the identity every other route in this
            # system uses (parsers.parse_football_json,
            # settlement._base_row). A bare id here would look right in
            # isolation and never join to its own settlement row.
            event_id=f"{board.sport}:{event_id_from_url(url)}",
            sport=board.sport,
            event_date=board.target_date,
            captured_at=captured_at,
            source_url=board.source_url,
            participant_1=home,
            participant_2=away,
            probability_1=probability_1,
            probability_2=probability_2,
            draw_probability=draw_probability,
            forebet_pick=int(pick) if pick in {"1", "2"} else None,
            kickoff=row.get("kickoff", "").strip(),
            predicted_score=row.get("predicted_score", "").strip(),
            predicted_total=float(totals[0]) if totals else None,
            raw_sha256=raw_sha256,
        ))
    return events


# --------------------------------------------------------------------------
# Capture policy
#
# Three questions had to be settled before this route could feed anything.
# All three are answered from invariants this project already holds, not
# from convenience.
# --------------------------------------------------------------------------

#: A board that could not be read is a gap in the record, not a quiet day.
COVERAGE_GAP = "COVERAGE_GAP"
#: The board was read cleanly and holds no match on the requested date.
NO_ROWS_FOR_DATE = "NO_ROWS_FOR_DATE"
#: The board was read cleanly and produced rankable events.
CAPTURED = "CAPTURED"


@dataclass
class BoardCapture:
    """Outcome of one attempt at one board on one date.

    Every field a receipt needs, including the reason for a failure and the
    dates the board actually showed. A caller never has to distinguish "no
    fixtures" from "we could not read it" by looking at a row count.
    """

    status: str
    sport: str
    target_date: str
    source_url: str
    events: list[EventSnapshot] = field(default_factory=list)
    reason: str = ""
    observed_dates: tuple[str, ...] = ()
    row_count: int = 0
    partial: bool = False
    suspect_short: bool = False
    #: The validated columns, kept so a caller can freeze the extracts
    #: themselves rather than only the events derived from them.
    board: "BoardColumns | None" = None

    @property
    def usable(self) -> bool:
        return self.status == CAPTURED and bool(self.events)


def observed_dates(columns: dict[str, list[str]],
                   order: str | None = None,
                   target_date: str | None = None) -> tuple[str, ...]:
    """Distinct dates the board rendered, earliest first.

    This is the measurement that tells us how far ahead each sport
    publishes — the thing the whole late-publishing problem turns on — so
    it is recorded even when it contains nothing for the requested date.
    """
    cells = columns.get("link", [])
    order = order or infer_date_order(cells, target_date)
    if order is None:
        return ()
    seen = {day for cell in cells if (day := event_day_from_kickoff(cell, order))}
    return tuple(sorted(seen))


def capture_board(board_url: str, sport: str, target_date: str, *,
                  captured_at: str, timeout: int = 60, opener=None,
                  expected_rows: int | None = None, raw_sha256: str = "",
                  attempts: int = 3, backoff: float = 8.0,
                  sleep=time.sleep,
                  selectors: dict[str, str] | None = None,
                  required: tuple[str, ...] | None = None,
                  before_request=None,
                  scope: str = ROW_SCOPE,
                  circuit_breaker_columns: int = 2,
                  circuit_breaker_attempts: int = 1) -> BoardCapture:
    """Capture one board, returning an outcome instead of raising.

    Policy decisions, and why:

    **A board that will not read is never walked forward to another date.**
    The capture is anchored to a date, and the 24h timing proof is a claim
    about that date. Re-pointing a failed capture at the next day would
    silently file evidence under a day it does not describe. When the board
    is readable but holds nothing for the requested date, the dates it did
    show are recorded instead — that measures the sport's publication
    horizon, which is the real input to scheduling.

    **A failure is never handed to the short-notice track.** The two tracks
    are compared to each other, so each one's record has to reflect its own
    timing discipline. Routing 24h-track capture failures into the event-day
    track would make the event-day hit rate a mixture of picks decided late
    by design and picks decided late because a fetch failed, and the
    comparison would stop meaning anything. Each track captures for itself;
    a gap is logged as a gap.

    **Silence is not evidence of absence.** A board that renders nothing at
    all is reported as a gap, not as a day without fixtures. An empty render
    and a throttled blank are indistinguishable from here, and this route
    has already served short bodies that looked like data. ``NO_ROWS_FOR_DATE``
    is therefore only ever claimed on positive evidence: the board rendered
    matches, and they belong to other dates.

    **There is no absolute row-count floor.** A floor is a guess about how
    busy a sport is on an arbitrary date, and on a genuinely quiet day it
    converts real coverage into a false failure — manufacturing absence is
    as wrong as manufacturing data. Partial renders show up as columns
    disagreeing, which already fails closed. A count far below what this
    board usually holds is recorded as ``suspect_short`` for review and
    does not, by itself, reject the capture.
    """
    try:
        board = fetch_board_columns(board_url, sport, target_date,
                                    timeout=timeout, opener=opener,
                                    attempts=attempts, backoff=backoff,
                                    sleep=sleep, selectors=selectors,
                                    required=required,
                                    before_request=before_request,
                                    scope=scope,
                                    circuit_breaker_columns=circuit_breaker_columns,
                                    circuit_breaker_attempts=circuit_breaker_attempts)
    except (ColumnFetchError, ColumnAlignmentError) as exc:
        return BoardCapture(status=COVERAGE_GAP, sport=sport,
                            target_date=target_date, source_url=board_url,
                            reason=str(exc))

    days = observed_dates(board.columns, target_date=target_date)
    try:
        events = rows_to_events(board, captured_at=captured_at,
                                raw_sha256=raw_sha256)
    except ColumnAlignmentError as exc:
        return BoardCapture(status=COVERAGE_GAP, sport=sport,
                            target_date=target_date, source_url=board_url,
                            observed_dates=days, row_count=board.row_count,
                            reason=str(exc))
    suspect = bool(expected_rows) and board.row_count * 2 < (expected_rows or 0)
    # A capture made for SETTLING carries no probabilities, because a
    # finished match is not being ranked. Such a board can never produce
    # events, so "no events" cannot mean "no fixtures on this date" for
    # it — run 36415677508 rendered 22 volleyball rows and reported
    # "none on 2026-09-27; dates present: 2026-09-27", which is a
    # contradiction the caller had no way to interpret. Judge those
    # captures by the rows themselves.
    ranking = "probabilities" in board.columns
    if not ranking:
        order = infer_date_order(board.columns.get("link", []), target_date)
        on_target = 0 if order is None else sum(
            1 for cell in board.columns.get("link", [])
            if event_day_from_kickoff(cell, order) == target_date)
        if order is None:
            return BoardCapture(
                status=COVERAGE_GAP, sport=sport, target_date=target_date,
                source_url=board_url, observed_dates=days,
                row_count=board.row_count, partial=board.partial,
                reason=(f"{sport} {target_date}: date order unreadable, so "
                        f"no row can be placed on a day"))
        if on_target == 0:
            return BoardCapture(
                status=NO_ROWS_FOR_DATE, sport=sport,
                target_date=target_date, source_url=board_url,
                observed_dates=days, row_count=board.row_count,
                partial=board.partial, suspect_short=suspect,
                reason=(f"board rendered {board.row_count} rows, none on "
                        f"{target_date}; dates present: "
                        f"{', '.join(days) or 'none'}"))
        return BoardCapture(
            status=CAPTURED, sport=sport, target_date=target_date,
            source_url=board_url, events=(), observed_dates=days,
            row_count=board.row_count, partial=board.partial,
            suspect_short=suspect, board=board)
    if not events:
        return BoardCapture(
            status=NO_ROWS_FOR_DATE, sport=sport, target_date=target_date,
            source_url=board_url, observed_dates=days,
            row_count=board.row_count, partial=board.partial,
            suspect_short=suspect,
            reason=(f"board rendered {board.row_count} rows, none on "
                    f"{target_date}; dates present: {', '.join(days) or 'none'}"))
    return BoardCapture(status=CAPTURED, sport=sport, target_date=target_date,
                        source_url=board_url, events=events,
                        observed_dates=days, row_count=board.row_count,
                        partial=board.partial, suspect_short=suspect,
                        board=board)


# --------------------------------------------------------------------------
# Serialisation and settlement
# --------------------------------------------------------------------------

#: Body format written when a board is captured through this route. The raw
#: bytes are the column extracts themselves, not the events derived from
#: them, so a parse is reproducible from what was frozen — same rule the
#: HTML captures follow.
BODY_FORMAT = "columns_v1"

#: Columns a D+1 settlement needs on top of identity: the score and the
#: status that says the score is final.
# Settling is a different job from ranking and needs a different board.
# Only these five are read by settled_rows: the link (identity and the
# row's own day), the two names, the score and the status. Fetching the
# other three cost three requests per board and bought nothing - and it
# was not free, because a column is a request: every settlement_probe run
# up to 36404825813 spent its whole time slice and graded nothing.
SETTLEMENT_COLUMN_SELECTORS: dict[str, str] = {
    "link": ".tnms",
    "home": ".homeTeam",
    "away": ".awayTeam",
    "score": ".lscr_td",
    "status": ".scoreLnk",
}

#: A settlement capture without these has nothing to grade: an empty score
#: column on a results board is a refusal to answer, not a sport that lacks
#: the field.
SETTLEMENT_REQUIRED_COLUMNS = tuple(SETTLEMENT_COLUMN_SELECTORS)

#: Statuses that mean the score on the board is final. Anything else — live,
#: postponed, abandoned — is not settled and must not be graded.
FINAL_STATUSES = frozenset({"FT", "AOT", "AP", "FINAL"})


def serialise_columns(board: BoardColumns) -> bytes:
    """Freeze the extracts themselves, so the parse can be redone."""
    return json.dumps({
        "format": BODY_FORMAT,
        "sport": board.sport,
        "target_date": board.target_date,
        "source_url": board.source_url,
        "row_scope": ROW_SCOPE,
        "row_count": board.row_count,
        "partial": board.partial,
        "columns": board.columns,
        # Not key-sorted: the format marker leads, so what a body IS can
        # be seen in its first bytes by a human or a hexdump, even though
        # looks_like_columns_body no longer depends on that.
    }, indent=2).encode()


def deserialise_columns(body: bytes) -> BoardColumns:
    """Rebuild a board from frozen bytes. Raises if they are not ours."""
    payload = json.loads(body.decode("utf-8"))
    if payload.get("format") != BODY_FORMAT:
        raise ValueError(f"not a {BODY_FORMAT} body: {payload.get('format')!r}")
    return BoardColumns(
        sport=payload["sport"],
        target_date=payload["target_date"],
        source_url=payload["source_url"],
        columns={name: list(values)
                 for name, values in payload["columns"].items()},
        row_count=int(payload["row_count"]),
        partial=bool(payload.get("partial")),
    )


def looks_like_columns_body(body: bytes) -> bool:
    """Is this stored body a column capture?

    Answered by READING the body, not by hoping a marker lands early in
    it. The first version searched the first 400 bytes for the format
    string; a real ten-row volleyball board puts it at byte 1406, because
    the JSON is key-sorted and "columns" sorts before "format". Run
    36421154844 captured that board correctly through the production
    collector, wrote 3,032 good bytes to disk, and then parsed it as HTML
    and produced nothing - a capture that is written and unreadable is
    worse than one that fails, because it looks like a quiet day.
    """
    if not body[:200].lstrip().startswith(b"{"):
        return False
    try:
        payload = json.loads(body.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("format") == BODY_FORMAT


def _score_pair(cell: str) -> tuple[float, float] | None:
    """Both scores from a rendered result cell such as ``87 - 74``."""
    numbers = [float(token) for token in _NUMBER.findall(cell)]
    if len(numbers) < 2:
        return None
    return numbers[0], numbers[1]


def settled_rows(board: BoardColumns) -> list[dict[str, Any]]:
    """Rows whose result is final, ready for grading.

    A fixture is graded only when the board says the score is final. Live,
    postponed and abandoned rows are skipped rather than graded on whatever
    numbers happen to be showing — a half-time score recorded as a result
    would settle a pick against a match that had not finished.
    """
    order = infer_date_order(board.columns.get("link", []), board.target_date)
    if order is None:
        raise ColumnAlignmentError(
            f"{board.sport} {board.target_date}: date order unreadable")
    out: list[dict[str, Any]] = []
    for row in board.rows():
        status = row.get("status", "").strip().upper()
        if status not in FINAL_STATUSES:
            continue
        if event_day_from_kickoff(row.get("link", ""), order) != board.target_date:
            continue
        scores = _score_pair(row.get("score", ""))
        url = match_url(row.get("link", ""))
        if scores is None or not url:
            continue
        score_1, score_2 = scores
        out.append({
            "event_id": f"{board.sport}:{event_id_from_url(url)}",
            "participant_1": row.get("home", "").strip(),
            "participant_2": row.get("away", "").strip(),
            "score_1": score_1,
            "score_2": score_2,
            "winner_index": 1 if score_1 > score_2 else 2 if score_2 > score_1 else 0,
            "status": status,
            "source_url": url,
        })
    return out


def rendered_kickoffs(board: BoardColumns) -> dict[str, str]:
    """Event id -> the listing text the renderer produced for it.

    One half of the render-clock calibration: the other half is the same
    matches' instants from the tz=0 JSON. Keyed by the site's own match id
    so the join is exact rather than by name.
    """
    out: dict[str, str] = {}
    for row in board.rows():
        cell = row.get("link", "")
        url = match_url(cell)
        if not url:
            continue
        out[f"{board.sport}:{event_id_from_url(url)}"] = cell
    return out
