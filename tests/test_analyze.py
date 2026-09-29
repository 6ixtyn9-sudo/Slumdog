import gzip
import json

from slumdog.analyze import analyze_depth, latest_census, ledger_profile


def _write_census(root, rows):
    path = root / "data" / "reports" / "depth_sweep_2026-08-21.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "target_date": "2026-08-21",
        "rows": {
            sport: {
                "listing_events": spec["listing"],
                "both_prices": spec["priced"],
                "price_coverage": round(spec["priced"] / spec["listing"], 4),
                "details_requested": spec["listing"],
                "details_succeeded": spec["listing"],
                "details_enriched": spec["listing"],
                "missing_required_fields": spec["missing"],
                "field_presence": spec["presence"],
            }
            for sport, spec in rows.items()
        },
    }, indent=2))


def _write_history(root, sport, start, end, rows, priced, ledger_rows):
    reports = root / "data" / "reports"
    manifest = {
        "sport": sport, "start": start, "end": end,
        "dates_requested": 3, "dates_completed": 3,
        "settled_rows": len(ledger_rows), "priced_rows": priced,
        "void_rows": sum(1 for r in ledger_rows if r.get("disposition") == "VOID"),
        "failures": [], "history_file": f"data/reports/history_{sport}.jsonl.gz",
        "daily_receipts": [{"date": d, "settled_rows": 1} for d in (start, )],
    }
    (reports / f"history_{sport}.json").write_text(json.dumps(manifest, indent=2))
    with gzip.open(reports / f"history_{sport}.jsonl.gz", "wt", encoding="utf-8") as handle:
        for row in ledger_rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def test_latest_census_finds_newest(tmp_path):
    (tmp_path / "data" / "reports").mkdir(parents=True)
    (tmp_path / "data" / "reports" / "depth_sweep_2026-08-20.json").write_text("{}")
    (tmp_path / "data" / "reports" / "depth_sweep_2026-08-21.json").write_text('{"rows": {}}')
    census = latest_census(tmp_path / "data" / "reports")
    assert census is not None


def test_analyze_depth_builds_report_and_json(tmp_path):
    census_rows = {
        "football": {
            "listing": 10, "priced": 8, "missing": 1,
            "presence": {"detail_weather_present": 9, "detail_corners_present": 0},
        },
        "basketball": {
            "listing": 4, "priced": 0, "missing": 0,
            "presence": {"detail_quarter_data_present": 4},
        },
    }
    _write_census(tmp_path, census_rows)
    ledger_football = [
        {"event_date": "2026-08-01", "sport": "football", "league": "EPL",
         "odds_1": 2.0, "odds_2": 3.0, "disposition": "SETTLED"},
        {"event_date": "2026-08-02", "sport": "football", "league": "EPL",
         "odds_1": None, "odds_2": None, "disposition": "SETTLED"},
        {"event_date": "2026-08-03", "sport": "football", "league": "LaLiga",
         "odds_1": 1.5, "odds_2": 5.0, "disposition": "VOID"},
    ]
    _write_history(tmp_path, "football", "2026-08-01", "2026-08-03",
                   rows=3, priced=2, ledger_rows=ledger_football)

    out = analyze_depth(tmp_path, target_date="2026-08-21")
    assert out.exists() and out.name == "analysis_2026-08-21.md"

    receipt = json.loads((tmp_path / "data" / "reports" / "analysis_2026-08-21.json").read_text())
    assert receipt["census"]["football"]["listing_events"] == 10
    # Zero-presence detail field flagged.
    assert "detail_corners_present" in receipt["census"]["football"]["zero_presence_detail_fields"]
    # Ledger profile.
    ledger = receipt["history"]["football"]["ledger"]
    assert ledger["rows"] == 3
    assert ledger["priced_rows"] == 2
    assert ledger["void_rows"] == 1
    assert ledger["seasons"]["2026"]["rows"] == 3
    assert ledger["top_leagues"][0][0] == "EPL"
    # Summary.
    assert receipt["summary"]["history_rows"] == 3
    assert receipt["summary"]["history_price_coverage"] == round(2 / 3, 4)


def test_ledger_profile_handles_missing_ledger(tmp_path):
    assert ledger_profile(tmp_path / "data" / "reports", "tennis") == {}


