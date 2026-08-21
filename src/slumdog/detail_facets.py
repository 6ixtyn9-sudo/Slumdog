"""Sport-specific Forebet match-detail facet extraction.

The parser preserves section text and extracts only timing-safe, reproducible
numeric summaries. Unknown or ambiguous values stay missing rather than zero.
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


def parse_detail(body: bytes, sport: str) -> DetailFacets:
    soup = BeautifulSoup(body, "html.parser")
    text = _clean(soup.get_text(" ", strip=True))
    lower = text.lower()
    p1_form, p2_form = _form_counts(soup)
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
    }
    specific: dict[str, Any] = {}

    if sport == "football":
        for key, phrase in {
            "weather_present": "weather conditions",
            "htft_present": "ht/ft probability",
            "corners_present": "avg. corners",
            "cards_present": "avg. cards",
            "btts_present": "both teams scored",
        }.items():
            specific[key] = phrase in lower
        specific["cards_present"] = "avg. cards" in lower or "cards score" in lower
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
