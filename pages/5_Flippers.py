"""
5_Flippers.py — H2H Category Flipper

Probabilistic model (Poisson / Normal approximation)
-----------------------------------------------------
Skater stats modelled as Poisson(λ) per game.  Variance = mean.
P(win cat) = Φ(gap_μ / gap_σ) where gap includes both rosters' remaining uncertainty.
xWA = Σ_c weight_c × [P(win_c | +player) − P(win_c | base)]

Goalie model
------------
Start rate  = goalie_season_gp / team_season_gp  (fraction of games they dress)
p_win(game) = log5(pyth_wp(my_team), pyth_wp(opponent))  ± small home advantage
              pyth_wp = GF² / (GF² + GA²)  from current NHL standings
proj_W      = Σ_{remaining games} start_rate × p_win(game)
proj_W_var  = Σ start_rate × p_win × (1 − p_win)  (Bernoulli sum)
Goalie xWA  = same formula as skaters for W (and SO/SVP if in league cats)

2-week skater xWA
-----------------
Same xWA calculation but FA contributions projected over 14 days instead of
remaining matchup days.  Useful for Sunday pickups that need next-week utility.
"""
import os
import streamlit as st
import pandas as pd
import numpy as np
from datetime import date, timedelta

try:
    from scipy.stats import norm as _norm
    _SCIPY = True
except ImportError:
    _SCIPY = False

st.set_page_config(page_title="Flippers — Thumpers GM", layout="wide")
st.title("⚔️ Category Flippers")

if "df" not in st.session_state:
    st.warning("Return to the home page to load data first.")
    st.stop()

df        = st.session_state["df"]
goalie_df = st.session_state.get("goalie_df")
cfg       = st.session_state["cfg"]
schedule  = st.session_state["schedule"]
today_str = st.session_state["today_str"]
weights   = st.session_state["weights"]

from src.yahoo_fantasy import fetch_weekly_matchup
from src.nhl_api       import fetch_team_standings
from src.schedule      import games_in_window

_LOGO = lambda t: f"https://assets.nhle.com/logos/nhl/svg/{t}_light.svg"

league_key = os.environ.get("YAHOO_LEAGUE_KEY", "")
if not league_key:
    try:
        league_key = st.secrets.get("YAHOO_LEAGUE_KEY", "")
    except Exception:
        pass
if not league_key:
    st.error("YAHOO_LEAGUE_KEY not set — add it to .env or .streamlit/secrets.toml.")
    st.stop()

my_team_num = cfg["my_team_number"]
CATS        = cfg["categories"]
_RATIO_CATS = {"SV%", "SVP", "GAA"}

# ── Fetch matchup ─────────────────────────────────────────────────────────────

if st.button("🔄 Fetch Current Matchup", type="primary"):
    with st.spinner("Fetching live matchup from Yahoo…"):
        try:
            matchup = fetch_weekly_matchup(league_key, my_team_num)
            st.session_state["flipper_matchup"] = matchup
            if matchup:
                st.success(
                    f"Week {matchup['week']} — **{matchup['my_name']}** vs **{matchup['opp_name']}**"
                )
            else:
                st.warning("No active matchup found (bye week or offseason).")
        except Exception as exc:
            st.error(f"Yahoo fetch failed: {exc}")

matchup = st.session_state.get("flipper_matchup")

if not matchup:
    st.info("Click **Fetch Current Matchup** to load live category stats from Yahoo.")
    st.stop()

my_stats  = matchup["my_stats"]
opp_stats = matchup["opp_stats"]
week      = matchup["week"]
my_name   = matchup["my_name"]
opp_name  = matchup["opp_name"]
my_logo   = matchup.get("my_logo")
opp_logo  = matchup.get("opp_logo")
opp_num   = matchup.get("opp_num")

# ── Week end → remaining days ─────────────────────────────────────────────────

today = date.fromisoformat(today_str)
if matchup.get("week_end"):
    week_end = date.fromisoformat(matchup["week_end"])
else:
    days_to_sunday = (6 - today.weekday()) % 7
    week_end = today + timedelta(days=days_to_sunday if days_to_sunday > 0 else 7)
remaining_days = max(1, (week_end - today).days + 1)

# ── Statistical helpers ───────────────────────────────────────────────────────

def _player_rpg(player, cat: str, gp: float) -> float:
    has_rec = float(player.get("recent_gp", 0) or 0) >= 2
    rpg_col = f"{cat}_rpg"
    if has_rec and rpg_col in player.index and player[rpg_col] == player[rpg_col]:
        return max(float(player[rpg_col] or 0), 0.0)
    return max(float(player.get(cat, 0) or 0) / max(gp, 1.0), 0.0)