# ---------------------------------------------------------------------------
# R1 scorecard
# ---------------------------------------------------------------------------

from slumdog.analyze import (
    MIN_N_FOR_SIGNIFICANCE,
    r1_scorecard,
    wilson_interval,
)


def _write_shadow_run(
    root, track_dir, date, run_id, *, sport_day_summary, grades,
    supplements=None, settled=True,
):
    """Write one synthetic <track>/<date>/<run_id>/ prediction run.

    ``settled=False`` omits settlement.json entirely (an unsettled date).
    ``supplements`` is a list of (timestamp_str, rows) pairs, each written
    as its own settlement_supplement_<timestamp>.json.
    """
    run_dir = root / "data" / "reports" / track_dir / date / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(json.dumps({
        "target_date": date, "run_id": run_id,
        "run_status": "SHADOW_SELECTIONS_EMITTED",
        "sport_day_summary": sport_day_summary,
    }, indent=2))
    if not settled:
        return run_dir
    (run_dir / "settlement.json").write_text(json.dumps({
        "target_date": date, "run_id": run_id,
        "settlement_schema_version": "shadow_settlement",
        "grading_contract": {"target": "UNDERDOG_WIN", "draw_is_failure": True,
                             "void_is_unresolved": True, "not_found_is_unsettled": True},
        "grades": grades,
    }, indent=2))
    for ts, rows in (supplements or []):
        (run_dir / f"settlement_supplement_{ts}.json").write_text(json.dumps({
            "target_date": date, "run_id": run_id,
            "generated_at": f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}T00:00:00Z",
            "rows": rows,
        }, indent=2))
    return run_dir


def _grade_row(
    event_id, sport, rank, considered_status, grade, *,
    disposition="SETTLED", winner_index=None, underdog_index=None,
    favorite_index=None, underdog_probability=None, favorite_probability=None,
    forebet_pick=None,
):
    row = {
        "event_id": event_id, "sport": sport, "rank_within_sport_day": rank,
        "considered_status": considered_status, "grade": grade,
        "disposition": disposition, "winner_index": winner_index,
        "underdog_index": underdog_index, "favorite_index": favorite_index,
        "underdog_probability": underdog_probability,
        "favorite_probability": favorite_probability,
        "event_date": "2026-01-01", "match_method": "exact_event_id",
        "source": "selections",
    }
    if forebet_pick is not None:
        row["settled_context"] = {"forebet_pick": forebet_pick}
    return row


def test_wilson_interval_narrows_with_more_data_and_handles_zero_n():
    assert wilson_interval(0, 0) is None
    small = wilson_interval(3, 4)
    big = wilson_interval(300, 400)
    assert small is not None and big is not None
    # Same point estimate (75%), far less certain at n=4.
    assert (small[1] - small[0]) > (big[1] - big[0])
    assert small[0] < 0.75 - 0.01  # wide enough to dip well below the point estimate


def test_r1_is_exactly_the_primary_shadow_selection_row(tmp_path):
    grades = [
        _grade_row("football:1", "football", 1, "PRIMARY_SHADOW_SELECTION", "SUCCESS",
                  winner_index=2, underdog_index=2, favorite_index=1,
                  underdog_probability=0.25, favorite_probability=0.55),
        _grade_row("football:2", "football", 2, "TOP3_EVALUATION_COHORT", "FAILURE",
                  winner_index=1, underdog_index=2, favorite_index=1),
    ]
    _write_shadow_run(tmp_path, "shadow", "2026-01-01", "runabc",
                      sport_day_summary=[{"sport": "football", "event_date": "2026-01-01",
                                          "status": "SHADOW_RULE_QUALIFIED"}],
                      grades=grades)
    path = r1_scorecard(tmp_path, target_date="2026-01-02")
    assert path.exists()
    receipt = json.loads((tmp_path / "data" / "reports" / "r1_scorecard_2026-01-02.json").read_text())
    standard = receipt["tracks"]["STANDARD"]
    assert standard["overall"]["n"] == 1  # only the rank-1 row counts, not rank 2
    assert standard["overall"]["successes"] == 1


