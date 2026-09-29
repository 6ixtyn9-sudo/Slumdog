"""Tests for the publication-horizon gate (Priority item v).

All fixtures are synthetic, offline capture receipts written directly to
``tmp_path`` -- this module issues no network calls of its own and none of
these tests should either.
"""
import json

from slumdog.horizon import (
    compute_observed_horizons,
    filter_sports_by_horizon,
    gate_sport_date,
)


def _write_receipt(root, name, target_date, generated_at, *, captured=(),
                    failures=()):
    reports = root / "data" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    payload = {
        "target_date": target_date,
        "generated_at": generated_at,
        "captured": [{"sport": s} for s in captured],
        "failures": list(failures),
    }
    (reports / name).write_text(json.dumps(payload))


class TestComputeObservedHorizons:
    def test_zero_receipts_yields_every_sport_with_no_evidence(self, tmp_path):
        horizons = compute_observed_horizons(tmp_path)
        assert horizons["rugby"].receipts_scanned == 0
        assert horizons["rugby"].max_reachable_offset is None
        assert horizons["rugby"].reachable_count == 0
        assert horizons["rugby"].not_yet_published_count == 0

    def test_football_is_always_gating_exempt(self, tmp_path):
        horizons = compute_observed_horizons(tmp_path)
        obs = horizons["football"]
        assert obs.gating_exempt is True
        assert "json" in obs.exempt_reason.lower()

    def test_current_only_sports_are_gating_exempt(self, tmp_path):
        horizons = compute_observed_horizons(tmp_path)
        for sport in ("esoccer", "afl"):
            assert horizons[sport].gating_exempt is True
            assert "current_only" in horizons[sport].exempt_reason

    def test_a_captured_sport_is_recorded_reachable_at_its_offset(self, tmp_path):
        # generated 2026-09-10, target 2026-09-14 => offset_days = 4
        _write_receipt(tmp_path, "capture_2026-09-14.json", "2026-09-14",
                        "2026-09-10T04:00:00+00:00", captured=["rugby"])
        horizons = compute_observed_horizons(tmp_path)
        obs = horizons["rugby"]
        assert obs.reachable_offsets == (4,)
        assert obs.reachable_count == 1
        assert obs.max_reachable_offset == 4
        assert obs.min_reachable_offset == 4

    def test_target_date_missing_from_html_is_recorded_not_yet_published(self, tmp_path):
        _write_receipt(tmp_path, "capture_2026-09-11.json", "2026-09-11",
                        "2026-09-10T04:00:00+00:00",
                        failures=["rugby:ValueError:target date missing from HTML: 2026-09-11"])
        horizons = compute_observed_horizons(tmp_path)
        obs = horizons["rugby"]
        assert obs.not_yet_published_offsets == (1,)
        assert obs.not_yet_published_count == 1
        assert obs.reachable_count == 0
        assert obs.max_reachable_offset is None

    def test_other_failure_types_are_not_horizon_evidence(self, tmp_path):
        _write_receipt(tmp_path, "capture_2026-09-11.json", "2026-09-11",
                        "2026-09-10T04:00:00+00:00",
                        failures=["rugby:ValueError:sport label missing from HTML: rugby",
                                  "rugby:ConnectionError:timed out"])
        horizons = compute_observed_horizons(tmp_path)
        obs = horizons["rugby"]
        assert obs.not_yet_published_count == 0
        assert obs.reachable_count == 0
        assert obs.other_failure_count == 2
        assert obs.max_reachable_offset is None

    def test_max_reachable_offset_takes_the_largest_confirmed_offset(self, tmp_path):
        _write_receipt(tmp_path, "capture_a.json", "2026-09-11",
                        "2026-09-10T04:00:00+00:00", captured=["mma"])  # offset 1
        _write_receipt(tmp_path, "capture_b.json", "2026-09-16",
                        "2026-09-10T04:00:00+00:00", captured=["mma"])  # offset 6
        _write_receipt(tmp_path, "capture_c.json", "2026-09-13",
                        "2026-09-10T04:00:00+00:00", captured=["mma"])  # offset 3
        horizons = compute_observed_horizons(tmp_path)
        obs = horizons["mma"]
        assert obs.max_reachable_offset == 6
        assert obs.min_reachable_offset == 1
        assert obs.reachable_count == 3

    def test_contradictory_evidence_at_the_same_offset_is_preserved_not_hidden(self, tmp_path):
        # Same offset (+6), one receipt succeeds, another fails -- both are
        # kept; this is the real, repeatedly-observed pattern documented in
        # docs/STATE.md (calendar-driven boards, not a fixed cutoff).
        _write_receipt(tmp_path, "capture_ok.json", "2026-09-16",
                        "2026-09-10T04:00:00+00:00", captured=["rugby"])
        _write_receipt(tmp_path, "capture_gap.json", "2026-09-23",
                        "2026-09-17T04:00:00+00:00",
                        failures=["rugby:ValueError:target date missing from HTML: 2026-09-23"])
        horizons = compute_observed_horizons(tmp_path)
        obs = horizons["rugby"]
        assert 6 in obs.reachable_offsets
        assert 6 in obs.not_yet_published_offsets
        assert obs.max_reachable_offset == 6

    def test_malformed_or_incomplete_receipts_are_silently_skipped(self, tmp_path):
        reports = tmp_path / "data" / "reports"
        reports.mkdir(parents=True)
        (reports / "capture_bad1.json").write_text("{not json")
        (reports / "capture_bad2.json").write_text(json.dumps({"target_date": "2026-09-11"}))
        (reports / "capture_bad3.json").write_text(json.dumps([1, 2, 3]))
        horizons = compute_observed_horizons(tmp_path)
        assert horizons["rugby"].receipts_scanned == 0

    def test_evidence_source_names_the_backing_receipt_files(self, tmp_path):
        _write_receipt(tmp_path, "capture_2026-09-14.json", "2026-09-14",
                        "2026-09-10T04:00:00+00:00", captured=["rugby"])
        horizons = compute_observed_horizons(tmp_path)
        source = horizons["rugby"].evidence_source
        assert source["receipts_scanned"] == 1
        assert source["reachable_offsets_with_receipts"]["4"] == [
            "capture_2026-09-14.json"]

    def test_refresh_receipts_are_included_via_the_same_glob(self, tmp_path):
        _write_receipt(tmp_path, "capture_refresh_2026-09-25_20260924T043632Z.json",
                        "2026-09-25", "2026-09-24T04:36:32+00:00",
                        captured=["basketball"])  # offset 1
        horizons = compute_observed_horizons(tmp_path)
        assert horizons["basketball"].reachable_offsets == (1,)


