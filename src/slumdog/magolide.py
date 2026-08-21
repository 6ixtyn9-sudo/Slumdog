"""Faithful Python reproduction of Ma Golide's legacy Robber detector.

Source contract reproduced from:
Ma_Golide_Satellites/docs/Accumulator_Builder.gs
  ROBBERS_CONFIG_DEFAULTS, detectRobbers, _robbers_normalizePick_.

The legacy probability calibration is retained for forensic comparison. It
mechanically imposes bounds relative to displayed price and must not be treated
as an independently learned probability.
"""
from __future__ import annotations

from dataclasses import dataclass

from .contracts import (
    CandidateState,
    EventSnapshot,
    H2HStats,
    RecentForm,
    RobberCandidate,
)


@dataclass(frozen=True)
class RobberConfig:
    min_h2h_games: int = 1
    underdog_win_threshold: float = 0.30
    momentum_games: int = 5
    momentum_win_threshold: float = 0.55
    min_odds: float = 1.80
    max_odds: float = 12.00
    max_favorite_odds: float = 1.95
    min_score: float = 20.0
    max_confidence: float = 80.0
    calibration_shrink: float = 0.38
    calibration_max_probability: float = 0.67
    calibration_min_advantage: float = 0.08
    calibration_max_ev: float = 0.85
    emit_min_confidence: float = 60.0


@dataclass(frozen=True)
class UnderdogIdentity:
    index: int
    basis: str


def identify_underdog(
    event: EventSnapshot,
    recent_1: RecentForm | None = None,
    recent_2: RecentForm | None = None,
) -> UnderdogIdentity:
    """Reproduce Ma Golide's odds -> pick -> probability -> form cascade."""
    if event.odds_1 is not None and event.odds_2 is not None:
        # Higher decimal odds = market underdog. Exact ties fall through to the
        # source prediction rather than inventing a side.
        if event.odds_1 != event.odds_2:
            return UnderdogIdentity(1 if event.odds_1 > event.odds_2 else 2, "displayed_odds")

    if event.forebet_pick in (1, 2):
        return UnderdogIdentity(2 if event.forebet_pick == 1 else 1, "opposite_forebet_pick")

    p1, p2 = event.probability_1, event.probability_2
    if p1 is not None or p2 is not None:
        v1 = float(p1 or 0.0)
        v2 = float(p2 or 0.0)
        if v1 != v2:
            return UnderdogIdentity(1 if v1 < v2 else 2, "lower_forebet_probability")

    r1 = recent_1.win_rate if recent_1 else None
    r2 = recent_2.win_rate if recent_2 else None
    v1 = float(r1 or 0.0)
    v2 = float(r2 or 0.0)
    return UnderdogIdentity(1 if v1 <= v2 else 2, "weaker_recent_form")


def _legacy_tier(confidence: float) -> str:
    if confidence >= 75:
        return "ELITE"
    if confidence >= 70:
        return "STRONG"
    if confidence >= 58:
        return "MEDIUM"
    if confidence >= 50:
        return "WEAK"
    return "SKIP"