def test_void_and_no_contest_excluded_draw_counts_as_loss(tmp_path):
    grades = [
        _grade_row("football:void", "football", 1, "PRIMARY_SHADOW_SELECTION", "UNRESOLVED",
                  disposition="VOID", winner_index=None, underdog_index=2, favorite_index=1),
        _grade_row("basketball:draw", "basketball", 1, "PRIMARY_SHADOW_SELECTION", "FAILURE",
                  disposition="SETTLED_DRAW", winner_index=0, underdog_index=2, favorite_index=1),
    ]
    _write_shadow_run(tmp_path, "shadow", "2026-01-01", "runabc",
                      sport_day_summary=[
                          {"sport": "football", "event_date": "2026-01-01", "status": "SHADOW_RULE_QUALIFIED"},
                          {"sport": "basketball", "event_date": "2026-01-01", "status": "SHADOW_RULE_QUALIFIED"},
                      ],
                      grades=grades)
    path = r1_scorecard(tmp_path, target_date="2026-01-02")
    receipt = json.loads(path.with_suffix(".json").read_text())
    standard = receipt["tracks"]["STANDARD"]
    # VOID excluded entirely (not in n); draw counts as a settled loss.
    assert standard["overall"]["n"] == 1
    assert standard["overall"]["successes"] == 0
    assert standard["overall"]["failures"] == 1
    assert standard["by_sport"]["football"]["n"] == 0
    assert standard["by_sport"]["basketball"]["n"] == 1
    assert standard["by_sport"]["basketball"]["failures"] == 1


def test_supplement_completes_a_previously_unsettled_row(tmp_path):
    grades = [
        _grade_row("football:1", "football", 1, "PRIMARY_SHADOW_SELECTION", "UNSETTLED"),
    ]
    supplement_row = _grade_row(
        "football:1", "football", 1, "PRIMARY_SHADOW_SELECTION", "SUCCESS",
        winner_index=2, underdog_index=2, favorite_index=1,
    )
    _write_shadow_run(
        tmp_path, "shadow", "2026-01-01", "runabc",
        sport_day_summary=[{"sport": "football", "event_date": "2026-01-01",
                           "status": "SHADOW_RULE_QUALIFIED"}],
        grades=grades,
        supplements=[("20260105T000000Z", [supplement_row])],
    )
    receipt = json.loads(
        r1_scorecard(tmp_path, target_date="2026-01-06").with_suffix(".json").read_text()
    )
    standard = receipt["tracks"]["STANDARD"]
    # Before the supplement this would have been UNSETTLED (n excludes it);
    # after merging, it is a settled SUCCESS.
    assert standard["overall"]["n"] == 1
    assert standard["overall"]["successes"] == 1


def test_baselines_are_computed_on_the_identical_rows(tmp_path):
    # Underdog (2) wins: our R1 pick succeeds, the favourite (1) loses,
    # and Forebet's own pick (1, i.e. the favourite) also loses.
    grades = [
        _grade_row("football:1", "football", 1, "PRIMARY_SHADOW_SELECTION", "SUCCESS",
                  winner_index=2, underdog_index=2, favorite_index=1,
                  underdog_probability=0.3, forebet_pick=1),
    ]
    _write_shadow_run(tmp_path, "shadow", "2026-01-01", "runabc",
                      sport_day_summary=[{"sport": "football", "event_date": "2026-01-01",
                                          "status": "SHADOW_RULE_QUALIFIED"}],
                      grades=grades)
    receipt = json.loads(
        r1_scorecard(tmp_path, target_date="2026-01-02").with_suffix(".json").read_text()
    )
    baselines = receipt["tracks"]["STANDARD"]["baselines_same_rows"]
    assert baselines["our_r1_pick"]["successes"] == 1
    assert baselines["always_favourite_same_rows"]["successes"] == 0
    assert baselines["always_favourite_same_rows"]["n"] == 1
    assert baselines["forebet_pick_same_rows"]["successes"] == 0
    assert baselines["forebet_pick_same_rows"]["n"] == 1
    assert baselines["forebet_pick_same_rows"]["rows_missing_forebet_pick"] == 0
    # Always-underdog on these same rows is definitionally the R1 outcome.
    assert baselines["always_underdog_same_rows"]["successes"] == 1
    assert baselines["always_underdog_same_rows"]["n"] == 1


