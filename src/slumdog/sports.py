"""Forebet sport inventory and outcome contracts."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SportSpec:
    key: str
    path: str
    outcome_kind: str
    draw_possible: bool
    period_labels: tuple[str, ...]
    known_facets: tuple[str, ...]
    current_only: bool = False
    #: Whether a contest can END level, which is NOT the same question as
    #: whether the board prices a draw. MMA prices two outcomes and still
    #: produces draws (unanimous, majority and split), so the market shape
    #: (``draw_possible``) and the outcome space (this field) differ. A sport
    #: that can end level settles a draw as a failed underdog win; a sport
    #: that cannot is telling us the parse went wrong, and that row is
    #: excluded rather than graded.
    draw_outcome_possible: bool | None = None

    @property
    def draw_settles(self) -> bool:
        """True when a level result is a real outcome to be graded."""
        if self.draw_outcome_possible is None:
            return self.draw_possible
        return self.draw_outcome_possible


HISTORY_STARTS: dict[str, str | None] = {
    "football": "2024-01-01",
    "basketball": "2023-01-01",
    "tennis": "2024-01-01",
    "hockey": "2022-01-01",
    "baseball": "2024-01-01",
    "american_football": "2023-01-01",
    "rugby": "2024-01-01",
    "handball": "2024-01-01",
    "volleyball": "2024-01-01",
    "cricket": "2025-01-01",
    "mma": "2025-01-01",
    "esports": "2024-01-01",
    "esoccer": None,
    "afl": None,
}


SPORTS: dict[str, SportSpec] = {
    "football": SportSpec(
        "football", "football-tips-and-predictions", "score_1x2", True,
        ("1H", "2H"),
        ("standings", "form", "home_form", "away_form", "h2h", "streaks", "weather", "predicted_score", "average_goals", "btts", "totals"),
    ),
    "basketball": SportSpec(
        "basketball", "basketball", "score_2way", False,
        ("Q1", "Q2", "Q3", "Q4", "OT"),
        ("standings", "form", "h2h", "quarter_scores", "predicted_score", "predicted_total", "moneyline"),
    ),
    "tennis": SportSpec(
        "tennis", "tennis", "sets_2way", False,
        ("S1", "S2", "S3", "S4", "S5"),
        ("surface", "tournament_round", "rank", "form", "h2h", "predicted_sets", "predicted_games", "moneyline"),
    ),
    "hockey": SportSpec(
        "hockey", "hockey", "score_2way_overtime", False,
        ("P1", "P2", "P3", "OT"),
        ("standings", "form", "h2h", "period_scores", "predicted_score", "predicted_total", "overtime_rule", "moneyline"),
    ),
    "baseball": SportSpec(
        "baseball", "baseball", "runs_2way", False,
        tuple(f"IN{i}" for i in range(1, 10)),
        ("standings", "form", "h2h", "innings", "hits", "predicted_score", "predicted_total", "moneyline"),
    ),
    "american_football": SportSpec(
        "american_football", "american-football", "score_2way", False,
        ("Q1", "Q2", "Q3", "Q4", "OT"),
        ("standings", "form", "h2h", "quarter_scores", "predicted_score", "predicted_total", "moneyline"),
    ),
    "rugby": SportSpec(
        "rugby", "rugby", "score_2way", False,
        ("1H", "2H", "OT"),
        ("standings", "form", "h2h", "half_scores", "predicted_score", "predicted_total", "moneyline"),
    ),
    "handball": SportSpec(
        "handball", "handball", "score_1x2", True,
        ("1H", "2H"),
        ("standings", "form", "h2h", "half_scores", "predicted_score", "predicted_total", "moneyline"),
    ),
    "volleyball": SportSpec(
        "volleyball", "volleyball", "sets_2way", False,
        ("S1", "S2", "S3", "S4", "S5"),
        ("standings", "form", "h2h", "set_scores", "predicted_sets", "predicted_total_points", "moneyline"),
    ),
    "cricket": SportSpec(
        "cricket", "cricket", "format_specific_1x2", True,
        ("INN1", "INN2", "INN3", "INN4"),
        ("match_format", "tour", "competition", "form", "h2h", "innings", "predicted_winner", "predicted_score", "moneyline", "draw_no_result"),
    ),
    "mma": SportSpec(
        "mma", "mma", "fight_2way", False,
        ("R1", "R2", "R3", "R4", "R5"),
        ("division", "fighter_record", "height", "weight", "reach", "stance", "strikes", "takedowns", "submissions", "control_time", "predicted_method", "moneyline"),
        draw_outcome_possible=True,
    ),
    "esports": SportSpec(
        "esports", "esports", "maps_2way", False,
        ("M1", "M2", "M3", "M4", "M5"),
        ("game", "league", "tournament", "form", "h2h", "predicted_score", "predicted_maps", "moneyline"),
    ),
    "esoccer": SportSpec(
        "esoccer", "esoccer", "score_1x2", True,
        ("1H", "2H"),
        ("player_identity", "game_format", "league", "form", "h2h", "predicted_score", "average_goals"),
        current_only=True,
    ),
    "afl": SportSpec(
        "afl", "afl", "score_2way", False,
        ("Q1", "Q2", "Q3", "Q4"),
        ("ladder", "form", "h2h", "quarter_scores", "predicted_score", "predicted_total", "moneyline"),
        current_only=True,
    ),
}


def sport_spec(key: str) -> SportSpec:
    try:
        return SPORTS[key]
    except KeyError as exc:
        raise ValueError(f"unsupported sport: {key}") from exc
