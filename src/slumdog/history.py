"""Historical settlement context indexed for prior-only H2H and form."""
from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict

from .contracts import H2HStats, RecentForm, SettledEvent
from .sports import SPORTS


def _key(name: str) -> str:
    return "".join(ch for ch in name.casefold() if ch.isalnum())


class HistoryIndex:
    """Build H2H and recent-form context using strictly earlier event dates."""

    def __init__(self, rows: list[SettledEvent]):
        self.rows = sorted(
            [
                row for row in rows
                if row.disposition != "VOID"
                and not (
                    row.winner_index == 0
                    and row.sport in SPORTS
                    and not SPORTS[row.sport].draw_possible
                )
                and not (
                    row.disposition == "SETTLED_DRAW"
                    and (row.sport not in SPORTS or not SPORTS[row.sport].draw_possible)
                )
            ],
            key=lambda row: (row.event_date, row.event_id),
        )
        self.by_sport: dict[str, list[SettledEvent]] = defaultdict(list)
        self.by_pair: dict[tuple[str, tuple[str, str]], list[SettledEvent]] = defaultdict(list)
        self.by_participant: dict[tuple[str, str], list[SettledEvent]] = defaultdict(list)
        for row in self.rows:
            self.by_sport[row.sport].append(row)
            p1, p2 = _key(row.participant_1), _key(row.participant_2)
            self.by_pair[(row.sport, tuple(sorted((p1, p2))))].append(row)
            self.by_participant[(row.sport, p1)].append(row)
            self.by_participant[(row.sport, p2)].append(row)

    @staticmethod
    def _earlier(rows: list[SettledEvent], event_date: str) -> list[SettledEvent]:
        keys = [(row.event_date, row.event_id) for row in rows]
        index = bisect_left(keys, (event_date, ""))
        return rows[:index]

    def prior_rows(self, sport: str, event_date: str) -> list[SettledEvent]:
        return self._earlier(self.by_sport.get(sport, []), event_date)

    def context(
        self,
        sport: str,
        event_date: str,
        participant_1: str,
        participant_2: str,
        recent_n: int = 5,
    ) -> tuple[H2HStats, RecentForm, RecentForm]:
        p1, p2 = _key(participant_1), _key(participant_2)
        pair_key = (sport, tuple(sorted((p1, p2))))
        h2h_rows = self._earlier(self.by_pair.get(pair_key, []), event_date)
        p1_wins = 0
        p2_wins = 0
        max_periods = max((len(row.period_scores_1) for row in h2h_rows), default=0)
        period_1_wins = [0] * max_periods
        period_2_wins = [0] * max_periods
        period_counts = [0] * max_periods
        for row in h2h_rows:
            direct = _key(row.participant_1) == p1
            winner = row.winner_index if direct else (2 if row.winner_index == 1 else 1 if row.winner_index == 2 else 0)
            p1_wins += winner == 1
            p2_wins += winner == 2
            scores_1 = row.period_scores_1 if direct else row.period_scores_2
            scores_2 = row.period_scores_2 if direct else row.period_scores_1
            for index, (score_1, score_2) in enumerate(zip(scores_1, scores_2)):
                period_counts[index] += 1
                period_1_wins[index] += score_1 > score_2
                period_2_wins[index] += score_2 > score_1

        rates_1 = tuple(
            period_1_wins[i] / period_counts[i] if period_counts[i] else 0.0
            for i in range(max_periods)
        )
        rates_2 = tuple(
            period_2_wins[i] / period_counts[i] if period_counts[i] else 0.0
            for i in range(max_periods)
        )

        def recent_form(participant: str) -> RecentForm:
            key = _key(participant)
            appearances = self._earlier(
                self.by_participant.get((sport, key), []), event_date
            )[-recent_n:]
            wins = 0
            for row in appearances:
                if _key(row.participant_1) == key:
                    wins += row.winner_index == 1
                else:
                    wins += row.winner_index == 2
            return RecentForm(wins=wins, games=len(appearances))

        return (
            H2HStats(
                total_games=len(h2h_rows),
                participant_1_wins=p1_wins,
                participant_2_wins=p2_wins,
                period_win_rates_1=rates_1,
                period_win_rates_2=rates_2,
            ),
            recent_form(participant_1),
            recent_form(participant_2),
        )
