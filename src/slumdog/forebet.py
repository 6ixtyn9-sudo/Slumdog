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
                "User-Agent": "Slumdog/0.1",
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


def source_url(spec: SportSpec, target_date: str) -> str:
    """Use Forebet's date-addressable sport page, not wall-clock labels."""
    if spec.key == "football":
        # Football's human date slug is not stable. The public Forebet JSON
        # endpoint is explicitly date-addressable and avoids wall-clock labels.
        return (
            "https://www.forebet.com/scripts/getrs.php?"
            f"ln=en&tp=1x2&in={target_date}&ord=0&tz=0&tzs=&tze="
        )
    if spec.key == "esoccer":
        # Esoccer exposes rolling today/tomorrow pages but no reliable dated
        # archive route. Capture the full Esoccer board and filter by event date.
        return "https://www.forebet.com/en/esoccer"
    return f"https://www.forebet.com/en/{spec.path}/predictions/{target_date}"


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


def validate_html_body(body: bytes, sport: str, target_date: str) -> None:
    if len(body) < 100:
        raise ValueError("HTML capture unexpectedly short")
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
    if sport != "esoccer":
        day = datetime.fromisoformat(target_date).strftime("%d/%m/%Y").encode()
        if day not in body:
            raise ValueError(f"target date missing from HTML: {target_date}")


def validate_football_json_body(body: bytes) -> None:
    """Accept the relay's HTML-wrapped JSON or Forebet's raw JSON payload."""
    if b"<body>[[{" in body.lower():
        return
    try:
        payload = json.loads(body.decode("utf-8", "replace"))
    except Exception as exc:
        raise ValueError(f"football JSON body missing: {exc}") from exc
    if not (isinstance(payload, list) and payload and isinstance(payload[0], list)):
        raise ValueError("unexpected football JSON shape")


def validate_capture_body(body: bytes, sport: str, target_date: str, route: str) -> None:
    """Sport- and route-aware validation of a captured body."""
    if route == "direct" and sport == "football":
        # Direct access returns raw JSON without the relay's HTML wrapper.
        validate_football_json_body(body)
        return
    validate_html_body(body, sport, target_date)


class ForebetCollector:
    def __init__(self, root: Path | str = ".", timeout: int = 35, workers: int = 4):
        self.root = Path(root)
        self.timeout = timeout
        self.workers = max(1, min(int(workers), 6))

    def _fetch(self, sport: str, target_date: str) -> RawCapture:
        spec = SPORTS[sport]
        target = source_url(spec, target_date)
        relay = RELAY_BASE + target
        body, route = fetch_with_fallback(relay, target, timeout=self.timeout)
        validate_capture_body(body, sport, target_date, route)
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
            body_format="html",
            sha256=digest,
            bytes=len(body),
            body_path=str(body_path.relative_to(self.root)),
            metadata_path=str(meta_path.relative_to(self.root)),
            route=route,
        )
        meta_path.write_text(json.dumps(asdict(capture), indent=2, sort_keys=True))
        return capture

    def capture_selected(self, target_date: str, sports: list[str] | None = None) -> list[RawCapture]:
        date.fromisoformat(target_date)
        selected = list(SPORTS) if not sports else sports
        unknown = [sport for sport in selected if sport not in SPORTS]
        if unknown:
            raise ValueError(f"unsupported sports: {unknown}")
        captures: list[RawCapture] = []
        failures: list[str] = []
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = {sport: executor.submit(self._fetch, sport, target_date) for sport in selected}
            for sport in selected:  # deterministic result order
                try:
                    captures.append(futures[sport].result())
                except Exception as exc:  # each satellite fails independently
                    failures.append(f"{sport}:{type(exc).__name__}:{exc}")
        report_dir = self.root / "data" / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        receipt = {
            "target_date": target_date,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "captured": [asdict(item) for item in captures],
            "failures": failures,
        }
        (report_dir / f"capture_{target_date}.json").write_text(
            json.dumps(receipt, indent=2, sort_keys=True)
        )
        return captures

    def capture_all(self, target_date: str) -> list[RawCapture]:
        return self.capture_selected(target_date, None)
