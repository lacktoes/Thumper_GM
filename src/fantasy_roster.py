"""
fantasy_roster.py — Parse fantasy roster data to identify:
  - Which players are rostered (and by which team)
  - Which players are Free Agents
  - Which players are on Team #10 (Thumpers)

Data source priority:
  1. CSV files dropped in data/exports/  (preferred, from MyCSVRostermaker)
  2. Yahoo Fantasy API (if yahoo_roster_json files are available)

CSV format expected (from MyCSVRostermaker or manual export):
  player_id, player_name, team_number, team_name, position

If player_id is unavailable in the CSV, we match on player_name to the
NHL stats data (fuzzy match by normalised lowercase name).
"""
import json
import re
from pathlib import Path

EXPORTS_DIR = Path(__file__).parent.parent / "data" / "exports"

# ── CSV ingestion ─────────────────────────────────────────────────────────────

def load_from_csv() -> list[dict] | None:
    """
    Look for the most recently modified roster CSV in data/exports/.
    Expected columns (flexible — we try multiple layouts):
      player_name, team_number, [player_id], [team_name], [position]

    Returns list of {player_id (or None), name, team_number, team_name, is_fa}
    or None if no CSV found.
    """
    csvs = sorted(EXPORTS_DIR.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not csvs:
        return None

    import pandas as pd
    df = pd.read_csv(csvs[0], dtype=str).fillna("")

    # Normalise column names
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    rows = []
    for _, row in df.iterrows():
        pid = int(row["player_id"]) if "player_id" in row and row["player_id"].isdigit() else None
        name = row.get("player_name", row.get("name", "")).strip()
        team_num_raw = row.get("team_number", row.get("team_num", "0")).strip()
        team_num = int(team_num_raw) if team_num_raw.isdigit() else 0
        team_name = row.get("team_name", "").strip()
        is_fa = (team_num == 0)
        rows.append({
            "player_id":   pid,
            "name":        name,
            "team_number": team_num,
            "team_name":   team_name,
            "is_fa":       is_fa,
        })
    return rows


# ── Yahoo roster JSON ingestion ────────────────────────────────────────────────

def load_from_yahoo_json() -> list[dict] | None:
    """
    Parse team_N_roster.json files (as produced by Myfantasy_stats.py).
    Returns same structure as load_from_csv().
    """
    roster_files = sorted(EXPORTS_DIR.glob("team_*_roster.json"))
    if not roster_files:
        return None

    rows = []
    for f in roster_files:
        m = re.search(r"team_(\d+)_roster", f.name)
        team_num = int(m.group(1)) if m else 0
        try:
            with open(f) as fh:
                data = json.load(fh)
            # Navigate Yahoo roster JSON structure
            roster = (data["fantasy_content"]["team"][1]["roster"]["0"]
                          ["players"])
            player_count = roster["count"]
            for i in range(player_count):
                p = roster[str(i)]["player"][0]
                pid = None
                name = ""
                for item in p:
                    if isinstance(item, dict):
                        if "player_id" in item:
                            pid = int(item["player_id"])
                        if "full" in item.get("name", {}):
                            name = item["name"]["full"]
                rows.append({
                    "player_id":   pid,
                    "name":        name,
                    "team_number": team_num,
                    "team_name":   "",
                    "is_fa":       False,
                })
        except Exception as exc:
            print(f"  [roster] error parsing {f.name}: {exc}")

    return rows if rows else None


# ── Name matching helper ───────────────────────────────────────────────────────

def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


def match_roster_to_skaters(
    roster_rows: list[dict],
    skaters: list[dict],
) -> dict[int, dict]:
    """
    Given roster rows (may lack player_id) and NHL skater dicts (have player_id + name),
    return {player_id: {team_number, team_name, is_fa}}.

    Matching priority:
      1. player_id exact match
      2. normalised name match
    """
    name_to_pid = {_norm_name(s["name"]): s["player_id"] for s in skaters}
    result = {}

    for row in roster_rows:
        pid = row.get("player_id")
        if pid is None:
            pid = name_to_pid.get(_norm_name(row["name"]))
        if pid is None:
            continue
        result[pid] = {
            "team_number": row["team_number"],
            "team_name":   row["team_name"],
            "is_fa":       row["is_fa"],
        }

    # Any skater not in the roster file is a FA
    rostered_ids = set(result.keys())
    for s in skaters:
        if s["player_id"] not in rostered_ids:
            result[s["player_id"]] = {
                "team_number": 0,
                "team_name":   "Free Agent",
                "is_fa":       True,
            }

    return result


# ── Public entry point ─────────────────────────────────────────────────────────

def load_roster(skaters: list[dict]) -> dict[int, dict]:
    """
    Try CSV first, then Yahoo JSON, then fall back to all-FA.
    Returns {player_id: {team_number, team_name, is_fa}}.
    """
    rows = load_from_csv() or load_from_yahoo_json()
    if rows:
        return match_roster_to_skaters(rows, skaters)
    # Fallback: no roster file — everyone is a FA
    print("  [roster] No roster data found in data/exports/. Treating all players as FA.")
    return {s["player_id"]: {"team_number": 0, "team_name": "Free Agent", "is_fa": True}
            for s in skaters}
