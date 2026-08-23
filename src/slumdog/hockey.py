"""Sport 4 (Hockey) dedicated domain models, feature engineering, and pipeline.

Ice Hockey is a 2-way sport (including Overtime and Shootouts) characterized by:
- 3 regulation periods (P1, P2, P3) + Overtime (OT)
- High overtime/shootout frequency (~20-25% of games go to extra time)
- Low total goal expectations (typically 4.5 to 6.5 goals)
- High variance / one-goal margin frequency
- Home ice advantage (last line change, faceoff positioning) and road fatigue

This module provides:
- Hockey-specific feature extraction (period splits, tight-game / OT proxy, 2-way de-vigging, travel distance)
- Goal differential, shot metrics, and standings point gap metrics
- Dedicated 2-way hockey Robber detector with home-ice, travel, and tight-total bonuses
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


def calculate_overround_hockey(odds_1: float | None, odds_2: float | None) -> float | None:
    if odds_1 is None or odds_2 is None or odds_1 <= 1.0 or odds_2 <= 1.0:
        return None
    return max(0.0, (1.0 / odds_1) + (1.0 / odds_2) - 1.0)


def devig_probabilities_hockey(
    odds_1: float | None,
    odds_2: float | None,
) -> tuple[float | None, float | None]:
    if odds_1 is None or odds_2 is None or odds_1 <= 1.0 or odds_2 <= 1.0:
        return None, None
    imp_1, imp_2 = 1.0 / odds_1, 1.0 / odds_2
    total = imp_1 + imp_2
    if total <= 0.0:
        return None, None
    return imp_1 / total, imp_2 / total


def shannon_entropy_hockey(p1: float | None, p2: float | None) -> float:
    probs = [p for p in (p1, p2) if p is not None and p > 0]
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
class HockeyFeatures:
    """Typed container for complete pre-event ice hockey features."""
    is_home_dog: float
    dog_index: int
    favorite_index: int

    # 2-Way Forebet Probabilities
    forebet_dog_prob: float
    forebet_favorite_prob: float
    forebet_prob_gap: float
    forebet_entropy: float
    favorite_dominance_ratio: float
    forebet_calls_dog: float

    # Pricing & 2-Way De-vigged Market Signals
    price_available: float
    dog_price: float | None
    favorite_price: float | None
    market_overround: float | None
    dog_fair_implied_prob: float | None
    favorite_fair_implied_prob: float | None
    price_value_edge: float | None

    # Goals, Margins & Total Environment
    predicted_total_goals: float | None
    predicted_goal_margin_dog: float | None
    dog_predicted_score: float | None
    fav_predicted_score: float | None
    low_total_environment: float  # 1.0 if total <= 5.0 goals, indicating high upset variance

    # Period Predictions (P1, P2, P3)
    p1_margin_dog: float | None
    p2_margin_dog: float | None
    p3_margin_dog: float | None
    periods_projected_won: float
    period_consistency_rate: float

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

    # Spatial, Detail & Tactical Differentials
    travel_distance_km: float | None = None
    dog_travel_distance: float | None = None
    fav_travel_distance: float | None = None
    dog_scored_avg: float | None = None
    fav_scored_avg: float | None = None
    dog_conceded_avg: float | None = None
    fav_conceded_avg: float | None = None
    net_goal_differential_gap: float | None = None
    dog_shots_avg: float | None = None
    fav_shots_avg: float | None = None
    shots_avg_gap: float | None = None

    # H2H Matchup History
    h2h_total_games: float = 0.0
    h2h_dog_win_rate: float = 0.0
    h2h_has_dog_win: float = 0.0
    h2h_period_dog_win_rate: float = 0.0

    # Legacy & Meta
    legacy_robber_score: float = 0.0
    legacy_raw_confidence: float = 0.0

    def to_dict(self) -> dict[str, float]:
        features: dict[str, float] = {
            "hk_is_home_dog": self.is_home_dog,
            "hk_forebet_dog_prob": self.forebet_dog_prob,
            "hk_forebet_favorite_prob": self.forebet_favorite_prob,
            "hk_forebet_prob_gap": self.forebet_prob_gap,
            "hk_forebet_entropy": self.forebet_entropy,
            "hk_favorite_dominance_ratio": self.favorite_dominance_ratio,
            "hk_forebet_calls_dog": self.forebet_calls_dog,
            "hk_price_available": self.price_available,
            "hk_low_total_environment": self.low_total_environment,
            "hk_periods_projected_won": self.periods_projected_won,
            "hk_period_consistency_rate": self.period_consistency_rate,
            "hk_dog_recent_win_rate": self.dog_recent_win_rate,
            "hk_favorite_recent_win_rate": self.favorite_recent_win_rate,
            "hk_win_rate_gap": self.win_rate_gap,
            "hk_dog_recent_games": self.dog_recent_games,
            "hk_h2h_total_games": self.h2h_total_games,
            "hk_h2h_dog_win_rate": self.h2h_dog_win_rate,
            "hk_h2h_has_dog_win": self.h2h_has_dog_win,
            "hk_h2h_period_dog_win_rate": self.h2h_period_dog_win_rate,
            "hk_legacy_robber_score": self.legacy_robber_score,
            "hk_legacy_raw_confidence": self.legacy_raw_confidence,
        }

        optional_fields: list[tuple[str, float | None]] = [
            ("hk_dog_price", self.dog_price),
            ("hk_favorite_price", self.favorite_price),
            ("hk_market_overround", self.market_overround),
            ("hk_dog_fair_implied_prob", self.dog_fair_implied_prob),
            ("hk_favorite_fair_implied_prob", self.favorite_fair_implied_prob),
            ("hk_price_value_edge", self.price_value_edge),
            ("hk_predicted_total_goals", self.predicted_total_goals),
            ("hk_predicted_goal_margin_dog", self.predicted_goal_margin_dog),
            ("hk_dog_predicted_score", self.dog_predicted_score),
            ("hk_fav_predicted_score", self.fav_predicted_score),
            ("hk_p1_margin_dog", self.p1_margin_dog),
            ("hk_p2_margin_dog", self.p2_margin_dog),
            ("hk_p3_margin_dog", self.p3_margin_dog),
            ("hk_dog_rank", self.dog_rank),
            ("hk_favorite_rank", self.favorite_rank),
            ("hk_rank_gap", self.rank_gap),
            ("hk_standings_pts_gap", self.standings_pts_gap),
            ("hk_standings_gd_gap", self.standings_gd_gap),
            ("hk_travel_distance_km", self.travel_distance_km),
            ("hk_dog_travel_distance", self.dog_travel_distance),
            ("hk_fav_travel_distance", self.fav_travel_distance),
            ("hk_dog_scored_avg", self.dog_scored_avg),
            ("hk_fav_scored_avg", self.fav_scored_avg),
            ("hk_dog_conceded_avg", self.dog_conceded_avg),
            ("hk_fav_conceded_avg", self.fav_conceded_avg),
            ("hk_net_goal_differential_gap", self.net_goal_differential_gap),
            ("hk_dog_shots_avg", self.dog_shots_avg),
            ("hk_fav_shots_avg", self.fav_shots_avg),
            ("hk_shots_avg_gap", self.shots_avg_gap),
        ]

        for name, val in optional_fields:
            features[f"{name}_missing"] = 1.0 if val is None else 0.0
            features[name] = float(val) if val is not None else 0.0

        return features


def extract_hockey_features(
    event: EventSnapshot,
    candidate: RobberCandidate,
    h2h: H2HStats | None = None,
    recent_1: RecentForm | None = None,
    recent_2: RecentForm | None = None,
) -> HockeyFeatures:
    """Extract complete, leak-safe HockeyFeatures from pre-event snapshots."""
    h2h = h2h or H2HStats()
    facets = event.pre_event_facets()

    dog = candidate.participant_index
    fav = 2 if dog == 1 else 1
    is_home_dog = 1.0 if dog == 1 else 0.0

    p1 = event.probability_1 or 0.0
    p2 = event.probability_2 or 0.0
    dog_prob = p1 if dog == 1 else p2
    fav_prob = p2 if dog == 1 else p1
    prob_gap = fav_prob - dog_prob
    entropy = shannon_entropy_hockey(p1, p2)
    dominance = fav_prob / max(0.01, dog_prob)
    calls_dog = 1.0 if event.forebet_pick == dog else 0.0

    dog_odds = event.odds(dog)
    fav_odds = event.odds(fav)
    odds_1 = event.odds_1
    odds_2 = event.odds_2

    overround = calculate_overround_hockey(odds_1, odds_2)
    devig_1, devig_2 = devig_probabilities_hockey(odds_1, odds_2)
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
    low_total = 1.0 if (total_goals is not None and total_goals <= 5.0) else 0.0

    # Periods (P1, P2, P3)
    periods = facets.get("period_values") or []
    period_margins: list[float] = []
    p_won = 0.0
    for p_cell in periods[:3]:
        if isinstance(p_cell, (list, tuple)) and len(p_cell) >= 2:
            p_p1 = _safe_float(p_cell[0])
            p_p2 = _safe_float(p_cell[1])
            if p_p1 is not None and p_p2 is not None:
                diff = (p_p1 - p_p2) if dog == 1 else (p_p2 - p_p1)
                period_margins.append(diff)
                if diff >= 0:
                    p_won += 1.0

    p1_margin = period_margins[0] if len(period_margins) > 0 else None
    p2_margin = period_margins[1] if len(period_margins) > 1 else None
    p3_margin = period_margins[2] if len(period_margins) > 2 else None
    consistency = (p_won / len(period_margins)) if period_margins else 0.0

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

    sh1_avg = _safe_float(facets.get("p1_total_shots_avg") or facets.get("detail_p1_total_shots_avg"))
    sh2_avg = _safe_float(facets.get("p2_total_shots_avg") or facets.get("detail_p2_total_shots_avg"))
    dog_sh = sh1_avg if dog == 1 else sh2_avg
    fav_sh = sh2_avg if dog == 1 else sh1_avg
    sh_gap = (dog_sh - fav_sh) if (dog_sh is not None and fav_sh is not None) else None

    # H2H
    h2h_games = float(h2h.total_games or facets.get("h2h_total_games") or 0)
    h2h_dog_wins = float(h2h.wins(dog) or 0)
    h2h_wr = (h2h_dog_wins / h2h_games) if h2h_games > 0 else 0.0
    has_dog_win = 1.0 if h2h_dog_wins > 0 else 0.0
    p_rates = h2h.period_rates(dog)
    h2h_period_wr = (sum(p_rates) / len(p_rates)) if p_rates else 0.0

    return HockeyFeatures(
        is_home_dog=is_home_dog,
        dog_index=dog,
        favorite_index=fav,
        forebet_dog_prob=dog_prob,
        forebet_favorite_prob=fav_prob,
        forebet_prob_gap=prob_gap,
        forebet_entropy=entropy,
        favorite_dominance_ratio=dominance,
        forebet_calls_dog=calls_dog,
        price_available=1.0 if (dog_odds is not None and fav_odds is not None) else 0.0,
        dog_price=dog_odds,
        favorite_price=fav_odds,
        market_overround=overround,
        dog_fair_implied_prob=dog_fair_prob,
        favorite_fair_implied_prob=fav_fair_prob,
        price_value_edge=value_edge,
        predicted_total_goals=total_goals,
        predicted_goal_margin_dog=goal_margin_dog,
        dog_predicted_score=dog_sc,
        fav_predicted_score=fav_sc,
        low_total_environment=low_total,
        p1_margin_dog=p1_margin,
        p2_margin_dog=p2_margin,
        p3_margin_dog=p3_margin,
        periods_projected_won=p_won,
        period_consistency_rate=consistency,
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
        dog_shots_avg=dog_sh,
        fav_shots_avg=fav_sh,
        shots_avg_gap=sh_gap,
        h2h_total_games=h2h_games,
        h2h_dog_win_rate=h2h_wr,
        h2h_has_dog_win=has_dog_win,
        h2h_period_dog_win_rate=h2h_period_wr,
        legacy_robber_score=candidate.score,
        legacy_raw_confidence=candidate.raw_confidence,
    )


def detect_hockey_robber(
    event: EventSnapshot,
    h2h: H2HStats | None = None,
    recent_1: RecentForm | None = None,
    recent_2: RecentForm | None = None,
    config: RobberConfig | None = None,
) -> RobberCandidate | None:
    """Dedicated Hockey 2-Way Robber detector with home-ice and tight-game weighting."""
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

    # Home Ice Advantage
    if dog_idx == 1:
        score += 4.0
        reasons.append("Home Ice Underdog (+4)")

    # Low Total / Tight Matchup Bonus
    total_goals = _safe_float(event.predicted_total)
    if total_goals is not None and total_goals <= 5.0:
        score += 5.0
        reasons.append(f"Tight game expectation (Total {total_goals:.1f})")

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

    # Period Dominance
    dominant = sum(1 for value in h2h.period_rates(dog_idx) if value > 0.50)
    if dominant >= 2:
        score += 12.0
        reasons.append(f"Period advantage ({dominant})")
    elif dominant == 1:
        score += 5.0
        reasons.append("Period advantage (1)")

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

    # Odds Value Factor
    if odds_avail and dog_odds is not None:
        if 2.10 <= dog_odds <= 4.20:
            score += 15.0
            reasons.append(f"Value @{dog_odds:.2f}")
        elif 1.85 <= dog_odds < 2.10:
            score += 10.0
            reasons.append(f"Playable @{dog_odds:.2f}")
        elif 4.20 < dog_odds <= 6.50:
            score += 8.0
            reasons.append(f"High payout @{dog_odds:.2f}")
        else:
            score += 4.0
            reasons.append(f"Longshot @{dog_odds:.2f}")
    else:
        reasons.append("Unpriced hockey match")

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
        sport="hockey",
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
