"""Sport-correct settled-row extraction from frozen Forebet listings."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from bs4 import BeautifulSoup

from .contracts import SettledEvent
from .parsers import _event_day, _number, _participant_odds, _text
from .sports import SPORTS


def parse_html_settled(body: bytes, sport: str, target_date: str) -> list[SettledEvent]:
    spec = SPORTS[sport]
    soup = BeautifulSoup(body, "html.parser")
    settled: list[SettledEvent] = []
    for row in soup.select("div.rcnt"):
        link = row.select_one("a.tnmscn")
        p1_node, p2_node = row.select_one(".homeTeam"), row.select_one(".awayTeam")
        date_node = row.select_one(".date_bah")
        if not link or not p1_node or not p2_node or not date_node:
            continue
        if _event_day(_text(date_node)) != target_date:
            continue
        score_values = [
            _number(_text(span)) for span in row.select(".lscr_td span")
            if _number(_text(span)) is not None
        ]
        status = _text(row.select_one(".scoreLnk"))
        if len(score_values) < 2 or status.upper() not in {"FT", "AOT", "AP", "FINAL"}:
            continue
        score_1, score_2 = score_values[0], score_values[1]
        winner = 1 if score_1 > score_2 else 2 if score_2 > score_1 else 0
        probs = [_number(_text(span)) for span in row.select(".fprc span")]
        probs = [value for value in probs if value is not None]
        if spec.draw_possible and len(probs) >= 3:
            probability_1, draw_probability, probability_2 = probs[0] / 100, probs[1] / 100, probs[2] / 100
        elif len(probs) >= 2:
            probability_1, probability_2, draw_probability = probs[0] / 100, probs[1] / 100, None
        else:
            continue
        pred = _text(row.select_one(".forepr span"))
        forebet_pick = int(pred) if pred in {"1", "2"} else None
        odds_1, odds_2, _ = _participant_odds(row, spec.draw_possible)
        href = str(link.get("href") or "")
        event_id = href.rstrip("/").split("/")[-1]
        periods_1 = []
        periods_2 = []
        for cell in row.select(".predQ .fj_column"):
            values = [_number(_text(span)) for span in cell.select("span")]
            if len(values) >= 2 and values[0] is not None and values[1] is not None:
                periods_1.append(values[0])
                periods_2.append(values[1])
        settled.append(SettledEvent(
            event_id=f"{sport}:{event_id}", sport=sport, event_date=target_date,
            participant_1=_text(p1_node), participant_2=_text(p2_node),
            winner_index=winner, score_1=score_1, score_2=score_2,
            probability_1=probability_1, probability_2=probability_2,
            draw_probability=draw_probability, forebet_pick=forebet_pick,
            odds_1=odds_1, odds_2=odds_2, league=_text(row.select_one(".shortTag")),
            period_scores_1=tuple(periods_1), period_scores_2=tuple(periods_2),
            source_url=href,
        ))
    return settled


def parse_football_settled(body: bytes, target_date: str) -> list[SettledEvent]:
    soup = BeautifulSoup(body, "html.parser")
    payload = json.loads(soup.body.get_text() if soup.body else body.decode("utf-8", "replace"))
    rows = payload[0] if isinstance(payload, list) and payload and isinstance(payload[0], list) else []
    settled = []
    for row in rows:
        if str(row.get("DATE_BAH") or "")[:10] != target_date:
            continue
        try:
            score_1, score_2 = float(row["Host_SC"]), float(row["Guest_SC"])
        except (KeyError, TypeError, ValueError):
            continue
        p1, p2 = _number(row.get("Pred_1")), _number(row.get("Pred_2"))
        if p1 is None or p2 is None:
            continue
        px = _number(row.get("Pred_X"))
        winner = 1 if score_1 > score_2 else 2 if score_2 > score_1 else 0
        best = max({1: p1, 2: p2, 0: px or -1}, key={1: p1, 2: p2, 0: px or -1}.get)
        settled.append(SettledEvent(
            event_id=f"football:{row.get('id')}", sport="football", event_date=target_date,
            participant_1=str(row.get("HOST_NAME") or ""), participant_2=str(row.get("GUEST_NAME") or ""),
            winner_index=winner, score_1=score_1, score_2=score_2,
            probability_1=p1/100, probability_2=p2/100,
            draw_probability=px/100 if px is not None else None,
            forebet_pick=best if best in (1, 2) else None,
            odds_1=_number(row.get("best_odd_1")), odds_2=_number(row.get("best_odd_2")),
            league=str(row.get("short_tag") or ""), source_url="",
        ))
    return settled


def append_settled_from_capture(target_date: str, root: Path | str = ".") -> Path:
    """Parse settled rows from a frozen capture and append unique facts."""
    root = Path(root)
    receipt = json.loads((root / "data" / "reports" / f"capture_{target_date}.json").read_text())
    rows: list[SettledEvent] = []
    failures: list[str] = []
    for metadata in receipt.get("captured", []):
        try:
            body = (root / metadata["body_path"]).read_bytes()
            if metadata["sport"] == "football":
                rows.extend(parse_football_settled(body, target_date))
            else:
                rows.extend(parse_html_settled(body, metadata["sport"], target_date))
        except Exception as exc:
            failures.append(f"{metadata.get('sport')}:{type(exc).__name__}:{exc}")
    path = root / "data" / "interim" / "settled_history.json"
    existing = []
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except Exception:
            existing = []
    seen = {
        (str(row.get("sport")), str(row.get("event_id")), str(row.get("event_date")))
        for row in existing if isinstance(row, dict)
    }
    for row in rows:
        payload = asdict(row)
        key = (row.sport, row.event_id, row.event_date)
        if key not in seen:
            existing.append(payload)
            seen.add(key)
    existing.sort(key=lambda row: (row.get("event_date", ""), row.get("sport", ""), row.get("event_id", "")))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, indent=2, sort_keys=True))
    report = root / "data" / "reports" / f"settlement_{target_date}.json"
    report.write_text(json.dumps({
        "date": target_date,
        "parsed": len(rows),
        "total_history": len(existing),
        "failures": failures,
    }, indent=2, sort_keys=True))
    return path
