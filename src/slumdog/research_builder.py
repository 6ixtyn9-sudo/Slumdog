"""Milestone 6A — incremental v2 price-free research builder core.

Strict-mode internals this module mirrors (eligibility chains, feature
formulas, float accumulation order) with the intentional v2 difference in
history membership (research_history_eligible replaces the legacy implicit
HistoryIndex filter; see PRICE_FREE_DATASET_CONTRACT.md). Research-only;
must never be imported by production pipeline modules (pipeline, training,
backfill, depth_sweep, research, forebet, cli).
"""


from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Protocol

from .contracts import RecentForm, SettledEvent
from .dataset import (
    COMPATIBILITY_VOID_ALIASES,
    LABEL_CONTRACT_VERSION,
    PROHIBITED_KEYS,
    VOID_DISPOSITIONS,
    ValidEventWithSource,
    PriceFreeUnderdogExample,
    _canonical_event_repr,
    _extract_provenance,
    _is_valid_sha256,
    _key,
    _source_location_key,
    classify_provenance_pair,
    research_content_repr,
)
from .sports import SPORTS
from .underdog import identify_forebet_underdog, label_underdog_outcome


class ExampleSink(Protocol):
    """Structural type for incremental example consumers (the research
    dataset module's ResearchExampleEmitter satisfies this)."""

    def emit(self, example: PriceFreeUnderdogExample) -> None: ...


RESEARCH_FEATURE_CONTRACT_VERSION = "price-free-v2-incremental-valid-history"


RESEARCH_INPUT_DIGEST_DOMAIN = "slumdog-research-input-v2"


def research_history_eligible(row: SettledEvent) -> bool:
    """Explicit v2 history-eligibility predicate (replaces the implicit
    legacy HistoryIndex membership filter). Intentional differences from
    the legacy filter (documented in PRICE_FREE_DATASET_CONTRACT.md):
    unknown sports and void compatibility aliases excluded; incoherent
    disposition/winner combinations excluded (e.g. SETTLED_CUP with
    winner_index 0, two-way draw under SETTLED); duplicate normalization
    happens before building, so no self-pair double counting is possible.
    """
    spec = SPORTS.get(row.sport)
    if spec is None:
        return False
    k1, k2 = _key(row.participant_1), _key(row.participant_2)
    if not k1 or not k2 or k1 == k2:
        return False
    if row.disposition == "SETTLED":
        return row.winner_index in (1, 2) or (row.winner_index == 0 and spec.draw_possible)
    if row.disposition == "SETTLED_CUP":
        return row.winner_index in (1, 2)
    if row.disposition == "SETTLED_DRAW":
        return row.winner_index == 0 and spec.draw_possible
    return False


@dataclass
class _ParticipantState:
    """Bounded per-(sport, participant-key) history state (v2 membership).

    recent_wins holds the last 5 win flags in (event_date, event_id) order —
    the same order the legacy HistoryIndex per-participant lists use, so
    readouts are bit-identical for equivalent rows.
    """

    appearances: int = 0
    recent_wins: deque = field(default_factory=lambda: deque(maxlen=5))
    draws: int = 0
    scored_sum: float = 0.0
    scored_count: int = 0
    conceded_sum: float = 0.0
    conceded_count: int = 0


@dataclass
class _H2HState:
    """Bounded per-(sport, sorted participant-key pair) state."""

    total: int = 0
    wins_a: int = 0
    wins_b: int = 0


def _exclusion_counter_name(reason: str) -> str:
    """Map identity/label ineligibility reasons to receipt counter names
    (mirrors the strict builder's reason chains exactly)."""
    return {
        "VOID": "excluded_void",
        "SOURCE_CONFLICT": "excluded_source_conflict",
        "UNKNOWN_SPORT": "excluded_unknown_sport",
        "UNEXPECTED_DRAW_FOR_TWO_WAY": "excluded_unexpected_two_way_draw",
        "INVALID_WINNER_INDEX": "excluded_invalid_winner",
        "EQUAL_PROBABILITY": "excluded_equal_probability",
        "MISSING_PROBABILITY": "excluded_missing_probability",
        "NON_FINITE_PROBABILITY": "excluded_non_finite_probability",
        "INVALID_PROBABILITY": "excluded_non_finite_probability",
        "OUT_OF_RANGE_PROBABILITY": "excluded_out_of_range_probability",
    }.get(reason, "excluded_other")