def test_forebet_pick_missing_is_excluded_and_counted(tmp_path):
    grades = [
        _grade_row("football:1", "football", 1, "PRIMARY_SHADOW_SELECTION", "SUCCESS",
                  winner_index=2, underdog_index=2, favorite_index=1),  # no forebet_pick
    ]
    _write_shadow_run(tmp_path, "shadow", "2026-01-01", "runabc",
                      sport_day_summary=[{"sport": "football", "event_date": "2026-01-01",
                                          "status": "SHADOW_RULE_QUALIFIED"}],
                      grades=grades)
    receipt = json.loads(
        r1_scorecard(tmp_path, target_date="2026-01-02").with_suffix(".json").read_text()
    )
    fb = receipt["tracks"]["STANDARD"]["baselines_same_rows"]["forebet_pick_same_rows"]
    assert fb["n"] == 0
    assert fb["rows_missing_forebet_pick"] == 1


def test_probability_band_bucketing(tmp_path):
    grades = [
        _grade_row("football:1", "football", 1, "PRIMARY_SHADOW_SELECTION", "SUCCESS",
                  winner_index=2, underdog_index=2, favorite_index=1, underdog_probability=0.10),
        _grade_row("basketball:1", "basketball", 1, "PRIMARY_SHADOW_SELECTION", "FAILURE",
                  winner_index=1, underdog_index=2, favorite_index=1, underdog_probability=0.35),
        _grade_row("hockey:1", "hockey", 1, "PRIMARY_SHADOW_SELECTION", "SUCCESS",
                  winner_index=2, underdog_index=2, favorite_index=1, underdog_probability=None),
    ]
    _write_shadow_run(tmp_path, "shadow", "2026-01-01", "runabc",
                      sport_day_summary=[
                          {"sport": "football", "event_date": "2026-01-01", "status": "SHADOW_RULE_QUALIFIED"},
                          {"sport": "basketball", "event_date": "2026-01-01", "status": "SHADOW_RULE_QUALIFIED"},
                          {"sport": "hockey", "event_date": "2026-01-01", "status": "SHADOW_RULE_QUALIFIED"},
                      ],
                      grades=grades)
    receipt = json.loads(
        r1_scorecard(tmp_path, target_date="2026-01-02").with_suffix(".json").read_text()
    )
    bands = receipt["tracks"]["STANDARD"]["by_underdog_probability_band"]
    assert bands["<0.20"]["n"] == 1 and bands["<0.20"]["successes"] == 1
    assert bands["0.30-0.40"]["n"] == 1 and bands["0.30-0.40"]["successes"] == 0
    assert bands["unknown"]["n"] == 1 and bands["unknown"]["successes"] == 1


def test_coverage_counts_attempted_and_qualified_sport_days(tmp_path):
    _write_shadow_run(
        tmp_path, "shadow", "2026-01-01", "run1",
        sport_day_summary=[
            {"sport": "football", "event_date": "2026-01-01", "status": "SHADOW_RULE_QUALIFIED"},
            {"sport": "tennis", "event_date": "2026-01-01", "status": "SHADOW_NO_SELECTION"},
        ],
        grades=[_grade_row("football:1", "football", 1, "PRIMARY_SHADOW_SELECTION", "SUCCESS",
                           winner_index=2, underdog_index=2, favorite_index=1)],
    )
    _write_shadow_run(
        tmp_path, "shadow", "2026-01-02", "run2",
        sport_day_summary=[
            {"sport": "football", "event_date": "2026-01-02", "status": "SHADOW_NO_SELECTION"},
        ],
        grades=[],
    )
    receipt = json.loads(
        r1_scorecard(tmp_path, target_date="2026-01-03").with_suffix(".json").read_text()
    )
    coverage = receipt["tracks"]["STANDARD"]["coverage"]
    assert coverage["by_sport"]["football"]["sport_days_attempted"] == 2
    assert coverage["by_sport"]["football"]["sport_days_with_r1"] == 1
    assert coverage["by_sport"]["tennis"]["sport_days_attempted"] == 1
    assert coverage["by_sport"]["tennis"]["sport_days_with_r1"] == 0


