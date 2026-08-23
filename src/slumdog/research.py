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
import math
from collections import defaultdict
from pathlib import Path

from .analyze import _stream_ledger
from .clock import today_iso
from .ml_meta import TrainingRow, feature_contract
from .pipeline import settled_from_dict
from .training import build_training_rows, validation_summary, walk_forward_predict

# Feature families by key prefix in the training vector (see facets.py, football.py, basketball.py, tennis.py, hockey.py).
FEATURE_FAMILIES: dict[str, tuple[str, ...]] = {
    "all": (),
    "drop_price": (
        "displayed_odds", "implied_probability", "price_available",
        "fb_price_available", "fb_dog_price", "fb_favorite_price",
        "fb_draw_price", "fb_market_overround", "fb_dog_fair_implied_prob",
        "fb_favorite_fair_implied_prob", "fb_draw_fair_implied_prob",
        "fb_price_value_edge",
        "bb_price_available", "bb_dog_price", "bb_favorite_price",
        "bb_market_overround", "bb_dog_fair_implied_prob",
        "bb_favorite_fair_implied_prob", "bb_price_value_edge",
        "ten_price_available", "ten_dog_price", "ten_favorite_price",
        "ten_market_overround", "ten_dog_fair_implied_prob",
        "ten_favorite_fair_implied_prob", "ten_price_value_edge",
        "hk_price_available", "hk_dog_price", "hk_favorite_price",
        "hk_market_overround", "hk_dog_fair_implied_prob",
        "hk_favorite_fair_implied_prob", "hk_price_value_edge",
        "ba_price_available", "ba_dog_price", "ba_favorite_price",
        "ba_market_overround", "ba_dog_fair_implied_prob",
        "ba_favorite_fair_implied_prob", "ba_price_value_edge",
        "af_price_available", "af_dog_price", "af_favorite_price",
        "af_market_overround", "af_dog_fair_implied_prob",
        "af_favorite_fair_implied_prob", "af_price_value_edge",
        "rg_price_available", "rg_dog_price", "rg_favorite_price",
        "rg_draw_price", "rg_market_overround", "rg_dog_fair_implied_prob",
        "rg_favorite_fair_implied_prob", "rg_draw_fair_implied_prob",
        "rg_price_value_edge",
        "hb_price_available", "hb_dog_price", "hb_favorite_price",
        "hb_draw_price", "hb_market_overround", "hb_dog_fair_implied_prob",
        "hb_favorite_fair_implied_prob", "hb_draw_fair_implied_prob",
        "hb_price_value_edge",
        "vb_price_available", "vb_dog_price", "vb_favorite_price",
        "vb_market_overround", "vb_dog_fair_implied_prob",
        "vb_favorite_fair_implied_prob", "vb_price_value_edge",
        "es_price_available", "es_dog_price", "es_favorite_price",
        "es_market_overround", "es_dog_fair_implied_prob",
        "es_favorite_fair_implied_prob", "es_price_value_edge",
        "cr_price_available", "cr_dog_price", "cr_favorite_price",
        "cr_draw_price", "cr_market_overround", "cr_dog_fair_implied_prob",
        "cr_favorite_fair_implied_prob", "cr_draw_fair_implied_prob",
        "cr_price_value_edge",
        "mma_price_available", "mma_dog_price", "mma_favorite_price",
        "mma_market_overround", "mma_dog_fair_implied_prob",
        "mma_favorite_fair_implied_prob", "mma_price_value_edge",
    ),
    "drop_probability": (
        "forebet_dog_probability", "forebet_other_probability",
        "forebet_favorite_probability", "forebet_probability_gap",
        "forebet_probability_ratio", "forebet_entropy", "forebet_calls_dog",
        "draw_probability", "draw_probability_missing",
        "fb_forebet_dog_prob", "fb_forebet_favorite_prob", "fb_forebet_draw_prob",
        "fb_forebet_prob_gap", "fb_forebet_entropy", "fb_draw_pressure_ratio",
        "fb_favorite_dominance_ratio", "fb_forebet_calls_dog",
        "bb_forebet_dog_prob", "bb_forebet_favorite_prob",
        "bb_forebet_prob_gap", "bb_forebet_entropy",
        "bb_favorite_dominance_ratio", "bb_forebet_calls_dog",
        "ten_forebet_dog_prob", "ten_forebet_favorite_prob",
        "ten_forebet_prob_gap", "ten_forebet_entropy",
        "ten_favorite_dominance_ratio", "ten_forebet_calls_dog",
        "hk_forebet_dog_prob", "hk_forebet_favorite_prob",
        "hk_forebet_prob_gap", "hk_forebet_entropy",
        "hk_favorite_dominance_ratio", "hk_forebet_calls_dog",
        "ba_forebet_dog_prob", "ba_forebet_favorite_prob",
        "ba_forebet_prob_gap", "ba_forebet_entropy",
        "ba_favorite_dominance_ratio", "ba_forebet_calls_dog",
        "af_forebet_dog_prob", "af_forebet_favorite_prob",
        "af_forebet_prob_gap", "af_forebet_entropy",
        "af_favorite_dominance_ratio", "af_forebet_calls_dog",
        "rg_forebet_dog_prob", "rg_forebet_favorite_prob", "rg_forebet_draw_prob",
        "rg_forebet_prob_gap", "rg_forebet_entropy", "rg_draw_pressure_ratio",
        "rg_favorite_dominance_ratio", "rg_forebet_calls_dog",
        "hb_forebet_dog_prob", "hb_forebet_favorite_prob", "hb_forebet_draw_prob",
        "hb_forebet_prob_gap", "hb_forebet_entropy", "hb_draw_pressure_ratio",
        "hb_favorite_dominance_ratio", "hb_forebet_calls_dog",
        "vb_forebet_dog_prob", "vb_forebet_favorite_prob",
        "vb_forebet_prob_gap", "vb_forebet_entropy",
        "vb_favorite_dominance_ratio", "vb_forebet_calls_dog",
        "es_forebet_dog_prob", "es_forebet_favorite_prob",
        "es_forebet_prob_gap", "es_forebet_entropy",
        "es_favorite_dominance_ratio", "es_forebet_calls_dog",
        "cr_forebet_dog_prob", "cr_forebet_favorite_prob", "cr_forebet_draw_prob",
        "cr_forebet_prob_gap", "cr_forebet_entropy",
        "cr_favorite_dominance_ratio", "cr_forebet_calls_dog",
        "mma_forebet_dog_prob", "mma_forebet_favorite_prob",
        "mma_forebet_prob_gap", "mma_forebet_entropy",
        "mma_favorite_dominance_ratio", "mma_forebet_calls_dog",
    ),
    "drop_legacy": (
        "legacy_robber_score", "legacy_raw_confidence",
        "fb_legacy_robber_score", "fb_legacy_raw_confidence",
        "bb_legacy_robber_score", "bb_legacy_raw_confidence",
        "ten_legacy_robber_score", "ten_legacy_raw_confidence",
        "hk_legacy_robber_score", "hk_legacy_raw_confidence",
        "ba_legacy_robber_score", "ba_legacy_raw_confidence",
        "af_legacy_robber_score", "af_legacy_raw_confidence",
        "rg_legacy_robber_score", "rg_legacy_raw_confidence",
        "hb_legacy_robber_score", "hb_legacy_raw_confidence",
        "vb_legacy_robber_score", "vb_legacy_raw_confidence",
        "es_legacy_robber_score", "es_legacy_raw_confidence",
        "cr_legacy_robber_score", "cr_legacy_raw_confidence",
        "mma_legacy_robber_score", "mma_legacy_raw_confidence",
    ),
    "drop_h2h": (
        "h2h_games", "h2h_dog_win_rate",
        "fb_h2h_total_games", "fb_h2h_dog_win_rate", "fb_h2h_draw_rate",
        "fb_h2h_dog_undefeated_rate", "fb_h2h_has_dog_win",
        "bb_h2h_total_games", "bb_h2h_dog_win_rate", "bb_h2h_has_dog_win",
        "bb_h2h_period_dog_win_rate",
        "ten_h2h_total_matches", "ten_h2h_dog_win_rate", "ten_h2h_has_dog_win",
        "hk_h2h_total_games", "hk_h2h_dog_win_rate", "hk_h2h_has_dog_win",
        "hk_h2h_period_dog_win_rate",
        "ba_h2h_total_games", "ba_h2h_dog_win_rate", "ba_h2h_has_dog_win",
        "af_h2h_total_games", "af_h2h_dog_win_rate", "af_h2h_has_dog_win",
        "rg_h2h_total_games", "rg_h2h_dog_win_rate", "rg_h2h_has_dog_win",
        "hb_h2h_total_games", "hb_h2h_dog_win_rate", "hb_h2h_has_dog_win",
        "vb_h2h_total_games", "vb_h2h_dog_win_rate", "vb_h2h_has_dog_win",
        "es_h2h_total_games", "es_h2h_dog_win_rate", "es_h2h_has_dog_win",
        "cr_h2h_total_games", "cr_h2h_dog_win_rate", "cr_h2h_has_dog_win",
        "mma_h2h_total_games", "mma_h2h_dog_win_rate", "mma_h2h_has_dog_win",
    ),
    "drop_form": (
        "dog_recent_games", "dog_recent_win_rate", "favorite_recent_win_rate",
        "fb_dog_ppg", "fb_favorite_ppg", "fb_ppg_gap", "fb_dog_win_rate",
        "fb_favorite_win_rate", "fb_dog_draw_rate", "fb_favorite_draw_rate",
        "fb_dog_recent_games",
        "bb_dog_recent_win_rate", "bb_favorite_recent_win_rate",
        "bb_win_rate_gap", "bb_dog_recent_games",
        "ten_dog_recent_win_rate", "ten_favorite_recent_win_rate",
        "ten_win_rate_gap", "ten_dog_recent_games",
        "hk_dog_recent_win_rate", "hk_favorite_recent_win_rate",
        "hk_win_rate_gap", "hk_dog_recent_games",
        "ba_dog_recent_win_rate", "ba_favorite_recent_win_rate",
        "ba_win_rate_gap", "ba_dog_recent_games",
        "af_dog_recent_win_rate", "af_favorite_recent_win_rate",
        "af_win_rate_gap", "af_dog_recent_games",
        "rg_dog_recent_win_rate", "rg_favorite_recent_win_rate",
        "rg_win_rate_gap", "rg_dog_recent_games",
        "hb_dog_recent_win_rate", "hb_favorite_recent_win_rate",
        "hb_win_rate_gap", "hb_dog_recent_games",
        "vb_dog_recent_win_rate", "vb_favorite_recent_win_rate",
        "vb_win_rate_gap", "vb_dog_recent_games",
        "es_dog_recent_win_rate", "es_favorite_recent_win_rate",
        "es_win_rate_gap", "es_dog_recent_games",
        "cr_dog_recent_win_rate", "cr_favorite_recent_win_rate",
        "cr_win_rate_gap", "cr_dog_recent_games",
        "mma_dog_recent_win_rate", "mma_favorite_recent_win_rate",
        "mma_win_rate_gap", "mma_dog_recent_games",
    ),
}

