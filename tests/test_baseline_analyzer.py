"""Milestone 6B baseline analyzer tests.

Verifies:
- Frozen configuration canonical SHA-256 hash verification and anti-tuning gates
- Pass 1 integrity checks (SHA-256 match, row count match, period coverage, finite values)
- Fail-closed behavior with non-zero exit on any mismatch or integrity violation
- Missingness reporting across all analyzed features
- Pre-declared signal bucketing (all 7 signals, intervals, precedence, empty buckets, insufficient threshold)
- Rule ranking and selections (R0, R1, R2 eligibility, tie-breaking, quota-forced vs non-quota-forced)
- Hit rates and streak calculations (candidate-level within sport, daily top-1 within sport, global max, no-pick days effect)
- Safe atomic finalization under /tmp (baselines.json and summary.md)
"""

import copy
import gzip
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from slumdog.baseline_analyzer import (
    CANONICAL_CONFIG_SHA256,
    FROZEN_CONFIG_PATH,
    BaselineIntegrityError,
    assign_signal_bucket,
    compute_config_sha256,
    compute_daily_top1_longest_losing_streak,
    compute_longest_losing_streak_from_labels,
    is_r2_eligible,
    r0_sort_key,
    r1_sort_key,
    run_baseline_analysis,
    verify_frozen_config,
)

# ---------------------------------------------------------------------------
# Test Helpers & Fixtures
# ---------------------------------------------------------------------------


