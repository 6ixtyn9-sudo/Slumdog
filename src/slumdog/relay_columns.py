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
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .contracts import EventSnapshot
from .forebet import RELAY_BASE, looks_like_challenge_page
from .sports import SPORTS

# One selector per field. The renderer returns each match's value on its own
# line, in board order.
COLUMN_SELECTORS: dict[str, str] = {
    # .tnms renders as a Markdown link, which is the only place the match
    # URL survives the render — and the URL is the only real match identity
    # available through this route.
    "link": ".rcnt .tnms",
    "home": ".homeTeam",
    "away": ".awayTeam",
    "kickoff": ".date_bah",
    "probabilities": ".fprc",
    "pick": ".forepr",
    "predicted_score": ".ex_sc",
    "average": ".avg_sc",
}

# Fields without which a row cannot be a pick: who is playing and when.
REQUIRED_COLUMNS: tuple[str, ...] = ("link", "home", "away", "kickoff")

# Column headings the renderer includes as the first line of an extract.
_HEADINGS = {
    "home team", "away team", "prob. %", "pred", "correct score",
    "avg. points", "coef.", "score", "date",
}

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


def parse_column(body: bytes, *, selector: str) -> list[str]:
    """Lines of one column extract, without the heading row.

    A bot-check page is a 200 with a body, so it is rejected here rather
    than counted as a zero-row board.
    """
    if looks_like_challenge_page(body):
        raise ColumnFetchError(
            f"{selector}: relay returned a bot-check page ({len(body)} bytes)")
    rows = [line.strip() for line in strip_wrapper(body).splitlines()
            if line.strip()]
    while rows and rows[0].strip().lower() in _HEADINGS:
        rows.pop(0)
    return rows


def fetch_column(board_url: str, selector: str, *, timeout: int = 60,
                 opener=None) -> list[str]:
    """Fetch a single column through the renderer."""
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
    try:
        with open_url(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        # 422 is what the renderer answers when it matches nothing, which
        # happens both for a genuinely absent selector and under throttling.
        raise ColumnFetchError(f"{selector}: HTTP {exc.code}") from exc
    except Exception as exc:  # noqa: BLE001 - surfaced as one failure type
        raise ColumnFetchError(f"{selector}: {type(exc).__name__}") from exc
    return parse_column(body, selector=selector)


def fetch_board_columns(board_url: str, sport: str, target_date: str, *,
                        timeout: int = 60, opener=None,
                        minimum_rows: int = 1) -> BoardColumns:
    """Fetch every column for a board and validate that they agree.

    Raises rather than returning a half-built board: a throttled render can
    hand back a genuine-looking but partial listing, and a short board
    frozen as a complete one is indistinguishable from a quiet fixture day.
    """
    columns: dict[str, list[str]] = {}
    failures: list[str] = []
    for name, selector in COLUMN_SELECTORS.items():
        try:
            columns[name] = fetch_column(board_url, selector, timeout=timeout,
                                         opener=opener)
        except ColumnFetchError as exc:
            failures.append(str(exc))

    missing = [name for name in REQUIRED_COLUMNS if name not in columns]
    if missing:
        raise ColumnFetchError(
            f"{sport} {target_date}: missing required column(s) "
            f"{', '.join(missing)}; failures: {'; '.join(failures) or 'none'}")

    counts = {name: len(values) for name, values in columns.items()}
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
        # Optional columns that failed leave the row thinner than ideal.
        partial=bool(failures),
    )


_MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\((?P<url>[^)]+)\)")
_EVENT_DAY = re.compile(r"(\d{2})/(\d{2})/(\d{4})")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def event_day_from_kickoff(value: str) -> str | None:
    """ISO day from a rendered kickoff like ``09/27/2026 2:00 AM``.

    The board renders American order (month first). This reads the day only;
    the clock is deliberately ignored, because the renderer emits it in a
    timezone derived from the relay's egress IP, not UTC.
    """
    match = _EVENT_DAY.search(value)
    if not match:
        return None
    return f"{match.group(3)}-{match.group(1)}-{match.group(2)}"


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
    events: list[EventSnapshot] = []
    for row in board.rows():
        url = match_url(row.get("link", ""))
        if not url:
            continue
        if event_day_from_kickoff(row.get("link", "")) != board.target_date:
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
            event_id=event_id_from_url(url),
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
