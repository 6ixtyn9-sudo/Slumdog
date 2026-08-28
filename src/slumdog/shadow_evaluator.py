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

Processing order (fail-closed):

1. Schema/keyability check per record (``_extract_decision_fingerprint``
   returns None for malformed/unkeyable records; they are bucketed as
   ``malformed_or_unkeyable`` and do NOT enter conflict detection).
2. Conservative timing gate on every keyable record.
3. Conflict classification on every timed-valid record: group by
   composite key ``(sport, event_id, event_date)``, compare price-free
   decision fingerprints, classify single-fingerprint groups as
   ``admitted_canonical_records`` (one per group) and
   ``exact_decision_duplicate_extra_rows`` (extras collapsed into the
   canonical record) and multi-fingerprint groups as
   ``conflict_groups`` / ``conflicting_rows`` (entire group excluded).
4. ONLY after conflict resolution: identity validation, feature
   construction, R2 eligibility, R1 ranking, per-sport-day
   primary/cohort assignment.
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
# Decision fingerprint
# ---------------------------------------------------------------------------


def _extract_decision_fingerprint(
    record: PreEventRecord,
) -> tuple | None:
    """Build the price-free decision fingerprint for a verified
    snapshot, OR return ``None`` if the record is too malformed to
    form a fingerprint.

    The fingerprint deliberately contains ONLY fields that must
    match for the price-free decision to be identical:

    - ``sport, event_date, event_id`` (composite key)
    - normalized ``participant_1, participant_2`` keys (via
      :func:`slumdog.shadow_contracts.key_of` — the same
      alphanumeric case-folded key the v2 history identity contract
      uses; display strings are NOT used for comparison)
    - ``probability_1, probability_2, draw_probability``

    The fingerprint deliberately EXCLUDES provenance fields whose
    values necessarily differ between two observations of the same
    event that differ only in odds or display metadata
    (``source_url``, ``raw_sha256``, ``sidecar_sha256``,
    ``captured_at``, ``route``, body/sidecar/receipt paths).
    These are committed to by ``input_digest`` separately.

    Returns ``None`` if any of the following is true (the record is
    classified as ``MALFORMED_OR_UNKEYABLE`` and is NOT included
    in conflict detection — it does not silently attach to any
    event):

    - sport is not in :data:`SPORTS`
    - ``event_id`` is empty
    - ``event_date`` is empty
    - either participant display string is empty
    - the two participants normalize to the same key
      (self-pair after normalization; the spec requires the DC
      token ``21`` to NOT be rewritten to ``12``, and ``key_of``
      preserves digit order, so ``key_of("21") == "21"`` and
      ``key_of("12") == "12"``)
    - any of ``probability_1``, ``probability_2``,
      ``draw_probability`` is None
    - any probability is outside ``[0.0, 1.0]``
    """
    from .sports import SPORTS
    if record.sport not in SPORTS:
        return None
    if not record.event_id or not record.event_date:
        return None
    if not record.participant_1 or not record.participant_2:
        return None
    k1 = key_of(record.participant_1)
    k2 = key_of(record.participant_2)
    if not k1 or not k2:
        return None
    if k1 == k2:
        return None
    p1, p2, dp = record.probability_1, record.probability_2, record.draw_probability
    if p1 is None or p2 is None or dp is None:
        return None
    if not (0.0 <= p1 <= 1.0) or not (0.0 <= p2 <= 1.0) or not (0.0 <= dp <= 1.0):
        return None
    return (
        record.sport, record.event_id, record.event_date,
        k1, k2, p1, p2, dp,
    )


def _fingerprint_to_dict(fp: tuple) -> dict[str, Any]:
    """Serialize a decision fingerprint tuple for manifest publication."""
    return {
        "sport": fp[0],
        "event_id": fp[1],
        "event_date": fp[2],
        "participant_key_1": fp[3],
        "participant_key_2": fp[4],
        "probability_1": fp[5],
        "probability_2": fp[6],
        "draw_probability": fp[7],
    }


# ---------------------------------------------------------------------------
# Identity validation (runs only on admitted canonical records)
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
# Per-decision-stage evaluation (runs only on admitted canonical records)
# ---------------------------------------------------------------------------


