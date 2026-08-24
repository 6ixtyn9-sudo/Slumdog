"""Price-free underdog identity, label, and assessment contracts.

Milestone 2 — pure Forebet identity, label, and contract foundation.

This module introduces price-free contracts without destructively repurposing
legacy odds-first machinery:

- Legacy `CandidateState` (SHADOW_UNPRICED/PRICED/CERTIFIED/REJECTED) and
  `RobberCandidate` remain in `contracts.py` for forensic comparability.
  They are marked as legacy and must not be used for new price-free path
  until explicitly approved cleanup.

- New path uses:
  - `UnderdogAssessmentStatus` (event-level): STRONG_UNDERDOG, WATCHLIST,
    INSUFFICIENT_EVIDENCE, REJECTED_SOURCE_CONFLICT, INELIGIBLE
  - `DailyShortlistStatus` (daily-level): CANDIDATES_FOUND, NO_STRONG_UNDERDOG,
    SOURCE_FAILURE
  - `ForebetUnderdogIdentity` — pure result of Forebet probability comparison
  - `UnderdogLabelResult` — pure label for underdog-win target
  - `StrongUnderdogAssessment` — price-free assessment (no price fields)
  - `DailyUnderdogShortlist` — daily result with explicit NO_STRONG_UNDERDOG

Invariants enforced:
- Favorite = higher Forebet participant probability
- Underdog = lower Forebet participant probability
- Draw probability does NOT determine identity, never selected
- Equal participant probabilities → no underdog, explicit ineligibility reason
- Missing/invalid probabilities → no underdog, explicit reason
- No fallback to odds, forebet_pick, or recent form
- No arbitrary gap threshold for identity (strength/gap belongs to later selection policy)
- Odds must be outside core assessment — optional_price_context isolated block only

Training remains frozen. No feature-vector, threshold, ranking, or model approval
changes in this milestone.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Assessment statuses — separated daily vs event level (per user correction)
# ---------------------------------------------------------------------------

class UnderdogAssessmentStatus(str, Enum):
    """Event-level assessment status — describes a single event evaluation."""

    STRONG_UNDERDOG = "STRONG_UNDERDOG"
    WATCHLIST = "WATCHLIST"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    REJECTED_SOURCE_CONFLICT = "REJECTED_SOURCE_CONFLICT"
    INELIGIBLE = "INELIGIBLE"


class DailyShortlistStatus(str, Enum):
    """Daily-level shortlist status — describes the whole day result."""

    CANDIDATES_FOUND = "CANDIDATES_FOUND"
    NO_STRONG_UNDERDOG = "NO_STRONG_UNDERDOG"
    SOURCE_FAILURE = "SOURCE_FAILURE"


# ---------------------------------------------------------------------------
# Identity — pure Forebet probability comparison
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ForebetUnderdogIdentity:
    """Pure result of Forebet participant probability comparison.

    No price inputs, no fallback to pick/form.
    """

    favorite_index: int | None
    underdog_index: int | None
    favorite_probability: float | None
    underdog_probability: float | None
    probability_gap: float | None
    eligible: bool
    ineligibility_reason: str | None = None
    # Optional context preserved for audit, not used for identity decision
    draw_probability: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_finite_prob(value: Any) -> bool:
    """Check present, finite, in [0,1]."""
    if value is None:
        return False
    try:
        f = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f) and 0.0 <= f <= 1.0


def identify_forebet_underdog(
    probability_1: float | None,
    probability_2: float | None,
    draw_probability: float | None = None,
) -> ForebetUnderdogIdentity:
    """Pure Forebet underdog identity — no price, no pick, no form fallback.

    Required behavior (Milestone 2A):
    - Validate both participant probabilities present, finite, in [0,1]
    - Higher prob = favorite, lower = underdog
    - Draw prob does not determine identity
    - Exact equal → no underdog
    - Missing/invalid → no underdog
    - Explicit ineligibility reason
    - No arbitrary gap threshold

    Draw probability may be larger than both participant probs (e.g., 0.5 draw,
    0.25 each side) — it must NOT become selected outcome; identity remains
    based only on participant probs (or ineligible if equal).
    """

    # Validate participant probabilities
    if not _is_finite_prob(probability_1) or not _is_finite_prob(probability_2):
        # Determine specific reason
        if probability_1 is None or probability_2 is None:
            reason = "MISSING_PROBABILITY"
        elif not math.isfinite(float(probability_1 or 0)) or not math.isfinite(float(probability_2 or 0)):
            reason = "NON_FINITE_PROBABILITY"
        else:
            # Out of range
            try:
                p1 = float(probability_1) if probability_1 is not None else -1
                p2 = float(probability_2) if probability_2 is not None else -1
                if not (0.0 <= p1 <= 1.0) or not (0.0 <= p2 <= 1.0):
                    reason = "OUT_OF_RANGE_PROBABILITY"
                else:
                    reason = "INVALID_PROBABILITY"
            except Exception:
                reason = "INVALID_PROBABILITY"
        return ForebetUnderdogIdentity(
            favorite_index=None,
            underdog_index=None,
            favorite_probability=float(probability_1) if _is_finite_prob(probability_1) else None,
            underdog_probability=float(probability_2) if _is_finite_prob(probability_2) else None,
            probability_gap=None,
            eligible=False,
            ineligibility_reason=reason,
            draw_probability=float(draw_probability) if _is_finite_prob(draw_probability) else None,
        )

    p1 = float(probability_1)
    p2 = float(probability_2)

    # Exact equal — no underdog (explicit, not silent)
    if p1 == p2:
        return ForebetUnderdogIdentity(
            favorite_index=None,
            underdog_index=None,
            favorite_probability=p1,
            underdog_probability=p2,
            probability_gap=0.0,
            eligible=False,
            ineligibility_reason="EQUAL_PROBABILITY",
            draw_probability=float(draw_probability) if _is_finite_prob(draw_probability) else None,
        )

    # Higher prob = favorite, lower = underdog
    if p1 > p2:
        fav_idx, dog_idx = 1, 2
        fav_prob, dog_prob = p1, p2
    else:
        fav_idx, dog_idx = 2, 1
        fav_prob, dog_prob = p2, p1

    gap = fav_prob - dog_prob

    return ForebetUnderdogIdentity(
        favorite_index=fav_idx,
        underdog_index=dog_idx,
        favorite_probability=fav_prob,
        underdog_probability=dog_prob,
        probability_gap=gap,
        eligible=True,
        ineligibility_reason=None,
        draw_probability=float(draw_probability) if _is_finite_prob(draw_probability) else None,
    )


# ---------------------------------------------------------------------------
# Label — pure underdog-win target
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UnderdogLabelResult:
    """Pure label for underdog-win target, separate from training orchestration.

    Milestone 2E hardening:
    - Preserves identity exclusion reason (EQUAL_PROBABILITY, MISSING_PROBABILITY,
      NON_FINITE, OUT_OF_RANGE, etc.) not collapsed into generic NO_ELIGIBLE_IDENTITY
      unless original reason also retained separately.
    - Derives draw capability from sport registry, not caller override.
    """

    label: int | None  # 1 underdog win, 0 favorite win or draw (draw-capable), None excluded
    eligible: bool
    exclusion_reason: str | None = None
    is_draw: bool = False
    is_void: bool = False
    is_source_conflict: bool = False
    winner_index: int | None = None
    favorite_index: int | None = None
    underdog_index: int | None = None
    # Milestone 2E: preserve original identity ineligibility reason separately
    identity_ineligibility_reason: str | None = None
    sport: str | None = None
    draw_possible: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_valid_winner_index(value: Any) -> bool:
    return value in (0, 1, 2)


def _label_from_indices(
    *,
    sport: str,
    favorite_index: int | None,
    underdog_index: int | None,
    winner_index: int | None,
    disposition: str = "SETTLED",
    draw_possible: bool,
    source_conflict: bool = False,
    identity_ineligibility_reason: str | None = None,
    has_eligible_identity: bool | None = None,
) -> UnderdogLabelResult:
    """Private low-level helper accepting raw indices — for internal focused use.

    Production callers and tests should use identity-bound public function
    `label_underdog_outcome(sport, identity, ...)`.
    """

    # Source conflict — highest priority exclusion
    if source_conflict:
        return UnderdogLabelResult(
            label=None,
            eligible=False,
            exclusion_reason="SOURCE_CONFLICT",
            is_draw=False,
            is_void=False,
            is_source_conflict=True,
            winner_index=winner_index if _is_valid_winner_index(winner_index) else None,
            favorite_index=favorite_index,
            underdog_index=underdog_index,
            identity_ineligibility_reason=identity_ineligibility_reason,
            sport=sport,
            draw_possible=draw_possible,
        )

    # Void / cancelled / no-contest
    if isinstance(disposition, str) and disposition.upper() == "VOID":
        return UnderdogLabelResult(
            label=None,
            eligible=False,
            exclusion_reason="VOID",
            is_draw=False,
            is_void=True,
            winner_index=winner_index if _is_valid_winner_index(winner_index) else None,
            favorite_index=favorite_index,
            underdog_index=underdog_index,
            identity_ineligibility_reason=identity_ineligibility_reason,
            sport=sport,
            draw_possible=draw_possible,
        )

    # No eligible identity — preserve exact identity reason if available
    if has_eligible_identity is False or favorite_index is None or underdog_index is None:
        # Preserve original identity reason if provided, else generic
        reason = identity_ineligibility_reason or "NO_ELIGIBLE_IDENTITY"
        return UnderdogLabelResult(
            label=None,
            eligible=False,
            exclusion_reason=reason,
            winner_index=winner_index if _is_valid_winner_index(winner_index) else None,
            favorite_index=favorite_index,
            underdog_index=underdog_index,
            identity_ineligibility_reason=identity_ineligibility_reason,
            sport=sport,
            draw_possible=draw_possible,
        )

    if favorite_index == underdog_index:
        return UnderdogLabelResult(
            label=None,
            eligible=False,
            exclusion_reason=identity_ineligibility_reason or "EQUAL_PROBABILITY",
            winner_index=winner_index if _is_valid_winner_index(winner_index) else None,
            favorite_index=favorite_index,
            underdog_index=underdog_index,
            identity_ineligibility_reason=identity_ineligibility_reason,
            sport=sport,
            draw_possible=draw_possible,
        )

    # Invalid winner index
    if not _is_valid_winner_index(winner_index):
        return UnderdogLabelResult(
            label=None,
            eligible=False,
            exclusion_reason="INVALID_WINNER_INDEX",
            winner_index=None,
            favorite_index=favorite_index,
            underdog_index=underdog_index,
            identity_ineligibility_reason=identity_ineligibility_reason,
            sport=sport,
            draw_possible=draw_possible,
        )

    # Draw handling — derived from sport registry, caller cannot override
    if winner_index == 0:
        if draw_possible:
            # Draw-capable: draw = 0 (failed underdog win)
            return UnderdogLabelResult(
                label=0,
                eligible=True,
                exclusion_reason=None,
                is_draw=True,
                is_void=False,
                winner_index=0,
                favorite_index=favorite_index,
                underdog_index=underdog_index,
                identity_ineligibility_reason=None,
                sport=sport,
                draw_possible=draw_possible,
            )
        else:
            # Two-way: unexpected draw excluded as invalid/unsettleable
            return UnderdogLabelResult(
                label=None,
                eligible=False,
                exclusion_reason="UNEXPECTED_DRAW_FOR_TWO_WAY",
                is_draw=True,
                is_void=False,
                winner_index=0,
                favorite_index=favorite_index,
                underdog_index=underdog_index,
                identity_ineligibility_reason=identity_ineligibility_reason,
                sport=sport,
                draw_possible=draw_possible,
            )

    # Favorite / underdog win
    if winner_index == underdog_index:
        return UnderdogLabelResult(
            label=1,
            eligible=True,
            exclusion_reason=None,
            is_draw=False,
            is_void=False,
            winner_index=winner_index,
            favorite_index=favorite_index,
            underdog_index=underdog_index,
            identity_ineligibility_reason=None,
            sport=sport,
            draw_possible=draw_possible,
        )
    elif winner_index == favorite_index:
        return UnderdogLabelResult(
            label=0,
            eligible=True,
            exclusion_reason=None,
            is_draw=False,
            is_void=False,
            winner_index=winner_index,
            favorite_index=favorite_index,
            underdog_index=underdog_index,
            identity_ineligibility_reason=None,
            sport=sport,
            draw_possible=draw_possible,
        )
    else:
        # Winner is neither favorite nor underdog (should not happen if identity valid)
        return UnderdogLabelResult(
            label=None,
            eligible=False,
            exclusion_reason="WINNER_MISMATCH_IDENTITY",
            is_draw=False,
            is_void=False,
            winner_index=winner_index,
            favorite_index=favorite_index,
            underdog_index=underdog_index,
            identity_ineligibility_reason=identity_ineligibility_reason,
            sport=sport,
            draw_possible=draw_possible,
        )


def label_underdog_outcome(
    sport: str,
    identity: ForebetUnderdogIdentity,
    winner_index: int | None,
    disposition: str = "SETTLED",
    source_conflict: bool = False,
) -> UnderdogLabelResult:
    """Public label function — accepts verified identity object directly (Milestone 2E).

    Hardening (Milestone 2E):
    - Accepts ForebetUnderdogIdentity directly, derives favorite_index,
      underdog_index, eligible, ineligibility_reason from identity.
      Caller cannot reverse favorite/underdog or claim eligible when not.
    - Derives draw capability from sport registry SPORTS[sport].draw_possible,
      not caller-provided draw_possible. Caller cannot override sport semantics.
    - Unknown sport → explicit exclusion (UNKNOWN_SPORT) per existing contract style.
    - Preserves identity exclusion reason (EQUAL_PROBABILITY, MISSING_PROBABILITY,
      NON_FINITE, OUT_OF_RANGE, etc.) not collapsed into generic NO_ELIGIBLE_IDENTITY
      unless original reason also retained separately in identity_ineligibility_reason.

    Required outcomes:
      Draw-capable: underdog win 1, fav win 0, draw 0, void excluded
      Two-way: underdog win 1, fav win 0, unexpected draw excluded, void excluded
    """

    # Derive draw capability from sport registry — caller cannot override
    try:
        from .sports import SPORTS

        spec = SPORTS.get(sport)
        if spec is None:
            # Unknown sport → explicit exclusion/error per existing contract style
            return UnderdogLabelResult(
                label=None,
                eligible=False,
                exclusion_reason="UNKNOWN_SPORT",
                is_draw=False,
                is_void=False,
                winner_index=winner_index if _is_valid_winner_index(winner_index) else None,
                favorite_index=identity.favorite_index,
                underdog_index=identity.underdog_index,
                identity_ineligibility_reason=identity.ineligibility_reason,
                sport=sport,
                draw_possible=None,
            )
        draw_possible = bool(spec.draw_possible)
    except Exception:
        # If registry import fails, treat as unknown sport for safety
        return UnderdogLabelResult(
            label=None,
            eligible=False,
            exclusion_reason="UNKNOWN_SPORT",
            is_draw=False,
            is_void=False,
            winner_index=winner_index if _is_valid_winner_index(winner_index) else None,
            favorite_index=identity.favorite_index,
            underdog_index=identity.underdog_index,
            identity_ineligibility_reason=identity.ineligibility_reason,
            sport=sport,
            draw_possible=None,
        )

    # Preserve identity exclusion reason — if not eligible, keep exact reason
    if not identity.eligible:
        return UnderdogLabelResult(
            label=None,
            eligible=False,
            exclusion_reason=identity.ineligibility_reason or "NO_ELIGIBLE_IDENTITY",
            is_draw=False,
            is_void=False,
            is_source_conflict=source_conflict,
            winner_index=winner_index if _is_valid_winner_index(winner_index) else None,
            favorite_index=identity.favorite_index,
            underdog_index=identity.underdog_index,
            identity_ineligibility_reason=identity.ineligibility_reason,
            sport=sport,
            draw_possible=draw_possible,
        )

    # Use private helper with derived values — ensures label uses indices from identity
    return _label_from_indices(
        sport=sport,
        favorite_index=identity.favorite_index,
        underdog_index=identity.underdog_index,
        winner_index=winner_index,
        disposition=disposition,
        draw_possible=draw_possible,
        source_conflict=source_conflict,
        identity_ineligibility_reason=identity.ineligibility_reason,
        has_eligible_identity=identity.eligible,
    )


# ---------------------------------------------------------------------------
# Price-free contracts — StrongUnderdogAssessment and DailyUnderdogShortlist
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StrongUnderdogAssessment:
    """Price-free assessment — no price fields in core contract.

    At minimum (Milestone 2C):
      event_id, sport, event_date, participant_1, participant_2,
      favorite_index, underdog_index, favorite_name, underdog_name,
      favorite_probability, underdog_probability, draw_probability,
      probability_gap, status, supporting_evidence, contradicting_evidence,
      missing_evidence, source_url, raw_sha256, captured_at, assessment_version

    Optional reserved for later approved stages (must remain None until legitimately produced):
      slumdog_underdog_probability, probability_lift, baseline_strength_score

    Optional price context isolated block — may be absent, no other field may depend on it.
    """

    event_id: str
    sport: str
    event_date: str
    participant_1: str
    participant_2: str
    favorite_index: int
    underdog_index: int
    favorite_name: str
    underdog_name: str
    favorite_probability: float
    underdog_probability: float
    draw_probability: float | None
    probability_gap: float
    status: UnderdogAssessmentStatus
    supporting_evidence: tuple[str, ...] = ()
    contradicting_evidence: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    source_url: str = ""
    raw_sha256: str = ""
    captured_at: str = ""
    assessment_version: str = "price-free-v1"

    # Reserved for later approved stages — must remain None until legitimately produced
    slumdog_underdog_probability: float | None = None
    probability_lift: float | None = None
    baseline_strength_score: float | None = None

    # Isolated optional price context — may be absent, no other field depends on it
    optional_price_context: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("event_id required")
        if self.favorite_index not in (1, 2) or self.underdog_index not in (1, 2):
            raise ValueError("favorite_index and underdog_index must be 1 or 2")
        if self.favorite_index == self.underdog_index:
            raise ValueError("favorite and underdog must differ")
        if not self.participant_1 or not self.participant_2:
            raise ValueError("two named participants required")
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
        # Ensure draw never selected — assessment is always participant 1 or 2
        # (enforced by favorite/underdog index 1/2)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value if isinstance(self.status, Enum) else str(self.status)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> StrongUnderdogAssessment:
        data = dict(payload)
        status_val = data.get("status")
        if isinstance(status_val, str):
            data["status"] = UnderdogAssessmentStatus(status_val)
        # Convert lists to tuples for evidence
        for key in ("supporting_evidence", "contradicting_evidence", "missing_evidence"):
            if key in data and isinstance(data[key], list):
                data[key] = tuple(data[key])
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass(frozen=True)
class DailyUnderdogShortlist:
    """Daily shortlist — explicit daily status, no fake candidate for no-pick.

    At minimum (Milestone 2C):
      target_date, generated_at, status, assessments_considered,
      strong_candidates, watchlist_candidates, rejection_counts,
      assessment_version, source_receipt

    No-pick example:
      status = NO_STRONG_UNDERDOG
      strong_candidates = ()
    """

    target_date: str
    generated_at: str
    status: DailyShortlistStatus
    assessments_considered: int
    strong_candidates: tuple[StrongUnderdogAssessment, ...] = ()
    watchlist_candidates: tuple[StrongUnderdogAssessment, ...] = ()
    rejection_counts: dict[str, int] = field(default_factory=dict)
    assessment_version: str = "price-free-v1"
    source_receipt: str = ""

    def __post_init__(self) -> None:
        # Validate target_date ISO
        try:
            datetime.fromisoformat(self.target_date)
        except Exception as exc:
            raise ValueError(f"target_date must be ISO date: {exc}") from exc
        # Validate generated_at timezone-aware
        try:
            parsed = datetime.fromisoformat(self.generated_at.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("generated_at must be timezone-aware")
        except Exception as exc:
            raise ValueError(f"generated_at must be ISO timezone-aware: {exc}") from exc
        if self.assessments_considered < 0:
            raise ValueError("assessments_considered must be >=0")
        # No fake candidate for no-pick: if NO_STRONG_UNDERDOG, strong_candidates must be empty
        if self.status == DailyShortlistStatus.NO_STRONG_UNDERDOG and len(self.strong_candidates) != 0:
            raise ValueError("NO_STRONG_UNDERDOG must have zero strong_candidates")
        # Ensure no draw selection — all candidates are participant 1/2 (enforced by assessment)
        for cand in self.strong_candidates + self.watchlist_candidates:
            if cand.underdog_index not in (1, 2):
                raise ValueError("candidate underdog_index must be 1 or 2, draw never selected")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value if isinstance(self.status, Enum) else str(self.status)
        payload["strong_candidates"] = [c.to_dict() for c in self.strong_candidates]
        payload["watchlist_candidates"] = [c.to_dict() for c in self.watchlist_candidates]
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> DailyUnderdogShortlist:
        data = dict(payload)
        status_val = data.get("status")
        if isinstance(status_val, str):
            data["status"] = DailyShortlistStatus(status_val)
        # Convert candidate dicts to objects
        if "strong_candidates" in data and isinstance(data["strong_candidates"], list):
            data["strong_candidates"] = tuple(
                StrongUnderdogAssessment.from_dict(item) if isinstance(item, dict) else item
                for item in data["strong_candidates"]
            )
        if "watchlist_candidates" in data and isinstance(data["watchlist_candidates"], list):
            data["watchlist_candidates"] = tuple(
                StrongUnderdogAssessment.from_dict(item) if isinstance(item, dict) else item
                for item in data["watchlist_candidates"]
            )
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Helper — build assessment from identity (without price)
# ---------------------------------------------------------------------------

def build_assessment_from_identity(
    *,
    event_id: str,
    sport: str,
    event_date: str,
    participant_1: str,
    participant_2: str,
    identity: ForebetUnderdogIdentity,
    status: UnderdogAssessmentStatus = UnderdogAssessmentStatus.INSUFFICIENT_EVIDENCE,
    supporting_evidence: tuple[str, ...] = (),
    contradicting_evidence: tuple[str, ...] = (),
    missing_evidence: tuple[str, ...] = (),
    source_url: str = "",
    raw_sha256: str = "",
    captured_at: str = "",
    assessment_version: str = "price-free-v1",
) -> StrongUnderdogAssessment | None:
    """Build StrongUnderdogAssessment from identity — returns None if not eligible."""

    if not identity.eligible:
        return None
    if identity.favorite_index is None or identity.underdog_index is None:
        return None
    if identity.favorite_probability is None or identity.underdog_probability is None:
        return None

    fav_idx = identity.favorite_index
    dog_idx = identity.underdog_index
    fav_name = participant_1 if fav_idx == 1 else participant_2
    dog_name = participant_2 if dog_idx == 2 else participant_1

    return StrongUnderdogAssessment(
        event_id=event_id,
        sport=sport,
        event_date=event_date,
        participant_1=participant_1,
        participant_2=participant_2,
        favorite_index=fav_idx,
        underdog_index=dog_idx,
        favorite_name=fav_name,
        underdog_name=dog_name,
        favorite_probability=identity.favorite_probability,
        underdog_probability=identity.underdog_probability,
        draw_probability=identity.draw_probability,
        probability_gap=identity.probability_gap or 0.0,
        status=status,
        supporting_evidence=supporting_evidence,
        contradicting_evidence=contradicting_evidence,
        missing_evidence=missing_evidence,
        source_url=source_url,
        raw_sha256=raw_sha256,
        captured_at=captured_at,
        assessment_version=assessment_version,
    )