def _roster_mu_var(roster_df: pd.DataFrame, cats: list, sched, today: str, days_left: int) -> dict:
    mu  = {cat: 0.0  for cat in cats}
    var = {cat: 0.01 for cat in cats}
    for _, player in roster_df.iterrows():
        team = player.get("team", "")
        if not team:
            continue
        gl = games_in_window(sched, team, today, days_left)
        if gl <= 0:
            continue
        gp = max(float(player.get("gp", 1) or 1), 1.0)
        for cat in cats:
            if cat in _RATIO_CATS:
                continue
            rpg = _player_rpg(player, cat, gp)
            mu[cat]  += rpg * gl
            var[cat] += rpg * gl
    return {cat: (mu[cat], var[cat]) for cat in cats}


def _p_win(gap_mu: float, gap_var: float) -> float:
    if not _SCIPY:
        return 1.0 if gap_mu > 0 else 0.0
    sigma = max(gap_var ** 0.5, 1e-6)
    return float(_norm.cdf(gap_mu / sigma))


def _status_label(p: float) -> str:
    if p >= 0.85: return "🔒 In the bag"
    if p >= 0.65: return "🟢 Leaning win"
    if p >= 0.45: return "⚪ Toss-up"
    if p >= 0.25: return "🔴 Leaning loss"
    return "💀 Out of reach"


def _pct(p: float) -> int:
    return int(round(p * 100))


# ── Goalie win-probability helpers ────────────────────────────────────────────

def _pyth_wp(gf: float, ga: float, exp: float = 2.0) -> float:
    """Pythagorean win expectancy for a team vs an average opponent."""
    return gf**exp / (gf**exp + ga**exp) if (gf + ga) > 0 else 0.5


def _log5(wp_a: float, wp_b: float) -> float:
    """Head-to-head win probability for team A vs team B (Bill James log5)."""
    denom = wp_a + wp_b - 2 * wp_a * wp_b
    return (wp_a - wp_a * wp_b) / denom if denom > 0 else 0.5


def _team_game_probs(
    team: str,
    sched: list[dict],
    standings: dict[str, dict],
    today: str,
    days: int,
    home_edge: float = 0.025,   # home teams win ~2.5% more in NHL
) -> list[dict]:
    """
    Return [{date, opponent, is_home, p_win}] for every remaining game
    of `team` in the next `days` days.
    p_win is Pythagorean log5 ± home edge.
    """
    cutoff = (date.fromisoformat(today) + timedelta(days=days)).isoformat()
    t_data = standings.get(team, {})
    t_gf   = t_data.get("gf", 1.0)
    t_ga   = t_data.get("ga", 1.0)
    t_wp   = _pyth_wp(t_gf, t_ga)

    games = []
    for g in sched:
        gd = g["game_date"]
        if gd < today or gd > cutoff:
            continue
        if g["home_team"] == team:
            opp    = g["away_team"]
            is_home = True
        elif g["away_team"] == team:
            opp    = g["home_team"]
            is_home = False
        else:
            continue

        o_data  = standings.get(opp, {})
        o_gf    = o_data.get("gf", 1.0)
        o_ga    = o_data.get("ga", 1.0)
        o_wp    = _pyth_wp(o_gf, o_ga)
        p_win   = _log5(t_wp, o_wp) + (home_edge if is_home else -home_edge)
        p_win   = max(0.05, min(0.95, p_win))
        games.append({"date": gd, "opponent": opp, "is_home": is_home, "p_win": p_win})

    return games


# ── Team standings (cached in session for the session lifetime) ───────────────

if "nhl_standings" not in st.session_state:
    with st.spinner("Fetching NHL standings for goalie win probabilities…"):
        st.session_state["nhl_standings"] = fetch_team_standings()

standings = st.session_state["nhl_standings"]

# ── Projection toggle ─────────────────────────────────────────────────────────

show_proj = st.checkbox(
    "📊 Project final week totals",
    value=True,
    help="Adds each rostered player's projected remaining-game contribution to live totals.",
)

my_roster_df  = df[df["team_number"] == my_team_num].copy()
opp_roster_df = df[df["team_number"] == opp_num].copy() if opp_num else pd.DataFrame()

my_remaining  = _roster_mu_var(my_roster_df,  CATS, schedule, today_str, remaining_days)
opp_remaining = _roster_mu_var(opp_roster_df, CATS, schedule, today_str, remaining_days)

if show_proj:
    my_final  = {cat: round(float(my_stats.get(cat, 0.0))  + my_remaining[cat][0],  1) for cat in CATS}
    opp_final = {cat: round(float(opp_stats.get(cat, 0.0)) + opp_remaining[cat][0], 1) for cat in CATS}
else:
    my_final  = {cat: float(my_stats.get(cat, 0.0))  for cat in CATS}
    opp_final = {cat: float(opp_stats.get(cat, 0.0)) for cat in CATS}

# ── Baseline P(win) per category ──────────────────────────────────────────────

