"""Milestone 7 — Shadow pick evaluator (price-free, R2-frozen, R1-ranking).

Public surface:

- :func:`evaluate_from_disk` — read-only orchestration that loads the
  capture receipt and prior history from disk, runs the frozen R2
  eligibility rule and the R1 ranking comparator, and writes the
  immutable payload + manifest under
  ``data/reports/shadow/<target_date>/<run_id>/``. Use this for the
  CLI path.
- :func:`main` — thin argument parser around :func:`evaluate_from_disk`.
  Supports ``python -m slumdog.shadow_evaluator --help``.

Scope and non-authorizations are identical to the declaration; see
``config/shadow_evaluator_v1.json``.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .baseline_analyzer import (
    canonical_json_bytes,
    is_r2_eligible,
    r1_sort_key as _baseline_r1_sort_key,
)
from .capture_loader import (
    CaptureLoadResult,
    load_capture_records,
)
from .dataset import build_pre_event_features
from .history_loader import HistoryLoadResult, load_valid_history, DEFAULT_MAX_INTERIM_BYTES
from .shadow_contracts import PreEventRecord, key_of
from .underdog import identify_forebet_underdog


# ---------------------------------------------------------------------------
# Frozen rule source
# ---------------------------------------------------------------------------

FROZEN_BASELINE_CONFIG_PATH = "config/research_baselines_v1.json"
FROZEN_BASELINE_CONFIG_SHA256 = (
    "666dabe7ea21e11867cf4816f4c2edcd771247646c6c9d7726c22611cda700a1"
)
FROZEN_R2_KEY = "R2_CONSERVATIVE_FIXED_RULE"
FROZEN_R2_PATH = (
    f"{FROZEN_BASELINE_CONFIG_PATH}:rules.{FROZEN_R2_KEY}"
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ShadowEvaluatorError(Exception):
    """Base class. All M7 integrity errors derive from this."""


# ---------------------------------------------------------------------------
# Declaration / frozen-config verification
# ---------------------------------------------------------------------------


def _canonical_sha256(obj: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(obj)).hexdigest()


def _parse_utc(ts: str) -> _dt.datetime:
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    parsed = _dt.datetime.fromisoformat(s)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp not tz-aware: {ts!r}")
    return parsed.astimezone(_dt.timezone.utc)


def safe_cutoff_utc(target_date: str, *, offset_hours: int = 24) -> _dt.datetime:
    try:
        d = _dt.datetime.strptime(target_date, "%Y-%m-%d")
    except ValueError as e:
        raise ValueError(f"target_date not ISO YYYY-MM-DD: {target_date!r}") from e
    return _dt.datetime(d.year, d.month, d.day, tzinfo=_dt.timezone.utc) - _dt.timedelta(hours=offset_hours)


def load_frozen_baseline_config(root: Path) -> dict[str, Any]:
    """Verify the 6B frozen config: SHA-256 and the exact R2 rule shape."""
    path = Path(root) / FROZEN_BASELINE_CONFIG_PATH
    if not path.is_file():
        raise ShadowEvaluatorError(f"frozen baseline config not found: {path}")
    obj = json.loads(path.read_text())
    actual = _canonical_sha256(obj)
    if actual != FROZEN_BASELINE_CONFIG_SHA256:
        raise ShadowEvaluatorError(
            f"frozen baseline config SHA-256 mismatch: actual={actual}"
        )
    rule = obj.get("rules", {}).get(FROZEN_R2_KEY)
    if not isinstance(rule, dict):
        raise ShadowEvaluatorError(
            f"frozen baseline config missing rules.{FROZEN_R2_KEY}"
        )
    expected = {
        ("underdog_prior_games", "gte", 5),
        ("favorite_prior_games", "gte", 5),
        ("h2h_prior_games", "gte", 1),
        ("forebet_probability_gap", "lte", 0.2),
    }
    actual_set = {
        (e.get("feature"), e.get("op"), e.get("value"))
        for e in rule.get("eligibility", [])
    }
    if actual_set != expected:
        raise ShadowEvaluatorError(
            f"frozen R2 eligibility drift: actual={sorted(actual_set)}"
        )
    return obj


def load_shadow_declaration(path: str | Path) -> dict[str, Any]:
    """Verify the shadow declaration's hard-fail-closed fields."""
    p = Path(path)
    if not p.is_file():
        raise ShadowEvaluatorError(f"declaration not found: {p}")
    obj = json.loads(p.read_text())
    if not isinstance(obj, dict):
        raise ShadowEvaluatorError("declaration must be a JSON object")
    auth = obj.get("authorizations", {})
    if auth.get("shadow_evaluation_authorized") is not True:
        raise ShadowEvaluatorError(
            "authorizations.shadow_evaluation_authorized must be True"
        )
    for gate in (
        "production_authorized",
        "shortlist_policy_authorized",
        "training_authorized",
        "threshold_optimization_authorized",
    ):
        if auth.get(gate) is not False:
            raise ShadowEvaluatorError(
                f"authorizations.{gate} must be False (fail closed)"
            )
    anti = obj.get("anti_tuning", {})
    if anti.get("result_driven_amendments") != "prohibited":
        raise ShadowEvaluatorError("anti_tuning.result_driven_amendments must be 'prohibited'")
    if anti.get("tuning_on_observed_results") != "prohibited":
        raise ShadowEvaluatorError("anti_tuning.tuning_on_observed_results must be 'prohibited'")
    if anti.get("rule_source_frozen") != FROZEN_R2_KEY:
        raise ShadowEvaluatorError(f"anti_tuning.rule_source_frozen must equal {FROZEN_R2_KEY!r}")
    if anti.get("rule_source_frozen_config_sha256") != FROZEN_BASELINE_CONFIG_SHA256:
        raise ShadowEvaluatorError("anti_tuning.rule_source_frozen_config_sha256 mismatch")
    timing = obj.get("timing_safety", {})
    if timing.get("safe_cutoff_offset_hours_utc") != 24:
        raise ShadowEvaluatorError("timing_safety.safe_cutoff_offset_hours_utc must be 24 (frozen)")
    if timing.get("require_captured_at_present") is not True:
        raise ShadowEvaluatorError("timing_safety.require_captured_at_present must be True")
    if timing.get("require_decision_committed_at_present") is not True:
        raise ShadowEvaluatorError("timing_safety.require_decision_committed_at_present must be True")
    if timing.get("require_both_timestamps_tz_aware_utc") is not True:
        raise ShadowEvaluatorError("timing_safety.require_both_timestamps_tz_aware_utc must be True")
    if timing.get("fail_closed_status_on_violation") != "PRE_EVENT_TIMING_UNVERIFIED":
        raise ShadowEvaluatorError("timing_safety.fail_closed_status_on_violation must be PRE_EVENT_TIMING_UNVERIFIED")
    if timing.get("margin_frozen_in_declaration") is not True:
        raise ShadowEvaluatorError("timing_safety.margin_frozen_in_declaration must be True")
    rule = obj.get("rule", {})
    if rule.get("name") != FROZEN_R2_KEY:
        raise ShadowEvaluatorError(f"rule.name must equal {FROZEN_R2_KEY!r}")
    if rule.get("policy_candidate") is not False:
        raise ShadowEvaluatorError("rule.policy_candidate must be False")
    if rule.get("quota_forced") is not False:
        raise ShadowEvaluatorError("rule.quota_forced must be False")
    if rule.get("rank_policy") != "R1_ALWAYS_RANK_COMPARATOR":
        raise ShadowEvaluatorError("rule.rank_policy must be R1_ALWAYS_RANK_COMPARATOR")
    cohort = obj.get("cohort_policy", {})
    if cohort.get("primary_selection_per_sport_day") != 1:
        raise ShadowEvaluatorError("cohort_policy.primary_selection_per_sport_day must be 1")
    if cohort.get("top3_cohort_per_sport_day") != 2:
        raise ShadowEvaluatorError("cohort_policy.top3_cohort_per_sport_day must be 2")
    if cohort.get("no_global_cap") is not True:
        raise ShadowEvaluatorError("cohort_policy.no_global_cap must be True")
    durability = obj.get("durability", {})
    if durability.get("status") != "LOCAL_CODESPACE_ONLY_NOT_BACKED_UP":
        raise ShadowEvaluatorError(
            "durability.status must be LOCAL_CODESPACE_ONLY_NOT_BACKED_UP"
        )
    return obj


