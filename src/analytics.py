"""
analytics.py — Z-scores, VORP, schedule weighting, drop suggestions,
and recent form analysis.

Recent form (last N days):
  - Calculated from game logs (G, A, PP, S, PIM available per game)
  - HIT, BLK, FOW are NOT available at per-game level from the NHL API;
    their recent form is approximated from season per-game averages
  - recent_form_z = Z-score of per-game averages over the last N days
  - injury_flag   = consecutive games missed >= 3 (or Yahoo status not "")
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from .schedule import games_in_window, game_dates_in_window, light_nights
from .nhl_api  import detect_missed_games

CATS = ["G", "A", "FOW", "PIM", "PP", "S", "HIT", "BLK"]

# All 8 categories available per-game from the 3 isGame=true endpoints:
#   summary (G, A, PP, S, PIM) + realtime (HIT, BLK) + faceoffwins (FOW)
GAMELOG_AVAIL = ["G", "A", "PP", "S", "PIM", "HIT", "BLK", "FOW"]
GAMELOG_RATE  = []   # nothing needs a season-rate fallback

# Injury status codes considered "active concern" (show warning badge)
INJURY_WARN_CODES = {"IR", "IR-LT", "IL", "O", "DTD", "Q", "GTD"}


# ── Z-score engine ────────────────────────────────────────────────────────────

def calculate_z_scores(
    df: pd.DataFrame,
    weights: dict[str, float],
    universe_size: int = 350,
    suffix: str = "",              # e.g. "_recent" for recent-form columns
    per_game: bool = False,        # if True, normalise cats by GP first
) -> pd.DataFrame:
    """
    Compute per-category Z-scores using the top `universe_size` players
    (by points or by per-game points) as the reference distribution.

    Writes {cat}_z{suffix} columns and total_z{suffix} column.
    """
    df = df.copy()
    cats = [c for c in CATS if c in df.columns]

    if per_game:
        gp_col = df["gp"].clip(lower=1)
        work   = df[cats].div(gp_col, axis=0)
        rank_col = (df["G"] + df["A"]) / gp_col
    else:
        work     = df[cats]
        rank_col = df["points"]

    universe = work.loc[rank_col.nlargest(universe_size).index]

    for cat in cats:
        w = weights.get(cat, 1.0)
        if w == 0.0:
            df[f"{cat}_z{suffix}"] = 0.0
            continue
        mean = universe[cat].mean()
        std  = universe[cat].std()
        std  = std if std > 0 else 1.0
        df[f"{cat}_z{suffix}"] = (work[cat] - mean) / std * w

    z_cols = [f"{c}_z{suffix}" for c in cats]
    df[f"total_z{suffix}"] = df[[c for c in z_cols if c in df.columns]].sum(axis=1)
    return df


# ── VORP ──────────────────────────────────────────────────────────────────────

def apply_vorp(df: pd.DataFrame, forward_rank: int = 180, defense_rank: int = 80) -> pd.DataFrame:
    df = df.copy()
    forwards = df[df["position"] != "D"].sort_values("total_z", ascending=False)
    defence  = df[df["position"] == "D"].sort_values("total_z", ascending=False)
    f_base = forwards.iloc[forward_rank]["total_z"] if len(forwards) > forward_rank else 0.0
    d_base = defence.iloc[defense_rank]["total_z"]  if len(defence)  > defense_rank  else 0.0
    df["vorp"] = df.apply(
        lambda r: r["total_z"] - (d_base if r["position"] == "D" else f_base),
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
    df = df.copy()
    if today is None:
        today = date.today().isoformat()

    df["games_7d"]      = df["team"].apply(lambda t: games_in_window(schedule, t, today, short_window))
    df["games_14d"]     = df["team"].apply(lambda t: games_in_window(schedule, t, today, long_window))
    df["density_7d"]    = df["games_7d"]  / short_window
    df["density_14d"]   = df["games_14d"] / long_window
    df["value_7d"]      = df["total_z"]   * df["density_7d"]
    df["game_dates_7d"] = df["team"].apply(lambda t: game_dates_in_window(schedule, t, today, short_window))
    return df


# ── Recent form from game logs ────────────────────────────────────────────────

def add_recent_form(
    df: pd.DataFrame,
    game_logs: dict[int, list[dict]],   # {player_id: [{game_date, G, A, PP, S, PIM}]}
    schedule: list[dict],
    weights: dict[str, float],
    universe_size: int = 350,
    n_days: int = 14,
    today: str | None = None,
) -> pd.DataFrame:
    """
    Adds columns:
      recent_gp            – games played in last n_days
      recent_{cat}_pg      – per-game average in last n_days (GAMELOG_AVAIL cats)
      {cat}_recent_pg      – per-game from game log (or season rate for HIT/BLK/FOW)
      total_z_recent       – Z-score from recent per-game averages
      consecutive_missed   – most recent streak of missed team games
      missed_last_14d      – games missed vs team schedule in last 14 days
      injury_flag          – True if consecutive_missed >= 3
    """
    df = df.copy()
    if today is None:
        today = date.today().isoformat()

    cutoff = (date.fromisoformat(today) - timedelta(days=n_days)).isoformat()

    # --- per-player recent stats ---
    recent_rows: dict[int, dict] = {}
    missed_rows: dict[int, dict] = {}

    for _, row in df.iterrows():
        pid  = row["player_id"]
        team = row["team"]
        log  = game_logs.get(pid, [])

        # Filter to last n_days
        recent = [g for g in log if g.get("game_date", "") >= cutoff]
        rgp    = len(recent)

        # Per-game averages for all 8 categories (all available from game log)
        pg_avail = {}
        for cat in GAMELOG_AVAIL:
            total = sum(g.get(cat, 0) for g in recent)
            pg_avail[cat] = total / rgp if rgp > 0 else 0.0

        recent_rows[pid] = {"recent_gp": rgp, **{f"{cat}_rpg": v for cat, v in pg_avail.items()}}

        # Injury detection: team schedule in last 14 days
        team_dates = game_dates_in_window(schedule, team, cutoff, n_days)
        missed_rows[pid] = detect_missed_games(log, team_dates)

    # Attach to df
    for col in ["recent_gp"] + [f"{c}_rpg" for c in CATS]:
        df[col] = df["player_id"].map(lambda pid, c=col: recent_rows.get(pid, {}).get(c, 0.0))

    for col in ["consecutive_missed", "missed_last_14d", "injury_flag"]:
        df[col] = df["player_id"].map(lambda pid, c=col: missed_rows.get(pid, {}).get(c, 0))

    # --- Recent form Z-score ---
    # Build a temp df with per-game recent averages as the stat columns
    temp = df.copy()
    for cat in CATS:
        temp[cat] = df[f"{cat}_rpg"]

    # Only calculate for players with recent_gp >= 2 (avoid noise from 0-1 game samples)
    temp.loc[temp["recent_gp"] < 2, CATS] = np.nan

    temp = calculate_z_scores(temp, weights, universe_size=universe_size, suffix="_recent")
    df["total_z_recent"] = temp["total_z_recent"].round(3)

    return df


# ── Injury status string ──────────────────────────────────────────────────────

def injury_label(status: str, injury_note: str, injury_flag: bool) -> str:
    """Return a short human-readable status string for display."""
    if status in INJURY_WARN_CODES:
        note = f" — {injury_note}" if injury_note else ""
        return f"⚠️ {status}{note}"
    if injury_flag:
        return "🔴 Possible injury (missed 3+ games)"
    return "✅ Active"


# ── Master builder ────────────────────────────────────────────────────────────

def build_player_df(
    skaters:   list[dict],
    roster:    dict[int, dict],
    schedule:  list[dict],
    game_logs: dict[int, list[dict]],
    weights:   dict[str, float],
    cfg:       dict,
    today:     str | None = None,
) -> pd.DataFrame:
    df = pd.DataFrame(skaters)
    if df.empty:
        return df

    if today is None:
        today = date.today().isoformat()

    # Roster membership + injury status from Yahoo
    df["team_number"] = df["player_id"].map(lambda p: roster.get(p, {}).get("team_number", 0))
    df["fantasy_team"]= df["player_id"].map(lambda p: roster.get(p, {}).get("team_name", "Free Agent"))
    df["is_fa"]       = df["player_id"].map(lambda p: roster.get(p, {}).get("is_fa", True))
    df["status"]      = df["player_id"].map(lambda p: roster.get(p, {}).get("status", ""))
    df["injury_note"] = df["player_id"].map(lambda p: roster.get(p, {}).get("injury_note", ""))

    # Z-scores + VORP
    df = calculate_z_scores(df, weights, universe_size=cfg.get("universe_size", 350))
    df = apply_vorp(
        df,
        forward_rank=cfg.get("vorp", {}).get("forward_rank", 180),
        defense_rank=cfg.get("vorp", {}).get("defense_rank", 80),
    )

    # Schedule density
    df = add_schedule_density(
        df, schedule, today=today,
        short_window=cfg.get("schedule_windows", {}).get("short", 7),
        long_window =cfg.get("schedule_windows", {}).get("long",  14),
    )

    # Recent form + injury detection (from game logs)
    if game_logs:
        n_days = cfg.get("recent_form_days", 14)
        df = add_recent_form(
            df, game_logs, schedule, weights,
            universe_size=cfg.get("universe_size", 350),
            n_days=n_days, today=today,
        )
    else:
        for col in ["recent_gp", "consecutive_missed", "missed_last_14d",
                    "injury_flag", "total_z_recent"]:
            df[col] = 0

    # Unified injury label
    df["injury_status"] = df.apply(
        lambda r: injury_label(r["status"], r["injury_note"], bool(r["injury_flag"])),
        axis=1,
    )

    # Round display columns
    for col in ["total_z", "total_z_recent", "vorp", "value_7d"]:
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
    if today is None:
        today = date.today().isoformat()

    fa = df[df["is_fa"]].copy().sort_values("value_7d", ascending=False)

    ln = light_nights(schedule, today, short_window)
    fa["plays_light_night"] = fa["game_dates_7d"].apply(
        lambda dates: any(d in ln for d in dates)
    )

    cols = ["name", "team", "position", "gp", "status",
            "G", "A", "PP", "S", "HIT", "BLK", "PIM", "FOW",
            "total_z", "total_z_recent", "vorp",
            "games_7d", "games_14d", "value_7d", "plays_light_night",
            "consecutive_missed", "missed_last_14d", "injury_flag", "injury_status"]
    return fa[[c for c in cols if c in fa.columns]].head(top_n).reset_index(drop=True)


# ── Weak Link Auditor ─────────────────────────────────────────────────────────

def get_drop_suggestions(
    df: pd.DataFrame,
    my_team_number: int,
    drop_threshold: float = 0.5,
    top_fa_n: int = 10,
) -> pd.DataFrame:
    my_roster = df[df["team_number"] == my_team_number].copy()
    top_fa    = (df[df["is_fa"] & ~df["injury_flag"].astype(bool)]
                 .sort_values("value_7d", ascending=False)
                 .head(top_fa_n))

    suggestions = []
    for _, fa_row in top_fa.iterrows():
        for _, my_row in my_roster.iterrows():
            drop_score = fa_row["value_7d"] - my_row["value_7d"]
            if drop_score > drop_threshold:
                suggestions.append({
                    "Drop (Rostered)":   my_row["name"],
                    "Drop Status":       my_row.get("injury_status", ""),
                    "Add (Free Agent)":  fa_row["name"],
                    "Drop Z":            my_row["total_z"],
                    "Add Z":             fa_row["total_z"],
                    "Drop Z (Recent)":   my_row.get("total_z_recent", 0),
                    "Add Z (Recent)":    fa_row.get("total_z_recent", 0),
                    "Drop Games (7d)":   my_row["games_7d"],
                    "Add Games (7d)":    fa_row["games_7d"],
                    "Drop Value":        my_row["value_7d"],
                    "Add Value":         fa_row["value_7d"],
                    "Drop Score":        round(drop_score, 3),
                    "Drop Team":         my_row["team"],
                    "Add Team":          fa_row["team"],
                    "Drop Pos":          my_row["position"],
                    "Add Pos":           fa_row["position"],
                })

    result = pd.DataFrame(suggestions)
    if not result.empty:
        result = (result
                  .sort_values("Drop Score", ascending=False)
                  .drop_duplicates(subset=["Add (Free Agent)"])
                  .reset_index(drop=True))
    return result
