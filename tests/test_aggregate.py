import json

from slumdog.aggregate import aggregate_depth


def test_aggregate_parallel_receipts(tmp_path):
    history = tmp_path / "history" / "history_baseball_2024-01-01_2026-08-21.json"
    history.parent.mkdir()
    history.write_text(json.dumps({
        "sport": "baseball", "start": "2024-01-01", "end": "2026-08-21",
        "dates_completed": 900, "dates_requested": 964, "settled_rows": 10000,
        "priced_rows": 7000, "void_rows": 4, "failures": ["x"],
    }))
    current = tmp_path / "current" / "depth_sweep_2026-08-22.json"
    current.parent.mkdir()
    current.write_text(json.dumps({"rows": {"baseball": {
        "listing_events": 20, "both_prices": 14, "details_succeeded": 20,
        "details_requested": 20, "details_enriched": 20,
        "missing_required_fields": 0,
    }}}))
    out = aggregate_depth(tmp_path, tmp_path / "summary.md")
    text = out.read_text()
    assert "900/964" in text
    assert "10000" in text
    assert "20/20" in text
