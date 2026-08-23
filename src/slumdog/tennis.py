"""Sport 3 (Tennis) dedicated domain models, feature engineering, and pipeline.

Tennis is an individual 2-way sport characterized by:
- Surface specificity (Hard, Clay, Grass) with dramatic player performance divergence
- Sets structure (S1-S5, best of 3 vs best of 5)
- Tournament round progression (Qualifying through Finals)
- Physical matchups (player height, service dominance vs return ability)
- Strict retirement / walkover void policies

This module provides:
- Surface-aware feature extraction (clay/hard/grass win rate gaps and sample sizes)
- Predicted set margins and game expectation metrics
- Dedicated 2-way tennis Robber detector with surface-specialist bonuses
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


def calculate_overround_tennis(odds_1: float | None, odds_2: float | None) -> float | None:
    if odds_1 is None or odds_2 is None or odds_1 <= 1.0 or odds_2 <= 1.0:
        return None
    return max(0.0, (1.0 / odds_1) + (1.0 / odds_2) - 1.0)


def devig_probabilities_tennis(
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


def shannon_entropy_tennis(p1: float | None, p2: float | None) -> float:
    probs = [p for p in (p1, p2) if p is not None and p > 0]
    total = sum(probs)
    if total <= 0:
        return 0.0
    normalized = [p / total for p in probs]
    return -sum(p * math.log(p) for p in normalized)


@dataclass
class TennisFeatures:
    """Typed container for complete pre-event tennis features."""
    dog_index: int
    favorite_index: int

    # 2-Way Forebet Probabilities
    forebet_dog_prob: float
    forebet_favorite_prob: float
    forebet_prob_gap: float
    forebet_entropy: float
    favorite_dominance_ratio: float
    forebet_calls_dog: float

    # Pricing & De-vigged Signals
    price_available: float
    dog_price: float | None
    favorite_price: float | None
    market_overround: float | None
    dog_fair_implied_prob: float | None
    favorite_fair_implied_prob: float | None
    price_value_edge: float | None

    # Surface Metrics (Clay, Hard, Grass)
    surface_dog_win_rate: float | None
    surface_fav_win_rate: float | None
    surface_win_rate_gap: float | None
    surface_dog_sample: float
    surface_fav_sample: float
    surface_specialist_dog: float  # 1.0 if dog surface win rate >= 0.60, else 0.0

    # Sets & Game Expectations
    predicted_set_margin_dog: float | None
    dog_predicted_sets: float | None
    fav_predicted_sets: float | None
    predicted_total_games: float | None

    # Player Attributes & Form
    dog_height_inches: float | None
    fav_height_inches: float | None
    height_gap_inches: float | None
    dog_recent_win_rate: float
    favorite_recent_win_rate: float
    win_rate_gap: float
    dog_recent_games: float

    # H2H Matchup History
    h2h_total_matches: float
    h2h_dog_win_rate: float
    h2h_has_dog_win: float

    # Legacy & Meta
    legacy_robber_score: float
    legacy_raw_confidence: float

    def to_dict(self) -> dict[str, float]:
        features: dict[str, float] = {
            "ten_forebet_dog_prob": self.forebet_dog_prob,
            "ten_forebet_favorite_prob": self.forebet_favorite_prob,
            "ten_forebet_prob_gap": self.forebet_prob_gap,
            "ten_forebet_entropy": self.forebet_entropy,
            "ten_favorite_dominance_ratio": self.favorite_dominance_ratio,
            "ten_forebet_calls_dog": self.forebet_calls_dog,
            "ten_price_available": self.price_available,
            "ten_surface_dog_sample": self.surface_dog_sample,
            "ten_surface_fav_sample": self.surface_fav_sample,
            "ten_surface_specialist_dog": self.surface_specialist_dog,
            "ten_dog_recent_win_rate": self.dog_recent_win_rate,
            "ten_favorite_recent_win_rate": self.favorite_recent_win_rate,
            "ten_win_rate_gap": self.win_rate_gap,
            "ten_dog_recent_games": self.dog_recent_games,
            "ten_h2h_total_matches": self.h2h_total_matches,
            "ten_h2h_dog_win_rate": self.h2h_dog_win_rate,
            "ten_h2h_has_dog_win": self.h2h_has_dog_win,
            "ten_legacy_robber_score": self.legacy_robber_score,
            "ten_legacy_raw_confidence": self.legacy_raw_confidence,
        }

        optional_fields: list[tuple[str, float | None]] = [
            ("ten_dog_price", self.dog_price),
            ("ten_favorite_price", self.favorite_price),
            ("ten_market_overround", self.market_overround),
            ("ten_dog_fair_implied_prob", self.dog_fair_implied_prob),
            ("ten_favorite_fair_implied_prob", self.favorite_fair_implied_prob),
            ("ten_price_value_edge", self.price_value_edge),
            ("ten_surface_dog_win_rate", self.surface_dog_win_rate),
            ("ten_surface_fav_win_rate", self.surface_fav_win_rate),
            ("ten_surface_win_rate_gap", self.surface_win_rate_gap),
            ("ten_predicted_set_margin_dog", self.predicted_set_margin_dog),
            ("ten_dog_predicted_sets", self.dog_predicted_sets),
            ("ten_fav_predicted_sets", self.fav_predicted_sets),
            ("ten_predicted_total_games", self.predicted_total_games),
            ("ten_dog_height_inches", self.dog_height_inches),
            ("ten_fav_height_inches", self.fav_height_inches),
            ("ten_height_gap_inches", self.height_gap_inches),
        ]

        for name, val in optional_fields:
            features[f"{name}_missing"] = 1.0 if val is None else 0.0
            features[name] = float(val) if val is not None else 0.0

        return features


def extract_tennis_features(
    event: EventSnapshot,
    candidate: RobberCandidate,
    h2h: H2HStats | None = None,
    recent_1: RecentForm | None = None,
    recent_2: RecentForm | None = None,
) -> TennisFeatures:
    """Extract complete, leak-safe TennisFeatures from pre-event snapshots."""
    h2h = h2h or H2HStats()
    facets = event.pre_event_facets()

    dog = candidate.participant_index
    fav = 2 if dog == 1 else 1

    p1 = event.probability_1 or 0.0
    p2 = event.probability_2 or 0.0
    dog_prob = p1 if dog == 1 else p2
    fav_prob = p2 if dog == 1 else p1
    prob_gap = fav_prob - dog_prob
    entropy = shannon_entropy_tennis(p1, p2)
    dominance = fav_prob / max(0.01, dog_prob)
    calls_dog = 1.0 if event.forebet_pick == dog else 0.0

    dog_odds = event.odds(dog)
    fav_odds = event.odds(fav)
    overround = calculate_overround_tennis(event.odds_1, event.odds_2)
    devig_1, devig_2 = devig_probabilities_tennis(event.odds_1, event.odds_2)
    dog_fair_prob = devig_1 if dog == 1 else devig_2
    fav_fair_prob = devig_2 if dog == 1 else devig_1
    value_edge = (dog_prob - dog_fair_prob) if dog_fair_prob is not None else None

    # Surface Metrics
    surface = str(facets.get("surface") or "hard").lower()
    dog_surf_wr = _safe_float(facets.get(f"p{dog}_{surface}_win_rate"))
    fav_surf_wr = _safe_float(facets.get(f"p{fav}_{surface}_win_rate"))
    dog_surf_s = _safe_float(facets.get(f"p{dog}_{surface}_sample")) or 0.0
    fav_surf_s = _safe_float(facets.get(f"p{fav}_{surface}_sample")) or 0.0
    surf_gap = (dog_surf_wr - fav_surf_wr) if (dog_surf_wr is not None and fav_surf_wr is not None) else None
    specialist = 1.0 if (dog_surf_wr is not None and dog_surf_wr >= 0.60 and dog_surf_s >= 10.0) else 0.0

    # Sets & Games
    match_sets = re.search(r"(\d+)\s*[-:]\s*(\d+)", str(event.predicted_score or ""))
    s1, s2 = (float(match_sets.group(1)), float(match_sets.group(2))) if match_sets else (None, None)
    dog_sets = s1 if dog == 1 else s2
    fav_sets = s2 if dog == 1 else s1
    set_margin = (dog_sets - fav_sets) if (dog_sets is not None and fav_sets is not None) else None
    total_games = _safe_float(event.predicted_total or facets.get("predicted_games"))

    # Physical Attributes
    h1 = _safe_float(facets.get("p1_height_inches") or facets.get("height_1"))
    h2 = _safe_float(facets.get("p2_height_inches") or facets.get("height_2"))
    dog_h = h1 if dog == 1 else h2
    fav_h = h2 if dog == 1 else h1
    h_gap = (dog_h - fav_h) if (dog_h is not None and fav_h is not None) else None

    # Form
    dog_recent = recent_1 if dog == 1 else recent_2
    fav_recent = recent_2 if dog == 1 else recent_1
    dog_wr = (dog_recent.win_rate or 0.0) if dog_recent else 0.0
    fav_wr = (fav_recent.win_rate or 0.0) if fav_recent else 0.0
    dog_games = float(dog_recent.games if dog_recent else 0)

    # H2H
    h2h_games = float(h2h.total_games or facets.get("h2h_total_games") or 0)
    h2h_dog_wins = float(h2h.wins(dog) or 0)
    h2h_wr = (h2h_dog_wins / h2h_games) if h2h_games > 0 else 0.0
    has_dog_win = 1.0 if h2h_dog_wins > 0 else 0.0

    return TennisFeatures(
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
        surface_dog_win_rate=dog_surf_wr,
        surface_fav_win_rate=fav_surf_wr,
        surface_win_rate_gap=surf_gap,
        surface_dog_sample=dog_surf_s,
        surface_fav_sample=fav_surf_s,
        surface_specialist_dog=specialist,
        predicted_set_margin_dog=set_margin,
        dog_predicted_sets=dog_sets,
        fav_predicted_sets=fav_sets,
        predicted_total_games=total_games,
        dog_height_inches=dog_h,
        fav_height_inches=fav_h,
        height_gap_inches=h_gap,
        dog_recent_win_rate=dog_wr,
        favorite_recent_win_rate=fav_wr,
        win_rate_gap=dog_wr - fav_wr,
        dog_recent_games=dog_games,
        h2h_total_matches=h2h_games,
        h2h_dog_win_rate=h2h_wr,
        h2h_has_dog_win=has_dog_win,
        legacy_robber_score=candidate.score,
        legacy_raw_confidence=candidate.raw_confidence,
    )


def detect_tennis_robber(
    event: EventSnapshot,
    h2h: H2HStats | None = None,
    recent_1: RecentForm | None = None,
    recent_2: RecentForm | None = None,
    config: RobberConfig | None = None,
) -> RobberCandidate | None:
    """Dedicated Tennis 2-Way Robber detector with surface specialization."""
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

    # Surface Specialist Factor
    facets = event.pre_event_facets()
    surface = str(facets.get("surface") or "hard").lower()
    dog_surf_wr = _safe_float(facets.get(f"p{dog_idx}_{surface}_win_rate"))
    dog_surf_s = _safe_float(facets.get(f"p{dog_idx}_{surface}_sample")) or 0.0
    if dog_surf_wr is not None and dog_surf_wr >= 0.60 and dog_surf_s >= 8.0:
        score += 6.0
        reasons.append(f"Surface Specialist ({surface.capitalize()} {round(dog_surf_wr * 100)}%)")

    # Favorite Strength Factor
    if odds_avail and fav_odds is not None:
        if fav_odds <= 1.25:
            score += 15.0
            reasons.append(f"Heavy fav @{fav_odds:.2f}")
        elif fav_odds <= 1.45:
            score += 12.0
            reasons.append(f"Strong fav @{fav_odds:.2f}")
        elif fav_odds <= 1.65:
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

    # Price Value Factor
    if odds_avail and dog_odds is not None:
        if 2.30 <= dog_odds <= 4.80:
            score += 15.0
            reasons.append(f"Value @{dog_odds:.2f}")
        elif 1.95 <= dog_odds < 2.30:
            score += 10.0
            reasons.append(f"Playable @{dog_odds:.2f}")
        elif 4.80 < dog_odds <= 8.00:
            score += 8.0
            reasons.append(f"High payout @{dog_odds:.2f}")
        else:
            score += 4.0
            reasons.append(f"Longshot @{dog_odds:.2f}")
    else:
        reasons.append("Unpriced tennis match")

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
        sport="tennis",
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