# ---------------------------------------------------------------------------
# Identity validation
# ---------------------------------------------------------------------------


def validate_event_identity(record: PreEventRecord) -> tuple[bool, str | None]:
    """Reject: empty participants, self-pairs, unknown sport, missing or
    out-of-range probability, non-zero draw on two-way sport, or
    identity-ineligible (e.g. equal participant probabilities).
    """
    from .sports import SPORTS
    if record.sport not in SPORTS:
        return False, "UNKNOWN_SPORT"
    if not record.participant_1 or not record.participant_2:
        return False, "MISSING_PARTICIPANTS"
    if key_of(record.participant_1) == key_of(record.participant_2):
        return False, "SELF_PAIR"
    spec = SPORTS[record.sport]
    if not getattr(spec, "draw_possible", False) and record.draw_probability not in (None, 0.0):
        return False, "DRAW_PROBABILITY_FOR_TWO_WAY_SPORT"
    p1, p2 = record.probability_1, record.probability_2
    if p1 is None or p2 is None:
        return False, "MISSING_PROBABILITY"
    if not (0.0 <= p1 <= 1.0) or not (0.0 <= p2 <= 1.0):
        return False, "OUT_OF_RANGE_PROBABILITY"
    identity = identify_forebet_underdog(
        probability_1=p1, probability_2=p2, draw_probability=record.draw_probability,
    )
    if not identity.eligible:
        return False, f"IDENTITY_INELIGIBLE:{identity.ineligibility_reason}"
    return True, None


# ---------------------------------------------------------------------------
# Per-record evaluation
# ---------------------------------------------------------------------------


