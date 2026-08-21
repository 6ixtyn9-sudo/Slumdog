"""Resumable, rate-bounded Forebet match-detail capture and facet audit."""
from __future__ import annotations

import hashlib
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .clock import today_iso
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


def capture_stratified_details(
    events_path: Path | str,
    root: Path | str = ".",
    per_sport: int = 3,
    workers: int = 4,
    batch_size: int = 18,
    delay_seconds: float = 62.0,
) -> Path:
    """Capture balanced details for every sport in relay-safe batches."""
    root = Path(root)
    events = json.loads(Path(events_path).read_text())
    by_sport: dict[str, list[tuple]] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        url = str(event.get("source_url") or "")
        if "/matches/" not in url:
            continue
        sport, event_id = str(event.get("sport")), str(event.get("event_id"))
        path = _detail_path(root, sport, event_id)
        if path.exists():
            continue
        p1 = float(event.get("probability_1") or 0)
        p2 = float(event.get("probability_2") or 0)
        dog_probability = min(p1, p2)
        by_sport.setdefault(sport, []).append((-dog_probability, event_id, url, path))

    selected = []
    for sport in sorted(by_sport):
        for priority, event_id, url, path in sorted(by_sport[sport])[:max(0, int(per_sport))]:
            selected.append((priority, sport, event_id, url, path))

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

    results = []
    safe_batch = max(1, min(int(batch_size), 18))
    for start in range(0, len(selected), safe_batch):
        batch = selected[start:start + safe_batch]
        with ThreadPoolExecutor(max_workers=max(1, min(int(workers), 6))) as executor:
            results.extend(executor.map(fetch, batch))
        if start + safe_batch < len(selected) and delay_seconds > 0:
            time.sleep(delay_seconds)

    report = root / "data" / "reports" / "detail_capture_latest.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "stratified",
        "per_sport": int(per_sport),
        "requested": len(selected),
        "available_by_sport": {sport: len(rows) for sport, rows in by_sport.items()},
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
    capture_day = today_iso()
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
        sport_cov = coverage.setdefault(
            sport, {"events": 0, "missing_fields": 0, "field_presence": {}}
        )
        sport_cov["events"] += 1
        sport_cov["missing_fields"] += len(facets.missing)
        for key in numeric:
            sport_cov["field_presence"][key] = sport_cov["field_presence"].get(key, 0) + 1
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
