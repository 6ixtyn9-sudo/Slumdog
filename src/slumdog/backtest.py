"""R1 rule backtest over the historical settlement corpus.

Owner directive (2026-09-28 redirect, second half): the live R1 scorecard
(``analyze.r1_scorecard``) answers "what happened on the 44 rows we have
graded so far" and is honest that 44 rows cannot say whether the rule has
an edge. This module answers the only question that does not need more
live captures to answer: replay the SAME frozen rule --
``identify_forebet_underdog`` -> ``build_pre_event_features`` ->
``is_r2_eligible`` (``R2_CONSERVATIVE_FIXED_RULE``) -> ``r1_sort_key``
(``R1_ALWAYS_RANK_COMPARATOR``) -- over every settled row already sitting
in ``data/reports/history_<sport>.jsonl.gz``, and grade the reconstructed
pick against the ledger's own recorded winner.

Nothing here re-derives the rule. Every decision function is imported
from the module that already owns it (``underdog``, ``dataset``,
``baseline_analyzer``, ``shadow_settle``) so a backtest number can never
silently drift from what the live system would have picked.

THE CAVEAT THAT MUST NOT BE BURIED (owner directive, verbatim): every row
``backfill.backfill_sport`` has ever written carries
``SettledEvent.reconstruction == "HISTORICAL_PAGE"`` -- there is no code
path in this repository that writes anything else. That field name is not
decorative: ``settlement.parse_html_settled`` / ``parse_football_settled``
/ etc. read a match's probabilities off Forebet's *historical results
page*, fetched long after the match finished, not off a board captured
before kickoff. If Forebet recomputes or normalizes a match's displayed
probabilities after the fact (there is no way to tell from this page
alone), a hit rate measured against them is optimistically biased -- the
"pick" being graded was never actually available to anyone before the
match was already known. This module partitions every row it reconstructs
by its ``reconstruction`` value and refuses to present a HISTORICAL_PAGE
population's hit rate as if it answered the live question. If a
pre-event-captured population ever exists (it does not today -- see
``KNOWN_LIMITATIONS``), the two are reported side by side, never pooled;
if they disagree, the disagreement itself is the finding.

A second, smaller limitation: ``history_loader.load_valid_history``
excludes ``VOID``/``NO_CONTEST`` rows before this module ever sees them,
so a match that later voided never enters the day's candidate pool here,
whereas the live pipeline would have ranked it (VOID is a settlement fact,
unknowable at pick time). This under-pools relative to the live system by
whatever the VOID rate is for that sport -- typically a small minority of
rows, see each sport's ``manifest_section`` counts -- and is recorded in
``KNOWN_LIMITATIONS`` rather than silently accepted.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .analyze import (
    MIN_N_FOR_SIGNIFICANCE,
    PROBABILITY_BANDS,
    UNKNOWN_PROBABILITY_BAND,
    _forebet_pick_of,
    _grade_pick,
    _iter_track_runs,
    _probability_band,
    _rate_block,
    _score_pick_rows,
)
from .baseline_analyzer import is_r2_eligible, r1_sort_key
from .clock import today_iso
from .contracts import SettledEvent
from .dataset import build_pre_event_features
from .history import HistoryIndex
from .history_loader import load_valid_history
from .shadow_settle import grade_underdog_win
from .sports import HISTORY_STARTS, SPORTS
from .underdog import identify_forebet_underdog

#: A cutoff strictly after every date any ledger in this repo could ever
#: contain, so ``load_valid_history``'s ``event_date < cutoff`` filter
#: (designed for a single decision date) admits the entire corpus instead
#: of truncating it. Not a claim about when this runs.
_FAR_FUTURE_CUTOFF = "2099-12-31"

KNOWN_LIMITATIONS = (
    "Every row in every history_<sport>.jsonl.gz ledger this repo has ever "
    "written carries reconstruction=HISTORICAL_PAGE: its probabilities "
    "and forebet_pick were read off Forebet's historical results page "
    "after the match finished, not captured from a live board before "
    "kickoff. Whether that biases a hit rate depends on whether Forebet's "
    "historical page recomputes its displayed prediction or shows the one "
    "it published pre-kickoff -- see the VERDICT FIRST section above/at "
    "the top of this report, which tests that directly rather than "
    "assuming it. No PRE_EVENT_CAPTURE population exists in this corpus "
    "today; if one appears in a future run of this tool, it will be "
    "reported separately, never pooled with HISTORICAL_PAGE rows.",
    "history_loader.load_valid_history excludes VOID/NO_CONTEST rows "
    "before this module sees them, so a match that later voided is never "
    "in a day's candidate pool here even though the live pipeline (which "
    "cannot know VOID in advance) would have ranked it. This under-pools "
    "relative to the live system by each sport's VOID rate.",
)


def _sport_ledger_path(root: Path, sport: str) -> Path:
    return root / "data" / "reports" / f"history_{sport}.jsonl.gz"


def _reconstruct_sport(sport: str, root: Path) -> dict[str, Any]:
    """Replay the frozen R1/R2 rule over one sport's settled ledger.

    Returns a dict with ``available`` and, when true, the full set of
    reconstructed rows plus the manifest the loader produced (row counts,
    date range, exclusion counts) -- reported before any rate is computed,
    per the owner directive to establish what exists first.
    """
    ledger_path = _sport_ledger_path(root, sport)
    if not ledger_path.is_file():
        return {
            "sport": sport,
            "available": False,
            "note": f"{ledger_path} does not exist in this checkout",
        }

    load_result = load_valid_history(
        target_date=_FAR_FUTURE_CUTOFF,
        repo_root=root,
        history_paths=[ledger_path],
    )
    settled: list[SettledEvent] = load_result.settled
    manifest = dict(load_result.manifest_section)

    if not settled:
        return {
            "sport": sport,
            "available": True,
            "settled_row_count": 0,
            "manifest": manifest,
            "note": "ledger exists but contains zero ledger-valid rows",
        }

    dates = sorted({ev.event_date for ev in settled})
    history_index = HistoryIndex(settled)
    by_date: dict[str, list[SettledEvent]] = defaultdict(list)
    for ev in settled:
        by_date[ev.event_date].append(ev)

    reconstruction_counts: Counter[str] = Counter()
    r1_rows: list[dict[str, Any]] = []
    cohort_rows: list[dict[str, Any]] = []
    sport_days_with_settled_data = 0
    sport_days_with_eligible_pool = 0

    for event_date in dates:
        day_rows = by_date[event_date]
        sport_days_with_settled_data += 1
        ranked: list[tuple[tuple, SettledEvent, Any]] = []
        for ev in day_rows:
            reconstruction_counts[ev.reconstruction] += 1
            identity = identify_forebet_underdog(
                ev.probability_1, ev.probability_2, ev.draw_probability)
            if not identity.eligible:
                continue
            if identity.favorite_index not in (1, 2) or identity.underdog_index not in (1, 2):
                continue
            features, _missingness = build_pre_event_features(
                sport=sport, event_date=event_date,
                participant_1=ev.participant_1, participant_2=ev.participant_2,
                identity=identity, history=history_index,
            )
            if not is_r2_eligible(features):
                continue
            rank_key = r1_sort_key({"features": features, "event_id": ev.event_id})
            ranked.append((rank_key, ev, identity))

        if not ranked:
            continue
        sport_days_with_eligible_pool += 1
        ranked.sort(key=lambda item: item[0])
        for rank_idx, (_key, ev, identity) in enumerate(ranked, start=1):
            grade = grade_underdog_win(
                underdog_index=identity.underdog_index,
                winner_index=ev.winner_index,
                disposition=ev.disposition,
                sport=sport,
            )
            row = {
                "sport": sport,
                "event_date": event_date,
                "event_id": ev.event_id,
                "winner_index": ev.winner_index,
                "disposition": ev.disposition,
                "underdog_index": identity.underdog_index,
                "favorite_index": identity.favorite_index,
                "underdog_probability": identity.underdog_probability,
                "favorite_probability": identity.favorite_probability,
                "probability_1": ev.probability_1,
                "probability_2": ev.probability_2,
                "draw_probability": ev.draw_probability,
                "settled_context": {"forebet_pick": ev.forebet_pick},
                "reconstruction": ev.reconstruction,
                "rank_within_sport_day": rank_idx,
                "grade": grade,
            }
            cohort_rows.append(row)
            if rank_idx == 1:
                r1_rows.append(row)

    return {
        "sport": sport,
        "available": True,
        "settled_row_count": len(settled),
        "date_range": [dates[0], dates[-1]] if dates else None,
        "manifest": manifest,
        "reconstruction_populations": dict(reconstruction_counts),
        "sport_days_with_settled_data": sport_days_with_settled_data,
        "sport_days_with_eligible_r1": sport_days_with_eligible_pool,
        "r1_rows": r1_rows,
        "cohort_rows": cohort_rows,
        "settled_events": settled,
    }


#: A pair below this many matched rows can detect gross, systematic
#: recomputation (every pair moves the same way) but cannot bound a small,
#: occasional discrepancy -- say which of those two applies, every time.
_MIN_PAIRS_TO_BOUND_A_SMALL_BIAS = 30

#: Two probabilities this close are the same value modulo float/round-trip
#: noise; the ledger and the shadow captures both store 2-decimal Forebet
#: percentages, so this tolerance is far tighter than any real recompute
#: would produce.
_PROBABILITY_MATCH_TOLERANCE = 0.005


def _pre_event_r1_selections(reports_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """The genuinely pre-event-captured R1 picks, one list per track.

    Reads ``shadow_selections.json`` (frozen at ``captured_at``, before the
    match was known) rather than ``settlement.json`` (which only carries
    post-hoc grades) -- this is the one population in this repo that is
    NOT reconstructed from a historical page. Restricted to target dates
    that already have a ``settlement.json`` (the same scope
    ``analyze.r1_scorecard`` uses), so every row returned here is settled
    and comparable to the historical ledger. Tracks are kept separate,
    never pooled, matching every other rule in this codebase.
    """
    tracks = {"STANDARD": reports_dir / "shadow", "EVENT_DAY": reports_dir / "shadow_event_day"}
    out: dict[str, list[dict[str, Any]]] = {}
    for track_name, track_root in tracks.items():
        rows: list[dict[str, Any]] = []
        try:
            runs = list(_iter_track_runs(track_root))
        except Exception:
            runs = []
        for _target_date, _run_id, run_dir in runs:
            if not (run_dir / "settlement.json").exists():
                continue
            sel_path = run_dir / "shadow_selections.json"
            if not sel_path.exists():
                continue
            try:
                sel_doc = json.loads(sel_path.read_text())
            except Exception:
                continue
            for sel in sel_doc.get("selections", []):
                if sel.get("status") == "PRIMARY_SHADOW_SELECTION":
                    rows.append(sel)
        out[track_name] = rows
    return out


def _contamination_check(
    reports_dir: Path, historical_by_key: dict[tuple[str, str], SettledEvent],
) -> dict[str, Any]:
    """Does Forebet's historical results page show the SAME prediction it
    published before kickoff, or a recomputed one?

    Joins every genuinely pre-event R1 pick this repo holds (frozen
    ``captured_at``, before any result was known) against the matching row
    in the historical-page ledger by ``event_id``, and compares the
    favourite/underdog probabilities and the underdog side itself. This
    decides whether ``populations["HISTORICAL_PAGE"]`` in this same report
    can be trusted or is only an upper bound -- see the module docstring.
    """
    pre_event_by_track = _pre_event_r1_selections(reports_dir)
    result: dict[str, Any] = {}
    for track_name, pre_rows in pre_event_by_track.items():
        pairs: list[dict[str, Any]] = []
        unmatched = 0
        for sel in pre_rows:
            sport = sel.get("sport")
            event_id = sel.get("event_id")
            key = (sport, event_id)
            hist_ev = historical_by_key.get(key)
            if hist_ev is None:
                unmatched += 1
                continue
            hist_identity = identify_forebet_underdog(
                hist_ev.probability_1, hist_ev.probability_2, hist_ev.draw_probability)
            pre_fav_p = sel.get("favorite_probability")
            pre_dog_p = sel.get("underdog_probability")
            pre_dog_idx = sel.get("underdog_index")
            deltas = []
            if pre_fav_p is not None and hist_identity.favorite_probability is not None:
                deltas.append(abs(pre_fav_p - hist_identity.favorite_probability))
            if pre_dog_p is not None and hist_identity.underdog_probability is not None:
                deltas.append(abs(pre_dog_p - hist_identity.underdog_probability))
            max_delta = max(deltas) if deltas else None
            identity_flipped = (
                pre_dog_idx is not None
                and hist_identity.underdog_index is not None
                and pre_dog_idx != hist_identity.underdog_index
            )
            differs = identity_flipped or (
                max_delta is not None and max_delta > _PROBABILITY_MATCH_TOLERANCE
            )
            pairs.append({
                "sport": sport,
                "event_id": event_id,
                "pre_event_favorite_probability": pre_fav_p,
                "pre_event_underdog_probability": pre_dog_p,
                "historical_page_favorite_probability": hist_identity.favorite_probability,
                "historical_page_underdog_probability": hist_identity.underdog_probability,
                "max_absolute_probability_delta": max_delta,
                "underdog_identity_flipped": identity_flipped,
                "differs": differs,
            })

        matched = len(pairs)
        differing = [p for p in pairs if p["differs"]]
        flipped = [p for p in pairs if p["underdog_identity_flipped"]]
        deltas_present = [p["max_absolute_probability_delta"] for p in pairs
                           if p["max_absolute_probability_delta"] is not None]

        if matched == 0:
            verdict = "INSUFFICIENT_DATA"
            headline = (
                f"0 of {len(pre_rows)} pre-event {track_name} pick(s) matched a row in the "
                "historical ledger by event_id -- either no ledger is present in this "
                "checkout, or none of these matches are in it yet. Cannot say anything "
                "about contamination for this track."
            )
        elif differing:
            verdict = "DIFFERING"
            headline = (
                f"{len(differing)}/{matched} matched pair(s) differ between the pre-event "
                f"capture and the historical-page ledger ({len(flipped)} with the underdog "
                "side itself flipped). Forebet's historical page does NOT reliably show the "
                "prediction it made before kickoff for this track -- every hit rate on the "
                "HISTORICAL_PAGE population must be labelled an upper bound, not a measurement."
            )
        else:
            verdict = "IDENTICAL"
            headline = (
                f"All {matched} matched pair(s) agree between the pre-event capture and the "
                "historical-page ledger (no probability differed by more than "
                f"{_PROBABILITY_MATCH_TOLERANCE}, no underdog side flipped)."
            )
        can_bound_small_bias = matched >= _MIN_PAIRS_TO_BOUND_A_SMALL_BIAS
        result[track_name] = {
            "verdict": verdict,
            "headline": headline,
            "pre_event_picks_available": len(pre_rows),
            "matched_to_historical_ledger": matched,
            "unmatched_no_ledger_row": unmatched,
            "differing_count": len(differing),
            "underdog_identity_flipped_count": len(flipped),
            "max_absolute_probability_delta_seen": max(deltas_present) if deltas_present else None,
            "statistical_power_note": (
                f"n={matched}: {'enough to bound even a small systematic bias' if can_bound_small_bias else 'enough to detect gross, systematic recomputation (every pair moving the same way) but NOT enough to bound a small, occasional discrepancy'} "
                f"(bound threshold used here: {_MIN_PAIRS_TO_BOUND_A_SMALL_BIAS} pairs)."
            ),
            "sample_pairs": pairs[:10],
        }
    return result


def _calibration_block(rows: list[dict[str, Any]], *, side: str) -> dict[str, Any]:
    """Observed outright-win rate against Forebet's mean assigned probability.

    The comparison is always on exactly the rows with both a decided grade and
    the named side's probability. Draws are failures for either side, matching
    the product contract and the raw-rate baseline.
    """
    probability_key = f"{side}_probability"
    eligible: list[tuple[dict[str, Any], float, str]] = []
    for row in rows:
        probability = row.get(probability_key)
        if not isinstance(probability, (int, float)) or isinstance(probability, bool):
            continue
        grade = (row.get("grade") if side == "underdog"
                 else _grade_pick(row, row.get("favorite_index")))
        if grade not in ("SUCCESS", "FAILURE"):
            continue
        eligible.append((row, float(probability), grade))

    successes = sum(1 for _row, _p, grade in eligible if grade == "SUCCESS")
    rate = _rate_block(successes, len(eligible))
    predicted_mean = (
        sum(probability for _row, probability, _grade in eligible) / len(eligible)
        if eligible else None
    )
    observed = rate.get("hit_rate")
    return {
        **rate,
        "mean_predicted_probability": predicted_mean,
        "observed_minus_predicted": (
            observed - predicted_mean
            if observed is not None and predicted_mean is not None else None),
        "rows_missing_probability_or_decision": len(rows) - len(eligible),
    }


def _calibration(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Calibration is the merit test; favourite-side calibration is control."""
    by_sport_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_band_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_sport_rows[row["sport"]].append(row)
        by_band_rows[_probability_band(row.get("underdog_probability"))].append(row)

    def pair(group: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "r1_underdog": _calibration_block(group, side="underdog"),
            "favourite_control": _calibration_block(group, side="favorite"),
        }

    band_labels = [label for label, _, _ in PROBABILITY_BANDS] + [UNKNOWN_PROBABILITY_BAND]
    return {
        "interpretation": (
            "PRIMARY MERIT METRIC: observed outright-win rate minus Forebet's "
            "mean assigned probability on the same R1 rows. Raw underdog-vs-"
            "favourite hit rates are retained below as descriptive baselines, "
            "not the measure of whether an underdog selector has edge. The "
            "favourite side is a calibration control: if it moves the same way, "
            "the effect may be Forebet-wide calibration bias rather than R1 selection."
        ),
        "overall": pair(rows),
        "by_underdog_probability_band": {
            label: pair(by_band_rows.get(label, [])) for label in band_labels
        },
        "by_sport": {
            sport: pair(group) for sport, group in sorted(by_sport_rows.items())
        },
    }