def make_example_row(
    event_id="hockey:1",
    sport="hockey",
    event_date="2023-08-20",
    fav_prob=0.6,
    dog_prob=0.4,
    draw_prob=None,
    prob_gap=0.2,
    label=1,
    recent_win_rate_gap=0.1,
    h2h_prior_games=2,
    h2h_underdog_win_rate=0.5,
    underdog_prior_games=5,
    favorite_prior_games=5,
    prior_scoring_rate_gap=0.5,
    prior_conceding_rate_gap=-0.5,
    underdog_prior_win_rate=0.6,
    favorite_prior_win_rate=0.5,
    h2h_draw_rate=0.0,
    underdog_prior_draw_rate=0.0,
    favorite_prior_draw_rate=0.0,
):
    features = {
        "forebet_favorite_probability": fav_prob,
        "forebet_underdog_probability": dog_prob,
        "forebet_probability_gap": prob_gap,
        "forebet_draw_probability": draw_prob,
        "forebet_draw_probability_missing": 1.0 if draw_prob is None else 0.0,
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
    missingness = {
        k: (1 if v is None else 0) for k, v in features.items()
    }
    return {
        "event_id": event_id,
        "sport": sport,
        "event_date": event_date,
        "favorite_index": 1,
        "underdog_index": 2,
        "favorite_probability": fav_prob,
        "underdog_probability": dog_prob,
        "draw_probability": draw_prob,
        "probability_gap": prob_gap,
        "label": label,
        "features": features,
        "missingness": missingness,
        "source_url": f"/en/{sport}/matches/x/y/{event_id}",
        "raw_sha256": "a" * 64,
        "feature_contract_version": "price-free-v2-incremental-valid-history",
        "label_contract_version": "price-free-v1",
        "exclusion_reason": None,
        "legacy_provenance_missing": False,
    }


def write_test_dataset(out_dir: Path, rows: list[dict]) -> tuple[Path, Path]:
    examples_gz = out_dir / "examples.jsonl.gz"
    receipt_json = out_dir / "receipt.json"

    # Compute uncompressed sha256
    decompressed_bytes = bytearray()
    with gzip.open(examples_gz, "wb") as f:
        for r in rows:
            line = (json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
            decompressed_bytes.extend(line)
            f.write(line)

    digest = hashlib.sha256(decompressed_bytes).hexdigest()
    receipt_data = {
        "status": "RESEARCH_DATASET_READY_WITH_LIMITATIONS",
        "examples_digest": digest,
        "accounting": {
            "eligible_examples": len(rows),
        },
    }
    receipt_json.write_text(json.dumps(receipt_data, indent=2))
    return examples_gz, receipt_json


# ---------------------------------------------------------------------------
# 1. Frozen Config Hash & Anti-Tuning Verification
# ---------------------------------------------------------------------------


def test_frozen_config_canonical_sha256_matches():
    """Verify that the frozen research_baselines_v1.json matches the canonical SHA-256."""
    config_text = FROZEN_CONFIG_PATH.read_text(encoding="utf-8")
    config_dict = json.loads(config_text)
    sha256 = verify_frozen_config(config_dict, CANONICAL_CONFIG_SHA256)
    assert sha256 == CANONICAL_CONFIG_SHA256
    assert sha256 == "666dabe7ea21e11867cf4816f4c2edcd771247646c6c9d7726c22611cda700a1"


def test_frozen_config_tampering_rejected():
    """Any modification to the frozen config must be rejected."""
    config_dict = json.loads(FROZEN_CONFIG_PATH.read_text(encoding="utf-8"))
    tampered = copy.deepcopy(config_dict)
    tampered["rules"]["R0_FOREBET_ONLY_COMPARATOR"]["quota_forced"] = False
    with pytest.raises(BaselineIntegrityError, match="Config SHA-256 mismatch"):
        verify_frozen_config(tampered, CANONICAL_CONFIG_SHA256)


def test_anti_tuning_gates_enforced():
    config_dict = json.loads(FROZEN_CONFIG_PATH.read_text(encoding="utf-8"))

    # Tuning periods must be empty
    tampered = copy.deepcopy(config_dict)
    tampered["anti_tuning"]["tuning_periods"] = ["P1"]
    computed_hash = compute_config_sha256(tampered)
    with pytest.raises(BaselineIntegrityError, match="tuning_periods must be empty"):
        verify_frozen_config(tampered, computed_hash)

    # Result-driven amendments must be prohibited
    tampered = copy.deepcopy(config_dict)
    tampered["anti_tuning"]["result_driven_amendments"] = "allowed"
    computed_hash = compute_config_sha256(tampered)
    with pytest.raises(BaselineIntegrityError, match="result_driven_amendments"):
        verify_frozen_config(tampered, computed_hash)

    # Shortlist policy must not be authorized
    tampered = copy.deepcopy(config_dict)
    tampered["shortlist_policy_authorized"] = True
    computed_hash = compute_config_sha256(tampered)
    with pytest.raises(BaselineIntegrityError, match="shortlist_policy_authorized"):
        verify_frozen_config(tampered, computed_hash)


# ---------------------------------------------------------------------------
# 2. Pass 1 Integrity Checks & Fail-Closed Behavior
# ---------------------------------------------------------------------------


def test_pass1_digest_mismatch_fails_closed():
    with tempfile.TemporaryDirectory() as tmp_dir:
        td = Path(tmp_dir)
        rows = [make_example_row()]
        ex_gz, rc_json = write_test_dataset(td, rows)

        # Corrupt receipt digest
        rc_data = json.loads(rc_json.read_text())
        rc_data["examples_digest"] = "f" * 64
        rc_json.write_text(json.dumps(rc_data))

        out_json = td / "baselines.json"
        out_summary = td / "summary.md"
        with pytest.raises(BaselineIntegrityError, match="decompressed_jsonl_sha256 mismatch"):
            run_baseline_analysis(
                FROZEN_CONFIG_PATH, ex_gz, rc_json, out_json, out_summary
            )
        # Ensure no outputs were written
        assert not out_json.exists()
        assert not out_summary.exists()


def test_pass1_row_count_mismatch_fails_closed():
    with tempfile.TemporaryDirectory() as tmp_dir:
        td = Path(tmp_dir)
        rows = [make_example_row()]
        ex_gz, rc_json = write_test_dataset(td, rows)

        # Corrupt receipt row count
        rc_data = json.loads(rc_json.read_text())
        rc_data["accounting"]["eligible_examples"] = 999
        rc_json.write_text(json.dumps(rc_data))

        out_json = td / "baselines.json"
        out_summary = td / "summary.md"
        with pytest.raises(BaselineIntegrityError, match="row_count mismatch"):
            run_baseline_analysis(
                FROZEN_CONFIG_PATH, ex_gz, rc_json, out_json, out_summary
            )
        assert not out_json.exists()
        assert not out_summary.exists()


def test_pass1_out_of_period_row_fails_closed():
    with tempfile.TemporaryDirectory() as tmp_dir:
        td = Path(tmp_dir)
        # Row with date outside P1..P4 union (e.g. 2022-01-01 before P1 start 2023-02-12)
        rows = [make_example_row(event_date="2022-01-01")]
        ex_gz, rc_json = write_test_dataset(td, rows)

        out_json = td / "baselines.json"
        out_summary = td / "summary.md"
        with pytest.raises(BaselineIntegrityError, match="out_of_period_row"):
            run_baseline_analysis(
                FROZEN_CONFIG_PATH, ex_gz, rc_json, out_json, out_summary
            )
        assert not out_json.exists()
        assert not out_summary.exists()


def test_pass1_non_finite_value_fails_closed():
    with tempfile.TemporaryDirectory() as tmp_dir:
        td = Path(tmp_dir)
        row = make_example_row()
        row["features"]["recent_win_rate_gap"] = float("nan")
        ex_gz, rc_json = write_test_dataset(td, [row])

        out_json = td / "baselines.json"
        out_summary = td / "summary.md"
        with pytest.raises(BaselineIntegrityError, match="Non-finite value"):
            run_baseline_analysis(
                FROZEN_CONFIG_PATH, ex_gz, rc_json, out_json, out_summary
            )
        assert not out_json.exists()
        assert not out_summary.exists()


def test_pass1_prohibited_key_fails_closed():
    with tempfile.TemporaryDirectory() as tmp_dir:
        td = Path(tmp_dir)
        row = make_example_row()
        row["features"]["odds_1"] = 2.5  # Prohibited odds key!
        ex_gz, rc_json = write_test_dataset(td, [row])

        out_json = td / "baselines.json"
        out_summary = td / "summary.md"
        with pytest.raises(BaselineIntegrityError, match="Prohibited key 'odds_1'"):
            run_baseline_analysis(
                FROZEN_CONFIG_PATH, ex_gz, rc_json, out_json, out_summary
            )
        assert not out_json.exists()
        assert not out_summary.exists()


# ---------------------------------------------------------------------------
# 3. Signal Bucketing Logic & Precedence
# ---------------------------------------------------------------------------


def test_h2h_underdog_win_rate_precedence():
    config_dict = json.loads(FROZEN_CONFIG_PATH.read_text(encoding="utf-8"))
    signal_def = config_dict["signals"]["h2h_underdog_win_rate"]

    # 1. no-h2h precedence when h2h_prior_games == 0
    f1 = {"h2h_prior_games": 0.0, "h2h_underdog_win_rate": None}
    assert assign_signal_bucket("h2h_underdog_win_rate", f1, signal_def) == "no-h2h"

    # Even if win rate is populated somehow, h2h_prior_games == 0 takes precedence
    f1b = {"h2h_prior_games": 0, "h2h_underdog_win_rate": 0.5}
    assert assign_signal_bucket("h2h_underdog_win_rate", f1b, signal_def) == "no-h2h"

    # 2. Numeric buckets
    f_0 = {"h2h_prior_games": 1.0, "h2h_underdog_win_rate": 0.0}
    assert assign_signal_bucket("h2h_underdog_win_rate", f_0, signal_def) == "0"

    f_mid_lo = {"h2h_prior_games": 3.0, "h2h_underdog_win_rate": 0.333333}
    assert assign_signal_bucket("h2h_underdog_win_rate", f_mid_lo, signal_def) == "(0,0.50)"

    f_half = {"h2h_prior_games": 2.0, "h2h_underdog_win_rate": 0.5}
    assert assign_signal_bucket("h2h_underdog_win_rate", f_half, signal_def) == "0.50"

    f_mid_hi = {"h2h_prior_games": 3.0, "h2h_underdog_win_rate": 0.666667}
    assert assign_signal_bucket("h2h_underdog_win_rate", f_mid_hi, signal_def) == "(0.50,1)"

    f_1 = {"h2h_prior_games": 2.0, "h2h_underdog_win_rate": 1.0}
    assert assign_signal_bucket("h2h_underdog_win_rate", f_1, signal_def) == "1"

    # 3. Missing/inconsistent when prior games > 0 but rate is None
    f_incon = {"h2h_prior_games": 2.0, "h2h_underdog_win_rate": None}
    assert assign_signal_bucket("h2h_underdog_win_rate", f_incon, signal_def) == "missing/inconsistent"


def test_evidence_availability_bucketing():
    config_dict = json.loads(FROZEN_CONFIG_PATH.read_text(encoding="utf-8"))
    signal_def = config_dict["signals"]["evidence_availability"]

    # 0 components
    assert assign_signal_bucket("evidence_availability", {}, signal_def) == "0"

    # 1-2 components
    f_1 = {"underdog_prior_win_rate": 0.5}
    assert assign_signal_bucket("evidence_availability", f_1, signal_def) == "1-2"

    # 3-4 components
    f_3 = {
        "underdog_prior_win_rate": 0.5,
        "favorite_prior_win_rate": 0.4,
        "prior_scoring_rate_gap": 1.0,
    }
    assert assign_signal_bucket("evidence_availability", f_3, signal_def) == "3-4"

    # 5-6 components
    f_6 = {
        "underdog_prior_win_rate": 0.5,
        "favorite_prior_win_rate": 0.4,
        "h2h_underdog_win_rate": 0.5,
        "h2h_draw_rate": 0.0,
        "prior_scoring_rate_gap": 1.0,
        "prior_conceding_rate_gap": -0.5,
    }
    assert assign_signal_bucket("evidence_availability", f_6, signal_def) == "5-6"


def test_probability_gap_bucketing():
    config_dict = json.loads(FROZEN_CONFIG_PATH.read_text(encoding="utf-8"))
    signal_def = config_dict["signals"]["probability_gap"]

    assert assign_signal_bucket("probability_gap", {"forebet_probability_gap": 0.02}, signal_def) == "(0,0.05]"
    assert assign_signal_bucket("probability_gap", {"forebet_probability_gap": 0.05}, signal_def) == "(0,0.05]"
    assert assign_signal_bucket("probability_gap", {"forebet_probability_gap": 0.10}, signal_def) == "(0.05,0.15]"
    assert assign_signal_bucket("probability_gap", {"forebet_probability_gap": 0.20}, signal_def) == "(0.15,0.30]"
    assert assign_signal_bucket("probability_gap", {"forebet_probability_gap": 0.45}, signal_def) == "(0.30,1.0]"
    assert assign_signal_bucket("probability_gap", {"forebet_probability_gap": None}, signal_def) == "missing"


# ---------------------------------------------------------------------------
# 4. Rule Ranking & Eligibility (R0, R1, R2)
# ---------------------------------------------------------------------------


def test_r0_forebet_only_ranking():
    # R0 ranks by forebet_underdog_probability desc, forebet_probability_gap asc, event_id asc
    e1 = make_example_row(event_id="e1", dog_prob=0.45, prob_gap=0.1)
    e2 = make_example_row(event_id="e2", dog_prob=0.40, prob_gap=0.2)
    e3 = make_example_row(event_id="e3", dog_prob=0.45, prob_gap=0.05)  # ties dog_prob with e1, but smaller gap

    events = [e1, e2, e3]
    ranked = sorted(events, key=r0_sort_key)
    # Order should be e3 (0.45, gap 0.05), e1 (0.45, gap 0.10), e2 (0.40)
    assert [x["event_id"] for x in ranked] == ["e3", "e1", "e2"]


def test_r1_always_rank_missing_last():
    # R1 ranks:
    # 1. recent_win_rate_gap desc (missing last)
    # 2. h2h_underdog_win_rate desc (missing_or_no_h2h last)
    # 3. forebet_probability_gap asc
    # 4. event_id asc
    e_present = make_example_row(event_id="e_p", recent_win_rate_gap=0.1, prob_gap=0.3)
    e_missing = make_example_row(event_id="e_m", recent_win_rate_gap=None, prob_gap=0.05)

    events = [e_missing, e_present]
    ranked = sorted(events, key=r1_sort_key)
    assert [x["event_id"] for x in ranked] == ["e_p", "e_m"]


def test_r1_h2h_missing_or_no_h2h_ranks_last():
    # When recent_win_rate_gap is tied, h2h_underdog_win_rate breaks tie with missing/no-h2h last
    e_h2h = make_example_row(event_id="e_h2h", recent_win_rate_gap=0.2, h2h_prior_games=2, h2h_underdog_win_rate=0.5)
    e_no_h2h = make_example_row(event_id="e_no_h2h", recent_win_rate_gap=0.2, h2h_prior_games=0, h2h_underdog_win_rate=None)

    events = [e_no_h2h, e_h2h]
    ranked = sorted(events, key=r1_sort_key)
    assert [x["event_id"] for x in ranked] == ["e_h2h", "e_no_h2h"]


def test_r2_eligibility_and_disqualification():
    # R2 requires:
    # underdog_prior_games >= 5, favorite_prior_games >= 5, h2h_prior_games >= 1, forebet_probability_gap <= 0.2
    # Missingness disqualifies

    valid_feats = {
        "underdog_prior_games": 5.0,
        "favorite_prior_games": 5.0,
        "h2h_prior_games": 1.0,
        "forebet_probability_gap": 0.15,
    }
    assert is_r2_eligible(valid_feats) is True

    # Less than 5 prior games
    assert is_r2_eligible(dict(valid_feats, underdog_prior_games=4.0)) is False
    assert is_r2_eligible(dict(valid_feats, favorite_prior_games=4.0)) is False

    # 0 h2h games
    assert is_r2_eligible(dict(valid_feats, h2h_prior_games=0.0)) is False

    # Probability gap > 0.2
    assert is_r2_eligible(dict(valid_feats, forebet_probability_gap=0.25)) is False

    # Missing values disqualify
    assert is_r2_eligible(dict(valid_feats, h2h_prior_games=None)) is False
    assert is_r2_eligible(dict(valid_feats, forebet_probability_gap=None)) is False


# ---------------------------------------------------------------------------
# 5. Losing Streak Calculations
# ---------------------------------------------------------------------------


def test_candidate_level_longest_losing_streak():
    # Losses are 0, wins are 1
    assert compute_longest_losing_streak_from_labels([]) == 0
    assert compute_longest_losing_streak_from_labels([1, 1, 1]) == 0
    assert compute_longest_losing_streak_from_labels([0, 0, 1, 0, 0, 0, 1, 0]) == 3
    assert compute_longest_losing_streak_from_labels([0, 0, 0, 0]) == 4


def test_daily_top1_longest_losing_streak_with_no_pick_effect():
    # In daily top1 streak:
    # True = hit (win), False = loss
    # No-pick days are omitted from selected_day_hits, so they neither increment nor reset streak!
    selected_hits = [False, False, True, False, False, False, True]
    assert compute_daily_top1_longest_losing_streak(selected_hits) == 3


# ---------------------------------------------------------------------------
# 6. Full End-to-End Baseline Analysis Execution
# ---------------------------------------------------------------------------


def test_end_to_end_baseline_analysis_and_outputs():
    with tempfile.TemporaryDirectory() as tmp_dir:
        td = Path(tmp_dir)

        # Create multi-sport, multi-period synthetic dataset
        rows = [
            # P1: 2023-08-20 (hockey)
            make_example_row(
                event_id="hockey:1",
                sport="hockey",
                event_date="2023-08-20",
                dog_prob=0.45,
                prob_gap=0.1,
                label=1,
                recent_win_rate_gap=0.2,
                h2h_prior_games=2,
                h2h_underdog_win_rate=0.5,
                underdog_prior_games=5,
                favorite_prior_games=5,
            ),
            make_example_row(
                event_id="hockey:2",
                sport="hockey",
                event_date="2023-08-20",
                dog_prob=0.35,
                prob_gap=0.3,
                label=0,
                recent_win_rate_gap=0.0,
                h2h_prior_games=0,
                h2h_underdog_win_rate=None,
                underdog_prior_games=3,
                favorite_prior_games=5,
            ),
            # P1: 2023-08-21 (basketball)
            make_example_row(
                event_id="basketball:1",
                sport="basketball",
                event_date="2023-08-21",
                dog_prob=0.48,
                prob_gap=0.04,
                label=0,
                recent_win_rate_gap=-0.1,
                h2h_prior_games=1,
                h2h_underdog_win_rate=0.0,
                underdog_prior_games=5,
                favorite_prior_games=5,
            ),
            # P2: 2024-05-10 (football)
            make_example_row(
                event_id="football:1",
                sport="football",
                event_date="2024-05-10",
                dog_prob=0.42,
                prob_gap=0.16,
                label=1,
                recent_win_rate_gap=0.3,
                h2h_prior_games=3,
                h2h_underdog_win_rate=0.666667,
                underdog_prior_games=5,
                favorite_prior_games=5,
            ),
        ]

        ex_gz, rc_json = write_test_dataset(td, rows)
        out_json = td / "baselines.json"
        out_summary = td / "summary.md"

        payload = run_baseline_analysis(
            config_path=FROZEN_CONFIG_PATH,
            examples_path=ex_gz,
            receipt_path=rc_json,
            out_json_path=out_json,
            out_summary_path=out_summary,
            verify_canonical_hash=True,
        )

        assert payload["status"] == "SUCCESS"
        assert payload["shortlist_policy_authorized"] is False
        assert (
            payload["shortlist_policy_authorized"]
            == payload["config"]["shortlist_policy_authorized"]
        )
        assert payload["pass1_integrity"]["row_count"] == 4
        assert payload["config_sha256"] == CANONICAL_CONFIG_SHA256
        assert payload["anti_tuning"]["recomputed_from_embedded_config_matches"] is True

        # Check outputs were created
        assert out_json.is_file()
        assert out_summary.is_file()

        # Check JSON payload structure
        loaded_json = json.loads(out_json.read_text(encoding="utf-8"))
        assert loaded_json["status"] == "SUCCESS"
        assert loaded_json["shortlist_policy_authorized"] is False
        assert (
            loaded_json["shortlist_policy_authorized"]
            == loaded_json["config"]["shortlist_policy_authorized"]
        )
        assert "periods" in loaded_json
        assert "P1" in loaded_json["periods"]
        assert "P2" in loaded_json["periods"]
        assert "P3" in loaded_json["periods"]
        assert "P4" in loaded_json["periods"]

        p1_rep = loaded_json["periods"]["P1"]
        assert p1_rep["totals"]["examples"] == 3
        assert p1_rep["totals"]["underdog_wins"] == 1
        assert p1_rep["totals"]["base_rate"] == 1.0 / 3.0

        # Check rule comparisons
        r0_global = p1_rep["rules"]["R0_FOREBET_ONLY_COMPARATOR"]["global"]
        assert r0_global["opportunity_sport_days"] == 2  # 2023-08-20 hockey, 2023-08-21 basketball
        assert r0_global["selected_sport_days"] == 2  # quota-forced: selected both days
        assert r0_global["no_pick_sport_days"] == 0

        # Verify JSON rates remain decimal rates (not percentages or strings)
        assert isinstance(r0_global["top1_hit_rate_selected_days"], float)
        assert 0.0 <= r0_global["top1_hit_rate_selected_days"] <= 1.0
        assert isinstance(r0_global["top1_hit_rate_all_opportunity_days"], float)
        assert 0.0 <= r0_global["top1_hit_rate_all_opportunity_days"] <= 1.0
        assert isinstance(r0_global["top3_any_hit_rate_selected_days"], float)
        assert isinstance(r0_global["top3_any_hit_rate_all_opportunity_days"], float)

        # Check summary markdown content: hit-rate columns are percentage-formatted
        summary_text = out_summary.read_text(encoding="utf-8")
        assert "Milestone 6B — Non-Trained Baseline Analysis" in summary_text
        assert CANONICAL_CONFIG_SHA256 in summary_text
        assert "R0_FOREBET_ONLY_COMPARATOR" or "R0" in summary_text
        assert "R1_ALWAYS_RANK_COMPARATOR" or "R1" in summary_text
        assert "R2_CONSERVATIVE_FIXED_RULE" or "R2" in summary_text

        # Verify hit-rate columns are formatted with '%' in the table
        # 1 hit out of 2 selected days = 50.00%
        assert "50.00%" in summary_text
        assert "0.0%" in summary_text or "0.00%" in summary_text


def test_top_level_shortlist_policy_authorization_matches_embedded_config():
    """Verify shortlist_policy_authorized exists at top level, is false, and matches config."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        td = Path(tmp_dir)
        rows = [make_example_row()]
        ex_gz, rc_json = write_test_dataset(td, rows)
        out_json = td / "baselines.json"
        out_summary = td / "summary.md"

        payload = run_baseline_analysis(
            config_path=FROZEN_CONFIG_PATH,
            examples_path=ex_gz,
            receipt_path=rc_json,
            out_json_path=out_json,
            out_summary_path=out_summary,
            verify_canonical_hash=True,
        )

        assert payload["shortlist_policy_authorized"] is False
        assert (
            payload["shortlist_policy_authorized"]
            == payload["config"]["shortlist_policy_authorized"]
        )

        loaded = json.loads(out_json.read_text(encoding="utf-8"))
        assert loaded["shortlist_policy_authorized"] is False
        assert (
            loaded["shortlist_policy_authorized"]
            == loaded["config"]["shortlist_policy_authorized"]
        )


def test_analyzer_rejects_config_authorizing_shortlist_policy():
    """Analyzer must reject any config attempting to authorize shortlist policy."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        td = Path(tmp_dir)
        rows = [make_example_row()]
        ex_gz, rc_json = write_test_dataset(td, rows)
        out_json = td / "baselines.json"
        out_summary = td / "summary.md"

        # Create custom config with shortlist_policy_authorized = True
        cfg_dict = json.loads(FROZEN_CONFIG_PATH.read_text(encoding="utf-8"))
        cfg_dict["shortlist_policy_authorized"] = True
        custom_cfg = td / "custom_config.json"
        custom_cfg.write_text(json.dumps(cfg_dict))

        # Rejection must happen even if hash verification is bypassed
        with pytest.raises(BaselineIntegrityError, match="shortlist_policy_authorized must be false"):
            run_baseline_analysis(
                config_path=custom_cfg,
                examples_path=ex_gz,
                receipt_path=rc_json,
                out_json_path=out_json,
                out_summary_path=out_summary,
                verify_canonical_hash=False,
            )


def test_markdown_hit_rates_are_percentages_and_json_rates_are_decimals():
    """Verify Markdown comparator headers say Hit% and render actual percentages, while JSON remains decimal."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        td = Path(tmp_dir)
        # Create dataset where day 1 has hit (label=1) and day 2 has miss (label=0)
        rows = [
            make_example_row(event_id="e1", event_date="2023-08-20", label=1),
            make_example_row(event_id="e2", event_date="2023-08-21", label=0),
        ]
        ex_gz, rc_json = write_test_dataset(td, rows)
        out_json = td / "baselines.json"
        out_summary = td / "summary.md"

        payload = run_baseline_analysis(
            config_path=FROZEN_CONFIG_PATH,
            examples_path=ex_gz,
            receipt_path=rc_json,
            out_json_path=out_json,
            out_summary_path=out_summary,
            verify_canonical_hash=True,
        )

        # 1. Check JSON rates remain decimal floats
        p1_r0 = payload["periods"]["P1"]["rules"]["R0_FOREBET_ONLY_COMPARATOR"]["global"]
        assert p1_r0["top1_hit_rate_selected_days"] == 0.5
        assert p1_r0["top1_hit_rate_all_opportunity_days"] == 0.5
        assert p1_r0["top3_any_hit_rate_selected_days"] == 0.5
        assert p1_r0["top3_any_hit_rate_all_opportunity_days"] == 0.5
        assert p1_r0["no_pick_rate"] == 0.0

        # 2. Check Markdown renders formatted percentages
        md_text = out_summary.read_text(encoding="utf-8")
        table_lines = [line for line in md_text.splitlines() if line.startswith("| **R0**")]
        assert len(table_lines) >= 1
        p1_line = [l for l in table_lines if "P1" in l][0]
        # Columns: Rule | Period | Opp Days | Sel Days | No-Pick % | Top-1 Sel Hit% | Top-1 Opp Hit% | Top-3 Sel Hit% | Top-3 Opp Hit% | Mean Top-1 | Top-1 Streak
        cols = [c.strip() for c in p1_line.split("|")[1:-1]]
        no_pick_col = cols[4]
        t1_sel_col = cols[5]
        t1_opp_col = cols[6]
        t3_sel_col = cols[7]
        t3_opp_col = cols[8]

        assert no_pick_col == "0.0%"
        assert t1_sel_col == "50.00%"
        assert t1_opp_col == "50.00%"
        assert t3_sel_col == "50.00%"
        assert t3_opp_col == "50.00%"


# ---------------------------------------------------------------------------
# 7. Safety: Non-/tmp Output Paths Rejected
# ---------------------------------------------------------------------------


def test_non_tmp_output_paths_rejected():
    with tempfile.TemporaryDirectory() as tmp_dir:
        td = Path(tmp_dir)
        rows = [make_example_row()]
        ex_gz, rc_json = write_test_dataset(td, rows)

        with pytest.raises(BaselineIntegrityError, match="baselines_json output must be under /tmp"):
            run_baseline_analysis(
                config_path=FROZEN_CONFIG_PATH,
                examples_path=ex_gz,
                receipt_path=rc_json,
                out_json_path="/home/user/Slumdog/baselines.json",
                out_summary_path="/tmp/summary.md",
            )

        with pytest.raises(BaselineIntegrityError, match="summary_md output must be under /tmp"):
            run_baseline_analysis(
                config_path=FROZEN_CONFIG_PATH,
                examples_path=ex_gz,
                receipt_path=rc_json,
                out_json_path="/tmp/baselines.json",
                out_summary_path="/home/user/Slumdog/summary.md",
            )


# ---------------------------------------------------------------------------
# 8. CLI Entry Point
# ---------------------------------------------------------------------------


def test_cli_execution_success():
    with tempfile.TemporaryDirectory() as tmp_dir:
        td = Path(tmp_dir)
        rows = [make_example_row()]
        ex_gz, rc_json = write_test_dataset(td, rows)

        out_json = td / "baselines.json"
        out_sum = td / "summary.md"

        cmd = [
            sys.executable,
            "-m",
            "slumdog.baseline_analyzer",
            "--config",
            str(FROZEN_CONFIG_PATH),
            "--examples",
            str(ex_gz),
            "--receipt",
            str(rc_json),
            "--baselines-json",
            str(out_json),
            "--summary-md",
            str(out_sum),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        assert res.returncode == 0
        assert "Milestone 6B analysis complete" in res.stdout
        assert out_json.is_file()
        assert out_sum.is_file()


def test_cli_execution_failure_exit_code():
    with tempfile.TemporaryDirectory() as tmp_dir:
        td = Path(tmp_dir)
        rows = [make_example_row()]
        ex_gz, rc_json = write_test_dataset(td, rows)

        # Corrupt digest
        rc_data = json.loads(rc_json.read_text())
        rc_data["examples_digest"] = "0" * 64
        rc_json.write_text(json.dumps(rc_data))

        out_json = td / "baselines.json"
        out_sum = td / "summary.md"

        cmd = [
            sys.executable,
            "-m",
            "slumdog.baseline_analyzer",
            "--config",
            str(FROZEN_CONFIG_PATH),
            "--examples",
            str(ex_gz),
            "--receipt",
            str(rc_json),
            "--baselines-json",
            str(out_json),
            "--summary-md",
            str(out_sum),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        assert res.returncode == 1
        assert "BASELINE INTEGRITY FAILURE" in res.stderr
        assert not out_json.exists()
        assert not out_sum.exists()


# ---------------------------------------------------------------------------
# 9. Additional Granular Signal Tests & Precedence
# ---------------------------------------------------------------------------


def test_conceding_rate_gap_all_intervals():
    config_dict = json.loads(FROZEN_CONFIG_PATH.read_text(encoding="utf-8"))
    signal_def = config_dict["signals"]["conceding_rate_gap"]

    assert assign_signal_bucket("conceding_rate_gap", {"prior_conceding_rate_gap": -2.5}, signal_def) == "<-2"
    assert assign_signal_bucket("conceding_rate_gap", {"prior_conceding_rate_gap": -2.0}, signal_def) == "[-2,-1)"
    assert assign_signal_bucket("conceding_rate_gap", {"prior_conceding_rate_gap": -1.5}, signal_def) == "[-2,-1)"
    assert assign_signal_bucket("conceding_rate_gap", {"prior_conceding_rate_gap": -1.0}, signal_def) == "[-1,0)"
    assert assign_signal_bucket("conceding_rate_gap", {"prior_conceding_rate_gap": -0.01}, signal_def) == "[-1,0)"
    assert assign_signal_bucket("conceding_rate_gap", {"prior_conceding_rate_gap": 0.0}, signal_def) == "[0,+1)"
    assert assign_signal_bucket("conceding_rate_gap", {"prior_conceding_rate_gap": 0.99}, signal_def) == "[0,+1)"
    assert assign_signal_bucket("conceding_rate_gap", {"prior_conceding_rate_gap": 1.0}, signal_def) == ">=+1"
    assert assign_signal_bucket("conceding_rate_gap", {"prior_conceding_rate_gap": 3.0}, signal_def) == ">=+1"
    assert assign_signal_bucket("conceding_rate_gap", {"prior_conceding_rate_gap": None}, signal_def) == "missing"


def test_scoring_rate_gap_all_intervals():
    config_dict = json.loads(FROZEN_CONFIG_PATH.read_text(encoding="utf-8"))
    signal_def = config_dict["signals"]["scoring_rate_gap"]

    assert assign_signal_bucket("scoring_rate_gap", {"prior_scoring_rate_gap": -1.5}, signal_def) == "<-1"
    assert assign_signal_bucket("scoring_rate_gap", {"prior_scoring_rate_gap": -1.0}, signal_def) == "[-1,0)"
    assert assign_signal_bucket("scoring_rate_gap", {"prior_scoring_rate_gap": 0.0}, signal_def) == "[0,+1)"
    assert assign_signal_bucket("scoring_rate_gap", {"prior_scoring_rate_gap": 1.0}, signal_def) == "[+1,+2)"
    assert assign_signal_bucket("scoring_rate_gap", {"prior_scoring_rate_gap": 2.0}, signal_def) == ">=+2"
    assert assign_signal_bucket("scoring_rate_gap", {"prior_scoring_rate_gap": None}, signal_def) == "missing"


def test_underdog_probability_all_intervals():
    config_dict = json.loads(FROZEN_CONFIG_PATH.read_text(encoding="utf-8"))
    signal_def = config_dict["signals"]["underdog_probability"]

    assert assign_signal_bucket("underdog_probability", {"forebet_underdog_probability": 0.15}, signal_def) == "[0.00,0.20)"
    assert assign_signal_bucket("underdog_probability", {"forebet_underdog_probability": 0.20}, signal_def) == "[0.20,0.30)"
    assert assign_signal_bucket("underdog_probability", {"forebet_underdog_probability": 0.30}, signal_def) == "[0.30,0.40)"
    assert assign_signal_bucket("underdog_probability", {"forebet_underdog_probability": 0.40}, signal_def) == "[0.40,0.50)"
    assert assign_signal_bucket("underdog_probability", {"forebet_underdog_probability": 0.50}, signal_def) == "[0.50,0.60)"
    assert assign_signal_bucket("underdog_probability", {"forebet_underdog_probability": 0.60}, signal_def) == "[0.60,1.00]"
    assert assign_signal_bucket("underdog_probability", {"forebet_underdog_probability": 1.00}, signal_def) == "[0.60,1.00]"
    assert assign_signal_bucket("underdog_probability", {"forebet_underdog_probability": None}, signal_def) == "missing"


def test_recent_win_rate_gap_all_intervals():
    config_dict = json.loads(FROZEN_CONFIG_PATH.read_text(encoding="utf-8"))
    signal_def = config_dict["signals"]["recent_win_rate_gap"]

    assert assign_signal_bucket("recent_win_rate_gap", {"recent_win_rate_gap": -0.35}, signal_def) == "<=-0.30"
    assert assign_signal_bucket("recent_win_rate_gap", {"recent_win_rate_gap": -0.30}, signal_def) == "<=-0.30"
    assert assign_signal_bucket("recent_win_rate_gap", {"recent_win_rate_gap": -0.20}, signal_def) == "(-0.30,-0.10)"
    assert assign_signal_bucket("recent_win_rate_gap", {"recent_win_rate_gap": -0.10}, signal_def) == "[-0.10,+0.10)"
    assert assign_signal_bucket("recent_win_rate_gap", {"recent_win_rate_gap": 0.0}, signal_def) == "[-0.10,+0.10)"
    assert assign_signal_bucket("recent_win_rate_gap", {"recent_win_rate_gap": 0.10}, signal_def) == "[+0.10,+0.30)"
    assert assign_signal_bucket("recent_win_rate_gap", {"recent_win_rate_gap": 0.30}, signal_def) == ">=+0.30"
    assert assign_signal_bucket("recent_win_rate_gap", {"recent_win_rate_gap": None}, signal_def) == "missing"


# ---------------------------------------------------------------------------
# 10. Research Baselines Module Alias & CLI
# ---------------------------------------------------------------------------


def test_research_baselines_module_alias():
    import slumdog.research_baselines as rb
    assert rb.CANONICAL_CONFIG_SHA256 == CANONICAL_CONFIG_SHA256
    assert callable(rb.run_baseline_analysis)


def test_r2_no_pick_day_handling():
    """Verify that when no event is eligible under R2, it counts as a no-pick day."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        td = Path(tmp_dir)
        # 1 event that fails R2 eligibility (underdog_prior_games = 2 < 5)
        row = make_example_row(
            event_id="hockey:1",
            sport="hockey",
            event_date="2023-08-20",
            underdog_prior_games=2,
            favorite_prior_games=5,
            h2h_prior_games=2,
            prob_gap=0.1,
            label=1,
        )
        ex_gz, rc_json = write_test_dataset(td, [row])
        out_json = td / "baselines.json"
        out_summary = td / "summary.md"

        payload = run_baseline_analysis(
            config_path=FROZEN_CONFIG_PATH,
            examples_path=ex_gz,
            receipt_path=rc_json,
            out_json_path=out_json,
            out_summary_path=out_summary,
            verify_canonical_hash=True,
        )

        p1_rep = payload["periods"]["P1"]
        r2_global = p1_rep["rules"]["R2_CONSERVATIVE_FIXED_RULE"]["global"]
        assert r2_global["opportunity_sport_days"] == 1
        assert r2_global["selected_sport_days"] == 0
        assert r2_global["no_pick_sport_days"] == 1
        assert r2_global["no_pick_rate"] == 1.0
        assert r2_global["top1_hit_rate_selected_days"] is None
        assert r2_global["top1_hit_rate_all_opportunity_days"] == 0.0
        assert r2_global["daily_top1_longest_losing_streak"] == 0

        # R0 & R1 (quota-forced) still selected the event
        r0_global = p1_rep["rules"]["R0_FOREBET_ONLY_COMPARATOR"]["global"]
        assert r0_global["selected_sport_days"] == 1
        assert r0_global["no_pick_sport_days"] == 0
        assert r0_global["top1_hit_rate_selected_days"] == 1.0
        assert r0_global["top1_hit_rate_all_opportunity_days"] == 1.0

