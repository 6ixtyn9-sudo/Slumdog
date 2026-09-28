#!/usr/bin/env python3
"""Probe how Forebet renders kickoff times to *us*, and whether the raw
listing HTML carries a machine-readable start instant.

Why this exists
---------------
The EVENT_DAY track's entire claim is "this pick was frozen at least N
minutes before kickoff". That is only as good as the timezone of the kickoff
we read off the listing. Measured on 2026-09-26 from a browser-side client:

* the football JSON endpoint pins ``tz=0`` and its ``DATE_BAH`` is UTC, but
* the HTML boards render kickoff in a timezone derived from the REQUESTING
  CLIENT, and ``?tz=0`` is ignored there (the 2026-09-26 1X2 board showed
  match 2468143 as ``09/25/2026 9:00 PM`` against ``2026-09-26 02:00:00`` in
  the tz=0 JSON — five hours).

So every non-football sport is currently refused by the gate
(``KICKOFF_TIMEZONE_NOT_PROVEN_UTC``). This script gathers the evidence
needed to lift that hold honestly. It answers two questions:

1. **Is there a machine-readable start instant in the raw HTML?** (a
   ``data-*`` attribute, a ``<time datetime=...>``, a JSON-LD ``startDate``,
   or a bare epoch). If yes, the offset problem disappears: parse that field
   instead of the rendered text.
2. **If not, what offset does OUR relay render at?** Measured, not assumed:
   fetch the football JSON (true UTC) and the football HTML board through the
   same relay in the same pass, join them on the match id in each row's href,
   and report the offset distribution.

It is read-only: no capture is frozen, no evidence tree is touched, nothing
is committed. Run it from the repo root::

    python scripts/probe_kickoff_timezone.py --date 2026-09-27
    python scripts/probe_kickoff_timezone.py --date 2026-09-27 --sport basketball
    python scripts/probe_kickoff_timezone.py --date 2026-09-27 --out /tmp/probe.json

Exit status is 0 only when the probe reached a definite answer (either a
machine-readable field exists, or the offset is unanimous across enough
joined matches). Anything else exits 1: an ambiguous probe must not be read
as permission to trust the timestamps.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bs4 import BeautifulSoup  # noqa: E402

from slumdog.forebet import board_url, looks_like_challenge_page  # noqa: E402
from slumdog.forebet import (  # noqa: E402
    RELAY_BASE,
    fetch_with_fallback,
    relay_get_markdown,
    source_url,
)
from slumdog.parsers import BASE  # noqa: E402
from slumdog.relay_columns import (  # noqa: E402
    CAPTURED,
    COVERAGE_GAP,
    COLUMN_SELECTORS,
    REQUIRED_COLUMNS,
    SETTLEMENT_COLUMN_SELECTORS,
    SETTLEMENT_REQUIRED_COLUMNS,
    SETTLEMENT_ROW_SCOPE,
    capture_board,
    settled_rows,
)
from slumdog.relay_columns import (  # noqa: E402
    event_id_from_url,
    fetch_column,
    match_url,
    scoped,
)
from slumdog.render_clock import measure_render_clock  # noqa: E402
from slumdog.relay_columns import (  # noqa: E402
    DAY_FIRST,
    MONTH_FIRST,
    ROW_SCOPE,
    event_day_from_kickoff,
    infer_date_order,
    strip_wrapper,
)
from slumdog.sports import SPORTS  # noqa: E402


_DEADLINE: float | None = None


def set_deadline(seconds: float | None) -> None:
    """Arm a wall-clock budget for the whole probe.

    The workflow caps the job at 15 minutes and is owner-authored, so a
    stage that overruns does not merely lose its own result — the job is
    killed before any annotation is emitted and the entire run reports
    nothing. Stages therefore check the clock rather than assuming they
    will be allowed to finish.
    """
    global _DEADLINE
    _DEADLINE = None if seconds is None else time.monotonic() + seconds


def time_left() -> float:
    """Seconds remaining, or a large number when no budget is armed."""
    return 1e9 if _DEADLINE is None else _DEADLINE - time.monotonic()


class BudgetExhausted(RuntimeError):
    """Raised in place of a request once the wall-clock budget is spent."""


def check_budget() -> None:
    """Refuse a network call that the job no longer has time to finish.

    This is the chokepoint that keeps a long tail of stages from spending
    the job's last minutes and getting the runner killed mid-stage. Once
    the budget is gone every remaining stage fails fast, records its own
    failure, and the probe still reaches the point where it reports.
    """
    if time_left() < 20:
        raise BudgetExhausted(
            f"probe budget exhausted ({time_left():.0f}s left)")


def pace(seconds: float) -> None:
    """Politeness sleep that never sleeps past the budget."""
    time.sleep(max(0.0, min(seconds, time_left() - 20)))


# A rendered listing time, e.g. "25/09/2026 21:00" or "09/25/2026 9:00 PM".
_DISPLAY_FORMATS = (
    "%d/%m/%Y %H:%M",
    "%m/%d/%Y %I:%M %p",
    "%Y-%m-%d %H:%M",
)

# Values that would let us skip the offset problem entirely.
_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
_EPOCH_RE = re.compile(r"^1[5-9]\d{8}$|^2[0-9]\d{8}$")


def parse_display_time(text: str) -> dt.datetime | None:
    """Parse a rendered listing timestamp, timezone unknown by definition."""
    text = " ".join((text or "").split())
    for fmt in _DISPLAY_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def offset_minutes(displayed: dt.datetime, utc: dt.datetime) -> int:
    """Minutes the rendered time runs AHEAD of the true UTC instant.

    A positive result is the dangerous direction: the board makes an event
    look later than it is, so a lead gate would overstate the lead.
    """
    utc = utc.replace(tzinfo=None)
    return int(round((displayed - utc).total_seconds() / 60.0))


def summarise_offsets(offsets: list[int]) -> dict[str, Any]:
    """Modal offset plus how unanimous the sample is."""
    if not offsets:
        return {
            "joined": 0, "modal_offset_minutes": None,
            "agreement": 0.0, "distribution": {}, "unanimous": False,
        }
    counts = Counter(offsets)
    modal, hits = counts.most_common(1)[0]
    return {
        "joined": len(offsets),
        "modal_offset_minutes": modal,
        "agreement": hits / len(offsets),
        "distribution": {str(k): v for k, v in sorted(counts.items())},
        "unanimous": len(counts) == 1,
    }


def machine_readable_candidates(html: bytes | str) -> list[dict[str, str]]:
    """Attributes/nodes in the raw HTML that look like a real instant.

    Any hit here is worth more than the whole offset calibration: it is a
    timestamp the site publishes for machines, not a string it rendered for
    a human in some timezone.
    """
    soup = BeautifulSoup(html, "html.parser")
    found: list[dict[str, str]] = []

    for script in soup.select('script[type="application/ld+json"]'):
        body = script.string or ""
        if "startDate" in body or _ISO_RE.search(body):
            found.append({
                "kind": "json_ld", "attribute": "startDate",
                "sample": body.strip()[:200],
            })

    for node in soup.find_all(True):
        for name, value in (node.attrs or {}).items():
            if isinstance(value, list):
                value = " ".join(value)
            value = str(value)
            interesting = (
                name in {"datetime", "data-time", "data-timestamp", "data-utc"}
                or (name.startswith("data-")
                    and (_ISO_RE.search(value) or _EPOCH_RE.match(value.strip())))
            )
            if interesting:
                found.append({
                    "kind": "attribute", "attribute": name,
                    "element": node.name, "sample": value[:120],
                })
        if len(found) >= 25:  # a handful is proof enough
            break
    return found


# Every fetch failure is recorded here instead of raising. A probe that dies
# on its first request tells us nothing at all, and on a runner its traceback
# goes to stderr where it is easy to lose — which is exactly what happened on
# the first attempt (run 36242469136: the step finished in under a second and
# wrote no report).
FETCH_ERRORS: list[dict[str, str]] = []


def fetch(url: str, *, timeout: int, json_endpoint: bool = False) -> bytes | None:
    """Fetch the way production does, and never raise.

    ``json_endpoint`` selects the order the collector itself uses: the
    football JSON endpoint is qualified for the relay's Markdown reader mode
    (the html-forced mode 401s on cloud IPs), while HTML boards go through
    ``fetch_with_fallback`` first. Returns ``None`` when every route failed;
    the reason lands in :data:`FETCH_ERRORS`.
    """
    try:
        check_budget()
    except BudgetExhausted as exc:
        FETCH_ERRORS.append(f"{url}: {exc}")
        return None
    relay = RELAY_BASE + url
    routes = (
        [("relay_markdown", lambda: relay_get_markdown(relay, url, timeout=timeout)),
         ("relay_or_direct", lambda: fetch_with_fallback(relay, url, timeout=timeout)[0])]
        if json_endpoint else
        [("relay_or_direct", lambda: fetch_with_fallback(relay, url, timeout=timeout)[0]),
         ("relay_markdown", lambda: relay_get_markdown(relay, url, timeout=timeout))]
    )
    for route, call in routes:
        try:
            body = call()
            if body:
                return body
            FETCH_ERRORS.append({"url": url, "route": route, "error": "empty body"})
        except Exception as exc:
            FETCH_ERRORS.append({
                "url": url, "route": route,
                "error": f"{type(exc).__name__}: {exc}"[:300],
            })
    return None


#: Filled in when the tz=0 endpoint answers something unparseable, so the
#: annotation can say WHAT it answered.
FOOTBALL_JSON_FINGERPRINT: dict[str, Any] = {}


def football_utc_kickoffs(date: str, *, timeout: int) -> dict[str, dt.datetime]:
    """``{match_id: kickoff_utc}`` from the tz=0-pinned JSON endpoint."""
    from slumdog.parsers import _load_football_payload

    body = fetch(
        source_url(SPORTS["football"], date), timeout=timeout, json_endpoint=True)
    if body is None:
        return {}
    try:
        payload = _load_football_payload(body)
    except Exception as exc:
        # What came back matters more than the fact it would not parse.
        # "JSONDecodeError at char 0" describes a challenge page, a relay
        # Markdown wrapper and an empty body identically, and those need
        # three different fixes. Production reads this endpoint too, so a
        # silent change here would stop football as well.
        FOOTBALL_JSON_FINGERPRINT.update(body_fingerprint(body))
        FOOTBALL_JSON_FINGERPRINT["parse_error"] = (
            f"{type(exc).__name__}: {exc}"[:200])
        FETCH_ERRORS.append({
            "url": "football-json", "route": "parse",
            "error": f"{type(exc).__name__}: {exc}"[:300],
        })
        return {}
    out: dict[str, dt.datetime] = {}
    for row in payload[0]:
        if not isinstance(row, dict):
            continue
        raw = str(row.get("DATE_BAH") or "")
        try:
            out[str(row.get("id"))] = dt.datetime.strptime(
                raw[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            continue
    return out


def body_fingerprint(body: bytes | None, *, sample: int = 320) -> dict[str, Any]:
    """Cheap description of a fetched body, for when parsing finds nothing.

    A board that parses to zero rows is either a different page (a block or
    challenge page), a different format (the relay's Markdown instead of
    HTML), or a genuinely empty board. These three need different fixes, so
    the probe must say which one it got.
    """
    if not body:
        return {"bytes": 0, "sample": "", "has_rcnt": False, "looks_like": "empty"}
    text = body.decode("utf-8", "replace")
    lowered = text.lower()
    if "rcnt" in lowered:
        looks_like = "board_html"
    elif "markdown content" in lowered or text.lstrip().startswith("Title:"):
        looks_like = "relay_markdown_wrapper"
    elif any(t in lowered for t in ("just a moment", "cf-browser", "cloudflare",
                                    "captcha", "attention required")):
        looks_like = "challenge_page"
    elif "<html" in lowered:
        looks_like = "other_html"
    else:
        looks_like = "unknown"
    return {
        "bytes": len(body),
        "sample": " ".join(text[:sample].split()),
        "has_rcnt": "rcnt" in lowered,
        "looks_like": looks_like,
    }


def html_board_rows(body: bytes) -> list[dict[str, str]]:
    """``[{match_id, displayed}]`` from a rendered listing board."""
    soup = BeautifulSoup(body, "html.parser")
    rows: list[dict[str, str]] = []
    for row in soup.select("div.rcnt"):
        link = row.select_one("a.tnmscn")
        date_node = row.select_one(".date_bah")
        if not link or not date_node:
            continue
        href = str(link.get("href") or "")
        match_id = href.rstrip("/").split("-")[-1]
        rows.append({
            "match_id": match_id,
            "displayed": " ".join(date_node.get_text(" ").split()),
        })
    return rows


# --- endpoint hunt ---------------------------------------------------------
# Football is the only sport that still captures, and the only one fetched
# through a JSON endpoint (/scripts/getrs.php) rather than an HTML board.
# That endpoint is not bot-checked. If the other sports have an equivalent,
# it fixes coverage without any key or credential. The live boards cannot be
# read to find out -- they return the interstitial -- but archived copies of
# the same pages are served by the Wayback Machine as raw HTML, scripts and
# all. So: recover the markup from the archive, read the endpoints out of it,
# then test those endpoints live.
WAYBACK_AVAILABLE = "https://archive.org/wayback/available?url="
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)
PHP_REF = re.compile(rb"""[\"'(=]\s*([^\"'()\s]*?/?[a-z0-9_\-]+\.php[^\"'()\s]*)""", re.I)
SCRIPT_SRC = re.compile(rb"""<script[^>]+src=[\"']([^\"']+)[\"']""", re.I)


