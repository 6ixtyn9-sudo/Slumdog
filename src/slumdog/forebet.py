"""Immutable multi-sport Forebet raw capture.

The collector freezes complete source pages before sport parsers are allowed to
interpret them. Jina Reader is used as a public network relay; wrapper source
provenance must match exactly. No credentials are transmitted.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from .sports import SPORTS, SportSpec

RELAY_BASE = "https://r.jina.ai/"
CONTENT_MARKER = "Markdown Content:\n"

# The public relay is aggressively rate-limited/auth-walled on shared
# datacenter IPs. Retry transient failures with bounded exponential backoff
# plus jitter; hard client errors (401/403) are not retried since they are
# deterministic per context and would only burn the budget.
_RETRYABLE = (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError)
_RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}

# Direct Forebet access (the relay fallback route) needs the AJAX header set
# Edge-Factory validated for /scripts/getrs.php: browser User-Agent, a real
# Referer and X-Requested-With. Without these the endpoint can refuse.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.forebet.com/en/football-tips-and-predictions-for-today",
    "X-Requested-With": "XMLHttpRequest",
    "Connection": "keep-alive",
}

# curl_cffi TLS impersonations are used for LOCAL direct capture (from an
# operator IP the endpoint works). Edge-Factory's 2026-08-20 probe proved
# browser TLS does NOT overcome the provider's GitHub-hosted-runner block, so
# this is intentionally not the cloud answer. Optional dep, lazy import.
_CFFI_IMPERSONATIONS = ("safari17_0", "firefox133")


def on_github_runner() -> bool:
    """True when running on a GitHub-hosted runner (Actions).

    Forebet's own IP path is blocked on GitHub runners even with browser TLS
    (Edge-Factory run #503 / addendum 2026-08-20), and the relay 401 is
    deterministic there. Failing fast avoids burning the run budget on a
    fallback that cannot succeed from that network.
    """
    return os.environ.get("GITHUB_ACTIONS", "").strip().lower() == "true"


def _cffi_get(url: str, impersonate: str, timeout: int) -> bytes:
    from curl_cffi import requests as curl_requests

    headers = {key: value for key, value in _BROWSER_HEADERS.items() if key.lower() != "user-agent"}
    response = curl_requests.get(url, impersonate=impersonate, headers=headers, timeout=timeout)
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}")
    return bytes(response.content)


def _sleep_with_jitter(attempt: int, base: float = 4.0, cap: float = 40.0) -> None:
    delay = min(cap, base * (2 ** attempt)) * (0.7 + 0.6 * random.random())
    time.sleep(delay)


def relay_get(url: str, timeout: int = 45, max_retries: int = 3) -> bytes:
    """GET a relay URL with bounded retry/backoff for transient failures."""
    last_error: Exception | None = None
    for attempt in range(max_retries):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Slumdog",
                "Accept": "text/plain",
                "X-No-Cache": "true",
                "X-Return-Format": "html",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in _RETRY_STATUS:
                raise  # 401/403/404 are deterministic; do not retry
        except _RETRYABLE as exc:
            last_error = exc
        if attempt + 1 < max_retries:
            _sleep_with_jitter(attempt)
    raise last_error  # type: ignore[misc]


def relay_get_markdown(url: str, expected_url: str, timeout: int = 45, max_retries: int = 3) -> bytes:
    """GET via the relay in Markdown reader mode (Edge-Factory-validated path).

    The relay's default reader mode (no ``X-Return-Format``) renders the
    target as a Markdown wrapper: ``Title: … URL Source: <url> … Markdown
    Content:\\n<body>``. Edge-Factory qualified this exact mode for the
    football JSON endpoint from GitHub Actions (byte-identical JSON, no
    secrets); the html-forced mode is what 401s there. Returns the unwrapped
    body. ``expected_url`` must match the wrapper's ``URL Source:`` exactly.
    """
    last_error: Exception | None = None
    for attempt in range(max_retries):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "EdgeFactory/1.0",
                "Accept": "text/plain",
                "X-No-Cache": "true",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                wrapped = response.read()
            return unwrap_reader(wrapped, expected_url)
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in _RETRY_STATUS:
                raise
        except _RETRYABLE as exc:
            last_error = exc
        if attempt + 1 < max_retries:
            _sleep_with_jitter(attempt)
    raise last_error  # type: ignore[misc]


def direct_get(url: str, timeout: int = 40, max_retries: int = 3) -> bytes:
    """GET a Forebet URL directly, trying distinct transports in order.

    The relay is auth-walled on shared runner IPs; direct is the fallback.
    Transport chain (mirrors Edge-Factory's forebet adapter): urllib with the
    AJAX header set, then curl_cffi TLS impersonations for anti-bot TLS
    fingerprinting. Returns the first body that decodes; each transport is
    tried once and the whole chain is bounded by ``max_retries`` total rounds.
    """
    transports: list[tuple[str, Any]] = [
        ("urllib", lambda: _urllib_get(url, timeout)),
    ]
    import importlib.util
    if importlib.util.find_spec("curl_cffi") is not None:
        for identity in _CFFI_IMPERSONATIONS:
            transports.append((f"curl_cffi:{identity}", lambda identity=identity: _cffi_get(url, identity, timeout)))

    errors: list[str] = []
    for attempt in range(max_retries):
        for name, request in transports:
            try:
                return request()
            except Exception as exc:  # try the next distinct transport
                errors.append(f"{name}={type(exc).__name__}")
                time.sleep(1.0 + attempt)
    raise RuntimeError(
        f"direct fetch failed across transports: {', '.join(errors[-6:])}"
    )


def _urllib_get(url: str, timeout: int) -> bytes:
    request = urllib.request.Request(url, headers=_BROWSER_HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_with_fallback(
    relay_url: str,
    direct_url: str,
    timeout: int = 45,
    max_retries: int = 3,
) -> tuple[bytes, str]:
    """Fetch via the relay, falling back to a direct request on any failure.

    Returns ``(body, route)`` where route is ``"relay"`` or ``"direct"``. The
    relay is throttled/auth-walled on shared runner IPs (football hit
    deterministic 401s), so a direct browser-like request is the fallback.
    """
    try:
        body = relay_get(relay_url, timeout=timeout, max_retries=max_retries)
        return body, "relay"
    except Exception:
        if on_github_runner():
            # The direct path cannot succeed from a GitHub runner (provider
            # blocks the IP even with browser TLS, per Edge-Factory). Fail
            # fast so the date stays retryable instead of stalling the run.
            raise
        body = direct_get(direct_url, timeout=timeout, max_retries=max_retries)
        return body, "direct"


@dataclass(frozen=True)
class RawCapture:
    sport: str
    target_date: str
    captured_at: str
    source_url: str
    relay_url: str
    body_format: str
    sha256: str
    bytes: int
    body_path: str
    metadata_path: str
    route: str = "relay"  # "relay" (r.jina.ai) or "direct" (forebet.com)


# Football JSON market endpoints that return a DISTINCT numeric surface,
# verified against live getrs.php on 2026-08-23 (714 rows each). `tp=htft`
# returns byte-identical keys/values to `tp=ht` (single odds_ht_ft price, not
# a 9-cell matrix), so it is excluded. `tp=corners`, `tp=doublechance` and
# `tp=goalscorer` echo the 1X2 payload exactly at the JSON layer — their data
# exists only on the per-match detail page (handled by detail_facets). These
# five markets cost one request per DATE (covering all matches), so they are
# cheap enough to capture historically, unlike per-match detail pages.
FOOTBALL_MARKETS: tuple[str, ...] = (
    "uo", "bts", "ht", "ah", "cards",
)
FOOTBALL_MARKET_KEYS: dict[str, tuple[str, ...]] = {
    "uo": (
        "pr_over", "pr_under", "odds_under_over", "best_over", "best_under",
        "odds_under_over_am", "best_over_am", "best_under_am",
    ),
    "bts": (
        "Pred_gg", "Pred_no_gg", "odds_gg_y", "odds_gg_n",
        "odds_gg_y_am", "odds_gg_n_am",
    ),
    "ht": (
        "Pred_1_HT", "Pred_X_HT", "Pred_2_HT", "best_odd_ht",
        "odds_ht_ft", "best_odd_am_ht",
    ),
    "ah": ("odds_ah", "AH_type", "predAH", "odds_ah_am"),
    "cards": (
        "avg_cards", "host_card_pred", "guest_card_pred", "pred_line",
        "pred_over", "pred_under", "host_yellowcards", "guest_yellowcards",
        "host_redcards", "guest_redcards", "host_yellowredcards",
        "guest_yellowredcards",
    ),
}
# Fields sourced from the per-match detail page (no JSON endpoint exists);
# documented here so parsers/facets share one vocabulary.
FOOTBALL_DETAIL_ONLY_MARKETS: tuple[str, ...] = ("corners", "doublechance", "goalscorer")


def fetch_football_markets(
    target_date: str,
    root: Path | str = ".",
    timeout: int = 45,
    force: bool = False,
) -> Path | None:
    """Capture distinct football markets (uo, bts, ht, ah, cards) for a date.

    Each market endpoint returns every match for the date in one request, so
    five requests cover an entire day's board (verified 2026-08-23: 714 rows
    each). This is cheap enough to run during historical backfill, unlike the
    per-match detail pages. `tp=corners`, `tp=doublechance` and `tp=goalscorer`
    are intentionally excluded: their JSON echoes the 1X2 payload and their
    data only exists on the detail page.

    A previous run's ``markets.json`` is reused unless ``force`` is set, so
    re-dispatches never re-spend the five relay requests.
    """
    root = Path(root)
    out = root / "data" / "raw" / "football" / target_date / "markets.json"
    if out.exists() and not force:
        return out
    base = source_url(SPORTS["football"], target_date)
    merged: dict[str, dict[str, object]] = {}
    succeeded: list[str] = []
    failures: list[dict[str, str]] = []
    for market in FOOTBALL_MARKETS:
        url = base.replace("tp=1x2", f"tp={market}")
        try:
            body = relay_get_markdown(
                RELAY_BASE + url, url, timeout=timeout, max_retries=2,
            )
            payload = json.loads(body.decode("utf-8", "replace"))
            rows = payload[0] if isinstance(payload, list) and payload else []
            if not isinstance(rows, list):
                raise ValueError("unexpected market row shape")
            for row in rows:
                if isinstance(row, dict) and row.get("id") not in (None, ""):
                    merged.setdefault(str(row["id"]), {}).update(row)
            succeeded.append(market)
        except Exception as exc:
            failures.append({
                "market": market,
                "error": f"{type(exc).__name__}: {exc}",
            })

    if not succeeded:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(list(merged.values()), indent=2, sort_keys=True))
    receipt = {
        "target_date": target_date,
        "requested": list(FOOTBALL_MARKETS),
        "succeeded": succeeded,
        "failures": failures,
        "rows": len(merged),
        "output": str(out.relative_to(root)),
    }
    (out.parent / "markets_receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True)
    )
    return out


def source_url(spec: SportSpec, target_date: str) -> str:
    """Use Forebet's date-addressable sport page, not wall-clock labels."""
    if spec.key == "football":
        # Football's human date slug is not stable. The public Forebet JSON
        # endpoint is explicitly date-addressable and avoids wall-clock labels.
        return (
            "https://www.forebet.com/scripts/getrs.php?"
            f"ln=en&tp=1x2&in={target_date}&ord=0&tz=0&tzs=&tze="
        )
    if spec.current_only:
        # Current-only boards (currently esoccer and AFL) have no reliable
        # dated archive. Capture the board and let the parser filter by the
        # event date shown in each row.
        return f"https://www.forebet.com/en/{spec.path}"
    return f"https://www.forebet.com/en/{spec.path}/predictions/{target_date}"


def board_url(spec: SportSpec, target_date: str) -> str:
    """The HUMAN board for a sport — the page the renderer reads.

    Not the same thing as :func:`source_url`, and the difference is not
    cosmetic. For football, ``source_url`` is the tz=0 JSON endpoint; there
    is no board markup there at all. For every other sport the two agree.

    Getting this wrong is silent: the renderer answers HTTP 422 for "your
    selector matched nothing", which is indistinguishable from throttling,
    so a wrong URL reads as a site that is refusing you. Run 36402990164
    spent 108 seconds and three attempts proving exactly that against
    ``/en/football-tips-and-predictions/predictions/...``, a page that does
    not exist.
    """
    if spec.key == "football":
        return ("https://www.forebet.com/en/football-predictions/"
                f"predictions-1x2/{target_date}")
    return source_url(spec, target_date)


def unwrap_reader(raw: bytes | str, expected_url: str) -> bytes:
    """Legacy Markdown-wrapper validator retained for forensic tests."""
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    if text.count(CONTENT_MARKER) != 1:
        raise ValueError("unexpected reader wrapper marker count")
    header, body = text.split(CONTENT_MARKER, 1)
    if f"URL Source: {expected_url}" not in header:
        raise ValueError("reader source URL mismatch")
    body_bytes = body.strip().encode("utf-8")
    if len(body_bytes) < 20:
        raise ValueError("reader body unexpectedly short")
    if b"Not what you were looking for?" in body_bytes or b"Forebet 404 Error" in body_bytes:
        raise ValueError("Forebet returned a 404 content page")
    return body_bytes


# Markers of a bot-check interstitial served instead of the page. Measured
# 2026-09-26: the relay's html mode returns a ~5.8KB "Just a moment..." page
# for Forebet listing boards. For dated boards that page failed the
# date check by accident, but for ``current_only`` sports (esoccer, afl) the
# lenient label check let it through, so challenge pages were being stored as
# genuine captures. Reject them explicitly rather than relying on a
# coincidence.
# 2026-09-28: a SECOND wording appeared, and on the tz=0 JSON endpoint
# rather than an HTML board — 272 bytes of "Performing security
# verification ... protect against malicious bots", captured live by the
# probe (run 36401440850). It matched none of the markers below. It failed
# closed anyway, by the same coincidence this list exists to stop relying
# on: the JSON parse threw, and an HTML board would have missed its sport
# label. Name it instead.
_CHALLENGE_MARKERS = (
    b"just a moment",
    b"performing security verification",
    b"security service to protect against malicious bots",
    b"verifying you are human",
    b"challenge-platform",
    b"cf_chl_opt",
    b"cf-chl",
    b"attention required! | cloudflare",
    b"enable javascript and cookies to continue",
)


def looks_like_challenge_page(body: bytes) -> bool:
    """True if the body is a bot-check interstitial rather than content."""
    lower = body.lower()
    return any(marker in lower for marker in _CHALLENGE_MARKERS)


def validate_html_body(body: bytes, sport: str, target_date: str) -> None:
    if len(body) < 100:
        raise ValueError("HTML capture unexpectedly short")
    if looks_like_challenge_page(body):
        raise ValueError(
            f"relay returned a bot-check challenge page, not a board "
            f"({len(body)} bytes)"
        )
    lower = body.lower()
    if b"not what you were looking for" in lower or b"forebet 404 error" in lower:
        raise ValueError("Forebet returned a 404 content page")
    if b"<html" not in lower:
        raise ValueError("relay did not return HTML")
    if sport == "football":
        if b"<body>[[{" not in lower:
            raise ValueError("football JSON body missing")
        return
    label = sport.replace("_", " ").encode()
    if label not in lower:
        raise ValueError(f"sport label missing from HTML: {sport}")
    if not SPORTS[sport].current_only:
        day = datetime.fromisoformat(target_date).strftime("%d/%m/%Y").encode()
        if day not in body:
            raise ValueError(f"target date missing from HTML: {target_date}")


def _parse_football_payload(body: bytes):
    """Parse football JSON from relay HTML-wrapped or raw form; validate shape."""
    stripped = body.lstrip()
    if stripped[:1] in (b"[", b"{"):
        try:
            payload = json.loads(body.decode("utf-8", "replace"))
        except Exception as exc:
            raise ValueError(f"football JSON body missing: {exc}") from exc
    else:
        soup = BeautifulSoup(body, "html.parser")
        text = soup.body.get_text() if soup.body else body.decode("utf-8", "replace")
        try:
            payload = json.loads(text)
        except Exception as exc:
            raise ValueError(f"football JSON body missing: {exc}") from exc
    if not (isinstance(payload, list) and payload and isinstance(payload[0], list)):
        raise ValueError("unexpected football JSON shape")
    return payload


def validate_football_json_body(body: bytes) -> None:
    """Accept the relay's HTML-wrapped JSON or Forebet's raw JSON payload."""
    _parse_football_payload(body)


def validate_capture_body(body: bytes, sport: str, target_date: str, route: str) -> None:
    """Sport- and route-aware validation of a captured body."""
    if sport == "football":
        # Football arrives as raw JSON (direct access, or the relay's Markdown
        # reader mode after unwrapping) or as the relay's HTML-wrapped JSON.
        # Both are accepted by validate_football_json_body.
        validate_football_json_body(body)
        return
    validate_html_body(body, sport, target_date)


def _classify_capture_outcome(exc: Exception) -> str:
    """Label a failed ``_fetch`` for the per-sport-date timing instrument.

    ``_fetch``'s column-route failure message embeds
    ``relay_columns.BoardCapture.status`` verbatim (``"... column capture
    returned {result.status}: {result.reason}"``), so the two board-read
    outcomes are recoverable from the exception text without a second
    return channel. Anything else — a network error, a validation error
    from a route that never reached the column fallback — is ``RAISED``:
    an exception this collector did not classify, not a quiet gap.
    """
    text = str(exc)
    for status in ("COVERAGE_GAP", "NO_ROWS_FOR_DATE"):
        if status in text:
            return status
    return "RAISED"


def sample_canary(target_date: str | None = None, *, timeout: int = 20) -> dict[str, Any]:
    """One standalone request to football's tz=0 JSON, VIA THE RELAY —
    production's default path, and the only path this function tests.

    ``_canary_state`` below reads this same discriminator for free, but
    only after a call to :meth:`ForebetCollector.capture_selected` has
    already fetched football as part of its normal sport selection. This
    function makes the check available on its own, BEFORE any per-sport
    capture has started — the pre-flight the owner asked for after two
    consecutive relay-path blocks (2026-09-28): "we've been optimising how
    much we ask, when the binding constraint may be when we ask."
    Callers: ``forward_shadow_batch.py``'s pre-flight/mid-run abort (do not
    spend a forward pass's capture budget against a wall), and the probe's
    ``--canary-only`` mode (a few seconds, safe on a tight cron, versus the
    full multi-stage sweep).

    **Relabelled 2026-09-28, second correction — read this before trusting
    a ``healthy: False`` reading:** a server-side fetch got a REAL response
    direct from ``forebet.com`` at the exact moment a relay fetch of the
    identical URL returned a challenge page. This function only ever goes
    through the relay (``relay_get_markdown``) — it therefore measures
    whether OUR PATH is currently reachable, not whether the source itself
    is refusing this runner. A ``False`` here is evidence the relay is
    blocked, not evidence the board is unpublished AND not proof Forebet
    itself is down — see ``direct_vs_relay_probe`` for the runner-side
    test of the direct path this function deliberately does not take.

    Deliberately ONE attempt (``relay_get_markdown(..., max_retries=1)``):
    a WAF challenge is a refusal, not congestion, and retrying it harder is
    how run 36426785929 spent two hours grinding ~70 sport-dates through
    their full retry budgets against a wall. A canary that itself retried
    would only be a slower, quieter version of that same mistake.

    Returns ``{"sport": "football", "checked": True, "healthy": bool,
    "reason": str | None, "sampled_at": <UTC ISO8601>}``. Never raises: a
    connection failure or an unparseable body IS an unhealthy canary —
    that is the entire reason to call this. ``reason`` says "via the
    relay" explicitly so the receipt cannot be misread as a source-level
    finding.
    """
    target_date = target_date or date.today().isoformat()
    sampled_at = datetime.now(timezone.utc).isoformat()
    target = source_url(SPORTS["football"], target_date)
    relay = RELAY_BASE + target
    try:
        body = relay_get_markdown(relay, target, timeout=timeout, max_retries=1)
    except Exception as exc:
        return {"sport": "football", "checked": True, "healthy": False,
                "reason": f"football tz=0 JSON via the relay raised "
                          f"{type(exc).__name__}: {exc}"[:200],
                "sampled_at": sampled_at}
    if looks_like_challenge_page(body):
        return {"sport": "football", "checked": True, "healthy": False,
                "reason": f"football tz=0 JSON via the relay looked like "
                          f"a challenge page ({len(body)} bytes) — this "
                          f"is a relay-path finding, not proof the "
                          f"source itself refused us",
                "sampled_at": sampled_at}
    try:
        validate_football_json_body(body)
    except Exception as exc:
        return {"sport": "football", "checked": True, "healthy": False,
                "reason": f"football tz=0 JSON via the relay failed to "
                          f"parse: {exc}"[:200],
                "sampled_at": sampled_at}
    return {"sport": "football", "checked": True, "healthy": True,
            "reason": None, "sampled_at": sampled_at}


def _canary_state(selected: list[str], existing: set[str],
                  captures: "list[RawCapture]",
                  failures: list[str]) -> dict[str, Any]:
    """Football's tz=0 JSON is the cheap, already-fetched discriminator
    between "this board is not published yet" and "our path is being
    refused right now" — both currently surface identically as an HTTP
    422 / ``COVERAGE_GAP`` from any other sport's column route. Whenever a
    call to :meth:`ForebetCollector.capture_selected` also fetches
    football (the common case — it is first in ``SPORTS`` and
    ``sports=None`` requests every sport), football's own result IS that
    discriminator, for free.

    Owner finding, 2026-09-28 (Priority 1, item iii): a near/far
    circuit-breaker comparison run while football's own fetch was
    challenge-blocked could not tell "not published" from "our path
    refused right now" apart — the one distinction the comparison exists
    to draw. Every receipt now records whether football (the canary) was
    healthy for THIS SAME run, so a blocked run can be recognised and
    discarded instead of misread as a publication-timing finding.

    **Relabelled 2026-09-28, second correction:** this reads football's
    result from ``_fetch`` — relay-only on a GitHub runner (direct
    fallback is skipped there by ``on_github_runner()``; see
    ``fetch_with_fallback``). A ``healthy: False`` reading on a runner
    therefore means "the relay path was blocked this run", not "the
    source refused us" — a server-side fetch got a real response direct
    from ``forebet.com`` at the exact moment the relay returned a
    challenge page for the identical URL. See ``sample_canary`` (the
    pre-flight sibling, same relay-only scope) and
    ``direct_vs_relay_probe`` (the runner-side confirmation) for the full
    finding. Do not read a ``False`` here as proof the site itself is
    down.
    """
    if "football" not in selected:
        return {"sport": "football", "checked": False, "healthy": None,
                "reason": "football was not requested in this capture"}
    if "football" in existing:
        return {"sport": "football", "checked": False, "healthy": None,
                "reason": ("football was reused from a prior capture on "
                          "disk this call; not rechecked")}
    if any(cap.sport == "football" for cap in captures):
        return {"sport": "football", "checked": True, "healthy": True,
                "reason": None}
    reason = next((f for f in failures if f.startswith("football:")), None)
    return {"sport": "football", "checked": True, "healthy": False,
            "reason": reason or
            "football capture failed with no recorded reason"}


#: Prepended to a non-football COVERAGE_GAP failure/outcome when the canary
#: (football) also failed in the same run — see ``_canary_state``. Leads
#: with the correction so it is the first thing read, not an appendix to
#: the breaker's own "not-published signal" wording.
#:
#: Relabelled 2026-09-28 from "SITE-WIDE REFUSAL": the owner fetched
#: football's tz=0 JSON directly (no relay) and via the relay at the same
#: moment and got a REAL response direct while the relay returned a
#: challenge page — the canary (relay-only, see ``sample_canary``) may be
#: measuring OUR PATH, not the source. Calling this "site-wide" asserted a
#: cause this discriminator was never able to prove; "CANARY PATH BLOCKED"
#: says only what was actually observed.
_CANARY_PATH_BLOCKED_PREFIX = (
    "[CANARY PATH BLOCKED \u2014 football (our canary) also failed this "
    "run over the same path; NOT evidence the board is unpublished, and "
    "NOT proof the source itself refused us \u2014 2026-09-28: a direct "
    "fetch succeeded at the exact moment the relay did not; see "
    "direct_vs_relay_probe] "
)


def _mark_canary_path_blocked(capture_timing: list[dict],
                              failures: list[str]) -> None:
    """When the canary is down, relabel every OTHER sport's ``COVERAGE_GAP``
    entry so nothing downstream repeats the "board not published" reading a
    blocked canary produces identically.

    ``NO_ROWS_FOR_DATE`` is never touched: it is only ever returned on
    positive evidence (the board rendered cleanly and held no match for the
    date — see ``relay_columns.capture_board``'s docstring), so a sport
    that reached that status did NOT get refused this run regardless of
    what happened to football.
    """
    for i, text in enumerate(failures):
        sport = text.split(":", 1)[0]
        if sport == "football" or "COVERAGE_GAP" not in text:
            continue
        failures[i] = _CANARY_PATH_BLOCKED_PREFIX + text
    for entry in capture_timing:
        if entry.get("sport") == "football":
            continue
        if str(entry.get("outcome", "")).startswith("COVERAGE_GAP"):
            entry["outcome"] = "COVERAGE_GAP:canary_path_blocked"



class ForebetCollector:
    def __init__(self, root: Path | str = ".", timeout: int = 35,
                 workers: int = 4, before_request=None,
                 circuit_breaker_columns: int = 0,
                 circuit_breaker_attempts: int = 1):
        self.root = Path(root)
        self.timeout = timeout
        self.workers = max(1, min(int(workers), 6))
        # Optional callback invoked before each board is fetched and before
        # each column request inside the fallback. A caller under a
        # wall-clock cap needs a say: one board can cost a first HTML
        # attempt plus eight column requests with retries, which outlived
        # a 110-second budget by a factor of four in run 36409134160.
        # Production passes nothing and is unchanged.
        self.before_request = before_request
        # Off by default (Priority 1, scoped 2026-09-28): the column-route
        # circuit breaker (see relay_columns.fetch_board_columns) is only
        # safe on a stage that expects most of its boards to genuinely not
        # exist yet (the D+2..D+6 forward pass). Refusals on this source
        # are intermittent per-column, not per-board, so a stage where the
        # board usually already exists (event-day, the daily refresh, any
        # settlement capture) must not enable it — a false trip there
        # costs a real pick or a real grade, not a wasted probe. Only
        # forward_shadow_batch.py's run_capture() passes
        # circuit_breaker_columns=2 explicitly.
        self.circuit_breaker_columns = circuit_breaker_columns
        self.circuit_breaker_attempts = circuit_breaker_attempts

    def _fetch(self, sport: str, target_date: str) -> RawCapture:
        if self.before_request is not None:
            self.before_request()
        body_format = "html"
        spec = SPORTS[sport]
        target = source_url(spec, target_date)
        relay = RELAY_BASE + target
        if sport == "football":
            # The relay's Markdown reader mode (Edge-Factory-validated) is the
            # qualified path for the football JSON endpoint; the html-forced
            # mode 401s on cloud IPs. Try markdown first, then html, then
            # direct (local-only, fail-fast on a runner).
            body = b""
            route = ""
            last_error = None
            for attempt in range(3):
                try:
                    try:
                        body = relay_get_markdown(relay, target, timeout=self.timeout)
                        route = "relay_markdown"
                    except Exception:
                        body, route = fetch_with_fallback(relay, target, timeout=self.timeout)
                    validate_capture_body(body, sport, target_date, route)
                    last_error = None
                    break
                except ValueError as exc:
                    last_error = exc
                    if attempt + 1 < 3:
                        _sleep_with_jitter(attempt)
            if last_error is not None:
                raise last_error
        else:
            body_format = "html"
            try:
                # ONE attempt, not three. Since 2026-09-22 this board has
                # answered a bot-check page to everything CI can send, and
                # it has never once succeeded since - so the three retries
                # inside fetch_with_fallback buy nothing and cost three
                # full-page renders of a 42KB document. In run 36419041728
                # the column requests that followed them came back 403
                # while the same relay served a five-column settlement
                # capture seconds later. One attempt is enough to detect a
                # bot-check; the retries were paying for the privilege of
                # being rate-limited.
                body, route = fetch_with_fallback(relay, target,
                                                  timeout=self.timeout,
                                                  max_retries=1)
                validate_capture_body(body, sport, target_date, route)
            except ValueError:
                # From 2026-09-22 the HTML boards answer a bot-check page to
                # everything CI can send, which is why R1 coverage collapsed
                # to football alone. The renderer still serves the board one
                # field at a time; capture_board fails closed, so reaching
                # here either produces a real board or raises.
                from .relay_columns import (
                    CAPTURED,
                    capture_board,
                    serialise_columns,
                )

                result = capture_board(
                    target, sport, target_date,
                    captured_at=datetime.now(timezone.utc).isoformat(),
                    timeout=self.timeout,
                    before_request=self.before_request,
                    circuit_breaker_columns=self.circuit_breaker_columns,
                    circuit_breaker_attempts=self.circuit_breaker_attempts)
                if result.status != CAPTURED:
                    raise ValueError(
                        f"{sport} {target_date}: html capture rejected and "
                        f"column capture returned {result.status}: "
                        f"{result.reason}") from None
                board = result.board
                if board is None:  # defensive: CAPTURED implies a board
                    raise ValueError(
                        f"{sport} {target_date}: captured without columns"
                    ) from None
                body = serialise_columns(board)
                route = "relay_columns"
                body_format = "columns_v1"
        captured_at = datetime.now(timezone.utc).isoformat()
        digest = hashlib.sha256(body).hexdigest()
        stamp = captured_at.replace(":", "").replace("+00:00", "Z").replace("-", "")
        directory = self.root / "data" / "raw" / sport / target_date
        directory.mkdir(parents=True, exist_ok=True)
        body_path = directory / f"{stamp}_{digest[:12]}.txt"
        meta_path = directory / f"{stamp}_{digest[:12]}.json"
        body_path.write_bytes(body)
        capture = RawCapture(
            sport=sport,
            target_date=target_date,
            captured_at=captured_at,
            source_url=target,
            relay_url=relay,
            body_format=body_format if sport != "football" else "html",
            sha256=digest,
            bytes=len(body),
            body_path=str(body_path.relative_to(self.root)),
            metadata_path=str(meta_path.relative_to(self.root)),
            route=route,
        )
        meta_path.write_text(json.dumps(asdict(capture), indent=2, sort_keys=True))
        return capture

    def capture_selected(self, target_date: str, sports: list[str] | None = None,
                         *, force: bool = False,
                         receipt_name: str | None = None,
                         pause_seconds: float = 0.0,
                         on_capture_timing=None,
                         serial: bool | None = None) -> list[RawCapture]:
        """``on_capture_timing``, if given, is called once per sport-date on
        the paced serial path the moment that sport's fetch finishes, with
        the same dict recorded into the receipt's ``capture_timing`` list
        (``sport``/``elapsed_seconds``/``requests``/``outcome``). This is a
        streaming callback, not just a post-hoc receipt field, so a caller
        can log it to stderr immediately — a run killed mid-batch still
        leaves that evidence in the job log even though the receipt file
        for the date in flight never gets written. A raising callback is
        swallowed; it must never break a capture.

        ``serial`` picks the timed, one-sport-at-a-time path explicitly
        instead of the untimed thread-pool one. Default ``None`` infers it
        from ``pause_seconds > 0`` (every production stage already passes a
        real pause and gets this for free). Pass ``serial=True`` outright
        for a caller that wants timing on a single sport where a "small
        but truthy" pause would otherwise be needed only to select the
        branch and never actually sleep — that was a real trap here once:
        it worked by accident and nothing marked the accident as load-
        bearing. ``on_capture_timing`` with ``serial`` false (explicitly or
        by inference) is refused outright rather than silently producing an
        empty ``capture_timing`` — the parallel path has no per-sport
        instrumentation at all, so asking for a callback there is a
        contradiction, not a valid no-op.
        """
        date.fromisoformat(target_date)
        if serial is None:
            serial = pause_seconds > 0
        if on_capture_timing is not None and not serial:
            raise ValueError(
                "on_capture_timing requires serial=True (or pause_seconds>0"
                " to infer it): the parallel (workers>1) path has no"
                " per-sport timing to call it with")
        selected = list(SPORTS) if not sports else sports
        unknown = [sport for sport in selected if sport not in SPORTS]
        if unknown:
            raise ValueError(f"unsupported sports: {unknown}")
        # receipt_name lets the daily-refresh stage write its own receipt
        # (capture_refresh_<date>_<stamp>.json); the original one-shot
        # capture_<date>.json is never overwritten (append-only evidence).
        receipt_filename = receipt_name or f"capture_{target_date}.json"
        if not receipt_filename.endswith(".json") or "/" in receipt_filename:
            raise ValueError(f"bad receipt_name: {receipt_name!r}")
        # Reuse captures already frozen for this date (same-day re-dispatch or
        # census-then-history): skip a sport if its raw dir for the date exists.
        # ``force`` skips this reuse rule entirely; the daily-refresh stage
        # needs a genuinely fresh snapshot even when a prior capture for the
        # date is present.
        existing = set() if force else {
            cap.sport for cap in self._existing_captures(target_date)}
        to_fetch = [sport for sport in selected if sport not in existing]
        captures: list[RawCapture] = [cap for cap in self._existing_captures(target_date) if cap.sport in selected]
        failures: list[str] = []
        # Per-sport-date instrumentation (Priority 1, 2026-09-28): the
        # forward pass captures D+2..D+6 x 14 sports through this loop with
        # zero internal timing, which is why Forward Shadow #33 (run
        # 36426785929) could only be described as ">=1h56m on one
        # undifferentiated step" — nobody could say which sport-date cost
        # what. Only the paced serial path gets this (the parallel path
        # below is used for historical backfill, not the timing-sensitive
        # forward pass). ``requests`` counts ``before_request`` calls, which
        # fire once before the initial fetch and once per column-route
        # attempt (``relay_columns.fetch_column``) — the same seam a request
        # budget would hook into, so the count is what a budget would see.
        capture_timing: list[dict] = []
        if serial:
            # Paced serial path. A same-day stage fetches every sport in one
            # burst; ``pause_seconds`` spaces those requests the same way the
            # settlement capture does, so an extra daily stage does not raise
            # the request rate seen by the source.
            outer_before_request = self.before_request
            for i, sport in enumerate(to_fetch):
                if i > 0:
                    time.sleep(pause_seconds)
                request_count = {"n": 0}

                def _counted_before_request(_outer=outer_before_request,
                                            _count=request_count):
                    _count["n"] += 1
                    if _outer is not None:
                        _outer()

                self.before_request = _counted_before_request
                started = time.monotonic()
                outcome = "CAPTURED"
                try:
                    cap = self._fetch(sport, target_date)
                    captures.append(cap)
                    outcome = f"CAPTURED:{cap.route}"
                except Exception as exc:  # each satellite fails independently
                    failures.append(f"{sport}:{type(exc).__name__}:{exc}")
                    outcome = _classify_capture_outcome(exc)
                finally:
                    self.before_request = outer_before_request
                    entry = {
                        "sport": sport,
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                        "requests": request_count["n"],
                        "outcome": outcome,
                    }
                    capture_timing.append(entry)
                    # Fired the moment this sport-date finishes, not after
                    # the whole capture_selected() call returns: a killed
                    # run (run 36426785929 was cancelled mid-batch with
                    # zero stderr output after its last completed stage)
                    # still leaves this evidence in the job log even if the
                    # receipt file for the in-flight date never gets
                    # written. Never allowed to break the capture itself.
                    if on_capture_timing is not None:
                        try:
                            on_capture_timing(entry)
                        except Exception:
                            pass
        else:
            with ThreadPoolExecutor(max_workers=self.workers) as executor:
                futures = {sport: executor.submit(self._fetch, sport, target_date) for sport in to_fetch}
                for sport in to_fetch:  # deterministic result order
                    try:
                        captures.append(futures[sport].result())
                    except Exception as exc:  # each satellite fails independently
                        failures.append(f"{sport}:{type(exc).__name__}:{exc}")

        # Capture the five distinct JSON markets for the date. They cover all
        # matches in one request each, so they are cheap enough to fetch for
        # historical dates too; fetch_football_markets itself skips a prior
        # markets.json on disk. Reused (already-frozen) football captures do
        # not re-trigger the market requests.
        markets_path: Path | None = None
        if "football" in to_fetch:
            try:
                markets_path = fetch_football_markets(target_date, self.root, self.timeout)
            except Exception as exc:
                failures.append(f"football-markets:{type(exc).__name__}:{exc}")

        # Priority 1, item (iii) correction (2026-09-28): decide whether
        # football (the canary) was itself refused THIS run before any
        # other sport's COVERAGE_GAP is written down as "not published" —
        # see _canary_state's docstring. Mutates failures/capture_timing
        # in place so every consumer of this receipt (the forward-pass
        # annotation rollup included) sees the correction, not just this
        # function's own return value.
        canary = _canary_state(selected, existing, captures, failures)
        if canary["healthy"] is False:
            _mark_canary_path_blocked(capture_timing, failures)

        report_dir = self.root / "data" / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        receipt = {
            "target_date": target_date,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "captured": [asdict(item) for item in captures],
            "failures": failures,
            "reused": len(captures) - len(to_fetch),
            "football_markets": (
                str(markets_path.relative_to(self.root))
                if markets_path is not None else None
            ),
            # Empty unless serial=True (or pause_seconds>0, which infers
            # it) — only the paced serial path times individual sports.
            # See the comment above the serial loop for why.
            "capture_timing": capture_timing,
            # Recorded for EVERY run, healthy or not — see _canary_state.
            # A future analysis over many receipts can filter blocked
            # runs out instead of re-deriving "was the site down" from
            # scratch each time.
            "canary": canary,
        }
        (report_dir / receipt_filename).write_text(
            json.dumps(receipt, indent=2, sort_keys=True)
        )
        return captures

    def _existing_captures(self, target_date: str) -> list[RawCapture]:
        """Load previously frozen capture metadata for a date from disk."""
        found: list[RawCapture] = []
        sport_dir = self.root / "data" / "raw"
        if not sport_dir.exists():
            return found
        for sport in SPORTS:
            day_dir = sport_dir / sport / target_date
            if not day_dir.is_dir():
                continue
            metas = sorted(day_dir.glob("*.json"))
            if not metas:
                continue
            try:
                meta = json.loads(metas[-1].read_text())
                found.append(RawCapture(**meta))
            except Exception:
                continue
        return found

    def capture_all(self, target_date: str) -> list[RawCapture]:
        return self.capture_selected(target_date, None)
