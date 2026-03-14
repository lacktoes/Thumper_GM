"""
3_Heatmap.py — Roster Heatmap (Gameday View)

Shows Thumpers roster players on a calendar grid for the next 7 days.
Each cell shows whether the player has a game that night, with a
Z-score strength overlay (colour intensity).

Rows = players (sorted by value_7d desc)
Cols = next 7 days
Cell colour intensity = player's total_z (green=high, grey=no game)
"""
import streamlit as st
import pandas as pd
import numpy as np
from datetime import date, timedelta

st.set_page_config(page_title="Heatmap — Thumpers GM", layout="wide")
st.title("📅 Roster Heatmap — Gameday View")

# ── Pull shared state ─────────────────────────────────────────────────────────

if "df" not in st.session_state:
    st.warning("Return to the home page to load data first.")
    st.stop()

df        = st.session_state["df"]
cfg       = st.session_state["cfg"]
schedule  = st.session_state["schedule"]
today_str = st.session_state["today_str"]
my_team   = cfg["my_team_number"]

# ── Build date range ──────────────────────────────────────────────────────────

today = date.fromisoformat(today_str)
days  = [today + timedelta(days=i) for i in range(7)]
day_strs = [d.isoformat() for d in days]
day_labels = [d.strftime("%a %b %-d") if hasattr(date, 'strftime') else d.strftime("%a %b %d")
              for d in days]

# Windows: try %-d (Linux), fall back to %d
try:
    day_labels = [d.strftime("%a %-d %b") for d in days]
except ValueError:
    day_labels = [d.strftime("%a %d %b") for d in days]

# ── Build game lookup: {team: set of date strings} ────────────────────────────

from collections import defaultdict
team_game_dates: dict[str, set] = defaultdict(set)
for g in schedule:
    if g["game_date"] in day_strs:
        team_game_dates[g["home_team"]].add(g["game_date"])
        team_game_dates[g["away_team"]].add(g["game_date"])

# ── Total NHL games per day (for light night detection) ───────────────────────

games_per_day: dict[str, int] = defaultdict(int)
for g in schedule:
    if g["game_date"] in day_strs:
        games_per_day[g["game_date"]] += 1

# ── My roster ────────────────────────────────────────────────────────────────

my = df[df["team_number"] == my_team].sort_values("value_7d", ascending=False)

if my.empty:
    st.warning("No roster data found. Sync your roster first (sidebar button on home page).")
    st.stop()

# ── Controls ─────────────────────────────────────────────────────────────────

show_z = st.checkbox("Show Z-score in cells", value=True)
show_cats = st.checkbox("Show stat breakdown below heatmap", value=False)

# ── Build heatmap HTML ───────────────────────────────────────────────────────

def z_to_colour(z: float, has_game: bool) -> str:
    """Map z-score to a background colour string."""
    if not has_game:
        return "#1e1e2e"  # dark, no game
    # Clamp z to [-2, 3] then map to green intensity
    clamped = max(-2.0, min(3.0, z))
    t = (clamped + 2.0) / 5.0  # 0→1
    # Interpolate #334 (low z) → #0d4 (high z)
    r = int(30  + t * 10)
    g = int(60  + t * 150)
    b = int(40  + t * 20)
    return f"rgb({r},{g},{b})"


def z_text_colour(z: float, has_game: bool) -> str:
    return "#888" if not has_game else "#eee"


# Build HTML table
header_cells = "".join(
    f'<th style="padding:6px 10px;font-size:11px;color:#aaa;'
    f'background:{"#2a2040" if games_per_day.get(d, 0) < 5 and games_per_day.get(d, 0) > 0 else "#111"}">'
    f'{lbl}{"<br>⭐" if games_per_day.get(d, 0) > 0 and games_per_day.get(d, 0) < 5 else ""}</th>'
    for d, lbl in zip(day_strs, day_labels)
)
header = f'<tr><th style="padding:6px 10px;font-size:11px;color:#aaa;text-align:left">Player</th>{header_cells}</tr>'

rows_html = []
for _, row in my.iterrows():
    team = row["team"]
    z    = row["total_z"]
    name_cell = (
        f'<td style="white-space:nowrap;padding:5px 10px;font-size:12px;'
        f'font-weight:600;color:#ddd">'
        f'{row["name"]} <span style="color:#888;font-weight:400">'
        f'({row["position"]}) z={z:.2f}</span></td>'
    )
    day_cells = []
    for d in day_strs:
        has_game = d in team_game_dates.get(team, set())
        bg  = z_to_colour(z, has_game)
        fg  = z_text_colour(z, has_game)
        label = team if has_game else "—"
        extra = " ⭐" if has_game and games_per_day.get(d, 99) < 5 else ""
        day_cells.append(
            f'<td style="text-align:center;padding:5px 8px;background:{bg};'
            f'color:{fg};font-size:11px;border-radius:4px">'
            f'{label}{extra}'
            f'{"<br><small>" + str(round(z,1)) + "</small>" if show_z and has_game else ""}'
            f'</td>'
        )
    rows_html.append(f'<tr>{name_cell}{"".join(day_cells)}</tr>')

table_html = (
    '<table style="border-collapse:separate;border-spacing:2px;width:100%">'
    f'<thead style="background:#111">{header}</thead>'
    f'<tbody>{"".join(rows_html)}</tbody>'
    '</table>'
)

st.markdown(
    f'<div style="overflow-x:auto">{table_html}</div>',
    unsafe_allow_html=True,
)

st.caption("⭐ = Light night (< 5 total NHL games). Colour intensity = Z-score strength.")

# ── Stat breakdown ────────────────────────────────────────────────────────────

if show_cats:
    st.subheader("Stat Breakdown")
    cat_cols = ["name", "position", "gp"] + cfg["categories"] + ["total_z", "vorp", "games_7d", "value_7d"]
    st.dataframe(
        my[[c for c in cat_cols if c in my.columns]].reset_index(drop=True),
        use_container_width=True,
        hide_index=True,
    )

# ── Games per day summary ─────────────────────────────────────────────────────

st.subheader("My Players Active Per Day")
active_counts = {}
for d in day_strs:
    active_counts[d] = sum(
        1 for _, row in my.iterrows()
        if d in team_game_dates.get(row["team"], set())
    )

summary_df = pd.DataFrame({
    "Date":        day_labels,
    "My Players":  [active_counts[d] for d in day_strs],
    "NHL Games":   [games_per_day.get(d, 0) for d in day_strs],
    "Light Night": ["⭐" if games_per_day.get(d, 99) < 5 and games_per_day.get(d, 0) > 0 else "" for d in day_strs],
})
st.dataframe(summary_df, use_container_width=True, hide_index=True)