base_p: dict[str, float] = {}
for cat in CATS:
    if cat in _RATIO_CATS:
        base_p[cat] = 1.0 if my_final[cat] > opp_final[cat] else 0.0
        continue
    mu_gap  = my_final[cat] - opp_final[cat]
    var_gap = my_remaining[cat][1] + opp_remaining[cat][1]
    base_p[cat] = _p_win(mu_gap, var_gap)

# ── Matchup header ────────────────────────────────────────────────────────────

c_my, c_vs, c_opp = st.columns([2, 1, 2])
with c_my:
    if my_logo: st.image(my_logo, width=48)
    st.markdown(f"### {my_name}")
with c_vs:
    st.markdown("<h3 style='text-align:center;color:#aaa'>vs</h3>", unsafe_allow_html=True)
    st.caption(f"Week {week}  \n{remaining_days}d remaining")
with c_opp:
    if opp_logo: st.image(opp_logo, width=48)
    st.markdown(f"### {opp_name}")

# ── Category comparison table ─────────────────────────────────────────────────

rows = []
for cat in CATS:
    mine_live = float(my_stats.get(cat, 0.0))
    opp_live  = float(opp_stats.get(cat, 0.0))
    mine_f    = my_final[cat]
    opp_f     = opp_final[cat]
    gap       = mine_f - opp_f
    p         = base_p[cat]
    row = {
        "Category": cat,
        my_name:    mine_live,
        opp_name:   opp_live,
        "Gap":      gap,
        "P(win)%":  _pct(p),
        "Status":   _status_label(p),
    }
    if show_proj:
        row[f"{my_name} »"]  = mine_f
        row[f"{opp_name} »"] = opp_f
    rows.append(row)

comp_df = pd.DataFrame(rows)
wins   = sum(1 for r in rows if r["Gap"] > 0)
losses = sum(1 for r in rows if r["Gap"] < 0)
ties   = sum(1 for r in rows if r["Gap"] == 0)
exp_w  = sum(base_p[cat] for cat in CATS)

score_col, table_col = st.columns([1, 3])
with score_col:
    st.metric("Score", f"{wins} – {losses} – {ties}",
              help="Projected categories Won – Lost – Tied")
    if _SCIPY:
        st.metric("Expected wins", f"{exp_w:.1f}", help="Σ P(win) across all categories")
    if show_proj:
        st.caption("**»** = projected finals")

with table_col:
    col_cfg = {
        "Category": st.column_config.TextColumn("Cat", width="small"),
        my_name:    st.column_config.NumberColumn(f"{my_name} (live)",  format="%.1f"),
        opp_name:   st.column_config.NumberColumn(f"{opp_name} (live)", format="%.1f"),
        "Gap":      st.column_config.NumberColumn("Gap",    format="%+.1f"),
        "P(win)%":  st.column_config.NumberColumn("P(win)", format="%d%%", width="small"),
        "Status":   st.column_config.TextColumn("Status"),
    }
    if show_proj:
        col_cfg[f"{my_name} »"]  = st.column_config.NumberColumn(f"{my_name} »",  format="%.1f")
        col_cfg[f"{opp_name} »"] = st.column_config.NumberColumn(f"{opp_name} »", format="%.1f")
    st.dataframe(comp_df, use_container_width=True, hide_index=True, column_config=col_cfg)

if not _SCIPY:
    st.caption("ℹ️ Install `scipy` for probability estimates (`pip install scipy`).")

st.divider()

# ── Controls ──────────────────────────────────────────────────────────────────

col_a, col_b = st.columns([1, 2])
close_pct    = col_a.slider("Close margin (%)", 5, 60, 30,
                             help="Gap within this % of leader's total = 'in reach'") / 100.0
top_n_picks  = col_b.slider("FAs to show per category", 3, 15, 6)


def _close(mine: float, opp: float, pct: float) -> bool:
    return (abs(mine - opp) / max(abs(mine), abs(opp), 1.0)) <= pct


_my_col  = f"{my_name} »"  if show_proj else my_name
_opp_col = f"{opp_name} »" if show_proj else opp_name

flip_targets = [r for r in rows if r["Gap"] < 0
                and _close(r[_my_col], r[_opp_col], close_pct)]
defend_cats  = [r for r in rows if r["Gap"] > 0
                and _close(r[_my_col], r[_opp_col], close_pct)]
out_of_reach = [r for r in rows if r["Gap"] < 0
                and not _close(r[_my_col], r[_opp_col], close_pct)]

# ── Skater FA projected contributions ─────────────────────────────────────────

