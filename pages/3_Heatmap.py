"""
3_Heatmap.py — Roster Heatmap (Gameday View)

7-day calendar grid for Thumpers roster.
Cell colour = Z-score strength. Red border = injured player.
"""
import streamlit as st
import pandas as pd
from datetime import date, timedelta
from collections import defaultdict

st.set_page_config(page_title="Heatmap — Thumpers GM", layout="wide")
st.title("📅 Roster Heatmap — Gameday View")

if "df" not in st.session_state:
    st.warning("Return to the home page to load data first.")
    st.stop()

df        = st.session_state["df"]
cfg       = st.session_state["cfg"]
schedule  = st.session_state["schedule"]
today_str = st.session_state["today_str"]
my_team   = cfg["my_team_number"]

today = date.fromisoformat(today_str)
days  = [today + timedelta(days=i) for i in range(7)]
day_strs = [d.isoformat() for d in days]
try:
    day_labels = [d.strftime("%a %-d %b") for d in days]
except ValueError:
    day_labels = [d.strftime("%a %d %b") for d in days]

# ── Build lookups ─────────────────────────────────────────────────────────────

team_game_dates: dict[str, set] = defaultdict(set)
games_per_day:   dict[str, int] = defaultdict(int)
for g in schedule:
    if g["game_date"] in day_strs:
        team_game_dates[g["home_team"]].add(g["game_date"])
        team_game_dates[g["away_team"]].add(g["game_date"])
        games_per_day[g["game_date"]] += 1

my = df[df["team_number"] == my_team].sort_values("value_7d", ascending=False)

if my.empty:
    st.warning("No roster data. Sync your Yahoo roster (click '👥 Roster' on the home page).")
    st.stop()

# ── Controls ─────────────────────────────────────────────────────────────────

show_z    = st.checkbox("Show Z-score in game cells", value=True)
use_recent = st.checkbox("Colour by Recent Form Z (instead of season Z)", value=False)

# ── Heatmap HTML ─────────────────────────────────────────────────────────────

def z_bg(z: float, has_game: bool, is_injured: bool) -> str:
    if is_injured and has_game:
        return "#5a1a1a"   # dark red
    if is_injured:
        return "#2e1a1a"
    if not has_game:
        return "#1a1a2e"
    clamped = max(-2.0, min(3.0, z))
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
header = f'<tr><th style="padding:6px 10px;text-align:left;color:#aaa;font-size:11px">Player</th>{header_cells}</tr>'

rows_html = []
for _, row in my.iterrows():
    team       = row["team"]
    z          = float(row["total_z_recent"] if use_recent and "total_z_recent" in row else row["total_z"])
    is_injured = bool(row.get("injury_flag")) or str(row.get("status", "")) in {"IR", "IR-LT", "IL", "O"}
    inj_badge  = " ⚠️" if is_injured else ""

    name_cell = (
        f'<td style="white-space:nowrap;padding:5px 10px;font-size:12px;font-weight:600;'
        f'color:{"#ff6b6b" if is_injured else "#ddd"}">'
        f'{row["name"]}{inj_badge} '
        f'<span style="color:#777;font-weight:400">({row["position"]})</span></td>'
    )

    day_cells = []
    for d in day_strs:
        has_game = d in team_game_dates.get(team, set())
        bg = z_bg(z, has_game, is_injured)
        light = 0 < games_per_day.get(d, 0) < 5
        label = team if has_game else "—"
        star  = " ⭐" if has_game and light else ""
        z_str = f"<br><small>{z:+.1f}</small>" if show_z and has_game else ""
        day_cells.append(
            f'<td style="text-align:center;padding:5px 8px;background:{bg};'
            f'color:#eee;font-size:11px;border-radius:4px">'
            f'{label}{star}{z_str}</td>'
        )

    rows_html.append(f'<tr>{name_cell}{"".join(day_cells)}</tr>')

table = (
    '<table style="border-collapse:separate;border-spacing:3px;width:100%">'
    f'<thead>{header}</thead>'
    f'<tbody>{"".join(rows_html)}</tbody>'
    '</table>'
)
st.markdown(f'<div style="overflow-x:auto">{table}</div>', unsafe_allow_html=True)

legend = ("Colour = " + ("recent form " if use_recent else "season ") +
          "Z-score. 🔴/⚠️ = injured/IR. ⭐ = light night (< 5 NHL games).")
st.caption(legend)

st.divider()

# ── Active count summary ──────────────────────────────────────────────────────

st.subheader("Active Players Per Day")
summary_df = pd.DataFrame({
    "Date":        day_labels,
    "My Active":   [sum(1 for _, r in my.iterrows()
                        if d in team_game_dates.get(r["team"], set())) for d in day_strs],
    "NHL Games":   [games_per_day.get(d, 0) for d in day_strs],
    "Light Night": ["⭐" if 0 < games_per_day.get(d, 99) < 5 else "" for d in day_strs],
})
st.dataframe(summary_df, use_container_width=True, hide_index=True)

# ── Injury detail ─────────────────────────────────────────────────────────────

injured = my[my["injury_flag"].astype(bool) | my["status"].isin(["IR", "IR-LT", "IL", "O"])]
if not injured.empty:
    st.divider()
    st.subheader("⚠️ Injury / Availability Concerns")
    inj_cols = ["name", "team", "position", "status", "injury_note",
                "consecutive_missed", "missed_last_14d", "injury_status"]
    st.dataframe(injured[[c for c in inj_cols if c in injured.columns]].reset_index(drop=True),
                 use_container_width=True, hide_index=True)
