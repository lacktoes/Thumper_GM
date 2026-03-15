"""
nhl_api.py — Fetch and merge three NHL Stats API endpoints on playerId,
plus per-player game logs for recent form and injury detection.

Season stats endpoints (api.nhle.com/stats/rest/en/skater/...):
  summary     → G, A, points, GP, PP (ppPoints), S (shots)
  realtime    → HIT, BLK, PIM
  faceoffwins → FOW (totalFaceoffWins)

Game log endpoint (api-web.nhle.com/v1/player/{id}/game-log/{season}/{type}):
  → per-game G, A, PP, S, PIM  (HIT/BLK/FOW not available at per-game level)
  → used for: recent form Z-scores, consecutive missed games (injury proxy)
"""
import requests
import time
from datetime import date, timedelta

STATS_BASE = "https://api.nhle.com/stats/rest/en/skater"
WEB_BASE   = "https://api-web.nhle.com/v1"
PAGE_SIZE  = 100

# ── Season aggregates ─────────────────────────────────────────────────────────

def _fetch_endpoint(endpoint: str, season_id: int, sort_field: str) -> list[dict]:
    rows: list[dict] = []
    start   = 0
    session = requests.Session()
    while True:
        params = {
            "limit":      PAGE_SIZE,
            "start":      start,
            "sort":       sort_field,
            "cayenneExp": f"seasonId={season_id} and gameTypeId=2",
        }
        try:
            r = session.get(f"{STATS_BASE}/{endpoint}", params=params, timeout=15)
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


def fetch_skaters(season_id: int) -> list[dict]:
    """
    Fetch and merge all three stat endpoints.
    Returns list of dicts with all 8 fantasy categories + metadata.
    Skips goalies.
    """
    print("  Fetching summary…")
    summary  = _fetch_endpoint("summary",     season_id, "points")
    print("  Fetching realtime…")
    realtime = _fetch_endpoint("realtime",    season_id, "hits")
    print("  Fetching faceoffwins…")
    faceoffs = _fetch_endpoint("faceoffwins", season_id, "totalFaceoffWins")

    rt_idx = {r["playerId"]: r for r in realtime}
    fo_idx = {r["playerId"]: r for r in faceoffs}

    merged = []
    for s in summary:
        pid = s.get("playerId")
        pos = s.get("positionCode", "")
        if pos == "G":
            continue

        rt = rt_idx.get(pid, {})
        fo = fo_idx.get(pid, {})

        merged.append({
            "player_id": pid,
            "name":      s.get("skaterFullName", "Unknown"),
            "team":      s.get("teamAbbrevs", ""),
            "position":  pos,
            "gp":        int(s.get("gamesPlayed", 0) or 0),
            "G":         float(s.get("goals",           0) or 0),
            "A":         float(s.get("assists",         0) or 0),
            "PP":        float(s.get("ppPoints",        0) or 0),
            "S":         float(s.get("shots",           0) or 0),
            "HIT":       float(rt.get("hits",           0) or 0),
            "BLK":       float(rt.get("blockedShots",   0) or 0),
            "PIM":       float(rt.get("penaltyMinutes", 0) or 0),
            "FOW":       float(fo.get("totalFaceoffWins", 0) or 0),
            "points":    float(s.get("points",          0) or 0),
        })

    print(f"  Merged {len(merged)} skaters.")
    return merged


# ── Per-game logs ─────────────────────────────────────────────────────────────

# Note: the NHL web API game log only provides G, A, PIM, PP (powerPlayPoints),
# and S (shots). HIT, BLK, and FOW are NOT available at per-game granularity.
GAMELOG_CATS = ["G", "A", "PP", "S", "PIM"]

def _fetch_one_game_log(player_id: int, season_id: int) -> list[dict]:
    """Fetch regular-season game log for one player. Returns list of game dicts."""
    url = f"{WEB_BASE}/player/{player_id}/game-log/{season_id}/2"
    try:
        r = requests.get(url, timeout=12)
        r.raise_for_status()
        raw = r.json().get("gameLog", [])
        return [
            {
                "game_date": g.get("gameDate", ""),
                "G":   float(g.get("goals",             0) or 0),
                "A":   float(g.get("assists",           0) or 0),
                "PP":  float(g.get("powerPlayPoints",   0) or 0),
                "S":   float(g.get("shots",             0) or 0),
                "PIM": float(g.get("pim",               0) or 0),
            }
            for g in raw
        ]
    except Exception as exc:
        print(f"  [nhl_api] game log {player_id}: {exc}")
        return []


def fetch_game_logs(player_ids: list[int], season_id: int) -> dict[int, list[dict]]:
    """
    Fetch game logs for a list of player_ids.
    Returns {player_id: [game_dicts]}.
    """
    result: dict[int, list[dict]] = {}
    for i, pid in enumerate(player_ids):
        result[pid] = _fetch_one_game_log(pid, season_id)
        if (i + 1) % 10 == 0:
            print(f"  [nhl_api] game logs: {i+1}/{len(player_ids)}")
        time.sleep(0.15)
    return result


# ── Injury detection from game log ────────────────────────────────────────────

def detect_missed_games(
    game_log: list[dict],
    schedule_dates: list[str],   # all dates the player's TEAM played
) -> dict:
    """
    Compare player's game log dates against their team's schedule dates.
    Returns:
      {
        consecutive_missed: int,   # most recent streak of missed games
        missed_last_14d: int,      # games missed in last 14 calendar days
        injury_flag: bool,         # True if consecutive_missed >= 3
      }
    """
    if not schedule_dates:
        return {"consecutive_missed": 0, "missed_last_14d": 0, "injury_flag": False}

    played_dates = {g["game_date"] for g in game_log if g["game_date"]}
    sorted_sched = sorted(schedule_dates)

    # Consecutive missed (working backwards from last scheduled game)
    consecutive = 0
    for d in reversed(sorted_sched):
        if d not in played_dates:
            consecutive += 1
        else:
            break

    # Missed in last 14 calendar days
    cutoff = (date.today() - timedelta(days=14)).isoformat()
    recent_sched  = [d for d in sorted_sched if d >= cutoff]
    missed_recent = sum(1 for d in recent_sched if d not in played_dates)

    return {
        "consecutive_missed": consecutive,
        "missed_last_14d":    missed_recent,
        "injury_flag":        consecutive >= 3,
    }
