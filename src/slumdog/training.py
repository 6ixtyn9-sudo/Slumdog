"""Historical training rows, validation thresholds and model registry."""
from __future__ import annotations

import json
import math
import pickle
from datetime import datetime, timezone
from pathlib import Path

from .contracts import (
    CandidateState,
    EventSnapshot,
    RobberCandidate,
    SettledEvent,
    TimingClass,
)
from .facets import build_numeric_features
from .history import HistoryIndex
from .magolide import RobberConfig, detect_robber, identify_underdog
from .ml_meta import ModelArtifact, TrainingRow, train_sport_model, walk_forward_predict


def settled_event_snapshot(row: SettledEvent) -> EventSnapshot:
    return EventSnapshot(
        event_id=row.event_id,
        sport=row.sport,
        event_date=row.event_date,
        captured_at=f"{row.event_date}T00:00:00+00:00",
        source_url=row.source_url or "historical_forebet_page",
        participant_1=row.participant_1,
        participant_2=row.participant_2,
        probability_1=row.probability_1,
        probability_2=row.probability_2,
        draw_probability=row.draw_probability,
        forebet_pick=row.forebet_pick,
        odds_1=row.odds_1,
        odds_2=row.odds_2,
        league=row.league,
        facets={"historical_reconstruction": 1.0},
        facet_timing={"historical_reconstruction": TimingClass.PRE_EVENT},
    )


def _proxy_candidate(event: EventSnapshot) -> RobberCandidate:
    dog = identify_underdog(event).index
    favorite = 2 if dog == 1 else 1
    price = event.odds(dog)
    implied = 1 / price if price else None
    return RobberCandidate(
        event_id=event.event_id,
        sport=event.sport,
        participant_index=dog,
        participant=event.participant(dog),
        opponent=event.participant(favorite),
        score=0.0,
        reasons=["Underdog training universe"],
        raw_confidence=46.0,
        legacy_confidence=50.0,
        price=price,
        implied_probability=implied,
        legacy_probability=event.probability(dog) or 0.0,
        legacy_expected_value=None,
        legacy_probability_advantage=None,
        price_state=event.price_state,
        state=CandidateState.SHADOW_PRICED if price else CandidateState.SHADOW_UNPRICED,
        underdog_basis=identify_underdog(event).basis,
        forebet_underdog_probability=event.probability(dog),
        forebet_favorite_probability=event.probability(favorite),
        legacy_qualified=False,
    )


def build_training_rows(settled: list[SettledEvent]) -> list[TrainingRow]:
    history = HistoryIndex(settled)
    training = []
    for row in sorted(settled, key=lambda item: (item.event_date, item.event_id)):
        event = settled_event_snapshot(row)
        h2h, recent_1, recent_2 = history.context(
            row.sport, row.event_date, row.participant_1, row.participant_2
        )
        candidate = detect_robber(
            event, h2h, recent_1, recent_2,
            RobberConfig(min_score=0, emit_min_confidence=50),
        ) or _proxy_candidate(event)
        features = build_numeric_features(event, candidate, h2h, recent_1, recent_2)
        training.append(TrainingRow(
            event_date=row.event_date,
            sport=row.sport,
            event_id=row.event_id,
            features=features,
            underdog_won=int(row.winner_index == candidate.participant_index),
        ))
    return training


def wilson_lower(wins: int, n: int, z: float = 1.645) -> float:
    if n <= 0:
        return 0.0
    p = wins / n
    denominator = 1 + z*z/n
    centre = p + z*z/(2*n)
    spread = z * math.sqrt((p*(1-p) + z*z/(4*n))/n)
    return max(0.0, (centre-spread)/denominator)


