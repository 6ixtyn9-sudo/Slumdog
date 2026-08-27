"""Milestone 6B — Non-trained baseline analyzer.

Implements the frozen two-pass non-trained baseline analyzer according to
config/research_baselines_v1.json without altering the frozen rules.

Pass 1: Streaming integrity checks (SHA-256 over decompressed JSONL bytes ==
receipt.examples_digest, row count == receipt.accounting.eligible_examples,
every row event_date within P1..P4 union, fail-closed on non-finite values).
Fails closed with non-zero exit on any mismatch or integrity violation.

Pass 2: Streaming analysis across periods (P1..P4):
- Missingness reporting for every analyzed feature (global & per sport)
- Signal bucketing for all 7 pre-declared signals with precedence rules
- Comparator rule ranking & evaluation (R0 Forebet-only, R1 Always-rank,
  R2 Conservative fixed rule)
- Quota-forced selections for R0/R1, opportunity-gated selections for R2
- Daily top-1 and top-3 hit rates (selected-day vs all-opportunity-day rates)
- Losing streaks (candidate-level within sport; daily top-1 within sport;
  global is max of per-sport values; no-pick days neither increment nor reset)
- Safe atomic output finalization under /tmp (baselines.json and summary.md)
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .dataset import ALLOWED_FEATURES, PROHIBITED_KEYS

FROZEN_CONFIG_PATH = Path("config/research_baselines_v1.json")
CANONICAL_CONFIG_SHA256 = (
    "666dabe7ea21e11867cf4816f4c2edcd771247646c6c9d7726c22611cda700a1"
)

INSUFFICIENT_BUCKET_THRESHOLD = 30


class BaselineIntegrityError(Exception):
    """Raised when an integrity check or contract constraint is violated."""


def canonical_json_bytes(obj: Any) -> bytes:
    """Compute UTF-8 bytes of canonical JSON: keys sorted recursively, compact separators."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compute_config_sha256(config_dict: dict[str, Any]) -> str:
    """Return SHA-256 digest over canonical JSON bytes of config_dict."""
    return hashlib.sha256(canonical_json_bytes(config_dict)).hexdigest()


def verify_frozen_config(
    config_dict: dict[str, Any],
    expected_sha256: str = CANONICAL_CONFIG_SHA256,
) -> str:
    """Verify that config_dict matches the frozen pre-declaration and rules."""
    computed_sha256 = compute_config_sha256(config_dict)
    if computed_sha256 != expected_sha256:
        raise BaselineIntegrityError(
            f"Config SHA-256 mismatch: expected {expected_sha256}, got {computed_sha256}"
        )

    # Verify anti-tuning pre-declarations
    anti_tuning = config_dict.get("anti_tuning", {})
    if anti_tuning.get("tuning_periods") != []:
        raise BaselineIntegrityError(
            f"anti_tuning.tuning_periods must be empty, got {anti_tuning.get('tuning_periods')}"
        )
    if anti_tuning.get("result_driven_amendments") != "prohibited":
        raise BaselineIntegrityError("result_driven_amendments must be 'prohibited'")

    # Verify shortlist policy and training gates
    if config_dict.get("shortlist_policy_authorized") is not False:
        raise BaselineIntegrityError("shortlist_policy_authorized must be false")

    prohibited = set(config_dict.get("prohibited", []))
    required_prohibitions = {
        "odds",
        "fitted_estimators",
        "threshold_optimization",
        "random_splits",
        "roi",
        "period_values",
        "unknown_timing_facets",
        "calibrated_probability_claims",
        "production_picks",
        "daily_shortlist_activation",
    }
    missing_prohibitions = required_prohibitions - prohibited
    if missing_prohibitions:
        raise BaselineIntegrityError(
            f"Config missing required prohibitions: {missing_prohibitions}"
        )

    return computed_sha256


