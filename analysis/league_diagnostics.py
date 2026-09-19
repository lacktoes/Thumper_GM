"""
league_diagnostics.py — why do we keep losing?

Reads the PWW dashboard's data.json (12-cat Yahoo H2H league results) and
answers three questions that drive draft strategy:

  1. WHICH CATEGORIES DECIDE THIS LEAGUE?
     Average weekly league rank per category for the top-4 teams vs the
     bottom-4. A large gap means that category separates winners from losers;
     a small gap means everyone is roughly equal there and it is not where
     matchups are won.

  2. WHICH CATEGORIES CAN YOU ACTUALLY OWN?
     Split each category's variance into between-team spread (how different
     teams are from each other) and within-team week-to-week noise (how much
     one team bounces around). Weekly values are normalised by that week's
     league mean first, so schedule density does not pollute the signal.

         ownability = between_sd / within_sd

     High ratio => a team's standing in that category is a stable property of
     its roster, so it can be locked in at the draft. Low ratio => week-to-week
     randomness swamps roster quality, so paying up for it at the draft is
     wasted capital.

  3. HOW DID WE ACTUALLY LOSE?
     Per-matchup category scores, split into narrow vs blowout results, to
     separate "roster is short" from "unlucky in close weeks".

Usage:
    python analysis/league_diagnostics.py --data ../pww-hockey/docs/data.json
    python analysis/league_diagnostics.py --data <path> --team "Thumpers 🏒"
    python analysis/league_diagnostics.py --data <path> --season 2025-26

Multi-season files (once the year-filter work lands) are handled via --season;
omit it to use the only/most recent season present.
"""

import argparse
import json
import math
import statistics as st
from collections import defaultdict
from pathlib import Path

# Yahoo returns these 12 scoring categories positionally, in this order.
CATS = ["G", "A", "PIM", "PPP", "SOG", "FW", "HIT", "BLK", "W", "SV", "SV%", "SHO"]

# For every category in this league, a higher value is better (GAA is not scored).
HIGHER_IS_BETTER = {c: True for c in CATS}


# ── Loading ───────────────────────────────────────────────────────────────────

def load_weeks(path: Path, season: str | None):
    """
    Return (weeks, label) where weeks maps int week -> week object, excluding any
    in-progress week. Accepts both the legacy flat file and the multi-season
    format {"seasons": {"2025-26": {...}}}.
    """
    data = json.loads(path.read_text())

    if "seasons" in data:
        seasons = data["seasons"]
        if season is None:
            season = max(seasons)
        if season not in seasons:
            raise SystemExit(f"Season {season!r} not in file. Available: {', '.join(sorted(seasons))}")
        node, label = seasons[season], season
    else:
        node, label = data, (season or "single-season file")

    weeks = {
        int(k): v for k, v in node.get("weeks", {}).items()
        if not v.get("is_current")
    }
    if not weeks:
        raise SystemExit("No completed weeks found.")
    return weeks, label


# ── Core metrics ──────────────────────────────────────────────────────────────

def category_points(weeks):
    """Yahoo scoring: category win = 2 pts, tie = 1. Returns {team: points}."""
    pts = defaultdict(float)
    for wk in weeks.values():
        for m in wk.get("matchups", []):
            for team, side in ((m["t1"], "1"), (m["t2"], "2")):
                for c in m["cats"]:
                    if c == side:
                        pts[team] += 2
                    elif c == "T":
                        pts[team] += 1
    return pts


def weekly_ranks(weeks):
    """{team: {cat: [weekly league rank, 1 = best]}}."""
    ranks = defaultdict(lambda: defaultdict(list))
    for wk in weeks.values():
        stats = wk.get("stats", {})
        for c in CATS:
            sign = -1 if HIGHER_IS_BETTER[c] else 1
            order = sorted(stats, key=lambda t: sign * float(stats[t].get(c, 0)))
            for pos, t in enumerate(order, 1):
                ranks[t][c].append(pos)
    return ranks


def ownability(weeks):
    """
    {cat: (between_sd, within_sd, ratio)} on league-mean-normalised weekly values.
    Normalising per week removes the shared effect of how many NHL games fell in
    that week, leaving only relative roster strength.
    """
    norm = {c: defaultdict(list) for c in CATS}
    for wk in sorted(weeks):
        stats = weeks[wk].get("stats", {})
        for c in CATS:
            vals = [float(stats[t].get(c, 0)) for t in stats]
            mu = st.mean(vals) if vals else 0.0
            for t in stats:
                norm[c][t].append(float(stats[t].get(c, 0)) / mu if mu else 0.0)

    out = {}
    for c in CATS:
        series = norm[c]
        if not series:
            continue
        between = st.pstdev([st.mean(v) for v in series.values()])
        within = st.mean([st.pstdev(v) for v in series.values() if len(v) > 1])
        out[c] = (between, within, between / within if within else 0.0)
    return out


def matchup_log(weeks, team):
    """[(week, opponent, cat_score_out_of_12)] for one team, chronological."""
    log = []
    for w in sorted(weeks):
        for m in weeks[w].get("matchups", []):
            if team not in (m["t1"], m["t2"]):
                continue
            side = "1" if m["t1"] == team else "2"
            opp = m["t2"] if side == "1" else m["t1"]
            score = (sum(1 for c in m["cats"] if c == side)
                     + 0.5 * sum(1 for c in m["cats"] if c == "T"))
            log.append((w, opp, score))
    return log


def pearson(xs, ys):
    mx, my = st.mean(xs), st.mean(ys)
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    den = math.sqrt(sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys))
    return num / den if den else 0.0


