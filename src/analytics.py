"""
analytics.py — Z-scores, VORP, schedule weighting, drop suggestions,
and recent form analysis.

Recent form (last N days):
  - Calculated from game logs (G, A, PP, S, PIM available per game)
  - HIT, BLK, FOW are NOT available at per-game level from the NHL API;
    their recent form is approximated from season per-game averages
  - recent_form_z = Z-score of per-game averages over the last N days
  - injury_flag   = Yahoo status in INJURY_WARN_CODES (IR, O, DTD, etc.)
"""
from __future__ import annotations

import unicodedata
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .schedule import games_in_window, game_dates_in_window, light_nights


def _norm(name: str) -> str:
    """Strip accents, lowercase, alphanumeric only.  Stützle → stutzle, J.J. → jj."""
    nfkd = unicodedata.normalize("NFKD", str(name))
    ascii_only = nfkd.encode("ascii", "ignore").decode("ascii")
    return "".join(c for c in ascii_only.lower() if c.isalnum())


# Common first-name nicknames → canonical form.
# Applied as a prefix match on the normalized slug so we don't need to split on spaces.
_NICKNAME_MAP: dict[str, str] = {
    "alexandre": "alexander",
    "matthew":   "matthew",   # keep as-is but catch "matt" / "matty" below
    "jonathan":  "jonathan",  # keep; catch "jon" below
    "mikey":     "michael",
    "mike":      "michael",
    "matt":      "matthew",
    "matty":     "matthew",
    "jon":       "jonathan",
    "tony":      "anthony",
    "alex":      "alexander",
    "cam":       "cameron",
    "josh":      "joshua",
    "pat":       "patrick",
    "will":      "william",
    "zach":      "zachary",
    "andy":      "andrew",
    "nick":      "nicholas",
    "vince":     "vincent",
    "jake":      "jacob",
    "danny":     "daniel",
    "dan":       "daniel",
    "tommy":     "thomas",
    "tom":       "thomas",
    "rob":       "robert",
    "bobby":     "robert",
    "jeff":      "jeffrey",
    "chris":     "christopher",
    "stevie":    "steven",
    "steve":     "steven",
    "freddie":   "fredrick",
    "fred":      "fredrick",
}
# Sort longest-first so "mikey" is tried before "mike"
_NICKNAME_SORTED = sorted(_NICKNAME_MAP.items(), key=lambda x: -len(x[0]))


def _canon(slug: str) -> str:
    """Expand a first-name nickname in a normalized slug to its canonical form."""
    for nick, canon in _NICKNAME_SORTED:
        if slug.startswith(nick):
            rest = slug[len(nick):]
            if rest:          # must have a last-name component
                return canon + rest
    return slug


def _load_name_overrides() -> dict[str, str]:
    """
    Load data/name_overrides.yaml → {nhl_normalized_slug: yahoo_normalized_slug}.
    Returns empty dict if file missing.
    """
    path = Path(__file__).parent.parent / "data" / "name_overrides.yaml"
    if not path.exists():
        return {}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    # Normalize keys and values for robustness
    return {_norm(k): _norm(v) for k, v in data.items()}


CATS = ["G", "A", "FOW", "PIM", "PP", "S", "HIT", "BLK"]

# All 8 categories available per-game from the 3 isGame=true endpoints:
#   summary (G, A, PP, S, PIM) + realtime (HIT, BLK) + faceoffwins (FOW)
GAMELOG_AVAIL = ["G", "A", "PP", "S", "PIM", "HIT", "BLK", "FOW"]
GAMELOG_RATE  = []   # nothing needs a season-rate fallback

# Injury status codes considered "active concern" (show warning badge)
INJURY_WARN_CODES = {"IR", "IR-LT", "IR-LTD", "IL", "O", "DTD", "Q", "GTD", "NA", "SUSP"}


# ── Z-score engine ────────────────────────────────────────────────────────────

