"""Research gate: per-sport model cards and feature ablations from settled ledgers.

Turns the completed history ledgers into the evidence the training freeze is
waiting for:

- model cards: per-sport walk-forward validation (threshold, hit rate, Wilson
  lower bound, Brier, priced ROI) plus coverage;
- ablations: walk-forward comparison of the full feature set against dropping
  each feature family (price / probability / legacy / h2h / form), reporting
  the delta in Brier and selected hit rate.

Still research-only: training remains frozen unless --research-override.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from .analyze import _stream_ledger
from .clock import today_iso
from .ml_meta import TrainingRow, feature_contract
from .pipeline import settled_from_dict
from .training import build_training_rows, validation_summary, walk_forward_predict

# Feature families by key prefix in the training vector (see facets.py).
FEATURE_FAMILIES: dict[str, tuple[str, ...]] = {
    "all": (),
    "drop_price": ("displayed_odds", "implied_probability", "price_available"),
    "drop_probability": (
        "forebet_dog_probability", "forebet_other_probability",
        "forebet_favorite_probability", "forebet_probability_gap",
        "forebet_probability_ratio", "forebet_entropy", "forebet_calls_dog",
        "draw_probability", "draw_probability_missing",
    ),
    "drop_legacy": ("legacy_robber_score", "legacy_raw_confidence"),
    "drop_h2h": ("h2h_games", "h2h_dog_win_rate"),
    "drop_form": (
        "dog_recent_games", "dog_recent_win_rate", "favorite_recent_win_rate",
    ),
}


def load_settled(reports_dir: Path) -> dict[str, list]:
    """Load every sport's rolling ledger into SettledEvent lists."""
    by_sport: dict[str, list] = defaultdict(list)
    for path in sorted(reports_dir.glob("history_*.jsonl.gz")):
        sport = path.name[len("history_"):-len(".jsonl.gz")]
        for row in _stream_ledger(path):
            by_sport[sport].append(settled_from_dict(row))
    return dict(by_sport)


def _filter_features(rows: list[TrainingRow], drop: tuple[str, ...]) -> list[TrainingRow]:
    if not drop:
        return rows
    drop_set = set(drop)
    out = []
    for row in rows:
        features = {k: v for k, v in row.features.items() if k not in drop_set}
        out.append(TrainingRow(
            event_date=row.event_date, sport=row.sport, event_id=row.event_id,
            features=features, underdog_won=row.underdog_won,
        ))
    return out


