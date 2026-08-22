import gzip
import json

import pytest
from bs4 import BeautifulSoup

from slumdog.audit import AuditGateError, build_audit, sport_receipt
from slumdog.backfill import backfill_sport
from slumdog.contracts import EventSnapshot, TimingClass
from slumdog.forebet import (
    FOOTBALL_MARKETS,
    fetch_football_markets,
    source_url,
    validate_html_body,
)
from slumdog.ml_meta import TrainingRow, walk_forward_splits
from slumdog.parsers import _american_odds_row, _merge_football_markets
from slumdog.research import _families_with_features
from slumdog.sports import SPORTS


def _history_row(day, event_id, sport="basketball", priced=False):
    return {
        "event_id": event_id,
        "sport": sport,
        "event_date": day,
        "odds_1": 2.1 if priced else None,
        "odds_2": 1.7 if priced else None,
        "disposition": "SETTLED",
        "period_scores_1": [],
        "period_scores_2": [],
        "league": "TEST",
    }


def _write_ledger(root, sport, rows, manifest=None, tail=""):
    reports = root / "data" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    with gzip.open(reports / f"history_{sport}.jsonl.gz", "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
        handle.write(tail)
    if manifest is not None:
        (reports / f"history_{sport}.json").write_text(json.dumps(manifest))


def test_audit_does_not_fail_for_expected_current_only_board(tmp_path):
    receipt = sport_receipt(tmp_path / "data" / "reports", "afl")
    assert receipt["current_only"] is True
    assert receipt["status"] == "CURRENT_ONLY"
    assert receipt["issues"] == []


def test_audit_counts_corruption_duplicates_and_date_gaps(tmp_path):
    rows = [_history_row("2026-01-01", f"e{i}") for i in range(100)]
    rows.append(_history_row("2026-01-01", "e0"))
    manifest = {
        "sport": "basketball",
        "start": "2026-01-01",
        "end": "2026-01-03",
        "dates_requested": 3,
        "dates_completed": 1,
        "settled_rows": 100,
        "priced_rows": 0,
        "daily_receipts": [{"date": "2026-01-01"}],
    }
    _write_ledger(tmp_path, "basketball", rows, manifest, tail="not-json\n")
    receipt = sport_receipt(tmp_path / "data" / "reports", "basketball")
    assert receipt["rows"] == 101
    assert receipt["malformed_lines"] == 1
    assert receipt["duplicate_event_ids"] == 1
    assert "missing dates: 2" in receipt["issues"]
    assert receipt["status"] == "BELOW_FLOOR"


def test_audit_does_not_apply_price_floor_to_unpriced_basketball(tmp_path):
    rows = [_history_row("2026-01-01", f"e{i}") for i in range(100)]
    manifest = {
        "sport": "basketball",
        "start": "2026-01-01",
        "end": "2026-01-01",
        "dates_requested": 1,
        "dates_completed": 1,
        "settled_rows": 100,
        "priced_rows": 0,
        "daily_receipts": [{"date": "2026-01-01"}],
    }
    _write_ledger(tmp_path, "basketball", rows, manifest)
    receipt = sport_receipt(tmp_path / "data" / "reports", "basketball")
    assert receipt["status"] == "OK"
    assert not any("price coverage" in issue for issue in receipt["issues"])


def test_audit_writes_before_raising_gate_error(tmp_path):
    path = build_audit(tmp_path, target_date="2026-08-22", fail_on_gate=False)
    assert path.exists()
    with pytest.raises(AuditGateError) as exc:
        build_audit(tmp_path, target_date="2026-08-22", fail_on_gate=True)
    assert exc.value.path == path


def test_american_odds_fallback_survives_bad_haodd():
    row = BeautifulSoup(
        "<div class='rcnt'><div class='haodd'><span>-</span><span>-</span></div>"
        "<span class='lscrsp'>-133</span><span>+112</span></div>",
        "html.parser",
    ).select_one(".rcnt")
    home, away = _american_odds_row(row)
    assert round(home, 2) == 1.75
    assert round(away, 2) == 2.12


def test_american_odds_rejects_one_sided_or_short_signed_tokens():
    row = BeautifulSoup(
        "<div class='rcnt'><span class='lscrsp'>+99</span><span>-110</span></div>",
        "html.parser",
    ).select_one(".rcnt")
    assert _american_odds_row(row) is None


def test_current_day_market_capture_is_hard_date_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr("slumdog.forebet.today_iso", lambda: "2026-08-22")
    calls = []

    def fake_market(url, expected_url, timeout=45, max_retries=2):
        calls.append(expected_url)
        return b'[[{"id": "7", "pr_over": 55}]]'

    monkeypatch.setattr("slumdog.forebet.relay_get_markdown", fake_market)
    output = fetch_football_markets("2026-08-22", tmp_path)
    assert output is not None
    assert len(calls) == len(FOOTBALL_MARKETS)
    assert json.loads(output.read_text())[0]["pr_over"] == 55
    assert fetch_football_markets("2024-01-01", tmp_path) is None
    assert len(calls) == len(FOOTBALL_MARKETS)


def test_market_merge_marks_only_selected_fields_pre_event(tmp_path):
    path = tmp_path / "markets.json"
    path.write_text(json.dumps([{"id": "7", "pr_over": 55, "Host_SC": 2}]))
    event = EventSnapshot(
        event_id="football:7", sport="football", event_date="2026-08-22",
        captured_at="2026-08-22T00:00:00+00:00", source_url="u",
        participant_1="A", participant_2="B", probability_1=.4,
        probability_2=.6, forebet_pick=2,
    )
    _merge_football_markets([event], path)
    assert event.facets["market_uo_pr_over"] == 55
    assert event.facet_timing["market_uo_pr_over"] == TimingClass.PRE_EVENT
    assert "Host_SC" not in event.facets


def test_current_only_afl_uses_current_board_url_and_validation():
    assert "predictions" not in source_url(SPORTS["afl"], "2026-08-22")
    validate_html_body(b"<html><body>AFL upcoming board" + b"x" * 100, "afl", "2026-08-22")


def test_current_only_sports_are_not_backfilled(tmp_path):
    with pytest.raises(ValueError, match="dated Forebet archive"):
        backfill_sport("afl", root=tmp_path, start="2026-01-01", end="2026-01-01")


def test_walk_forward_cap_keeps_only_latest_test_dates():
    rows = [
        TrainingRow(
            event_date=f"2026-01-{i + 1:02d}", sport="tennis", event_id=f"e{i}",
            features={"x": 1.0}, underdog_won=i % 2,
        )
        for i in range(30)
    ]
    splits = walk_forward_splits(rows, min_train=5, max_test_dates=3)
    assert [test[0].event_date for _, test in splits] == [
        "2026-01-28", "2026-01-29", "2026-01-30"
    ]


def test_research_family_filter_uses_signal_not_placeholder_keys():
    row = TrainingRow(
        event_date="2026-01-01", sport="hockey", event_id="e1",
        features={
            "displayed_odds": 0.0,
            "price_available": 0.0,
            "h2h_games": 4.0,
            "dog_recent_games": 0.0,
            "favorite_recent_win_rate": 0.0,
            "forebet_dog_probability": 0.4,
            "forebet_other_probability": 0.6,
            "legacy_robber_score": 0.0,
        },
        underdog_won=0,
    )
    families = _families_with_features([row])
    assert "all" in families
    assert "drop_probability" in families
    assert "drop_h2h" in families
    assert "drop_price" not in families
    assert "drop_form" not in families
    assert "drop_legacy" not in families
