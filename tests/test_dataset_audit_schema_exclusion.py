import json
import tempfile
from pathlib import Path

from slumdog.dataset_audit import audit_dataset


def test_schema_exclusion_report_creation():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        # Create a mock data dir with some invalid rows
        data_dir = tmp_path / "data"
        interim_dir = data_dir / "interim"
        interim_dir.mkdir(parents=True)
        
        # Valid event plus some invalid ones
        events = [
            # Valid event
            {
                "sport": "football",
                "event_id": "100",
                "event_date": "2024-01-01T12:00:00Z",
                "participant_1": "Team A",
                "participant_2": "Team B",
                "winner_index": 1,
                "disposition": "SETTLED",
                "probability_1": 0.3,
                "probability_2": 0.4,
                "draw_probability": 0.3,
                "facets": {"some_key": "val", "other_key": 2}
            },
            # Missing participant 1
            {
                "sport": "football",
                "event_id": "101",
                "event_date": "2024-01-02T12:00:00Z",
                "participant_2": "Team B",
                "team1_alias": "Team Alias A", # participant-like field
                "winner_index": 1,
                "disposition": "SETTLED",
                "probability_1": 0.3,
                "probability_2": 0.4,
                "draw_probability": 0.3,
                "facets": {"huge_list": [1]*1000}
            },
            # Not a dict (will become __not_a_dict__)
            "I am a string, not a dict",
            # Invalid winner index
            {
                "sport": "football",
                "event_id": "102",
                "event_date": "2024-01-03T12:00:00Z",
                "participant_1": "Team A",
                "participant_2": "Team B",
                "winner_index": "1", # invalid
                "disposition": "SETTLED",
                "probability_1": 0.3,
                "probability_2": 0.4,
                "draw_probability": 0.3,
                "participant_1_long_name": "A" * 200, # truncation test
            }
        ]
        
        (interim_dir / "settled_history.json").write_text(json.dumps(events))

        receipt = tmp_path / "tmp" / "receipt.json"
        sample = tmp_path / "tmp" / "sample.json"
        schema_excl = tmp_path / "tmp" / "schema_excl.json"
        
        # 1. No report is written unless supplied
        code = audit_dataset(data_dir, receipt, sample)
        assert code == 0
        assert not schema_excl.exists()
        
        # 2. Output path must be under /tmp
        bad_schema_excl = Path("/home/user/bad_schema_excl.json") # Not under /tmp
        code = audit_dataset(data_dir, receipt, sample, schema_exclusion_report_path=bad_schema_excl)
        assert code == 1
        assert not bad_schema_excl.exists()
        
        # Test with correct /tmp path
        code = audit_dataset(data_dir, receipt, sample, schema_exclusion_report_path=schema_excl)
        assert code == 0
        assert schema_excl.exists()
        
        report = json.loads(schema_excl.read_text())
        assert len(report) == 3 # 3 invalid rows
        
        # 3. Missing-participant row appears with file and line/index
        missing_p1 = [r for r in report if r["exclusion_reason"] == "SCHEMA_MISSING_PARTICIPANT_1"][0]
        assert "settled_history.json" in missing_p1["source_file"]
        assert missing_p1["source_location"] == "index:1"
        
        # 4. Valid rows do not appear
        assert not any(r.get("event_id") == "100" for r in report)
        
        # 5. Multiple exclusion reasons are deterministic
        reasons = sorted(r["exclusion_reason"] for r in report)
        assert reasons == ["SCHEMA_INVALID_WINNER_INDEX_TYPE", "SCHEMA_MISSING_EVENT_ID", "SCHEMA_MISSING_PARTICIPANT_1"]
        
        # 6. Full raw dictionaries are not serialized
        assert "facets" not in missing_p1
        assert "huge_list" in missing_p1["facets_keys"]
        assert "top_level_keys" in missing_p1
        assert "participant_2" in missing_p1["top_level_keys"]
        
        # 7. Nested facets are represented only by type and sorted keys
        assert missing_p1["facets_type"] == "dict"
        assert missing_p1["facets_keys"] == ["huge_list"]
        
        # 8. Participant-like audit fields are bounded and deterministic
        assert "team1_alias" in missing_p1["participant_like_fields"]
        assert missing_p1["participant_like_fields"]["team1_alias"] == "Team Alias A"
        
        invalid_winner = [r for r in report if r["exclusion_reason"] == "SCHEMA_INVALID_WINNER_INDEX_TYPE"][0]
        long_name = invalid_winner["participant_like_fields"]["participant_1_long_name"]
        assert len(long_name) == 103 # 100 + "..."
        assert long_name.endswith("...")

