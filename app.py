"""
app.py — Thumpers GM Dashboard
Streamlit entry point. Handles data loading, caching, and sidebar.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import streamlit as st
import yaml
from datetime import date

from src.cache          import (init_players_db, init_schedule_db,
                                 save_skaters, load_skaters, skaters_stale,
                                 save_schedule, load_schedule, schedule_stale,
                                 save_roster_membership, load_roster_membership)
from src.nhl_api        import fetch_skaters
from src.schedule       import fetch_schedule
from src.fantasy_roster import load_roster
from src.analytics      import build_player_df

# ── Config ────────────────────────────────────────────────────────────────────

@st.cache_resource
def load_config():
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)

# ── Page setup ────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Thumpers GM Dashboard",
    page_icon="🏒",
    layout="wide",
    initial_sidebar_state="expanded",
)

cfg = load_config()

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("🏒 Thumpers GM")
    st.caption(f"Team #{cfg['my_team_number']} — {cfg['my_team_name']}")
    st.divider()

    st.subheader("Category Weights")
    st.caption("Set to 0 to punt a category.")
    weights = {}
    for cat in cfg["categories"]:
        default = cfg["weights"].get(cat, 1.0)
        weights[cat] = st.slider(cat, 0.0, 1.0, default, 0.1, key=f"w_{cat}")

    st.divider()

    drop_threshold = st.number_input(
        "Drop Suggestion Threshold",
        min_value=0.0, max_value=5.0,
        value=float(cfg["drop_threshold"]),
        step=0.1,
        help="Min value score gap (FA - Rostered) to flag a drop",
    )

    today_input = st.date_input("Schedule start date", value=date.today())
    today_str   = today_input.isoformat()

    st.divider()
    refresh_players  = st.button("🔄 Refresh Player Stats")
    refresh_schedule = st.button("🔄 Refresh Schedule")
    refresh_roster   = st.button("🔄 Sync Roster")

# ── DB init ───────────────────────────────────────────────────────────────────

init_players_db()
init_schedule_db()

# ── Data loading ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=cfg["cache_ttl_hours"] * 3600, show_spinner=False)
def get_skaters(season_id: int) -> list[dict]:
    return load_skaters()

@st.cache_data(ttl=3600, show_spinner=False)
def get_schedule() -> list[dict]:
    return load_schedule()

@st.cache_data(ttl=3600, show_spinner=False)
def get_roster_membership() -> dict:
    return load_roster_membership()


# ── Refresh triggers ──────────────────────────────────────────────────────────

if refresh_players or skaters_stale(cfg["cache_ttl_hours"]):
    with st.spinner("Fetching NHL player stats from api.nhle.com…"):
        skaters = fetch_skaters(cfg["season_id"])
        save_skaters(skaters)
    get_skaters.clear()
    st.success(f"Loaded {len(skaters)} skaters.")

if refresh_schedule or schedule_stale(7):
    with st.spinner("Scraping schedule from hockey-reference.com…"):
        sched = fetch_schedule()
        if sched:
            save_schedule(sched)
    get_schedule.clear()
    if sched:
        st.success(f"Loaded {len(sched)} games.")
    else:
        st.warning("Could not scrape schedule — check your internet connection.")

if refresh_roster:
    skaters = get_skaters(cfg["season_id"])
    roster = load_roster(skaters)
    save_roster_membership([
        {"player_id": pid, **info}
        for pid, info in roster.items()
    ])
    get_roster_membership.clear()
    st.success("Roster synced.")


# ── Build master DataFrame ────────────────────────────────────────────────────

@st.cache_data(ttl=cfg["cache_ttl_hours"] * 3600, show_spinner=False)
def build_df(weights_tuple, today_str):
    weights = dict(weights_tuple)
    skaters  = load_skaters()
    schedule = load_schedule()
    roster   = load_roster_membership()

    if not skaters:
        return None, "No player data. Click 'Refresh Player Stats'."

    if not roster:
        from src.fantasy_roster import load_roster as _lr
        roster = _lr(skaters)

    df = build_player_df(skaters, roster, schedule, weights, cfg, today=today_str)
    return df, None


with st.spinner("Building player data…"):
    df, err = build_df(tuple(sorted(weights.items())), today_str)

if err:
    st.error(err)
    st.stop()

# Expose to pages via session_state
st.session_state["df"]              = df
st.session_state["cfg"]             = cfg
st.session_state["weights"]         = weights
st.session_state["drop_threshold"]  = drop_threshold
st.session_state["today_str"]       = today_str
st.session_state["schedule"]        = get_schedule()

# ── Home page ─────────────────────────────────────────────────────────────────

st.title("🏒 Thumpers GM Dashboard")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Skaters loaded",     len(df))
col2.metric("Rostered players",   int((~df["is_fa"]).sum()))
col3.metric("Free agents",        int(df["is_fa"].sum()))
col4.metric("My roster (Thumpers)", int((df["team_number"] == cfg["my_team_number"]).sum()))

st.info("Use the sidebar to adjust category weights, then navigate to a view using the pages menu.")

# Quick preview: my roster
st.subheader(f"My Roster — {cfg['my_team_name']}")
my = df[df["team_number"] == cfg["my_team_number"]].sort_values("total_z", ascending=False)
display_cols = ["name", "team", "position", "gp", "G", "A", "PP", "S", "HIT", "BLK", "PIM", "FOW",
                "total_z", "vorp", "games_7d", "value_7d"]
st.dataframe(
    my[[c for c in display_cols if c in my.columns]],
    use_container_width=True,
    hide_index=True,
)