def detect_robber(
    event: EventSnapshot,
    h2h: H2HStats | None = None,
    recent_1: RecentForm | None = None,
    recent_2: RecentForm | None = None,
    config: RobberConfig | None = None,
) -> RobberCandidate | None:
    """Return a faithful legacy Robber candidate, or None below threshold."""
    config = config or RobberConfig()
    h2h = h2h or H2HStats()
    underdog = identify_underdog(event, recent_1, recent_2)
    dog = underdog.index
    favorite = 2 if dog == 1 else 1
    dog_odds = event.odds(dog)
    favorite_odds = event.odds(favorite)
    odds_available = dog_odds is not None and favorite_odds is not None

    score = 0.0
    reasons: list[str] = []

    # Factor 0 — Favorite strength (legacy max 15).
    if odds_available:
        assert favorite_odds is not None
        if favorite_odds <= 1.35:
            score += 15
            reasons.append(f"Heavy fav @{favorite_odds:.2f}")
        elif favorite_odds <= 1.55:
            score += 12
            reasons.append(f"Strong fav @{favorite_odds:.2f}")
        elif favorite_odds <= 1.75:
            score += 8
            reasons.append(f"Clear fav @{favorite_odds:.2f}")
        else:
            score += 3
            reasons.append(f"Slight fav @{favorite_odds:.2f}")

    # Factor 1 — H2H upset history (legacy max 20).
    if h2h.total_games >= config.min_h2h_games:
        wins = h2h.wins(dog)
        rate = wins / h2h.total_games
        if rate >= config.underdog_win_threshold:
            score += 20
            reasons.append(f"H2H {round(rate * 100)}% ({wins}/{h2h.total_games})")
        elif wins > 0:
            score += 6
            reasons.append(f"H2H wins ({wins})")

    # Factor 2 — Period/quarter dominance (legacy max 12).
    dominant = sum(1 for value in h2h.period_rates(dog) if value > 0.50)
    if dominant >= 2:
        score += 12
        reasons.append(f"Period advantage ({dominant})")
    elif dominant == 1:
        score += 5
        reasons.append("Period advantage (1)")

    # Factor 3 — Half performance (legacy max 10).
    half_1, half_2 = h2h.half_rates(dog)
    if half_1 > 0.50:
        score += 5
        reasons.append(f"Strong first segment {round(half_1 * 100)}%")
    if half_2 > 0.50:
        score += 5
        reasons.append(f"Strong second segment {round(half_2 * 100)}%")

    # Factor 4 — Recent momentum (legacy max 15).
    recent = recent_1 if dog == 1 else recent_2
    if recent and recent.games >= config.momentum_games:
        rate = recent.wins / recent.games
        if rate >= config.momentum_win_threshold:
            score += 15
            reasons.append(f"Hot {recent.wins}W/{recent.games}G")
        elif rate >= 0.45:
            score += 8
            reasons.append(f"Form {recent.wins}W/{recent.games}G")

    # Factor 5 — Displayed odds value (legacy max 15).
    if odds_available:
        assert dog_odds is not None
        if 2.50 <= dog_odds <= 5.50:
            score += 15
            reasons.append(f"Value @{dog_odds:.2f}")
        elif 2.00 <= dog_odds < 2.50:
            score += 10
            reasons.append(f"Playable @{dog_odds:.2f}")
        elif 5.50 < dog_odds <= 8.00:
            score += 8
            reasons.append(f"High payout @{dog_odds:.2f}")
        else:
            score += 4
            reasons.append(f"Longshot @{dog_odds:.2f}")
    else:
        reasons.append("No displayed price (percentage-defined upset)")

    threshold = config.min_score if odds_available else max(10.0, round(config.min_score * 0.55))
    if score < threshold:
        return None

    raw_confidence = min(config.max_confidence, 46.0 + score * 0.55)
    raw_probability = raw_confidence / 100.0
    implied = None
    expected_value = None
    advantage = None

    if odds_available:
        assert dog_odds is not None
        implied = 1.0 / dog_odds
        shrink = min(0.60, max(0.15, config.calibration_shrink))
        legacy_probability = implied + (raw_probability - implied) * shrink
        max_probability = min(0.75, config.calibration_max_probability)
        min_probability = min(max_probability - 0.05, implied + config.calibration_min_advantage)
        min_probability = max(0.30, min_probability)
        legacy_probability = min(max_probability, max(min_probability, legacy_probability))
        legacy_confidence = min(95.0, max(50.0, round(legacy_probability * 100)))
        expected_value = legacy_probability * dog_odds - 1.0
        expected_value = min(config.calibration_max_ev, max(-0.50, expected_value))
        advantage = legacy_probability - implied
        state = CandidateState.SHADOW_PRICED
    else:
        legacy_probability = raw_probability
        legacy_confidence = min(95.0, max(50.0, round(raw_confidence)))
        state = CandidateState.SHADOW_UNPRICED

    # v0.1 is shadow-only. The minimum controls the high-confidence output
    # surface but never forces a fixed number of picks.
    if legacy_confidence < config.emit_min_confidence:
        return None

    return RobberCandidate(
        event_id=event.event_id,
        sport=event.sport,
        participant_index=dog,
        participant=event.participant(dog),
        opponent=event.participant(favorite),
        score=score,
        reasons=reasons,
        raw_confidence=round(raw_confidence, 3),
        legacy_confidence=legacy_confidence,
        price=dog_odds,
        implied_probability=round(implied, 6) if implied is not None else None,
        legacy_probability=round(legacy_probability, 6),
        legacy_expected_value=round(expected_value, 6) if expected_value is not None else None,
        legacy_probability_advantage=round(advantage, 6) if advantage is not None else None,
        price_state=event.price_state,
        state=state,
        underdog_basis=underdog.basis,
        forebet_underdog_probability=event.probability(dog),
        forebet_favorite_probability=event.probability(favorite),
    )
