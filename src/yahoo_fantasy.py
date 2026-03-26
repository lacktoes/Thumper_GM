"""
yahoo_fantasy.py — Yahoo Fantasy Hockey API integration.

Credential loading order (mirrors pww-hockey/scripts/yahoo_oauth.py):
  1. .env file in the project root  (local development)
  2. Streamlit secrets               (Streamlit Community Cloud deployment)
  3. os.environ                      (CI / other environments)

Required keys: YAHOO_CLIENT_ID, YAHOO_CLIENT_SECRET, YAHOO_REFRESH_TOKEN,
               YAHOO_LEAGUE_KEY, TOTAL_TEAMS (optional, default 12)
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import requests
from requests.auth import HTTPBasicAuth

# ── Load .env first (same as pww-hockey fetch_data.py) ───────────────────────

try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(_env_path)
except ImportError:
    pass

# ── If running inside Streamlit, populate os.environ from st.secrets ──────────
# (only for keys not already set via .env / environment)

def _load_streamlit_secrets():
    try:
        import streamlit as st
        for key in ("YAHOO_CLIENT_ID", "YAHOO_CLIENT_SECRET",
                    "YAHOO_REFRESH_TOKEN", "YAHOO_LEAGUE_KEY", "TOTAL_TEAMS"):
            if key not in os.environ:
                val = st.secrets.get(key)
                if val:
                    os.environ[key] = str(val)
    except Exception:
        pass   # not running in Streamlit, or secrets not available


# ── Credential helpers ────────────────────────────────────────────────────────

def _require(key: str) -> str:
    """Read a required credential from os.environ (after loading .env / secrets)."""
    _load_streamlit_secrets()
    val = os.environ.get(key)
    if not val:
        raise EnvironmentError(
            f"Missing credential: {key}.\n"
            "  Local:  add it to .env in the project root\n"
            "  Cloud:  add it to .streamlit/secrets.toml\n"
        )
    return val


# ── OAuth (identical to pww-hockey yahoo_oauth.py) ────────────────────────────

TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"


def refresh_access_token() -> str:
    """Exchange the stored refresh token for a new access token."""
    client_id     = _require("YAHOO_CLIENT_ID")
    client_secret = _require("YAHOO_CLIENT_SECRET")
    refresh_token = _require("YAHOO_REFRESH_TOKEN")

    resp = requests.post(
        TOKEN_URL,
        auth=HTTPBasicAuth(client_id, client_secret),
        data={
            "refresh_token": refresh_token,
            "grant_type":    "refresh_token",
            "redirect_uri":  "oob",
        },
        timeout=15,
    )

    if not resp.ok:
        # Surface the actual Yahoo error body to make debugging possible
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        raise RuntimeError(
            f"Yahoo token refresh failed {resp.status_code}: {body}"
        )

    tokens = resp.json()

    # Rotate the stored refresh token if Yahoo issued a new one
    new_refresh = tokens.get("refresh_token", refresh_token)
    if new_refresh and new_refresh != refresh_token:
        os.environ["YAHOO_REFRESH_TOKEN"] = new_refresh
        # Update secrets.toml if running locally so the new token persists
        _persist_refresh_token(new_refresh)

    return tokens["access_token"]


def _persist_refresh_token(new_token: str):
    """Write the rotated refresh token back to .env and/or secrets.toml."""
    # Update .env
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        lines = env_path.read_text().splitlines()
        updated = False
        for i, line in enumerate(lines):
            if line.startswith("YAHOO_REFRESH_TOKEN"):
                lines[i] = f'YAHOO_REFRESH_TOKEN="{new_token}"'
                updated = True
                break
        if not updated:
            lines.append(f'YAHOO_REFRESH_TOKEN="{new_token}"')
        env_path.write_text("\n".join(lines) + "\n")

    # Update secrets.toml
    secrets_path = Path(__file__).parent.parent / ".streamlit" / "secrets.toml"
    if secrets_path.exists():
        lines = secrets_path.read_text().splitlines()
        for i, line in enumerate(lines):
            if line.startswith("YAHOO_REFRESH_TOKEN"):
                lines[i] = f'YAHOO_REFRESH_TOKEN = "{new_token}"'
                break
        secrets_path.write_text("\n".join(lines) + "\n")


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {refresh_access_token()}"}


# ── API helper ────────────────────────────────────────────────────────────────

YAHOO_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"


def _api_get(path: str, headers: dict, retries: int = 3) -> Any:
    url = f"{YAHOO_BASE}/{path}"
    for attempt in range(retries):
        try:
            r = requests.get(url, params={"format": "json"}, headers=headers, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = 2 ** attempt
            print(f"  [yahoo] retry {attempt + 1} in {wait}s — {exc}")
            time.sleep(wait)


# ── Player info extraction ────────────────────────────────────────────────────

def _extract_player_info(player_arr: list) -> dict:
    info: dict = {}
    meta = player_arr[0] if player_arr else []
    for item in meta:
        if not isinstance(item, dict):
            continue
        info.update(item)

    pid_raw  = info.get("player_id")
    name_raw = info.get("name", {})
    return {
        "player_id":   int(pid_raw) if pid_raw else None,
        "name":        name_raw.get("full", "") if isinstance(name_raw, dict) else str(name_raw),
        "position":    info.get("display_position", ""),
        "status":      info.get("status", ""),
        "injury_note": info.get("injury_note", ""),
    }


# ── FA position fetch ─────────────────────────────────────────────────────────

def fetch_fa_positions(league_key: str, max_players: int = 400) -> dict[str, str]:
    """
    Paginate through Yahoo free agents and return {player_name: display_position}.
    Used to fill in multi-position data for players not on any roster.
    """
    hdrs      = _headers()
    positions: dict[str, str] = {}
    start     = 0
    page_size = 25

    while start < max_players:
        try:
            data = _api_get(
                f"league/{league_key}/players;status=A;start={start};count={page_size}",
                hdrs,
            )
            players = data["fantasy_content"]["league"][1]["players"]
            n = players.get("count", 0)
            if n == 0:
                break
            for i in range(n):
                p_arr = players[str(i)]["player"]
                info  = _extract_player_info(p_arr)
                name  = info.get("name", "")
                pos   = info.get("position", "")
                if name and pos:
                    positions[name] = pos
            start += n
            if n < page_size:
                break
            time.sleep(0.15)
        except Exception as exc:
            print(f"  [yahoo] FA position fetch error at start={start}: {exc}")
            break

    print(f"  [yahoo] fetched positions for {len(positions)} available players")
    return positions


# ── Roster fetch ──────────────────────────────────────────────────────────────

def fetch_all_rosters(league_key: str, total_teams: int = 12) -> dict[int, dict]:
    """
    Fetch every team's roster. Returns {player_id: {team_number, team_name,
    is_fa, status, injury_note}}.
    """
    hdrs   = _headers()
    result: dict[int, dict] = {}

    for team_num in range(1, total_teams + 1):
        team_key = f"{league_key}.t.{team_num}"
        try:
            data      = _api_get(f"team/{team_key}/roster/players", hdrs)
            fc        = data["fantasy_content"]
            team_name = (fc["team"][0][2]["name"]
                         if len(fc["team"][0]) > 2 else f"Team {team_num}")
            players   = fc["team"][1]["roster"]["0"]["players"]
            count     = players.get("count", 0)

            for i in range(count):
                p_arr = players[str(i)]["player"]
                info  = _extract_player_info(p_arr)
                pid   = info.get("player_id")
                if pid is None:
                    continue
                result[pid] = {
                    "team_number":   team_num,
                    "team_name":     team_name,
                    "is_fa":         False,
                    "name":          info["name"],
                    "status":        info["status"],
                    "injury_note":   info["injury_note"],
                    "yahoo_position": info["position"],
                }

            print(f"  [yahoo] team {team_num:2d} ({team_name}): {count} players")
            time.sleep(0.2)

        except Exception as exc:
            print(f"  [yahoo] team {team_num} error: {exc}")

    return result


# ── Weekly matchup (live H2H stats) ──────────────────────────────────────────

# Yahoo returns skater stats positionally; first 8 map to our categories.
# Order: G, A, PIM, PPP(→PP), SOG(→S), FW(→FOW), HIT, BLK
_SKATER_CATS = ["G", "A", "PIM", "PP", "S", "FOW", "HIT", "BLK"]


def _extract_week_stats(team_node: dict) -> dict[str, float]:
    """
    Parse Yahoo team[1] node (from /stats or /matchups endpoint) into
    {cat: float} for our 8 skater categories using positional indexing.
    """
    result = {c: 0.0 for c in _SKATER_CATS}
    try:
        stats_list = team_node["team_stats"]["stats"]
        for i, cat in enumerate(_SKATER_CATS):
            if i < len(stats_list):
                val = stats_list[i]["stat"]["value"]
                try:
                    result[cat] = float(val)
                except (ValueError, TypeError):
                    pass
    except (KeyError, TypeError, IndexError):
        pass
    return result


def _fetch_week_dates(league_key: str, week: int, hdrs: dict) -> tuple:
    """
    Return (week_start, week_end) ISO strings for the given fantasy week.

    Tries the scoreboard endpoint first.  Falls back to /game/{game_key}/game_weeks
    which covers non-standard weeks (Christmas break, All-Star, etc.).
    """
    # 1. Scoreboard (fastest, usually works)
    try:
        sb   = _api_get(f"league/{league_key}/scoreboard;week={week}", hdrs)
        sc   = sb["fantasy_content"]["league"][1]["scoreboard"]
        ws   = sc.get("week_start")
        we   = sc.get("week_end")
        if ws and we:
            return ws, we
    except Exception:
        pass

    # 2. game_weeks fallback
    try:
        game_key = league_key.split(".l.")[0]
        gw_data  = _api_get(f"game/{game_key}/game_weeks", hdrs)
        gw_list  = gw_data["fantasy_content"]["game"][1]["game_weeks"]
        count    = int(gw_list.get("count", 0))
        for i in range(count):
            gw = gw_list[str(i)]["game_week"]
            if int(gw.get("week", -1)) == week:
                return gw.get("start"), gw.get("end")
    except Exception:
        pass

    return None, None


def fetch_weekly_matchup(league_key: str, my_team_num: int) -> dict | None:
    """
    Fetch live week stats for the current H2H matchup.

    Returns {
        week, week_start, week_end,
        my_name, my_num, my_stats, my_logo,
        opp_name, opp_num, opp_stats, opp_logo,
    } or None on bye week / offseason.
    """
    hdrs = _headers()

    # 1. Current week number from league endpoint
    lg_data     = _api_get(f"league/{league_key}/", hdrs)
    league_meta = lg_data["fantasy_content"]["league"][0]
    cw          = league_meta.get("current_week")
    week        = int(cw["value"] if isinstance(cw, dict) else cw)

    # 2. My team's cumulative stats for this week
    my_data = _api_get(
        f"team/{league_key}.t.{my_team_num}/stats;type=week;week={week}", hdrs
    )
    my_team  = my_data["fantasy_content"]["team"]
    my_name  = next((v["name"] for v in my_team[0] if isinstance(v, dict) and "name" in v),
                    f"Team {my_team_num}")
    my_logo  = next((v["team_logos"][0]["team_logo"]["url"]
                     for v in my_team[0] if isinstance(v, dict) and "team_logos" in v), None)
    my_stats = _extract_week_stats(my_team[1])

    # 3. Identify opponent via matchup endpoint (returns None on bye week)
    try:
        mu_data = _api_get(
            f"team/{league_key}.t.{my_team_num}/matchups;weeks={week}", hdrs
        )
        teams    = (mu_data["fantasy_content"]["team"][1]
                    ["matchups"]["0"]["matchup"]["0"]["teams"])
        opp_meta = teams["1"]["team"][0]
        opp_name = next((v["name"] for v in opp_meta if isinstance(v, dict) and "name" in v),
                        "Opponent")
        opp_key  = next((v["team_key"] for v in opp_meta
                         if isinstance(v, dict) and "team_key" in v), None)
        opp_logo = next((v["team_logos"][0]["team_logo"]["url"]
                         for v in opp_meta if isinstance(v, dict) and "team_logos" in v), None)
        opp_num  = int(opp_key.split(".t.")[-1]) if opp_key else None
    except (KeyError, IndexError, TypeError):
        return None   # bye week or no active matchup

    if opp_num is None:
        return None

    # 4. Week start / end dates (scoreboard first, game_weeks fallback for
    #    non-standard weeks: holidays, all-star break, etc.)
    week_start, week_end = _fetch_week_dates(league_key, week, hdrs)

    # 5. Opponent's cumulative stats for this week
    opp_data  = _api_get(
        f"team/{league_key}.t.{opp_num}/stats;type=week;week={week}", hdrs
    )
    opp_stats = _extract_week_stats(opp_data["fantasy_content"]["team"][1])

    return {
        "week":        week,
        "week_start":  week_start,
        "week_end":    week_end,
        "my_name":     my_name,
        "my_num":      my_team_num,
        "my_stats":    my_stats,
        "my_logo":     my_logo,
        "opp_name":    opp_name,
        "opp_num":     opp_num,
        "opp_stats":   opp_stats,
        "opp_logo":    opp_logo,
    }


def build_roster_membership(
    yahoo_roster: dict[int, dict],
    all_skater_ids: list[int],
) -> dict[int, dict]:
    result = dict(yahoo_roster)
    for pid in all_skater_ids:
        if pid not in result:
            result[pid] = {
                "team_number":    0,
                "team_name":      "Free Agent",
                "is_fa":          True,
                "name":           "",
                "status":         "",
                "injury_note":    "",
                "yahoo_position": "",
            }
    return result
