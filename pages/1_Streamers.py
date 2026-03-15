"""
1_Streamers.py — Streamer Recommender

Top free agents ranked by value_7d (total_z × schedule density).
Highlights light nights and flags injured players.
"""
import streamlit as st

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

from src.analytics import get_streamers
from src.schedule  import light_nights

# ── Controls ──────────────────────────────────────────────────────────────────

col1, col2, col3, col4 = st.columns(4)
top_n      = col1.number_input("Top N FAs", 10, 100, 30, 5)
pos_filter = col2.multiselect("Position", ["C", "L", "R", "D"], default=[])
min_games  = col3.slider("Min games (7d)", 0, 7, 2)
hide_injured = col4.checkbox("Hide injured/IR players", value=True)

# ── Light nights callout ──────────────────────────────────────────────────────

ln = light_nights(schedule, today_str, short_w)
if ln:
    st.info(
        f"**Light nights (next {short_w} days):** {', '.join(sorted(ln))}  \n"
        "⭐ = player has a game on a light night (less bench competition)."
    )

# ── Compute ───────────────────────────────────────────────────────────────────

streamers = get_streamers(df, schedule, today_str, short_w, top_n=int(top_n))

if pos_filter:
    streamers = streamers[streamers["position"].isin(pos_filter)]
if min_games:
    streamers = streamers[streamers["games_7d"] >= min_games]
if hide_injured:
    streamers = streamers[~streamers["injury_flag"].astype(bool)]

# ── Display ───────────────────────────────────────────────────────────────────

show = streamers.reset_index(drop=True).copy()
show.insert(0, "Rank", range(1, len(show) + 1))

# Highlight light-night rows
def _row_style(row):
    if row.get("plays_light_night"):
        return ["background-color: rgba(255,215,0,0.08)"] * len(row)
    return [""] * len(row)

display_cols = [
    "Rank", "name", "team", "position", "gp",
    "G", "A", "PP", "S", "HIT", "BLK", "PIM", "FOW",
    "total_z", "total_z_recent", "vorp",
    "games_7d", "games_14d", "value_7d",
    "plays_light_night", "injury_status",
    "consecutive_missed", "missed_last_14d",
]
st.dataframe(
    show[[c for c in display_cols if c in show.columns]]
        .style.apply(_row_style, axis=1),
    use_container_width=True,
    hide_index=True,
    column_config={
        "name":              st.column_config.TextColumn("Player"),
        "total_z":           st.column_config.NumberColumn("Z (Season)", format="%.3f"),
        "total_z_recent":    st.column_config.NumberColumn("Z (Recent)", format="%.3f"),
        "vorp":              st.column_config.NumberColumn("VORP", format="%.3f"),
        "value_7d":          st.column_config.NumberColumn("Value (7d)", format="%.3f"),
        "plays_light_night": st.column_config.CheckboxColumn("⭐ Light Night"),
        "injury_status":     st.column_config.TextColumn("Status"),
        "consecutive_missed":st.column_config.NumberColumn("Missed"),
        "missed_last_14d":   st.column_config.NumberColumn("Missed (14d)"),
    },
)

st.caption(
    f"Ranked by **Value (7d)** = Z-score × schedule density (next {short_w} days).  \n"
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