def _evaluate_for_decision_stage(
    record: PreEventRecord,
    history: HistoryLoadResult,
) -> dict[str, Any]:
    """Run the full per-decision evaluation on the ADMITTED CANONICAL
    record: identity + features + R2 + R1 rank-key. Returns a dict
    with everything the per-sport-day rank loop needs.

    This function runs ONLY on the canonical record of a
    conflict-resolved single-fingerprint group. It MUST NOT be
    called on records excluded by conflict detection or on
    records classified as MALFORMED_OR_UNKEYABLE.
    """
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
# Admitted-canonical wrapper (carries provenance observations for downstream)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _AdmittedCanonical:
    """Wraps a conflict-resolved canonical record with its
    per-composite-key provenance observations and observation
    count. The wrapped ``PreEventRecord`` itself remains
    immutable; this wrapper is the unit the downstream per-record
    evaluation and ranking steps operate on.
    """
    record: PreEventRecord
    provenance_observations: list[dict[str, Any]]
    observation_count: int


# ---------------------------------------------------------------------------
# Stage 1: Timing gate
# ---------------------------------------------------------------------------


def _timing_classify(
    records: list[PreEventRecord],
    *,
    safe_cutoff: _dt.datetime,
) -> tuple[list[PreEventRecord], int, int]:
    """Stage 1 of the pipeline.

    Split verified records into timed-valid + timing-rejected, and
    separately bucket records too malformed to form a decision
    fingerprint.

    Returns ``(timed_records, timing_rejected_count,
    malformed_or_unkeyable_count)``.

    The malformed bucket is reserved for records that fail
    :func:`_extract_decision_fingerprint` (no composite key, no
    fingerprint fields, or self-pair after normalization). These
    records are NOT included in conflict detection and are NOT
    silently attached to an unrelated event.
    """
    timed: list[PreEventRecord] = []
    timing_rejected = 0
    malformed = 0
    for r in records:
        if _extract_decision_fingerprint(r) is None:
            malformed += 1
            continue
        try:
            cap_at = _parse_utc(r.captured_at)
        except ValueError:
            timing_rejected += 1
            continue
        if cap_at > safe_cutoff:
            timing_rejected += 1
            continue
        timed.append(r)
    return timed, timing_rejected, malformed


# ---------------------------------------------------------------------------
# Stage 2: Conflict classification (runs on every timed-valid record)
# ---------------------------------------------------------------------------