fa_df = df[df["is_fa"] & ~df["injury_flag"].astype(bool)].copy()
fa_df["remaining_games"] = fa_df["team"].apply(
    lambda t: games_in_window(schedule, t, today_str, remaining_days)
)
fa_df["games_14d_sched"] = fa_df["team"].apply(
    lambda t: games_in_window(schedule, t, today_str, 14)
)
has_recent = (fa_df["recent_gp"] >= 2) if "recent_gp" in fa_df.columns \
             else pd.Series(False, index=fa_df.index)

for cat in CATS:
    if cat in _RATIO_CATS:
        continue
    gp        = fa_df["gp"].clip(lower=1)
    season_pg = fa_df[cat] / gp
    rpg_col   = f"{cat}_rpg"
    rpg       = fa_df[rpg_col].where(has_recent, season_pg) if rpg_col in fa_df.columns else season_pg
    fa_df[f"{cat}_proj"]    = (rpg * fa_df["remaining_games"]).round(2)
    fa_df[f"{cat}_proj_14"] = (rpg * fa_df["games_14d_sched"]).round(2)

# ── Flip Targets ──────────────────────────────────────────────────────────────

st.subheader("📈 Flip Targets")

if flip_targets:
    st.caption(
        f"Trailing within the {int(close_pct*100)}% margin "
        f"{'(projected finals)' if show_proj else ''} — until {week_end}."
    )
    for row in sorted(flip_targets, key=lambda r: r["Gap"]):
        cat  = row["Category"]
        mine = row[_my_col]
        opp  = row[_opp_col]
        p    = base_p[cat]

        with st.expander(
            f"**{cat}**  —  {row['Status']}  ({mine:.1f} vs {opp:.1f})  P(win) = {_pct(p)}%",
            expanded=True,
        ):
            proj_col = f"{cat}_proj"
            if proj_col not in fa_df.columns:
                st.info(f"{cat} is a ratio stat.")
                continue
            picks = (
                fa_df[["name", "team", "position", "gp", cat, proj_col, "remaining_games", "total_z"]]
                .sort_values(proj_col, ascending=False).head(top_n_picks).reset_index(drop=True)
            )
            picks.insert(0, "Rank", range(1, len(picks) + 1))
            picks.insert(2, "logo", picks["team"].map(_LOGO))

            if _SCIPY:
                p_flips = []
                for _, pick in picks.iterrows():
                    gp_p   = max(float(pick.get("gp", 1) or 1), 1.0)
                    rpg    = _player_rpg(pick, cat, gp_p)
                    gl     = int(pick.get("remaining_games", 0))
                    fa_mu  = rpg * gl
                    mu_gap  = (mine + fa_mu) - opp
                    var_gap = my_remaining[cat][1] + fa_mu + opp_remaining[cat][1]
                    p_flips.append(_pct(_p_win(mu_gap, var_gap)))
                picks["P(flip)%"] = p_flips

            col_cfg = {
                "name":            st.column_config.TextColumn("Player"),
                "logo":            st.column_config.ImageColumn("Team",         width="small"),
                "position":        st.column_config.TextColumn("Pos",           width="small"),
                "gp":              st.column_config.NumberColumn("GP",          format="%d"),
                cat:               st.column_config.NumberColumn(f"{cat} (Season)", format="%.0f"),
                proj_col:          st.column_config.NumberColumn(f"{cat} Proj", format="%.1f"),
                "remaining_games": st.column_config.NumberColumn("Games Left",  format="%d"),
                "total_z":         st.column_config.NumberColumn("Z",           format="%.1f"),
            }
            if "P(flip)%" in picks.columns:
                col_cfg["P(flip)%"] = st.column_config.NumberColumn("P(flip)", format="%d%%", width="small")

            st.dataframe(picks, use_container_width=True, hide_index=True, column_config=col_cfg)

            if not picks.empty:
                top      = picks.iloc[0]
                new_mine = mine + top[proj_col]
                if new_mine > opp:
                    st.success(
                        f"✅ Adding **{top['name']}** projects to flip {cat} "
                        f"(+{top[proj_col]:.1f} → {new_mine:.1f} vs {opp:.1f})"
                    )
                else:
                    st.info(
                        f"Adding **{top['name']}** projects +{top[proj_col]:.1f} "
                        f"— still {opp - new_mine:.1f} short ({new_mine:.1f} vs {opp:.1f})"
                    )
else:
    st.success("No close trailing categories — nothing to flip right now. 🎉")

# ── Defend ────────────────────────────────────────────────────────────────────

st.subheader("🛡️ Defend")

