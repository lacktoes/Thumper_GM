"""
1_Streamers.py — Streamer Recommender

Top free agents ranked by value_7d (total_z × schedule density).
Highlights light nights and flags injured players.
"""
import streamlit as st
from datetime import date

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

from src.analytics import get_streamers, per_game_display, STAT_CATS
from src.schedule  import light_nights

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

# ── Light nights callout ──────────────────────────────────────────────────────

ln = light_nights(schedule, today_str, short_w)
if ln:
    st.info(
        f"**Light nights (next {short_w} days):** {', '.join(sorted(ln))}  \n"
        "⭐ = player has a game on a light night (less bench competition)."
    )

# ── Compute ───────────────────────────────────────────────────────────────────

streamers = get_streamers(df, schedule, today_str, short_w, top_n=int(top_n), rank_by=rank_col)

if pos_filter:
    # Multi-position aware: player matches if any of their positions is in the filter
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

# ── Display ───────────────────────────────────────────────────────────────────

show = streamers.reset_index(drop=True).copy()
show.insert(0, "Rank", range(1, len(show) + 1))
if show_pg:
    show = per_game_display(show, STAT_CATS)
stat_fmt = "%.1f" if show_pg else "%d"

# Highlight light-night rows
def _row_style(row):
    if row.get("plays_light_night"):
        return ["background-color: rgba(255,215,0,0.08)"] * len(row)
    return [""] * len(row)

display_cols = [
    "Rank", "name", "team", "position", "gp",
    "G", "A", "PP", "S", "HIT", "BLK", "PIM", "FOW",
    "total_z", "total_z_recent", "vorp",
    "games_3d", "games_7d", "games_14d",
    "value_3d", "value_7d",
    "plays_light_night", "injury_status",
]
st.dataframe(
    show[[c for c in display_cols if c in show.columns]]
        .style.apply(_row_style, axis=1),
    use_container_width=True,
    hide_index=True,
    column_config={
        "name":              st.column_config.TextColumn("Player"),
        "gp":                st.column_config.NumberColumn("GP",         format="%d"),
        "G":                 st.column_config.NumberColumn("G",          format=stat_fmt),
        "A":                 st.column_config.NumberColumn("A",          format=stat_fmt),
        "PP":                st.column_config.NumberColumn("PP",         format=stat_fmt),
        "S":                 st.column_config.NumberColumn("S",          format=stat_fmt),
        "HIT":               st.column_config.NumberColumn("HIT",        format=stat_fmt),
        "BLK":               st.column_config.NumberColumn("BLK",        format=stat_fmt),
        "PIM":               st.column_config.NumberColumn("PIM",        format=stat_fmt),
        "FOW":               st.column_config.NumberColumn("FOW",        format=stat_fmt),
        "games_3d":          st.column_config.NumberColumn("Games 3d",   format="%d"),
        "games_7d":          st.column_config.NumberColumn("Games 7d",   format="%d"),
        "games_14d":         st.column_config.NumberColumn("Games 14d",  format="%d"),
        "total_z":           st.column_config.NumberColumn("Z (Season)", format="%.1f"),
        "total_z_recent":    st.column_config.NumberColumn("Z (Recent)", format="%.1f"),
        "vorp":              st.column_config.NumberColumn("VORP",       format="%.1f"),
        "value_3d":          st.column_config.NumberColumn("Value (3d)", format="%.1f"),
        "value_7d":          st.column_config.NumberColumn("Value (7d)", format="%.1f"),
        "plays_light_night": st.column_config.CheckboxColumn("⭐ Light Night"),
        "injury_status":     st.column_config.TextColumn("Status"),
    },
)

st.caption(
    f"Ranked by **Value ({window})** = per-game Z-score × games in window ÷ window length.  \n"
    "**Z (Recent)** uses last "
    f"{st.session_state.get('recent_days', 14)} days — all 8 categories per game.  \n"
    "⭐ = light night (< 5 total NHL games). Injured players hidden by default."
)

# ── Per-category Z breakdown ──────────────────────────────────────────────────