def test_unsettled_dates_are_excluded_from_scope_and_not_counted(tmp_path):
    _write_shadow_run(
        tmp_path, "shadow", "2026-01-01", "run1",
        sport_day_summary=[{"sport": "football", "event_date": "2026-01-01",
                           "status": "SHADOW_RULE_QUALIFIED"}],
        grades=[_grade_row("football:1", "football", 1, "PRIMARY_SHADOW_SELECTION", "SUCCESS",
                           winner_index=2, underdog_index=2, favorite_index=1)],
    )
    _write_shadow_run(
        tmp_path, "shadow", "2026-01-05", "run2",
        sport_day_summary=[{"sport": "football", "event_date": "2026-01-05",
                           "status": "SHADOW_RULE_QUALIFIED"}],
        grades=[], settled=False,  # no settlement.json yet — future/preview run
    )
    receipt = json.loads(
        r1_scorecard(tmp_path, target_date="2026-01-06").with_suffix(".json").read_text()
    )
    scope = receipt["tracks"]["STANDARD"]["scope"]
    assert scope["settled_target_dates"] == ["2026-01-01"]
    assert scope["unsettled_target_dates_excluded"] == ["2026-01-05"]
    # The unsettled date's sport-day summary still counts toward coverage
    # (it answers "did an R1 exist", independent of settlement), but adds
    # nothing to the hit-rate n.
    assert receipt["tracks"]["STANDARD"]["overall"]["n"] == 1


def test_small_n_is_flagged_not_significant_and_large_n_is(tmp_path):
    grades = [
        _grade_row(f"football:{i}", "football", 1, "PRIMARY_SHADOW_SELECTION",
                  "SUCCESS" if i % 2 == 0 else "FAILURE",
                  winner_index=2 if i % 2 == 0 else 1, underdog_index=2, favorite_index=1)
        for i in range(40)
    ]
    # Give each its own date so all 40 are independently rank-1 (rank_within_sport_day
    # collisions don't matter across different dates/run dirs).
    for i, row in enumerate(grades):
        date = f"2026-02-{i + 1:02d}"
        _write_shadow_run(tmp_path, "shadow", date, f"run{i}",
                          sport_day_summary=[{"sport": "football", "event_date": date,
                                              "status": "SHADOW_RULE_QUALIFIED"}],
                          grades=[row])
    receipt = json.loads(
        r1_scorecard(tmp_path, target_date="2026-03-01").with_suffix(".json").read_text()
    )
    overall = receipt["tracks"]["STANDARD"]["overall"]
    assert overall["n"] == 40
    assert overall["significant_n"] is True
    assert "note" not in overall or "too small" not in overall.get("note", "")
    assert overall["n"] >= MIN_N_FOR_SIGNIFICANCE

    # And a single-row football report predictably resolves to n=1, flagged small.
    tiny_root = tmp_path / "tiny"
    _write_shadow_run(tiny_root, "shadow", "2026-01-01", "run1",
                      sport_day_summary=[{"sport": "football", "event_date": "2026-01-01",
                                          "status": "SHADOW_RULE_QUALIFIED"}],
                      grades=[_grade_row("football:1", "football", 1, "PRIMARY_SHADOW_SELECTION",
                                        "SUCCESS", winner_index=2, underdog_index=2, favorite_index=1)])
    tiny_receipt = json.loads(
        r1_scorecard(tiny_root, target_date="2026-01-02").with_suffix(".json").read_text()
    )
    tiny_overall = tiny_receipt["tracks"]["STANDARD"]["overall"]
    assert tiny_overall["significant_n"] is False
    assert "too small" in tiny_overall["note"]


def test_event_day_track_reports_unavailable_when_no_directory_exists(tmp_path):
    _write_shadow_run(tmp_path, "shadow", "2026-01-01", "run1",
                      sport_day_summary=[{"sport": "football", "event_date": "2026-01-01",
                                          "status": "SHADOW_RULE_QUALIFIED"}],
                      grades=[_grade_row("football:1", "football", 1, "PRIMARY_SHADOW_SELECTION",
                                        "SUCCESS", winner_index=2, underdog_index=2, favorite_index=1)])
    receipt = json.loads(
        r1_scorecard(tmp_path, target_date="2026-01-02").with_suffix(".json").read_text()
    )
    event_day = receipt["tracks"]["EVENT_DAY"]
    assert event_day["available"] is False
    assert "no" in event_day["note"].lower()
    # STANDARD track is unaffected and never pooled with EVENT_DAY.
    assert receipt["tracks"]["STANDARD"]["overall"]["n"] == 1