# These are intentionally conservative. The cap is applied after sorting by
# event date, so the validation window is recent and reproducible rather than
# an arbitrary first-N sample. The signal map distinguishes a family that is
# structurally present in every generic vector from one with actual source
# evidence in this sport's ledger.
MAX_ROWS_PER_SPORT = 60_000
MAX_TEST_DATES = 180
FAMILY_SIGNAL_KEYS: dict[str, tuple[str, ...]] = {
    "drop_price": (
        "price_available", "fb_price_available", "fb_dog_price",
        "bb_price_available", "bb_dog_price",
        "ten_price_available", "ten_dog_price",
        "hk_price_available", "hk_dog_price",
        "ba_price_available", "ba_dog_price",
        "af_price_available", "af_dog_price",
        "rg_price_available", "rg_dog_price",
        "hb_price_available", "hb_dog_price",
        "vb_price_available", "vb_dog_price",
        "es_price_available", "es_dog_price",
        "cr_price_available", "cr_dog_price",
        "mma_price_available", "mma_dog_price",
    ),
    "drop_probability": (
        "forebet_dog_probability", "forebet_other_probability",
        "fb_forebet_dog_prob", "bb_forebet_dog_prob",
        "ten_forebet_dog_prob", "hk_forebet_dog_prob",
        "ba_forebet_dog_prob", "af_forebet_dog_prob",
        "rg_forebet_dog_prob", "hb_forebet_dog_prob",
        "vb_forebet_dog_prob", "es_forebet_dog_prob",
        "cr_forebet_dog_prob", "mma_forebet_dog_prob",
    ),
    "drop_legacy": (
        "legacy_robber_score", "fb_legacy_robber_score",
        "bb_legacy_robber_score", "ten_legacy_robber_score",
        "hk_legacy_robber_score", "ba_legacy_robber_score",
        "af_legacy_robber_score", "rg_legacy_robber_score",
        "hb_legacy_robber_score", "vb_legacy_robber_score",
        "es_legacy_robber_score", "cr_legacy_robber_score",
        "mma_legacy_robber_score",
    ),
    "drop_h2h": (
        "h2h_games", "fb_h2h_total_games",
        "bb_h2h_total_games", "ten_h2h_total_matches",
        "hk_h2h_total_games", "ba_h2h_total_games",
        "af_h2h_total_games", "rg_h2h_total_games",
        "hb_h2h_total_games", "vb_h2h_total_games",
        "es_h2h_total_games", "cr_h2h_total_games",
        "mma_h2h_total_games",
    ),
    "drop_form": (
        "dog_recent_games", "favorite_recent_win_rate",
        "fb_dog_ppg", "bb_dog_recent_win_rate",
        "ten_dog_recent_win_rate", "hk_dog_recent_win_rate",
        "ba_dog_recent_win_rate", "af_dog_recent_win_rate",
        "rg_dog_recent_win_rate", "hb_dog_recent_win_rate",
        "vb_dog_recent_win_rate", "es_dog_recent_win_rate",
        "cr_dog_recent_win_rate", "mma_dog_recent_win_rate",
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


def _cap_rows(rows: list, limit: int = MAX_ROWS_PER_SPORT) -> list:
    """Keep the most recent rows without mutating the caller's list."""
    if limit <= 0:
        raise ValueError("research row limit must be positive")
    if len(rows) <= limit:
        return rows
    ordered = sorted(rows, key=lambda row: (row.event_date, row.event_id))
    return ordered[-limit:]


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


def _has_family_signal(rows: list[TrainingRow], family: str) -> bool:
    """Return true only when a family has non-missing source evidence."""
    keys = FAMILY_SIGNAL_KEYS.get(family, ())
    for row in rows:
        for key in keys:
            value = row.features.get(key)
            try:
                if value is not None and math.isfinite(float(value)) and float(value) != 0.0:
                    return True
            except (TypeError, ValueError):
                continue
    return False


def _families_with_features(training: list[TrainingRow]) -> list[str]:
    # ``build_numeric_features`` emits missing-safe placeholder keys for every
    # sport. Checking key membership alone therefore does not tell us whether
    # an ablation has anything to remove; use source-signal keys instead.
    return [
        name for name, drop in FEATURE_FAMILIES.items()
        if not drop or _has_family_signal(training, name)
    ]


def sport_model_card(settled: list, min_rows: int = 100) -> dict:
    training = build_training_rows(_cap_rows(settled))
    if len(training) < min_rows or len({r.underdog_won for r in training}) < 2:
        return {"status": "INSUFFICIENT", "rows": len(training)}
    predictions = walk_forward_predict(
        training,
        min_train=max(20, min_rows // 2),
        max_test_dates=MAX_TEST_DATES,
    )
    summary = validation_summary(predictions)
    return {
        "status": "OK",
        "rows": len(training),
        "dates": len({r.event_date for r in training}),
        "features": len(feature_contract(training)),
        **summary,
    }


def sport_ablation(settled: list, min_rows: int = 100) -> list[dict]:
    training = build_training_rows(_cap_rows(settled))
    if len(training) < min_rows or len({r.underdog_won for r in training}) < 2:
        return []
    baseline = None
    results = []
    for name in _families_with_features(training):
        drop = FEATURE_FAMILIES[name]
        try:
            subset = _filter_features(training, drop)
            preds = walk_forward_predict(
                subset,
                min_train=max(20, min_rows // 2),
                max_test_dates=MAX_TEST_DATES,
            )
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
        "limits": {
            "max_rows_per_sport": MAX_ROWS_PER_SPORT,
            "max_test_dates": MAX_TEST_DATES,
            "families": "only families with non-missing source signal",
        },
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
