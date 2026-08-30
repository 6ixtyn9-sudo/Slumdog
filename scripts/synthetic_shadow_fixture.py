"""Milestone 7D — Synthetic completed shadow-run fixture generator.

Builds an ENTIRELY SYNTHETIC completed shadow run inside an explicitly
supplied external root (e.g. a runner's temporary directory) by driving
the REAL merged evaluator (``slumdog.shadow_evaluator.evaluate_from_disk``)
with synthetic inputs and an injected decision clock.

Purpose: cloud-only second-copy verification of the Milestone 7B bundle
tooling without any real capture, real data, or network access.

Guarantees
----------
- Synthetic names only ("Synthetic Alpha" ... "Synthetic Zeta"); no
  retained real participant/event data is read, copied, or generated.
- Reads from the repository ONLY the two frozen config files
  (``config/research_baselines_v1.json``, ``config/shadow_evaluator_v1.json``),
  after verifying their canonical SHA-256 digests.
- Never writes inside the repository; every generated file lands under
  the supplied external root.
- No network access of any kind (no network-module imports here,
  and no collector code is invoked).
- Deterministic: fixed synthetic inputs, fixed injected decision clock,
  and gzip mtime=0 history bytes, so every invocation produces the same
  input/decision digests and the same run_id.
- Expected result: ``SHADOW_SELECTIONS_EMITTED`` with exactly one
  ``PRIMARY_SHADOW_SELECTION`` and two ``TOP3_EVALUATION_COHORT``
  selections on a single football sport-day. The script fails closed
  (non-zero exit) if the evaluator produces anything else.

The fixture construction reuses the proven tested shapes from
``tests/test_shadow_bundle.py`` (settled-history row schema, capture
sidecar/receipt schema, football direct-route raw-JSON body ``[rows, {}]``)
so the generated run is byte-compatible with the Milestone 7B bundler.

CLI:

    python scripts/synthetic_shadow_fixture.py --root <external-dir>

Prints a JSON summary (ids/digests only — no participant data) and
exits 0 on success, 2 on any failure.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

# --- frozen synthetic timeline (injected, safely before the cutoff) -------
TARGET_DATE = "2026-09-02"
CAPTURED_AT = "2026-08-30T10:00:00Z"
STAMP = "20260830T100000Z"
DECISION_CLOCK = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)
# Frozen cutoff for 2026-09-02 is 2026-09-01T00:00:00Z (target 00:00Z minus
# 24h); both CAPTURED_AT and the decision clock sit 36h before it.

# --- frozen config canonical hashes (must match the merged constants) -----
EXPECTED_FROZEN_CONFIG_SHA256 = (
    "666dabe7ea21e11867cf4816f4c2edcd771247646c6c9d7726c22611cda700a1"
)
EXPECTED_DECLARATION_SHA256 = (
    "dd08976a262e7a1882a4e29846612094c20447faf587c01a42608d57f4f4d597"
)

SYNTHETIC_PAIRINGS = (
    ("ab", "Synthetic Alpha", "Synthetic Beta", "900001"),
    ("gd", "Synthetic Gamma", "Synthetic Delta", "900002"),
    ("ez", "Synthetic Epsilon", "Synthetic Zeta", "900003"),
)


class SyntheticFixtureError(Exception):
    """Raised when the synthetic fixture cannot be built or is invalid."""


def _canonical_sha256(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _settled_row(event_id: str, date: str, p1: str, p2: str,
                 winner: int, prob1: float, prob2: float, draw: float) -> dict:
    return {
        "event_id": event_id, "sport": "football", "event_date": date,
        "participant_1": p1, "participant_2": p2, "winner_index": winner,
        "score_1": 2.0, "score_2": 1.0, "probability_1": prob1,
        "probability_2": prob2, "draw_probability": draw,
        "forebet_pick": None, "disposition": "SETTLED",
    }


def synthetic_history_rows() -> list[dict]:
    """42 synthetic settled rows: per pairing 6 home + 6 away priors + 2 H2H.

    The counts satisfy the frozen R2 eligibility minimums (>=5 prior games
    per side, >=1 H2H) exactly as the tested bundle fixtures do.
    """
    rows: list[dict] = []
    for tag, home, away, _event_id in SYNTHETIC_PAIRINGS:
        for i in range(6):
            rows.append(_settled_row(
                f"syn_{tag}_h{i:02d}", f"2025-06-{(i % 28) + 1:02d}",
                home, away, 1, 0.55, 0.30, 0.15))
        for i in range(6):
            rows.append(_settled_row(
                f"syn_{tag}_a{i:02d}", f"2025-07-{(i % 28) + 1:02d}",
                away, home, 1, 0.50, 0.30, 0.20))
        for i in range(2):
            rows.append(_settled_row(
                f"syn_{tag}_h2h{i}", f"2025-08-{(i % 28) + 1:02d}",
                home, away, 1, 0.55, 0.30, 0.15))
    return rows


def synthetic_forebet_rows() -> list[dict]:
    """Three synthetic football rows in the real Forebet JSON row shape."""
    kickoffs = ("15:00", "18:00", "20:00")
    probs = (("50", "10", "40"), ("45", "15", "40"), ("48", "14", "38"))
    rows = []
    for (_tag, home, away, event_id), kickoff, (p1, px, p2) in zip(
            SYNTHETIC_PAIRINGS, kickoffs, probs):
        rows.append({
            "id": event_id, "HOST_NAME": home, "GUEST_NAME": away,
            "Pred_1": p1, "Pred_X": px, "Pred_2": p2,
            "best_odd_1": "2.00", "best_odd_2": "2.50", "best_odd_X": "10.00",
            "short_tag": "SYN", "DATE_BAH": f"{TARGET_DATE} {kickoff}",
            "host_sc_pr": "1", "guest_sc_pr": "1", "goalsavg": "2.5",
            "Host_SC": None, "Guest_SC": None, "comment": "",
        })
    return rows


def _deterministic_gzip_bytes(payload: bytes) -> bytes:
    """gzip with mtime=0 and a fixed header so bytes are reproducible."""
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf,
                       mtime=0, compresslevel=9) as gz:
        gz.write(payload)
    return buf.getvalue()


def build_synthetic_run(root: Path) -> dict[str, Any]:
    """Create a synthetic completed shadow run under ``root``.

    ``root`` must not exist (fail closed — no overwrite of any kind).
    Returns a summary dict of ids/digests/paths (no participant data).
    """
    root = Path(root)
    if root.exists():
        raise SyntheticFixtureError(
            f"refusing to touch existing path: {root}")

    # 1) Synthetic repository root with ONLY the two verified config copies.
    frozen_src = REPO_ROOT / "config" / "research_baselines_v1.json"
    decl_src = REPO_ROOT / "config" / "shadow_evaluator_v1.json"
    for p in (frozen_src, decl_src):
        if not p.is_file():
            raise SyntheticFixtureError(f"required repository config missing: {p}")
    frozen_obj = json.loads(frozen_src.read_text())
    decl_obj = json.loads(decl_src.read_text())
    frozen_sha = _canonical_sha256(frozen_obj)
    decl_sha = _canonical_sha256(decl_obj)
    if frozen_sha != EXPECTED_FROZEN_CONFIG_SHA256:
        raise SyntheticFixtureError(
            f"frozen baseline config canonical hash drift: {frozen_sha}")
    if decl_sha != EXPECTED_DECLARATION_SHA256:
        raise SyntheticFixtureError(
            f"shadow declaration canonical hash drift: {decl_sha}")

    (root / "config").mkdir(parents=True)
    frozen_dst = root / "config" / "research_baselines_v1.json"
    decl_dst = root / "config" / "shadow_evaluator_v1.json"
    shutil.copy(frozen_src, frozen_dst)
    shutil.copy(decl_src, decl_dst)

    # 2) Synthetic capture: direct-route raw-JSON football body + sidecar.
    body = json.dumps([synthetic_forebet_rows(), {}]).encode("utf-8")
    body_sha = hashlib.sha256(body).hexdigest()
    body_dir = root / "data" / "raw" / "football" / TARGET_DATE
    body_dir.mkdir(parents=True)
    body_path = body_dir / f"{STAMP}_{body_sha[:12]}.txt"
    sidecar_path = body_dir / f"{STAMP}_{body_sha[:12]}.json"
    body_path.write_bytes(body)
    sidecar = {
        "sport": "football", "target_date": TARGET_DATE,
        "captured_at": CAPTURED_AT,
        "source_url": f"https://synthetic.invalid/football/{TARGET_DATE}",
        "body_format": "json", "sha256": body_sha, "bytes": len(body),
        "body_path": body_path.relative_to(root).as_posix(),
        "metadata_path": sidecar_path.relative_to(root).as_posix(),
        "route": "direct",
    }
    sidecar_path.write_text(json.dumps(sidecar, indent=2, sort_keys=True))
    receipt = {
        "target_date": TARGET_DATE, "generated_at": CAPTURED_AT,
        "captured": [sidecar], "failures": [], "reused": 0,
        "football_markets": None,
    }
    receipt_path = root / "data" / "reports" / f"capture_{TARGET_DATE}.json"
    (root / "data" / "reports").mkdir(parents=True)
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True))

    # 3) Deterministic synthetic history ledger (gzip mtime=0).
    hist_rows = synthetic_history_rows()
    hist_payload = "".join(
        json.dumps(row) + "\n" for row in hist_rows).encode("utf-8")
    gz_path = root / "data" / "reports" / "history_football.jsonl.gz"
    gz_path.write_bytes(_deterministic_gzip_bytes(hist_payload))

    # 4) REAL evaluator, injected decision clock, synthetic root only.
    from slumdog.shadow_evaluator import evaluate_from_disk
    result = evaluate_from_disk(
        target_date=TARGET_DATE,
        capture_receipt_path=receipt_path,
        declaration_path=decl_dst,
        repo_root=root,
        history_paths=[gz_path],
        decision_clock=DECISION_CLOCK,
        history_max_interim_bytes=10 * 1024 * 1024,
    )

    if result.run_status != "SHADOW_SELECTIONS_EMITTED":
        raise SyntheticFixtureError(
            f"synthetic fixture produced {result.run_status!r} instead of "
            "SHADOW_SELECTIONS_EMITTED")
    statuses = [s["status"] for s in result.payload["selections"]]
    if (statuses.count("PRIMARY_SHADOW_SELECTION") != 1
            or statuses.count("TOP3_EVALUATION_COHORT") != 2):
        raise SyntheticFixtureError(
            f"unexpected synthetic selection shape: {statuses}")

    return {
        "synthetic_root": str(root),
        "run_dir": result.artifact_dir,
        "run_id": result.run_id,
        "run_status": result.run_status,
        "target_date": result.target_date,
        "decision_committed_at": result.decision_committed_at,
        "safe_cutoff_utc": result.manifest.get("safe_cutoff_utc"),
        "selection_count": len(statuses),
        "selection_statuses": statuses,
        "input_digest": result.manifest.get("input_digest"),
        "decision_digest": result.manifest.get("decision_digest"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="synthetic_shadow_fixture",
        description=(
            "Build an entirely synthetic completed shadow run (for bundle "
            "tooling verification only). Writes only under --root, which "
            "must not already exist. No network, no real data."),
    )
    parser.add_argument("--root", required=True, type=Path,
                        help="External directory to build the synthetic run in "
                             "(created; must not already exist)")
    args = parser.parse_args(argv)
    try:
        summary = build_synthetic_run(args.root)
    except SyntheticFixtureError as e:
        print(f"SYNTHETIC_FIXTURE_FAILED: {e}", file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
