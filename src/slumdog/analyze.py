"""Post-census analysis: turn raw census + history receipts into research reports.

The depth pipeline produces two artifact families:

- ``depth_sweep_<date>.json`` — per-sport current-board census with
  listing counts, price coverage and detail field presence.
- ``history_<sport>.json`` (+ ``history_<sport>.jsonl.gz``) — rolling,
  resumable per-sport settlement ledgers with a manifest of covered dates.

``analyze_depth`` reads whatever exists (missing artifacts degrade to empty
sections) and writes a dated JSON receipt plus a markdown research report.

This module also builds the **R1 scorecard** (``r1_scorecard``) — the owner's
actual standing ask ("I really need the R1s to be super solid and perform
much better than they do now"), measured offline from already-committed
shadow evidence under ``data/reports/shadow/**``. No network access, no new
capture: everything it reads is already on disk. See ``r1_scorecard``'s
docstring for the full method and the rules it is held to.
"""
from __future__ import annotations

import gzip
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .clock import today_iso
from .shadow_settle import grade_underdog_win
from .sports import SPORTS


def _load_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def latest_census(root: Path) -> dict | None:
    candidates = sorted(root.glob("depth_sweep_*.json"))
    return _load_json(candidates[-1]) if candidates else None


def history_manifests(reports_dir: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted(reports_dir.glob("history_*.json")):
        data = _load_json(path)
        sport = (data or {}).get("sport")
        if sport:
            out[sport] = data
    return out


def _stream_ledger(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def ledger_profile(reports_dir: Path, sport: str) -> dict:
    """Aggregate one sport's rolling ledger by season and league."""
    matches = sorted(reports_dir.glob(f"history_{sport}.jsonl.gz"))
    if not matches:
        return {}
    seasons: dict[str, dict[str, int]] = defaultdict(lambda: {"rows": 0, "priced": 0})
    leagues: Counter[str] = Counter()
    rows = priced = void = 0
    for row in _stream_ledger(matches[-1]):
        rows += 1
        has_price = row.get("odds_1") is not None and row.get("odds_2") is not None
        priced += int(has_price)
        void += int(row.get("disposition") == "VOID")
        season = str(row.get("event_date") or "")[:4] or "unknown"
        seasons[season]["rows"] += 1
        seasons[season]["priced"] += int(has_price)
        leagues[str(row.get("league") or "unknown")] += 1
    return {
        "rows": rows,
        "priced_rows": priced,
        "price_coverage": round(priced / rows, 4) if rows else None,
        "void_rows": void,
        "seasons": {season: seasons[season] for season in sorted(seasons)},
        "top_leagues": leagues.most_common(10),
    }


def _top_missing(census_rows: dict, sport: str) -> list[str]:
    """Detail fields with zero presence in the census for this sport."""
    presence = (census_rows.get(sport) or {}).get("field_presence") or {}
    return sorted(key for key, count in presence.items() if count == 0)


def analyze_depth(root: Path | str = ".", target_date: str | None = None) -> Path:
    root = Path(root)
    target_date = target_date or today_iso()
    reports_dir = root / "data" / "reports"

    census = latest_census(reports_dir)
    census_rows = (census or {}).get("rows", {})
    manifests = history_manifests(reports_dir)

    analysis: dict = {
        "target_date": target_date,
        "census_present": census is not None,
        "census": {},
        "history": {},
    }
    for sport in sorted(set(SPORTS) | set(manifests)):
        row = census_rows.get(sport)
        if row:
            entry = dict(row)
            missing = _top_missing(census_rows, sport)
            if missing:
                entry["zero_presence_detail_fields"] = missing
            analysis["census"][sport] = entry
        manifest = manifests.get(sport)
        profile = ledger_profile(reports_dir, sport)
        if manifest or profile:
            analysis["history"][sport] = {
                "manifest": manifest or {},
                "ledger": profile,
            }

    total_rows = sum(
        (h.get("ledger") or {}).get("rows", 0)
        for h in analysis["history"].values()
    )
    total_priced = sum(
        (h.get("ledger") or {}).get("priced_rows", 0)
        for h in analysis["history"].values()
    )
    analysis["summary"] = {
        "sports_censused": len(analysis["census"]),
        "sports_with_history": len(analysis["history"]),
        "history_rows": total_rows,
        "history_priced_rows": total_priced,
        "history_price_coverage": round(total_priced / total_rows, 4) if total_rows else None,
    }

    json_path = reports_dir / f"analysis_{target_date}.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(analysis, indent=2, sort_keys=True))
    md_path = reports_dir / f"analysis_{target_date}.md"
    md_path.write_text(_render_markdown(analysis))
    return md_path


def _render_markdown(analysis: dict) -> str:
    lines = [
        f"# Slumdog Depth Analysis — {analysis['target_date']}",
        "",
        "## Summary",
        "",
    ]
    summary = analysis.get("summary", {})
    lines.append(
        f"- Sports censused: {summary.get('sports_censused', 0)}  |  "
        f"Sports with history ledgers: {summary.get('sports_with_history', 0)}"
    )
    lines.append(
        f"- History rows: {summary.get('history_rows', 0)}  |  Priced: "
        f"{summary.get('history_priced_rows', 0)}  |  Price coverage: "
        f"{summary.get('history_price_coverage') if summary.get('history_price_coverage') is not None else 'n/a'}"
    )
    lines.extend(["", "## Current census", "", "| Sport | Events | Both prices | Price cov | Details OK/req | Enriched | Missing req |",
                  "|---|---:|---:|---:|---:|---:|---:|"])
    for sport, row in analysis.get("census", {}).items():
        price = row.get("price_coverage")
        details = row.get("details_succeeded")
        requested = row.get("details_requested")
        lines.append(
            f"| {sport} | {row.get('listing_events', 0)} | {row.get('both_prices', 0)} | "
            f"{f'{price:.1%}' if price is not None else 'n/a'} | "
            f"{details}/{requested} | {row.get('details_enriched', 0)} | {row.get('missing_required_fields', 0)} |"
        )
    lines.extend(["", "## History ledgers", "",
                  "| Sport | Range | Dates done/req | Rows | Priced | Price cov | Voids | Failures |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|"])
    for sport, h in analysis.get("history", {}).items():
        m = h.get("manifest") or {}
        g = h.get("ledger") or {}
        dates = m.get("dates_completed")
        requested = m.get("dates_requested")
        cov = g.get("price_coverage")
        lines.append(
            f"| {sport} | {m.get('start', '?')} → {m.get('end', '?')} | "
            f"{dates}/{requested} | {g.get('rows', 0)} | {g.get('priced_rows', 0)} | "
            f"{f'{cov:.1%}' if cov is not None else 'n/a'} | {g.get('void_rows', 0)} | "
            f"{len(m.get('failures') or [])} |"
        )
    for sport, h in analysis.get("history", {}).items():
        g = h.get("ledger") or {}
        seasons = g.get("seasons") or {}
        if seasons:
            lines.extend([
                "",
                f"### {sport} — rows by season",
                "",
                "| Season | Rows | Priced |",
                "|---|---:|---:|",
            ])
            for season, counts in seasons.items():
                lines.append(f"| {season} | {counts['rows']} | {counts['priced']} |")
            leagues = g.get("top_leagues") or []
            if leagues:
                lines.extend(["", "Top leagues: " + ", ".join(
                    f"{name} ({count})" for name, count in leagues[:5]
                )])
    for sport, row in analysis.get("census", {}).items():
        zero = row.get("zero_presence_detail_fields")
        if zero:
            lines.append(
                f"\n### {sport} — detail fields with zero presence in census\n\n"
                + ", ".join(zero)
            )
    lines.append("")
    return "\n".join(lines)


# ============================================================================
# R1 scorecard — offline, committed-evidence-only performance measurement
# ============================================================================
#
# Owner directive, 2026-09-28 (redirect): "the standing ask has been 'I
# really need the R1s to be super solid and perform much better than they
# do now.' Nothing so far has measured how they perform at all." This
# section is that measurement, and it needs no network at all: every input
# is already committed under ``data/reports/shadow/**``
# (``manifest.json``, ``settlement.json``, ``settlement_supplement_*.json``).
#
# "R1" == a grade row whose ``considered_status`` is ``PRIMARY_SHADOW_
# SELECTION`` — the frozen cohort policy's own name for the single rank-1,
# R2-eligible, R1_ALWAYS_RANK_COMPARATOR-chosen pick per sport-day
# (``config/shadow_evaluator.json:cohort_policy``). Verified 1:1 against
# ``rank_within_sport_day == 1`` on every committed row.
#
# Rules this scorecard is held to (owner directive, verbatim intent):
#   - Wilson interval, not a bare percentage — most samples here are small.
#   - Bands by underdog probability: <0.20, 0.20-0.30, 0.30-0.40, 0.40+.
#   - Baselines on the SAME rows: always-favourite, always-underdog (which,
#     for R1 rows, is definitionally the R1 outcome itself — see below),
#     and Forebet's own ``forebet_pick``.
#   - Coverage: how many sport-days produced an R1 at all.
#   - STANDARD and EVENT_DAY tracks reported separately, never pooled.
#   - VOID / NO_CONTEST excluded (``GRADE_UNRESOLVED``); a settled draw
#     counts as a loss (``GRADE_FAILURE``) — both already enforced by the
#     frozen grading contract in ``shadow_settle.grade_underdog_win``,
#     reused here rather than re-implemented.
#   - Refuse to claim significance under a fixed floor; always print n.
#   - If the evidence is too thin to say anything, say so plainly.

#: Below this settled n, a rate is reported with its Wilson interval but is
#: explicitly flagged as too small to support a significance claim. Chosen
#: to match the existing frozen-baseline-analyzer precedent
#: (``baseline_analyzer.INSUFFICIENT_BUCKET_THRESHOLD``) rather than invent
#: a second, different bar for the same kind of question.
MIN_N_FOR_SIGNIFICANCE = 30

#: Underdog-probability bands, exactly as specified by the owner directive.
#: Rows with no recorded probability get their own explicit bucket rather
#: than being silently dropped or folded into a neighbour.
PROBABILITY_BANDS: tuple[tuple[str, float, float], ...] = (
    ("<0.20", 0.0, 0.20),
    ("0.20-0.30", 0.20, 0.30),
    ("0.30-0.40", 0.30, 0.40),
    ("0.40+", 0.40, 1.0000001),
)
UNKNOWN_PROBABILITY_BAND = "unknown"

#: Directory names under a track's artifact root that hold aggregate/derived
#: material rather than a per-date prediction run; never mistaken for a
#: ``<date>/<run_id>/`` pair.
_NON_RUN_DIR_NAMES = frozenset({"bundles", "errata", "settlements"})

_SUPPLEMENT_RE = re.compile(r"^settlement_(?:supplement|delta)_(\d{8}T\d{6}Z)\.json$")


def wilson_interval(successes: int, n: int, z: float = 1.959963984540054) -> tuple[float, float] | None:
    """Two-sided Wilson score interval for a binomial proportion.

    Returns ``(lo, hi)`` in ``[0, 1]``, or ``None`` if ``n == 0``. ``z`` is
    95% two-sided by default (1.959963984540054). This is what stops a
    3-from-4 reading as a confident "75%": at ``n=4`` the interval spans
    roughly 30%-95%, which is the honest statement, not the point estimate.
    """
    if n <= 0:
        return None
    phat = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = phat + z2 / (2 * n)
    margin = z * math.sqrt(phat * (1 - phat) / n + z2 / (4 * n * n))
    lo = (center - margin) / denom
    hi = (center + margin) / denom
    return (max(0.0, lo), min(1.0, hi))


def _rate_block(successes: int, n: int) -> dict[str, Any]:
    """Exact counts + Wilson interval + an explicit small-n refusal flag.

    Never returns a bare rounded percentage without also carrying ``n``,
    ``successes`` and ``failures`` alongside it, per the owner's directive
    that a percentage without a count is not a finding.
    """
    interval = wilson_interval(successes, n)
    block: dict[str, Any] = {
        "n": n,
        "successes": successes,
        "failures": n - successes,
        "hit_rate": round(successes / n, 4) if n else None,
        "wilson_95_lo": round(interval[0], 4) if interval else None,
        "wilson_95_hi": round(interval[1], 4) if interval else None,
        "significant_n": n >= MIN_N_FOR_SIGNIFICANCE,
    }
    if n == 0:
        block["note"] = "n=0 — no settled rows; nothing to report"
    elif n < MIN_N_FOR_SIGNIFICANCE:
        block["note"] = (
            f"n too small to claim significance (n={n}, floor={MIN_N_FOR_SIGNIFICANCE}); "
            "Wilson interval above is the honest range, not a confident estimate"
        )
    return block


def _probability_band(value: float | None) -> str:
    if value is None:
        return UNKNOWN_PROBABILITY_BAND
    for label, lo, hi in PROBABILITY_BANDS:
        if lo <= value < hi:
            return label
    return UNKNOWN_PROBABILITY_BAND


def _merge_settlement_with_supplements(run_dir: Path) -> list[dict] | None:
    """Final, as-of-today grade for every row in one date's prediction run.

    Reads ``settlement.json`` (the base pass) and applies every
    ``settlement_supplement_*``/``settlement_delta_*`` file found in the
    same directory, in chronological order (their filenames carry a UTC
    timestamp), each of which only ever completes rows that were still
    ``UNSETTLED``/``UNRESOLVED`` in the base pass or an earlier supplement
    — see ``shadow_settle``'s append-only completion contract. A decided
    grade (``SUCCESS``/``FAILURE``) is never overwritten by a later file;
    this only ever replaces still-open rows with their eventual outcome.

    Returns ``None`` if no ``settlement.json`` exists yet for this run (the
    date has selections but is not yet settled — excluded from this
    scorecard's scope entirely, not counted as anything).
    """
    base_path = run_dir / "settlement.json"
    if not base_path.exists():
        return None
    base = json.loads(base_path.read_text())
    by_event_id: dict[str, dict] = {g["event_id"]: dict(g) for g in base.get("grades", [])}

    supplements = []
    for path in run_dir.glob("settlement_supplement_*.json"):
        match = _SUPPLEMENT_RE.match(path.name)
        if match:
            supplements.append((match.group(1), path))
    for path in run_dir.glob("settlement_delta_*.json"):
        match = _SUPPLEMENT_RE.match(path.name)
        if match:
            supplements.append((match.group(1), path))
    for _, path in sorted(supplements):
        try:
            supplement = json.loads(path.read_text())
        except Exception:
            continue
        for row in supplement.get("rows", []):
            event_id = row.get("event_id")
            if event_id in by_event_id:
                by_event_id[event_id] = dict(row)
    return list(by_event_id.values())


def _iter_track_runs(track_root: Path):
    """Yield ``(target_date, run_id, run_dir)`` for every real prediction run.

    Skips ``bundles``/``errata``/``settlements`` (aggregate/derived
    directories, never a ``<date>/<run_id>/`` prediction run) and any
    ``<date>`` directory that is not itself a run's immediate parent.
    """
    if not track_root.exists():
        return
    for date_dir in sorted(track_root.iterdir()):
        if not date_dir.is_dir() or date_dir.name in _NON_RUN_DIR_NAMES:
            continue
        for run_dir in sorted(date_dir.iterdir()):
            if not run_dir.is_dir():
                continue
            manifest_path = run_dir / "manifest.json"
            if manifest_path.exists():
                yield date_dir.name, run_dir.name, run_dir


def _grade_pick(row: dict, pick_index: int | None) -> str:
    """Grade one committed row as if ``pick_index`` (1/2) had been the pick.

    Reuses the frozen ``shadow_settle.grade_underdog_win`` contract exactly
    — it only ever compares ``winner_index`` to whichever index is passed
    in, so passing ``favorite_index`` or ``forebet_pick`` instead of the
    row's own ``underdog_index`` grades "what if we had backed the
    favourite / followed Forebet's own pick on this SAME match" under the
    identical VOID/NO_CONTEST-is-unresolved, draw-is-failure rules the real
    R1 grade already uses. No parallel grading logic to drift out of sync.
    """
    return grade_underdog_win(
        underdog_index=pick_index,
        winner_index=row.get("winner_index"),
        disposition=row.get("disposition") or "",
        sport=row.get("sport") or "",
    )


def _forebet_pick_of(row: dict) -> int | None:
    context = row.get("settled_context")
    if not isinstance(context, dict):
        return None
    pick = context.get("forebet_pick")
    return pick if pick in (1, 2) else None


def _score_pick_rows(rows: list[dict], grades: list[str]) -> dict[str, Any]:
    """Aggregate a list of already-graded outcomes into a rate block."""
    n = sum(1 for g in grades if g in ("SUCCESS", "FAILURE"))
    successes = sum(1 for g in grades if g == "SUCCESS")
    return _rate_block(successes, n)


def _track_scorecard(track_name: str, track_root: Path) -> dict[str, Any]:
    if not track_root.exists():
        return {
            "track": track_name,
            "available": False,
            "note": (
                f"no {track_root} directory exists in this checkout — "
                "no real run has ever been committed on this track. "
                "This is the finding, reported plainly, not papered over."
            ),
        }

    settled_dates: list[str] = []
    unsettled_dates: list[str] = []
    coverage_attempted: Counter[str] = Counter()
    coverage_qualified: Counter[str] = Counter()
    r1_rows: list[dict] = []

    for target_date, run_id, run_dir in _iter_track_runs(track_root):
        manifest = json.loads((run_dir / "manifest.json").read_text())
        for entry in manifest.get("sport_day_summary") or []:
            sport = entry.get("sport")
            if not sport:
                continue
            coverage_attempted[sport] += 1
            if entry.get("status") == "SHADOW_RULE_QUALIFIED":
                coverage_qualified[sport] += 1

        merged = _merge_settlement_with_supplements(run_dir)
        if merged is None:
            unsettled_dates.append(target_date)
            continue
        settled_dates.append(target_date)
        for row in merged:
            if row.get("considered_status") == "PRIMARY_SHADOW_SELECTION":
                row = dict(row)
                row["target_date"] = target_date
                row["run_id"] = run_id
                r1_rows.append(row)

    settled_dates = sorted(set(settled_dates))
    unsettled_dates = sorted(set(unsettled_dates))

    # --- Headline: R1 hit rate, overall and per sport --------------------
    own_grades = [row.get("grade") for row in r1_rows]
    overall = _score_pick_rows(r1_rows, own_grades)
    overall["settled_draws"] = sum(
        1 for row in r1_rows
        if row.get("grade") in ("SUCCESS", "FAILURE") and row.get("winner_index") == 0
    )

    by_sport: dict[str, Any] = {}
    rows_by_sport: dict[str, list[dict]] = defaultdict(list)
    for row in r1_rows:
        rows_by_sport[row.get("sport") or "unknown"].append(row)
    for sport, rows in sorted(rows_by_sport.items()):
        by_sport[sport] = _score_pick_rows(rows, [r.get("grade") for r in rows])
        by_sport[sport]["primary_selections_recorded"] = len(rows)

    # --- Bands by underdog probability ------------------------------------
    by_band: dict[str, Any] = {}
    rows_by_band: dict[str, list[dict]] = defaultdict(list)
    for row in r1_rows:
        rows_by_band[_probability_band(row.get("underdog_probability"))].append(row)
    band_labels = [label for label, _, _ in PROBABILITY_BANDS] + [UNKNOWN_PROBABILITY_BAND]
    for label in band_labels:
        rows = rows_by_band.get(label, [])
        by_band[label] = _score_pick_rows(rows, [r.get("grade") for r in rows])
        by_band[label]["primary_selections_recorded"] = len(rows)

    # --- Baselines, computed on the SAME r1_rows --------------------------
    favourite_grades = [_grade_pick(row, row.get("favorite_index")) for row in r1_rows]
    forebet_pick_rows = [(row, _forebet_pick_of(row)) for row in r1_rows]
    forebet_pick_available = [(row, pick) for row, pick in forebet_pick_rows if pick is not None]
    forebet_pick_grades = [_grade_pick(row, pick) for row, pick in forebet_pick_available]

    baselines = {
        "our_r1_pick": {
            **overall,
            "note": (overall.get("note", "") + " " if overall.get("note") else "")
            + "this IS the headline R1 rate above, repeated here for side-by-side comparison",
        },
        "always_underdog_same_rows": {
            **_score_pick_rows(r1_rows, own_grades),
            "note": (
                "identical to our_r1_pick by construction: every R1 row's pick IS "
                "the underdog of its match, so 'always bet the underdog' on these "
                "SAME rows cannot differ from the R1 outcome itself. Included because "
                "the owner asked for it explicitly as an anchor line, not because it "
                "is informative on its own — see cohort_wide_underdog_rate below for "
                "a baseline that actually differs."
            ),
        },
        "always_favourite_same_rows": {
            **_rate_block(
                sum(1 for g in favourite_grades if g == "SUCCESS"),
                sum(1 for g in favourite_grades if g in ("SUCCESS", "FAILURE")),
            ),
            "note": (
                "a settled draw fails BOTH the underdog bet and the favourite "
                "bet (outright win only, per AGENTS.md invariant 1-3), so this "
                "row's successes+failures need not mirror our_r1_pick's exactly "
                f"even though both are graded on the identical {overall['n']} "
                f"matches ({overall['settled_draws']} of which were draws)"
            ),
        },
        "forebet_pick_same_rows": {
            **_rate_block(
                sum(1 for g in forebet_pick_grades if g == "SUCCESS"),
                sum(1 for g in forebet_pick_grades if g in ("SUCCESS", "FAILURE")),
            ),
            "rows_missing_forebet_pick": len(r1_rows) - len(forebet_pick_available),
        },
    }

    # --- Supplementary: full R2-eligible cohort, not just rank 1 ---------
    # A DIFFERENT row set from r1_rows (every eligible candidate that day,
    # not only the one chosen as rank 1) — explicitly NOT one of the three
    # "same rows" baselines above. Answers a different, still useful,
    # question: does rank-1 selection beat just betting every eligible
    # underdog the pipeline ever surfaces?
    cohort_rows: list[dict] = []
    for target_date, run_id, run_dir in _iter_track_runs(track_root):
        if target_date not in settled_dates:
            continue
        merged = _merge_settlement_with_supplements(run_dir) or []
        cohort_rows.extend(
            row for row in merged
            if row.get("considered_status") in (
                "PRIMARY_SHADOW_SELECTION", "TOP3_EVALUATION_COHORT",
                "ELIGIBLE_RANKED_BEYOND_TOP3",
            )
        )
    cohort_wide = _score_pick_rows(cohort_rows, [r.get("grade") for r in cohort_rows])
    cohort_wide["note"] = (
        "supplementary, NOT one of the three required same-row baselines — a "
        "different, larger row set (every R2-eligible candidate any rank, not "
        "just the rank-1 pick actually selected)."
    )

    return {
        "track": track_name,
        "available": True,
        "scope": {
            "settled_target_dates": settled_dates,
            "settled_target_date_count": len(settled_dates),
            "settled_target_date_range": (
                [settled_dates[0], settled_dates[-1]] if settled_dates else None
            ),
            "unsettled_target_dates_excluded": unsettled_dates,
            "note": (
                "only target dates with a committed settlement.json are scored; "
                f"{len(unsettled_dates)} additional date(s) have selections "
                "but no settlement yet and are excluded from every rate below "
                "(not zero-filled, not guessed)."
            ),
        },
        "coverage": {
            "by_sport": {
                sport: {
                    "sport_days_attempted": coverage_attempted[sport],
                    "sport_days_with_r1": coverage_qualified.get(sport, 0),
                }
                for sport in sorted(coverage_attempted)
            },
            "total_sport_days_attempted": sum(coverage_attempted.values()),
            "total_sport_days_with_r1": sum(coverage_qualified.values()),
            "note": (
                "'attempted' means this sport appeared in that date's "
                "sport_day_summary at all — a sport that failed to fetch "
                "entirely that day (WAF block, timing hold, etc.) never "
                "appears here and is not counted as an attempt; see "
                "HANDOFF.md / docs/STATE.md for the fetch-failure history "
                "this coverage table cannot see."
            ),
        },
        "overall": overall,
        "by_sport": by_sport,
        "by_underdog_probability_band": by_band,
        "baselines_same_rows": baselines,
        "cohort_wide_underdog_rate_supplementary": cohort_wide,
    }


def r1_scorecard(root: Path | str = ".", target_date: str | None = None) -> Path:
    """Build the R1 performance scorecard from committed shadow evidence only.

    Reads every ``data/reports/shadow/**`` (STANDARD track) and
    ``data/reports/shadow_event_day/**`` (EVENT_DAY track) prediction run
    that has a committed ``settlement.json``, applies any
    ``settlement_supplement_*``/``settlement_delta_*`` completions, and
    scores every ``PRIMARY_SHADOW_SELECTION`` ("R1") row: overall hit rate,
    per-sport hit rate, hit rate by underdog-probability band, and three
    baselines computed on the identical rows (always-favourite,
    always-underdog, Forebet's own pick) — see the module-level docstring
    for the full method and the small-n honesty rules this is held to.

    Writes ``data/reports/r1_scorecard_<target_date>.json`` (full detail)
    and a companion ``.md`` (the human-readable table) and returns the
    markdown path. ``target_date`` only labels the artifact filename
    (default: today) — the analysis itself always covers every committed
    date found, exactly like ``analyze_depth``'s "latest census" pattern.
    """
    root = Path(root)
    target_date = target_date or today_iso()
    reports_dir = root / "data" / "reports"

    analysis: dict[str, Any] = {
        "generated_for_date": target_date,
        "method": {
            "r1_definition": (
                "a grade row with considered_status == 'PRIMARY_SHADOW_SELECTION' "
                "(equivalently rank_within_sport_day == 1); verified 1:1 on every "
                "committed row this scorecard has ever read"
            ),
            "grading_contract": (
                "reuses shadow_settle.grade_underdog_win unmodified: VOID/NO_CONTEST/"
                "CANCELLED/ABANDONED -> UNRESOLVED (excluded); a settled draw -> "
                "FAILURE; missing winner or missing pick identity -> UNRESOLVED "
                "(never fabricated as a loss)"
            ),
            "significance_floor": MIN_N_FOR_SIGNIFICANCE,
            "interval": "Wilson score interval, 95% two-sided",
        },
        "tracks": {
            "STANDARD": _track_scorecard("STANDARD", reports_dir / "shadow"),
            "EVENT_DAY": _track_scorecard("EVENT_DAY", reports_dir / "shadow_event_day"),
        },
    }

    json_path = reports_dir / f"r1_scorecard_{target_date}.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(analysis, indent=2, sort_keys=True))
    md_path = reports_dir / f"r1_scorecard_{target_date}.md"
    md_path.write_text(_render_r1_scorecard_markdown(analysis))
    return md_path


def _fmt_rate(block: dict[str, Any]) -> str:
    n = block.get("n", 0)
    if n == 0:
        return "n=0 (no settled rows)"
    rate = block.get("hit_rate")
    lo, hi = block.get("wilson_95_lo"), block.get("wilson_95_hi")
    flag = "" if block.get("significant_n") else " \u26a0\ufe0f n<{}".format(MIN_N_FOR_SIGNIFICANCE)
    return (
        f"{block.get('successes')}/{n} ({rate:.1%}) "
        f"[95% CI {lo:.1%}\u2013{hi:.1%}]{flag}"
    )


def _render_r1_scorecard_markdown(analysis: dict[str, Any]) -> str:
    lines = [
        f"# Slumdog R1 Scorecard — generated for {analysis['generated_for_date']}",
        "",
        "Offline measurement from already-committed shadow evidence only "
        "(`data/reports/shadow/**`). No network access. See `method` in the "
        "JSON receipt for the exact grading contract and significance floor.",
        "",
    ]
    for track_name in ("STANDARD", "EVENT_DAY"):
        track = analysis["tracks"][track_name]
        lines.extend(["", f"## {track_name} track", ""])
        if not track.get("available"):
            lines.append(f"**Not available.** {track.get('note', '')}")
            continue
        scope = track["scope"]
        lines.append(
            f"Scope: **{scope['settled_target_date_count']}** settled target date(s)"
            + (
                f", {scope['settled_target_date_range'][0]} to "
                f"{scope['settled_target_date_range'][1]}"
                if scope["settled_target_date_range"] else ""
            )
            + f". {scope['note']}"
        )
        draws_note = (
            f" (of which {track['overall']['settled_draws']} settled as a draw, "
            "counted as a loss per the grading contract)"
            if track["overall"]["settled_draws"] else ""
        )
        lines.extend([
            "", f"**Overall R1 hit rate: {_fmt_rate(track['overall'])}**{draws_note}", "",
        ])

        lines.extend(["### Coverage — did an R1 exist at all?", "",
                      "| Sport | Sport-days attempted | Sport-days with an R1 | Coverage |",
                      "|---|---:|---:|---:|"])
        cov = track["coverage"]["by_sport"]
        for sport, row in sorted(cov.items()):
            attempted = row["sport_days_attempted"]
            withr1 = row["sport_days_with_r1"]
            pct = f"{withr1 / attempted:.0%}" if attempted else "n/a"
            lines.append(f"| {sport} | {attempted} | {withr1} | {pct} |")
        total = track["coverage"]
        if total["total_sport_days_attempted"]:
            total_pct = f"{total['total_sport_days_with_r1'] / total['total_sport_days_attempted']:.0%}"
        else:
            total_pct = "n/a"
        lines.append(
            f"| **all sports** | **{total['total_sport_days_attempted']}** | "
            f"**{total['total_sport_days_with_r1']}** | {total_pct} |"
        )
        lines.append("")
        lines.append(f"_{track['coverage']['note']}_")

        lines.extend(["", "### R1 hit rate by sport", "",
                      "| Sport | R1 selections recorded | Settled hit rate |",
                      "|---|---:|---|"])
        for sport, block in sorted(track["by_sport"].items()):
            lines.append(
                f"| {sport} | {block['primary_selections_recorded']} | {_fmt_rate(block)} |"
            )

        lines.extend(["", "### R1 hit rate by underdog-probability band", "",
                      "| Band | R1 selections recorded | Settled hit rate |",
                      "|---|---:|---|"])
        for label, _, _ in PROBABILITY_BANDS:
            block = track["by_underdog_probability_band"][label]
            lines.append(
                f"| {label} | {block['primary_selections_recorded']} | {_fmt_rate(block)} |"
            )
        unknown = track["by_underdog_probability_band"][UNKNOWN_PROBABILITY_BAND]
        if unknown["primary_selections_recorded"]:
            lines.append(
                f"| unknown probability | {unknown['primary_selections_recorded']} | "
                f"{_fmt_rate(unknown)} |"
            )

        lines.extend(["", "### Baselines, computed on the SAME R1 rows", "",
                      "| Strategy | Settled hit rate |", "|---|---|"])
        baselines = track["baselines_same_rows"]
        lines.append(f"| Our R1 pick | {_fmt_rate(baselines['our_r1_pick'])} |")
        lines.append(
            f"| Always favourite (opposite pick, same match) | "
            f"{_fmt_rate(baselines['always_favourite_same_rows'])} |"
        )
        fb = baselines["forebet_pick_same_rows"]
        missing = fb["rows_missing_forebet_pick"]
        lines.append(
            f"| Follow Forebet's own pick, same match{f' ({missing} row(s) missing forebet_pick, excluded)' if missing else ''} | "
            f"{_fmt_rate(fb)} |"
        )
        lines.append(
            "| Always underdog (same rows — identical to our R1 pick by "
            f"construction) | {_fmt_rate(baselines['always_underdog_same_rows'])} |"
        )

        cohort = track["cohort_wide_underdog_rate_supplementary"]
        lines.extend([
            "",
            "### Supplementary: full eligible-cohort underdog rate (not one of the three required baselines)",
            "",
            f"Betting the underdog on EVERY R2-eligible candidate the pipeline surfaced "
            f"that day (not just the rank-1 pick actually selected): {_fmt_rate(cohort)}",
            "",
            f"_{cohort['note']}_",
        ])
    lines.append("")
    return "\n".join(lines)
