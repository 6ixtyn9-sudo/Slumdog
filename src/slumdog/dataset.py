"""Milestone 4 — leak-safe, price-free historical example builder with 4E hardening.

Core principle:
settled event
    ↓
Forebet participant probabilities
    ↓
price-free favorite/underdog identity (identify_forebet_underdog)
    ↓
prior-only pre-event evidence (HistoryIndex, date < current)
    ↓
price-free feature snapshot (ALLOWED only)
    ↓
UNDERDOG_WIN label (label_underdog_outcome, SPORTS registry)

Never flows through legacy odds-first candidate, displayed odds, market implied probability,
price availability, legacy Robber score, ROI gate.

Training remains frozen — this module produces research dataset foundation only.

Hardening (Milestone 4E):
- No fabricated defaults: missing winner → excluded_invalid_winner, never participant 1
- No silent swallowing: malformed rows counted, corrupt files fail loudly
- Raw vs canonical accounting with explicit invariants
- Strengthened input digest hashing all fields affecting identity/label/history/eligibility/dedup/provenance, excluding odds deliberately
- Duplicate identity validated: composite key (sport, event_id, event_date) matching settlement.py, same event_id in different sports does not collapse, conflicting content fails loudly
- Provenance validation: raw_sha256 must be 64 hex chars to count as present, malformed counted separately
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from .contracts import SettledEvent
from .history import HistoryIndex
from .sports import SPORTS
from .underdog import identify_forebet_underdog, label_underdog_outcome

FEATURE_CONTRACT_VERSION = "price-free-v1-minimal-2026-08-24"
LABEL_CONTRACT_VERSION = "price-free-v1"

REQUIRED_IDENTITY_FEATURES = (
    "forebet_favorite_probability",
    "forebet_underdog_probability",
    "forebet_probability_gap",
    "forebet_draw_probability",
    "forebet_draw_probability_missing",
)

ALLOWED_PRIOR_FEATURES = (
    "underdog_prior_games",
    "favorite_prior_games",
    "underdog_prior_win_rate",
    "favorite_prior_win_rate",
    "recent_win_rate_gap",
    "h2h_prior_games",
    "h2h_underdog_win_rate",
    "h2h_draw_rate",
    "underdog_prior_draw_rate",
    "favorite_prior_draw_rate",
    "prior_scoring_rate_gap",
    "prior_conceding_rate_gap",
)

ALLOWED_FEATURES = REQUIRED_IDENTITY_FEATURES + ALLOWED_PRIOR_FEATURES

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

_SHA256_RE = re.compile(r"^[a-fA-F0-9]{64}$")


def _key(name: str) -> str:
    return "".join(ch for ch in str(name or "").casefold() if ch.isalnum())


def _is_valid_sha256(s: str) -> bool:
    return bool(_SHA256_RE.match(s.strip())) if isinstance(s, str) else False


def _canonical_event_repr(row: SettledEvent) -> dict[str, Any]:
    """Canonical versioned representation of all fields affecting dataset, excluding odds deliberately.

    Included fields (affect identity, labeling, historical features, eligibility, dedup, provenance):
    - event_id, sport, event_date, participant_1, participant_2, winner_index, disposition,
      probability_1, probability_2, draw_probability, score_1, score_2 (used by prior-history),
      league (competition key used by history if needed), source_url, raw_sha256 (from facets if present)
    Excluded: odds_1, odds_2 (price independence, documented)
    """
    # Extract raw_sha256 from facets if present (history_*.jsonl.gz stores it there)
    raw_sha = ""
    if isinstance(row.facets, dict):
        candidate = row.facets.get("raw_sha256")
        if isinstance(candidate, str):
            raw_sha = candidate

    return {
        "event_id": row.event_id,
        "sport": row.sport,
        "event_date": row.event_date,
        "participant_1": row.participant_1,
        "participant_2": row.participant_2,
        "winner_index": row.winner_index,
        "disposition": row.disposition,
        "probability_1": row.probability_1,
        "probability_2": row.probability_2,
        "draw_probability": row.draw_probability,
        "score_1": row.score_1,
        "score_2": row.score_2,
        "league": row.league,
        "source_url": row.source_url,
        "raw_sha256": raw_sha,
        "version": "canonical-v1",  # versioned representation
    }


def _compute_input_digest(rows: list[SettledEvent]) -> str:
    """Strengthened digest: hash canonical representation of all fields affecting dataset, stable under reordering.

    Odds deliberately excluded (documented) since they do not affect new dataset.
    """
    # Sort rows deterministically before hashing to ensure stable under reordering
    sorted_rows = sorted(rows, key=lambda r: (r.event_date, r.sport, r.event_id))
    blobs = []
    for r in sorted_rows:
        canon = _canonical_event_repr(r)
        # json dumps sorted keys for stability
        blobs.append(json.dumps(canon, sort_keys=True, separators=(",", ":")))
    combined = "\n".join(blobs)
    return hashlib.sha256(combined.encode()).hexdigest()[:16]


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
    label: int
    features: dict[str, float | None]
    missingness: dict[str, int]
    source_url: str = ""
    raw_sha256: str = ""
    feature_contract_version: str = FEATURE_CONTRACT_VERSION
    label_contract_version: str = LABEL_CONTRACT_VERSION
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
        for k in self.features:
            if k in PROHIBITED_KEYS:
                raise ValueError(f"prohibited feature key in example: {k}")
        if self.underdog_index == 0:
            raise ValueError("underdog_index must never be 0 (draw)")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["features"] = {k: self.features[k] for k in sorted(self.features)}
        payload["missingness"] = {k: self.missingness[k] for k in sorted(self.missingness)}
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> PriceFreeUnderdogExample:
        data = dict(payload)
        allowed = set(cls.__dataclass_fields__.keys())
        filtered = {k: v for k, v in data.items() if k in allowed}
        return cls(**filtered)


@dataclass(frozen=True)
class PriceFreeDatasetReceipt:
    """Deterministic audit receipt with raw vs canonical accounting (Milestone 4E hardening)."""

    # Raw vs canonical accounting
    raw_input_rows: int
    schema_excluded_rows: int
    valid_loaded_rows: int
    exact_duplicates_collapsed: int
    canonical_input_rows: int
    eligible_examples: int
    builder_excluded_rows: int

    # Legacy required counts (global) — builder exclusions
    input_rows: int  # alias for canonical_input_rows for backward compat, but explicit
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
    provenance_invalid: int
    positive_rate: float | None
    # Date semantics explicit
    canonical_date_min: str | None
    canonical_date_max: str | None
    eligible_date_min: str | None
    eligible_date_max: str | None
    # Backward compat date_min/max alias eligible dates
    date_min: str | None
    date_max: str | None
    feature_contract_version: str
    label_contract_version: str
    input_digest: str
    per_sport: dict[str, dict[str, int]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
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


def _prior_scoring_stats(
    sport: str,
    participant_name: str,
    event_date: str,
    history: HistoryIndex,
) -> tuple[float | None, float | None, int, int]:
    key = _key(participant_name)
    rows = history._earlier(history.by_participant.get((sport, key), []), event_date)
    total = len(rows)
    scored_sum = 0.0
    conceded_sum = 0.0
    scored_count = 0
    conceded_count = 0
    draw_count = 0
    for r in rows:
        is_p1 = _key(r.participant_1) == key
        is_p2 = _key(r.participant_2) == key
        if not (is_p1 or is_p2):
            continue
        if r.winner_index == 0:
            draw_count += 1
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


# ---------------------------------------------------------------------------
# Schema adapter — no unsafe defaults, explicit counting
# ---------------------------------------------------------------------------

@dataclass
class SchemaLoadResult:
    valid_events: list[SettledEvent]
    raw_input_rows: int
    schema_excluded_rows: int
    schema_exclusion_reasons: Counter
    file_errors: list[str]


def _validate_settled_dict(d: dict[str, Any]) -> SettledEvent:
    """Validate a raw dict as SettledEvent — no fabricated defaults.

    Required behavior (Milestone 4E):
    - Missing winner → explicit excluded_invalid_winner (raise with reason INVALID_WINNER)
    - Missing/unknown disposition → explicit exclusion (raise DISPOSITION_MISSING/UNKNOWN)
    - Missing date, sport, participants → explicit schema exclusion
    - Missing probabilities allowed as None at schema level (builder will exclude as missing_probability)
    - Never infer outcome from score unless canonical loader already does so (we don't)
    - Never invent source hashes
    - Only documented schema fields accepted, unknown schema version rejected if present
    """

    # Check for unknown schema version if present
    if "schema_version" in d:
        # Only allow known versions or no version — reject unknown
        sv = d.get("schema_version")
        # Documented schemas: no version field, or version "v1" — reject others
        if sv not in (None, "", "v1", "canonical-v1", "price-free-v1"):
            raise ValueError(f"UNKNOWN_SCHEMA_VERSION:{sv}")

    # Required fields — no defaults
    event_id = d.get("event_id")
    sport = d.get("sport")
    event_date = d.get("event_date")
    p1 = d.get("participant_1")
    p2 = d.get("participant_2")
    winner = d.get("winner_index")
    disposition = d.get("disposition")

    if not event_id or not isinstance(event_id, str):
        raise ValueError("SCHEMA_MISSING_EVENT_ID")
    if not sport or not isinstance(sport, str):
        raise ValueError("SCHEMA_MISSING_SPORT")
    if not event_date or not isinstance(event_date, str):
        raise ValueError("SCHEMA_MISSING_EVENT_DATE")
    # Validate date ISO
    try:
        # Allow YYYY-MM-DD
        datetime_date = event_date[:10]
        # Simple ISO check
        parts = datetime_date.split("-")
        if len(parts) != 3:
            raise ValueError
        int(parts[0]), int(parts[1]), int(parts[2])
    except Exception:
        raise ValueError(f"SCHEMA_INVALID_EVENT_DATE:{event_date}")

    if not p1 or not isinstance(p1, str):
        raise ValueError("SCHEMA_MISSING_PARTICIPANT_1")
    if not p2 or not isinstance(p2, str):
        raise ValueError("SCHEMA_MISSING_PARTICIPANT_2")

    if winner is None:
        raise ValueError("SCHEMA_MISSING_WINNER_INDEX")
    if winner not in (0, 1, 2):
        raise ValueError(f"SCHEMA_INVALID_WINNER_INDEX:{winner}")

    if disposition is None or not isinstance(disposition, str) or not disposition.strip():
        raise ValueError("SCHEMA_MISSING_DISPOSITION")
    # Unknown disposition → explicit exclusion later, but allow at schema level if non-empty
    # We will treat unknown disposition as builder exclusion, not schema exclusion, unless empty

    # Probabilities — allow None at schema level, but key must exist? We require key present to avoid silent loss
    # If key absent, count as schema missing probability
    if "probability_1" not in d:
        raise ValueError("SCHEMA_MISSING_PROBABILITY_1")
    if "probability_2" not in d:
        raise ValueError("SCHEMA_MISSING_PROBABILITY_2")

    prob1 = d.get("probability_1")
    prob2 = d.get("probability_2")
    draw_prob = d.get("draw_probability")

    # Scores — allow None
    score1 = d.get("score_1")
    score2 = d.get("score_2")

    # League — optional
    league = d.get("league", "")

    # Source URL — optional
    source_url = d.get("source_url", "")

    # Facets — optional dict, may contain raw_sha256
    facets = d.get("facets", {})
    if not isinstance(facets, dict):
        facets = {}

    # Odds — allowed in raw but must not affect new dataset (documented exclusion)
    odds1 = d.get("odds_1")
    odds2 = d.get("odds_2")

    # Build SettledEvent — this will validate some fields further
    try:
        return SettledEvent(
            event_id=event_id,
            sport=sport,
            event_date=event_date,
            participant_1=p1,
            participant_2=p2,
            winner_index=winner,
            score_1=score1,
            score_2=score2,
            probability_1=prob1,
            probability_2=prob2,
            draw_probability=draw_prob,
            forebet_pick=d.get("forebet_pick"),
            odds_1=odds1,
            odds_2=odds2,
            league=league,
            period_scores_1=tuple(d.get("period_scores_1", ())),
            period_scores_2=tuple(d.get("period_scores_2", ())),
            source_url=source_url,
            disposition=disposition,
            facets=facets,
        )
    except Exception as e:
        raise ValueError(f"SCHEMA_VALIDATION_FAILED:{type(e).__name__}:{e}") from e


def load_settled_events_from_dicts(raw_dicts: list[dict[str, Any]]) -> SchemaLoadResult:
    """Load SettledEvents from raw dicts with explicit counting, no silent swallowing of malformed rows.

    Returns valid events, counts, reasons, file_errors (empty for this in-memory loader).
    Malformed rows counted by reason, not silently skipped.
    """
    valid: list[SettledEvent] = []
    raw = len(raw_dicts)
    schema_excluded = 0
    reasons: Counter = Counter()
    file_errors: list[str] = []

    for idx, d in enumerate(raw_dicts):
        if not isinstance(d, dict):
            schema_excluded += 1
            reasons["SCHEMA_NOT_A_DICT"] += 1
            continue
        try:
            ev = _validate_settled_dict(d)
            valid.append(ev)
        except ValueError as ve:
            schema_excluded += 1
            msg = str(ve)
            # Extract reason prefix
            reason = msg.split(":")[0] if ":" in msg else msg
            reasons[reason] += 1
        except Exception as e:
            schema_excluded += 1
            reasons[f"SCHEMA_UNEXPECTED_{type(e).__name__}"] += 1

    return SchemaLoadResult(
        valid_events=valid,
        raw_input_rows=raw,
        schema_excluded_rows=schema_excluded,
        schema_exclusion_reasons=reasons,
        file_errors=file_errors,
    )


def build_price_free_examples(
    settled_events: list[SettledEvent],
    *,
    feature_contract_version: str = FEATURE_CONTRACT_VERSION,
    label_contract_version: str = LABEL_CONTRACT_VERSION,
) -> tuple[list[PriceFreeUnderdogExample], PriceFreeDatasetReceipt]:
    """Build leak-safe price-free examples from already validated settled events.

    This function assumes input has passed schema validation (via _validate_settled_dict or direct SettledEvent construction).
    For raw accounting with schema exclusions, use load_settled_events_from_dicts first, then this builder, then combine counts.

    Rules:
    - history_event_date < current_event_date (same-date excluded via HistoryIndex._earlier)
    - No odds influence
    - Draw-capable: underdog win 1, fav win 0, draw 0, void excluded
    - Two-way: underdog win 1, fav win 0, draw excluded
    - Equal/missing/non-finite/out-of-range probabilities excluded
    - Unknown sport excluded
    - Deterministic ordering, duplicate handling, conflicting keys fail loudly
    - Composite key (sport, event_id, event_date) matching settlement.py — same event_id in different sports does not collapse
    """

    # Deduplicate and detect conflicting composite keys
    # Composite key: (sport, event_id, event_date) — matches settlement.py seen key
    dedup: dict[tuple[str, str, str], SettledEvent] = {}
    exact_duplicates_collapsed = 0

    for row in settled_events:
        key = (row.sport, row.event_id, row.event_date)
        if key in dedup:
            existing = dedup[key]
            # Exact duplicate check: all fields affecting dataset must match, excluding provenance and odds deliberately
            # Fields: event_id, sport, event_date, participant_1, participant_2, winner_index, disposition,
            # probability_1, probability_2, draw_probability, score_1, score_2, league
            # If these match, collapse even if source_url/raw_sha256 differ (per existing integrity policy)
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
                and existing.league == row.league
                and existing.event_id == row.event_id
                and existing.sport == row.sport
                and existing.event_date == row.event_date
            ):
                exact_duplicates_collapsed += 1
                continue
            else:
                raise ValueError(f"conflicting composite key {key}: {existing} vs {row}")
        dedup[key] = row

    sorted_rows = sorted(dedup.values(), key=lambda r: (r.event_date, r.sport, r.event_id))

    # Raw vs canonical accounting for this builder stage
    # Here valid_loaded_rows = len(settled_events) (input to builder)
    # canonical_input_rows = len(sorted_rows) after dedup
    valid_loaded_rows = len(settled_events)
    canonical_input_rows = len(sorted_rows)

    input_digest = _compute_input_digest(sorted_rows)
    canonical_date_min = min((r.event_date for r in sorted_rows), default=None)
    canonical_date_max = max((r.event_date for r in sorted_rows), default=None)

    history = HistoryIndex(sorted_rows)

    examples: list[PriceFreeUnderdogExample] = []
    exclusion_counter: Counter = Counter()
    per_sport_counter: dict[str, Counter] = defaultdict(Counter)
    provenance_present = 0
    provenance_missing = 0
    provenance_invalid = 0
    positive = 0
    negative_fav = 0
    negative_draw = 0

    for row in sorted_rows:
        sport = row.sport
        if sport not in SPORTS:
            exclusion_counter["excluded_unknown_sport"] += 1
            per_sport_counter[sport]["excluded_unknown_sport"] += 1
            continue

        disp = (row.disposition or "SETTLED").upper()
        if "VOID" in disp or disp in {"CANCELLED", "CANCELED", "ABANDONED", "NO_CONTEST", "POSTPONED"}:
            exclusion_counter["excluded_void"] += 1
            per_sport_counter[sport]["excluded_void"] += 1
            continue

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

        assert label_result.label in (0, 1)
        label = label_result.label
        if label == 1:
            positive += 1
        else:
            if label_result.is_draw:
                negative_draw += 1
            else:
                negative_fav += 1

        # Provenance validation — raw_sha256 must be 64 hex chars to count as present
        raw_sha = ""
        if isinstance(row.facets, dict):
            cand = row.facets.get("raw_sha256")
            if isinstance(cand, str):
                raw_sha = cand
        if raw_sha:
            if _is_valid_sha256(raw_sha):
                provenance_present += 1
            else:
                provenance_invalid += 1
        else:
            # Also check source_url presence? For provenance_present we require valid sha256
            # But legacy compatibility: if raw_sha missing but source_url present, count as missing not invalid
            provenance_missing += 1

        per_sport_counter[sport]["eligible_examples"] += 1
        if label == 1:
            per_sport_counter[sport]["positive_underdog_wins"] += 1
        else:
            if label_result.is_draw:
                per_sport_counter[sport]["negative_draws"] += 1
            else:
                per_sport_counter[sport]["negative_favorite_wins"] += 1

        h2h, recent_1, recent_2 = history.context(sport, row.event_date, row.participant_1, row.participant_2)

        fav_idx = identity.favorite_index
        dog_idx = identity.underdog_index
        assert fav_idx in (1, 2) and dog_idx in (1, 2)

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

        h2h_prior_games = h2h.total_games
        if h2h_prior_games > 0:
            p1_wins = h2h.participant_1_wins
            p2_wins = h2h.participant_2_wins
            if dog_idx == 1:
                dog_h2h_wins = p1_wins
            else:
                dog_h2h_wins = p2_wins
            h2h_underdog_win_rate = dog_h2h_wins / h2h_prior_games if h2h_prior_games else None
            h2h_draw_rate = (h2h_prior_games - p1_wins - p2_wins) / h2h_prior_games if h2h_prior_games else None
        else:
            h2h_underdog_win_rate = None
            h2h_draw_rate = None

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

        features: dict[str, float | None] = {
            "forebet_favorite_probability": identity.favorite_probability,
            "forebet_underdog_probability": identity.underdog_probability,
            "forebet_probability_gap": identity.probability_gap,
            "forebet_draw_probability": identity.draw_probability,
            "forebet_draw_probability_missing": 1.0 if identity.draw_probability is None else 0.0,
            "underdog_prior_games": float(underdog_prior_games),
            "favorite_prior_games": float(favorite_prior_games),
            "underdog_prior_win_rate": underdog_prior_win_rate,
            "favorite_prior_win_rate": favorite_prior_win_rate,
            "recent_win_rate_gap": recent_win_rate_gap,
            "h2h_prior_games": float(h2h_prior_games),
            "h2h_underdog_win_rate": h2h_underdog_win_rate,
            "h2h_draw_rate": h2h_draw_rate,
            "underdog_prior_draw_rate": underdog_prior_draw_rate,
            "favorite_prior_draw_rate": favorite_prior_draw_rate,
            "prior_scoring_rate_gap": prior_scoring_rate_gap,
            "prior_conceding_rate_gap": prior_conceding_rate_gap,
        }

        missingness: dict[str, int] = {}
        for k, v in features.items():
            if k == "forebet_draw_probability_missing":
                missingness[k] = 0
            else:
                missingness[k] = 1 if v is None else 0
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
            raw_sha256=raw_sha,
            feature_contract_version=feature_contract_version,
            label_contract_version=label_contract_version,
            exclusion_reason=None,
            legacy_provenance_missing=not bool(raw_sha),
        )

        for prohibited in PROHIBITED_KEYS:
            if prohibited in example.features:
                raise ValueError(f"prohibited key in features: {prohibited}")

        examples.append(example)

    examples = sorted(examples, key=lambda e: (e.event_date, e.sport, e.event_id))

    eligible_examples = len(examples)
    builder_excluded_rows = canonical_input_rows - eligible_examples

    # For backward compat, input_rows = canonical_input_rows
    input_rows = canonical_input_rows

    positive_rate = (positive / eligible_examples) if eligible_examples else None
    eligible_date_min = min((e.event_date for e in examples), default=None)
    eligible_date_max = max((e.event_date for e in examples), default=None)

    # Raw accounting for this builder alone (no schema stage) — set raw=valid=canonical for now
    # When used via adapter, raw accounting will be combined
    raw_input_rows = valid_loaded_rows
    schema_excluded_rows = 0
    # Note: exact_duplicates_collapsed already counted

    receipt = PriceFreeDatasetReceipt(
        raw_input_rows=raw_input_rows,
        schema_excluded_rows=schema_excluded_rows,
        valid_loaded_rows=valid_loaded_rows,
        exact_duplicates_collapsed=exact_duplicates_collapsed,
        canonical_input_rows=canonical_input_rows,
        eligible_examples=eligible_examples,
        builder_excluded_rows=builder_excluded_rows,
        input_rows=input_rows,
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
        provenance_invalid=provenance_invalid,
        positive_rate=positive_rate,
        canonical_date_min=canonical_date_min,
        canonical_date_max=canonical_date_max,
        eligible_date_min=eligible_date_min,
        eligible_date_max=eligible_date_max,
        date_min=eligible_date_min,
        date_max=eligible_date_max,
        feature_contract_version=feature_contract_version,
        label_contract_version=label_contract_version,
        input_digest=input_digest,
        per_sport={sport: dict(counter) for sport, counter in per_sport_counter.items()},
    )

    return examples, receipt


def build_dataset_with_raw_accounting(
    raw_dicts: list[dict[str, Any]],
    *,
    feature_contract_version: str = FEATURE_CONTRACT_VERSION,
    label_contract_version: str = LABEL_CONTRACT_VERSION,
) -> tuple[list[PriceFreeUnderdogExample], PriceFreeDatasetReceipt, SchemaLoadResult]:
    """Full pipeline with raw vs canonical accounting (Milestone 4E).

    Steps:
    1. Schema validation via load_settled_events_from_dicts — counts raw, schema_excluded, valid_loaded
    2. Builder via build_price_free_examples — counts exact_duplicates_collapsed, canonical, eligible, builder_excluded
    3. Combined receipt with invariants:
       raw = schema_excluded + valid_loaded
       valid = exact_duplicates_collapsed + canonical
       canonical = eligible + builder_excluded
    """
    schema_result = load_settled_events_from_dicts(raw_dicts)

    # Builder stage
    examples, builder_receipt = build_price_free_examples(
        schema_result.valid_events,
        feature_contract_version=feature_contract_version,
        label_contract_version=label_contract_version,
    )

    # Combine accounting — builder receipt already has valid_loaded, canonical, exact_duplicates, etc.
    # But raw_input_rows in builder receipt is valid_loaded_rows, not true raw
    # So we construct final receipt with true raw counts

    final_receipt = PriceFreeDatasetReceipt(
        raw_input_rows=schema_result.raw_input_rows,
        schema_excluded_rows=schema_result.schema_excluded_rows,
        valid_loaded_rows=schema_result.raw_input_rows - schema_result.schema_excluded_rows,
        exact_duplicates_collapsed=builder_receipt.exact_duplicates_collapsed,
        canonical_input_rows=builder_receipt.canonical_input_rows,
        eligible_examples=builder_receipt.eligible_examples,
        builder_excluded_rows=builder_receipt.builder_excluded_rows,
        input_rows=builder_receipt.canonical_input_rows,
        positive_underdog_wins=builder_receipt.positive_underdog_wins,
        negative_favorite_wins=builder_receipt.negative_favorite_wins,
        negative_draws=builder_receipt.negative_draws,
        excluded_void=builder_receipt.excluded_void,
        excluded_source_conflict=builder_receipt.excluded_source_conflict,
        excluded_equal_probability=builder_receipt.excluded_equal_probability,
        excluded_missing_probability=builder_receipt.excluded_missing_probability,
        excluded_non_finite_probability=builder_receipt.excluded_non_finite_probability,
        excluded_out_of_range_probability=builder_receipt.excluded_out_of_range_probability,
        excluded_unknown_sport=builder_receipt.excluded_unknown_sport,
        excluded_unexpected_two_way_draw=builder_receipt.excluded_unexpected_two_way_draw,
        excluded_invalid_winner=builder_receipt.excluded_invalid_winner,
        excluded_other=builder_receipt.excluded_other,
        provenance_present=builder_receipt.provenance_present,
        provenance_missing=builder_receipt.provenance_missing,
        provenance_invalid=builder_receipt.provenance_invalid,
        positive_rate=builder_receipt.positive_rate,
        canonical_date_min=builder_receipt.canonical_date_min,
        canonical_date_max=builder_receipt.canonical_date_max,
        eligible_date_min=builder_receipt.eligible_date_min,
        eligible_date_max=builder_receipt.eligible_date_max,
        date_min=builder_receipt.date_min,
        date_max=builder_receipt.date_max,
        feature_contract_version=builder_receipt.feature_contract_version,
        label_contract_version=builder_receipt.label_contract_version,
        input_digest=builder_receipt.input_digest,
        per_sport=builder_receipt.per_sport,
    )

    return examples, final_receipt, schema_result
