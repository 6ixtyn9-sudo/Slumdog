import gzip
import json

from slumdog.analyze import analyze_depth, latest_census, ledger_profile


def _write_census(root, rows):
    path = root / "data" / "reports" / "depth_sweep_2026-08-21.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "target_date": "2026-08-21",
        "rows": {
            sport: {
                "listing_events": spec["listing"],
                "both_prices": spec["priced"],
                "price_coverage": round(spec["priced"] / spec["listing"], 4),
                "details_requested": spec["listing"],
                "details_succeeded": spec["listing"],
                "details_enriched": spec["listing"],
                "missing_required_fields": spec["missing"],
                "field_presence": spec["presence"],
            }
            for sport, spec in rows.items()
        },
    }, indent=2))


def _write_history(root, sport, start, end, rows, priced, ledger_rows):
    reports = root / "data" / "reports"
    manifest = {
        "sport": sport, "start": start, "end": end,
        "dates_requested": 3, "dates_completed": 3,
        "settled_rows": len(ledger_rows), "priced_rows": priced,
        "void_rows": sum(1 for r in ledger_rows if r.get("disposition") == "VOID"),
        "failures": [], "history_file": f"data/reports/history_{sport}.jsonl.gz",
        "daily_receipts": [{"date": d, "settled_rows": 1} for d in (start, )],
    }
    (reports / f"history_{sport}.json").write_text(json.dumps(manifest, indent=2))
    with gzip.open(reports / f"history_{sport}.jsonl.gz", "wt", encoding="utf-8") as handle:
        for row in ledger_rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def test_latest_census_finds_newest(tmp_path):
    (tmp_path / "data" / "reports").mkdir(parents=True)
    (tmp_path / "data" / "reports" / "depth_sweep_2026-08-20.json").write_text("{}")
    (tmp_path / "data" / "reports" / "depth_sweep_2026-08-21.json").write_text('{"rows": {}}')
    census = latest_census(tmp_path / "data" / "reports")
    assert census is not None


def test_analyze_depth_builds_report_and_json(tmp_path):
    census_rows = {
        "football": {
            "listing": 10, "priced": 8, "missing": 1,
            "presence": {"detail_weather_present": 9, "detail_corners_present": 0},
        },
        "basketball": {
            "listing": 4, "priced": 0, "missing": 0,
            "presence": {"detail_quarter_data_present": 4},
        },
    }
    _write_census(tmp_path, census_rows)
    ledger_football = [
        {"event_date": "2026-08-01", "sport": "football", "league": "EPL",
         "odds_1": 2.0, "odds_2": 3.0, "disposition": "SETTLED"},
        {"event_date": "2026-08-02", "sport": "football", "league": "EPL",
         "odds_1": None, "odds_2": None, "disposition": "SETTLED"},
        {"event_date": "2026-08-03", "sport": "football", "league": "LaLiga",
         "odds_1": 1.5, "odds_2": 5.0, "disposition": "VOID"},
    ]
    _write_history(tmp_path, "football", "2026-08-01", "2026-08-03",
                   rows=3, priced=2, ledger_rows=ledger_football)

    out = analyze_depth(tmp_path, target_date="2026-08-21")
    assert out.exists() and out.name == "analysis_2026-08-21.md"

    receipt = json.loads((tmp_path / "data" / "reports" / "analysis_2026-08-21.json").read_text())
    assert receipt["census"]["football"]["listing_events"] == 10
    # Zero-presence detail field flagged.
    assert "detail_corners_present" in receipt["census"]["football"]["zero_presence_detail_fields"]
    # Ledger profile.
    ledger = receipt["history"]["football"]["ledger"]
    assert ledger["rows"] == 3
    assert ledger["priced_rows"] == 2
    assert ledger["void_rows"] == 1
    assert ledger["seasons"]["2026"]["rows"] == 3
    assert ledger["top_leagues"][0][0] == "EPL"
    # Summary.
    assert receipt["summary"]["history_rows"] == 3
    assert receipt["summary"]["history_price_coverage"] == round(2 / 3, 4)


def test_ledger_profile_handles_missing_ledger(tmp_path):
    assert ledger_profile(tmp_path / "data" / "reports", "tennis") == {}
