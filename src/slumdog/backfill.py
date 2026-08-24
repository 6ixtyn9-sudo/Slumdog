"""Bounded historical capture and settlement accrual."""
from __future__ import annotations

import gzip
import json
import shutil
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

from .clock import yesterday_iso
from .forebet import ForebetCollector
from .settlement import (
    append_settled_from_capture,
    parse_cricket_settled,
    parse_esoccer_settled,
    parse_football_settled,
    parse_html_settled,
    parse_mma_settled,
)
from .sports import HISTORY_STARTS, SPORTS


def date_range(start: str, end: str) -> list[str]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if last < first:
        raise ValueError("end before start")
    days = []
    current = first
    while current <= last:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def backfill(
    start: str | None = None,
    end: str | None = None,
    root: Path | str = ".",
    workers: int = 4,
    delay_seconds: float = 60.0,
) -> Path:
    """Bounded all-sport discovery probe.

    Defaults to the trailing seven days ending yesterday so a bare invocation
    is always a safe, clock-derived probe; explicit dates are overrides.
    """
    root = Path(root)
    end = end or yesterday_iso()
    start = start or (date.fromisoformat(end) - timedelta(days=6)).isoformat()
    days = date_range(start, end)
    collector = ForebetCollector(root, workers=workers)
    history = root / "data" / "interim" / "settled_history.json"
    for index, day in enumerate(days):
        collector.capture_all(day)
        history = append_settled_from_capture(day, root)
        if index + 1 < len(days) and delay_seconds > 0:
            time.sleep(delay_seconds)
    return history


def _parse_settled_body(sport: str, body: bytes, day: str):
    if sport == "football":
        return parse_football_settled(body, day)
    if sport == "mma":
        return parse_mma_settled(body, day)
    if sport == "cricket":
        return parse_cricket_settled(body, day)
    if sport == "esoccer":
        return parse_esoccer_settled(body, day)
    return parse_html_settled(body, sport, day)


def _load_manifest(report_dir: Path, sport: str) -> dict:
    path = report_dir / f"history_{sport}.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict) and isinstance(data.get("daily_receipts"), list):
            return data
    except Exception:
        return {}
    return {}


def _validated_ledger_payloads(rows, sport: str, raw_sha256: str) -> list[dict]:
    """Stamp provenance, collapse exact keys, and reject conflicting facts."""
    payloads: list[dict] = []
    by_key: dict[tuple[str, str, str], dict] = {}
    for row in rows:
        payload = asdict(row)
        payload.setdefault("facets", {})
        if isinstance(payload.get("facets"), dict):
            payload["facets"]["raw_sha256"] = raw_sha256

        event_id = str(payload.get("event_id") or "")
        event_date = str(payload.get("event_date") or "")
        if not event_id:
            payloads.append(payload)
            continue

        key = (sport, event_id, event_date)
        prior = by_key.get(key)
        if prior is None:
            by_key[key] = payload
            payloads.append(payload)
            continue
        if json.dumps(prior, sort_keys=True, separators=(",", ":")) == json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ):
            continue

        differing = sorted(
            field for field in set(prior) | set(payload)
            if prior.get(field) != payload.get(field)
        )
        raise ValueError(
            "conflicting settled rows for "
            f"sport={sport} date={event_date} event_id={event_id}; "
            f"differing_fields={','.join(differing)}"
        )
    return payloads


def _is_empty_day_error(sport: str, exc: BaseException) -> bool:
    """Whether a fetch error means "no games on that date", not a real failure.

    For the HTML-listing sports (everything but football), Forebet serves an
    empty/unprocessable page on out-of-season dates which the relay surfaces as
    an HTTP 422. We treat that as a *covered empty day* so off-season ranges
    don't get retried forever. Football uses the getrs.php JSON endpoint, which
    returns ``[]`` (already handled as zero rows) and never 422s for an empty
    day -- so a 422 there stays a genuine failure and must not be swallowed.
    """
    if sport == "football":
        return False
    return isinstance(exc, urllib.error.HTTPError) and exc.code == 422


