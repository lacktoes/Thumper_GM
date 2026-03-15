"""
app.py — Thumpers GM Dashboard (home page + data orchestration).

Data flow:
  1. NHL Stats API  → season skater stats (8 cats, all skaters)
  2. Yahoo Fantasy  → roster membership + injury status (who is rostered/FA)
  3. NHL Web API    → game logs for top players (recent form + injury detection)
  4. NHL Web API    → full season schedule (used for density + injury gap detection)

Credentials (set in .streamlit/secrets.toml for deployment, or .env locally):
  YAHOO_CLIENT_ID, YAHOO_CLIENT_SECRET, YAHOO_REFRESH_TOKEN, YAHOO_LEAGUE_KEY
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import streamlit as st
import yaml
from datetime import date

from src.cache        import (
    init_players_db, init_schedule_db,
    save_skaters,    load_skaters,    skaters_stale,
    save_schedule,   load_schedule,   schedule_stale,
    save_roster_membership, load_roster_membership, roster_stale,
    save_game_logs,  load_game_logs,
    latest_game_log_date, game_logs_need_update,
)
from src.nhl_api      import fetch_skaters, fetch_per_game_stats, SEASON_START
from src.schedule     import fetch_schedule
from src.yahoo_fantasy import fetch_all_rosters, build_roster_membership
from src.analytics    import build_player_df

# ── Config ────────────────────────────────────────────────────────────────────

@st.cache_resource
def load_config():
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)

cfg = load_config()

# ── Page setup ────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Thumpers GM Dashboard",
    page_icon="🏒",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── DB init ───────────────────────────────────────────────────────────────────

init_players_db()
init_schedule_db()

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("🏒 Thumpers GM")
    st.caption(f"Team #{cfg['my_team_number']} — {cfg['my_team_name']}")
    st.divider()

    st.subheader("Category Weights")
    st.caption("Set to 0.0 to punt a category.")
    weights = {}
    for cat in cfg["categories"]:
        weights[cat] = st.slider(cat, 0.0, 1.0, float(cfg["weights"].get(cat, 1.0)), 0.1, key=f"w_{cat}")

    st.divider()

    drop_threshold = st.number_input(
        "Drop Suggestion Threshold",
        min_value=0.0, max_value=5.0,
        value=float(cfg["drop_threshold"]),
        step=0.1,
    )
    recent_days = st.slider(
        "Recent Form Window (days)", 7, 60,
        int(cfg.get("recent_form_days", 14)),
        help="Calculate form Z-score from games in this window",
    )
    today_input = st.date_input("Schedule start date", value=date.today())
    today_str   = today_input.isoformat()

    st.divider()
    col_a, col_b = st.columns(2)
    refresh_stats    = col_a.button("📊 Stats",    help="Refresh NHL player stats")
    refresh_schedule = col_b.button("📅 Schedule", help="Refresh NHL schedule")
    col_c, col_d = st.columns(2)
    refresh_roster   = col_c.button("👥 Roster",   help="Sync Yahoo Fantasy rosters + injury status")
    refresh_logs     = col_d.button("📈 Form",     help="Fetch new per-game stats since last update")

# ── Data refresh ──────────────────────────────────────────────────────────────

yahoo_ok = True

# 1. Season stats (NHL API)
if refresh_stats or skaters_stale(cfg["cache_ttl_hours"]):
    with st.spinner("Fetching NHL skater stats…"):
        skaters = fetch_skaters(cfg["season_id"])
        save_skaters(skaters)
    st.toast(f"Loaded {len(skaters)} skaters.")

# 2. Schedule (NHL API)
if refresh_schedule or schedule_stale(7):
    with st.spinner("Fetching NHL schedule from api-web.nhle.com…"):
        sched = fetch_schedule()
        if sched:
            save_schedule(sched)
            st.toast(f"Loaded {len(sched)} games.")
        else:
            st.warning("Schedule fetch returned no games.")

# 3. Roster + injury status (Yahoo Fantasy API)
if refresh_roster or roster_stale(cfg["cache_ttl_hours"]):
    league_key  = None
    try:
        import os
        try:
            league_key = st.secrets.get("YAHOO_LEAGUE_KEY") or os.environ.get("YAHOO_LEAGUE_KEY")
        except Exception:
            league_key = os.environ.get("YAHOO_LEAGUE_KEY")

        if not league_key:
            st.warning("YAHOO_LEAGUE_KEY not set — roster/injury data unavailable. "
                       "Add it to .streamlit/secrets.toml.")
            yahoo_ok = False
        else:
            with st.spinner("Syncing Yahoo Fantasy rosters + injury status…"):
                skater_list  = load_skaters()
                yahoo_roster = fetch_all_rosters(league_key, cfg["total_teams"])
                merged       = build_roster_membership(yahoo_roster, [s["player_id"] for s in skater_list])
                save_roster_membership([
                    {"player_id": pid, **info}
                    for pid, info in merged.items()
                ])
            st.toast(f"Roster synced — {len([v for v in merged.values() if not v['is_fa']])} rostered players.")
    except Exception as exc:
        st.warning(f"Yahoo roster sync failed: {exc}")
        yahoo_ok = False

# 4. Per-game stats — incremental: fetch only games since last stored date
def _game_log_date_range() -> tuple[str, str]:
    """
    Returns (since_date, until_date) for the next incremental fetch.
    since_date = day after the latest stored game, or SEASON_START for first run.
    until_date = today.
    """
    latest = latest_game_log_date()
    if latest:
        from datetime import datetime, timedelta
        since = (datetime.fromisoformat(latest) + timedelta(days=1)).date().isoformat()
    else:
        since = SEASON_START
    until = date.today().isoformat()
    return since, until


def _do_game_log_fetch():
    since, until = _game_log_date_range()
    if since > until:
        st.toast("Game logs already up to date.")
        return
    with st.spinner(f"Fetching per-game stats {since} → {until}…"):
        rows = fetch_per_game_stats(since, until)
        if rows:
            save_game_logs(rows)
    st.toast(f"Game logs updated: {len(rows)} player-game rows ({since} → {until}).")


if refresh_logs:
    _do_game_log_fetch()

# Auto-refresh if stale
if game_logs_need_update(ttl_hours=cfg["cache_ttl_hours"]):
    _do_game_log_fetch()

# ── Load all data ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=cfg["cache_ttl_hours"] * 3600, show_spinner=False)
def build_df(weights_tuple, today_str, recent_days):
    from datetime import timedelta, datetime as _dt
    weights    = dict(weights_tuple)
    skaters    = load_skaters()
    schedule   = load_schedule()
    roster     = load_roster_membership()
    # Only load game logs within the recent form window (+ buffer) to keep memory lean
    since      = (_dt.fromisoformat(today_str) - timedelta(days=recent_days + 2)).date().isoformat()
    game_logs  = load_game_logs(since_date=since)

    if not skaters:
        return None, "No player data. Click '📊 Stats' to fetch from NHL API."

    df = build_player_df(
        skaters, roster, schedule, game_logs, weights, cfg,
        today=today_str,
    )
    return df, None


with st.spinner("Building player data…"):
    df, err = build_df(tuple(sorted(weights.items())), today_str, recent_days)

if err:
    st.error(err)
    st.stop()

# Publish to session_state for page modules
st.session_state.update({
    "df":             df,
    "cfg":            cfg,
    "weights":        weights,
    "drop_threshold": drop_threshold,
    "today_str":      today_str,
    "recent_days":    recent_days,
    "schedule":       load_schedule(),
})

# ── Home page ─────────────────────────────────────────────────────────────────

st.title("🏒 Thumpers GM Dashboard")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Skaters loaded",      len(df))
col2.metric("Rostered",            int((~df["is_fa"]).sum()))
col3.metric("Free Agents",         int(df["is_fa"].sum()))
col4.metric("My roster (Thumpers)", int((df["team_number"] == cfg["my_team_number"]).sum()))

# Injury alert banner
injured = df[
    (df["team_number"] == cfg["my_team_number"]) &
    (df["injury_flag"].astype(bool) | df["status"].isin(["IR", "IR-LT", "IL", "O"]))
]
if not injured.empty:
    names = ", ".join(injured["name"].tolist())
    st.error(f"⚠️  Injury alert on your roster: {names}")

st.divider()
st.subheader(f"My Roster — {cfg['my_team_name']}")

my = df[df["team_number"] == cfg["my_team_number"]].sort_values("total_z", ascending=False)
display_cols = [
    "name", "team", "position", "gp",
    "G", "A", "PP", "S", "HIT", "BLK", "PIM", "FOW",
    "total_z", "total_z_recent", "vorp", "games_7d", "value_7d",
    "injury_status", "consecutive_missed",
]
st.dataframe(
    my[[c for c in display_cols if c in my.columns]].reset_index(drop=True),
    use_container_width=True,
    hide_index=True,
    column_config={
        "injury_status":     st.column_config.TextColumn("Status"),
        "total_z":           st.column_config.NumberColumn("Z-Score", format="%.3f"),
        "total_z_recent":    st.column_config.NumberColumn("Z (Recent)", format="%.3f"),
        "consecutive_missed":st.column_config.NumberColumn("Missed"),
    },
)

st.caption(
    "**Z-Score** = season total. **Z (Recent)** = last "
    f"{recent_days} days, all 8 categories (G, A, PP, S, PIM, HIT, BLK, FOW per game).  \n"
    "**Missed** = consecutive team games not played (≥3 triggers injury flag)."
)