def direct_fetch(url: str, *, timeout: int, attempts: int = 3) -> bytes:
    import urllib.request

    request = urllib.request.Request(url, headers={
        "User-Agent": BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,*/*",
    })
    last: Exception | None = None
    for attempt in range(attempts):
        if attempt:
            # archive.org refuses connections under burst load rather than
            # returning 429, so back off instead of calling it a failure.
            time.sleep(4 * attempt)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception as exc:  # noqa: BLE001 - retried, then re-raised
            last = exc
    raise last if last else RuntimeError("unreachable")


CDX_SEARCH = (
    "https://web.archive.org/cdx/search/cdx?url={pattern}&output=json"
    "&filter=statuscode:200&collapse=urlkey&limit={limit}&matchType=prefix"
)


def cdx_snapshots(pattern: str, *, limit: int, timeout: int) -> list[tuple[str, str]]:
    """(timestamp, original_url) pairs from the Wayback index, newest last.

    The availability API only answers for an exact URL; the CDX index does
    prefix search, which is what finds a sport board whose exact path was
    never archived but whose dated siblings were.

    The index returns rows in chronological order, so a positive limit hands
    back the OLDEST N. That is why this kept reading a bundle from 2020 and
    a board from 2024 while reasoning about a site that has since been
    rebuilt. A negative limit asks for the most recent N instead.
    """
    raw = direct_fetch(
        CDX_SEARCH.format(pattern=pattern, limit=-abs(limit)), timeout=timeout)
    rows = json.loads(raw or b"[]")
    return [(r[1], r[2]) for r in rows[1:]] if len(rows) > 1 else []


def archived_bytes(timestamp: str, original: str, *, timeout: int,
                   kind: str = "id_") -> bytes:
    return direct_fetch(
        f"https://web.archive.org/web/{timestamp}{kind}/{original}",
        timeout=timeout)


def archived_html(page_url: str, *, timeout: int) -> tuple[str, bytes]:
    """Raw archived markup for a page (or its nearest archived sibling)."""
    pattern = page_url.split("://", 1)[-1]
    snaps = cdx_snapshots(pattern, limit=8, timeout=timeout)
    if not snaps:
        # Fall back to the parent path: any board of this sport will do,
        # since they all ship the same scripts.
        snaps = cdx_snapshots(pattern.rsplit("/", 1)[0], limit=8, timeout=timeout)
    if not snaps:
        raise RuntimeError(f"no archived snapshot for {page_url}")
    timestamp, original = snaps[-1]
    url = f"https://web.archive.org/web/{timestamp}id_/{original}"
    return url, archived_bytes(timestamp, original, timeout=timeout)


TP_LITERAL = re.compile(rb"""tp[=:]\s*[\"']([a-z0-9_]{1,14})[\"']""", re.I)
INLINE_SCRIPT = re.compile(rb"<script(?![^>]*src=)[^>]*>(.*?)</script>",
                           re.I | re.S)
WAYBACK_PREFIX = re.compile(r"^https?://web\.archive\.org/web/[0-9a-z_]+/", re.I)


def normalise_ref(ref: str) -> str:
    """Strip archive rewriting and repair scheme-less forebet references."""
    ref = WAYBACK_PREFIX.sub("", ref)
    ref = re.sub(r"^https?://www\.forebet\.com/en/[a-z_\-]+/(?=forebet\.com/)",
                 "", ref)
    if ref.startswith("forebet.com/") or ref.startswith("m.forebet.com/"):
        ref = "https://" + ref
    return ref


def endpoint_candidates(html: bytes, page_url: str) -> list[str]:
    """Absolute .php URLs referenced by a page, most specific first."""
    from urllib.parse import urljoin

    found: list[str] = []
    for match in PHP_REF.findall(html) + SCRIPT_SRC.findall(html):
        ref = match.decode("utf-8", "replace").strip()
        if not ref or ref.endswith(".js") and "getrs" not in ref:
            continue
        if ".php" not in ref.lower():
            continue
        absolute = normalise_ref(urljoin(page_url, normalise_ref(ref)))
        if "forebet.com" in absolute and absolute not in found:
            found.append(absolute)
    found.sort(key=lambda u: (0 if "getrs" in u else 1, len(u)))
    return found


def hunt_endpoints(sport_page: str, *, timeout: int, pause: float) -> dict[str, Any]:
    """Recover a sport board's markup from the archive and mine it."""
    out: dict[str, Any] = {"page": sport_page}
    try:
        raw_url, html = archived_html(sport_page, timeout=timeout)
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"[:200]
        return out

    out["snapshot"] = raw_url
    out["bytes"] = len(html)
    out["rcnt_rows"] = html.lower().count(b'class="rcnt')
    out["php_refs"] = endpoint_candidates(html, sport_page)[:12]
    scripts = [s.decode("utf-8", "replace") for s in SCRIPT_SRC.findall(html)]
    out["script_srcs"] = scripts[:8]

    # The board fetches its rows from somewhere; that call lives in the
    # page's JavaScript, not its markup.
    from urllib.parse import urljoin

    timestamp = ""
    snap = out.get("snapshot", "")
    if "/web/" in snap:
        timestamp = snap.split("/web/", 1)[1].split("id_", 1)[0]
    mined: list[str] = []
    for src in scripts:
        if len(mined) >= 6:
            break
        absolute = urljoin(sport_page, src.replace("id_/", "/"))
        path = absolute.split("?", 1)[0].split("#", 1)[0]
        if "forebet.com" not in absolute or not path.endswith(".js"):
            continue
        pace(pause)
        try:
            body = archived_bytes(timestamp, absolute, timeout=timeout, kind="js_")
        except Exception as exc:
            mined.append(f"! {absolute}: {type(exc).__name__}")
            continue
        for ref in endpoint_candidates(body, sport_page):
            if ref not in mined:
                mined.append(ref)
        idx = body.lower().find(b"getrs")
        if idx != -1 and "js_context" not in out:
            out["js_context"] = body[max(0, idx - 220): idx + 260].decode(
                "utf-8", "replace")
    out["js_php_refs"] = mined[:10]
    out["php_refs"] = (out["php_refs"] + [
        m for m in mined if not m.startswith("!")])[:14]

    # getrs.php is built as ...&tp=<X>&... so the board's identity is that
    # one parameter. Recover the literal the page passes.
    out["tp_literals"] = sorted({
        m.decode("utf-8", "replace")
        for m in TP_LITERAL.findall(html)})[:12]
    inline: list[str] = []
    for block in INLINE_SCRIPT.findall(html):
        for needle in (b"getrs", b"&tp=", b"tp:"):
            idx = block.find(needle)
            if idx != -1 and len(inline) < 4:
                inline.append(block[max(0, idx - 160): idx + 220].decode(
                    "utf-8", "replace"))
                break
    out["inline_script_context"] = inline

    # Question 1 of the original hold: does a listing row carry a
    # machine-readable start instant, or only rendered local text?
    for needle in (b"date_bah", b"data-time", b"datetime", b"data-ts"):
        idx = html.lower().find(needle)
        if idx != -1:
            out.setdefault("markup_samples", {})[needle.decode()] = (
                html[max(0, idx - 160): idx + 200].decode("utf-8", "replace"))
    return out


def test_tp_candidates(date: str, values: list[str], *, timeout: int,
                       pause: float) -> dict[str, Any]:
    """Call getrs.php with each candidate sport code and see what comes back.

    Football's capture route is this endpoint with tp=1x2. If a sport code
    returns rows here, that sport can be captured the same way -- through
    JSON that is not bot-checked and is explicitly tz=0.
    """
    results: dict[str, Any] = {}
    for i, value in enumerate(values):
        if i:
            pace(pause)
        url = ("https://www.forebet.com/scripts/getrs.php?"
               f"ln=en&tp={value}&in={date}&ord=0&tz=0&tzs=&tze=")
        try:
            body = relay_request(RELAY_BASE + url, {
                "User-Agent": "EdgeFactory/1.0", "Accept": "text/plain",
                "X-No-Cache": "true"}, timeout=timeout)
        except Exception as exc:
            results[value] = {"error": f"{type(exc).__name__}: {exc}"[:100]}
            continue
        lowered = body.lower()
        payload_at = lowered.find(b"[[{")
        results[value] = {
            "bytes": len(body),
            "json_like": payload_at != -1,
            "sample": body[payload_at: payload_at + 220].decode(
                "utf-8", "replace") if payload_at != -1
            else body[-160:].decode("utf-8", "replace"),
        }
    return results


def test_candidates(candidates: list[str], *, timeout: int,
                    pause: float) -> dict[str, Any]:
    """Fetch each candidate endpoint live and say whether it returns data."""
    results: dict[str, Any] = {}
    for i, url in enumerate(candidates):
        if i:
            pace(pause)
        try:
            body = relay_request(RELAY_BASE + url, {
                "User-Agent": "Slumdog", "Accept": "text/plain",
                "X-No-Cache": "true", "X-Return-Format": "html"}, timeout=timeout)
        except Exception as exc:
            results[url] = {"error": f"{type(exc).__name__}: {exc}"[:120]}
            continue
        stripped = body.lstrip()
        fingerprint = body_fingerprint(body, sample=160)
        fingerprint["json_like"] = (
            stripped[:1] in (b"[", b"{") or b"<body>[[{" in body.lower())
        results[url] = fingerprint
    return results


def relay_request(url: str, headers: dict[str, str], *, timeout: int) -> bytes:
    """Raw relay GET with explicit headers, for route comparison."""
    import urllib.request

    check_budget()

    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


MATCH_LINK = re.compile(
    rb"https?://(?:www\.|m\.)?forebet\.com/en/([a-z_\-]+)/"
    rb"(?:matches|predictions)/([a-z0-9\-]*?)-(\d{5,})\b", re.I)
CLOCK = re.compile(rb"\b([01]?\d|2[0-3]):[0-5]\d\b")


ALL_JS = "https://www.forebet.com/includes/js/all.js"


def mine_js_contexts(*, timeout: int) -> dict[str, Any]:
    """Pull the bundle and show how each JSON endpoint is actually called.

    getjson.php answers with [] rather than a 404 or an interstitial, so the
    endpoint is live and unchallenged and only the parameters are wrong.
    The call site names them.
    """
    out: dict[str, Any] = {}
    try:
        snaps = cdx_snapshots("www.forebet.com/includes/js/all.js",
                              limit=8, timeout=timeout)
        if not snaps:
            out["error"] = "all.js not archived"
            return out
        timestamp, original = snaps[-1]
        out["snapshot"] = timestamp
        body = archived_bytes(timestamp, original, timeout=timeout, kind="js_")
        out["bytes"] = len(body)
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"[:140]
        return out

    for needle in (b"getjson.php", b"getjson_y", b"getjson_t", b"getftr.php"):
        start = 0
        hits: list[str] = []
        while len(hits) < 2:
            idx = body.find(needle, start)
            if idx == -1:
                break
            hits.append(body[max(0, idx - 260): idx + 260].decode(
                "utf-8", "replace"))
            start = idx + 1
        if hits:
            out[needle.decode()] = hits
    return out


def crack_getjson(date: str, *, timeout: int, pause: float) -> dict[str, Any]:
    """Vary getjson.php's parameters until something returns rows."""
    base = "https://www.forebet.com/scripts/getjson.php"
    from datetime import datetime, timedelta

    day = datetime.fromisoformat(date)
    yesterday = (day - timedelta(days=1)).strftime("%Y-%m-%d")
    variants = {
        "iso": f"{base}?gdt={date}",
        "dmy_dash": f"{base}?gdt={day.strftime('%d-%m-%Y')}",
        "dmy_slash": f"{base}?gdt={day.strftime('%d/%m/%Y')}",
        "compact": f"{base}?gdt={day.strftime('%Y%m%d')}",
        "with_sport": f"{base}?gdt={date}&sp=2",
        "with_tp": f"{base}?ln=en&gdt={date}&tp=bsk",
        "with_league": f"{base}?ln=en&gdt={date}&lg=0",
        # If future dates are simply empty, a past date proves the shape.
        "yesterday": f"{base}?gdt={yesterday}",
    }
    out: dict[str, Any] = {}
    for i, (name, url) in enumerate(variants.items()):
        if i:
            pace(min(pause, 5))
        try:
            body = relay_request(RELAY_BASE + url, {
                "User-Agent": "EdgeFactory/1.0", "Accept": "text/plain",
                "X-No-Cache": "true"}, timeout=timeout)
        except Exception as exc:
            out[name] = {"error": f"{type(exc).__name__}: {exc}"[:90]}
            continue
        marker = b"Markdown Content:"
        payload = body.split(marker, 1)[1].strip() if marker in body else body
        out[name] = {
            "bytes": len(payload),
            "empty": payload[:2] in (b"[]", b""),
            "sample": payload[:200].decode("utf-8", "replace"),
        }
    return out


SITEMAP_URL = re.compile(rb"<loc>\s*([^<\s]+)\s*</loc>", re.I)
MATCH_SLUG = re.compile(
    rb"forebet\.com/en/([a-z\-]+)/[^\s<\"']*?([a-z0-9\-]+)-(\d{5,})\b", re.I)


def discover_sitemaps(*, timeout: int, pause: float) -> dict[str, Any]:
    """Find the sitemaps, which are static XML served for crawlers.

    getjson.php wants a match id, and I called that circular because ids
    live on the board. That is only true if the board is the only place
    they live. Sitemaps list match pages by design.
    """
    out: dict[str, Any] = {}

    def _get(url: str) -> bytes:
        try:
            return direct_fetch(url, timeout=timeout, attempts=1)
        except Exception as exc:
            out.setdefault("direct_errors", []).append(
                f"{url.rsplit('/', 1)[-1]}: {type(exc).__name__}"[:60])
            return relay_request(RELAY_BASE + url, {
                "User-Agent": "EdgeFactory/1.0", "Accept": "text/plain",
                "X-No-Cache": "true", "X-Return-Format": "text"},
                timeout=timeout)

    try:
        robots = _get("https://www.forebet.com/robots.txt")
        out["robots_bytes"] = len(robots)
        out["robots_sample"] = robots[:300].decode("utf-8", "replace")
        listed = re.findall(rb"(?im)^\s*sitemap:\s*(\S+)", robots)
        out["sitemaps_listed"] = [s.decode() for s in listed][:8]
    except Exception as exc:
        out["robots_error"] = f"{type(exc).__name__}: {exc}"[:120]

    candidates = out.get("sitemaps_listed") or [
        "https://www.forebet.com/sitemap.xml"]
    out["children"] = {}
    for i, sitemap in enumerate(candidates[:2]):
        if i:
            pace(min(pause, 5))
        try:
            body = _get(sitemap)
        except Exception as exc:
            out["children"][sitemap] = {
                "error": f"{type(exc).__name__}: {exc}"[:90]}
            continue
        locs = [m.decode("utf-8", "replace")
                for m in SITEMAP_URL.findall(body)]
        out["children"][sitemap] = {
            "bytes": len(body),
            "locs": len(locs),
            "sample": locs[:5],
            "is_index": any(loc.endswith((".xml", ".xml.gz")) for loc in locs),
        }
    return out


def harvest_match_ids(sitemap_url: str, *, timeout: int) -> dict[str, Any]:
    """Pull (sport, slug, match id) triples out of a sitemap."""
    out: dict[str, Any] = {"sitemap": sitemap_url}
    try:
        try:
            body = direct_fetch(sitemap_url, timeout=timeout, attempts=1)
        except Exception:
            body = relay_request(RELAY_BASE + sitemap_url, {
                "User-Agent": "EdgeFactory/1.0", "Accept": "text/plain",
                "X-No-Cache": "true", "X-Return-Format": "text"},
                timeout=timeout)
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"[:120]
        return out

    out["bytes"] = len(body)
    found: dict[str, list[tuple[str, str]]] = {}
    for sport, slug, mid in MATCH_SLUG.findall(body):
        key = sport.decode().lower()
        entry = (slug.decode(), mid.decode())
        bucket = found.setdefault(key, [])
        if entry not in bucket and len(bucket) < 4:
            bucket.append(entry)
    out["by_sport"] = {k: v for k, v in list(found.items())[:10]}
    return out


def test_match_json(slug: str, mid: str, *, timeout: int,
                    pause: float = 4) -> dict[str, Any]:
    """Call getjson.php the way the page does.

    The call site reads `L = document.URL.split("/").pop()`, so gdt is the
    WHOLE last path segment — slug and id together. Earlier attempts passed
    the slug with the id stripped off, which is likely why a real match
    returned 14 bytes. Try the faithful form first, then the fallbacks.
    """
    forms = {
        "slug_with_id": f"gdt={slug}-{mid}&mid={mid}",
        "slug_only": f"gdt={slug}&mid={mid}",
        "id_only": f"gdt={mid}&mid={mid}",
        "mid_alone": f"mid={mid}",
    }
    out: dict[str, Any] = {"tried": {}}
    payload = b""
    for i, (name, query) in enumerate(forms.items()):
        if i:
            pace(min(pause, 4))
        url = f"https://www.forebet.com/scripts/getjson.php?{query}"
        try:
            body = relay_request(RELAY_BASE + url, {
                "User-Agent": "EdgeFactory/1.0", "Accept": "text/plain",
                "X-No-Cache": "true"}, timeout=timeout)
        except Exception as exc:
            out["tried"][name] = f"{type(exc).__name__}"[:40]
            continue
        marker = b"Markdown Content:"
        candidate = body.split(marker, 1)[1].strip() if marker in body else body
        out["tried"][name] = f"{len(candidate)}B"
        if len(candidate) > len(payload):
            payload, out["url"], out["form"] = candidate, url, name
    if not payload:
        out["error"] = "no form returned a body"
        return out
    out["bytes"] = len(payload)
    # 14 bytes is not data. Require a plausible JSON body, not merely
    # something that is not literally "[]".
    out["empty"] = (payload[:2] in (b"[]", b"") or len(payload) < 60
                    or payload.lstrip()[:1] not in (b"[", b"{"))
    out["has_date_bah"] = b"DATE_BAH" in payload or b"date_bah" in payload
    out["sample"] = payload[:300].decode("utf-8", "replace")
    return out


# Sports whose boards the 24h forward capture cannot reach, in the order
# worth spending a limited request budget on.
COVERAGE_SPORTS: tuple[str, ...] = (
    # Proven through the column route, plus the two the sweep found ready.
    "basketball", "hockey", "handball", "volleyball",
)


# Sports never probed through the column route. Football is excluded: it has
# a JSON endpoint and does not need this.
UNPROBED_SPORTS: tuple[str, ...] = (
    "american_football", "rugby", "handball", "volleyball", "mma",
    "cricket", "esports", "esoccer", "afl",
)


def coverage_sweep(date: str, *, timeout: int, pause: float,
                   sports: tuple[str, ...] = UNPROBED_SPORTS,
                   order: str | None = None) -> dict[str, Any]:
    """One row-scoped request per sport: does this route reach it at all?

    The scope ':has(.fprc):has(.tnms)' only matches if the board uses the
    same row markup as basketball and hockey. A sport that answers with
    rows is reachable and needs only a full column capture; a sport that
    matches nothing needs its own markup investigated. One request each
    keeps nine sports inside a single job's budget.

    The dates found are as important as the count: a board full of matches
    for the wrong day is the late-publishing problem, not a capture
    failure, and the two must never be confused.
    """
    out: dict[str, Any] = {}
    for sport in sports:
        spec = SPORTS.get(sport)
        if spec is None:
            out[sport] = {"error": "not a known sport"}
            continue
        if time_left() < 45:
            out[sport] = {"error": "skipped: out of time budget"}
            continue
        url = f"https://www.forebet.com/en/{spec.path}/predictions/{date}"
        pace(min(pause, 3))
        try:
            body = relay_request(RELAY_BASE + url, {
                "User-Agent": "EdgeFactory/1.0",
                "Accept": "text/plain",
                "X-No-Cache": "true",
                "X-Timeout": "25",
                "X-Target-Selector": ROW_SCOPE,
            }, timeout=timeout + 20)
        except Exception as exc:  # noqa: BLE001
            code = getattr(exc, "code", "")
            out[sport] = {"error": f"{type(exc).__name__}{code}", "url": url}
            continue
        text = strip_wrapper(body)
        links = re.findall(r"\[([^\]]{3,120})\]\((https?://[^)]+)\)", text)
        labels = [label for label, _href in links]
        order = order or infer_date_order(labels)
        days = Counter()
        either = set()
        for label in labels:
            day = event_day_from_kickoff(label, order) if order else None
            if day:
                days[day] += 1
            # Candidate dates under either reading, for boards that cannot
            # settle their own order.
            for candidate in (MONTH_FIRST, DAY_FIRST):
                both = event_day_from_kickoff(label, candidate)
                if both:
                    either.add(both)
        out[sport] = {
            "bytes": len(body),
            "links": len(links),
            "date_order": order or "AMBIGUOUS",
            "dates_either": sorted(either)[:8],
            "dates": dict(days.most_common(5)),
            "on_target_date": days.get(date, 0),
            "sample": links[0][0][:90] if links else text[:120],
        }
    return out


def horizon_coverage(date: str, *, timeout: int, pause: float,
                     sports: tuple[str, ...],
                     slice_seconds: float = 100.0) -> dict[str, Any]:
    """Prove rankability for sports that publish beyond the target date.

    Rugby, mma and cricket are reachable but had nothing on 2026-09-28:
    their boards were full of 10-01 through 10-04. That is a publication
    horizon, not a capture failure, and the distinction only means
    something if the later date can actually be captured.

    So: one cheap request to learn which dates the board holds, then a
    full capture at the earliest of them that is not in the past. A sport
    that proves rankable at its own horizon needs scheduling, not
    rescue.
    """
    out: dict[str, Any] = {}
    for sport in sports:
        spec = SPORTS.get(sport)
        if spec is None or time_left() < 120:
            out[sport] = {"verdict": "skipped: out of time budget"}
            continue
        sweep = coverage_sweep(date, timeout=timeout, pause=pause,
                               sports=(sport,))
        found = sweep.get(sport, {})
        # A board whose dates all read both ways yields nothing here. The
        # capture resolves that for itself: it anchors on the date it asked
        # for. So consider candidates under both readings and let the
        # capture reject the wrong one.
        dates = sorted({d for d in (found.get("dates_either") or
                                    found.get("dates") or {}) if d >= date})
        if not dates:
            out[sport] = {"verdict": "no future dates on the board",
                          "sweep": found}
            continue
        target = dates[0]
        url = f"https://www.forebet.com/en/{spec.path}/predictions/{target}"
        pace(min(pause, 3))
        # Throttling is the only thing still failing these boards: rugby
        # lost its kickoff column and cricket its home column to 422s while
        # everything else came back clean. The refusals move between runs,
        # so spend the retries here rather than lose the whole board.
        try:
            result = capture_board(
                url, sport, target, captured_at=date + "T00:00:00Z",
                timeout=timeout, attempts=3, backoff=7.0, sleep=pace,
                before_request=slice_guard(slice_seconds))
        except BudgetExhausted as exc:
            out[sport] = {"horizon_date": target,
                          "verdict": f"stopped: {exc}"}
            if "slice" in str(exc):
                continue  # this sport's turn is over, not the stage's
            break
        record: dict[str, Any] = {
            "horizon_date": target,
            "days_ahead": (dt.date.fromisoformat(target)
                           - dt.date.fromisoformat(date)).days,
            "status": result.status,
            "rows": result.row_count,
            "rankable_events": len(result.events),
            "reason": result.reason[:160],
            "partial": result.partial,
        }
        if result.events:
            best = max(result.events, key=lambda e: max(
                e.probability_1 or 0.0, e.probability_2 or 0.0))
            record["top_by_probability"] = {
                "event_id": best.event_id,
                "match": f"{best.participant_1} vs {best.participant_2}",
                "p1": best.probability_1, "p2": best.probability_2,
            }
        out[sport] = record
    return out


def slice_guard(seconds: float):
    """A ``before_request`` that also stops a stage overrunning its share.

    Run 36395609881 spent the whole 660-second budget inside the first
    stage and every later stage reported "out of time budget" — including
    the two that were supposed to be cheap. The job-level budget cannot
    prevent that on its own: it only refuses the request AFTER the clock
    is gone. A stage that is worth two requests gets a slice, and the
    slice is enforced on the same callback the budget uses.
    """
    deadline = time.monotonic() + seconds

    def guard() -> None:
        check_budget()
        if time.monotonic() > deadline:
            raise BudgetExhausted(
                f"stage slice of {seconds:.0f}s spent")

    return guard


def render_clock_probe(date: str, *, timeout: int, pause: float,
                       slice_seconds: float = 150.0,
                       attempts: int = 3) -> dict[str, Any]:
    """Measure the renderer's offset live, the way the nightly stage will.

    This is the measurement that decides whether thirteen sports can ever
    produce a pick: football is the one sport visible through both the
    tz=0 JSON and the renderer, so the gap between them is the offset the
    renderer applies to every other sport's board. The 2026-09-26 red-team
    finding put it at five hours; whether that is stable, and whether it is
    the same for a whole board rather than one match, has never been
    measured.

    Two requests: the football JSON the probe already fetches, and one
    rendered column.
    """
    record: dict[str, Any] = {"target_date": date}
    started = time.monotonic()
    guard = slice_guard(slice_seconds)
    # Both channels have to answer in the SAME run, and each is being
    # served a bot-check page perhaps half the time: run 36400033744 got
    # 139 matches, run 36401440850 got 272 bytes of "Performing security
    # verification". Joint success on single attempts is a coin flip on a
    # coin flip, so each channel retries inside the slice.
    instants: dict[str, str] = {}
    attempts_made = 0
    try:
        for json_try in range(1, max(1, attempts) + 1):
            instants = {
                f"football:{match_id}": moment.strftime("%Y-%m-%d %H:%M:%S")
                for match_id, moment in football_utc_kickoffs(
                    date, timeout=timeout).items()
            }
            if instants:
                break
            guard()
            pace(min(pause, 6))
        attempts_made = json_try
    except BudgetExhausted as exc:
        return {"verdict": f"stopped: {exc}"}
    record["json_attempts"] = attempts_made
    record["json_matches"] = len(instants)
    record["json_seconds"] = round(time.monotonic() - started, 1)
    if not instants:
        record["verdict"] = "no tz=0 instants; nothing to join against"
        # Run 36395609881: the tz=0 endpoint answered something that was
        # not JSON at all. Whether that is a challenge page or a throttle
        # matters more than the empty result, so it is reported here rather
        # than left in a fetch-error list nobody reads.
        if FOOTBALL_JSON_FINGERPRINT:
            record["json_body"] = dict(FOOTBALL_JSON_FINGERPRINT)
        elif FETCH_ERRORS:
            record["json_error"] = str(FETCH_ERRORS[-1])[:200]
        return record
    pace(min(pause, 3))
    url = board_url(SPORTS["football"], date)
    record["board_url"] = url
    try:
        column_started = time.monotonic()
        # One attempt, a short read timeout and a stage slice: the football
        # board is the largest page on the site, and a relay render that
        # drips bytes outlives a per-read timeout however small it is.
        # Refusals move between minutes: the same column 422s on one
        # request and answers on the next. One attempt was too stingy for
        # the one measurement that unblocks every sport, and the slice
        # bounds the cost either way.
        cells = fetch_column(url, scoped(".tnms"),
                             timeout=min(timeout, 40),
                             column="link", attempts=attempts, backoff=6.0,
                             sleep=pace, before_request=guard)
        record["column_seconds"] = round(time.monotonic() - column_started, 1)
    except BudgetExhausted as exc:
        record["verdict"] = f"stopped: {exc}"
        record["seconds"] = round(time.monotonic() - started, 1)
        return record
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        # The type alone is not the finding: a 422 from the renderer and a
        # socket timeout are different problems wearing one exception.
        record["verdict"] = (
            f"rendered column unavailable: {type(exc).__name__}: {exc}"[:200])
        record["seconds"] = round(time.monotonic() - started, 1)
        return record
    rendered: dict[str, str] = {}
    for cell in cells:
        link = match_url(cell)
        if link:
            rendered[f"football:{event_id_from_url(link)}"] = cell
    record["rendered_rows"] = len(rendered)
    record["joined"] = len(set(rendered) & set(instants))
    result = measure_render_clock(
        instants, rendered, target_date=date,
        measured_at=dt.datetime.now(dt.timezone.utc).isoformat())
    if result.proven:
        record.update(
            proven=True,
            offset_minutes=result.clock.offset_minutes,
            offset_hours=round(result.clock.offset_minutes / 60, 2),
            samples=result.clock.samples,
            distinct_hours=result.clock.distinct_hours)
    else:
        record.update(proven=False, reason=result.reason,
                      detail=result.detail[:200],
                      observed_offsets=dict(
                          sorted(result.observed_offsets.items())[:8]))
    record["sample"] = [
        {"event_id": key, "rendered": rendered[key][:60],
         "utc": instants[key]}
        for key in sorted(set(rendered) & set(instants))[:3]
    ]
    record["seconds"] = round(time.monotonic() - started, 1)
    return record


def diagnose_board(url: str, *, timeout: int) -> dict[str, Any]:
    """Is the board refusing our SELECTOR, or refusing US?

    The renderer answers 422 for "matched nothing", which is the same
    answer whether it was throttled, whether the selector is wrong, or
    whether the page it rendered was a bot-check interstitial with no
    board on it at all. Five runs have read 422 as throttling.

    The asymmetry that prompted this: the same ``.tnms`` selector returns
    in 6.5 seconds on the football 1x2 board and 422s on
    ``/en/<sport>/predictions/<date>`` in the same run, on the same relay.
    One coarse request against the whole body says which world we are in.
    """
    try:
        body = relay_get_selector(url, "body", timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return {"error": f"{type(exc).__name__}: {exc}"[:160]}
    report = body_fingerprint(body)
    report["is_challenge"] = looks_like_challenge_page(body or b"")
    report["verdict"] = (
        "the board itself is a bot-check page, so no selector can match"
        if report["is_challenge"] else
        "the board rendered; the selector or the throttle is the problem")
    return report


def relay_get_selector(url: str, selector: str, *, timeout: int) -> bytes:
    """One rendered extract, by selector, with no retry or interpretation."""
    import urllib.request

    request = urllib.request.Request(
        "https://r.jina.ai/" + url,
        headers={
            "User-Agent": "EdgeFactory/1.0",
            "Accept": "text/plain",
            "X-No-Cache": "true",
            "X-Timeout": "25",
            "X-Target-Selector": selector,
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def collector_end_to_end(date: str, *, timeout: int, pause: float,
                         sport: str = "hockey",
                         slice_seconds: float = 240.0,
                         circuit_breaker_columns: int = 0,
                         circuit_breaker_attempts: int = 1) -> dict[str, Any]:
    """Drive the PRODUCTION capture path, not a probe-shaped copy of it.

    Everything proven about the column route so far was proven by calling
    ``capture_board`` directly. The path production actually takes is
    longer than that, and every extra step is somewhere a capture can be
    written that cannot be read back:

        ForebetCollector._fetch  -> html rejected -> capture_board
          -> serialise_columns   -> bytes on disk + receipt
          -> parse_capture       -> EventSnapshots

    So this runs exactly that, into a throwaway root, and reports what
    came out the far end. A capture that parses to zero events here is a
    capture that would produce no picks in the nightly job, however good
    the board looked.
    """
    import tempfile

    from slumdog.capture_loader import load_capture_records
    from slumdog.forebet import ForebetCollector

    record: dict[str, Any] = {"sport": sport, "target_date": date}
    guard = slice_guard(slice_seconds)
    started = time.monotonic()
    try:
        guard()
    except BudgetExhausted as exc:
        return {"verdict": f"skipped: {exc}"}

    with tempfile.TemporaryDirectory(prefix="slumdog-e2e-") as tmp:
        root = Path(tmp)
        receipt = f"capture_probe_{date}.json"
        try:
            collector = ForebetCollector(
                root=root, timeout=timeout, workers=1, before_request=guard,
                circuit_breaker_columns=circuit_breaker_columns,
                circuit_breaker_attempts=circuit_breaker_attempts)
            # serial=True (not a "pause_seconds small but truthy" trick)
            # selects capture_selected's TIMED one-sport-at-a-time path,
            # which is what populates the receipt's capture_timing
            # (elapsed/requests/outcome) this probe needs to report. A
            # single-sport list never actually sleeps on that path (the
            # pause only applies between the 2nd+ sport in one call), so
            # pause=0 here costs nothing.
            collector.capture_selected(date, [sport], force=True,
                                       receipt_name=receipt,
                                       pause_seconds=0, serial=True)
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            record["verdict"] = f"capture failed: {type(exc).__name__}: {exc}"[:240]
            # One coarse request to separate "the renderer refused us" from
            # "the page it rendered had no board on it".
            try:
                record["board"] = diagnose_board(
                    board_url(SPORTS[sport], date), timeout=min(timeout, 30))
            except Exception:  # noqa: BLE001
                pass
            record["seconds"] = round(time.monotonic() - started, 1)
            return record

        receipt_path = root / "data" / "reports" / receipt
        try:
            payload = json.loads(receipt_path.read_text())
        except Exception as exc:  # noqa: BLE001
            record["verdict"] = f"no receipt: {type(exc).__name__}"
            return record
        captured = payload.get("captured") or []
        record["captured"] = len(captured)
        record["failures"] = (payload.get("failures") or [])[:2]
        # Priority 1 (2026-09-28): the per-sport-date timing this stage's
        # single sport went through, straight from the production receipt
        # (elapsed_seconds/requests/outcome) — the same field the forward
        # pass writes, so this is a live measurement of the actual code
        # path, not a re-derivation of it.
        record["capture_timing"] = payload.get("capture_timing") or []
        # 403 and 422 are different animals wearing one failure string:
        # 422 is "your selector matched nothing", 403 is "you are being
        # refused". Counting them per run is how the difference between a
        # wrong selector and a rate limit became visible at all.
        codes = Counter(re.findall(
            r"HTTP (\d{3})", " ".join(payload.get("failures") or [])))
        if codes:
            record["column_http"] = dict(codes.most_common())
        if captured:
            first = captured[0]
            record["route"] = first.get("route")
            record["body_format"] = first.get("body_format")
            record["bytes"] = first.get("bytes")
            body_path = root / str(first.get("body_path") or "")
            record["body_on_disk"] = body_path.is_file()

        # The half that has never been exercised: reading back what was
        # written, through the same loader the evaluator uses.
        try:
            loaded = load_capture_records(
                target_date=date, capture_receipt_path=receipt_path,
                repo_root=root)
            records = list(loaded.records)
            record["parsed_events"] = len(records)
            # When a capture is written and yields nothing, the accounting
            # says whether the body failed to parse, the snapshots were
            # rejected, or the parser simply emitted none. Reading that out
            # of a zero took two runs the first time.
            record["capture_accounting"] = {
                k: v for k, v in
                (getattr(loaded, "capture_accounting", None) or {}).items()
                if v}
            record["snapshot_accounting"] = {
                k: v for k, v in
                (getattr(loaded, "snapshot_accounting", None) or {}).items()
                if v}
            if records:
                best = max(records, key=lambda r: max(
                    r.probability_1 or 0.0, r.probability_2 or 0.0))
                record["top_by_probability"] = {
                    "event_id": best.event_id,
                    "match": f"{best.participant_1} vs {best.participant_2}",
                    "p1": best.probability_1, "p2": best.probability_2,
                    "kickoff": str(best.kickoff)[:24],
                }
            record["verdict"] = (
                "capture -> disk -> parse produced events"
                if records else
                "capture written but parsed to zero events")
        except Exception as exc:  # noqa: BLE001
            record["verdict"] = (
                f"capture unreadable: {type(exc).__name__}: {exc}"[:240])
    record["seconds"] = round(time.monotonic() - started, 1)
    return record


def circuit_breaker_comparison(near: dict[str, Any],
                               far: dict[str, Any]) -> dict[str, Any]:
    """Read the breaker's two live questions straight off two already-run
    ``collector_end_to_end`` records — not inferred, not assumed:

    1. On a board that almost certainly DOES exist (``near``), did turning
       the breaker on cost a FALSE ABORT — the exact failure mode Section 1
       of the owner's correction was about?
    2. On a board that almost certainly does NOT exist yet (``far``,
       mirroring the forward pass's D+2..D+6 reach), did the breaker abort
       cheaply, or did the live refusal not look like the clean HTTP 422
       the breaker is scoped to trip on?

    Pure and I/O-free by design: this never makes a request itself, so it
    can be called on the SAME near-board record ``run_probe`` already
    produces for the plain ``collector_end_to_end`` stage instead of paying
    for a second capture of a board this probe already fetched.
    """
    near_timing = (near.get("capture_timing") or [{}])[0]
    far_timing = (far.get("capture_timing") or [{}])[0]
    return {
        "near_requests": near_timing.get("requests"),
        "near_outcome": near_timing.get("outcome"),
        "near_false_abort": bool(
            near_timing.get("outcome", "").startswith("COVERAGE_GAP")
            and "circuit breaker" in "; ".join(near.get("failures") or [])),
        "far_requests": far_timing.get("requests"),
        "far_outcome": far_timing.get("outcome"),
        "far_breaker_tripped": "circuit breaker" in "; ".join(
            far.get("failures") or []),
        # A theoretical ceiling, not a live measurement: production never
        # actually runs with the breaker off (circuit_breaker_columns=0),
        # so there is no "off" request count to compare against directly.
        # len(selectors) * default attempts is the worst case every column
        # fails once and is retried to exhaustion; a real successful
        # capture (see collector_end_to_end's own "requests", i.e.
        # near_requests above) is typically far cheaper than this ceiling
        # even with the breaker off, because most columns succeed first
        # try. This is context for the far side's savings, not a baseline
        # to expect near_requests to have hit.
        "no_breaker_worst_case_requests": len(COLUMN_SELECTORS) * 3,
    }


def circuit_breaker_measurement(date: str, *, timeout: int, pause: float,
                                sport: str = "volleyball",
                                far_offset_days: int = 5,
                                slice_seconds: float = 240.0
                                ) -> dict[str, Any]:
    """Measure the circuit breaker (Priority 1, scoped 2026-09-28) live,
    against the real relay, instead of re-dispatching the 350-minute
    Forward Shadow job to find out.

    Unit tests already prove the retry arithmetic deterministically against
    a fake opener (tests/test_relay_columns.py::TestCircuitBreaker); what
    they cannot prove is whether the real source's live refusal behaviour
    matches the assumptions the breaker is built on. See
    ``circuit_breaker_comparison`` for the two questions this answers.

    This is the STANDALONE, two-fresh-capture form (both near and far
    captured here), kept as the ``--circuit-breaker-probe`` CLI override
    for a focused, on-demand re-check. The default probe sweep in
    ``run_probe`` does NOT call this function — it gets the near half for
    free from the plain ``collector_end_to_end`` stage (also run with the
    breaker on) and only pays for one fresh far capture, then calls
    ``circuit_breaker_comparison`` directly on both. Calling this function
    from the sweep too would capture the near board twice for the same
    answer, which is exactly the request cost Priority 1 is trying to cut.

    Both go through ``collector_end_to_end``, i.e. the real production
    path (``ForebetCollector.capture_selected`` -> ``capture_board`` ->
    ``fetch_board_columns``), with ``circuit_breaker_columns=2`` explicitly
    opted in — exactly as ``forward_shadow_batch.run_capture`` does — so
    this is a measurement of the shipped code, not a probe-shaped copy of
    it. Each half's ``capture_timing`` (elapsed/requests/outcome) comes
    straight from the production receipt.
    """
    far_date = (dt.date.fromisoformat(date)
               + dt.timedelta(days=far_offset_days)).isoformat()
    out: dict[str, Any] = {"near_date": date, "far_date": far_date,
                           "sport": sport}
    half_budget = max(60.0, slice_seconds / 2)

    if time_left() < 90:
        out["verdict"] = "skipped: out of time budget"
        return out

    out["near"] = collector_end_to_end(
        date, timeout=timeout, pause=pause, sport=sport,
        slice_seconds=min(half_budget, time_left() - 30),
        circuit_breaker_columns=2, circuit_breaker_attempts=1)
    pace(min(pause, 5))

    if time_left() < 60:
        out["far"] = {"verdict": "skipped: out of time budget"}
    else:
        out["far"] = collector_end_to_end(
            far_date, timeout=timeout, pause=pause, sport=sport,
            slice_seconds=min(half_budget, time_left() - 20),
            circuit_breaker_columns=2, circuit_breaker_attempts=1)

    out["comparison"] = circuit_breaker_comparison(out["near"], out["far"])
    return out


def settlement_probe(date: str, *, timeout: int, pause: float,
                     sports: tuple[str, ...] = ("hockey",),
                     settled_date: str | None = None,
                     slice_seconds: float = 150.0,
                     attempts: int = 3) -> dict[str, Any]:
    """Does a captured pick actually settle the next day, by the same id?

    Coverage without settlement is half a system: a rank-1 pick that can
    never be graded teaches nothing. Yesterday's board carries the result
    and the status that says the result is final, so this captures it
    through the same column route and reports how many rows grade — and
    for mma, how the fights were decided, since a KO, a submission and a
    draw are three different outcomes and only one of them is nobody
    winning.
    """
    out: dict[str, Any] = {}
    # The day to settle is yesterday in real time, not the day before the
    # capture target. Run 36385309872 asked for 2026-09-28 while it was
    # still 2026-09-28: the board answered with 34 fixtures and no scores
    # at all, because none of them had been played. A settlement probe must
    # look at a day that is over.
    yesterday = settled_date or (
        dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
    ).isoformat()
    for sport in sports:
        spec = SPORTS.get(sport)
        if spec is None or time_left() < 120:
            out[sport] = {"verdict": "skipped: out of time budget"}
            continue
        url = f"https://www.forebet.com/en/{spec.path}/predictions/{yesterday}"
        pace(min(pause, 3))
        try:
            result = capture_board(
                url, sport, yesterday, captured_at=date + "T00:00:00Z",
                timeout=timeout, attempts=attempts, backoff=7.0, sleep=pace,
                selectors=SETTLEMENT_COLUMN_SELECTORS,
                required=SETTLEMENT_REQUIRED_COLUMNS,
                scope=SETTLEMENT_ROW_SCOPE,
                before_request=slice_guard(slice_seconds))
        except BudgetExhausted as exc:
            out[sport] = {"settled_date": yesterday, "url": url,
                          "verdict": f"stopped: {exc}"}
            break
        record: dict[str, Any] = {
            "settled_date": yesterday,
            "url": url,
            "status": result.status,
            "rows": result.row_count,
            "reason": result.reason[:160],
            "partial": result.partial,
        }
        if result.status != CAPTURED:
            # A settlement gap is worth one diagnostic request: whether the
            # board refused us, or rendered and simply held other days.
            record["observed_dates"] = list(result.observed_dates)
            if result.status == COVERAGE_GAP:
                record["board"] = diagnose_board(url, timeout=min(timeout, 30))
        board = result.board
        if board is not None:
            statuses = Counter(
                (value or "").strip().upper()[:12]
                for value in board.columns.get("status", []))
            record["statuses_seen"] = dict(statuses.most_common(6))
            record["scores_sample"] = board.columns.get("score", [])[:3]
            # The row text the day was read from, so a date mismatch is
            # debuggable from the annotation instead of by inference.
            record["link_sample"] = [
                cell[:70] for cell in board.columns.get("link", [])[:3]]
            try:
                graded = settled_rows(board)
            except Exception as exc:  # unreadable date order, etc.
                record["graded_error"] = f"{type(exc).__name__}: {exc}"[:160]
                graded = []
            record["graded"] = len(graded)
            record["sample"] = [
                {"event_id": row["event_id"],
                 "match": f"{row['participant_1']} vs {row['participant_2']}",
                 "score": f"{row['score_1']:g}-{row['score_2']:g}",
                 "winner_index": row["winner_index"]}
                for row in graded[:3]
            ]
        out[sport] = record
    return out


def r1_coverage(date: str, *, timeout: int, pause: float,
                sports: tuple[str, ...] = COVERAGE_SPORTS,
                slice_seconds: float = 45.0) -> dict[str, Any]:
    """Can each sport produce a rankable field for this date?

    This calls the production capture path rather than a probe-local copy
    of it. The earlier version re-implemented fetching and therefore missed
    the retry that production has, which made throttling look like a
    property of the site instead of a property of the probe. What is
    measured here is now exactly what a capture would do.
    """
    out: dict[str, Any] = {}
    for sport in sports:
        spec = SPORTS.get(sport)
        if spec is None:
            continue
        if time_left() < 90:
            out[sport] = {"verdict": "skipped: out of time budget"}
            continue
        url = f"https://www.forebet.com/en/{spec.path}/predictions/{date}"
        pace(min(pause, 3))
        try:
            result = capture_board(
                url, sport, date, captured_at=date + "T00:00:00Z",
                timeout=timeout, attempts=2, backoff=6.0, sleep=pace,
                before_request=slice_guard(slice_seconds))
        except BudgetExhausted as exc:
            out[sport] = {"verdict": f"stopped: {exc}"}
            if "slice" in str(exc):
                continue
            break
        record: dict[str, Any] = {
            "status": result.status,
            "rows": result.row_count,
            "rankable_events": len(result.events),
            "observed_dates": list(result.observed_dates),
            "reason": result.reason[:200],
            "suspect_short": result.suspect_short,
        }
        if result.events:
            best = max(result.events, key=lambda e: max(
                e.probability_1 or 0.0, e.probability_2 or 0.0))
            record["top_by_probability"] = {
                "event_id": best.event_id,
                "match": f"{best.participant_1} vs {best.participant_2}",
                "p1": best.probability_1, "p2": best.probability_2,
                "kickoff": best.kickoff,
            }
        out[sport] = record
    return out


def column_extracts(date: str, sport_path: str, *, timeout: int,
                     pause: float) -> dict[str, Any]:
    """Pull each column separately and check the rows line up.

    A .tnms row reads "Abejas Santos 09/27/2026 2:00 AM", which cannot be
    split reliably into home and away. But the page has a class per field,
    and selector-scoped extraction returns one per line. Fetch the columns
    independently and zip them by index — provided every column returns the
    same number of lines, which is exactly what this measures.
    """
    url = f"https://www.forebet.com/en/{sport_path}/predictions/{date}"
    base = {"User-Agent": "EdgeFactory/1.0", "Accept": "text/plain",
            "X-No-Cache": "true", "X-Timeout": "25"}
    columns = {
        "home": ".homeTeam",
        "away": ".awayTeam",
        "kickoff": ".date_bah",
        "probabilities": ".fprc",
        "pick": ".forepr",
        "predicted_score": ".ex_sc",
        "average": ".avg_sc",
    }
    out: dict[str, Any] = {}
    for i, (name, selector) in enumerate(columns.items()):
        if i:
            pace(min(pause, 6))
        try:
            body = relay_request(RELAY_BASE + url, {
                **base, "X-Target-Selector": selector}, timeout=timeout + 20)
        except Exception as exc:
            out[name] = {"selector": selector,
                         "error": f"{type(exc).__name__}: {exc}"[:90]}
            continue
        text = body.decode("utf-8", "replace")
        marker = "Markdown Content:"
        payload = text.split(marker, 1)[1].strip() if marker in text else text
        rows = [ln.strip() for ln in payload.splitlines() if ln.strip()]
        out[name] = {
            "selector": selector,
            "bytes": len(body),
            "rows": len(rows),
            "first": rows[:4],
            "last": rows[-2:],
        }
    counts = {n: fp.get("rows") for n, fp in out.items() if fp.get("rows")}
    out["_aligned"] = len(set(counts.values())) == 1 if counts else False
    out["_counts"] = counts
    return out


def selector_html_modes(date: str, sport_path: str, *, timeout: int,
                        pause: float) -> dict[str, Any]:
    """Can the renderer hand back the matched subtree as HTML?

    Selector-scoped extraction already returns the board as text. If the
    same request can return the rendered DOM subtree as markup, the existing
    parser consumes it unchanged — no new Markdown parser, no row-order
    join, and .homeTeam/.awayTeam/.date_bah come through as the fields the
    pipeline already expects. The html mode alone returns the interstitial
    because it fetches without rendering; combined with a selector it may
    take the rendered path instead.
    """
    url = f"https://www.forebet.com/en/{sport_path}/predictions/{date}"
    base = {"User-Agent": "EdgeFactory/1.0", "Accept": "text/plain",
            "X-No-Cache": "true", "X-Timeout": "25"}
    attempts = {
        "selector_plus_return_html": {"X-Target-Selector": "div.rcnt",
                                      "X-Return-Format": "html"},
        "selector_plus_respond_html": {"X-Target-Selector": "div.rcnt",
                                       "X-Respond-With": "html"},
        "page_return_html_rendered": {"X-Return-Format": "html",
                                      "X-Wait-For-Selector": "div.rcnt"},
    }
    out: dict[str, Any] = {}
    for i, (name, extra) in enumerate(attempts.items()):
        if i:
            pace(min(pause, 6))
        try:
            body = relay_request(RELAY_BASE + url, {**base, **extra},
                                 timeout=timeout + 20)
        except Exception as exc:
            out[name] = {"error": f"{type(exc).__name__}: {exc}"[:100]}
            continue
        lowered = body.lower()
        out[name] = {
            "bytes": len(body),
            "challenge": looks_like_challenge(body),
            "rcnt": lowered.count(b'class="rcnt'),
            "home_team": lowered.count(b"hometeam"),
            "date_bah": lowered.count(b"date_bah"),
            "sample": body[:260].decode("utf-8", "replace"),
        }
    return out


def live_dom_selectors(date: str, sport_path: str, *,
                       timeout: int, pause: float) -> dict[str, Any]:
    """Ask the renderer which selectors exist on the live board.

    A target-selector request answers 422 when the selector matches
    nothing, so this reads the live DOM's shape without ever seeing it.
    `.rcnt` — what our parser is built on and what the 2024 archive used —
    returned 422, which would mean the board has been rebuilt.
    """
    url = f"https://www.forebet.com/en/{sport_path}/predictions/{date}"
    out: dict[str, Any] = {}
    # .tnms is the team-name block: the archived markup carries
    # <div class="tnms"><meta itemprop="name" content="A vs B" /> inside it.
    # The row container (.rcnt) came back without names, so ask for the name
    # element itself rather than the row that should contain it.
    for i, selector in enumerate(
            (".tnms", "div.tnms", ".rcnt .tnms", "[itemprop=name]",
             ".homeTeam", ".rcnt")):
        if i:
            pace(min(pause, 4))
        try:
            body = relay_request(RELAY_BASE + url, {
                "User-Agent": "EdgeFactory/1.0", "Accept": "text/plain",
                "X-No-Cache": "true", "X-Target-Selector": selector},
                timeout=timeout)
            text = body.decode("utf-8", "replace")
            marker = "Markdown Content:"
            payload = text.split(marker, 1)[1].strip() if marker in text \
                else text
            encoded = payload.encode()
            out[selector] = {
                "bytes": len(body),
                "found": True,
                "names": len(NAME_TOKEN.findall(encoded)),
                # A capture needs one row per match: names, a kickoff and a
                # link, in the same count as the numeric rows.
                "links": len(dict.fromkeys(MATCH_LINK.findall(encoded))),
                "clocks": len(CLOCK.findall(encoded)),
                "dates": len(re.findall(rb"\d{2}/\d{2}/\d{4}", encoded)),
                "sample": payload[:1500],
            }
        except Exception as exc:
            code = getattr(exc, "code", None)
            out[selector] = {"found": False if code == 422 else None,
                             "error": f"{type(exc).__name__}:{code}"[:40]}
    return out


def api_endpoint_sweep(date: str, *, timeout: int,
                       pause: float) -> dict[str, Any]:
    """Test the sibling API endpoints mined from the bundle, direct and relayed.

    The bundle referenced getjson.php?gdt=, getjson_y.php?lg=, getjson_t.php,
    getftr.php?int=, get_live_r.php and get_menu.php, and none of them were
    ever tried — the earlier sweep only varied getrs.php's tp value. These
    matter because the bot check is on the site, not on its APIs: getrs.php
    answers fine, which is the sole reason football still captures. If any of
    these serves other-sport rows, the blocked sports are capturable again.
    """
    base = "https://www.forebet.com/scripts/"
    targets = {
        "getjson_gdt": f"{base}getjson.php?gdt={date}",
        "getjson_gdt_ln": f"{base}getjson.php?ln=en&gdt={date}",
        "getftr_int": f"{base}getftr.php?int=1&ln=en",
        "get_live_r": f"{base}get_live_r.php?ln=en",
        "get_menu": f"{base}get_menu.php?ln=en",
        "getrs_control": (f"{base}getrs.php?ln=en&tp=1x2&in={date}"
                          "&ord=0&tz=0&tzs=&tze="),
    }
    out: dict[str, Any] = {}
    for i, (name, url) in enumerate(targets.items()):
        if i:
            pace(min(pause, 5))
        entry: dict[str, Any] = {"url": url}
        # Direct first: if the APIs are not behind the check, this is the
        # cheapest possible capture route — no relay, no rate limit.
        try:
            body = direct_fetch(url, timeout=timeout, attempts=1)
            entry["direct"] = _api_fingerprint(body)
        except Exception as exc:
            entry["direct"] = {"error": f"{type(exc).__name__}: {exc}"[:90]}
        if not (entry["direct"].get("json_like") if
                isinstance(entry["direct"], dict) else False):
            pace(min(pause, 5))
            try:
                body = relay_request(RELAY_BASE + url, {
                    "User-Agent": "EdgeFactory/1.0", "Accept": "text/plain",
                    "X-No-Cache": "true"}, timeout=timeout)
                entry["relayed"] = _api_fingerprint(body)
            except Exception as exc:
                entry["relayed"] = {"error": f"{type(exc).__name__}: {exc}"[:90]}
        out[name] = entry
    return out


def _api_fingerprint(body: bytes) -> dict[str, Any]:
    stripped = body.lstrip()
    payload_at = body.lower().find(b"[[{")
    json_like = stripped[:1] in (b"[", b"{") or payload_at != -1
    return {
        "bytes": len(body),
        "json_like": json_like,
        "challenge": looks_like_challenge(body),
        "sample": body[max(0, payload_at): max(0, payload_at) + 260].decode(
            "utf-8", "replace"),
    }


def looks_like_challenge(body: bytes) -> bool:
    lowered = body.lower()
    return b"just a moment" in lowered or b"cf_chl" in lowered


def save_page_now(page_url: str, *, timeout: int) -> dict[str, Any]:
    """Ask the archive to fetch the live page, then read what it captured.

    Archive.org crawls from its own infrastructure, and the runner can
    already read archived snapshots — so if their crawler is allowed
    through, this is a rendering proxy that costs nothing.
    """
    out: dict[str, Any] = {"page": page_url}
    try:
        direct_fetch("https://web.archive.org/save/" + page_url,
                     timeout=timeout, attempts=1)
        out["save"] = "requested"
    except Exception as exc:
        out["save"] = f"{type(exc).__name__}: {exc}"[:110]

    # Whether or not the save call returned cleanly, ask the index what the
    # newest snapshot now is.
    try:
        snaps = cdx_snapshots(page_url.split("://", 1)[-1], limit=40,
                              timeout=timeout)
        out["snapshots"] = len(snaps)
        if snaps:
            timestamp, original = snaps[-1]
            out["newest"] = timestamp
            body = archived_bytes(timestamp, original, timeout=timeout)
            out["bytes"] = len(body)
            out["rows"] = body.lower().count(b'class="rcnt')
            out["challenge"] = looks_like_challenge(body)
    except Exception as exc:
        out["read_error"] = f"{type(exc).__name__}: {exc}"[:110]
    return out


def install_playwright(*, runner: Any = None) -> str:
    """Install Playwright, Chromium and a virtual display.

    The first browser attempt ran headless and was served the interstitial
    even on the homepage. Headless Chrome is the most heavily fingerprinted
    signal there is, and the relay's own renderer clears the same check from
    a datacenter address — so the block is unlikely to be purely about the
    IP. Run a real headed browser on a virtual display instead.
    """
    import subprocess
    import sys

    run = runner or subprocess.run
    for args in (
        ["sudo", "apt-get", "install", "-y", "-qq", "xvfb"],
        [sys.executable, "-m", "pip", "install", "--quiet", "playwright"],
        [sys.executable, "-m", "playwright", "install", "chromium"],
    ):
        done = run(args, capture_output=True, timeout=420)
        if getattr(done, "returncode", 1) != 0:
            tail = (getattr(done, "stderr", b"") or b"")[-160:]
            return f"install failed: {args[-1]}: {tail.decode('utf-8', 'replace')}"
    return "ok"


def playwright_fetch(url: str, *, wait_ms: int = 45000,
                     launcher: Any = None,
                     headless: bool = True) -> dict[str, Any]:
    """Load the board in a real browser and return what the DOM holds.

    Every other route is exhausted: direct fetches are refused outright, an
    unrelated proxy's IP was challenged too, the relay's html modes return
    the interstitial, and its Markdown engine drops team names and clocks.
    A genuine browser is the one thing that can satisfy the bot check the
    way a visitor's would.
    """
    if launcher is None:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415

        launcher = sync_playwright

    out: dict[str, Any] = {}
    with launcher() as play:
        browser = play.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled",
                  "--no-sandbox", "--start-maximized",
                  "--disable-dev-shm-usage"])
        page = browser.new_page(
            user_agent=BROWSER_UA, locale="en-GB",
            timezone_id="Europe/London",
            viewport={"width": 1280, "height": 900})
        # navigator.webdriver is the single clearest automation tell.
        try:
            page.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
                "window.chrome={runtime:{}};"
                "Object.defineProperty(navigator,'languages',"
                "{get:()=>['en-GB','en']});"
                "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3]});")
        except Exception:  # noqa: BLE001 - a stub launcher may not support it
            pass
        try:
            page.goto("https://www.forebet.com/en/", wait_until="load",
                      timeout=wait_ms)
            page.wait_for_timeout(12000)
            out["warmup"] = (page.title() or "")[:80]
        except Exception as exc:
            out["warmup"] = f"{type(exc).__name__}"[:40]

        page.goto(url, wait_until="domcontentloaded", timeout=wait_ms)
        page.wait_for_timeout(8000)
        try:
            # The interstitial resolves itself and then the board renders.
            page.wait_for_selector("div.rcnt", timeout=wait_ms)
        except Exception as exc:
            out["wait_error"] = f"{type(exc).__name__}"[:60]
        html = page.content().encode("utf-8", "replace")
        out.update(body_fingerprint(html, sample=200))
        out["rows"] = html.lower().count(b'class="rcnt')
        out["title"] = (page.title() or "")[:120]
        rows = page.query_selector_all("div.rcnt")
        if rows:
            out["first_row_text"] = " ".join(
                (rows[0].inner_text() or "").split())[:240]
            out["first_row_html"] = (rows[0].inner_html() or "")[:400]
        browser.close()
    return out


def start_virtual_display() -> str:
    """Start Xvfb so Chromium can run headed, and point DISPLAY at it."""
    import subprocess

    try:
        subprocess.Popen(
            ["Xvfb", ":99", "-screen", "0", "1280x1024x24", "-nolisten", "tcp"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3)
        os.environ["DISPLAY"] = ":99"
        return ":99"
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"[:80]


def browser_probe(date: str, sport_path: str) -> dict[str, Any]:
    url = f"https://www.forebet.com/en/{sport_path}/predictions/{date}"
    out: dict[str, Any] = {"url": url}
    try:
        out["install"] = install_playwright()
        if out["install"] != "ok":
            return out
        out["display"] = start_virtual_display()
        out.update(playwright_fetch(url, headless=out["display"] != ":99"))
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"[:220]
    return out


def _table_slice(body: bytes) -> str:
    """The listing itself, from the board heading onward.

    Sampling around the first clock kept landing in the navigation and the
    table header, which says nothing about whether a ROW names its teams.
    """
    lowered = body.lower()
    # "Predictions for" also appears in the nav ("Predictions for TODAY"),
    # so anchor on the table's own header first.
    for marker in (b"home team", b"prob. %", b"correct score"):
        idx = lowered.find(marker)
        if idx != -1:
            return body[idx: idx + 1400].decode("utf-8", "replace")
    # A selector-scoped extract IS the listing, so show it from the top.
    return body[:1400].decode("utf-8", "replace")


def _clock_context(body: bytes) -> str:
    """Text around the first kickoff time, to see what a row actually says."""
    match = CLOCK.search(body)
    if not match:
        return ""
    start = max(0, match.start() - 220)
    return body[start: match.end() + 160].decode("utf-8", "replace")


NAME_TOKEN = re.compile(rb"[A-Z][a-z]{3,}(?:\s+[A-Z][a-z]{2,})?")


def recent_markup_check(sport_path: str, *, timeout: int,
                        pause: float) -> dict[str, Any]:
    """Read the NEWEST archived copy of the board and of its scripts.

    Everything so far was read from the oldest snapshots the index holds,
    because of the limit bug above. The newest copy answers the question
    that matters: does today's markup still carry team names, and which
    scripts does today's page load?
    """
    out: dict[str, Any] = {}
    page = f"https://www.forebet.com/en/{sport_path}/predictions"
    try:
        snaps = cdx_snapshots(page.split("://", 1)[-1], limit=12,
                              timeout=timeout)
        out["snapshot_count"] = len(snaps)
        out["newest_timestamps"] = [s[0] for s in snaps[-4:]]
        if not snaps:
            out["error"] = "no snapshots"
            return out
        timestamp, original = snaps[-1]
        out["using"] = f"{timestamp} {original}"
        body = archived_bytes(timestamp, original, timeout=timeout)
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"[:140]
        return out

    lowered = body.lower()
    out["bytes"] = len(body)
    out["rows"] = lowered.count(b'class="rcnt')
    out["challenge"] = looks_like_challenge(body)
    out["has_itemprop_name"] = b'itemprop="name"' in lowered
    out["has_date_bah"] = b"date_bah" in lowered
    idx = lowered.find(b'itemprop="name"')
    if idx != -1:
        out["name_markup"] = body[max(0, idx - 200): idx + 260].decode(
            "utf-8", "replace")
    out["script_srcs"] = [s.decode("utf-8", "replace")
                          for s in SCRIPT_SRC.findall(body)][:8]

    # Follow today's scripts and mine them for endpoints.
    from urllib.parse import urljoin

    mined: list[str] = []
    for src in out["script_srcs"]:
        if len(mined) >= 8:
            break
        absolute = normalise_ref(urljoin(page, src.replace("id_/", "/")))
        path = absolute.split("?", 1)[0]
        if "forebet.com" not in absolute or not path.endswith(".js"):
            continue
        pace(min(pause, 4))
        try:
            js = archived_bytes(timestamp, absolute, timeout=timeout,
                                kind="js_")
        except Exception as exc:
            mined.append(f"! {path.rsplit('/', 1)[-1]}: {type(exc).__name__}")
            continue
        out.setdefault("js_bytes", {})[path.rsplit("/", 1)[-1]] = len(js)
        for ref in endpoint_candidates(js, page):
            if ref not in mined:
                mined.append(ref)
    out["endpoints_today"] = mined[:12]
    return out


def current_bundle_scan(*, timeout: int, pause: float) -> dict[str, Any]:
    """Read TODAY's script bundle, not a 2020 archived copy.

    Everything known about the endpoints came from a bundle snapshotted in
    November 2020. Meanwhile the board itself has changed: the 2024 archive
    has team names in the markup as <span itemprop="name">, and the live
    page renders none, even after a hydration wait. So the names now arrive
    some other way, and only the current bundle can say how. Static assets
    are plain files and may not sit behind the check at all.
    """
    out: dict[str, Any] = {}
    urls = [
        "https://www.forebet.com/includes/js/all.js",
        "https://www.forebet.com/includes/js/all.js?v=378",
    ]
    body = b""
    for i, url in enumerate(urls):
        if i:
            pace(min(pause, 5))
        for mode in ("direct", "relay"):
            try:
                if mode == "direct":
                    body = direct_fetch(url, timeout=timeout, attempts=1)
                else:
                    body = relay_request(RELAY_BASE + url, {
                        "User-Agent": "EdgeFactory/1.0",
                        "Accept": "text/plain", "X-No-Cache": "true",
                        "X-Return-Format": "text"}, timeout=timeout)
                out["source"] = f"{mode}: {url}"
                out["bytes"] = len(body)
                break
            except Exception as exc:
                out.setdefault("errors", []).append(
                    f"{mode} {url.rsplit('/', 1)[-1][:14]}: "
                    f"{type(exc).__name__}"[:60])
                body = b""
        if body:
            break

    if not body:
        return out

    refs = []
    for match in PHP_REF.findall(body):
        ref = normalise_ref(match.decode("utf-8", "replace").strip())
        if "forebet" in ref or ref.startswith("/"):
            if ref not in refs:
                refs.append(ref)
    out["php_refs"] = refs[:14]

    # How does a row get built? Find where markup is written for a listing.
    for needle in (b"rcnt", b"itemprop", b"innerHTML", b"getrs", b"tp="):
        idx = body.find(needle)
        if idx != -1:
            out.setdefault("contexts", {})[needle.decode()] = body[
                max(0, idx - 200): idx + 260].decode("utf-8", "replace")
    return out


def render_wait_modes(date: str, sport_path: str, *, timeout: int,
                      pause: float) -> dict[str, Any]:
    """Give the renderer time to hydrate before it snapshots.

    The listing's header carries "Home team / Away team" but the cells come
    back holding only a league code and numbers. The columns exist, so the
    names are most likely written in by JavaScript after first paint and the
    snapshot is simply too early. Ask the renderer to wait — and try the
    mobile host, whose layout may put the names in the markup.
    """
    www = f"https://www.forebet.com/en/{sport_path}/predictions/{date}"
    mob = f"https://m.forebet.com/en/{sport_path}/predictions/{date}"
    base = {"User-Agent": "EdgeFactory/1.0", "Accept": "text/plain",
            "X-No-Cache": "true"}
    attempts = {
        "wait_timeout_25": (www, {"X-Timeout": "25"}),
        "wait_for_selector": (www, {"X-Timeout": "25",
                                    "X-Wait-For-Selector": "div.rcnt a"}),
        "selector_after_wait": (www, {"X-Timeout": "25",
                                      "X-Target-Selector": "div.rcnt"}),
        "mobile_host": (mob, {"X-Timeout": "20"}),
    }
    out: dict[str, Any] = {}
    for i, (name, (url, extra)) in enumerate(attempts.items()):
        if i:
            pace(min(pause, 8))
        try:
            body = relay_request(RELAY_BASE + url, {**base, **extra},
                                 timeout=timeout + 30)
        except Exception as exc:
            out[name] = {"error": f"{type(exc).__name__}: {exc}"[:110]}
            continue
        listing = _table_slice(body)
        pairs = list(dict.fromkeys(MATCH_LINK.findall(body)))
        # Words that look like names, counted inside the listing only.
        names = [m.decode("utf-8", "replace")
                 for m in NAME_TOKEN.findall(listing.encode())]
        out[name] = {
            "bytes": len(body),
            "match_links": len(pairs),
            "name_tokens": len(names),
            "name_sample": names[:6],
            "listing": listing[:500],
        }
    return out


def markdown_modes(date: str, sport_path: str, *, timeout: int,
                   pause: float) -> dict[str, Any]:
    """The Markdown engine is the only route that clears the bot check, so
    the question is no longer "can we fetch" but "does what it returns carry
    a match identity and a kickoff time".

    The plain Markdown of a basketball board has the numbers but no team
    names and no clock. These variants ask the same engine for the same page
    in forms that might keep them — notably a link summary, since every row
    links to a match page whose URL carries both teams and the match id.
    """
    url = f"https://www.forebet.com/en/{sport_path}/predictions/{date}"
    base = {"User-Agent": "EdgeFactory/1.0", "Accept": "text/plain",
            "X-No-Cache": "true"}
    variants = {
        "plain": {},
        "text_format": {"X-Return-Format": "text"},
        "links_summary": {"X-With-Links-Summary": "true"},
        "target_selector": {"X-Target-Selector": ".rcnt"},
    }
    out: dict[str, Any] = {}
    for i, (name, extra) in enumerate(variants.items()):
        if i:
            pace(min(pause, 8))
        try:
            body = relay_request(RELAY_BASE + url, {**base, **extra},
                                 timeout=timeout)
        except Exception as exc:
            out[name] = {"error": f"{type(exc).__name__}: {exc}"[:110]}
            continue
        pairs = list(dict.fromkeys(MATCH_LINK.findall(body)))
        links = [f"{s.decode()}|{slug.decode()}|{mid.decode()}"
                 for s, slug, mid in pairs]
        clocks = CLOCK.findall(body)
        out[name] = {
            "bytes": len(body),
            "match_links": len(links),
            "clocks": len(clocks),
            "link_sample": links[:4],
            # A row's text is what a parser would have to read.
            "row_context": _clock_context(body),
            "table_slice": _table_slice(body),
            "sample": body[400:900].decode("utf-8", "replace"),
        }
    return out


def fetch_matrix(date: str, sport_path: str, *, timeout: int,
                 pause: float) -> dict[str, Any]:
    """Try every host and fetcher we know of against one board.

    Two leads drive this. First, the scripts reference a second host,
    m.forebet.com, which may not sit behind the same bot check. Second, the
    relay's Markdown engine clears the check while its html mode does not,
    so an ordinary open proxy is worth one request each to see whether the
    block is about the IP or about the request.
    """
    www = f"https://www.forebet.com/en/{sport_path}/predictions/{date}"
    mob = f"https://m.forebet.com/en/{sport_path}/predictions/{date}"
    plain = {"User-Agent": BROWSER_UA, "Accept": "text/html,*/*"}
    relay_html = {"User-Agent": "Slumdog", "Accept": "text/plain",
                  "X-No-Cache": "true", "X-Return-Format": "html"}
    attempts: dict[str, Any] = {
        "www_direct": lambda: direct_fetch(www, timeout=timeout, attempts=1),
        "mobile_direct": lambda: direct_fetch(mob, timeout=timeout, attempts=1),
        "mobile_relay_html": lambda: relay_request(
            RELAY_BASE + mob, relay_html, timeout=timeout),
        "mobile_relay_markdown": lambda: relay_request(
            RELAY_BASE + mob, {"User-Agent": "EdgeFactory/1.0",
                               "Accept": "text/plain", "X-No-Cache": "true"},
            timeout=timeout),
        "codetabs_proxy": lambda: relay_request(
            "https://api.codetabs.com/v1/proxy?quest=" + www, plain,
            timeout=timeout),
        "allorigins_raw": lambda: relay_request(
            "https://api.allorigins.win/raw?url=" + www, plain, timeout=timeout),
    }
    out: dict[str, Any] = {}
    for i, (name, call) in enumerate(attempts.items()):
        if i:
            pace(min(pause, 8))
        try:
            body = call()
        except Exception as exc:
            out[name] = {"error": f"{type(exc).__name__}: {exc}"[:110]}
            continue
        fingerprint = body_fingerprint(body, sample=180)
        lowered = body.lower()
        fingerprint["rows"] = lowered.count(b'class="rcnt')
        fingerprint["has_team_markup"] = b"itemprop" in lowered
        out[name] = fingerprint
    return out


def probe_routes(board_url: str, *, timeout: int, pause: float) -> dict[str, Any]:
    """Fingerprint every way we know of asking the relay for one board.

    The capture path currently uses ``X-Return-Format: html``, which is what
    returned the challenge page. If another mode comes back with real listing
    rows, that is the fix for the whole coverage collapse, not just for the
    timezone question.
    """
    relay = RELAY_BASE + board_url
    base = {"User-Agent": "Slumdog", "Accept": "text/plain", "X-No-Cache": "true"}

    def _headers(**extra: str) -> dict[str, str]:
        return {**base, **extra}

    # The default (Markdown reader) engine already gets past the bot check —
    # it returned 15KB of content where both html modes got the 5.9KB
    # interstitial. So the question is not "can the relay fetch this page"
    # but "can it hand the fetched page back as HTML". These combinations
    # ask the relay for HTML while keeping the engine that works.
    attempts = {
        "return_format_html": lambda: relay_request(
            relay, _headers(**{"X-Return-Format": "html"}), timeout=timeout),
        "html_browser_engine": lambda: relay_request(
            relay, _headers(**{"X-Return-Format": "html",
                               "X-Engine": "browser"}), timeout=timeout),
        "html_cf_engine": lambda: relay_request(
            relay, _headers(**{"X-Return-Format": "html",
                               "X-Engine": "cf-browser-rendering"}),
            timeout=timeout),
        "respond_with_html_browser": lambda: relay_request(
            relay, _headers(**{"X-Respond-With": "html",
                               "X-Engine": "browser"}), timeout=timeout),
        "markdown_reader": lambda: relay_request(
            relay, {"User-Agent": "EdgeFactory/1.0", "Accept": "text/plain",
                    "X-No-Cache": "true"}, timeout=timeout),
    }
    out: dict[str, Any] = {}
    for i, (name, call) in enumerate(attempts.items()):
        if i:
            pace(pause)
        try:
            body = call()
            sample = 1200 if name == "markdown_reader" else 320
            out[name] = body_fingerprint(body, sample=sample)
            lowered = (body or b"").lower()
            out[name]["has_date_bah"] = b"date_bah" in lowered
            out[name]["has_time_pattern"] = bool(
                re.search(rb"\d{2}/\d{2}/\d{4}", body or b""))
        except Exception as exc:
            out[name] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
    return out


#: Sections already written to the log as they were produced. Run
#: 36386778571 was cancelled at the 15-minute wall and reported nothing at
#: all, because every annotation was emitted after the last stage. A stage
#: that finishes now publishes its own result immediately, so an overrun
#: costs only the stages that had not run.
EMITTED_SECTIONS: set[str] = set()


def emit_section(key: str, value: Any) -> None:
    """Publish one stage's result the moment it exists."""
    if not value or key in EMITTED_SECTIONS:
        return
    EMITTED_SECTIONS.add(key)
    blob = json.dumps(value, sort_keys=True)[:2600]
    print(f"::notice title=probe:{key}::{_annotation_escape(blob)}",
          flush=True)


def stage_succeeded(name: str, record: dict[str, Any]) -> bool:
    """Did this stage actually answer its question?

    "Ran without raising" is not the same as "answered". A calibration
    that found no instants and a settlement that graded nothing both
    return perfectly well-formed records.
    """
    if not record:
        return False
    if name == "render_clock":
        return bool(record.get("proven"))
    if name == "collector_end_to_end":
        return (record.get("parsed_events") or 0) > 0
    if name == "circuit_breaker_far":
        # Unlike the plain collector_end_to_end stage, a COVERAGE_GAP here
        # (the board not existing yet) is the expected, useful answer, not
        # a failure to retry — the question is only "did a request happen
        # and get timed", which capture_timing being non-empty proves.
        return bool(record.get("capture_timing"))
    if name == "settlement_probe":
        return any((rec.get("graded") or 0) > 0
                   for rec in record.values() if isinstance(rec, dict))
    return True


def run_open_questions(date: str, *, timeout: int, pause: float,
                       passes: int = 3,
                       far_offset_days: int = 5) -> dict[str, Any]:
    """Run the unanswered stages in passes, not in one long grind.

    Every run so far spent each stage's whole slice retrying a refusal
    that was still in force seconds later, then moved on for good.
    Run 36407370005 is the clearest case: three stages, three slices
    spent, nothing answered, and the refusals were minutes long while the
    retries were seconds apart.

    Spacing beats persistence here. Each stage gets ONE cheap attempt per
    pass, and the passes are naturally minutes apart because the other
    stages run in between. A stage that has answered is not asked again.
    """
    # Ordered by what is still unknown, and sized by what each costs.
    #
    # Five runs say the binding constraint is REQUESTS, not seconds: the
    # calibration spends two and has proven the same offset three times
    # (its rendered column came back in 6.5 seconds), while a whole board
    # is eight columns times up to three attempts and has never once
    # completed in a run that also did anything else.
    #
    # So the expensive questions go first and the settled one goes last.
    # Both board stages also move to volleyball: ten rows against
    # hockey's sixty-four, same route, same proof - a smaller page renders
    # faster and is refused less, and what is being tested here is the
    # PATH, not the sport.
    far_date = (dt.date.fromisoformat(date)
               + dt.timedelta(days=far_offset_days)).isoformat()
    stages = (
        # circuit_breaker_columns=2 (the forward pass's exact opt-in, see
        # forward_shadow_batch.run_capture) rides along on this stage for
        # free: a capture that succeeds never trips the breaker, so this
        # is also the "near board, breaker on" half of Priority 1's live
        # measurement (item iii) — see circuit_breaker_comparison below,
        # which reads it back out instead of paying for a second capture
        # of the same board.
        ("collector_end_to_end", lambda budget: collector_end_to_end(
            date, timeout=timeout, pause=pause, sport="volleyball",
            slice_seconds=budget,
            circuit_breaker_columns=2, circuit_breaker_attempts=1)),
        ("settlement_probe", lambda budget: settlement_probe(
            date, timeout=timeout, pause=pause, sports=("volleyball",),
            slice_seconds=budget, attempts=1)),
        ("render_clock", lambda budget: render_clock_probe(
            date, timeout=timeout, pause=pause,
            slice_seconds=min(budget, 90), attempts=1)),
        # The far ("almost certainly absent") half of the same measurement.
        # Deliberately its own stage rather than folded into
        # circuit_breaker_measurement()'s two-fresh-capture form (see that
        # function's docstring): the near board is already captured above,
        # so this is the only additional request cost item (iii) pays.
        ("circuit_breaker_far", lambda budget: collector_end_to_end(
            far_date, timeout=timeout, pause=pause, sport="volleyball",
            slice_seconds=budget,
            circuit_breaker_columns=2, circuit_breaker_attempts=1)),
    )
    results: dict[str, Any] = {}
    stage_meta: dict[str, dict[str, Any]] = {}
    attempts_used: dict[str, int] = {name: 0 for name, _ in stages}
    for attempt in range(1, passes + 1):
        outstanding = [item for item in stages
                       if not stage_succeeded(item[0], results.get(item[0]))]
        if not outstanding or time_left() < 80:
            break
        # Split what is left between the questions still unanswered,
        # rather than holding every stage to a fixed share decided before
        # the run knew which ones would need it. A whole board is eight
        # column requests with retries; 110 seconds was never going to be
        # enough for it, and was more than the calibration ever needed.
        # The calibration has proven the same offset in three separate
        # runs on two requests. It is last in the list and it does not get
        # a share: it runs on what the open questions leave behind.
        costly = [item for item in outstanding if item[0] != "render_clock"]
        share = max(90.0, (time_left() - 70) / max(1, len(costly)))
        for name, run in outstanding:
            if time_left() < 80:
                break
            started = time.monotonic()
            try:
                record = run(share)
            except Exception as exc:  # noqa: BLE001 - reported, not raised
                record = {"verdict": f"{type(exc).__name__}: {exc}"[:200]}
            # Meta lives beside the record, never inside it: the
            # settlement record is keyed BY SPORT, and a "pass" key added
            # to it reads as a sport called "pass" to everything
            # downstream.
            attempts_used[name] = attempt
            stage_meta[name] = {"pass": attempt,
                                "seconds": round(time.monotonic() - started, 1)}
            # Keep the answer if one was ever obtained: a later refusal
            # does not unprove an earlier measurement.
            if stage_succeeded(name, record) or name not in results:
                results[name] = record
            if stage_succeeded(name, record):
                emit_section(name, record)
    for name, record in results.items():
        emit_section(name, record)
    results["passes_used"] = attempts_used
    results["stage_meta"] = stage_meta
    return results


def run_probe(date: str, *, sport: str, timeout: int, pause: float,
              run_hunt: bool = False, run_browser: bool = False) -> dict[str, Any]:
    EMITTED_SECTIONS.clear()
    report: dict[str, Any] = {
        "probed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "target_date": date,
        "extra_sport": sport,
    }

    # Coverage runs first: it is the question the project is actually
    # blocked on, and a later stage overrunning must not cost its answer.
    # The capture contract in force for this run. Annotations are read long
    # after the fact, and "columns disagree" means nothing without knowing
    # which columns were required and how rows were scoped.
    report["capture_contract"] = {
        "row_scope": ROW_SCOPE,
        "required_columns": list(REQUIRED_COLUMNS),
        "columns": dict(COLUMN_SELECTORS),
    }

    # Coverage is the question in hand and gets the budget first. The sweep
    # has already answered reachability for every sport, so it only reruns
    # when there is time to spare; row_blocks is retired for the same reason.
    # Whatever is still unanswered goes first. Basketball, hockey, handball
    # and volleyball are proven; rugby, mma and cricket are reachable but
    # have never been captured at the dates they actually publish, and in
    # run 36379077488 they were skipped entirely because the proven four
    # spent the budget re-proving themselves.
    # Coverage that cannot be graded is not coverage, and settlement is now
    # the only thing never proven against a real board — so it goes first,
    # ahead of sports that re-prove themselves every run.
    # The offset decides whether any sport but football can ever produce a
    # pick, and it costs two requests. Nothing else in this probe earns its
    # budget as cheaply.
    # The three questions this branch is blocked on, run in passes so a
    # refusal that lasts minutes does not cost a stage its only chance.
    open_questions = run_open_questions(date, timeout=timeout, pause=pause)
    report["passes_used"] = open_questions.pop("passes_used", {})
    stage_meta = open_questions.pop("stage_meta", {})
    report.update(open_questions)
    report["stage_seconds"] = {
        name: meta.get("seconds") for name, meta in stage_meta.items()}
    report["stage_seconds"]["budget_left"] = round(time_left())

    # Priority 1, item (iii): the live circuit-breaker measurement, built
    # from the two records open_questions already produced above (near =
    # collector_end_to_end, far = circuit_breaker_far) rather than a fresh
    # pair of captures — see circuit_breaker_comparison's docstring. Only
    # emitted once both halves exist; a stage that never got its turn
    # (time ran out) leaves nothing here rather than a misleading partial
    # comparison built from one real record and one empty one.
    if report.get("collector_end_to_end") and report.get("circuit_breaker_far"):
        report["circuit_breaker_comparison"] = circuit_breaker_comparison(
            report["collector_end_to_end"], report["circuit_breaker_far"])
        emit_section("circuit_breaker_comparison",
                     report["circuit_breaker_comparison"])

    # Coverage for the proven sports only runs on what is left: those
    # sports have produced rank-1 fields repeatedly, and re-proving them
    # was costing the stages that have never succeeded. Their slice used
    # to be a flat 45s each regardless of how much budget remained, so a
    # run with budget_left in the hundreds still reported every sport
    # "stopped: stage slice of 45s spent" (run a5e5720, 2026-09-28:
    # budget_left 482, r1_coverage itself spent only 235.2s and still
    # produced zero rankable fields). Divide what is actually left instead.
    if time_left() > 200:
        stage_started = time.monotonic()
        per_sport = max(45.0, (time_left() - 60) / max(1, len(COVERAGE_SPORTS)))
        report["r1_coverage"] = r1_coverage(date, timeout=timeout,
                                            pause=pause,
                                            slice_seconds=per_sport)
        report["stage_seconds"]["r1_coverage"] = round(
            time.monotonic() - stage_started, 1)
        emit_section("r1_coverage", report["r1_coverage"])
    emit_section("stage_seconds", report["stage_seconds"])

    if time_left() > 300:
        report["coverage_sweep"] = coverage_sweep(date, timeout=timeout,
                                                  pause=pause)
        emit_section("coverage_sweep", report["coverage_sweep"])
    json_kickoffs = football_utc_kickoffs(date, timeout=timeout)
    report["football_json_matches"] = len(json_kickoffs)

    pace(pause)
    board_url = f"{BASE}/en/football-predictions/predictions-1x2/{date}"
    board = fetch(board_url, timeout=timeout)
    report["football_board_url"] = board_url
    report["football_board_bytes"] = len(board or b"")

    rows = html_board_rows(board) if board else []
    report["football_board_rows"] = len(rows)
    report["football_board_body"] = body_fingerprint(board)

    offsets: list[int] = []
    samples: list[dict[str, Any]] = []
    for row in rows:
        utc = json_kickoffs.get(row["match_id"])
        displayed = parse_display_time(row["displayed"])
        if utc is None or displayed is None:
            continue
        delta = offset_minutes(displayed, utc)
        offsets.append(delta)
        if len(samples) < 5:
            samples.append({
                "match_id": row["match_id"],
                "displayed": row["displayed"],
                "utc": utc.isoformat(),
                "offset_minutes": delta,
            })
    report["offset"] = summarise_offsets(offsets)
    report["offset_samples"] = samples
    report["football_html_candidates"] = (
        machine_readable_candidates(board) if board else [])

    if sport and sport in SPORTS:
        pace(pause)
        other_url = source_url(SPORTS[sport], date)
        other = fetch(other_url, timeout=timeout)
        report["extra_sport_url"] = other_url
        report["extra_sport_bytes"] = len(other or b"")
        report["extra_sport_rows"] = len(html_board_rows(other)) if other else 0
        report["extra_sport_body"] = body_fingerprint(other)
        report["extra_sport_candidates"] = (
            machine_readable_candidates(other) if other else [])

    # Which relay mode, if any, returns a real board?
    pace(pause)
    routes_url = source_url(SPORTS[sport], date) if sport in SPORTS else board_url
    report["route_diagnostic_url"] = routes_url
    report["route_diagnostic"] = probe_routes(
        routes_url, timeout=timeout, pause=pause)

    pace(pause)
    report["markdown_modes"] = markdown_modes(
        date, SPORTS[sport].path if sport in SPORTS else sport,
        timeout=timeout, pause=pause)

    if run_hunt:
        pace(pause)
        report["api_sweep"] = api_endpoint_sweep(
            date, timeout=timeout, pause=pause)

        pace(pause)
        report["getjson_crack"] = crack_getjson(
            date, timeout=timeout, pause=pause)

        pace(pause)
        report["js_call_sites"] = mine_js_contexts(timeout=timeout)

        pace(pause)
        report["save_page_now"] = save_page_now(
            f"https://www.forebet.com/en/"
            f"{SPORTS[sport].path if sport in SPORTS else sport}"
            f"/predictions/{date}", timeout=timeout)

    if time_left() > 180:
        pace(min(pause, 5))
        report["columns"] = column_extracts(
            date, SPORTS[sport].path if sport in SPORTS else sport,
            timeout=timeout, pause=pause)

    if time_left() > 120:
        pace(min(pause, 5))
        report["selector_html"] = selector_html_modes(
            date, SPORTS[sport].path if sport in SPORTS else sport,
            timeout=timeout, pause=pause)

    pace(pause)
    report["recent_markup"] = recent_markup_check(
        SPORTS[sport].path if sport in SPORTS else sport,
        timeout=timeout, pause=pause)

    pace(pause)
    report["current_bundle"] = current_bundle_scan(
        timeout=timeout, pause=pause)

    pace(pause)
    report["render_waits"] = render_wait_modes(
        date, SPORTS[sport].path if sport in SPORTS else sport,
        timeout=timeout, pause=pause)

    # If any Markdown variant exposed match links, that is identity: the
    # slug names both teams and the id keys the per-match endpoint.
    harvested: list[tuple[str, str, str]] = []
    for fingerprint in (report.get("markdown_modes") or {}).values():
        for entry in (fingerprint.get("link_sample") or []):
            parts = entry.split("|")
            if len(parts) == 3 and tuple(parts) not in harvested:
                harvested.append(tuple(parts))
    report["harvested_links"] = harvested[:6]
    if harvested:
        sport_first = sorted(
            harvested, key=lambda p: 0 if "football" not in p[0] else 1)
        _, slug, mid = sport_first[0]
        pace(pause)
        report["match_json"] = test_match_json(slug, mid, timeout=timeout)

    pace(pause)
    report["sitemaps"] = discover_sitemaps(timeout=timeout, pause=pause)

    # If a sitemap lists match pages, harvest ids and feed one to the
    # per-match endpoint — the step that closes the circle.
    children = (report["sitemaps"].get("children") or {})
    target = None
    for name, child in children.items():
        if child.get("error"):
            continue
        if child.get("is_index"):
            for loc in child.get("sample", []):
                if any(k in loc.lower() for k in
                       ("match", "pred", "sport", "event")):
                    target = loc
                    break
        elif child.get("locs"):
            target = name
        if target:
            break
    if target:
        pace(pause)
        report["match_ids"] = harvest_match_ids(target, timeout=timeout)
        by_sport = report["match_ids"].get("by_sport") or {}
        pick = next((entries[0] for key, entries in by_sport.items()
                     if "football" not in key and entries), None)
        if pick is None:
            pick = next((entries[0] for entries in by_sport.values()
                         if entries), None)
        if pick:
            pace(pause)
            report["match_json"] = test_match_json(
                pick[0], pick[1], timeout=timeout)

    pace(pause)
    report["dom_selectors"] = live_dom_selectors(
        date, SPORTS[sport].path if sport in SPORTS else sport,
        timeout=timeout, pause=pause)

    # The browser attempt is the live question now, so it runs by default;
    # --no-browser skips it once it has been answered.
    if run_browser:
        report["browser_probe"] = browser_probe(
            date, SPORTS[sport].path if sport in SPORTS else sport)

    if not run_hunt:
        report["fetch_errors"] = list(FETCH_ERRORS)
        return report

    pace(pause)
    report["fetch_matrix"] = fetch_matrix(
        date, SPORTS[sport].path if sport in SPORTS else sport,
        timeout=timeout, pause=pause)

    # Hunt for a JSON endpoint for the blocked sports.
    pace(pause)
    page = f"https://www.forebet.com/en/{SPORTS[sport].path}/predictions" \
        if sport in SPORTS else board_url
    hunt = hunt_endpoints(page, timeout=timeout, pause=pause)
    report["endpoint_hunt"] = hunt
    # Positive control: run the same miner over a football board, where we
    # already know a JSON endpoint exists. If it finds nothing there either,
    # the miner is at fault, not the sport.
    pace(pause)
    report["endpoint_hunt_control"] = hunt_endpoints(
        "https://www.forebet.com/en/football-predictions/predictions-1x2",
        timeout=timeout, pause=pause)

    # Candidate sport codes for getrs.php: whatever the page itself used,
    # plus the obvious spellings for this sport.
    codes: list[str] = []
    for code in (hunt.get("tp_literals") or []) + [
            sport[:3], sport, "bsk", "bk", "bb"]:
        if code and code not in codes and code != "1x2":
            codes.append(code)
    if codes:
        pace(pause)
        report["tp_candidates"] = test_tp_candidates(
            date, codes[:8], timeout=timeout, pause=min(pause, 6))

    refs = [u for u in hunt.get("php_refs", [])
            if any(k in u.lower() for k in ("getrs", "get", "rs.php", "ajax"))][:4]
    if refs:
        pace(pause)
        report["endpoint_tests"] = test_candidates(
            refs, timeout=timeout, pause=pause)

    report["fetch_errors"] = list(FETCH_ERRORS)
    return report


def verdict(report: dict[str, Any]) -> tuple[bool, list[str]]:
    """Turn the report into a decision, erring toward 'not proven'."""
    lines: list[str] = []
    resolved = False

    if report.get("crashed"):
        lines.append(f"PROBE CRASHED: {report['crashed']}")
        lines.append((report.get("traceback") or "").strip()[-600:])

    candidates = (report.get("football_html_candidates") or []) + (
        report.get("extra_sport_candidates") or [])
    if candidates:
        resolved = True
        lines.append(
            f"MACHINE-READABLE START FOUND ({len(candidates)} hit(s)) — parse "
            "that field instead of the rendered text; no offset calibration "
            "needed.")
        for c in candidates[:5]:
            lines.append(f"  {c.get('kind')}: {c.get('attribute')} = {c.get('sample')}")
    else:
        lines.append(
            "No machine-readable start instant in the raw HTML — the rendered "
            "text is all there is.")

    errors = report.get("fetch_errors") or []
    if errors:
        lines.append(f"FETCH FAILURES ({len(errors)}) — the probe could not "
                     "reach part of the source:")
        for err in errors[:6]:
            if isinstance(err, dict):
                lines.append(f"  {err.get('route')} {err.get('url')}: "
                             f"{err.get('error')}")
            else:
                lines.append(f"  {err}")

    for key, label in (("football_board_body", "football board"),
                       ("extra_sport_body", "extra sport board")):
        fp = report.get(key)
        if fp and not fp.get("has_rcnt"):
            lines.append(
                f"{label.upper()} RETURNED NO LISTING ROWS "
                f"({fp.get('bytes')} bytes, looks_like={fp.get('looks_like')}) — "
                "the board was not fetched at all, so this run says nothing "
                "about timezones.")
            lines.append(f"  first bytes: {fp.get('sample', '')[:200]}")

    routes = report.get("route_diagnostic") or {}
    working = [name for name, fp in routes.items() if fp.get("has_rcnt")]
    if routes:
        lines.append("Relay route comparison on " +
                     str(report.get("route_diagnostic_url", "")) + ":")
        for name, fp in routes.items():
            detail = fp.get("error") or (
                f"{fp.get('bytes')} bytes, {fp.get('looks_like')}, "
                f"rows={'yes' if fp.get('has_rcnt') else 'no'}")
            lines.append(f"  {name}: {detail}")
        if working:
            lines.append(
                "WORKING CAPTURE ROUTE(S): " + ", ".join(working) +
                " — switching the collector to this mode is the fix for the "
                "missing non-football boards.")
        else:
            lines.append(
                "NO RELAY MODE RETURNED A BOARD — the boards are unreachable "
                "from a runner right now; that, not publishing lag, is why "
                "non-football sports have no picks.")

    sweep = report.get("api_sweep") or {}
    if sweep:
        lines.append("Sibling API endpoints (never tested before):")
        wins = []
        for name, entry in sweep.items():
            for mode in ("direct", "relayed"):
                fp = entry.get(mode)
                if not fp:
                    continue
                desc = fp.get("error") or (
                    f"{fp.get('bytes')}B json={fp.get('json_like')} "
                    f"challenge={fp.get('challenge')}")
                lines.append(f"  {name} [{mode}]: {desc}")
                if fp.get("json_like") and name != "getrs_control":
                    wins.append(f"{name} [{mode}]")
        if wins:
            lines.append("API RETURNS DATA: " + ", ".join(wins))
            for name in {w.split(" ")[0] for w in wins}:
                fp = sweep[name].get("direct") or sweep[name].get("relayed")
                lines.append(f"  {name} sample: {str(fp.get('sample'))[:260]}")
        control = (sweep.get("getrs_control") or {}).get("direct") or {}
        if control.get("json_like"):
            lines.append("  control: getrs.php answers DIRECTLY from the "
                         "runner — the APIs are not behind the bot check, so "
                         "an API route would need no relay at all.")

    sweep = report.get("coverage_sweep") or {}
    if sweep:
        lines.append("Coverage sweep of never-probed sports:")
        for sport, rec in sweep.items():
            lines.append(f"  {sport}: " + (rec.get("error") or
                         f"{rec.get('bytes')}B rows~{rec.get('links')} "
                         f"order={rec.get('date_order')} "
                         f"on_target={rec.get('on_target_date')} "
                         f"dates={rec.get('dates')}"))
        reachable = [s for s, r in sweep.items() if (r.get("links") or 0) > 0]
        ready = [s for s, r in sweep.items() if (r.get("on_target_date") or 0) > 0]
        lines.append(f"REACHABLE VIA THE COLUMN ROUTE: {len(reachable)}/"
                     f"{len(sweep)} — {', '.join(reachable) or 'none'}")
        lines.append(f"WITH MATCHES ON THE TARGET DATE: "
                     f"{', '.join(ready) or 'none'}")

    horizon = report.get("horizon_coverage") or {}
    if horizon:
        lines.append("Sports captured at their own publication horizon:")
        for sport, rec in horizon.items():
            lines.append(
                f"  {sport}: {rec.get('status', rec.get('verdict'))} "
                f"date={rec.get('horizon_date')} "
                f"(+{rec.get('days_ahead')}d) rows={rec.get('rows')} "
                f"events={rec.get('rankable_events')} {rec.get('reason', '')}")
            top = rec.get("top_by_probability")
            if top:
                lines.append(f"    strongest: {top['match']} "
                             f"p1={top['p1']} p2={top['p2']}")

    clock = report.get("render_clock") or {}
    if clock:
        if clock.get("proven"):
            lines.append(
                f"RENDER CLOCK MEASURED: rendered times are UTC"
                f"{clock['offset_hours']:+g}h "
                f"({clock['offset_minutes']} min) across "
                f"{clock['samples']} matches and "
                f"{clock['distinct_hours']} distinct hours. Every sport's "
                f"rendered kickoff can be converted to an instant.")
        else:
            lines.append(
                f"RENDER CLOCK NOT MEASURED: "
                f"{clock.get('reason') or clock.get('verdict')} "
                f"({clock.get('detail', '')}) — joined "
                f"{clock.get('joined')} of {clock.get('json_matches')} "
                f"json matches")

    settling = report.get("settlement_probe") or {}
    if settling:
        lines.append("D+1 settlement through the same route:")
        for sport, rec in settling.items():
            if not isinstance(rec, dict):
                continue
            lines.append(
                f"  {sport}: {rec.get('status', rec.get('verdict'))} "
                f"date={rec.get('settled_date')} rows={rec.get('rows')} "
                f"graded={rec.get('graded')} "
                f"statuses={rec.get('statuses_seen')} "
                f"{rec.get('graded_error', '')} {rec.get('reason', '')}")
            for row in rec.get("sample") or []:
                lines.append(f"    {row['match']} {row['score']} "
                             f"winner={row['winner_index']} "
                             f"id={row['event_id']}")

    coverage = report.get("r1_coverage") or {}
    if coverage:
        lines.append("R1 coverage per sport (live, nothing frozen):")
        for sport, rec in coverage.items():
            lines.append(
                f"  {sport}: {rec.get('status', rec.get('verdict'))} "
                f"rows={rec.get('rows')} "
                f"events={rec.get('rankable_events', 0)} "
                f"dates={rec.get('observed_dates')} "
                f"{rec.get('reason', '')[:120]}")
            top = rec.get("top_by_probability")
            if top:
                lines.append(f"    strongest: {top['match']} "
                             f"p1={top['p1']} p2={top['p2']} @ {top['kickoff']}")
        rankable = [s for s, r in coverage.items() if r.get("rankable_events")]
        lines.append(f"SPORTS WITH A RANKABLE FIELD: {len(rankable)}/"
                     f"{len(coverage)} — {', '.join(rankable) or 'none'}")

    cols = report.get("columns") or {}
    if cols:
        lines.append(f"Column extracts, aligned={cols.get('_aligned')} "
                     f"{cols.get('_counts')}")
        for name, fp in cols.items():
            if name.startswith("_"):
                continue
            lines.append(f"  {name} ({fp.get('selector')}): " +
                         (fp.get("error") or
                          f"{fp.get('rows')} rows {fp.get('first')}"))
        if cols.get("_aligned"):
            lines.append("COLUMNS ZIP CLEANLY — every field returns the same "
                         "row count, so a capture can join them by index.")

    shtml = report.get("selector_html") or {}
    if shtml:
        lines.append("Selector + HTML output (would feed the existing parser):")
        for name, fp in shtml.items():
            lines.append(f"  {name}: " + (fp.get("error") or
                         f"{fp.get('bytes')}B rcnt={fp.get('rcnt')} "
                         f"homeTeam={fp.get('home_team')} "
                         f"date_bah={fp.get('date_bah')} "
                         f"challenge={fp.get('challenge')}"))
        winners = [n for n, fp in shtml.items()
                   if (fp.get("rcnt") or 0) > 5 and (fp.get("home_team") or 0) > 5]
        if winners:
            lines.append(
                "RENDERED BOARD MARKUP AVAILABLE: " + ", ".join(winners) +
                " — parse_html_events can consume this as-is, so capture "
                "needs a fetch change and nothing else.")
            lines.append(f"  sample: {shtml[winners[0]].get('sample', '')[:300]}")

    recent = report.get("recent_markup") or {}
    if recent:
        lines.append(
            f"NEWEST archived board: {recent.get('using')} "
            f"({recent.get('bytes')}B, rows={recent.get('rows')}, "
            f"challenge={recent.get('challenge')}) of "
            f"{recent.get('snapshot_count')} snapshots "
            f"{recent.get('newest_timestamps')} {recent.get('error', '')}")
        lines.append(f"  team names in markup: {recent.get('has_itemprop_name')}"
                     f"  date_bah: {recent.get('has_date_bah')}")
        if recent.get("name_markup"):
            lines.append(f"  name markup: {recent['name_markup'][:320]}")
        if recent.get("endpoints_today"):
            lines.append("  endpoints in the newest scripts: " +
                         ", ".join(recent["endpoints_today"]))
        if recent.get("js_bytes"):
            lines.append(f"  scripts read: {recent['js_bytes']}")

    bundle = report.get("current_bundle") or {}
    if bundle:
        lines.append(f"Current bundle: {bundle.get('source')} "
                     f"{bundle.get('bytes')}B "
                     f"{bundle.get('errors') or ''}")
        if bundle.get("php_refs"):
            lines.append("  endpoints in TODAY's bundle: " +
                         ", ".join(bundle["php_refs"]))
        for needle, ctx in (bundle.get("contexts") or {}).items():
            lines.append(f"  bundle[{needle}]: {ctx[:300]}")

    waits = report.get("render_waits") or {}
    if waits:
        lines.append("Render-wait variants (do the team names arrive late?):")
        for name, fp in waits.items():
            lines.append(f"  {name}: " + (fp.get("error") or
                         f"{fp.get('bytes')}B links={fp.get('match_links')} "
                         f"names={fp.get('name_tokens')} "
                         f"{fp.get('name_sample')}"))
        winners = [n for n, fp in waits.items()
                   if (fp.get("match_links") or 0) > 5
                   or (fp.get("name_tokens") or 0) > 20]
        if winners:
            lines.append("TEAM NAMES ARRIVE WITH A WAIT: " + ", ".join(winners)
                         + " — the board is capturable through the renderer.")
            for n in winners[:1]:
                lines.append(f"  {n} listing: {waits[n].get('listing', '')[:500]}")

    got = report.get("harvested_links") or []
    if got:
        lines.append(f"Harvested identities ({len(got)}): {got[:4]}")

    maps = report.get("sitemaps") or {}
    if maps:
        lines.append(f"robots.txt: {maps.get('robots_bytes')}B "
                     f"sitemaps={maps.get('sitemaps_listed')} "
                     f"{maps.get('robots_error', '')}")
        for name, child in (maps.get("children") or {}).items():
            lines.append(f"  sitemap {name}: " + (child.get("error") or
                         f"{child.get('bytes')}B locs={child.get('locs')} "
                         f"index={child.get('is_index')} "
                         f"{child.get('sample')}"))
        if maps.get("direct_errors"):
            lines.append(f"  direct failed, relayed instead: "
                         f"{maps['direct_errors']}")

    harvest = report.get("match_ids") or {}
    if harvest:
        lines.append(f"Match ids from sitemap ({harvest.get('bytes')}B): "
                     f"{harvest.get('by_sport') or harvest.get('error')}")
    mjson = report.get("match_json") or {}
    if mjson:
        lines.append("getjson.php with a real match id: " +
                     (mjson.get("error") or
                      f"{mjson.get('bytes')}B empty={mjson.get('empty')} "
                      f"date_bah={mjson.get('has_date_bah')} "
                      f"body={mjson.get('sample', '')[:200]!r}"))
        if not mjson.get("empty") and not mjson.get("error"):
            lines.append("PER-MATCH JSON WORKS — sitemap gives the ids, this "
                         "endpoint gives the data, and neither is behind the "
                         "bot check. That is a capture route.")
            lines.append(f"  sample: {mjson.get('sample', '')[:300]}")

    dom = report.get("dom_selectors") or {}
    if dom:
        lines.append("Live DOM selectors (422 = selector matches nothing):")
        for selector, fp in dom.items():
            lines.append(f"  {selector}: " + (
                f"FOUND {fp.get('bytes')}B" if fp.get("found")
                else str(fp.get("error"))))
        for selector, fp in dom.items():
            if fp.get("found"):
                lines.append(
                    f"  {selector} → {fp.get('bytes')}B "
                    f"names={fp.get('names')} links={fp.get('links')} "
                    f"clocks={fp.get('clocks')} dates={fp.get('dates')}")
        best = max(((s, fp) for s, fp in dom.items() if fp.get("found")),
                   key=lambda kv: kv[1].get("links") or 0, default=None)
        if best and (best[1].get("links") or 0) > 5:
            lines.append(f"  {best[0]} rows: {best[1].get('sample', '')[:1100]}")
        named = [s for s, fp in dom.items() if (fp.get("names") or 0) > 25]
        if named:
            lines.append("TEAM NAMES EXTRACTED VIA SELECTOR: " +
                         ", ".join(named) + " — identity comes from this "
                         "element, the numbers from .rcnt, and neither needs "
                         "the raw HTML.")
        if dom.get(".rcnt", {}).get("found") is False:
            lines.append("THE LIVE BOARD HAS NO .rcnt — the markup our parser "
                         "targets is gone, so fetching alone would not have "
                         "been enough.")

    crack = report.get("getjson_crack") or {}
    if crack:
        lines.append("getjson.php parameter sweep (endpoint is live and "
                     "unchallenged, only the arguments were wrong):")
        hits = []
        for name, fp in crack.items():
            lines.append(f"  {name}: " + (fp.get("error") or
                         f"{fp.get('bytes')}B empty={fp.get('empty')} "
                         f"{fp.get('sample', '')[:90]}"))
            if not fp.get("error") and not fp.get("empty"):
                hits.append(name)
        if hits:
            lines.append("GETJSON RETURNS DATA FOR: " + ", ".join(hits))
            for name in hits[:2]:
                lines.append(f"  {name} sample: "
                             f"{crack[name].get('sample', '')[:300]}")

    sites = report.get("js_call_sites") or {}
    for key, value in sites.items():
        if key in ("snapshot", "bytes", "error"):
            lines.append(f"  js {key}: {value}")
            continue
        for hit in value[:2]:
            lines.append(f"  call site {key}: {hit[:360]}")

    spn = report.get("save_page_now") or {}
    if spn:
        lines.append(
            f"Save Page Now: save={spn.get('save')} "
            f"snapshots={spn.get('snapshots')} newest={spn.get('newest')} "
            f"bytes={spn.get('bytes')} rows={spn.get('rows')} "
            f"challenge={spn.get('challenge')} "
            f"{spn.get('read_error', '')}")
        if (spn.get("rows") or 0) > 0:
            lines.append("ARCHIVE CRAWLER GETS THE BOARD — the archive can "
                         "fetch what we cannot, so capture can go through it.")

    browser = report.get("browser_probe") or {}
    if browser:
        if browser.get("rows"):
            lines.append(
                f"REAL BROWSER GETS THE BOARD: {browser['rows']} rows, "
                f"{browser.get('bytes')} bytes, title={browser.get('title')!r} "
                "— headless Chromium on the runner clears the bot check, so "
                "the blocked sports are capturable again.")
            if browser.get("first_row_text"):
                lines.append(f"  first row: {browser['first_row_text']}")
            if browser.get("first_row_html"):
                lines.append(f"  first row html: {browser['first_row_html']}")
        else:
            detail = (f"install={browser.get('install')} "
                      f"{browser.get('bytes')}B "
                      f"{browser.get('looks_like')} "
                      f"title={browser.get('title')!r} "
                      f"wait={browser.get('wait_error', '-')} "
                      f"warmup={browser.get('warmup', '-')}")
            if browser.get("error"):
                detail = f"{browser['error']} | {detail}"
            lines.append("BROWSER PROBE FOUND NO ROWS: " + detail)

    modes = report.get("markdown_modes") or {}
    if modes:
        lines.append("Markdown variants (the only route that clears the check):")
        for name, fp in modes.items():
            lines.append(f"  {name}: " + (fp.get("error") or
                         f"{fp.get('bytes')}B links={fp.get('match_links')} "
                         f"clocks={fp.get('clocks')}"))
        for name, fp in modes.items():
            if fp.get("table_slice"):
                lines.append(f"  {name} listing: {fp['table_slice'][:900]}")
        usable = [n for n, fp in modes.items()
                  if (fp.get("match_links") or 0) > 5]
        if usable:
            lines.append(
                "MATCH IDENTITY RECOVERABLE VIA: " + ", ".join(usable) +
                " — the row links carry team names and match ids, which is "
                "enough to build a pick without the HTML board.")
            for n in usable[:2]:
                lines.append(f"  {n} links: {modes[n].get('link_sample')}")
        else:
            lines.append("NO MARKDOWN VARIANT CARRIES MATCH IDENTITY — the "
                         "numbers come through but nothing names the teams.")
        for n, fp in modes.items():
            if fp.get("sample"):
                lines.append(f"  {n} sample: {fp['sample'][:300]}")

    matrix = report.get("fetch_matrix") or {}
    if matrix:
        lines.append("Host/fetcher matrix:")
        for name, fp in matrix.items():
            lines.append(f"  {name}: " + (fp.get("error") or
                         f"{fp.get('bytes')}B {fp.get('looks_like')} "
                         f"rows={fp.get('rows')} "
                         f"teams={'yes' if fp.get('has_team_markup') else 'no'}"))
        wins = [n for n, fp in matrix.items() if (fp.get("rows") or 0) > 0]
        if wins:
            lines.append("BOARD HTML RECOVERED VIA: " + ", ".join(wins) +
                         " — this is the capture route for the blocked sports.")

    hunt = report.get("endpoint_hunt") or {}
    if hunt:
        if hunt.get("error"):
            lines.append(f"ENDPOINT HUNT FAILED: {hunt['error']}")
        else:
            lines.append(
                f"Archived markup for {hunt.get('page')} "
                f"({hunt.get('bytes')} bytes, {hunt.get('rcnt_rows')} rows) "
                f"via {hunt.get('snapshot')}")
            lines.append("  php refs: " + (", ".join(hunt.get("php_refs", []))
                                           or "none found"))
            if hunt.get("js_context"):
                lines.append("  js context: " + hunt["js_context"][:420])
            for needle, sample in (hunt.get("markup_samples") or {}).items():
                lines.append(f"  markup[{needle}]: {sample[:280]}")
    tps = report.get("tp_candidates") or {}
    winners = [c for c, fp in tps.items() if fp.get("json_like")]
    if tps:
        lines.append("getrs.php sport codes tried: " + ", ".join(
            f"{c}={'JSON' if fp.get('json_like') else (fp.get('error') or str(fp.get('bytes')) + 'B')}"
            for c, fp in tps.items()))
        if winners:
            lines.append(
                "SPORT CODE FOUND: tp=" + ", tp=".join(winners) +
                " returns JSON from getrs.php — capture this sport the way "
                "football is captured, no bot check and tz=0.")
            for c in winners:
                lines.append(f"  tp={c} sample: {tps[c].get('sample', '')[:240]}")
    for key in ("tp_literals", "inline_script_context"):
        value = (report.get("endpoint_hunt") or {}).get(key)
        if value:
            lines.append(f"  {key}: " + str(value)[:400])

    control = report.get("endpoint_hunt_control") or {}
    if control:
        lines.append("  control (football board) php refs: " +
                     (", ".join(control.get("php_refs", [])) or
                      "NONE — the miner itself found nothing where an "
                      "endpoint is known to exist"))
    tested = report.get("endpoint_tests") or {}
    for url, fp in tested.items():
        if fp.get("json_like"):
            lines.append(f"JSON ENDPOINT WORKS FOR THIS SPORT: {url} "
                         f"({fp.get('bytes')} bytes) — this is the capture "
                         f"route that unblocks it.")
        else:
            lines.append(f"  candidate {url}: "
                         f"{fp.get('error') or fp.get('looks_like')}")

    off = report.get("offset") or {}
    joined = off.get("joined", 0)
    modal = off.get("modal_offset_minutes")
    if joined >= 20 and off.get("agreement", 0) >= 0.98:
        resolved = True
        lines.append(
            f"RELAY RENDERING OFFSET = {modal:+d} min "
            f"(joined {joined} matches, agreement {off['agreement']:.1%}).")
        if modal == 0:
            lines.append(
                "  Offset is zero for THIS request. That is one observation, "
                "not a guarantee: it is IP-derived and can change. Calibrate "
                "per capture rather than hardcoding it.")
        else:
            lines.append(
                "  Rendered times must be shifted by this offset before any "
                "lead comparison. A positive offset is the dangerous "
                "direction (events look later than they are).")
    else:
        lines.append(
            f"OFFSET NOT RESOLVED (joined {joined}, "
            f"agreement {off.get('agreement', 0):.1%}) — the hold stands.")

    return resolved, lines


def _annotation_escape(text: str) -> str:
    """Escape a value for a GitHub Actions workflow command."""
    return (text.replace("%", "%25").replace("\r", "%0D")
                .replace("\n", "%0A").replace("::", "%3A%3A"))


#: Annotations are the channel that needs no human in the loop: they are
#: served by api.github.com straight to a read of the run/job, no paste
#: required. Full logs and artifacts (blob storage) ARE also readable from
#: this sandbox, but only via a signed URL the owner pastes in — see
#: AGENTS.md's "Remote Probing" table, corrected 2026-09-28 after two
#: earlier, narrower claims here both got this wrong in opposite
#: directions. Run 36343604474 emitted no annotations at all while
#: reporting success, and at the time there was no way to tell whether the
#: probe had crashed, been throttled, or simply said nothing. Everything
#: below exists to make that distinguishable without waiting on a paste.
MAX_SECTION_ANNOTATIONS = 8


def emit_heartbeat(date: str, sport: str) -> None:
    """Announce that the script started, before anything can go wrong.

    If this appears and the verdict does not, the probe died mid-run. If
    neither appears, it never started.
    """
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::notice title=probe:started::date={date} sport={sport} "
              f"pid={os.getpid()}", flush=True)


def emit_annotations(report: dict[str, Any], lines: list[str]) -> list[str]:
    """Print the verdict as Actions annotations and return what was printed.

    Annotations are served by api.github.com, whereas run logs and build
    artifacts are served from blob storage that needs a signed URL (see
    AGENTS.md). That difference matters: an annotation is what lets the
    result be read back with no owner action at all, not the only way the
    result CAN be read back.
    """
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return []
    compact = json.dumps({
        k: v for k, v in report.items()
        if k not in {"verdict", "offset_samples"}
    }, sort_keys=True)[:3000]
    emitted = [
        f"::notice title=Kickoff timezone verdict::{_annotation_escape(chr(10).join(lines))}",
        f"::notice title=Kickoff timezone report::{_annotation_escape(compact)}",
    ]
    # One annotation per section: a single blob silently truncates at ~3000
    # characters and the interesting result is usually last.
    sections = (
        "render_clock", "stage_seconds", "settlement_probe",
        "collector_end_to_end", "circuit_breaker_far",
        "circuit_breaker_comparison",
        "passes_used", "r1_coverage",
        "horizon_coverage",
        "capture_contract",
        "coverage_sweep", "match_json", "columns", "selector_html", "dom_selectors", "harvested_links",
        "recent_markup", "render_waits", "markdown_modes", "api_sweep",
        "getjson_crack", "js_call_sites", "current_bundle", "sitemaps",
        "endpoint_hunt", "fetch_matrix", "browser_probe", "save_page_now",
    )
    room = MAX_SECTION_ANNOTATIONS
    for key in sections:
        value = report.get(key)
        if not value or room <= 0 or key in EMITTED_SECTIONS:
            continue
        room -= 1
        blob = json.dumps(value, sort_keys=True)[:2600]
        emitted.append(
            f"::notice title=probe:{key}::{_annotation_escape(blob)}")
    for line in emitted:
        print(line)
    return emitted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="YYYY-MM-DD board to probe")
    parser.add_argument("--no-browser", dest="browser",
                        action="store_false",
                        help="skip the real-browser probe")
    parser.add_argument("--hunt", action="store_true",
                        help="also run the archive/endpoint hunt (slow, "
                             "already answered: no JSON twin exists)")
    parser.add_argument("--sport", default="basketball",
                        help="extra HTML board to scan for machine-readable "
                             "timestamps (default: basketball)")
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--pause", type=float, default=62.0,
                        help="seconds between requests (politeness)")
    parser.add_argument("--budget-seconds", type=float, default=660.0,
                        help="wall-clock budget; stages stop rather than let "
                             "the job be killed before it can report")
    parser.add_argument("--out", type=Path, default=None,
                        help="write the full JSON report here")
    parser.add_argument(
        "--circuit-breaker-probe", action="store_true",
        help=("Priority 1 (2026-09-28): measure the column-route circuit "
              "breaker live instead of running the full diagnostic sweep "
              "or re-dispatching the 350-minute Forward Shadow job. "
              "Captures --date (likely published) and --date plus "
              "--far-offset-days (likely absent), both with the breaker "
              "opted in, and reports capture_timing for each."))
    parser.add_argument(
        "--far-offset-days", type=int, default=5,
        help="days past --date for the likely-absent half of "
             "--circuit-breaker-probe (default 5, mirroring the forward "
             "pass's D+2..D+6 reach)")
    args = parser.parse_args(argv)

    dt.date.fromisoformat(args.date)
    set_deadline(args.budget_seconds)
    emit_heartbeat(args.date, args.sport)

    if args.circuit_breaker_probe:
        # Deliberately bypasses run_probe()'s dozens of legacy stages: this
        # is a small, targeted, fast measurement (2 real captures), not
        # another claimant on the multi-stage budget scheduler those
        # stages already share tightly.
        report = {
            "probed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "target_date": args.date,
            "mode": "circuit_breaker_probe",
        }
        try:
            report["circuit_breaker_measurement"] = circuit_breaker_measurement(
                args.date, timeout=args.timeout, pause=args.pause,
                sport=args.sport if args.sport in SPORTS else "volleyball",
                far_offset_days=args.far_offset_days,
                slice_seconds=min(args.budget_seconds - 60, 240.0))
        except Exception as exc:  # a crashed probe must still report
            import traceback
            report["crashed"] = f"{type(exc).__name__}: {exc}"
            report["traceback"] = traceback.format_exc()[-1500:]
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(report, indent=2, sort_keys=True))
        print(json.dumps(report, indent=2, sort_keys=True))
        if os.environ.get("GITHUB_ACTIONS") == "true":
            blob = json.dumps(
                report.get("circuit_breaker_measurement") or {},
                sort_keys=True)[:2600]
            print(f"::notice title=probe:circuit_breaker_measurement::"
                  f"{_annotation_escape(blob)}", flush=True)
        # Exit 0 whenever both halves produced a receipt-backed verdict
        # (capture_timing present), whatever that verdict was — a false
        # abort or a failed trip is itself the answer, not a probe failure.
        measurement = report.get("circuit_breaker_measurement") or {}
        answered = bool(
            (measurement.get("near") or {}).get("capture_timing")
            and (measurement.get("far") or {}).get("capture_timing"))
        return 0 if answered else 1

    try:
        report = run_probe(args.date, sport=args.sport, timeout=args.timeout,
                              pause=args.pause, run_hunt=args.hunt,
                              run_browser=args.browser)
    except Exception as exc:  # a crashed probe must still report
        import traceback
        report = {
            "probed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "target_date": args.date,
            "crashed": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc()[-1500:],
            "fetch_errors": list(FETCH_ERRORS),
            "offset": summarise_offsets([]),
        }
    try:
        resolved, lines = verdict(report)
    except Exception as exc:  # the verdict must never cost us the findings
        import traceback
        resolved = False
        lines = [f"VERDICT CRASHED: {type(exc).__name__}: {exc}",
                 traceback.format_exc()[-600:]]
    report["resolved"] = resolved
    report["verdict"] = lines

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, sort_keys=True))

    print(json.dumps(
        {k: v for k, v in report.items() if k != "verdict"},
        indent=2, sort_keys=True))
    print()
    for line in lines:
        print(line)

    try:
        emit_annotations(report, lines)
    except Exception as exc:  # noqa: BLE001
        print(f"::notice title=probe:emit_failed::{type(exc).__name__}: "
              f"{str(exc)[:300]}", flush=True)
    return 0 if resolved else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
