"""Tests for the draw-avoidance analysis module (P7)."""
from __future__ import annotations

from slumdog.draw_analysis import (
    DRAW_CAPABLE_SPORTS,
    analyze_draw_rates,
)


class TestDrawCapableSports:
    def test_football_is_draw_capable(self):
        assert "football" in DRAW_CAPABLE_SPORTS

    def test_handball_is_draw_capable(self):
        assert "handball" in DRAW_CAPABLE_SPORTS

    def test_cricket_is_draw_capable(self):
        assert "cricket" in DRAW_CAPABLE_SPORTS

    def test_basketball_not_draw_capable(self):
        assert "basketball" not in DRAW_CAPABLE_SPORTS

    def test_tennis_not_draw_capable(self):
        assert "tennis" not in DRAW_CAPABLE_SPORTS


class TestAnalyzeDrawRates:
    def test_empty_baselines(self):
        report = analyze_draw_rates({})
        assert report["analysis_type"] == "draw_avoidance_read_only"
        assert report["per_sport_base_draw_rates"] == {}

    def test_with_period_data(self):
        baselines = {
            "periods": {
                "P4": {
                    "per_sport": {
                        "football": {
                            "totals": {
                                "total": 1000,
                                "underdog_wins": 300,
                                "favorite_wins": 500,
                                "draw_negatives": 200,
                            },
                        },
                        "basketball": {
                            "totals": {
                                "total": 500,
                                "underdog_wins": 200,
                                "favorite_wins": 300,
                                "draw_negatives": 0,
                            },
                        },
                    },
                },
            },
        }
        report = analyze_draw_rates(baselines)
        rates = report["per_sport_base_draw_rates"]
        assert "football" in rates
        assert "basketball" not in rates  # not draw-capable
        fb = rates["football"]["P4"]
        assert fb["draw_rate"] == 0.2
        assert fb["total"] == 1000

    def test_findings_include_sufficient_n(self):
        baselines = {
            "periods": {
                "P4": {
                    "per_sport": {
                        "football": {
                            "totals": {
                                "total": 100,
                                "underdog_wins": 30,
                                "favorite_wins": 50,
                                "draw_negatives": 20,
                            },
                        },
                    },
                },
            },
        }
        report = analyze_draw_rates(baselines)
        assert len(report["findings"]) == 1
        finding = report["findings"][0]
        assert finding["sport"] == "football"
        assert finding["n"] == 100
        assert finding["draw_rate"] == 0.2

    def test_notes_always_present(self):
        report = analyze_draw_rates({})
        assert len(report["notes"]) >= 3
        # Verify n<30 warning
        assert any("n<30" in note or "n < 30" in note for note in report["notes"])

    def test_signals_analyzed(self):
        report = analyze_draw_rates({})
        assert len(report["signals_analyzed"]) == 7
        assert "probability_gap" in report["signals_analyzed"]
        assert "underdog_probability" in report["signals_analyzed"]

    def test_read_only_no_config_changes(self):
        """The analysis must not propose any config changes."""
        report = analyze_draw_rates({})
        # No "proposed_config" or "threshold_change" key
        assert "proposed_config" not in report
        assert "threshold_change" not in report
        assert "selection_width_change" not in report
