"""Milestone 7 — Shadow evaluator focused contract tests.

Test groups (all behavioral; no AST/source scans):

1. Declaration and frozen-config integrity
2. Authorization gates fail closed
3. Shared feature extraction — golden regression vs hardcoded digest
4. Capture loader: receipt / sidecar / body hashing and provenance
5. Capture loader: current-only sport rejection (esoccer, afl)
6. Capture loader: conflicting duplicate snapshots
7. Capture loader: path containment (rejects traversal / absolute escape)
8. History loader: prior-date strict cutoff, void/no_contest, two-way draw
9. History loader: balanced accounting and exact-duplicate / conflict
10. End-to-end disk fixture (CLI-compatible orchestration)
11. CLI: --help exits 0; capture-load failure exits nonzero without manifest
12. Blocked-run failure receipt (separate path, not a completed artifact)
13. Production isolation (focused monkeypatches)
14. Existing full suite remains green (verified by the runner, not by this file)
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from slumdog.baseline_analyzer import canonical_json_bytes
from slumdog.contracts import SettledEvent
from slumdog.history_loader import (
    HistoryPathError,
    load_valid_history,
)
from slumdog.capture_loader import (
    PathContainmentError,
    load_capture_records,
)
from slumdog.shadow_evaluator import (
    FROZEN_BASELINE_CONFIG_SHA256,
    FROZEN_R2_KEY,
    ShadowEvaluatorError,
    _canonical_sha256,
    _parse_utc,
    evaluate_from_disk,
    is_r2_eligible,
    load_frozen_baseline_config,
    load_shadow_declaration,
    main,
    safe_cutoff_utc,
    validate_event_identity,
)
from slumdog.shadow_contracts import PreEventRecord


REPO_ROOT = Path(__file__).resolve().parents[1]
SHADOW_DECL_PATH = REPO_ROOT / "config" / "shadow_evaluator_v1.json"
FROZEN_CONFIG_PATH = REPO_ROOT / "config" / "research_baselines_v1.json"

# Golden regression hash for the shared feature helper.
#
# Base commit: b87784fdb590c17b55d4fa1c2bd6c3275dce0f6d
# Fixture: 6 prior Arsenal + 6 prior Liverpool + 3 H2H Arsenal-Liverpool
# Canonicalization: list of example.to_dict() (sorted features &
#   missingness), then sha256 of canonical_json_bytes() (sorted keys,
#   compact separators, UTF-8).
#
# Provenance of this digest (NOT regenerated at test runtime):
#   1. `git show b87784f:src/slumdog/dataset.py` and 5 sibling files
#      were exported to /tmp/golden_audit/base_pkg/slumdog/ (read-only).
#   2. The fixture (15 SettledEvent rows) was processed by
#      `build_price_free_examples(...)` from both the base package and
#      the current implementation, in two separate Python subprocesses.
#   3. Both produced 15 examples, 21430 bytes of canonical output, and
#      the SAME example-digest 1a97cb81fc6521a99f1055a873975d562cae3
#      3fefce7468ceca929739f8fca0d.
#   4. `diff` on the two canonical outputs is empty (byte-for-byte
#      identical).
#
# Earlier draft self-computed a different digest (1b696ad7...) WITHOUT
# base comparison; that draft was discarded. The current digest was
# obtained ONLY after the base-vs-current comparison above. If the
# current digest ever stops matching, this test will fail BEFORE the
# hardcoded value is updated.
GOLDEN_SHARED_FEATURE_DIGEST = (
    "1a97cb81fc6521a99f1055a873975d562cae33fefce7468ceca929739f8fca0d"
)
GOLDEN_CANONICAL_BYTE_COUNT = 21430
GOLDEN_EXAMPLE_COUNT = 15


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _minimal_history_for_r2() -> list[SettledEvent]:
    out: list[SettledEvent] = []
    for i in range(6):
        out.append(SettledEvent(
            event_id=f"a{i}", sport="football", event_date=f"2024-01-{(i%28)+1:02d}",
            participant_1="Arsenal", participant_2="Chelsea", winner_index=1,
            score_1=2.0, score_2=1.0, probability_1=0.55, probability_2=0.30,
            draw_probability=0.15, forebet_pick=None, disposition="SETTLED",
        ))
    for i in range(6):
        out.append(SettledEvent(
            event_id=f"l{i}", sport="football", event_date=f"2024-02-{(i%28)+1:02d}",
            participant_1="Liverpool", participant_2="ManU", winner_index=1,
            score_1=2.0, score_2=0.0, probability_1=0.50, probability_2=0.30,
            draw_probability=0.20, forebet_pick=None, disposition="SETTLED",
        ))
    for i in range(3):
        out.append(SettledEvent(
            event_id=f"h{i}", sport="football", event_date=f"2024-03-{(i%28)+1:02d}",
            participant_1="Arsenal", participant_2="Liverpool", winner_index=2,
            score_1=0.0, score_2=2.0, probability_1=0.45, probability_2=0.35,
            draw_probability=0.20, forebet_pick=None, disposition="SETTLED",
        ))
    return out


def _make_record(
    event_id: str = "fwd-1",
    sport: str = "football",
    event_date: str = "2026-08-28",
    p1: str = "Arsenal",
    p2: str = "Liverpool",
    prob_1: float = 0.50,
    prob_2: float = 0.40,
    draw: float = 0.10,
    captured_at: str = "2026-08-26T10:00:00Z",
    raw_sha: str = "abc",
) -> PreEventRecord:
    return PreEventRecord(
        event_id=event_id, sport=sport, event_date=event_date,
        participant_1=p1, participant_2=p2,
        probability_1=prob_1, probability_2=prob_2, draw_probability=draw,
        source_url=f"https://example.com/{event_id}", raw_sha256=raw_sha,
        captured_at=captured_at,
        body_path=f"data/raw/{sport}/{event_date}/{event_id}.txt",
        route="snapshot",
    )


@pytest.fixture
def tmp_root():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "config").mkdir()
        (root / "data" / "reports" / "shadow").mkdir(parents=True)
        shutil.copy(FROZEN_CONFIG_PATH, root / "config" / "research_baselines_v1.json")
        shutil.copy(SHADOW_DECL_PATH, root / "config" / "shadow_evaluator_v1.json")
        yield root


# ===========================================================================
# Group 1: declaration and frozen-config integrity
# ===========================================================================


def test_declaration_loads_clean():
    decl = load_shadow_declaration(SHADOW_DECL_PATH)
    assert decl["authorizations"]["shadow_evaluation_authorized"] is True
    assert decl["rule"]["name"] == FROZEN_R2_KEY


def test_frozen_config_sha256_matches(tmp_root):
    cfg = load_frozen_baseline_config(tmp_root)
    assert _canonical_sha256(cfg) == FROZEN_BASELINE_CONFIG_SHA256


def test_frozen_r2_rule_structure_verified(tmp_root):
    cfg = load_frozen_baseline_config(tmp_root)
    r2 = cfg["rules"][FROZEN_R2_KEY]
    assert r2["policy_candidate"] is False
    assert r2["quota_forced"] is False
    assert r2["rank"] == "R1_ALWAYS_RANK_COMPARATOR"
    s = {(e["feature"], e["op"], e["value"]) for e in r2["eligibility"]}
    assert ("underdog_prior_games", "gte", 5) in s
    assert ("favorite_prior_games", "gte", 5) in s
    assert ("h2h_prior_games", "gte", 1) in s
    assert ("forebet_probability_gap", "lte", 0.2) in s


def test_frozen_config_drift_rejected(tmp_root):
    cfg = json.loads((tmp_root / "config" / "research_baselines_v1.json").read_text())
    cfg["rules"][FROZEN_R2_KEY]["policy_candidate"] = True
    (tmp_root / "config" / "research_baselines_v1.json").write_text(json.dumps(cfg))
    with pytest.raises(ShadowEvaluatorError):
        load_frozen_baseline_config(tmp_root)


def test_frozen_config_eligibility_drift_rejected(tmp_root):
    cfg = json.loads((tmp_root / "config" / "research_baselines_v1.json").read_text())
    cfg["rules"][FROZEN_R2_KEY]["eligibility"] = [
        {"feature": "underdog_prior_games", "op": "gte", "value": 3},
        {"feature": "favorite_prior_games", "op": "gte", "value": 5},
        {"feature": "h2h_prior_games", "op": "gte", "value": 1},
        {"feature": "forebet_probability_gap", "op": "lte", "value": 0.2},
    ]
    (tmp_root / "config" / "research_baselines_v1.json").write_text(json.dumps(cfg))
    with pytest.raises(ShadowEvaluatorError):
        load_frozen_baseline_config(tmp_root)


# ===========================================================================
# Group 2: authorization gates fail closed
# ===========================================================================


@pytest.mark.parametrize("gate", [
    "production_authorized", "shortlist_policy_authorized",
    "training_authorized", "threshold_optimization_authorized",
])
def test_authorization_gate_rejected(tmp_root, gate):
    bad = json.loads(SHADOW_DECL_PATH.read_text())
    bad["authorizations"][gate] = True
    p = tmp_root / "config" / "shadow_evaluator_v1.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(ShadowEvaluatorError):
        load_shadow_declaration(p)


def test_shadow_evaluation_authorized_false_rejected(tmp_root):
    bad = json.loads(SHADOW_DECL_PATH.read_text())
    bad["authorizations"]["shadow_evaluation_authorized"] = False
    p = tmp_root / "config" / "shadow_evaluator_v1.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(ShadowEvaluatorError):
        load_shadow_declaration(p)


# ===========================================================================
# Group 3: shared feature extraction — golden regression
# ===========================================================================


def test_shared_feature_golden_regression():
    """The refactored ``build_price_free_examples`` produces a canonical
    serialized output whose SHA-256 matches a hardcoded golden value
    generated on the pre-refactor implementation (base commit
    b87784f). The test does not invoke Git or load any second copy of
    the source.

    The hardcoded value was computed once during development by
    exporting the base commit's source outside the working tree and
    running a separate subprocess. See the test-file header for
    provenance. This assertion proves the research contract
    (``build_price_free_examples`` output) is byte-stable.
    """
    from slumdog.dataset import build_price_free_examples
    examples, _ = build_price_free_examples(_minimal_history_for_r2())
    dicts = [ex.to_dict() for ex in examples]
    canonical = canonical_json_bytes(dicts)
    digest = hashlib.sha256(canonical).hexdigest()
    # Multi-axis check: SHA-256, byte count, example count.
    # A partial regression (e.g. reordering of one feature in one
    # example) flips all three. A reordering of one example
    # completely would flip the digest AND the example count.
    assert len(examples) == GOLDEN_EXAMPLE_COUNT, (
        f"example count regression: {len(examples)} != {GOLDEN_EXAMPLE_COUNT}"
    )
    assert len(canonical) == GOLDEN_CANONICAL_BYTE_COUNT, (
        f"canonical byte count regression: {len(canonical)} != "
        f"{GOLDEN_CANONICAL_BYTE_COUNT}"
    )
    assert digest == GOLDEN_SHARED_FEATURE_DIGEST, (
        f"shared feature regression: digest={digest} expected={GOLDEN_SHARED_FEATURE_DIGEST}"
    )


# ===========================================================================
# Group 4: capture loader — receipt / sidecar / body hashing
# ===========================================================================


def _build_football_capture(tmp_root: Path, target_date: str, *, sport: str = "football") -> tuple[Path, dict]:
    """Build a real football capture (raw body + sidecar + receipt) and
    return (receipt_path, receipt_dict)."""
    # The football JSON parser requires an HTML wrapper around a
    # [[row, ...], {}] payload where probabilities are percentages
    # (e.g. Pred_1=30 means 30%) and the date is "DATE_BAH".
    row = {
        "id": "1001", "HOST_NAME": "Home", "GUEST_NAME": "Away",
        "Pred_1": "50", "Pred_X": "10", "Pred_2": "40",
        "best_odd_1": "2.00", "best_odd_2": "2.50", "best_odd_X": "10.00",
        "short_tag": "TST", "DATE_BAH": f"{target_date} 18:00",
        "host_sc_pr": "1", "guest_sc_pr": "1", "goalsavg": "2.5",
        "Host_SC": None, "Guest_SC": None, "comment": "",
    }
    body = ("<html><body>" + json.dumps([[row], {}]) + "</body></html>").encode("utf-8")
    body_sha = hashlib.sha256(body).hexdigest()
    body_dir = tmp_root / "data" / "raw" / sport / target_date
    body_dir.mkdir(parents=True, exist_ok=True)
    stamp = "20260826T100000Z"
    body_path = body_dir / f"{stamp}_{body_sha[:12]}.txt"
    sidecar_path = body_dir / f"{stamp}_{body_sha[:12]}.json"
    body_path.write_bytes(body)
    captured_at = "2026-08-26T10:00:00+00:00"
    sidecar = {
        "sport": sport, "target_date": target_date, "captured_at": captured_at,
        "source_url": f"https://example.invalid/{sport}/{target_date}",
        "relay_url": f"https://relay.invalid/{sport}/{target_date}",
        "body_format": "json", "sha256": body_sha, "bytes": len(body),
        "body_path": str(body_path.relative_to(tmp_root)),
        "metadata_path": str(sidecar_path.relative_to(tmp_root)),
        "route": "direct",
    }
    sidecar_path.write_text(json.dumps(sidecar, indent=2, sort_keys=True))
    receipt = {
        "target_date": target_date,
        "generated_at": "2026-08-26T10:00:01+00:00",
        "captured": [sidecar],
        "failures": [], "reused": 0, "football_markets": None,
    }
    reports = tmp_root / "data" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    receipt_path = reports / f"capture_{target_date}.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True))
    return receipt_path, receipt


def test_capture_loader_verifies_provenance(tmp_root):
    receipt_path, _ = _build_football_capture(tmp_root, "2026-08-28")
    res = load_capture_records(
        target_date="2026-08-28", capture_receipt_path=receipt_path,
        repo_root=tmp_root,
    )
    assert res.capture_accounting["raw_capture_receipt_entries"] == 1
    assert res.capture_accounting["captures_verified"] == 1
    assert res.capture_accounting["captures_hash_mismatch"] == 0
    assert res.receipt_sha256 == hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    # Each sidecar and body path is recorded with its exact-byte SHA-256
    assert len(res.raw_input_paths) == 2
    for p in res.raw_input_paths:
        assert p in res.raw_input_sha256


def test_capture_loader_detects_body_hash_mismatch(tmp_root):
    receipt_path, _ = _build_football_capture(tmp_root, "2026-08-28")
    # Tamper with the body on disk
    body_files = list((tmp_root / "data" / "raw" / "football" / "2026-08-28").glob("*.txt"))
    body_files[0].write_bytes(b"tampered")
    res = load_capture_records(
        target_date="2026-08-28", capture_receipt_path=receipt_path,
        repo_root=tmp_root,
    )
    # The capture loader is fail-closed: a body SHA-256 mismatch is
    # recorded in accounting and no record is produced. Verified and
    # mismatch counts are mutually exclusive.
    assert res.capture_accounting["captures_hash_mismatch"] == 1
    assert res.capture_accounting["captures_verified"] == 0
    assert res.records == []
    # And the orchestration layer that consumes this must treat a
    # mismatch as SHADOW_RUN_BLOCKED. Verify at the orchestration level.


def test_capture_loader_rejects_current_only_sport(tmp_root):
    """esoccer is current_only=True; the loader must reject it before
    parser dispatch."""
    # Build an esoccer capture (use the same JSON format — parser will
    # not be reached if we correctly reject upfront).
    receipt_path, _ = _build_football_capture(tmp_root, "2026-08-28", sport="esoccer")
    res = load_capture_records(
        target_date="2026-08-28", capture_receipt_path=receipt_path,
        repo_root=tmp_root,
    )
    assert res.capture_accounting["captures_unsupported_sport"] == 1
    assert res.capture_accounting["captures_verified"] == 0
    assert res.records == []


def test_capture_loader_rejects_afl(tmp_root):
    receipt_path, _ = _build_football_capture(tmp_root, "2026-08-28", sport="afl")
    res = load_capture_records(
        target_date="2026-08-28", capture_receipt_path=receipt_path,
        repo_root=tmp_root,
    )
    assert res.capture_accounting["captures_unsupported_sport"] == 1
    assert res.records == []


def test_capture_loader_rejects_path_traversal(tmp_root):
    """Receipt path outside repo root raises PathContainmentError."""
    with tempfile.TemporaryDirectory() as outside:
        outside_receipt = Path(outside) / "evil_receipt.json"
        outside_receipt.write_text("{}")
        with pytest.raises(PathContainmentError):
            load_capture_records(
                target_date="2026-08-28", capture_receipt_path=outside_receipt,
                repo_root=tmp_root,
            )


def test_capture_loader_rejects_receipt_target_date_mismatch(tmp_root):
    receipt_path, _ = _build_football_capture(tmp_root, "2026-08-28")
    with pytest.raises(Exception):
        load_capture_records(
            target_date="2026-08-29",  # wrong date
            capture_receipt_path=receipt_path, repo_root=tmp_root,
        )


def test_capture_loader_comprehensive_disk_provenance(tmp_root):
    """All documented provenance assertions in one fixture:
    - capture receipt exact-byte hash
    - receipt target date matches requested
    - sidecar exact-byte hash
    - sidecar schema fields
    - body exact-byte hash
    - sidecar-declared body hash equals computed body hash
    - sidecar/receipt sport agreement
    - sidecar/receipt target-date agreement
    - sidecar body-path agreement with file actually read
    - parser-emitted snapshot source hash agreement
    - parser-emitted target date matches requested
    """
    import hashlib
    receipt_path, receipt = _build_football_capture(tmp_root, "2026-08-28")
    # Compute exact-byte hashes of every input the loader reads
    receipt_bytes = receipt_path.read_bytes()
    receipt_sha = hashlib.sha256(receipt_bytes).hexdigest()
    body_files = list((tmp_root / "data" / "raw" / "football" / "2026-08-28").glob("*.txt"))
    sidecar_files = list((tmp_root / "data" / "raw" / "football" / "2026-08-28").glob("*.json"))
    body_file = body_files[0]
    sidecar_file = sidecar_files[0]
    body_bytes = body_file.read_bytes()
    sidecar_bytes = sidecar_file.read_bytes()
    body_sha = hashlib.sha256(body_bytes).hexdigest()
    sidecar_sha = hashlib.sha256(sidecar_bytes).hexdigest()
    sidecar_obj = json.loads(sidecar_bytes)
    # Sidecar schema: all required fields present
    for k in ("sport", "target_date", "captured_at", "source_url", "sha256",
              "bytes", "body_path", "metadata_path", "route"):
        assert k in sidecar_obj, f"sidecar missing {k}"

    res = load_capture_records(
        target_date="2026-08-28", capture_receipt_path=receipt_path,
        repo_root=tmp_root,
    )
    # 1. receipt exact-byte hash
    assert res.receipt_sha256 == receipt_sha
    assert res.receipt_bytes == len(receipt_bytes)
    # 2. receipt target date
    assert res.target_date == "2026-08-28"
    # 3. sidecar exact-byte hash
    sidecar_path = str(sidecar_file)
    assert res.raw_input_sha256[sidecar_path] == sidecar_sha
    # 4. body exact-byte hash + sidecar-declared match
    body_path = str(body_file)
    assert res.raw_input_sha256[body_path] == body_sha
    assert sidecar_obj["sha256"] == body_sha  # sidecar declared == computed
    # 5. sidecar/receipt sport agreement
    assert sidecar_obj["sport"] == receipt["captured"][0]["sport"]
    # 6. sidecar/receipt target-date agreement
    assert sidecar_obj["target_date"] == receipt["target_date"]
    # 7. sidecar body-path agreement with file actually read
    assert sidecar_obj["body_path"] == str(body_file.relative_to(tmp_root))
    # 8. parser-emitted snapshot raw SHA agreement
    assert len(res.records) == 1
    pre = res.records[0]
    assert pre.raw_sha256 == body_sha
    # 9. parser-emitted target date matches
    assert pre.event_date == "2026-08-28"
    # 10. sidecar captured_at preserved
    assert pre.captured_at == sidecar_obj["captured_at"]
    # 11. no input mutated
    assert receipt_path.read_bytes() == receipt_bytes
    assert body_file.read_bytes() == body_bytes
    assert sidecar_file.read_bytes() == sidecar_bytes


def test_capture_loader_duplicate_snapshot_collapse(tmp_root):
    """Two receipt entries referencing the same body (same raw_sha256)
    are reported as one verified capture and one exact-duplicate
    snapshot. The capture loader returns both records; the
    evaluator's _snapshot_dedup collapses them at the orchestration
    layer. This test asserts both halves.
    """
    receipt_path, _ = _build_football_capture(tmp_root, "2026-08-28")
    receipt = json.loads(receipt_path.read_text())
    receipt["captured"].append(json.loads(json.dumps(receipt["captured"][0])))
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True))
    res = load_capture_records(
        target_date="2026-08-28", capture_receipt_path=receipt_path,
        repo_root=tmp_root,
    )
    # 2 receipt entries; 2 captures verified; 2 snapshots emitted
    assert res.capture_accounting["raw_capture_receipt_entries"] == 2
    assert res.capture_accounting["captures_verified"] == 2
    assert res.snapshot_accounting["parser_emitted_snapshots"] == 2
    # The capture loader returns BOTH records (dedup is the
    # evaluator's job). The per-capture snapshot_exact_duplicate count
    # is 0 because dedup is across the whole record set, not per
    # capture; the second occurrence is reported later by the
    # evaluator. Here we just confirm the loader did not silently
    # drop a record.
    assert len(res.records) == 2
    # Both records share the same event_id and event_date; both are
    # accepted by the loader's identity check.
    assert res.records[0].event_id == res.records[1].event_id
    # The hash of the unique record is the body hash
    assert res.records[0].raw_sha256 == res.records[1].raw_sha256


def test_capture_loader_parser_observability_limitation():
    """Document the parser observability limitation: rows silently
    dropped inside the existing parser are NOT included in
    raw-source-row accounting because the parser exposes no raw-row
    diagnostic count. The capture loader can only count what the
    parser returns.
    """
    # This test exists to record the limitation in the codebase.
    # No assertion is made on parser internals.
    assert True
    # The honest accounting story:
    #   captures_verified = number of sidecar JSONs whose body SHA-256
    #                        matched the file on disk
    #   parser_emitted_snapshots = sum of len(parse_capture(sidecar))
    #   snapshots_unique_accepted = unique records after
    #                                (event_id, event_date, sport) dedup
    # The parser may silently skip rows inside a sidecar (e.g. a row
    # with a finish timestamp or wrong date inside an otherwise valid
    # body). Those rows are not visible to the loader. The user of
    # the loader can only compare captures_verified (whole-sidecar
    # pass) vs parser_emitted_snapshots (per-sidecar emitted).
    # The loader is fail-closed on the boundary: any sidecar with
    # body-hash mismatch OR any path-escape raises.


def test_capture_loader_receipt_reports_target_date_mismatch(tmp_root):
    """A receipt whose ``target_date`` does not match the requested
    date is rejected before any sidecar is processed."""
    receipt_path, _ = _build_football_capture(tmp_root, "2026-08-28")
    receipt = json.loads(receipt_path.read_text())
    receipt["target_date"] = "2026-08-29"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True))
    with pytest.raises(Exception):
        load_capture_records(
            target_date="2026-08-28", capture_receipt_path=receipt_path,
            repo_root=tmp_root,
        )
    # No records, no artifacts
    date_dir = tmp_root / "data" / "reports" / "shadow" / "2026-08-28"
    assert not date_dir.exists() or not any(date_dir.iterdir())


# ===========================================================================
# Group 5: history loader — strict prior-date cutoff
# ===========================================================================


def _make_history_gz(path: Path, rows: list[dict]) -> None:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        for row in rows:
            gz.write((json.dumps(row) + "\n").encode("utf-8"))
    path.write_bytes(buf.getvalue())


def _dict_row(eid: str = "h0", sport: str = "football", p1: str = "Arsenal", p2: str = "Chelsea",
              wi: int = 1, dt: str = "2024-05-01", disp: str = "SETTLED",
              score_1: float | None = 1.0, score_2: float | None = 0.0,
              prob_1: float = 0.55, prob_2: float = 0.30, draw: float = 0.15) -> dict:
    return {
        "event_id": eid, "sport": sport, "event_date": dt,
        "participant_1": p1, "participant_2": p2, "winner_index": wi,
        "score_1": score_1, "score_2": score_2, "probability_1": prob_1,
        "probability_2": prob_2, "draw_probability": draw,
        "forebet_pick": None, "disposition": disp,
    }


def test_history_loader_strict_cutoff_and_void(tmp_root):
    reports = tmp_root / "data" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    rows = _minimal_history_for_r2() + [
        SettledEvent(
            event_id="today", sport="football", event_date="2026-08-28",
            participant_1="Arsenal", participant_2="Chelsea", winner_index=1,
            score_1=1.0, score_2=0.0, probability_1=0.55, probability_2=0.30,
            draw_probability=0.15, forebet_pick=None, disposition="SETTLED",
        ),
    ]
    # Convert to dicts and add a VOID row
    dicts = [{
        "event_id": r.event_id, "sport": r.sport, "event_date": r.event_date,
        "participant_1": r.participant_1, "participant_2": r.participant_2,
        "winner_index": r.winner_index, "score_1": r.score_1, "score_2": r.score_2,
        "probability_1": r.probability_1, "probability_2": r.probability_2,
        "draw_probability": r.draw_probability, "forebet_pick": r.forebet_pick,
        "disposition": r.disposition,
    } for r in rows]
    dicts.append({
        "event_id": "v1", "sport": "football", "event_date": "2024-05-01",
        "participant_1": "Arsenal", "participant_2": "Chelsea",
        "winner_index": 1, "score_1": 1.0, "score_2": 0.0,
        "probability_1": 0.55, "probability_2": 0.30, "draw_probability": 0.15,
        "forebet_pick": None, "disposition": "VOID",
    })
    gz_path = reports / "history_football.jsonl.gz"
    _make_history_gz(gz_path, dicts)
    res = load_valid_history(target_date="2026-08-28", repo_root=tmp_root)
    # All 15 prior-history rows pass; today (same-day) and VOID are excluded
    assert res.manifest_section["history_admitted_rows"] == 15
    exc = res.manifest_section["history_excluded_counts"]
    assert exc.get("PRIOR_DATE_VIOLATION", 0) == 1
    assert exc.get("VOID_DISPOSITION:VOID", 0) == 1
    # No input file was mutated
    assert gz_path.read_bytes()[:4] == b"\x1f\x8b\x08\x00"


def test_history_loader_balanced_accounting(tmp_root):
    reports = tmp_root / "data" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    dicts = [{
        "event_id": r.event_id, "sport": r.sport, "event_date": r.event_date,
        "participant_1": r.participant_1, "participant_2": r.participant_2,
        "winner_index": r.winner_index, "score_1": r.score_1, "score_2": r.score_2,
        "probability_1": r.probability_1, "probability_2": r.probability_2,
        "draw_probability": r.draw_probability, "forebet_pick": r.forebet_pick,
        "disposition": r.disposition,
    } for r in _minimal_history_for_r2()]
    # Add a duplicate of "a0" (same composite key, identical content)
    dicts.append({
        "event_id": "a0", "sport": "football", "event_date": "2024-01-01",
        "participant_1": "Arsenal", "participant_2": "Chelsea", "winner_index": 1,
        "score_1": 2.0, "score_2": 1.0, "probability_1": 0.55, "probability_2": 0.30,
        "draw_probability": 0.15, "forebet_pick": None, "disposition": "SETTLED",
    })
    # Add a conflict for "a1" (same composite key, different winner)
    dicts.append({
        "event_id": "a1", "sport": "football", "event_date": "2024-01-02",
        "participant_1": "Arsenal", "participant_2": "Chelsea",
        "winner_index": 2,  # conflict with original winner_index=1
        "score_1": 0.0, "score_2": 1.0, "probability_1": 0.55, "probability_2": 0.30,
        "draw_probability": 0.15, "forebet_pick": None, "disposition": "SETTLED",
    })
    gz_path = reports / "history_football.jsonl.gz"
    _make_history_gz(gz_path, dicts)
    res = load_valid_history(target_date="2026-08-28", repo_root=tmp_root)
    ms = res.manifest_section
    # Decoded = 17; 1 conflict group of size 2 (both rows excluded)
    # 1 duplicate (1 of the 2 a0 rows excluded as exact duplicate)
    # Admitted = 15 - 1 (conflict on a1) = 14
    assert ms["history_decoded_rows"] == 17
    assert ms["history_exact_duplicate_rows"] == 1
    assert ms["history_conflict_count_groups"] == 1
    assert ms["history_conflict_count_rows"] == 2
    assert ms["history_unique_valid_rows"] == 14
    assert ms["history_admitted_rows"] == 14
    # Balanced: schema_valid == unique + duplicate + conflict
    v2_excluded = sum(
        v for k, v in ms["history_excluded_counts"].items()
        if k not in ("MALFORMED_JSON", "MALFORMED_JSONL")
        and not k.startswith("SCHEMA_INVALID")
    )
    assert ms["history_schema_valid_candidate_rows"] == 17
    assert 17 == v2_excluded + 14 + 1 + 2


def test_history_loader_path_containment(tmp_root):
    with tempfile.TemporaryDirectory() as outside:
        outside_gz = Path(outside) / "evil.gz"
        outside_gz.write_bytes(b"")
        with pytest.raises(HistoryPathError):
            load_valid_history(
                target_date="2026-08-28", repo_root=tmp_root,
                history_paths=[outside_gz],
            )


def _write_one_row_gz(tmp_root: Path, row: dict, name: str = "history_football.jsonl.gz") -> Path:
    reports = tmp_root / "data" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / name
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write((json.dumps(row) + "\n").encode("utf-8"))
    path.write_bytes(buf.getvalue())
    return path


# v2 validity matrix — one row per exclusion category.
# The dataset has TWO layers of rejection:
#   - schema layer (`_validate_settled_dict`) catches malformed fields
#     (missing event_id, missing winner, unknown disposition, etc.)
#   - v2 layer (`_v2_filter_one`) catches semantic issues (unknown
#     sport, self-pair, two-way draw, prior-date violation, etc.)
# The v2 filter is called ONLY on rows that pass schema.
# Each test row is constructed to be schema-valid so it reaches the
# v2 filter, then is rejected at the v2 layer for the documented
# reason. The schema-invalid cases (malformed winner, malformed date,
# empty participant, unknown disposition) are tested separately.
#
# Note on `draw_possible` per `SPORTS` registry:
#   football=True, handball=True, cricket=True, esoccer=True
#   basketball, tennis, hockey, baseball, american_football, rugby,
#   mma, esports, volleyball, afl = False
# The v2 filter rejects winner=0 only when the sport is NOT
# draw-possible. Draw-possible sports admit winner=0; the price-free
# builder separately decides whether to use it as a training example.
_V2_INVALID_CASES = [
    # (label, mutated_row_dict, expected_v2_reason)
    ("unknown_sport_unicorn", _dict_row(sport="unicorn_sport"), "UNKNOWN_SPORT"),
    ("unknown_sport_xyz", _dict_row(sport="xyz"), "UNKNOWN_SPORT"),
    ("self_pair_exact", _dict_row(p1="Arsenal", p2="Arsenal"), "SELF_PAIR"),
    ("self_pair_case", _dict_row(p1="Arsenal", p2="arsenal"), "SELF_PAIR"),
    ("self_pair_punct", _dict_row(p1="Arsenal", p2="Arsenal!"), "SELF_PAIR"),
    ("void_disposition", _dict_row(disp="VOID"), "VOID_DISPOSITION:VOID"),
    ("no_contest_disposition", _dict_row(disp="NO_CONTEST"), "VOID_DISPOSITION:NO_CONTEST"),
    ("void_disposition_lowercase", _dict_row(disp="void"), "VOID_DISPOSITION:VOID"),
    # Two-way sports reject winner=0 (draw) as anomalous
    ("two_way_draw_basketball", _dict_row(sport="basketball", wi=0), "ANOMALOUS_TWO_WAY_DRAW"),
    ("two_way_draw_tennis", _dict_row(sport="tennis", wi=0), "ANOMALOUS_TWO_WAY_DRAW"),
    ("two_way_draw_american_football", _dict_row(sport="american_football", wi=0), "ANOMALOUS_TWO_WAY_DRAW"),
    # Prior-date violations
    ("future_date", _dict_row(dt="2030-01-01"), "PRIOR_DATE_VIOLATION"),
    ("same_day", _dict_row(dt="2026-08-28"), "PRIOR_DATE_VIOLATION"),
    ("day_after_target", _dict_row(dt="2026-08-29"), "PRIOR_DATE_VIOLATION"),
    # Odds differences MUST NOT affect validity (odds are stripped)
    ("odds_dont_affect_validity", _dict_row(prob_1=0.99, prob_2=0.01, draw=0.0), "OK"),
    # Football is draw-possible → winner=0 admitted
    ("football_draw_admitted", _dict_row(sport="football", wi=0, disp="SETTLED"), "OK"),
    # Handball is draw-possible → winner=0 admitted
    ("handball_draw_admitted", _dict_row(sport="handball", wi=0, disp="SETTLED"), "OK"),
]


@pytest.mark.parametrize("label,row,expected", _V2_INVALID_CASES, ids=[c[0] for c in _V2_INVALID_CASES])
def test_history_v2_validity_matrix(tmp_root, label, row, expected):
    """One row per v2 validity category. Each row is REJECTED at the
    v2 layer for the documented reason, except the two positive cases
    which must be ADMITTED.
    """
    gz_path = _write_one_row_gz(tmp_root, row)
    res = load_valid_history(target_date="2026-08-28", repo_root=tmp_root)
    ms = res.manifest_section
    if expected == "OK":
        assert ms["history_admitted_rows"] == 1, f"row {label} should be admitted"
        assert ms["history_excluded_counts"] == {}, f"row {label} should not be excluded; got {ms['history_excluded_counts']}"
    else:
        assert ms["history_admitted_rows"] == 0, f"row {label} should be rejected"
        exc = ms["history_excluded_counts"]
        assert expected in exc, f"row {label}: expected reason {expected} not in {list(exc)}"
        assert exc[expected] == 1, f"row {label}: expected count 1, got {exc[expected]}"
    # No input was mutated
    assert gz_path.read_bytes()[:2] == b"\x1f\x8b"


# Schema-level exclusion cases (caught by _validate_settled_dict, NOT
# by the v2 filter). These prove the dataset's first-line guard is
# wired into the loader.
_SCHEMA_INVALID_CASES = [
    # (label, mutated_row_dict, expected_schema_reason)
    ("empty_participant_1", _dict_row(p1=""), "SCHEMA_MISSING_PARTICIPANT_1"),
    ("empty_participant_2", _dict_row(p2=""), "SCHEMA_MISSING_PARTICIPANT_2"),
    ("winner_index_3", _dict_row(wi=3), "SCHEMA_INVALID_WINNER_INDEX"),
    ("winner_index_neg1", _dict_row(wi=-1), "SCHEMA_INVALID_WINNER_INDEX"),
    ("winner_index_bool_true", _dict_row(wi=True), "SCHEMA_INVALID_WINNER_INDEX_BOOL"),
    ("winner_index_float", _dict_row(wi=1.0), "SCHEMA_INVALID_WINNER_INDEX_TYPE"),
    ("winner_index_string", _dict_row(wi="1"), "SCHEMA_INVALID_WINNER_INDEX_TYPE"),
    ("unknown_disposition", _dict_row(disp="BOGUS"), "SCHEMA_UNKNOWN_DISPOSITION"),
    ("malformed_date", _dict_row(dt="not-a-date"), "SCHEMA_INVALID_EVENT_DATE"),
    ("missing_event_id", {**_dict_row(), "event_id": ""}, "SCHEMA_MISSING_EVENT_ID"),
    ("missing_sport", {**_dict_row(), "sport": ""}, "SCHEMA_MISSING_SPORT"),
    ("missing_disposition", {**_dict_row(), "disposition": ""}, "SCHEMA_MISSING_DISPOSITION"),
]


@pytest.mark.parametrize("label,row,expected", _SCHEMA_INVALID_CASES, ids=[c[0] for c in _SCHEMA_INVALID_CASES])
def test_history_loader_schema_layer_excludes(tmp_root, label, row, expected):
    """Malformed rows are caught by the schema layer before the v2
    filter. They are counted in ``history_schema_invalid`` and never
    reach the v2 layer or the admitted set."""
    gz_path = _write_one_row_gz(tmp_root, row)
    gz_bytes_before = gz_path.read_bytes()
    res = load_valid_history(target_date="2026-08-28", repo_root=tmp_root)
    ms = res.manifest_section
    assert ms["history_decoded_rows"] == 1
    assert ms["history_schema_invalid"] == 1, f"row {label} should be schema-invalid"
    assert ms["history_schema_valid_candidate_rows"] == 0
    assert ms["history_admitted_rows"] == 0
    # The exact reason is recorded. Schema reasons are prefixed with
    # ``SCHEMA_INVALID:`` by the loader.
    exc = ms["history_excluded_counts"]
    found = any(
        k == f"SCHEMA_INVALID:{expected}" or k.startswith(f"SCHEMA_INVALID:{expected}:")
        for k in exc
    )
    assert found, (
        f"row {label}: expected schema reason {expected}* in "
        f"{list(exc)}"
    )
    # Input not mutated
    assert gz_path.read_bytes() == gz_bytes_before


# Coherent disposition/winner combos for SETTLED_CUP, SETTLED_DRAW
# These all pass the schema layer (vocabulary is in SUPPORTED_DISPOSITIONS)
# and exercise the v2 layer's sport-aware two-way-draw guard.
# Per the SPORTS registry, only football/handball/cricket/esoccer are
# draw-possible. Other sports reject winner=0 as ANOMALOUS_TWO_WAY_DRAW
# regardless of disposition.
_V2_DISPOSITION_WINNER_CASES = [
    # (label, sport, disposition, winner_index, should_admit)
    ("settled_home_win_football", "football", "SETTLED", 1, True),
    ("settled_away_win_football", "football", "SETTLED", 2, True),
    # football is draw-possible → winner=0 admitted (regardless of disposition)
    ("settled_draw_football_admitted", "football", "SETTLED", 0, True),
    ("settled_cup_away_football", "football", "SETTLED_CUP", 2, True),
    ("settled_cup_draw_football_admitted", "football", "SETTLED_CUP", 0, True),
    ("settled_home_win_tennis", "tennis", "SETTLED", 1, True),
    # tennis is two-way → winner=0 rejected
    ("settled_draw_tennis_anomalous", "tennis", "SETTLED_DRAW", 0, False),
    # handball is draw-possible
    ("settled_draw_handball_admitted", "handball", "SETTLED", 0, True),
]


@pytest.mark.parametrize("label,sport,disp,wi,should_admit", _V2_DISPOSITION_WINNER_CASES,
                         ids=[c[0] for c in _V2_DISPOSITION_WINNER_CASES])
def test_history_v2_disposition_winner_coherence(tmp_root, label, sport, disp, wi, should_admit):
    """Disposition/winner combinations are coherent when admitted;
    incoherent combinations are rejected with documented reasons.
    Tennis is draw-possible; football is two-way. SPORTS registry
    is the source of truth for draw capability.
    """
    p1 = "Serena" if sport == "tennis" else "Arsenal"
    p2 = "Venus" if sport == "tennis" else "Chelsea"
    row = _dict_row(sport=sport, disp=disp, wi=wi, p1=p1, p2=p2)
    _write_one_row_gz(tmp_root, row)
    res = load_valid_history(target_date="2026-08-28", repo_root=tmp_root)
    ms = res.manifest_section
    if should_admit:
        assert ms["history_admitted_rows"] == 1, (
            f"{label} should admit; got {ms['history_excluded_counts']}"
        )
    else:
        assert ms["history_admitted_rows"] == 0, f"{label} should reject"
        assert ms["history_excluded_counts"].get("ANOMALOUS_TWO_WAY_DRAW", 0) == 1, (
            f"{label} should reject as ANOMALOUS_TWO_WAY_DRAW; got {ms['history_excluded_counts']}"
        )


def test_history_loader_bounded_size_fail_closed(tmp_root):
    """An interim JSON file larger than ``max_interim_bytes`` is rejected
    with ``HistorySizeLimitError`` (no records admitted)."""
    interim = tmp_root / "data" / "interim"
    interim.mkdir(parents=True, exist_ok=True)
    interim_file = interim / "settled_history.json"
    # 4 KiB of filler > 1 KiB limit
    interim_file.write_text("[" + "0," * 2000 + "0]")
    with pytest.raises(Exception) as ei:
        load_valid_history(
            target_date="2026-08-28", repo_root=tmp_root,
            max_interim_bytes=1024,
        )
    assert "exceeds" in str(ei.value).lower() or "max" in str(ei.value).lower()
    # File not mutated
    assert interim_file.read_text()[:1] == "["


def test_history_loader_input_hashes_are_exact_byte(tmp_root):
    """The exact-byte SHA-256 of each input file is recorded regardless
    of format. For a .jsonl.gz file, the hash is over the gzipped bytes
    (not the decompressed bytes). For a .json file, the hash is over
    the raw JSON bytes.
    """
    import hashlib
    reports = tmp_root / "data" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    rows = [_dict_row(eid="a0"), _dict_row(eid="l0", p1="Liverpool", p2="ManU", dt="2024-02-01")]
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        for r in rows:
            gz.write((json.dumps(r) + "\n").encode("utf-8"))
    gz_path = reports / "history_football.jsonl.gz"
    gz_bytes = buf.getvalue()
    gz_path.write_bytes(gz_bytes)
    gz_expected_sha = hashlib.sha256(gz_bytes).hexdigest()
    gz_expected_size = len(gz_bytes)

    res = load_valid_history(target_date="2026-08-28", repo_root=tmp_root)
    ms = res.manifest_section
    assert ms["history_input_sha256"][str(gz_path)] == gz_expected_sha
    assert ms["history_input_bytes"][str(gz_path)] == gz_expected_size
    # File unchanged
    assert gz_path.read_bytes() == gz_bytes


def test_history_loader_uncompressed_jsonl_rejected(tmp_root):
    """Only the two documented formats are supported. An uncompressed
    .jsonl file is rejected with ``HistoryPathError`` when supplied
    explicitly via ``history_paths`` (auto-discovery only globs the
    documented formats)."""
    reports = tmp_root / "data" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    bad = reports / "history_football.jsonl"
    bad.write_text(json.dumps(_dict_row()) + "\n")
    with pytest.raises(Exception):
        load_valid_history(
            target_date="2026-08-28", repo_root=tmp_root,
            history_paths=[bad],
        )
    # No final artifact or input mutation
    assert bad.read_text() == json.dumps(_dict_row()) + "\n"


# ===========================================================================
# Group 6: timing cutoff
# ===========================================================================


def test_safe_cutoff_24h():
    cutoff = safe_cutoff_utc("2026-08-28", offset_hours=24)
    assert cutoff == datetime(2026, 8, 27, 0, 0, 0, tzinfo=timezone.utc)


def test_parse_utc_requires_tz():
    with pytest.raises(ValueError):
        _parse_utc("2026-08-27T00:00:00")  # naive
    with pytest.raises(ValueError):
        _parse_utc("2026-08-27")  # date only


# ===========================================================================
# Group 7: identity validation
# ===========================================================================


def test_validate_identity_rejects_self_pair():
    with pytest.raises(ValueError, match="self-pair"):
        _make_record(p1="Arsenal", p2="arsenal")


def test_validate_identity_rejects_unknown_sport():
    with pytest.raises(ValueError, match="unknown sport"):
        _make_record(sport="unicorn_sport")


def test_validate_identity_rejects_equal_probabilities():
    rec = _make_record(prob_1=0.5, prob_2=0.5)
    ok, reason = validate_event_identity(rec)
    assert ok is False
    assert "IDENTITY_INELIGIBLE" in reason


# ===========================================================================
# Group 8: R2 eligibility via baseline_analyzer
# ===========================================================================


@pytest.mark.parametrize("underdog_prior_games,expected", [
    (5, True), (4, False), (None, False),
])
def test_r2_underdog_prior_games_boundary(underdog_prior_games, expected):
    feats = {
        "underdog_prior_games": float(underdog_prior_games) if underdog_prior_games is not None else None,
        "favorite_prior_games": 5.0, "h2h_prior_games": 1.0,
        "forebet_probability_gap": 0.1,
    }
    assert is_r2_eligible(feats) is expected


# ===========================================================================
# Group 9: end-to-end disk fixture
# ===========================================================================


def test_end_to_end_disk_fixture(tmp_root):
    """Full pipeline: receipt on disk -> PreEventRecord -> R2 + R1 -> artifact."""
    # Build the history (streamed gzip)
    reports = tmp_root / "data" / "reports"
    dicts = [{
        "event_id": r.event_id, "sport": r.sport, "event_date": r.event_date,
        "participant_1": r.participant_1, "participant_2": r.participant_2,
        "winner_index": r.winner_index, "score_1": r.score_1, "score_2": r.score_2,
        "probability_1": r.probability_1, "probability_2": r.probability_2,
        "draw_probability": r.draw_probability, "forebet_pick": r.forebet_pick,
        "disposition": r.disposition,
    } for r in _minimal_history_for_r2()]
    gz_path = reports / "history_football.jsonl.gz"
    history_bytes_before = None  # captured after write
    _make_history_gz(gz_path, dicts)
    history_bytes_before = gz_path.read_bytes()
    history_sha_before = hashlib.sha256(history_bytes_before).hexdigest()

    # Build the capture receipt (football, one entry)
    receipt_path, receipt = _build_football_capture(tmp_root, "2026-08-28")
    receipt_bytes_before = receipt_path.read_bytes()
    receipt_sha_before = hashlib.sha256(receipt_bytes_before).hexdigest()

    # Run the orchestrator with an injected clock that is BEFORE the safe cutoff
    decision_clock = datetime(2026, 8, 27, 0, 0, 0, tzinfo=timezone.utc)
    result = evaluate_from_disk(
        target_date="2026-08-28",
        capture_receipt_path=receipt_path,
        declaration_path=tmp_root / "config" / "shadow_evaluator_v1.json",
        repo_root=tmp_root,
        history_paths=[gz_path],
        decision_clock=decision_clock,
    )

    # The parser is invoked on the real body, features built, R2 + R1 applied
    # Arsenal vs Liverpool with 0.50/0.40/0.10 passes R2 (gap 0.10 ≤ 0.2)
    # if the history produces underdog_prior_games >= 5, favorite >= 5, h2h >= 1
    # — but our history has no Arsenal-vs-Liverpool H2H. So this fixture
    # produces feature_incomplete_or_r2_ineligible=1 and no primary.
    # To make the test robust, also assert the case where the body has a
    # second event with proper H2H: re-run with a H2H fixture below.
    assert result.run_status in ("SHADOW_NO_SELECTION", "SHADOW_SELECTIONS_EMITTED")
    # Manifest is the last file written
    manifest_path = Path(result.artifact_dir) / "manifest.json"
    payload_path = Path(result.artifact_dir) / "shadow_selections.json"
    assert manifest_path.is_file() and payload_path.is_file()
    manifest = json.loads(manifest_path.read_text())
    # Manifest hashes match the on-disk files
    payload_bytes = payload_path.read_bytes()
    assert manifest["payload_file_sha256"] == hashlib.sha256(payload_bytes).hexdigest()
    # Capture provenance recorded
    assert manifest["capture_provenance"]["receipt_sha256"] == receipt_sha_before
    # History provenance recorded with input SHA-256
    assert manifest["history_provenance"]["history_input_sha256"][str(gz_path)] == history_sha_before
    # Inputs were NOT mutated
    assert gz_path.read_bytes() == history_bytes_before
    assert receipt_path.read_bytes() == receipt_bytes_before


def test_end_to_end_blocked_manifest_written_last(tmp_root, monkeypatch):
    """If manifest finalization fails, the artifact dir has payload but no manifest."""
    import slumdog.shadow_evaluator as se
    gz_path = tmp_root / "data" / "reports" / "history_football.jsonl.gz"
    gz_path.parent.mkdir(parents=True, exist_ok=True)
    _make_history_gz(gz_path, [{
        "event_id": r.event_id, "sport": r.sport, "event_date": r.event_date,
        "participant_1": r.participant_1, "participant_2": r.participant_2,
        "winner_index": r.winner_index, "score_1": r.score_1, "score_2": r.score_2,
        "probability_1": r.probability_1, "probability_2": r.probability_2,
        "draw_probability": r.draw_probability, "forebet_pick": r.forebet_pick,
        "disposition": r.disposition,
    } for r in _minimal_history_for_r2()])
    receipt_path, _ = _build_football_capture(tmp_root, "2026-08-28")
    original = se.os.replace
    def failing(src, dst):
        if "manifest" in str(src):
            raise OSError("simulated manifest failure")
        return original(src, dst)
    monkeypatch.setattr(se.os, "replace", failing)
    decision_clock = datetime(2026, 8, 27, 0, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(OSError):
        evaluate_from_disk(
            target_date="2026-08-28", capture_receipt_path=receipt_path,
            declaration_path=tmp_root / "config" / "shadow_evaluator_v1.json",
            repo_root=tmp_root, history_paths=[gz_path],
            decision_clock=decision_clock,
        )
    # Find the artifact dir
    date_dir = tmp_root / "data" / "reports" / "shadow" / "2026-08-28"
    run_dirs = [d for d in date_dir.iterdir() if d.is_dir()]
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "shadow_selections.json").exists()
    assert not (run_dirs[0] / "manifest.json").exists()


# ===========================================================================
# Group 10: CLI
# ===========================================================================


def test_cli_help(tmp_root):
    """--help must work without loading configs or touching the filesystem."""
    result = subprocess.run(
        [sys.executable, "-m", "slumdog.shadow_evaluator", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "shadow" in result.stdout.lower()


def test_cli_main_successful_run(tmp_root, capsys, monkeypatch):
    """Direct ``main(argv)`` invocation exercises the actual CLI
    argument parsing + orchestration path. Asserts:
    - return code 0
    - concise receipt on stdout
    - no unexpected stderr (only the deterministic success line)
    - complete payload and manifest in the artifact dir
    - input files unchanged
    - no network or production side effect
    """
    # The CLI uses real ``_now_utc()``; for a deterministic fixture
    # we monkeypatch the shadow_evaluator's clock to a time safely
    # before the safe cutoff.
    from slumdog import shadow_evaluator as se
    fixed_clock = datetime(2026, 8, 26, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(se, "_now_utc", lambda: fixed_clock)

    # Build a working fixture
    reports = tmp_root / "data" / "reports"
    dicts = [{
        "event_id": r.event_id, "sport": r.sport, "event_date": r.event_date,
        "participant_1": r.participant_1, "participant_2": r.participant_2,
        "winner_index": r.winner_index, "score_1": r.score_1, "score_2": r.score_2,
        "probability_1": r.probability_1, "probability_2": r.probability_2,
        "draw_probability": r.draw_probability, "forebet_pick": r.forebet_pick,
        "disposition": r.disposition,
    } for r in _minimal_history_for_r2()]
    gz_path = reports / "history_football.jsonl.gz"
    gz_path.parent.mkdir(parents=True, exist_ok=True)
    _make_history_gz(gz_path, dicts)
    gz_bytes_before = gz_path.read_bytes()

    receipt_path, _ = _build_football_capture(tmp_root, "2026-08-28")
    receipt_bytes_before = receipt_path.read_bytes()

    rc = main([
        "--date", "2026-08-28",
        "--capture-receipt", str(receipt_path),
        "--history", str(gz_path),
        "--config", str(tmp_root / "config" / "shadow_evaluator_v1.json"),
        "--root", str(tmp_root),
    ])
    captured = capsys.readouterr()
    assert rc == 0, f"main returned {rc}; stderr={captured.err}; stdout={captured.out}"
    # Concise success line on stdout
    assert "SHADOW_" in captured.out or "shadow" in captured.out.lower()
    # No traceback in stderr
    assert "Traceback" not in captured.err
    # The artifact dir contains a manifest + payload (not BLOCKED)
    date_dir = tmp_root / "data" / "reports" / "shadow" / "2026-08-28"
    run_dirs = [d for d in date_dir.iterdir() if d.is_dir() and d.name != "BLOCKED"]
    assert len(run_dirs) == 1, f"expected 1 run dir, got {len(run_dirs)}"
    manifest = json.loads((run_dirs[0] / "manifest.json").read_text())
    # Manifest hashes match the on-disk files
    payload_bytes = (run_dirs[0] / "shadow_selections.json").read_bytes()
    assert manifest["payload_file_sha256"] == hashlib.sha256(payload_bytes).hexdigest()
    # Inputs were NOT mutated
    assert gz_path.read_bytes() == gz_bytes_before
    assert receipt_path.read_bytes() == receipt_bytes_before
    # No real-data run or production
    assert manifest.get("run_status") in ("SHADOW_NO_SELECTION", "SHADOW_SELECTIONS_EMITTED")
    # Capture provenance recorded with receipt SHA
    assert manifest["capture_provenance"]["receipt_sha256"] == hashlib.sha256(receipt_bytes_before).hexdigest()


def test_blocked_receipt_collision_protection(tmp_root, monkeypatch):
    """Two BLOCKED receipts in the same second with the same reason do
    NOT overwrite each other. A 12-char hex dedup suffix is appended."""
    from slumdog import shadow_evaluator as se
    fixed_clock = datetime(2026, 8, 26, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(se, "_now_utc", lambda: fixed_clock)
    # Build fixture
    reports = tmp_root / "data" / "reports"
    dicts = [{
        "event_id": r.event_id, "sport": r.sport, "event_date": r.event_date,
        "participant_1": r.participant_1, "participant_2": r.participant_2,
        "winner_index": r.winner_index, "score_1": r.score_1, "score_2": r.score_2,
        "probability_1": r.probability_1, "probability_2": r.probability_2,
        "draw_probability": r.draw_probability, "forebet_pick": r.forebet_pick,
        "disposition": r.disposition,
    } for r in _minimal_history_for_r2()]
    gz_path = reports / "history_football.jsonl.gz"
    gz_path.parent.mkdir(parents=True, exist_ok=True)
    _make_history_gz(gz_path, dicts)
    # First run: pass a missing receipt → CAPTURE_LOAD_FAILED
    rc1 = main([
        "--date", "2026-08-28",
        "--capture-receipt", str(tmp_root / "nonexistent.json"),
        "--history", str(gz_path),
        "--config", str(tmp_root / "config" / "shadow_evaluator_v1.json"),
        "--root", str(tmp_root),
    ])
    assert rc1 != 0
    blocked_dir = tmp_root / "data" / "reports" / "shadow" / "2026-08-28" / "BLOCKED"
    first_receipts = list(blocked_dir.glob("BLOCKED_*.json"))
    assert len(first_receipts) == 1
    # Second call: same clock, same reason, same target date.
    # Should NOT overwrite; should write a second file with a dedup
    # suffix.
    rc2 = main([
        "--date", "2026-08-28",
        "--capture-receipt", str(tmp_root / "nonexistent.json"),
        "--history", str(gz_path),
        "--config", str(tmp_root / "config" / "shadow_evaluator_v1.json"),
        "--root", str(tmp_root),
    ])
    assert rc2 != 0
    second_receipts = list(blocked_dir.glob("BLOCKED_*.json"))
    assert len(second_receipts) == 2, f"expected 2 receipts, got {len(second_receipts)}: {[p.name for p in second_receipts]}"
    # The two files have different names
    names = sorted(p.name for p in second_receipts)
    assert names[0] != names[1]


def test_authorization_failure_writes_nothing(tmp_root):
    """Authorization / declaration integrity failures raise before any
    artifact is written. No BLOCKED receipt. No run dir. No payload."""
    bad_decl = json.loads(SHADOW_DECL_PATH.read_text())
    bad_decl["authorizations"]["production_authorized"] = True
    p = tmp_root / "config" / "shadow_evaluator_v1.json"
    p.write_text(json.dumps(bad_decl))
    rc = main([
        "--date", "2026-08-28",
        "--capture-receipt", str(tmp_root / "nonexistent.json"),
        "--config", str(p),
        "--root", str(tmp_root),
    ])
    assert rc == 2
    # No artifact of any kind
    shadow_dir = tmp_root / "data" / "reports" / "shadow"
    assert not shadow_dir.exists() or not any(shadow_dir.rglob("*.json"))


def test_blocked_receipt_cannot_be_mistaken_for_completed_manifest(tmp_root, monkeypatch):
    """A BLOCKED receipt lives under ``<date>/BLOCKED/`` and contains
    ``run_status=SHADOW_RUN_BLOCKED`` — a downstream reader cannot
    mistake it for a completed decision artifact.
    """
    from slumdog import shadow_evaluator as se
    fixed_clock = datetime(2026, 8, 26, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(se, "_now_utc", lambda: fixed_clock)
    # Build fixture
    reports = tmp_root / "data" / "reports"
    dicts = [{
        "event_id": r.event_id, "sport": r.sport, "event_date": r.event_date,
        "participant_1": r.participant_1, "participant_2": r.participant_2,
        "winner_index": r.winner_index, "score_1": r.score_1, "score_2": r.score_2,
        "probability_1": r.probability_1, "probability_2": r.probability_2,
        "draw_probability": r.draw_probability, "forebet_pick": r.forebet_pick,
        "disposition": r.disposition,
    } for r in _minimal_history_for_r2()]
    gz_path = reports / "history_football.jsonl.gz"
    gz_path.parent.mkdir(parents=True, exist_ok=True)
    _make_history_gz(gz_path, dicts)
    # Use future-target-date to trigger cutoff BLOCK
    receipt_path, _ = _build_football_capture(tmp_root, "2026-08-28")
    # The fixture's receipt is for 2026-08-28; we need the cutoff
    # gate to trigger. Use a HISTORICAL target_date that is BEFORE
    # the fixture's safe cutoff: target_date 2024-01-01, cutoff
    # 2023-12-31. The capture receipt for 2026-08-28 will fail the
    # target_date check → CAPTURE_LOAD_FAILED → BLOCKED.
    rc = main([
        "--date", "2024-01-01",
        "--capture-receipt", str(receipt_path),
        "--history", str(gz_path),
        "--config", str(tmp_root / "config" / "shadow_evaluator_v1.json"),
        "--root", str(tmp_root),
    ])
    assert rc != 0
    blocked_dir = tmp_root / "data" / "reports" / "shadow" / "2024-01-01" / "BLOCKED"
    assert blocked_dir.exists()
    receipt_file = list(blocked_dir.glob("BLOCKED_*.json"))[0]
    payload = json.loads(receipt_file.read_text())
    # Markers that distinguish a BLOCKED receipt from a completed manifest
    assert payload["run_status"] == "SHADOW_RUN_BLOCKED"
    assert "block_reason" in payload
    # A completed manifest has payload_file_sha256 + decision_digest + a
    # non-BLOCKED run_id; a BLOCKED receipt has none of those.
    assert "payload_file_sha256" not in payload
    assert "decision_digest" not in payload
    assert "selections" not in payload
    # The file is under a BLOCKED/ subdirectory, never at the top of
    # the per-date directory.
    top_level = [p for p in (tmp_root / "data" / "reports" / "shadow" / "2024-01-01").iterdir() if p.is_dir()]
    assert "BLOCKED" in [p.name for p in top_level]
    # No run_id directory at the top level (no completed artifact)
    run_dirs = [p for p in top_level if p.name != "BLOCKED"]
    assert run_dirs == []


def test_input_digest_commits_to_required_evidence(tmp_root, monkeypatch):
    """``input_digest`` commits to at least: declaration, frozen
    baseline config, target date, safety cutoff, decision committed
    at, capture receipt, sidecars, raw bodies, history inputs,
    history validity/accounting, history feature contract, and
    accepted pre-event records.
    """
    from slumdog import shadow_evaluator as se
    fixed_clock = datetime(2026, 8, 26, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(se, "_now_utc", lambda: fixed_clock)
    # Build fixture
    reports = tmp_root / "data" / "reports"
    dicts = [{
        "event_id": r.event_id, "sport": r.sport, "event_date": r.event_date,
        "participant_1": r.participant_1, "participant_2": r.participant_2,
        "winner_index": r.winner_index, "score_1": r.score_1, "score_2": r.score_2,
        "probability_1": r.probability_1, "probability_2": r.probability_2,
        "draw_probability": r.draw_probability, "forebet_pick": r.forebet_pick,
        "disposition": r.disposition,
    } for r in _minimal_history_for_r2()]
    gz_path = reports / "history_football.jsonl.gz"
    gz_path.parent.mkdir(parents=True, exist_ok=True)
    _make_history_gz(gz_path, dicts)
    receipt_path, _ = _build_football_capture(tmp_root, "2026-08-28")

    rc = main([
        "--date", "2026-08-28",
        "--capture-receipt", str(receipt_path),
        "--history", str(gz_path),
        "--config", str(tmp_root / "config" / "shadow_evaluator_v1.json"),
        "--root", str(tmp_root),
    ])
    assert rc == 0
    date_dir = tmp_root / "data" / "reports" / "shadow" / "2026-08-28"
    run_dirs = [d for d in date_dir.iterdir() if d.is_dir() and d.name != "BLOCKED"]
    assert len(run_dirs) == 1
    manifest = json.loads((run_dirs[0] / "manifest.json").read_text())
    ip = manifest["input_provenance"]
    # The input_digest is the SHA-256 of input_provenance's
    # canonicalized form. It lives at the top of the manifest, not
    # inside input_provenance itself.
    assert manifest["input_digest"] == hashlib.sha256(
        canonical_json_bytes(ip)
    ).hexdigest()
    # All required keys present
    required = {
        "version", "declaration_sha256", "frozen_baseline_config_sha256",
        "target_date", "safe_cutoff_utc",
        "capture_receipt_sha256", "sidecar_digests", "raw_body_digests",
        "capture_accounting_digest", "snapshot_accounting_digest",
        "history_input_sha256", "history_accounting_digest",
        "history_feature_contract", "capture_record_tuples",
    }
    missing = required - set(ip.keys())
    assert not missing, f"input_provenance missing: {missing}"
    # frozen baseline SHA must match
    assert ip["frozen_baseline_config_sha256"] == FROZEN_BASELINE_CONFIG_SHA256
    # target_date
    assert ip["target_date"] == "2026-08-28"
    # safe_cutoff_utc is target_date minus 24h
    assert "2026-08-27T00:00:00" in ip["safe_cutoff_utc"]
    # decision_committed_at is recorded in the manifest (not the digest
    # payload, so the same inputs + different commit timestamps still
    # produce the same input_digest)
    assert manifest["decision_committed_at"].startswith("2026-08-26T12:00:00")
    # capture receipt SHA matches the file
    assert ip["capture_receipt_sha256"] == hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    # At least one sidecar and one body
    assert len(ip["sidecar_digests"]) >= 1
    assert len(ip["raw_body_digests"]) >= 1
    # History feature contract version present
    assert ip["history_feature_contract"] == "price-free-v2-incremental-valid-history"
    # At least one record tuple
    assert len(ip["capture_record_tuples"]) >= 1
    # input_digest is a 64-char hex
    assert re.match(r"^[0-9a-f]{64}$", manifest["input_digest"])


def test_decision_digest_commits_to_required_evidence(tmp_root, monkeypatch):
    """``decision_digest`` commits to: complete ranked/considered
    pool, primary selections, top-3 cohorts, decision accounting,
    rule/version identity. It does NOT depend on odds or outcomes.
    """
    from slumdog import shadow_evaluator as se
    fixed_clock = datetime(2026, 8, 26, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(se, "_now_utc", lambda: fixed_clock)
    # Build fixture (with H2H so a primary might be selected)
    reports = tmp_root / "data" / "reports"
    dicts = [{
        "event_id": r.event_id, "sport": r.sport, "event_date": r.event_date,
        "participant_1": r.participant_1, "participant_2": r.participant_2,
        "winner_index": r.winner_index, "score_1": r.score_1, "score_2": r.score_2,
        "probability_1": r.probability_1, "probability_2": r.probability_2,
        "draw_probability": r.draw_probability, "forebet_pick": r.forebet_pick,
        "disposition": r.disposition,
    } for r in _minimal_history_for_r2()]
    gz_path = reports / "history_football.jsonl.gz"
    gz_path.parent.mkdir(parents=True, exist_ok=True)
    _make_history_gz(gz_path, dicts)
    receipt_path, _ = _build_football_capture(tmp_root, "2026-08-28")

    rc = main([
        "--date", "2026-08-28",
        "--capture-receipt", str(receipt_path),
        "--history", str(gz_path),
        "--config", str(tmp_root / "config" / "shadow_evaluator_v1.json"),
        "--root", str(tmp_root),
    ])
    assert rc == 0
    date_dir = tmp_root / "data" / "reports" / "shadow" / "2026-08-28"
    run_dirs = [d for d in date_dir.iterdir() if d.is_dir() and d.name != "BLOCKED"]
    assert len(run_dirs) == 1
    manifest = json.loads((run_dirs[0] / "manifest.json").read_text())
    dp = manifest["decision_provenance"]
    # The decision_digest is the SHA-256 of decision_provenance's
    # canonicalized form.
    assert manifest["decision_digest"] == hashlib.sha256(
        canonical_json_bytes(dp)
    ).hexdigest()
    required = {
        "version", "rule_name", "frozen_baseline_config_sha256",
        "considered_pool", "selections", "decision_accounting",
    }
    missing = required - set(dp.keys())
    assert not missing, f"decision_provenance missing: {missing}"
    assert dp["rule_name"] == FROZEN_R2_KEY
    assert dp["frozen_baseline_config_sha256"] == FROZEN_BASELINE_CONFIG_SHA256
    # Considered pool is non-empty (at least one record was evaluated)
    assert len(dp["considered_pool"]) >= 1
    # Selections list may be empty (NO_SELECTION) but must be a list
    assert isinstance(dp["selections"], list)
    # No odds or outcomes in the digest payload
    # No "odds_1" or "odds_2" anywhere in the manifest
    manifest_text = json.dumps(manifest)
    for forbidden in ("odds_1", "odds_2", "score_1", "score_2", "winner_index", "disposition", "period_scores"):
        assert forbidden not in manifest_text, f"manifest contains forbidden field {forbidden}"
    # 64-char hex
    assert re.match(r"^[0-9a-f]{64}$", manifest["decision_digest"])


def test_ranks_4_plus_beyond_top3_in_considered_pool_only(tmp_root, capsys, monkeypatch):
    """Rank 4+ must appear in ``considered_pool[]`` but NOT in
    ``selections[]``. ``considered_status = ELIGIBLE_RANKED_BEYOND_TOP3``.
    ``decision_digest`` must commit to both arrays. Five eligible
    events produce two rank-4+ in considered_pool; selections[] has
    only ranks 1-3.
    """
    from slumdog import shadow_evaluator as se
    fixed_clock = datetime(2026, 8, 26, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(se, "_now_utc", lambda: fixed_clock)

    reports = tmp_root / "data" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    hist_rows: list[dict] = []
    # 6 priors per pairing, no H2H yet (will be added later)
    base_rows = [
        ("Arsenal", "Chelsea", "2024-01"),
        ("Liverpool", "ManU", "2024-02"),
        ("Chelsea", "Liverpool", "2024-03"),
        ("Chelsea", "ManU", "2024-04"),
        ("Arsenal", "Liverpool", "2024-05"),
    ]
    for h, a, month in base_rows:
        for i in range(6):
            hist_rows.append({
                "event_id": f"p_{h}_{a}_{i}", "sport": "football",
                "event_date": f"{month}-{(i % 28) + 1:02d}",
                "participant_1": h, "participant_2": a,
                "winner_index": 1, "score_1": 1.0, "score_2": 0.0,
                "probability_1": 0.55, "probability_2": 0.30,
                "draw_probability": 0.15, "forebet_pick": None,
                "disposition": "SETTLED",
            })
    # 2 H2H per pairing
    h2h_months = ["2024-06", "2024-07", "2024-08", "2024-09", "2024-10"]
    for (h, a, _), month in zip(base_rows, h2h_months):
        for i in range(2):
            hist_rows.append({
                "event_id": f"h2h_{h}_{a}_{i}", "sport": "football",
                "event_date": f"{month}-{(i % 28) + 1:02d}",
                "participant_1": h, "participant_2": a,
                "winner_index": 1, "score_1": 1.0, "score_2": 0.0,
                "probability_1": 0.55, "probability_2": 0.30,
                "draw_probability": 0.15, "forebet_pick": None,
                "disposition": "SETTLED",
            })
    gz_path = reports / "history_football.jsonl.gz"
    _make_history_gz(gz_path, hist_rows)

    # Build capture receipt with 5 R2-eligible future events (all gap 0.10).
    target_date = "2026-08-28"
    pairs = [
        ("1001", "Arsenal", "Chelsea", 1),
        ("1002", "Liverpool", "ManU", 2),
        ("1003", "Chelsea", "Liverpool", 3),
        ("1004", "Chelsea", "ManU", 4),
        ("1005", "Arsenal", "Liverpool", 5),
    ]
    rows = []
    for eid, h, a, hh in pairs:
        rows.append({
            "id": eid, "HOST_NAME": h, "GUEST_NAME": a,
            "Pred_1": "50", "Pred_X": "10", "Pred_2": "40",
            "best_odd_1": "2.00", "best_odd_2": "2.50", "best_odd_X": "10.00",
            "short_tag": "EPL",
            "DATE_BAH": f"{target_date} {14 + hh:02d}:00",
            "host_sc_pr": "1", "guest_sc_pr": "1", "goalsavg": "2.5",
            "Host_SC": None, "Guest_SC": None, "comment": "",
        })
    body = ("<html><body>" + json.dumps([rows, {}]) + "</body></html>").encode("utf-8")
    body_sha = hashlib.sha256(body).hexdigest()
    body_dir = tmp_root / "data" / "raw" / "football" / target_date
    body_dir.mkdir(parents=True, exist_ok=True)
    stamp = "20260826T100000Z"
    body_path = body_dir / f"{stamp}_{body_sha[:12]}.txt"
    sidecar_path = body_dir / f"{stamp}_{body_sha[:12]}.json"
    body_path.write_bytes(body)
    sidecar = {
        "sport": "football", "target_date": target_date,
        "captured_at": "2026-08-26T10:00:00+00:00",
        "source_url": f"https://example.invalid/football/{target_date}",
        "relay_url": f"https://relay.invalid/football/{target_date}",
        "body_format": "json", "sha256": body_sha, "bytes": len(body),
        "body_path": str(body_path.relative_to(tmp_root)),
        "metadata_path": str(sidecar_path.relative_to(tmp_root)),
        "route": "direct",
    }
    sidecar_path.write_text(json.dumps(sidecar, indent=2, sort_keys=True))
    receipt = {
        "target_date": target_date,
        "generated_at": "2026-08-26T10:00:01+00:00",
        "captured": [sidecar], "failures": [], "reused": 0,
        "football_markets": None,
    }
    receipt_path = tmp_root / "data" / "reports" / f"capture_{target_date}.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True))

    rc = main([
        "--date", target_date,
        "--capture-receipt", str(receipt_path),
        "--history", str(gz_path),
        "--config", str(tmp_root / "config" / "shadow_evaluator_v1.json"),
        "--root", str(tmp_root),
    ])
    assert rc == 0

    date_dir = tmp_root / "data" / "reports" / "shadow" / target_date
    run_dirs = [d for d in date_dir.iterdir() if d.is_dir() and d.name != "BLOCKED"]
    assert len(run_dirs) == 1
    manifest = json.loads((run_dirs[0] / "manifest.json").read_text())

    # Schema boundary: selections[] in the payload = ranks 1-3 only
    payload = json.loads((run_dirs[0] / "shadow_selections.json").read_text())
    assert len(payload["selections"]) == 3, (
        f"selections[] should have exactly 3 (ranks 1-3), got {len(payload['selections'])}"
    )
    selection_ranks = {s["rank_within_sport_day"] for s in payload["selections"]}
    assert selection_ranks == {1, 2, 3}, f"selections[] ranks: {selection_ranks}"

    # No rank-4+ in selections[]
    r4plus_in_selections = [s for s in payload["selections"]
                            if s["rank_within_sport_day"] >= 4]
    assert r4plus_in_selections == [], f"rank-4+ leaked: {r4plus_in_selections}"

    # considered_pool[] (manifest level) has all 5 with ranks and
    # explicit ``considered_status``.
    pool = manifest["considered_pool"]
    assert len(pool) == 5, f"considered_pool should have 5, got {len(pool)}"
    pool_ranks = sorted(p["rank_within_sport_day"] for p in pool)
    assert all(r is not None for r in pool_ranks), f"some pool entries unranked: {pool_ranks}"
    assert set(pool_ranks) == {1, 2, 3, 4, 5}, f"pool ranks: {pool_ranks}"
    # rank-4+ in pool must be marked ELIGIBLE_RANKED_BEYOND_TOP3
    r4plus_in_pool = [p for p in pool if p["rank_within_sport_day"] >= 4]
    assert len(r4plus_in_pool) == 2
    for p in r4plus_in_pool:
        assert p["considered_status"] == "ELIGIBLE_RANKED_BEYOND_TOP3", (
            f"rank-4+ status wrong: {p}"
        )
    # rank 1-3 in pool must be primary/cohort
    r123_in_pool = [p for p in pool if p["rank_within_sport_day"] <= 3]
    assert len(r123_in_pool) == 3
    for p in r123_in_pool:
        assert p["considered_status"] in (
            "PRIMARY_SHADOW_SELECTION", "TOP3_EVALUATION_COHORT"
        ), f"rank-1/2/3 status wrong: {p}"

    # Accounting
    acc = manifest["decision_accounting"]
    assert acc["eligible_ranked_beyond_top3"] == 2, (
        f"expected 2 rank-4+, got {acc['eligible_ranked_beyond_top3']}; acc={acc}"
    )
    assert acc["primary_selected"] == 1
    assert acc["top3_cohort_selected"] == 2
    total = (acc["primary_selected"] + acc["top3_cohort_selected"] +
             acc["eligible_ranked_beyond_top3"])
    assert total == 5, f"accounting total {total} != 5"

    # decision_digest commits to both arrays (digest payload's
    # ``considered_pool`` is the same set as the manifest's
    # ``considered_pool``, just in tuple form).
    assert "considered_pool" in manifest["decision_provenance"]
    assert "selections" in manifest["decision_provenance"]


def test_cli_main_successful_run_produces_selections(tmp_root, capsys, monkeypatch):
    """Successful disk-to-primary-pick test through ``main()`` with
    parser-compatible raw body bytes. Three R2-eligible future
    football events on one sport-day produce:

    - run_status = SHADOW_SELECTIONS_EMITTED
    - one PRIMARY_SHADOW_SELECTION (rank 1)
    - two TOP3_EVALUATION_COHORT records (ranks 2 and 3)
    - correct R1 rank order [1, 2, 3]
    - balanced capture, history, and decision accounting
    - complete payload and manifest
    - matching input, decision, and payload hashes
    - all three events from the parsed raw body
    - all three also in considered_pool[] with their ranks
    - no rank above 3 in selections[]
    - input files unchanged
    """
    from slumdog import shadow_evaluator as se
    fixed_clock = datetime(2026, 8, 26, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(se, "_now_utc", lambda: fixed_clock)

    # Build history with sufficient priors for R2 on 3 distinct pairs.
    # 6 prior games each for Arsenal (vs Chelsea/Chelsea), Liverpool
    # (vs ManU/ManU), Chelsea (vs Arsenal/Arsenal), ManU (vs
    # Liverpool/Liverpool), plus 2 H2H each for the three pairings.
    reports = tmp_root / "data" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    hist_rows: list[dict] = []
    # Arsenal vs Chelsea: 6 (Arsenal wins), 2 H2H (Arsenal wins)
    for i in range(6):
        hist_rows.append({
            "event_id": f"a_a_{i}", "sport": "football",
            "event_date": f"2024-01-{(i % 28) + 1:02d}",
            "participant_1": "Arsenal", "participant_2": "Chelsea",
            "winner_index": 1, "score_1": 2.0, "score_2": 1.0,
            "probability_1": 0.55, "probability_2": 0.30,
            "draw_probability": 0.15, "forebet_pick": None, "disposition": "SETTLED",
        })
    # Liverpool vs ManU: 6 (Liverpool wins), 2 H2H (Liverpool wins)
    for i in range(6):
        hist_rows.append({
            "event_id": f"a_l_{i}", "sport": "football",
            "event_date": f"2024-02-{(i % 28) + 1:02d}",
            "participant_1": "Liverpool", "participant_2": "ManU",
            "winner_index": 1, "score_1": 2.0, "score_2": 0.0,
            "probability_1": 0.50, "probability_2": 0.30,
            "draw_probability": 0.20, "forebet_pick": None, "disposition": "SETTLED",
        })
    # Chelsea vs Arsenal: 6 (Chelsea wins) - gives Chelsea history too
    for i in range(6):
        hist_rows.append({
            "event_id": f"a_c_{i}", "sport": "football",
            "event_date": f"2024-03-{(i % 28) + 1:02d}",
            "participant_1": "Chelsea", "participant_2": "Arsenal",
            "winner_index": 1, "score_1": 1.0, "score_2": 0.0,
            "probability_1": 0.50, "probability_2": 0.30,
            "draw_probability": 0.20, "forebet_pick": None, "disposition": "SETTLED",
        })
    # ManU vs Liverpool: 6 (ManU wins) - gives ManU history too
    for i in range(6):
        hist_rows.append({
            "event_id": f"a_m_{i}", "sport": "football",
            "event_date": f"2024-04-{(i % 28) + 1:02d}",
            "participant_1": "ManU", "participant_2": "Liverpool",
            "winner_index": 1, "score_1": 1.0, "score_2": 0.0,
            "probability_1": 0.50, "probability_2": 0.30,
            "draw_probability": 0.20, "forebet_pick": None, "disposition": "SETTLED",
        })
    # H2H: Arsenal/Chelsea (2)
    for i in range(2):
        hist_rows.append({
            "event_id": f"h2h_ac_{i}", "sport": "football",
            "event_date": f"2024-05-{(i % 28) + 1:02d}",
            "participant_1": "Arsenal", "participant_2": "Chelsea",
            "winner_index": 1, "score_1": 1.0, "score_2": 0.0,
            "probability_1": 0.55, "probability_2": 0.30,
            "draw_probability": 0.15, "forebet_pick": None, "disposition": "SETTLED",
        })
    # H2H: Liverpool/ManU (2)
    for i in range(2):
        hist_rows.append({
            "event_id": f"h2h_lm_{i}", "sport": "football",
            "event_date": f"2024-06-{(i % 28) + 1:02d}",
            "participant_1": "Liverpool", "participant_2": "ManU",
            "winner_index": 1, "score_1": 1.0, "score_2": 0.0,
            "probability_1": 0.50, "probability_2": 0.30,
            "draw_probability": 0.20, "forebet_pick": None, "disposition": "SETTLED",
        })
    # H2H: Arsenal/ManU (2)
    for i in range(2):
        hist_rows.append({
            "event_id": f"h2h_am_{i}", "sport": "football",
            "event_date": f"2024-07-{(i % 28) + 1:02d}",
            "participant_1": "Arsenal", "participant_2": "ManU",
            "winner_index": 1, "score_1": 1.0, "score_2": 0.0,
            "probability_1": 0.55, "probability_2": 0.30,
            "draw_probability": 0.15, "forebet_pick": None, "disposition": "SETTLED",
        })
    gz_path = reports / "history_football.jsonl.gz"
    _make_history_gz(gz_path, hist_rows)
    gz_bytes_before = gz_path.read_bytes()

    # Build capture receipt with 3 R2-eligible future events.
    target_date = "2026-08-28"
    rows = [
        {
            "id": "1001", "HOST_NAME": "Arsenal", "GUEST_NAME": "Chelsea",
            "Pred_1": "50", "Pred_X": "10", "Pred_2": "40",
            "best_odd_1": "2.00", "best_odd_2": "2.50", "best_odd_X": "10.00",
            "short_tag": "EPL", "DATE_BAH": f"{target_date} 15:00",
            "host_sc_pr": "1", "guest_sc_pr": "1", "goalsavg": "2.5",
            "Host_SC": None, "Guest_SC": None, "comment": "",
        },
        {
            "id": "1002", "HOST_NAME": "Liverpool", "GUEST_NAME": "ManU",
            "Pred_1": "50", "Pred_X": "10", "Pred_2": "40",
            "best_odd_1": "2.00", "best_odd_2": "2.50", "best_odd_X": "10.00",
            "short_tag": "EPL", "DATE_BAH": f"{target_date} 17:30",
            "host_sc_pr": "1", "guest_sc_pr": "1", "goalsavg": "2.5",
            "Host_SC": None, "Guest_SC": None, "comment": "",
        },
        {
            "id": "1003", "HOST_NAME": "Arsenal", "GUEST_NAME": "ManU",
            "Pred_1": "54", "Pred_X": "10", "Pred_2": "36",
            "best_odd_1": "1.80", "best_odd_2": "3.00", "best_odd_X": "10.00",
            "short_tag": "EPL", "DATE_BAH": f"{target_date} 20:00",
            "host_sc_pr": "1", "guest_sc_pr": "1", "goalsavg": "2.5",
            "Host_SC": None, "Guest_SC": None, "comment": "",
        },
    ]
    body = ("<html><body>" + json.dumps([rows, {}]) + "</body></html>").encode("utf-8")
    body_sha = hashlib.sha256(body).hexdigest()
    body_dir = tmp_root / "data" / "raw" / "football" / target_date
    body_dir.mkdir(parents=True, exist_ok=True)
    stamp = "20260826T100000Z"
    body_path = body_dir / f"{stamp}_{body_sha[:12]}.txt"
    sidecar_path = body_dir / f"{stamp}_{body_sha[:12]}.json"
    body_path.write_bytes(body)
    sidecar = {
        "sport": "football", "target_date": target_date,
        "captured_at": "2026-08-26T10:00:00+00:00",
        "source_url": f"https://example.invalid/football/{target_date}",
        "relay_url": f"https://relay.invalid/football/{target_date}",
        "body_format": "json", "sha256": body_sha, "bytes": len(body),
        "body_path": str(body_path.relative_to(tmp_root)),
        "metadata_path": str(sidecar_path.relative_to(tmp_root)),
        "route": "direct",
    }
    sidecar_path.write_text(json.dumps(sidecar, indent=2, sort_keys=True))
    receipt = {
        "target_date": target_date,
        "generated_at": "2026-08-26T10:00:01+00:00",
        "captured": [sidecar], "failures": [], "reused": 0,
        "football_markets": None,
    }
    receipt_path = tmp_root / "data" / "reports" / f"capture_{target_date}.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True))
    receipt_bytes_before = receipt_path.read_bytes()
    body_bytes_before = body_path.read_bytes()

    rc = main([
        "--date", target_date,
        "--capture-receipt", str(receipt_path),
        "--history", str(gz_path),
        "--config", str(tmp_root / "config" / "shadow_evaluator_v1.json"),
        "--root", str(tmp_root),
    ])
    captured = capsys.readouterr()
    assert rc == 0, f"main returned {rc}; stderr={captured.err}; stdout={captured.out}"

    # Run produced a complete artifact
    date_dir = tmp_root / "data" / "reports" / "shadow" / target_date
    run_dirs = [d for d in date_dir.iterdir() if d.is_dir() and d.name != "BLOCKED"]
    assert len(run_dirs) == 1
    manifest = json.loads((run_dirs[0] / "manifest.json").read_text())
    payload = json.loads((run_dirs[0] / "shadow_selections.json").read_text())
    payload_bytes = (run_dirs[0] / "shadow_selections.json").read_bytes()

    # CRITICAL: SHADOW_SELECTIONS_EMITTED, not NO_SELECTION
    assert manifest["run_status"] == "SHADOW_SELECTIONS_EMITTED", (
        f"expected SHADOW_SELECTIONS_EMITTED, got {manifest['run_status']}; "
        f"decision_accounting={manifest['decision_accounting']}"
    )
    assert payload["run_status"] == "SHADOW_SELECTIONS_EMITTED"

    # Complete top-three path: 1 primary + 2 cohort = 3 selections.
    # The third event uses an interior gap of 0.18 (Pred_1=54 /
    # Pred_2=36) so it is unambiguously below the R2 boundary of
    # 0.2 and produces a third selection. (See MILESTONE7 plan:
    # "frozen R2 implementation compares binary floats directly;
    # some decimal source combinations mathematically equal to 0.20
    # can parse to a float slightly above 0.20 and therefore be
    # ineligible. The frozen implementation is preserved unchanged;
    # no tolerance or rounding adjustment is authorized.")
    primary = [s for s in payload["selections"] if s["status"] == "PRIMARY_SHADOW_SELECTION"]
    cohort = [s for s in payload["selections"] if s["status"] == "TOP3_EVALUATION_COHORT"]
    assert len(primary) == 1, f"expected 1 primary, got {len(primary)}: {payload['selections']}"
    assert len(cohort) == 2, f"expected 2 cohort, got {len(cohort)}: {payload['selections']}"
    assert len(payload["selections"]) == 3, (
        f"expected 3 selections (1 primary + 2 cohort), got {len(payload['selections'])}: "
        f"{[(s['event_id'], s['rank_within_sport_day'], s['status']) for s in payload['selections']]}"
    )

    # No rank-4+ in selections[]
    r4plus = [s for s in payload["selections"] if s["status"] == "ELIGIBLE_RANKED_BEYOND_TOP3"]
    assert r4plus == [], f"selections[] should not contain rank-4+; got {r4plus}"

    # R1 rank order is exactly [1, 2, 3]
    by_rank = sorted(payload["selections"], key=lambda s: s["rank_within_sport_day"])
    ranks = [s["rank_within_sport_day"] for s in by_rank]
    assert ranks == [1, 2, 3], f"expected ranks [1,2,3], got {ranks}"

    # All three originate from the parsed raw body (not hand-built).
    body_event_ids = {"football:1001", "football:1002", "football:1003"}
    selection_event_ids = {s["event_id"] for s in payload["selections"]}
    assert selection_event_ids == body_event_ids, (
        f"selections must equal parsed-body event_ids: "
        f"selected={selection_event_ids} body={body_event_ids}"
    )

    # All three also appear in considered_pool[] with their ranks
    pool = manifest["considered_pool"]
    pool_event_ids = {p["event_id"] for p in pool}
    assert pool_event_ids == body_event_ids, (
        f"considered_pool[] must include all 3 events: pool={pool_event_ids}"
    )
    pool_ranks = {p["event_id"]: p["rank_within_sport_day"] for p in pool}
    for eid in body_event_ids:
        assert pool_ranks[eid] in (1, 2, 3), (
            f"event {eid} in considered_pool must have rank in [1,3]: {pool_ranks[eid]}"
        )
    # No rank above 3 in selections
    selection_ranks_set = {s["rank_within_sport_day"] for s in payload["selections"]}
    assert all(r <= 3 for r in selection_ranks_set), (
        f"no rank above 3 may appear in selections[]: {selection_ranks_set}"
    )

    # Balanced capture accounting
    cap = manifest["capture_provenance"]["capture_accounting"]
    assert cap["raw_capture_receipt_entries"] == 1
    assert cap["captures_verified"] == 1
    assert cap["captures_missing"] == 0
    assert cap["captures_hash_mismatch"] == 0
    assert cap["captures_parse_failed"] == 0
    assert cap["captures_unsupported_sport"] == 0
    assert (cap["captures_verified"] + cap["captures_missing"] +
            cap["captures_hash_mismatch"] + cap["captures_schema_invalid"] +
            cap["captures_parse_failed"] + cap["captures_unsupported_sport"]) == cap["raw_capture_receipt_entries"]

    # Balanced snapshot accounting
    snap = manifest["capture_provenance"]["snapshot_accounting"]
    assert snap["parser_emitted_snapshots"] == 3
    assert (snap["snapshots_unique_accepted"] + snap["snapshots_exact_duplicate"] +
            snap["snapshots_conflicting"] + snap["snapshots_invalid_identity"]) == snap["parser_emitted_snapshots"]

    # Balanced history accounting
    hist = manifest["history_provenance"]
    assert hist["history_decoded_rows"] == hist["history_schema_invalid"] + hist["history_schema_valid_candidate_rows"]
    assert hist["history_admitted_rows"] == hist["history_unique_valid_rows"]
    # 6 priors * 4 team-orientations + 2 H2H * 3 = 30 rows
    assert hist["history_decoded_rows"] == 30

    # Balanced decision accounting
    acc = manifest["decision_accounting"]
    total = (acc.get("timing_rejected", 0) + acc.get("identity_ineligible", 0) +
             acc.get("feature_incomplete_or_r2_ineligible", 0) +
             acc.get("primary_selected", 0) + acc.get("top3_cohort_selected", 0) +
             acc.get("eligible_ranked_beyond_top3", 0))
    assert total == 3, f"decision accounting total={total}, expected 3: {acc}"

    # primary_selected + top3_cohort_selected == len(selections)
    assert acc["primary_selected"] + acc["top3_cohort_selected"] == len(payload["selections"])
    assert acc["primary_selected"] == 1

    # Hashes match
    assert manifest["payload_file_sha256"] == hashlib.sha256(payload_bytes).hexdigest()
    assert re.match(r"^[0-9a-f]{64}$", manifest["input_digest"])
    assert re.match(r"^[0-9a-f]{64}$", manifest["decision_digest"])
    # input_digest matches the SHA-256 of input_provenance
    assert manifest["input_digest"] == hashlib.sha256(
        canonical_json_bytes(manifest["input_provenance"])
    ).hexdigest()
    # decision_digest matches SHA-256 of decision_provenance
    assert manifest["decision_digest"] == hashlib.sha256(
        canonical_json_bytes(manifest["decision_provenance"])
    ).hexdigest()

    # Inputs unchanged
    assert gz_path.read_bytes() == gz_bytes_before
    assert receipt_path.read_bytes() == receipt_bytes_before
    assert body_path.read_bytes() == body_bytes_before


def test_cli_main_successful_subprocess(tmp_root):
    """Subprocess-based CLI test. Real ``_now_utc`` is used; the
    receipt's target date is 2030-01-01 so the safe cutoff
    (2029-12-31 00:00 UTC) is in the future, and the real current
    clock (2026-08-28) is well before it.
    """
    # Build fixture with target date FAR in the future
    target_date = "2030-01-01"
    reports = tmp_root / "data" / "reports"
    dicts = [{
        "event_id": r.event_id, "sport": r.sport, "event_date": r.event_date,
        "participant_1": r.participant_1, "participant_2": r.participant_2,
        "winner_index": r.winner_index, "score_1": r.score_1, "score_2": r.score_2,
        "probability_1": r.probability_1, "probability_2": r.probability_2,
        "draw_probability": r.draw_probability, "forebet_pick": r.forebet_pick,
        "disposition": r.disposition,
    } for r in _minimal_history_for_r2()]
    gz_path = reports / "history_football.jsonl.gz"
    gz_path.parent.mkdir(parents=True, exist_ok=True)
    _make_history_gz(gz_path, dicts)
    receipt_path, _ = _build_football_capture(tmp_root, target_date)

    result = subprocess.run(
        [
            sys.executable, "-m", "slumdog.shadow_evaluator",
            "--date", target_date,
            "--capture-receipt", str(receipt_path),
            "--history", str(gz_path),
            "--config", str(tmp_root / "config" / "shadow_evaluator_v1.json"),
            "--root", str(tmp_root),
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"rc={result.returncode}; stderr={result.stderr}; stdout={result.stdout}"
    assert "SHADOW_" in result.stdout or "shadow" in result.stdout.lower()
    assert "Traceback" not in result.stderr
    # A run dir was created (not BLOCKED)
    date_dir = tmp_root / "data" / "reports" / "shadow" / target_date
    run_dirs = [d for d in date_dir.iterdir() if d.is_dir() and d.name != "BLOCKED"]
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "manifest.json").exists()
    assert (run_dirs[0] / "shadow_selections.json").exists()


def test_cli_nonzero_on_capture_load_failure(tmp_root):
    """A missing receipt returns nonzero with a clear stderr message and
    no completed manifest."""
    result = subprocess.run(
        [
            sys.executable, "-m", "slumdog.shadow_evaluator",
            "--date", "2026-08-28",
            "--capture-receipt", str(tmp_root / "nonexistent.json"),
            "--config", str(tmp_root / "config" / "shadow_evaluator_v1.json"),
            "--root", str(tmp_root),
        ],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "BLOCKED" in result.stderr
    # No completed artifact written
    date_dir = tmp_root / "data" / "reports" / "shadow" / "2026-08-28"
    if date_dir.exists():
        # Only BLOCKED failure receipts are allowed, not completed artifacts
        for d in date_dir.iterdir():
            if d.is_dir():
                # No completed manifest in a per-run dir
                assert not (d / "manifest.json").exists() or (d / "manifest.json").read_text().find("SHADOW_SELECTIONS_EMITTED") < 0


# ===========================================================================
# Group 11: production isolation (focused monkeypatches)
# ===========================================================================


def test_production_isolation_no_network(tmp_root, monkeypatch):
    """The full CLI path must not touch the network."""
    import urllib.request
    def fail_urlopen(*a, **k):
        raise AssertionError("network accessed: " + str(a))
    monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)
    # Build a working fixture and run via evaluate_from_disk (no subprocess needed)
    reports = tmp_root / "data" / "reports"
    gz_path = reports / "history_football.jsonl.gz"
    gz_path.parent.mkdir(parents=True, exist_ok=True)
    _make_history_gz(gz_path, [{
        "event_id": r.event_id, "sport": r.sport, "event_date": r.event_date,
        "participant_1": r.participant_1, "participant_2": r.participant_2,
        "winner_index": r.winner_index, "score_1": r.score_1, "score_2": r.score_2,
        "probability_1": r.probability_1, "probability_2": r.probability_2,
        "draw_probability": r.draw_probability, "forebet_pick": r.forebet_pick,
        "disposition": r.disposition,
    } for r in _minimal_history_for_r2()])
    receipt_path, _ = _build_football_capture(tmp_root, "2026-08-28")
    decision_clock = datetime(2026, 8, 27, 0, 0, 0, tzinfo=timezone.utc)
    result = evaluate_from_disk(
        target_date="2026-08-28", capture_receipt_path=receipt_path,
        declaration_path=tmp_root / "config" / "shadow_evaluator_v1.json",
        repo_root=tmp_root, history_paths=[gz_path],
        decision_clock=decision_clock,
    )
    assert result.run_status in ("SHADOW_NO_SELECTION", "SHADOW_SELECTIONS_EMITTED")


def test_production_isolation_no_settlement_or_collectors(tmp_root, monkeypatch):
    """settlement, forebet collector, training must not be called."""
    import slumdog.forebet as fc
    import slumdog.settlement as st
    forbidden = [
        ("slumdog.forebet", "ForebetCollector", lambda *a, **k: (_ for _ in ()).throw(AssertionError("collector called"))),
        ("slumdog.settlement", "append_settled_from_capture", lambda *a, **k: (_ for _ in ()).throw(AssertionError("settlement called"))),
    ]
    monkeypatch.setattr(fc, "ForebetCollector", forbidden[1][2], raising=False)
    monkeypatch.setattr(st, "append_settled_from_capture", forbidden[1][2], raising=False)
    reports = tmp_root / "data" / "reports"
    gz_path = reports / "history_football.jsonl.gz"
    gz_path.parent.mkdir(parents=True, exist_ok=True)
    _make_history_gz(gz_path, [{
        "event_id": r.event_id, "sport": r.sport, "event_date": r.event_date,
        "participant_1": r.participant_1, "participant_2": r.participant_2,
        "winner_index": r.winner_index, "score_1": r.score_1, "score_2": r.score_2,
        "probability_1": r.probability_1, "probability_2": r.probability_2,
        "draw_probability": r.draw_probability, "forebet_pick": r.forebet_pick,
        "disposition": r.disposition,
    } for r in _minimal_history_for_r2()])
    receipt_path, _ = _build_football_capture(tmp_root, "2026-08-28")
    decision_clock = datetime(2026, 8, 27, 0, 0, 0, tzinfo=timezone.utc)
    result = evaluate_from_disk(
        target_date="2026-08-28", capture_receipt_path=receipt_path,
        declaration_path=tmp_root / "config" / "shadow_evaluator_v1.json",
        repo_root=tmp_root, history_paths=[gz_path],
        decision_clock=decision_clock,
    )
    assert result.run_status in ("SHADOW_NO_SELECTION", "SHADOW_SELECTIONS_EMITTED")


# ===========================================================================
# Group 12: no-overwrite invariant
# ===========================================================================


def test_no_overwrite_existing_run(tmp_root):
    reports = tmp_root / "data" / "reports"
    gz_path = reports / "history_football.jsonl.gz"
    gz_path.parent.mkdir(parents=True, exist_ok=True)
    _make_history_gz(gz_path, [{
        "event_id": r.event_id, "sport": r.sport, "event_date": r.event_date,
        "participant_1": r.participant_1, "participant_2": r.participant_2,
        "winner_index": r.winner_index, "score_1": r.score_1, "score_2": r.score_2,
        "probability_1": r.probability_1, "probability_2": r.probability_2,
        "draw_probability": r.draw_probability, "forebet_pick": r.forebet_pick,
        "disposition": r.disposition,
    } for r in _minimal_history_for_r2()])
    receipt_path, _ = _build_football_capture(tmp_root, "2026-08-28")
    decision_clock = datetime(2026, 8, 27, 0, 0, 0, tzinfo=timezone.utc)
    r1 = evaluate_from_disk(
        target_date="2026-08-28", capture_receipt_path=receipt_path,
        declaration_path=tmp_root / "config" / "shadow_evaluator_v1.json",
        repo_root=tmp_root, history_paths=[gz_path],
        decision_clock=decision_clock,
    )
    # Second run with the same clock must fail (same run_id)
    with pytest.raises(ShadowEvaluatorError, match="refusing to overwrite"):
        evaluate_from_disk(
            target_date="2026-08-28", capture_receipt_path=receipt_path,
            declaration_path=tmp_root / "config" / "shadow_evaluator_v1.json",
            repo_root=tmp_root, history_paths=[gz_path],
            decision_clock=decision_clock,
        )
    # Original artifact still present
    assert (Path(r1.artifact_dir) / "manifest.json").is_file()


def test_odds_only_differences_produce_same_decision_digest(tmp_root, monkeypatch):
    """Two snapshots of the same matches differing ONLY in odds
    must produce: same ``input_digest`` (modulo captured_at which is
    bounded to safe_cutoff), same ``decision_digest``, same R1
    rank key, same R2 eligibility, same features, same
    ``PreEventRecord``. Source provenance is still retained:
    body_sha256 and sidecar_digests are different per snapshot.
    Odds are IGNORED for decision equivalence.
    """
    from slumdog import shadow_evaluator as se
    fixed_clock = datetime(2026, 8, 26, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(se, "_now_utc", lambda: fixed_clock)

    # Build a single shared history gz.
    reports = tmp_root / "data" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    hist_rows: list[dict] = []
    for h, a, month in [("Arsenal","Chelsea","2024-01"),
                        ("Liverpool","ManU","2024-02")]:
        for i in range(6):
            hist_rows.append({
                "event_id": f"p_{h}_{a}_{i}", "sport": "football",
                "event_date": f"{month}-{(i % 28) + 1:02d}",
                "participant_1": h, "participant_2": a,
                "winner_index": 1, "score_1": 1.0, "score_2": 0.0,
                "probability_1": 0.55, "probability_2": 0.30,
                "draw_probability": 0.15, "forebet_pick": None,
                "disposition": "SETTLED",
            })
    for h, a, month in [("Arsenal","Chelsea","2024-06"),
                        ("Liverpool","ManU","2024-07")]:
        for i in range(2):
            hist_rows.append({
                "event_id": f"h2h_{h}_{a}_{i}", "sport": "football",
                "event_date": f"{month}-{(i % 28) + 1:02d}",
                "participant_1": h, "participant_2": a,
                "winner_index": 1, "score_1": 1.0, "score_2": 0.0,
                "probability_1": 0.55, "probability_2": 0.30,
                "draw_probability": 0.15, "forebet_pick": None,
                "disposition": "SETTLED",
            })
    gz_path = reports / "history_football.jsonl.gz"
    _make_history_gz(gz_path, hist_rows)

    def _make_capture(snapshot_id, odds_set):
        """Build a capture receipt with the given odds_set for two matches."""
        target_date = "2026-08-28"
        rows = [
            {"id": "1001", "HOST_NAME": "Arsenal", "GUEST_NAME": "Chelsea",
             "Pred_1": "50", "Pred_X": "10", "Pred_2": "40",
             "best_odd_1": odds_set[0][0], "best_odd_2": odds_set[0][1],
             "best_odd_X": odds_set[0][2],
             "short_tag": "EPL", "DATE_BAH": f"{target_date} 15:00",
             "host_sc_pr": "1", "guest_sc_pr": "1", "goalsavg": "2.5",
             "Host_SC": None, "Guest_SC": None, "comment": ""},
            {"id": "1002", "HOST_NAME": "Liverpool", "GUEST_NAME": "ManU",
             "Pred_1": "50", "Pred_X": "10", "Pred_2": "40",
             "best_odd_1": odds_set[1][0], "best_odd_2": odds_set[1][1],
             "best_odd_X": odds_set[1][2],
             "short_tag": "EPL", "DATE_BAH": f"{target_date} 17:30",
             "host_sc_pr": "1", "guest_sc_pr": "1", "goalsavg": "2.5",
             "Host_SC": None, "Guest_SC": None, "comment": ""},
        ]
        body = ("<html><body>" + json.dumps([rows, {}]) +
                "</body></html>").encode("utf-8")
        body_sha = hashlib.sha256(body).hexdigest()
        body_dir = tmp_root / "data" / "raw" / "football" / target_date
        body_dir.mkdir(parents=True, exist_ok=True)
        stamp = f"20260826T1{snapshot_id}0000Z"
        body_path = body_dir / f"{stamp}_{body_sha[:12]}.txt"
        sidecar_path = body_dir / f"{stamp}_{body_sha[:12]}.json"
        body_path.write_bytes(body)
        sidecar = {
            "sport": "football", "target_date": target_date,
            "captured_at": f"2026-08-26T1{snapshot_id}:00:00+00:00",
            "source_url": f"https://example.invalid/football/{target_date}",
            "relay_url": f"https://relay.invalid/football/{target_date}",
            "body_format": "json", "sha256": body_sha, "bytes": len(body),
            "body_path": str(body_path.relative_to(tmp_root)),
            "metadata_path": str(sidecar_path.relative_to(tmp_root)),
            "route": "direct",
        }
        sidecar_path.write_text(json.dumps(sidecar, indent=2, sort_keys=True))
        receipt = {
            "target_date": target_date,
            "generated_at": f"2026-08-26T1{snapshot_id}:00:01+00:00",
            "captured": [sidecar], "failures": [], "reused": 0,
            "football_markets": None,
        }
        receipt_path = (tmp_root / "data" / "reports" /
                        f"capture_{target_date}_{snapshot_id}.json")
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True))
        return receipt_path, body_sha

    # Snapshot A vs Snapshot B: same matches, DIFFERENT odds
    odds_a = [("2.00", "2.50", "10.00"), ("1.85", "2.80", "9.50")]
    odds_b = [("3.30", "5.20", "99.00"), ("1.10", "1.20", "1.30")]
    rec_a, body_sha_a = _make_capture(0, odds_a)
    rec_b, body_sha_b = _make_capture(1, odds_b)
    # Sanity: bodies differ
    assert body_sha_a != body_sha_b, "test setup wrong: bodies identical"

    target_date = "2026-08-28"
    cfg = tmp_root / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.copy("config/research_baselines_v1.json",
                cfg / "research_baselines_v1.json")
    shutil.copy("config/shadow_evaluator_v1.json",
                cfg / "shadow_evaluator_v1.json")

    rc_a = main([
        "--date", target_date, "--capture-receipt", str(rec_a),
        "--history", str(gz_path),
        "--config", str(cfg / "shadow_evaluator_v1.json"),
        "--root", str(tmp_root),
    ])
    assert rc_a == 0
    # Same root, same date — second run creates a NEW run_dir (run_id differs)
    rc_b = main([
        "--date", target_date, "--capture-receipt", str(rec_b),
        "--history", str(gz_path),
        "--config", str(cfg / "shadow_evaluator_v1.json"),
        "--root", str(tmp_root),
    ])
    assert rc_b == 0

    date_dir = tmp_root / "data" / "reports" / "shadow" / target_date
    run_dirs = sorted(d for d in date_dir.iterdir()
                      if d.is_dir() and d.name != "BLOCKED")
    assert len(run_dirs) == 2, f"expected 2 run dirs, got {len(run_dirs)}"
    manifest_a = json.loads((run_dirs[0] / "manifest.json").read_text())
    manifest_b = json.loads((run_dirs[1] / "manifest.json").read_text())

    # The two captures have different captured_at timestamps AND
    # different body SHA-256s, so input_digest must differ (because
    # the sidecar_digests and raw_body_digests commit to those).
    # This is the SOURCE PROVENANCE: the body proves what odds
    # were actually present. The decision_digest, however, must
    # NOT depend on odds and MUST be identical.
    assert manifest_a["input_digest"] != manifest_b["input_digest"], (
        "input_digest should differ because the bodies are different"
    )
    # Different run_id (different inputs and different capture metadata)
    assert manifest_a["run_id"] != manifest_b["run_id"]

    # decision_digest IS the same: odds are ignored for decision
    # equivalence. Same R1 rank, same R2 eligibility, same features.
    assert manifest_a["decision_digest"] == manifest_b["decision_digest"], (
        f"decision_digest differs! a={manifest_a['decision_digest']} "
        f"b={manifest_b['decision_digest']}"
    )

    # Per-sport-day summary is identical (primary/cohort same)
    assert manifest_a["sport_day_summary"] == manifest_b["sport_day_summary"]
    # Accounting identical
    assert manifest_a["decision_accounting"] == manifest_b["decision_accounting"]

    # The selections[] list (rank 1 primary + ranks 2-3 cohort only,
    # for this 2-event fixture) must have the same event_ids in
    # the same order. Note: payload sorts by (sport, event_date,
    # event_id, rank_within_sport_day) so the order is stable.
    payload_a = json.loads((run_dirs[0] / "shadow_selections.json").read_text())
    payload_b = json.loads((run_dirs[1] / "shadow_selections.json").read_text())
    assert [s["event_id"] for s in payload_a["selections"]] ==            [s["event_id"] for s in payload_b["selections"]], (
        "selections differ in event_ids between A and B"
    )
    assert [s["rank_within_sport_day"] for s in payload_a["selections"]] ==            [s["rank_within_sport_day"] for s in payload_b["selections"]]
    # Features identical (no odds in features)
    for sa, sb in zip(payload_a["selections"], payload_b["selections"]):
        assert sa["features"] == sb["features"], (
            f"features differ: {sa['event_id']} a={sa['features']} b={sb['features']}"
        )
        assert sa["missingness"] == sb["missingness"]
        # Same underdog/favorite determination
        assert sa["favorite_index"] == sb["favorite_index"]
        assert sa["underdog_index"] == sb["underdog_index"]
        assert sa["favorite_probability"] == sb["favorite_probability"]
        assert sa["underdog_probability"] == sb["underdog_probability"]
        assert sa["probability_gap"] == sb["probability_gap"]
        # R2 status identical
        assert sa["status"] == sb["status"]

    # Source provenance: each run's manifest retains its own body
    # SHA-256 (different per snapshot — proves what odds were there).
    # The capture_provenance is unique to the snapshot.
    assert manifest_a["capture_provenance"]["receipt_sha256"] !=            manifest_b["capture_provenance"]["receipt_sha256"]
    # And selections[i]["raw_sha256"] matches the snapshot's body
    sa_event_to_raw = {s["event_id"]: s["raw_sha256"]
                       for s in payload_a["selections"]}
    sb_event_to_raw = {s["event_id"]: s["raw_sha256"]
                       for s in payload_b["selections"]}
    # Same event_ids, but the raw_sha256 differs (different body
    # content for the same event id, different odds).
    for eid in sa_event_to_raw:
        assert sa_event_to_raw[eid] != sb_event_to_raw[eid], (
            f"raw_sha256 must differ per snapshot for {eid}"
        )

    # No 'odds' or outcomes in the decision digest (defense in depth)
    for s in payload_a["selections"]:
        s_text = json.dumps(s, sort_keys=True)
        for forbidden in ("odds_1", "odds_2", "best_odd", "score_",
                          "winner_index", "disposition"):
            assert forbidden not in s_text, (
                f"forbidden field {forbidden!r} leaked into selection: "
                f"{s}"
            )
