"""Milestone 4 — leak-safe, price-free historical example builder.

Core principle:
settled event
    ↓
Forebet participant probabilities
    ↓
price-free favorite/underdog identity
    ↓
prior-only pre-event evidence
    ↓
price-free feature snapshot
    ↓
UNDERDOG_WIN label

Never flows through legacy odds-first candidate, displayed odds, market implied probability,
price availability, legacy Robber score, ROI gate.

Training remains frozen — this module produces research dataset foundation only.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from .contracts import SettledEvent
from .history import HistoryIndex
from .sports import SPORTS
from .underdog import identify_forebet_underdog, label_underdog_outcome

FEATURE_CONTRACT_VERSION = "price-free-v1-minimal-2026-08-24"
LABEL_CONTRACT_VERSION = "price-free-v1"

# Allowed identity features (per FEATURE_TIMING_CONTRACT.md ALLOWED)
REQUIRED_IDENTITY_FEATURES = (
    "forebet_favorite_probability",
    "forebet_underdog_probability",
    "forebet_probability_gap",
    "forebet_draw_probability",
    "forebet_draw_probability_missing",
)

# Allowed prior-history features — subset reliably supported by HistoryIndex
# We implement those that can be computed strictly from earlier event dates.
ALLOWED_PRIOR_FEATURES = (
    "underdog_prior_games",
    "favorite_prior_games",
    "underdog_prior_win_rate",
    "favorite_prior_win_rate",
    "recent_win_rate_gap",
    "h2h_prior_games",
    "h2h_underdog_win_rate",
    "h2h_draw_rate",
    # Extended but still prior-only, computed from prior_rows where scores available:
    "underdog_prior_draw_rate",
    "favorite_prior_draw_rate",
    "prior_scoring_rate_gap",
    "prior_conceding_rate_gap",
)

ALLOWED_FEATURES = REQUIRED_IDENTITY_FEATURES + ALLOWED_PRIOR_FEATURES

# Prohibited keys — must never appear in serialized example output
PROHIBITED_KEYS = {
    "odds_1",
    "odds_2",
    "price",
    "overround",
    "fair_market_probability",
    "fair_implied_probability",
    "value_edge",
    "ROI",
    "legacy_robber_score",
    "legacy_raw_confidence",
    "displayed_odds",
    "implied_probability",
    "price_available",
    "period_values",
    "score_1",
    "score_2",
    "period_scores_1",
    "period_scores_2",
    "extra_time_score",
    "penalty_score",
    "disposition",
    "live_score",
    "result",
    "result_text",
}


def _key(name: str) -> str:
    return "".join(ch for ch in str(name or "").casefold() if ch.isalnum())


def _is_finite(v: Any) -> bool:
    try:
        f = float(v)
        return math.isfinite(f)
    except Exception:
        return False


@dataclass(frozen=True)
class PriceFreeUnderdogExample:
    """Leak-safe price-free historical example — eligible only when label 0/1."""

    event_id: str
    sport: str
    event_date: str
    favorite_index: int
    underdog_index: int
    favorite_probability: float
    underdog_probability: float
    draw_probability: float | None
    probability_gap: float
    label: int  # 0 favorite win or draw (draw-capable), 1 underdog win
    features: dict[str, float | None]
    missingness: dict[str, int]  # 1 missing, 0 present
    source_url: str = ""
    raw_sha256: str = ""
    feature_contract_version: str = FEATURE_CONTRACT_VERSION
    label_contract_version: str = LABEL_CONTRACT_VERSION
    # Optional audit metadata
    exclusion_reason: str | None = None
    legacy_provenance_missing: bool | None = None

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("event_id required")
        if self.sport not in SPORTS:
            raise ValueError(f"unknown sport in example: {self.sport}")
        if self.favorite_index not in (1, 2) or self.underdog_index not in (1, 2):
            raise ValueError("favorite/underdog must be 1 or 2")
        if self.favorite_index == self.underdog_index:
            raise ValueError("favorite and underdog must differ")
        if self.label not in (0, 1):
            raise ValueError("label must be 0 or 1 for eligible example")
        if not 0.0 <= self.favorite_probability <= 1.0:
            raise ValueError("favorite_probability outside [0,1]")
        if not 0.0 <= self.underdog_probability <= 1.0:
            raise ValueError("underdog_probability outside [0,1]")
        if self.favorite_probability <= self.underdog_probability:
            raise ValueError("favorite_probability must be > underdog_probability")
        if self.draw_probability is not None and not 0.0 <= self.draw_probability <= 1.0:
            raise ValueError("draw_probability outside [0,1]")
        if self.probability_gap < 0:
            raise ValueError("probability_gap must be >=0")
        # Ensure no prohibited keys in features
        for k in self.features:
            if k in PROHIBITED_KEYS:
                raise ValueError(f"prohibited feature key in example: {k}")
        # Ensure draw never selected as underdog
        if self.underdog_index == 0:
            raise ValueError("underdog_index must never be 0 (draw)")

    def to_dict(self) -> dict[str, Any]:
        # Deterministic feature ordering
        payload = asdict(self)
        # Sort features and missingness keys for deterministic output
        payload["features"] = {k: self.features[k] for k in sorted(self.features)}
        payload["missingness"] = {k: self.missingness[k] for k in sorted(self.missingness)}
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> PriceFreeUnderdogExample:
        data = dict(payload)
        # Only keep known fields
        allowed = set(cls.__dataclass_fields__.keys())
        filtered = {k: v for k, v in data.items() if k in allowed}
        return cls(**filtered)


@dataclass(frozen=True)
class PriceFreeDatasetReceipt:
    """Deterministic audit receipt for price-free dataset build."""

    input_rows: int
    eligible_examples: int
    positive_underdog_wins: int
    negative_favorite_wins: int
    negative_draws: int
    excluded_void: int
    excluded_source_conflict: int
    excluded_equal_probability: int
    excluded_missing_probability: int
    excluded_non_finite_probability: int
    excluded_out_of_range_probability: int
    excluded_unknown_sport: int
    excluded_unexpected_two_way_draw: int
    excluded_invalid_winner: int
    excluded_other: int
    provenance_present: int
    provenance_missing: int
    positive_rate: float | None
    date_min: str | None
    date_max: str | None
    feature_contract_version: str
    label_contract_version: str
    input_digest: str
    per_sport: dict[str, dict[str, int]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        # Stable ordering for per_sport
        payload["per_sport"] = {k: payload["per_sport"][k] for k in sorted(payload["per_sport"])}
        for sport in payload["per_sport"]:
            payload["per_sport"][sport] = {kk: payload["per_sport"][sport][kk] for kk in sorted(payload["per_sport"][sport])}
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> PriceFreeDatasetReceipt:
        data = dict(payload)
        allowed = set(cls.__dataclass_fields__.keys())
        filtered = {k: v for k, v in data.items() if k in allowed}
        return cls(**filtered)


def _compute_input_digest(rows: list[SettledEvent]) -> str:
    # Deterministic digest: sorted event_id|sport|event_date|winner
    sorted_keys = sorted((r.sport, r.event_id, r.event_date, r.winner_index) for r in rows)
    blob = "\n".join(f"{s}|{eid}|{d}|{w}" for s, eid, d, w in sorted_keys)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _prior_scoring_stats(
    sport: str,
    participant_name: str,
    event_date: str,
    history: HistoryIndex,
) -> tuple[float | None, float | None, int, int]:
    """Return (avg_scored, avg_conceded, games_with_scores, total_prior_games) for participant before event_date."""
    key = _key(participant_name)
    # Get prior rows for this participant
    rows = history._earlier(history.by_participant.get((sport, key), []), event_date)
    total = len(rows)
    scored_sum = 0.0
    conceded_sum = 0.0
    scored_count = 0
    conceded_count = 0
    draw_count = 0
    for r in rows:
        # Determine if participant is p1 or p2
        is_p1 = _key(r.participant_1) == key
        is_p2 = _key(r.participant_2) == key
        if not (is_p1 or is_p2):
            continue
        # Draw counting
        if r.winner_index == 0:
            draw_count += 1
        # Scoring — only if scores present
        if r.score_1 is not None and r.score_2 is not None:
            if is_p1:
                scored_sum += float(r.score_1)
                conceded_sum += float(r.score_2)
            else:
                scored_sum += float(r.score_2)
                conceded_sum += float(r.score_1)
            scored_count += 1
            conceded_count += 1
    avg_scored = scored_sum / scored_count if scored_count else None
    avg_conceded = conceded_sum / conceded_count if conceded_count else None
    return avg_scored, avg_conceded, draw_count, total


def build_price_free_examples(
    settled_events: list[SettledEvent],
    *,
    feature_contract_version: str = FEATURE_CONTRACT_VERSION,
    label_contract_version: str = LABEL_CONTRACT_VERSION,
) -> tuple[list[PriceFreeUnderdogExample], PriceFreeDatasetReceipt]:
    """Build leak-safe price-free examples from settled events.

    Rules:
    - history_event_date < current_event_date (same-date excluded via HistoryIndex._earlier)
    - No odds influence
    - Draw-capable: underdog win 1, fav win 0, draw 0, void excluded
    - Two-way: underdog win 1, fav win 0, draw excluded
    - Equal/missing/non-finite/out-of-range probabilities excluded
    - Unknown sport excluded
    - Deterministic ordering, duplicate handling, conflicting keys fail loudly
    """

    # Deduplicate and detect conflicting composite keys
    # Composite key: (sport, event_id, event_date)
    dedup: dict[tuple[str, str, str], SettledEvent] = {}
    for row in settled_events:
        key = (row.sport, row.event_id, row.event_date)
        if key in dedup:
            existing = dedup[key]
            # Exact duplicate check: same winner, probabilities, scores, participants
            if (
                existing.winner_index == row.winner_index
                and existing.probability_1 == row.probability_1
                and existing.probability_2 == row.probability_2
                and existing.draw_probability == row.draw_probability
                and existing.participant_1 == row.participant_1
                and existing.participant_2 == row.participant_2
                and existing.score_1 == row.score_1
                and existing.score_2 == row.score_2
                and existing.disposition == row.disposition
            ):
                # Exact duplicate — collapse per integrity contract
                continue
            else:
                raise ValueError(f"conflicting composite key {key}: {existing} vs {row}")
        dedup[key] = row

    # Deterministic input ordering: by (event_date, sport, event_id)
    sorted_rows = sorted(dedup.values(), key=lambda r: (r.event_date, r.sport, r.event_id))

    input_digest = _compute_input_digest(sorted_rows)
    date_min = min((r.event_date for r in sorted_rows), default=None)
    date_max = max((r.event_date for r in sorted_rows), default=None)

    # Build HistoryIndex from all sorted rows (prior-only via _earlier)
    history = HistoryIndex(sorted_rows)

    examples: list[PriceFreeUnderdogExample] = []
    exclusion_counter: Counter = Counter()
    per_sport_counter: dict[str, Counter] = defaultdict(Counter)
    provenance_present = 0
    provenance_missing = 0
    positive = 0
    negative_fav = 0
    negative_draw = 0

    for row in sorted_rows:
        sport = row.sport
        # Unknown sport
        if sport not in SPORTS:
            exclusion_counter["excluded_unknown_sport"] += 1
            per_sport_counter[sport]["excluded_unknown_sport"] += 1
            continue

        # Disposition void handling
        disp = (row.disposition or "SETTLED").upper()
        if "VOID" in disp or disp in {"CANCELLED", "CANCELED", "ABANDONED", "NO_CONTEST", "POSTPONED"}:
            exclusion_counter["excluded_void"] += 1
            per_sport_counter[sport]["excluded_void"] += 1
            continue

        # Identity
        identity = identify_forebet_underdog(row.probability_1, row.probability_2, row.draw_probability)
        if not identity.eligible:
            reason = identity.ineligibility_reason or "NO_ELIGIBLE_IDENTITY"
            if reason == "EQUAL_PROBABILITY":
                exclusion_counter["excluded_equal_probability"] += 1
                per_sport_counter[sport]["excluded_equal_probability"] += 1
            elif reason == "MISSING_PROBABILITY":
                exclusion_counter["excluded_missing_probability"] += 1
                per_sport_counter[sport]["excluded_missing_probability"] += 1
            elif reason in ("NON_FINITE_PROBABILITY", "INVALID_PROBABILITY"):
                exclusion_counter["excluded_non_finite_probability"] += 1
                per_sport_counter[sport]["excluded_non_finite_probability"] += 1
            elif reason == "OUT_OF_RANGE_PROBABILITY":
                exclusion_counter["excluded_out_of_range_probability"] += 1
                per_sport_counter[sport]["excluded_out_of_range_probability"] += 1
            else:
                exclusion_counter["excluded_other"] += 1
                per_sport_counter[sport]["excluded_other"] += 1
            continue

        # Label
        label_result = label_underdog_outcome(sport, identity, row.winner_index, disposition=disp, source_conflict=False)
        if not label_result.eligible:
            ex = label_result.exclusion_reason or "UNKNOWN"
            if ex == "VOID":
                exclusion_counter["excluded_void"] += 1
                per_sport_counter[sport]["excluded_void"] += 1
            elif ex == "SOURCE_CONFLICT":
                exclusion_counter["excluded_source_conflict"] += 1
                per_sport_counter[sport]["excluded_source_conflict"] += 1
            elif ex == "UNKNOWN_SPORT":
                exclusion_counter["excluded_unknown_sport"] += 1
                per_sport_counter[sport]["excluded_unknown_sport"] += 1
            elif ex == "UNEXPECTED_DRAW_FOR_TWO_WAY":
                exclusion_counter["excluded_unexpected_two_way_draw"] += 1
                per_sport_counter[sport]["excluded_unexpected_two_way_draw"] += 1
            elif ex == "INVALID_WINNER_INDEX":
                exclusion_counter["excluded_invalid_winner"] += 1
                per_sport_counter[sport]["excluded_invalid_winner"] += 1
            elif ex == "EQUAL_PROBABILITY":
                exclusion_counter["excluded_equal_probability"] += 1
                per_sport_counter[sport]["excluded_equal_probability"] += 1
            elif ex == "MISSING_PROBABILITY":
                exclusion_counter["excluded_missing_probability"] += 1
                per_sport_counter[sport]["excluded_missing_probability"] += 1
            elif ex in ("NON_FINITE_PROBABILITY", "INVALID_PROBABILITY"):
                exclusion_counter["excluded_non_finite_probability"] += 1
                per_sport_counter[sport]["excluded_non_finite_probability"] += 1
            elif ex == "OUT_OF_RANGE_PROBABILITY":
                exclusion_counter["excluded_out_of_range_probability"] += 1
                per_sport_counter[sport]["excluded_out_of_range_probability"] += 1
            else:
                exclusion_counter["excluded_other"] += 1
                per_sport_counter[sport]["excluded_other"] += 1
            continue

        # Eligible — build features
        assert label_result.label in (0, 1)
        label = label_result.label
        # Count positives/negatives
        if label == 1:
            positive += 1
        else:
            # label 0 could be favorite win or draw (draw-capable)
            if label_result.is_draw:
                negative_draw += 1
            else:
                negative_fav += 1

        # Provenance
        if row.source_url:
            provenance_present += 1
        else:
            provenance_missing += 1
        per_sport_counter[sport]["eligible_examples"] += 1
        if label == 1:
            per_sport_counter[sport]["positive_underdog_wins"] += 1
        else:
            if label_result.is_draw:
                per_sport_counter[sport]["negative_draws"] += 1
            else:
                per_sport_counter[sport]["negative_favorite_wins"] += 1

        # Prior history via HistoryIndex
        h2h, recent_1, recent_2 = history.context(sport, row.event_date, row.participant_1, row.participant_2)

        # Determine underdog/favorite participants
        fav_idx = identity.favorite_index
        dog_idx = identity.underdog_index
        assert fav_idx in (1, 2) and dog_idx in (1, 2)

        # Recent form mapping
        if dog_idx == 1:
            dog_recent = recent_1
            fav_recent = recent_2
        else:
            dog_recent = recent_2
            fav_recent = recent_1

        underdog_prior_games = dog_recent.games if dog_recent else 0
        favorite_prior_games = fav_recent.games if fav_recent else 0
        underdog_prior_win_rate = dog_recent.win_rate if dog_recent and dog_recent.games > 0 else None
        favorite_prior_win_rate = fav_recent.win_rate if fav_recent and fav_recent.games > 0 else None
        if underdog_prior_win_rate is not None and favorite_prior_win_rate is not None:
            recent_win_rate_gap = underdog_prior_win_rate - favorite_prior_win_rate
        else:
            recent_win_rate_gap = None

        # H2H
        h2h_prior_games = h2h.total_games
        if h2h_prior_games > 0:
            # Wins for underdog
            p1_wins = h2h.participant_1_wins
            p2_wins = h2h.participant_2_wins
            # Map to underdog
            if dog_idx == 1:
                dog_h2h_wins = p1_wins
            else:
                dog_h2h_wins = p2_wins
            h2h_underdog_win_rate = dog_h2h_wins / h2h_prior_games if h2h_prior_games else None
            h2h_draw_rate = (h2h_prior_games - p1_wins - p2_wins) / h2h_prior_games if h2h_prior_games else None
        else:
            h2h_underdog_win_rate = None
            h2h_draw_rate = None

        # Extended prior stats from prior_rows
        dog_avg_scored, dog_avg_conceded, dog_draws, dog_total = _prior_scoring_stats(sport, row.participant_1 if dog_idx == 1 else row.participant_2, row.event_date, history)
        fav_avg_scored, fav_avg_conceded, fav_draws, fav_total = _prior_scoring_stats(sport, row.participant_1 if fav_idx == 1 else row.participant_2, row.event_date, history)

        if dog_total > 0:
            underdog_prior_draw_rate = dog_draws / dog_total if dog_total else None
        else:
            underdog_prior_draw_rate = None

        if fav_total > 0:
            favorite_prior_draw_rate = fav_draws / fav_total if fav_total else None
        else:
            favorite_prior_draw_rate = None

        if dog_avg_scored is not None and fav_avg_scored is not None:
            prior_scoring_rate_gap = dog_avg_scored - fav_avg_scored
        else:
            prior_scoring_rate_gap = None

        if dog_avg_conceded is not None and fav_avg_conceded is not None:
            prior_conceding_rate_gap = dog_avg_conceded - fav_avg_conceded
        else:
            prior_conceding_rate_gap = None

        # Build features dict — only allowed keys, preserve None for missing
        features: dict[str, float | None] = {
            "forebet_favorite_probability": identity.favorite_probability,
            "forebet_underdog_probability": identity.underdog_probability,
            "forebet_probability_gap": identity.probability_gap,
            "forebet_draw_probability": identity.draw_probability,
            "forebet_draw_probability_missing": 1.0 if identity.draw_probability is None else 0.0,
            "underdog_prior_games": float(underdog_prior_games) if underdog_prior_games is not None else None,
            "favorite_prior_games": float(favorite_prior_games) if favorite_prior_games is not None else None,
            "underdog_prior_win_rate": underdog_prior_win_rate,
            "favorite_prior_win_rate": favorite_prior_win_rate,
            "recent_win_rate_gap": recent_win_rate_gap,
            "h2h_prior_games": float(h2h_prior_games) if h2h_prior_games is not None else None,
            "h2h_underdog_win_rate": h2h_underdog_win_rate,
            "h2h_draw_rate": h2h_draw_rate,
            "underdog_prior_draw_rate": underdog_prior_draw_rate,
            "favorite_prior_draw_rate": favorite_prior_draw_rate,
            "prior_scoring_rate_gap": prior_scoring_rate_gap,
            "prior_conceding_rate_gap": prior_conceding_rate_gap,
        }

        # Missingness dict — 1 if None, 0 if present
        # For forebet_draw_probability_missing, its missingness is always 0 (it's an indicator itself)
        missingness: dict[str, int] = {}
        for k, v in features.items():
            if k == "forebet_draw_probability_missing":
                missingness[k] = 0
            else:
                missingness[k] = 1 if v is None else 0

        # Special handling: underdog_prior_games and favorite_prior_games and h2h_prior_games are always present as 0 if no history
        # Per missingness policy: genuine observed zero remains 0 with missing 0
        # If HistoryIndex cannot distinguish no history from zero games, document limitation
        # Here games=0 means no history, but we treat as genuine 0 with missing 0 for now, and note limitation in docs
        # To follow policy: if games=0 and win_rate None, then win_rate missing 1, but games missing 0 (genuine zero prior games)
        # So adjust: for games fields, missingness 0 even if 0
        for gkey in ("underdog_prior_games", "favorite_prior_games", "h2h_prior_games"):
            missingness[gkey] = 0

        example = PriceFreeUnderdogExample(
            event_id=row.event_id,
            sport=sport,
            event_date=row.event_date,
            favorite_index=fav_idx,
            underdog_index=dog_idx,
            favorite_probability=identity.favorite_probability,
            underdog_probability=identity.underdog_probability,
            draw_probability=identity.draw_probability,
            probability_gap=identity.probability_gap or 0.0,
            label=label,
            features={k: features[k] for k in sorted(features)},
            missingness={k: missingness[k] for k in sorted(missingness)},
            source_url=row.source_url,
            raw_sha256="",  # SettledEvent does not carry raw_sha256, keep empty but present for contract
            feature_contract_version=feature_contract_version,
            label_contract_version=label_contract_version,
            exclusion_reason=None,
            legacy_provenance_missing=row.source_url == "",
        )

        # Ensure no prohibited keys
        for prohibited in PROHIBITED_KEYS:
            if prohibited in example.features:
                raise ValueError(f"prohibited key in features: {prohibited}")

        examples.append(example)

    # Deterministic output ordering
    examples = sorted(examples, key=lambda e: (e.event_date, e.sport, e.event_id))

    # Receipt
    input_rows = len(sorted_rows)
    eligible_examples = len(examples)
    # Accounting invariant: input_rows == eligible + sum(exclusions) — verified by tests

    positive_rate = (positive / eligible_examples) if eligible_examples else None

    receipt = PriceFreeDatasetReceipt(
        input_rows=input_rows,
        eligible_examples=eligible_examples,
        positive_underdog_wins=positive,
        negative_favorite_wins=negative_fav,
        negative_draws=negative_draw,
        excluded_void=exclusion_counter.get("excluded_void", 0),
        excluded_source_conflict=exclusion_counter.get("excluded_source_conflict", 0),
        excluded_equal_probability=exclusion_counter.get("excluded_equal_probability", 0),
        excluded_missing_probability=exclusion_counter.get("excluded_missing_probability", 0),
        excluded_non_finite_probability=exclusion_counter.get("excluded_non_finite_probability", 0),
        excluded_out_of_range_probability=exclusion_counter.get("excluded_out_of_range_probability", 0),
        excluded_unknown_sport=exclusion_counter.get("excluded_unknown_sport", 0),
        excluded_unexpected_two_way_draw=exclusion_counter.get("excluded_unexpected_two_way_draw", 0),
        excluded_invalid_winner=exclusion_counter.get("excluded_invalid_winner", 0),
        excluded_other=exclusion_counter.get("excluded_other", 0),
        provenance_present=provenance_present,
        provenance_missing=provenance_missing,
        positive_rate=positive_rate,
        date_min=date_min,
        date_max=date_max,
        feature_contract_version=feature_contract_version,
        label_contract_version=label_contract_version,
        input_digest=input_digest,
        per_sport={sport: dict(counter) for sport, counter in per_sport_counter.items()},
    )

    return examples, receipt
