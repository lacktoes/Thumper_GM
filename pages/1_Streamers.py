"""
1_Streamers.py — Streamer Recommender

Top free agents ranked by value_7d (total_z × schedule density).
Row colours:
  🔴 red    = Yahoo injury flag (IR / O / DTD / etc.)
  🟡 yellow = low recent participation (played < 60% of team's last N games)
              — could be returning from injury, AHL yo-yo, or regular scratch.
              Investigate before pickup.
Heatmap retains ⭐ light-night markers.
"""
import os
import streamlit as st
from datetime import date, timedelta
from collections import defaultdict

st.set_page_config(page_title="Streamers — Thumpers GM", layout="wide")
st.title("🔥 Streamer Recommender")

if "df" not in st.session_state:
    st.warning("Return to the home page to load data first.")
    st.stop()

df        = st.session_state["df"]
cfg       = st.session_state["cfg"]
schedule  = st.session_state["schedule"]
today_str = st.session_state["today_str"]
short_w   = cfg.get("schedule_windows", {}).get("short", 7)

from src.analytics      import get_streamers, per_game_display, STAT_CATS, INJURY_WARN_CODES
from src.schedule       import light_nights
from src.nhl_api        import SEASON_START
from src.cache          import latest_game_log_date
from src.yahoo_fantasy  import fetch_injured_player_status

_LOGO = lambda t: f"https://assets.nhle.com/logos/nhl/svg/{t}_light.svg"

# ── Controls ──────────────────────────────────────────────────────────────────

col1, col2, col3, col4, col5, col6, col7 = st.columns(7)
top_n        = col1.number_input("Top N FAs", 10, 100, 30, 5)
window       = col2.radio("Rank by", ["3-day", "7-day"], index=1, horizontal=True)
pos_filter   = col3.multiselect("Position", ["C", "LW", "RW", "D"], default=[])
min_gp       = col4.number_input("Min GP (season)", 0, 82, 10, 5)
min_games    = col5.slider("Min games (window)", 0, 7, 2)
hide_injured = col6.checkbox("Hide injured/IR players", value=True)
show_pg      = col7.checkbox("Per game avg", value=False, help="Show per-game averages instead of season totals")

rank_col = "value_3d" if window == "3-day" else "value_7d"

# ── Compute ───────────────────────────────────────────────────────────────────

streamers = get_streamers(df, schedule, today_str, short_w, top_n=int(top_n), rank_by=rank_col)

if pos_filter:
    streamers = streamers[
        streamers["position"].apply(
            lambda p: bool({x.strip() for x in str(p).split(",")} & set(pos_filter))
        )
    ]
if min_gp:
    streamers = streamers[streamers["gp"] >= min_gp]
games_filter_col = "games_3d" if window == "3-day" else "games_7d"
if min_games and games_filter_col in streamers.columns:
    streamers = streamers[streamers[games_filter_col] >= min_games]
if hide_injured:
    streamers = streamers[~streamers["injury_flag"].astype(bool)]

# ── FA injury status (Yahoo omits this from roster fetches) ──────────────────
# Fetch IR/NA player statuses once per session and patch into streamers df so
# injured FAs (e.g. Draisaitl after being dropped) get injury_flag = True.

_league_key = os.environ.get("YAHOO_LEAGUE_KEY", "")
if not _league_key:
    try:
        _league_key = st.secrets.get("YAHOO_LEAGUE_KEY", "")
    except Exception:
        pass

if _league_key and "injured_player_status" not in st.session_state:
    try:
        with st.spinner("Fetching FA injury status from Yahoo…"):
            st.session_state["injured_player_status"] = fetch_injured_player_status(_league_key)
    except Exception:
        st.session_state["injured_player_status"] = {}

_injured_status: dict = st.session_state.get("injured_player_status", {})