def test_schema_exclusion_report_with_conflicts():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        data_dir = tmp_path / "data"
        interim_dir = data_dir / "interim"
        interim_dir.mkdir(parents=True)
        
        # Two conflicting valid events + one invalid event
        events = [
            {
                "sport": "football", "event_id": "100", "event_date": "2024-01-01T12:00:00Z",
                "participant_1": "A", "participant_2": "B", "winner_index": 1, "disposition": "SETTLED",
                "probability_1": 0.3, "probability_2": 0.4, "draw_probability": 0.3, "score_1": 1, "score_2": 0
            },
            {
                "sport": "football", "event_id": "100", "event_date": "2024-01-01T12:00:00Z",
                "participant_1": "A", "participant_2": "B", "winner_index": 1, "disposition": "SETTLED",
                "probability_1": 0.3, "probability_2": 0.4, "draw_probability": 0.3, "score_1": 2, "score_2": 0 # CONFLICT
            },
            {
                "sport": "football", "event_id": "101", "event_date": "2024-01-01T12:00:00Z",
                "participant_2": "B", "winner_index": 1, "disposition": "SETTLED" # MISSING P1
            }
        ]
        (interim_dir / "settled_history.json").write_text(json.dumps(events))

        receipt = tmp_path / "tmp" / "receipt.json"
        sample = tmp_path / "tmp" / "sample.json"
        schema_excl = tmp_path / "tmp" / "schema_excl.json"
        conflict_rep = tmp_path / "tmp" / "conflicts.json"
        
        # 10. Existing conflict behavior remains fail-closed without --conflict-report
        code = audit_dataset(data_dir, receipt, sample, schema_exclusion_report_path=schema_excl)
        assert code == 1
        # It still writes the schema exclusion report before failing
        assert schema_excl.exists()
        assert len(json.loads(schema_excl.read_text())) == 1

        # 9. Report is still emitted when census status is DATA_CONFLICTS
        schema_excl.unlink()
        code = audit_dataset(data_dir, receipt, sample, conflict_report_path=conflict_rep, schema_exclusion_report_path=schema_excl)
        assert code == 1 # DATA_CONFLICTS
        assert schema_excl.exists()
        assert len(json.loads(schema_excl.read_text())) == 1
        assert conflict_rep.exists()

def test_schema_exclusion_report_ordering_and_corrupt():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        data_dir = tmp_path / "data"
        interim_dir = data_dir / "interim"
        interim_dir.mkdir(parents=True)
        
        receipt = tmp_path / "tmp" / "receipt.json"
        sample = tmp_path / "tmp" / "sample.json"
        schema_excl = tmp_path / "tmp" / "schema_excl.json"
        
        # 11. Unknown schema/corrupt file still exits nonzero
        (interim_dir / "settled_history.json").write_text("{ corrupt json")
        code = audit_dataset(data_dir, receipt, sample, schema_exclusion_report_path=schema_excl)
        assert code == 1
        
        # Fix and test 12. Input order does not change report ordering (sorted by source_location)
        ev1 = {"participant_2": "B"} # Missing P1
        ev2 = {"participant_1": "A"} # Missing P2
        
        (interim_dir / "settled_history.json").write_text(json.dumps([ev1, ev2]))
        audit_dataset(data_dir, receipt, sample, schema_exclusion_report_path=schema_excl)
        rep_forward = json.loads(schema_excl.read_text())
        
        # Reverse order
        (interim_dir / "settled_history.json").write_text(json.dumps([ev2, ev1]))
        audit_dataset(data_dir, receipt, sample, schema_exclusion_report_path=schema_excl)
        rep_reverse = json.loads(schema_excl.read_text())
        
        # They should be sorted by source_file and source_location, so index:0 comes before index:1
        # which means the content order changes relative to input, but remains strictly deterministic by location
        assert rep_forward[0]["source_location"] == "index:0"
        assert rep_forward[1]["source_location"] == "index:1"
        assert rep_reverse[0]["source_location"] == "index:0"
        assert rep_reverse[1]["source_location"] == "index:1"
