"""Forebet listing parsers for normalized pre-event Slumdog events."""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .contracts import EventSnapshot, TimingClass
from .sports import SPORTS

BASE = "https://www.forebet.com"


def _text(node) -> str:
    return node.get_text(" ", strip=True) if node else ""


def _number(value: str) -> float | None:
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value or "").replace(",", ""))
    return float(match.group()) if match else None


def decimal_odds(value: str) -> float | None:
    number = _number(value)
    if number is None:
        return None
    raw = str(value or "").strip()
    if raw.startswith("+") and number >= 100:
        return 1.0 + number / 100.0
    if raw.startswith("-") and abs(number) >= 100:
        return 1.0 + 100.0 / abs(number)
    return number if number > 1.0 else None


def _event_day(value: str) -> str | None:
    match = re.search(r"(\d{2})/(\d{2})/(\d{4})", value)
    if not match:
        return None
    return f"{match.group(3)}-{match.group(2)}-{match.group(1)}"


def _participant_odds(row, draw_possible: bool) -> tuple[float | None, float | None, list[str]]:
    values = [_text(span) for span in row.select(".haodd span") if _text(span)]
    if not values:
        return None, None, []
    parsed = [decimal_odds(value) for value in values]
    if draw_possible and len(parsed) >= 3:
        return parsed[0], parsed[2], values
    if len(parsed) >= 2:
        return parsed[0], parsed[1], values
    return None, None, values


def parse_html_events(
    body: bytes,
    sport: str,
    target_date: str,
    captured_at: str,
    source_url: str,
    raw_sha256: str = "",
) -> list[EventSnapshot]:
    spec = SPORTS[sport]
    soup = BeautifulSoup(body, "html.parser")
    events: list[EventSnapshot] = []
    for row in soup.select("div.rcnt"):
        link = row.select_one("a.tnmscn")
        p1_node = row.select_one(".homeTeam")
        p2_node = row.select_one(".awayTeam")
        date_node = row.select_one(".date_bah")
        if not link or not p1_node or not p2_node or not date_node:
            continue
        event_day = _event_day(_text(date_node))
        if event_day != target_date:
            continue

        participant_1, participant_2 = _text(p1_node), _text(p2_node)
        href = str(link.get("href") or "")
        expected_path = f"/en/{spec.path}/"
        if expected_path not in href:
            # Sport pages include football featured-match widgets; never let
            # those rows enter another sport's model.
            continue
        event_id = href.rstrip("/").split("/")[-1] or hashlib.sha256(href.encode()).hexdigest()[:16]
        probability_values = [
            _number(_text(span))
            for span in row.select(".fprc span")
            if _number(_text(span)) is not None
        ]
        if spec.draw_possible and len(probability_values) >= 3:
            probability_1 = probability_values[0] / 100.0
            draw_probability = probability_values[1] / 100.0
            probability_2 = probability_values[2] / 100.0
        elif len(probability_values) >= 2:
            probability_1 = probability_values[0] / 100.0
            probability_2 = probability_values[1] / 100.0
            draw_probability = None
        else:
            continue

        pred_text = _text(row.select_one(".forepr span"))
        forebet_pick = int(pred_text) if pred_text in {"1", "2"} else None
        predicted_score = _text(row.select_one(".scrmobpred")) or _text(row.select_one(".ex_sc.tabonly"))
        predicted_total = _number(_text(row.select_one(".avg_sc")))
        odds_1, odds_2, odds_raw = _participant_odds(row, spec.draw_possible)
        league = _text(row.select_one(".shortTag"))
        period_values = [
            [_text(span) for span in cell.select("span")]
            for cell in row.select(".predQ .fj_column")
        ]
        result_text = _text(row.select_one(".lscr_td"))
        # Listing pages can contain finished/live rows. They remain in raw HTML
        # for later settlement parsers but cannot enter the pre-event event set.
        if result_text and re.search(r"\d", result_text):
            continue

        raw_row_text = _text(row)
        facets = {
            "league_code": league,
            "probability_values_raw": probability_values,
            "odds_values_raw": odds_raw,
            "selected_odds_raw": _text(row.select_one(".lscrsp")),
            "period_values": period_values,
            "prediction_cell_text": _text(row.select_one(".predict")),
            "raw_row_text": raw_row_text,
        }
        timing = {
            "league_code": TimingClass.PRE_EVENT,
            "probability_values_raw": TimingClass.PRE_EVENT,
            "odds_values_raw": TimingClass.PRE_EVENT,
            "selected_odds_raw": TimingClass.PRE_EVENT,
            "period_values": TimingClass.PRE_EVENT,
            "prediction_cell_text": TimingClass.PRE_EVENT,
            "raw_row_text": TimingClass.UNKNOWN,
        }
        events.append(
            EventSnapshot(
                event_id=f"{sport}:{event_id}",
                sport=sport,
                event_date=event_day,
                captured_at=captured_at,
                source_url=urljoin(BASE, href),
                participant_1=participant_1,
                participant_2=participant_2,
                probability_1=probability_1,
                probability_2=probability_2,
                draw_probability=draw_probability,
                forebet_pick=forebet_pick,
                odds_1=odds_1,
                odds_2=odds_2,
                league=league,
                kickoff=_text(date_node),
                predicted_score=predicted_score,
                predicted_total=predicted_total,
                raw_sha256=raw_sha256,
                facets=facets,
                facet_timing=timing,
            )
        )
    return events


