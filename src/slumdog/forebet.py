"""Immutable multi-sport Forebet raw capture.

The collector freezes complete source pages before sport parsers are allowed to
interpret them. Jina Reader is used as a public network relay; wrapper source
provenance must match exactly. No credentials are transmitted.
"""
from __future__ import annotations

import hashlib
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from .sports import SPORTS, SportSpec

RELAY_BASE = "https://r.jina.ai/"
CONTENT_MARKER = "Markdown Content:\n"


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


class ForebetCollector:
    def __init__(self, root: Path | str = ".", timeout: int = 35, workers: int = 4):
        self.root = Path(root)
        self.timeout = timeout
        self.workers = max(1, min(int(workers), 6))

    def _fetch(self, sport: str, target_date: str) -> RawCapture:
        spec = SPORTS[sport]
        target = source_url(spec, target_date)
        relay = RELAY_BASE + target
        request = urllib.request.Request(
            relay,
            headers={
                "User-Agent": "Slumdog/0.1",
                "Accept": "text/plain",
                "X-No-Cache": "true",
                "X-Return-Format": "html",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = response.read()
        validate_html_body(body, sport, target_date)
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