def _provenance_coverage(event: SettledEvent) -> int:
    raw, url = _extract_provenance(event)
    return int(bool(raw)) + int(bool(url))


def _pick_representative(entries: list[ValidEventWithSource]) -> ValidEventWithSource:
    """Deterministic duplicate representative: max provenance coverage, then
    stable (source_file, numeric source location) tie-break. No input-order
    selection — the key is total within a composite-key group (one row per
    source file + location)."""

    def sort_key(v: ValidEventWithSource) -> tuple:
        return (
            -_provenance_coverage(v.event),
            v.source_file,
            _source_location_key(v.source_location),
        )

    return min(entries, key=sort_key)


def _normalize_duplicates(
    candidates: list[ValidEventWithSource],
) -> tuple[list[SettledEvent], int, list[str]]:
    """Content/provenance-separated duplicate normalization over
    non-conflicting rows (the census already excluded conflicting keys).

    Layer 1 (content): all research_content_repr must be equal within a
    composite-key group — fail closed otherwise (the census should have
    flagged any such group; a mismatch is an internal error). Layer 2
    (provenance): pairwise classify_provenance_pair; any "conflict" is an
    internal error; otherwise the deterministic representative is kept.

    Returns (canonical_events, exact_duplicates_collapsed, errors).
    """
    errors: list[str] = []
    grouped: dict[tuple[str, str, str], list[ValidEventWithSource]] = defaultdict(list)
    for v in candidates:
        grouped[_composite_key(v.event)].append(v)

    canonical: list[SettledEvent] = []
    exact_duplicates_collapsed = 0
    for key, entries in sorted(grouped.items()):
        group_error = False
        first = research_content_repr(entries[0].event)
        for other in entries[1:]:
            if research_content_repr(other.event) != first:
                errors.append(f"internal_content_mismatch:{key[0]}:{key[1]}:{key[2]}")
                group_error = True
                break
        if not group_error:
            for i in range(len(entries)):
                for j in range(i + 1, len(entries)):
                    if classify_provenance_pair(entries[i].event, entries[j].event) == "conflict":
                        errors.append(
                            f"internal_provenance_conflict:{key[0]}:{key[1]}:{key[2]}"
                        )
                        group_error = True
                        break
                if group_error:
                    break
        if group_error:
            continue
        exact_duplicates_collapsed += len(entries) - 1
        canonical.append(_pick_representative(entries).event)
    return canonical, exact_duplicates_collapsed, errors


def _compute_research_input_digest(
    rows_by_sport: dict[str, list[SettledEvent]],
) -> tuple[str, dict[str, str]]:
    """v2 input digests: per-sport SHA-256 over the LF-terminated
    _canonical_event_repr JSONL (rows sorted by (event_date, event_id));
    combined SHA-256 over the exact bytes
    ``slumdog-research-input-v2\\n`` + per-sport ``sport\\nrow_count\\ndigest\\n``
    blocks sorted by sport. Full 64-hex digests, never truncated.

    Returns (combined_input_digest, sport_digests).
    """
    sport_digests: dict[str, str] = {}
    for sport in sorted(rows_by_sport):
        h = hashlib.sha256()
        for row in rows_by_sport[sport]:
            canon = _canonical_event_repr(row)
            h.update(
                (json.dumps(canon, sort_keys=True, separators=(",", ":")) + "\n").encode(
                    "utf-8"
                )
            )
        sport_digests[sport] = h.hexdigest()
    combined = (RESEARCH_INPUT_DIGEST_DOMAIN + "\n").encode("utf-8")
    for sport in sorted(sport_digests):
        combined += f"{sport}\n{len(rows_by_sport[sport])}\n{sport_digests[sport]}\n".encode(
            "utf-8"
        )
    return hashlib.sha256(combined).hexdigest(), sport_digests


