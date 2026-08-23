"""Sport-correct settled-row extraction from frozen Forebet listings."""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path

from bs4 import BeautifulSoup

from .contracts import SettledEvent
from .parsers import _event_day, _load_football_payload, _number, _participant_odds, _slug, _text
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
    payload = _load_football_payload(body)
    rows = payload[0] if isinstance(payload, list) and payload and isinstance(payload[0], list) else []
    settled = []
    for row in rows:
        if str(row.get("DATE_BAH") or "")[:10] != target_date:
            continue
        comment = str(row.get("comment") or "").upper()
        if any(tok in comment for tok in ("CANCL", "POSTP", "ABAND", "INT")):
            disposition = "VOID"
            score_1, score_2 = None, None
            winner = 0
        else:
            try:
                score_1, score_2 = float(row["Host_SC"]), float(row["Guest_SC"])
                winner = 1 if score_1 > score_2 else 2 if score_2 > score_1 else 0
                disposition = "SETTLED"
            except (KeyError, TypeError, ValueError):
                continue
        p1, p2 = _number(row.get("Pred_1")), _number(row.get("Pred_2"))
        if p1 is None or p2 is None:
            continue
        px = _number(row.get("Pred_X"))
        best = max({1: p1, 2: p2, 0: px or -1}, key={1: p1, 2: p2, 0: px or -1}.get)
        
        periods_1: list[float] = []
        periods_2: list[float] = []
        ht_1, ht_2 = _number(row.get("Host_SC_HT")), _number(row.get("Guest_SC_HT"))
        if ht_1 is not None and ht_2 is not None and score_1 is not None and score_2 is not None:
            periods_1 = [ht_1, score_1]
            periods_2 = [ht_2, score_2]

        host_name = str(row.get("HOST_NAME") or "")
        guest_name = str(row.get("GUEST_NAME") or "")
        row_id = str(row.get("id") or "")
        detail_url = (
            f"https://www.forebet.com/en/football/matches/{_slug(host_name)}-{_slug(guest_name)}-{row_id}"
            if (host_name and guest_name and row_id) else ""
        )

        settled.append(SettledEvent(
            event_id=f"football:{row_id}", sport="football", event_date=target_date,
            participant_1=host_name, participant_2=guest_name,
            winner_index=winner, score_1=score_1, score_2=score_2,
            probability_1=p1/100, probability_2=p2/100,
            draw_probability=px/100 if px is not None else None,
            forebet_pick=best if best in (1, 2) else None,
            odds_1=_number(row.get("best_odd_1")), odds_2=_number(row.get("best_odd_2")),
            league=str(row.get("short_tag") or ""),
            period_scores_1=tuple(periods_1), period_scores_2=tuple(periods_2),
            source_url=detail_url, disposition=disposition,
        ))
    return settled


def _identity(value: str) -> str:
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


def _base_row(row, sport: str, target_date: str):
    link = row.select_one("a.tnmscn")
    p1_node, p2_node = row.select_one(".homeTeam"), row.select_one(".awayTeam")
    date_node = row.select_one(".date_bah") or row.select_one(".dtrange")
    if not link or not p1_node or not p2_node or not date_node:
        return None
    date_text = _text(date_node)
    if target_date not in date_text and _event_day(date_text) != target_date:
        # Multi-day cricket ranges end with DD/MM/YYYY rather than ISO.
        expected = f"{target_date[8:10]}/{target_date[5:7]}/{target_date[:4]}"
        if expected not in date_text:
            return None
    probs = [_number(_text(span)) for span in row.select(".fprc span")]
    probs = [value for value in probs if value is not None]
    spec = SPORTS[sport]
    if spec.draw_possible and len(probs) >= 3:
        probability_1, draw_probability, probability_2 = probs[0]/100, probs[1]/100, probs[2]/100
    elif len(probs) >= 2:
        probability_1, probability_2, draw_probability = probs[0]/100, probs[1]/100, None
    else:
        return None
    pred = _text(row.select_one(".forepr span"))
    forebet_pick = int(pred) if pred in {"1", "2"} else None
    odds_1, odds_2, _ = _participant_odds(row, spec.draw_possible)
    href = str(link.get("href") or "")
    return {
        "event_id": f"{sport}:{href.rstrip('/').split('/')[-1]}",
        "participant_1": _text(p1_node), "participant_2": _text(p2_node),
        "probability_1": probability_1, "probability_2": probability_2,
        "draw_probability": draw_probability, "forebet_pick": forebet_pick,
        "odds_1": odds_1, "odds_2": odds_2,
        "league": _text(row.select_one(".shortTag")), "source_url": href,
    }


