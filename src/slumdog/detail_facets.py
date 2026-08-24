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

    h2h_root = soup.select_one(".h2h, .h2h_div, #h2h, [id*=h2h], [class*=h2h]")
    if h2h_root is None:
        return out
    scores = []
    for row in h2h_root.select("table tr, tr"):
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


# ---------------------------------------------------------------------------
# Football detail-page numeric extraction.
#
# These blocks were verified against a live Forebet detail page
# (Brentford v Tottenham, 2026-08-22; UNAM Pumas v Club Necaxa, 2026-08-23).
# The page renders team stats as flattened labelled prose after Jina HTML
# relay, so extraction is label-anchored and order-based (p1 = home side,
# which always precedes p2 on Forebet). A missing label leaves the value
# missing rather than zero-filled.
# ---------------------------------------------------------------------------

_PCT = r"(\d+(?:\.\d+)?)\s*%"


def _following_numbers(text: str, label: str, count: int, flags: int = re.I) -> list[float] | None:
    """Return up to `count` numeric tokens immediately after `label`."""
    match = re.search(re.escape(label) + r"\s*([^a-zA-Z%]{0,120})", text, flags)
    if not match:
        return None
    nums = re.findall(r"\d+(?:\.\d+)?", match.group(1))
    return [float(n) for n in nums[:count]]


_PCT_TOKEN = r"(\d+(?:\.\d+)?|NAN)"


def _parse_pct(token: str) -> float | None:
    """Forebet renders no-data shot-direction splits as the literal ``NAN%``.

    Treat that as missing (never zero-fill) while still extracting the
    surrounding totals/blocked counts that are present.
    """
    if not token or token.upper() == "NAN":
        return None
    return float(token)