# Patch injury_flag for any FA whose player_id appears in the injured list
if _injured_status:
    def _patch_injury(row):
        if row["injury_flag"]:
            return True
        pid = row.get("player_id")
        if pid and pid in _injured_status:
            status, _ = _injured_status[pid]
            return status in INJURY_WARN_CODES
        return False
    streamers["injury_flag"] = streamers.apply(_patch_injury, axis=1)
    # Re-apply hide_injured filter now that flags are accurate
    if hide_injured:
        streamers = streamers[~streamers["injury_flag"].astype(bool)]

# ── Scratch-risk flag: low recent participation vs expected rate ───────────────
# Only meaningful when game logs have actually been fetched (recent_gp populated).
# Flags players whose recent game rate is <50% of their own season rate.

_logs_available  = latest_game_log_date() is not None
recent_days_val  = st.session_state.get("recent_days", 14)
cutoff_start     = (date.fromisoformat(today_str) - timedelta(days=recent_days_val)).isoformat()
season_start_str = SEASON_START

team_recent_games: dict = defaultdict(int)
team_season_games: dict = defaultdict(int)
for g in schedule:
    gd = g["game_date"]
    if season_start_str <= gd < today_str:
        team_season_games[g["home_team"]] += 1
        team_season_games[g["away_team"]] += 1
    if cutoff_start <= gd < today_str:
        team_recent_games[g["home_team"]] += 1
        team_recent_games[g["away_team"]] += 1

def _scratch_risk(row) -> bool:
    """
    True if player's recent game rate is <50% of their own season rate.
    Returns False immediately if game logs haven't been fetched yet —
    avoids flagging everyone when recent_gp is 0 across the board.
    """
    if not _logs_available:
        return False
    if row.get("injury_flag"):
        return False
    team     = row.get("team", "")
    t_recent = team_recent_games.get(team, 0)
    t_season = team_season_games.get(team, 0)
    if t_recent < 4 or t_season < 10:
        return False
    player_gp   = float(row.get("gp", 0) or 0)
    season_rate = player_gp / t_season          # fraction of team games player usually appears in
    expected    = season_rate * t_recent        # expected appearances in recent window
    if expected < 1.0:                          # not expected to play much anyway — skip
        return False
    rgp = row.get("recent_gp", 0)
    if rgp != rgp:                              # NaN guard
        rgp = 0.0
    return float(rgp) < expected * 0.5         # playing at less than half their expected pace

streamers["_scratch_risk"] = streamers.apply(_scratch_risk, axis=1)

# ── Streamer Table ────────────────────────────────────────────────────────────

show = streamers.reset_index(drop=True).copy()
show.insert(0, "Rank", range(1, len(show) + 1))
if show_pg:
    show = per_game_display(show, STAT_CATS)
stat_fmt = "%.1f" if show_pg else "%d"

display_cols = [
    "Rank", "name", "team", "position", "gp",
    "G", "A", "PP", "S", "HIT", "BLK", "PIM", "FOW",
    "total_z", "total_z_recent", "vorp",
    "games_3d", "games_7d", "games_14d",
    "value_3d", "value_7d",
    "injury_status",
]

# Attach flag columns so _row_style can read them, then hide from display
_STYLE_FLAGS = ["_inj_", "_scratch_"]
show_display = show[[c for c in display_cols if c in show.columns]].copy()
show_display["_inj_"]     = show["injury_flag"].values
show_display["_scratch_"] = show["_scratch_risk"].values

def _row_style(row):
    if row.get("_inj_"):
        return ["background-color: rgba(220,38,38,0.18)"] * len(row)
    if row.get("_scratch_"):
        return ["background-color: rgba(234,179,8,0.14)"] * len(row)
    return [""] * len(row)

