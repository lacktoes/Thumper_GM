"""
league_diagnostics.py — why do we keep losing?

Reads the PWW dashboard's season files (12-cat Yahoo H2H league results) and
answers the questions that drive draft strategy.

  1. WHICH CATEGORIES DECIDE THIS LEAGUE?
     Compare the top third of the table against the bottom third in each
     category. A large gap means that category separates winners from losers;
     a small gap means everyone is roughly equal there and matchups are not
     won or lost on it.

  2. WHICH CATEGORIES CAN YOU ACTUALLY OWN?
     Split each category's variance into between-team spread (how different
     teams are from each other) and within-team week-to-week noise (how much
     one team bounces around). Weekly values are normalised by that week's
     league mean first, so schedule density does not pollute the signal.

         ownability = between_sd / within_sd

     High ratio => a team's standing is a stable property of its roster, so it
     can be locked in at the draft. Low ratio => week-to-week randomness swamps
     roster quality, so paying up for it at the draft is wasted capital.

  3. HOW DID WE ACTUALLY LOSE?
     Per-matchup category scores, split narrow vs blowout, to separate "roster
     is short" from "unlucky in close weeks".

  4. DOES IT HOLD UP ACROSS SEASONS?  (--dir)
     Runs 1 and 2 over every season on disk. One season is an anecdote; a
     category that is decisive every year is a strategy.

LEAGUE SIZE
-----------
This league had 10 teams until 2024-25 and 12 after, so raw league rank is NOT
comparable across seasons: mid-table is 5.5 in a 10-team league and 6.5 in a
12-team one. Everything cross-season is therefore reported as a PERCENTILE:

    pct = (n_teams - rank) / (n_teams - 1)      1.00 = best in league, 0.00 = worst

Single-season output still shows raw ranks, which are easier to read when the
league size is fixed and known.

Usage:
    # one season
    python analysis/league_diagnostics.py --data ../pww-hockey/docs/data.json
    python analysis/league_diagnostics.py --data <path> --team "Thumpers"

    # every season on disk -- the question that governs draft strategy
    python analysis/league_diagnostics.py --dir ../pww-hockey/docs
"""

import argparse
import json
import math
import statistics as st
import unicodedata
from collections import defaultdict
from pathlib import Path

# Yahoo returns these 12 scoring categories positionally, in this order.
CATS = ["G", "A", "PIM", "PPP", "SOG", "FW", "HIT", "BLK", "W", "SV", "SV%", "SHO"]


# ── Loading ───────────────────────────────────────────────────────────────────

