import gzip
import json

import pytest

from slumdog.research import build_research, load_settled, sport_ablation, sport_model_card


def _write_ledger(root, sport, rows):
    reports = root / "data" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    with gzip.open(reports / f"history_{sport}.jsonl.gz", "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def _rows(n=90, sport="football", seed=1):
    """Rows where the underdog (higher odds side) wins ~60% when odds gap is wide."""
    import random
    rng = random.Random(seed)
    rows = []
    dates = []
    day = 1
    month = 1
    for _ in range(n):
        dates.append(f"2026-{month:02d}-{day:02d}")
        day += 1
        if day > 28:
            day, month = 1, month + 1
    for i, date in enumerate(dates):
        odds_2 = 2.2 + (i % 5) * 0.4  # 2.2 .. 3.8
        wide = odds_2 >= 3.0
        underdog_wins = (rng.random() < 0.62) if wide else (rng.random() < 0.30)
        winner = 2 if underdog_wins else 1
        rows.append({
            "event_id": f"{sport}:{i}", "sport": sport, "event_date": date,
            "participant_1": "Alpha", "participant_2": "Beta",
            "winner_index": winner, "score_1": 1.0, "score_2": 0.0,
            "probability_1": 0.6, "probability_2": 0.4,
            "draw_probability": None, "forebet_pick": 1,
            "odds_1": 1.6, "odds_2": round(odds_2, 2), "disposition": "SETTLED",
        })
    return rows


def test_load_settled_reads_ledgers(tmp_path):
    _write_ledger(tmp_path, "football", _rows(10))
    _write_ledger(tmp_path, "basketball", _rows(10, "basketball"))
    settled = load_settled(tmp_path / "data" / "reports")
    assert set(settled) == {"football", "basketball"}
    assert len(settled["football"]) == 10


def test_model_card_reports_walk_forward_metrics(tmp_path):
    _write_ledger(tmp_path, "football", _rows(90))
    settled = load_settled(tmp_path / "data" / "reports")["football"]
    card = sport_model_card(settled, min_rows=60)
    assert card["status"] == "OK"
    assert card["rows"] == 90
    assert card["n"] > 0
    assert card["brier"] is not None


def test_model_card_insufficient(tmp_path):
    _write_ledger(tmp_path, "football", _rows(10))
    settled = load_settled(tmp_path / "data" / "reports")["football"]
    assert sport_model_card(settled, min_rows=60)["status"] == "INSUFFICIENT"


def test_ablation_compares_families(tmp_path):
    _write_ledger(tmp_path, "football", _rows(90))
    settled = load_settled(tmp_path / "data" / "reports")["football"]
    results = sport_ablation(settled, min_rows=60)
    families = [r["family"] for r in results]
    assert "all" in families and "drop_price" in families and "drop_probability" in families
    drop = next(r for r in results if r["family"] == "drop_price")
    assert "delta_brier" in drop or "error" in drop


def test_build_research_skips_rows_without_participants(tmp_path):
    rows = _rows(90)
    # Corrupt two rows: empty participant names (parser edge case).
    rows[3]["participant_1"] = ""
    rows[7]["participant_2"] = ""
    _write_ledger(tmp_path, "football", rows)
    settled = load_settled(tmp_path / "data" / "reports")["football"]
    from slumdog.training import build_training_rows
    training = build_training_rows(settled)
    assert len(training) == 88  # two bad rows skipped, no crash


def test_walk_forward_skips_single_class_folds(tmp_path):
    from slumdog.ml_meta import walk_forward_predict, TrainingRow
    # 120 rows where the FIRST 30 dates all have underdog_won=1 (single-class
    # train folds early), then a mix. walk_forward must not crash.
    rows = []
    for i in range(60):
        rows.append(TrainingRow(
            event_date=f"2026-01-{i%28+1:02d}", sport="football", event_id=f"a{i}",
            features={"displayed_odds": 2.5, "forebet_dog_probability": 0.4},
            underdog_won=1,
        ))
    for i in range(60):
        rows.append(TrainingRow(
            event_date=f"2026-03-{i%28+1:02d}", sport="football", event_id=f"b{i}",
            features={"displayed_odds": 2.5, "forebet_dog_probability": 0.4},
            underdog_won=i % 2,
        ))
    preds = walk_forward_predict(rows, min_train=30)
    assert isinstance(preds, list)  # no crash


def test_build_research_writes_report_and_gate(tmp_path):
    _write_ledger(tmp_path, "football", _rows(90))
    with pytest.raises(RuntimeError, match="frozen"):
        build_research(tmp_path, min_rows=60)  # no override -> frozen
    md = build_research(tmp_path, min_rows=60, allow_research=True)
    assert md.exists() and md.name.startswith("research_")
    text = md.read_text()
    assert "Model cards" in text and "Ablations" in text
    receipt = json.loads((tmp_path / "data" / "reports" / md.name.replace(".md", ".json")).read_text())
    assert receipt["cards"]["football"]["status"] == "OK"