if defend_cats:
    st.caption(
        f"Leads within the {int(close_pct*100)}% margin "
        f"{'(projected)' if show_proj else ''} — FAs your opponent could use to flip these."
    )
    for row in sorted(defend_cats, key=lambda r: r["Gap"]):
        cat  = row["Category"]
        mine = row[_my_col]
        opp  = row[_opp_col]
        p    = base_p[cat]

        with st.expander(
            f"**{cat}**  —  {row['Status']}  ({mine:.1f} vs {opp:.1f})  P(win) = {_pct(p)}%",
            expanded=False,
        ):
            proj_col = f"{cat}_proj"
            if proj_col not in fa_df.columns:
                continue
            threats = (
                fa_df[["name", "team", "position", "gp", cat, proj_col, "remaining_games"]]
                .sort_values(proj_col, ascending=False).head(top_n_picks).reset_index(drop=True)
            )
            threats.insert(0, "Rank", range(1, len(threats) + 1))
            threats.insert(2, "logo", threats["team"].map(_LOGO))
            st.dataframe(
                threats, use_container_width=True, hide_index=True,
                column_config={
                    "name":            st.column_config.TextColumn("Player"),
                    "logo":            st.column_config.ImageColumn("Team",         width="small"),
                    "position":        st.column_config.TextColumn("Pos",           width="small"),
                    "gp":              st.column_config.NumberColumn("GP",          format="%d"),
                    cat:               st.column_config.NumberColumn(f"{cat} (Season)", format="%.0f"),
                    proj_col:          st.column_config.NumberColumn(f"{cat} Proj", format="%.1f"),
                    "remaining_games": st.column_config.NumberColumn("Games Left",  format="%d"),
                },
            )
            if not threats.empty:
                top_t   = threats.iloc[0]
                new_opp = opp + top_t[proj_col]
                if new_opp > mine:
                    st.warning(
                        f"⚠️ Opponent adding **{top_t['name']}** could flip {cat} "
                        f"(+{top_t[proj_col]:.1f} → {new_opp:.1f} vs your {mine:.1f})"
                    )
else:
    st.success("No leading categories within the close margin — nothing to defend right now.")

# ── Out of reach ──────────────────────────────────────────────────────────────

if out_of_reach:
    with st.expander(f"📊 {len(out_of_reach)} categor{'y' if len(out_of_reach)==1 else 'ies'} out of reach"):
        for row in sorted(out_of_reach, key=lambda r: r["Gap"]):
            cat = row["Category"]
            st.write(
                f"**{cat}**: {row[_my_col]:.1f} vs {row[_opp_col]:.1f}  "
                f"(gap: {row['Gap']:.1f}  P(win): {_pct(base_p[cat])}%)"
            )

# ── xWA helpers (shared by skater + goalie sections) ─────────────────────────

def _compute_skater_xwa(
    player_df: pd.DataFrame,
    cats: list,
    my_final: dict,
    opp_final: dict,
    my_remaining: dict,
    opp_remaining: dict,
    weights: dict,
    base_p: dict,
    games_col: str = "remaining_games",   # "remaining_games" or "games_14d_sched"
) -> pd.DataFrame:
    """
    Compute xWA and per-category P(win)% for each FA in player_df.
    Returns player_df with added columns: xwa, flip_score, P({cat})%.
    """
    records = []
    for idx, player in player_df.iterrows():
        gl  = int(player.get(games_col, 0))
        gp  = max(float(player.get("gp", 1) or 1), 1.0)
        xwa = 0.0
        cat_pcts: dict[str, int] = {}

        for cat in cats:
            w = weights.get(cat, 1.0)
            if cat in _RATIO_CATS:
                p_with = base_p[cat]
            else:
                rpg    = _player_rpg(player, cat, gp)
                fa_mu  = rpg * gl
                fa_var = fa_mu
                mu_gap  = my_final[cat] + fa_mu - opp_final[cat]
                var_gap = my_remaining[cat][1] + fa_var + opp_remaining[cat][1]
                p_with  = _p_win(mu_gap, var_gap)
            xwa += w * (p_with - base_p[cat])
            cat_pcts[f"P({cat})%"] = _pct(p_with)

        flip_score = sum(
            float(player.get(f"{c}_proj", 0) or 0) * weights.get(c, 1.0)
            for c in cats if f"{c}_proj" in player_df.columns
        )
        records.append({"idx": idx, "xwa": round(xwa, 3),
                        "flip_score": round(flip_score, 1), **cat_pcts})

    xwa_frame = pd.DataFrame(records).set_index("idx")
    return player_df.join(xwa_frame)

# ── Best All-Round Pickup — This Week xWA ─────────────────────────────────────

st.divider()
st.subheader("⭐ Best All-Round Pickup — This Week")

if _SCIPY:
    st.caption(
        "**xWA** = Σ weight × [P(win cat | +player) − P(win cat | base)] across all categories.  "
        "Two 20% boosts outrank a single 35% spike."
    )

fa_scored_wk = _compute_skater_xwa(
    fa_df, CATS, my_final, opp_final,
    my_remaining, opp_remaining, weights, base_p,
    games_col="remaining_games",
)

rank_col  = "xwa" if _SCIPY else "flip_score"
p_cols    = [f"P({cat})%" for cat in CATS]

