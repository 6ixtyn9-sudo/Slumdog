"""Sport 9 (Volleyball) dedicated domain models, feature engineering, and pipeline.

Volleyball is a 2-way sport (Best of 5 sets: 3-0, 3-1, 3-2, 2-3, 1-3, 0-3) characterized by:
- 5 sets structure (S1-S5, first to 3 sets wins)
- Set margin dynamics and decider match volatility (3-2 / 2-3 deciders)
- Set point margins (25-point standard sets vs 15-point tiebreak 5th set)
- 2-way moneyline market pricing
- Pronounced home court advantage (service/reception familiarity) and travel fatigue
- Standings set differential and win-loss ratio metrics

This module provides:
- Volleyball-specific feature extraction (set splits, 5-set decider proxy, 2-way de-vigging, travel distance)
- Standings, set ratio, and form metrics
- Dedicated 2-way Robber detector with home-underdog, travel, and decider bonuses
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


def calculate_overround_volleyball(odds_1: float | None, odds_2: float | None) -> float | None:
    if odds_1 is None or odds_2 is None or odds_1 <= 1.0 or odds_2 <= 1.0:
        return None
    return max(0.0, (1.0 / odds_1) + (1.0 / odds_2) - 1.0)


def devig_probabilities_volleyball(
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


def shannon_entropy_volleyball(p1: float | None, p2: float | None) -> float:
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
class VolleyballFeatures:
    """Typed container for complete pre-event volleyball features."""
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

    # Pricing & 2-Way De-vigged Signals
    price_available: float
    dog_price: float | None
    favorite_price: float | None
    market_overround: float | None
    dog_fair_implied_prob: float | None
    favorite_fair_implied_prob: float | None
    price_value_edge: float | None

    # Sets, Margins & Match Dynamics
    predicted_total_sets: float | None
    predicted_set_margin_dog: float | None
    dog_predicted_sets: float | None
    fav_predicted_sets: float | None
    decider_match_expectation: float  # 1.0 if score is 3-2 / 2-3 or total sets >= 4.5
    sweep_match_expectation: float    # 1.0 if score is 3-0 / 0-3

    # Set Splits (S1 - S5)
    s1_margin_dog: float | None
    s2_margin_dog: float | None
    s3_margin_dog: float | None
    sets_projected_won: float

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
    standings_set_diff_gap: float | None

    # Spatial & Detail Match Averages
    travel_distance_km: float | None = None
    dog_travel_distance: float | None = None
    fav_travel_distance: float | None = None
    dog_scored_avg: float | None = None
    fav_scored_avg: float | None = None
    dog_conceded_avg: float | None = None
    fav_conceded_avg: float | None = None
    net_set_differential_gap: float | None = None

    # H2H Matchup History
    h2h_total_games: float = 0.0
    h2h_dog_win_rate: float = 0.0
    h2h_has_dog_win: float = 0.0

    # Legacy & Meta
    legacy_robber_score: float = 0.0
    legacy_raw_confidence: float = 0.0

    def to_dict(self) -> dict[str, float]:
        features: dict[str, float] = {
            "vb_is_home_dog": self.is_home_dog,
            "vb_forebet_dog_prob": self.forebet_dog_prob,
            "vb_forebet_favorite_prob": self.forebet_favorite_prob,
            "vb_forebet_prob_gap": self.forebet_prob_gap,
            "vb_forebet_entropy": self.forebet_entropy,
            "vb_favorite_dominance_ratio": self.favorite_dominance_ratio,
            "vb_forebet_calls_dog": self.forebet_calls_dog,
            "vb_price_available": self.price_available,
            "vb_decider_match_expectation": self.decider_match_expectation,
            "vb_sweep_match_expectation": self.sweep_match_expectation,
            "vb_sets_projected_won": self.sets_projected_won,
            "vb_dog_recent_win_rate": self.dog_recent_win_rate,
            "vb_favorite_recent_win_rate": self.favorite_recent_win_rate,
            "vb_win_rate_gap": self.win_rate_gap,
            "vb_dog_recent_games": self.dog_recent_games,
            "vb_h2h_total_games": self.h2h_total_games,
            "vb_h2h_dog_win_rate": self.h2h_dog_win_rate,
            "vb_h2h_has_dog_win": self.h2h_has_dog_win,
            "vb_legacy_robber_score": self.legacy_robber_score,
            "vb_legacy_raw_confidence": self.legacy_raw_confidence,
        }

        optional_fields: list[tuple[str, float | None]] = [
            ("vb_dog_price", self.dog_price),
            ("vb_favorite_price", self.favorite_price),
            ("vb_market_overround", self.market_overround),
            ("vb_dog_fair_implied_prob", self.dog_fair_implied_prob),
            ("vb_favorite_fair_implied_prob", self.favorite_fair_implied_prob),
            ("vb_price_value_edge", self.price_value_edge),
            ("vb_predicted_total_sets", self.predicted_total_sets),
            ("vb_predicted_set_margin_dog", self.predicted_set_margin_dog),
            ("vb_dog_predicted_sets", self.dog_predicted_sets),
            ("vb_fav_predicted_sets", self.fav_predicted_sets),
            ("vb_s1_margin_dog", self.s1_margin_dog),
            ("vb_s2_margin_dog", self.s2_margin_dog),
            ("vb_s3_margin_dog", self.s3_margin_dog),
            ("vb_dog_rank", self.dog_rank),
            ("vb_favorite_rank", self.favorite_rank),
            ("vb_rank_gap", self.rank_gap),
            ("vb_standings_pts_gap", self.standings_pts_gap),
            ("vb_standings_set_diff_gap", self.standings_set_diff_gap),
            ("vb_travel_distance_km", self.travel_distance_km),
            ("vb_dog_travel_distance", self.dog_travel_distance),
            ("vb_fav_travel_distance", self.fav_travel_distance),
            ("vb_dog_scored_avg", self.dog_scored_avg),
            ("vb_fav_scored_avg", self.fav_scored_avg),
            ("vb_dog_conceded_avg", self.dog_conceded_avg),
            ("vb_fav_conceded_avg", self.fav_conceded_avg),
            ("vb_net_set_differential_gap", self.net_set_differential_gap),
        ]

        for name, val in optional_fields:
            features[f"{name}_missing"] = 1.0 if val is None else 0.0
            features[name] = float(val) if val is not None else 0.0

        return features


def extract_volleyball_features(
    event: EventSnapshot,
    candidate: RobberCandidate,
    h2h: H2HStats | None = None,
    recent_1: RecentForm | None = None,
    recent_2: RecentForm | None = None,
) -> VolleyballFeatures:
    """Extract complete, leak-safe VolleyballFeatures from pre-event snapshots."""
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
    entropy = shannon_entropy_volleyball(p1, p2)
    dominance = fav_prob / max(0.01, dog_prob)
    calls_dog = 1.0 if event.forebet_pick == dog else 0.0

    dog_odds = event.odds(dog)
    fav_odds = event.odds(fav)
    overround = calculate_overround_volleyball(event.odds_1, event.odds_2)
    devig_1, devig_2 = devig_probabilities_volleyball(event.odds_1, event.odds_2)
    dog_fair_prob = devig_1 if dog == 1 else devig_2
    fav_fair_prob = devig_2 if dog == 1 else devig_1
    value_edge = (dog_prob - dog_fair_prob) if dog_fair_prob is not None else None

    # Scores & Totals
    sc_1, sc_2 = parse_score_string(event.predicted_score)
    total_sets = _safe_float(event.predicted_total)
    if total_sets is None and sc_1 is not None and sc_2 is not None:
        total_sets = sc_1 + sc_2

    dog_sc = sc_1 if dog == 1 else sc_2
    fav_sc = sc_2 if dog == 1 else sc_1
    set_margin_dog = (dog_sc - fav_sc) if (dog_sc is not None and fav_sc is not None) else None

    decider = 0.0
    sweep = 0.0
    if dog_sc is not None and fav_sc is not None:
        if (dog_sc == 2.0 and fav_sc == 3.0) or (dog_sc == 3.0 and fav_sc == 2.0):
            decider = 1.0
        elif (dog_sc == 0.0 and fav_sc == 3.0) or (dog_sc == 3.0 and fav_sc == 0.0):
            sweep = 1.0
    if total_sets is not None and total_sets >= 4.5:
        decider = 1.0

    # Set Splits
    period_values = facets.get("period_values") or facets.get("sets") or facets.get("set_values")
    s_margins: list[float | None] = [None, None, None]
    sets_won = 0.0

    if isinstance(period_values, (list, tuple)):
        for idx in range(min(3, len(period_values))):
            item = period_values[idx]
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                v1 = _safe_float(item[0])
                v2 = _safe_float(item[1])
                if v1 is not None and v2 is not None:
                    m = (v1 - v2) if dog == 1 else (v2 - v1)
                    s_margins[idx] = m
                    if m > 0:
                        sets_won += 1.0

    s1_m, s2_m, s3_m = s_margins

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

    gd_1 = _safe_float(facets.get("standings_1_gd") or facets.get("standings_1_sd"))
    gd_2 = _safe_float(facets.get("standings_2_gd") or facets.get("standings_2_sd"))
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
    net_set_gap = None
    if dog_sc_avg is not None and dog_conc_avg is not None and fav_sc_avg is not None and fav_conc_avg is not None:
        net_set_gap = (dog_sc_avg - dog_conc_avg) - (fav_sc_avg - fav_conc_avg)

    # H2H
    h2h_games = float(h2h.total_games or facets.get("h2h_total_games") or 0)
    h2h_dog_wins = float(h2h.wins(dog) or 0)
    h2h_wr = (h2h_dog_wins / h2h_games) if h2h_games > 0 else 0.0
    has_dog_win = 1.0 if h2h_dog_wins > 0 else 0.0

    return VolleyballFeatures(
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
        predicted_total_sets=total_sets,
        predicted_set_margin_dog=set_margin_dog,
        dog_predicted_sets=dog_sc,
        fav_predicted_sets=fav_sc,
        decider_match_expectation=decider,
        sweep_match_expectation=sweep,
        s1_margin_dog=s1_m,
        s2_margin_dog=s2_m,
        s3_margin_dog=s3_m,
        sets_projected_won=sets_won,
        dog_recent_win_rate=dog_wr,
        favorite_recent_win_rate=fav_wr,
        win_rate_gap=dog_wr - fav_wr,
        dog_recent_games=dog_games,
        dog_rank=dog_rank,
        favorite_rank=fav_rank,
        rank_gap=rank_gap,
        standings_pts_gap=pts_gap,
        standings_set_diff_gap=gd_gap,
        travel_distance_km=dist_km,
        dog_travel_distance=dog_travel,
        fav_travel_distance=fav_travel,
        dog_scored_avg=dog_sc_avg,
        fav_scored_avg=fav_sc_avg,
        dog_conceded_avg=dog_conc_avg,
        fav_conceded_avg=fav_conc_avg,
        net_set_differential_gap=net_set_gap,
        h2h_total_games=h2h_games,
        h2h_dog_win_rate=h2h_wr,
        h2h_has_dog_win=has_dog_win,
        legacy_robber_score=candidate.score,
        legacy_raw_confidence=candidate.raw_confidence,
    )


def detect_volleyball_robber(
    event: EventSnapshot,
    h2h: H2HStats | None = None,
    recent_1: RecentForm | None = None,
    recent_2: RecentForm | None = None,
    config: RobberConfig | None = None,
) -> RobberCandidate | None:
    """Dedicated Volleyball 2-Way Robber detector with home-court, travel, and decider bonuses."""
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

    # 5-Set Decider Match Expectation Bonus
    sc_1, sc_2 = parse_score_string(event.predicted_score)
    total_sets = _safe_float(event.predicted_total)
    if sc_1 is not None and sc_2 is not None:
        dog_sc = sc_1 if dog_idx == 1 else sc_2
        fav_sc = sc_2 if dog_idx == 1 else sc_1
        if (dog_sc == 2.0 and fav_sc == 3.0) or (dog_sc == 3.0 and fav_sc == 2.0):
            score += 5.0
            reasons.append("5-set decider expectation (3-2/2-3)")
    elif total_sets is not None and total_sets >= 4.5:
        score += 5.0
        reasons.append("5-set decider expectation (Total sets >= 4.5)")

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

    # Odds Value Factor (Moneyline 2-Way)
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
        reasons.append("Unpriced volleyball match")

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
        sport="volleyball",
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
