"""Sport 6 (American Football) dedicated domain models, feature engineering, and pipeline.

American Football is a 2-way sport (including overtime) characterized by:
- 4 quarters structure (Q1-Q4) + OT with discrete scoring (touchdowns, field goals)
- Key margins around 3 points (field goal) and 7 points (converted touchdown)
- One-score game expectation (predicted margin within 7.0 points)
- Low-total environment (<= 41.5 defensive battle) vs shootout (>= 51.5)
- Substantial home field advantage (~2.5-3.0 point baseline)
- Quarter and First Half (Q1+Q2) split dynamics

This module provides:
- American Football-specific feature extraction (quarter splits, one-score game proxy, 2-way de-vigging)
- Standings, point differential, and win-percentage gap metrics
- Dedicated 2-way Robber detector with home-underdog, one-score, and low-total bonuses
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


def calculate_overround_af(odds_1: float | None, odds_2: float | None) -> float | None:
    if odds_1 is None or odds_2 is None or odds_1 <= 1.0 or odds_2 <= 1.0:
        return None
    return max(0.0, (1.0 / odds_1) + (1.0 / odds_2) - 1.0)


def devig_probabilities_af(
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


def shannon_entropy_af(p1: float | None, p2: float | None) -> float:
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
class AmericanFootballFeatures:
    """Typed container for complete pre-event american football features."""
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

    # Points, Margins & Total Environment
    predicted_total_points: float | None
    predicted_point_margin_dog: float | None
    dog_predicted_score: float | None
    fav_predicted_score: float | None
    one_score_game_expectation: float  # 1.0 if dog predicted margin >= -7.0
    field_goal_game_expectation: float # 1.0 if dog predicted margin >= -3.0
    low_total_environment: float        # 1.0 if total <= 41.5
    high_total_environment: float       # 1.0 if total >= 51.5

    # Quarter Splits (Q1 - Q4) & First Half Margin
    q1_margin_dog: float | None
    q2_margin_dog: float | None
    q3_margin_dog: float | None
    q4_margin_dog: float | None
    h1_margin_dog: float | None
    quarters_projected_won: float

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
    standings_win_pct_gap: float | None

    # H2H Matchup History
    h2h_total_games: float
    h2h_dog_win_rate: float
    h2h_has_dog_win: float

    # Legacy & Meta
    legacy_robber_score: float
    legacy_raw_confidence: float

    def to_dict(self) -> dict[str, float]:
        features: dict[str, float] = {
            "af_is_home_dog": self.is_home_dog,
            "af_forebet_dog_prob": self.forebet_dog_prob,
            "af_forebet_favorite_prob": self.forebet_favorite_prob,
            "af_forebet_prob_gap": self.forebet_prob_gap,
            "af_forebet_entropy": self.forebet_entropy,
            "af_favorite_dominance_ratio": self.favorite_dominance_ratio,
            "af_forebet_calls_dog": self.forebet_calls_dog,
            "af_price_available": self.price_available,
            "af_one_score_game_expectation": self.one_score_game_expectation,
            "af_field_goal_game_expectation": self.field_goal_game_expectation,
            "af_low_total_environment": self.low_total_environment,
            "af_high_total_environment": self.high_total_environment,
            "af_quarters_projected_won": self.quarters_projected_won,
            "af_dog_recent_win_rate": self.dog_recent_win_rate,
            "af_favorite_recent_win_rate": self.favorite_recent_win_rate,
            "af_win_rate_gap": self.win_rate_gap,
            "af_dog_recent_games": self.dog_recent_games,
            "af_h2h_total_games": self.h2h_total_games,
            "af_h2h_dog_win_rate": self.h2h_dog_win_rate,
            "af_h2h_has_dog_win": self.h2h_has_dog_win,
            "af_legacy_robber_score": self.legacy_robber_score,
            "af_legacy_raw_confidence": self.legacy_raw_confidence,
        }

        optional_fields: list[tuple[str, float | None]] = [
            ("af_dog_price", self.dog_price),
            ("af_favorite_price", self.favorite_price),
            ("af_market_overround", self.market_overround),
            ("af_dog_fair_implied_prob", self.dog_fair_implied_prob),
            ("af_favorite_fair_implied_prob", self.favorite_fair_implied_prob),
            ("af_price_value_edge", self.price_value_edge),
            ("af_predicted_total_points", self.predicted_total_points),
            ("af_predicted_point_margin_dog", self.predicted_point_margin_dog),
            ("af_dog_predicted_score", self.dog_predicted_score),
            ("af_fav_predicted_score", self.fav_predicted_score),
            ("af_q1_margin_dog", self.q1_margin_dog),
            ("af_q2_margin_dog", self.q2_margin_dog),
            ("af_q3_margin_dog", self.q3_margin_dog),
            ("af_q4_margin_dog", self.q4_margin_dog),
            ("af_h1_margin_dog", self.h1_margin_dog),
            ("af_dog_rank", self.dog_rank),
            ("af_favorite_rank", self.favorite_rank),
            ("af_rank_gap", self.rank_gap),
            ("af_standings_pts_gap", self.standings_pts_gap),
            ("af_standings_win_pct_gap", self.standings_win_pct_gap),
        ]

        for name, val in optional_fields:
            features[f"{name}_missing"] = 1.0 if val is None else 0.0
            features[name] = float(val) if val is not None else 0.0

        return features


def extract_american_football_features(
    event: EventSnapshot,
    candidate: RobberCandidate,
    h2h: H2HStats | None = None,
    recent_1: RecentForm | None = None,
    recent_2: RecentForm | None = None,
) -> AmericanFootballFeatures:
    """Extract complete, leak-safe AmericanFootballFeatures from pre-event snapshots."""
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
    entropy = shannon_entropy_af(p1, p2)
    dominance = fav_prob / max(0.01, dog_prob)
    calls_dog = 1.0 if event.forebet_pick == dog else 0.0

    dog_odds = event.odds(dog)
    fav_odds = event.odds(fav)
    overround = calculate_overround_af(event.odds_1, event.odds_2)
    devig_1, devig_2 = devig_probabilities_af(event.odds_1, event.odds_2)
    dog_fair_prob = devig_1 if dog == 1 else devig_2
    fav_fair_prob = devig_2 if dog == 1 else devig_1
    value_edge = (dog_prob - dog_fair_prob) if dog_fair_prob is not None else None

    # Scores & Totals
    sc_1, sc_2 = parse_score_string(event.predicted_score)
    total_pts = _safe_float(event.predicted_total)
    if total_pts is None and sc_1 is not None and sc_2 is not None:
        total_pts = sc_1 + sc_2

    dog_sc = sc_1 if dog == 1 else sc_2
    fav_sc = sc_2 if dog == 1 else sc_1
    point_margin_dog = (dog_sc - fav_sc) if (dog_sc is not None and fav_sc is not None) else None

    one_score = 1.0 if (point_margin_dog is not None and point_margin_dog >= -7.0) else 0.0
    field_goal = 1.0 if (point_margin_dog is not None and point_margin_dog >= -3.0) else 0.0
    low_total = 1.0 if (total_pts is not None and total_pts <= 41.5) else 0.0
    high_total = 1.0 if (total_pts is not None and total_pts >= 51.5) else 0.0

    # Quarter Splits
    quarter_values = facets.get("quarter_values") or facets.get("quarters") or facets.get("period_values")
    q_margins: list[float | None] = [None, None, None, None]
    quarters_won = 0.0

    if isinstance(quarter_values, (list, tuple)):
        for idx in range(min(4, len(quarter_values))):
            item = quarter_values[idx]
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                v1 = _safe_float(item[0])
                v2 = _safe_float(item[1])
                if v1 is not None and v2 is not None:
                    m = (v1 - v2) if dog == 1 else (v2 - v1)
                    q_margins[idx] = m
                    if m >= 0:
                        quarters_won += 1.0

    q1_m, q2_m, q3_m, q4_m = q_margins
    h1_m = (q1_m + q2_m) if (q1_m is not None and q2_m is not None) else None

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

    w_1 = _safe_float(facets.get("standings_1_wins"))
    l_1 = _safe_float(facets.get("standings_1_losses"))
    w_2 = _safe_float(facets.get("standings_2_wins"))
    l_2 = _safe_float(facets.get("standings_2_losses"))
    win_pct_gap = None
    if w_1 is not None and l_1 is not None and (w_1 + l_1) > 0 and w_2 is not None and l_2 is not None and (w_2 + l_2) > 0:
        pct_1 = w_1 / (w_1 + l_1)
        pct_2 = w_2 / (w_2 + l_2)
        win_pct_gap = (pct_1 - pct_2) if dog == 1 else (pct_2 - pct_1)

    # H2H
    h2h_games = float(h2h.total_games or facets.get("h2h_total_games") or 0)
    h2h_dog_wins = float(h2h.wins(dog) or 0)
    h2h_wr = (h2h_dog_wins / h2h_games) if h2h_games > 0 else 0.0
    has_dog_win = 1.0 if h2h_dog_wins > 0 else 0.0

    return AmericanFootballFeatures(
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
        predicted_total_points=total_pts,
        predicted_point_margin_dog=point_margin_dog,
        dog_predicted_score=dog_sc,
        fav_predicted_score=fav_sc,
        one_score_game_expectation=one_score,
        field_goal_game_expectation=field_goal,
        low_total_environment=low_total,
        high_total_environment=high_total,
        q1_margin_dog=q1_m,
        q2_margin_dog=q2_m,
        q3_margin_dog=q3_m,
        q4_margin_dog=q4_m,
        h1_margin_dog=h1_m,
        quarters_projected_won=quarters_won,
        dog_recent_win_rate=dog_wr,
        favorite_recent_win_rate=fav_wr,
        win_rate_gap=dog_wr - fav_wr,
        dog_recent_games=dog_games,
        dog_rank=dog_rank,
        favorite_rank=fav_rank,
        rank_gap=rank_gap,
        standings_pts_gap=pts_gap,
        standings_win_pct_gap=win_pct_gap,
        h2h_total_games=h2h_games,
        h2h_dog_win_rate=h2h_wr,
        h2h_has_dog_win=has_dog_win,
        legacy_robber_score=candidate.score,
        legacy_raw_confidence=candidate.raw_confidence,
    )


def detect_american_football_robber(
    event: EventSnapshot,
    h2h: H2HStats | None = None,
    recent_1: RecentForm | None = None,
    recent_2: RecentForm | None = None,
    config: RobberConfig | None = None,
) -> RobberCandidate | None:
    """Dedicated American Football 2-Way Robber detector with home-field and one-score bonuses."""
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

    # Home Field Advantage
    if dog_idx == 1:
        score += 4.0
        reasons.append("Home Field Underdog (+4)")

    # Score Margin / One-Score Game Expectation
    sc_1, sc_2 = parse_score_string(event.predicted_score)
    if sc_1 is not None and sc_2 is not None:
        dog_sc = sc_1 if dog_idx == 1 else sc_2
        fav_sc = sc_2 if dog_idx == 1 else sc_1
        margin = dog_sc - fav_sc
        if margin >= -3.0:
            score += 6.0
            reasons.append(f"Field goal game expectation ({margin:+.1f} pts)")
        elif margin >= -7.0:
            score += 4.0
            reasons.append(f"One-score game expectation ({margin:+.1f} pts)")

    # Low Total / Defensive Environment
    total_pts = _safe_float(event.predicted_total)
    if total_pts is not None and total_pts <= 41.5:
        score += 4.0
        reasons.append(f"Defensive/low total ({total_pts:.1f} pts)")

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

    # Odds Value Factor (Moneyline 2-Way)
    if odds_avail and dog_odds is not None:
        if 2.10 <= dog_odds <= 4.00:
            score += 15.0
            reasons.append(f"Value @{dog_odds:.2f}")
        elif 1.80 <= dog_odds < 2.10:
            score += 10.0
            reasons.append(f"Playable @{dog_odds:.2f}")
        elif 4.00 < dog_odds <= 6.50:
            score += 8.0
            reasons.append(f"High payout @{dog_odds:.2f}")
        else:
            score += 4.0
            reasons.append(f"Longshot @{dog_odds:.2f}")
    else:
        reasons.append("Unpriced american football match")

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
        sport="american_football",
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
