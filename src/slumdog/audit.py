"""Fail-closed, per-sport history completeness receipts.

The receipt is deliberately separate from model research. It audits the
persisted ledger and its manifest, counts malformed/duplicate rows, checks
actual date gaps, and only applies the odds floor to sports whose archived
listing is verified to carry participant prices. Current-only sports are
reported as such rather than being treated as failed historical backfills.
"""
from __future__ import annotations

import gzip
import json
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .clock import today_iso
from .sports import HISTORY_STARTS, SPORTS

# Verified by the surface inventory: archived participant prices exist for
# football's JSON 1X2, and tennis/baseball listing rows. Basketball and MMA
# display '-' in the archived listing and must not be judged on price coverage.
PRICED_SPORTS = frozenset({"football", "tennis", "baseball"})
MIN_ROWS = 100
MIN_PRICE_COVERAGE = 0.05
MIN_DATE_COVERAGE = 0.60


class AuditGateError(RuntimeError):
    """Raised after a receipt has been written when a historical gate fails."""

    def __init__(self, path: Path):
        self.path = path
        super().__init__(f"audit gate failed; see {path}")


def _date_range(start: str, end: str) -> list[str]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if last < first:
        return []
    return [
        (first + timedelta(days=offset)).isoformat()
        for offset in range((last - first).days + 1)
    ]


def _ledger_stats(path: Path) -> dict[str, Any]:
    rows = 0
    priced = 0
    voids = 0
    periods = 0
    malformed = 0
    duplicate_ids = 0
    seen: set[str] = set()
    leagues: Counter[str] = Counter()
    dates: set[str] = set()
    if not path.exists():
        return {
            "rows": 0, "priced": 0, "voids": 0, "periods": 0,
            "malformed_lines": 0, "duplicate_event_ids": 0,
            "leagues": 0, "dates": [],
        }
    try:
        handle = gzip.open(path, "rt", encoding="utf-8")
    except OSError:
        return {
            "rows": 0, "priced": 0, "voids": 0, "periods": 0,
            "malformed_lines": 1, "duplicate_event_ids": 0,
            "leagues": 0, "dates": [],
        }
    try:
        with handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except (TypeError, ValueError):
                    malformed += 1
                    continue
                if not isinstance(row, dict):
                    malformed += 1
                    continue
                event_id = str(row.get("event_id") or "")
                if event_id and event_id in seen:
                    duplicate_ids += 1
                if event_id:
                    seen.add(event_id)
                rows += 1
                if row.get("odds_1") is not None and row.get("odds_2") is not None:
                    priced += 1
                if row.get("disposition") == "VOID":
                    voids += 1
                if row.get("period_scores_1") or row.get("period_scores_2"):
                    periods += 1
                if row.get("league"):
                    leagues[str(row["league"])] += 1
                if row.get("event_date"):
                    dates.add(str(row["event_date"]))
    except (OSError, EOFError):
        malformed += 1
    return {
        "rows": rows, "priced": priced, "voids": voids, "periods": periods,
        "malformed_lines": malformed, "duplicate_event_ids": duplicate_ids,
        "leagues": len(leagues), "dates": sorted(dates),
    }


def _manifest_int(manifest: dict[str, Any], key: str, issues: list[str]) -> int:
    value = manifest.get(key)
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        issues.append(f"manifest {key} invalid")
        return 0


def _load_manifest(path: Path) -> tuple[dict[str, Any], list[str]]:
    if not path.exists():
        return {}, ["manifest missing"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}, ["manifest unreadable"]
    if not isinstance(payload, dict):
        return {}, ["manifest is not an object"]
    return payload, []


