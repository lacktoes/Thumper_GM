"""
1_Streamers.py — Streamer Recommender

Shows the top available Free Agents ranked by:
  value_7d = total_z × schedule_density_7d

Highlights players appearing on "Light Nights" — days with few NHL games
where your opponent is more likely to have a bench slot open.
"""
import streamlit as st
import pandas as pd
from src.analytics import get_streamers
from src.schedule  import light_nights

st.set_page_config(page_title="Streamers — Thumpers GM", layout="wide")
st.title("🔥 Streamer Recommender")

# ── Pull shared state ─────────────────────────────────────────────────────────

if "df" not in st.session_state:
    st.warning("Return to the home page to load data first.")
    st.stop()

df        = st.session_state["df"]
cfg       = st.session_state["cfg"]
schedule  = st.session_state["schedule"]
today_str = st.session_state["today_str"]
short_w   = cfg.get("schedule_windows", {}).get("short", 7)

# ── Controls ──────────────────────────────────────────────────────────────────

col1, col2, col3 = st.columns(3)
top_n     = col1.number_input("Show top N free agents", 10, 100, 30, 5)
pos_filter = col2.multiselect("Filter by position", ["C", "L", "R", "D"], default=[])
min_games  = col3.slider("Min games in next 7 days", 0, 7, 2)

# ── Compute ───────────────────────────────────────────────────────────────────

streamers = get_streamers(df, schedule, today_str, short_w, top_n=int(top_n))

if pos_filter:
    streamers = streamers[streamers["position"].isin(pos_filter)]

if min_games:
    streamers = streamers[streamers["games_7d"] >= min_games]

# ── Light nights callout ──────────────────────────────────────────────────────

ln = light_nights(schedule, today_str, short_w, threshold=5)
if ln:
    st.info(
        f"**Light nights in the next {short_w} days:** {', '.join(sorted(ln))}  \n"
        "Players marked ⭐ have a game on at least one of these nights."
    )

# ── Display ───────────────────────────────────────────────────────────────────

def _fmt(row):
    star = "⭐" if row.get("plays_light_night") else ""
    return star

display_cols = [
    "name", "team", "position", "gp",
    "G", "A", "PP", "S", "HIT", "BLK", "PIM", "FOW",
    "total_z", "vorp", "games_7d", "games_14d", "value_7d", "plays_light_night",
]
show = streamers[[c for c in display_cols if c in streamers.columns]].copy()
show.insert(0, "Rank", range(1, len(show) + 1))

st.dataframe(
    show.style.apply(
        lambda row: ["background-color: #1a3a1a" if row.get("plays_light_night") else "" for _ in row],
        axis=1,
    ).format({
        "total_z": "{:.3f}",
        "vorp":    "{:.3f}",
        "value_7d":"{:.3f}",
        "density_7d": "{:.0%}" if "density_7d" in show.columns else None,
    }),
    use_container_width=True,
    hide_index=True,
)

st.caption(
    f"Ranked by **value_7d** = total Z-score × schedule density (next {short_w} days).  \n"
    "⭐ = plays on at least one light night (< 5 total NHL games that day)."
)

# ── Per-category Z breakdown ──────────────────────────────────────────────────

with st.expander("Category Z-score breakdown"):
    z_cols = ["name", "position"] + [f"{c}_z" for c in cfg["categories"] if f"{c}_z" in streamers.columns]
    # Need to pull z-cols from main df since get_streamers strips them
    z_data = df[df["is_fa"]].sort_values("value_7d", ascending=False).head(int(top_n))
    if pos_filter:
        z_data = z_data[z_data["position"].isin(pos_filter)]
    z_show = z_data[[c for c in z_cols if c in z_data.columns]].copy().reset_index(drop=True)
    st.dataframe(z_show, use_container_width=True, hide_index=True)