best_wk = (
    fa_scored_wk[["name", "team", "position", "gp", "total_z", "xwa", "flip_score"] + p_cols]
    .sort_values(rank_col, ascending=False).head(15).reset_index(drop=True)
)
best_wk.insert(0, "Rank", range(1, len(best_wk) + 1))
best_wk.insert(2, "logo", best_wk["team"].map(_LOGO))

display_wk = ["Rank", "logo", "name", "position", "gp", "total_z", "xwa", "flip_score"] + p_cols
p_col_cfg  = {
    f"P({cat})%": st.column_config.NumberColumn(f"P({cat})", format="%d%%", width="small")
    for cat in CATS
}

st.dataframe(
    best_wk[[c for c in display_wk if c in best_wk.columns]],
    use_container_width=True, hide_index=True,
    column_config={
        "logo":       st.column_config.ImageColumn("Team",        width="small"),
        "name":       st.column_config.TextColumn("Player"),
        "position":   st.column_config.TextColumn("Pos",          width="small"),
        "gp":         st.column_config.NumberColumn("GP",         format="%d",   width="small"),
        "total_z":    st.column_config.NumberColumn("Z",          format="%.1f", width="small"),
        "xwa":        st.column_config.NumberColumn("xWA",        format="%+.2f"),
        "flip_score": st.column_config.NumberColumn("Flip Score", format="%.1f", width="small"),
        **p_col_cfg,
    },
)

# ── Best All-Round Pickup — 2-Week xWA ────────────────────────────────────────

st.subheader("📅 Best All-Round Pickup — 2-Week View")
st.caption(
    "Same xWA model but FA contributions projected over **14 days** instead of remaining matchup days.  \n"
    "Use this when picking up a player late in the week who you need utility from next week too.  \n"
    "Baseline opponent and current matchup standings are kept the same — category gap probabilities  \n"
    "simply reflect more games in the FA's schedule."
)

fa_scored_14 = _compute_skater_xwa(
    fa_df, CATS, my_final, opp_final,
    my_remaining, opp_remaining, weights, base_p,
    games_col="games_14d_sched",
)

best_14 = (
    fa_scored_14[["name", "team", "position", "gp", "total_z", "games_14d_sched",
                  "xwa", "flip_score"] + p_cols]
    .sort_values(rank_col, ascending=False).head(15).reset_index(drop=True)
)
best_14.insert(0, "Rank", range(1, len(best_14) + 1))
best_14.insert(2, "logo", best_14["team"].map(_LOGO))

display_14 = ["Rank", "logo", "name", "position", "gp", "total_z",
              "games_14d_sched", "xwa", "flip_score"] + p_cols

st.dataframe(
    best_14[[c for c in display_14 if c in best_14.columns]],
    use_container_width=True, hide_index=True,
    column_config={
        "logo":             st.column_config.ImageColumn("Team",        width="small"),
        "name":             st.column_config.TextColumn("Player"),
        "position":         st.column_config.TextColumn("Pos",          width="small"),
        "gp":               st.column_config.NumberColumn("GP",         format="%d",   width="small"),
        "total_z":          st.column_config.NumberColumn("Z",          format="%.1f", width="small"),
        "games_14d_sched":  st.column_config.NumberColumn("Games 14d",  format="%d",   width="small"),
        "xwa":              st.column_config.NumberColumn("xWA (14d)",  format="%+.2f"),
        "flip_score":       st.column_config.NumberColumn("Score (14d)", format="%.1f", width="small"),
        **p_col_cfg,
    },
)
st.caption(
    "**xWA (14d)** uses the same P(win cat) formula as the weekly table.  \n"
    "A player with 8 games in 14 days scores much higher here than someone with 2 remaining this week."
)

# ── Goalie Pickups ────────────────────────────────────────────────────────────

st.divider()
st.subheader("🥅 Goalie Pickups")

if goalie_df is None or goalie_df.empty:
    st.info("Click **🥅 Goalies** in the sidebar to fetch goalie stats.")
elif not standings:
    st.warning("NHL standings unavailable — cannot compute win probabilities. Refresh the page to retry.")
