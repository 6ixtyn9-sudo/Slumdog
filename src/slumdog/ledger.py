"""Append-only frozen Slumdog candidate ledgers."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .contracts import RobberCandidate


def candidate_key(candidate: RobberCandidate) -> tuple[str, str, int]:
    return candidate.sport, candidate.event_id, candidate.participant_index


def freeze_candidates(
    target_date: str,
    candidates: Iterable[RobberCandidate],
    root: Path | str = ".",
) -> Path:
    """Append new identities while preserving the first frozen payload."""
    root = Path(root)
    path = root / "data" / "ledgers" / f"robbers_{target_date}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: list[dict] = []
    if path.exists():
        try:
            loaded = json.loads(path.read_text())
            if isinstance(loaded, list):
                existing = [item for item in loaded if isinstance(item, dict)]
        except Exception:
            existing = []
    seen = {
        (str(item.get("sport")), str(item.get("event_id")), int(item.get("participant_index") or 0))
        for item in existing
    }
    for candidate in candidates:
        key = candidate_key(candidate)
        if key in seen:
            continue
        payload = candidate.to_dict()
        payload["frozen_at"] = datetime.now(timezone.utc).isoformat()
        existing.append(payload)
        seen.add(key)
    path.write_text(json.dumps(existing, indent=2, sort_keys=True))
    return path
