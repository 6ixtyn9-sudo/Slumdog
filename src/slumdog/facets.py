"""Forebet facet catalogue and leak-safe feature extraction."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .contracts import EventSnapshot, H2HStats, RecentForm, RobberCandidate, TimingClass
from .sports import SPORTS


@dataclass(frozen=True)
class FacetDefinition:
    name: str
    timing: TimingClass
    description: str


COMMON_FACETS: tuple[FacetDefinition, ...] = (
    FacetDefinition("probability_1", TimingClass.PRE_EVENT, "Forebet participant 1 probability"),
    FacetDefinition("probability_2", TimingClass.PRE_EVENT, "Forebet participant 2 probability"),
    FacetDefinition("draw_probability", TimingClass.PRE_EVENT, "Forebet draw probability where applicable"),
    FacetDefinition("predicted_participant", TimingClass.PRE_EVENT, "Forebet predicted winner"),
    FacetDefinition("predicted_score", TimingClass.PRE_EVENT, "Forebet score/sets/points prediction"),
    FacetDefinition("predicted_total", TimingClass.PRE_EVENT, "Forebet expected total"),
    FacetDefinition("odds_1", TimingClass.PRE_EVENT, "Displayed participant 1 price"),
    FacetDefinition("odds_2", TimingClass.PRE_EVENT, "Displayed participant 2 price"),
    FacetDefinition("standings", TimingClass.PRE_EVENT, "Displayed standings/ranks"),
    FacetDefinition("form", TimingClass.PRE_EVENT, "Displayed recent form"),
    FacetDefinition("home_away_form", TimingClass.PRE_EVENT, "Venue/split form"),
    FacetDefinition("h2h", TimingClass.PRE_EVENT, "Displayed matchup history"),
    FacetDefinition("streaks", TimingClass.PRE_EVENT, "Displayed trends and streaks"),
    FacetDefinition("result", TimingClass.RESULT_ONLY, "Final score/outcome"),
    FacetDefinition("live_score", TimingClass.LIVE_ONLY, "In-event score"),
    FacetDefinition("status", TimingClass.UNKNOWN, "Must be classified per raw page state"),
)


def facet_catalogue() -> dict[str, dict[str, TimingClass]]:
    """Return the common + sport-specific inventory used during discovery."""
    common = {item.name: item.timing for item in COMMON_FACETS}
    return {
        sport: {
            **common,
            **{name: TimingClass.PRE_EVENT for name in spec.known_facets},
        }
        for sport, spec in SPORTS.items()
    }


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _entropy(values: list[float]) -> float:
    usable = [max(1e-12, value) for value in values if value >= 0]
    total = sum(usable)
    if total <= 0:
        return 0.0
    probs = [value / total for value in usable]
    return -sum(p * math.log(p) for p in probs)


def build_numeric_features(
    event: EventSnapshot,
    candidate: RobberCandidate,
    h2h: H2HStats | None = None,
    recent_1: RecentForm | None = None,
    recent_2: RecentForm | None = None,
) -> dict[str, float]:
    """Build the initial sport-model vector from pre-event evidence only.

    Every numeric custom facet is accompanied by a missingness flag. Facets
    whose timing is live/result/unknown are excluded even when present.
    """
    h2h = h2h or H2HStats()
    probabilities = [p for p in (event.probability_1, event.draw_probability, event.probability_2) if p is not None]
    favorite_prob = max(probabilities) if probabilities else 0.0
    dog_prob = event.probability(candidate.participant_index) or 0.0
    other_prob = event.probability(2 if candidate.participant_index == 1 else 1) or 0.0
    dog_recent = recent_1 if candidate.participant_index == 1 else recent_2
    favorite_recent = recent_2 if candidate.participant_index == 1 else recent_1

    features: dict[str, float] = {
        "forebet_dog_probability": float(dog_prob),
        "forebet_other_probability": float(other_prob),
        "forebet_favorite_probability": float(favorite_prob),
        "forebet_probability_gap": float(favorite_prob - dog_prob),
        "forebet_probability_ratio": float(dog_prob / favorite_prob) if favorite_prob > 0 else 0.0,
        "forebet_entropy": _entropy(probabilities),
        "forebet_calls_dog": float(event.forebet_pick == candidate.participant_index),
        "legacy_robber_score": float(candidate.score),
        "legacy_raw_confidence": float(candidate.raw_confidence) / 100.0,
        "price_available": float(candidate.price is not None),
        "displayed_odds": float(candidate.price or 0.0),
        "implied_probability": float(candidate.implied_probability or 0.0),
        "h2h_games": float(h2h.total_games),
        "h2h_dog_win_rate": float(h2h.wins(candidate.participant_index) / h2h.total_games) if h2h.total_games else 0.0,
        "dog_recent_games": float(dog_recent.games if dog_recent else 0),
        "dog_recent_win_rate": float(dog_recent.win_rate or 0.0) if dog_recent else 0.0,
        "favorite_recent_win_rate": float(favorite_recent.win_rate or 0.0) if favorite_recent else 0.0,
        "predicted_total": float(event.predicted_total or 0.0),
        "predicted_total_missing": float(event.predicted_total is None),
        "draw_probability": float(event.draw_probability or 0.0),
        "draw_probability_missing": float(event.draw_probability is None),
    }

    for key, value in event.pre_event_facets().items():
        number = _finite(value)
        feature_key = "facet_" + "".join(ch if ch.isalnum() else "_" for ch in key.lower()).strip("_")
        features[feature_key + "_missing"] = float(number is None)
        if number is not None:
            features[feature_key] = number

    return features
