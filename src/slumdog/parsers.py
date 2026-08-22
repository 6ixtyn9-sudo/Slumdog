"""Forebet listing parsers for normalized pre-event Slumdog events."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .contracts import EventSnapshot, TimingClass
from .sports import SPORTS

BASE = "https://www.forebet.com"


def _slug(value: str) -> str:
    folded = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.casefold()).strip("-")


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


def _load_football_payload(body: bytes):
    """Load the football JSON payload from either relay-wrapped HTML or raw JSON.

    Direct Forebet access returns raw JSON (starts with '[' or '{'); the relay
    wraps it in HTML. Parsing raw JSON directly avoids BeautifulSoup entity
    mangling of team names/comments.
    """
    stripped = body.lstrip()
    if stripped[:1] in (b"[", b"{"):
        return json.loads(body.decode("utf-8", "replace"))
    soup = BeautifulSoup(body, "html.parser")
    return json.loads(soup.body.get_text() if soup.body else body.decode("utf-8", "replace"))


def _event_day(value: str) -> str | None:
    match = re.search(r"(\d{2})/(\d{2})/(\d{4})", value)
    if not match:
        return None
    return f"{match.group(3)}-{match.group(2)}-{match.group(1)}"


_AMERICAN_ODDS = re.compile(r"(?<![0-9])[+-][0-9]{3,5}(?![0-9])")


def _american_odds_row(row) -> tuple[float | None, float | None] | None:
    """Read the two participant prices from the listing's ``lscrsp`` cells.

    This is intentionally narrower than scanning the entire row: signed
    numbers can also occur in scores, handicaps, or timestamps. A malformed
    or one-sided price returns ``None`` so callers retain a missing value.
    """
    anchors = list(row.select(".lscrsp"))
    tokens: list[str] = []
    for anchor in anchors:
        tokens.extend(_AMERICAN_ODDS.findall(_text(anchor)))
        if len(tokens) >= 2:
            break
    if len(tokens) < 2:
        for anchor in anchors:
            sibling = anchor.find_next_sibling()
            while sibling is not None and len(tokens) < 2:
                tokens.extend(_AMERICAN_ODDS.findall(_text(sibling)))
                sibling = sibling.find_next_sibling()
    if len(tokens) < 2:
        return None
    home, away = (decimal_odds(tokens[0]), decimal_odds(tokens[1]))
    if home is None or away is None:
        return None
    return home, away


def _participant_odds(row, draw_possible: bool) -> tuple[float | None, float | None, list[str]]:
    values = [_text(span) for span in row.select(".haodd span") if _text(span)]
    parsed = [decimal_odds(value) for value in values]
    if draw_possible and len(parsed) >= 3 and parsed[0] is not None and parsed[2] is not None:
        return parsed[0], parsed[2], values
    if not draw_possible and len(parsed) >= 2 and parsed[0] is not None and parsed[1] is not None:
        return parsed[0], parsed[1], values

    # Some boards expose only American prices in .lscrsp; also fall back when
    # the legacy .haodd block exists but contains dashes or one bad token.
    american = _american_odds_row(row)
    if american is not None:
        return american[0], american[1], values
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
    payload = _load_football_payload(body)
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
        detail_url = (
            f"{BASE}/en/football/matches/"
            f"{_slug(row.get('HOST_NAME'))}-{_slug(row.get('GUEST_NAME'))}-{row.get('id')}"
        )
        events.append(
            EventSnapshot(
                event_id=f"football:{row.get('id')}",
                sport="football",
                event_date=target_date,
                captured_at=captured_at,
                source_url=detail_url,
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


def _merge_football_markets(events: list[EventSnapshot], markets_path: Path) -> None:
    """Attach captured extra-market fields without changing the 1X2 target.

    Missing/corrupt sidecars are a degraded capture, not a parser failure.
    Values remain raw facets and are admitted to features only when numeric;
    every admitted facet is explicitly marked pre-event.
    """
    if not markets_path.exists():
        return
    try:
        payload = json.loads(markets_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return
    if not isinstance(payload, list):
        return
    by_id = {
        str(row.get("id")): row
        for row in payload
        if isinstance(row, dict) and row.get("id") not in (None, "")
    }
    from .forebet import FOOTBALL_MARKET_KEYS

    for event in events:
        row = by_id.get(event.event_id.split(":", 1)[-1])
        if row is None:
            continue
        for market, keys in FOOTBALL_MARKET_KEYS.items():
            for key in keys:
                value = row.get(key)
                if value in (None, "", "-"):
                    continue
                facet = f"market_{market}_{key}"
                event.facets[facet] = value
                event.facet_timing[facet] = TimingClass.PRE_EVENT


def parse_capture(metadata: dict, root=".") -> list[EventSnapshot]:
    root = Path(root)
    body = (root / metadata["body_path"]).read_bytes()
    if metadata["sport"] == "football":
        events = parse_football_json(
            body, metadata["target_date"], metadata["captured_at"],
            metadata["source_url"], metadata["sha256"],
        )
        _merge_football_markets(
            events,
            root / "data" / "raw" / "football" / metadata["target_date"] / "markets.json",
        )
        return events
    return parse_html_events(
        body, metadata["sport"], metadata["target_date"], metadata["captured_at"],
        metadata["source_url"], metadata["sha256"],
    )
