"""Tests for the R2 exclusion-reason recovery (2026-09-08).

Between 56% and 82% of every considered pool was excluded before grading under a
single label, ``FEATURE_INCOMPLETE_OR_R2_INELIGIBLE`` — the ``OR`` is in the
name. ``is_r2_eligible`` distinguishes five separate causes internally and
returns a bare ``bool``, and the excluded rows persisted only six keys, so the
cause was not merely unrecorded but **unrecoverable** from committed evidence.

That made a real question permanently unanswerable: ``forebet_probability_gap
<= 0.2`` is the R2 gate, and the records that *failed* it are exactly the
population needed to judge whether 0.2 is the right number. They were thrown
away.

These tests cover the recovery, and three properties that make it safe:

1. **It records, it does not decide.** ``is_r2_eligible`` is untouched and
   remains the single frozen rule; agreement between rule and decomposition is
   asserted over a value matrix rather than assumed.
2. **It cannot perturb ``decision_digest``.** Proven against real committed
   manifests for all four settled dates, with a negative control that shows the
   proof is sensitive rather than vacuous.
3. **It cannot be misread.** ``prior_games == 0.0`` with missingness flag ``0``
   means "no history in the bounded window", i.e. absent data — not a cutoff
   that was too tight. The flags are recorded so the two stay distinguishable.
"""

from __future__ import annotations

import glob
import hashlib
import importlib.util
import json
import tempfile
from pathlib import Path

import pytest