def sport_receipt(reports_dir: Path | str, sport: str) -> dict[str, Any]:
    reports_dir = Path(reports_dir)
    ledger = reports_dir / f"history_{sport}.jsonl.gz"
    manifest_path = reports_dir / f"history_{sport}.json"
    manifest, issues = _load_manifest(manifest_path)
    stats = _ledger_stats(ledger)
    current_only = HISTORY_STARTS.get(sport) is None or SPORTS[sport].current_only
    if current_only and not manifest_path.exists() and not ledger.exists():
        # No historical artifact is expected for a current-only board.
        issues = []

    start = str(manifest.get("start") or HISTORY_STARTS.get(sport) or "")
    end = str(manifest.get("end") or "")
    try:
        expected_dates = _date_range(start, end) if start and end else []
    except ValueError:
        expected_dates = []
        issues.append("manifest date range invalid")
    daily = manifest.get("daily_receipts")
    completed_dates = {
        str(item.get("date"))
        for item in daily
        if isinstance(item, dict) and item.get("date")
    } if isinstance(daily, list) else set()
    missing_dates = sorted(set(expected_dates) - completed_dates)
    requested = len(expected_dates) or _manifest_int(manifest, "dates_requested", issues)
    completed = len(completed_dates) or _manifest_int(manifest, "dates_completed", issues)
    date_coverage = round(completed / requested, 4) if requested else 0.0
    price_coverage = round(stats["priced"] / stats["rows"], 4) if stats["rows"] else 0.0

    if stats["malformed_lines"]:
        issues.append(f"malformed ledger lines: {stats['malformed_lines']}")
    if stats["duplicate_event_ids"]:
        issues.append(f"duplicate event ids: {stats['duplicate_event_ids']}")
    if manifest and "settled_rows" in manifest:
        if _manifest_int(manifest, "settled_rows", issues) != stats["rows"]:
            issues.append("manifest settled_rows disagrees with ledger")
    if manifest and "priced_rows" in manifest:
        if _manifest_int(manifest, "priced_rows", issues) != stats["priced"]:
            issues.append("manifest priced_rows disagrees with ledger")
    if missing_dates and not current_only:
        issues.append(f"missing dates: {len(missing_dates)}")

    gate_issues = list(issues)
    if not current_only:
        if stats["rows"] < MIN_ROWS:
            gate_issues.append(f"rows {stats['rows']} < {MIN_ROWS}")
        if requested and date_coverage < MIN_DATE_COVERAGE:
            gate_issues.append(
                f"date coverage {date_coverage:.1%} < {MIN_DATE_COVERAGE:.0%}"
            )
        if sport in PRICED_SPORTS and price_coverage < MIN_PRICE_COVERAGE:
            gate_issues.append(
                f"price coverage {price_coverage:.1%} < {MIN_PRICE_COVERAGE:.0%}"
            )

    if current_only and not issues:
        status = "CURRENT_ONLY"
    elif gate_issues:
        status = "BELOW_FLOOR"
    else:
        status = "OK"
    return {
        "sport": sport,
        "current_only": current_only,
        "rows": stats["rows"],
        "priced": stats["priced"],
        "price_coverage": price_coverage,
        "voids": stats["voids"],
        "period_score_rows": stats["periods"],
        "leagues": stats["leagues"],
        "malformed_lines": stats["malformed_lines"],
        "duplicate_event_ids": stats["duplicate_event_ids"],
        "dates_completed": completed,
        "dates_requested": requested,
        "date_coverage": date_coverage,
        "missing_dates": missing_dates[:100],
        "start": start,
        "end": end,
        "status": status,
        "issues": gate_issues,
    }


def build_audit(
    root: Path | str = ".",
    target_date: str | None = None,
    fail_on_gate: bool = True,
) -> Path:
    root = Path(root)
    target_date = target_date or today_iso()
    receipts = {
        sport: sport_receipt(root / "data" / "reports", sport)
        for sport in sorted(SPORTS)
    }
    failing = [
        sport for sport, receipt in receipts.items()
        if receipt["status"] == "BELOW_FLOOR"
    ]
    audit = {
        "target_date": target_date,
        "thresholds": {
            "min_rows": MIN_ROWS,
            "min_price_coverage": MIN_PRICE_COVERAGE,
            "min_date_coverage": MIN_DATE_COVERAGE,
            "priced_sports": sorted(PRICED_SPORTS),
        },
        "sports": receipts,
        "below_floor": failing,
        "verdict": "PASS" if not failing else "FAIL",
    }
    reports_dir = root / "data" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_path = reports_dir / f"audit_{target_date}.json"
    json_path.write_text(json.dumps(audit, indent=2, sort_keys=True))
    md_path = reports_dir / f"audit_{target_date}.md"
    md_path.write_text(_render_markdown(audit), encoding="utf-8")
    if fail_on_gate and failing:
        raise AuditGateError(md_path)
    return md_path


def _render_markdown(audit: dict[str, Any]) -> str:
    lines = [
        f"# Slumdog Data Completeness Audit — {audit['target_date']}",
        "",
        f"Verdict: **{audit['verdict']}** (below floor: {audit['below_floor'] or 'none'})",
        "",
        "| Sport | Mode | Rows | Priced | Price cov | Dates done/req | Missing | Malformed | Duplicates | Status |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for sport, receipt in audit["sports"].items():
        price = "n/a" if sport not in PRICED_SPORTS else f"{receipt['price_coverage']:.1%}"
        mode = "current-only" if receipt["current_only"] else "history"
        lines.append(
            f"| {sport} | {mode} | {receipt['rows']} | {receipt['priced']} | {price} | "
            f"{receipt['dates_completed']}/{receipt['dates_requested']} | "
            f"{len(receipt['missing_dates'])} | {receipt['malformed_lines']} | "
            f"{receipt['duplicate_event_ids']} | {receipt['status']} |"
        )
    return "\n".join(lines) + "\n"