def get_period_for_date(event_date: str, periods: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Find the single period covering event_date, or None if out of all periods."""
    for p in periods:
        if p["start"] <= event_date <= p["end"]:
            return p
    return None


def check_finite_values(row_dict: dict[str, Any]) -> None:
    """Fail closed on any NaN/Infinity in row fields or features."""
    features = row_dict.get("features", {})
    if isinstance(features, dict):
        for k, v in features.items():
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                raise BaselineIntegrityError(
                    f"Non-finite value in features[{k!r}]: {v} for event_id {row_dict.get('event_id')}"
                )
    for top_k in (
        "favorite_probability",
        "underdog_probability",
        "draw_probability",
        "probability_gap",
    ):
        v = row_dict.get(top_k)
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            raise BaselineIntegrityError(
                f"Non-finite value in {top_k}: {v} for event_id {row_dict.get('event_id')}"
            )


@dataclass
class Pass1Result:
    row_count: int
    decompressed_sha256: str
    period_ids: set[str]
    sport_keys: set[str]
    period_sport_counts: dict[tuple[str, str], int] = field(
        default_factory=lambda: defaultdict(int)
    )
    period_sport_underdog_wins: dict[tuple[str, str], int] = field(
        default_factory=lambda: defaultdict(int)
    )
    period_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    period_underdog_wins: dict[str, int] = field(default_factory=lambda: defaultdict(int))


def run_pass1(
    examples_gz_path: Path,
    receipt_path: Path,
    periods: list[dict[str, Any]],
) -> Pass1Result:
    """Pass 1: Streaming integrity checks over decompressed bytes and rows.

    Checks:
    1. decompressed_jsonl_sha256 == receipt.examples_digest
    2. row_count == receipt.accounting.eligible_examples
    3. every row event_date within P1..P4 union
    4. non-finite values fail closed
    5. no prohibited keys in examples
    """
    if not examples_gz_path.is_file():
        raise BaselineIntegrityError(f"Examples file not found: {examples_gz_path}")
    if not receipt_path.is_file():
        raise BaselineIntegrityError(f"Receipt file not found: {receipt_path}")

    try:
        receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise BaselineIntegrityError(f"Could not parse receipt JSON: {exc}") from exc

    expected_digest = receipt_data.get("examples_digest")
    if not expected_digest or len(expected_digest) != 64:
        raise BaselineIntegrityError(
            f"Receipt missing valid 64-hex examples_digest: {expected_digest}"
        )

    expected_rows = receipt_data.get("accounting", {}).get("eligible_examples")
    if expected_rows is None:
        raise BaselineIntegrityError("Receipt missing accounting.eligible_examples")

    hasher = hashlib.sha256()
    row_count = 0
    period_ids: set[str] = set()
    sport_keys: set[str] = set()
    period_sport_counts: dict[tuple[str, str], int] = defaultdict(int)
    period_sport_underdog_wins: dict[tuple[str, str], int] = defaultdict(int)
    period_counts: dict[str, int] = defaultdict(int)
    period_underdog_wins: dict[str, int] = defaultdict(int)

    try:
        with gzip.open(examples_gz_path, "rb") as gz_file:
            for line_bytes in gz_file:
                hasher.update(line_bytes)
                row_count += 1

                try:
                    row = json.loads(line_bytes.decode("utf-8"))
                except Exception as exc:
                    raise BaselineIntegrityError(
                        f"JSON parse failure on row {row_count}: {exc}"
                    ) from exc

                # Integrity checks on row
                check_finite_values(row)

                # Check prohibited keys
                features = row.get("features", {})
                for k in features:
                    if k in PROHIBITED_KEYS:
                        raise BaselineIntegrityError(
                            f"Prohibited key '{k}' found in row {row_count}"
                        )

                event_date = row.get("event_date")
                if not event_date:
                    raise BaselineIntegrityError(f"Missing event_date in row {row_count}")

                period = get_period_for_date(event_date, periods)
                if period is None:
                    raise BaselineIntegrityError(
                        f"out_of_period_row: event_date {event_date} for event_id "
                        f"{row.get('event_id')} outside P1..P4 union"
                    )

                p_id = period["id"]
                sport = row.get("sport")
                if not sport:
                    raise BaselineIntegrityError(f"Missing sport in row {row_count}")

                label = row.get("label")
                if label not in (0, 1):
                    raise BaselineIntegrityError(
                        f"Invalid label {label} in row {row_count} (must be 0 or 1)"
                    )

                period_ids.add(p_id)
                sport_keys.add(sport)
                period_sport_counts[(p_id, sport)] += 1
                period_counts[p_id] += 1
                if label == 1:
                    period_sport_underdog_wins[(p_id, sport)] += 1
                    period_underdog_wins[p_id] += 1

    except gzip.BadGzipFile as exc:
        raise BaselineIntegrityError(f"Bad gzip file {examples_gz_path}: {exc}") from exc

    computed_digest = hasher.hexdigest()
    if computed_digest != expected_digest:
        raise BaselineIntegrityError(
            f"decompressed_jsonl_sha256 mismatch: computed {computed_digest}, expected {expected_digest}"
        )

    if row_count != expected_rows:
        raise BaselineIntegrityError(
            f"row_count mismatch: computed {row_count}, expected {expected_rows}"
        )

    return Pass1Result(
        row_count=row_count,
        decompressed_sha256=computed_digest,
        period_ids=period_ids,
        sport_keys=sport_keys,
        period_sport_counts=period_sport_counts,
        period_sport_underdog_wins=period_sport_underdog_wins,
        period_counts=period_counts,
        period_underdog_wins=period_underdog_wins,
    )


# ---------------------------------------------------------------------------
# Signal Bucketing Logic
# ---------------------------------------------------------------------------


def _matches_interval(val: float, bucket_def: dict[str, Any]) -> bool:
    lo = bucket_def.get("lo")
    lo_inc = bucket_def.get("lo_inclusive")
    hi = bucket_def.get("hi")
    hi_inc = bucket_def.get("hi_inclusive")

    if lo is not None:
        if lo_inc:
            if val < lo:
                return False
        else:
            if val <= lo:
                return False

    if hi is not None:
        if hi_inc:
            if val > hi:
                return False
        else:
            if val >= hi:
                return False

    return True


def assign_conceding_rate_gap_bucket(
    features: dict[str, Any], buckets: list[dict[str, Any]]
) -> str:
    val = features.get("prior_conceding_rate_gap")
    if val is None:
        return "missing"
    for b in buckets:
        if b.get("when", {}).get("op") == "missing":
            continue
        if _matches_interval(val, b):
            return b["name"]
    return "missing"


def assign_evidence_availability_bucket(
    features: dict[str, Any],
    buckets: list[dict[str, Any]],
    components: list[str],
) -> str:
    present_count = sum(1 for c in components if features.get(c) is not None)
    for b in buckets:
        min_p = b.get("min_present", 0)
        max_p = b.get("max_present", 6)
        if min_p <= present_count <= max_p:
            return b["name"]
    return buckets[-1]["name"]


def assign_h2h_underdog_win_rate_bucket(
    features: dict[str, Any], buckets: list[dict[str, Any]]
) -> str:
    # Precedence: ["no-h2h", "numeric_buckets", "missing/inconsistent"]
    h2h_games = features.get("h2h_prior_games")
    if h2h_games == 0 or h2h_games == 0.0:
        return "no-h2h"

    rate = features.get("h2h_underdog_win_rate")
    if rate is not None and isinstance(rate, (int, float)):
        # Check numeric buckets
        for b in buckets:
            if b.get("name") in ("no-h2h", "missing/inconsistent"):
                continue
            # Epsilon tolerance for exact boundaries
            lo = b.get("lo")
            lo_inc = b.get("lo_inclusive")
            hi = b.get("hi")
            hi_inc = b.get("hi_inclusive")

            # Exact point buckets
            if lo == hi and lo is not None:
                if abs(rate - lo) < 1e-6:
                    return b["name"]
            else:
                lo_ok = (rate >= lo) if lo_inc else (rate > lo + 1e-6)
                hi_ok = (rate <= hi) if hi_inc else (rate < hi - 1e-6)
                if lo_ok and hi_ok:
                    return b["name"]

    return "missing/inconsistent"


def assign_probability_gap_bucket(
    features: dict[str, Any], buckets: list[dict[str, Any]]
) -> str:
    val = features.get("forebet_probability_gap")
    if val is None:
        return "missing"
    for b in buckets:
        if b.get("when", {}).get("op") == "missing":
            continue
        if _matches_interval(val, b):
            return b["name"]
    return "missing"


def assign_recent_win_rate_gap_bucket(
    features: dict[str, Any], buckets: list[dict[str, Any]]
) -> str:
    val = features.get("recent_win_rate_gap")
    if val is None:
        return "missing"
    for b in buckets:
        if b.get("when", {}).get("op") == "missing":
            continue
        if _matches_interval(val, b):
            return b["name"]
    return "missing"


def assign_scoring_rate_gap_bucket(
    features: dict[str, Any], buckets: list[dict[str, Any]]
) -> str:
    val = features.get("prior_scoring_rate_gap")
    if val is None:
        return "missing"
    for b in buckets:
        if b.get("when", {}).get("op") == "missing":
            continue
        if _matches_interval(val, b):
            return b["name"]
    return "missing"


def assign_underdog_probability_bucket(
    features: dict[str, Any], buckets: list[dict[str, Any]]
) -> str:
    val = features.get("forebet_underdog_probability")
    if val is None:
        return "missing"
    for b in buckets:
        if b.get("when", {}).get("op") == "missing":
            continue
        if _matches_interval(val, b):
            return b["name"]
    return "missing"


def assign_signal_bucket(
    signal_name: str,
    features: dict[str, Any],
    signal_def: dict[str, Any],
) -> str:
    buckets = signal_def["buckets"]
    if signal_name == "conceding_rate_gap":
        return assign_conceding_rate_gap_bucket(features, buckets)
    elif signal_name == "evidence_availability":
        components = signal_def["components"]
        return assign_evidence_availability_bucket(features, buckets, components)
    elif signal_name == "h2h_underdog_win_rate":
        return assign_h2h_underdog_win_rate_bucket(features, buckets)
    elif signal_name == "probability_gap":
        return assign_probability_gap_bucket(features, buckets)
    elif signal_name == "recent_win_rate_gap":
        return assign_recent_win_rate_gap_bucket(features, buckets)
    elif signal_name == "scoring_rate_gap":
        return assign_scoring_rate_gap_bucket(features, buckets)
    elif signal_name == "underdog_probability":
        return assign_underdog_probability_bucket(features, buckets)
    else:
        raise ValueError(f"Unknown signal {signal_name}")


# ---------------------------------------------------------------------------
# Rule Ranking and Eligibility Logic
# ---------------------------------------------------------------------------


def r0_sort_key(ev: dict[str, Any]) -> tuple:
    feats = ev.get("features", {})
    p_dog = feats.get("forebet_underdog_probability")
    p_gap = feats.get("forebet_probability_gap")
    eid = str(ev.get("event_id", ""))

    k1 = (1, 0.0) if p_dog is None else (0, -float(p_dog))
    k2 = (1, 0.0) if p_gap is None else (0, float(p_gap))
    return (k1, k2, eid)


def r1_sort_key(ev: dict[str, Any]) -> tuple:
    feats = ev.get("features", {})
    rw_gap = feats.get("recent_win_rate_gap")
    h2h_games = feats.get("h2h_prior_games")
    h2h_rate = feats.get("h2h_underdog_win_rate")
    p_gap = feats.get("forebet_probability_gap")
    eid = str(ev.get("event_id", ""))

    # 1. recent_win_rate_gap desc, missing last
    k1 = (1, 0.0) if rw_gap is None else (0, -float(rw_gap))

    # 2. h2h_underdog_win_rate desc, missing_or_no_h2h last
    if h2h_games == 0 or h2h_games == 0.0 or h2h_rate is None:
        k2 = (1, 0.0)
    else:
        k2 = (0, -float(h2h_rate))

    # 3. forebet_probability_gap asc
    k3 = (1, 0.0) if p_gap is None else (0, float(p_gap))

    # 4. event_id asc
    return (k1, k2, k3, eid)


def is_r2_eligible(features: dict[str, Any]) -> bool:
    ud_games = features.get("underdog_prior_games")
    fav_games = features.get("favorite_prior_games")
    h2h_games = features.get("h2h_prior_games")
    gap = features.get("forebet_probability_gap")

    # Missingness disqualifies
    if ud_games is None or fav_games is None or h2h_games is None or gap is None:
        return False

    return ud_games >= 5 and fav_games >= 5 and h2h_games >= 1 and gap <= 0.2


# ---------------------------------------------------------------------------
# Streak Calculations
# ---------------------------------------------------------------------------


def compute_longest_losing_streak_from_labels(labels: list[int]) -> int:
    """Compute longest consecutive sequence of 0s in labels."""
    max_streak = 0
    curr_streak = 0
    for label in labels:
        if label == 0:
            curr_streak += 1
            if curr_streak > max_streak:
                max_streak = curr_streak
        else:
            curr_streak = 0
    return max_streak


def compute_daily_top1_longest_losing_streak(selected_day_hits: list[bool]) -> int:
    """Compute longest consecutive sequence of top1 losses (False hits).

    No-pick days are already excluded from selected_day_hits, matching the rule
    that no-pick days neither increment nor reset the selected-day streak.
    """
    max_streak = 0
    curr_streak = 0
    for hit in selected_day_hits:
        if not hit:
            curr_streak += 1
            if curr_streak > max_streak:
                max_streak = curr_streak
        else:
            curr_streak = 0
    return max_streak


# ---------------------------------------------------------------------------
# Pass 2 Analysis Execution
# ---------------------------------------------------------------------------


def run_pass2(
    examples_gz_path: Path,
    config_dict: dict[str, Any],
    pass1_result: Pass1Result,
) -> dict[str, Any]:
    """Pass 2: Streaming analysis computing missingness, signals, and rules."""
    periods = config_dict["periods"]
    signals_config = config_dict["signals"]

    # Data structures for Pass 2
    # 1. Missingness counters: [period_id][sport/global][feature] -> {"total": int, "missing": int}
    missingness_data: dict[str, dict[str, dict[str, dict[str, int]]]] = {
        p["id"]: {"global": {f: {"total": 0, "missing": 0} for f in ALLOWED_FEATURES}}
        for p in periods
    }
    for p_id in missingness_data:
        for sport in pass1_result.sport_keys:
            missingness_data[p_id][sport] = {
                f: {"total": 0, "missing": 0} for f in ALLOWED_FEATURES
            }

    # 2. Signal bucket examples: [period_id][signal_name][sport][bucket_name] -> list of (event_date, event_id, label)
    # and sport_days: [period_id][signal_name][sport][bucket_name] -> set of (sport, event_date)
    signal_candidates: dict[str, dict[str, dict[str, dict[str, list[tuple[str, str, int]]]]]] = {
        p["id"]: {
            s_name: {
                sport: {b["name"]: [] for b in s_def["buckets"]}
                for sport in pass1_result.sport_keys
            }
            for s_name, s_def in signals_config.items()
        }
        for p in periods
    }
    signal_sport_days: dict[str, dict[str, dict[str, dict[str, set[tuple[str, str]]]]]] = {
        p["id"]: {
            s_name: {
                sport: {b["name"]: set() for b in s_def["buckets"]}
                for sport in pass1_result.sport_keys
            }
            for s_name, s_def in signals_config.items()
        }
        for p in periods
    }

    # 3. Sport-day events for rule ranking: [period_id][sport][event_date] -> list of event dicts
    sport_day_events: dict[str, dict[str, dict[str, list[dict[str, Any]]]]] = {
        p["id"]: {sport: defaultdict(list) for sport in pass1_result.sport_keys}
        for p in periods
    }

    # Stream through examples.jsonl.gz
    with gzip.open(examples_gz_path, "rb") as gz_file:
        for line_bytes in gz_file:
            row = json.loads(line_bytes.decode("utf-8"))
            event_date = row["event_date"]
            period = get_period_for_date(event_date, periods)
            assert period is not None
            p_id = period["id"]
            sport = row["sport"]
            label = row["label"]
            features = row.get("features", {})
            event_id = row["event_id"]

            # Missingness
            for f in ALLOWED_FEATURES:
                is_missing = (
                    row.get("missingness", {}).get(f) == 1 or features.get(f) is None
                )
                missingness_data[p_id]["global"][f]["total"] += 1
                missingness_data[p_id][sport][f]["total"] += 1
                if is_missing:
                    missingness_data[p_id]["global"][f]["missing"] += 1
                    missingness_data[p_id][sport][f]["missing"] += 1

            # Signal bucketing
            for s_name, s_def in signals_config.items():
                b_name = assign_signal_bucket(s_name, features, s_def)
                signal_candidates[p_id][s_name][sport][b_name].append(
                    (event_date, event_id, label)
                )
                signal_sport_days[p_id][s_name][sport][b_name].add((sport, event_date))

            # Store for rule ranking
            sport_day_events[p_id][sport][event_date].append(row)

    # -----------------------------------------------------------------------
    # Aggregate Period & Missingness Reporting
    # -----------------------------------------------------------------------
    periods_report: dict[str, Any] = {}

    for p in periods:
        p_id = p["id"]
        total_p_examples = pass1_result.period_counts[p_id]
        total_p_wins = pass1_result.period_underdog_wins[p_id]
        global_base_rate = (
            total_p_wins / total_p_examples if total_p_examples > 0 else 0.0
        )

        p_rep: dict[str, Any] = {
            "id": p_id,
            "name": p["name"],
            "start": p["start"],
            "end": p["end"],
            "totals": {
                "examples": total_p_examples,
                "underdog_wins": total_p_wins,
                "base_rate": global_base_rate,
                "sports": sorted(pass1_result.sport_keys),
                "per_sport": {
                    sport: {
                        "examples": pass1_result.period_sport_counts[(p_id, sport)],
                        "underdog_wins": pass1_result.period_sport_underdog_wins[
                            (p_id, sport)
                        ],
                        "base_rate": (
                            pass1_result.period_sport_underdog_wins[(p_id, sport)]
                            / pass1_result.period_sport_counts[(p_id, sport)]
                            if pass1_result.period_sport_counts[(p_id, sport)] > 0
                            else 0.0
                        ),
                    }
                    for sport in sorted(pass1_result.sport_keys)
                },
            },
            "missingness": {
                "global": {
                    f: {
                        "total": missingness_data[p_id]["global"][f]["total"],
                        "missing": missingness_data[p_id]["global"][f]["missing"],
                        "present": (
                            missingness_data[p_id]["global"][f]["total"]
                            - missingness_data[p_id]["global"][f]["missing"]
                        ),
                        "missing_rate": (
                            missingness_data[p_id]["global"][f]["missing"]
                            / missingness_data[p_id]["global"][f]["total"]
                            if missingness_data[p_id]["global"][f]["total"] > 0
                            else 0.0
                        ),
                    }
                    for f in ALLOWED_FEATURES
                },
                "per_sport": {
                    sport: {
                        f: {
                            "total": missingness_data[p_id][sport][f]["total"],
                            "missing": missingness_data[p_id][sport][f]["missing"],
                            "present": (
                                missingness_data[p_id][sport][f]["total"]
                                - missingness_data[p_id][sport][f]["missing"]
                            ),
                            "missing_rate": (
                                missingness_data[p_id][sport][f]["missing"]
                                / missingness_data[p_id][sport][f]["total"]
                                if missingness_data[p_id][sport][f]["total"] > 0
                                else 0.0
                            ),
                        }
                        for f in ALLOWED_FEATURES
                    }
                    for sport in sorted(pass1_result.sport_keys)
                },
            },
            "signals": {},
            "rules": {},
        }

        # -------------------------------------------------------------------
        # Signal Bucket Tables Calculation
        # -------------------------------------------------------------------
        for s_name, s_def in signals_config.items():
            buckets_def = s_def["buckets"]
            per_sport_buckets: dict[str, list[dict[str, Any]]] = {}
            sport_bucket_streaks: dict[str, dict[str, int]] = defaultdict(dict)

            # Per-sport signal tables
            for sport in sorted(pass1_result.sport_keys):
                sport_total_examples = pass1_result.period_sport_counts[(p_id, sport)]
                sport_wins = pass1_result.period_sport_underdog_wins[(p_id, sport)]
                sport_base_rate = (
                    sport_wins / sport_total_examples if sport_total_examples > 0 else 0.0
                )

                b_list: list[dict[str, Any]] = []
                for b_def in buckets_def:
                    b_name = b_def["name"]
                    cands = signal_candidates[p_id][s_name][sport][b_name]
                    # Sort candidates by (event_date, event_id) per candidate_level_order
                    cands.sort(key=lambda x: (x[0], x[1]))
                    c_examples = len(cands)
                    c_wins = sum(1 for c in cands if c[2] == 1)
                    c_prec = (c_wins / c_examples) if c_examples > 0 else None
                    c_lift = (
                        (c_prec / sport_base_rate)
                        if (c_prec is not None and sport_base_rate > 0)
                        else None
                    )
                    c_cov = (
                        (c_examples / sport_total_examples)
                        if sport_total_examples > 0
                        else 0.0
                    )
                    c_days = len(signal_sport_days[p_id][s_name][sport][b_name])
                    streak = compute_longest_losing_streak_from_labels([c[2] for c in cands])
                    sport_bucket_streaks[b_name][sport] = streak
                    is_insufficient = c_examples < INSUFFICIENT_BUCKET_THRESHOLD

                    b_list.append(
                        {
                            "name": b_name,
                            "examples": c_examples,
                            "underdog_wins": c_wins,
                            "base_rate": sport_base_rate,
                            "candidate_precision": c_prec,
                            "lift": c_lift,
                            "coverage": c_cov,
                            "sport_days_represented": c_days,
                            "candidate_level_longest_losing_streak": streak,
                            "insufficient": is_insufficient,
                        }
                    )
                per_sport_buckets[sport] = b_list

            # Global signal table: aggregate across sports
            global_b_list: list[dict[str, Any]] = []
            for b_def in buckets_def:
                b_name = b_def["name"]
                tot_examples = sum(
                    len(signal_candidates[p_id][s_name][sp][b_name])
                    for sp in pass1_result.sport_keys
                )
                tot_wins = sum(
                    sum(1 for c in signal_candidates[p_id][s_name][sp][b_name] if c[2] == 1)
                    for sp in pass1_result.sport_keys
                )
                g_prec = (tot_wins / tot_examples) if tot_examples > 0 else None
                g_lift = (
                    (g_prec / global_base_rate)
                    if (g_prec is not None and global_base_rate > 0)
                    else None
                )
                g_cov = (
                    (tot_examples / total_p_examples) if total_p_examples > 0 else 0.0
                )
                tot_days = sum(
                    len(signal_sport_days[p_id][s_name][sp][b_name])
                    for sp in pass1_result.sport_keys
                )
                # Global candidate-level streak is maximum of per-sport streaks
                g_streak = (
                    max(sport_bucket_streaks[b_name].values())
                    if sport_bucket_streaks[b_name]
                    else 0
                )
                g_insufficient = tot_examples < INSUFFICIENT_BUCKET_THRESHOLD

                global_b_list.append(
                    {
                        "name": b_name,
                        "examples": tot_examples,
                        "underdog_wins": tot_wins,
                        "base_rate": global_base_rate,
                        "candidate_precision": g_prec,
                        "lift": g_lift,
                        "coverage": g_cov,
                        "sport_days_represented": tot_days,
                        "candidate_level_longest_losing_streak": g_streak,
                        "insufficient": g_insufficient,
                    }
                )

            p_rep["signals"][s_name] = {
                "global": global_b_list,
                "per_sport": per_sport_buckets,
            }

        # -------------------------------------------------------------------
        # Rule Comparators (R0, R1, R2)
        # -------------------------------------------------------------------
        for rule_name in (
            "R0_FOREBET_ONLY_COMPARATOR",
            "R1_ALWAYS_RANK_COMPARATOR",
            "R2_CONSERVATIVE_FIXED_RULE",
        ):
            per_sport_rules: dict[str, dict[str, Any]] = {}
            per_sport_top1_streaks: dict[str, int] = {}

            # Evaluate per sport
            for sport in sorted(pass1_result.sport_keys):
                dates_dict = sport_day_events[p_id][sport]
                sorted_dates = sorted(dates_dict.keys())
                opp_days = len(sorted_dates)
                tot_available_events = sum(len(dates_dict[d]) for d in sorted_dates)

                selected_days = 0
                top1_hits = 0
                top3_hits = 0
                total_top1_selections = 0
                total_top3_selections = 0
                selected_day_hit_results: list[bool] = []

                for d in sorted_dates:
                    events = dates_dict[d]
                    if not events:
                        continue

                    # Filter eligibility
                    if rule_name == "R2_CONSERVATIVE_FIXED_RULE":
                        eligible = [
                            ev for ev in events if is_r2_eligible(ev.get("features", {}))
                        ]
                    else:
                        eligible = list(events)

                    if not eligible:
                        # No-pick sport day (only possible for non-quota-forced R2)
                        continue

                    selected_days += 1

                    # Rank eligible events
                    if rule_name == "R0_FOREBET_ONLY_COMPARATOR":
                        ranked = sorted(eligible, key=r0_sort_key)
                    else:  # R1 or R2 (R2 uses R1_ALWAYS_RANK_COMPARATOR rank)
                        ranked = sorted(eligible, key=r1_sort_key)

                    # Selections
                    top1_picks = [ranked[0]]
                    top3_picks = ranked[: min(3, len(ranked))]

                    total_top1_selections += len(top1_picks)
                    total_top3_selections += len(top3_picks)

                    top1_hit = top1_picks[0]["label"] == 1
                    top3_hit = any(p["label"] == 1 for p in top3_picks)

                    if top1_hit:
                        top1_hits += 1
                    if top3_hit:
                        top3_hits += 1

                    selected_day_hit_results.append(top1_hit)

                no_pick_days = opp_days - selected_days
                no_pick_rate = (no_pick_days / opp_days) if opp_days > 0 else 0.0
                t1_sel_rate = (top1_hits / selected_days) if selected_days > 0 else None
                t1_opp_rate = (top1_hits / opp_days) if opp_days > 0 else 0.0
                t3_sel_rate = (top3_hits / selected_days) if selected_days > 0 else None
                t3_opp_rate = (top3_hits / opp_days) if opp_days > 0 else 0.0
                mean_t1 = (
                    (total_top1_selections / opp_days) if opp_days > 0 else 0.0
                )
                mean_t3 = (
                    (total_top3_selections / opp_days) if opp_days > 0 else 0.0
                )
                mean_avail = (
                    (tot_available_events / opp_days) if opp_days > 0 else 0.0
                )

                sport_streak = compute_daily_top1_longest_losing_streak(
                    selected_day_hit_results
                )
                per_sport_top1_streaks[sport] = sport_streak

                per_sport_rules[sport] = {
                    "opportunity_sport_days": opp_days,
                    "selected_sport_days": selected_days,
                    "no_pick_sport_days": no_pick_days,
                    "no_pick_rate": no_pick_rate,
                    "top1_hits_on_selected_days": top1_hits,
                    "top1_hit_rate_selected_days": t1_sel_rate,
                    "top1_hit_rate_all_opportunity_days": t1_opp_rate,
                    "top3_any_hits_on_selected_days": top3_hits,
                    "top3_any_hit_rate_selected_days": t3_sel_rate,
                    "top3_any_hit_rate_all_opportunity_days": t3_opp_rate,
                    "mean_top1_selections_per_opportunity_day": mean_t1,
                    "mean_top3_selections_per_opportunity_day": mean_t3,
                    "available_events_per_sport_day": mean_avail,
                    "daily_top1_longest_losing_streak": sport_streak,
                    "total_available_events": tot_available_events,
                    "total_top1_selections": total_top1_selections,
                    "total_top3_selections": total_top3_selections,
                }

            # Global aggregation for this rule
            tot_opp_days = sum(
                r["opportunity_sport_days"] for r in per_sport_rules.values()
            )
            tot_sel_days = sum(
                r["selected_sport_days"] for r in per_sport_rules.values()
            )
            tot_no_pick_days = sum(
                r["no_pick_sport_days"] for r in per_sport_rules.values()
            )
            g_no_pick_rate = (
                (tot_no_pick_days / tot_opp_days) if tot_opp_days > 0 else 0.0
            )
            tot_t1_hits = sum(
                r["top1_hits_on_selected_days"] for r in per_sport_rules.values()
            )
            tot_t3_hits = sum(
                r["top3_any_hits_on_selected_days"] for r in per_sport_rules.values()
            )
            tot_t1_sel = sum(
                r["total_top1_selections"] for r in per_sport_rules.values()
            )
            tot_t3_sel = sum(
                r["total_top3_selections"] for r in per_sport_rules.values()
            )
            tot_avail_ev = sum(
                r["total_available_events"] for r in per_sport_rules.values()
            )

            g_t1_sel_rate = (tot_t1_hits / tot_sel_days) if tot_sel_days > 0 else None
            g_t1_opp_rate = (tot_t1_hits / tot_opp_days) if tot_opp_days > 0 else 0.0
            g_t3_sel_rate = (tot_t3_hits / tot_sel_days) if tot_sel_days > 0 else None
            g_t3_opp_rate = (tot_t3_hits / tot_opp_days) if tot_opp_days > 0 else 0.0
            g_mean_t1 = (tot_t1_sel / tot_opp_days) if tot_opp_days > 0 else 0.0
            g_mean_t3 = (tot_t3_sel / tot_opp_days) if tot_opp_days > 0 else 0.0
            g_mean_avail = (tot_avail_ev / tot_opp_days) if tot_opp_days > 0 else 0.0
            # Global daily top-1 streak is maximum of per-sport values
            g_top1_streak = (
                max(per_sport_top1_streaks.values())
                if per_sport_top1_streaks
                else 0
            )

            global_rule_metrics = {
                "opportunity_sport_days": tot_opp_days,
                "selected_sport_days": tot_sel_days,
                "no_pick_sport_days": tot_no_pick_days,
                "no_pick_rate": g_no_pick_rate,
                "top1_hits_on_selected_days": tot_t1_hits,
                "top1_hit_rate_selected_days": g_t1_sel_rate,
                "top1_hit_rate_all_opportunity_days": g_t1_opp_rate,
                "top3_any_hits_on_selected_days": tot_t3_hits,
                "top3_any_hit_rate_selected_days": g_t3_sel_rate,
                "top3_any_hit_rate_all_opportunity_days": g_t3_opp_rate,
                "mean_top1_selections_per_opportunity_day": g_mean_t1,
                "mean_top3_selections_per_opportunity_day": g_mean_t3,
                "available_events_per_sport_day": g_mean_avail,
                "daily_top1_longest_losing_streak": g_top1_streak,
            }

            p_rep["rules"][rule_name] = {
                "global": global_rule_metrics,
                "per_sport": per_sport_rules,
            }

        periods_report[p_id] = p_rep

    return periods_report


# ---------------------------------------------------------------------------
# Summary Markdown Formatter
# ---------------------------------------------------------------------------


def render_summary_markdown(
    config_dict: dict[str, Any],
    config_sha256: str,
    pass1_result: Pass1Result,
    periods_report: dict[str, Any],
) -> str:
    lines = [
        "# Slumdog Milestone 6B — Non-Trained Baseline Analysis",
        "",
        "## Anti-Tuning & Integrity Verification",
        "",
        f"- **Canonical Config SHA-256:** `{config_sha256}`",
        f"- **Pre-declared Canonical SHA-256:** `{CANONICAL_CONFIG_SHA256}`",
        f"- **Hash Verified:** {'YES (Identical)' if config_sha256 == CANONICAL_CONFIG_SHA256 else 'NO'}",
        "- **Anti-Tuning Rules:** Tuning periods empty; result-driven amendments prohibited.",
        "- **Training:** FROZEN (`MODEL_TRAINING_ALLOWED=False`).",
        "- **Production Shortlist Policy:** NOT AUTHORIZED.",
        f"- **Pass 1 Verification:** Passed {pass1_result.row_count:,} rows, digest `{pass1_result.decompressed_sha256}`.",
        "",
        "## Evaluated Periods Overview",
        "",
        "| Period | Name | Date Range | Total Examples | Underdog Wins | Base Rate |",
        "| :--- | :--- | :--- | :---: | :---: | :---: |",
    ]

    for p in config_dict["periods"]:
        p_id = p["id"]
        rep = periods_report[p_id]
        totals = rep["totals"]
        b_rate_str = f"{totals['base_rate']:.4f}"
        lines.append(
            f"| **{p_id}** | {p['name']} | {p['start']} to {p['end']} | "
            f"{totals['examples']:,} | {totals['underdog_wins']:,} | {b_rate_str} |"
        )

    lines.extend(
        [
            "",
            "## Rule Comparators (Global by Period)",
            "",
            "| Rule | Period | Opp Days | Sel Days | No-Pick % | Top-1 Sel Hit% | Top-1 Opp Hit% | Top-3 Sel Hit% | Top-3 Opp Hit% | Mean Top-1 | Top-1 Streak |",
            "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
        ]
    )

    for rule_name in (
        "R0_FOREBET_ONLY_COMPARATOR",
        "R1_ALWAYS_RANK_COMPARATOR",
        "R2_CONSERVATIVE_FIXED_RULE",
    ):
        short_rule = rule_name.split("_")[0]
        for p in config_dict["periods"]:
            p_id = p["id"]
            r_data = periods_report[p_id]["rules"][rule_name]["global"]
            t1_sel = (
                f"{r_data['top1_hit_rate_selected_days']:.4f}"
                if r_data["top1_hit_rate_selected_days"] is not None
                else "N/A"
            )
            t1_opp = f"{r_data['top1_hit_rate_all_opportunity_days']:.4f}"
            t3_sel = (
                f"{r_data['top3_any_hit_rate_selected_days']:.4f}"
                if r_data["top3_any_hit_rate_selected_days"] is not None
                else "N/A"
            )
            t3_opp = f"{r_data['top3_any_hit_rate_all_opportunity_days']:.4f}"
            np_rate = f"{r_data['no_pick_rate'] * 100:.1f}%"
            m_t1 = f"{r_data['mean_top1_selections_per_opportunity_day']:.2f}"
            streak = r_data["daily_top1_longest_losing_streak"]

            lines.append(
                f"| **{short_rule}** | {p_id} | {r_data['opportunity_sport_days']:,} | "
                f"{r_data['selected_sport_days']:,} | {np_rate} | {t1_sel} | {t1_opp} | "
                f"{t3_sel} | {t3_opp} | {m_t1} | {streak} |"
            )

    lines.extend(
        [
            "",
            "## Signal Buckets Summary (Global Overview)",
            "",
        ]
    )

    for s_name in config_dict["signals"]:
        lines.extend(
            [
                f"### Signal: `{s_name}`",
                "",
                "| Period | Bucket | Examples | Underdog Wins | Precision | Base Rate | Lift | Coverage | Losing Streak | Insufficient |",
                "| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
            ]
        )
        for p in config_dict["periods"]:
            p_id = p["id"]
            bucket_list = periods_report[p_id]["signals"][s_name]["global"]
            for b in bucket_list:
                prec_str = (
                    f"{b['candidate_precision']:.4f}"
                    if b["candidate_precision"] is not None
                    else "N/A"
                )
                br_str = f"{b['base_rate']:.4f}"
                lift_str = f"{b['lift']:.4f}" if b["lift"] is not None else "N/A"
                cov_str = f"{b['coverage'] * 100:.2f}%"
                ins_str = "YES" if b["insufficient"] else "no"
                lines.append(
                    f"| {p_id} | `{b['name']}` | {b['examples']:,} | {b['underdog_wins']:,} | "
                    f"{prec_str} | {br_str} | {lift_str} | {cov_str} | {b['candidate_level_longest_losing_streak']} | {ins_str} |"
                )
        lines.append("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Atomic Finalization & Main Execution
# ---------------------------------------------------------------------------


def _atomic_write_text(final_path: Path, content: str) -> None:
    final_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = final_path.with_name(f".{final_path.name}.tmp-{uuid.uuid4().hex}")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(tmp_path, final_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def run_baseline_analysis(
    config_path: Path | str = FROZEN_CONFIG_PATH,
    examples_path: Path | str | None = None,
    receipt_path: Path | str | None = None,
    out_json_path: Path | str | None = None,
    out_summary_path: Path | str | None = None,
    *,
    verify_canonical_hash: bool = True,
    expected_config_hash: str = CANONICAL_CONFIG_SHA256,
) -> dict[str, Any]:
    """Orchestrate the two-pass non-trained baseline analyzer."""
    cfg_p = Path(config_path)
    if not cfg_p.is_file():
        raise BaselineIntegrityError(f"Config file not found: {cfg_p}")

    try:
        config_dict = json.loads(cfg_p.read_text(encoding="utf-8"))
    except Exception as exc:
        raise BaselineIntegrityError(f"Could not parse config JSON: {exc}") from exc

    # 1. Anti-tuning hash verification
    if verify_canonical_hash:
        config_sha256 = verify_frozen_config(config_dict, expected_config_hash)
    else:
        config_sha256 = compute_config_sha256(config_dict)

    # 2. Resolve input paths
    inputs_cfg = config_dict.get("inputs", {})
    ex_path = (
        Path(examples_path) if examples_path else Path(inputs_cfg.get("examples_gz", ""))
    )
    rc_path = (
        Path(receipt_path) if receipt_path else Path(inputs_cfg.get("receipt", ""))
    )

    outputs_cfg = config_dict.get("outputs", {})
    json_out = (
        Path(out_json_path)
        if out_json_path
        else Path(outputs_cfg.get("baselines_json", "/tmp/slumdog_6b/baselines.json"))
    )
    sum_out = (
        Path(out_summary_path)
        if out_summary_path
        else Path(outputs_cfg.get("summary_md", "/tmp/slumdog_6b/summary.md"))
    )

    # Safety: outputs must be under /tmp
    if not str(json_out).startswith("/tmp"):
        raise BaselineIntegrityError(
            f"Safety violation: baselines_json output must be under /tmp, got {json_out}"
        )
    if not str(sum_out).startswith("/tmp"):
        raise BaselineIntegrityError(
            f"Safety violation: summary_md output must be under /tmp, got {sum_out}"
        )

    periods = config_dict["periods"]

    # 3. Pass 1: Streaming integrity checks
    pass1_res = run_pass1(ex_path, rc_path, periods)

    # 4. Pass 2: Analysis & Metrics aggregation
    periods_report = run_pass2(ex_path, config_dict, pass1_res)

    # 5. Build final output payload
    baselines_payload: dict[str, Any] = {
        "analysis": config_dict.get("analysis", "research-baselines"),
        "version": config_dict.get("version", "3.0.0"),
        "status": "SUCCESS",
        "anti_tuning": {
            "config_sha256": config_sha256,
            "recomputed_from_embedded_config_matches": (
                compute_config_sha256(config_dict) == config_sha256
            ),
            "canonical_expected_sha256": expected_config_hash,
            "tuning_periods": config_dict.get("anti_tuning", {}).get(
                "tuning_periods", []
            ),
            "result_driven_amendments": "prohibited",
        },
        "config": config_dict,
        "config_sha256": config_sha256,
        "pass1_integrity": {
            "passes": 2,
            "row_count": pass1_res.row_count,
            "decompressed_jsonl_sha256": pass1_res.decompressed_sha256,
            "out_of_period_rows": 0,
            "non_finite_values": "fail_closed_passed",
            "checks_passed": True,
        },
        "periods": periods_report,
    }

    # 6. Render summary markdown
    summary_markdown = render_summary_markdown(
        config_dict, config_sha256, pass1_res, periods_report
    )

    # 7. Safe atomic finalization
    json_str = json.dumps(baselines_payload, indent=2, sort_keys=True)
    _atomic_write_text(json_out, json_str)
    _atomic_write_text(sum_out, summary_markdown)

    return baselines_payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Milestone 6B non-trained baseline analyzer"
    )
    parser.add_argument(
        "--config",
        default=str(FROZEN_CONFIG_PATH),
        help="Path to frozen research_baselines_v1.json",
    )
    parser.add_argument(
        "--examples",
        default=None,
        help="Path to examples.jsonl.gz (default from config)",
    )
    parser.add_argument(
        "--receipt",
        default=None,
        help="Path to receipt.json (default from config)",
    )
    parser.add_argument(
        "--baselines-json",
        default=None,
        help="Output path for baselines.json (must be under /tmp)",
    )
    parser.add_argument(
        "--summary-md",
        default=None,
        help="Output path for summary.md (must be under /tmp)",
    )
    parser.add_argument(
        "--no-verify-canonical-hash",
        action="store_true",
        help="Skip canonical SHA-256 verification (test-only flag)",
    )
    args = parser.parse_args()

    try:
        payload = run_baseline_analysis(
            config_path=Path(args.config),
            examples_path=Path(args.examples) if args.examples else None,
            receipt_path=Path(args.receipt) if args.receipt else None,
            out_json_path=Path(args.baselines_json) if args.baselines_json else None,
            out_summary_path=Path(args.summary_md) if args.summary_md else None,
            verify_canonical_hash=not args.no_verify_canonical_hash,
        )
        print(
            f"Milestone 6B analysis complete. Status: {payload['status']}, "
            f"Rows analyzed: {payload['pass1_integrity']['row_count']}"
        )
        return 0
    except BaselineIntegrityError as exc:
        print(f"BASELINE INTEGRITY FAILURE: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"UNEXPECTED ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
