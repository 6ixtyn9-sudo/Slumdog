"""Resumable, rate-bounded Forebet match-detail capture and facet audit."""
from __future__ import annotations

import hashlib
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

from .contracts import TimingClass
from .detail_facets import parse_detail
from .forebet import RELAY_BASE


def _detail_path(root: Path, sport: str, event_id: str) -> Path:
    digest = hashlib.sha256(event_id.encode()).hexdigest()[:16]
    return root / "data" / "raw" / "details" / sport / f"{digest}.html"


def _fetch_detail(url: str, timeout: int = 35) -> bytes:
    request = urllib.request.Request(
        RELAY_BASE + url,
        headers={
            "User-Agent": "Slumdog-Detail/0.1",
            "X-Return-Format": "html",
            "X-No-Cache": "true",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
    lower = body.lower()
    if len(body) < 100 or b"not what you were looking for" in lower:
        raise ValueError("invalid detail page")
    return body


def capture_detail_batch(
    events_path: Path | str,
    root: Path | str = ".",
    max_events: int = 18,
    workers: int = 4,
) -> Path:
    """Capture next missing detail batch; never exceeds one relay-minute budget."""
    root = Path(root)
    events = json.loads(Path(events_path).read_text())
    candidates = []
    for event in events:
        if not isinstance(event, dict):
            continue
        url = str(event.get("source_url") or "")
        if "/matches/" not in url:
            continue
        path = _detail_path(root, str(event.get("sport")), str(event.get("event_id")))
        if path.exists():
            continue
        p1 = float(event.get("probability_1") or 0)
        p2 = float(event.get("probability_2") or 0)
        dog_probability = min(p1, p2)
        # Most plausible upset candidates first; this orders work but never
        # changes the eventual full queue or output count.
        candidates.append((-dog_probability, str(event.get("sport")), str(event.get("event_id")), url, path))
    batch = sorted(candidates)[: max(0, min(int(max_events), 18))]

    def fetch(item):
        _, sport, event_id, url, path = item
        try:
            body = _fetch_detail(url)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            return {"sport": sport, "event_id": event_id, "url": url,
                    "status": "OK", "path": str(path.relative_to(root)), "bytes": len(body)}
        except Exception as exc:
            return {"sport": sport, "event_id": event_id, "url": url,
                    "status": f"ERROR:{type(exc).__name__}"}

    with ThreadPoolExecutor(max_workers=max(1, min(int(workers), 6))) as executor:
        results = list(executor.map(fetch, batch))
    report = root / "data" / "reports" / "detail_capture_latest.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "requested": len(batch),
        "remaining_before_run": len(candidates),
        "results": results,
    }, indent=2, sort_keys=True))
    return report


def enrich_events_from_details(
    events_path: Path | str,
    root: Path | str = ".",
) -> Path:
    root = Path(root)
    events = json.loads(Path(events_path).read_text())
    coverage: dict[str, dict[str, int]] = {}
    enriched = 0
    capture_day = date.today().isoformat()
    for event in events:
        if not isinstance(event, dict):
            continue
        sport, event_id = str(event.get("sport")), str(event.get("event_id"))
        path = _detail_path(root, sport, event_id)
        if not path.exists():
            continue
        facets = parse_detail(path.read_bytes(), sport)
        numeric = facets.numeric()
        timing_value = (
            TimingClass.PRE_EVENT.value
            if str(event.get("event_date") or "") > capture_day
            else TimingClass.UNKNOWN.value
        )
        event.setdefault("facets", {}).update(numeric)
        event.setdefault("facet_timing", {}).update({key: timing_value for key in numeric})
        event["detail_missing"] = facets.missing
        enriched += 1
        sport_cov = coverage.setdefault(sport, {"events": 0, "missing_fields": 0})
        sport_cov["events"] += 1
        sport_cov["missing_fields"] += len(facets.missing)
    output = root / "data" / "interim" / (Path(events_path).stem + "_detailed.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(events, indent=2, sort_keys=True))
    report = root / "data" / "reports" / "detail_missingness_latest.json"
    report.write_text(json.dumps({
        "events_total": len(events),
        "events_enriched": enriched,
        "coverage": coverage,
        "output": str(output.relative_to(root)),
    }, indent=2, sort_keys=True))
    return output