def parse_football_json(
    body: bytes,
    target_date: str,
    captured_at: str,
    source_url: str,
    raw_sha256: str = "",
) -> list[EventSnapshot]:
    soup = BeautifulSoup(body, "html.parser")
    payload = json.loads(soup.body.get_text() if soup.body else body.decode("utf-8", "replace"))
    if not (isinstance(payload, list) and payload and isinstance(payload[0], list)):
        raise ValueError("unexpected football JSON shape")
    events = []
    result_keys = {"Host_SC", "Guest_SC", "Host_SC_HT", "Guest_SC_HT", "comment"}
    for row in payload[0]:
        if not isinstance(row, dict):
            continue
        if str(row.get("DATE_BAH") or "")[:10] != target_date:
            continue
        if row.get("Host_SC") not in (None, "") or str(row.get("comment") or "").upper() in {"FT", "LIVE"}:
            continue
        p1 = _number(row.get("Pred_1"))
        px = _number(row.get("Pred_X"))
        p2 = _number(row.get("Pred_2"))
        if p1 is None or p2 is None:
            continue
        draw = px / 100.0 if px is not None else None
        probs = {1: p1, 2: p2, 0: px if px is not None else -1}
        best = max(probs, key=probs.get)
        timing = {
            key: (TimingClass.RESULT_ONLY if key in result_keys else TimingClass.PRE_EVENT)
            for key in row
        }
        events.append(
            EventSnapshot(
                event_id=f"football:{row.get('id')}",
                sport="football",
                event_date=target_date,
                captured_at=captured_at,
                source_url=source_url,
                participant_1=str(row.get("HOST_NAME") or ""),
                participant_2=str(row.get("GUEST_NAME") or ""),
                probability_1=p1 / 100.0,
                probability_2=p2 / 100.0,
                draw_probability=draw,
                forebet_pick=best if best in (1, 2) else None,
                odds_1=decimal_odds(str(row.get("best_odd_1") or "")),
                odds_2=decimal_odds(str(row.get("best_odd_2") or "")),
                league=str(row.get("short_tag") or ""),
                kickoff=str(row.get("DATE_BAH") or ""),
                predicted_score=f"{row.get('host_sc_pr', '')}-{row.get('guest_sc_pr', '')}",
                predicted_total=_number(row.get("goalsavg")),
                raw_sha256=raw_sha256,
                facets=dict(row),
                facet_timing=timing,
            )
        )
    return events


def parse_capture(metadata: dict, root=".") -> list[EventSnapshot]:
    from pathlib import Path

    body = (Path(root) / metadata["body_path"]).read_bytes()
    if metadata["sport"] == "football":
        return parse_football_json(
            body, metadata["target_date"], metadata["captured_at"],
            metadata["source_url"], metadata["sha256"],
        )
    return parse_html_events(
        body, metadata["sport"], metadata["target_date"], metadata["captured_at"],
        metadata["source_url"], metadata["sha256"],
    )