def _surplus_with_shifted_wilson(block: dict[str, Any]) -> dict[str, Any]:
    predicted = block.get("mean_predicted_probability")
    lo = block.get("wilson_95_lo")
    hi = block.get("wilson_95_hi")
    return {
        **block,
        "surplus_wilson_95_lo": lo - predicted if lo is not None and predicted is not None else None,
        "surplus_wilson_95_hi": hi - predicted if hi is not None and predicted is not None else None,
        "indicative_only_n_lt_500": (block.get("n") or 0) < 500,
    }


def _draw_space_split(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Separate clean two-way calibration from draw-capable differential."""
    two_way = [row for row in rows if not SPORTS[row["sport"]].draw_settles]
    draw_capable = [row for row in rows if SPORTS[row["sport"]].draw_settles]

    def two_way_block(group: list[dict[str, Any]]) -> dict[str, Any]:
        return _surplus_with_shifted_wilson(
            _calibration_block(group, side="underdog"))

    def differential(group: list[dict[str, Any]]) -> dict[str, Any]:
        dog = _calibration_block(group, side="underdog")
        fav = _calibration_block(group, side="favorite")
        usable = []
        for row in group:
            dog_p = row.get("underdog_probability")
            fav_p = row.get("favorite_probability")
            dog_grade = row.get("grade")
            fav_grade = _grade_pick(row, row.get("favorite_index"))
            if (not isinstance(dog_p, (int, float)) or isinstance(dog_p, bool)
                    or not isinstance(fav_p, (int, float)) or isinstance(fav_p, bool)
                    or dog_grade not in ("SUCCESS", "FAILURE")
                    or fav_grade not in ("SUCCESS", "FAILURE")):
                continue
            observed_difference = ((1 if dog_grade == "SUCCESS" else 0)
                                   - (1 if fav_grade == "SUCCESS" else 0))
            usable.append((observed_difference, float(dog_p) - float(fav_p)))
        if usable:
            values = [observed - predicted for observed, predicted in usable]
            mean = sum(values) / len(values)
            if len(values) > 1:
                variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
                half_width = 1.96 * math.sqrt(variance / len(values))
            else:
                half_width = None
        else:
            mean = None
            half_width = None
        return {
            "n": len(usable),
            "underdog": dog,
            "favourite_control": fav,
            "differential_surplus": (
                dog.get("observed_minus_predicted")
                - fav.get("observed_minus_predicted")
                if dog.get("observed_minus_predicted") is not None
                and fav.get("observed_minus_predicted") is not None else None),
            "differential_surplus_95_lo": mean - half_width if half_width is not None else None,
            "differential_surplus_95_hi": mean + half_width if half_width is not None else None,
            "differential_interval_method": (
                "normal 95% interval for the sample mean of paired "
                "[(underdog_win-favourite_win)-(p_underdog-p_favourite)]"
            ),
            "indicative_only_n_lt_500": len(usable) < 500,
        }

    two_way_by_sport = defaultdict(list)
    for row in two_way:
        two_way_by_sport[row["sport"]].append(row)
    draw_by_sport = defaultdict(list)
    for row in draw_capable:
        draw_by_sport[row["sport"]].append(row)
    return {
        "two_way_sports": {
            "sports": sorted(two_way_by_sport),
            "pooled": two_way_block(two_way),
            "per_sport": {sport: two_way_block(group)
                          for sport, group in sorted(two_way_by_sport.items())},
            "interpretation": (
                "Clean edge test: these sports cannot settle as draws, so "
                "underdog and favourite calibration errors are exact mirrors."
            ),
        },
        "draw_capable_sports": {
            "sports": sorted(draw_by_sport),
            "pooled": differential(draw_capable),
            "per_sport": {sport: differential(group)
                          for sport, group in sorted(draw_by_sport.items())},
            "interpretation": (
                "Use underdog surplus minus favourite surplus; shared movement "
                "in both win sides can be draw-probability miscalibration."
            ),
        },
    }


HOLDOUT_CUTOFF = "2026-06-30"


def _temporal_holdout(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Predeclared temporal split; records effects but changes no rule."""
    development = [row for row in rows if row["event_date"] <= HOLDOUT_CUTOFF]
    holdout = [row for row in rows if row["event_date"] > HOLDOUT_CUTOFF]

    def summarize(group: list[dict[str, Any]], draw_capable: bool) -> dict[str, Any]:
        if not draw_capable:
            return _surplus_with_shifted_wilson(
                _calibration_block(group, side="underdog"))
        return _draw_space_split(group)["draw_capable_sports"]["pooled"]

    sports = sorted({row["sport"] for row in rows})
    per_sport = {}
    for sport in sports:
        draw_capable = SPORTS[sport].draw_settles
        sport_rows = [row for row in rows if row["sport"] == sport]
        dev_rows = [row for row in development if row["sport"] == sport]
        hold_rows = [row for row in holdout if row["sport"] == sport]
        per_sport[sport] = {
            "outcome_space": "DRAW_CAPABLE" if draw_capable else "TWO_WAY",
            "all": summarize(sport_rows, draw_capable),
            "development_through_cutoff": summarize(dev_rows, draw_capable),
            "holdout_after_cutoff": summarize(hold_rows, draw_capable),
            "indicative_only_holdout_n_lt_500": len(hold_rows) < 500,
        }

    return {
        "cutoff": HOLDOUT_CUTOFF,
        "development_contract": f"event_date <= {HOLDOUT_CUTOFF}",
        "holdout_contract": f"event_date > {HOLDOUT_CUTOFF}",
        "multiplicity_warning": (
            "Per-sport effects were inspected across multiple sports. Treat a "
            "full-period effect as a lead only; it must preserve direction in "
            "the untouched holdout, and n<500 remains indicative only."
        ),
        "per_sport": per_sport,
    }


def _score_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Mirror analyze._track_scorecard's overall/by-sport/by-band/baselines
    shape exactly, so the backtest report reads like the live scorecard."""
    own_grades = [row["grade"] for row in rows]
    overall = _score_pick_rows(rows, own_grades)
    overall["settled_draws"] = sum(
        1 for row in rows
        if row["grade"] in ("SUCCESS", "FAILURE") and row["winner_index"] == 0
    )

    by_sport: dict[str, Any] = {}
    rows_by_sport: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        rows_by_sport[row["sport"]].append(row)
    for sport, sport_rows in sorted(rows_by_sport.items()):
        by_sport[sport] = _score_pick_rows(sport_rows, [r["grade"] for r in sport_rows])
        by_sport[sport]["r1_rows_recorded"] = len(sport_rows)

    by_band: dict[str, Any] = {}
    rows_by_band: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        rows_by_band[_probability_band(row.get("underdog_probability"))].append(row)
    band_labels = [label for label, _, _ in PROBABILITY_BANDS] + [UNKNOWN_PROBABILITY_BAND]
    for label in band_labels:
        band_rows = rows_by_band.get(label, [])
        by_band[label] = _score_pick_rows(band_rows, [r["grade"] for r in band_rows])
        by_band[label]["r1_rows_recorded"] = len(band_rows)

    favourite_grades = [_grade_pick(row, row["favorite_index"]) for row in rows]
    forebet_pick_rows = [(row, _forebet_pick_of(row)) for row in rows]
    forebet_pick_available = [(row, pick) for row, pick in forebet_pick_rows if pick is not None]
    forebet_pick_grades = [_grade_pick(row, pick) for row, pick in forebet_pick_available]

    baselines = {
        "our_r1_pick": {
            **overall,
            "note": "this IS the headline rate above, repeated for side-by-side comparison",
        },
        "always_underdog_same_rows": {
            **_score_pick_rows(rows, own_grades),
            "note": (
                "identical to our_r1_pick by construction on these SAME rows "
                "-- see cohort_wide_underdog_rate for a baseline that differs"
            ),
        },
        "always_favourite_same_rows": {
            **_rate_block(
                sum(1 for g in favourite_grades if g == "SUCCESS"),
                sum(1 for g in favourite_grades if g in ("SUCCESS", "FAILURE")),
            ),
            "note": (
                "a settled draw fails BOTH bets (outright win only), so "
                "successes+failures need not mirror our_r1_pick's exactly "
                f"even on these identical {overall['n']} matches"
            ),
        },
        "forebet_pick_same_rows": {
            **_rate_block(
                sum(1 for g in forebet_pick_grades if g == "SUCCESS"),
                sum(1 for g in forebet_pick_grades if g in ("SUCCESS", "FAILURE")),
            ),
            "rows_missing_forebet_pick": len(rows) - len(forebet_pick_available),
        },
    }

    return {
        "calibration": _calibration(rows),
        "draw_space_split": _draw_space_split(rows),
        "temporal_holdout": _temporal_holdout(rows),
        "coverage": {
            "distinct_sport_days": len({(row["sport"], row["event_date"]) for row in rows}),
            "distinct_calendar_days": len({row["event_date"] for row in rows}),
            "mean_r1_picks_per_sport_day": (
                len(rows) / len({(row["sport"], row["event_date"]) for row in rows})
                if rows else None),
            "mean_r1_picks_per_calendar_day": (
                len(rows) / len({row["event_date"] for row in rows}) if rows else None),
        },
        "overall": overall,
        "by_sport": by_sport,
        "by_underdog_probability_band": by_band,
        "baselines_same_rows": baselines,
        "raw_hit_rate_note": (
            "DESCRIPTIVE ONLY, NOT THE MERIT TEST: underdogs have lower assigned "
            "win probabilities by definition, so comparing their raw hit rate "
            "to favourites does not test whether R1 found miscalibration. Use "
            "the calibration section above."
        ),
    }


BOOTSTRAP_REPLICATES = 1000
BOOTSTRAP_SEED = 20260929


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _cluster_bootstrap_surplus(
    records: list[tuple[str, str, str, float, float]],
    bucket_labels: list[str],
    *,
    replicates: int = BOOTSTRAP_REPLICATES,
    seed: int = BOOTSTRAP_SEED,
    scheme_names: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Deterministic block bootstrap for calibration surplus.

    Records are ``(sport, event_date, bucket, predicted, observed_0_or_1)``.
    Calendar-day is primary because Forebet's model/regime is shared across
    sports; sport-day, ISO week and calendar month are sensitivity analyses.
    Aggregating before resampling keeps the 388k-row draw corpus cheap.
    """
    schemes = {
        "calendar_day_PRIMARY": lambda sport, day: day,
        "sport_day": lambda sport, day: f"{sport}:{day}",
        "iso_week": lambda sport, day: (
            lambda iso: f"{iso.year}-W{iso.week:02d}"
        )(dt.date.fromisoformat(day).isocalendar()),
        "calendar_month": lambda sport, day: day[:7],
    }
    output: dict[str, Any] = {
        "replicates": replicates,
        "seed": seed,
        "primary_block": "calendar_day_PRIMARY",
        "primary_block_reason": (
            "Forebet model/regime errors can be shared across physically "
            "unrelated sports on the same date; calendar-day blocks preserve "
            "that cross-sport dependence. Sport-day, week and month are shown "
            "as sensitivity analyses."
        ),
        "schemes": {},
    }
    selected_schemes = [
        (name, fn) for name, fn in schemes.items()
        if scheme_names is None or name in scheme_names
    ]
    for scheme_index, (scheme, key_fn) in enumerate(selected_schemes):
        blocks: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: defaultdict(lambda: [0.0, 0.0, 0.0]))
        for sport, day, bucket, predicted, observed in records:
            aggregate = blocks[key_fn(sport, day)][bucket]
            aggregate[0] += 1
            aggregate[1] += predicted
            aggregate[2] += observed
        keys = sorted(blocks)
        rng = random.Random(seed + scheme_index)
        draws: dict[str, list[float]] = defaultdict(list)
        if not keys:
            output["schemes"][scheme] = {
                "blocks": 0,
                "buckets": {
                    label: {"bootstrap_95_lo": None, "bootstrap_95_hi": None,
                            "valid_replicates": 0}
                    for label in bucket_labels
                },
            }
            continue
        for _ in range(replicates):
            totals = {label: [0.0, 0.0, 0.0] for label in bucket_labels}
            for _block in keys:
                sampled = blocks[keys[rng.randrange(len(keys))]]
                for label, aggregate in sampled.items():
                    total = totals[label]
                    total[0] += aggregate[0]
                    total[1] += aggregate[1]
                    total[2] += aggregate[2]
            for label, (n, predicted_sum, observed_sum) in totals.items():
                if n:
                    draws[label].append(observed_sum / n - predicted_sum / n)
        output["schemes"][scheme] = {
            "blocks": len(keys),
            "buckets": {
                label: {
                    "bootstrap_95_lo": _percentile(draws[label], 0.025),
                    "bootstrap_95_hi": _percentile(draws[label], 0.975),
                    "valid_replicates": len(draws[label]),
                }
                for label in bucket_labels
            },
        }
    return output


