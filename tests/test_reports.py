import json

from slumdog.reports import render_suggestions


def test_report_renders_every_candidate_without_cap(tmp_path):
    rows = []
    for i in range(6):
        rows.append({
            "event_id": f"e{i}", "sport": "tennis", "participant_index": 1,
            "participant": f"Dog {i}", "opponent": f"Fav {i}", "score": 40+i,
            "reasons": ["H2H 50%", "Hot 4W/5G"], "raw_confidence": 68,
            "legacy_confidence": 65+i, "price": None if i == 0 else 2.5,
            "implied_probability": None if i == 0 else 0.4,
            "legacy_probability": 0.55, "legacy_expected_value": None,
            "legacy_probability_advantage": None,
            "price_state": "PRICE_MISSING" if i == 0 else "FOREBET_PRICED",
            "state": "SHADOW_UNPRICED" if i == 0 else "SHADOW_PRICED",
            "underdog_basis": "lower_forebet_probability",
            "legacy_calibration_forensic": True, "ml_probability": None,
        })
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps(rows))
    report = render_suggestions(ledger, "2026-08-22", tmp_path)
    text = report.read_text()
    assert text.count(" to upset ") == 6
    assert "PRICE MISSING" in text
    assert "PENDING TRAINING" in text
    assert "NOT CERTIFIED" in text