class TestGateSportDate:
    def test_same_day_or_past_is_always_allowed_regardless_of_evidence(self, tmp_path):
        horizons = compute_observed_horizons(tmp_path)  # zero evidence
        decision = gate_sport_date("mma", "2026-09-10", "2026-09-10", horizons)
        assert decision.allowed is True
        assert decision.offset_days == 0
        past = gate_sport_date("mma", "2026-09-09", "2026-09-10", horizons)
        assert past.allowed is True
        assert past.offset_days == -1

    def test_unknown_sport_raises(self, tmp_path):
        horizons = compute_observed_horizons(tmp_path)
        try:
            gate_sport_date("quidditch", "2026-09-12", "2026-09-10", horizons)
        except ValueError as exc:
            assert "unsupported sport" in str(exc)
        else:
            raise AssertionError("expected ValueError")

    def test_zero_receipts_never_blocks_a_forward_offset(self, tmp_path):
        horizons = compute_observed_horizons(tmp_path)
        decision = gate_sport_date("rugby", "2026-09-20", "2026-09-10", horizons)
        assert decision.allowed is True
        assert "cold start" in decision.reason

    def test_football_is_never_gated_even_far_forward(self, tmp_path):
        horizons = compute_observed_horizons(tmp_path)
        decision = gate_sport_date("football", "2026-12-25", "2026-09-10", horizons)
        assert decision.allowed is True

    def test_offset_within_observed_max_is_allowed(self, tmp_path):
        _write_receipt(tmp_path, "capture_a.json", "2026-09-16",
                        "2026-09-10T04:00:00+00:00", captured=["mma"])  # offset 6
        horizons = compute_observed_horizons(tmp_path)
        decision = gate_sport_date("mma", "2026-09-13", "2026-09-10", horizons)  # offset 3
        assert decision.allowed is True
        assert "observed max reachable offset 6" in decision.reason

    def test_offset_beyond_observed_max_is_refused(self, tmp_path):
        _write_receipt(tmp_path, "capture_a.json", "2026-09-16",
                        "2026-09-10T04:00:00+00:00", captured=["mma"])  # offset 6
        horizons = compute_observed_horizons(tmp_path)
        decision = gate_sport_date("mma", "2026-09-20", "2026-09-10", horizons)  # offset 10
        assert decision.allowed is False
        assert "exceeds the observed max reachable offset 6" in decision.reason

    def test_sport_never_confirmed_reachable_with_real_evidence_is_refused(self, tmp_path):
        # 41-receipt-scale case, minimal repro: many receipts scanned, all
        # of them "not yet published" for esports, never one success.
        for i, day in enumerate(["2026-09-11", "2026-09-12", "2026-09-13"]):
            _write_receipt(tmp_path, f"capture_{day}.json", day,
                            "2026-09-10T04:00:00+00:00",
                            failures=[f"esports:ValueError:target date missing from HTML: {day}"])
        horizons = compute_observed_horizons(tmp_path)
        decision = gate_sport_date("esports", "2026-09-13", "2026-09-10", horizons)
        assert decision.allowed is False
        assert "never once been confirmed reachable" in decision.reason
        assert "3 committed" in decision.reason


