"""
nhl_api.py — Fetch and merge NHL Stats API data.

Season aggregates (3 endpoints merged on playerId):
  skater/summary      → G, A, points, GP, PP (ppPoints), S (shots)
  skater/realtime     → HIT, BLK, PIM
  skater/faceoffwins  → FOW (totalFaceoffWins)

Per-game data (2 endpoints, isGame=true, merged on gameId+playerId):
  skater/summary  → G, A, PIM, PP, S per game
  skater/realtime → HIT, BLK per game
  FOW is NOT available per game from these endpoints (season rate used instead)

Date-range filtering via cayenneExp: gameDate>="YYYY-MM-DD" and gameDate<="YYYY-MM-DD 23:59:59"
Designed for incremental ingestion: call fetch_per_game_stats(since, until) with
only the date range that isn't yet in the DB.
"""
import requests
import time
from datetime import date, timedelta

STATS_BASE   = "https://api.nhle.com/stats/rest/en/skater"
GOALIE_BASE  = "https://api.nhle.com/stats/rest/en/goalie"
WEB_BASE     = "https://api-web.nhle.com/v1"
PAGE_SIZE    = 100

SEASON_START = "2025-10-01"   # first day of 2025-26 regular season


# ── Shared paginator ──────────────────────────────────────────────────────────

def _paginate(endpoint: str, extra_params: dict) -> list[dict]:
    """Paginate through a stats REST endpoint, returning all rows."""
    rows:    list[dict] = []
    start   = 0
    session = requests.Session()

    while True:
        params = {"limit": PAGE_SIZE, "start": start, **extra_params}
        try:
            r = session.get(f"{STATS_BASE}/{endpoint}", params=params, timeout=20)
            r.raise_for_status()
            payload = r.json()
        except Exception as exc:
            print(f"  [nhl_api] {endpoint} start={start} error: {exc}")
            break

        data = payload.get("data", [])
        rows.extend(data)
        total = payload.get("total", 0)
        start += PAGE_SIZE
        if start >= total:
            break
        time.sleep(0.1)

    return rows


# ── Season aggregate stats ────────────────────────────────────────────────────

def fetch_team_standings() -> dict[str, dict]:
    """
    Fetch current NHL standings from the Web API.
    Returns {team_abbrev: {gp, w, l, ot, gf, ga, home_gp, home_w, road_gp, road_w}}.
    Used to compute Pythagorean win probabilities for goalie projections.
    """
    url = f"{WEB_BASE}/standings/now"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        print(f"  [nhl_api] standings fetch error: {exc}")
        return {}

    result: dict[str, dict] = {}
    for entry in data.get("standings", []):
        abbrev = (entry.get("teamAbbrev") or {}).get("default", "")
        if not abbrev:
            continue
        result[abbrev] = {
            "gp":      int(entry.get("gamesPlayed", 0)    or 0),
            "w":       int(entry.get("wins", 0)           or 0),
            "l":       int(entry.get("losses", 0)         or 0),
            "ot":      int(entry.get("otLosses", 0)       or 0),
            "gf":      float(entry.get("goalFor", 0)      or 0),
            "ga":      float(entry.get("goalAgainst", 0)  or 0),
            "home_gp": int(entry.get("homeGamesPlayed", 0) or 0),
            "home_w":  int(entry.get("homeWins", 0)       or 0),
            "road_gp": int(entry.get("roadGamesPlayed", 0) or 0),
            "road_w":  int(entry.get("roadWins", 0)       or 0),
        }
    print(f"  [nhl_api] standings fetched for {len(result)} teams.")
    return result


def fetch_goalies(season_id: int) -> list[dict]:
    """
    Fetch season goalie stats from /goalie/summary.
    Returns [{player_id, name, team, position, gp, W, L, OTL, SVP, GAA, SO, SA, SV}]
    SVP is stored as a decimal (0.920, not 92.0).
    """
    season_exp = f"seasonId={season_id} and gameTypeId=2"
    rows: list[dict] = []
    start   = 0
    session = requests.Session()

    while True:
        params = {
            "limit": PAGE_SIZE,
            "start": start,
            "sort": "wins",
            "cayenneExp": season_exp,
        }
        try:
            r = session.get(f"{GOALIE_BASE}/summary", params=params, timeout=20)
            r.raise_for_status()
            payload = r.json()
        except Exception as exc:
            print(f"  [nhl_api] goalie/summary start={start} error: {exc}")
            break

        for g in payload.get("data", []):
            rows.append({
                "player_id": g.get("playerId"),
                "name":      g.get("goalieFullName", "Unknown"),
                "team":      (g.get("teamAbbrevs") or "").split(",")[-1],
                "position":  "G",
                "gp":        int(g.get("gamesPlayed",        0) or 0),
                "W":         float(g.get("wins",             0) or 0),
                "L":         float(g.get("losses",           0) or 0),
                "OTL":       float(g.get("otLosses",         0) or 0),
                "SVP":       float(g.get("savePercentage",   0) or 0),  # 0.920, not 92.0
                "GAA":       float(g.get("goalsAgainstAverage", 0) or 0),
                "SO":        float(g.get("shutouts",         0) or 0),
                "SA":        float(g.get("shotsAgainst",     0) or 0),
                "SV":        float(g.get("saves",            0) or 0),
            })

        total  = payload.get("total", 0)
        start += PAGE_SIZE
        if start >= total:
            break
        time.sleep(0.1)

    print(f"  Fetched {len(rows)} goalies.")
    return rows


