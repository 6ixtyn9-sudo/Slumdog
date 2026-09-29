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

import json
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
