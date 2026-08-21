"""Post-census analysis: turn raw census + history receipts into research reports.

The depth pipeline produces two artifact families:

- ``depth_sweep_<date>.json`` — per-sport current-board census with
  listing counts, price coverage and detail field presence.
- ``history_<sport>.json`` (+ ``history_<sport>.jsonl.gz``) — rolling,
  resumable per-sport settlement ledgers with a manifest of covered dates.

``analyze_depth`` reads whatever exists (missing artifacts degrade to empty
sections) and writes a dated JSON receipt plus a markdown research report.
"""
from __future__ import annotations

import gzip
import json
from collections import Counter, defaultdict
from pathlib import Path

from .clock import today_iso
from .sports import SPORTS


def _load_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def latest_census(root: Path) -> dict | None:
    candidates = sorted(root.glob("depth_sweep_*.json"))
    return _load_json(candidates[-1]) if candidates else None


def history_manifests(reports_dir: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted(reports_dir.glob("history_*.json")):
        data = _load_json(path)
        sport = (data or {}).get("sport")
        if sport:
            out[sport] = data
    return out


def _stream_ledger(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def ledger_profile(reports_dir: Path, sport: str) -> dict:
    """Aggregate one sport's rolling ledger by season and league."""
    matches = sorted(reports_dir.glob(f"history_{sport}.jsonl.gz"))
    if not matches:
        return {}
    seasons: dict[str, dict[str, int]] = defaultdict(lambda: {"rows": 0, "priced": 0})
    leagues: Counter[str] = Counter()
    rows = priced = void = 0
    for row in _stream_ledger(matches[-1]):
        rows += 1
        has_price = row.get("odds_1") is not None and row.get("odds_2") is not None
        priced += int(has_price)
        void += int(row.get("disposition") == "VOID")
        season = str(row.get("event_date") or "")[:4] or "unknown"
        seasons[season]["rows"] += 1
        seasons[season]["priced"] += int(has_price)
        leagues[str(row.get("league") or "unknown")] += 1
    return {
        "rows": rows,
        "priced_rows": priced,
        "price_coverage": round(priced / rows, 4) if rows else None,
        "void_rows": void,
        "seasons": {season: seasons[season] for season in sorted(seasons)},
        "top_leagues": leagues.most_common(10),
    }


def _top_missing(census_rows: dict, sport: str) -> list[str]:
    """Detail fields with zero presence in the census for this sport."""
    presence = (census_rows.get(sport) or {}).get("field_presence") or {}
    return sorted(key for key, count in presence.items() if count == 0)


def analyze_depth(root: Path | str = ".", target_date: str | None = None) -> Path:
    root = Path(root)
    target_date = target_date or today_iso()
    reports_dir = root / "data" / "reports"

    census = latest_census(reports_dir)
    census_rows = (census or {}).get("rows", {})
    manifests = history_manifests(reports_dir)

    analysis: dict = {
        "target_date": target_date,
        "census_present": census is not None,
        "census": {},
        "history": {},
    }
    for sport in sorted(set(SPORTS) | set(manifests)):
        row = census_rows.get(sport)
        if row:
            entry = dict(row)
            missing = _top_missing(census_rows, sport)
            if missing:
                entry["zero_presence_detail_fields"] = missing
            analysis["census"][sport] = entry
        manifest = manifests.get(sport)
        profile = ledger_profile(reports_dir, sport)
        if manifest or profile:
            analysis["history"][sport] = {
                "manifest": manifest or {},
                "ledger": profile,
            }

    total_rows = sum(
        (h.get("ledger") or {}).get("rows", 0)
        for h in analysis["history"].values()
    )
    total_priced = sum(
        (h.get("ledger") or {}).get("priced_rows", 0)
        for h in analysis["history"].values()
    )
    analysis["summary"] = {
        "sports_censused": len(analysis["census"]),
        "sports_with_history": len(analysis["history"]),
        "history_rows": total_rows,
        "history_priced_rows": total_priced,
        "history_price_coverage": round(total_priced / total_rows, 4) if total_rows else None,
    }

    json_path = reports_dir / f"analysis_{target_date}.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(analysis, indent=2, sort_keys=True))
    md_path = reports_dir / f"analysis_{target_date}.md"
    md_path.write_text(_render_markdown(analysis))
    return md_path


def _render_markdown(analysis: dict) -> str:
    lines = [
        f"# Slumdog Depth Analysis — {analysis['target_date']}",
        "",
        "## Summary",
        "",
    ]
    summary = analysis.get("summary", {})
    lines.append(
        f"- Sports censused: {summary.get('sports_censused', 0)}  |  "
        f"Sports with history ledgers: {summary.get('sports_with_history', 0)}"
    )
    lines.append(
        f"- History rows: {summary.get('history_rows', 0)}  |  Priced: "
        f"{summary.get('history_priced_rows', 0)}  |  Price coverage: "
        f"{summary.get('history_price_coverage') if summary.get('history_price_coverage') is not None else 'n/a'}"
    )
    lines.extend(["", "## Current census", "", "| Sport | Events | Both prices | Price cov | Details OK/req | Enriched | Missing req |",
                  "|---|---:|---:|---:|---:|---:|---:|"])
    for sport, row in analysis.get("census", {}).items():
        price = row.get("price_coverage")
        details = row.get("details_succeeded")
        requested = row.get("details_requested")
        lines.append(
            f"| {sport} | {row.get('listing_events', 0)} | {row.get('both_prices', 0)} | "
            f"{f'{price:.1%}' if price is not None else 'n/a'} | "
            f"{details}/{requested} | {row.get('details_enriched', 0)} | {row.get('missing_required_fields', 0)} |"
        )
    lines.extend(["", "## History ledgers", "",
                  "| Sport | Range | Dates done/req | Rows | Priced | Price cov | Voids | Failures |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|"])
    for sport, h in analysis.get("history", {}).items():
        m = h.get("manifest") or {}
        g = h.get("ledger") or {}
        dates = m.get("dates_completed")
        requested = m.get("dates_requested")
        cov = g.get("price_coverage")
        lines.append(
            f"| {sport} | {m.get('start', '?')} → {m.get('end', '?')} | "
            f"{dates}/{requested} | {g.get('rows', 0)} | {g.get('priced_rows', 0)} | "
            f"{f'{cov:.1%}' if cov is not None else 'n/a'} | {g.get('void_rows', 0)} | "
            f"{len(m.get('failures') or [])} |"
        )
    for sport, h in analysis.get("history", {}).items():
        g = h.get("ledger") or {}
        seasons = g.get("seasons") or {}
        if seasons:
            lines.extend([
                "",
                f"### {sport} — rows by season",
                "",
                "| Season | Rows | Priced |",
                "|---|---:|---:|",
            ])
            for season, counts in seasons.items():
                lines.append(f"| {season} | {counts['rows']} | {counts['priced']} |")
            leagues = g.get("top_leagues") or []
            if leagues:
                lines.extend(["", "Top leagues: " + ", ".join(
                    f"{name} ({count})" for name, count in leagues[:5]
                )])
    for sport, row in analysis.get("census", {}).items():
        zero = row.get("zero_presence_detail_fields")
        if zero:
            lines.append(
                f"\n### {sport} — detail fields with zero presence in census\n\n"
                + ", ".join(zero)
            )
    lines.append("")
    return "\n".join(lines)
