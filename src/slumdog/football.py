"""Sport 1 (Football) dedicated domain models, feature engineering, and pipeline.

Football is a 3-way (1X2) sport with high draw prevalence (~25-30%), strong home
advantage dynamics, multi-market betting structures (1X2, Over/Under, BTTS,
Half-Time, Asian Handicap, Cards, Corners), and rich league standings/form.

This module provides:
- Unified multi-market schema definitions
- Comprehensive football feature extraction with de-vigging, PPG, and deep detail metrics
- Football-specific Robber detection with draw-buffer modeling and travel/tactical signals
- Leak-safe numeric vector builder with explicit missingness flags
- Walk-forward validation and specialized settlement
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .contracts import (
    CandidateState,
    EventSnapshot,
    H2HStats,
    RecentForm,
    RobberCandidate,
)
from .magolide import RobberConfig


# ---------------------------------------------------------------------------
# Multi-Market Constants and Keys
# ---------------------------------------------------------------------------

ALL_FOOTBALL_MARKETS: tuple[str, ...] = (
    "1x2", "uo", "bts", "ht", "htft", "ah", "cards", "corners", "doublechance",
)

FOOTBALL_MARKET_KEYS: dict[str, tuple[str, ...]] = {
    "1x2": (
        "Pred_1", "Pred_X", "Pred_2", "best_odd_1", "best_odd_X", "best_odd_2",
        "host_sc_pr", "guest_sc_pr", "goalsavg", "host_pos", "guest_pos",
        "weather_high", "weather_low", "weather_code", "kelly", "Round",
    ),
    "uo": ("pr_over", "pr_under", "odds_under_over", "best_over", "best_under"),
    "bts": ("Pred_gg", "Pred_no_gg", "odds_gg_y", "odds_gg_n"),
    "ht": ("Pred_1_HT", "Pred_X_HT", "Pred_2_HT", "best_odd_ht"),
    "htft": ("odds_ht_ft", "Pred_1_HT", "Pred_X_HT", "Pred_2_HT"),
    "ah": ("odds_ah", "AH_type", "predAH"),
    "cards": (
        "avg_cards", "host_card_pred", "guest_card_pred", "pred_line",
        "pred_over", "pred_under", "host_yellowcards", "guest_yellowcards",
        "host_redcards", "guest_redcards",
    ),
    "corners": (
        "avg_corners", "host_corners", "guest_corners",
        "pred_corners_over", "pred_corners_under",
    ),
    "doublechance": (
        "pred_1x", "pred_12", "pred_x2", "odds_1x", "odds_12", "odds_x2",
    ),
}


# ---------------------------------------------------------------------------
# Mathematical & Statistical Helpers
# ---------------------------------------------------------------------------

def _safe_float(value: Any) -> float | None:
    if value in (None, "", "-", "n/a", "N/A"):
        return None
    try:
        val = float(value)
        return val if math.isfinite(val) else None
    except (TypeError, ValueError):
        return None


def calculate_overround(odds_1: float | None, odds_x: float | None, odds_2: float | None) -> float | None:
    """Calculate 3-way bookmaker overround: sum(1/odds) - 1.0."""
    if odds_1 is None or odds_x is None or odds_2 is None:
        return None
    if odds_1 <= 1.0 or odds_x <= 1.0 or odds_2 <= 1.0:
        return None
    raw_sum = (1.0 / odds_1) + (1.0 / odds_x) + (1.0 / odds_2)
    return max(0.0, raw_sum - 1.0)


def devig_probabilities_3way(
    odds_1: float | None,
    odds_x: float | None,
    odds_2: float | None,
) -> tuple[float | None, float | None, float | None]:
    """Compute fair (de-vigged) implied probabilities using proportional normalization."""
    if odds_1 is None or odds_x is None or odds_2 is None:
        return None, None, None
    if odds_1 <= 1.0 or odds_x <= 1.0 or odds_2 <= 1.0:
        return None, None, None
    imp_1, imp_x, imp_2 = 1.0 / odds_1, 1.0 / odds_x, 1.0 / odds_2
    total = imp_1 + imp_x + imp_2
    if total <= 0.0:
        return None, None, None
    return imp_1 / total, imp_x / total, imp_2 / total


def shannon_entropy_3way(p1: float | None, px: float | None, p2: float | None) -> float:
    """Compute Shannon entropy for the 3-way distribution (Home, Draw, Away)."""
    probs = [p for p in (p1, px, p2) if p is not None and p > 0]
    total = sum(probs)
    if total <= 0:
        return 0.0
    normalized = [p / total for p in probs]
    return -sum(p * math.log(p) for p in normalized)


def form_points_per_game(form_list: list[str] | tuple[str, ...]) -> tuple[float, float, float, float, float]:
    """Calculate (ppg, win_rate, draw_rate, loss_rate, games) from ['w','d','l'] letters."""
    if not form_list:
        return 0.0, 0.0, 0.0, 0.0, 0.0
    letters = [str(x).strip().lower()[:1] for x in form_list if str(x).strip()]
    games = len(letters)
    if games == 0:
        return 0.0, 0.0, 0.0, 0.0, 0.0
    wins = sum(1 for x in letters if x == "w")
    draws = sum(1 for x in letters if x == "d")
    losses = sum(1 for x in letters if x == "l")
    ppg = (3.0 * wins + 1.0 * draws) / games
    return ppg, wins / games, draws / games, losses / games, float(games)


# ---------------------------------------------------------------------------
# Football Feature Extractor
# ---------------------------------------------------------------------------

@dataclass
class FootballFeatures:
    """Typed container for complete pre-event football features."""
    # Context & Role
    is_home_dog: float
    dog_index: int
    favorite_index: int
    
    # 3-Way Forebet Probabilities
    forebet_dog_prob: float
    forebet_favorite_prob: float
    forebet_draw_prob: float
    forebet_prob_gap: float
    forebet_entropy: float
    draw_pressure_ratio: float
    favorite_dominance_ratio: float
    forebet_calls_dog: float
    
    # Pricing & De-Vigged Market Signals
    price_available: float
    dog_price: float | None
    favorite_price: float | None
    draw_price: float | None
    market_overround: float | None
    dog_fair_implied_prob: float | None
    favorite_fair_implied_prob: float | None
    draw_fair_implied_prob: float | None
    price_value_edge: float | None
    
    # Goal Expectancy & Predictions
    predicted_total_goals: float | None
    predicted_goal_diff_dog: float | None
    dog_pred_score: float | None
    fav_pred_score: float | None
    
    # Form & Points Per Game (PPG)
    dog_ppg: float
    favorite_ppg: float
    ppg_gap: float
    dog_win_rate: float
    favorite_win_rate: float
    dog_draw_rate: float
    favorite_draw_rate: float
    dog_recent_games: float
    
    # League Standings
    dog_rank: float | None
    favorite_rank: float | None
    rank_gap: float | None
    standings_pts_gap: float | None
    standings_gd_gap: float | None
    standings_ppg_gap: float | None
    
    # H2H History
    h2h_total_games: float
    h2h_dog_win_rate: float
    h2h_draw_rate: float
    h2h_dog_undefeated_rate: float
    h2h_has_dog_win: float
    
    # Multi-Market Cross Signals (Over/Under, BTTS, HT, Cards, Corners)
    over_25_prob: float | None
    under_25_prob: float | None
    btts_yes_prob: float | None
    btts_no_prob: float | None
    ht_dog_prob: float | None
    ht_draw_prob: float | None
    ht_fav_prob: float | None
    asian_handicap_line: float | None
    card_intensity: float | None
    dog_card_differential: float | None
    corner_intensity: float | None
    
    # Spatial & Environmental
    travel_distance_km: float | None
    dog_travel_distance: float | None
    fav_travel_distance: float | None
    weather_temperature: float | None

    # Tactical & Detail Match Averages
    dog_clean_sheets_avg: float | None = None
    fav_clean_sheets_avg: float | None = None
    clean_sheets_avg_gap: float | None = None
    dog_corners_avg: float | None = None
    fav_corners_avg: float | None = None
    corners_avg_gap: float | None = None
    dog_yellow_cards_avg: float | None = None
    fav_yellow_cards_avg: float | None = None
    yellow_cards_avg_gap: float | None = None
    dog_fouls_avg: float | None = None
    fav_fouls_avg: float | None = None
    fouls_avg_gap: float | None = None
    dog_tackles_avg: float | None = None
    fav_tackles_avg: float | None = None
    tackles_avg_gap: float | None = None
    dog_total_shots_avg: float | None = None
    fav_total_shots_avg: float | None = None
    total_shots_avg_gap: float | None = None
    dog_dangerous_attacks_avg: float | None = None
    fav_dangerous_attacks_avg: float | None = None
    dangerous_attacks_avg_gap: float | None = None
    dog_scored_avg: float | None = None
    fav_scored_avg: float | None = None
    dog_conceded_avg: float | None = None
    fav_conceded_avg: float | None = None
    net_goal_efficiency_gap: float | None = None

    # Legacy & Meta Signals
    legacy_robber_score: float = 0.0
    legacy_raw_confidence: float = 0.0

    def to_dict(self) -> dict[str, float]:
        """Produce a flat dictionary of numeric features with missing flags."""
        features: dict[str, float] = {
            "fb_is_home_dog": self.is_home_dog,
            "fb_forebet_dog_prob": self.forebet_dog_prob,
            "fb_forebet_favorite_prob": self.forebet_favorite_prob,
            "fb_forebet_draw_prob": self.forebet_draw_prob,
            "fb_forebet_prob_gap": self.forebet_prob_gap,
            "fb_forebet_entropy": self.forebet_entropy,
            "fb_draw_pressure_ratio": self.draw_pressure_ratio,
            "fb_favorite_dominance_ratio": self.favorite_dominance_ratio,
            "fb_forebet_calls_dog": self.forebet_calls_dog,
            "fb_price_available": self.price_available,
            "fb_dog_ppg": self.dog_ppg,
            "fb_favorite_ppg": self.favorite_ppg,
            "fb_ppg_gap": self.ppg_gap,
            "fb_dog_win_rate": self.dog_win_rate,
            "fb_favorite_win_rate": self.favorite_win_rate,
            "fb_dog_draw_rate": self.dog_draw_rate,
            "fb_favorite_draw_rate": self.favorite_draw_rate,
            "fb_dog_recent_games": self.dog_recent_games,
            "fb_h2h_total_games": self.h2h_total_games,
            "fb_h2h_dog_win_rate": self.h2h_dog_win_rate,
            "fb_h2h_draw_rate": self.h2h_draw_rate,
            "fb_h2h_dog_undefeated_rate": self.h2h_dog_undefeated_rate,
            "fb_h2h_has_dog_win": self.h2h_has_dog_win,
            "fb_legacy_robber_score": self.legacy_robber_score,
            "fb_legacy_raw_confidence": self.legacy_raw_confidence,
        }

        # Optional signals with explicit missingness flags
        optional_fields: list[tuple[str, float | None]] = [
            ("fb_dog_price", self.dog_price),
            ("fb_favorite_price", self.favorite_price),
            ("fb_draw_price", self.draw_price),
            ("fb_market_overround", self.market_overround),
            ("fb_dog_fair_implied_prob", self.dog_fair_implied_prob),
            ("fb_favorite_fair_implied_prob", self.favorite_fair_implied_prob),
            ("fb_draw_fair_implied_prob", self.draw_fair_implied_prob),
            ("fb_price_value_edge", self.price_value_edge),
            ("fb_predicted_total_goals", self.predicted_total_goals),
            ("fb_predicted_goal_diff_dog", self.predicted_goal_diff_dog),
            ("fb_dog_pred_score", self.dog_pred_score),
            ("fb_fav_pred_score", self.fav_pred_score),
            ("fb_dog_rank", self.dog_rank),
            ("fb_favorite_rank", self.favorite_rank),
            ("fb_rank_gap", self.rank_gap),
            ("fb_standings_pts_gap", self.standings_pts_gap),
            ("fb_standings_gd_gap", self.standings_gd_gap),
            ("fb_standings_ppg_gap", self.standings_ppg_gap),
            ("fb_over_25_prob", self.over_25_prob),
            ("fb_under_25_prob", self.under_25_prob),
            ("fb_btts_yes_prob", self.btts_yes_prob),
            ("fb_btts_no_prob", self.btts_no_prob),
            ("fb_ht_dog_prob", self.ht_dog_prob),
            ("fb_ht_draw_prob", self.ht_draw_prob),
            ("fb_ht_fav_prob", self.ht_fav_prob),
            ("fb_asian_handicap_line", self.asian_handicap_line),
            ("fb_card_intensity", self.card_intensity),
            ("fb_dog_card_differential", self.dog_card_differential),
            ("fb_corner_intensity", self.corner_intensity),
            ("fb_travel_distance_km", self.travel_distance_km),
            ("fb_dog_travel_distance", self.dog_travel_distance),
            ("fb_fav_travel_distance", self.fav_travel_distance),
            ("fb_weather_temperature", self.weather_temperature),
            ("fb_dog_clean_sheets_avg", self.dog_clean_sheets_avg),
            ("fb_fav_clean_sheets_avg", self.fav_clean_sheets_avg),
            ("fb_clean_sheets_avg_gap", self.clean_sheets_avg_gap),
            ("fb_dog_corners_avg", self.dog_corners_avg),
            ("fb_fav_corners_avg", self.fav_corners_avg),
            ("fb_corners_avg_gap", self.corners_avg_gap),
            ("fb_dog_yellow_cards_avg", self.dog_yellow_cards_avg),
            ("fb_fav_yellow_cards_avg", self.fav_yellow_cards_avg),
            ("fb_yellow_cards_avg_gap", self.yellow_cards_avg_gap),
            ("fb_dog_fouls_avg", self.dog_fouls_avg),
            ("fb_fav_fouls_avg", self.fav_fouls_avg),
            ("fb_fouls_avg_gap", self.fouls_avg_gap),
            ("fb_dog_tackles_avg", self.dog_tackles_avg),
            ("fb_fav_tackles_avg", self.fav_tackles_avg),
            ("fb_tackles_avg_gap", self.tackles_avg_gap),
            ("fb_dog_total_shots_avg", self.dog_total_shots_avg),
            ("fb_fav_total_shots_avg", self.fav_total_shots_avg),
            ("fb_total_shots_avg_gap", self.total_shots_avg_gap),
            ("fb_dog_dangerous_attacks_avg", self.dog_dangerous_attacks_avg),
            ("fb_fav_dangerous_attacks_avg", self.fav_dangerous_attacks_avg),
            ("fb_dangerous_attacks_avg_gap", self.dangerous_attacks_avg_gap),
            ("fb_dog_scored_avg", self.dog_scored_avg),
            ("fb_fav_scored_avg", self.fav_scored_avg),
            ("fb_dog_conceded_avg", self.dog_conceded_avg),
            ("fb_fav_conceded_avg", self.fav_conceded_avg),
            ("fb_net_goal_efficiency_gap", self.net_goal_efficiency_gap),
        ]

        for name, val in optional_fields:
            features[f"{name}_missing"] = 1.0 if val is None else 0.0
            features[name] = float(val) if val is not None else 0.0

        return features


def extract_football_features(
    event: EventSnapshot,
    candidate: RobberCandidate,
    h2h: H2HStats | None = None,
    recent_1: RecentForm | None = None,
    recent_2: RecentForm | None = None,
) -> FootballFeatures:
    """Extract complete, leak-safe FootballFeatures from pre-event snapshots and facets."""
    h2h = h2h or H2HStats()
    facets = event.pre_event_facets()
    
    dog = candidate.participant_index
    fav = 2 if dog == 1 else 1
    is_home_dog = 1.0 if dog == 1 else 0.0

    # 1X2 Probabilities
    p1 = event.probability_1 or 0.0
    px = event.draw_probability or 0.0
    p2 = event.probability_2 or 0.0
    dog_prob = p1 if dog == 1 else p2
    fav_prob = p2 if dog == 1 else p1
    draw_prob = px
    prob_gap = fav_prob - dog_prob
    entropy = shannon_entropy_3way(p1, px, p2)
    draw_pressure = draw_prob / (fav_prob + dog_prob) if (fav_prob + dog_prob) > 0 else 0.0
    dominance = fav_prob / max(0.01, dog_prob)
    calls_dog = 1.0 if event.forebet_pick == dog else 0.0

    # Prices & De-vigging
    dog_odds = event.odds(dog)
    fav_odds = event.odds(fav)
    draw_odds = _safe_float(facets.get("odds_draw") or facets.get("best_odd_X"))
    odds_1 = event.odds_1
    odds_2 = event.odds_2
    
    overround = calculate_overround(odds_1, draw_odds, odds_2)
    devig_1, devig_x, devig_2 = devig_probabilities_3way(odds_1, draw_odds, odds_2)
    
    dog_fair_prob = devig_1 if dog == 1 else devig_2
    fav_fair_prob = devig_2 if dog == 1 else devig_1
    draw_fair_prob = devig_x
    
    value_edge = (dog_prob - dog_fair_prob) if (dog_fair_prob is not None) else None

    # Predicted Scores & Totals
    host_sc = _safe_float(facets.get("host_sc_pr"))
    guest_sc = _safe_float(facets.get("guest_sc_pr"))
    total_goals = _safe_float(event.predicted_total or facets.get("goalsavg"))
    if total_goals is None and host_sc is not None and guest_sc is not None:
        total_goals = host_sc + guest_sc
        
    dog_sc = host_sc if dog == 1 else guest_sc
    fav_sc = guest_sc if dog == 1 else host_sc
    goal_diff_dog = (dog_sc - fav_sc) if (dog_sc is not None and fav_sc is not None) else None

    # Form & PPG
    raw_host_form = facets.get("host_form") or []
    raw_guest_form = facets.get("guest_form") or []
    host_ppg, host_wr, host_dr, host_lr, host_g = form_points_per_game(raw_host_form)
    guest_ppg, guest_wr, guest_dr, guest_lr, guest_g = form_points_per_game(raw_guest_form)

    # Fallback to recent_1/recent_2 if form array empty
    if host_g == 0 and recent_1 and recent_1.games > 0:
        host_wr = recent_1.win_rate or 0.0
        host_g = float(recent_1.games)
        host_ppg = host_wr * 3.0
    if guest_g == 0 and recent_2 and recent_2.games > 0:
        guest_wr = recent_2.win_rate or 0.0
        guest_g = float(recent_2.games)
        guest_ppg = guest_wr * 3.0

    dog_ppg = host_ppg if dog == 1 else guest_ppg
    fav_ppg = guest_ppg if dog == 1 else host_ppg
    dog_wr = host_wr if dog == 1 else guest_wr
    fav_wr = guest_wr if dog == 1 else host_wr
    dog_dr = host_dr if dog == 1 else guest_dr
    fav_dr = guest_dr if dog == 1 else host_dr
    dog_games = host_g if dog == 1 else guest_g

    # Standings
    pos_1 = _safe_float(facets.get("standings_1") or facets.get("host_pos") or facets.get("standings_1_rank"))
    pos_2 = _safe_float(facets.get("standings_2") or facets.get("guest_pos") or facets.get("standings_2_rank"))
    dog_rank = pos_1 if dog == 1 else pos_2
    fav_rank = pos_2 if dog == 1 else pos_1
    rank_gap = (fav_rank - dog_rank) if (dog_rank is not None and fav_rank is not None) else None

    pts_1 = _safe_float(facets.get("standings_1_pts"))
    pts_2 = _safe_float(facets.get("standings_2_pts"))
    pts_gap = (pts_1 - pts_2) if (pts_1 is not None and pts_2 is not None) else None
    if dog == 2 and pts_gap is not None:
        pts_gap = -pts_gap

    gd_1 = _safe_float(facets.get("standings_1_gd"))
    gd_2 = _safe_float(facets.get("standings_2_gd"))
    gd_gap = (gd_1 - gd_2) if (gd_1 is not None and gd_2 is not None) else None
    if dog == 2 and gd_gap is not None:
        gd_gap = -gd_gap

    gp_1 = _safe_float(facets.get("standings_1_gp"))
    gp_2 = _safe_float(facets.get("standings_2_gp"))
    standings_ppg_gap = None
    if pts_1 is not None and gp_1 and pts_2 is not None and gp_2:
        ppg_1 = pts_1 / gp_1
        ppg_2 = pts_2 / gp_2
        standings_ppg_gap = (ppg_1 - ppg_2) if dog == 1 else (ppg_2 - ppg_1)

    # H2H
    h2h_games = float(h2h.total_games or facets.get("h2h_total_games") or 0)
    h2h_dog_wins = float(h2h.wins(dog) or (facets.get("h2h_participant_1_wins") if dog == 1 else facets.get("h2h_participant_2_wins")) or 0)
    h2h_draws = float(facets.get("h2h_draws") or 0)
    h2h_wr = (h2h_dog_wins / h2h_games) if h2h_games > 0 else 0.0
    h2h_dr = (h2h_draws / h2h_games) if h2h_games > 0 else 0.0
    h2h_undefeated = ((h2h_dog_wins + h2h_draws) / h2h_games) if h2h_games > 0 else 0.0
    has_dog_win = 1.0 if h2h_dog_wins > 0 else 0.0

    # Multi-Market Cross Signals
    uo_over = _safe_float(facets.get("market_uo_pr_over") or facets.get("pr_over"))
    uo_under = _safe_float(facets.get("market_uo_pr_under") or facets.get("pr_under"))
    over_25_prob = (uo_over / 100.0) if uo_over is not None else None
    under_25_prob = (uo_under / 100.0) if uo_under is not None else None

    bts_gg = _safe_float(facets.get("market_bts_Pred_gg") or facets.get("Pred_gg"))
    bts_no = _safe_float(facets.get("market_bts_Pred_no_gg") or facets.get("Pred_no_gg"))
    btts_yes = (bts_gg / 100.0) if bts_gg is not None else None
    btts_no = (bts_no / 100.0) if bts_no is not None else None

    ht_1 = _safe_float(facets.get("market_ht_Pred_1_HT") or facets.get("Pred_1_HT"))
    ht_x = _safe_float(facets.get("market_ht_Pred_X_HT") or facets.get("Pred_X_HT"))
    ht_2 = _safe_float(facets.get("market_ht_Pred_2_HT") or facets.get("Pred_2_HT"))
    ht_dog = ((ht_1 if dog == 1 else ht_2) / 100.0) if (ht_1 is not None or ht_2 is not None) else None
    ht_fav = ((ht_2 if dog == 1 else ht_1) / 100.0) if (ht_1 is not None or ht_2 is not None) else None
    ht_draw = (ht_x / 100.0) if ht_x is not None else None

    ah_line = _safe_float(facets.get("market_ah_AH_type") or facets.get("AH_type"))
    
    card_avg = _safe_float(facets.get("market_cards_avg_cards") or facets.get("avg_cards"))
    host_cards = _safe_float(facets.get("market_cards_host_card_pred") or facets.get("host_card_pred"))
    guest_cards = _safe_float(facets.get("market_cards_guest_card_pred") or facets.get("guest_card_pred"))
    dog_card_diff = None
    if host_cards is not None and guest_cards is not None:
        dog_card_diff = (host_cards - guest_cards) if dog == 1 else (guest_cards - host_cards)

    corner_avg = _safe_float(facets.get("market_corners_avg_corners") or facets.get("avg_corners"))
    weather_temp = _safe_float(
        facets.get("weather_temperature_c") or facets.get("detail_weather_temperature_c")
        or facets.get("weather_high") or facets.get("weather_low")
    )

    # Spatial & Travel Distance
    dist_km = _safe_float(facets.get("travel_distance_km") or facets.get("detail_travel_distance_km"))
    dog_travel = (dist_km if dog == 2 else 0.0) if dist_km is not None else None
    fav_travel = (dist_km if fav == 2 else 0.0) if dist_km is not None else None

    # Tactical & Detail Match Averages
    def _facet_pair(metric_name: str) -> tuple[float | None, float | None, float | None]:
        m1 = _safe_float(facets.get(f"p1_{metric_name}") or facets.get(f"detail_p1_{metric_name}"))
        m2 = _safe_float(facets.get(f"p2_{metric_name}") or facets.get(f"detail_p2_{metric_name}"))
        dog_m = m1 if dog == 1 else m2
        fav_m = m2 if dog == 1 else m1
        diff = (dog_m - fav_m) if (dog_m is not None and fav_m is not None) else None
        return dog_m, fav_m, diff

    dog_cs, fav_cs, cs_gap = _facet_pair("clean_sheets_avg")
    dog_corn, fav_corn, corn_gap = _facet_pair("corners_avg")
    dog_yc, fav_yc, yc_gap = _facet_pair("yellow_cards_avg")
    dog_fls, fav_fls, fls_gap = _facet_pair("fouls_avg")
    dog_tkl, fav_tkl, tkl_gap = _facet_pair("tackles_avg")
    dog_shots, fav_shots, shots_gap = _facet_pair("total_shots_avg")
    dog_datt, fav_datt, datt_gap = _facet_pair("dangerous_attacks_avg")

    dog_sc_avg, fav_sc_avg, _ = _facet_pair("scored_avg")
    dog_conc_avg, fav_conc_avg, _ = _facet_pair("conceded_avg")
    net_eff_gap = None
    if dog_sc_avg is not None and dog_conc_avg is not None and fav_sc_avg is not None and fav_conc_avg is not None:
        net_eff_gap = (dog_sc_avg - dog_conc_avg) - (fav_sc_avg - fav_conc_avg)

    return FootballFeatures(
        is_home_dog=is_home_dog,
        dog_index=dog,
        favorite_index=fav,
        forebet_dog_prob=dog_prob,
        forebet_favorite_prob=fav_prob,
        forebet_draw_prob=draw_prob,
        forebet_prob_gap=prob_gap,
        forebet_entropy=entropy,
        draw_pressure_ratio=draw_pressure,
        favorite_dominance_ratio=dominance,
        forebet_calls_dog=calls_dog,
        price_available=1.0 if (dog_odds is not None and fav_odds is not None) else 0.0,
        dog_price=dog_odds,
        favorite_price=fav_odds,
        draw_price=draw_odds,
        market_overround=overround,
        dog_fair_implied_prob=dog_fair_prob,
        favorite_fair_implied_prob=fav_fair_prob,
        draw_fair_implied_prob=draw_fair_prob,
        price_value_edge=value_edge,
        predicted_total_goals=total_goals,
        predicted_goal_diff_dog=goal_diff_dog,
        dog_pred_score=dog_sc,
        fav_pred_score=fav_sc,
        dog_ppg=dog_ppg,
        favorite_ppg=fav_ppg,
        ppg_gap=dog_ppg - fav_ppg,
        dog_win_rate=dog_wr,
        favorite_win_rate=fav_wr,
        dog_draw_rate=dog_dr,
        favorite_draw_rate=fav_dr,
        dog_recent_games=dog_games,
        dog_rank=dog_rank,
        favorite_rank=fav_rank,
        rank_gap=rank_gap,
        standings_pts_gap=pts_gap,
        standings_gd_gap=gd_gap,
        standings_ppg_gap=standings_ppg_gap,
        h2h_total_games=h2h_games,
        h2h_dog_win_rate=h2h_wr,
        h2h_draw_rate=h2h_dr,
        h2h_dog_undefeated_rate=h2h_undefeated,
        h2h_has_dog_win=has_dog_win,
        over_25_prob=over_25_prob,
        under_25_prob=under_25_prob,
        btts_yes_prob=btts_yes,
        btts_no_prob=btts_no,
        ht_dog_prob=ht_dog,
        ht_draw_prob=ht_draw,
        ht_fav_prob=ht_fav,
        asian_handicap_line=ah_line,
        card_intensity=card_avg,
        dog_card_differential=dog_card_diff,
        corner_intensity=corner_avg,
        travel_distance_km=dist_km,
        dog_travel_distance=dog_travel,
        fav_travel_distance=fav_travel,
        weather_temperature=weather_temp,
        dog_clean_sheets_avg=dog_cs,
        fav_clean_sheets_avg=fav_cs,
        clean_sheets_avg_gap=cs_gap,
        dog_corners_avg=dog_corn,
        fav_corners_avg=fav_corn,
        corners_avg_gap=corn_gap,
        dog_yellow_cards_avg=dog_yc,
        fav_yellow_cards_avg=fav_yc,
        yellow_cards_avg_gap=yc_gap,
        dog_fouls_avg=dog_fls,
        fav_fouls_avg=fav_fls,
        fouls_avg_gap=fls_gap,
        dog_tackles_avg=dog_tkl,
        fav_tackles_avg=fav_tkl,
        tackles_avg_gap=tkl_gap,
        dog_total_shots_avg=dog_shots,
        fav_total_shots_avg=fav_shots,
        total_shots_avg_gap=shots_gap,
        dog_dangerous_attacks_avg=dog_datt,
        fav_dangerous_attacks_avg=fav_datt,
        dangerous_attacks_avg_gap=datt_gap,
        dog_scored_avg=dog_sc_avg,
        fav_scored_avg=fav_sc_avg,
        dog_conceded_avg=dog_conc_avg,
        fav_conceded_avg=fav_conc_avg,
        net_goal_efficiency_gap=net_eff_gap,
        legacy_robber_score=candidate.score,
        legacy_raw_confidence=candidate.raw_confidence,
    )


# ---------------------------------------------------------------------------
# Football-Specific Robber Detector
# ---------------------------------------------------------------------------

def detect_football_robber(
    event: EventSnapshot,
    h2h: H2HStats | None = None,
    recent_1: RecentForm | None = None,
    recent_2: RecentForm | None = None,
    config: RobberConfig | None = None,
) -> RobberCandidate | None:
    """Dedicated Football 1X2 Robber detector with draw-aware scoring."""
    config = config or RobberConfig()
    h2h = h2h or H2HStats()

    # Determine Underdog Identity in 3-Way Market
    dog_idx = 1
    basis = "lower_forebet_probability"
    if event.odds_1 is not None and event.odds_2 is not None:
        if event.odds_1 != event.odds_2:
            dog_idx = 1 if event.odds_1 > event.odds_2 else 2
            basis = "displayed_odds"
    elif event.forebet_pick in (1, 2):
        dog_idx = 2 if event.forebet_pick == 1 else 1
        basis = "opposite_forebet_pick"
    elif event.probability_1 is not None and event.probability_2 is not None:
        if event.probability_1 != event.probability_2:
            dog_idx = 1 if event.probability_1 < event.probability_2 else 2
            basis = "lower_forebet_probability"

    fav_idx = 2 if dog_idx == 1 else 1
    dog_odds = event.odds(dog_idx)
    fav_odds = event.odds(fav_idx)
    odds_avail = (dog_odds is not None and fav_odds is not None)

    score = 0.0
    reasons: list[str] = []

    # Home Dog Advantage Bonus
    if dog_idx == 1:
        score += 5.0
        reasons.append("Home Underdog Advantage (+5)")

    # Favorite Strength Factor
    if odds_avail and fav_odds is not None:
        if fav_odds <= 1.35:
            score += 15.0
            reasons.append(f"Heavy fav @{fav_odds:.2f}")
        elif fav_odds <= 1.55:
            score += 12.0
            reasons.append(f"Strong fav @{fav_odds:.2f}")
        elif fav_odds <= 1.75:
            score += 8.0
            reasons.append(f"Clear fav @{fav_odds:.2f}")
        else:
            score += 3.0
            reasons.append(f"Slight fav @{fav_odds:.2f}")

    # H2H Upset Factor
    if h2h.total_games >= config.min_h2h_games:
        wins = h2h.wins(dog_idx)
        rate = wins / h2h.total_games
        if rate >= config.underdog_win_threshold:
            score += 20.0
            reasons.append(f"H2H {round(rate * 100)}% ({wins}/{h2h.total_games})")
        elif wins > 0:
            score += 8.0
            reasons.append(f"Prior H2H win ({wins})")

    # Form & PPG Momentum
    recent = recent_1 if dog_idx == 1 else recent_2
    if recent and recent.games >= config.momentum_games:
        rate = (recent.wins / recent.games) if recent.games > 0 else 0.0
        if rate >= config.momentum_win_threshold:
            score += 15.0
            reasons.append(f"Hot form {recent.wins}W/{recent.games}G")
        elif rate >= 0.40:
            score += 8.0
            reasons.append(f"Solid form {recent.wins}W/{recent.games}G")

    # Tactical & Environmental Catalysts
    facets = event.pre_event_facets()
    dist = _safe_float(facets.get("travel_distance_km") or facets.get("detail_travel_distance_km"))
    if dist and dist >= 400.0 and dog_idx == 1:
        score += 5.0
        reasons.append(f"Fav Away Travel Fatigue ({int(dist)}km)")

    # Odds Value Factor
    if odds_avail and dog_odds is not None:
        if 2.50 <= dog_odds <= 5.50:
            score += 15.0
            reasons.append(f"Value @{dog_odds:.2f}")
        elif 2.00 <= dog_odds < 2.50:
            score += 10.0
            reasons.append(f"Playable @{dog_odds:.2f}")
        elif 5.50 < dog_odds <= 9.00:
            score += 8.0
            reasons.append(f"High payout @{dog_odds:.2f}")
        else:
            score += 4.0
            reasons.append(f"Longshot @{dog_odds:.2f}")
    else:
        reasons.append("Unpriced 3-way match")

    threshold = config.min_score if odds_avail else max(10.0, round(config.min_score * 0.55))
    if score < threshold:
        return None

    raw_conf = min(config.max_confidence, 46.0 + score * 0.55)
    raw_prob = raw_conf / 100.0
    implied = 1.0 / dog_odds if (odds_avail and dog_odds is not None) else None
    
    if odds_avail and dog_odds is not None and implied is not None:
        shrink = min(0.60, max(0.15, config.calibration_shrink))
        legacy_prob = implied + (raw_prob - implied) * shrink
        legacy_prob = min(config.calibration_max_probability, max(0.25, legacy_prob))
        legacy_conf = min(95.0, max(50.0, round(legacy_prob * 100.0)))
        ev = legacy_prob * dog_odds - 1.0
        advantage = legacy_prob - implied
        state = CandidateState.SHADOW_PRICED
    else:
        legacy_prob = raw_prob
        legacy_conf = min(95.0, max(50.0, round(raw_conf)))
        ev = None
        advantage = None
        state = CandidateState.SHADOW_UNPRICED

    return RobberCandidate(
        event_id=event.event_id,
        sport="football",
        participant_index=dog_idx,
        participant=event.participant(dog_idx),
        opponent=event.participant(fav_idx),
        score=score,
        reasons=reasons,
        raw_confidence=round(raw_conf, 3),
        legacy_confidence=legacy_conf,
        price=dog_odds,
        implied_probability=round(implied, 6) if implied is not None else None,
        legacy_probability=round(legacy_prob, 6),
        legacy_expected_value=round(ev, 6) if ev is not None else None,
        legacy_probability_advantage=round(advantage, 6) if advantage is not None else None,
        price_state=event.price_state,
        state=state,
        underdog_basis=basis,
        forebet_underdog_probability=event.probability(dog_idx),
        forebet_favorite_probability=event.probability(fav_idx),
    )
