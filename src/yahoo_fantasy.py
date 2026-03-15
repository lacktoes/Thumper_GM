"""
yahoo_fantasy.py — Yahoo Fantasy Hockey API integration.

Responsibilities:
  1. OAuth: refresh access token from stored credentials
  2. Roster sync: fetch all team rosters → player_id → {team_number, team_name,
     injury_status, injury_note, is_fa}
  3. Works with Streamlit secrets (online) or .env (local)

Credentials required (in Streamlit secrets or environment variables):
  YAHOO_CLIENT_ID
  YAHOO_CLIENT_SECRET
  YAHOO_REFRESH_TOKEN
  YAHOO_LEAGUE_KEY    (e.g. "452.l.12345")
  TOTAL_TEAMS         (optional, default 12)

Yahoo injury status codes:
  "IR"    → Injured Reserve (long-term)
  "IR-LT" → IR Long-Term
  "IL"    → Injured List
  "O"     → Out
  "DTD"   → Day-to-Day
  "Q"     → Questionable
  "GTD"   → Game-Time Decision
  ""      → Active/healthy
"""
from __future__ import annotations

import os
import time
from typing import Any

import requests

# ── Credentials ───────────────────────────────────────────────────────────────

def _get_env(key: str) -> str | None:
    """Try Streamlit secrets first, then os.environ."""
    try:
        import streamlit as st
        val = st.secrets.get(key)
        if val:
            return str(val)
    except Exception:
        pass
    return os.environ.get(key)


def _require(key: str) -> str:
    v = _get_env(key)
    if not v:
        raise EnvironmentError(
            f"Missing credential: {key}. "
            "Set it in .streamlit/secrets.toml or as an environment variable."
        )
    return v


# ── OAuth ─────────────────────────────────────────────────────────────────────

TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"

def refresh_access_token() -> str:
    """Exchange the stored refresh token for a fresh access token."""
    client_id     = _require("YAHOO_CLIENT_ID")
    client_secret = _require("YAHOO_CLIENT_SECRET")
    refresh_token = _require("YAHOO_REFRESH_TOKEN")

    r = requests.post(
        TOKEN_URL,
        data={
            "grant_type":    "refresh_token",
            "refresh_token": refresh_token,
            "redirect_uri":  "oob",
        },
        auth=(client_id, client_secret),
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["access_token"]


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
            print(f"  [yahoo] retry {attempt+1} in {wait}s — {exc}")
            time.sleep(wait)


# ── Player info extraction ────────────────────────────────────────────────────

def _extract_player_info(player_arr: list) -> dict:
    """
    Parse a Yahoo player array (from roster response) into a flat dict.
    Returns: {player_id, name, position, status, injury_note}
    """
    info: dict = {}

    # player_arr[0] is a list of attribute dicts
    meta = player_arr[0] if player_arr else []
    for item in meta:
        if not isinstance(item, dict):
            continue
        info.update(item)

    # Yahoo player_id is nested under "player_id" key
    pid_raw = info.get("player_id")
    name_raw = info.get("name", {})

    return {
        "player_id":   int(pid_raw) if pid_raw else None,
        "name":        name_raw.get("full", "") if isinstance(name_raw, dict) else str(name_raw),
        "position":    info.get("display_position", ""),
        "status":      info.get("status", ""),          # e.g. "IR", "DTD", ""
        "injury_note": info.get("injury_note", ""),
    }


# ── Roster fetch ──────────────────────────────────────────────────────────────

def fetch_all_rosters(
    league_key:  str,
    total_teams: int = 12,
) -> dict[int, dict]:
    """
    Fetch every team's roster from the Yahoo Fantasy API.

    Returns {player_id: {team_number, team_name, is_fa, status, injury_note}}
    """
    hdrs   = _headers()
    result: dict[int, dict] = {}

    for team_num in range(1, total_teams + 1):
        team_key = f"{league_key}.t.{team_num}"
        try:
            data = _api_get(f"team/{team_key}/roster/players", hdrs)
            fc   = data["fantasy_content"]
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
                    "team_number": team_num,
                    "team_name":   team_name,
                    "is_fa":       False,
                    "status":      info["status"],
                    "injury_note": info["injury_note"],
                }

            print(f"  [yahoo] team {team_num:2d} ({team_name}): {count} players")
            time.sleep(0.2)

        except Exception as exc:
            print(f"  [yahoo] team {team_num} error: {exc}")

    return result


def build_roster_membership(
    yahoo_roster: dict[int, dict],
    all_skater_ids: list[int],
) -> dict[int, dict]:
    """
    Combine Yahoo roster data with full skater list.
    Any skater not rostered is marked as Free Agent.
    """
    result = dict(yahoo_roster)
    for pid in all_skater_ids:
        if pid not in result:
            result[pid] = {
                "team_number": 0,
                "team_name":   "Free Agent",
                "is_fa":       True,
                "status":      "",
                "injury_note": "",
            }
    return result
