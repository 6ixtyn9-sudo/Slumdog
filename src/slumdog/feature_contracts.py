"""Sport-specific Slumdog feature contracts.

These contracts describe intended pre-event inputs after detail parsers and
historical coverage gates are complete. They are not yet training inputs.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SportFeatureContract:
    sport: str
    outcome_contract: str
    required: tuple[str, ...]
    optional: tuple[str, ...]
    blocked: tuple[str, ...]


COMMON = (
    "forebet_participant_probabilities",
    "forebet_predicted_participant",
    "probability_gap",
    "probability_entropy",
    "competition",
    "round_or_stage",
)

BLOCKED_COMMON = (
    "live_score",
    "final_score",
    "result_status_after_start",
    "post_event_form",
    "unknown_timing_field",
)


CONTRACTS: dict[str, SportFeatureContract] = {
    "football": SportFeatureContract(
        "football", "regulation/full-time 1X2 with draw",
        COMMON + ("draw_probability", "standings_gap", "home_away_form_gap", "attack_defence_gap", "h2h_1x2"),
        ("btts_profile", "totals_profile", "htft_profile", "corners_profile", "cards_profile", "weather", "streaks"),
        BLOCKED_COMMON,
    ),
    "basketball": SportFeatureContract(
        "basketball", "full-game moneyline including overtime",
        COMMON + ("predicted_margin", "predicted_total", "standings_win_rate_gap", "home_away_form_gap", "recent_point_diff"),
        ("quarter_consistency", "h2h_margin", "rest_days", "schedule_density", "pace_proxy"),
        BLOCKED_COMMON,
    ),
    "tennis": SportFeatureContract(
        "tennis", "match winner; retirement/walkover excluded until explicit policy",
        COMMON + ("surface", "surface_win_rate_gap", "surface_sample", "tournament_round", "predicted_set_margin", "recent_form_gap"),
        ("ranking_gap", "height_gap", "h2h", "expected_games_per_set"),
        BLOCKED_COMMON + ("post-retirement_result",),
    ),
    "hockey": SportFeatureContract(
        "hockey", "match winner including OT/penalties; regulation markets separate",
        COMMON + ("predicted_margin", "predicted_total", "standings_gap", "recent_goal_diff", "h2h"),
        ("period_dominance", "period_variance", "home_away_form_gap", "overtime_frequency"),
        BLOCKED_COMMON,
    ),
    "baseball": SportFeatureContract(
        "baseball", "full-game winner including extra innings; postponements void",
        COMMON + ("predicted_run_margin", "predicted_total", "recent_run_diff", "h2h", "home_away_form_gap"),
        ("hits_environment", "innings_profile", "pitcher_factors_if_prematch_proven", "competition_context"),
        BLOCKED_COMMON + ("unconfirmed_pitcher",),
    ),
    "american_football": SportFeatureContract(
        "american_football", "full-game moneyline including overtime; ties explicit",
        COMMON + ("predicted_margin", "predicted_total", "recent_point_diff", "h2h_margin", "home_away_form_gap", "quarter_scoring_balance"),
        ("rest_days", "competition_context"),
        BLOCKED_COMMON,
    ),
    "rugby": SportFeatureContract(
        "rugby", "full-game winner; draw handling must match displayed market",
        COMMON + ("predicted_margin", "predicted_total", "recent_point_diff", "h2h_margin", "home_away_form_gap"),
        ("half_profile", "competition_round"),
        BLOCKED_COMMON + ("ambiguous_draw_market",),
    ),
    "handball": SportFeatureContract(
        "handball", "full-time 1X2 with draw",
        COMMON + ("draw_probability", "predicted_margin", "predicted_total", "recent_goal_diff", "h2h_1x2"),
        ("standings_gap", "half_strength_gap", "home_away_form_gap"),
        BLOCKED_COMMON,
    ),
    "volleyball": SportFeatureContract(
        "volleyball", "match winner by sets; retirements/abandonments void",
        COMMON + ("predicted_set_margin", "points_per_set", "recent_set_diff", "h2h", "group_rank_gap"),
        ("set_stability", "home_away_form_gap"),
        BLOCKED_COMMON,
    ),
    "cricket": SportFeatureContract(
        "cricket", "format-specific winner; draw/no-result/DLS explicit",
        COMMON + ("match_format", "draw_no_result_probability", "predicted_runs", "format_recent_form", "h2h_format"),
        ("innings_profile", "tour_context", "venue_context", "bat_chase_context_if_prematch"),
        BLOCKED_COMMON + ("mixed_format_feature",),
    ),
    "mma": SportFeatureContract(
        "mma", "fight winner; draw/no-contest void",
        COMMON + ("division", "record_gap", "reach_gap", "stance_matchup", "strike_gap", "takedown_gap", "submission_gap", "control_time_gap"),
        ("height_gap", "weight_gap", "predicted_method", "scheduled_rounds", "opposition_adjusted_form"),
        BLOCKED_COMMON,
    ),
    "esoccer": SportFeatureContract(
        "esoccer", "full-time 1X2 by player handle",
        COMMON + ("player_handle_1", "player_handle_2", "game_format", "handle_pair_h2h", "repeat_frequency", "short_horizon_drift"),
        ("score_environment", "htft_profile", "corners_profile", "cards_profile"),
        BLOCKED_COMMON + ("physical_club_identity",),
    ),
    "afl": SportFeatureContract(
        "afl", "full-game winner; draw handling follows displayed market",
        COMMON + ("predicted_margin", "predicted_total", "quarter_scoring_balance", "ladder_gap"),
        ("recent_point_diff", "h2h_margin", "home_away_form_gap", "competition_round", "rest_days"),
        BLOCKED_COMMON + ("ambiguous_draw_market",),
    ),
}

MODEL_TRAINING_ALLOWED = False