with st.expander("Per-category Z-score breakdown"):
    fa_full = df[df["is_fa"]].sort_values("value_7d", ascending=False).head(int(top_n))
    if pos_filter:
        fa_full = fa_full[fa_full["position"].isin(pos_filter)]
    z_cols = ["name", "position"] + [f"{c}_z" for c in cfg["categories"] if f"{c}_z" in df.columns]
    st.dataframe(fa_full[[c for c in z_cols if c in fa_full.columns]].reset_index(drop=True),
                 use_container_width=True, hide_index=True)

# ── Streamer Heatmap (calendar view) ──────────────────────────────────────────

st.divider()
show_heatmap = st.checkbox("📅 Show schedule heatmap for these streamers", value=False)

if show_heatmap and not streamers.empty:
    from datetime import timedelta
    from collections import defaultdict

    today = date.fromisoformat(today_str)
    days     = [today + timedelta(days=i) for i in range(7)]
    day_strs = [d.isoformat() for d in days]
    try:
        day_labels = [d.strftime("%a %#d %b") for d in days]
    except ValueError:
        day_labels = [d.strftime("%a %d %b") for d in days]

    team_game_dates: dict = defaultdict(set)
    games_per_day:   dict = defaultdict(int)
    for g in schedule:
        if g["game_date"] in day_strs:
            team_game_dates[g["home_team"]].add(g["game_date"])
            team_game_dates[g["away_team"]].add(g["game_date"])
            games_per_day[g["game_date"]] += 1

    def _z_bg(z: float, has_game: bool) -> str:
        if not has_game:
            return "#1a1a2e"
        clamped = max(-2.0, min(3.0, float(z) if z == z else 0))
        t = (clamped + 2.0) / 5.0
        r = int(20  + t * 15)
        g = int(55  + t * 160)
        b = int(35  + t * 20)
        return f"rgb({r},{g},{b})"

    header_cells = "".join(
        f'<th style="padding:6px 12px;font-size:11px;color:#aaa;'
        f'background:{"#2a2040" if 0 < games_per_day.get(d, 0) < 5 else "#111"}">'
        f'{lbl}{"<br>⭐" if 0 < games_per_day.get(d, 0) < 5 else ""}</th>'
        for d, lbl in zip(day_strs, day_labels)
    )
    header_row = (
        '<tr><th style="padding:6px 10px;text-align:left;color:#aaa;font-size:11px">Player</th>'
        f'{header_cells}</tr>'
    )

    show_z_hm = st.checkbox("Show Z in heatmap cells", value=True, key="hm_show_z")

    rows_html = []
    for _, row in streamers.iterrows():
        team = row["team"]
        z    = float(row.get("total_z", 0) or 0)

        name_cell = (
            f'<td style="white-space:nowrap;padding:5px 10px;font-size:12px;font-weight:600;color:#ddd">'
            f'{row["name"]} <span style="color:#777;font-weight:400">({row["position"]})</span></td>'
        )

        day_cells = []
        for d in day_strs:
            has_game = d in team_game_dates.get(team, set())
            bg    = _z_bg(z, has_game)
            light = 0 < games_per_day.get(d, 0) < 5
            label = team if has_game else "—"
            star  = " ⭐" if has_game and light else ""
            z_str = f"<br><small>{z:+.1f}</small>" if show_z_hm and has_game else ""
            day_cells.append(
                f'<td style="text-align:center;padding:5px 8px;background:{bg};'
                f'color:#eee;font-size:11px;border-radius:4px">'
                f'{label}{star}{z_str}</td>'
            )

        rows_html.append(f'<tr>{name_cell}{"".join(day_cells)}</tr>')

    table_html = (
        '<table style="border-collapse:separate;border-spacing:3px;width:100%">'
        f'<thead>{header_row}</thead>'
        f'<tbody>{"".join(rows_html)}</tbody>'
        '</table>'
    )
    st.markdown(f'<div style="overflow-x:auto">{table_html}</div>', unsafe_allow_html=True)
    st.caption("Colour = season Z-score. ⭐ = light night (< 5 NHL games).")