def fetch_skaters(season_id: int) -> list[dict]:
    """
    Fetch season totals from 3 endpoints, merge on playerId.
    Skips goalies. Returns list of dicts with all 8 fantasy categories.
    """
    season_exp = f"seasonId={season_id} and gameTypeId=2"

    print("  Fetching summary…")
    summary  = _paginate("summary",     {"sort": "points",             "cayenneExp": season_exp})
    print("  Fetching realtime…")
    realtime = _paginate("realtime",    {"sort": "hits",               "cayenneExp": season_exp})
    print("  Fetching faceoffwins…")
    faceoffs = _paginate("faceoffwins", {"sort": "totalFaceoffWins",   "cayenneExp": season_exp})

    rt_idx = {r["playerId"]: r for r in realtime}
    fo_idx = {r["playerId"]: r for r in faceoffs}

    merged = []
    for s in summary:
        pid = s.get("playerId")
        pos = s.get("positionCode", "")
        if pos == "G":
            continue
        # Expand NHL single-letter codes to full names used by Yahoo
        _POS_MAP = {"L": "LW", "R": "RW", "C": "C", "D": "D"}
        pos = _POS_MAP.get(pos, pos)
        rt = rt_idx.get(pid, {})
        fo = fo_idx.get(pid, {})
        merged.append({
            "player_id": pid,
            "name":      s.get("skaterFullName", "Unknown"),
            "team":      s.get("teamAbbrevs", "").split(",")[-1],
            "position":  pos,
            "gp":        int(s.get("gamesPlayed", 0) or 0),
            "G":         float(s.get("goals",             0) or 0),
            "A":         float(s.get("assists",           0) or 0),
            "PP":        float(s.get("ppPoints",          0) or 0),
            "S":         float(s.get("shots",             0) or 0),
            "HIT":       float(rt.get("hits",             0) or 0),
            "BLK":       float(rt.get("blockedShots",     0) or 0),
            "PIM":       float(rt.get("penaltyMinutes",   0) or 0),
            "FOW":       float(fo.get("totalFaceoffWins", 0) or 0),
            "points":    float(s.get("points",            0) or 0),
        })

    print(f"  Merged {len(merged)} skaters.")
    return merged


# ── Per-game stats (for recent form + injury detection) ───────────────────────

def fetch_per_game_stats(since_date: str, until_date: str) -> list[dict]:
    """
    Fetch per-game stats for ALL skaters between since_date and until_date (inclusive).
    Merges summary (G, A, PIM, PP, S) with realtime (HIT, BLK) on gameId+playerId.
    FOW is not available per-game; callers should use season per-game rate.

    Returns [{player_id, game_id, game_date, G, A, PIM, PP, S, HIT, BLK}]
    """
    date_exp  = (f'gameDate>="{since_date}" and '
                 f'gameDate<="{until_date} 23:59:59" and gameTypeId=2')
    game_params = {
        "isAggregate": "false",
        "isGame":      "true",
        "cayenneExp":  date_exp,
        "factCayenneExp": "gamesPlayed>=1",
    }

    print(f"  Fetching per-game summary {since_date} → {until_date}…")
    summary_rows = _paginate("summary",     {**game_params, "sort": "points"})

    print(f"  Fetching per-game realtime {since_date} → {until_date}…")
    rt_rows      = _paginate("realtime",    {**game_params, "sort": "hits"})

    print(f"  Fetching per-game faceoffwins {since_date} → {until_date}…")
    fo_rows      = _paginate("faceoffwins", {**game_params, "sort": "totalFaceoffWins"})

    # Index realtime and faceoffwins by (gameId, playerId)
    rt_idx = {(r.get("gameId"), r["playerId"]): r for r in rt_rows}
    fo_idx = {(r.get("gameId"), r["playerId"]): r for r in fo_rows}

    merged = []
    seen   = set()
    for s in summary_rows:
        pid = s.get("playerId")
        gid = s.get("gameId")
        pos = s.get("positionCode", "")
        if pos == "G" or not pid or not gid:
            continue
        key = (gid, pid)
        if key in seen:
            continue
        seen.add(key)

        rt = rt_idx.get(key, {})
        fo = fo_idx.get(key, {})
        merged.append({
            "player_id": pid,
            "game_id":   gid,
            "game_date": s.get("gameDate", "")[:10],   # "YYYY-MM-DD"
            "G":         float(s.get("goals",              0) or 0),
            "A":         float(s.get("assists",            0) or 0),
            "PP":        float(s.get("ppPoints",           0) or 0),
            "S":         float(s.get("shots",              0) or 0),
            "PIM":       float(s.get("penaltyMinutes",     0) or 0),
            "HIT":       float(rt.get("hits",              0) or 0),
            "BLK":       float(rt.get("blockedShots",      0) or 0),
            "FOW":       float(fo.get("totalFaceoffWins",  0) or 0),
        })

    print(f"  Got {len(merged)} player-game rows.")
    return merged


# ── Injury detection from game log ────────────────────────────────────────────

def detect_missed_games(
    game_log:       list[dict],    # [{game_date, ...}] from cache
    schedule_dates: list[str],     # all dates the player's team played
) -> dict:
    """
    Compare played dates against team schedule to detect probable injuries.
    Returns {consecutive_missed, missed_last_14d, injury_flag}.
    """
    if not schedule_dates:
        return {"consecutive_missed": 0, "missed_last_14d": 0, "injury_flag": False}

    played = {g["game_date"] for g in game_log}
    sched  = sorted(schedule_dates)

    # Consecutive missed games from the most recent scheduled game backwards
    consecutive = 0
    for d in reversed(sched):
        if d not in played:
            consecutive += 1
        else:
            break

    cutoff        = (date.today() - timedelta(days=14)).isoformat()
    recent_sched  = [d for d in sched if d >= cutoff]
    missed_recent = sum(1 for d in recent_sched if d not in played)

    return {
        "consecutive_missed": consecutive,
        "missed_last_14d":    missed_recent,
        "injury_flag":        consecutive >= 3,
    }