THREE_OUTCOME_PROBABILITY_BANDS: tuple[tuple[str, float, float], ...] = (
    ("<0.20", 0.0, 0.20),
    ("0.20-0.25", 0.20, 0.25),
    ("0.25-0.30", 0.25, 0.30),
    ("0.30-0.35", 0.30, 0.35),
    ("0.35+", 0.35, 1.0000001),
)


def _three_outcome_calibration(events: list[SettledEvent]) -> dict[str, Any]:
    """Calibration map for home/away/draw on sports where draws can settle."""
    draw_events = [event for event in events if SPORTS[event.sport].draw_settles]

    def outcome_map(group: list[SettledEvent], probability_attr: str,
                    winner_index: int) -> dict[str, Any]:
        by_band: dict[str, list[tuple[float, bool]]] = defaultdict(list)
        missing = 0
        for event in group:
            probability = getattr(event, probability_attr)
            if not isinstance(probability, (int, float)) or isinstance(probability, bool):
                missing += 1
                continue
            label = next((label for label, lo, hi in THREE_OUTCOME_PROBABILITY_BANDS
                          if lo <= float(probability) < hi), "unknown")
            by_band[label].append((float(probability), event.winner_index == winner_index))
        out = {}
        for label, _lo, _hi in THREE_OUTCOME_PROBABILITY_BANDS:
            rows = by_band.get(label, [])
            rates = _rate_block(sum(1 for _p, won in rows if won), len(rows))
            predicted = sum(p for p, _won in rows) / len(rows) if rows else None
            out[label] = {
                **rates,
                "mean_predicted_probability": predicted,
                "observed_minus_predicted": (
                    rates["hit_rate"] - predicted
                    if rates.get("hit_rate") is not None and predicted is not None else None),
                "surplus_wilson_95_lo": (
                    rates["wilson_95_lo"] - predicted
                    if rates.get("wilson_95_lo") is not None and predicted is not None else None),
                "surplus_wilson_95_hi": (
                    rates["wilson_95_hi"] - predicted
                    if rates.get("wilson_95_hi") is not None and predicted is not None else None),
                "indicative_only_n_lt_500": len(rows) < 500,
            }
        return {"buckets": out, "rows_missing_probability": missing}

    def map_group(group: list[SettledEvent]) -> dict[str, Any]:
        return {
            "home": outcome_map(group, "probability_1", 1),
            "away": outcome_map(group, "probability_2", 2),
            "draw": outcome_map(group, "draw_probability", 0),
        }

    by_sport: dict[str, list[SettledEvent]] = defaultdict(list)
    draw_bootstrap_records: list[tuple[str, str, str, float, int]] = []
    for event in draw_events:
        by_sport[event.sport].append(event)
        probability = event.draw_probability
        if isinstance(probability, (int, float)) and not isinstance(probability, bool):
            label = next((label for label, lo, hi in THREE_OUTCOME_PROBABILITY_BANDS
                          if lo <= float(probability) < hi), "unknown")
            if label != "unknown":
                draw_bootstrap_records.append((
                    event.sport, event.event_date, label, float(probability),
                    1 if event.winner_index == 0 else 0,
                ))
    labels = [label for label, _lo, _hi in THREE_OUTCOME_PROBABILITY_BANDS]
    return {
        "scope": "all ledger-valid settled rows in draw-capable sports, not only R1 picks",
        "bucket_contract": [label for label, _lo, _hi in THREE_OUTCOME_PROBABILITY_BANDS],
        "pooled": map_group(draw_events),
        "per_sport": {sport: map_group(group)
                      for sport, group in sorted(by_sport.items())},
        "sports": sorted(by_sport),
        "settled_rows_in_draw_capable_sports": len(draw_events),
        "draw_surplus_cluster_bootstrap": _cluster_bootstrap_surplus(
            draw_bootstrap_records, labels),
        "warning": (
            "A rare outcome is not a power play by itself. Only positive "
            "held-out observed-minus-predicted surplus with adequate n is "
            "candidate evidence. n<500 buckets are indicative only."
        ),
    }


GENUINE_DRAW_FORECAST_FLOOR = 0.005

LOW_DRAW_TAIL_BANDS: tuple[tuple[str, float, float], ...] = (
    ("<0.05", 0.0, 0.05),
    ("0.05-0.10", 0.05, 0.10),
    ("0.10-0.15", 0.10, 0.15),
    ("0.15-0.20", 0.15, 0.20),
)


