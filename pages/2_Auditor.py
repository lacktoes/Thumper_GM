"""
2_Auditor.py — Weak Link Auditor

Compares Thumpers roster against top FAs.
Drop Score = FA_value_7d - Rostered_value_7d > threshold => flag drop.
Excludes injured FAs from suggestions (they're not safe pickups).
"""
import streamlit as st
from src.analytics import get_drop_suggestions

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

col1, col2 = st.columns(2)
threshold = col1.number_input(
    "Drop Score threshold", 0.0, 10.0, float(drop_threshold), 0.1,
    help="Higher = only flag severe mismatches",
)
top_fa_n = col2.slider("Compare against top N FAs", 5, 50, 10)

# ── My roster ────────────────────────────────────────────────────────────────

st.subheader(f"My Roster — {cfg['my_team_name']}")

my = df[df["team_number"] == my_team].sort_values("value_7d", ascending=False)
disp = [
    "name", "team", "position", "gp",
    "G", "A", "PP", "S", "HIT", "BLK", "PIM", "FOW",
    "total_z", "total_z_recent", "vorp", "games_7d", "value_7d",
    "injury_status", "consecutive_missed", "missed_last_14d",
]
st.dataframe(
    my[[c for c in disp if c in my.columns]].reset_index(drop=True),
    use_container_width=True,
    hide_index=True,
    column_config={
        "injury_status":      st.column_config.TextColumn("Status"),
        "total_z":            st.column_config.NumberColumn("Z (Season)", format="%.3f"),
        "total_z_recent":     st.column_config.NumberColumn("Z (Recent)", format="%.3f"),
        "consecutive_missed": st.column_config.NumberColumn("Missed"),
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
            "Drop Status": st.column_config.TextColumn("Drop Status"),
            "Drop Score":  st.column_config.NumberColumn("Drop Score", format="%.3f"),
        },
    )
    st.caption(
        "**Drop Score** = FA value_7d − Rostered value_7d.  \n"
        "**Value 7d** = total Z-score × schedule density. One row per FA (best drop candidate shown)."
    )

st.divider()

# ── Form comparison ───────────────────────────────────────────────────────────

st.subheader(f"Season vs Recent Form ({recent_days}d) — My Roster")
st.caption("Players where recent Z-score is significantly below season Z-score may be cooling off.")

form_cols = ["name", "team", "position", "gp", "recent_gp", "total_z", "total_z_recent", "injury_status"]
form = my[[c for c in form_cols if c in my.columns]].copy()
if "total_z" in form.columns and "total_z_recent" in form.columns:
    form["Form Δ"] = (form["total_z_recent"] - form["total_z"]).round(3)
st.dataframe(form.reset_index(drop=True), use_container_width=True, hide_index=True)

# ── Full FA reference ─────────────────────────────────────────────────────────

with st.expander("Top Free Agents (full list, healthy only)"):
    fa = (df[df["is_fa"] & ~df["injury_flag"].astype(bool)]
          .sort_values("value_7d", ascending=False).head(int(top_fa_n)))
    fa_cols = ["name", "team", "position", "gp",
               "G", "A", "PP", "S", "HIT", "BLK", "PIM", "FOW",
               "total_z", "total_z_recent", "vorp",
               "games_7d", "games_14d", "value_7d", "injury_status"]
    st.dataframe(fa[[c for c in fa_cols if c in fa.columns]].reset_index(drop=True),
                 use_container_width=True, hide_index=True)