from slumdog.baseline_analyzer import (
    FROZEN_R2_RULE_NAME,
    R2_ELIGIBILITY_SPEC,
    canonical_json_bytes,
    is_r2_eligible,
    r2_ineligibility_reason,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# The six fields ``pool_for_digest`` projects. Anything outside this tuple
# cannot reach ``decision_digest`` — that is the property this change relies on,
# and it is the same mechanism the rank-4+ identity fix used.
PROJECTED_FIELDS = (
    "sport", "event_id", "event_date",
    "considered_status", "eligible", "rank_within_sport_day",
)

R2_FEATURES = tuple(feature for feature, _op, _value in R2_ELIGIBILITY_SPEC)

# Dates whose settlement artifacts are committed, i.e. real evidence.
SETTLED_MANIFESTS = sorted(
    glob.glob(str(REPO_ROOT / "data/reports/shadow/2026-*/[0-9a-f]*/manifest.json"))
)


def _canonical_sha256(obj) -> str:
    return hashlib.sha256(canonical_json_bytes(obj)).hexdigest()


def _project(pool_dicts: list[dict]) -> list[list]:
    """Reproduce production's ``pool_for_digest`` projection."""
    return sorted([list(d[k] for k in PROJECTED_FIELDS) for d in pool_dicts])


def _eligible_features(**overrides) -> dict:
    """A feature dict that passes R2, with individual values overridable."""
    base = {
        "underdog_prior_games": 5.0,
        "favorite_prior_games": 5.0,
        "h2h_prior_games": 1.0,
        "forebet_probability_gap": 0.15,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. The decomposition must never disagree with the frozen rule
# ---------------------------------------------------------------------------


class TestAgreesWithFrozenRule:
    @pytest.mark.parametrize(
        "overrides",
        [
            {},                                             # passes cleanly
            {"forebet_probability_gap": 0.2},               # boundary: <= passes
            {"forebet_probability_gap": 0.20000000000000004},  # just over: fails
            {"forebet_probability_gap": 0.6},               # far over
            {"underdog_prior_games": 5},                    # boundary: >= passes
            {"underdog_prior_games": 4},                    # just under
            {"underdog_prior_games": 0.0},                  # no history
            {"favorite_prior_games": 4},
            {"h2h_prior_games": 1},                         # boundary
            {"h2h_prior_games": 0},
            {"underdog_prior_games": None},                 # missing
            {"favorite_prior_games": None},
            {"h2h_prior_games": None},
            {"forebet_probability_gap": None},
            {"underdog_prior_games": None, "forebet_probability_gap": 0.6},
            {"h2h_prior_games": 0, "forebet_probability_gap": 0.21},
        ],
    )
    def test_reason_is_none_exactly_when_the_rule_says_eligible(self, overrides):
        """The single most important property: rule and record cannot diverge."""
        features = _eligible_features(**overrides)
        reason = r2_ineligibility_reason(features)
        if is_r2_eligible(features):
            assert reason is None, f"rule said eligible but reason recorded {reason}"
        else:
            assert reason is not None, "rule said ineligible but reason is None"

    def test_exhaustive_agreement_over_a_value_grid(self):
        """No cherry-picked cases: sweep the boundaries of all four features."""
        values = {
            "underdog_prior_games": [None, 0.0, 4, 5, 6],
            "favorite_prior_games": [None, 0.0, 4, 5],
            "h2h_prior_games": [None, 0, 1, 2],
            "forebet_probability_gap": [None, 0.0, 0.19, 0.2, 0.21, 0.6],
        }
        checked = 0
        for ud in values["underdog_prior_games"]:
            for fav in values["favorite_prior_games"]:
                for h2h in values["h2h_prior_games"]:
                    for gap in values["forebet_probability_gap"]:
                        features = {
                            "underdog_prior_games": ud,
                            "favorite_prior_games": fav,
                            "h2h_prior_games": h2h,
                            "forebet_probability_gap": gap,
                        }
                        assert (r2_ineligibility_reason(features) is None) == (
                            is_r2_eligible(features)
                        ), f"divergence at {features}"
                        checked += 1
        assert checked == 5 * 4 * 4 * 6

    def test_spec_matches_the_frozen_config_declaration(self):
        """One source of truth: code constants == the hash-pinned config."""
        config = json.loads(
            (REPO_ROOT / "config" / "research_baselines.json").read_text()
        )
        declared = {
            (e["feature"], e["op"], e["value"])
            for e in config["rules"][FROZEN_R2_RULE_NAME]["eligibility"]
        }
        assert declared == set(R2_ELIGIBILITY_SPEC)

    def test_load_frozen_config_still_accepts_the_rule(self):
        """The runtime drift guard must not trip on this change."""
        from slumdog.shadow_evaluator import load_frozen_baseline_config

        obj = load_frozen_baseline_config(REPO_ROOT)
        assert obj["rules"][FROZEN_R2_RULE_NAME]["eligibility"]


# ---------------------------------------------------------------------------
# 2. All five failure points, with actual values
# ---------------------------------------------------------------------------


class TestDecomposition:
    def test_missing_fields_are_named_individually(self):
        reason = r2_ineligibility_reason(
            _eligible_features(h2h_prior_games=None, favorite_prior_games=None)
        )
        assert reason["primary_reason"] == "MISSING_FEATURES"
        assert reason["missing_fields"] == [
            "favorite_prior_games", "h2h_prior_games",
        ]
        assert reason["failed_thresholds"] == []

    def test_threshold_failure_records_the_real_number(self):
        """gap=0.21 and gap=0.6 must not collapse to the same boolean."""
        narrow = r2_ineligibility_reason(_eligible_features(
            forebet_probability_gap=0.21
        ))
        wide = r2_ineligibility_reason(_eligible_features(
            forebet_probability_gap=0.6
        ))
        assert narrow["primary_reason"] == wide["primary_reason"] == "THRESHOLD_NOT_MET"
        assert narrow["failed_thresholds"][0]["observed"] == 0.21
        assert wide["failed_thresholds"][0]["observed"] == 0.6
        assert narrow["observed_values"]["forebet_probability_gap"] == 0.21
        assert wide["observed_values"]["forebet_probability_gap"] == 0.6
        # The threshold in force travels with the observation.
        assert narrow["failed_thresholds"][0]["threshold"] == 0.2
        assert narrow["failed_thresholds"][0]["op"] == "lte"

    def test_all_five_failure_points_are_separately_visible(self):
        """Four thresholds + missingness: each must be individually recordable."""
        seen = set()
        for feature, op, threshold in R2_ELIGIBILITY_SPEC:
            failing = 0 if op == "gte" else threshold + 0.5
            reason = r2_ineligibility_reason(_eligible_features(**{feature: failing}))
            assert reason["failed_thresholds"][0]["feature"] == feature
            seen.add(feature)
        missing = r2_ineligibility_reason(
            _eligible_features(underdog_prior_games=None)
        )
        assert missing["missing_fields"] == ["underdog_prior_games"]
        seen.add("MISSINGNESS")
        assert seen == set(R2_FEATURES) | {"MISSINGNESS"}

    def test_both_causes_reported_together_not_short_circuited(self):
        """is_r2_eligible short-circuits on missingness; the record must not."""
        reason = r2_ineligibility_reason(
            _eligible_features(h2h_prior_games=None, forebet_probability_gap=0.6)
        )
        assert reason["primary_reason"] == "BOTH"
        assert reason["missing_fields"] == ["h2h_prior_games"]
        assert [f["feature"] for f in reason["failed_thresholds"]] == [
            "forebet_probability_gap"
        ]
        assert reason["failed_thresholds"][0]["observed"] == 0.6

    def test_observed_values_always_covers_all_four_features(self):
        reason = r2_ineligibility_reason(_eligible_features(h2h_prior_games=None))
        assert set(reason["observed_values"]) == set(R2_FEATURES)
        assert reason["observed_values"]["h2h_prior_games"] is None
        # Present values are still recorded, so the row stays analysable.
        assert reason["observed_values"]["forebet_probability_gap"] == 0.15

    def test_values_are_recorded_unrounded(self):
        """Boundary cases must survive: 0.6-0.2 is not exactly 0.4."""
        features = _eligible_features()
        features["forebet_probability_gap"] = 0.6 - 0.2
        reason = r2_ineligibility_reason(features)
        assert reason["observed_values"]["forebet_probability_gap"] == 0.6 - 0.2
        assert reason["failed_thresholds"][0]["observed"] == 0.39999999999999997

    def test_missingness_flags_distinguish_no_history_from_strict_rule(self):
        """The misreading guard: prior_games 0.0 with flag 0 is absent DATA."""
        reason = r2_ineligibility_reason(
            _eligible_features(underdog_prior_games=0.0, favorite_prior_games=0.0,
                               h2h_prior_games=0.0),
            missingness={
                "underdog_prior_games": 0,   # a real zero, not imputed
                "favorite_prior_games": 0,
                "h2h_prior_games": 0,
                "forebet_probability_gap": 0,
                "underdog_prior_win_rate": 1,  # derived from 0 games -> missing
            },
        )
        assert reason["missingness_flags"]["underdog_prior_games"] == 0
        assert reason["primary_reason"] == "THRESHOLD_NOT_MET"
        assert reason["observed_values"]["underdog_prior_games"] == 0.0

    def test_missingness_flags_default_to_none_when_not_supplied(self):
        reason = r2_ineligibility_reason(_eligible_features(h2h_prior_games=0))
        assert set(reason["missingness_flags"]) == set(R2_FEATURES)
        assert all(v is None for v in reason["missingness_flags"].values())

    def test_reason_names_the_frozen_rule(self):
        reason = r2_ineligibility_reason(_eligible_features(h2h_prior_games=0))
        assert reason["rule"] == FROZEN_R2_RULE_NAME == "R2_CONSERVATIVE_FIXED_RULE"

    def test_output_is_json_serialisable(self):
        reason = r2_ineligibility_reason(
            _eligible_features(h2h_prior_games=None, forebet_probability_gap=0.6)
        )
        round_tripped = json.loads(json.dumps(reason, sort_keys=True))
        assert round_tripped == reason


# ---------------------------------------------------------------------------
# 3. Digest safety, proven on real committed evidence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("manifest_path", SETTLED_MANIFESTS,
                         ids=lambda p: p.split("/")[-3])
class TestDigestSafety:
    """``decision_digest`` must be byte-identical before and after this change.

    Proven against the committed manifests of the already-settled dates rather
    than a synthetic case, with a negative control so the proof cannot pass
    vacuously.
    """

    def test_committed_digest_recomputes_from_decision_provenance(self, manifest_path):
        manifest = json.loads(Path(manifest_path).read_text())
        assert _canonical_sha256(manifest["decision_provenance"]) == (
            manifest["decision_digest"]
        )

    def test_projection_of_full_pool_rows_reproduces_the_digest_input(self, manifest_path):
        """The digest only ever sees the six projected fields."""
        manifest = json.loads(Path(manifest_path).read_text())
        assert _project(manifest["considered_pool"]) == [
            list(row) for row in manifest["decision_provenance"]["considered_pool"]
        ]

    def test_adding_r2_exclusion_leaves_the_digest_byte_identical(self, manifest_path):
        manifest = json.loads(Path(manifest_path).read_text())
        provenance = manifest["decision_provenance"]
        before = manifest["decision_digest"]

        # Simulate exactly what the widened schema now writes onto every row.
        mutated = [
            dict(row, r2_exclusion={
                "rule": FROZEN_R2_RULE_NAME,
                "primary_reason": "THRESHOLD_NOT_MET",
                "missing_fields": [],
                "failed_thresholds": [{
                    "feature": "forebet_probability_gap", "op": "lte",
                    "threshold": 0.2, "observed": 0.21,
                }],
                "observed_values": {f: 1.0 for f in R2_FEATURES},
                "missingness_flags": {f: 0 for f in R2_FEATURES},
            })
            for row in manifest["considered_pool"]
        ]
        payload = dict(provenance)
        payload["considered_pool"] = _project(mutated)

        assert _canonical_sha256(payload) == before, (
            "adding r2_exclusion perturbed decision_digest — the change is not "
            "digest-safe and would break reproducibility of committed runs"
        )

    def test_negative_control_the_proof_is_sensitive(self, manifest_path):
        """Without this, the test above could pass by proving nothing at all.

        ``decision_accounting`` IS part of the digest payload, so touching it
        must change the digest. This is why the aggregate exclusion counts go in
        a separate top-level manifest section instead of there.
        """
        manifest = json.loads(Path(manifest_path).read_text())
        provenance = manifest["decision_provenance"]
        payload = dict(provenance)
        payload["decision_accounting"] = dict(
            provenance["decision_accounting"], extra_key=1
        )
        assert _canonical_sha256(payload) != manifest["decision_digest"]

    def test_status_label_was_not_refined(self, manifest_path):
        """``considered_status`` is a projected field, so it must stay frozen."""
        manifest = json.loads(Path(manifest_path).read_text())
        statuses = {row["considered_status"] for row in manifest["considered_pool"]}
        # The conflated label is still there, unchanged, on historical evidence.
        assert statuses <= {
            "PRIMARY_SHADOW_SELECTION", "TOP3_EVALUATION_COHORT",
            "ELIGIBLE_RANKED_BEYOND_TOP3", "FEATURE_INCOMPLETE_OR_R2_INELIGIBLE",
            "DECISION_CONFLICT_EXCLUDED", "EXACT_DECISION_DUPLICATE_OBSERVATION",
            "MALFORMED_OR_UNKEYABLE", "TIMING_REJECTED",
        }


# ---------------------------------------------------------------------------
# 4. End-to-end wiring through production code
# ---------------------------------------------------------------------------


def _record(prob_1: float = 0.60, prob_2: float = 0.20):
    from slumdog.shadow_contracts import PreEventRecord

    return PreEventRecord(
        event_id="football:1", sport="football", event_date="2026-09-05",
        participant_1="Alpha FC", participant_2="Beta SC",
        probability_1=prob_1, probability_2=prob_2, draw_probability=0.20,
        source_url="https://example.com/1", raw_sha256="ab" * 32,
        captured_at="2026-09-03T10:00:00Z",
        body_path="data/raw/football/2026-09-05/1.txt", route="snapshot",
    )


class TestEndToEndWiring:
    def test_decision_stage_attaches_the_reason(self, tmp_path):
        from slumdog.history_loader import load_valid_history
        from slumdog.shadow_evaluator import _evaluate_for_decision_stage

        history = load_valid_history(
            target_date="2026-09-05", repo_root=tmp_path,
            history_paths=[], max_interim_bytes=10 ** 9,
        )
        ev = _evaluate_for_decision_stage(_record(), history)
        assert ev["eligible"] is False
        assert ev["status"] == "FEATURE_INCOMPLETE_OR_R2_INELIGIBLE"

        reason = ev["r2_exclusion"]
        assert reason is not None
        # Empty history presents as real zeros, not None — which is exactly the
        # case the missingness flags exist to disambiguate.
        assert reason["primary_reason"] == "THRESHOLD_NOT_MET"
        assert reason["observed_values"]["underdog_prior_games"] == 0.0
        assert reason["observed_values"]["forebet_probability_gap"] == pytest.approx(0.4)
        assert reason["missingness_flags"]["underdog_prior_games"] == 0
        assert {f["feature"] for f in reason["failed_thresholds"]} == set(R2_FEATURES)

    def test_reason_is_serialisable_through_canonical_json(self, tmp_path):
        from slumdog.history_loader import load_valid_history
        from slumdog.shadow_evaluator import _evaluate_for_decision_stage

        history = load_valid_history(
            target_date="2026-09-05", repo_root=tmp_path,
            history_paths=[], max_interim_bytes=10 ** 9,
        )
        ev = _evaluate_for_decision_stage(_record(), history)
        canonical_json_bytes({"r2_exclusion": ev["r2_exclusion"]})  # must not raise


def _synthetic_manifest() -> dict:
    spec = importlib.util.spec_from_file_location(
        "synthetic_shadow_fixture",
        REPO_ROOT / "scripts" / "synthetic_shadow_fixture.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "fixture-run"
        module.build_synthetic_run(root)
        path = next(root.glob("data/reports/shadow/*/*/manifest.json"))
        return json.loads(path.read_text())


@pytest.fixture(scope="module")
def synthetic_manifest():
    return _synthetic_manifest()


class TestManifestShape:
    def test_every_pool_row_carries_the_key(self, synthetic_manifest):
        pool = synthetic_manifest["considered_pool"]
        assert pool
        assert all("r2_exclusion" in row for row in pool)

    def test_breakdown_section_is_present_and_self_describing(self, synthetic_manifest):
        breakdown = synthetic_manifest["r2_exclusion_breakdown"]
        assert breakdown["rule"] == FROZEN_R2_RULE_NAME
        assert breakdown["thresholds_in_force"] == [
            {"feature": f, "op": op, "value": v} for f, op, v in R2_ELIGIBILITY_SPEC
        ]
        assert breakdown["rows_with_a_recorded_reason"] == sum(
            breakdown["by_primary_reason"].values()
        )

    def test_breakdown_is_outside_the_digest_payload(self, synthetic_manifest):
        """The aggregate must not sit where it could perturb the digest."""
        assert "r2_exclusion_breakdown" not in synthetic_manifest["decision_provenance"]
        assert "r2_exclusion_breakdown" not in synthetic_manifest["decision_accounting"]
        assert _canonical_sha256(synthetic_manifest["decision_provenance"]) == (
            synthetic_manifest["decision_digest"]
        )


class TestAggregate:
    def test_counts_are_tallied_per_reason_field_and_threshold(self):
        from slumdog.shadow_evaluator import _summarise_r2_exclusions

        pool = [
            {"r2_exclusion": None},                                    # eligible
            {"r2_exclusion": {                                        # missing
                "primary_reason": "MISSING_FEATURES",
                "missing_fields": ["h2h_prior_games"],
                "failed_thresholds": [],
            }},
            {"r2_exclusion": {                                        # threshold
                "primary_reason": "THRESHOLD_NOT_MET",
                "missing_fields": [],
                "failed_thresholds": [{
                    "feature": "forebet_probability_gap", "op": "lte",
                    "threshold": 0.2, "observed": 0.21,
                }],
            }},
            {"r2_exclusion": {                                        # both
                "primary_reason": "BOTH",
                "missing_fields": ["h2h_prior_games"],
                "failed_thresholds": [{
                    "feature": "forebet_probability_gap", "op": "lte",
                    "threshold": 0.2, "observed": 0.6,
                }],
            }},
            {},                                                       # no key at all
        ]
        summary = _summarise_r2_exclusions(pool)
        assert summary["rows_with_a_recorded_reason"] == 3
        assert summary["by_primary_reason"] == {
            "BOTH": 1, "MISSING_FEATURES": 1, "THRESHOLD_NOT_MET": 1,
        }
        assert summary["missing_field_counts"] == {"h2h_prior_games": 2}
        assert summary["failed_threshold_counts"] == {
            "forebet_probability_gap lte 0.2": 2
        }

    def test_empty_pool_yields_zeroed_summary_not_an_error(self):
        from slumdog.shadow_evaluator import _summarise_r2_exclusions

        summary = _summarise_r2_exclusions([])
        assert summary["rows_with_a_recorded_reason"] == 0
        assert summary["by_primary_reason"] == {}


# ---------------------------------------------------------------------------
# 5. Governance: recorded for analysis, never as licence to tune
# ---------------------------------------------------------------------------


class TestGovernance:
    def test_policy_block_forbids_reading_it_as_tuning_justification(self, synthetic_manifest):
        policy = synthetic_manifest["r2_exclusion_breakdown"]["policy"]
        assert policy["purpose"] == "recording_only"
        assert policy["decides_nothing"] is True
        assert policy["r2_rule_modified"] is False
        assert policy["thresholds_changed"] is False
        assert policy["not_justification_for_tuning"] is True

    def test_policy_names_the_threshold_and_the_required_authorisation(self, synthetic_manifest):
        note = synthetic_manifest["r2_exclusion_breakdown"]["policy"]["note"]
        assert "gap <= 0.2" in note
        assert "owner-approved tuning decision" in note
        assert "anti_tuning" in note

    def test_the_frozen_rule_itself_is_untouched(self):
        """Widen what is recorded, freeze what decides."""
        assert is_r2_eligible(_eligible_features()) is True
        assert is_r2_eligible(_eligible_features(
            forebet_probability_gap=0.21)) is False
        assert is_r2_eligible(_eligible_features(h2h_prior_games=None)) is False
        assert is_r2_eligible(_eligible_features(underdog_prior_games=4)) is False

    def test_frozen_config_hash_unchanged(self):
        from slumdog.baseline_analyzer import CANONICAL_CONFIG_SHA256

        config = json.loads(
            (REPO_ROOT / "config" / "research_baselines.json").read_text()
        )
        assert _canonical_sha256(config) == CANONICAL_CONFIG_SHA256
        assert CANONICAL_CONFIG_SHA256.startswith("666dabe7")