def _low_draw_tail_analysis(events: list[SettledEvent]) -> dict[str, Any]:
    """Decompose the robust <0.20 draw effect without fitting a selector."""
    draw_events = [event for event in events if SPORTS[event.sport].draw_settles]
    labels = [label for label, _lo, _hi in LOW_DRAW_TAIL_BANDS]

    def summarize(group: list[SettledEvent], seed_offset: int) -> dict[str, Any]:
        by_band: dict[str, list[SettledEvent]] = defaultdict(list)
        records: list[tuple[str, str, str, float, float]] = []
        exclusion_counts: Counter[str] = Counter()
        raw_lt_0_20_n = 0
        for event in group:
            probability = event.draw_probability
            numeric_probability = (
                isinstance(probability, (int, float))
                and not isinstance(probability, bool)
            )
            if numeric_probability and float(probability) < 0.20:
                raw_lt_0_20_n += 1
            # draw_settles and board shape are deliberately distinct. MMA can
            # settle a fight draw but Forebet exposes a two-outcome board; any
            # zero-like value is not a draw forecast and must not enter calibration.
            if not SPORTS[event.sport].draw_possible:
                exclusion_counts["sport_board_does_not_publish_draw_probability"] += 1
                continue
            if not numeric_probability:
                exclusion_counts["missing_or_non_numeric_draw_probability"] += 1
                continue
            probability = float(probability)
            if probability < GENUINE_DRAW_FORECAST_FLOOR:
                exclusion_counts["below_0_005_no_forecast_floor"] += 1
                continue
            label = next((label for label, lo, hi in LOW_DRAW_TAIL_BANDS
                          if lo <= probability < hi), None)
            if label is None:
                continue
            by_band[label].append(event)
            records.append((event.sport, event.event_date, label, probability,
                            1.0 if event.winner_index == 0 else 0.0))
        buckets = {}
        for label in labels:
            rows = by_band.get(label, [])
            predicted = (sum(float(event.draw_probability) for event in rows) / len(rows)
                         if rows else None)
            observed = (sum(1 for event in rows if event.winner_index == 0) / len(rows)
                        if rows else None)
            dates = sorted({event.event_date for event in rows})
            buckets[label] = {
                "n": len(rows),
                "mean_predicted_probability": predicted,
                "observed_hit_rate": observed,
                "absolute_surplus": (
                    observed - predicted
                    if observed is not None and predicted is not None else None),
                "relative_surplus_observed_divided_by_predicted": (
                    observed / predicted
                    if observed is not None and predicted not in (None, 0) else None),
                "active_days": len(dates),
                "candidate_rows_per_active_day": (
                    len(rows) / len(dates) if dates else None),
                "indicative_only_n_lt_500": len(rows) < 500,
            }
        bootstrap = _cluster_bootstrap_surplus(
            records, labels, seed=BOOTSTRAP_SEED + seed_offset,
            scheme_names=("calendar_day_PRIMARY", "calendar_month"))
        return {
            "population": (
                "ledger-valid settled rows in sports whose board publishes a "
                "draw probability, after the predeclared 0.005 genuine-forecast "
                "floor; bucket rows require 0.005 <= draw_probability <0.20"
            ),
            "forecast_exclusion_audit": {
                "all_settled_rows_considered": len(group),
                "raw_numeric_lt_0_20_n_before_forecast_filter": raw_lt_0_20_n,
                "excluded": dict(sorted(exclusion_counts.items())),
                "retained_lt_0_20_n": sum(bucket["n"] for bucket in buckets.values()),
            },
            "parent_lt_0_20_n": sum(bucket["n"] for bucket in buckets.values()),
            "buckets": buckets,
            "cluster_bootstrap": bootstrap,
        }

    def classify_shape(period: dict[str, Any]) -> str:
        buckets = period["buckets"]
        month = period["cluster_bootstrap"]["schemes"].get(
            "calendar_month", {}).get("buckets", {})
        tail_ratio = buckets["<0.05"].get(
            "relative_surplus_observed_divided_by_predicted")
        higher_ratios = [
            buckets[label].get("relative_surplus_observed_divided_by_predicted")
            for label in labels[1:]
        ]
        tail_month_lo = month.get("<0.05", {}).get("bootstrap_95_lo")
        higher_null = sum(
            1 for label in labels[1:]
            if month.get(label, {}).get("bootstrap_95_lo") is not None
            and month[label]["bootstrap_95_lo"] <= 0
            and month[label].get("bootstrap_95_hi") is not None
            and month[label]["bootstrap_95_hi"] >= 0
        )
        extreme = (
            tail_month_lo is not None and tail_month_lo > 0
            and tail_ratio is not None
            and all(ratio is not None and tail_ratio >= 2 * ratio
                    for ratio in higher_ratios)
            and higher_null >= 2
        )
        positive_month = [
            label for label in labels
            if month.get(label, {}).get("bootstrap_95_lo") is not None
            and month[label]["bootstrap_95_lo"] > 0
        ]
        surpluses = [buckets[label].get("absolute_surplus") for label in labels]
        broad = (
            len(positive_month) >= 3
            and all(value is not None for value in surpluses)
            and max(surpluses) - min(surpluses) <= 0.005
        )
        if extreme:
            return "extreme-tail power-play shape in this period; holdout repetition required"
        if broad:
            return "mild broad miscalibration shape in this period; holdout repetition required"
        return "mixed or unresolved; do not call it a power play"

    def periods(group: list[SettledEvent], seed_offset: int) -> dict[str, Any]:
        output = {
            "all": summarize(group, seed_offset),
            "development_through_cutoff": summarize(
                [event for event in group if event.event_date <= HOLDOUT_CUTOFF],
                seed_offset + 1),
            "holdout_after_cutoff": summarize(
                [event for event in group if event.event_date > HOLDOUT_CUTOFF],
                seed_offset + 2),
        }
        for period in output.values():
            period["frozen_shape_verdict"] = classify_shape(period)
        development_verdict = output["development_through_cutoff"][
            "frozen_shape_verdict"]
        holdout = output["holdout_after_cutoff"]
        holdout_tail = holdout["buckets"]["<0.05"]
        holdout_month = holdout["cluster_bootstrap"]["schemes"].get(
            "calendar_month", {}).get("buckets", {}).get("<0.05", {})
        holdout_repeats_direction = (
            holdout_tail.get("absolute_surplus") is not None
            and holdout_tail["absolute_surplus"] > 0
            and holdout_month.get("bootstrap_95_lo") is not None
            and holdout_month["bootstrap_95_lo"] > 0
        )
        output["validated_shape_verdict"] = (
            "EXTREME-TAIL / POWER-PLAY SHAPE, VALIDATED"
            if development_verdict.startswith("extreme-tail")
            and holdout_repeats_direction
            else "NOT VALIDATED"
        )
        output["validation_rule_application"] = {
            "development_sets_shape": development_verdict,
            "holdout_need_only_repeat_positive_lt_0_05_direction": (
                holdout_repeats_direction),
            "holdout_standalone_verdict": holdout.get("frozen_shape_verdict"),
        }
        return output

    by_sport: dict[str, list[SettledEvent]] = defaultdict(list)
    for event in draw_events:
        by_sport[event.sport].append(event)
    pooled_periods = periods(draw_events, 3000)
    per_sport_periods = {
        sport: periods(group, 3100 + index * 10)
        for index, (sport, group) in enumerate(sorted(by_sport.items()))
    }

    composition = {}
    for period_name in ("all", "development_through_cutoff", "holdout_after_cutoff"):
        pooled_period = pooled_periods[period_name]
        pooled_n = pooled_period["buckets"]["<0.05"]["n"]
        sports = {}
        for sport, sport_periods in per_sport_periods.items():
            sport_period = sport_periods[period_name]
            bucket = sport_period["buckets"]["<0.05"]
            month = (sport_period["cluster_bootstrap"]["schemes"]
                     .get("calendar_month", {}).get("buckets", {}).get("<0.05", {}))
            sports[sport] = {
                **bucket,
                "share_of_pooled_retained_lt_0_05": (
                    bucket["n"] / pooled_n if pooled_n else None),
                "calendar_month_95_lo": month.get("bootstrap_95_lo"),
                "calendar_month_95_hi": month.get("bootstrap_95_hi"),
                "forecast_exclusion_audit": sport_period[
                    "forecast_exclusion_audit"],
            }
        composition[period_name] = {
            "pooled_retained_lt_0_05_n": pooled_n,
            "sports": sports,
            "reconciliation": {
                "raw_numeric_lt_0_20_n_before_forecast_filter": (
                    pooled_period["forecast_exclusion_audit"]
                    ["raw_numeric_lt_0_20_n_before_forecast_filter"]),
                "retained_lt_0_20_n": pooled_period[
                    "forecast_exclusion_audit"]["retained_lt_0_20_n"],
                "excluded_by_reason": pooled_period[
                    "forecast_exclusion_audit"]["excluded"],
                "note": (
                    "The unfiltered-to-filtered <0.05 gap is reconciled by the "
                    "per-sport below-floor and board/missing exclusions above; "
                    "never attribute the residual without these measured counts."
                ),
            },
        }
    return {
        "scope": (
            "all ledger-valid settled rows after requiring a sport board that "
            "publishes draw probability and predicted draw probability >=0.005; "
            "not a selector and not R1-only"
        ),
        "genuine_draw_forecast_contract": {
            "minimum_probability": GENUINE_DRAW_FORECAST_FLOOR,
            "excluded_board_contract": "SPORTS[sport].draw_possible is false",
            "rationale": (
                "Predeclared conservative floor for the decisive rerun: "
                "below 0.5% (one in 200) is treated as absent/zero-like encoding, "
                "not a calibrated small forecast. This removes cricket's 0.0146% "
                "sentinel-like mean without excluding football's genuine 3.48% cell."
            ),
        },
        "cutoff": HOLDOUT_CUTOFF,
        "bucket_contract": labels,
        "draw_outcome_semantics": {
            "football": "level full-time score; settled draw",
            "handball": "level full-time score; settled draw",
            "cricket": (
                "result text explicitly says draw; no-result, abandoned and "
                "cancelled contests are VOID and excluded by the history loader"
            ),
            "mma": (
                "unanimous, majority or split fight draw; no-contest is VOID and "
                "excluded by the history loader; Forebet's board is two-outcome, "
                "so draw_probability may be absent"
            ),
        },
        "semantic_warning": (
            "A cricket draw and an MMA fight draw are not the same object as a "
            "level-score football/handball draw; interpret per-sport concentration "
            "before proposing a shared product."
        ),
        "interval_contract": (
            "absolute observed-minus-predicted surplus, calendar-day primary and "
            "calendar-month sensitivity; relative surplus is observed/predicted"
        ),
        "predeclared_shape_interpretation": {
            "extreme_tail_power_play_shape": (
                "Development <0.05 month lower bound >0 and its relative ratio is "
                "at least twice every higher sub-bucket, with at least two higher "
                "sub-buckets whose month intervals include zero; holdout must repeat "
                "the direction before the shape is validated."
            ),
            "mild_broad_miscalibration_shape": (
                "At least three of four development month lower bounds exceed zero "
                "and the range of their absolute-surplus point estimates is <=0.005; "
                "holdout must repeat the direction before the shape is validated."
            ),
            "otherwise": "mixed or unresolved; do not call it a power play",
        },
        "pooled": pooled_periods,
        "per_sport": per_sport_periods,
        "retained_lt_0_05_composition": composition,
    }