def _conflict_classify(
    records: list[PreEventRecord],
) -> tuple[list[_AdmittedCanonical], dict[str, int], list[dict[str, Any]]]:
    """Stage 2 of the pipeline.

    For every timed-valid record, build the price-free decision
    fingerprint and group records by composite key
    ``(sport, event_id, event_date)``. For each group:

    - one fingerprint, N observations → one admitted canonical
      record, N-1 extras counted as decision-equivalent duplicate
      observations; all N source observations preserved as
      provenance;
    - multiple fingerprints, any number of observations →
      entire group excluded; ``conflict_groups`` += 1,
      ``conflicting_rows`` += N; both fingerprints recorded in
      ``conflict_fingerprints``; all source observations
      preserved in ``capture_provenance`` / ``input_provenance``.

    This function MUST be called BEFORE identity / R2 / R1
    filtering so that an R2-ineligible observation whose
    decision content differs from an R2-eligible observation
    can still trigger conflict detection. The previous
    implementation received only records that had already
    passed R2, which allowed an R2-ineligible conflicting
    observation to be silently filtered out and the
    R2-eligible one to be selected.

    Returns ``(admitted_canonicals, accounting, conflict_fingerprints)``
    where:

    - ``admitted_canonicals`` is a list of
      :class:`_AdmittedCanonical` (one per non-conflict composite
      key).
    - ``accounting`` is a dict with the keys
      ``admitted_canonical_records``,
      ``exact_decision_duplicate_groups``,
      ``exact_decision_duplicate_extra_rows``,
      ``conflict_groups``,
      ``conflicting_rows``.
    - ``conflict_fingerprints`` is a list of dicts (one per
      conflicting composite-key group), each containing the
      composite key and the list of distinct decision fingerprints
      observed plus their per-fingerprint observation counts. This
      is published in the manifest for forensic review and
      committed to by ``input_digest``; it is NOT in the
      ``decision_digest``.

    Provenance observations (raw_sha256, captured_at, source_url,
    route, body/sidecar/receipt paths) are stashed on each
    admitted canonical's ``provenance_observations`` attribute.
    For conflicting groups, all source observations' provenance
    remains available in ``capture_provenance`` /
    ``input_provenance`` and via the per-conflict fingerprint
    trail in ``decision_conflicts``.
    """
    groups: dict[tuple[str, str, str], list[PreEventRecord]] = {}
    fingerprints_by_record: dict[int, tuple] = {}
    for r in records:
        fp = _extract_decision_fingerprint(r)
        if fp is None:
            raise ShadowEvaluatorError(
                f"record {r.event_id!r} (sport={r.sport!r}) failed to "
                f"produce a decision fingerprint in the conflict stage; "
                f"the upstream keyability check should have caught this"
            )
        fingerprints_by_record[id(r)] = fp
        key = (fp[0], fp[1], fp[2])
        groups.setdefault(key, []).append(r)
    admitted: list[_AdmittedCanonical] = []
    accounting = {
        "admitted_canonical_records": 0,
        "exact_decision_duplicate_groups": 0,
        "exact_decision_duplicate_extra_rows": 0,
        "conflict_groups": 0,
        "conflicting_rows": 0,
    }
    conflict_fingerprints: list[dict[str, Any]] = []
    for key, members in groups.items():
        by_fingerprint: dict[tuple, list[PreEventRecord]] = {}
        for r in members:
            fp = fingerprints_by_record[id(r)]
            by_fingerprint.setdefault(fp, []).append(r)
        if len(by_fingerprint) > 1:
            # Genuine decision conflict.
            conflict_count = sum(len(v) for v in by_fingerprint.values())
            accounting["conflict_groups"] += 1
            accounting["conflicting_rows"] += conflict_count
            fps_serialized = []
            for fp, rs in by_fingerprint.items():
                entry = _fingerprint_to_dict(fp)
                entry["observation_count"] = len(rs)
                fps_serialized.append(entry)
            conflict_fingerprints.append({
                "sport": key[0], "event_id": key[1], "event_date": key[2],
                "decision_fingerprints": sorted(
                    fps_serialized,
                    key=lambda d: (
                        d["participant_key_1"], d["participant_key_2"],
                        d["probability_1"], d["probability_2"],
                    ),
                ),
            })
            continue
        # Single fingerprint under this composite key.
        members_in_bucket = by_fingerprint[next(iter(by_fingerprint))]
        canonical = members_in_bucket[0]
        provenance_observations: list[dict[str, Any]] = []
        for r in members_in_bucket:
            provenance_observations.append({
                "capture_receipt_path": r.capture_receipt_path or "",
                "sidecar_path": r.sidecar_path or "",
                "body_path": r.body_path or "",
                "raw_sha256": r.raw_sha256 or "",
                "captured_at": r.captured_at or "",
                "source_url": r.source_url or "",
                "route": r.route or "",
            })
        admitted.append(_AdmittedCanonical(
            record=canonical,
            provenance_observations=provenance_observations,
            observation_count=len(members_in_bucket),
        ))
        accounting["admitted_canonical_records"] += 1
        extra = len(members_in_bucket) - 1
        if extra > 0:
            accounting["exact_decision_duplicate_groups"] += 1
            accounting["exact_decision_duplicate_extra_rows"] += extra
    return admitted, accounting, conflict_fingerprints


# ---------------------------------------------------------------------------
# Orchestration result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ShadowRunResult:
    run_id: str
    target_date: str
    decision_committed_at: str
    run_status: str
    artifact_dir: str
    manifest: dict[str, Any]
    payload: dict[str, Any]


