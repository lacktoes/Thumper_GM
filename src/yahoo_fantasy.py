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
