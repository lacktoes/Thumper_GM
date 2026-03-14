"""
2_Auditor.py — Weak Link Auditor

Compares each Thumpers roster player against the top 10 available Free Agents.
Flags players who should be dropped based on:
  Drop Score = FA_value_7d - Rostered_value_7d > threshold
"""
import streamlit as st
import pandas as pd
from src.analytics import get_drop_suggestions

st.set_page_config(page_title="Auditor — Thumpers GM", layout="wide")
st.title("🔍 Weak Link Auditor")

# ── Pull shared state ─────────────────────────────────────────────────────────

if "df" not in st.session_state:
    st.warning("Return to the home page to load data first.")
    st.stop()

df             = st.session_state["df"]
cfg            = st.session_state["cfg"]
drop_threshold = st.session_state.get("drop_threshold", cfg.get("drop_threshold", 0.5))
my_team        = cfg["my_team_number"]

# ── Controls ──────────────────────────────────────────────────────────────────

col1, col2 = st.columns(2)
threshold  = col1.number_input(
    "Drop Score threshold",
    min_value=0.0, max_value=10.0,
    value=float(drop_threshold),
    step=0.1,
    help="Higher = only flag more egregious mismatches",
)
top_fa_n = col2.slider("Compare against top N FAs", 5, 50, 10)

# ── My roster snapshot ────────────────────────────────────────────────────────

st.subheader(f"My Roster — {cfg['my_team_name']}")
my = df[df["team_number"] == my_team].sort_values("value_7d", ascending=False)
disp = ["name", "team", "position", "gp", "G", "A", "PP", "S", "HIT", "BLK", "PIM", "FOW",
        "total_z", "vorp", "games_7d", "value_7d"]
st.dataframe(
    my[[c for c in disp if c in my.columns]].reset_index(drop=True),
    use_container_width=True,
    hide_index=True,
)

st.divider()

# ── Drop suggestions ──────────────────────────────────────────────────────────

st.subheader("Recommended Drops")

suggestions = get_drop_suggestions(
    df,
    my_team_number=my_team,
    drop_threshold=float(threshold),
    top_fa_n=int(top_fa_n),
)

if suggestions.empty:
    st.success(
        f"No drops recommended at threshold {threshold:.1f}. "
        "Your roster looks solid vs the top FAs!"
    )
else:
    st.warning(f"{len(suggestions)} potential drop(s) found.")

    # Colour code by Drop Score
    def _colour(val):
        if isinstance(val, float):
            intensity = min(int(val * 30), 120)
            return f"color: rgb({intensity+80}, {255-intensity}, 80)"
        return ""

    st.dataframe(
        suggestions.style.map(_colour, subset=["Drop Score"]),
        use_container_width=True,
        hide_index=True,
    )
    st.caption(
        "**Drop Score** = FA value_7d − Rostered value_7d.  \n"
        "value_7d = total Z-score × schedule density (next 7 days).  \n"
        "Only the best drop candidate is shown per FA."
    )

# ── Full FA table for manual comparison ──────────────────────────────────────

with st.expander("Top Free Agents (full list)"):
    fa = df[df["is_fa"]].sort_values("value_7d", ascending=False).head(int(top_fa_n))
    fa_cols = ["name", "team", "position", "gp", "G", "A", "PP", "S", "HIT", "BLK",
               "PIM", "FOW", "total_z", "vorp", "games_7d", "games_14d", "value_7d"]
    st.dataframe(
        fa[[c for c in fa_cols if c in fa.columns]].reset_index(drop=True),
        use_container_width=True,
        hide_index=True,
    )