def _handball_draw_diagnostics(events: list[SettledEvent]) -> dict[str, Any]:
    """Walk-forward and concentration audit for the handball-only tail lead."""
    rows = [event for event in events if event.sport == "handball"]

    def tail_summary(group: list[SettledEvent], seed_offset: int) -> dict[str, Any]:
        usable = [
            event for event in group
            if isinstance(event.draw_probability, (int, float))
            and not isinstance(event.draw_probability, bool)
            and GENUINE_DRAW_FORECAST_FLOOR <= float(event.draw_probability) < 0.05
        ]
        predicted = (sum(float(event.draw_probability) for event in usable) / len(usable)
                     if usable else None)
        observed = (sum(1 for event in usable if event.winner_index == 0) / len(usable)
                    if usable else None)
        records = [
            (event.sport, event.event_date, "tail", float(event.draw_probability),
             1.0 if event.winner_index == 0 else 0.0)
            for event in usable
        ]
        bootstrap = _cluster_bootstrap_surplus(
            records, ["tail"], seed=BOOTSTRAP_SEED + seed_offset,
            scheme_names=("calendar_month",))
        interval = (bootstrap.get("schemes", {}).get("calendar_month", {})
                    .get("buckets", {}).get("tail", {}))
        dates = {event.event_date for event in usable}
        surplus = (observed - predicted
                   if observed is not None and predicted is not None else None)
        return {
            "n": len(usable),
            "mean_predicted_probability": predicted,
            "observed_hit_rate": observed,
            "absolute_surplus": surplus,
            "relative_observed_divided_by_predicted": (
                observed / predicted
                if observed is not None and predicted not in (None, 0) else None),
            "calendar_month_95_lo": interval.get("bootstrap_95_lo"),
            "calendar_month_95_hi": interval.get("bootstrap_95_hi"),
            "sign": ("POSITIVE" if surplus is not None and surplus > 0 else
                     "NEGATIVE" if surplus is not None and surplus < 0 else "ZERO_OR_EMPTY"),
            "active_days": len(dates),
            "candidate_rows_per_active_day": (
                len(usable) / len(dates) if dates else None),
            "indicative_only_n_lt_500": len(usable) < 500,
        }

    # Fixed before execution: every calendar quarter from 2024-Q1 through
    # 2026-Q3, including empty folds rather than silently selecting active ones.
    folds = []
    fold_index = 0
    for year in (2024, 2025, 2026):
        for quarter in (1, 2, 3, 4):
            if year == 2026 and quarter == 4:
                continue
            start_month = (quarter - 1) * 3 + 1
            start = dt.date(year, start_month, 1)
            if quarter == 4:
                end = dt.date(year + 1, 1, 1)
            else:
                end = dt.date(year, start_month + 3, 1)
            group = [event for event in rows
                     if start.isoformat() <= event.event_date < end.isoformat()]
            fold = tail_summary(group, 4000 + fold_index)
            fold.update({
                "fold": f"{year}-Q{quarter}",
                "start_inclusive": start.isoformat(),
                "end_exclusive": end.isoformat(),
            })
            folds.append(fold)
            fold_index += 1

    nonempty = [fold for fold in folds if fold["n"] > 0]
    positive = sum(fold["sign"] == "POSITIVE" for fold in nonempty)
    significant_positive = sum(
        fold.get("calendar_month_95_lo") is not None
        and fold["calendar_month_95_lo"] > 0
        for fold in nonempty)
    final_two = nonempty[-2:]
    persistence = (
        len(nonempty) > 0
        and positive / len(nonempty) >= 0.75
        and significant_positive / len(nonempty) >= 0.50
        and len(final_two) == 2
        and all(fold["sign"] == "POSITIVE" for fold in final_two)
    )

    by_league: dict[str, list[SettledEvent]] = defaultdict(list)
    for event in rows:
        by_league[event.league or "UNKNOWN_LEAGUE"].append(event)
    league_rows = []
    all_tail_n = tail_summary(rows, 4200)["n"]
    for index, (league, group) in enumerate(sorted(by_league.items())):
        summary = tail_summary(group, 4300 + index)
        if summary["n"] == 0:
            continue
        league_rows.append({
            "league": league,
            "share_of_handball_tail": summary["n"] / all_tail_n if all_tail_n else None,
            **summary,
        })
    league_rows.sort(key=lambda item: (-item["n"], item["league"]))

    curve_bands = (
        ("<0.05", 0.005, 0.05), ("0.05-0.10", 0.05, 0.10),
        ("0.10-0.15", 0.10, 0.15), ("0.15-0.20", 0.15, 0.20),
        ("0.20-0.25", 0.20, 0.25), ("0.25-0.30", 0.25, 0.30),
        ("0.30-0.35", 0.30, 0.35), ("0.35+", 0.35, 1.0000001),
    )
    curve = {}
    for index, (label, lo, hi) in enumerate(curve_bands):
        group = [event for event in rows
                 if isinstance(event.draw_probability, (int, float))
                 and not isinstance(event.draw_probability, bool)
                 and lo <= float(event.draw_probability) < hi]
        # Reuse the same estimator by temporarily selecting this band's rows;
        # unlike tail_summary, calculate directly because p may exceed 0.05.
        predicted = (sum(float(event.draw_probability) for event in group) / len(group)
                     if group else None)
        observed = (sum(event.winner_index == 0 for event in group) / len(group)
                    if group else None)
        records = [(event.sport, event.event_date, label,
                    float(event.draw_probability),
                    1.0 if event.winner_index == 0 else 0.0) for event in group]
        boot = _cluster_bootstrap_surplus(
            records, [label], seed=BOOTSTRAP_SEED + 4500 + index,
            scheme_names=("calendar_month",))
        interval = (boot.get("schemes", {}).get("calendar_month", {})
                    .get("buckets", {}).get(label, {}))
        curve[label] = {
            "n": len(group), "mean_predicted_probability": predicted,
            "observed_hit_rate": observed,
            "absolute_surplus": (observed - predicted
                                 if observed is not None and predicted is not None else None),
            "relative_observed_divided_by_predicted": (
                observed / predicted
                if observed is not None and predicted not in (None, 0) else None),
            "calendar_month_95_lo": interval.get("bootstrap_95_lo"),
            "calendar_month_95_hi": interval.get("bootstrap_95_hi"),
            "indicative_only_n_lt_500": len(group) < 500,
        }

    return {
        "scope": "handball genuine draw forecasts; 0.005 <= p(draw) <0.05 for tail tests",
        "fold_contract": "fixed calendar quarters 2024-Q1 through 2026-Q3, including empty folds",
        "predeclared_persistence_rule": (
            "At least 75% of nonempty folds positive, at least 50% with month-block "
            "lower bound >0, and the final two nonempty folds both positive."
        ),
        "walk_forward_folds": folds,
        "persistence_summary": {
            "total_fixed_folds": len(folds),
            "nonempty_folds": len(nonempty),
            "empty_folds": [fold["fold"] for fold in folds if fold["n"] == 0],
            "positive_sign_folds": positive,
            "month_interval_excludes_zero_positive_folds": significant_positive,
            "final_two_nonempty_positive": (
                len(final_two) == 2 and all(fold["sign"] == "POSITIVE"
                                            for fold in final_two)),
            "persistence_rule_met": persistence,
        },
        "league_concentration": {
            "warning": "League slices are multiplicity-exposed; n<500 is indicative only.",
            "leagues": league_rows,
        },
        "full_draw_calibration_curve": curve,
    }


