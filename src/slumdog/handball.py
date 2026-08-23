"""Sport 8 (Handball) dedicated domain models, feature engineering, and pipeline.

Handball is a 3-way sport (1X2 regulation with ~8-12% draw probability) characterized by:
- 2 halves structure (H1, H2 of 30 minutes each)
- High goal volume (typically 50-65 total goals)
- Tight margin volatility (1-2 goal margins frequent)
- Fast pace / high totals (>= 61.5) vs defensive grinds (<= 52.5)
- Pronounced home court advantage in European/domestic leagues and travel fatigue
- 3-way pricing (1X2) and standings goal differential (GD) dynamics

This module provides:
- Handball-specific feature extraction (halves splits, tight margin proxy, 3-way de-vigging, travel distance)
- Standings, goal differential, scoring averages, and form pace metrics
- Dedicated 3-way Robber detector with home-underdog, travel, and tight-game bonuses
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


def calculate_overround_handball(
    odds_1: float | None,
    odds_x: float | None,
    odds_2: float | None,
) -> float | None:
    if odds_1 is None or odds_2 is None or odds_1 <= 1.0 or odds_2 <= 1.0:
        return None
    if odds_x is not None and odds_x > 1.0:
        return max(0.0, (1.0 / odds_1) + (1.0 / odds_x) + (1.0 / odds_2) - 1.0)
    return max(0.0, (1.0 / odds_1) + (1.0 / odds_2) - 1.0)


def devig_probabilities_handball(
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


def shannon_entropy_handball(p1: float | None, px: float | None, p2: float | None) -> float:
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
class HandballFeatures:
    """Typed container for complete pre-event handball features."""
    is_home_dog: float
    dog_index: int
    favorite_index: int

    # 3-Way Forebet Probabilities
    forebet_dog_prob: float
    forebet_favorite_prob: float
    forebet_draw_prob: float | None
    forebet_prob_gap: float
    forebet_entropy: float
    draw_pressure_ratio: float
    favorite_dominance_ratio: float
    forebet_calls_dog: float

    # Pricing & 3-Way De-vigged Signals
    price_available: float
    dog_price: float | None
    favorite_price: float | None
    draw_price: float | None
    market_overround: float | None
    dog_fair_implied_prob: float | None
    favorite_fair_implied_prob: float | None
    draw_fair_implied_prob: float | None
    price_value_edge: float | None

    # Goals, Margins & Total Environment
    predicted_total_goals: float | None
    predicted_goal_margin_dog: float | None
    dog_predicted_score: float | None
    fav_predicted_score: float | None
    close_game_expectation: float     # 1.0 if dog predicted margin >= -2.0 goals
    low_total_environment: float      # 1.0 if total <= 52.5 (defensive battle)
    high_total_environment: float     # 1.0 if total >= 61.5 (high-pace shootout)

    # Halves Splits (H1, H2)
    h1_margin_dog: float | None
    h2_margin_dog: float | None
    halves_projected_won: float

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
    standings_gd_gap: float | None

    # Spatial & Detail Match Averages
    travel_distance_km: float | None = None
    dog_travel_distance: float | None = None
    fav_travel_distance: float | None = None
    dog_scored_avg: float | None = None
    fav_scored_avg: float | None = None
    dog_conceded_avg: float | None = None
    fav_conceded_avg: float | None = None
    net_goal_differential_gap: float | None = None

    # H2H Matchup History
    h2h_total_games: float = 0.0
    h2h_dog_win_rate: float = 0.0
    h2h_has_dog_win: float = 0.0

    # Legacy & Meta
    legacy_robber_score: float = 0.0
    legacy_raw_confidence: float = 0.0

    def to_dict(self) -> dict[str, float]:
        features: dict[str, float] = {
            "hb_is_home_dog": self.is_home_dog,
            "hb_forebet_dog_prob": self.forebet_dog_prob,
            "hb_forebet_favorite_prob": self.forebet_favorite_prob,
            "hb_forebet_prob_gap": self.forebet_prob_gap,
            "hb_forebet_entropy": self.forebet_entropy,
            "hb_draw_pressure_ratio": self.draw_pressure_ratio,
            "hb_favorite_dominance_ratio": self.favorite_dominance_ratio,
            "hb_forebet_calls_dog": self.forebet_calls_dog,
            "hb_price_available": self.price_available,
            "hb_close_game_expectation": self.close_game_expectation,
            "hb_low_total_environment": self.low_total_environment,
            "hb_high_total_environment": self.high_total_environment,
            "hb_halves_projected_won": self.halves_projected_won,
            "hb_dog_recent_win_rate": self.dog_recent_win_rate,
            "hb_favorite_recent_win_rate": self.favorite_recent_win_rate,
            "hb_win_rate_gap": self.win_rate_gap,
            "hb_dog_recent_games": self.dog_recent_games,
            "hb_h2h_total_games": self.h2h_total_games,
            "hb_h2h_dog_win_rate": self.h2h_dog_win_rate,
            "hb_h2h_has_dog_win": self.h2h_has_dog_win,
            "hb_legacy_robber_score": self.legacy_robber_score,
            "hb_legacy_raw_confidence": self.legacy_raw_confidence,
        }

        optional_fields: list[tuple[str, float | None]] = [
            ("hb_forebet_draw_prob", self.forebet_draw_prob),
            ("hb_dog_price", self.dog_price),
            ("hb_favorite_price", self.favorite_price),
            ("hb_draw_price", self.draw_price),
            ("hb_market_overround", self.market_overround),
            ("hb_dog_fair_implied_prob", self.dog_fair_implied_prob),
            ("hb_favorite_fair_implied_prob", self.favorite_fair_implied_prob),
            ("hb_draw_fair_implied_prob", self.draw_fair_implied_prob),
            ("hb_price_value_edge", self.price_value_edge),
            ("hb_predicted_total_goals", self.predicted_total_goals),
            ("hb_predicted_goal_margin_dog", self.predicted_goal_margin_dog),
            ("hb_dog_predicted_score", self.dog_predicted_score),
            ("hb_fav_predicted_score", self.fav_predicted_score),
            ("hb_h1_margin_dog", self.h1_margin_dog),
            ("hb_h2_margin_dog", self.h2_margin_dog),
            ("hb_dog_rank", self.dog_rank),
            ("hb_favorite_rank", self.favorite_rank),
            ("hb_rank_gap", self.rank_gap),
            ("hb_standings_pts_gap", self.standings_pts_gap),
            ("hb_standings_gd_gap", self.standings_gd_gap),
            ("hb_travel_distance_km", self.travel_distance_km),
            ("hb_dog_travel_distance", self.dog_travel_distance),
            ("hb_fav_travel_distance", self.fav_travel_distance),
            ("hb_dog_scored_avg", self.dog_scored_avg),
            ("hb_fav_scored_avg", self.fav_scored_avg),
            ("hb_dog_conceded_avg", self.dog_conceded_avg),
            ("hb_fav_conceded_avg", self.fav_conceded_avg),
            ("hb_net_goal_differential_gap", self.net_goal_differential_gap),
        ]

        for name, val in optional_fields:
            features[f"{name}_missing"] = 1.0 if val is None else 0.0
            features[name] = float(val) if val is not None else 0.0

        return features


def extract_handball_features(
    event: EventSnapshot,
    candidate: RobberCandidate,
    h2h: H2HStats | None = None,
    recent_1: RecentForm | None = None,
    recent_2: RecentForm | None = None,
) -> HandballFeatures:
    """Extract complete, leak-safe HandballFeatures from pre-event snapshots."""
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
    entropy = shannon_entropy_handball(p1, px, p2)
    dominance = fav_prob / max(0.01, dog_prob)
    calls_dog = 1.0 if event.forebet_pick == dog else 0.0
    draw_pressure = (px / max(0.01, p1 + p2)) if px is not None else 0.0

    dog_odds = event.odds(dog)
    fav_odds = event.odds(fav)
    draw_odds = _safe_float(facets.get("odds_draw") or facets.get("odds_x") or facets.get("best_odd_X"))
    overround = calculate_overround_handball(event.odds_1, draw_odds, event.odds_2)
    devig_1, devig_x, devig_2 = devig_probabilities_handball(event.odds_1, draw_odds, event.odds_2)
    dog_fair_prob = devig_1 if dog == 1 else devig_2
    fav_fair_prob = devig_2 if dog == 1 else devig_1
    value_edge = (dog_prob - dog_fair_prob) if dog_fair_prob is not None else None

    # Scores & Totals
    sc_1, sc_2 = parse_score_string(event.predicted_score)
    total_goals = _safe_float(event.predicted_total)
    if total_goals is None and sc_1 is not None and sc_2 is not None:
        total_goals = sc_1 + sc_2

    dog_sc = sc_1 if dog == 1 else sc_2
    fav_sc = sc_2 if dog == 1 else sc_1
    goal_margin_dog = (dog_sc - fav_sc) if (dog_sc is not None and fav_sc is not None) else None

    close_game = 1.0 if (goal_margin_dog is not None and goal_margin_dog >= -2.0) else 0.0
    low_total = 1.0 if (total_goals is not None and total_goals <= 52.5) else 0.0
    high_total = 1.0 if (total_goals is not None and total_goals >= 61.5) else 0.0

    # Halves Splits (H1, H2)
    period_values = facets.get("period_values") or facets.get("halves") or facets.get("half_values")
    h1_m: float | None = None
    h2_m: float | None = None
    halves_won = 0.0

    if isinstance(period_values, (list, tuple)) and len(period_values) >= 2:
        p0 = period_values[0]
        p1_val = period_values[1]
        if isinstance(p0, (list, tuple)) and len(p0) >= 2:
            v1, v2 = _safe_float(p0[0]), _safe_float(p0[1])
            if v1 is not None and v2 is not None:
                h1_m = (v1 - v2) if dog == 1 else (v2 - v1)
                if h1_m >= 0:
                    halves_won += 1.0
        if isinstance(p1_val, (list, tuple)) and len(p1_val) >= 2:
            v1, v2 = _safe_float(p1_val[0]), _safe_float(p1_val[1])
            if v1 is not None and v2 is not None:
                h2_m = (v1 - v2) if dog == 1 else (v2 - v1)
                if h2_m >= 0:
                    halves_won += 1.0

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

    gd_1 = _safe_float(facets.get("standings_1_gd"))
    gd_2 = _safe_float(facets.get("standings_2_gd"))
    gd_gap = (gd_1 - gd_2) if (gd_1 is not None and gd_2 is not None) else None
    if dog == 2 and gd_gap is not None:
        gd_gap = -gd_gap

    # Travel & Detail Match Averages
    dist_km = _safe_float(facets.get("travel_distance_km") or facets.get("detail_travel_distance_km"))
    dog_travel = (dist_km if dog == 2 else 0.0) if dist_km is not None else None
    fav_travel = (dist_km if fav == 2 else 0.0) if dist_km is not None else None

    sc1_avg = _safe_float(facets.get("p1_scored_avg") or facets.get("detail_p1_scored_avg"))
    sc2_avg = _safe_float(facets.get("p2_scored_avg") or facets.get("detail_p2_scored_avg"))
    conc1_avg = _safe_float(facets.get("p1_conceded_avg") or facets.get("detail_p1_conceded_avg"))
    conc2_avg = _safe_float(facets.get("p2_conceded_avg") or facets.get("detail_p2_conceded_avg"))

    dog_sc_avg = sc1_avg if dog == 1 else sc2_avg
    fav_sc_avg = sc2_avg if dog == 1 else sc1_avg
    dog_conc_avg = conc1_avg if dog == 1 else conc2_avg
    fav_conc_avg = conc2_avg if dog == 1 else conc1_avg
    net_goal_gap = None
    if dog_sc_avg is not None and dog_conc_avg is not None and fav_sc_avg is not None and fav_conc_avg is not None:
        net_goal_gap = (dog_sc_avg - dog_conc_avg) - (fav_sc_avg - fav_conc_avg)

    # H2H
    h2h_games = float(h2h.total_games or facets.get("h2h_total_games") or 0)
    h2h_dog_wins = float(h2h.wins(dog) or 0)
    h2h_wr = (h2h_dog_wins / h2h_games) if h2h_games > 0 else 0.0
    has_dog_win = 1.0 if h2h_dog_wins > 0 else 0.0

    return HandballFeatures(
        is_home_dog=is_home_dog,
        dog_index=dog,
        favorite_index=fav,
        forebet_dog_prob=dog_prob,
        forebet_favorite_prob=fav_prob,
        forebet_draw_prob=px,
        forebet_prob_gap=prob_gap,
        forebet_entropy=entropy,
        draw_pressure_ratio=draw_pressure,
        favorite_dominance_ratio=dominance,
        forebet_calls_dog=calls_dog,
        price_available=1.0 if (dog_odds is not None and fav_odds is not None) else 0.0,
        dog_price=dog_odds,
        favorite_price=fav_odds,
        draw_price=draw_odds,
        market_overround=overround,
        dog_fair_implied_prob=dog_fair_prob,
        favorite_fair_implied_prob=fav_fair_prob,
        draw_fair_implied_prob=devig_x,
        price_value_edge=value_edge,
        predicted_total_goals=total_goals,
        predicted_goal_margin_dog=goal_margin_dog,
        dog_predicted_score=dog_sc,
        fav_predicted_score=fav_sc,
        close_game_expectation=close_game,
        low_total_environment=low_total,
        high_total_environment=high_total,
        h1_margin_dog=h1_m,
        h2_margin_dog=h2_m,
        halves_projected_won=halves_won,
        dog_recent_win_rate=dog_wr,
        favorite_recent_win_rate=fav_wr,
        win_rate_gap=dog_wr - fav_wr,
        dog_recent_games=dog_games,
        dog_rank=dog_rank,
        favorite_rank=fav_rank,
        rank_gap=rank_gap,
        standings_pts_gap=pts_gap,
        standings_gd_gap=gd_gap,
        travel_distance_km=dist_km,
        dog_travel_distance=dog_travel,
        fav_travel_distance=fav_travel,
        dog_scored_avg=dog_sc_avg,
        fav_scored_avg=fav_sc_avg,
        dog_conceded_avg=dog_conc_avg,
        fav_conceded_avg=fav_conc_avg,
        net_goal_differential_gap=net_goal_gap,
        h2h_total_games=h2h_games,
        h2h_dog_win_rate=h2h_wr,
        h2h_has_dog_win=has_dog_win,
        legacy_robber_score=candidate.score,
        legacy_raw_confidence=candidate.raw_confidence,
    )


def detect_handball_robber(
    event: EventSnapshot,
    h2h: H2HStats | None = None,
    recent_1: RecentForm | None = None,
    recent_2: RecentForm | None = None,
    config: RobberConfig | None = None,
) -> RobberCandidate | None:
    """Dedicated Handball 3-Way Robber detector with home-underdog, travel, and tight-margin bonuses."""
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

    # Home Court Advantage
    if dog_idx == 1:
        score += 4.0
        reasons.append("Home Court Underdog (+4)")

    # Score Margin / Tight Game Expectation
    sc_1, sc_2 = parse_score_string(event.predicted_score)
    if sc_1 is not None and sc_2 is not None:
        dog_sc = sc_1 if dog_idx == 1 else sc_2
        fav_sc = sc_2 if dog_idx == 1 else sc_1
        margin = dog_sc - fav_sc
        if margin >= -2.0:
            score += 5.0
            reasons.append(f"Tight game expectation ({margin:+.1f} goals)")

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

    # Travel Road Fatigue Catalyst
    facets = event.pre_event_facets()
    dist = _safe_float(facets.get("travel_distance_km") or facets.get("detail_travel_distance_km"))
    if dist and dist >= 500.0 and dog_idx == 1:
        score += 5.0
        reasons.append(f"Fav Away Road Fatigue ({int(dist)}km)")

    # Odds Value Factor (1X2 3-Way)
    if odds_avail and dog_odds is not None:
        if 2.20 <= dog_odds <= 4.20:
            score += 15.0
            reasons.append(f"Value @{dog_odds:.2f}")
        elif 1.80 <= dog_odds < 2.20:
            score += 10.0
            reasons.append(f"Playable @{dog_odds:.2f}")
        elif 4.20 < dog_odds <= 7.00:
            score += 8.0
            reasons.append(f"High payout @{dog_odds:.2f}")
        else:
            score += 4.0
            reasons.append(f"Longshot @{dog_odds:.2f}")
    else:
        reasons.append("Unpriced handball match")

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
        sport="handball",
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
