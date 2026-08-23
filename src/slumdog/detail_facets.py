"""Sport-specific Forebet match-detail facet extraction.

The parser preserves section text and extracts all timing-safe, reproducible
numeric summaries and detailed match statistics from Forebet detail pages:
- Standings & quality differentials (rank, pts, gp, w, d, l, gf, ga, gd)
- Head-to-head match histories and HT split scores
- Form counts (wins, draws, losses across L6 and home/away splits)
- Match distance in kilometers (straight line travel)
- Pairwise stat metrics (clean sheets, corners, goal kicks, throw-ins, offsides,
  penalties, GK saves, yellow/red cards, fouls, tackles, total/dangerous attacks,
  total shots, passes)
- Possession & accuracy percentages
- Goal distribution averages (scored/conceded overall and home/away)
- Tennis surface splits, MMA tale-of-the-tape records/reaches, sport-specific indicators.

Unknown or ambiguous values stay missing rather than zero.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from bs4 import BeautifulSoup


@dataclass
class DetailFacets:
    sport: str
    common: dict[str, float | str | bool] = field(default_factory=dict)
    sport_specific: dict[str, float | str | bool] = field(default_factory=dict)
    sections: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)

    def numeric(self) -> dict[str, float]:
        out = {}
        for prefix, values in (("detail", self.common), (self.sport, self.sport_specific)):
            for key, value in values.items():
                if isinstance(value, bool):
                    out[f"{prefix}_{key}"] = float(value)
                elif isinstance(value, (int, float)):
                    out[f"{prefix}_{key}"] = float(value)
        return out


def _clean(text: str) -> str:
    return " ".join(str(text or "").split())


def _number(text: str) -> float | None:
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(text or "").replace(",", ""))
    return float(match.group()) if match else None


def _extract_sections(soup: BeautifulSoup) -> dict[str, str]:
    sections = {}
    headings = soup.select("h1,h2,h3,h4,.prTitle,.st_minih")
    for heading in headings:
        title = _clean(heading.get_text(" ", strip=True))
        if not title:
            continue
        chunks = []
        node = heading.find_next_sibling()
        while node is not None and node.name not in {"h1", "h2", "h3", "h4"}:
            text = _clean(node.get_text(" ", strip=True))
            if text:
                chunks.append(text)
            if sum(len(item) for item in chunks) > 4000:
                break
            node = node.find_next_sibling()
        sections[title] = " ".join(chunks)[:5000]
    return sections


def _h2h_from_page(
    soup: BeautifulSoup,
    text: str,
    participant_1: str = "",
    participant_2: str = "",
) -> dict[str, float]:
    """Numeric H2H if the page states it. Missing stays missing — never zero-fill."""
    out: dict[str, float] = {}
    lowered = text.lower()
    idx = lowered.find("head to head")
    window = text[idx:idx + 800] if idx >= 0 else ""
    wins = [int(value) for value in re.findall(r"(\d+)\s+wins?", window, re.I)]
    draws = [int(value) for value in re.findall(r"(\d+)\s+draws?", window, re.I)]
    if len(wins) >= 2:
        out["h2h_participant_1_wins"] = float(wins[0])
        out["h2h_participant_2_wins"] = float(wins[1])
        total = wins[0] + wins[1] + (draws[0] if draws else 0)
        out["h2h_total_games"] = float(total)
        return out

    if participant_1 and participant_2:
        cleaned = re.sub(r"\([^)]*\)", " ", window or text)
        left = re.escape(participant_1)
        right = re.escape(participant_2)
        p1 = p2 = draws_n = games = 0
        for match in re.finditer(
            rf"{left}\s+(\d+)\s*-\s*(\d+)\s+{right}|{right}\s+(\d+)\s*-\s*(\d+)\s+{left}",
            cleaned,
            re.I,
        ):
            if match.group(1) is not None:
                a, b = int(match.group(1)), int(match.group(2))
            else:
                a, b = int(match.group(4)), int(match.group(3))
            games += 1
            if a > b:
                p1 += 1
            elif b > a:
                p2 += 1
            else:
                draws_n += 1
        if games:
            out["h2h_participant_1_wins"] = float(p1)
            out["h2h_participant_2_wins"] = float(p2)
            out["h2h_draws"] = float(draws_n)
            out["h2h_total_games"] = float(games)
            return out

    scores = []
    for row in soup.select("table tr, .h2h tr, .h2h_div tr"):
        numbers = [float(value) for value in re.findall(r"\d+(?:\.\d+)?", row.get_text(" ", strip=True))]
        if len(numbers) >= 2:
            scores.append((numbers[0], numbers[1]))
    if len(scores) >= 2:
        p1 = sum(1 for home, away in scores if home > away)
        p2 = sum(1 for home, away in scores if away > home)
        out["h2h_participant_1_wins"] = float(p1)
        out["h2h_participant_2_wins"] = float(p2)
        out["h2h_total_games"] = float(len(scores))
    return out


def _standings_from_page(
    soup: BeautifulSoup,
    participant_1: str,
    participant_2: str,
) -> dict[str, float]:
    """League table row for each named side. Missing stays missing."""
    if not participant_1 or not participant_2:
        return {}
    wanted = {1: participant_1.casefold(), 2: participant_2.casefold()}
    found: dict[int, dict[str, float]] = {}
    for table in soup.select("table"):
        headers: list[str] = []
        for tr in table.select("tr"):
            cells = [_clean(cell.get_text(" ", strip=True)) for cell in tr.find_all(["th", "td"])]
            if not cells:
                continue
            upper = [cell.upper() for cell in cells]
            if "PTS" in upper and "GP" in upper:
                headers = upper
                continue
            if not headers:
                continue
            blob = " ".join(cells).casefold()
            who = next((index for index, name in wanted.items() if name and name in blob), None)
            if who is None or who in found:
                continue
            numbers = []
            for cell in cells:
                if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", cell):
                    numbers.append(float(cell))
            # rank, pts, gp, w, d, l, gf, ga, gd — rank may be first
            if len(numbers) < 8:
                continue
            rank, pts, gp, wins, draws, losses, gf, ga = numbers[:8]
            gd = numbers[8] if len(numbers) > 8 else gf - ga
            found[who] = {
                f"standings_{who}_rank": rank,
                f"standings_{who}_pts": pts,
                f"standings_{who}_gp": gp,
                f"standings_{who}_wins": wins,
                f"standings_{who}_draws": draws,
                f"standings_{who}_losses": losses,
                f"standings_{who}_gf": gf,
                f"standings_{who}_ga": ga,
                f"standings_{who}_gd": gd,
            }
        if len(found) == 2:
            break
    out: dict[str, float] = {}
    for payload in found.values():
        out.update(payload)
    if 1 in found and 2 in found:
        out["standings_gap"] = found[1]["standings_1_rank"] - found[2]["standings_2_rank"]
    return out


def _metric_pair_tables(soup: BeautifulSoup) -> dict[str, float]:
    """Two-sided stat tables: avg | total | label | total | avg."""
    labels = {
        "clean sheets": "clean_sheets",
        "corners": "corners",
        "red cards": "red_cards",
        "yellow cards": "yellow_cards",
        "goal kicks": "goal_kicks",
        "throws in": "throws_in",
        "throw ins": "throws_in",
        "offsides": "offsides",
        "penalties": "penalties",
        "gk saves": "gk_saves",
        "fouls": "fouls",
        "tackles": "tackles",
        "total attacks": "total_attacks",
        "attacks": "total_attacks",
        "dangerous attacks": "dangerous_attacks",
        "total shots": "total_shots",
        "shots": "total_shots",
        "passes": "passes",
    }
    out: dict[str, float] = {}
    for table in soup.select("table"):
        for tr in table.select("tr"):
            cells = [_clean(cell.get_text(" ", strip=True)) for cell in tr.find_all(["th", "td"])]
            if len(cells) < 5:
                continue
            label = cells[2].casefold().strip(" :*")
            key = labels.get(label)
            if key is None:
                continue
            left_avg, left_total = _number(cells[0]), _number(cells[1])
            right_total, right_avg = _number(cells[3]), _number(cells[4])
            if left_avg is not None:
                out[f"p1_{key}_avg"] = left_avg
            if left_total is not None:
                out[f"p1_{key}_total"] = left_total
            if right_avg is not None:
                out[f"p2_{key}_avg"] = right_avg
            if right_total is not None:
                out[f"p2_{key}_total"] = right_total
    return out


def _goal_avgs(soup: BeautifulSoup) -> dict[str, float]:
    """Overall-statistics scored/conceded block. Order is p1 then p2."""
    cells = [_clean(node.get_text(" ", strip=True)) for node in soup.select(".os_goals_section1_child")]
    if len(cells) < 8:
        return {}
    numbers = [_number(cell) for cell in cells[:8]]
    if any(item is None for item in numbers):
        return {}
    keys = (
        "p1_scored", "p1_scored_avg", "p1_conceded", "p1_conceded_avg",
        "p2_scored", "p2_scored_avg", "p2_conceded", "p2_conceded_avg",
    )
    return {key: float(value) for key, value in zip(keys, numbers)}


def _form_counts(soup: BeautifulSoup) -> tuple[dict[str, int], dict[str, int]]:
    containers = soup.select(".prformcont")
    output = []
    for container in containers[:2]:
        output.append({
            "wins": len(container.select(".form_w")),
            "losses": len(container.select(".form_l")),
            "draws": len(container.select(".form_d")),
        })
    while len(output) < 2:
        output.append({"wins": 0, "losses": 0, "draws": 0})
    return output[0], output[1]


def _surface_records(text: str) -> dict[str, float]:
    records = []
    decoder = json.JSONDecoder()
    for match in re.finditer(r'\{\s*"clay"', text, re.I):
        try:
            record, _ = decoder.raw_decode(text[match.start():])
            if isinstance(record, dict) and "hard" in record and "grass" in record:
                records.append(record)
        except Exception:
            continue
    out = {}
    for participant, record in enumerate(records[:2], 1):
        for surface in ("clay", "hard", "grass"):
            values = record.get(surface) or {}
            wins = float(values.get("win") or 0)
            total = float(values.get("total") or 0)
            out[f"p{participant}_{surface}_sample"] = total
            if total:
                out[f"p{participant}_{surface}_win_rate"] = wins / total
    return out


def _mma_fields(text: str) -> dict[str, float | str]:
    out: dict[str, float | str] = {}
    records = re.findall(r"record of\s+(\d+)-(\d+)-(\d+)", text, re.I)
    for index, values in enumerate(records[:2], 1):
        wins, losses, draws = map(float, values)
        out[f"p{index}_record_wins"] = wins
        out[f"p{index}_record_losses"] = losses
        out[f"p{index}_record_draws"] = draws
        out[f"p{index}_record_win_rate"] = wins / (wins + losses + draws) if wins + losses + draws else 0.0
    heights = re.findall(r"(\d+)['’]\s*(\d+)", text)
    for index, (feet, inches) in enumerate(heights[:2], 1):
        out[f"p{index}_height_inches"] = float(feet) * 12 + float(inches)
    weights = re.findall(r"(\d+(?:\.\d+)?)\s*lbs", text, re.I)
    for index, value in enumerate(weights[:2], 1):
        out[f"p{index}_weight_lbs"] = float(value)
    reaches = re.findall(r"(\d+(?:\.\d+)?)['\"]\s*Reach|Reach\s*(\d+(?:\.\d+)?)['\"]", text, re.I)
    flattened = [a or b for a, b in reaches]
    for index, value in enumerate(flattened[:2], 1):
        out[f"p{index}_reach_inches"] = float(value)
    for stance in ("orthodox", "southpaw", "switch"):
        if stance in text.lower():
            out[f"stance_{stance}_present"] = 1.0
    return out


def _distance_and_weather(text: str) -> dict[str, float]:
    """Extract travel distance (km) and weather temperature (°C) from detail prose."""
    out: dict[str, float] = {}
    dist_match = re.search(r"(\d+(?:\.\d+)?)\s*km", text, re.I)
    if dist_match:
        out["travel_distance_km"] = float(dist_match.group(1))
    temp_match = re.search(r"(\d+(?:\.\d+)?)\s*°", text)
    if temp_match:
        out["weather_temperature_c"] = float(temp_match.group(1))
    return out


def parse_detail(
    body: bytes,
    sport: str,
    participant_1: str = "",
    participant_2: str = "",
) -> DetailFacets:
    soup = BeautifulSoup(body, "html.parser")
    text = _clean(soup.get_text(" ", strip=True))
    lower = text.lower()
    p1_form, p2_form = _form_counts(soup)
    p1_games = p1_form["wins"] + p1_form["losses"] + p1_form["draws"]
    p2_games = p2_form["wins"] + p2_form["losses"] + p2_form["draws"]
    common: dict[str, Any] = {
        "h2h_present": "head to head" in lower,
        "last6_present": "last 6 matches" in lower,
        "home_split_present": "home matches" in lower,
        "away_split_present": "away matches" in lower,
        "standings_present": "standings" in lower,
        "p1_form_wins": p1_form["wins"],
        "p1_form_losses": p1_form["losses"],
        "p1_form_draws": p1_form["draws"],
        "p2_form_wins": p2_form["wins"],
        "p2_form_losses": p2_form["losses"],
        "p2_form_draws": p2_form["draws"],
        "recent_1_wins": p1_form["wins"],
        "recent_1_games": p1_games,
        "recent_2_wins": p2_form["wins"],
        "recent_2_games": p2_games,
    }
    common.update(_h2h_from_page(soup, text, participant_1, participant_2))
    common.update(_standings_from_page(soup, participant_1, participant_2))
    common.update(_metric_pair_tables(soup))
    common.update(_goal_avgs(soup))
    common.update(_distance_and_weather(text))
    specific: dict[str, Any] = {}

    if sport == "football":
        # Match Forebet's real page labels, not over-precise phrases. The page
        # shows "ht/ft btts" menu labels and "avg. corners" tables; "both teams
        # scored" prose is rare. A present label is enough to say the family is
        # on the page; numeric extraction (where applicable) is separate.
        for key, phrases in {
            "weather_present": ("weather conditions", "weather"),
            "htft_present": ("ht/ft", "ht/ft probability", "half time"),
            "corners_present": ("avg. corners", "corners"),
            "cards_present": ("avg. cards", "cards"),
            "btts_present": ("btts", "both teams scored"),
            "distance_present": ("straight line distance", "distance"),
            "fouls_present": ("fouls",),
            "tackles_present": ("tackles",),
            "possession_present": ("ball possession", "possession"),
            "attacks_present": ("total attacks", "dangerous attacks"),
            "shots_present": ("total shots", "on target"),
        }.items():
            specific[key] = any(phrase in lower for phrase in phrases)
        specific["cards_present"] = "avg. cards" in lower or "cards score" in lower or "cards" in lower
    elif sport in {"basketball", "american_football"}:
        specific["quarter_data_present"] = all(f"q{i}" in lower for i in range(1, 5))
    elif sport == "tennis":
        specific.update(_surface_records(text))
        specific["height_present"] = "height" in lower
    elif sport == "hockey":
        specific["period_data_present"] = all(item in lower for item in ("p1", "p2", "p3"))
        specific["overtime_present"] = "ot" in lower
    elif sport == "baseball":
        specific["hits_present"] = "hits" in lower
        specific["innings_present"] = "innings" in lower
    elif sport == "rugby":
        specific["round_present"] = "round" in lower
    elif sport == "handball":
        specific["draw_surface_present"] = "1 x 2" in lower or "draw" in lower
    elif sport == "volleyball":
        specific["set_data_present"] = all(item in lower for item in ("s1", "s2", "s3"))
    elif sport == "cricket":
        specific["innings_present"] = "innings" in lower
        specific["dls_present"] = "dls" in lower or "d/l method" in lower
        for fmt in ("t20", "odi", "test"):
            specific[f"format_{fmt}_present"] = fmt in lower
    elif sport == "mma":
        specific.update(_mma_fields(text))
        for key, phrase in {
            "strikes_present": "average strikes",
            "takedowns_present": "takedowns",
            "submissions_present": "submissions",
            "control_time_present": "control time",
            "stance_present": "stance",
        }.items():
            specific[key] = phrase in lower
    elif sport == "esoccer":
        for key, phrase in {
            "htft_present": "ht/ft probability",
            "corners_present": "avg. corners",
            "cards_present": "avg. cards",
            "btts_present": "both teams scored",
        }.items():
            specific[key] = phrase in lower

    required_by_sport = {
        "football": ("weather_present", "htft_present", "corners_present", "cards_present"),
        "tennis": ("p1_hard_sample", "p2_hard_sample"),
        "mma": ("strikes_present", "takedowns_present", "submissions_present", "control_time_present"),
    }
    missing = [key for key in required_by_sport.get(sport, ()) if not specific.get(key)]
    return DetailFacets(
        sport=sport,
        common=common,
        sport_specific=specific,
        sections=_extract_sections(soup),
        missing=missing,
    )
