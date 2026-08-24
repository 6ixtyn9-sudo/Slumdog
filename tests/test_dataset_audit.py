"""Tests for dataset_audit entry point — no network, writes only under /tmp, explicit status handling."""

import gzip
import json
import tempfile
from pathlib import Path

from slumdog.dataset_audit import audit_dataset


def make_settled_dict(
    event_id="football:1",
    sport="football",
    event_date="2026-01-01",
    p1="Team A",
    p2="Team B",
    prob1=0.6,
    prob2=0.3,
    draw_prob=0.1,
    winner=2,
    score1=0,
    score2=1,
    disposition="SETTLED",
    facets=None,
):
    return {
        "event_id": event_id,
        "sport": sport,
        "event_date": event_date,
        "participant_1": p1,
        "participant_2": p2,
        "winner_index": winner,
        "score_1": score1,
        "score_2": score2,
        "probability_1": prob1,
        "probability_2": prob2,
        "draw_probability": draw_prob,
        "forebet_pick": 1,
        "odds_1": None,
        "odds_2": None,
        "league": "TST",
        "period_scores_1": (),
        "period_scores_2": (),
        "source_url": "https://www.forebet.com/en/football/matches/a-b/1",
        "disposition": disposition,
        "facets": facets or {},
    }


def test_audit_no_supported_input_files_returns_2():
    with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_out:
        root = Path(tmp_root)
        # Create empty structure — no interim/settled_history.json nor reports/history_*.jsonl.gz
        (root / "interim").mkdir()
        (root / "reports").mkdir()

        receipt_path = Path(tmp_out) / "receipt.json"
        sample_path = Path(tmp_out) / "sample.json"

        code = audit_dataset(root, receipt_path, sample_path, sample_size=2)
        assert code == 0
        assert receipt_path.exists()
        data = json.loads(receipt_path.read_text())
        assert data["status"] == "NO_SUPPORTED_INPUT_FILES"


def test_audit_with_valid_settled_history_json():
    with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_out:
        root = Path(tmp_root)
        interim = root / "interim"
        interim.mkdir()
        reports = root / "reports"
        reports.mkdir()

        # Write settled_history.json
        settled = [
            make_settled_dict(event_id="football:1", event_date="2026-01-01"),
            make_settled_dict(event_id="football:2", event_date="2026-01-02"),
        ]
        (interim / "settled_history.json").write_text(json.dumps(settled))

        receipt_path = Path(tmp_out) / "receipt.json"
        sample_path = Path(tmp_out) / "sample.json"

        code = audit_dataset(root, receipt_path, sample_path, sample_size=1)
        assert code == 0
        assert receipt_path.exists()
        assert sample_path.exists()

        receipt = json.loads(receipt_path.read_text())
        assert receipt["raw_input_rows"] == 2
        assert receipt["eligible_examples"] == 2

        sample = json.loads(sample_path.read_text())
        assert len(sample) == 1


def test_audit_with_valid_history_jsonl_gz():
    with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_out:
        root = Path(tmp_root)
        (root / "interim").mkdir()
        reports = root / "reports"
        reports.mkdir()

        settled = [
            make_settled_dict(event_id="football:1", event_date="2026-01-01", facets={"raw_sha256": "a"*64}),
            make_settled_dict(event_id="football:2", event_date="2026-01-02", facets={"raw_sha256": "b"*64}),
        ]

        gz_path = reports / "history_football_2026.jsonl.gz"
        with gzip.open(gz_path, "wt", encoding="utf-8") as f:
            for d in settled:
                f.write(json.dumps(d) + "\n")

        receipt_path = Path(tmp_out) / "receipt.json"
        sample_path = Path(tmp_out) / "sample.json"

        code = audit_dataset(root, receipt_path, sample_path, sample_size=5)
        assert code == 0
        receipt = json.loads(receipt_path.read_text())
        assert receipt["raw_input_rows"] == 2
        assert receipt["eligible_examples"] == 2


