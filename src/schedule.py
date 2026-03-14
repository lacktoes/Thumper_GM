"""
schedule.py — Scrape the 2025-26 NHL schedule from hockey-reference.com.

Produces rows: [{game_date: "YYYY-MM-DD", home_team: "BOS", away_team: "TOR"}, ...]

Team name normalisation maps hockey-reference full names to 3-letter NHL codes
so they match the team abbreviations returned by the NHL Stats API.
"""
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime

URL = "https://www.hockey-reference.com/leagues/NHL_2026_games.html"

# Map hockey-reference visitor/home names to NHL API abbreviations
TEAM_MAP = {
    "Anaheim Ducks":           "ANA",
    "Arizona Coyotes":         "ARI",
    "Utah Hockey Club":        "UTA",
    "Boston Bruins":           "BOS",
    "Buffalo Sabres":          "BUF",
    "Calgary Flames":          "CGY",
    "Carolina Hurricanes":     "CAR",
    "Chicago Blackhawks":      "CHI",
    "Colorado Avalanche":      "COL",
    "Columbus Blue Jackets":   "CBJ",
    "Dallas Stars":            "DAL",
    "Detroit Red Wings":       "DET",
    "Edmonton Oilers":         "EDM",
    "Florida Panthers":        "FLA",
    "Los Angeles Kings":       "LAK",
    "Minnesota Wild":          "MIN",
    "Montreal Canadiens":      "MTL",
    "Nashville Predators":     "NSH",
    "New Jersey Devils":       "NJD",
    "New York Islanders":      "NYI",
    "New York Rangers":        "NYR",
    "Ottawa Senators":         "OTT",
    "Philadelphia Flyers":     "PHI",
    "Pittsburgh Penguins":     "PIT",
    "San Jose Sharks":         "SJS",
    "Seattle Kraken":          "SEA",
    "St. Louis Blues":         "STL",
    "Tampa Bay Lightning":     "TBL",
    "Toronto Maple Leafs":     "TOR",
    "Vancouver Canucks":       "VAN",
    "Vegas Golden Knights":    "VGK",
    "Washington Capitals":     "WSH",
    "Winnipeg Jets":           "WPG",
}


def _norm(name: str) -> str:
    return TEAM_MAP.get(name.strip(), name.strip()[:3].upper())


def fetch_schedule() -> list[dict]:
    """Scrape and return all 2025-26 regular season games."""
    headers = {"User-Agent": "Mozilla/5.0 (thumpers-gm-dashboard/1.0)"}
    try:
        r = requests.get(URL, headers=headers, timeout=20)
        r.raise_for_status()
    except Exception as exc:
        print(f"  [schedule] fetch error: {exc}")
        return []

    soup = BeautifulSoup(r.text, "lxml")
    table = soup.find("table", {"id": "games"})
    if not table:
        print("  [schedule] could not find #games table")
        return []

    rows = []
    for tr in table.find("tbody").find_all("tr"):
        if tr.get("class") and "thead" in tr.get("class", []):
            continue
        cols = tr.find_all(["td", "th"])
        if len(cols) < 5:
            continue
        try:
            # Column order: Date, Visitor, Goals, Home, Goals, [OT], Attendance, ...
            raw_date  = cols[0].get_text(strip=True)
            visitor   = cols[1].get_text(strip=True)
            home      = cols[3].get_text(strip=True)

            # hockey-reference uses "Mon, Oct 14, 2025" format
            date_str = datetime.strptime(raw_date, "%a, %b %d, %Y").strftime("%Y-%m-%d")

            if visitor and home:
                rows.append({
                    "game_date": date_str,
                    "home_team": _norm(home),
                    "away_team": _norm(visitor),
                })
        except Exception:
            continue

    print(f"  [schedule] scraped {len(rows)} games.")
    return rows


def games_in_window(schedule: list[dict], team: str, start_date: str, days: int) -> int:
    """Count how many games `team` plays from start_date over the next `days` days."""
    from datetime import date, timedelta
    d0 = date.fromisoformat(start_date)
    d1 = d0 + timedelta(days=days)
    count = 0
    for g in schedule:
        gd = date.fromisoformat(g["game_date"])
        if d0 <= gd < d1 and (g["home_team"] == team or g["away_team"] == team):
            count += 1
    return count


def game_dates_in_window(schedule: list[dict], team: str, start_date: str, days: int) -> list[str]:
    """Return sorted list of game dates for `team` in the next `days` days."""
    from datetime import date, timedelta
    d0 = date.fromisoformat(start_date)
    d1 = d0 + timedelta(days=days)
    dates = []
    for g in schedule:
        gd = date.fromisoformat(g["game_date"])
        if d0 <= gd < d1 and (g["home_team"] == team or g["away_team"] == team):
            dates.append(g["game_date"])
    return sorted(set(dates))


def light_nights(schedule: list[dict], start_date: str, days: int, threshold: int = 5) -> set[str]:
    """
    Return set of dates within the window that have fewer than `threshold`
    total NHL games — i.e. "light nights" where streaming is advantageous.
    """
    from datetime import date, timedelta
    from collections import Counter
    d0 = date.fromisoformat(start_date)
    d1 = d0 + timedelta(days=days)
    counts: Counter = Counter()
    for g in schedule:
        gd = date.fromisoformat(g["game_date"])
        if d0 <= gd < d1:
            counts[g["game_date"]] += 1
    return {d for d, cnt in counts.items() if cnt < threshold}