def _evaluate_record(
    record: PreEventRecord,
    history: HistoryLoadResult,
) -> dict[str, Any]:
    """Run identity + features + R2 eligibility + R1 rank-key on one
    record. Returns a dict with all information the run-loop needs to
    assign a final status."""
    ok, reason = validate_event_identity(record)
    if not ok:
        return {
            "record": record, "eligible": False,
            "status": reason or "IDENTITY_INELIGIBLE",
            "features": {}, "missingness": {}, "identity": None,
            "rank_key": (),
        }
    identity = identify_forebet_underdog(
        probability_1=record.probability_1, probability_2=record.probability_2,
        draw_probability=record.draw_probability,
    )
    features, missingness = build_pre_event_features(
        sport=record.sport, event_date=record.event_date,
        participant_1=record.participant_1, participant_2=record.participant_2,
        identity=identity, history=history.history_index,
    )
    if not is_r2_eligible(features):
        return {
            "record": record, "eligible": False,
            "status": "FEATURE_INCOMPLETE_OR_R2_INELIGIBLE",
            "features": features, "missingness": missingness,
            "identity": identity, "rank_key": (),
        }
    rank_key = _baseline_r1_sort_key({"event_id": record.event_id, "features": features})
    return {
        "record": record, "eligible": True,
        "status": "ELIGIBLE_PENDING_RANK",
        "features": features, "missingness": missingness,
        "identity": identity, "rank_key": rank_key,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ShadowRunResult:
    run_id: str
    target_date: str
    decision_committed_at: str
    run_status: str  # SHADOW_RUN_BLOCKED | SHADOW_NO_SELECTION | SHADOW_SELECTIONS_EMITTED
    artifact_dir: str
    manifest: dict[str, Any]
    payload: dict[str, Any]


def _now_utc() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _now_utc_iso(dt: _dt.datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _timing_classify(
    evaluations: list[dict[str, Any]],
    *,
    safe_cutoff: _dt.datetime,
) -> tuple[list[dict[str, Any]], int]:
    """Split evaluations into (timing_ok, timing_rejected_count)."""
    ok: list[dict[str, Any]] = []
    rejected = 0
    for ev in evaluations:
        record = ev["record"]
        try:
            cap_at = _parse_utc(record.captured_at)
        except ValueError:
            rejected += 1
            continue
        if cap_at > safe_cutoff:
            rejected += 1
            continue
        ok.append(ev)
    return ok, rejected


def _snapshot_dedup(
    evaluations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
    """Across-capture composite-key dedup with strict separation
    of decision content from source provenance (owner integrity
    review).

    The composite key is ``(sport, event_id, event_date)``. Within
    one composite-key group, the **decision fingerprint** is the
    price-free decision content. Two observations with the same
    composite key and the same decision fingerprint are
    **decision-equivalent duplicate observations**: one is admitted
    as the canonical decision record, the others are counted as
    exact decision duplicates. All source observations are
    preserved as provenance observations attached to the admitted
    record.

    Two observations with the same composite key but different
    decision fingerprints are a **genuine decision conflict**: the
    group is excluded from decision evaluation, all members are
    counted in conflict accounting, and no arbitrary winner is
    chosen. All provenance observations are still preserved.

    The decision fingerprint deliberately contains ONLY fields that
    must match for the price-free decision to be identical:

    - ``sport, event_date, event_id``
    - ``participant_1, participant_2`` (the parser has already
      normalized the strings; the captured form is what reaches
      the decision)
    - ``probability_1, probability_2, draw_probability``

    The decision fingerprint deliberately EXCLUDES provenance
    fields whose values necessarily differ between two observations
    of the same event that differ only in odds or display metadata:

    - ``source_url`` (may change across captures)
    - ``raw_sha256`` (the body bytes differ if odds differ)
    - ``sidecar_sha256`` (mirror of body hash for verification)
    - ``captured_at`` (the capture timestamp changes)
    - ``route`` (direct / relay / etc.)
    - ``body_path, sidecar_path, capture_receipt_path``

    These are PROVENANCE, not decision content. Including them in
    the fingerprint would cause the runner to mis-classify
    two observations of the same event with different odds as
    "conflicting" and drop the entire group from decision
    evaluation, which violates the permanent rule that odds
    cannot affect the price-free decision.

    Returns ``(kept_evaluations, accounting, conflict_fingerprints)``
    where:

    - ``accounting`` is a dict with the keys
      ``unique_decision_records_admitted``,
      ``exact_decision_duplicate_groups``,
      ``exact_decision_duplicate_extra_rows``,
      ``conflict_groups``,
      ``conflicting_rows``.
    - ``conflict_fingerprints`` is a list of dicts (one per
      conflicting composite-key group), each containing the
      composite key and the list of distinct decision fingerprints
      observed. This is published in the manifest for forensic
      review; it is NOT in the decision_digest.

    The returned ``kept_evaluations`` are REFERENCES to the
    original eval dicts (not copies). The per-sport-day ranking
    loop later mutates these same dicts to set
    ``considered_status`` and ``rank_within_sport_day``; the
    ``considered_pool`` manifest view is built from the same
    ``raw_evals`` list and therefore sees those mutations.
    Provenance observations are stashed on a side-table
    ``_provenance_observations`` attribute of each kept eval so
    they are visible in the manifest without polluting the eval
    dict's main key surface.
    """
    from .shadow_contracts import PreEventRecord as _PER
    def _decision_fingerprint(r: _PER) -> tuple:
        # Price-free decision content only. See docstring.
        return (
            r.sport, r.event_id, r.event_date,
            r.participant_1, r.participant_2,
            r.probability_1, r.probability_2, r.draw_probability,
        )
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for ev in evaluations:
        key = (ev["record"].sport, ev["record"].event_id, ev["record"].event_date)
        groups.setdefault(key, []).append(ev)
    kept: list[dict[str, Any]] = []
    accounting = {
        "unique_decision_records_admitted": 0,
        "exact_decision_duplicate_groups": 0,
        "exact_decision_duplicate_extra_rows": 0,
        "conflict_groups": 0,
        "conflicting_rows": 0,
    }
    # Per-conflict-group fingerprint trail. Each entry is a dict
    # with the composite key plus the list of distinct decision
    # fingerprints observed. This is for forensic review of the
    # manifest; it is NOT included in the decision_digest.
    conflict_fingerprints: list[dict[str, Any]] = []
    for key, members in groups.items():
        # Group by decision fingerprint. If more than one distinct
        # fingerprint appears under one composite key, that is a
        # genuine decision conflict and the entire group is excluded
        # from decision evaluation. If only one fingerprint appears,
        # the first observation is admitted as the canonical record
        # and any extras are decision-equivalent duplicate
        # observations (counted but not in the decision path).
        by_fingerprint: dict[tuple, list[dict[str, Any]]] = {}
        for ev in members:
            fp = _decision_fingerprint(ev["record"])
            by_fingerprint.setdefault(fp, []).append(ev)
        if len(by_fingerprint) > 1:
            # Genuine decision conflict: two or more distinct
            # decision fingerprints under one composite key.
            # No winner is chosen; the entire group is excluded
            # from decision evaluation. The conflict is fully
            # recorded in the accounting below AND the distinct
            # fingerprints are preserved in
            # ``conflict_fingerprints`` (published in the
            # manifest). The individual observations' provenance
            # is already published in ``capture_provenance`` /
            # ``input_provenance`` (per sidecar/body digests and
            # the capture_record_tuples committed to by
            # ``input_digest``), so no source observation is
            # silently lost.
            conflict_count = sum(len(v) for v in by_fingerprint.values())
            accounting["conflict_groups"] += 1
            accounting["conflicting_rows"] += conflict_count
            # Record the distinct fingerprints (no odds, no
            # provenance) so the conflict is auditable from the
            # manifest alone.
            fingerprints_serialized = []
            for fp, evs in by_fingerprint.items():
                fingerprints_serialized.append({
                    "sport": fp[0],
                    "event_id": fp[1],
                    "event_date": fp[2],
                    "participant_1": fp[3],
                    "participant_2": fp[4],
                    "probability_1": fp[5],
                    "probability_2": fp[6],
                    "draw_probability": fp[7],
                    "observation_count": len(evs),
                })
            conflict_fingerprints.append({
                "sport": key[0], "event_id": key[1], "event_date": key[2],
                "decision_fingerprints": sorted(
                    fingerprints_serialized,
                    key=lambda d: (
                        d["participant_1"], d["participant_2"],
                        d["probability_1"], d["probability_2"],
                    ),
                ),
            })
            continue
        # Single fingerprint under this composite key: admit one
        # canonical record (reference to the original eval dict),
        # count any extras as decision-equivalent duplicates, and
        # preserve all observations' provenance on the canonical
        # record's ``_provenance_observations`` list.
        members_in_bucket = by_fingerprint[next(iter(by_fingerprint))]
        canonical = members_in_bucket[0]
        provenance_observations: list[dict[str, Any]] = []
        for ev in members_in_bucket:
            r = ev["record"]
            provenance_observations.append({
                "capture_receipt_path": r.capture_receipt_path or "",
                "sidecar_path": r.sidecar_path or "",
                "body_path": r.body_path or "",
                "raw_sha256": r.raw_sha256 or "",
                "captured_at": r.captured_at or "",
                "source_url": r.source_url or "",
                "route": r.route or "",
            })
        # Stash provenance observations and observation count on
        # the canonical eval dict itself (underscore-prefixed to
        # avoid collision with the public eval interface). The
        # downstream ranking loop mutates this same dict to set
        # ``considered_status`` and ``rank_within_sport_day``;
        # those mutations are visible to the
        # ``considered_pool_dicts`` builder which walks the
        # original ``raw_evals`` list.
        canonical["_provenance_observations"] = provenance_observations
        canonical["_observation_count"] = len(members_in_bucket)
        kept.append(canonical)
        accounting["unique_decision_records_admitted"] += 1
        extra = len(members_in_bucket) - 1
        if extra > 0:
            accounting["exact_decision_duplicate_groups"] += 1
            accounting["exact_decision_duplicate_extra_rows"] += extra
    return kept, accounting, conflict_fingerprints


def _emit_run(
    *,
    target_date: str,
    capture_result: CaptureLoadResult,
    history_result: HistoryLoadResult,
    declaration: dict[str, Any],
    repo_root: Path,
    decision_clock: _dt.datetime | None = None,
) -> ShadowRunResult:
    decision_dt = decision_clock or _now_utc()
    decision_committed_at = _now_utc_iso(decision_dt)
    safe_cutoff = safe_cutoff_utc(target_date)
    if decision_dt > safe_cutoff:
        return _blocked_run(
            target_date=target_date, decision_committed_at=decision_committed_at,
            declaration=declaration, capture_result=capture_result,
            history_result=history_result, safe_cutoff=safe_cutoff,
            block_reason="DECISION_COMMITTED_AT_AFTER_SAFE_CUTOFF",
            repo_root=repo_root,
        )

    # Per-record evaluation
    raw_evals = [_evaluate_record(r, history_result) for r in capture_result.records]
    # Initialize every raw_eval's considered_status. The final
    # considered_status is set by one of: timing gate, identity
    # check, feature check, dedup, or the per-sport-day rank loop.
    # The considered_pool manifest view walks raw_evals at the end
    # and reads the final considered_status / rank_within_sport_day.
    for ev in raw_evals:
        ev.setdefault("considered_status", "PENDING")
        ev.setdefault("rank_within_sport_day", None)
    timing_ok, timing_rejected = _timing_classify(raw_evals, safe_cutoff=safe_cutoff)
    # Mark timing-rejected records so they show up correctly in
    # considered_pool.
    timing_rejected_evals = [ev for ev in raw_evals if ev not in timing_ok]
    for ev in timing_rejected_evals:
        ev["considered_status"] = "TIMING_REJECTED"
        ev["rank_within_sport_day"] = None

    # Identity and feature splits
    identity_ineligible: list[dict[str, Any]] = []
    feature_incomplete: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    for ev in timing_ok:
        if not ev["eligible"]:
            if ev["status"] == "FEATURE_INCOMPLETE_OR_R2_INELIGIBLE":
                feature_incomplete.append(ev)
                ev["considered_status"] = "FEATURE_INCOMPLETE_OR_R2_INELIGIBLE"
            else:
                identity_ineligible.append(ev)
                ev["considered_status"] = ev["status"] or "IDENTITY_INELIGIBLE"
            ev["rank_within_sport_day"] = None
        else:
            eligible.append(ev)

    # Cross-record snapshot dedup (executed on eligible only, since
    # duplicate of an ineligible record doesn't matter for cohort).
    # Returns ``(eligible_kept, dedup_accounting, conflict_fps)``
    # where ``dedup_accounting`` has the per-composite-key
    # decision duplicate / conflict accounting and
    # ``conflict_fps`` records the distinct decision fingerprints
    # for each conflicting composite-key group.
    eligible_kept, dedup_accounting, conflict_fingerprints = _snapshot_dedup(eligible)
    # Mark the extras (decision-equivalent duplicates) so they
    # show up correctly in considered_pool. The canonical record
    # for each group is in ``eligible_kept`` and will receive its
    # final considered_status (PRIMARY / COHORT / R4+) from the
    # per-sport-day rank loop below. The extras in the same
    # fingerprint bucket are still in the original ``eligible``
    # list but not in ``eligible_kept``; mark them now.
    kept_ids = {id(ev) for ev in eligible_kept}
    # Re-walk the dedup to mark extras: group by composite key
    # and fingerprint, mark all-but-first in each fingerprint
    # bucket as EXACT_DECISION_DUPLICATE_OBSERVATION.
    from .shadow_contracts import PreEventRecord as _PER
    def _decision_fingerprint(r: _PER) -> tuple:
        return (
            r.sport, r.event_id, r.event_date,
            r.participant_1, r.participant_2,
            r.probability_1, r.probability_2, r.draw_probability,
        )
    extras_by_fp: dict[tuple[str, str, str], set[int]] = {}
    for ev in eligible:
        if id(ev) in kept_ids:
            continue
        r = ev["record"]
        fp = _decision_fingerprint(r)
        extras_by_fp.setdefault(fp, set()).add(id(ev))
    # An extra is "not kept" because it's a decision-equivalent
    # duplicate OR a member of a conflict group. We can tell
    # which by checking whether the composite key has a conflict
    # recorded in conflict_fingerprints.
    conflict_keys: set[tuple[str, str, str]] = {
        (c["sport"], c["event_id"], c["event_date"])
        for c in conflict_fingerprints
    }
    for fp, extra_ids in extras_by_fp.items():
        composite_key = (fp[0], fp[1], fp[2])
        if composite_key in conflict_keys:
            # Conflict group: every member is
            # DECISION_CONFLICT_EXCLUDED, not just the extras.
            # We need to mark all members of this composite key
            # (kept + extras) as DECISION_CONFLICT_EXCLUDED.
            # The canonical is the one that was admitted; it
            # needs to be re-marked to DECISION_CONFLICT_EXCLUDED
            # so the per-sport-day rank loop does not classify it.
            for ev in eligible:
                r = ev["record"]
                if (r.sport, r.event_id, r.event_date) == composite_key:
                    ev["considered_status"] = "DECISION_CONFLICT_EXCLUDED"
                    ev["rank_within_sport_day"] = None
            continue
        # Decision-equivalent duplicate extras: mark them.
        for ev in eligible:
            if id(ev) in extra_ids:
                ev["considered_status"] = "EXACT_DECISION_DUPLICATE_OBSERVATION"
                ev["rank_within_sport_day"] = None
    # If any canonical of a conflict group is still in
    # eligible_kept, the per-sport-day rank loop below will run on
    # it and overwrite considered_status. We must remove conflict
    # group members from eligible_kept.
    eligible_kept = [
        ev for ev in eligible_kept
        if ev.get("considered_status") != "DECISION_CONFLICT_EXCLUDED"
    ]

    # Per-sport-day ranking
    by_sport_day: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for ev in eligible_kept:
        sd = (ev["record"].sport, ev["record"].event_date)
        by_sport_day.setdefault(sd, []).append(ev)
    for sd in by_sport_day:
        by_sport_day[sd].sort(key=lambda e: e["rank_key"])

    all_sport_days: set[tuple[str, str]] = set()
    for r in capture_result.records:
        all_sport_days.add((r.sport, r.event_date))
    for sd in by_sport_day:
        all_sport_days.add(sd)

    selections: list[dict[str, Any]] = []
    primary_count = 0
    cohort_count = 0
    r4plus_count = 0
    sport_day_summary: list[dict[str, Any]] = []
    for sd in sorted(all_sport_days):
        sport, _date = sd
        evs = by_sport_day.get(sd, [])
        if not evs:
            sport_day_summary.append({
                "sport": sport, "event_date": _date,
                "status": "SHADOW_NO_SELECTION",
                "eligible_count": 0, "primary_event_id": None,
                "cohort_event_ids": [], "eligible_r4_plus_event_ids": [],
            })
            continue
        primary_event_id = None
        cohort_ids: list[str] = []
        r4plus_ids: list[str] = []
        for rank_idx, ev in enumerate(evs, start=1):
            record = ev["record"]
            identity = ev["identity"]
            # Schema boundary (owner item 2): ``selections[]`` holds only
            # ranks 1-3 (primary + cohort). Rank 4+ goes to
            # ``considered_pool[]`` with ``considered_status =
            # ELIGIBLE_RANKED_BEYOND_TOP3`` and is never copied into
            # ``selections[]``. The accounting counter and per-sport-day
            # tracking still record rank-4+ for transparency.
            if rank_idx == 1:
                status = "PRIMARY_SHADOW_SELECTION"
                primary_count += 1
                primary_event_id = record.event_id
            elif rank_idx <= 3:
                status = "TOP3_EVALUATION_COHORT"
                cohort_count += 1
                cohort_ids.append(record.event_id)
            else:
                status = "ELIGIBLE_RANKED_BEYOND_TOP3"
                r4plus_count += 1
                r4plus_ids.append(record.event_id)
            # Per-ev ``considered_status`` and ``rank_within_sport_day``
            # are mirrored onto the eval so the ``considered_pool``
            # digest and the manifest pool both expose them — even for
            # rank-4+ records which do NOT enter ``selections[]``.
            ev["considered_status"] = status
            ev["rank_within_sport_day"] = rank_idx
            if rank_idx > 3:
                # Rank-4+ MUST NOT appear in ``selections[]``;
                # accounted for in ``considered_pool[]`` only.
                continue
            selections.append({
                "sport": sport, "event_date": _date, "event_id": record.event_id,
                "rank_within_sport_day": rank_idx, "status": status,
                "favorite_index": identity.favorite_index,
                "underdog_index": identity.underdog_index,
                "favorite_probability": identity.favorite_probability,
                "underdog_probability": identity.underdog_probability,
                "probability_gap": identity.probability_gap,
                "draw_probability": identity.draw_probability,
                "features": {k: ev["features"][k] for k in sorted(ev["features"])},
                "missingness": {k: ev["missingness"][k] for k in sorted(ev["missingness"])},
                "raw_sha256": record.raw_sha256,
                "source_url": record.source_url,
                "captured_at": record.captured_at,
                "body_path": record.body_path,
                "sidecar_path": record.sidecar_path,
                "capture_receipt_path": record.capture_receipt_path,
                "route": record.route,
                "rule_source": FROZEN_R2_PATH,
                "run_id": "",
            })
        sport_day_summary.append({
            "sport": sport, "event_date": _date,
            "status": "SHADOW_RULE_QUALIFIED" if primary_event_id else "SHADOW_NO_SELECTION",
            "eligible_count": len(evs),
            "primary_event_id": primary_event_id,
            "cohort_event_ids": cohort_ids,
            "eligible_r4_plus_event_ids": r4plus_ids,
        })

    # Decision-level accounting (mutually exclusive staged equation):
    #
    #   total_in
    #     = timing_rejected
    #     + identity_ineligible
    #     + feature_incomplete_or_r2_ineligible
    #     + unique_decision_records_admitted      (one per non-conflict composite key)
    #     + exact_decision_duplicate_extra_rows   (extras collapsed into the canonical record)
    #     + conflicting_rows                      (all observations in conflict groups)
    #
    # ``unique_decision_records_admitted`` is the number of
    # composite-key groups that have exactly one decision fingerprint
    # (the same groups that flow into ranking as
    # primary + cohort + rank-4+).
    # ``exact_decision_duplicate_extra_rows`` is the number of
    # additional observations in single-fingerprint groups that
    # were collapsed into the canonical record; they are not in
    # the decision path but they ARE counted as a separate
    # mutually exclusive bucket so the staged equation balances.
    # ``conflicting_rows`` is the number of observations that were
    # excluded because the composite key had multiple decision
    # fingerprints (no arbitrary winner chosen; the conflict is
    # recorded in the accounting).
    total_in = len(capture_result.records)
    assert (
        timing_rejected + len(identity_ineligible) + len(feature_incomplete)
        + dedup_accounting["unique_decision_records_admitted"]
        + dedup_accounting["exact_decision_duplicate_extra_rows"]
        + dedup_accounting["conflicting_rows"]
    ) == total_in, "decision-level staging imbalance"

    decision_accounting = {
        "decision_total_records": total_in,
        "timing_rejected": timing_rejected,
        "identity_ineligible": len(identity_ineligible),
        "feature_incomplete_or_r2_ineligible": len(feature_incomplete),
        "primary_selected": primary_count,
        "top3_cohort_selected": cohort_count,
        "eligible_ranked_beyond_top3": r4plus_count,
        # Decision-equivalent duplicate observations (same composite
        # key, same decision fingerprint, different provenance).
        # The provenance observations are preserved on the canonical
        # decision record and published in the manifest; the
        # accounting below counts the GROUPS and EXTRA ROWS so the
        # staging equation remains balanced.
        "exact_decision_duplicate_groups": dedup_accounting["exact_decision_duplicate_groups"],
        "exact_decision_duplicate_extra_rows": dedup_accounting["exact_decision_duplicate_extra_rows"],
        "unique_decision_records_admitted": dedup_accounting["unique_decision_records_admitted"],
        # Genuine decision conflicts: same composite key, different
        # decision fingerprint. Excluded from decision evaluation.
        # All conflicting observations are preserved in
        # ``capture_provenance`` / ``input_provenance`` for forensic
        # review; no source observation is silently lost.
        "conflict_groups": dedup_accounting["conflict_groups"],
        "conflicting_rows": dedup_accounting["conflicting_rows"],
    }

    # Compute input_digest (canonical over capture, history, declaration)
    declaration_sha = _canonical_sha256(declaration)
    # Canonicalize capture provenance: per-sidecar, per-body exact
    # SHA-256, plus the parsed record tuples.
    sidecar_digests = {
        path: sha
        for path, sha in capture_result.raw_input_sha256.items()
        if path.endswith(".json")
    }
    body_digests = {
        path: sha
        for path, sha in capture_result.raw_input_sha256.items()
        if path.endswith(".txt")
    }
    # Capture accounting (verifies balance equation)
    capture_accounting_digest = _canonical_sha256(capture_result.capture_accounting)
    snapshot_accounting_digest = _canonical_sha256(capture_result.snapshot_accounting)
    # History accounting summary
    history_accounting_digest = _canonical_sha256({
        k: history_result.manifest_section.get(k)
        for k in (
            "history_decoded_rows", "history_schema_invalid",
            "history_schema_valid_candidate_rows",
            "history_unique_valid_rows", "history_exact_duplicate_rows",
            "history_conflict_count_rows", "history_admitted_rows",
            "history_excluded_counts",
        )
    })
    capture_record_tuples = sorted(
        (r.sport, r.event_id, r.event_date, r.participant_1, r.participant_2,
         str(r.probability_1), str(r.probability_2), str(r.draw_probability),
         r.raw_sha256, r.captured_at, r.body_path, r.source_url)
        for r in capture_result.records
    )
    input_digest_payload = {
        "version": "shadow_evaluator_v1",
        "declaration_sha256": declaration_sha,
        "frozen_baseline_config_sha256": FROZEN_BASELINE_CONFIG_SHA256,
        "target_date": target_date,
        "safe_cutoff_utc": _now_utc_iso(safe_cutoff),
        "capture_receipt_sha256": capture_result.receipt_sha256,
        "sidecar_digests": sidecar_digests,
        "raw_body_digests": body_digests,
        "capture_accounting_digest": capture_accounting_digest,
        "snapshot_accounting_digest": snapshot_accounting_digest,
        "history_input_sha256": history_result.manifest_section.get("history_input_sha256", {}),
        "history_accounting_digest": history_accounting_digest,
        "history_feature_contract": history_result.manifest_section.get("history_feature_contract"),
        "capture_record_tuples": capture_record_tuples,
    }
    input_digest = _canonical_sha256(input_digest_payload)

    # decision_digest: complete ranked/considered pool + selections +
    # decision accounting + rule/version identity. The pool is the
    # list of all evaluated records with their eligibility outcome,
    # so the digest is sufficient to reproduce the selection. The
    # pool includes ineligible records and rank-4+ records (with
    # ``considered_status = ELIGIBLE_RANKED_BEYOND_TOP3``) so
    # decision_digest commits to the FULL set of decision
    # candidates — not only the chosen ones.
    #
    # Owner item 3: ``decision_digest`` MUST NOT depend on
    # per-snapshot fields (raw_sha256, source_url, captured_at,
    # body_path, sidecar_path, capture_receipt_path, route) or
    # on odds. Two snapshots of the same matches differing only
    # in odds MUST produce the same decision_digest. Those
    # per-snapshot fields ARE retained in the manifest (and
    # committed to by ``input_digest``) for source provenance —
    # they are simply excluded from this digest so it reflects
    # only the decision-relevant content. Likewise, odds and
    # outcomes are never on the selection record at all.
    pool_for_digest = sorted(
        (
            ev["record"].sport, ev["record"].event_id, ev["record"].event_date,
            ev.get("considered_status") or ev.get("status"),
            bool(ev.get("eligible")),
            ev.get("rank_within_sport_day"),
        )
        for ev in raw_evals
    )
    # Manifest-pool as list of dicts (one per evaluated record). This
    # mirrors the digest tuple form 1:1 but is friendly to test
    # inspection. The same set of records is committed to in the
    # digest via ``pool_for_digest``.
    considered_pool_dicts = sorted(
        (
            {
                "sport": ev["record"].sport,
                "event_id": ev["record"].event_id,
                "event_date": ev["record"].event_date,
                "considered_status": ev.get("considered_status") or ev.get("status"),
                "eligible": bool(ev.get("eligible")),
                "rank_within_sport_day": ev.get("rank_within_sport_day"),
            }
            for ev in raw_evals
        ),
        key=lambda d: (d["sport"], d["event_date"], d["event_id"]),
    )
    # Per-snapshot source-provenance fields are excluded from the
    # decision digest (they are committed to by input_digest). See
    # owner item 3: odds-only differences must produce the same
    # decision_digest.
    _DIGEST_EXCLUDED_FROM_SELECTION = frozenset({
        "run_id", "raw_sha256", "source_url", "captured_at",
        "body_path", "sidecar_path", "capture_receipt_path", "route",
    })
    selections_for_digest = [
        {k: v for k, v in s.items() if k not in _DIGEST_EXCLUDED_FROM_SELECTION}
        for s in selections
    ]
    decision_digest_payload = {
        "version": "shadow_evaluator_v1",
        "rule_name": FROZEN_R2_KEY,
        "frozen_baseline_config_sha256": FROZEN_BASELINE_CONFIG_SHA256,
        "considered_pool": pool_for_digest,
        "selections": sorted(selections_for_digest, key=lambda x: (x["sport"], x["event_date"], x["event_id"], x["rank_within_sport_day"])),
        "decision_accounting": decision_accounting,
    }
    decision_digest = _canonical_sha256(decision_digest_payload)

    run_id_payload = {
        "version": "shadow_evaluator_v1",
        "input_digest": input_digest,
        "decision_digest": decision_digest,
        "decision_committed_at": decision_committed_at,
    }
    run_id = hashlib.sha256(canonical_json_bytes(run_id_payload)).hexdigest()[:16]

    for s in selections:
        s["run_id"] = run_id

    # Atomic write: payload first, then manifest last
    artifact_root_rel = Path(declaration.get("artifact_path", {}).get("root", "data/reports/shadow"))
    artifact_root = artifact_root_rel if artifact_root_rel.is_absolute() else (repo_root / artifact_root_rel)
    artifact_dir = artifact_root / target_date / run_id
    if artifact_dir.exists():
        raise ShadowEvaluatorError(
            f"refusing to overwrite existing artifact directory: {artifact_dir}"
        )
    artifact_dir.mkdir(parents=True, exist_ok=False)

    run_status = "SHADOW_SELECTIONS_EMITTED" if primary_count > 0 else "SHADOW_NO_SELECTION"
    payload = {
        "run_id": run_id,
        "target_date": target_date,
        "decision_committed_at": decision_committed_at,
        "input_digest": input_digest,
        "decision_digest": decision_digest,
        "rule_source": FROZEN_R2_PATH,
        "safe_cutoff_utc": _now_utc_iso(safe_cutoff),
        "run_status": run_status,
        "selections": sorted(selections, key=lambda x: (x["sport"], x["event_date"], x["event_id"], x["rank_within_sport_day"])),
        "sport_day_summary": sport_day_summary,
        "decision_accounting": decision_accounting,
    }
    payload_bytes = canonical_json_bytes(payload)
    fd_p, tmp_p = tempfile.mkstemp(prefix="shadow_selections.", suffix=".json.tmp", dir=str(artifact_dir))
    try:
        with os.fdopen(fd_p, "wb") as f:
            f.write(payload_bytes)
        payload_path = artifact_dir / "shadow_selections.json"
        os.replace(tmp_p, payload_path)
    except Exception:
        if os.path.exists(tmp_p):
            os.unlink(tmp_p)
        raise
    payload_sha256 = hashlib.sha256(payload_path.read_bytes()).hexdigest()

    manifest = {
        "run_id": run_id,
        "target_date": target_date,
        "decision_committed_at": decision_committed_at,
        "input_digest": input_digest,
        "decision_digest": decision_digest,
        "payload_file_sha256": payload_sha256,
        "rule_source": FROZEN_R2_PATH,
        "frozen_baseline_config_sha256": FROZEN_BASELINE_CONFIG_SHA256,
        "declaration_sha256": declaration_sha,
        "safe_cutoff_utc": _now_utc_iso(safe_cutoff),
        "run_status": run_status,
        "decision_accounting": decision_accounting,
        "sport_day_summary": sport_day_summary,
        "input_provenance": input_digest_payload,
        "input_digest": input_digest,
        "decision_digest": decision_digest,
        "decision_provenance": decision_digest_payload,
        # Manifest-level view of the considered pool with explicit
        # ``considered_status`` per record (PRIMARY_SHADOW_SELECTION /
        # TOP3_EVALUATION_COHORT / ELIGIBLE_RANKED_BEYOND_TOP3 /
        # ineligible). The digest payload above (and ``decision_digest``)
        # commits to the same records in tuple form. This top-level
        # ``considered_pool`` field is for downstream inspection and is
        # NOT in the digest. Owner item 2: rank-4+ records are
        # present here with ``considered_status =
        # ELIGIBLE_RANKED_BEYOND_TOP3`` and MUST NOT appear in
        # ``selections[]``.
        "considered_pool": considered_pool_dicts,
        # Per-conflict-group fingerprint trail (manifest only; NOT
        # in the decision_digest). For every composite-key group
        # classified as a genuine decision conflict (i.e. multiple
        # distinct decision fingerprints under one composite key),
        # we record the distinct fingerprints. This makes the
        # conflict auditable from the manifest alone, without
        # touching the decision_digest.
        "decision_conflicts": conflict_fingerprints,
        "capture_provenance": {
            "receipt_path": capture_result.receipt_path,
            "receipt_sha256": capture_result.receipt_sha256,
            "receipt_bytes": capture_result.receipt_bytes,
            "raw_input_paths": capture_result.raw_input_paths,
            "raw_input_sha256": capture_result.raw_input_sha256,
            "capture_accounting": capture_result.capture_accounting,
            "snapshot_accounting": capture_result.snapshot_accounting,
        },
        "history_provenance": history_result.manifest_section,
        "durability_policy": declaration.get("durability", {}),
        "anti_tuning": declaration.get("anti_tuning", {}),
        "version": "shadow_evaluator_v1",
    }
    fd_m, tmp_m = tempfile.mkstemp(prefix="manifest.", suffix=".json.tmp", dir=str(artifact_dir))
    try:
        with os.fdopen(fd_m, "wb") as f:
            f.write(canonical_json_bytes(manifest))
        manifest_path = artifact_dir / "manifest.json"
        os.replace(tmp_m, manifest_path)
    except Exception:
        if os.path.exists(tmp_m):
            os.unlink(tmp_m)
        raise

    return ShadowRunResult(
        run_id=run_id,
        target_date=target_date,
        decision_committed_at=decision_committed_at,
        run_status=run_status,
        artifact_dir=str(artifact_dir),
        manifest=manifest,
        payload=payload,
    )


def _blocked_run(
    *,
    target_date: str,
    decision_committed_at: str,
    declaration: dict[str, Any],
    capture_result: CaptureLoadResult | None,
    history_result: HistoryLoadResult | None,
    safe_cutoff: _dt.datetime,
    block_reason: str,
    repo_root: Path,
) -> ShadowRunResult:
    """Write a SHADOW_RUN_BLOCKED failure receipt. The failure receipt
    is stored under a separate path segment so it cannot be confused
    with a completed decision artifact."""
    artifact_root_rel = Path(declaration.get("artifact_path", {}).get("root", "data/reports/shadow"))
    artifact_root = artifact_root_rel if artifact_root_rel.is_absolute() else (repo_root / artifact_root_rel)
    base = artifact_root / target_date / "BLOCKED"
    if not base.exists():
        base.mkdir(parents=True, exist_ok=True)
    # Use the block reason + decision timestamp for the file name. If
    # a file with that exact name already exists (same second + same
    # reason), append a 12-char dedup suffix to prevent silent
    # overwrite. No force / overwrite / replace behavior.
    stamp = decision_committed_at.replace(":", "").replace("Z", "")
    # Sanitize block_reason: replace path separators and colons that
    # would break the filename. The full reason is still recorded in
    # the receipt body.
    safe_reason = block_reason.replace("/", "_").replace(":", "_").replace(" ", "_")
    base_name = f"BLOCKED_{stamp}_{safe_reason}"
    receipt_path = base / f"{base_name}.json"
    if receipt_path.exists():
        import uuid
        suffix = uuid.uuid4().hex[:12]
        receipt_path = base / f"{base_name}__{suffix}.json"
    body = {
        "target_date": target_date,
        "decision_committed_at": decision_committed_at,
        "safe_cutoff_utc": _now_utc_iso(safe_cutoff),
        "run_status": "SHADOW_RUN_BLOCKED",
        "block_reason": block_reason,
        "declaration_sha256": _canonical_sha256(declaration),
        "frozen_baseline_config_sha256": FROZEN_BASELINE_CONFIG_SHA256,
    }
    if capture_result is not None:
        body["capture_provenance"] = {
            "receipt_path": capture_result.receipt_path,
            "receipt_sha256": capture_result.receipt_sha256,
            "capture_accounting": capture_result.capture_accounting,
            "snapshot_accounting": capture_result.snapshot_accounting,
        }
    if history_result is not None:
        body["history_provenance"] = history_result.manifest_section
    receipt_path.write_bytes(canonical_json_bytes(body))
    return ShadowRunResult(
        run_id="BLOCKED",
        target_date=target_date,
        decision_committed_at=decision_committed_at,
        run_status="SHADOW_RUN_BLOCKED",
        artifact_dir=str(receipt_path),
        manifest=body,
        payload=body,
    )


def evaluate_from_disk(
    *,
    target_date: str,
    capture_receipt_path: str | Path,
    declaration_path: str | Path,
    repo_root: str | Path,
    history_paths: list[str | Path] | None = None,
    decision_clock: _dt.datetime | None = None,
    history_max_interim_bytes: int | None = None,
) -> ShadowRunResult:
    """Top-level disk-to-artifact orchestration. Reads the frozen
    declaration, the 6B frozen config, the capture receipt, and the
    prior history. Writes the immutable payload + manifest, or a
    SHADOW_RUN_BLOCKED failure receipt if any integrity gate fails.
    """
    repo_root = Path(repo_root).resolve()
    declaration = load_shadow_declaration(declaration_path)
    load_frozen_baseline_config(repo_root)
    safe_cutoff = safe_cutoff_utc(target_date)
    try:
        capture_result = load_capture_records(
            target_date=target_date,
            capture_receipt_path=capture_receipt_path,
            repo_root=repo_root,
        )
    except Exception as e:
        return _blocked_run(
            target_date=target_date,
            decision_committed_at=_now_utc_iso(decision_clock or _now_utc()),
            declaration=declaration, capture_result=None,
            history_result=None, safe_cutoff=safe_cutoff,
            block_reason=f"CAPTURE_LOAD_FAILED:{type(e).__name__}:{e}",
            repo_root=repo_root,
        )
    try:
        history_result = load_valid_history(
            target_date=target_date, repo_root=repo_root,
            history_paths=history_paths,
            max_interim_bytes=(
                history_max_interim_bytes
                if history_max_interim_bytes is not None
                else DEFAULT_MAX_INTERIM_BYTES
            ),
        )
    except Exception as e:
        return _blocked_run(
            target_date=target_date,
            decision_committed_at=_now_utc_iso(decision_clock or _now_utc()),
            declaration=declaration, capture_result=capture_result,
            history_result=None, safe_cutoff=safe_cutoff,
            block_reason=f"HISTORY_LOAD_FAILED:{type(e).__name__}:{e}",
            repo_root=repo_root,
        )
    return _emit_run(
        target_date=target_date,
        capture_result=capture_result,
        history_result=history_result,
        declaration=declaration,
        repo_root=repo_root,
        decision_clock=decision_clock,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m slumdog.shadow_evaluator",
        description="Milestone 7 shadow pick evaluator (price-free, R2-frozen).",
    )
    p.add_argument("--date", required=True, help="Target date YYYY-MM-DD")
    p.add_argument("--capture-receipt", required=True, type=Path,
                   help="Path to data/reports/capture_<date>.json")
    p.add_argument("--history", action="append", type=Path, default=[],
                   help="Optional explicit history paths (repeatable)")
    p.add_argument("--config", required=True, type=Path,
                   help="Path to config/shadow_evaluator_v1.json")
    p.add_argument("--root", default=Path("."), type=Path,
                   help="Repository root (default: current working directory)")
    p.add_argument("--history-max-interim-bytes", type=int, default=None,
                   help="Override the in-memory bound for non-gz interim "
                        "ledgers (bytes). Default: 256 MiB. Use a smaller "
                        "value in tests; the loader never silently disables "
                        "the cap.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    try:
        result = evaluate_from_disk(
            target_date=args.date,
            capture_receipt_path=args.capture_receipt,
            declaration_path=args.config,
            repo_root=args.root,
            history_paths=args.history or None,
            history_max_interim_bytes=args.history_max_interim_bytes,
        )
    except ShadowEvaluatorError as e:
        print(f"SHADOW_RUN_BLOCKED: {e}", file=sys.stderr)
        return 2
    print(json.dumps({
        "run_id": result.run_id,
        "target_date": result.target_date,
        "run_status": result.run_status,
        "decision_committed_at": result.decision_committed_at,
        "artifact_dir": result.artifact_dir,
        "input_digest": result.manifest.get("input_digest"),
        "decision_digest": result.manifest.get("decision_digest"),
        "payload_file_sha256": result.manifest.get("payload_file_sha256"),
        "durability_status": result.manifest.get("durability_policy", {}).get("status"),
    }, indent=2, sort_keys=True))
    # A SHADOW_RUN_BLOCKED outcome is non-zero so callers can detect
    # integrity failures. NO_SELECTION and SELECTIONS_EMITTED return 0.
    if result.run_status == "SHADOW_RUN_BLOCKED":
        print("SHADOW_RUN_BLOCKED: see artifact_dir for failure receipt", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