def calculate_z_scores(
    df: pd.DataFrame,
    weights: dict[str, float],
    universe_size: int = 350,
    suffix: str = "",              # e.g. "_recent" for recent-form columns
    per_game: bool = True,         # normalise by GP before scoring
    min_gp: int = 10,              # minimum GP to qualify for the reference universe
) -> pd.DataFrame:
    """
    Compute per-category Z-scores using the top `universe_size` players
    (ranked by per-game points among those with >= min_gp) as the reference distribution.

    All players are scored against that distribution, including those below min_gp
    (they simply extrapolate relative to the established mean/std).

    Writes {cat}_z{suffix} columns and total_z{suffix} column.
    """
    df = df.copy()
    cats = [c for c in CATS if c in df.columns]

    gp_col = df["gp"].clip(lower=1)
    if per_game:
        work     = df[cats].div(gp_col, axis=0)
        rank_col = (df["G"] + df["A"]) / gp_col
        eligible = df["gp"] >= min_gp
    else:
        work     = df[cats].copy()
        rank_col = df["points"]
        eligible = pd.Series(True, index=df.index)

    # Universe = top N qualified players by rank_col
    qualified_rank = rank_col[eligible]
    universe_idx   = qualified_rank.nlargest(universe_size).index
    universe       = work.loc[universe_idx]

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


# ── Display helpers ────────────────────────────────────────────────────────────

STAT_CATS = ["G", "A", "PP", "S", "HIT", "BLK", "PIM", "FOW"]


def per_game_display(df: pd.DataFrame, cats: list[str] | None = None) -> pd.DataFrame:
    """
    Return a copy of df with stat columns divided by GP, rounded to 1 decimal.
    Used by pages to toggle between season totals and per-game averages.
    """
    cats = cats or STAT_CATS
    result = df.copy()
    gp = result["gp"].clip(lower=1)
    for cat in cats:
        if cat in result.columns:
            result[cat] = (result[cat] / gp).round(1)
    return result


# ── VORP ──────────────────────────────────────────────────────────────────────