def validation_summary(predictions: list[dict[str, object]]) -> dict[str, object]:
    if not predictions:
        return {"n": 0, "threshold": None}
    brier = sum((float(item["probability"]) - int(item["actual"]))**2 for item in predictions) / len(predictions)
    scans = []
    for threshold in (0.35, 0.40, 0.45, 0.50, 0.55, 0.60):
        selected = [item for item in predictions if float(item["probability"]) >= threshold]
        if len(selected) < 10:
            continue
        wins = sum(int(item["actual"]) for item in selected)
        priced = [item for item in selected if float(item.get("displayed_odds") or 0) > 1.0]
        priced_return = sum(
            float(item["displayed_odds"]) if int(item["actual"]) else 0.0
            for item in priced
        )
        priced_roi = (priced_return - len(priced)) / len(priced) if priced else None
        scans.append({
            "threshold": threshold,
            "n": len(selected),
            "wins": wins,
            "hit_rate": wins/len(selected),
            "wilson_lower_90": wilson_lower(wins, len(selected)),
            "priced_n": len(priced),
            "priced_roi": priced_roi,
        })
    eligible = [
        item for item in scans
        if item["n"] >= 20
        and item["hit_rate"] >= 0.45
        and item["wilson_lower_90"] >= 0.35
        and (item["priced_n"] < 10 or (item["priced_roi"] is not None and item["priced_roi"] > 0))
    ]
    # Price evidence wins when it has at least ten rows and positive ROI;
    # otherwise rank the honest hit-rate evidence conservatively.
    best = max(
        eligible,
        key=lambda item: (
            item["priced_n"] >= 10 and (item["priced_roi"] or -99) > 0,
            item["wilson_lower_90"],
            item["threshold"],
        ),
        default=None,
    )
    return {
        "n": len(predictions),
        "brier": round(brier, 6),
        "threshold": best["threshold"] if best else None,
        "selected_n": best["n"] if best else 0,
        "selected_wins": best["wins"] if best else 0,
        "selected_hit_rate": round(best["hit_rate"], 6) if best else None,
        "wilson_lower_90": round(best["wilson_lower_90"], 6) if best else None,
        "priced_n": best["priced_n"] if best else 0,
        "priced_roi": round(best["priced_roi"], 6) if best and best["priced_roi"] is not None else None,
        "scans": scans,
    }


def train_registry(
    history_path: Path | str,
    root: Path | str = ".",
    min_rows: int = 50,
    allow_research: bool = False,
) -> Path:
    from .feature_contracts import MODEL_TRAINING_ALLOWED
    from .pipeline import settled_from_dict

    if not MODEL_TRAINING_ALLOWED and not allow_research:
        raise RuntimeError(
            "model training frozen until Forebet depth and sport contracts are complete"
        )

    root = Path(root)
    payload = json.loads(Path(history_path).read_text())
    settled = [settled_from_dict(item) for item in payload if isinstance(item, dict)]
    training = build_training_rows(settled)
    models_dir = root / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    registry = {"generated_at": datetime.now(timezone.utc).isoformat(), "sports": {}}
    for sport in sorted({row.sport for row in training}):
        rows = [row for row in training if row.sport == sport]
        if len(rows) < min_rows or len({row.underdog_won for row in rows}) < 2:
            registry["sports"][sport] = {"status": "INSUFFICIENT", "rows": len(rows)}
            continue
        minimum = max(20, min_rows // 2)
        predictions = walk_forward_predict(rows, min_train=minimum)
        summary = validation_summary(predictions)
        artifact = train_sport_model(rows, min_rows=min_rows)
        artifact_path = models_dir / f"{sport}.pkl"
        artifact_path.write_bytes(pickle.dumps(artifact))
        registry["sports"][sport] = {
            "status": "SHADOW_MODEL" if summary.get("threshold") is not None else "OBSERVE_MODEL",
            "rows": len(rows),
            "trained_through": artifact.trained_through,
            "artifact": str(artifact_path.relative_to(root)),
            "contract_hash": artifact.contract_hash,
            **summary,
        }
    path = models_dir / "registry.json"
    path.write_text(json.dumps(registry, indent=2, sort_keys=True))
    return path


def load_artifact(root: Path | str, relative_path: str) -> ModelArtifact:
    return pickle.loads((Path(root) / relative_path).read_bytes())
