"""Shadow settlement module — post-event grading of frozen shadow runs.

This module provides a repeatable, read-only settlement command that:

1. Reads a frozen prediction run from ``data/reports/shadow/<date>/<run>/``.
2. Fetches a NEW post-event Forebet listing capture for the target date
   (one listing request per sport, same collector route as the forward
   batch; ``workers=1``; ``62s`` pauses between sports; no retries on
   failure — record and move on).
3. Parses settled results from that capture using the existing
   ``settlement.py`` parsers.
4. Grades every selection AND every ``considered_pool[]`` entry against
   final results using the frozen grading contract:

   - ``UNDERDOG_WIN`` only (outright underdog win = ``SUCCESS``).
   - Draw in a draw-capable sport = ``FAILURE``.
   - Favorite wins = ``FAILURE``.
   - Void / no-contest / cancelled / abandoned = ``UNRESOLVED``.
   - Event not found in settled data = ``UNSETTLED``.
   - Match to a prediction entry with a different disposition = recorded.

5. Writes an immutable dated settlement artifact + SHA-256 marker
   (no overwrite of any existing artifact).
6. Never modifies the prediction run.

Artifact layout (per run):

    data/reports/shadow/<target_date>/<run_id>/settlement.json
    data/reports/shadow/<target_date>/<run_id>/settlement.json.sha256

Settlement evidence (raw captures, separate from pre-event captures):

    data/settlement_evidence/<target_date>/settlement_capture_receipt.json
    data/settlement_evidence/<target_date>/<sport>/...

CLI::

    python -m slumdog.shadow_settle --date YYYY-MM-DD --run-id <id> [--root <repo>]
    python -m slumdog.shadow_settle --date YYYY-MM-DD --run-id <id> --offline \\
        --settlement-receipt <path>
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .baseline_analyzer import canonical_json_bytes
from .contracts import SettledEvent
from .settlement import (
    parse_cricket_settled,
    parse_esoccer_settled,
    parse_football_settled,
    parse_html_settled,
    parse_mma_settled,
)
from .shadow_contracts import key_of
from .sports import SPORTS
from .underdog import identify_forebet_underdog


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SettlementError(Exception):
    """Base class for shadow settlement errors."""


# ---------------------------------------------------------------------------
# Grading contract (frozen — do not modify)
# ---------------------------------------------------------------------------

GRADE_SUCCESS = "SUCCESS"
GRADE_FAILURE = "FAILURE"
GRADE_UNRESOLVED = "UNRESOLVED"
GRADE_UNSETTLED = "UNSETTLED"

# Facet retention policy.
#
# The owner's standing instruction (2026-09-07) is that collected data must not
# be discarded — the mission is to predict the underdog, and any tool that gets
# us there should be retained. So facets are persisted by DEFAULT and this list
# is a governance denylist, currently EMPTY.
#
# It was not always empty: `kelly` was withheld here until the owner overruled
# that on 2026-09-07. It is retained now. Retaining the *datum* and using it as
# a *staking input* are different acts — the first is required, the second is
# forbidden by AGENTS.md invariant 10 — so the bar lives where the features are
# built (`dataset.PROHIBITED_KEYS`, `shadow_contracts._FORBIDDEN_RECORD_FIELDS`)
# rather than here at the point of recording.
#
# The mechanism is kept, rather than deleted, so that any future withholding is
# a deliberate recorded act instead of a silent drop: withheld key names are
# written to the artifact under "facets_withheld".
WITHHELD_FACET_KEYS: tuple[str, ...] = ()


def _settled_context(settled: SettledEvent | None) -> dict[str, Any]:
    """Curated post-event display/audit context for one graded row.

    Carries through the SettledEvent fields that used to be dropped entirely
    between parse and artifact: Forebet's own pick, the league, all three board
    prices (including the draw price, which ``parsers._participant_odds`` never
    returned), the published probabilities, period scores and the facets.

    Facets are retained in full by default — see WITHHELD_FACET_KEYS.

    This is metadata, not signal. Odds are display-only (invariant 11) and are
    never model features or gates (invariants 8-9); nothing here may feed an EV,
    de-vigging, Kelly or staking calculation (invariant 10). No grading rule may
    read these values, and the artifact says so in its ``metadata_policy`` block.
    """
    if settled is None:
        return {}
    facets = dict(settled.facets or {})
    kept = {
        key: value
        for key, value in facets.items()
        if key not in WITHHELD_FACET_KEYS
    }
    # Empty under the current policy; recorded so a future withholding is visible.
    withheld = sorted(key for key in facets if key in WITHHELD_FACET_KEYS)
    return {
        "forebet_pick": settled.forebet_pick,
        "league": settled.league or None,
        "league_id": settled.league_id or None,
        "participant_1_id": settled.participant_1_id or None,
        "participant_2_id": settled.participant_2_id or None,
        "odds_1": settled.odds_1,
        "odds_2": settled.odds_2,
        "odds_draw": settled.odds_draw,
        "probability_1": settled.probability_1,
        "probability_2": settled.probability_2,
        "draw_probability": settled.draw_probability,
        "period_scores_1": list(settled.period_scores_1),
        "period_scores_2": list(settled.period_scores_2),
        "source_url": settled.source_url or None,
        "reconstruction": settled.reconstruction,
        "disposition": settled.disposition,
        "facets": kept,
        "facets_withheld": withheld,
    }


def grade_underdog_win(
    *,
    underdog_index: int | None,
    winner_index: int | None,
    disposition: str,
    sport: str,
) -> str:
    """Apply the frozen grading contract for a single event.

    - ``UNDERDOG_WIN`` only: the underdog must win outright.
    - ``winner_index`` is 1 (participant_1), 2 (participant_2), or 0 (draw).
    - ``underdog_index`` is 1 or 2 (the selected underdog participant).
    - ``disposition`` is the settled event's disposition string.

    Returns one of: ``SUCCESS``, ``FAILURE``, ``UNRESOLVED``, ``UNSETTLED``.

    The contract is immutable:

    - ``disposition`` in {``VOID``, ``NO_CONTEST``} → ``UNRESOLVED``
    - ``winner_index == 0`` (draw) → ``FAILURE`` (draw-capable sports)
      or ``UNRESOLVED`` (two-way sports where draw is anomalous)
    - ``winner_index == underdog_index`` → ``SUCCESS``
    - Otherwise → ``FAILURE``

    Ungradable inputs are reported as ``UNRESOLVED``, never as ``FAILURE``:

    - ``winner_index is None`` → ``UNRESOLVED``. There is no result to grade.
      (Previously this fell through both equality tests — ``None == 0`` and
      ``None == underdog_index`` are both False — and silently returned
      ``FAILURE``, manufacturing a decided loss out of a missing result.)
    - ``underdog_index`` not in {1, 2} → ``UNRESOLVED`` *when a winner exists*.
      ``0`` is the draw sentinel, so comparing a real winner against a missing
      identity can never yield ``SUCCESS``; it can only fabricate ``FAILURE``.
      Refusing to grade is the honest outcome. Note the draw branches above run
      first and are unaffected: a draw is a failed ``UNDERDOG_WIN`` regardless
      of which participant was the underdog, so those stay ``FAILURE``.
    """
    disp = (disposition or "SETTLED").upper()
    if disp in ("VOID", "NO_CONTEST", "CANCELLED", "ABANDONED", "ABANDON"):
        return GRADE_UNRESOLVED
    if disp in ("SETTLED_DRAW",):
        return GRADE_FAILURE
    if winner_index is None:
        return GRADE_UNRESOLVED
    if winner_index == 0:
        spec = SPORTS.get(sport)
        if spec and spec.draw_possible:
            return GRADE_FAILURE
        return GRADE_UNRESOLVED
    if underdog_index not in (1, 2):
        return GRADE_UNRESOLVED
    if winner_index == underdog_index:
        return GRADE_SUCCESS
    return GRADE_FAILURE


# ---------------------------------------------------------------------------
# Prediction run loading
# ---------------------------------------------------------------------------


def load_prediction_run(
    target_date: str,
    run_id: str,
    repo_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load and validate a frozen prediction run.

    Returns ``(selections_payload, manifest)``.
    Raises :class:`SettlementError` on any integrity failure.
    """
    run_dir = repo_root / "data" / "reports" / "shadow" / target_date / run_id
    if not run_dir.is_dir():
        raise SettlementError(f"prediction run directory not found: {run_dir}")
    selections_path = run_dir / "shadow_selections.json"
    manifest_path = run_dir / "manifest.json"
    if not selections_path.is_file():
        raise SettlementError(f"shadow_selections.json not found in {run_dir}")
    if not manifest_path.is_file():
        raise SettlementError(f"manifest.json not found in {run_dir}")
    selections = json.loads(selections_path.read_text())
    manifest = json.loads(manifest_path.read_text())
    # Verify run_id consistency
    if selections.get("run_id") != run_id:
        raise SettlementError(
            f"run_id mismatch: selections={selections.get('run_id')!r} "
            f"requested={run_id!r}"
        )
    if manifest.get("run_id") != run_id:
        raise SettlementError(
            f"run_id mismatch: manifest={manifest.get('run_id')!r} "
            f"requested={run_id!r}"
        )
    if selections.get("target_date") != target_date:
        raise SettlementError(
            f"target_date mismatch: selections={selections.get('target_date')!r} "
            f"requested={target_date!r}"
        )
    return selections, manifest