st.dataframe(
    show_display.style.apply(_row_style, axis=1).hide(subset=_STYLE_FLAGS, axis=1),
    use_container_width=True,
    hide_index=True,
    column_config={
        "name":           st.column_config.TextColumn("Player",      width="medium"),
        "team":           st.column_config.TextColumn("Team",        width="small"),
        "position":       st.column_config.TextColumn("Pos",         width="small"),
        "gp":             st.column_config.NumberColumn("GP",        format="%d",     width="small"),
        "G":              st.column_config.NumberColumn("G",         format=stat_fmt, width="small"),
        "A":              st.column_config.NumberColumn("A",         format=stat_fmt, width="small"),
        "PP":             st.column_config.NumberColumn("PP",        format=stat_fmt, width="small"),
        "S":              st.column_config.NumberColumn("S",         format=stat_fmt, width="small"),
        "HIT":            st.column_config.NumberColumn("HIT",       format=stat_fmt, width="small"),
        "BLK":            st.column_config.NumberColumn("BLK",       format=stat_fmt, width="small"),
        "PIM":            st.column_config.NumberColumn("PIM",       format=stat_fmt, width="small"),
        "FOW":            st.column_config.NumberColumn("FOW",       format=stat_fmt, width="small"),
        "games_3d":       st.column_config.NumberColumn("G3d",       format="%d",     width="small"),
        "games_7d":       st.column_config.NumberColumn("G7d",       format="%d",     width="small"),
        "games_14d":      st.column_config.NumberColumn("G14d",      format="%d",     width="small"),
        "total_z":        st.column_config.NumberColumn("Z",         format="%.1f",   width="small"),
        "total_z_recent": st.column_config.NumberColumn("Z Rec",     format="%.1f",   width="small"),
        "vorp":           st.column_config.NumberColumn("VORP",      format="%.1f",   width="small"),
        "value_3d":       st.column_config.NumberColumn("Val 3d",    format="%.1f",   width="small"),
        "value_7d":       st.column_config.NumberColumn("Val 7d",    format="%.1f",   width="small"),
        "injury_status":  st.column_config.TextColumn("Status",      width="small"),
    },
)

st.divider()

# ── Schedule Heatmap ──────────────────────────────────────────────────────────

show_heatmap = st.checkbox("📅 Show schedule heatmap", value=True)

