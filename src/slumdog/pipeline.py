"""Slumdog shadow candidate pipeline."""
from __future__ import annotations

import json
from pathlib import Path

from .contracts import EventSnapshot, H2HStats, RecentForm, RobberCandidate, SettledEvent, TimingClass
from .facets import build_numeric_features
from .history import HistoryIndex
from .ledger import freeze_candidates
from .magolide import RobberConfig, detect_robber
from .parsers import parse_capture
from .training import _proxy_candidate, load_artifact


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _stats_from_event(event: EventSnapshot) -> tuple[H2HStats, RecentForm, RecentForm]:
    """Build legacy context only from timing-approved Forebet facets."""
    facets = event.pre_event_facets()

    def rates(key: str) -> tuple[float, ...]:
        value = facets.get(key, ())
        if not isinstance(value, (list, tuple)):
            return ()
        return tuple(_number(item) for item in value)

    h2h = H2HStats(
        total_games=int(_number(facets.get("h2h_total_games"))),
        participant_1_wins=int(_number(facets.get("h2h_participant_1_wins"))),
        participant_2_wins=int(_number(facets.get("h2h_participant_2_wins"))),
        period_win_rates_1=rates("period_win_rates_1"),
        period_win_rates_2=rates("period_win_rates_2"),
        half_1_rate_1=_number(facets.get("half_1_rate_1")),
        half_2_rate_1=_number(facets.get("half_2_rate_1")),
        half_1_rate_2=_number(facets.get("half_1_rate_2")),
        half_2_rate_2=_number(facets.get("half_2_rate_2")),
    )
    recent_1 = RecentForm(
        wins=int(_number(facets.get("recent_1_wins"))),
        games=int(_number(facets.get("recent_1_games"))),
    )
    recent_2 = RecentForm(
        wins=int(_number(facets.get("recent_2_wins"))),
        games=int(_number(facets.get("recent_2_games"))),
    )
    return h2h, recent_1, recent_2


def event_from_dict(payload: dict) -> EventSnapshot:
    timing = {
        key: value if isinstance(value, TimingClass) else TimingClass(str(value))
        for key, value in (payload.get("facet_timing") or {}).items()
    }
    allowed = {
        "event_id", "sport", "event_date", "captured_at", "source_url",
        "participant_1", "participant_2", "probability_1", "probability_2",
        "forebet_pick", "draw_probability", "odds_1", "odds_2", "league",
        "tournament", "round_name", "kickoff", "predicted_score",
        "predicted_total", "raw_sha256", "facets",
    }
    kwargs = {key: value for key, value in payload.items() if key in allowed}
    kwargs["facet_timing"] = timing
    return EventSnapshot(**kwargs)


def settled_from_dict(payload: dict) -> SettledEvent:
    allowed = set(SettledEvent.__dataclass_fields__)
    kwargs = {key: value for key, value in payload.items() if key in allowed}
    for key in ("period_scores_1", "period_scores_2"):
        if key in kwargs:
            kwargs[key] = tuple(kwargs[key])
    return SettledEvent(**kwargs)