class TestFilterSportsByHorizon:
    def test_splits_allowed_and_refused_preserving_order(self, tmp_path):
        _write_receipt(tmp_path, "capture_a.json", "2026-09-16",
                        "2026-09-10T04:00:00+00:00",
                        captured=["mma", "football"])
        horizons = compute_observed_horizons(tmp_path)
        allowed, decisions = filter_sports_by_horizon(
            ["football", "mma", "esports"], "2026-09-13", "2026-09-10", horizons)
        # esports has zero receipts here -> cold start -> allowed too.
        assert allowed == ["football", "mma", "esports"]
        assert len(decisions) == 3
        assert all(d.allowed for d in decisions)

    def test_a_refusal_shows_up_in_decisions_but_not_in_allowed(self, tmp_path):
        for i, day in enumerate(["2026-09-11", "2026-09-12", "2026-09-13"]):
            _write_receipt(tmp_path, f"capture_{day}.json", day,
                            "2026-09-10T04:00:00+00:00",
                            failures=[f"esports:ValueError:target date missing from HTML: {day}"])
        horizons = compute_observed_horizons(tmp_path)
        allowed, decisions = filter_sports_by_horizon(
            ["football", "esports"], "2026-09-13", "2026-09-10", horizons)
        assert allowed == ["football"]
        refused = [d for d in decisions if not d.allowed]
        assert len(refused) == 1
        assert refused[0].sport == "esports"
        assert refused[0].to_dict()["allowed"] is False


class TestRealCommittedEvidence:
    """Sanity check against this repo's own committed capture receipts --
    still offline (reads files already on disk), not a network test."""

    def test_esports_has_never_been_confirmed_reachable_in_this_repo(self):
        horizons = compute_observed_horizons(".")
        obs = horizons["esports"]
        if obs.receipts_scanned == 0:
            return  # no committed receipts in this checkout; nothing to assert
        assert obs.reachable_count == 0
        assert obs.max_reachable_offset is None

    def test_football_esoccer_afl_are_exempt_against_real_data(self):
        horizons = compute_observed_horizons(".")
        assert horizons["football"].gating_exempt is True
        assert horizons["esoccer"].gating_exempt is True
        assert horizons["afl"].gating_exempt is True
