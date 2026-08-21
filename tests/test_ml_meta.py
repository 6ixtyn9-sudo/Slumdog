import json
from dataclasses import asdict
from datetime import date, timedelta

import pytest

from slumdog.contracts import SettledEvent
from slumdog.ml_meta import TrainingRow, train_sport_model, walk_forward_predict, walk_forward_splits
from slumdog.training import train_registry


def rows(n=40):
    out = []
    for i in range(n):
        day = 1 + i // 4
        target = i % 2
        out.append(
            TrainingRow(
                event_date=f"2026-01-{day:02d}",
                sport="tennis",
                event_id=f"e{i}",
                features={
                    "forebet_dog_probability": 0.25 + 0.35 * target,
                    "probability_gap": 0.5 - 0.2 * target,
                    "optional": float(i) if i % 3 else float("nan"),
                },
                underdog_won=target,
            )
        )
    return out


def test_walk_forward_never_trains_on_test_or_future_dates():
    splits = walk_forward_splits(rows(), min_train=20)
    assert splits
    for train, test in splits:
        assert max(r.event_date for r in train) < min(r.event_date for r in test)


def test_sport_model_trains_and_predicts_probability():
    artifact = train_sport_model(rows(), min_rows=20)
    probability = artifact.predict({"forebet_dog_probability": 0.60, "probability_gap": 0.20})
    assert 0.0 <= probability <= 1.0
    assert artifact.sport == "tennis"
    assert artifact.train_rows == 40
    assert len(artifact.contract_hash) == 64


def test_walk_forward_outputs_record_training_ceiling():
    predictions = walk_forward_predict(rows(), min_train=20)
    assert predictions
    assert all(item["trained_through"] < item["event_date"] for item in predictions)


def test_registry_trains_real_sport_artifact(tmp_path):
    settled = []
    start = date(2026, 1, 1)
    for i in range(60):
        day = (start + timedelta(days=i)).isoformat()
        settled.append(SettledEvent(
            event_id=f"e{i}", sport="tennis", event_date=day,
            participant_1=f"Dog{i%4}", participant_2=f"Fav{i%5}",
            winner_index=1 if i % 2 else 2, score_1=2 if i % 2 else 0,
            score_2=0 if i % 2 else 2, probability_1=0.35 + (i % 2)*0.1,
            probability_2=0.65 - (i % 2)*0.1, draw_probability=None,
            forebet_pick=2, odds_1=2.4, odds_2=1.5,
        ))
    history = tmp_path / "history.json"
    history.write_text(json.dumps([asdict(row) for row in settled]))
    registry_path = train_registry(history, tmp_path, min_rows=30, allow_research=True)
    registry = json.loads(registry_path.read_text())
    assert registry["sports"]["tennis"]["status"] == "SHADOW_MODEL"
    assert (tmp_path / registry["sports"]["tennis"]["artifact"]).exists()
    assert registry["sports"]["tennis"]["n"] > 0


def test_training_is_frozen_without_explicit_research_override(tmp_path):
    history = tmp_path / "history.json"
    history.write_text("[]")
    with pytest.raises(RuntimeError, match="training frozen"):
        train_registry(history, tmp_path, min_rows=1)