def build_shadow_robbers(
    events: list[EventSnapshot],
    config: RobberConfig | None = None,
    history: HistoryIndex | None = None,
    model_registry: dict | None = None,
    root: Path | str = ".",
) -> list[RobberCandidate]:
    """Emit every high-confidence legacy Robber; there is no count cap."""
    candidates = []
    model_cache = {}
    for event in events:
        if history is not None:
            h2h, recent_1, recent_2 = history.context(
                event.sport, event.event_date, event.participant_1, event.participant_2
            )
        else:
            h2h, recent_1, recent_2 = _stats_from_event(event)
        candidate = detect_robber(event, h2h, recent_1, recent_2, config)
        model_info = (model_registry or {}).get("sports", {}).get(event.sport, {})
        if model_info.get("status") == "SHADOW_MODEL" and model_info.get("threshold") is not None:
            if candidate is None:
                candidate = _proxy_candidate(event)
            features = build_numeric_features(event, candidate, h2h, recent_1, recent_2)
            if event.sport not in model_cache:
                model_cache[event.sport] = load_artifact(root, model_info["artifact"])
            probability = model_cache[event.sport].predict(features)
            if probability < float(model_info["threshold"]):
                continue
            candidate.ml_probability = round(probability, 6)
            candidate.ml_threshold = float(model_info["threshold"])
            candidate.ml_train_rows = int(model_info.get("rows") or 0)
            candidate.ml_validation_n = int(model_info.get("selected_n") or 0)
            candidate.ml_validation_hit_rate = model_info.get("selected_hit_rate")
            candidate.ml_validation_brier = model_info.get("brier")
            candidate.ml_validation_wilson_lower = model_info.get("wilson_lower_90")
            candidate.ml_validation_priced_n = int(model_info.get("priced_n") or 0)
            candidate.ml_validation_priced_roi = model_info.get("priced_roi")
            candidate.reasons.append(
                f"Sport ML {probability:.1%} >= {float(model_info['threshold']):.0%} shadow threshold"
            )
        else:
            if candidate is None:
                continue
            dog_recent = recent_1 if candidate.participant_index == 1 else recent_2
            # Slumdog's high-confidence lane is stricter than the forensic
            # Ma Golide reproducer: require strong raw score plus non-trivial
            # prior evidence when no validated sport model is available.
            if candidate.raw_confidence < 65:
                continue
            if h2h.total_games < 3 and dog_recent.games < 5:
                continue
            build_numeric_features(event, candidate, h2h, recent_1, recent_2)
        candidates.append(candidate)
    return sorted(
        candidates,
        key=lambda item: (
            -(item.ml_probability if item.ml_probability is not None else item.legacy_confidence / 100.0),
            -item.score,
            item.sport,
            item.event_id,
        ),
    )


def parse_capture_receipt(
    target_date: str,
    root: Path | str = ".",
) -> Path:
    root = Path(root)
    receipt_path = root / "data" / "reports" / f"capture_{target_date}.json"
    receipt = json.loads(receipt_path.read_text())
    events: list[EventSnapshot] = []
    failures: list[str] = []
    for metadata in receipt.get("captured", []):
        try:
            events.extend(parse_capture(metadata, root))
        except Exception as exc:  # sport parsers fail independently and visibly
            failures.append(f"{metadata.get('sport')}:{type(exc).__name__}:{exc}")
    events.sort(key=lambda item: (item.sport, item.kickoff, item.event_id))
    output = root / "data" / "interim" / f"events_{target_date}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps([event.to_dict() for event in events], indent=2, sort_keys=True))
    report = root / "data" / "reports" / f"parse_{target_date}.json"
    report.write_text(json.dumps({
        "target_date": target_date,
        "events": len(events),
        "by_sport": {
            sport: sum(1 for event in events if event.sport == sport)
            for sport in sorted({event.sport for event in events})
        },
        "failures": failures,
        "output": str(output.relative_to(root)),
    }, indent=2, sort_keys=True))
    return output


def run_from_json(
    events_path: Path | str,
    target_date: str,
    root: Path | str = ".",
    config: RobberConfig | None = None,
    history_path: Path | str | None = None,
) -> Path:
    payload = json.loads(Path(events_path).read_text())
    if not isinstance(payload, list):
        raise ValueError("events JSON must be a list")
    events = [event_from_dict(item) for item in payload if isinstance(item, dict)]
    history = None
    if history_path is not None and Path(history_path).exists():
        history_payload = json.loads(Path(history_path).read_text())
        history_rows = [
            settled_from_dict(item) for item in history_payload if isinstance(item, dict)
        ]
        history = HistoryIndex(history_rows)
    root_path = Path(root)
    registry_path = root_path / "models" / "registry.json"
    model_registry = json.loads(registry_path.read_text()) if registry_path.exists() else None
    candidates = build_shadow_robbers(events, config, history, model_registry, root_path)
    return freeze_candidates(target_date, candidates, root)
