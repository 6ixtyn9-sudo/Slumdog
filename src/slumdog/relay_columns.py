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

import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .forebet import RELAY_BASE, looks_like_challenge_page

# One selector per field. The renderer returns each match's value on its own
# line, in board order.
COLUMN_SELECTORS: dict[str, str] = {
    "home": ".homeTeam",
    "away": ".awayTeam",
    "kickoff": ".date_bah",
    "probabilities": ".fprc",
    "pick": ".forepr",
    "predicted_score": ".ex_sc",
    "average": ".avg_sc",
}

# Fields without which a row cannot be a pick: who is playing and when.
REQUIRED_COLUMNS: tuple[str, ...] = ("home", "away", "kickoff")

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
