"""
schedule.py — Fetch the full 2025-26 NHL regular season schedule via the
official NHL Web API (api-web.nhle.com).  Replaces the hockey-reference
scraper which was blocked by bot detection.

Endpoint: GET /v1/club-schedule-season/{abbrev}/{season}
  -> returns all games for that team (pre-season, regular, playoffs)
  -> filtered to gameType==2 (regular season)
  -> deduplicated by game ID across all 32 teams

Each stored game: {game_date: "YYYY-MM-DD", home_team: "BOS", away_team: "TOR"}
"""
import requests
import time
from datetime import date, timedelta
from collections import Counter

BASE  = "https://api-web.nhle.com/v1"
SEASON = "20252026"

NHL_TEAMS = [
    "ANA", "BOS", "BUF", "CAR", "CBJ", "CGY", "CHI", "COL",
    "DAL", "DET", "EDM", "FLA", "LAK", "MIN", "MTL", "NJD",
    "NSH", "NYI", "NYR", "OTT", "PHI", "PIT", "SEA", "SJS",
    "STL", "TBL", "TOR", "UTA", "VAN", "VGK", "WSH", "WPG",
]


def fetch_schedule() -> list[dict]:
    """
    Fetch schedules for all 32 NHL teams and merge into a deduplicated game list.
    Returns [{game_date, home_team, away_team}, ...]
    """
    session = requests.Session()
    seen_ids: set[int] = set()
    rows: list[dict] = []

    for abbrev in NHL_TEAMS:
        url = f"{BASE}/club-schedule-season/{abbrev}/{SEASON}"
        try:
            r = session.get(url, timeout=15)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            print(f"  [schedule] {abbrev} error: {exc}")
            time.sleep(0.5)
            continue

        for game in data.get("games", []):
            if game.get("gameType") != 2:   # regular season only
                continue
            gid = game.get("id")
            if gid in seen_ids:
                continue
            seen_ids.add(gid)

            rows.append({
                "game_id":   gid,
                "game_date": game["gameDate"],                          # "YYYY-MM-DD"
                "home_team": game["homeTeam"]["abbrev"],
                "away_team": game["awayTeam"]["abbrev"],
            })

        time.sleep(0.1)

    rows.sort(key=lambda g: g["game_date"])
    print(f"  [schedule] fetched {len(rows)} regular season games across 32 teams.")
    return rows


# ── Query helpers ─────────────────────────────────────────────────────────────

def games_in_window(schedule: list[dict], team: str, start_date: str, days: int) -> int:
    """Count games for `team` from start_date over the next `days` days."""
    d0 = date.fromisoformat(start_date)
    d1 = d0 + timedelta(days=days)
    return sum(
        1 for g in schedule
        if d0 <= date.fromisoformat(g["game_date"]) < d1
        and (g["home_team"] == team or g["away_team"] == team)
    )


def game_dates_in_window(schedule: list[dict], team: str, start_date: str, days: int) -> list[str]:
    """Return sorted game dates for `team` in the next `days` days."""
    d0 = date.fromisoformat(start_date)
    d1 = d0 + timedelta(days=days)
    return sorted({
        g["game_date"] for g in schedule
        if d0 <= date.fromisoformat(g["game_date"]) < d1
        and (g["home_team"] == team or g["away_team"] == team)
    })


def team_games_in_window(schedule: list[dict], team: str, start_date: str, days: int) -> list[str]:
    """Same as game_dates_in_window — explicit alias for injury detection."""
    return game_dates_in_window(schedule, team, start_date, days)


def light_nights(schedule: list[dict], start_date: str, days: int, threshold: int = 5) -> set[str]:
    """
    Dates within window with fewer than `threshold` total NHL games
    (i.e. light nights where streaming is advantageous).
    """
    d0 = date.fromisoformat(start_date)
    d1 = d0 + timedelta(days=days)
    counts: Counter = Counter()
    for g in schedule:
        gd = date.fromisoformat(g["game_date"])
        if d0 <= gd < d1:
            counts[g["game_date"]] += 1
    return {d for d, cnt in counts.items() if 0 < cnt < threshold}