def load_season(path, season=None):
    """Return (weeks, label, teams_meta). Handles flat and nested season files."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    if "seasons" in data:                      # hypothetical nested shape
        seasons = data["seasons"]
        season = season or max(seasons)
        if season not in seasons:
            raise SystemExit("Season {!r} not in file. Available: {}".format(
                season, ", ".join(sorted(seasons))))
        node, label = seasons[season], season
    else:
        node = data
        label = season or node.get("meta", {}).get("season") or Path(path).stem

    weeks = {int(k): v for k, v in node.get("weeks", {}).items()
             if not v.get("is_current")}
    return weeks, label, node.get("teams", {})


def discover_season_files(dirpath):
    """[(season_label, path)] for every data-<season>.json, oldest first."""
    out = []
    for p in sorted(Path(dirpath).glob("data-*.json")):
        out.append((p.stem[len("data-"):], p))
    return out


# ── Team identity across seasons ──────────────────────────────────────────────

def _norm(s):
    nfkd = unicodedata.normalize("NFKD", str(s))
    ascii_only = nfkd.encode("ascii", "ignore").decode("ascii")
    return "".join(c for c in ascii_only.lower() if c.isalnum())


def resolve_team(want, teams_meta, standings_keys):
    """
    Find a team in one season given a name, manager nickname, or manager guid.

    `want` may be several comma-separated identifiers, tried in order. That
    matters because team names change between seasons and older season files
    predate manager-guid capture, so no single identifier necessarily resolves
    every season. Passing "GUID,Old Name,New Name" covers the whole history.

    Within one identifier the order is:
      1. exact display name
      2. manager guid
      3. manager nickname
      4. loose name match (accents/case/punctuation stripped, then substring)
    """
    for token in [t.strip() for t in str(want).split(",") if t.strip()]:
        hit = _resolve_one(token, teams_meta, standings_keys)
        if hit:
            return hit
    return None


def _resolve_one(want, teams_meta, standings_keys):
    if want in standings_keys:
        return want

    for name, meta in (teams_meta or {}).items():
        if meta.get("guid") and meta["guid"] == want:
            return name

    wn = _norm(want)
    if not wn:
        return None

    for name, meta in (teams_meta or {}).items():
        if meta.get("manager") and _norm(meta["manager"]) == wn:
            return name

    for name in standings_keys:
        n = _norm(name)
        if n == wn or wn in n or n in wn:
            return name
    return None


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
    """{team: {cat: [weekly league rank, 1 = best]}}. Higher stat is better for all 12."""
    ranks = defaultdict(lambda: defaultdict(list))
    for wk in weeks.values():
        stats = wk.get("stats", {})
        for c in CATS:
            order = sorted(stats, key=lambda t: -float(stats[t].get(c, 0)))
            for pos, t in enumerate(order, 1):
                ranks[t][c].append(pos)
    return ranks


def to_pct(rank, n_teams):
    """Rank (1 = best) -> percentile (1.0 = best). Comparable across league sizes."""
    if n_teams <= 1:
        return 0.5
    return (n_teams - rank) / (n_teams - 1)


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
        means = [st.mean(v) for v in series.values()]
        withins = [st.pstdev(v) for v in series.values() if len(v) > 1]
        if not withins:
            continue
        between = st.pstdev(means)
        within = st.mean(withins)
        out[c] = (between, within, between / within if within else 0.0)
    return out


def decisiveness(weeks, n_teams):
    """
    {cat: (top_pct, bot_pct, gap)} using percentiles so seasons of different
    league sizes can be compared. Top/bottom third of the table by category points.
    """
    pts = category_points(weeks)
    ranks = weekly_ranks(weeks)
    order = sorted(pts, key=lambda t: -pts[t])
    q = max(2, len(order) // 3)
    top, bot = order[:q], order[-q:]

    out = {}
    for c in CATS:
        a = st.mean([to_pct(st.mean(ranks[t][c]), n_teams) for t in top])
        b = st.mean([to_pct(st.mean(ranks[t][c]), n_teams) for t in bot])
        out[c] = (a, b, a - b)
    return out


def matchup_log(weeks, team):
    """[(week, opponent, cat_score)] for one team, chronological."""
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


# ── Single-season report ──────────────────────────────────────────────────────

def report_season(weeks, label, teams_meta, want_team):
    pts = category_points(weeks)
    ranks = weekly_ranks(weeks)
    order = sorted(pts, key=lambda t: -pts[t])
    n_teams = len(pts)

    me = resolve_team(want_team, teams_meta, set(pts))
    if not me:
        raise SystemExit("Team {!r} not found. Teams: {}".format(
            want_team, ", ".join(sorted(pts))))

    print("LEAGUE DIAGNOSTICS — {}   ({} completed weeks, {} teams)".format(
        label, len(weeks), n_teams))
    print("=" * 78)

    print("\n1. FINAL STANDINGS (category points: win=2, tie=1)")
    for i, t in enumerate(order, 1):
        print("  {:2d}. {:26s} {:6.0f}{}".format(i, t, pts[t], "  <<<" if t == me else ""))

    print("\n2. WHICH CATEGORIES DECIDE THIS LEAGUE?")
    print("   Percentile of league (1.00 = best team in that category, 0.00 = worst).")
    q = max(2, n_teams // 3)
    dec = decisiveness(weeks, n_teams)
    print("\n   {:6s}{:>9s}{:>9s}{:>8s}{:>8s}   verdict".format(
        "CAT", "top" + str(q), "bot" + str(q), "gap", "you"))
    for c, (a, b, gap) in sorted(dec.items(), key=lambda kv: -kv[1][2]):
        mine = to_pct(st.mean(ranks[me][c]), n_teams)
        flag = ("DECISIVE" if gap >= 0.20 else
                "matters" if gap >= 0.10 else "not where games are won")
        warn = "  <-- YOUR HOLE" if mine < b else ""
        print("   {:6s}{:9.2f}{:9.2f}{:8.2f}{:8.2f}   {}{}".format(
            c, a, b, gap, mine, flag, warn))

    print("\n3. WHICH CATEGORIES CAN YOU OWN? (between-team sd / week-to-week sd)")
    print("   High = stable roster property, lockable at the draft.")
    print("   Low  = noise dominates, paying up for it is wasted draft capital.")
    print("\n   {:6s}{:>10s}{:>9s}{:>8s}   verdict".format("CAT", "between", "within", "ratio"))
    for c, (b, w, r) in sorted(ownability(weeks).items(), key=lambda kv: -kv[1][2]):
        verdict = ("LOCKABLE — build around it" if r > 0.55
                   else "moderate" if r > 0.35 else "NOISY — do not chase")
        print("   {:6s}{:10.3f}{:9.3f}{:8.2f}   {}".format(c, b, w, r, verdict))

    print("\n4. SPIKY vs FLAT — does having an identity pay?")
    xs, ys = [], []
    for t in pts:
        pcts = [to_pct(st.mean(ranks[t][c]), n_teams) for c in CATS]
        xs.append(st.pstdev(pcts))
        ys.append(pts[t])
    print("   corr(percentile spread, points) = {:+.3f}   (n={} teams)".format(
        pearson(xs, ys), len(xs)))
    print("   NOTE: n={} is far too small to be significant on its own.".format(len(xs)))
    print("\n   {:26s}{:>8s}{:>7s}{:>7s}{:>8s}".format("TEAM", "spread", "strong", "weak", "pts"))
    for t in order:
        pcts = [to_pct(st.mean(ranks[t][c]), n_teams) for c in CATS]
        strong = sum(1 for p in pcts if p >= 0.75)
        weak = sum(1 for p in pcts if p <= 0.25)
        print("   {:26s}{:8.3f}{:7d}{:7d}{:8.0f}{}".format(
            t, st.pstdev(pcts), strong, weak, pts[t], "  <<<" if t == me else ""))

    print("\n5. HOW {} ACTUALLY LOST".format(me))
    log = matchup_log(weeks, me)
    if log:
        scores = [s for _, _, s in log]
        half = len(CATS) / 2
        print("   avg category score {:.2f} / {}   (>{:.0f} = win)".format(
            st.mean(scores), len(CATS), half))
        print("   narrow losses {:2d}   blowout losses {:2d}".format(
            sum(1 for s in scores if half - 1.5 <= s < half),
            sum(1 for s in scores if s < half - 1.5)))
        print("   narrow wins   {:2d}   blowout wins   {:2d}".format(
            sum(1 for s in scores if half < s <= half + 1.5),
            sum(1 for s in scores if s > half + 1.5)))
        print("\n   {:>4s}  {:3s} {:>11s}  OPPONENT".format("WK", "RES", "SCORE"))
        for w, opp, s in log:
            res = "W" if s > half else ("L" if s < half else "T")
            print("   {:4d}  {:3s} {:5.1f}-{:5.1f}  {}".format(w, res, s, len(CATS) - s, opp))

    print("\n6. {} PER-CATEGORY WIN RATE".format(me))
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
    if n:
        print("\n   {:6s}{:>10s}{:>12s}".format("CAT", "winrate", "percentile"))
        for c in sorted(CATS, key=lambda c: won[c]):
            print("   {:6s}{:9.1%}{:12.2f}".format(
                c, won[c] / n, to_pct(st.mean(ranks[me][c]), n_teams)))


# ── Cross-season report ───────────────────────────────────────────────────────

def report_all(dirpath, want_team):
    files = discover_season_files(dirpath)
    if not files:
        raise SystemExit("No data-<season>.json files in {}. Run the backfill first.".format(dirpath))

    seasons = []
    for label, path in files:
        weeks, lab, meta = load_season(path, None)
        if not weeks:
            print("  (skipping {} -- no completed weeks)".format(label))
            continue
        pts = category_points(weeks)
        if not pts:
            continue
        me = resolve_team(want_team, meta, set(pts))
        seasons.append({
            "label": label, "weeks": weeks, "meta": meta,
            "n_teams": len(pts), "pts": pts, "me": me,
            "dec": decisiveness(weeks, len(pts)),
            "own": ownability(weeks),
            "ranks": weekly_ranks(weeks),
        })

    if not seasons:
        raise SystemExit("No usable seasons found.")

    print("CROSS-SEASON DIAGNOSTICS — {} season(s)".format(len(seasons)))
    print("=" * 78)
    print("\nAll figures are PERCENTILES (1.00 = best in league), so seasons with")
    print("different league sizes are directly comparable.\n")

    print("{:10s}{:>7s}{:>7s}  {:26s}{:>6s}".format(
        "SEASON", "TEAMS", "WEEKS", "YOUR TEAM", "FIN"))
    for s in seasons:
        order = sorted(s["pts"], key=lambda t: -s["pts"][t])
        fin = str(order.index(s["me"]) + 1) if s["me"] else "-"
        print("{:10s}{:7d}{:7d}  {:26s}{:>6s}".format(
            s["label"], s["n_teams"], len(s["weeks"]),
            s["me"] or "NOT FOUND", fin))

    missing = [s["label"] for s in seasons if not s["me"]]
    if missing:
        print("\n  !! Your team could not be resolved in: {}".format(", ".join(missing)))
        print("     Pass a comma-separated list covering those seasons, e.g.")
        print("       --team \"<guid>,Thumpers,<old team name>\"")
        print("     Season files fetched before manager-guid capture have no guid;")
        print("     refetching them fills it in and makes one identifier sufficient.")

    # --- decisiveness across seasons ---
    print("\n" + "-" * 78)
    print("WHICH CATEGORIES DECIDE THIS LEAGUE, SEASON BY SEASON?")
    print("Gap = top third's percentile minus bottom third's. Higher = more decisive.\n")
    hdr = "{:6s}".format("CAT") + "".join("{:>9s}".format(s["label"][-5:]) for s in seasons)
    print(hdr + "{:>8s}{:>8s}".format("MEAN", "WORST"))
    rows = []
    for c in CATS:
        gaps = [s["dec"][c][2] for s in seasons if c in s["dec"]]
        rows.append((st.mean(gaps), min(gaps), gaps, c))
    for mean_gap, worst, gaps, c in sorted(rows, reverse=True):
        line = "{:6s}".format(c) + "".join("{:9.2f}".format(g) for g in gaps)
        tag = "  DECISIVE EVERY SEASON" if worst >= 0.15 else ""
        print(line + "{:8.2f}{:8.2f}{}".format(mean_gap, worst, tag))

    # --- your standing across seasons ---
    if any(s["me"] for s in seasons):
        print("\n" + "-" * 78)
        print("YOUR PERCENTILE BY CATEGORY, SEASON BY SEASON")
        print("(1.00 = best in league that season, 0.50 = mid-table)\n")
        print(hdr + "{:>8s}".format("MEAN"))
        rows = []
        for c in CATS:
            vals = [to_pct(st.mean(s["ranks"][s["me"]][c]), s["n_teams"])
                    for s in seasons if s["me"]]
            rows.append((st.mean(vals), vals, c))
        for mean_v, vals, c in sorted(rows):
            line = "{:6s}".format(c) + "".join("{:9.2f}".format(v) for v in vals)
            tag = "  <-- CHRONIC WEAKNESS" if mean_v <= 0.35 else (
                  "  <-- your strength" if mean_v >= 0.65 else "")
            print(line + "{:8.2f}{}".format(mean_v, tag))

    # --- ownability across seasons ---
    print("\n" + "-" * 78)
    print("OWNABILITY, SEASON BY SEASON  (between-team sd / week-to-week sd)\n")
    print(hdr + "{:>8s}".format("MEAN"))
    rows = []
    for c in CATS:
        vals = [s["own"][c][2] for s in seasons if c in s["own"]]
        if vals:
            rows.append((st.mean(vals), vals, c))
    for mean_v, vals, c in sorted(rows, reverse=True):
        line = "{:6s}".format(c) + "".join("{:9.2f}".format(v) for v in vals)
        tag = "  LOCKABLE" if mean_v > 0.55 else ("  NOISY" if mean_v < 0.35 else "")
        print(line + "{:8.2f}{}".format(mean_v, tag))

    # --- the verdict that governs the draft ---
    print("\n" + "=" * 78)
    print("VERDICT")
    print("=" * 78)
    consistent = []
    for c in CATS:
        gaps = [s["dec"][c][2] for s in seasons if c in s["dec"]]
        owns = [s["own"][c][2] for s in seasons if c in s["own"]]
        if not gaps or not owns:
            continue
        if min(gaps) >= 0.15 and st.mean(owns) > 0.55:
            consistent.append((st.mean(gaps), c))
    if consistent:
        print("\nDecisive in EVERY season AND lockable at the draft:")
        for g, c in sorted(consistent, reverse=True):
            mine = ""
            if any(s["me"] for s in seasons):
                v = st.mean([to_pct(st.mean(s["ranks"][s["me"]][c]), s["n_teams"])
                             for s in seasons if s["me"]])
                mine = "   your mean percentile {:.2f}".format(v)
            print("  {:5s} mean gap {:.2f}{}".format(c, g, mine))
        print("\nThese are where the draft should be spent.")
    else:
        print("\nNo category is both consistently decisive and reliably lockable.")
        print("That itself is a finding: it argues for a balanced build rather")
        print("than a punt strategy.")

    if len(seasons) == 1:
        print("\nNOTE: only one season on disk. Run the backfill before trusting any")
        print("of this -- a single season cannot distinguish signal from a bad year.")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--data", help="One season file")
    g.add_argument("--dir", help="Directory of data-<season>.json files")
    ap.add_argument("--team", default="Thumpers",
                    help="Team name, manager nickname, or manager guid. Accepts a "
                         "comma-separated list tried in order, for seasons where the "
                         "team was renamed, e.g. --team \"GUID,Thumpers,Thumper Time\"")
    ap.add_argument("--season", default=None, help="Season label, for a nested file")
    args = ap.parse_args()

    if args.dir:
        report_all(args.dir, args.team)
    else:
        weeks, label, meta = load_season(args.data, args.season)
        if not weeks:
            raise SystemExit("No completed weeks found in {}".format(args.data))
        report_season(weeks, label, meta, args.team)


if __name__ == "__main__":
    main()
