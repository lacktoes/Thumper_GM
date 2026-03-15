"""
2_Auditor.py — Weak Link Auditor

Compares Thumpers roster against top FAs.
Drop Score = FA_value_7d - Rostered_value_7d > threshold => flag drop.
Excludes injured FAs from suggestions (they're not safe pickups).
"""
import streamlit as st
from src.analytics import get_drop_suggestions, per_game_display, STAT_CATS

st.set_page_config(page_title="Auditor — Thumpers GM", layout="wide")
st.title("🔍 Weak Link Auditor")

if "df" not in st.session_state:
    st.warning("Return to the home page to load data first.")
    st.stop()

df             = st.session_state["df"]
cfg            = st.session_state["cfg"]
drop_threshold = st.session_state.get("drop_threshold", cfg.get("drop_threshold", 0.5))
my_team        = cfg["my_team_number"]
recent_days    = st.session_state.get("recent_days", 14)

# ── Controls ──────────────────────────────────────────────────────────────────

col1, col2, col3, col4 = st.columns(4)
threshold = col1.number_input(
    "Drop Score threshold", 0.0, 10.0, float(drop_threshold), 0.1,
    help="Higher = only flag severe mismatches",
)
top_fa_n     = col2.slider("FA pool size", 5, 100, 40)
pos_match    = col3.checkbox("Position match only", value=True,
                             help="Only suggest FAs who share a position with the player being dropped")
show_pg      = col4.checkbox("Per game avg", value=False,
                             help="Show per-game averages instead of season totals")

stat_fmt = "%.1f" if show_pg else "%d"

# ── My roster ────────────────────────────────────────────────────────────────

st.subheader(f"My Roster — {cfg['my_team_name']}")

my = df[df["team_number"] == my_team].sort_values("value_7d", ascending=False)
my_show = per_game_display(my, STAT_CATS) if show_pg else my
disp = [
    "name", "team", "position", "gp",
    "G", "A", "PP", "S", "HIT", "BLK", "PIM", "FOW",
    "total_z", "total_z_recent", "vorp", "games_7d", "value_7d",
    "injury_status",
]
st.dataframe(
    my_show[[c for c in disp if c in my_show.columns]].reset_index(drop=True),
    use_container_width=True,
    hide_index=True,
    column_config={
        "gp":             st.column_config.NumberColumn("GP",         format="%d"),
        "G":              st.column_config.NumberColumn("G",          format=stat_fmt),
        "A":              st.column_config.NumberColumn("A",          format=stat_fmt),
        "PP":             st.column_config.NumberColumn("PP",         format=stat_fmt),
        "S":              st.column_config.NumberColumn("S",          format=stat_fmt),
        "HIT":            st.column_config.NumberColumn("HIT",        format=stat_fmt),
        "BLK":            st.column_config.NumberColumn("BLK",        format=stat_fmt),
        "PIM":            st.column_config.NumberColumn("PIM",        format=stat_fmt),
        "FOW":            st.column_config.NumberColumn("FOW",        format=stat_fmt),
        "games_7d":       st.column_config.NumberColumn("Games 7d",   format="%d"),
        "total_z":        st.column_config.NumberColumn("Z (Season)", format="%.1f"),
        "total_z_recent": st.column_config.NumberColumn("Z (Recent)", format="%.1f"),
        "vorp":           st.column_config.NumberColumn("VORP",       format="%.1f"),
        "value_7d":       st.column_config.NumberColumn("Value (7d)", format="%.1f"),
        "injury_status":  st.column_config.TextColumn("Status"),
    },
)

st.divider()

# ── Drop suggestions ──────────────────────────────────────────────────────────

st.subheader("Recommended Drops")
st.caption("Only healthy FAs are considered for drop suggestions.")

suggestions = get_drop_suggestions(
    df,
    my_team_number=my_team,
    drop_threshold=float(threshold),
    top_fa_n=int(top_fa_n),
    position_match=pos_match,
)

if suggestions.empty:
    st.success(
        f"No drops recommended at threshold {threshold:.1f}. "
        "Your roster is competitive against the top available FAs!"
    )