def _build_event_index(
    selections_payload: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Build a composite-key index of all graded-eligible entries.

    The index covers:
    - All ``selections[]`` entries (PRIMARY + TOP3 cohort)
    - All ``considered_pool[]`` entries with
      ``considered_status == ELIGIBLE_RANKED_BEYOND_TOP3``

    Returns ``{composite_key: entry_dict}`` where ``composite_key`` is
    ``"sport:event_id:event_date"``.
    """
    index: dict[str, dict[str, Any]] = {}
    # Selections
    for sel in selections_payload.get("selections", []):
        key = f"{sel['sport']}:{sel['event_id']}:{sel['event_date']}"
        entry = dict(sel)
        entry["_source"] = "selections"
        index[key] = entry
    # Considered pool (ranks 4+)
    for cp in manifest.get("considered_pool", []):
        if cp.get("considered_status") != "ELIGIBLE_RANKED_BEYOND_TOP3":
            continue
        key = f"{cp['sport']}:{cp['event_id']}:{cp['event_date']}"
        if key in index:
            continue  # already in selections
        entry = dict(cp)
        entry["_source"] = "considered_pool"
        index[key] = entry
    return index


# ---------------------------------------------------------------------------
# Post-event capture and settlement parsing
# ---------------------------------------------------------------------------


def fetch_settlement_capture(
    target_date: str,
    repo_root: Path,
    *,
    workers: int = 1,
    pause_seconds: int = 62,
    timeout: int = 45,
    sports: list[str] | None = None,
) -> dict[str, Any]:
    """Fetch post-event Forebet listings for sports on ``target_date``.

    Uses the existing :class:`ForebetCollector` with ``workers=1`` and
    ``62s`` pauses between sports. The capture is written under
    ``data/settlement_evidence/<target_date>/`` (separate from pre-event
    captures).

    By default fetches every non-``current_only`` sport in ``SPORTS``
    (the original, exhaustive behavior). Pass ``sports`` to restrict the
    fetch to a specific subset — e.g. only the sports that actually
    appear in a given prediction run's selections. Fetching sports with
    no predictions to grade wastes the ``pause_seconds`` politeness
    delay for no benefit, so callers that know which sports they need
    (such as the automated D+1 settlement step) should pass ``sports``
    explicitly. Unknown or ``current_only`` sport names in ``sports``
    are silently dropped (never fetched), matching the unrestricted
    default's exclusion of ``current_only`` sports.

    Returns a receipt dict with the same shape as the collector's
    capture receipt.
    """
    from .forebet import source_url, RELAY_BASE
    from .forebet import fetch_with_fallback, validate_capture_body
    from .forebet import relay_get_markdown
    from .forebet import RawCapture
    from dataclasses import asdict
    from datetime import datetime, timezone

    evidence_root = repo_root / "data" / "settlement_evidence" / target_date
    evidence_root.mkdir(parents=True, exist_ok=True)

    captured: list[RawCapture] = []
    failures: list[str] = []
    all_fetchable = [s for s in SPORTS if not SPORTS[s].current_only]
    if sports is None:
        sports_to_fetch = all_fetchable
    else:
        allowed = set(all_fetchable)
        # Preserve SPORTS registry order; drop unknown/current_only names.
        sports_to_fetch = [s for s in all_fetchable if s in set(sports) & allowed]

    for i, sport in enumerate(sports_to_fetch):
        if i > 0:
            time.sleep(pause_seconds)
        spec = SPORTS[sport]
        target = source_url(spec, target_date)
        relay = RELAY_BASE + target
        try:
            if sport == "football":
                try:
                    body = relay_get_markdown(relay, target, timeout=timeout)
                    route = "relay_markdown"
                except Exception:
                    body, route = fetch_with_fallback(
                        relay, target, timeout=timeout, max_retries=1,
                    )
            else:
                body, route = fetch_with_fallback(
                    relay, target, timeout=timeout, max_retries=1,
                )
            validate_capture_body(body, sport, target_date, route)
        except Exception as exc:
            failures.append(f"{sport}:{type(exc).__name__}:{exc}")
            continue
        captured_at = datetime.now(timezone.utc).isoformat()
        digest = hashlib.sha256(body).hexdigest()
        stamp = captured_at.replace(":", "").replace("+00:00", "Z").replace("-", "")
        sport_dir = evidence_root / sport
        sport_dir.mkdir(parents=True, exist_ok=True)
        body_path = sport_dir / f"{stamp}_{digest[:12]}.txt"
        meta_path = sport_dir / f"{stamp}_{digest[:12]}.json"
        body_path.write_bytes(body)
        cap = RawCapture(
            sport=sport,
            target_date=target_date,
            captured_at=captured_at,
            source_url=target,
            relay_url=relay,
            body_format="html",
            sha256=digest,
            bytes=len(body),
            body_path=str(body_path.relative_to(repo_root)),
            metadata_path=str(meta_path.relative_to(repo_root)),
            route=route,
        )
        meta_path.write_text(json.dumps(asdict(cap), indent=2, sort_keys=True))
        captured.append(cap)

    receipt = {
        "target_date": target_date,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "capture_type": "settlement_evidence",
        "captured": [asdict(item) for item in captured],
        "failures": failures,
    }
    receipt_path = evidence_root / "settlement_capture_receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True))
    return receipt


def parse_settled_from_receipt(
    receipt: dict[str, Any],
    repo_root: Path,
) -> list[SettledEvent]:
    """Parse settled rows from a settlement capture receipt.

    Uses the existing sport-specific settlement parsers.
    """
    rows: list[SettledEvent] = []
    target_date = receipt["target_date"]
    for entry in receipt.get("captured", []):
        sport = entry["sport"]
        body_path = repo_root / entry["body_path"]
        if not body_path.is_file():
            continue
        body = body_path.read_bytes()
        try:
            if sport == "football":
                rows.extend(parse_football_settled(body, target_date))
            elif sport == "mma":
                rows.extend(parse_mma_settled(body, target_date))
            elif sport == "cricket":
                rows.extend(parse_cricket_settled(body, target_date))
            elif sport == "esoccer":
                rows.extend(parse_esoccer_settled(body, target_date))
            else:
                rows.extend(parse_html_settled(body, sport, target_date))
        except Exception:
            continue
    return rows


def load_settlement_receipt(
    target_date: str,
    repo_root: Path,
    receipt_path: Path | None = None,
) -> dict[str, Any]:
    """Load an existing settlement capture receipt from disk."""
    if receipt_path is None:
        receipt_path = (
            repo_root / "data" / "settlement_evidence" / target_date
            / "settlement_capture_receipt.json"
        )
    if not receipt_path.is_file():
        raise SettlementError(f"settlement receipt not found: {receipt_path}")
    return json.loads(receipt_path.read_text())


# ---------------------------------------------------------------------------
# Matching and grading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SettlementGrade:
    """One graded entry from the prediction run."""
    sport: str
    event_id: str
    event_date: str
    source: str  # "selections" or "considered_pool"
    considered_status: str
    rank_within_sport_day: int | None
    underdog_index: int | None
    underdog_probability: float | None
    favorite_index: int | None
    favorite_probability: float | None
    grade: str  # SUCCESS, FAILURE, UNRESOLVED, UNSETTLED
    winner_index: int | None
    disposition: str | None
    score_1: float | None
    score_2: float | None
    settled_participant_1: str
    settled_participant_2: str
    match_method: str  # "exact_event_id", "identity_match", "no_match"
    # Where the underdog identity came from: "entry" (carried by the prediction
    # run), "capture_record_tuples" (re-derived from committed pre-event
    # probabilities), or "unavailable" (no identity, so the row cannot grade
    # SUCCESS or FAILURE). Recorded so a reader can tell a graded row from a
    # recovered one without re-deriving anything.
    underdog_index_provenance: str = ""
    # Post-event display/audit context carried through from the matched
    # SettledEvent: Forebet's own pick, league, prices, published probabilities
    # and a curated facet subset. This is METADATA ONLY — see _settled_context
    # and the metadata_policy block in write_settlement_artifact. None of it may
    # be read back as a feature, a gate or a staking input.
    settled_context: dict[str, Any] = field(default_factory=dict)


def _match_settled(
    entry: dict[str, Any],
    settled_by_key: dict[str, SettledEvent],
    settled_by_identity: dict[tuple[str, str, str], SettledEvent],
) -> tuple[SettledEvent | None, str]:
    """Try to match a prediction entry to a settled event.

    Returns ``(settled_event, match_method)``.
    """
    event_id = entry.get("event_id", "")
    event_date = entry.get("event_date", "")
    sport = entry.get("sport", "")
    # Exact event_id match
    composite = f"{sport}:{event_id}:{event_date}"
    if composite in settled_by_key:
        return settled_by_key[composite], "exact_event_id"
    # Identity match (normalized participants + sport + date)
    p1_key = key_of(entry.get("participant_1", ""))
    p2_key = key_of(entry.get("participant_2", ""))
    if p1_key and p2_key:
        ident = (sport, event_date, f"{p1_key}|{p2_key}")
        if ident in settled_by_identity:
            return settled_by_identity[ident], "identity_match"
        # Try reversed
        ident_rev = (sport, event_date, f"{p2_key}|{p1_key}")
        if ident_rev in settled_by_identity:
            return settled_by_identity[ident_rev], "identity_match"
    return None, "no_match"


def _build_settled_indexes(
    settled_rows: list[SettledEvent],
) -> tuple[dict[str, SettledEvent], dict[tuple[str, str, str], SettledEvent]]:
    """Build lookup indexes from settled events."""
    by_key: dict[str, SettledEvent] = {}
    by_identity: dict[tuple[str, str, str], SettledEvent] = {}
    for row in settled_rows:
        composite = f"{row.sport}:{row.event_id}:{row.event_date}"
        by_key[composite] = row
        p1 = key_of(row.participant_1)
        p2 = key_of(row.participant_2)
        if p1 and p2:
            ident = (row.sport, row.event_date, f"{p1}|{p2}")
            by_identity[ident] = row
    return by_key, by_identity


def _entry_participants(entry: dict[str, Any]) -> tuple[str, str]:
    """Extract participant names from a prediction entry.

    Selections carry participant names in the features dict indirectly;
    the manifest's considered_pool entries only have event_id. We look
    at the manifest's full selection data for participant names.
    """
    # For selections, we can find the names in the prediction payload
    # (they're in the features dict or can be reconstructed).
    # For simplicity, we'll store them when we build the index.
    return entry.get("_p1", ""), entry.get("_p2", "")


def _to_float_or_none(value: Any) -> float | None:
    """Coerce a stored probability to float, or None if absent/invalid."""
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _resolve_underdog_identity(
    entry: dict[str, Any],
    prob_lookup: dict[str, tuple[Any, Any, Any]],
    composite_key: str,
    conflicted_keys: frozenset[str] = frozenset(),
) -> tuple[int | None, float | None, int | None, float | None, str]:
    """Resolve the underdog/favorite identity for one graded entry.

    Returns ``(underdog_index, underdog_probability, favorite_index,
    favorite_probability, provenance)`` where ``provenance`` is one of
    ``entry`` (carried by the prediction run), ``capture_record_tuples``
    (re-derived), ``unavailable`` (no identity to re-derive from), or
    ``unavailable_conflicting_capture`` (two capture records disagree about the
    pre-event probabilities, so no single one may decide the grade).

    ``selections[]`` entries carry the identity directly. ``considered_pool[]``
    entries written before the identity-serialisation fix carry only six keys,
    so the identity is re-derived from the **pre-event** probabilities already
    committed in the manifest's ``input_provenance.capture_record_tuples``,
    using the same frozen rule the evaluator uses
    (:func:`~slumdog.underdog.identify_forebet_underdog` — higher probability is
    the favorite, lower is the underdog).

    This never consults a post-event fact and never invents a value. When the
    probabilities are absent, or the identity is genuinely undecidable (equal
    probabilities), the result is ``None`` — never the ``0`` draw sentinel that
    made a rank-4+ ``SUCCESS`` structurally unreachable.
    """
    if entry.get("underdog_index") in (1, 2):
        return (
            entry["underdog_index"],
            _to_float_or_none(entry.get("underdog_probability")),
            entry.get("favorite_index"),
            _to_float_or_none(entry.get("favorite_probability")),
            "entry",
        )
    if composite_key in conflicted_keys:
        # Two capture records for this sport/event/date disagree about the
        # pre-event probabilities. Dict assignment would have kept whichever
        # arrived last, so the identity — and therefore the grade — would have
        # been decided by capture ordering. Refuse instead: the row grades
        # UNRESOLVED and says why.
        return (None, None, None, None, "unavailable_conflicting_capture")
    probs = prob_lookup.get(composite_key)
    if probs is not None:
        identity = identify_forebet_underdog(
            _to_float_or_none(probs[0]),
            _to_float_or_none(probs[1]),
            _to_float_or_none(probs[2]),
        )
        if identity.underdog_index in (1, 2):
            return (
                identity.underdog_index,
                _to_float_or_none(identity.underdog_probability),
                identity.favorite_index,
                _to_float_or_none(identity.favorite_probability),
                "capture_record_tuples",
            )
    return (None, None, None, None, "unavailable")


def grade_all_entries(
    event_index: dict[str, dict[str, Any]],
    settled_rows: list[SettledEvent],
    selections_payload: dict[str, Any],
    manifest: dict[str, Any],
) -> list[SettlementGrade]:
    """Grade every prediction entry against settled results.

    Returns a list of :class:`SettlementGrade` objects.
    """
    by_key, by_identity = _build_settled_indexes(settled_rows)

    # Build a participant-name lookup from the manifest's capture provenance
    # and the selections payload (the selections carry features which include
    # participant identity implicitly via event_id, but not names directly).
    # We reconstruct participant names from the manifest's considered_pool
    # entries which may carry them, or from the capture record tuples.
    # For robustness, we also build an identity index from the selection
    # entries themselves (they carry the sport/event_id/event_date).
    name_lookup: dict[str, tuple[str, str]] = {}
    for sel in selections_payload.get("selections", []):
        key = f"{sel['sport']}:{sel['event_id']}:{sel['event_date']}"
        # The selections don't carry participant names directly.
        # We'll try to find them in the capture record tuples.
        name_lookup[key] = ("", "")

    # Try to enrich from capture_record_tuples in input_provenance
    input_prov = manifest.get("input_provenance", {})
    prob_lookup: dict[str, tuple[Any, Any, Any]] = {}
    # Keys where two capture records disagree about the pre-event probabilities.
    # Last-write-wins would silently decide which one grades the row, so those
    # keys are collected here and the identity is refused for them instead: an
    # ambiguous lookup must never be the thing that turns a row into a SUCCESS
    # or a FAILURE.
    prob_conflicts: set[str] = set()
    for tup in input_prov.get("capture_record_tuples", []):
        if isinstance(tup, (list, tuple)) and len(tup) >= 4:
            sport, eid, edate, p1 = tup[0], tup[1], tup[2], tup[3]
            p2 = tup[4] if len(tup) > 4 else ""
            key = f"{sport}:{eid}:{edate}"
            name_lookup[key] = (str(p1), str(p2))
            # Tuple layout (see shadow_evaluator.capture_record_tuples):
            # 0 sport, 1 event_id, 2 event_date, 3 participant_1,
            # 4 participant_2, 5 probability_1, 6 probability_2,
            # 7 draw_probability, 8 raw_sha256, 9 captured_at, ...
            if len(tup) >= 8:
                values = (tup[5], tup[6], tup[7])
                previous = prob_lookup.get(key)
                if previous is not None and previous != values:
                    prob_conflicts.add(key)
                prob_lookup[key] = values

    grades: list[SettlementGrade] = []
    for composite_key, entry in sorted(event_index.items()):
        sport = entry.get("sport", "")
        event_id = entry.get("event_id", "")
        event_date = entry.get("event_date", "")
        source = entry.get("_source", "")
        considered_status = entry.get("status", entry.get("considered_status", ""))
        rank = entry.get("rank_within_sport_day")

        # Extract underdog/favorite info. A missing identity is NEVER defaulted
        # to ``0`` — that is the draw sentinel, and silently substituting it
        # made a rank-4+ SUCCESS structurally unreachable (see
        # _resolve_underdog_identity).
        (
            underdog_index,
            underdog_prob,
            favorite_index,
            favorite_prob,
            identity_provenance,
        ) = _resolve_underdog_identity(
            entry, prob_lookup, composite_key, frozenset(prob_conflicts)
        )

        # Get participant names for identity matching
        names = name_lookup.get(composite_key, ("", ""))
        entry["_p1"] = names[0]
        entry["_p2"] = names[1]

        settled, match_method = _match_settled(entry, by_key, by_identity)

        if settled is None:
            grades.append(SettlementGrade(
                sport=sport, event_id=event_id, event_date=event_date,
                source=source, considered_status=considered_status,
                rank_within_sport_day=rank,
                underdog_index=underdog_index,
                underdog_probability=underdog_prob,
                favorite_index=favorite_index,
                favorite_probability=favorite_prob,
                grade=GRADE_UNSETTLED,
                winner_index=None, disposition=None,
                score_1=None, score_2=None,
                settled_participant_1="", settled_participant_2="",
                match_method=match_method,
                underdog_index_provenance=identity_provenance,
                settled_context={},  # nothing matched, so nothing to carry
            ))
            continue

        grade = grade_underdog_win(
            underdog_index=underdog_index,
            winner_index=settled.winner_index,
            disposition=settled.disposition,
            sport=sport,
        )
        grades.append(SettlementGrade(
            sport=sport, event_id=event_id, event_date=event_date,
            source=source, considered_status=considered_status,
            rank_within_sport_day=rank,
            underdog_index=underdog_index,
            underdog_probability=underdog_prob,
            favorite_index=favorite_index,
            favorite_probability=favorite_prob,
            grade=grade,
            winner_index=settled.winner_index,
            disposition=settled.disposition,
            score_1=settled.score_1, score_2=settled.score_2,
            settled_participant_1=settled.participant_1,
            settled_participant_2=settled.participant_2,
            match_method=match_method,
            underdog_index_provenance=identity_provenance,
            settled_context=_settled_context(settled),
        ))
    return grades


# ---------------------------------------------------------------------------
# Rolling summary
# ---------------------------------------------------------------------------

# ``per_rank`` keeps every individual rank because it is the audit view, but on a
# 500-event sport-day almost every rank holds exactly one row — a per-rank hit
# rate there is n=1 noise dressed up as a statistic (09-05 produced 507 such
# cells). These bands are the reportable view: they pool enough rows to be
# readable while still showing ``n`` per cell, so a reader can see which bands
# clear the n>=30 bar and which do not.
RANK_BANDS: tuple[tuple[str, int, int | None], ...] = (
    ("1", 1, 1),
    ("2-3", 2, 3),
    ("4-10", 4, 10),
    ("11-25", 11, 25),
    ("26-50", 26, 50),
    ("51+", 51, None),
)


def _rank_band(rank: Any) -> str:
    """Band label for a rank; ``"none"`` when the rank is missing or invalid."""
    if not isinstance(rank, int) or rank < 1:
        return "none"
    for label, low, high in RANK_BANDS:
        if rank >= low and (high is None or rank <= high):
            return label
    return RANK_BANDS[-1][0]


def _rank_sort_key(item: tuple[str, Any]) -> tuple[int, int]:
    """Numeric ordering for per-rank keys, with the ``none`` bucket last."""
    key = item[0]
    return (0, int(key)) if key.isdigit() else (1, 0)


def compute_rolling_summary(
    grades: list[SettlementGrade],
) -> dict[str, Any]:
    """Compute per-rank, per-band, and cohort summary statistics.

    Emits ``n`` per cell; no claims from cells with ``n < 30``.
    """
    # Overall
    total = len(grades)
    settled_grades = [g for g in grades if g.grade != GRADE_UNSETTLED]
    resolved = [g for g in settled_grades if g.grade != GRADE_UNRESOLVED]

    def _hit_rate(items: list[SettlementGrade]) -> dict[str, Any]:
        n = len(items)
        successes = sum(1 for g in items if g.grade == GRADE_SUCCESS)
        return {
            "n": n,
            "successes": successes,
            "failures": sum(1 for g in items if g.grade == GRADE_FAILURE),
            "unresolved": sum(1 for g in items if g.grade == GRADE_UNRESOLVED),
            "unsettled": sum(1 for g in items if g.grade == GRADE_UNSETTLED),
            "hit_rate": successes / n if n > 0 else None,
        }

    # Primary (rank 1) hit rate
    primary = [g for g in grades if g.rank_within_sport_day == 1 and g.source == "selections"]
    # Cohort (rank 2-3) hit rate
    cohort = [g for g in grades if g.rank_within_sport_day in (2, 3) and g.source == "selections"]
    # Top-3 combined
    top3 = primary + cohort
    # Ranks 4+ (considered_pool)
    r4plus = [g for g in grades if g.source == "considered_pool"]

    # Per-rank — audit view. Every individual rank is kept, including the n=1
    # cells; see per_rank_band below for the pooled, reportable view.
    per_rank: dict[str, list[SettlementGrade]] = {}
    for g in grades:
        rank_key = str(g.rank_within_sport_day or "none")
        per_rank.setdefault(rank_key, []).append(g)
    per_rank_summary = {
        k: _hit_rate(v)
        for k, v in sorted(per_rank.items(), key=_rank_sort_key)
    }

    # Per-rank band — reportable view over the same rows.
    per_band: dict[str, list[SettlementGrade]] = {}
    for g in grades:
        per_band.setdefault(_rank_band(g.rank_within_sport_day), []).append(g)
    band_order = [label for label, _, _ in RANK_BANDS] + ["none"]
    per_rank_band_summary = {
        label: _hit_rate(per_band[label])
        for label in band_order
        if label in per_band
    }

    # By underdog-probability band
    bands = [
        ("<0.10", 0.0, 0.10),
        ("0.10-0.15", 0.10, 0.15),
        ("0.15-0.20", 0.15, 0.20),
        ("0.20-0.25", 0.20, 0.25),
        ("0.25-0.30", 0.25, 0.30),
        ("0.30-0.35", 0.30, 0.35),
        ("0.35-0.40", 0.35, 0.40),
        ("0.40-0.45", 0.40, 0.45),
        ("0.45-0.50", 0.45, 0.50),
    ]
    by_band: dict[str, dict[str, Any]] = {}
    for label, lo, hi in bands:
        items = [
            g for g in resolved
            if g.underdog_probability is not None
            and lo <= g.underdog_probability < hi
        ]
        by_band[label] = _hit_rate(items)

    # Per-sport
    per_sport: dict[str, list[SettlementGrade]] = {}
    for g in grades:
        per_sport.setdefault(g.sport, []).append(g)
    per_sport_summary = {k: _hit_rate(v) for k, v in sorted(per_sport.items())}

    # Cohort cumulative: any of top-3 succeeds?
    cohort_by_sport_day: dict[tuple[str, str], list[SettlementGrade]] = {}
    for g in top3:
        key = (g.sport, g.event_date)
        cohort_by_sport_day.setdefault(key, []).append(g)
    cohort_any_success = 0
    cohort_total_days = len(cohort_by_sport_day)
    for _key, day_grades in cohort_by_sport_day.items():
        if any(g.grade == GRADE_SUCCESS for g in day_grades):
            cohort_any_success += 1

    return {
        "total_entries": total,
        "total_settled": len(settled_grades),
        "total_resolved": len(resolved),
        "primary_hit_rate": _hit_rate(primary),
        "cohort_top3_hit_rate": _hit_rate(cohort),
        "top3_combined_hit_rate": _hit_rate(top3),
        "ranks_4plus_hit_rate": _hit_rate(r4plus),
        "per_rank": per_rank_summary,
        "per_rank_band": per_rank_band_summary,
        "by_underdog_probability_band": by_band,
        "per_sport": per_sport_summary,
        "cohort_cumulative": {
            "sport_days_with_top3": cohort_total_days,
            "sport_days_with_any_success": cohort_any_success,
            "cohort_day_success_rate": (
                cohort_any_success / cohort_total_days
                if cohort_total_days > 0 else None
            ),
        },
    }


# ---------------------------------------------------------------------------
# Artifact writing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SettlementResult:
    target_date: str
    run_id: str
    settled_at: str
    settlement_artifact_path: str
    settlement_marker_path: str
    settlement_receipt_path: str
    settlement_artifact_sha256: str
    grades: list[SettlementGrade]
    summary: dict[str, Any]
    settlement_capture_receipt: dict[str, Any]


def write_settlement_artifact(
    *,
    target_date: str,
    run_id: str,
    grades: list[SettlementGrade],
    summary: dict[str, Any],
    settlement_receipt: dict[str, Any],
    repo_root: Path,
    settled_at: str | None = None,
) -> SettlementResult:
    """Write the immutable settlement artifact + SHA-256 marker.

    The artifact is written to the prediction run's directory alongside
    ``shadow_selections.json`` and ``manifest.json``. Refuses to
    overwrite an existing settlement artifact.
    """
    run_dir = repo_root / "data" / "reports" / "shadow" / target_date / run_id
    artifact_path = run_dir / "settlement.json"
    marker_path = run_dir / "settlement.json.sha256"
    if artifact_path.exists():
        raise SettlementError(
            f"refusing to overwrite existing settlement artifact: {artifact_path}"
        )
    if marker_path.exists():
        raise SettlementError(
            f"refusing to overwrite existing settlement marker: {marker_path}"
        )

    settled_at = settled_at or _dt.datetime.now(_dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    # Build the settlement payload
    grade_dicts = []
    for g in grades:
        grade_dicts.append({
            "sport": g.sport,
            "event_id": g.event_id,
            "event_date": g.event_date,
            "source": g.source,
            "considered_status": g.considered_status,
            "rank_within_sport_day": g.rank_within_sport_day,
            "underdog_index": g.underdog_index,
            "underdog_probability": g.underdog_probability,
            "favorite_index": g.favorite_index,
            "favorite_probability": g.favorite_probability,
            "grade": g.grade,
            "winner_index": g.winner_index,
            "disposition": g.disposition,
            "score_1": g.score_1,
            "score_2": g.score_2,
            "settled_participant_1": g.settled_participant_1,
            "settled_participant_2": g.settled_participant_2,
            "match_method": g.match_method,
            "underdog_index_provenance": g.underdog_index_provenance,
            # Post-event display/audit context (Forebet pick, league, all three
            # board prices, published probabilities, period scores, curated
            # facets). Metadata only — see metadata_policy below.
            "settled_context": g.settled_context,
        })

    settlement_payload = {
        "settlement_schema_version": "shadow_settlement",
        "target_date": target_date,
        "run_id": run_id,
        "settled_at": settled_at,
        "grading_contract": {
            "target": "UNDERDOG_WIN",
            "draw_is_failure": True,
            "void_is_unresolved": True,
            "not_found_is_unsettled": True,
            # An entry whose underdog identity cannot be resolved to 1 or 2 has
            # no basis for a SUCCESS/FAILURE decision and is reported
            # UNRESOLVED. It is never compared against the 0 draw sentinel.
            "missing_underdog_identity_is_unresolved": True,
            "missing_winner_is_unresolved": True,
        },
        # Governance note bound to the data this artifact now carries, so the
        # policy travels with the numbers instead of living only in AGENTS.md.
        "metadata_policy": {
            "settled_context_is_metadata_only": True,
            "odds_used_in_grading": False,
            "odds_used_as_model_features": False,
            "odds_gate_candidates": False,
            "missing_odds_lower_confidence": False,
            # Retention policy: collected data is KEPT (owner directive
            # 2026-09-07 — "any tool that gets us there should be retained").
            # Kelly fractions are recorded as inert metadata and barred from the
            # feature layer, not deleted. Recording a number and staking on it
            # are different acts; invariant 10 forbids the second.
            "facets_retained_by_default": True,
            "withheld_facet_keys": list(WITHHELD_FACET_KEYS),
            "kelly_retained_as_inert_metadata": True,
            "kelly_used_for_ev_devig_or_staking": False,
            "invariants": {
                "odds_are_display_only": "AGENTS.md invariant 11",
                "odds_never_a_feature_or_gate": "AGENTS.md invariants 8-9",
                "no_ev_devig_kelly_or_staking": "AGENTS.md invariant 10",
            },
            "note": (
                "settled_context records what the post-event page said, so a "
                "reader can audit a grade without re-fetching. It is not "
                "signal: no grading rule, feature, gate or staking calculation "
                "may read it, and a missing price must never change a grade or "
                "a confidence."
            ),
        },
        "grades": sorted(
            grade_dicts,
            key=lambda d: (d["sport"], d["event_date"], d["event_id"],
                           d.get("rank_within_sport_day") or 999),
        ),
        "summary": summary,
        "settlement_capture_receipt": settlement_receipt,
    }
    payload_bytes = canonical_json_bytes(settlement_payload)

    # Atomic write
    import tempfile
    fd, tmp = tempfile.mkstemp(
        prefix="settlement.", suffix=".json.tmp", dir=str(run_dir),
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload_bytes)
        os.replace(tmp, artifact_path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

    # SHA-256 marker
    artifact_sha = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    marker_content = f"{artifact_sha}  settlement.json\n"
    marker_path.write_text(marker_content)

    # Find the settlement receipt path
    evidence_dir = repo_root / "data" / "settlement_evidence" / target_date
    receipt_path = evidence_dir / "settlement_capture_receipt.json"

    return SettlementResult(
        target_date=target_date,
        run_id=run_id,
        settled_at=settled_at,
        settlement_artifact_path=str(artifact_path),
        settlement_marker_path=str(marker_path),
        settlement_receipt_path=str(receipt_path),
        settlement_artifact_sha256=artifact_sha,
        grades=grades,
        summary=summary,
        settlement_capture_receipt=settlement_receipt,
    )


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


def settle_run(
    *,
    target_date: str,
    run_id: str,
    repo_root: str | Path,
    offline: bool = False,
    settlement_receipt_path: str | Path | None = None,
    workers: int = 1,
    pause_seconds: int = 62,
    timeout: int = 45,
    settled_at: str | None = None,
    sports: list[str] | None = None,
) -> SettlementResult:
    """Settle a frozen shadow prediction run.

    Parameters:
        target_date: The prediction run's target date (YYYY-MM-DD).
        run_id: The prediction run's 16-hex-char run_id.
        repo_root: Repository root directory.
        offline: If True, skip the network fetch and use an existing
            settlement capture receipt.
        settlement_receipt_path: Explicit path to the settlement
            capture receipt (used with ``--offline``).
        workers: Collector workers (must be 1 for politeness).
        pause_seconds: Pause between sport fetches (62s default).
        timeout: Per-request timeout in seconds.
        settled_at: Override the settlement timestamp.
        sports: Optional subset of sports to fetch during the network
            settlement capture (ignored when ``offline`` is True, since
            an offline run reuses a pre-existing receipt). Defaults to
            every non-``current_only`` sport when omitted, matching
            :func:`fetch_settlement_capture`'s default. Grading itself
            is unaffected: only sports present in the prediction run's
            own entries are ever graded regardless of this parameter.

    Returns:
        A :class:`SettlementResult` with artifact paths and checksums.
    """
    repo_root = Path(repo_root).resolve()

    # Step 1: Load the prediction run
    selections_payload, manifest = load_prediction_run(
        target_date, run_id, repo_root,
    )

    # Step 2: Check no existing settlement
    run_dir = repo_root / "data" / "reports" / "shadow" / target_date / run_id
    if (run_dir / "settlement.json").exists():
        raise SettlementError(
            f"settlement already exists for {target_date}/{run_id}; "
            "refusing to overwrite"
        )

    # Step 3: Fetch or load settlement evidence
    if offline:
        settlement_receipt = load_settlement_receipt(
            target_date, repo_root,
            Path(settlement_receipt_path) if settlement_receipt_path else None,
        )
    else:
        settlement_receipt = fetch_settlement_capture(
            target_date, repo_root,
            workers=workers, pause_seconds=pause_seconds, timeout=timeout,
            sports=sports,
        )

    # Step 4: Parse settled results
    settled_rows = parse_settled_from_receipt(settlement_receipt, repo_root)

    # Step 5: Build event index from prediction
    event_index = _build_event_index(selections_payload, manifest)

    # Step 6: Grade all entries
    grades = grade_all_entries(
        event_index, settled_rows, selections_payload, manifest,
    )

    # Step 7: Compute summary
    summary = compute_rolling_summary(grades)

    # Step 8: Write immutable artifact
    return write_settlement_artifact(
        target_date=target_date,
        run_id=run_id,
        grades=grades,
        summary=summary,
        settlement_receipt=settlement_receipt,
        repo_root=repo_root,
        settled_at=settled_at,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m slumdog.shadow_settle",
        description="Shadow settlement: grade a frozen prediction run "
                    "against post-event Forebet results.",
    )
    p.add_argument("--date", required=True, help="Target date YYYY-MM-DD")
    p.add_argument("--run-id", required=True, help="Prediction run_id (16 hex)")
    p.add_argument("--root", default=Path("."), type=Path,
                   help="Repository root (default: cwd)")
    p.add_argument("--offline", action="store_true",
                   help="Skip network fetch; use existing settlement receipt")
    p.add_argument("--settlement-receipt", type=Path, default=None,
                   help="Explicit settlement receipt path (with --offline)")
    p.add_argument("--pause-seconds", type=int, default=62,
                   help="Pause between sport fetches (default: 62)")
    p.add_argument("--timeout", type=int, default=45,
                   help="Per-request timeout seconds (default: 45)")
    p.add_argument("--sports", default=None,
                   help="Comma-separated subset of sports to fetch during "
                        "settlement capture (default: all non-current_only "
                        "sports). Grading is unaffected by this flag; it "
                        "only limits which post-event listings are fetched.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    sports = None
    if args.sports:
        sports = [s.strip() for s in args.sports.split(",") if s.strip()]
    try:
        result = settle_run(
            target_date=args.date,
            run_id=args.run_id,
            repo_root=args.root,
            offline=args.offline,
            settlement_receipt_path=args.settlement_receipt,
            pause_seconds=args.pause_seconds,
            timeout=args.timeout,
            sports=sports,
        )
    except SettlementError as e:
        print(f"SETTLEMENT_FAILED: {e}", file=sys.stderr)
        return 2
    print(json.dumps({
        "target_date": result.target_date,
        "run_id": result.run_id,
        "settled_at": result.settled_at,
        "settlement_artifact_path": result.settlement_artifact_path,
        "settlement_artifact_sha256": result.settlement_artifact_sha256,
        "total_entries": result.summary["total_entries"],
        "total_settled": result.summary["total_settled"],
        "total_resolved": result.summary["total_resolved"],
        "primary_hit_rate": result.summary["primary_hit_rate"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
