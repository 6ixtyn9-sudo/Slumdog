"""Sport 11 (Cricket) dedicated domain models, feature engineering, and pipeline.

Cricket is a format-specific sport (Test 1X2 with draw possible; ODI/T20 2-way) characterized by:
- Multi-innings structure (INN1-INN4 for Tests, INN1-INN2 for Limited Overs)
- Format variance (T20 high-variance short format vs ODI vs 5-day Test match)
- Run rate and wicket dynamics
- Pitch/conditions and home ground advantage
- 2-way or 3-way market pricing

This module provides:
- Cricket-specific feature extraction (format indicator, run margins, 2-way/3-way de-vigging)
- Standings, recent form, and H2H metrics
- Dedicated Robber detector with format volatility and home bonuses
- Leak-safe numeric vector builder with explicit missingness flags
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from .contracts import (
    CandidateState,
    EventSnapshot,
    H2HStats,
    RecentForm,
    RobberCandidate,
)
from .magolide import RobberConfig


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "-", "n/a", "N/A"):
        return None
    try:
        val = float(value)
        return val if math.isfinite(val) else None
    except (TypeError, ValueError):
        return None


def calculate_overround_cricket(
    odds_1: float | None,
    odds_x: float | None,
    odds_2: float | None,
) -> float | None:
    if odds_1 is None or odds_2 is None or odds_1 <= 1.0 or odds_2 <= 1.0:
        return None
    if odds_x is not None and odds_x > 1.0:
        return max(0.0, (1.0 / odds_1) + (1.0 / odds_x) + (1.0 / odds_2) - 1.0)
    return max(0.0, (1.0 / odds_1) + (1.0 / odds_2) - 1.0)


def devig_probabilities_cricket(
    odds_1: float | None,
    odds_x: float | None,
    odds_2: float | None,
) -> tuple[float | None, float | None, float | None]:
    if odds_1 is None or odds_2 is None or odds_1 <= 1.0 or odds_2 <= 1.0:
        return None, None, None
    imp_1, imp_2 = 1.0 / odds_1, 1.0 / odds_2
    imp_x = (1.0 / odds_x) if (odds_x is not None and odds_x > 1.0) else 0.0
    total = imp_1 + imp_x + imp_2
    if total <= 0.0:
        return None, None, None
    return imp_1 / total, (imp_x / total if imp_x > 0 else None), imp_2 / total


def shannon_entropy_cricket(p1: float | None, px: float | None, p2: float | None) -> float:
    probs = [p for p in (p1, px, p2) if p is not None and p > 0]
    total = sum(probs)
    if total <= 0:
        return 0.0
    normalized = [p / total for p in probs]
    return -sum(p * math.log(p) for p in normalized)


def parse_score_string(score_str: str) -> tuple[float | None, float | None]:
    match = re.search(r"(\d+)\s*[-:]\s*(\d+)", str(score_str or ""))
    if not match:
        return None, None
    return float(match.group(1)), float(match.group(2))


@dataclass
class CricketFeatures:
    """Typed container for complete pre-event cricket features."""
    is_home_dog: float
    dog_index: int
    favorite_index: int

    # Probabilities
    forebet_dog_prob: float
    forebet_favorite_prob: float
    forebet_draw_prob: float | None
    forebet_prob_gap: float
    forebet_entropy: float
    favorite_dominance_ratio: float
    forebet_calls_dog: float

    # Format indicators
    is_t20_format: float
    is_odi_format: float
    is_test_format: float

    # Pricing & Market Signals
    price_available: float
    dog_price: float | None
    favorite_price: float | None
    draw_price: float | None
    market_overround: float | None
    dog_fair_implied_prob: float | None
    favorite_fair_implied_prob: float | None
    draw_fair_implied_prob: float | None
    price_value_edge: float | None

    # Runs, Margins & Total
    predicted_total_runs: float | None
    predicted_run_margin_dog: float | None
    dog_predicted_runs: float | None
    fav_predicted_runs: float | None

    # Form & Recent Performance
    dog_recent_win_rate: float
    favorite_recent_win_rate: float
    win_rate_gap: float
    dog_recent_games: float

    # Standings & Quality Differentials
    dog_rank: float | None
    favorite_rank: float | None
    rank_gap: float | None
    standings_pts_gap: float | None
    standings_nrr_gap: float | None

    # H2H Matchup History
    h2h_total_games: float
    h2h_dog_win_rate: float
    h2h_has_dog_win: float

    # Legacy & Meta
    legacy_robber_score: float
    legacy_raw_confidence: float

    def to_dict(self) -> dict[str, float]:
        features: dict[str, float] = {
            "cr_is_home_dog": self.is_home_dog,
            "cr_forebet_dog_prob": self.forebet_dog_prob,
            "cr_forebet_favorite_prob": self.forebet_favorite_prob,
            "cr_forebet_prob_gap": self.forebet_prob_gap,
            "cr_forebet_entropy": self.forebet_entropy,
            "cr_favorite_dominance_ratio": self.favorite_dominance_ratio,
            "cr_forebet_calls_dog": self.forebet_calls_dog,
            "cr_is_t20_format": self.is_t20_format,
            "cr_is_odi_format": self.is_odi_format,
            "cr_is_test_format": self.is_test_format,
            "cr_price_available": self.price_available,
            "cr_dog_recent_win_rate": self.dog_recent_win_rate,
            "cr_favorite_recent_win_rate": self.favorite_recent_win_rate,
            "cr_win_rate_gap": self.win_rate_gap,
            "cr_dog_recent_games": self.dog_recent_games,
            "cr_h2h_total_games": self.h2h_total_games,
            "cr_h2h_dog_win_rate": self.h2h_dog_win_rate,
            "cr_h2h_has_dog_win": self.h2h_has_dog_win,
            "cr_legacy_robber_score": self.legacy_robber_score,
            "cr_legacy_raw_confidence": self.legacy_raw_confidence,
        }

        optional_fields: list[tuple[str, float | None]] = [
            ("cr_forebet_draw_prob", self.forebet_draw_prob),
            ("cr_dog_price", self.dog_price),
            ("cr_favorite_price", self.favorite_price),
            ("cr_draw_price", self.draw_price),
            ("cr_market_overround", self.market_overround),
            ("cr_dog_fair_implied_prob", self.dog_fair_implied_prob),
            ("cr_favorite_fair_implied_prob", self.favorite_fair_implied_prob),
            ("cr_draw_fair_implied_prob", self.draw_fair_implied_prob),
            ("cr_price_value_edge", self.price_value_edge),
            ("cr_predicted_total_runs", self.predicted_total_runs),
            ("cr_predicted_run_margin_dog", self.predicted_run_margin_dog),
            ("cr_dog_predicted_runs", self.dog_predicted_runs),
            ("cr_fav_predicted_runs", self.fav_predicted_runs),
            ("cr_dog_rank", self.dog_rank),
            ("cr_favorite_rank", self.favorite_rank),
            ("cr_rank_gap", self.rank_gap),
            ("cr_standings_pts_gap", self.standings_pts_gap),
            ("cr_standings_nrr_gap", self.standings_nrr_gap),
        ]

        for name, val in optional_fields:
            features[f"{name}_missing"] = 1.0 if val is None else 0.0
            features[name] = float(val) if val is not None else 0.0

        return features


def extract_cricket_features(
    event: EventSnapshot,
    candidate: RobberCandidate,
    h2h: H2HStats | None = None,
    recent_1: RecentForm | None = None,
    recent_2: RecentForm | None = None,
) -> CricketFeatures:
    """Extract complete, leak-safe CricketFeatures from pre-event snapshots."""
    h2h = h2h or H2HStats()
    facets = event.pre_event_facets()

    dog = candidate.participant_index
    fav = 2 if dog == 1 else 1
    is_home_dog = 1.0 if dog == 1 else 0.0

    p1 = event.probability_1 or 0.0
    p2 = event.probability_2 or 0.0
    px = event.draw_probability
    dog_prob = p1 if dog == 1 else p2
    fav_prob = p2 if dog == 1 else p1
    prob_gap = fav_prob - dog_prob
    entropy = shannon_entropy_cricket(p1, px, p2)
    dominance = fav_prob / max(0.01, dog_prob)
    calls_dog = 1.0 if event.forebet_pick == dog else 0.0

    # Format detection
    fmt_str = str(facets.get("match_format") or facets.get("format") or event.tournament or "").lower()
    is_t20 = 1.0 if ("t20" in fmt_str or "twenty20" in fmt_str or "ipl" in fmt_str or "bbl" in fmt_str) else 0.0
    is_odi = 1.0 if ("odi" in fmt_str or "one day" in fmt_str or "50 over" in fmt_str) else 0.0
    is_test = 1.0 if ("test" in fmt_str or "first class" in fmt_str or "sheffield" in fmt_str) else 0.0

    dog_odds = event.odds(dog)
    fav_odds = event.odds(fav)
    draw_odds = _safe_float(facets.get("odds_draw") or facets.get("odds_x") or facets.get("best_odd_X"))
    overround = calculate_overround_cricket(event.odds_1, draw_odds, event.odds_2)
    devig_1, devig_x, devig_2 = devig_probabilities_cricket(event.odds_1, draw_odds, event.odds_2)
    dog_fair_prob = devig_1 if dog == 1 else devig_2
    fav_fair_prob = devig_2 if dog == 1 else devig_1
    value_edge = (dog_prob - dog_fair_prob) if dog_fair_prob is not None else None

    # Scores & Totals
    sc_1, sc_2 = parse_score_string(event.predicted_score)
    total_runs = _safe_float(event.predicted_total)
    if total_runs is None and sc_1 is not None and sc_2 is not None:
        total_runs = sc_1 + sc_2

    dog_sc = sc_1 if dog == 1 else sc_2
    fav_sc = sc_2 if dog == 1 else sc_1
    run_margin_dog = (dog_sc - fav_sc) if (dog_sc is not None and fav_sc is not None) else None

    # Form
    dog_recent = recent_1 if dog == 1 else recent_2
    fav_recent = recent_2 if dog == 1 else recent_1
    dog_wr = (dog_recent.win_rate or 0.0) if dog_recent else 0.0
    fav_wr = (fav_recent.win_rate or 0.0) if fav_recent else 0.0
    dog_games = float(dog_recent.games if dog_recent else 0)

    # Standings
    pos_1 = _safe_float(facets.get("standings_1") or facets.get("standings_1_rank"))
    pos_2 = _safe_float(facets.get("standings_2") or facets.get("standings_2_rank"))
    dog_rank = pos_1 if dog == 1 else pos_2
    fav_rank = pos_2 if dog == 1 else pos_1
    rank_gap = (fav_rank - dog_rank) if (dog_rank is not None and fav_rank is not None) else None

    pts_1 = _safe_float(facets.get("standings_1_pts"))
    pts_2 = _safe_float(facets.get("standings_2_pts"))
    pts_gap = (pts_1 - pts_2) if (pts_1 is not None and pts_2 is not None) else None
    if dog == 2 and pts_gap is not None:
        pts_gap = -pts_gap

    nrr_1 = _safe_float(facets.get("standings_1_nrr") or facets.get("nrr_1"))
    nrr_2 = _safe_float(facets.get("standings_2_nrr") or facets.get("nrr_2"))
    nrr_gap = (nrr_1 - nrr_2) if (nrr_1 is not None and nrr_2 is not None) else None
    if dog == 2 and nrr_gap is not None:
        nrr_gap = -nrr_gap

    # H2H
    h2h_games = float(h2h.total_games or facets.get("h2h_total_games") or 0)
    h2h_dog_wins = float(h2h.wins(dog) or 0)
    h2h_wr = (h2h_dog_wins / h2h_games) if h2h_games > 0 else 0.0
    has_dog_win = 1.0 if h2h_dog_wins > 0 else 0.0

    return CricketFeatures(
        is_home_dog=is_home_dog,
        dog_index=dog,
        favorite_index=fav,
        forebet_dog_prob=dog_prob,
        forebet_favorite_prob=fav_prob,
        forebet_draw_prob=px,
        forebet_prob_gap=prob_gap,
        forebet_entropy=entropy,
        favorite_dominance_ratio=dominance,
        forebet_calls_dog=calls_dog,
        is_t20_format=is_t20,
        is_odi_format=is_odi,
        is_test_format=is_test,
        price_available=1.0 if (dog_odds is not None and fav_odds is not None) else 0.0,
        dog_price=dog_odds,
        favorite_price=fav_odds,
        draw_price=draw_odds,
        market_overround=overround,
        dog_fair_implied_prob=dog_fair_prob,
        favorite_fair_implied_prob=fav_fair_prob,
        draw_fair_implied_prob=devig_x,
        price_value_edge=value_edge,
        predicted_total_runs=total_runs,
        predicted_run_margin_dog=run_margin_dog,
        dog_predicted_runs=dog_sc,
        fav_predicted_runs=fav_sc,
        dog_recent_win_rate=dog_wr,
        favorite_recent_win_rate=fav_wr,
        win_rate_gap=dog_wr - fav_wr,
        dog_recent_games=dog_games,
        dog_rank=dog_rank,
        favorite_rank=fav_rank,
        rank_gap=rank_gap,
        standings_pts_gap=pts_gap,
        standings_nrr_gap=nrr_gap,
        h2h_total_games=h2h_games,
        h2h_dog_win_rate=h2h_wr,
        h2h_has_dog_win=has_dog_win,
        legacy_robber_score=candidate.score,
        legacy_raw_confidence=candidate.raw_confidence,
    )


def detect_cricket_robber(
    event: EventSnapshot,
    h2h: H2HStats | None = None,
    recent_1: RecentForm | None = None,
    recent_2: RecentForm | None = None,
    config: RobberConfig | None = None,
) -> RobberCandidate | None:
    """Dedicated Cricket Robber detector with format volatility and conditions bonuses."""
    config = config or RobberConfig()
    h2h = h2h or H2HStats()

    dog_idx = 1
    basis = "lower_forebet_probability"
    if event.odds_1 is not None and event.odds_2 is not None:
        if event.odds_1 != event.odds_2:
            dog_idx = 1 if event.odds_1 > event.odds_2 else 2
            basis = "displayed_odds"
    elif event.forebet_pick in (1, 2):
        dog_idx = 2 if event.forebet_pick == 1 else 1
        basis = "opposite_forebet_pick"
    elif event.probability_1 is not None and event.probability_2 is not None:
        if event.probability_1 != event.probability_2:
            dog_idx = 1 if event.probability_1 < event.probability_2 else 2
            basis = "lower_forebet_probability"

    fav_idx = 2 if dog_idx == 1 else 1
    dog_odds = event.odds(dog_idx)
    fav_odds = event.odds(fav_idx)
    odds_avail = (dog_odds is not None and fav_odds is not None)

    score = 0.0
    reasons: list[str] = []

    # Home Ground / Conditions Advantage
    if dog_idx == 1:
        score += 4.0
        reasons.append("Home Ground Underdog (+4)")

    # T20 High-Variance Short Format Bonus
    facets = event.pre_event_facets()
    fmt_str = str(facets.get("match_format") or facets.get("format") or event.tournament or "").lower()
    if "t20" in fmt_str or "twenty20" in fmt_str or "ipl" in fmt_str or "bbl" in fmt_str:
        score += 4.0
        reasons.append("T20 high-volatility format (+4)")

    # Favorite Strength Factor
    if odds_avail and fav_odds is not None:
        if fav_odds <= 1.30:
            score += 15.0
            reasons.append(f"Heavy fav @{fav_odds:.2f}")
        elif fav_odds <= 1.50:
            score += 12.0
            reasons.append(f"Strong fav @{fav_odds:.2f}")
        elif fav_odds <= 1.70:
            score += 8.0
            reasons.append(f"Clear fav @{fav_odds:.2f}")
        else:
            score += 3.0
            reasons.append(f"Slight fav @{fav_odds:.2f}")

    # H2H Factor
    if h2h.total_games >= config.min_h2h_games:
        wins = h2h.wins(dog_idx)
        rate = wins / h2h.total_games
        if rate >= config.underdog_win_threshold:
            score += 20.0
            reasons.append(f"H2H {round(rate * 100)}% ({wins}/{h2h.total_games})")
        elif wins > 0:
            score += 8.0
            reasons.append(f"Prior H2H win ({wins})")

    # Recent Form
    recent = recent_1 if dog_idx == 1 else recent_2
    if recent and recent.games >= config.momentum_games:
        rate = (recent.wins / recent.games) if recent.games > 0 else 0.0
        if rate >= config.momentum_win_threshold:
            score += 15.0
            reasons.append(f"Hot form {recent.wins}W/{recent.games}G")
        elif rate >= 0.45:
            score += 8.0
            reasons.append(f"Solid form {recent.wins}W/{recent.games}G")

    # Odds Value Factor
    if odds_avail and dog_odds is not None:
        if 2.10 <= dog_odds <= 3.80:
            score += 15.0
            reasons.append(f"Value @{dog_odds:.2f}")
        elif 1.80 <= dog_odds < 2.10:
            score += 10.0
            reasons.append(f"Playable @{dog_odds:.2f}")
        elif 3.80 < dog_odds <= 6.50:
            score += 8.0
            reasons.append(f"High payout @{dog_odds:.2f}")
        else:
            score += 4.0
            reasons.append(f"Longshot @{dog_odds:.2f}")
    else:
        reasons.append("Unpriced cricket match")

    threshold = config.min_score if odds_avail else max(10.0, round(config.min_score * 0.55))
    if score < threshold:
        return None

    raw_conf = min(config.max_confidence, 46.0 + score * 0.55)
    raw_prob = raw_conf / 100.0
    implied = 1.0 / dog_odds if (odds_avail and dog_odds is not None) else None

    if odds_avail and dog_odds is not None and implied is not None:
        shrink = min(0.60, max(0.15, config.calibration_shrink))
        legacy_prob = implied + (raw_prob - implied) * shrink
        legacy_prob = min(config.calibration_max_probability, max(0.25, legacy_prob))
        legacy_conf = min(95.0, max(50.0, round(legacy_prob * 100.0)))
        ev = legacy_prob * dog_odds - 1.0
        advantage = legacy_prob - implied
        state = CandidateState.SHADOW_PRICED
    else:
        legacy_prob = raw_prob
        legacy_conf = min(95.0, max(50.0, round(raw_conf)))
        ev = None
        advantage = None
        state = CandidateState.SHADOW_UNPRICED

    return RobberCandidate(
        event_id=event.event_id,
        sport="cricket",
        participant_index=dog_idx,
        participant=event.participant(dog_idx),
        opponent=event.participant(fav_idx),
        score=score,
        reasons=reasons,
        raw_confidence=round(raw_conf, 3),
        legacy_confidence=legacy_conf,
        price=dog_odds,
        implied_probability=round(implied, 6) if implied is not None else None,
        legacy_probability=round(legacy_prob, 6),
        legacy_expected_value=round(ev, 6) if ev is not None else None,
        legacy_probability_advantage=round(advantage, 6) if advantage is not None else None,
        price_state=event.price_state,
        state=state,
        underdog_basis=basis,
        forebet_underdog_probability=event.probability(dog_idx),
        forebet_favorite_probability=event.probability(fav_idx),
    )