if show_heatmap and not streamers.empty:
    today    = date.fromisoformat(today_str)
    days     = [today + timedelta(days=i) for i in range(7)]
    day_strs = [d.isoformat() for d in days]
    try:
        day_labels = [d.strftime("%a %#d %b") for d in days]
    except ValueError:
        day_labels = [d.strftime("%a %d %b") for d in days]

    # Index schedule: store (away, home) tuple per (team, date)
    team_game_dates: dict = defaultdict(set)
    games_per_day:   dict = defaultdict(int)
    game_matchup:    dict = {}   # (team, date) → (away, home)
    for g in schedule:
        if g["game_date"] in day_strs:
            away = g["away_team"]
            home = g["home_team"]
            team_game_dates[away].add(g["game_date"])
            team_game_dates[home].add(g["game_date"])
            game_matchup[(away, g["game_date"])] = (away, home)
            game_matchup[(home, g["game_date"])] = (away, home)
            games_per_day[g["game_date"]] += 1

    def _z_bg(z: float, has_game: bool) -> str:
        """Red (low Z) → dark neutral (Z≈0) → green (high Z). Dark navy when no game."""
        if not has_game:
            return "#0f172a"
        clamped = max(-2.5, min(3.0, float(z) if z == z else 0.0))
        if clamped <= 0:
            t = (clamped + 2.5) / 2.5
            r = int(153 + t * (51  - 153))
            g = int(27  + t * (65  - 27))
            b = int(27  + t * (85  - 27))
        else:
            t = clamped / 3.0
            r = int(51  + t * (20  - 51))
            g = int(65  + t * (160 - 65))
            b = int(85  + t * (40  - 85))
        return f"rgb({r},{g},{b})"

    header_cells = "".join(
        f'<th style="padding:6px 12px;font-size:11px;color:#aaa;text-align:center;'
        f'background:{"#2a2040" if 0 < games_per_day.get(d, 0) < 5 else "#111"}">'
        f'{lbl}{"<br>⭐" if 0 < games_per_day.get(d, 0) < 5 else ""}</th>'
        for d, lbl in zip(day_strs, day_labels)
    )
    header_row = (
        '<tr>'
        '<th style="padding:6px 10px;text-align:left;color:#aaa;font-size:11px;min-width:180px">Player</th>'
        f'{header_cells}'
        '</tr>'
    )

    rows_html = []
    for _, row in streamers.iterrows():
        team = row["team"]
        z    = float(row.get("total_z", 0) or 0)

        # Player name cell — team logo inline
        name_cell = (
            f'<td style="white-space:nowrap;padding:5px 10px;font-size:12px;font-weight:600;color:#ddd">'
            f'<img src="{_LOGO(team)}" width="16" height="16" '
            f'style="vertical-align:middle;margin-right:5px">'
            f'{row["name"]} '
            f'<span style="color:#777;font-weight:400">({row["position"]})</span>'
            f'</td>'
        )

        day_cells = []
        for d in day_strs:
            has_game = d in team_game_dates.get(team, set())
            bg       = _z_bg(z, has_game)
            light    = 0 < games_per_day.get(d, 0) < 5
            star     = " ⭐" if has_game and light else ""

            if has_game:
                away, home = game_matchup.get((team, d), (team, ""))
                matchup_html = (
                    f'<img src="{_LOGO(away)}" width="14" height="14" style="vertical-align:middle">'
                    f'<span style="color:#aaa;font-size:10px"> @ </span>'
                    f'<img src="{_LOGO(home)}" width="14" height="14" style="vertical-align:middle">'
                    f'{star}'
                )
            else:
                matchup_html = '—'

            day_cells.append(
                f'<td style="text-align:center;padding:5px 8px;background:{bg};'
                f'color:#eee;font-size:11px;border-radius:4px;white-space:nowrap">'
                f'{matchup_html}</td>'
            )

        rows_html.append(f'<tr>{name_cell}{"".join(day_cells)}</tr>')

    table_html = (
        '<table style="border-collapse:separate;border-spacing:3px;width:100%">'
        f'<thead>{header_row}</thead>'
        f'<tbody>{"".join(rows_html)}</tbody>'
        '</table>'
    )
    st.markdown(f'<div style="overflow-x:auto">{table_html}</div>', unsafe_allow_html=True)

# ── Legend & light nights ─────────────────────────────────────────────────────

ln = light_nights(schedule, today_str, short_w)
if ln:
    st.info(
        f"**Light nights (next {short_w} days):** {', '.join(sorted(ln))}  \n"
        "⭐ = player has a game on a light night (less bench competition)."
    )

st.caption(
    f"Ranked by **Value ({window})** = per-game Z-score × games in window ÷ window length.  \n"
    "**Z Rec** uses last "
    f"{st.session_state.get('recent_days', 14)} days — all 8 categories per game.  \n"
    "🔴 **Red row** = Yahoo injury designation (IR / O / DTD).  \n"
    "🟡 **Yellow row** = played <60% of team's last games — possible AHL yo-yo, lineup scratch, "
    "or return from injury. Investigate before picking up.  \n"
    "Heatmap: 🟥 red = low Z, ⬛ dark = neutral, 🟩 green = high Z. ⭐ = light night (< 5 games)."
)

with st.expander("Per-category Z-score breakdown"):
    fa_full = df[df["is_fa"]].sort_values("value_7d", ascending=False).head(int(top_n))
    if pos_filter:
        fa_full = fa_full[fa_full["position"].isin(pos_filter)]
    z_cols = ["name", "position"] + [f"{c}_z" for c in cfg["categories"] if f"{c}_z" in df.columns]
    st.dataframe(fa_full[[c for c in z_cols if c in fa_full.columns]].reset_index(drop=True),
                 use_container_width=True, hide_index=True)
