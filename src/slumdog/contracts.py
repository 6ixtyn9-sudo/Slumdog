"""Core Slumdog contracts.

All model inputs must be demonstrably pre-event. Raw captures may contain live
or result information, but those fields are retained only for audit/settlement.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class TimingClass(str, Enum):
    PRE_EVENT = "PRE_EVENT"
    LIVE_ONLY = "LIVE_ONLY"
    RESULT_ONLY = "RESULT_ONLY"
    UNKNOWN = "UNKNOWN"


class PriceState(str, Enum):
    FOREBET_PRICED = "FOREBET_PRICED"
    PRICE_MISSING = "PRICE_MISSING"


class CandidateState(str, Enum):
    SHADOW_UNPRICED = "SHADOW_UNPRICED"
    SHADOW_PRICED = "SHADOW_PRICED"
    CERTIFIED = "CERTIFIED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class EventSnapshot:
    event_id: str
    sport: str
    event_date: str
    captured_at: str
    source_url: str
    participant_1: str
    participant_2: str
    probability_1: float | None
    probability_2: float | None
    forebet_pick: int | None
    draw_probability: float | None = None
    odds_1: float | None = None
    odds_2: float | None = None
    league: str = ""
    tournament: str = ""
    round_name: str = ""
    kickoff: str = ""
    predicted_score: str = ""
    predicted_total: float | None = None
    raw_sha256: str = ""
    participant_1_id: str = ""
    participant_2_id: str = ""
    league_id: str = ""
    facets: dict[str, Any] = field(default_factory=dict)
    facet_timing: dict[str, TimingClass] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("event_id is required")
        if not self.participant_1 or not self.participant_2:
            raise ValueError("two named participants are required")
        if self.forebet_pick not in (None, 1, 2):
            raise ValueError("forebet_pick must be 1, 2 or None")
        for name, value in (
            ("probability_1", self.probability_1),
            ("probability_2", self.probability_2),
            ("draw_probability", self.draw_probability),
        ):
            if value is not None and not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"{name} outside [0,1]")
        for name, value in (("odds_1", self.odds_1), ("odds_2", self.odds_2)):
            if value is not None and float(value) <= 1.0:
                raise ValueError(f"{name} must be decimal odds > 1")
        # Captures must carry a parseable timestamp, including timezone.
        parsed = datetime.fromisoformat(self.captured_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("captured_at must be timezone-aware")

    def participant(self, index: int) -> str:
        if index == 1:
            return self.participant_1
        if index == 2:
            return self.participant_2
        raise ValueError("participant index must be 1 or 2")

    def probability(self, index: int) -> float | None:
        return self.probability_1 if index == 1 else self.probability_2 if index == 2 else None

    def odds(self, index: int) -> float | None:
        return self.odds_1 if index == 1 else self.odds_2 if index == 2 else None

    @property
    def price_state(self) -> PriceState:
        if self.odds_1 is not None and self.odds_2 is not None:
            return PriceState.FOREBET_PRICED
        return PriceState.PRICE_MISSING

    def pre_event_facets(self) -> dict[str, Any]:
        """Return only explicitly pre-event facets; unknown timing fails closed."""
        return {
            key: value
            for key, value in self.facets.items()
            if self.facet_timing.get(key) == TimingClass.PRE_EVENT
        }

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["price_state"] = self.price_state.value
        payload["facet_timing"] = {
            key: value.value if isinstance(value, TimingClass) else str(value)
            for key, value in self.facet_timing.items()
        }
        return payload


@dataclass(frozen=True)
class SettledEvent:
    event_id: str
    sport: str
    event_date: str
    participant_1: str
    participant_2: str
    winner_index: int  # 1/2 participant, 0 draw
    score_1: float | None
    score_2: float | None
    probability_1: float | None
    probability_2: float | None
    draw_probability: float | None
    forebet_pick: int | None
    odds_1: float | None = None
    odds_2: float | None = None
    league: str = ""
    period_scores_1: tuple[float, ...] = ()
    period_scores_2: tuple[float, ...] = ()
    source_url: str = ""
    reconstruction: str = "HISTORICAL_PAGE"
    disposition: str = "SETTLED"
    participant_1_id: str = ""
    participant_2_id: str = ""
    league_id: str = ""
    facets: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.winner_index not in (0, 1, 2):
            raise ValueError("winner_index must be 0, 1 or 2")


@dataclass(frozen=True)
class H2HStats:
    total_games: int = 0
    participant_1_wins: int = 0
    participant_2_wins: int = 0
    period_win_rates_1: tuple[float, ...] = ()
    period_win_rates_2: tuple[float, ...] = ()
    half_1_rate_1: float = 0.0
    half_2_rate_1: float = 0.0
    half_1_rate_2: float = 0.0
    half_2_rate_2: float = 0.0

    def wins(self, index: int) -> int:
        return self.participant_1_wins if index == 1 else self.participant_2_wins

    def period_rates(self, index: int) -> tuple[float, ...]:
        return self.period_win_rates_1 if index == 1 else self.period_win_rates_2

    def half_rates(self, index: int) -> tuple[float, float]:
        if index == 1:
            return self.half_1_rate_1, self.half_2_rate_1
        return self.half_1_rate_2, self.half_2_rate_2


@dataclass(frozen=True)
class RecentForm:
    wins: int = 0
    games: int = 0

    @property
    def win_rate(self) -> float | None:
        return self.wins / self.games if self.games > 0 else None


@dataclass
class RobberCandidate:
    event_id: str
    sport: str
    participant_index: int
    participant: str
    opponent: str
    score: float
    reasons: list[str]
    raw_confidence: float
    legacy_confidence: float
    price: float | None
    implied_probability: float | None
    legacy_probability: float
    legacy_expected_value: float | None
    legacy_probability_advantage: float | None
    price_state: PriceState
    state: CandidateState
    underdog_basis: str
    forebet_underdog_probability: float | None = None
    forebet_favorite_probability: float | None = None
    legacy_qualified: bool = True
    legacy_calibration_forensic: bool = True
    ml_probability: float | None = None
    ml_threshold: float | None = None
    ml_train_rows: int | None = None
    ml_validation_n: int | None = None
    ml_validation_hit_rate: float | None = None
    ml_validation_brier: float | None = None
    ml_validation_wilson_lower: float | None = None
    ml_validation_priced_n: int | None = None
    ml_validation_priced_roi: float | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["price_state"] = self.price_state.value
        payload["state"] = self.state.value
        return payload