def test_tracks_are_never_pooled_even_when_both_have_data(tmp_path):
    _write_shadow_run(tmp_path, "shadow", "2026-01-01", "run1",
                      sport_day_summary=[{"sport": "football", "event_date": "2026-01-01",
                                          "status": "SHADOW_RULE_QUALIFIED"}],
                      grades=[_grade_row("football:1", "football", 1, "PRIMARY_SHADOW_SELECTION",
                                        "SUCCESS", winner_index=2, underdog_index=2, favorite_index=1)])
    _write_shadow_run(tmp_path, "shadow_event_day", "2026-01-01", "run2",
                      sport_day_summary=[{"sport": "football", "event_date": "2026-01-01",
                                          "status": "SHADOW_RULE_QUALIFIED"}],
                      grades=[_grade_row("football:2", "football", 1, "PRIMARY_SHADOW_SELECTION",
                                        "FAILURE", winner_index=1, underdog_index=2, favorite_index=1)])
    receipt = json.loads(
        r1_scorecard(tmp_path, target_date="2026-01-02").with_suffix(".json").read_text()
    )
    assert receipt["tracks"]["STANDARD"]["overall"]["n"] == 1
    assert receipt["tracks"]["STANDARD"]["overall"]["successes"] == 1
    assert receipt["tracks"]["EVENT_DAY"]["available"] is True
    assert receipt["tracks"]["EVENT_DAY"]["overall"]["n"] == 1
    assert receipt["tracks"]["EVENT_DAY"]["overall"]["successes"] == 0


def test_bundles_errata_settlements_directories_are_not_treated_as_dates(tmp_path):
    # These sibling directories exist for real in data/reports/shadow/ and
    # must never be misread as a <date> with run-id sub-directories.
    for name in ("bundles", "errata", "settlements"):
        (tmp_path / "data" / "reports" / "shadow" / name / "2026-01-01").mkdir(parents=True)
    _write_shadow_run(tmp_path, "shadow", "2026-01-01", "run1",
                      sport_day_summary=[{"sport": "football", "event_date": "2026-01-01",
                                          "status": "SHADOW_RULE_QUALIFIED"}],
                      grades=[_grade_row("football:1", "football", 1, "PRIMARY_SHADOW_SELECTION",
                                        "SUCCESS", winner_index=2, underdog_index=2, favorite_index=1)])
    # Must not raise, and must not miscount.
    receipt = json.loads(
        r1_scorecard(tmp_path, target_date="2026-01-02").with_suffix(".json").read_text()
    )
    assert receipt["tracks"]["STANDARD"]["overall"]["n"] == 1


def test_settled_draws_are_counted_and_fail_both_sides(tmp_path):
    grades = [
        _grade_row("football:draw", "football", 1, "PRIMARY_SHADOW_SELECTION", "FAILURE",
                  disposition="SETTLED_DRAW", winner_index=0, underdog_index=2, favorite_index=1),
    ]
    _write_shadow_run(tmp_path, "shadow", "2026-01-01", "runabc",
                      sport_day_summary=[{"sport": "football", "event_date": "2026-01-01",
                                          "status": "SHADOW_RULE_QUALIFIED"}],
                      grades=grades)
    receipt = json.loads(
        r1_scorecard(tmp_path, target_date="2026-01-02").with_suffix(".json").read_text()
    )
    standard = receipt["tracks"]["STANDARD"]
    assert standard["overall"]["settled_draws"] == 1
    assert standard["overall"]["failures"] == 1
    baselines = standard["baselines_same_rows"]
    # The draw fails the favourite bet too — not a mirror-image success.
    assert baselines["always_favourite_same_rows"]["failures"] == 1
    assert baselines["always_favourite_same_rows"]["successes"] == 0