def _draw_model_discrimination(events: list[SettledEvent]) -> dict[str, Any]:
    """Full-curve calibration and rank discrimination by genuine draw board."""
    curve_bands = (
        ("<0.05", 0.005, 0.05), ("0.05-0.10", 0.05, 0.10),
        ("0.10-0.15", 0.10, 0.15), ("0.15-0.20", 0.15, 0.20),
        ("0.20-0.25", 0.20, 0.25), ("0.25-0.30", 0.25, 0.30),
        ("0.30-0.35", 0.30, 0.35), ("0.35+", 0.35, 1.0000001),
    )

    def average_ranks(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda index: values[index])
        ranks = [0.0] * len(values)
        start = 0
        while start < len(order):
            end = start + 1
            while end < len(order) and values[order[end]] == values[order[start]]:
                end += 1
            rank = ((start + 1) + end) / 2
            for position in range(start, end):
                ranks[order[position]] = rank
            start = end
        return ranks

    def correlation_from_totals(n: float, sx: float, sy: float, sxx: float,
                                syy: float, sxy: float) -> float | None:
        if n <= 1:
            return None
        covariance = sxy - sx * sy / n
        var_x = sxx - sx * sx / n
        var_y = syy - sy * sy / n
        if var_x <= 0 or var_y <= 0:
            return None
        return covariance / math.sqrt(var_x * var_y)

    per_sport = {}
    sports = sorted({event.sport for event in events
                     if SPORTS[event.sport].draw_settles
                     and SPORTS[event.sport].draw_possible})
    for sport_index, sport in enumerate(sports):
        rows = [
            event for event in events
            if event.sport == sport
            and isinstance(event.draw_probability, (int, float))
            and not isinstance(event.draw_probability, bool)
            and float(event.draw_probability) >= GENUINE_DRAW_FORECAST_FLOOR
        ]
        curve = {}
        observed_rates = []
        for band_index, (label, lo, hi) in enumerate(curve_bands):
            group = [event for event in rows
                     if lo <= float(event.draw_probability) < hi]
            predicted = (sum(float(event.draw_probability) for event in group) / len(group)
                         if group else None)
            observed = (sum(event.winner_index == 0 for event in group) / len(group)
                        if group else None)
            records = [(sport, event.event_date, label,
                        float(event.draw_probability),
                        1.0 if event.winner_index == 0 else 0.0) for event in group]
            boot = _cluster_bootstrap_surplus(
                records, [label], seed=BOOTSTRAP_SEED + 5000
                + sport_index * 20 + band_index,
                scheme_names=("calendar_month",))
            interval = (boot.get("schemes", {}).get("calendar_month", {})
                        .get("buckets", {}).get(label, {}))
            if observed is not None:
                observed_rates.append(observed)
            curve[label] = {
                "n": len(group), "mean_predicted_probability": predicted,
                "observed_hit_rate": observed,
                "absolute_surplus": (observed - predicted
                                     if observed is not None and predicted is not None else None),
                "relative_observed_divided_by_predicted": (
                    observed / predicted
                    if observed is not None and predicted not in (None, 0) else None),
                "calendar_month_95_lo": interval.get("bootstrap_95_lo"),
                "calendar_month_95_hi": interval.get("bootstrap_95_hi"),
                "indicative_only_n_lt_500": len(group) < 500,
            }

        probabilities = [float(event.draw_probability) for event in rows]
        outcomes = [1.0 if event.winner_index == 0 else 0.0 for event in rows]
        ranks = average_ranks(probabilities)
        blocks: dict[str, list[float]] = defaultdict(
            lambda: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        for event, rank, outcome in zip(rows, ranks, outcomes):
            total = blocks[event.event_date[:7]]
            total[0] += 1
            total[1] += rank
            total[2] += outcome
            total[3] += rank * rank
            total[4] += outcome * outcome
            total[5] += rank * outcome
        overall = [sum(block[index] for block in blocks.values()) for index in range(6)]
        point = correlation_from_totals(*overall)
        keys = sorted(blocks)
        rng = random.Random(BOOTSTRAP_SEED + 5500 + sport_index)
        draws = []
        if keys:
            for _ in range(BOOTSTRAP_REPLICATES):
                totals = [0.0] * 6
                for _block in keys:
                    sampled = blocks[keys[rng.randrange(len(keys))]]
                    for index, value in enumerate(sampled):
                        totals[index] += value
                value = correlation_from_totals(*totals)
                if value is not None:
                    draws.append(value)
        per_sport[sport] = {
            "n": len(rows),
            "full_draw_calibration_curve": curve,
            "observed_rate_range_across_nonempty_bands": {
                "minimum": min(observed_rates) if observed_rates else None,
                "maximum": max(observed_rates) if observed_rates else None,
                "range": (max(observed_rates) - min(observed_rates)
                          if observed_rates else None),
            },
            "spearman_rank_correlation_predicted_draw_vs_realised_draw": point,
            "spearman_calendar_month_bootstrap_95_lo": _percentile(draws, 0.025),
            "spearman_calendar_month_bootstrap_95_hi": _percentile(draws, 0.975),
            "spearman_valid_replicates": len(draws),
            "spearman_method": (
                "Pearson correlation of global average ranks of predicted probability "
                "with binary realised-draw outcome (binary ranks are an affine transform); "
                "month bootstrap resamples month blocks and reweights those fixed ranks."
            ),
            "interpretation": (
                "Spearman correlation measures discrimination/ranking, not calibration "
                "or profit; zero means predicted draw probability does not rank realised "
                "draws better than chance in a monotonic sense."
            ),
        }
    return {
        "scope": (
            "ledger-valid settled rows with genuine published draw forecast >=0.005; "
            "MMA excluded because its board is two-outcome"
        ),
        "per_sport": per_sport,
    }


def _eligible_signal_analysis(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Calibration over every R2-eligible underdog, not only daily R1."""
    def frequency(group: list[dict[str, Any]]) -> dict[str, Any]:
        dates = sorted({row["event_date"] for row in group})
        if not dates:
            return {
                "candidate_rows": 0, "active_days": 0, "calendar_span_days": 0,
                "mean_candidates_per_active_day": None,
                "mean_candidates_per_calendar_day": None,
            }
        span = (dt.date.fromisoformat(dates[-1])
                - dt.date.fromisoformat(dates[0])).days + 1
        return {
            "candidate_rows": len(group),
            "active_days": len(dates),
            "calendar_span_days": span,
            "first_date": dates[0], "last_date": dates[-1],
            "mean_candidates_per_active_day": len(group) / len(dates),
            "mean_candidates_per_calendar_day": len(group) / span,
        }

    def summarize(group: list[dict[str, Any]], seed_offset: int) -> dict[str, Any]:
        underdog = _surplus_with_shifted_wilson(
            _calibration_block(group, side="underdog"))
        favourite = _surplus_with_shifted_wilson(
            _calibration_block(group, side="favorite"))
        underdog_records = []
        favourite_records = []
        differential_records = []
        for row in group:
            dog_p = row.get("underdog_probability")
            fav_p = row.get("favorite_probability")
            dog_grade = row.get("grade")
            fav_grade = _grade_pick(row, row.get("favorite_index"))
            if (not isinstance(dog_p, (int, float)) or isinstance(dog_p, bool)
                    or not isinstance(fav_p, (int, float)) or isinstance(fav_p, bool)
                    or dog_grade not in ("SUCCESS", "FAILURE")
                    or fav_grade not in ("SUCCESS", "FAILURE")):
                continue
            dog_observed = 1 if dog_grade == "SUCCESS" else 0
            fav_observed = 1 if fav_grade == "SUCCESS" else 0
            prefix = (row["sport"], row["event_date"], "all")
            underdog_records.append((*prefix, float(dog_p), dog_observed))
            favourite_records.append((*prefix, float(fav_p), fav_observed))
            differential_records.append((
                *prefix, float(dog_p) - float(fav_p),
                dog_observed - fav_observed,
            ))
        dog_surplus = underdog.get("observed_minus_predicted")
        fav_surplus = favourite.get("observed_minus_predicted")
        differential = {
            "n": len(differential_records),
            "observed_minus_predicted": (
                dog_surplus - fav_surplus
                if dog_surplus is not None and fav_surplus is not None else None
            ),
            "definition": "underdog surplus minus favourite surplus on the same rows",
            "draw_artifact_warning": (
                "For draw-capable sports, positive underdog surplus is not an edge "
                "when this differential is non-positive or its cluster interval "
                "includes zero; both win sides can rise when draws are over-predicted."
            ),
        }
        return {
            # Keep the established key for consumers, but place its mandatory
            # same-row control and paired differential beside it.
            "calibration": underdog,
            "favourite_control": favourite,
            "differential": differential,
            "calendar_day_cluster_bootstrap": _cluster_bootstrap_surplus(
                underdog_records, ["all"], seed=BOOTSTRAP_SEED + seed_offset,
                scheme_names=("calendar_day_PRIMARY",)),
            "favourite_calendar_day_cluster_bootstrap": _cluster_bootstrap_surplus(
                favourite_records, ["all"], seed=BOOTSTRAP_SEED + seed_offset,
                scheme_names=("calendar_day_PRIMARY",)),
            "differential_calendar_day_cluster_bootstrap": _cluster_bootstrap_surplus(
                differential_records, ["all"], seed=BOOTSTRAP_SEED + seed_offset,
                scheme_names=("calendar_day_PRIMARY",)),
            "candidate_frequency": frequency(group),
        }

    sports = sorted({row["sport"] for row in rows})
    per_sport = {}
    for index, sport in enumerate(sports):
        sport_rows = [row for row in rows if row["sport"] == sport]
        development = [row for row in sport_rows
                       if row["event_date"] <= HOLDOUT_CUTOFF]
        holdout = [row for row in sport_rows
                   if row["event_date"] > HOLDOUT_CUTOFF]
        per_sport[sport] = {
            "all": summarize(sport_rows, index * 10),
            "development_through_cutoff": summarize(development, index * 10 + 1),
            "holdout_after_cutoff": summarize(holdout, index * 10 + 2),
        }
    return {
        "scope": "every R2-eligible underdog row before daily R1 rank truncation",
        "cutoff": HOLDOUT_CUTOFF,
        "primary_uncertainty_block": "calendar day across all sports",
        "block_reason": (
            "Forebet's model/regime is shared across sports, so physically "
            "unrelated sports on one date may still have correlated errors. "
            "Calendar-day blocks preserve that dependence; within one sport "
            "they coincide with sport-day blocks."
        ),
        "multiplicity_warning": (
            "Per-sport signals remain multiple comparisons. Handball's prior "
            "development/holdout sign reversal is the standing counterexample."
        ),
        "overall": {
            "all": summarize(rows, 1000),
            "development_through_cutoff": summarize(
                [row for row in rows if row["event_date"] <= HOLDOUT_CUTOFF], 1001),
            "holdout_after_cutoff": summarize(
                [row for row in rows if row["event_date"] > HOLDOUT_CUTOFF], 1002),
        },
        "per_sport": per_sport,
    }


def _negative_sport_gate_variant(
    signal: dict[str, Any], r1_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Analysis-only R1 variant excluding development-negative sports."""
    excluded = []
    selection_receipt = {}
    for sport, periods in (signal.get("per_sport") or {}).items():
        development = periods.get("development_through_cutoff") or {}
        bucket = (((development.get("calendar_day_cluster_bootstrap") or {})
                   .get("schemes") or {}).get("calendar_day_PRIMARY", {})
                  .get("buckets", {}).get("all", {}))
        hi = bucket.get("bootstrap_95_hi")
        decision = hi is not None and hi < 0
        selection_receipt[sport] = {
            "development_underdog_cluster_95_lo": bucket.get("bootstrap_95_lo"),
            "development_underdog_cluster_95_hi": hi,
            "excluded": decision,
        }
        if decision:
            excluded.append(sport)

    def period(group: list[dict[str, Any]], seed_offset: int) -> dict[str, Any]:
        kept = [row for row in group if row["sport"] not in excluded]

        def outcome_space(draw_capable: bool, offset: int) -> dict[str, Any]:
            frozen_rows = [row for row in group
                           if SPORTS[row["sport"]].draw_settles == draw_capable]
            variant_rows = [row for row in kept
                            if SPORTS[row["sport"]].draw_settles == draw_capable]
            if draw_capable:
                frozen_metric = _draw_space_split(frozen_rows)[
                    "draw_capable_sports"]["pooled"]
                variant_metric = _draw_space_split(variant_rows)[
                    "draw_capable_sports"]["pooled"]
                records = []
                for row in variant_rows:
                    dog_p = row.get("underdog_probability")
                    fav_p = row.get("favorite_probability")
                    dog_grade = row.get("grade")
                    fav_grade = _grade_pick(row, row.get("favorite_index"))
                    if (not isinstance(dog_p, (int, float)) or isinstance(dog_p, bool)
                            or not isinstance(fav_p, (int, float))
                            or isinstance(fav_p, bool)
                            or dog_grade not in ("SUCCESS", "FAILURE")
                            or fav_grade not in ("SUCCESS", "FAILURE")):
                        continue
                    records.append((
                        row["sport"], row["event_date"], "all",
                        float(dog_p) - float(fav_p),
                        float((1 if dog_grade == "SUCCESS" else 0)
                              - (1 if fav_grade == "SUCCESS" else 0)),
                    ))
                metric_name = "underdog_minus_favourite_differential"
            else:
                frozen_metric = _surplus_with_shifted_wilson(
                    _calibration_block(frozen_rows, side="underdog"))
                variant_metric = _surplus_with_shifted_wilson(
                    _calibration_block(variant_rows, side="underdog"))
                records = [
                    (row["sport"], row["event_date"], "all",
                     float(row["underdog_probability"]),
                     1.0 if row.get("grade") == "SUCCESS" else 0.0)
                    for row in variant_rows
                    if isinstance(row.get("underdog_probability"), (int, float))
                    and not isinstance(row.get("underdog_probability"), bool)
                    and row.get("grade") in ("SUCCESS", "FAILURE")
                ]
                metric_name = "underdog_surplus"
            return {
                "merit_metric": metric_name,
                "frozen_r1": frozen_metric,
                "variant_r1": variant_metric,
                "rows_removed": len(frozen_rows) - len(variant_rows),
                "variant_calendar_day_cluster_bootstrap": _cluster_bootstrap_surplus(
                    records, ["all"], seed=BOOTSTRAP_SEED + seed_offset + offset,
                    scheme_names=("calendar_day_PRIMARY",)),
            }

        return {
            "pooled_raw_surplus_prohibited_due_to_outcome_space_mix_shift": True,
            "two_way": outcome_space(False, 0),
            "draw_capable": outcome_space(True, 1),
            "total_rows_removed": len(group) - len(kept),
        }

    development = [row for row in r1_rows if row["event_date"] <= HOLDOUT_CUTOFF]
    holdout = [row for row in r1_rows if row["event_date"] > HOLDOUT_CUTOFF]
    return {
        "status": "RETIRED_FAILED_DEVELOPMENT_OUTCOME_SPACE_TEST",
        "selection_rule": (
            "Exclude a sport only when its signal-wide development-period "
            "underdog-surplus calendar-day bootstrap upper 95% bound is below zero."
        ),
        "excluded_sports_selected_on_development_only": excluded,
        "selection_receipt": selection_receipt,
        "development_through_cutoff": period(development, 2001),
        "holdout_after_cutoff": period(holdout, 2002),
        "warning": (
            "This hypothesis was prompted by inspected sport effects and remains "
            "multiplicity-exposed. Never compare pooled raw surplus before/after "
            "this gate: excluding three two-way sports and one draw-capable sport "
            "changes the outcome-space mix and mechanically changes draw-artifact "
            "exposure. Two-way merit is underdog surplus; draw-capable merit is "
            "the underdog-minus-favourite differential."
        ),
    }


def r1_backtest(root: Path | str = ".", target_date: str | None = None) -> Path:
    """Replay the frozen R1/R2 rule over the committed historical corpus.

    Offline: reads only ``data/reports/history_<sport>.jsonl.gz`` files
    already on disk (never fetches). Writes
    ``data/reports/r1_backtest_<target_date>.json`` and ``.md`` and
    returns the JSON path. A sport with no ledger in this checkout is
    reported ``available: False`` -- plainly, not zero-filled, exactly the
    convention ``analyze.r1_scorecard`` uses for EVENT_DAY.
    """
    root = Path(root)
    target_date = target_date or today_iso()

    dated_sports = sorted(
        sport for sport, start in HISTORY_STARTS.items()
        if start is not None and not SPORTS[sport].current_only
    )

    per_sport: dict[str, Any] = {}
    all_r1_rows: list[dict[str, Any]] = []
    all_cohort_rows: list[dict[str, Any]] = []
    all_settled_events: list[SettledEvent] = []
    corpus_wide_reconstruction_counts: Counter[str] = Counter()
    historical_by_key: dict[tuple[str, str], SettledEvent] = {}

    for sport in dated_sports:
        # This tool must never fail the CI job it runs in (owner directive):
        # a malformed/corrupt ledger for one sport degrades to an honest
        # per-sport error, not a crash that loses every other sport's report.
        try:
            result = _reconstruct_sport(sport, root)
        except Exception as exc:
            per_sport[sport] = {
                "sport": sport,
                "available": False,
                "note": f"error while reconstructing this sport's ledger: {type(exc).__name__}: {exc}",
            }
            continue
        per_sport[sport] = {
            k: v for k, v in result.items()
            if k not in ("r1_rows", "cohort_rows", "settled_events")
        }
        if result.get("available") and result.get("r1_rows") is not None:
            all_r1_rows.extend(result["r1_rows"])
            all_cohort_rows.extend(result["cohort_rows"])
            all_settled_events.extend(result.get("settled_events", []))
            for label, count in result.get("reconstruction_populations", {}).items():
                corpus_wide_reconstruction_counts[label] += count
            for ev in result.get("settled_events", []):
                historical_by_key[(ev.sport, ev.event_id)] = ev

    try:
        provenance_verdict = _contamination_check(root / "data" / "reports", historical_by_key)
    except Exception as exc:
        provenance_verdict = {
            "error": f"contamination check failed: {type(exc).__name__}: {exc}",
            "note": (
                "the check that tests whether the historical-page ledger "
                "agrees with genuine pre-event captures could not run; "
                "treat every rate below as UNVERIFIED, not as IDENTICAL"
            ),
        }

    sports_with_ledger = sum(1 for s in per_sport.values() if s.get("available"))
    sports_with_rows = sum(1 for s in per_sport.values() if s.get("settled_row_count"))

    # NOTE: this is the provenance distribution of the rows actually
    # RANKED AND GRADED as R1 picks -- not the whole settled corpus (see
    # corpus_wide_reconstruction_counts below for that broader, purely
    # diagnostic figure). These two can legitimately differ if eligibility
    # correlates with provenance.
    populations = sorted({row["reconstruction"] for row in all_r1_rows})
    rows_by_population: dict[str, list[dict]] = defaultdict(list)
    for row in all_r1_rows:
        rows_by_population[row["reconstruction"]].append(row)

    eligible_signal = _eligible_signal_analysis(all_cohort_rows)
    analysis: dict[str, Any] = {
        "generated_at_target_date": target_date,
        "provenance_verdict": provenance_verdict,
        "method": (
            "offline replay of the frozen R1/R2 rule "
            "(underdog.identify_forebet_underdog -> "
            "dataset.build_pre_event_features -> "
            "baseline_analyzer.is_r2_eligible [R2_CONSERVATIVE_FIXED_RULE] "
            "-> baseline_analyzer.r1_sort_key [R1_ALWAYS_RANK_COMPARATOR]) "
            "over every row in data/reports/history_<sport>.jsonl.gz; zero "
            "network calls; grading via shadow_settle.grade_underdog_win, "
            "identical to the live R1 scorecard's contract"
        ),
        "known_limitations": list(KNOWN_LIMITATIONS),
        "corpus_inventory": {
            "sports_checked": len(dated_sports),
            "sports_with_a_ledger_in_this_checkout": sports_with_ledger,
            "sports_with_at_least_one_settled_row": sports_with_rows,
            "per_sport": per_sport,
            "note": (
                "row counts and date ranges above are read directly from "
                "each ledger's manifest before any rate is computed, per "
                "the owner directive to establish what exists first"
            ),
        },
        "corpus_wide_reconstruction_counts": dict(corpus_wide_reconstruction_counts),
        "three_outcome_calibration_map": _three_outcome_calibration(all_settled_events),
        "low_draw_tail_analysis": _low_draw_tail_analysis(all_settled_events),
        "handball_draw_diagnostics": _handball_draw_diagnostics(all_settled_events),
        "draw_model_discrimination": _draw_model_discrimination(all_settled_events),
        "eligible_underdog_signal": eligible_signal,
        "negative_sport_gate_variant": _negative_sport_gate_variant(
            eligible_signal, all_r1_rows),
        "reconstruction_populations_present": populations,
        "populations": {},
    }

    if not all_r1_rows:
        analysis["headline"] = (
            "no sport in this checkout has both a committed ledger and at "
            "least one R2-eligible, ranked R1 candidate -- there is "
            "nothing to score. This is the finding, reported plainly with "
            f"the per-sport row counts above ({sports_with_ledger}/"
            f"{len(dated_sports)} sports have any ledger at all in this "
            "checkout), not a confident-looking empty table."
        )
    else:
        verdicts_seen = {
            check.get("verdict") for check in provenance_verdict.values()
            if isinstance(check, dict) and "verdict" in check
        }
        # Most conservative wins: any track showing a difference outweighs
        # any other track that happened to come back clean or untested.
        if "DIFFERING" in verdicts_seen:
            bias_clause = (
                "the provenance check above found the historical-page ledger "
                "DIFFERS from genuine pre-event captures on at least one "
                "matched pair, so EVERY rate below is an upper bound on the "
                "live rule's edge, not a measurement of it -- see VERDICT FIRST"
            )
        elif "IDENTICAL" in verdicts_seen:
            bias_clause = (
                "the provenance check above found every matched pair identical "
                "to its pre-event capture, so these rates are treated as a real "
                "measurement, not just an upper bound -- see VERDICT FIRST for n"
            )
        else:
            bias_clause = (
                "the provenance check above could not reach a verdict "
                "(insufficient matched pairs or an error) -- treat every rate "
                "below as UNVERIFIED, not as a clean measurement, until it can"
            )
        analysis["headline"] = (
            f"{len(populations)} reconstruction population(s) present: "
            f"{', '.join(populations)}. Every population found in this "
            f"corpus today is HISTORICAL_PAGE (post-hoc); {bias_clause}."
        )
        for population in populations:
            pop_rows = rows_by_population.get(population, [])
            analysis["populations"][population] = _score_rows(pop_rows)
        if len(populations) > 1:
            analysis["cross_population_note"] = (
                "more than one reconstruction population exists in this "
                "corpus -- their rates are reported separately above and "
                "MUST NOT be pooled; a large disagreement between them "
                "would itself be the most interesting number in this "
                "report (see module docstring)."
            )

    report_dir = root / "data" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / f"r1_backtest_{target_date}.json"
    json_path.write_text(json.dumps(analysis, indent=2, sort_keys=True))
    md_path = report_dir / f"r1_backtest_{target_date}.md"
    md_path.write_text(_render_r1_backtest_markdown(analysis))
    return json_path


def _render_calibration_line(label: str, block: dict[str, Any]) -> str:
    if not block.get("n"):
        return f"| {label} | n=0 | - | - | - | - |"
    return (
        f"| {label} | {block['mean_predicted_probability']:.2%} | "
        f"{block['successes']}/{block['n']} ({block['hit_rate']:.2%}) | "
        f"{block['wilson_95_lo']:.2%}-{block['wilson_95_hi']:.2%} | "
        f"{block['observed_minus_predicted']:+.2%} | {block['n']} |"
    )


def _render_rate_line(label: str, block: dict[str, Any]) -> str:
    n = block["n"]
    if n == 0:
        return f"| {label} | n=0 | - | - |"
    ci = (
        f"{block['wilson_95_lo']:.1%}-{block['wilson_95_hi']:.1%}"
        if block["wilson_95_lo"] is not None else "-"
    )
    flag = "" if block["significant_n"] else " ⚠️ n<30"
    return (
        f"| {label} | {block['successes']}/{n} "
        f"({block['hit_rate']:.1%}){flag} | {ci} | {n} |"
    )


def _render_provenance_verdict_markdown(provenance_verdict: dict[str, Any]) -> list[str]:
    lines = [
        "## VERDICT FIRST: is the historical-page corpus contaminated?",
        "",
        "Every row below this section is read from Forebet's *historical results "
        "page*, fetched after each match finished. That is only a problem if the "
        "page recomputes its displayed prediction after the fact rather than "
        "showing what it published before kickoff. This is decidable: every "
        "genuinely pre-event R1 pick this repo holds (frozen at capture time) is "
        "joined here against the same match's row in the historical ledger.",
        "",
    ]
    if "error" in provenance_verdict:
        lines += [f"**Could not run: {provenance_verdict['error']}**", provenance_verdict.get("note", ""), ""]
        return lines
    for track, check in provenance_verdict.items():
        lines += [
            f"### {track} track",
            "",
            f"**Verdict: {check['verdict']}** — {check['headline']}",
            "",
            f"- Pre-event picks available: {check['pre_event_picks_available']}",
            f"- Matched to a historical-ledger row by event_id: {check['matched_to_historical_ledger']}",
            f"- Unmatched (no ledger row for that event_id): {check['unmatched_no_ledger_row']}",
            f"- Differing pairs: {check['differing_count']}",
            f"- Underdog identity flipped: {check['underdog_identity_flipped_count']}",
            f"- Max |probability delta| seen: {check['max_absolute_probability_delta_seen']}",
            f"- {check['statistical_power_note']}",
            "",
        ]
    return lines


def _render_r1_backtest_markdown(analysis: dict[str, Any]) -> str:
    lines = [
        f"# R1 rule backtest on the historical corpus — {analysis['generated_at_target_date']}",
        "",
    ]
    lines += _render_provenance_verdict_markdown(analysis.get("provenance_verdict", {}))
    lines += [
        "## The caveat that must not be buried",
        "",
    ]
    for limitation in analysis["known_limitations"]:
        lines.append(f"- {limitation}")
    lines += ["", f"**{analysis['headline']}**", ""]

    inv = analysis["corpus_inventory"]
    lines += [
        "## Corpus inventory (before any rate is computed)",
        "",
        f"{inv['sports_with_a_ledger_in_this_checkout']}/{inv['sports_checked']} sports "
        "have a committed ledger in this checkout; "
        f"{inv['sports_with_at_least_one_settled_row']} have at least one "
        "ledger-valid settled row.",
        "",
        "| Sport | Ledger present | Settled rows | Date range | Sport-days w/ R1 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for sport, info in sorted(inv["per_sport"].items()):
        if not info.get("available"):
            lines.append(f"| {sport} | no | - | - | - |")
            continue
        n_rows = info.get("settled_row_count", 0)
        date_range = info.get("date_range")
        rng = f"{date_range[0]}..{date_range[1]}" if date_range else "-"
        r1_days = info.get("sport_days_with_eligible_r1", "-")
        lines.append(f"| {sport} | yes | {n_rows} | {rng} | {r1_days} |")

    outcome_map = analysis.get("three_outcome_calibration_map") or {}
    if outcome_map:
        lines += [
            "",
            "## Three-outcome calibration map (draw-capable sports, whole corpus)",
            "",
            outcome_map.get("scope", ""),
            "",
            outcome_map.get("warning", ""),
            "",
            "| Outcome/bucket | Mean predicted | Observed | Observed 95% CI | Observed - predicted | n | n<500 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
        for outcome in ("home", "away", "draw"):
            for label, block in (outcome_map.get("pooled", {}).get(outcome, {})
                                 .get("buckets", {})).items():
                if not block.get("n"):
                    continue
                lines.append(
                    f"| {outcome}/{label} | {block['mean_predicted_probability']:.2%} | "
                    f"{block['hit_rate']:.2%} | {block['wilson_95_lo']:.2%}-"
                    f"{block['wilson_95_hi']:.2%} | "
                    f"{block['observed_minus_predicted']:+.2%} | {block['n']} | "
                    f"{'yes' if block['indicative_only_n_lt_500'] else 'no'} |"
                )
        lines += ["", "Per-sport maps are retained in the JSON report.", ""]
        bootstrap = outcome_map.get("draw_surplus_cluster_bootstrap") or {}
        lines += [
            "### Draw-surplus block-bootstrap sensitivity",
            "",
            bootstrap.get("primary_block_reason", ""),
            "",
            "| Block | Bucket | Surplus bootstrap 95% CI | Blocks |",
            "| --- | --- | ---: | ---: |",
        ]
        for scheme, scheme_result in (bootstrap.get("schemes") or {}).items():
            for label, block in scheme_result.get("buckets", {}).items():
                lo = block.get("bootstrap_95_lo")
                hi = block.get("bootstrap_95_hi")
                ci = f"{lo:+.2%}..{hi:+.2%}" if lo is not None and hi is not None else "-"
                lines.append(
                    f"| {scheme} | {label} | {ci} | {scheme_result.get('blocks')} |"
                )

    tail = analysis.get("low_draw_tail_analysis") or {}
    if tail:
        lines += [
            "", "## Low-draw tail decomposition", "", tail.get("scope", ""), "",
            "Predeclared interpretation: "
            + json.dumps(tail.get("predeclared_shape_interpretation", {}),
                         sort_keys=True),
            "",
            "| Scope | Period | Bucket | n | Predicted | Observed | Absolute surplus | Observed/predicted | Calendar-day 95% | Month 95% | Rows/active day |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        tail_scopes = [("pooled", tail.get("pooled") or {})]
        tail_scopes.extend((sport, periods)
                           for sport, periods in (tail.get("per_sport") or {}).items())
        for scope, periods in tail_scopes:
            for period_name in ("development_through_cutoff", "holdout_after_cutoff"):
                period = periods.get(period_name) or {}
                schemes = (period.get("cluster_bootstrap") or {}).get("schemes") or {}
                for label in tail.get("bucket_contract", []):
                    bucket = (period.get("buckets") or {}).get(label) or {}
                    day = (schemes.get("calendar_day_PRIMARY", {}).get("buckets", {})
                           .get(label, {}))
                    month = (schemes.get("calendar_month", {}).get("buckets", {})
                             .get(label, {}))
                    def interval(block: dict) -> str:
                        lo = block.get("bootstrap_95_lo")
                        hi = block.get("bootstrap_95_hi")
                        return (f"{lo:+.2%}..{hi:+.2%}"
                                if lo is not None and hi is not None else "-")
                    def percent(value: float | None) -> str:
                        return f"{value:.2%}" if value is not None else "-"
                    relative = bucket.get(
                        "relative_surplus_observed_divided_by_predicted")
                    frequency = bucket.get("candidate_rows_per_active_day")
                    relative_text = f"{relative:.3f}" if relative is not None else "-"
                    frequency_text = f"{frequency:.3f}" if frequency is not None else "-"
                    lines.append(
                        f"| {scope} | {period_name} | {label} | {bucket.get('n', 0)} | "
                        f"{percent(bucket.get('mean_predicted_probability'))} | "
                        f"{percent(bucket.get('observed_hit_rate'))} | "
                        f"{percent(bucket.get('absolute_surplus'))} | "
                        f"{relative_text} | {interval(day)} | {interval(month)} | "
                        f"{frequency_text} |"
                    )

    handball = analysis.get("handball_draw_diagnostics") or {}
    if handball:
        lines += [
            "", "## Handball low-draw walk-forward", "",
            handball.get("predeclared_persistence_rule", ""), "",
            "Persistence summary: " + json.dumps(
                handball.get("persistence_summary", {}), sort_keys=True),
            "",
            "| Fold | n | Predicted | Observed | Surplus | Relative | Month 95% | Sign |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
        for fold in handball.get("walk_forward_folds", []):
            lo = fold.get("calendar_month_95_lo")
            hi = fold.get("calendar_month_95_hi")
            interval = (f"{lo:+.2%}..{hi:+.2%}"
                        if lo is not None and hi is not None else "-")
            relative = fold.get("relative_observed_divided_by_predicted")
            lines.append(
                f"| {fold.get('fold')} | {fold.get('n')} | "
                f"{fold.get('mean_predicted_probability') or 0:.2%} | "
                f"{fold.get('observed_hit_rate') or 0:.2%} | "
                f"{fold.get('absolute_surplus') or 0:+.2%} | "
                f"{relative:.3f} | {interval} | {fold.get('sign')} |"
                if relative is not None else
                f"| {fold.get('fold')} | 0 | - | - | - | - | - | EMPTY |"
            )
        lines += [
            "", "### Handball league concentration", "",
            "| League | n | Share | Predicted | Observed | Relative | Month 95% |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for league in (handball.get("league_concentration") or {}).get("leagues", []):
            lo = league.get("calendar_month_95_lo")
            hi = league.get("calendar_month_95_hi")
            relative = league.get("relative_observed_divided_by_predicted")
            lines.append(
                f"| {league.get('league')} | {league.get('n')} | "
                f"{league.get('share_of_handball_tail'):.2%} | "
                f"{league.get('mean_predicted_probability'):.2%} | "
                f"{league.get('observed_hit_rate'):.2%} | {relative:.3f} | "
                f"{lo:+.2%}..{hi:+.2%} |"
            )
        lines += [
            "", "### Full handball draw calibration curve", "",
            "| Bucket | n | Predicted | Observed | Surplus | Relative | Month 95% |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for label, bucket in (handball.get("full_draw_calibration_curve") or {}).items():
            predicted = bucket.get("mean_predicted_probability")
            observed = bucket.get("observed_hit_rate")
            surplus = bucket.get("absolute_surplus")
            relative = bucket.get("relative_observed_divided_by_predicted")
            lo = bucket.get("calendar_month_95_lo")
            hi = bucket.get("calendar_month_95_hi")
            if predicted is None:
                lines.append(f"| {label} | 0 | - | - | - | - | - |")
            else:
                lines.append(
                    f"| {label} | {bucket.get('n')} | {predicted:.2%} | "
                    f"{observed:.2%} | {surplus:+.2%} | {relative:.3f} | "
                    f"{lo:+.2%}..{hi:+.2%} |"
                )

    discrimination = analysis.get("draw_model_discrimination") or {}
    if discrimination:
        lines += [
            "", "## Draw-model discrimination by sport", "",
            discrimination.get("scope", ""), "",
            "| Sport | n | Observed-rate min | max | range | Spearman | Month-bootstrap 95% |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for sport, result in (discrimination.get("per_sport") or {}).items():
            observed_range = result.get(
                "observed_rate_range_across_nonempty_bands") or {}
            correlation = result.get(
                "spearman_rank_correlation_predicted_draw_vs_realised_draw")
            lo = result.get("spearman_calendar_month_bootstrap_95_lo")
            hi = result.get("spearman_calendar_month_bootstrap_95_hi")
            def pct(value: float | None) -> str:
                return f"{value:.2%}" if value is not None else "-"
            corr_text = f"{correlation:.4f}" if correlation is not None else "-"
            ci_text = (f"{lo:.4f}..{hi:.4f}"
                       if lo is not None and hi is not None else "-")
            lines.append(
                f"| {sport} | {result.get('n')} | "
                f"{pct(observed_range.get('minimum'))} | "
                f"{pct(observed_range.get('maximum'))} | "
                f"{pct(observed_range.get('range'))} | {corr_text} | {ci_text} |"
            )
            lines += [
                "", f"### {sport} full draw curve", "",
                "| Bucket | n | Predicted | Observed | Surplus | Relative | Month 95% |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
            for label, bucket in result.get("full_draw_calibration_curve", {}).items():
                predicted = bucket.get("mean_predicted_probability")
                observed = bucket.get("observed_hit_rate")
                if predicted is None:
                    lines.append(f"| {label} | 0 | - | - | - | - | - |")
                    continue
                lines.append(
                    f"| {label} | {bucket.get('n')} | {predicted:.2%} | "
                    f"{observed:.2%} | {bucket.get('absolute_surplus'):+.2%} | "
                    f"{bucket.get('relative_observed_divided_by_predicted'):.3f} | "
                    f"{bucket.get('calendar_month_95_lo'):+.2%}.."
                    f"{bucket.get('calendar_month_95_hi'):+.2%} |"
                )

    signal = analysis.get("eligible_underdog_signal") or {}
    if signal:
        lines += [
            "",
            "## Signal-wide eligible-underdog calibration",
            "",
            signal.get("scope", ""),
            "",
            signal.get("block_reason", ""),
            "",
            signal.get("multiplicity_warning", ""),
            "",
            "| Sport | Period | Underdog surplus [cluster 95% CI] | Favourite control [cluster 95% CI] | Dog-favourite differential [cluster 95% CI] | n | Candidates/calendar day |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for sport, periods in (signal.get("per_sport") or {}).items():
            for period in ("development_through_cutoff", "holdout_after_cutoff"):
                result = periods[period]
                def metric(calibration_key: str, bootstrap_key: str) -> str:
                    calibration = result[calibration_key]
                    bucket = (result[bootstrap_key].get("schemes", {})
                              .get("calendar_day_PRIMARY", {}).get("buckets", {})
                              .get("all", {}))
                    surplus = calibration.get("observed_minus_predicted")
                    lo = bucket.get("bootstrap_95_lo")
                    hi = bucket.get("bootstrap_95_hi")
                    if surplus is None:
                        return "-"
                    interval = (f"{lo:+.2%}..{hi:+.2%}"
                                if lo is not None and hi is not None else "-")
                    return f"{surplus:+.2%} [{interval}]"

                dog_text = metric("calibration", "calendar_day_cluster_bootstrap")
                fav_text = metric(
                    "favourite_control", "favourite_calendar_day_cluster_bootstrap")
                differential_text = metric(
                    "differential", "differential_calendar_day_cluster_bootstrap")
                freq = result["candidate_frequency"].get("mean_candidates_per_calendar_day")
                freq_text = f"{freq:.3f}" if freq is not None else "-"
                lines.append(
                    f"| {sport} | {period} | {dog_text} | {fav_text} | "
                    f"{differential_text} | {result['calibration'].get('n', 0)} | "
                    f"{freq_text} |"
                )

    for population, scorecard in analysis.get("populations", {}).items():
        lines += [
            "",
            f"## Population: {population}",
            "",
        ]
        calibration = scorecard["calibration"]
        lines += [
            "### PRIMARY MERIT METRIC: calibration against Forebet probability",
            "",
            calibration["interpretation"],
            "",
            "| Slice | Mean predicted | Observed | Observed 95% CI | Observed - predicted | n |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
            _render_calibration_line(
                "R1 underdog — overall", calibration["overall"]["r1_underdog"]),
            _render_calibration_line(
                "Favourite control — same rows",
                calibration["overall"]["favourite_control"]),
            "",
            "#### By R1 underdog-probability band",
            "",
            "| Slice | Mean predicted | Observed | Observed 95% CI | Observed - predicted | n |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for label, _lo, _hi in PROBABILITY_BANDS:
            pair = calibration["by_underdog_probability_band"][label]
            lines.append(_render_calibration_line(
                f"{label} — R1 underdog", pair["r1_underdog"]))
            lines.append(_render_calibration_line(
                f"{label} — favourite control", pair["favourite_control"]))
        lines += [
            "",
            "#### By sport",
            "",
            "| Slice | Mean predicted | Observed | Observed 95% CI | Observed - predicted | n |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for sport, pair in calibration["by_sport"].items():
            lines.append(_render_calibration_line(
                f"{sport} — R1 underdog", pair["r1_underdog"]))
            lines.append(_render_calibration_line(
                f"{sport} — favourite control", pair["favourite_control"]))

        split = scorecard["draw_space_split"]
        coverage = scorecard["coverage"]
        two_way = split["two_way_sports"]
        draw_capable = split["draw_capable_sports"]
        lines += [
            "",
            "### Draw-space-separated edge test",
            "",
            f"Distinct sport-days: {coverage['distinct_sport_days']}; distinct calendar days: "
            f"{coverage['distinct_calendar_days']}; mean R1 picks/sport-day: "
            f"{coverage['mean_r1_picks_per_sport_day']:.3f}; mean R1 picks/calendar-day: "
            f"{coverage['mean_r1_picks_per_calendar_day']:.3f}.",
            "",
            "#### Two-way sports (clean pooled surplus)",
            "",
            _render_calibration_line("pooled two-way R1 underdog", two_way["pooled"]),
            "",
            "| Sport | Mean predicted | Observed | Observed 95% CI | Observed - predicted | n |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for sport, block in two_way["per_sport"].items():
            lines.append(_render_calibration_line(sport, block))
        lines += [
            "",
            "#### Draw-capable sports (underdog surplus minus favourite surplus)",
            "",
            "| Sport | Differential surplus | Differential 95% CI | n | n<500 |",
            "| --- | ---: | ---: | ---: | --- |",
        ]
        for sport, block in [("pooled", draw_capable["pooled"]),
                             *draw_capable["per_sport"].items()]:
            lo = block.get("differential_surplus_95_lo")
            hi = block.get("differential_surplus_95_hi")
            ci = f"{lo:+.2%}..{hi:+.2%}" if lo is not None and hi is not None else "-"
            value = block.get("differential_surplus")
            lines.append(
                f"| {sport} | {value:+.2%} | {ci} | {block['n']} | "
                f"{'yes' if block['indicative_only_n_lt_500'] else 'no'} |"
                if value is not None else f"| {sport} | - | - | {block['n']} | yes |"
            )

        holdout = scorecard["temporal_holdout"]
        lines += [
            "",
            f"### Temporal holdout (development <= {holdout['cutoff']}; evaluation after)",
            "",
            holdout["multiplicity_warning"],
            "",
            "| Sport | Space | Development effect (n) | Holdout effect (n) | Holdout n<500 |",
            "| --- | --- | ---: | ---: | --- |",
        ]
        for sport, periods in holdout["per_sport"].items():
            key = ("observed_minus_predicted" if periods["outcome_space"] == "TWO_WAY"
                   else "differential_surplus")
            dev = periods["development_through_cutoff"]
            test = periods["holdout_after_cutoff"]
            dev_effect = dev.get(key)
            test_effect = test.get(key)
            lines.append(
                f"| {sport} | {periods['outcome_space']} | "
                f"{dev_effect:+.2%} ({dev.get('n', 0)}) | "
                f"{test_effect:+.2%} ({test.get('n', 0)}) | "
                f"{'yes' if periods['indicative_only_holdout_n_lt_500'] else 'no'} |"
                if dev_effect is not None and test_effect is not None else
                f"| {sport} | {periods['outcome_space']} | - ({dev.get('n', 0)}) | "
                f"- ({test.get('n', 0)}) | yes |"
            )

        lines += [
            "",
            "### Raw hit rates (descriptive baselines, not the measure of merit)",
            "",
            scorecard["raw_hit_rate_note"],
            "",
        ]
        overall = scorecard["overall"]
        draws_note = (
            f" (of which {overall['settled_draws']} settled as a draw, "
            "counted as a loss)" if overall.get("settled_draws") else ""
        )
        lines.append(
            f"**Overall: {overall['successes']}/{overall['n']} "
            f"({overall['hit_rate']:.1%})" if overall['n'] else "**Overall: n=0"
        )
        if overall["n"]:
            lines[-1] += (
                f" [95% CI {overall['wilson_95_lo']:.1%}-{overall['wilson_95_hi']:.1%}]"
                f"{draws_note}**"
            )
            if not overall["significant_n"]:
                lines[-1] = (
                    lines[-1][:-2]
                    + f" -- n too small (n={overall['n']}, "
                    f"floor={MIN_N_FOR_SIGNIFICANCE})**"
                )
        else:
            lines[-1] += "**"


        lines += ["", "### By sport", "", "| Sport | Hit rate | 95% CI | n |", "| --- | --- | --- | --- |"]
        for sport, block in sorted(scorecard["by_sport"].items()):
            lines.append(_render_rate_line(sport, block))

        lines += ["", "### By underdog-probability band", "", "| Band | Hit rate | 95% CI | n |", "| --- | --- | --- | --- |"]
        for label, _lo, _hi in PROBABILITY_BANDS:
            lines.append(_render_rate_line(label, scorecard["by_underdog_probability_band"][label]))
        lines.append(_render_rate_line(
            UNKNOWN_PROBABILITY_BAND,
            scorecard["by_underdog_probability_band"][UNKNOWN_PROBABILITY_BAND]))

        lines += ["", "### Baselines, same rows", "", "| Bet | Hit rate | 95% CI | n |", "| --- | --- | --- | --- |"]
        b = scorecard["baselines_same_rows"]
        lines.append(_render_rate_line("Our R1 pick", b["our_r1_pick"]))
        lines.append(_render_rate_line("Always favourite", b["always_favourite_same_rows"]))
        lines.append(_render_rate_line("Always underdog (identical by construction)", b["always_underdog_same_rows"]))
        fb = b["forebet_pick_same_rows"]
        missing = fb["rows_missing_forebet_pick"]
        lines.append(_render_rate_line(
            f"Follow Forebet's own pick{f' ({missing} row(s) missing, excluded)' if missing else ''}",
            fb))

    if "cross_population_note" in analysis:
        lines += ["", f"**{analysis['cross_population_note']}**"]

    return "\n".join(lines) + "\n"