# ── Report ────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="Path to PWW data.json")
    ap.add_argument("--team", default="Thumpers 🏒", help="Team to diagnose")
    ap.add_argument("--season", default=None, help="Season label in a multi-season file")
    args = ap.parse_args()

    weeks, label = load_weeks(Path(args.data), args.season)
    pts = category_points(weeks)
    ranks = weekly_ranks(weeks)
    order = sorted(pts, key=lambda t: -pts[t])
    me = args.team

    if me not in pts:
        raise SystemExit(f"Team {me!r} not found. Teams: {', '.join(sorted(pts))}")

    n_teams = len(pts)
    print(f"LEAGUE DIAGNOSTICS — {label}   ({len(weeks)} completed weeks, {n_teams} teams)")
    print("=" * 78)

    print("\n1. FINAL STANDINGS (category points: win=2, tie=1)")
    for i, t in enumerate(order, 1):
        mark = "  <<<" if t == me else ""
        print(f"  {i:2d}. {t:26s} {pts[t]:6.0f}{mark}")

    print("\n2. WHICH CATEGORIES DECIDE THIS LEAGUE?")
    print("   Average weekly league rank (1 = best). 'gap' = how much the top of")
    print("   the table separates from the bottom in that category.")
    q = max(2, n_teams // 3)
    top, bot = order[:q], order[-q:]
    print(f"\n   {'CAT':6s}{'top'+str(q):>8s}{'bot'+str(q):>8s}{'gap':>8s}{'you':>8s}   verdict")
    rows = []
    for c in CATS:
        a = st.mean([st.mean(ranks[t][c]) for t in top])
        b = st.mean([st.mean(ranks[t][c]) for t in bot])
        rows.append((b - a, c, a, b, st.mean(ranks[me][c])))
    for gap, c, a, b, mine in sorted(rows, reverse=True):
        flag = "DECISIVE" if gap >= 2.0 else ("matters" if gap >= 1.0 else "not where games are won")
        warn = "  <-- YOUR HOLE" if mine > b else ""
        print(f"   {c:6s}{a:8.2f}{b:8.2f}{gap:8.2f}{mine:8.2f}   {flag}{warn}")

    print("\n3. WHICH CATEGORIES CAN YOU OWN? (between-team sd / week-to-week sd)")
    print("   High = stable roster property, lockable at the draft.")
    print("   Low  = noise dominates, paying up for it is wasted draft capital.")
    print(f"\n   {'CAT':6s}{'between':>10s}{'within':>9s}{'ratio':>8s}   verdict")
    for c, (b, w, r) in sorted(ownability(weeks).items(), key=lambda kv: -kv[1][2]):
        verdict = ("LOCKABLE — build around it" if r > 0.55
                   else "moderate" if r > 0.35 else "NOISY — do not chase")
        print(f"   {c:6s}{b:10.3f}{w:9.3f}{r:8.2f}   {verdict}")

    print(f"\n4. SPIKY vs FLAT — does having an identity pay?")
    xs, ys = [], []
    for t in pts:
        avgs = [st.mean(ranks[t][c]) for c in CATS]
        xs.append(st.pstdev(avgs))
        ys.append(pts[t])
    r = pearson(xs, ys)
    print(f"   corr(rank spread, points) = {r:+.3f}   (n={len(xs)} teams)")
    print(f"   NOTE: n={len(xs)} is far too small for this to be significant on its own.")
    print("   Read it alongside the per-team detail below, not as a finding by itself.")
    print(f"\n   {'TEAM':26s}{'spread':>8s}{'top3':>6s}{'bot3':>6s}{'pts':>7s}")
    for t in order:
        avgs = [st.mean(ranks[t][c]) for c in CATS]
        hi = sum(1 for a in avgs if a <= 3.5)
        lo = sum(1 for a in avgs if a >= n_teams - 2.5)
        mark = "  <<<" if t == me else ""
        print(f"   {t:26s}{st.pstdev(avgs):8.2f}{hi:6d}{lo:6d}{pts[t]:7.0f}{mark}")

    print(f"\n5. HOW {me} ACTUALLY LOST")
    log = matchup_log(weeks, me)
    scores = [s for _, _, s in log]
    half = len(CATS) / 2
    narrow_l = sum(1 for s in scores if half - 1.5 <= s < half)
    narrow_w = sum(1 for s in scores if half < s <= half + 1.5)
    print(f"   avg category score {st.mean(scores):.2f} / {len(CATS)}   (>{half:.0f} = win)")
    print(f"   narrow losses {narrow_l:2d}   blowout losses {sum(1 for s in scores if s < half - 1.5):2d}")
    print(f"   narrow wins   {narrow_w:2d}   blowout wins   {sum(1 for s in scores if s > half + 1.5):2d}")
    print(f"\n   {'WK':>4s}  {'RES':3s} {'SCORE':>11s}  OPPONENT")
    for w, opp, s in log:
        res = "W" if s > half else ("L" if s < half else "T")
        print(f"   {w:4d}  {res:3s} {s:5.1f}-{len(CATS)-s:5.1f}  {opp}")

    print(f"\n6. {me} PER-CATEGORY WIN RATE")
    won = defaultdict(float)
    n = 0
    for wk in weeks.values():
        for m in wk.get("matchups", []):
            if me not in (m["t1"], m["t2"]):
                continue
            n += 1
            side = "1" if m["t1"] == me else "2"
            for i, c in enumerate(m["cats"]):
                if c == side:
                    won[CATS[i]] += 1
                elif c == "T":
                    won[CATS[i]] += 0.5
    print(f"\n   {'CAT':6s}{'winrate':>10s}{'avg rank':>10s}")
    for c in sorted(CATS, key=lambda c: won[c]):
        print(f"   {c:6s}{won[c]/n:9.1%}{st.mean(ranks[me][c]):10.1f}")


if __name__ == "__main__":
    main()