else:
    st.warning(f"**{len(suggestions)}** potential drop(s) found.")

    def _score_colour(val):
        if not isinstance(val, float):
            return ""
        intensity = min(int(val * 25), 120)
        return f"color: rgb({intensity + 80}, {255 - intensity}, 80)"

    st.dataframe(
        suggestions.style.map(_score_colour, subset=["Drop Score"]),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Drop (Rostered)":  st.column_config.TextColumn("Drop"),
            "Pos":              st.column_config.TextColumn("Pos"),
            "Drop Value":       st.column_config.NumberColumn("Drop Val", format="%.1f"),
            "Drop Z":           st.column_config.NumberColumn("Drop Z",   format="%.1f"),
            "Drop Status":      st.column_config.TextColumn("Status"),
            "Add (Free Agent)": st.column_config.TextColumn("Pick Up"),
            "FA Pos":           st.column_config.TextColumn("FA Pos"),
            "Add Value":        st.column_config.NumberColumn("Add Val",  format="%.1f"),
            "Add Z":            st.column_config.NumberColumn("Add Z",    format="%.1f"),
            "Add Games (7d)":   st.column_config.NumberColumn("Games",    format="%d"),
            "Drop Score":       st.column_config.NumberColumn("Score",    format="%.1f"),
        },
    )
    st.caption(
        "**Drop Score** = FA value_7d − Rostered value_7d.  \n"
        "One row per rostered player — showing their best available position-matched FA upgrade."
    )

st.divider()

# ── Form comparison ───────────────────────────────────────────────────────────

st.subheader(f"Season vs Recent Form ({recent_days}d) — My Roster")
st.caption("Players where recent Z-score is significantly below season Z-score may be cooling off.")

form_cols = ["name", "team", "position", "gp", "recent_gp", "total_z", "total_z_recent", "injury_status"]
form = my[[c for c in form_cols if c in my.columns]].copy()
if "total_z" in form.columns and "total_z_recent" in form.columns:
    form["Form Δ"] = (form["total_z_recent"] - form["total_z"]).round(1)
st.dataframe(form.reset_index(drop=True), use_container_width=True, hide_index=True,
             column_config={
                 "gp":             st.column_config.NumberColumn("GP",         format="%d"),
                 "recent_gp":      st.column_config.NumberColumn("Recent GP",  format="%d"),
                 "total_z":        st.column_config.NumberColumn("Z (Season)", format="%.1f"),
                 "total_z_recent": st.column_config.NumberColumn("Z (Recent)", format="%.1f"),
                 "Form Δ":         st.column_config.NumberColumn("Form Δ",     format="%.1f"),
             })

# ── Full FA reference ─────────────────────────────────────────────────────────

with st.expander("Top Free Agents (full list, healthy only)"):
    fa = (df[df["is_fa"] & ~df["injury_flag"].astype(bool)]
          .sort_values("value_7d", ascending=False).head(int(top_fa_n)))
    fa_show = per_game_display(fa, STAT_CATS) if show_pg else fa
    fa_cols = ["name", "team", "position", "gp",
               "G", "A", "PP", "S", "HIT", "BLK", "PIM", "FOW",
               "total_z", "total_z_recent", "vorp",
               "games_7d", "games_14d", "value_7d", "injury_status"]
    st.dataframe(fa_show[[c for c in fa_cols if c in fa_show.columns]].reset_index(drop=True),
                 use_container_width=True, hide_index=True,
                 column_config={
                     "gp":  st.column_config.NumberColumn("GP",   format="%d"),
                     "G":   st.column_config.NumberColumn("G",    format=stat_fmt),
                     "A":   st.column_config.NumberColumn("A",    format=stat_fmt),
                     "PP":  st.column_config.NumberColumn("PP",   format=stat_fmt),
                     "S":   st.column_config.NumberColumn("S",    format=stat_fmt),
                     "HIT": st.column_config.NumberColumn("HIT",  format=stat_fmt),
                     "BLK": st.column_config.NumberColumn("BLK",  format=stat_fmt),
                     "PIM": st.column_config.NumberColumn("PIM",  format=stat_fmt),
                     "FOW": st.column_config.NumberColumn("FOW",  format=stat_fmt),
                     "total_z":        st.column_config.NumberColumn("Z (Season)", format="%.1f"),
                     "total_z_recent": st.column_config.NumberColumn("Z (Recent)", format="%.1f"),
                     "vorp":           st.column_config.NumberColumn("VORP",       format="%.1f"),
                     "value_7d":       st.column_config.NumberColumn("Value (7d)", format="%.1f"),
                 })