def sport_model_card(settled: list, min_rows: int = 100) -> dict:
    training = build_training_rows(settled)
    if len(training) < min_rows or len({r.underdog_won for r in training}) < 2:
        return {"status": "INSUFFICIENT", "rows": len(training)}
    predictions = walk_forward_predict(training, min_train=max(20, min_rows // 2))
    summary = validation_summary(predictions)
    return {
        "status": "OK",
        "rows": len(training),
        "dates": len({r.event_date for r in training}),
        "features": len(feature_contract(training)),
        **summary,
    }


def sport_ablation(settled: list, min_rows: int = 100) -> list[dict]:
    training = build_training_rows(settled)
    if len(training) < min_rows or len({r.underdog_won for r in training}) < 2:
        return []
    baseline = None
    results = []
    for name, drop in FEATURE_FAMILIES.items():
        try:
            subset = _filter_features(training, drop)
            preds = walk_forward_predict(subset, min_train=max(20, min_rows // 2))
            summary = validation_summary(preds)
            entry = {
                "family": name,
                "features": len(feature_contract(subset)),
                "n": summary.get("n"),
                "brier": summary.get("brier"),
                "threshold": summary.get("threshold"),
                "selected_n": summary.get("selected_n"),
                "selected_hit_rate": summary.get("selected_hit_rate"),
                "wilson_lower_90": summary.get("wilson_lower_90"),
            }
            if name == "all":
                baseline = entry
            else:
                if entry["brier"] is not None and baseline and baseline["brier"] is not None:
                    entry["delta_brier"] = round(entry["brier"] - baseline["brier"], 6)
                if (entry["selected_hit_rate"] is not None and baseline
                        and baseline["selected_hit_rate"] is not None):
                    entry["delta_selected_hit"] = round(
                        entry["selected_hit_rate"] - baseline["selected_hit_rate"], 6)
            results.append(entry)
        except Exception as exc:
            results.append({"family": name, "error": f"{type(exc).__name__}: {exc}"})
    return results


def build_research(
    root: Path | str = ".",
    min_rows: int = 100,
    target_date: str | None = None,
    allow_research: bool = False,
) -> Path:
    from .feature_contracts import MODEL_TRAINING_ALLOWED

    if not MODEL_TRAINING_ALLOWED and not allow_research:
        raise RuntimeError(
            "research gate frozen until depth contracts complete; pass --research-override"
        )
    root = Path(root)
    target_date = target_date or today_iso()
    by_sport = load_settled(root / "data" / "reports")
    cards = {}
    ablations = {}
    for sport, rows in sorted(by_sport.items()):
        try:
            cards[sport] = sport_model_card(rows, min_rows)
        except Exception as exc:
            cards[sport] = {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
        try:
            ablations[sport] = sport_ablation(rows, min_rows)
        except Exception as exc:
            ablations[sport] = [{"family": "all", "error": f"{type(exc).__name__}: {exc}"}]
    research = {
        "target_date": target_date,
        "min_rows": min_rows,
        "sports": sorted(by_sport),
        "cards": cards,
        "ablations": ablations,
    }
    reports_dir = root / "data" / "reports"
    json_path = reports_dir / f"research_{target_date}.json"
    json_path.write_text(json.dumps(research, indent=2, sort_keys=True))
    md_path = reports_dir / f"research_{target_date}.md"
    md_path.write_text(_render_markdown(research))
    return md_path


def _render_markdown(research: dict) -> str:
    lines = [
        f"# Slumdog Research Gate — {research['target_date']}",
        "",
        f"Sports with ledgers: {len(research['sports'])}  |  min_rows: {research['min_rows']}",
        "",
        "## Model cards",
        "",
        "| Sport | Rows | Dates | Feat | Valid n | Brier | Thresh | Sel n | Hit | Wilson LB90 | Priced ROI |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for sport, card in research["cards"].items():
        if card.get("status") != "OK":
            lines.append(f"| {sport} | {card.get('rows', 0)} | — | — | INSUFFICIENT | | | | | | |")
            continue
        roi = card.get("priced_roi")
        lines.append(
            f"| {sport} | {card['rows']} | {card['dates']} | {card['features']} | "
            f"{card.get('n', 0)} | {card.get('brier')} | {card.get('threshold')} | "
            f"{card.get('selected_n', 0)} | {card.get('selected_hit_rate')} | "
            f"{card.get('wilson_lower_90')} | {f'{roi:+.1%}' if roi is not None else '—'} |"
        )
    lines.extend(["", "## Ablations (drop family, delta vs all-features)", "",
                  "| Sport | Family | Feat | Brier | ΔBrier | Sel hit | ΔSel hit |",
                  "|---|---:|---:|---:|---:|---:|---:|"])
    for sport, rows in research["ablations"].items():
        for entry in rows:
            if "error" in entry:
                lines.append(f"| {sport} | {entry['family']} | — | — | — | — | {entry['error']} |")
                continue
            db = entry.get("delta_brier")
            dh = entry.get("delta_selected_hit")
            lines.append(
                f"| {sport} | {entry['family']} | {entry['features']} | "
                f"{entry.get('brier')} | {db if db is not None else '—'} | "
                f"{entry.get('selected_hit_rate')} | {dh if dh is not None else '—'} |"
            )
    lines.extend([
        "",
        "Brier: lower is better. ΔBrier/ΔSel hit are relative to the all-features",
        "walk-forward baseline; positive ΔBrier means the dropped family helped.",
        "Research-only output: training stays frozen absent an explicit override.",
    ])
    return "\n".join(lines) + "\n"
