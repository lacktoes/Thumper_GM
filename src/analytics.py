"""
analytics.py — Z-scores, VORP, schedule weighting, and drop suggestion logic.

Key functions:
  build_player_df()      → enriched DataFrame with z-scores, vorp, schedule density
  get_drop_suggestions() → compare my roster vs top FAs
  get_streamers()        → top FAs by schedule-adjusted value
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from .schedule import games_in_window, game_dates_in_window, light_nights

CATS = ["G", "A", "FOW", "PIM", "PP", "S", "HIT", "BLK"]


# ── Z-score engine ────────────────────────────────────────────────────────────

def calculate_z_scores(
    df: pd.DataFrame,
    weights: dict[str, float],
    universe_size: int = 350,
) -> pd.DataFrame:
    """
    Compute per-category z-scores using the top `universe_size` players
    (by points) as the reference distribution, then apply category weights.
    """
    df = df.copy()
    cats = [c for c in CATS if c in df.columns]

    # Build universe from top N by points
    universe = df.nlargest(universe_size, "points")

    for cat in cats:
        w = weights.get(cat, 1.0)
        if w == 0.0:
            df[f"{cat}_z"] = 0.0
            continue
        mean = universe[cat].mean()
        std  = universe[cat].std()
        std  = std if std > 0 else 1.0
        df[f"{cat}_z"] = (df[cat] - mean) / std * w

    z_cols = [f"{c}_z" for c in cats]
    df["total_z"] = df[z_cols].sum(axis=1)
    return df


# ── VORP ──────────────────────────────────────────────────────────────────────

def apply_vorp(
    df: pd.DataFrame,
    forward_rank: int = 180,
    defense_rank: int = 80,
) -> pd.DataFrame:
    """
    Subtract positional replacement level from total_z.
    Forwards = all non-D skaters. Defence = position == 'D'.
    """
    df = df.copy()

    forwards = df[df["position"] != "D"].sort_values("total_z", ascending=False)
    defence  = df[df["position"] == "D"].sort_values("total_z", ascending=False)

    f_baseline = forwards.iloc[forward_rank]["total_z"] if len(forwards) > forward_rank else 0.0
    d_baseline = defence.iloc[defense_rank]["total_z"]  if len(defence) > defense_rank  else 0.0

    df["vorp"] = df.apply(
        lambda r: r["total_z"] - (d_baseline if r["position"] == "D" else f_baseline),
        axis=1,
    )
    return df


# ── Schedule density ──────────────────────────────────────────────────────────

def add_schedule_density(
    df: pd.DataFrame,
    schedule: list[dict],
    today: str | None = None,
    short_window: int = 7,
    long_window:  int = 14,
) -> pd.DataFrame:
    """
    Add columns:
      games_7d, games_14d,
      density_7d  (games_7d / short_window, range [0,1]),
      density_14d (games_14d / long_window),
      value_7d    (total_z × density_7d)  ← primary ranking metric
      game_dates_7d  (list of dates as str, for heatmap)
    """
    df = df.copy()
    if today is None:
        today = date.today().isoformat()

    def _games(team, days):
        return games_in_window(schedule, team, today, days)

    def _dates(team, days):
        return game_dates_in_window(schedule, team, today, days)

    df["games_7d"]     = df["team"].apply(lambda t: _games(t, short_window))
    df["games_14d"]    = df["team"].apply(lambda t: _games(t, long_window))
    df["density_7d"]   = df["games_7d"]  / short_window
    df["density_14d"]  = df["games_14d"] / long_window
    df["value_7d"]     = df["total_z"]   * df["density_7d"]
    df["game_dates_7d"]= df["team"].apply(lambda t: _dates(t, short_window))

    return df


# ── Master builder ────────────────────────────────────────────────────────────

def build_player_df(
    skaters: list[dict],
    roster:  dict[int, dict],
    schedule: list[dict],
    weights: dict[str, float],
    cfg: dict,
    today: str | None = None,
) -> pd.DataFrame:
    """
    Full pipeline: raw skater dicts → enriched DataFrame ready for UI views.
    """
    df = pd.DataFrame(skaters)
    if df.empty:
        return df

    # Attach roster info
    df["team_number"] = df["player_id"].map(lambda pid: roster.get(pid, {}).get("team_number", 0))
    df["fantasy_team"]= df["player_id"].map(lambda pid: roster.get(pid, {}).get("team_name", "Free Agent"))
    df["is_fa"]       = df["player_id"].map(lambda pid: roster.get(pid, {}).get("is_fa", True))

    # Z-scores
    df = calculate_z_scores(df, weights, universe_size=cfg.get("universe_size", 350))

    # VORP
    df = apply_vorp(
        df,
        forward_rank=cfg.get("vorp", {}).get("forward_rank", 180),
        defense_rank=cfg.get("vorp", {}).get("defense_rank", 80),
    )

    # Schedule
    df = add_schedule_density(
        df, schedule, today=today,
        short_window=cfg.get("schedule_windows", {}).get("short", 7),
        long_window =cfg.get("schedule_windows", {}).get("long",  14),
    )

    # Round display columns
    for col in ["total_z", "vorp", "value_7d", "density_7d"]:
        if col in df.columns:
            df[col] = df[col].round(3)

    return df


# ── Streamer Recommender ──────────────────────────────────────────────────────

def get_streamers(
    df: pd.DataFrame,
    schedule: list[dict],
    today: str | None = None,
    short_window: int = 7,
    top_n: int = 30,
) -> pd.DataFrame:
    """
    Top free agents ranked by value_7d (total_z × schedule density).
    Adds `plays_light_night` flag (True if any game falls on a light night).
    """
    if today is None:
        today = date.today().isoformat()

    fa = df[df["is_fa"]].copy().sort_values("value_7d", ascending=False)

    ln = light_nights(schedule, today, short_window)
    fa["plays_light_night"] = fa["game_dates_7d"].apply(
        lambda dates: any(d in ln for d in dates)
    )

    cols = ["name", "team", "position", "gp",
            "G", "A", "PP", "S", "HIT", "BLK", "PIM", "FOW",
            "total_z", "vorp", "games_7d", "games_14d", "value_7d",
            "plays_light_night"]
    return fa[[c for c in cols if c in fa.columns]].head(top_n).reset_index(drop=True)


# ── Weak Link Auditor ─────────────────────────────────────────────────────────

def get_drop_suggestions(
    df: pd.DataFrame,
    my_team_number: int,
    drop_threshold: float = 0.5,
    top_fa_n: int = 10,
) -> pd.DataFrame:
    """
    Compare each rostered Thumpers player against the top `top_fa_n` FAs.
    Returns a DataFrame of potential drops, sorted by Drop Score descending.

    Drop Score = FA_value_7d - Rostered_value_7d
    Flag if Drop Score > drop_threshold.
    """
    my_roster = df[df["team_number"] == my_team_number].copy()
    top_fa    = df[df["is_fa"]].sort_values("value_7d", ascending=False).head(top_fa_n)

    suggestions = []
    for _, fa_row in top_fa.iterrows():
        for _, my_row in my_roster.iterrows():
            drop_score = fa_row["value_7d"] - my_row["value_7d"]
            if drop_score > drop_threshold:
                suggestions.append({
                    "Drop (Rostered)":  my_row["name"],
                    "Add (Free Agent)": fa_row["name"],
                    "Drop Z-Score":     my_row["total_z"],
                    "Add Z-Score":      fa_row["total_z"],
                    "Drop Games (7d)":  my_row["games_7d"],
                    "Add Games (7d)":   fa_row["games_7d"],
                    "Drop Value":       my_row["value_7d"],
                    "Add Value":        fa_row["value_7d"],
                    "Drop Score":       round(drop_score, 3),
                    "Drop Team":        my_row["team"],
                    "Add Team":         fa_row["team"],
                    "Drop Position":    my_row["position"],
                    "Add Position":     fa_row["position"],
                })

    result = pd.DataFrame(suggestions)
    if not result.empty:
        result = (result
                  .sort_values("Drop Score", ascending=False)
                  .drop_duplicates(subset=["Add (Free Agent)"])   # one best drop per FA
                  .reset_index(drop=True))
    return result
