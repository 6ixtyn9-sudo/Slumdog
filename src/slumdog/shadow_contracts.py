"""Milestone 7 — Shadow-evaluator typed contracts (lowest layer).

This module owns the typed boundary objects used by the capture loader and
the shadow evaluator. It is intentionally minimal and has no imports from
other Slumdog modules to keep the dependency direction clean:

    shadow_evaluator  ─┐
                       ├──> shadow_contracts
    capture_loader    ─┘

No production source file imports from this module. ``contracts.py``
itself is not modified.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .contracts import EventSnapshot
from .sports import SPORTS


# Outcome / odds vocabulary that must NEVER appear in PreEventRecord
_FORBIDDEN_RECORD_FIELDS = frozenset({
    "score_1", "score_2", "winner_index", "disposition",
    "period_scores_1", "period_scores_2", "extra_time_score", "penalty_score",
    "odds_1", "odds_2", "price", "overround", "implied_probability",
    "live_score", "result", "result_text",
})


def key_of(name: str) -> str:
    """Canonical case-folded alphanumeric key for participant comparison."""
    return "".join(ch for ch in str(name or "").casefold() if ch.isalnum())


@dataclass(frozen=True)
class PreEventRecord:
    """Price-free, pre-event record for shadow evaluation.

    Field set is minimal: identity, feature construction, ranking, and
    provenance only. No outcome / score / odds / disposition field
    exists by construction. ``__post_init__`` enforces the field-name
    boundary defensively.
    """

    event_id: str
    sport: str
    event_date: str
    participant_1: str
    participant_2: str
    probability_1: float | None
    probability_2: float | None
    draw_probability: float | None
    source_url: str
    raw_sha256: str
    captured_at: str
    body_path: str
    route: str
    capture_receipt_path: str = ""
    sidecar_path: str = ""
    facets: dict[str, Any] = field(default_factory=dict)
    facet_timing: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("PreEventRecord: event_id required")
        if self.sport not in SPORTS:
            raise ValueError(f"PreEventRecord: unknown sport {self.sport!r}")
        if not self.participant_1 or not self.participant_2:
            raise ValueError("PreEventRecord: both participants required")
        if key_of(self.participant_1) == key_of(self.participant_2):
            raise ValueError("PreEventRecord: self-pair participants")
        if not self.captured_at:
            raise ValueError("PreEventRecord: captured_at required")
        if not self.event_date:
            raise ValueError("PreEventRecord: event_date required")
        for forbidden in _FORBIDDEN_RECORD_FIELDS:
            if forbidden in self.__dataclass_fields__:
                raise AssertionError(
                    f"PreEventRecord must not declare {forbidden!r}"
                )

    @classmethod
    def from_event_snapshot(
        cls,
        snap: EventSnapshot,
        *,
        body_path: str = "",
        capture_receipt_path: str = "",
        sidecar_path: str = "",
    ) -> "PreEventRecord":
        """Copy only approved fields from an EventSnapshot.

        ``facets`` is filtered to those with explicit ``facet_timing``
        keys; values without a timing key are dropped to keep the record
        strictly pre-event by construction.
        """
        if not isinstance(snap, EventSnapshot):
            raise TypeError(
                f"PreEventRecord.from_event_snapshot requires EventSnapshot, "
                f"got {type(snap).__name__}"
            )
        timing = {
            k: (v.value if hasattr(v, "value") else str(v))
            for k, v in snap.facet_timing.items()
        }
        facets = {k: snap.facets[k] for k in snap.facets if k in timing}
        return cls(
            event_id=snap.event_id,
            sport=snap.sport,
            event_date=snap.event_date,
            participant_1=snap.participant_1,
            participant_2=snap.participant_2,
            probability_1=snap.probability_1,
            probability_2=snap.probability_2,
            draw_probability=snap.draw_probability,
            source_url=snap.source_url,
            raw_sha256=snap.raw_sha256,
            captured_at=snap.captured_at,
            body_path=body_path or snap.raw_sha256,
            route="snapshot",
            capture_receipt_path=capture_receipt_path,
            sidecar_path=sidecar_path,
            facets=facets,
            facet_timing=timing,
        )
