"""Sport 12 (MMA) dedicated domain models, feature engineering, and pipeline.

MMA is a 2-way fight sport (3 or 5 rounds of 5 minutes each) characterized by:
- Tale of the Tape physical attributes (height, reach, stance, age)
- Striking vs Grappling clash of styles (takedowns, submissions, significant strikes)
- Finish probability (KO/TKO, submission, decision)
- Octagon ring control and momentum
- 2-way moneyline pricing

This module provides:
- MMA-specific feature extraction (reach advantage, finish indicators, strike/takedown differentials, 2-way de-vigging)
- Physical differential, stance matchups, and form metrics
- Dedicated 2-way Robber detector with reach advantage, southpaw edge, and finish potential bonuses
- Leak-safe numeric vector builder with explicit missingness flags
"""
from __future__ import annotations

import math
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


def calculate_overround_mma(odds_1: float | None, odds_2: float | None) -> float | None:
    if odds_1 is None or odds_2 is None or odds_1 <= 1.0 or odds_2 <= 1.0:
        return None
    return max(0.0, (1.0 / odds_1) + (1.0 / odds_2) - 1.0)


def devig_probabilities_mma(
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


def shannon_entropy_mma(p1: float | None, p2: float | None) -> float:
    probs = [p for p in (p1, p2) if p is not None and p > 0]
    total = sum(probs)
    if total <= 0:
        return 0.0
    normalized = [p / total for p in probs]
    return -sum(p * math.log(p) for p in normalized)


@dataclass
class MMAFeatures:
    """Typed container for complete pre-event MMA features."""
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

    # Tale of the Tape Physical Metrics
    dog_height_cm: float | None
    fav_height_cm: float | None
    height_gap_cm: float | None
    dog_reach_cm: float | None
    fav_reach_cm: float | None
    reach_gap_cm: float | None
    has_reach_advantage_dog: float # 1.0 if reach gap >= 5.0 cm
    is_southpaw_dog: float
    is_southpaw_fav: float

    # Striking & Grappling Metrics
    takedown_avg_dog: float | None
    takedown_avg_fav: float | None
    takedown_differential: float | None
    sig_strikes_landed_dog: float | None
    sig_strikes_landed_fav: float | None
    sig_strikes_differential: float | None
    ko_finish_potential: float
    sub_finish_potential: float

    # Form & Recent Performance
    dog_recent_win_rate: float
    favorite_recent_win_rate: float
    win_rate_gap: float
    dog_recent_games: float

    # H2H Matchup History
    h2h_total_games: float
    h2h_dog_win_rate: float
    h2h_has_dog_win: float

    # Legacy & Meta
    legacy_robber_score: float
    legacy_raw_confidence: float

    def to_dict(self) -> dict[str, float]:
        features: dict[str, float] = {
            "mma_forebet_dog_prob": self.forebet_dog_prob,
            "mma_forebet_favorite_prob": self.forebet_favorite_prob,
            "mma_forebet_prob_gap": self.forebet_prob_gap,
            "mma_forebet_entropy": self.forebet_entropy,
            "mma_favorite_dominance_ratio": self.favorite_dominance_ratio,
            "mma_forebet_calls_dog": self.forebet_calls_dog,
            "mma_has_reach_advantage_dog": self.has_reach_advantage_dog,
            "mma_is_southpaw_dog": self.is_southpaw_dog,
            "mma_is_southpaw_fav": self.is_southpaw_fav,
            "mma_ko_finish_potential": self.ko_finish_potential,
            "mma_sub_finish_potential": self.sub_finish_potential,
            "mma_price_available": self.price_available,
            "mma_dog_recent_win_rate": self.dog_recent_win_rate,
            "mma_favorite_recent_win_rate": self.favorite_recent_win_rate,
            "mma_win_rate_gap": self.win_rate_gap,
            "mma_dog_recent_games": self.dog_recent_games,
            "mma_h2h_total_games": self.h2h_total_games,
            "mma_h2h_dog_win_rate": self.h2h_dog_win_rate,
            "mma_h2h_has_dog_win": self.h2h_has_dog_win,
            "mma_legacy_robber_score": self.legacy_robber_score,
            "mma_legacy_raw_confidence": self.legacy_raw_confidence,
        }

        optional_fields: list[tuple[str, float | None]] = [
            ("mma_dog_price", self.dog_price),
            ("mma_favorite_price", self.favorite_price),
            ("mma_market_overround", self.market_overround),
            ("mma_dog_fair_implied_prob", self.dog_fair_implied_prob),
            ("mma_favorite_fair_implied_prob", self.favorite_fair_implied_prob),
            ("mma_price_value_edge", self.price_value_edge),
            ("mma_dog_height_cm", self.dog_height_cm),
            ("mma_fav_height_cm", self.fav_height_cm),
            ("mma_height_gap_cm", self.height_gap_cm),
            ("mma_dog_reach_cm", self.dog_reach_cm),
            ("mma_fav_reach_cm", self.fav_reach_cm),
            ("mma_reach_gap_cm", self.reach_gap_cm),
            ("mma_takedown_avg_dog", self.takedown_avg_dog),
            ("mma_takedown_avg_fav", self.takedown_avg_fav),
            ("mma_takedown_differential", self.takedown_differential),
            ("mma_sig_strikes_landed_dog", self.sig_strikes_landed_dog),
            ("mma_sig_strikes_landed_fav", self.sig_strikes_landed_fav),
            ("mma_sig_strikes_differential", self.sig_strikes_differential),
        ]

        for name, val in optional_fields:
            features[f"{name}_missing"] = 1.0 if val is None else 0.0
            features[name] = float(val) if val is not None else 0.0

        return features


def extract_mma_features(
    event: EventSnapshot,
    candidate: RobberCandidate,
    h2h: H2HStats | None = None,
    recent_1: RecentForm | None = None,
    recent_2: RecentForm | None = None,
) -> MMAFeatures:
    """Extract complete, leak-safe MMAFeatures from pre-event snapshots."""
    h2h = h2h or H2HStats()
    facets = event.pre_event_facets()

    dog = candidate.participant_index
    fav = 2 if dog == 1 else 1

    p1 = event.probability_1 or 0.0
    p2 = event.probability_2 or 0.0
    dog_prob = p1 if dog == 1 else p2
    fav_prob = p2 if dog == 1 else p1
    prob_gap = fav_prob - dog_prob
    entropy = shannon_entropy_mma(p1, p2)
    dominance = fav_prob / max(0.01, dog_prob)
    calls_dog = 1.0 if event.forebet_pick == dog else 0.0

    dog_odds = event.odds(dog)
    fav_odds = event.odds(fav)
    overround = calculate_overround_mma(event.odds_1, event.odds_2)
    devig_1, devig_2 = devig_probabilities_mma(event.odds_1, event.odds_2)
    dog_fair_prob = devig_1 if dog == 1 else devig_2
    fav_fair_prob = devig_2 if dog == 1 else devig_1
    value_edge = (dog_prob - dog_fair_prob) if dog_fair_prob is not None else None

    # Tale of the Tape
    h1 = _safe_float(facets.get("fighter_1_height") or facets.get("height_1"))
    h2 = _safe_float(facets.get("fighter_2_height") or facets.get("height_2"))
    dog_h = h1 if dog == 1 else h2
    fav_h = h2 if dog == 1 else h1
    height_gap = (dog_h - fav_h) if (dog_h is not None and fav_h is not None) else None

    r1 = _safe_float(facets.get("fighter_1_reach") or facets.get("reach_1"))
    r2 = _safe_float(facets.get("fighter_2_reach") or facets.get("reach_2"))
    dog_r = r1 if dog == 1 else r2
    fav_r = r2 if dog == 1 else r1
    reach_gap = (dog_r - fav_r) if (dog_r is not None and fav_r is not None) else None
    reach_adv = 1.0 if (reach_gap is not None and reach_gap >= 5.0) else 0.0

    st_1 = str(facets.get("fighter_1_stance") or facets.get("stance_1") or "").lower()
    st_2 = str(facets.get("fighter_2_stance") or facets.get("stance_2") or "").lower()
    dog_st = st_1 if dog == 1 else st_2
    fav_st = st_2 if dog == 1 else st_1
    is_southpaw_dog = 1.0 if "southpaw" in dog_st else 0.0
    is_southpaw_fav = 1.0 if "southpaw" in fav_st else 0.0

    method_str = str(facets.get("predicted_method") or "").lower()
    ko_finish = 1.0 if ("ko" in method_str or "tko" in method_str) else 0.0
    sub_finish = 1.0 if ("sub" in method_str or "submission" in method_str) else 0.0

    td_1 = _safe_float(facets.get("takedowns_1") or facets.get("fighter_1_takedowns"))
    td_2 = _safe_float(facets.get("takedowns_2") or facets.get("fighter_2_takedowns"))
    dog_td = td_1 if dog == 1 else td_2
    fav_td = td_2 if dog == 1 else td_1
    td_diff = (dog_td - fav_td) if (dog_td is not None and fav_td is not None) else None

    strk_1 = _safe_float(facets.get("strikes_1") or facets.get("fighter_1_strikes"))
    strk_2 = _safe_float(facets.get("strikes_2") or facets.get("fighter_2_strikes"))
    dog_strk = strk_1 if dog == 1 else strk_2
    fav_strk = strk_2 if dog == 1 else strk_1
    strk_diff = (dog_strk - fav_strk) if (dog_strk is not None and fav_strk is not None) else None

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

    return MMAFeatures(
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
        dog_height_cm=dog_h,
        fav_height_cm=fav_h,
        height_gap_cm=height_gap,
        dog_reach_cm=dog_r,
        fav_reach_cm=fav_r,
        reach_gap_cm=reach_gap,
        has_reach_advantage_dog=reach_adv,
        is_southpaw_dog=is_southpaw_dog,
        is_southpaw_fav=is_southpaw_fav,
        takedown_avg_dog=dog_td,
        takedown_avg_fav=fav_td,
        takedown_differential=td_diff,
        sig_strikes_landed_dog=dog_strk,
        sig_strikes_landed_fav=fav_strk,
        sig_strikes_differential=strk_diff,
        ko_finish_potential=ko_finish,
        sub_finish_potential=sub_finish,
        dog_recent_win_rate=dog_wr,
        favorite_recent_win_rate=fav_wr,
        win_rate_gap=dog_wr - fav_wr,
        dog_recent_games=dog_games,
        h2h_total_games=h2h_games,
        h2h_dog_win_rate=h2h_wr,
        h2h_has_dog_win=has_dog_win,
        legacy_robber_score=candidate.score,
        legacy_raw_confidence=candidate.raw_confidence,
    )


def detect_mma_robber(
    event: EventSnapshot,
    h2h: H2HStats | None = None,
    recent_1: RecentForm | None = None,
    recent_2: RecentForm | None = None,
    config: RobberConfig | None = None,
) -> RobberCandidate | None:
    """Dedicated MMA 2-Way Robber detector with physical, southpaw, and finish bonuses."""
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

    # Reach Advantage Bonus
    facets = event.pre_event_facets()
    r1 = _safe_float(facets.get("fighter_1_reach") or facets.get("reach_1"))
    r2 = _safe_float(facets.get("fighter_2_reach") or facets.get("reach_2"))
    if r1 is not None and r2 is not None:
        dog_r = r1 if dog_idx == 1 else r2
        fav_r = r2 if dog_idx == 1 else r1
        if (dog_r - fav_r) >= 5.0:
            score += 4.0
            reasons.append(f"Reach advantage ({dog_r - fav_r:+.1f}cm)")

    # Southpaw Stance Bonus
    st_1 = str(facets.get("fighter_1_stance") or facets.get("stance_1") or "").lower()
    st_2 = str(facets.get("fighter_2_stance") or facets.get("stance_2") or "").lower()
    dog_st = st_1 if dog_idx == 1 else st_2
    fav_st = st_2 if dog_idx == 1 else st_1
    if "southpaw" in dog_st and "southpaw" not in fav_st:
        score += 3.0
        reasons.append("Southpaw stance advantage vs Orthodox (+3)")

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
        reasons.append("Unpriced MMA fight")

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
        sport="mma",
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
