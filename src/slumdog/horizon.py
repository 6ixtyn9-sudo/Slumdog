"""Publication-horizon gate (Priority item v).

Pure logic, no network calls. Decides, before a forward-looking capture
request is issued, whether a sport's board has ever been confirmed to
carry a date this far ahead -- driven entirely from the committed capture
receipts under ``data/reports/capture_*.json`` (a glob that already
matches the daily-refresh receipts too, ``capture_refresh_*.json``),
never from a hardcoded table.

Why this exists: the forward pass (D+2..D+6, see
``scripts/forward_shadow_batch.py:run_capture``) used to attempt every
sport at every offset regardless of history, paying a full pause-and-fetch
(and, on failure, the column-route fallback) for sport-dates that had
never once worked. Forward Shadow #33 (run 36426785929) ran >=1h56m on
exactly this undifferentiated cost (see ``docs/STATE.md``,
``HANDOFF.md``). This module is the fix named there as item (v):
"deciding, before issuing any far-board request at all, whether 'D+1' or
similar is even plausibly published yet, based on when boards for that
sport have historically gone live."

Two kinds of sport need no gate at all, because their capture path never
checks the target date against the response body in the first place:

- ``football``: captured from a JSON schedule endpoint and validated
  structurally (``validate_football_json_body``); the
  ``"target date missing from HTML"`` check this module measures from is
  never reached for football at all (see ``validate_capture_body``).
- Sports declared ``current_only=True`` in ``sports.SPORTS`` (``esoccer``,
  ``afl``): ``validate_html_body`` explicitly skips the date-string check
  for these ("for ``current_only`` sports ... the date check [is
  skipped]").

Every other sport's forward reachability is measured, per offset, from
whether a capture ever raised exactly
``ValueError("target date missing from HTML: <date>")`` -- a narrow,
specific signal that the requested date's section is simply not present
in the rendered page, not a network error, a WAF challenge page, or a
parse defect -- versus whether that sport was ever recorded in a receipt's
``captured`` list for that offset.

The gate is asymmetric-safe by design: it will never refuse an offset it
has *seen* work at least once for that sport, even when other receipts
show that same offset failing on a different calendar week. That
contradiction is real and repeatedly observed in the committed evidence
(e.g. rugby has both a confirmed capture and a confirmed
"missing from HTML" at offset +6, on different dates) -- these boards
appear to be governed by each sport's own fixture calendar, not a fixed
technical cutoff, so a sparse-fixture week reads as unreachable even
though the board can and does carry a date that far out on a busier week.
See ``docs/STATE.md``'s "a board is never walked forward to another date"
invariant and its ``observed_dates`` discussion. A false refusal here
(blocking a genuinely publishable date) is the worse failure mode per the
"silence is not evidence of absence" invariant, so the gate only ever
blocks offsets that have *never once* been confirmed reachable.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

from .sports import SPORTS

#: The exact, narrow failure text this module treats as informative about
#: publication timing (see ``validate_html_body`` in ``forebet.py``).
NOT_YET_PUBLISHED_MARKER = "target date missing from HTML"

DEFAULT_RECEIPTS_GLOB = "capture_*.json"


@dataclass(frozen=True)
class HorizonObservation:
    """One sport's forward-reachability record, derived from committed
    evidence. Every field traces back to ``evidence_source``; nothing here
    is asserted without a receipt filename backing it.
    """
    sport: str
    gating_exempt: bool
    exempt_reason: str | None
    reachable_offsets: tuple[int, ...]
    reachable_count: int
    not_yet_published_offsets: tuple[int, ...]
    not_yet_published_count: int
    other_failure_count: int
    receipts_scanned: int
    max_reachable_offset: int | None
    min_reachable_offset: int | None
    evidence_source: dict


def _iter_capture_receipts(root: Path, glob: str):
    """Yield ``(receipt_name, offset_days, payload)`` for every readable,
    well-formed capture receipt under ``data/reports/``. Offline only:
    reads committed JSON files, issues no requests. A receipt missing
    ``target_date``/``generated_at``, or with values that don't parse as
    dates, is silently skipped -- it carries no horizon evidence either
    way, not a claim of absence.
    """
    reports_dir = Path(root) / "data" / "reports"
    if not reports_dir.is_dir():
        return
    for path in sorted(reports_dir.glob(glob)):
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        target_date = payload.get("target_date")
        generated_at = payload.get("generated_at")
        if not isinstance(target_date, str) or not isinstance(generated_at, str):
            continue
        try:
            target = dt.date.fromisoformat(target_date)
            as_of = dt.datetime.fromisoformat(
                generated_at.replace("Z", "+00:00")).date()
        except ValueError:
            continue
        yield path.name, (target - as_of).days, payload


def compute_observed_horizons(
    root: str | Path, *, glob: str = DEFAULT_RECEIPTS_GLOB,
) -> dict[str, HorizonObservation]:
    """Scan committed capture receipts and derive per-sport forward
    reachability. Offline, deterministic, no network, no caching: run it
    again any time and the horizon widens automatically as real capture
    evidence accumulates -- there is no frozen table to go stale.
    """
    root = Path(root)
    reachable: dict[str, dict[int, list[str]]] = {s: {} for s in SPORTS}
    not_yet: dict[str, dict[int, list[str]]] = {s: {} for s in SPORTS}
    other_fail_count: dict[str, int] = {s: 0 for s in SPORTS}
    receipts_scanned = 0

    for name, offset, payload in _iter_capture_receipts(root, glob):
        receipts_scanned += 1
        for cap in payload.get("captured", []) or []:
            sport = cap.get("sport") if isinstance(cap, dict) else None
            if sport in reachable:
                reachable[sport].setdefault(offset, []).append(name)
        for fail in payload.get("failures", []) or []:
            if not isinstance(fail, str) or ":" not in fail:
                continue
            sport, _, rest = fail.partition(":")
            if sport not in SPORTS:
                continue
            if NOT_YET_PUBLISHED_MARKER in rest:
                not_yet[sport].setdefault(offset, []).append(name)
            else:
                other_fail_count[sport] += 1

    out: dict[str, HorizonObservation] = {}
    for sport, spec in SPORTS.items():
        gating_exempt = False
        exempt_reason: str | None = None
        if sport == "football":
            gating_exempt = True
            exempt_reason = (
                "football is captured via the JSON schedule endpoint "
                "(validate_football_json_body); the "
                f"'{NOT_YET_PUBLISHED_MARKER}' check this gate is built on "
                "never runs for football (see validate_capture_body)")
        elif spec.current_only:
            gating_exempt = True
            exempt_reason = (
                f"sports.SPORTS['{sport}'].current_only is True: "
                "validate_html_body skips the date-string check entirely "
                "for current-only boards, so there is no publication-date "
                "signal to gate on")

        r_offsets = sorted(reachable[sport])
        n_offsets = sorted(not_yet[sport])
        r_count = sum(len(v) for v in reachable[sport].values())
        n_count = sum(len(v) for v in not_yet[sport].values())

        evidence_source = {
            "method": (
                f"data/reports/{glob} (committed capture receipts): a "
                "sport is 'reachable' at offset_days=(target_date - "
                "generated_at date) when it appears in that receipt's "
                "'captured' list; 'not_yet_published' when it failed that "
                f"receipt with '{NOT_YET_PUBLISHED_MARKER}'; every other "
                "failure (network error, challenge page, parse defect) is "
                "not informative about publication timing and is excluded "
                "from both counts"),
            "receipts_scanned": receipts_scanned,
            "reachable_offsets_with_receipts": {
                str(o): sorted(reachable[sport][o]) for o in r_offsets},
            "not_yet_published_offsets_with_receipts": {
                str(o): sorted(not_yet[sport][o]) for o in n_offsets},
            "other_failure_count": other_fail_count[sport],
        }

        out[sport] = HorizonObservation(
            sport=sport,
            gating_exempt=gating_exempt,
            exempt_reason=exempt_reason,
            reachable_offsets=tuple(r_offsets),
            reachable_count=r_count,
            not_yet_published_offsets=tuple(n_offsets),
            not_yet_published_count=n_count,
            other_failure_count=other_fail_count[sport],
            receipts_scanned=receipts_scanned,
            max_reachable_offset=max(r_offsets) if r_offsets else None,
            min_reachable_offset=min(r_offsets) if r_offsets else None,
            evidence_source=evidence_source,
        )
    return out


@dataclass(frozen=True)
class HorizonDecision:
    sport: str
    target_date: str
    as_of_date: str
    offset_days: int
    allowed: bool
    reason: str

    def to_dict(self) -> dict:
        return {
            "sport": self.sport,
            "target_date": self.target_date,
            "as_of_date": self.as_of_date,
            "offset_days": self.offset_days,
            "allowed": self.allowed,
            "reason": self.reason,
        }


def gate_sport_date(
    sport: str,
    target_date: str,
    as_of_date: str,
    horizons: dict[str, HorizonObservation],
) -> HorizonDecision:
    """Decide whether ``sport``'s board is plausibly worth requesting for
    ``target_date`` when the request would actually be sent on
    ``as_of_date``. Pure function: no network, no filesystem access, no
    side effects -- ``horizons`` must already be computed (normally via
    ``compute_observed_horizons``).
    """
    if sport not in SPORTS:
        raise ValueError(f"unsupported sport: {sport}")
    target = dt.date.fromisoformat(target_date)
    as_of = dt.date.fromisoformat(as_of_date)
    offset = (target - as_of).days

    if offset <= 0:
        return HorizonDecision(
            sport, target_date, as_of_date, offset, True,
            "offset_days<=0 (same-day or past date): the horizon gate "
            "only constrains forward-looking requests")

    obs = horizons.get(sport)
    if obs is None:
        return HorizonDecision(
            sport, target_date, as_of_date, offset, True,
            f"'{sport}' has no horizon record at all (never scanned); "
            "refusing to manufacture a limit from zero evidence, per the "
            "'silence is not evidence of absence' invariant")

    if obs.gating_exempt:
        return HorizonDecision(
            sport, target_date, as_of_date, offset, True, obs.exempt_reason)

    informative_observations = obs.reachable_count + obs.not_yet_published_count
    if informative_observations == 0:
        return HorizonDecision(
            sport, target_date, as_of_date, offset, True,
            f"no publication-timing evidence exists for '{sport}' "
            f"specifically ({obs.receipts_scanned} receipts scanned "
            "overall, none informative for this sport -- cold start); "
            "refusing to manufacture a limit from zero evidence, per the "
            "'silence is not evidence of absence' invariant -- this is not "
            "the same as a sport that has been tried and never worked")

    if obs.max_reachable_offset is None:
        return HorizonDecision(
            sport, target_date, as_of_date, offset, False,
            f"'{sport}' has never once been confirmed reachable at a "
            f"forward offset across {obs.receipts_scanned} committed "
            "receipts (confirmed not-yet-published at offsets "
            f"{list(obs.not_yet_published_offsets)}, "
            f"n={obs.not_yet_published_count}); refusing offset_days="
            f"{offset} until a first confirmed success is on record")

    if offset <= obs.max_reachable_offset:
        return HorizonDecision(
            sport, target_date, as_of_date, offset, True,
            f"offset_days={offset} <= observed max reachable offset "
            f"{obs.max_reachable_offset} for '{sport}' (confirmed "
            f"reachable at offsets {list(obs.reachable_offsets)}, "
            f"n={obs.reachable_count}, from {obs.receipts_scanned} "
            "receipts scanned)")

    return HorizonDecision(
        sport, target_date, as_of_date, offset, False,
        f"offset_days={offset} exceeds the observed max reachable offset "
        f"{obs.max_reachable_offset} for '{sport}' (confirmed reachable "
        f"at offsets {list(obs.reachable_offsets)}, n={obs.reachable_count}"
        f"; not-yet-published at offsets "
        f"{list(obs.not_yet_published_offsets)}, "
        f"n={obs.not_yet_published_count}; from {obs.receipts_scanned} "
        "receipts scanned): no evidence this sport's board has ever "
        "carried a date this far ahead")


def filter_sports_by_horizon(
    sports: list[str],
    target_date: str,
    as_of_date: str,
    horizons: dict[str, HorizonObservation],
) -> tuple[list[str], list[HorizonDecision]]:
    """Split ``sports`` into ``(allowed_sports, all_decisions)`` for one
    target date. ``allowed_sports`` preserves input order; ``all_decisions``
    carries one ``HorizonDecision`` per input sport (allowed or not) so a
    caller can log every refusal with its reason, not just the survivors.
    """
    decisions = [
        gate_sport_date(sport, target_date, as_of_date, horizons)
        for sport in sports
    ]
    allowed = [d.sport for d in decisions if d.allowed]
    return allowed, decisions
