"""Integrity tests for the rank-4+ settlement erratum.

The rank-4+ rows in every committed ``settlement.json`` were graded against
``underdog_index == 0`` — the schema's draw sentinel — because the manifest's
``considered_pool[]`` entries never carried an underdog identity. A rank-4+
``SUCCESS`` was therefore structurally unreachable and every such row graded
``FAILURE`` whatever the real result.

``scripts/rank4_settlement_erratum.py`` restates those grades from pre-event
probabilities already committed in each manifest, **without** modifying the
original evidence. These tests hold that erratum to its own claims:

- the originals are byte-identical to what the erratum says it read;
- every correction really did come from a sentinel-graded row;
- each corrected grade is independently re-derivable from the recorded fields;
- the generator fails closed rather than overwriting.

See ``docs/ADVERSARIAL_REVIEW_RANK4_SETTLEMENT_FINDINGS.md``.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SHADOW_ROOT = REPO_ROOT / "data" / "reports" / "shadow"
ERRATA_ROOT = SHADOW_ROOT / "errata"
SCRIPT = REPO_ROOT / "scripts" / "rank4_settlement_erratum.py"

sys.path.insert(0, str(REPO_ROOT / "src"))
from slumdog.shadow_settle import grade_underdog_win  # noqa: E402


def _errata() -> list[tuple[Path, dict]]:
    out = []
    for path in sorted(ERRATA_ROOT.rglob("*.rank4_erratum.json")):
        out.append((path, json.loads(path.read_text())))
    return out


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def errata():
    found = _errata()
    assert found, f"no errata found under {ERRATA_ROOT}"
    return found


def test_erratum_artifacts_verify_against_their_markers(errata):
    for path, payload in errata:
        marker = path.with_name(path.name + ".sha256")
        assert marker.is_file(), f"missing marker for {path}"
        expected = marker.read_text().split()[0].strip().lower()
        assert _sha256(path) == expected, f"erratum marker mismatch: {path}"
        assert payload["erratum_schema_version"] == "rank4_underdog_identity_erratum"


def test_original_evidence_is_untouched(errata):
    """The whole point of an append-only erratum: the originals never moved."""
    for _path, payload in errata:
        original = REPO_ROOT / payload["original_artifact"]["path"]
        assert original.is_file(), f"original artifact missing: {original}"
        assert _sha256(original) == payload["original_artifact"]["sha256"], (
            f"{original} no longer matches the sha256 the erratum recorded — "
            "committed settlement evidence has been altered"
        )
        assert payload["original_artifact"]["modified"] is False
        assert payload["governance"]["original_evidence_overwritten"] is False
        # the marker beside the original must still agree too
        marker = original.with_name(original.name + ".sha256")
        assert marker.read_text().split()[0].strip() == _sha256(original)


def test_manifest_is_untouched(errata):
    for _path, payload in errata:
        run_dir = (REPO_ROOT / payload["original_artifact"]["path"]).parent
        assert _sha256(run_dir / "manifest.json") == (
            payload["original_artifact"]["manifest_sha256"]
        )


def test_every_correction_came_from_a_sentinel_row(errata):
    """No row with a real identity may appear in the corrections list."""
    for _path, payload in errata:
        for c in payload["corrections"]:
            assert c["committed_underdog_index"] == 0, (
                f"{c['event_id']}: committed underdog_index was "
                f"{c['committed_underdog_index']}, not the 0 sentinel — this "
                "row was not affected by the defect and must not be restated"
            )
            assert c["committed_grade"] == "FAILURE"
            assert c["corrected_grade"] == "SUCCESS"


def test_corrected_identity_is_a_real_participant(errata):
    for _path, payload in errata:
        for c in payload["corrections"]:
            assert c["corrected_underdog_index"] in (1, 2), c
            assert c["favorite_index"] in (1, 2), c
            assert c["corrected_underdog_index"] != c["favorite_index"], c
            assert c["identity_provenance"] == "capture_record_tuples", c
            # the underdog must be the side that actually won
            assert c["corrected_underdog_index"] == c["winner_index"], c


def test_corrected_grade_is_independently_rederivable(errata):
    """Re-grade from the recorded fields with the production function."""
    for _path, payload in errata:
        for c in payload["corrections"]:
            assert grade_underdog_win(
                underdog_index=c["corrected_underdog_index"],
                winner_index=c["winner_index"],
                disposition=c["disposition"],
                sport=c["sport"],
            ) == c["corrected_grade"], c


def test_scoreline_agrees_with_the_corrected_winner(errata):
    """Cross-check against the raw scores, not just the recorded winner_index."""
    for _path, payload in errata:
        for c in payload["corrections"]:
            s1, s2, w = c["score_1"], c["score_2"], c["winner_index"]
            assert s1 is not None and s2 is not None, c
            if w == 1:
                assert s1 > s2, f"{c['event_id']}: winner=1 but score {s1}-{s2}"
            elif w == 2:
                assert s2 > s1, f"{c['event_id']}: winner=2 but score {s1}-{s2}"


def test_aggregates_are_internally_consistent(errata):
    total_changed = 0
    total_decided = 0
    total_success = 0
    for _path, payload in errata:
        r4 = payload["rank4_plus"]
        committed, corrected = r4["committed"], r4["corrected"]

        assert committed["successes"] == 0, (
            "committed evidence shows a rank-4+ SUCCESS — the defect was "
            "supposed to make that unreachable"
        )
        assert r4["rows_still_on_draw_sentinel"] == 0
        assert r4["grades_changed"] == len(payload["corrections"])
        # the same rows are decided before and after; only labels move
        assert committed["decided"] == corrected["decided"]
        assert committed["unsettled"] == corrected["unsettled"]
        assert committed["unresolved"] == corrected["unresolved"]
        assert (
            corrected["successes"] + corrected["failures"] == corrected["decided"]
        )
        if corrected["decided"]:
            assert corrected["success_rate"] == pytest.approx(
                corrected["successes"] / corrected["decided"]
            )
        total_changed += r4["grades_changed"]
        total_decided += corrected["decided"]
        total_success += corrected["successes"]

    # Pinned totals, restated as evidence grows. These are CORRECTED numbers
    # that restate the record; per AGENTS.md they must never be used to justify
    # a threshold, rule or config amendment.
    #
    #   2026-09-02   22 decided    6 success
    #   2026-09-05  480 decided  168 success
    #   2026-09-06  318 decided   96 success
    #   ------------------------------ (original audit: 270 / 820 = 0.3293)
    #   2026-09-07   35 decided   13 success  <- added 2026-09-08
    #   ------------------------------ (current:        283 / 855 = 0.3310)
    #
    # 2026-09-07 was settled by the D+1 automation on main at
    # 2026-09-08T04:00:49Z, i.e. by the UNFIXED grading code, so it carries the
    # same sentinel-0 defect and needs the same correction. Adding a date
    # widens the denominator; it is not a restatement of any earlier figure —
    # the three original per-date rows above are unchanged.
    assert total_changed == total_success == 283
    assert total_decided == 855
    assert total_success / total_decided == pytest.approx(0.3310, abs=1e-4)


def test_every_settled_date_is_covered(errata):
    """The original report read only two of the three settled dates.

    Its headline ``n=798`` was 480 (2026-09-05) + 318 (2026-09-06): 2026-09-02
    was silently skipped because two settlement artifacts exist for that
    run_id and the glob resolved to the wrong one. Coverage is pinned here so
    a missing date fails loudly instead of shrinking the denominator.
    """
    covered = {payload["target_date"] for _p, payload in errata}
    settled = {
        d.name
        for d in SHADOW_ROOT.iterdir()
        if d.is_dir() and any(d.glob("*/settlement.json"))
    }
    assert covered == settled, (
        f"erratum coverage {sorted(covered)} != settled dates {sorted(settled)}"
    )
    assert "2026-09-02" in covered


def test_regenerating_refuses_to_overwrite(errata):
    """Fail closed, exactly like ``write_settlement_artifact``."""
    before = {path: _sha256(path) for path, _p in errata}
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=300,
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "refusing to overwrite existing erratum" in proc.stderr
    for path, digest in before.items():
        assert _sha256(path) == digest, f"{path} was modified by a refused re-run"


def test_skip_existing_appends_without_rewriting_published_errata(errata):
    """Incremental mode must never touch already-published corrections.

    The generator used to be one-shot: after its first run it failed closed
    forever, so a newly discovered defective date could not be corrected
    without deliberately deleting published, hash-marked evidence. That matters
    because ``main`` keeps producing defective artifacts on the D+1 schedule
    until the grading fix merges — 2026-09-07 landed the day after the first
    three were published.

    ``--skip-existing`` appends only what is missing. With everything already
    published it must write nothing at all and leave every byte alone. The
    default (no flag) still refuses outright — see
    ``test_regenerating_refuses_to_overwrite``.
    """
    before = {path: _sha256(path) for path, _p in errata}
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--skip-existing"],
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # Idempotent: nothing new to write, so nothing written.
    assert "wrote 0," in proc.stdout, proc.stdout
    # Every published erratum is named as skipped and left byte-identical.
    for path, digest in before.items():
        assert f"skipped {path.relative_to(REPO_ROOT)}" in proc.stdout, proc.stdout
        assert _sha256(path) == digest, f"{path} was modified by --skip-existing"


def test_check_mode_writes_nothing(errata):
    before = {path: _sha256(path) for path, _p in errata}
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "nothing written" in proc.stdout
    for path, digest in before.items():
        assert _sha256(path) == digest


def test_erratum_does_not_claim_a_rule_change(errata):
    """Governance: an instrument correction must not present itself as tuning."""
    for _path, payload in errata:
        basis = payload["correction_basis"]
        assert basis["pre_event_only"] is True
        assert basis["post_event_facts_used"] is False
        assert basis["grading_contract_changed"] is False
        assert basis["frozen_rule_config_touched"] is False
        assert payload["governance"]["result_driven_rule_amendment"] is False
        assert payload["governance"]["no_force_overwrite_respected"] is True
