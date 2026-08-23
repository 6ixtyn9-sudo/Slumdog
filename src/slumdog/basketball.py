"""Sport 2 (Basketball) dedicated domain models, feature engineering, and pipeline.

Basketball is a 2-way (no-draw) sport characterized by:
- 4-quarter and 2-half scoring structure (Q1-Q4, H1, H2, OT)
- High point totals (pace environments: 140-240+ pts)
- Point margins and spread predictions
- Quarter-by-quarter consistency and second-half scoring resilience
- Significant home-court advantage dynamics

This module provides:
- Basketball-specific feature extraction (2-way de-vigging, quarter splits, pace proxy)
- Quarter and half margin calculations
- Dedicated 2-way basketball Robber detector
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


# ---------------------------------------------------------------------------
# Mathematical & Statistical Helpers
# ---------------------------------------------------------------------------

def _safe_float(value: Any) -> float | None:
    if value in (None, "", "-", "n/a", "N/A"):
        return None
    try:
        val = float(value)
        return val if math.isfinite(val) else None
    except (TypeError, ValueError):
        return None


def calculate_overround_2way(odds_1: float | None, odds_2: float | None) -> float | None:
    """Calculate 2-way bookmaker overround: sum(1/odds) - 1.0."""
    if odds_1 is None or odds_2 is None:
        return None
    if odds_1 <= 1.0 or odds_2 <= 1.0:
        return None
    return max(0.0, (1.0 / odds_1) + (1.0 / odds_2) - 1.0)


def devig_probabilities_2way(
    odds_1: float | None,
    odds_2: float | None,
) -> tuple[float | None, float | None]:
    """Compute fair 2-way de-vigged implied probabilities."""
    if odds_1 is None or odds_2 is None:
        return None, None
    if odds_1 <= 1.0 or odds_2 <= 1.0:
        return None, None
    imp_1, imp_2 = 1.0 / odds_1, 1.0 / odds_2
    total = imp_1 + imp_2
    if total <= 0.0:
        return None, None
    return imp_1 / total, imp_2 / total


def shannon_entropy_2way(p1: float | None, p2: float | None) -> float:
    """Compute Shannon entropy for the 2-way distribution."""
    probs = [p for p in (p1, p2) if p is not None and p > 0]
    total = sum(probs)
    if total <= 0:
        return 0.0
    normalized = [p / total for p in probs]
    return -sum(p * math.log(p) for p in normalized)


def parse_score_string(score_str: str) -> tuple[float | None, float | None]:
    """Parse '78-84' or '78:84' into (78.0, 84.0)."""
    match = re.search(r"(\d+)\s*[-:]\s*(\d+)", str(score_str or ""))
    if not match:
        return None, None
    return float(match.group(1)), float(match.group(2))


# ---------------------------------------------------------------------------
# Basketball Feature Container
# ---------------------------------------------------------------------------

@dataclass
class BasketballFeatures:
    """Typed container for complete pre-event basketball features."""
    # Context & Role
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

    # Pricing & 2-Way De-Vigged Signals
    price_available: float
    dog_price: float | None
    favorite_price: float | None
    market_overround: float | None
    dog_fair_implied_prob: float | None
    favorite_fair_implied_prob: float | None
    price_value_edge: float | None

    # Points, Margins & Pace Environment
    predicted_total_points: float | None
    predicted_point_margin_dog: float | None
    dog_predicted_score: float | None
    fav_predicted_score: float | None
    high_pace_environment: float  # 1.0 if total > 200 pts, else 0.0

    # Quarter Predictions (Q1 - Q4)
    q1_margin_dog: float | None
    q2_margin_dog: float | None
    q3_margin_dog: float | None
    q4_margin_dog: float | None
    first_half_margin_dog: float | None
    second_half_margin_dog: float | None
    quarters_projected_won: float
    quarter_consistency_rate: float

    # Recent Form & Win Rates
    dog_recent_win_rate: float
    favorite_recent_win_rate: float
    win_rate_gap: float
    dog_recent_games: float

    # Standings & Quality Differential
    dog_rank: float | None
    favorite_rank: float | None
    rank_gap: float | None
    standings_pts_gap: float | None
    standings_win_pct_gap: float | None

    # H2H Matchup History
    h2h_total_games: float
    h2h_dog_win_rate: float
    h2h_has_dog_win: float
    h2h_period_dog_win_rate: float

    # Legacy & Meta
    legacy_robber_score: float
    legacy_raw_confidence: float

    def to_dict(self) -> dict[str, float]:
        """Produce flat dictionary of numeric features with missing flags."""
        features: dict[str, float] = {
            "bb_is_home_dog": self.is_home_dog,
            "bb_forebet_dog_prob": self.forebet_dog_prob,
            "bb_forebet_favorite_prob": self.forebet_favorite_prob,
            "bb_forebet_prob_gap": self.forebet_prob_gap,
            "bb_forebet_entropy": self.forebet_entropy,
            "bb_favorite_dominance_ratio": self.favorite_dominance_ratio,
            "bb_forebet_calls_dog": self.forebet_calls_dog,
            "bb_price_available": self.price_available,
            "bb_high_pace_environment": self.high_pace_environment,
            "bb_quarters_projected_won": self.quarters_projected_won,
            "bb_quarter_consistency_rate": self.quarter_consistency_rate,
            "bb_dog_recent_win_rate": self.dog_recent_win_rate,
            "bb_favorite_recent_win_rate": self.favorite_recent_win_rate,
            "bb_win_rate_gap": self.win_rate_gap,
            "bb_dog_recent_games": self.dog_recent_games,
            "bb_h2h_total_games": self.h2h_total_games,
            "bb_h2h_dog_win_rate": self.h2h_dog_win_rate,
            "bb_h2h_has_dog_win": self.h2h_has_dog_win,
            "bb_h2h_period_dog_win_rate": self.h2h_period_dog_win_rate,
            "bb_legacy_robber_score": self.legacy_robber_score,
            "bb_legacy_raw_confidence": self.legacy_raw_confidence,
        }

        optional_fields: list[tuple[str, float | None]] = [
            ("bb_dog_price", self.dog_price),
            ("bb_favorite_price", self.favorite_price),
            ("bb_market_overround", self.market_overround),
            ("bb_dog_fair_implied_prob", self.dog_fair_implied_prob),
            ("bb_favorite_fair_implied_prob", self.favorite_fair_implied_prob),
            ("bb_price_value_edge", self.price_value_edge),
            ("bb_predicted_total_points", self.predicted_total_points),
            ("bb_predicted_point_margin_dog", self.predicted_point_margin_dog),
            ("bb_dog_predicted_score", self.dog_predicted_score),
            ("bb_fav_predicted_score", self.fav_predicted_score),
            ("bb_q1_margin_dog", self.q1_margin_dog),
            ("bb_q2_margin_dog", self.q2_margin_dog),
            ("bb_q3_margin_dog", self.q3_margin_dog),
            ("bb_q4_margin_dog", self.q4_margin_dog),
            ("bb_first_half_margin_dog", self.first_half_margin_dog),
            ("bb_second_half_margin_dog", self.second_half_margin_dog),
            ("bb_dog_rank", self.dog_rank),
            ("bb_favorite_rank", self.favorite_rank),
            ("bb_rank_gap", self.rank_gap),
            ("bb_standings_pts_gap", self.standings_pts_gap),
            ("bb_standings_win_pct_gap", self.standings_win_pct_gap),
        ]

        for name, val in optional_fields:
            features[f"{name}_missing"] = 1.0 if val is None else 0.0
            features[name] = float(val) if val is not None else 0.0

        return features


# ---------------------------------------------------------------------------
# Basketball Feature Extraction
# ---------------------------------------------------------------------------

def extract_basketball_features(
    event: EventSnapshot,
    candidate: RobberCandidate,
    h2h: H2HStats | None = None,
    recent_1: RecentForm | None = None,
    recent_2: RecentForm | None = None,
) -> BasketballFeatures:
    """Extract complete, leak-safe BasketballFeatures from pre-event snapshots."""
    h2h = h2h or H2HStats()
    facets = event.pre_event_facets()

    dog = candidate.participant_index
    fav = 2 if dog == 1 else 1
    is_home_dog = 1.0 if dog == 1 else 0.0

    # 2-Way Probabilities
    p1 = event.probability_1 or 0.0
    p2 = event.probability_2 or 0.0
    dog_prob = p1 if dog == 1 else p2
    fav_prob = p2 if dog == 1 else p1
    prob_gap = fav_prob - dog_prob
    entropy = shannon_entropy_2way(p1, p2)
    dominance = fav_prob / max(0.01, dog_prob)
    calls_dog = 1.0 if event.forebet_pick == dog else 0.0

    # Prices & 2-Way De-vigging
    dog_odds = event.odds(dog)
    fav_odds = event.odds(fav)
    odds_1 = event.odds_1
    odds_2 = event.odds_2

    overround = calculate_overround_2way(odds_1, odds_2)
    devig_1, devig_2 = devig_probabilities_2way(odds_1, odds_2)
    dog_fair_prob = devig_1 if dog == 1 else devig_2
    fav_fair_prob = devig_2 if dog == 1 else devig_1
    value_edge = (dog_prob - dog_fair_prob) if (dog_fair_prob is not None) else None

    # Scores, Totals & Margins
    sc_1, sc_2 = parse_score_string(event.predicted_score)
    total_pts = _safe_float(event.predicted_total)
    if total_pts is None and sc_1 is not None and sc_2 is not None:
        total_pts = sc_1 + sc_2

    dog_sc = sc_1 if dog == 1 else sc_2
    fav_sc = sc_2 if dog == 1 else sc_1
    point_margin_dog = (dog_sc - fav_sc) if (dog_sc is not None and fav_sc is not None) else None
    high_pace = 1.0 if (total_pts is not None and total_pts >= 195.0) else 0.0

    # Quarters: period_values format is [["18", "22"], ["20", "20"], ...]
    periods = facets.get("period_values") or []
    quarter_margins: list[float] = []
    q_won = 0.0
    for q_cell in periods[:4]:
        if isinstance(q_cell, (list, tuple)) and len(q_cell) >= 2:
            q_p1 = _safe_float(q_cell[0])
            q_p2 = _safe_float(q_cell[1])
            if q_p1 is not None and q_p2 is not None:
                diff = (q_p1 - q_p2) if dog == 1 else (q_p2 - q_p1)
                quarter_margins.append(diff)
                if diff >= 0:
                    q_won += 1.0

    q1_margin = quarter_margins[0] if len(quarter_margins) > 0 else None
    q2_margin = quarter_margins[1] if len(quarter_margins) > 1 else None
    q3_margin = quarter_margins[2] if len(quarter_margins) > 2 else None
    q4_margin = quarter_margins[3] if len(quarter_margins) > 3 else None

    h1_margin = (q1_margin + q2_margin) if (q1_margin is not None and q2_margin is not None) else None
    h2_margin = (q3_margin + q4_margin) if (q3_margin is not None and q4_margin is not None) else None
    consistency_rate = (q_won / len(quarter_margins)) if quarter_margins else 0.0

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
    h2h_dog_wins = float(h2h.wins(dog) or (facets.get("h2h_participant_1_wins") if dog == 1 else facets.get("h2h_participant_2_wins")) or 0)
    h2h_wr = (h2h_dog_wins / h2h_games) if h2h_games > 0 else 0.0
    has_dog_win = 1.0 if h2h_dog_wins > 0 else 0.0

    p_rates = h2h.period_rates(dog)
    h2h_period_wr = (sum(p_rates) / len(p_rates)) if p_rates else 0.0

    return BasketballFeatures(
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
        high_pace_environment=high_pace,
        q1_margin_dog=q1_margin,
        q2_margin_dog=q2_margin,
        q3_margin_dog=q3_margin,
        q4_margin_dog=q4_margin,
        first_half_margin_dog=h1_margin,
        second_half_margin_dog=h2_margin,
        quarters_projected_won=q_won,
        quarter_consistency_rate=consistency_rate,
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
        h2h_period_dog_win_rate=h2h_period_wr,
        legacy_robber_score=candidate.score,
        legacy_raw_confidence=candidate.raw_confidence,
    )


# ---------------------------------------------------------------------------
# Dedicated Basketball Robber Detector
# ---------------------------------------------------------------------------

def detect_basketball_robber(
    event: EventSnapshot,
    h2h: H2HStats | None = None,
    recent_1: RecentForm | None = None,
    recent_2: RecentForm | None = None,
    config: RobberConfig | None = None,
) -> RobberCandidate | None:
    """Dedicated Basketball 2-Way Robber detector."""
    config = config or RobberConfig()
    h2h = h2h or H2HStats()

    # Determine Underdog Identity in 2-Way Moneyline
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

    # H2H Upset Factor
    if h2h.total_games >= config.min_h2h_games:
        wins = h2h.wins(dog_idx)
        rate = wins / h2h.total_games
        if rate >= config.underdog_win_threshold:
            score += 20.0
            reasons.append(f"H2H {round(rate * 100)}% ({wins}/{h2h.total_games})")
        elif wins > 0:
            score += 8.0
            reasons.append(f"Prior H2H win ({wins})")

    # Period / Quarter Dominance
    dominant = sum(1 for value in h2h.period_rates(dog_idx) if value > 0.50)
    if dominant >= 2:
        score += 12.0
        reasons.append(f"Quarter advantage ({dominant})")
    elif dominant == 1:
        score += 5.0
        reasons.append("Quarter advantage (1)")

    # Form Momentum
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
        if 2.20 <= dog_odds <= 4.50:
            score += 15.0
            reasons.append(f"Prime value @{dog_odds:.2f}")
        elif 1.90 <= dog_odds < 2.20:
            score += 10.0
            reasons.append(f"Playable @{dog_odds:.2f}")
        elif 4.50 < dog_odds <= 7.00:
            score += 8.0
            reasons.append(f"High payout @{dog_odds:.2f}")
        else:
            score += 4.0
            reasons.append(f"Longshot @{dog_odds:.2f}")
    else:
        reasons.append("Unpriced 2-way match")

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
        sport="basketball",
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