class _IncrementalBuilder:
    """Incremental per-sport, per-event-date builder with same-date
    isolation: the read phase uses only state from dates < D; the update
    phase records date-D rows after the batch's read phase. Eligibility
    chains, feature formulas, and float accumulation order mirror the
    strict builder exactly; only bounded state is kept and only
    research_history_eligible rows feed history state (v2 membership).
    """

    def __init__(self) -> None:
        self.participants: dict[tuple[str, str], _ParticipantState] = {}
        self.h2h_states: dict[tuple[str, tuple[str, str]], _H2HState] = {}
        self.excluded: Counter = Counter()
        self.prohibited_found: set[str] = set()
        self.leaked_keys = 0
        self.positive = 0
        self.negative_fav = 0
        self.negative_draw = 0

    def _recent_form(self, sport: str, key: str) -> RecentForm | None:
        ps = self.participants.get((sport, key))
        if ps is None:
            return None
        return RecentForm(wins=sum(ps.recent_wins), games=len(ps.recent_wins))

    def _scoring_stats(
        self, sport: str, participant_name: str
    ) -> tuple[float | None, float | None, int, int]:
        ps = self.participants.get((sport, _key(participant_name)))
        if ps is None:
            return None, None, 0, 0
        avg_scored = ps.scored_sum / ps.scored_count if ps.scored_count else None
        avg_conceded = ps.conceded_sum / ps.conceded_count if ps.conceded_count else None
        return avg_scored, avg_conceded, ps.draws, ps.appearances

    def process_row(
        self,
        row: SettledEvent,
        conflicting_keys: set[tuple[str, str, str]],
        emitter: ExampleSink,
        global_agg: _ReadinessAgg,
        sport_agg: _ReadinessAgg,
    ) -> None:
        """Read phase for one canonical row: eligibility chains, feature
        snapshot from prior-only state, emit (or count an exclusion)."""
        sport = row.sport
        if sport not in SPORTS:
            self.excluded["excluded_unknown_sport"] += 1
            return

        disp = (row.disposition or "SETTLED").strip().upper()
        if disp in VOID_DISPOSITIONS or disp in COMPATIBILITY_VOID_ALIASES:
            self.excluded["excluded_void"] += 1
            return

        identity = identify_forebet_underdog(row.probability_1, row.probability_2, row.draw_probability)
        if not identity.eligible:
            reason = identity.ineligibility_reason or "NO_ELIGIBLE_IDENTITY"
            self.excluded[_exclusion_counter_name(reason)] += 1
            return

        # v2 research eligibility: a row whose two sides are the same
        # participant is not a valid canonical settled event — never emit an
        # example and never feed history (the strict builder may emit such
        # rows; intentional difference, mirroring research_history_eligible).
        if _key(row.participant_1) == _key(row.participant_2):
            self.excluded["excluded_self_pair"] += 1
            return

        label_result = label_underdog_outcome(
            sport, identity, row.winner_index, disposition=disp, source_conflict=False
        )
        if not label_result.eligible:
            ex = label_result.exclusion_reason or "UNKNOWN"
            self.excluded[_exclusion_counter_name(ex)] += 1
            return

        assert label_result.label in (0, 1)
        label = label_result.label
        is_draw = label_result.is_draw
        if label == 1:
            self.positive += 1
        elif is_draw:
            self.negative_draw += 1
        else:
            self.negative_fav += 1

        # Provenance validation — raw_sha256 must be 64 hex chars to count as present
        raw_sha, _ = _extract_provenance(row)
        if raw_sha:
            provenance = "present" if _is_valid_sha256(raw_sha) else "invalid"
        else:
            provenance = "missing"

        k1, k2 = _key(row.participant_1), _key(row.participant_2)
        recent_1 = self._recent_form(sport, k1)
        recent_2 = self._recent_form(sport, k2)

        fav_idx = identity.favorite_index
        dog_idx = identity.underdog_index
        assert fav_idx in (1, 2) and dog_idx in (1, 2)

        if dog_idx == 1:
            dog_recent = recent_1
            fav_recent = recent_2
        else:
            dog_recent = recent_2
            fav_recent = recent_1

        underdog_prior_games = dog_recent.games if dog_recent else 0
        favorite_prior_games = fav_recent.games if fav_recent else 0
        underdog_prior_win_rate = (
            dog_recent.win_rate if dog_recent and dog_recent.games > 0 else None
        )
        favorite_prior_win_rate = (
            fav_recent.win_rate if fav_recent and fav_recent.games > 0 else None
        )
        if underdog_prior_win_rate is not None and favorite_prior_win_rate is not None:
            recent_win_rate_gap = underdog_prior_win_rate - favorite_prior_win_rate
        else:
            recent_win_rate_gap = None

        pair = tuple(sorted((k1, k2)))
        hs = self.h2h_states.get((sport, pair))
        h2h_prior_games = hs.total if hs is not None else 0
        if h2h_prior_games > 0:
            p1_wins = hs.wins_a if k1 == pair[0] else hs.wins_b
            p2_wins = hs.wins_b if k1 == pair[0] else hs.wins_a
            if dog_idx == 1:
                dog_h2h_wins = p1_wins
            else:
                dog_h2h_wins = p2_wins
            h2h_underdog_win_rate = dog_h2h_wins / h2h_prior_games if h2h_prior_games else None
            h2h_draw_rate = (h2h_prior_games - p1_wins - p2_wins) / h2h_prior_games if h2h_prior_games else None
        else:
            h2h_underdog_win_rate = None
            h2h_draw_rate = None

        dog_avg_scored, dog_avg_conceded, dog_draws, dog_total = self._scoring_stats(
            sport, row.participant_1 if dog_idx == 1 else row.participant_2
        )
        fav_avg_scored, fav_avg_conceded, fav_draws, fav_total = self._scoring_stats(
            sport, row.participant_1 if fav_idx == 1 else row.participant_2
        )

        if dog_total > 0:
            underdog_prior_draw_rate = dog_draws / dog_total if dog_total else None
        else:
            underdog_prior_draw_rate = None

        if fav_total > 0:
            favorite_prior_draw_rate = fav_draws / fav_total if fav_total else None
        else:
            favorite_prior_draw_rate = None

        if dog_avg_scored is not None and fav_avg_scored is not None:
            prior_scoring_rate_gap = dog_avg_scored - fav_avg_scored
        else:
            prior_scoring_rate_gap = None

        if dog_avg_conceded is not None and fav_avg_conceded is not None:
            prior_conceding_rate_gap = dog_avg_conceded - fav_avg_conceded
        else:
            prior_conceding_rate_gap = None

        features: dict[str, float | None] = {
            "forebet_favorite_probability": identity.favorite_probability,
            "forebet_underdog_probability": identity.underdog_probability,
            "forebet_probability_gap": identity.probability_gap,
            "forebet_draw_probability": identity.draw_probability,
            "forebet_draw_probability_missing": 1.0 if identity.draw_probability is None else 0.0,
            "underdog_prior_games": float(underdog_prior_games),
            "favorite_prior_games": float(favorite_prior_games),
            "underdog_prior_win_rate": underdog_prior_win_rate,
            "favorite_prior_win_rate": favorite_prior_win_rate,
            "recent_win_rate_gap": recent_win_rate_gap,
            "h2h_prior_games": float(h2h_prior_games),
            "h2h_underdog_win_rate": h2h_underdog_win_rate,
            "h2h_draw_rate": h2h_draw_rate,
            "underdog_prior_draw_rate": underdog_prior_draw_rate,
            "favorite_prior_draw_rate": favorite_prior_draw_rate,
            "prior_scoring_rate_gap": prior_scoring_rate_gap,
            "prior_conceding_rate_gap": prior_conceding_rate_gap,
        }

        missingness: dict[str, int] = {}
        for k, v in features.items():
            if k == "forebet_draw_probability_missing":
                missingness[k] = 0
            else:
                missingness[k] = 1 if v is None else 0
        for gkey in ("underdog_prior_games", "favorite_prior_games", "h2h_prior_games"):
            missingness[gkey] = 0

        raw_for_example, url_for_example = _extract_provenance(row)

        example = PriceFreeUnderdogExample(
            event_id=row.event_id,
            sport=sport,
            event_date=row.event_date,
            favorite_index=fav_idx,
            underdog_index=dog_idx,
            favorite_probability=identity.favorite_probability,
            underdog_probability=identity.underdog_probability,
            draw_probability=identity.draw_probability,
            probability_gap=identity.probability_gap or 0.0,
            label=label,
            features={k: features[k] for k in sorted(features)},
            missingness={k: missingness[k] for k in sorted(missingness)},
            source_url=url_for_example,
            raw_sha256=raw_for_example,
            feature_contract_version=RESEARCH_FEATURE_CONTRACT_VERSION,
            label_contract_version=LABEL_CONTRACT_VERSION,
            exclusion_reason=None,
            legacy_provenance_missing=not bool(raw_for_example),
        )

        _collect_prohibited_keys(example.to_dict(), self.prohibited_found)
        if (sport, row.event_id, row.event_date) in conflicting_keys:
            self.leaked_keys += 1

        for agg in (global_agg, sport_agg):
            agg.record(
                date=row.event_date,
                label=label,
                is_draw=is_draw,
                provenance=provenance,
                features=features,
                underdog_prior_games=underdog_prior_games,
                favorite_prior_games=favorite_prior_games,
                h2h_prior_games=h2h_prior_games,
            )

        emitter.emit(example)

    def update_state(self, row: SettledEvent) -> None:
        """Update phase: record one date-D row into bounded state.

        Called only for research_history_eligible rows, after the row's
        event-date batch has been read (same-date isolation).
        """
        sport = row.sport
        k1, k2 = _key(row.participant_1), _key(row.participant_2)
        w = row.winner_index
        for pos, key in ((1, k1), (2, k2)):
            ps = self.participants.get((sport, key))
            if ps is None:
                ps = self.participants[(sport, key)] = _ParticipantState()
            ps.appearances += 1
            ps.recent_wins.append(1 if w == pos else 0)
            if w == 0:
                ps.draws += 1
            if row.score_1 is not None and row.score_2 is not None:
                scored, conceded = (row.score_1, row.score_2) if pos == 1 else (row.score_2, row.score_1)
                ps.scored_sum += float(scored)
                ps.conceded_sum += float(conceded)
                ps.scored_count += 1
                ps.conceded_count += 1
        pair = tuple(sorted((k1, k2)))
        hs = self.h2h_states.get((sport, pair))
        if hs is None:
            hs = self.h2h_states[(sport, pair)] = _H2HState()
        hs.total += 1
        if w in (1, 2):
            winner_key = k1 if w == 1 else k2
            if winner_key == pair[0]:
                hs.wins_a += 1
            else:
                hs.wins_b += 1


