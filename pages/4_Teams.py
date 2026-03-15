"""
4_Teams.py — NHL Team Schedule Density

Shows how many games each NHL team plays over the next 3, 7, and 14 days,
ranked so the most favourable streaming targets are at the top.
"""
import streamlit as st
import pandas as pd
from datetime import date, timedelta

st.set_page_config(page_title="Team Schedules — Thumpers GM", layout="wide")
st.title("📆 NHL Team Schedule Density")

if "schedule" not in st.session_state:
    st.warning("Return to the home page to load data first.")
    st.stop()

schedule  = st.session_state["schedule"]
today_str = st.session_state["today_str"]
today     = date.fromisoformat(today_str)

from src.schedule import games_in_window, game_dates_in_window, NHL_TEAMS

# ── Build team schedule table ──────────────────────────────────────────────────

rows = []
for team in sorted(NHL_TEAMS):
    g3  = games_in_window(schedule, team, today_str, 3)
    g7  = games_in_window(schedule, team, today_str, 7)
    g14 = games_in_window(schedule, team, today_str, 14)
    dates3  = game_dates_in_window(schedule, team, today_str, 3)
    dates7  = game_dates_in_window(schedule, team, today_str, 7)
    rows.append({
        "Team":      team,
        "Games (3d)":  g3,
        "Games (7d)":  g7,
        "Games (14d)": g14,
        "Next 3 days":  ", ".join(
            date.fromisoformat(d).strftime("%a %#d") for d in dates3
        ) if dates3 else "—",
        "Next 7 days":  ", ".join(
            date.fromisoformat(d).strftime("%a %#d") for d in dates7
        ) if dates7 else "—",
    })

team_df = pd.DataFrame(rows)

# ── Controls ──────────────────────────────────────────────────────────────────

col1, col2 = st.columns([1, 3])
sort_by = col1.radio("Sort by", ["Games (3d)", "Games (7d)", "Games (14d)"], index=1, horizontal=True)

team_df = team_df.sort_values(sort_by, ascending=False).reset_index(drop=True)
team_df.insert(0, "Rank", range(1, len(team_df) + 1))

# ── Colour helper ──────────────────────────────────────────────────────────────

def _colour_games(val):
    if not isinstance(val, (int, float)):
        return ""
    if val >= 4:
        return "background-color: rgba(0,200,100,0.25)"
    if val == 3:
        return "background-color: rgba(180,220,80,0.20)"
    if val == 2:
        return "background-color: rgba(255,200,0,0.15)"
    return ""

# ── Display ───────────────────────────────────────────────────────────────────

st.dataframe(
    team_df.style
        .map(_colour_games, subset=["Games (3d)", "Games (7d)", "Games (14d)"])
        .format({"Games (3d)": "%d", "Games (7d)": "%d", "Games (14d)": "%d"}),
    use_container_width=True,
    hide_index=True,
    column_config={
        "Rank":        st.column_config.NumberColumn("Rank",       format="%d"),
        "Team":        st.column_config.TextColumn("Team"),
        "Games (3d)":  st.column_config.NumberColumn("Games (3d)",  format="%d"),
        "Games (7d)":  st.column_config.NumberColumn("Games (7d)",  format="%d"),
        "Games (14d)": st.column_config.NumberColumn("Games (14d)", format="%d"),
        "Next 3 days": st.column_config.TextColumn("Next 3 days"),
        "Next 7 days": st.column_config.TextColumn("Next 7 days"),
    },
)

st.caption(
    "Green = 4+ games in window. Yellow = 3 games. White = 2 or fewer.  \n"
    f"Schedule start date: **{today_str}** (adjustable on the home page sidebar)."
)

# ── Back-to-back / 3-in-3 callout ─────────────────────────────────────────────

st.divider()
st.subheader("Back-to-backs & 3-in-3 (next 7 days)")
st.caption("Teams with heavy short-burst workloads — useful for targeting or avoiding players.")

b2b_rows = []
for team in sorted(NHL_TEAMS):
    dates = game_dates_in_window(schedule, team, today_str, 7)
    if len(dates) < 2:
        continue
    d_objs = [date.fromisoformat(d) for d in dates]

    back_to_backs = sum(
        1 for i in range(len(d_objs) - 1)
        if (d_objs[i + 1] - d_objs[i]).days == 1
    )

    three_in_three = sum(
        1 for i in range(len(d_objs) - 2)
        if (d_objs[i + 2] - d_objs[i]).days <= 2
    )

    if back_to_backs or three_in_three:
        b2b_rows.append({
            "Team":          team,
            "Games (7d)":    len(dates),
            "Back-to-backs": back_to_backs,
            "3-in-3":        three_in_three,
            "Dates":         ", ".join(date.fromisoformat(d).strftime("%a %#d") for d in dates),
        })

if b2b_rows:
    b2b_df = pd.DataFrame(b2b_rows).sort_values("Back-to-backs", ascending=False).reset_index(drop=True)
    st.dataframe(b2b_df, use_container_width=True, hide_index=True,
                 column_config={
                     "Back-to-backs": st.column_config.NumberColumn("B2B", format="%d"),
                     "3-in-3":        st.column_config.NumberColumn("3-in-3", format="%d"),
                     "Games (7d)":    st.column_config.NumberColumn("Games (7d)", format="%d"),
                 })
else:
    st.info("No back-to-backs in the next 7 days.")