def test_audit_fails_on_unreadable_json():
    with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_out:
        root = Path(tmp_root)
        interim = root / "interim"
        interim.mkdir()
        (root / "reports").mkdir()

        # Write corrupt JSON
        (interim / "settled_history.json").write_text("{ invalid json }")

        receipt_path = Path(tmp_out) / "receipt.json"
        sample_path = Path(tmp_out) / "sample.json"

        code = audit_dataset(root, receipt_path, sample_path, sample_size=1)
        assert code == 1


def test_audit_fails_on_corrupt_gzip():
    with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_out:
        root = Path(tmp_root)
        (root / "interim").mkdir()
        reports = root / "reports"
        reports.mkdir()

        gz_path = reports / "history_football_2026.jsonl.gz"
        gz_path.write_bytes(b"not gzip content")

        receipt_path = Path(tmp_out) / "receipt.json"
        sample_path = Path(tmp_out) / "sample.json"

        code = audit_dataset(root, receipt_path, sample_path, sample_size=1)
        assert code == 1


def test_audit_fails_on_unknown_schema_version():
    with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_out:
        root = Path(tmp_root)
        interim = root / "interim"
        interim.mkdir()
        (root / "reports").mkdir()

        settled = [make_settled_dict(event_id="football:1")]
        settled[0]["schema_version"] = "v999-unknown"
        (interim / "settled_history.json").write_text(json.dumps(settled))

        receipt_path = Path(tmp_out) / "receipt.json"
        sample_path = Path(tmp_out) / "sample.json"

        code = audit_dataset(root, receipt_path, sample_path, sample_size=1)
        assert code == 1


def test_audit_fails_on_conflicting_duplicates():
    with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_out:
        root = Path(tmp_root)
        interim = root / "interim"
        interim.mkdir()
        (root / "reports").mkdir()

        settled = [
            make_settled_dict(event_id="football:conflict", event_date="2026-01-01", winner=1, score1=1, score2=0),
            make_settled_dict(event_id="football:conflict", event_date="2026-01-01", winner=2, score1=0, score2=1),
        ]
        (interim / "settled_history.json").write_text(json.dumps(settled))

        receipt_path = Path(tmp_out) / "receipt.json"
        sample_path = Path(tmp_out) / "sample.json"

        code = audit_dataset(root, receipt_path, sample_path, sample_size=1)
        assert code == 1


def test_audit_rejects_non_tmp_output():
    with tempfile.TemporaryDirectory() as tmp_root:
        root = Path(tmp_root)
        (root / "interim").mkdir()
        (root / "reports").mkdir()
        settled = [make_settled_dict()]
        (root / "interim" / "settled_history.json").write_text(json.dumps(settled))

        # Non-/tmp paths should fail — use a path definitely outside /tmp
        receipt_path = Path("/home/user/Slumdog") / "receipt.json"
        sample_path = Path("/home/user/Slumdog") / "sample.json"

        code = audit_dataset(root, receipt_path, sample_path, sample_size=1)
        assert code == 1


def test_audit_counts_malformed_rows_visible():
    with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_out:
        root = Path(tmp_root)
        interim = root / "interim"
        interim.mkdir()
        (root / "reports").mkdir()

        settled = [
            make_settled_dict(event_id="football:1", event_date="2026-01-01"),
            {"bad": "row"},  # malformed
            make_settled_dict(event_id="football:2", event_date="2026-01-02"),
        ]
        (interim / "settled_history.json").write_text(json.dumps(settled))

        receipt_path = Path(tmp_out) / "receipt.json"
        sample_path = Path(tmp_out) / "sample.json"

        code = audit_dataset(root, receipt_path, sample_path, sample_size=5)
        assert code == 0
        receipt = json.loads(receipt_path.read_text())
        assert receipt["raw_input_rows"] == 3
        assert receipt["schema_excluded_rows"] == 1
        assert receipt["eligible_examples"] == 2
        assert "schema_exclusion_reasons" in receipt
        assert sum(receipt["schema_exclusion_reasons"].values()) == 1