def backfill_sport(
    sport: str,
    end: str | None = None,
    root: Path | str = ".",
    start: str | None = None,
    workers: int = 6,
    batch_size: int = 18,
    delay_seconds: float = 62.0,
    keep_raw: bool = True,
) -> Path:
    """Accumulate one sport's dated archive into a rolling compressed ledger.

    The ledger (``history_<sport>.jsonl.gz``) and its manifest
    (``history_<sport>.json``) persist across runs: dates already captured are
    skipped, so a re-dispatch or a scheduled follow-up only fetches the days
    that are actually new. ``end`` defaults to yesterday from the runner clock.

    Raw bodies are retained by default (content-addressed under
    ``data/raw/<sport>/<date>/``). This honors the "freeze every source page"
    contract: parser improvements can be replayed against history without
    re-fetching. Each settled row in the ledger carries its source
    ``raw_sha256`` so a row always points back to the exact bytes parsed.
    """
    if sport not in SPORTS or SPORTS[sport].current_only:
        raise ValueError("sport must have a dated Forebet archive")
    end = end or yesterday_iso()
    start = start or HISTORY_STARTS[sport]
    if start is None:
        raise ValueError(f"no historical start for {sport}")
    days = date_range(start, end)
    root = Path(root)
    collector = ForebetCollector(root, workers=workers)
    report_dir = root / "data" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    history_path = report_dir / f"history_{sport}.jsonl.gz"
    manifest_path = report_dir / f"history_{sport}.json"

    previous = _load_manifest(report_dir, sport)
    receipts = {str(item.get("date")) for item in previous.get("daily_receipts", [])}
    total_rows = int(previous.get("settled_rows") or 0)
    priced_rows = int(previous.get("priced_rows") or 0)
    void_rows = int(previous.get("void_rows") or 0)
    empty_days = int(previous.get("empty_days") or 0)
    failures = list(previous.get("failures") or [])
    manifest = [item for item in previous.get("daily_receipts", []) if isinstance(item, dict)]
    done = {str(item.get("date")) for item in manifest}

    pending = [day for day in days if day not in done]
    # Keep batches and concurrency gentle: the public relay throttles shared
    # datacenter IPs hard (football hit 100% 401s at 18-in-parallel on the
    # runner). Smaller batches + backoff yield higher completion per run.
    safe_batch = max(1, min(int(batch_size), 6))

    raw_bytes_retained = 0
    if pending:
        with gzip.open(history_path, "at", encoding="utf-8") as output:
            for offset in range(0, len(pending), safe_batch):
                batch = pending[offset:offset + safe_batch]
                with ThreadPoolExecutor(max_workers=max(1, min(int(workers), 6))) as executor:
                    futures = {day: executor.submit(collector._fetch, sport, day) for day in batch}
                    for day in batch:
                        try:
                            capture = futures[day].result()
                            body_path = root / capture.body_path
                            rows = _parse_settled_body(sport, body_path.read_bytes(), day)
                            # Validate the complete day before writing anything:
                            # exact source repeats collapse; conflicting facts fail.
                            payloads = _validated_ledger_payloads(rows, sport, capture.sha256)
                            for payload in payloads:
                                output.write(json.dumps(payload, sort_keys=True) + "\n")
                                total_rows += 1
                                priced_rows += (
                                    payload.get("odds_1") is not None
                                    and payload.get("odds_2") is not None
                                )
                                void_rows += payload.get("disposition") == "VOID"
                            settled_rows_written = len(payloads)
                            if keep_raw:
                                raw_bytes_retained += capture.bytes
                            # A valid page with zero settled rows is a covered
                            # (empty) day, not a failure.
                            manifest.append({
                                "date": day, "source_url": capture.source_url,
                                "sha256": capture.sha256, "bytes": capture.bytes,
                                "settled_rows": settled_rows_written,
                                "raw_retained": keep_raw,
                            })
                            if not keep_raw:
                                shutil.rmtree(body_path.parent, ignore_errors=True)
                        except Exception as exc:
                            if _is_empty_day_error(sport, exc):
                                # No listing for this date (e.g. off-season):
                                # record it as covered so it is never retried.
                                empty_days += 1
                                manifest.append({
                                    "date": day,
                                    "source_url": f"https://www.forebet.com/en/{sport}/predictions/{day}",
                                    "empty": True,
                                    "reason": "relay 422: no event listing for date",
                                })
                            else:
                                failures.append(f"{day}:{type(exc).__name__}:{exc}")
                if offset + safe_batch < len(pending) and delay_seconds > 0:
                    time.sleep(delay_seconds)

    # The manifest reports the union of everything covered, not just this run.
    covered_dates = sorted(receipts | set(days))
    manifest_path.write_text(json.dumps({
        "sport": sport, "start": min(covered_dates), "end": max(covered_dates),
        "dates_requested": len(covered_dates), "dates_completed": len(manifest),
        "settled_rows": total_rows, "priced_rows": priced_rows,
        "void_rows": void_rows, "empty_days": empty_days, "failures": failures,
        "raw_bytes_retained_this_run": raw_bytes_retained,
        "keep_raw": keep_raw,
        "history_file": str(history_path.relative_to(root)),
        "daily_receipts": manifest,
    }, indent=2, sort_keys=True))
    return manifest_path
