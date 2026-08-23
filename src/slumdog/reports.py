"""Operator-facing Slumdog shadow suggestions."""
from __future__ import annotations

import json
from pathlib import Path


def render_suggestions(ledger_path: Path | str, target_date: str, root: Path | str = ".") -> Path:
    root = Path(root)
    ledger_path = Path(ledger_path)
    rows = json.loads(ledger_path.read_text()) if ledger_path.exists() else []
    rows = [row for row in rows if isinstance(row, dict)]
    rows.sort(key=lambda row: (
        -float(row.get("ml_probability") if row.get("ml_probability") is not None
               else (row.get("legacy_confidence") or 0) / 100.0),
        -float(row.get("score") or 0),
        str(row.get("sport") or ""),
        str(row.get("event_id") or ""),
    ))
    priced = sum(row.get("price") is not None for row in rows)
    lines = [
        f"SLUMDOG SHADOW SUGGESTIONS — {target_date}",
        "=" * 72,
        f"Qualifying Robbers: {len(rows)}  |  Priced by Forebet: {priced}  |  Unpriced: {len(rows)-priced}",
        "STATUS: SHADOW RESEARCH — NOT CERTIFIED",
        "",
    ]
    if not rows:
        lines.extend(["NO QUALIFYING ROBBERS", "", "No quota is filled when evidence is insufficient."])
    for index, row in enumerate(rows, 1):
        sport = str(row.get("sport") or "?").replace("_", " ").upper()
        participant = row.get("participant") or "?"
        opponent = row.get("opponent") or "?"
        state = row.get("state") or "SHADOW"
        confidence = float(row.get("legacy_confidence") or 0)
        score = float(row.get("score") or 0)
        price = row.get("price")
        price_text = f"@{float(price):.2f}" if price is not None else "PRICE MISSING"
        implied = row.get("implied_probability")
        implied_text = f"{float(implied):.1%}" if implied is not None else "n/a"
        ml = row.get("ml_probability")
        ml_text = f"{float(ml):.1%}" if ml is not None else "PENDING TRAINING"
        legacy_line = (
            f"Ma Golide forensic confidence: {confidence:.0f}%  |  Robber score: {score:.0f}"
            if row.get("legacy_qualified", True)
            else "Ma Golide forensic baseline: did not independently qualify"
        )
        dog_probability = row.get("forebet_underdog_probability")
        favorite_probability = row.get("forebet_favorite_probability")
        probability_line = (
            f"Forebet: underdog {float(dog_probability):.1%} vs favourite {float(favorite_probability):.1%}"
            if dog_probability is not None and favorite_probability is not None
            else "Forebet participant probabilities: incomplete"
        )
        lines.extend([
            f"#{index} [{state}] {sport}",
            f"   {participant} to upset {opponent}  {price_text}",
            f"   {probability_line}",
            f"   {legacy_line}",
            f"   Price-implied probability: {implied_text}  |  Slumdog ML: {ml_text}",
            f"   Underdog basis: {row.get('underdog_basis') or '?'}",
        ])
        if ml is not None:
            hit_rate = row.get("ml_validation_hit_rate")
            lines.append(
                f"   ML validation: train={row.get('ml_train_rows') or 0}, "
                f"selected n={row.get('ml_validation_n') or 0}, "
                f"hit={float(hit_rate):.1%} " if hit_rate is not None else
                f"   ML validation: train={row.get('ml_train_rows') or 0}, selected n=0"
            )
            lines.append(
                f"   ML threshold: {float(row.get('ml_threshold') or 0):.0%}  |  "
                f"Wilson LB90: {float(row.get('ml_validation_wilson_lower') or 0):.1%}  |  "
                f"Brier: {row.get('ml_validation_brier')}"
            )
            roi = row.get("ml_validation_priced_roi")
            lines.append(
                f"   Priced validation: n={row.get('ml_validation_priced_n') or 0}, "
                f"ROI={float(roi):+.1%}" if roi is not None else
                "   Priced validation: unavailable"
            )
        lines.append("   Reasons:")
        for reason in row.get("reasons") or []:
            lines.append(f"     - {reason}")
        if price is None:
            lines.append("   Warning: no Forebet price; ROI/value cannot be calculated.")
        lines.append("")
    lines.extend([
        "Ma Golide confidence is reproduced for forensic comparison and is not",
        "a learned win probability. Actionable certification is disabled.",
    ])
    output = root / "data" / "reports" / f"suggestions_{target_date}.txt"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n")
    return output
