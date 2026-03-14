"""
nhl_api.py — Fetch and merge three NHL Stats API endpoints on playerId.

Endpoints (all paginated, 100 per page):
  summary   → G, A, points, GP
  realtime  → HIT, BLK, PIM
  faceoffwins → FOW (totalFaceoffWins)

PP (power play points) comes from the summary endpoint as ppPoints.
S  (shots on goal)     comes from the summary endpoint as shots.
"""
import requests
import time

BASE = "https://api.nhle.com/stats/rest/en/skater"
PAGE_SIZE = 100


def _fetch_endpoint(endpoint: str, season_id: int, sort_field: str) -> list[dict]:
    """Paginate through an NHL stats endpoint and return all rows."""
    rows = []
    start = 0
    session = requests.Session()
    while True:
        params = {
            "limit":       PAGE_SIZE,
            "start":       start,
            "sort":        sort_field,
            "cayenneExp":  f"seasonId={season_id} and gameTypeId=2",
        }
        try:
            r = session.get(f"{BASE}/{endpoint}", params=params, timeout=15)
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
        time.sleep(0.15)   # be polite

    return rows


def fetch_skaters(season_id: int) -> list[dict]:
    """
    Fetch all three endpoints, merge on playerId, return list of dicts with:
      player_id, name, team, position, gp, G, A, FOW, PIM, PP, S, HIT, BLK, points
    Skips goalies (position == 'G').
    """
    print("  Fetching summary…")
    summary   = _fetch_endpoint("summary",      season_id, "points")
    print("  Fetching realtime…")
    realtime  = _fetch_endpoint("realtime",     season_id, "hits")
    print("  Fetching faceoffwins…")
    faceoffs  = _fetch_endpoint("faceoffwins",  season_id, "totalFaceoffWins")

    # Index realtime and faceoffs by playerId for O(1) lookup
    rt_idx = {r["playerId"]: r for r in realtime}
    fo_idx = {r["playerId"]: r for r in faceoffs}

    merged = []
    for s in summary:
        pid = s.get("playerId")
        pos = s.get("positionCode", "")
        if pos == "G":
            continue   # ignore goalies

        rt = rt_idx.get(pid, {})
        fo = fo_idx.get(pid, {})

        merged.append({
            "player_id": pid,
            "name":      s.get("skaterFullName", "Unknown"),
            "team":      s.get("teamAbbrevs", ""),
            "position":  pos,
            "gp":        s.get("gamesPlayed", 0),
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
