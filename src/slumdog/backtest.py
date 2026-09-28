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
    "kickoff. A hit rate measured on this population is an upper bound on "
    "the live rule's edge, not a measurement of it -- see this module's "
    "docstring. No PRE_EVENT_CAPTURE population exists in this corpus "
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
        "overall": overall,
        "by_sport": by_sport,
        "by_underdog_probability_band": by_band,
        "baselines_same_rows": baselines,
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

    for sport in dated_sports:
        result = _reconstruct_sport(sport, root)
        per_sport[sport] = {
            k: v for k, v in result.items()
            if k not in ("r1_rows", "cohort_rows")
        }
        if result.get("available") and result.get("r1_rows") is not None:
            all_r1_rows.extend(result["r1_rows"])
            all_cohort_rows.extend(result["cohort_rows"])
            for label, count in result.get("reconstruction_populations", {}).items():
                corpus_wide_reconstruction_counts[label] += count

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
        analysis["headline"] = (
            f"{len(populations)} reconstruction population(s) present: "
            f"{', '.join(populations)}. See known_limitations -- every "
            "population found in this corpus today is HISTORICAL_PAGE "
            "(post-hoc), so any rate below is an upper bound on the live "
            "rule's edge, not a measurement of it."
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


def _render_r1_backtest_markdown(analysis: dict[str, Any]) -> str:
    lines = [
        f"# R1 rule backtest on the historical corpus — {analysis['generated_at_target_date']}",
        "",
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