def _now_utc() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _now_utc_iso(dt: _dt.datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Stage 3 + 4 + 5: identity, features, R2, R1, per-sport-day rank
# ---------------------------------------------------------------------------


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

    # The verified records come from the capture loader's parser;
    # they have already passed the per-record parser sanity checks
    # (sport in SPORTS, schema, etc.). The pipeline stages are:
    #
    #   Stage 1  _timing_classify  -> malformed_or_unkeyable
    #                              + timing_rejected
    #                              + timed_keyable_snapshots
    #   Stage 2  _conflict_classify on timed_keyable_snapshots
    #                              -> admitted_canonical_records
    #                              + exact_decision_duplicate_extra_rows
    #                              + conflicting_rows
    #   Stage 3+ _evaluate_for_decision_stage on each admitted
    #                              canonical
    #   Stage 4  per-sport-day ranking + primary/cohort selection

    timed_records, timing_rejected, malformed_or_unkeyable = _timing_classify(
        capture_result.records, safe_cutoff=safe_cutoff,
    )
    admitted_canonicals, conflict_accounting, conflict_fingerprints = _conflict_classify(
        timed_records,
    )

    # Build the considered_pool dict view for the manifest and
    # the decision_digest. Every verified parser snapshot is
    # represented exactly once, with its final considered_status.
    # For single-fingerprint groups with N>1 observations, the
    # canonical record appears once with its rank status, and
    # the N-1 extras each appear as
    # EXACT_DECISION_DUPLICATE_OBSERVATION. This makes the
    # duplicate-source accounting visible in the decision_digest
    # (documented choice).
    considered_pool_dicts: list[dict[str, Any]] = []
    # 1) Conflict-excluded source observations.
    for fp_group in conflict_fingerprints:
        for fp in fp_group["decision_fingerprints"]:
            for _ in range(fp["observation_count"]):
                considered_pool_dicts.append({
                    "sport": fp_group["sport"],
                    "event_id": fp_group["event_id"],
                    "event_date": fp_group["event_date"],
                    "considered_status": "DECISION_CONFLICT_EXCLUDED",
                    "eligible": False,
                    "rank_within_sport_day": None,
                })
    # 2) Single-fingerprint duplicate extras (collapsed under the
    #    canonical record). One entry per extra observation.
    #    This is needed so the decision_digest commits to
    #    the duplicate-source accounting.
    for ac in admitted_canonicals:
        for _ in range(ac.observation_count - 1):
            considered_pool_dicts.append({
                "sport": ac.record.sport,
                "event_id": ac.record.event_id,
                "event_date": ac.record.event_date,
                "considered_status": "EXACT_DECISION_DUPLICATE_OBSERVATION",
                "eligible": False,
                "rank_within_sport_day": None,
            })

    # Stage 3: per-canonical identity / features / R2 / R1.
    per_record_evals: list[dict[str, Any]] = []
    for ac in admitted_canonicals:
        ev = _evaluate_for_decision_stage(ac.record, history_result)
        ev["_provenance_observations"] = ac.provenance_observations
        ev["_observation_count"] = ac.observation_count
        per_record_evals.append(ev)
    # Per-canonical classification counts.
    identity_ineligible_count = 0
    feature_incomplete_count = 0
    for ev in per_record_evals:
        if not ev["eligible"]:
            if ev["status"] == "FEATURE_INCOMPLETE_OR_R2_INELIGIBLE":
                feature_incomplete_count += 1
            else:
                identity_ineligible_count += 1

    # 2) Malformed / timing-rejected snapshots.
    timed_set: set[int] = {id(r) for r in timed_records}
    for r in capture_result.records:
        if id(r) in timed_set:
            continue
        if _extract_decision_fingerprint(r) is None:
            considered_pool_dicts.append({
                "sport": r.sport, "event_id": r.event_id,
                "event_date": r.event_date,
                "considered_status": "MALFORMED_OR_UNKEYABLE",
                "eligible": False, "rank_within_sport_day": None,
            })
        else:
            considered_pool_dicts.append({
                "sport": r.sport, "event_id": r.event_id,
                "event_date": r.event_date,
                "considered_status": "TIMING_REJECTED",
                "eligible": False, "rank_within_sport_day": None,
            })
    # 3) Per-sport-day ranking for the admitted canonicals that
    #    passed the decision stage.
    by_sport_day: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for ev in per_record_evals:
        if not ev["eligible"]:
            continue
        record = ev["record"]
        sd = (record.sport, record.event_date)
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
        primary_event_id = None
        cohort_ids: list[str] = []
        r4plus_ids: list[str] = []
        for rank_idx, ev in enumerate(evs, start=1):
            record = ev["record"]
            identity = ev["identity"]
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
            ev["considered_status"] = status
            ev["rank_within_sport_day"] = rank_idx
            if rank_idx > 3:
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
        if primary_event_id is not None:
            summary_status = "SHADOW_RULE_QUALIFIED"
        else:
            summary_status = "SHADOW_NO_SELECTION"
        sport_day_summary.append({
            "sport": sport, "event_date": _date,
            "status": summary_status,
            "eligible_count": len(evs),
            "primary_event_id": primary_event_id,
            "cohort_event_ids": cohort_ids,
            "eligible_r4_plus_event_ids": r4plus_ids,
        })
    # 4) Admitted canonicals that did not pass the per-sport-day
    #    rank loop (identity-ineligible, feature-incomplete).
    for ev in per_record_evals:
        cs = ev.get("considered_status") or ev.get("status")
        if cs in (
            "PRIMARY_SHADOW_SELECTION",
            "TOP3_EVALUATION_COHORT",
            "ELIGIBLE_RANKED_BEYOND_TOP3",
        ):
            considered_pool_dicts.append({
                "sport": ev["record"].sport,
                "event_id": ev["record"].event_id,
                "event_date": ev["record"].event_date,
                "considered_status": cs,
                "eligible": True,
                "rank_within_sport_day": ev.get("rank_within_sport_day"),
            })
        else:
            considered_pool_dicts.append({
                "sport": ev["record"].sport,
                "event_id": ev["record"].event_id,
                "event_date": ev["record"].event_date,
                "considered_status": cs,
                "eligible": False,
                "rank_within_sport_day": None,
            })
    considered_pool_dicts.sort(key=lambda d: (d["sport"], d["event_date"], d["event_id"]))

    # ------------------------------------------------------------------
    # Staged accounting (three equations, asserted before write)
    # ------------------------------------------------------------------
    #
    # Stage 1: verified_parser_snapshots = malformed + timing + timed
    total_in = len(capture_result.records)
    assert (
        malformed_or_unkeyable
        + timing_rejected
        + (total_in - malformed_or_unkeyable - timing_rejected)
        == total_in
    ), "stage 1 staging imbalance"
    timed_keyable_snapshots = total_in - malformed_or_unkeyable - timing_rejected
    # Stage 2: timed_keyable = admitted + extras + conflicting
    assert (
        conflict_accounting["admitted_canonical_records"]
        + conflict_accounting["exact_decision_duplicate_extra_rows"]
        + conflict_accounting["conflicting_rows"]
        == timed_keyable_snapshots
    ), "stage 2 staging imbalance"
    # Stage 3: admitted_canonicals = identity_ineligible + feature_incomplete + ranked
    assert (
        identity_ineligible_count
        + feature_incomplete_count
        + primary_count
        + cohort_count
        + r4plus_count
        == conflict_accounting["admitted_canonical_records"]
    ), "stage 3 staging imbalance"

    decision_accounting = {
        "decision_total_records": total_in,
        # Stage 1
        "malformed_or_unkeyable": malformed_or_unkeyable,
        "timing_rejected": timing_rejected,
        "timed_keyable_snapshots": timed_keyable_snapshots,
        # Stage 2
        "admitted_canonical_records": conflict_accounting["admitted_canonical_records"],
        "exact_decision_duplicate_groups": conflict_accounting["exact_decision_duplicate_groups"],
        "exact_decision_duplicate_extra_rows": conflict_accounting["exact_decision_duplicate_extra_rows"],
        "conflict_groups": conflict_accounting["conflict_groups"],
        "conflicting_rows": conflict_accounting["conflicting_rows"],
        # Stage 3
        "identity_ineligible": identity_ineligible_count,
        "feature_incomplete_or_r2_ineligible": feature_incomplete_count,
        "primary_selected": primary_count,
        "top3_cohort_selected": cohort_count,
        "eligible_ranked_beyond_top3": r4plus_count,
    }

    # ------------------------------------------------------------------
    # Digests
    # ------------------------------------------------------------------
    declaration_sha = _canonical_sha256(declaration)
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
    capture_accounting_digest = _canonical_sha256(capture_result.capture_accounting)
    snapshot_accounting_digest = _canonical_sha256(capture_result.snapshot_accounting)
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
    # ``input_digest`` commits to every source observation AND to the
    # conflict fingerprint trail. It does NOT depend on the per-record
    # considered_status; that lives in the decision_digest.
    conflict_fingerprint_trail = []
    for c in conflict_fingerprints:
        conflict_fingerprint_trail.append({
            "sport": c["sport"], "event_id": c["event_id"],
            "event_date": c["event_date"],
            "decision_fingerprint_count": len(c["decision_fingerprints"]),
        })
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
        "conflict_fingerprint_trail": conflict_fingerprint_trail,
    }
    input_digest = _canonical_sha256(input_digest_payload)

    # ``decision_digest`` commits to the conflict-resolved pool,
    # exclusions, primary/cohort selections, and the staged
    # accounting. It intentionally includes the duplicate-source
    # accounting (``admitted_canonical_records``,
    # ``exact_decision_duplicate_extra_rows``,
    # ``conflict_groups``, ``conflicting_rows``) so two runs with
    # the same decision content but different number of
    # observations produce different decision_digests — this is
    # the documented choice (the accounting IS the reproducible
    # record of how many observations were collapsed). The
    # decision_digest is INDEPENDENT of per-snapshot source fields
    # (odds-only differences do not change it).
    pool_for_digest = sorted(
        (d["sport"], d["event_id"], d["event_date"],
         d["considered_status"], d["eligible"], d["rank_within_sport_day"])
        for d in considered_pool_dicts
    )
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

    # ------------------------------------------------------------------
    # Atomic write
    # ------------------------------------------------------------------
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
        "considered_pool": considered_pool_dicts,
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
    stamp = decision_committed_at.replace(":", "").replace("Z", "")
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
    """Top-level disk-to-artifact orchestration."""
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
    if result.run_status == "SHADOW_RUN_BLOCKED":
        print("SHADOW_RUN_BLOCKED: see artifact_dir for failure receipt", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