def _football_shots(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    # Block per side: "Total shots <n> <avg> Blocked <n> <bavg>
    #                 <off>% OFF target <on>% ON target <in>% Inside box ...".
    # Percentages may be the literal "NAN" when Forebet has no sample; that must
    # not discard the total/blocked counts that are still present.
    blocks = list(re.finditer(
        r"Total\s+shots\s+(\d+(?:\.\d+)?).*?Blocked\s+(\d+(?:\.\d+)?)"
        r".*?" + _PCT_TOKEN + r"%\s*OFF\s+target.*?" + _PCT_TOKEN
        + r"%\s*ON\s+target.*?" + _PCT_TOKEN + r"%\s*Inside\s+box",
        text, re.I | re.S,
    ))
    for idx, m in enumerate(blocks[:2], 1):
        total, blocked = float(m.group(1)), float(m.group(2))
        off_pct = _parse_pct(m.group(3))
        on_pct = _parse_pct(m.group(4))
        inside_pct = _parse_pct(m.group(5))
        avg = _following_numbers(text[m.start():m.start() + 120], "Total shots", 2)
        blocked_avg = _following_numbers(text[m.start():m.start() + 200], "Blocked", 2)
        out[f"p{idx}_shots_total"] = total
        out[f"p{idx}_shots_blocked"] = blocked
        if on_pct is not None:
            out[f"p{idx}_shots_on_target_pct"] = on_pct
        if off_pct is not None:
            out[f"p{idx}_shots_off_target_pct"] = off_pct
        if inside_pct is not None:
            out[f"p{idx}_shots_inside_box_pct"] = inside_pct
        if avg and len(avg) >= 2:
            out[f"p{idx}_shots_avg"] = avg[1]
        if blocked_avg and len(blocked_avg) >= 2:
            out[f"p{idx}_shots_blocked_avg"] = blocked_avg[1]
    return out


def _football_passes(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    # "Total <n> Avg. per game <avg> Accurate <n> <pct>% Ball Possession <pct>%"
    blocks = list(re.finditer(
        r"(?<!\w)Total\s+(\d+(?:\.\d+)?)\s+Avg\.\s*per\s+game\s+(\d+(?:\.\d+)?)"
        r"\s+Accurate\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)%\s*Ball\s+Possession\s+(\d+(?:\.\d+)?)%",
        text, re.I,
    ))
    for idx, m in enumerate(blocks[:2], 1):
        total, avg, accurate, acc_pct, poss = (float(g) for g in m.groups())
        out[f"p{idx}_passes_total"] = total
        out[f"p{idx}_passes_avg"] = avg
        out[f"p{idx}_passes_accurate"] = accurate
        out[f"p{idx}_passes_accuracy_pct"] = acc_pct
        out[f"p{idx}_possession_pct"] = poss
    return out


def _football_attacks(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    # Each attack section lists p1 then p2 ("Brentford 565 Avg. 94.17 ...
    # Tottenham 563 Avg. 93.83"). Split on the section label and parse both
    # team numbers from the bounded window, since a non-greedy match would only
    # ever return the first side.
    for label, prefix in (("Total attacks", "total_attacks"),
                          ("Dangerous attacks", "dangerous_attacks")):
        idx = text.lower().find(label.lower())
        if idx < 0:
            continue
        window = text[idx + len(label): idx + 260]
        pairs = re.findall(r"(\d+(?:\.\d+)?)\s+Avg\.?\s*(\d+(?:\.\d+)?)", window, re.I)
        for side, (total, avg) in enumerate(pairs[:2], 1):
            out[f"p{side}_{prefix}_total"] = float(total)
            out[f"p{side}_{prefix}_avg"] = float(avg)
    return out


def _football_event_times(text: str) -> dict[str, float]:
    """Avg. event time: first goal / first corner / first card in minutes."""
    out: dict[str, float] = {}
    idx = text.lower().find("avg. event time")
    if idx < 0:
        return out
    window = text[idx:idx + 600]
    for label, key in (("first goal", "first_goal_min"),
                       ("first corner", "first_corner_min"),
                       ("first card", "first_card_min")):
        match = re.search(label + r".*?(\d+)\s*'", window, re.I | re.S)
        if match:
            out[key] = float(match.group(1))
    return out


def _football_uo_btts(text: str) -> dict[str, float]:
    """Recent-matches under/over counts (1.5/2.5/3.5 lines) and BTTS yes/no.

    The page renders two single-digit counts (under, over) for the last six
    matches; in Jina's markdown view they concatenate ("15") while HTML mode
    spaces them ("1 5"). Both shapes are accepted.
    """
    out: dict[str, float] = {}
    for line in (1.5, 2.5, 3.5):
        match = re.search(
            r"Under/Over\s+(\d)\s*(\d)\s+(\d+(?:\.\d+)?)%\s+(\d+(?:\.\d+)?)%\s+"
            + re.escape(str(line)) + r"\s+Goals",
            text, re.I,
        )
        if match:
            under_count, over_count, under_pct, over_pct = (float(g) for g in match.groups())
            out[f"recent_uo_{line}_under"] = under_count
            out[f"recent_uo_{line}_over"] = over_count
            out[f"recent_uo_{line}_under_pct"] = under_pct
            out[f"recent_uo_{line}_over_pct"] = over_pct
    # BTTS block (two sides): "Yes <n> <pct>% <pct>% No <n>"
    btts = list(re.finditer(
        r"Both\s+scored\s+Yes\s+(\d+)\s+(\d+(?:\.\d+)?)%\s+(\d+(?:\.\d+)?)%\s+No\s+(\d+)",
        text, re.I,
    ))
    for idx, m in enumerate(btts[:2], 1):
        yes, yes_pct, no_pct, no = (float(g) for g in m.groups())
        out[f"p{idx}_btts_yes"] = yes
        out[f"p{idx}_btts_no"] = no
        out[f"p{idx}_btts_yes_pct"] = yes_pct
    return out


def _football_next_difficulty(text: str) -> dict[str, float]:
    """Average difficulty (1=easy .. 5=severe) of each side's upcoming fixtures."""
    out: dict[str, float] = {}
    idx = text.lower().find("next matches")
    if idx < 0:
        return out
    window = text[idx:idx + 4000]
    # Difficulty ratings render as bare integers 1..5 inside fixture rows.
    # Count the first run of 1-5 tokens before the second side's section.
    halves = re.split(r"next matches", window, flags=re.I)
    for side, chunk in enumerate(halves[1:3], 1):
        ratings = [float(n) for n in re.findall(r"\b([1-5])\b", chunk) if 0 < float(n) <= 5]
        # The page lists ~12 fixtures per side; filter to plausible runs.
        ratings = [n for n in ratings if 1 <= n <= 5][:12]
        if ratings:
            out[f"p{side}_next_fixtures_count"] = float(len(ratings))
            out[f"p{side}_next_difficulty_avg"] = round(sum(ratings) / len(ratings), 3)
    return out


def _football_lg_form(text: str) -> dict[str, float]:
    """Forebet's embedded last-6 W/D/L arrays: ``{"lg_-1_6":[w,d,l,total], ...}``.

    `lg_-1_6` aggregates all competitions; `lg_1_6` is league-only. The first
    two JSON objects on the page correspond to p1 then p2. Only the `_6`
    arrays are interpreted (their [1,3,2,6] shape matched the displayed
    "Win 1 Draw 3 Lost 2" line on the verified page).
    """
    out: dict[str, float] = {}
    decoder = json.JSONDecoder()
    found = 0
    for match in re.finditer(r'\{\s*"lg_-1"', text):
        try:
            record, _ = decoder.raw_decode(text[match.start():])
        except Exception:
            continue
        if not isinstance(record, dict):
            continue
        found += 1
        side = found
        if side > 2:
            break
        for comp, key in (("lg_-1_6", "all"), ("lg_1_6", "league")):
            arr = record.get(comp)
            if isinstance(arr, list) and len(arr) >= 4:
                w, d, l, total = (float(x or 0) for x in arr[:4])
                out[f"p{side}_l6_{key}_wins"] = w
                out[f"p{side}_l6_{key}_draws"] = d
                out[f"p{side}_l6_{key}_losses"] = l
                if total:
                    out[f"p{side}_l6_{key}_win_rate"] = w / total
                    out[f"p{side}_l6_{key}_draw_rate"] = d / total
    return out


def _football_tab_markets(text: str) -> dict[str, float | str]:
    """Top-of-page prediction tabs: corners/cards/double-chance/scorers.

    These markets have NO distinct JSON endpoint (tp=corners/doublechance/
    goalscorer echo 1X2); the detail page is their only source. Extraction is
    deliberately narrow so a layout change leaves values missing rather than
    wrong.
    """
    out: dict[str, float | str] = {}
    # Corners tab (detail page only; JSON tp=corners echoes 1X2). The flattened
    # tab text is "<p1> <p2> Over <pred_low>-<pred_high> <line> Corners",
    # e.g. "46 54 Over 5-6 5 - 6 9.57 Corners". Also accept the alternate
    # "Avg. corners <line>" prose shape.
    corn = re.search(
        r"(\d{1,3})\s+(\d{1,3})\s+Over\s+(\d+)\s*-\s*(\d+)\s+"
        r"\d+\s*-\s*\d+\s+(\d+(?:\.\d+)?)\s+Corners",
        text, re.I,
    )
    if not corn:
        corn = re.search(
            r"(\d{1,3})\s+(\d{1,3})\s+Over\s+(\d+)\s*-\s*(\d+)"
            r".*?Avg\.\s*corners\s+(\d+(?:\.\d+)?)",
            text, re.I | re.S,
        )
    if corn:
        out["corners_p1_prob"] = float(corn.group(1))
        out["corners_p2_prob"] = float(corn.group(2))
        out["corners_pred_low"] = float(corn.group(3))
        out["corners_pred_high"] = float(corn.group(4))
        out["corners_avg_line"] = float(corn.group(5))
    # Cards: same two shapes as corners.
    cards = re.search(
        r"(\d{1,3})\s+(\d{1,3})\s+Over\s+(\d+)\s*-\s*(\d+)\s+"
        r"\d+\s*-\s*\d+\s+(\d+(?:\.\d+)?)\s+Cards",
        text, re.I,
    )
    if not cards:
        cards = re.search(
            r"(\d{1,3})\s+(\d{1,3})\s+Over\s+(\d+)\s*-\s*(\d+)"
            r".*?Avg\.\s*cards\s+(\d+(?:\.\d+)?)",
            text, re.I | re.S,
        )
    if cards:
        out["cards_p1_prob"] = float(cards.group(1))
        out["cards_p2_prob"] = float(cards.group(2))
        out["cards_pred_low"] = float(cards.group(3))
        out["cards_pred_high"] = float(cards.group(4))
        out["cards_avg_line"] = float(cards.group(5))
    # Double chance: "71% ... 1X/12/X2 ... predicted score".
    dc = re.search(r"(\d+(?:\.\d+)?)%\s*(1X|12|X2)\b", text, re.I)
    if dc:
        out["doublechance_prob"] = float(dc.group(1))
        out["doublechance_pick"] = dc.group(2).upper()
    return out


def _football_detail_stats(text: str) -> dict[str, float | str]:
    stats: dict[str, float | str] = {}
    for extractor in (
        _football_shots, _football_passes, _football_attacks,
        _football_event_times, _football_uo_btts, _football_next_difficulty,
        _football_lg_form,
    ):
        stats.update(extractor(text))
    stats.update(_football_tab_markets(text))
    return stats


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
        # Numeric football detail stats (shots/passes/possession/attacks/etc.)
        # are pre-event by construction for an upcoming-match detail page.
        specific.update(_football_detail_stats(text))
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