def apply_vorp(df: pd.DataFrame, forward_rank: int = 180, defense_rank: int = 80) -> pd.DataFrame:
    """
    Compute VORP per player. Multi-position players (e.g. "C,LW" or "D,LW") get
    the maximum VORP across every position group they are eligible for.
    """
    df = df.copy()

    _FWD = {"C", "LW", "RW", "W", "F"}

    def _has_fwd(pos: str) -> bool:
        return bool({p.strip() for p in str(pos).split(",")} & _FWD)

    def _has_def(pos: str) -> bool:
        return "D" in {p.strip() for p in str(pos).split(",")}

    fwd_pool = df[df["position"].apply(_has_fwd)].sort_values("total_z", ascending=False)
    def_pool = df[df["position"].apply(_has_def)].sort_values("total_z", ascending=False)

    f_base = fwd_pool.iloc[forward_rank]["total_z"] if len(fwd_pool) > forward_rank else 0.0
    d_base = def_pool.iloc[defense_rank]["total_z"]  if len(def_pool)  > defense_rank  else 0.0

    def _vorp(pos: str, z: float) -> float:
        options = []
        if _has_fwd(pos):
            options.append(z - f_base)
        if _has_def(pos):
            options.append(z - d_base)
        return max(options) if options else z - f_base

    df["vorp"] = df.apply(lambda r: _vorp(r["position"], r["total_z"]), axis=1)
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

    df["games_3d"]      = df["team"].apply(lambda t: games_in_window(schedule, t, today, 3))
    df["games_7d"]      = df["team"].apply(lambda t: games_in_window(schedule, t, today, short_window))
    df["games_14d"]     = df["team"].apply(lambda t: games_in_window(schedule, t, today, long_window))
    df["density_3d"]    = df["games_3d"]  / 3
    df["density_7d"]    = df["games_7d"]  / short_window
    df["density_14d"]   = df["games_14d"] / long_window
    df["value_3d"]      = df["total_z"]   * df["density_3d"]
    df["value_7d"]      = df["total_z"]   * df["density_7d"]
    df["game_dates_7d"] = df["team"].apply(lambda t: game_dates_in_window(schedule, t, today, short_window))
    df["game_dates_3d"] = df["team"].apply(lambda t: game_dates_in_window(schedule, t, today, 3))
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
      {cat}_rpg            – per-game average in last n_days (all 8 GAMELOG_AVAIL cats)
      total_z_recent       – Z-score from recent per-game averages
    """
    df = df.copy()
    if today is None:
        today = date.today().isoformat()

    cutoff = (date.fromisoformat(today) - timedelta(days=n_days)).isoformat()

    # --- per-player recent stats ---
    recent_rows: dict[int, dict] = {}

    for _, row in df.iterrows():
        pid  = row["player_id"]
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

    # Attach to df
    for col in ["recent_gp"] + [f"{c}_rpg" for c in CATS]:
        df[col] = df["player_id"].map(lambda pid, c=col: recent_rows.get(pid, {}).get(c, 0.0))

    # --- Recent form Z-score ---
    # Build a temp df with per-game recent averages as the stat columns
    temp = df.copy()
    for cat in CATS:
        temp[cat] = df[f"{cat}_rpg"]

    # Only calculate for players with recent_gp >= 2 (avoid noise from 0-1 game samples)
    temp.loc[temp["recent_gp"] < 2, CATS] = np.nan

    # The _rpg columns are already per-game averages, so per_game=False here.
    # Use recent_gp as the GP proxy so min_gp applies to the recent window.
    temp["gp"] = temp["recent_gp"].clip(lower=1)
    temp = calculate_z_scores(temp, weights, universe_size=universe_size,
                              suffix="_recent", per_game=False, min_gp=2)
    df["total_z_recent"] = temp["total_z_recent"].round(1)

    return df


# ── Injury status string ──────────────────────────────────────────────────────

def injury_label(status: str, injury_note: str) -> str:
    """Return a short human-readable status string for display."""
    if status in INJURY_WARN_CODES:
        note = f" — {injury_note}" if injury_note else ""
        return f"⚠️ {status}{note}"
    return "✅ Active"


# ── Master builder ────────────────────────────────────────────────────────────

def build_player_df(
    skaters:     list[dict],
    roster:      dict[int, dict],
    schedule:    list[dict],
    game_logs:   dict[int, list[dict]],
    weights:     dict[str, float],
    cfg:         dict,
    today:       str | None = None,
    fa_positions: dict[str, str] | None = None,  # {yahoo_name: display_position}
) -> pd.DataFrame:
    df = pd.DataFrame(skaters)
    if df.empty:
        return df

    if today is None:
        today = date.today().isoformat()

    # Roster membership + injury status from Yahoo
    # Resolution layers (in priority order):
    #   1. Exact normalized slug  (handles accents via NFKD)
    #   2. Canonical first-name   (Mike→Michael, Matt→Matthew, Jon→Jonathan, etc.)
    #   3. data/name_overrides.yaml (Mathew→Matthew, Evgenii→Evgeny, etc.)
    _overrides = _load_name_overrides()   # {nhl_slug: yahoo_slug}

    _name_roster: dict[str, dict] = {}
    for v in roster.values():
        if not v.get("name"):
            continue
        slug  = _norm(v["name"])
        canon = _canon(slug)
        _name_roster[slug]  = v
        if canon != slug:
            _name_roster[canon] = v

    # Add override aliases: NHL slug → same Yahoo entry as the override target
    for nhl_slug, yahoo_slug in _overrides.items():
        if yahoo_slug in _name_roster:
            _name_roster[nhl_slug] = _name_roster[yahoo_slug]

    def _rlookup(n: str) -> dict:
        slug = _norm(n)
        return (
            _name_roster.get(slug)
            or _name_roster.get(_canon(slug))
            or {}
        )

    df["team_number"] = df["name"].map(lambda n: _rlookup(n).get("team_number", 0))
    df["fantasy_team"]= df["name"].map(lambda n: _rlookup(n).get("team_name", "Free Agent"))
    df["is_fa"]       = df["name"].map(lambda n: bool(_rlookup(n).get("is_fa", True)))

    # Injury status: try direct player_id lookup first (bypasses name-matching failures),
    # then fall back to the name-based roster entry.
    # Both Yahoo Fantasy and the NHL Stats API use the same numeric player IDs.
    def _status_lookup(pid: int, name: str) -> dict:
        direct = roster.get(pid) or roster.get(int(pid))
        if direct and (direct.get("status") or direct.get("injury_note")):
            return direct
        return _rlookup(name)

    df["status"]      = df.apply(lambda r: _status_lookup(r["player_id"], r["name"]).get("status", ""), axis=1)
    df["injury_note"] = df.apply(lambda r: _status_lookup(r["player_id"], r["name"]).get("injury_note", ""), axis=1)

    # Override NHL position with Yahoo's multi-position string when available
    # Yahoo uses "LW", "RW", "C,LW", "D" etc. — richer than NHL's single code
    _yahoo_pos = df["name"].map(lambda n: _rlookup(n).get("yahoo_position", ""))

    # For players without a yahoo_position in the roster cache (mostly FAs),
    # try the fa_positions dict using the same normalized name matching.
    if fa_positions:
        _fa_lookup: dict[str, str] = {}
        for yn, ypos in fa_positions.items():
            slug  = _norm(yn)
            canon = _canon(slug)
            _fa_lookup[slug]  = ypos
            if canon != slug:
                _fa_lookup[canon] = ypos
        # Also add override aliases so e.g. "Matthew Barzal" finds "Mathew Barzal"
        for nhl_slug, yahoo_slug in _overrides.items():
            if yahoo_slug in _fa_lookup and nhl_slug not in _fa_lookup:
                _fa_lookup[nhl_slug] = _fa_lookup[yahoo_slug]

        def _fa_pos(n: str, cur: str) -> str:
            if cur:
                return cur
            slug = _norm(n)
            return _fa_lookup.get(slug) or _fa_lookup.get(_canon(slug)) or cur

        _yahoo_pos = _yahoo_pos.combine(
            df["name"],
            lambda cur, n: _fa_pos(n, cur),
        )

    df["position"] = _yahoo_pos.where(_yahoo_pos != "", df["position"])

    # Z-scores + VORP  (per-game rates, universe = top N players with ≥10 GP)
    df = calculate_z_scores(df, weights, universe_size=cfg.get("universe_size", 350),
                            per_game=True, min_gp=cfg.get("min_gp_universe", 10))
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
        for col in ["recent_gp", "total_z_recent"]:
            df[col] = 0

    # injury_flag driven solely by Yahoo status codes
    df["injury_flag"] = df["status"].isin(INJURY_WARN_CODES)

    # Unified injury label
    df["injury_status"] = df.apply(
        lambda r: injury_label(r["status"], r["injury_note"]),
        axis=1,
    )

    # Round display columns
    for col in ["total_z", "total_z_recent", "vorp", "value_3d", "value_7d"]:
        if col in df.columns:
            df[col] = df[col].round(1)

    return df


# ── Goalie builder ────────────────────────────────────────────────────────────

GOALIE_CATS = ["W", "SVP", "GAA", "SO"]   # SVP = save % as decimal (0.920)


def build_goalie_df(
    goalies:      list[dict],
    roster:       dict[int, dict],
    schedule:     list[dict],
    today:        str | None = None,
) -> pd.DataFrame:
    """
    Build a DataFrame of NHL goalies with roster membership and schedule density.

    Added columns beyond raw stats:
      team_number, fantasy_team, is_fa, status, injury_note, injury_flag
      games_3d, games_7d, games_14d
      W_pg, SO_pg  — per-game rates for Poisson projection
      SVP, GAA     — already per-game rates by nature
    """
    df = pd.DataFrame(goalies)
    if df.empty:
        return df

    if today is None:
        today = date.today().isoformat()

    _overrides    = _load_name_overrides()
    _name_roster: dict[str, dict] = {}
    for v in roster.values():
        if not v.get("name"):
            continue
        slug  = _norm(v["name"])
        canon = _canon(slug)
        _name_roster[slug]  = v
        if canon != slug:
            _name_roster[canon] = v
    for nhl_slug, yahoo_slug in _overrides.items():
        if yahoo_slug in _name_roster:
            _name_roster[nhl_slug] = _name_roster[yahoo_slug]

    def _rlookup(n: str) -> dict:
        slug = _norm(n)
        return _name_roster.get(slug) or _name_roster.get(_canon(slug)) or {}

    def _status_lookup(pid: int, name: str) -> dict:
        direct = roster.get(pid) or roster.get(int(pid))
        if direct and (direct.get("status") or direct.get("injury_note")):
            return direct
        return _rlookup(name)

    df["team_number"]  = df["name"].map(lambda n: _rlookup(n).get("team_number", 0))
    df["fantasy_team"] = df["name"].map(lambda n: _rlookup(n).get("team_name", "Free Agent"))
    df["is_fa"]        = df["name"].map(lambda n: bool(_rlookup(n).get("is_fa", True)))
    df["status"]       = df.apply(lambda r: _status_lookup(r["player_id"], r["name"]).get("status", ""), axis=1)
    df["injury_note"]  = df.apply(lambda r: _status_lookup(r["player_id"], r["name"]).get("injury_note", ""), axis=1)
    df["injury_flag"]  = df["status"].isin(INJURY_WARN_CODES)
    df["injury_status"] = df.apply(lambda r: injury_label(r["status"], r["injury_note"]), axis=1)

    df["games_3d"]  = df["team"].apply(lambda t: games_in_window(schedule, t, today, 3))
    df["games_7d"]  = df["team"].apply(lambda t: games_in_window(schedule, t, today, 7))
    df["games_14d"] = df["team"].apply(lambda t: games_in_window(schedule, t, today, 14))

    gp = df["gp"].clip(lower=1)
    df["W_pg"]  = (df["W"]  / gp).round(3)
    df["SO_pg"] = (df["SO"] / gp).round(3)

    return df


# ── Streamer Recommender ──────────────────────────────────────────────────────

def get_streamers(
    df: pd.DataFrame,
    schedule: list[dict],
    today: str | None = None,
    short_window: int = 7,
    top_n: int = 30,
    rank_by: str = "value_7d",   # "value_3d" or "value_7d"
) -> pd.DataFrame:
    if today is None:
        today = date.today().isoformat()

    fa = df[df["is_fa"]].copy()

    # Compute 3d values if not present (stale cache fallback)
    if "value_3d" not in fa.columns or "games_3d" not in fa.columns:
        fa["games_3d"] = fa["team"].apply(lambda t: games_in_window(schedule, t, today, 3))
        fa["value_3d"] = (fa["total_z"] * fa["games_3d"] / 3).round(1)
        fa["game_dates_3d"] = fa["team"].apply(
            lambda t: game_dates_in_window(schedule, t, today, 3)
        )

    sort_col = rank_by if rank_by in fa.columns else "value_7d"
    fa = fa.sort_values(sort_col, ascending=False)

    # Light nights based on active window
    window_days = 3 if rank_by == "value_3d" else short_window
    ln = light_nights(schedule, today, window_days)
    dates_col = "game_dates_3d" if rank_by == "value_3d" else "game_dates_7d"
    if dates_col in fa.columns:
        fa["plays_light_night"] = fa[dates_col].apply(
            lambda dates: any(d in ln for d in dates)
        )
    else:
        fa["plays_light_night"] = False

    cols = ["name", "team", "position", "gp", "status",
            "G", "A", "PP", "S", "HIT", "BLK", "PIM", "FOW",
            "total_z", "total_z_recent", "vorp",
            "games_3d", "games_7d", "games_14d",
            "value_3d", "value_7d", "plays_light_night",
            "injury_flag", "injury_status"]
    return fa[[c for c in cols if c in fa.columns]].head(top_n).reset_index(drop=True)


# ── Weak Link Auditor ─────────────────────────────────────────────────────────

def get_drop_suggestions(
    df: pd.DataFrame,
    my_team_number: int,
    drop_threshold: float = 0.5,
    top_fa_n: int = 10,
    position_match: bool = True,
) -> pd.DataFrame:
    """
    For each rostered player, find the best available FA who shares at least one
    position with them (when position_match=True). Returns one row per rostered
    player where a better FA exists above the threshold, sorted by Drop Score.
    """
    my_roster = df[df["team_number"] == my_team_number].copy()

    # Wider FA pool so position matching has enough candidates
    fa_pool = (df[df["is_fa"] & ~df["injury_flag"].astype(bool)]
               .sort_values("value_7d", ascending=False)
               .head(top_fa_n * 4))

    def _positions(pos_str: str) -> set[str]:
        return {p.strip() for p in str(pos_str).split(",") if p.strip()}

    suggestions = []
    for _, my_row in my_roster.iterrows():
        my_pos = _positions(my_row["position"])

        best_score  = -999.0
        best_fa_row = None

        for _, fa_row in fa_pool.iterrows():
            if position_match:
                fa_pos = _positions(fa_row["position"])
                if not (my_pos & fa_pos):
                    continue
            drop_score = fa_row["value_7d"] - my_row["value_7d"]
            if drop_score > best_score:
                best_score  = drop_score
                best_fa_row = fa_row

        if best_fa_row is not None and best_score > drop_threshold:
            suggestions.append({
                "Drop (Rostered)":  my_row["name"],
                "Pos":              my_row["position"],
                "Drop Value":       round(float(my_row["value_7d"]), 1),
                "Drop Z":           round(float(my_row["total_z"]), 1),
                "Drop Status":      my_row.get("injury_status", ""),
                "Add (Free Agent)": best_fa_row["name"],
                "FA Pos":           best_fa_row["position"],
                "Add Value":        round(float(best_fa_row["value_7d"]), 1),
                "Add Z":            round(float(best_fa_row["total_z"]), 1),
                "Add Games (7d)":   int(best_fa_row["games_7d"]),
                "Drop Score":       round(best_score, 1),
            })

    result = pd.DataFrame(suggestions)
    if not result.empty:
        result = result.sort_values("Drop Score", ascending=False).reset_index(drop=True)
    return result
