"""Sport-specific ML-meta learner for the underdog-win target."""
from __future__ import annotations

import hashlib
import json
import pickle
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


@dataclass(frozen=True)
class TrainingRow:
    event_date: str
    sport: str
    event_id: str
    features: dict[str, float]
    underdog_won: int

    def __post_init__(self) -> None:
        date.fromisoformat(self.event_date)
        if self.underdog_won not in (0, 1):
            raise ValueError("underdog_won must be 0 or 1")


@dataclass
class ModelArtifact:
    sport: str
    feature_names: list[str]
    trained_through: str
    train_rows: int
    model: Pipeline

    @property
    def contract_hash(self) -> str:
        return hashlib.sha256("\n".join(self.feature_names).encode()).hexdigest()

    def predict(self, features: dict[str, float]) -> float:
        vector = np.array([[features.get(name, np.nan) for name in self.feature_names]])
        return float(self.model.predict_proba(vector)[0, 1])

    def save(self, path: Path | str) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pickle.dumps(self))
        path.with_suffix(path.suffix + ".json").write_text(
            json.dumps(
                {
                    "sport": self.sport,
                    "feature_names": self.feature_names,
                    "contract_hash": self.contract_hash,
                    "trained_through": self.trained_through,
                    "train_rows": self.train_rows,
                },
                indent=2,
                sort_keys=True,
            )
        )


def feature_contract(rows: Iterable[TrainingRow]) -> list[str]:
    return sorted({key for row in rows for key in row.features})


def train_sport_model(rows: list[TrainingRow], min_rows: int = 100) -> ModelArtifact:
    if not rows:
        raise ValueError("no training rows")
    sports = {row.sport for row in rows}
    if len(sports) != 1:
        raise ValueError("one model per sport is required")
    if len(rows) < min_rows:
        raise ValueError(f"insufficient rows: {len(rows)} < {min_rows}")
    if len({row.underdog_won for row in rows}) < 2:
        raise ValueError("both outcome classes are required")
    ordered = sorted(rows, key=lambda row: (row.event_date, row.event_id))
    names = feature_contract(ordered)
    matrix = np.array([[row.features.get(name, np.nan) for name in names] for row in ordered])
    target = np.array([row.underdog_won for row in ordered])
    model = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("logit", LogisticRegression(C=0.5, class_weight="balanced", solver="liblinear", max_iter=2000)),
        ]
    )
    model.fit(matrix, target)
    return ModelArtifact(
        sport=ordered[0].sport,
        feature_names=names,
        trained_through=max(row.event_date for row in ordered),
        train_rows=len(ordered),
        model=model,
    )


def walk_forward_splits(
    rows: list[TrainingRow],
    min_train: int = 100,
) -> list[tuple[list[TrainingRow], list[TrainingRow]]]:
    """Expanding-date splits: a date is predicted only from earlier dates."""
    ordered = sorted(rows, key=lambda row: (row.event_date, row.event_id))
    dates = sorted({row.event_date for row in ordered})
    splits = []
    for test_date in dates:
        train = [row for row in ordered if row.event_date < test_date]
        test = [row for row in ordered if row.event_date == test_date]
        if len(train) >= min_train and test:
            splits.append((train, test))
    return splits


def walk_forward_predict(
    rows: list[TrainingRow],
    min_train: int = 100,
) -> list[dict[str, object]]:
    predictions: list[dict[str, object]] = []
    for train, test in walk_forward_splits(rows, min_train=min_train):
        # A train fold can be single-class early in a sport's history (e.g.
        # every underdog won/lost); a classifier cannot be fit on one class.
        # Skip that fold rather than crash the whole research gate.
        if len({row.underdog_won for row in train}) < 2:
            continue
        artifact = train_sport_model(train, min_rows=min_train)
        for row in test:
            predictions.append(
                {
                    "event_id": row.event_id,
                    "event_date": row.event_date,
                    "sport": row.sport,
                    "probability": artifact.predict(row.features),
                    "actual": row.underdog_won,
                    "displayed_odds": row.features.get("displayed_odds", 0.0),
                    "trained_through": artifact.trained_through,
                    "train_rows": artifact.train_rows,
                }
            )
    return predictions