else:
    goalie_fa = (
        goalie_df[goalie_df["is_fa"] & ~goalie_df["injury_flag"].astype(bool)]
        .copy()
    )

    if goalie_fa.empty:
        st.info("No FA goalies available.")
    else:
        # ── Per-goalie win probability model ──────────────────────────────────
        # start_rate = fraction of team games this goalie has started this season
        # proj_W (week) = Σ_{remaining games} start_rate × p_win(game)
        # proj_W (14d)  = same over 14-day window

        def _goalie_projections(goalie_row, days: int) -> dict:
            team  = goalie_row.get("team", "")
            g_gp  = max(int(goalie_row.get("gp", 0)), 0)
            t_gp  = max(standings.get(team, {}).get("gp", 1), 1)
            start_rate = min(g_gp / t_gp, 1.0)

            games = _team_game_probs(team, schedule, standings, today_str, days)

            proj_starts = sum(start_rate for _ in games)
            proj_w      = sum(start_rate * g["p_win"] for g in games)
            proj_w_var  = sum(start_rate * g["p_win"] * (1 - g["p_win"]) for g in games)
            sa_pg       = float(goalie_row.get("SA", 0) or 0) / max(g_gp, 1)
            proj_sa     = sa_pg * proj_starts
            svp         = float(goalie_row.get("SVP", 0) or 0)
            proj_saves  = svp * proj_sa

            return {
                "start_rate":   round(start_rate, 3),
                "team_games":   len(games),
                "proj_starts":  round(proj_starts, 2),
                "proj_W":       round(proj_w, 2),
                "proj_W_var":   round(proj_w_var, 4),
                "proj_SA":      round(proj_sa, 1),
                "proj_saves":   round(proj_saves, 1),
            }

        week_proj = goalie_fa.apply(
            lambda r: pd.Series(_goalie_projections(r, remaining_days)), axis=1
        )
        twoweek_proj = goalie_fa.apply(
            lambda r: pd.Series(_goalie_projections(r, 14)), axis=1
        )

        goalie_fa = goalie_fa.join(week_proj)

        # SV% net impact on my team's cumulative SV% this week
        my_svp_live   = float(my_stats.get("SV%", my_stats.get("SVP", 0)) or 0)
        my_shots_live = float(my_stats.get("SA", 0) or 0)
        if my_svp_live > 0 and my_shots_live > 0:
            my_saves_live = my_svp_live * my_shots_live
            goalie_fa["svp_delta"] = (
                (my_saves_live + goalie_fa["proj_saves"]) /
                (my_shots_live + goalie_fa["proj_SA"]) - my_svp_live
            ).round(4)
            show_svp_delta = True
        else:
            show_svp_delta = False

        # ── Goalie xWA for W category ─────────────────────────────────────────
        # My rostered goalies' remaining W projection + live W (if W in CATS)
        if _SCIPY:
            my_goalie_df  = goalie_df[goalie_df["team_number"] == my_team_num]
            opp_goalie_df = goalie_df[goalie_df["team_number"] == opp_num] if opp_num else pd.DataFrame()

            def _goalie_roster_W(roster_g_df: pd.DataFrame, days: int) -> tuple[float, float]:
                mu, var = 0.0, 0.01
                for _, g in roster_g_df.iterrows():
                    p = _goalie_projections(g, days)
                    mu  += p["proj_W"]
                    var += p["proj_W_var"]
                return mu, var

            my_g_mu_wk,  my_g_var_wk  = _goalie_roster_W(my_goalie_df,  remaining_days)
            opp_g_mu_wk, opp_g_var_wk = _goalie_roster_W(opp_goalie_df, remaining_days)

            live_W_my  = float(my_stats.get("W", 0)  or 0)
            live_W_opp = float(opp_stats.get("W", 0) or 0)

            base_p_W = _p_win(
                (live_W_my + my_g_mu_wk) - (live_W_opp + opp_g_mu_wk),
                my_g_var_wk + opp_g_var_wk,
            )

            xwa_G_list = []
            for idx, g_row in goalie_fa.iterrows():
                fa_proj = _goalie_projections(g_row, remaining_days)
                fa_mu   = fa_proj["proj_W"]
                fa_var  = fa_proj["proj_W_var"]
                mu_gap  = (live_W_my + my_g_mu_wk + fa_mu) - (live_W_opp + opp_g_mu_wk)
                var_gap = my_g_var_wk + fa_var + opp_g_var_wk
                p_with  = _p_win(mu_gap, var_gap)
                w_cat   = weights.get("W", 1.0)
                xwa_G_list.append({
                    "idx":        idx,
                    "xWA(W)":     round(w_cat * (p_with - base_p_W), 3),
                    "P(win W)%":  _pct(p_with),
                })

            xwa_G_df = pd.DataFrame(xwa_G_list).set_index("idx")
            goalie_fa = goalie_fa.join(xwa_G_df)
            has_goalie_xwa = True
        else:
            has_goalie_xwa = False

        # ── Sort and display ──────────────────────────────────────────────────
        sort_col_g = "xWA(W)" if has_goalie_xwa else "proj_W"
        goalie_fa  = goalie_fa.sort_values(sort_col_g, ascending=False).reset_index(drop=True)
        goalie_fa.insert(0, "Rank", range(1, len(goalie_fa) + 1))
        goalie_fa.insert(2, "logo", goalie_fa["team"].map(_LOGO))

        display_g = [
            "Rank", "logo", "name", "gp",
            "W", "SVP", "GAA",
            "start_rate", "team_games",
            "proj_starts", "proj_W",
            "proj_SA", "proj_saves",
        ]
        if has_goalie_xwa:
            display_g += ["xWA(W)", "P(win W)%"]
        if show_svp_delta:
            display_g.append("svp_delta")

        col_cfg_g = {
            "logo":        st.column_config.ImageColumn("Team",       width="small"),
            "name":        st.column_config.TextColumn("Goalie"),
            "gp":          st.column_config.NumberColumn("GP",        format="%d",    width="small"),
            "W":           st.column_config.NumberColumn("W (ssn)",   format="%.0f",  width="small"),
            "SVP":         st.column_config.NumberColumn("SV%",       format="%.3f",  width="small"),
            "GAA":         st.column_config.NumberColumn("GAA",       format="%.2f",  width="small"),
            "start_rate":  st.column_config.NumberColumn("Start%",    format="%.0%",  width="small",
                               help="Fraction of team games started this season"),
            "team_games":  st.column_config.NumberColumn("Team G",    format="%d",    width="small",
                               help="Team games remaining in window"),
            "proj_starts": st.column_config.NumberColumn("Proj GS",   format="%.1f",
                               help="Expected starts = team games × start rate"),
            "proj_W":      st.column_config.NumberColumn("Proj W",    format="%.2f",
                               help="Σ start_rate × P(team wins) per game — Pythagorean log5 model"),
            "proj_SA":     st.column_config.NumberColumn("Proj SA",   format="%.0f",  width="small"),
            "proj_saves":  st.column_config.NumberColumn("Proj SV",   format="%.0f",  width="small"),
        }
        if has_goalie_xwa:
            col_cfg_g["xWA(W)"]    = st.column_config.NumberColumn(
                "xWA (W)", format="%+.3f",
                help="Marginal expected win added in the W category from adding this goalie")
            col_cfg_g["P(win W)%"] = st.column_config.NumberColumn(
                "P(win W)", format="%d%%", width="small")
        if show_svp_delta:
            col_cfg_g["svp_delta"] = st.column_config.NumberColumn(
                "SV% Δ", format="%+.4f",
                help="Net shift in your team's cumulative SV% from adding this goalie")

        st.dataframe(
            goalie_fa[[c for c in display_g if c in goalie_fa.columns]],
            use_container_width=True, hide_index=True,
            column_config=col_cfg_g,
        )

        # 2-week goalie table
        with st.expander("📅 Goalie 2-week view"):
            goalie_fa_14 = goalie_df[
                goalie_df["is_fa"] & ~goalie_df["injury_flag"].astype(bool)
            ].copy()
            w14 = goalie_fa_14.apply(
                lambda r: pd.Series(_goalie_projections(r, 14)), axis=1
            )
            goalie_fa_14 = goalie_fa_14.join(w14).sort_values("proj_W", ascending=False).reset_index(drop=True)
            goalie_fa_14.insert(0, "Rank", range(1, len(goalie_fa_14) + 1))
            goalie_fa_14.insert(2, "logo", goalie_fa_14["team"].map(_LOGO))
            st.dataframe(
                goalie_fa_14[["Rank", "logo", "name", "gp", "W", "SVP", "GAA",
                              "start_rate", "team_games", "proj_starts", "proj_W"]],
                use_container_width=True, hide_index=True,
                column_config={
                    "logo":        st.column_config.ImageColumn("Team",     width="small"),
                    "name":        st.column_config.TextColumn("Goalie"),
                    "gp":          st.column_config.NumberColumn("GP",      format="%d",   width="small"),
                    "W":           st.column_config.NumberColumn("W (ssn)", format="%.0f", width="small"),
                    "SVP":         st.column_config.NumberColumn("SV%",     format="%.3f", width="small"),
                    "GAA":         st.column_config.NumberColumn("GAA",     format="%.2f", width="small"),
                    "start_rate":  st.column_config.NumberColumn("Start%",  format="%.0%", width="small"),
                    "team_games":  st.column_config.NumberColumn("Team G",  format="%d",   width="small"),
                    "proj_starts": st.column_config.NumberColumn("Proj GS", format="%.1f"),
                    "proj_W":      st.column_config.NumberColumn("Proj W (14d)", format="%.2f"),
                },
            )

        st.caption(
            "**Proj W** = Σ (start rate × P(team wins)) per remaining game.  \n"
            "**P(team wins)** via Pythagorean win% (GF²/(GF²+GA²)) + log5 head-to-head from NHL standings.  \n"
            "Home teams receive a +2.5% edge.  \n"
            + (f"**SV% Δ** uses your team's current week: {my_svp_live:.3f} SV% on {my_shots_live:.0f} SA.  \n"
               if show_svp_delta else "")
            + "**Start%** = goalie season GP ÷ team season GP — not confirmed lineups."
        )
