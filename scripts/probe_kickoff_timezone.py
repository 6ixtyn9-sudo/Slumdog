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

from slumdog.forebet import (  # noqa: E402
    RELAY_BASE,
    fetch_with_fallback,
    relay_get_markdown,
    source_url,
)
from slumdog.parsers import BASE  # noqa: E402
from slumdog.sports import SPORTS  # noqa: E402

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
    """(timestamp, original_url) pairs from the Wayback index.

    The availability API only answers for an exact URL; the CDX index does
    prefix search, which is what finds a sport board whose exact path was
    never archived but whose dated siblings were.
    """
    raw = direct_fetch(
        CDX_SEARCH.format(pattern=pattern, limit=limit), timeout=timeout)
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
        time.sleep(pause)
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
            time.sleep(pause)
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
            time.sleep(pause)
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

    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


MATCH_LINK = re.compile(
    rb"https?://(?:www\.|m\.)?forebet\.com/en/[a-z\-]+/predictions?/[^\s)\"']*\d{4,}",
    re.I)
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
            time.sleep(min(pause, 5))
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
            time.sleep(min(pause, 5))
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


def test_match_json(slug: str, mid: str, *, timeout: int) -> dict[str, Any]:
    """The call site wants gdt=<slug>&mid=<id>. Give it exactly that."""
    url = ("https://www.forebet.com/scripts/getjson.php?"
           f"gdt={slug}&mid={mid}")
    out: dict[str, Any] = {"url": url}
    try:
        body = relay_request(RELAY_BASE + url, {
            "User-Agent": "EdgeFactory/1.0", "Accept": "text/plain",
            "X-No-Cache": "true"}, timeout=timeout)
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"[:110]
        return out
    marker = b"Markdown Content:"
    payload = body.split(marker, 1)[1].strip() if marker in body else body
    out["bytes"] = len(payload)
    out["empty"] = payload[:2] in (b"[]", b"")
    out["has_date_bah"] = b"DATE_BAH" in payload or b"date_bah" in payload
    out["sample"] = payload[:300].decode("utf-8", "replace")
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
    for i, selector in enumerate(
            (".rcnt", "div.rcnt", "table", ".schema", "tbody", ".moduletable")):
        if i:
            time.sleep(min(pause, 4))
        try:
            body = relay_request(RELAY_BASE + url, {
                "User-Agent": "EdgeFactory/1.0", "Accept": "text/plain",
                "X-No-Cache": "true", "X-Target-Selector": selector},
                timeout=timeout)
            out[selector] = {"bytes": len(body), "found": True,
                             "sample": body[-200:].decode("utf-8", "replace")}
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
            time.sleep(min(pause, 5))
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
            time.sleep(min(pause, 5))
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
            time.sleep(min(pause, 8))
        try:
            body = relay_request(RELAY_BASE + url, {**base, **extra},
                                 timeout=timeout)
        except Exception as exc:
            out[name] = {"error": f"{type(exc).__name__}: {exc}"[:110]}
            continue
        links = [m.decode() for m in dict.fromkeys(MATCH_LINK.findall(body))]
        out[name] = {
            "bytes": len(body),
            "match_links": len(links),
            "clocks": len(CLOCK.findall(body)),
            "link_sample": links[:3],
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
            time.sleep(min(pause, 8))
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
            time.sleep(pause)
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


def run_probe(date: str, *, sport: str, timeout: int, pause: float,
              run_hunt: bool = False, run_browser: bool = False) -> dict[str, Any]:
    report: dict[str, Any] = {
        "probed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "target_date": date,
        "extra_sport": sport,
    }

    json_kickoffs = football_utc_kickoffs(date, timeout=timeout)
    report["football_json_matches"] = len(json_kickoffs)

    time.sleep(pause)
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
        time.sleep(pause)
        other_url = source_url(SPORTS[sport], date)
        other = fetch(other_url, timeout=timeout)
        report["extra_sport_url"] = other_url
        report["extra_sport_bytes"] = len(other or b"")
        report["extra_sport_rows"] = len(html_board_rows(other)) if other else 0
        report["extra_sport_body"] = body_fingerprint(other)
        report["extra_sport_candidates"] = (
            machine_readable_candidates(other) if other else [])

    # Which relay mode, if any, returns a real board?
    time.sleep(pause)
    routes_url = source_url(SPORTS[sport], date) if sport in SPORTS else board_url
    report["route_diagnostic_url"] = routes_url
    report["route_diagnostic"] = probe_routes(
        routes_url, timeout=timeout, pause=pause)

    time.sleep(pause)
    report["markdown_modes"] = markdown_modes(
        date, SPORTS[sport].path if sport in SPORTS else sport,
        timeout=timeout, pause=pause)

    if run_hunt:
        time.sleep(pause)
        report["api_sweep"] = api_endpoint_sweep(
            date, timeout=timeout, pause=pause)

        time.sleep(pause)
        report["getjson_crack"] = crack_getjson(
            date, timeout=timeout, pause=pause)

        time.sleep(pause)
        report["js_call_sites"] = mine_js_contexts(timeout=timeout)

        time.sleep(pause)
        report["save_page_now"] = save_page_now(
            f"https://www.forebet.com/en/"
            f"{SPORTS[sport].path if sport in SPORTS else sport}"
            f"/predictions/{date}", timeout=timeout)

    time.sleep(pause)
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
        time.sleep(pause)
        report["match_ids"] = harvest_match_ids(target, timeout=timeout)
        by_sport = report["match_ids"].get("by_sport") or {}
        pick = next((entries[0] for key, entries in by_sport.items()
                     if "football" not in key and entries), None)
        if pick is None:
            pick = next((entries[0] for entries in by_sport.values()
                         if entries), None)
        if pick:
            time.sleep(pause)
            report["match_json"] = test_match_json(
                pick[0], pick[1], timeout=timeout)

    time.sleep(pause)
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

    time.sleep(pause)
    report["fetch_matrix"] = fetch_matrix(
        date, SPORTS[sport].path if sport in SPORTS else sport,
        timeout=timeout, pause=pause)

    # Hunt for a JSON endpoint for the blocked sports.
    time.sleep(pause)
    page = f"https://www.forebet.com/en/{SPORTS[sport].path}/predictions" \
        if sport in SPORTS else board_url
    hunt = hunt_endpoints(page, timeout=timeout, pause=pause)
    report["endpoint_hunt"] = hunt
    # Positive control: run the same miner over a football board, where we
    # already know a JSON endpoint exists. If it finds nothing there either,
    # the miner is at fault, not the sport.
    time.sleep(pause)
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
        time.sleep(pause)
        report["tp_candidates"] = test_tp_candidates(
            date, codes[:8], timeout=timeout, pause=min(pause, 6))

    refs = [u for u in hunt.get("php_refs", [])
            if any(k in u.lower() for k in ("getrs", "get", "rs.php", "ajax"))][:4]
    if refs:
        time.sleep(pause)
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
            lines.append(f"  {err.get('route')} {err.get('url')}: {err.get('error')}")

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
                      f"date_bah={mjson.get('has_date_bah')}"))
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


def emit_annotations(report: dict[str, Any], lines: list[str]) -> list[str]:
    """Print the verdict as Actions annotations and return what was printed.

    Annotations are served by api.github.com, whereas run logs and build
    artifacts are served from blob storage. That difference matters: it is
    what lets the result be read back without a human copying it out of a
    browser.
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
    hunt_blob = json.dumps({k: report.get(k) for k in
                            ("endpoint_hunt", "endpoint_hunt_control",
                             "endpoint_tests", "tp_candidates",
                             "fetch_matrix", "markdown_modes",
                             "browser_probe", "api_sweep",
                             "save_page_now", "getjson_crack",
                             "js_call_sites", "sitemaps",
                             "match_ids", "match_json", "dom_selectors")},
                           sort_keys=True)[:3000]
    emitted.append(
        f"::notice title=Endpoint hunt::{_annotation_escape(hunt_blob)}")
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
    parser.add_argument("--out", type=Path, default=None,
                        help="write the full JSON report here")
    args = parser.parse_args(argv)

    dt.date.fromisoformat(args.date)
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
    resolved, lines = verdict(report)
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

    emit_annotations(report, lines)
    return 0 if resolved else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