def parse_mma_settled(body: bytes, target_date: str) -> list[SettledEvent]:
    soup = BeautifulSoup(body, "html.parser")
    settled = []
    for row in soup.select("div.rcnt"):
        base = _base_row(row, "mma", target_date)
        if not base:
            continue
        result_text = _text(row.select_one(".lscr_td"))
        if not result_text:
            continue
        lowered = result_text.casefold()
        if any(token in lowered for token in ("cancl", "no contest", "abandon")):
            winner, disposition = 0, "VOID"
        else:
            winner_name = _text(row.select_one(".lscr_td .oltrpy"))
            if _identity(winner_name) == _identity(base["participant_1"]):
                winner = 1
            elif _identity(winner_name) == _identity(base["participant_2"]):
                winner = 2
            else:
                continue
            disposition = "SETTLED"
        settled.append(SettledEvent(
            **base, sport="mma", event_date=target_date, winner_index=winner,
            score_1=None, score_2=None, disposition=disposition,
        ))
    return settled


def parse_esoccer_settled(body: bytes, target_date: str) -> list[SettledEvent]:
    soup = BeautifulSoup(body, "html.parser")
    settled = []
    for row in soup.select("div.rcnt"):
        base = _base_row(row, "esoccer", target_date)
        if not base:
            continue
        match = re.search(r"(\d+)\s*[-:]\s*(\d+)", _text(row.select_one(".lscr_td")))
        status = _text(row.select_one(".scoreLnk")).upper()
        if not match or status != "FT":
            continue
        score_1, score_2 = float(match.group(1)), float(match.group(2))
        winner = 1 if score_1 > score_2 else 2 if score_2 > score_1 else 0
        settled.append(SettledEvent(
            **base, sport="esoccer", event_date=target_date, winner_index=winner,
            score_1=score_1, score_2=score_2,
        ))
    return settled


def parse_cricket_settled(body: bytes, target_date: str) -> list[SettledEvent]:
    soup = BeautifulSoup(body, "html.parser")
    settled = []
    for row in soup.select("div.rcnt"):
        base = _base_row(row, "cricket", target_date)
        if not base:
            continue
        comment = _text(row.select_one(".crftcomm"))
        if not comment:
            continue
        lowered = comment.casefold()
        if "no result" in lowered or "abandon" in lowered or "cancel" in lowered:
            winner, disposition = 0, "VOID"
        elif "draw" in lowered:
            winner, disposition = 0, "SETTLED_DRAW"
        else:
            p1, p2 = _identity(base["participant_1"]), _identity(base["participant_2"])
            lead = _identity(re.split(r"\bwon\b", comment, flags=re.I)[0])
            if lead and (p1.startswith(lead) or lead.startswith(p1)):
                winner = 1
            elif lead and (p2.startswith(lead) or lead.startswith(p2)):
                winner = 2
            else:
                # Forebet bolds the winning innings when the textual team name
                # is abbreviated beyond reliable identity matching.
                spans = row.select(".lscr_td span")
                winner = 1 if spans and spans[0].find("b") else 2 if len(spans) > 1 and spans[1].find("b") else -1
                if winner == -1:
                    continue
            disposition = "SETTLED"
        settled.append(SettledEvent(
            **base, sport="cricket", event_date=target_date, winner_index=winner,
            score_1=None, score_2=None, disposition=disposition,
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
            sport = metadata["sport"]
            if sport == "football":
                rows.extend(parse_football_settled(body, target_date))
            elif sport == "mma":
                rows.extend(parse_mma_settled(body, target_date))
            elif sport == "cricket":
                rows.extend(parse_cricket_settled(body, target_date))
            elif sport == "esoccer":
                rows.extend(parse_esoccer_settled(body, target_date))
            else:
                rows.extend(parse_html_settled(body, sport, target_date))
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
