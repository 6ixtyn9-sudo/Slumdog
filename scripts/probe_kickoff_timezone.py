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


def direct_fetch(url: str, *, timeout: int) -> bytes:
    import urllib.request

    request = urllib.request.Request(url, headers={
        "User-Agent": BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,*/*",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


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
        absolute = urljoin(page_url, ref)
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
        if "forebet.com" not in absolute or not absolute.endswith(".js"):
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
    out["js_php_refs"] = mined[:10]
    out["php_refs"] = (out["php_refs"] + [
        m for m in mined if not m.startswith("!")])[:14]

    # Question 1 of the original hold: does a listing row carry a
    # machine-readable start instant, or only rendered local text?
    for needle in (b"date_bah", b"data-time", b"datetime", b"data-ts"):
        idx = html.lower().find(needle)
        if idx != -1:
            out.setdefault("markup_samples", {})[needle.decode()] = (
                html[max(0, idx - 160): idx + 200].decode("utf-8", "replace"))
    return out


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


def run_probe(date: str, *, sport: str, timeout: int, pause: float) -> dict[str, Any]:
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
            for needle, sample in (hunt.get("markup_samples") or {}).items():
                lines.append(f"  markup[{needle}]: {sample[:280]}")
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
                             "endpoint_tests")},
                           sort_keys=True)[:3000]
    emitted.append(
        f"::notice title=Endpoint hunt::{_annotation_escape(hunt_blob)}")
    for line in emitted:
        print(line)
    return emitted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="YYYY-MM-DD board to probe")
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
        report = run_probe(
            args.date, sport=args.sport, timeout=args.timeout, pause=args.pause)
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