def _composite_key(event: SettledEvent) -> tuple[str, str, str]:
    return (event.sport, event.event_id, event.event_date)


def _collect_prohibited_keys(obj: Any, found: set[str]) -> None:
    """Recursively collect exact prohibited key names from an example payload.

    Exact key matching only — values are never scanned, so receipt prose that
    legitimately mentions e.g. ROI is not flagged.
    """
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(key, str) and key in PROHIBITED_KEYS:
                found.add(key)
            _collect_prohibited_keys(value, found)
    elif isinstance(obj, list):
        for item in obj:
            _collect_prohibited_keys(item, found)


@dataclass
class _ReadinessAgg:
    """Bounded readiness aggregates updated as examples are emitted."""

    eligible: int = 0
    positive: int = 0
    negative_fav: int = 0
    negative_draw: int = 0
    provenance_present: int = 0
    provenance_missing: int = 0
    provenance_invalid: int = 0
    date_min: str | None = None
    date_max: str | None = None
    per_date: Counter = field(default_factory=Counter)
    feat_missing: Counter = field(default_factory=Counter)
    hist_underdog_ok: int = 0
    hist_favorite_ok: int = 0
    hist_both_ok: int = 0
    hist_h2h_ok: int = 0

    def record(
        self,
        *,
        date: str,
        label: int,
        is_draw: bool,
        provenance: str,
        features: dict[str, float | None],
        underdog_prior_games: int,
        favorite_prior_games: int,
        h2h_prior_games: int,
    ) -> None:
        self.eligible += 1
        if label == 1:
            self.positive += 1
        elif is_draw:
            self.negative_draw += 1
        else:
            self.negative_fav += 1
        if provenance == "present":
            self.provenance_present += 1
        elif provenance == "invalid":
            self.provenance_invalid += 1
        else:
            self.provenance_missing += 1
        if self.date_min is None or date < self.date_min:
            self.date_min = date
        if self.date_max is None or date > self.date_max:
            self.date_max = date
        self.per_date[date] += 1
        for name, value in features.items():
            if value is None:
                self.feat_missing[name] += 1
        u = 1 if underdog_prior_games > 0 else 0
        f = 1 if favorite_prior_games > 0 else 0
        self.hist_underdog_ok += u
        self.hist_favorite_ok += f
        self.hist_both_ok += 1 if (u and f) else 0
        self.hist_h2h_ok += 1 if h2h_prior_games > 0 else 0
