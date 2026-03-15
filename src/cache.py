"""
cache.py — SQLite read/write helpers for player stats, schedule,
game logs (recent form), and roster membership.
"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_DIR = Path(__file__).parent.parent / "data"
DB_DIR.mkdir(exist_ok=True)

PLAYERS_DB  = DB_DIR / "players.db"
SCHEDULE_DB = DB_DIR / "schedule.db"


# ── Connections ───────────────────────────────────────────────────────────────

def _players_conn():
    con = sqlite3.connect(PLAYERS_DB)
    con.row_factory = sqlite3.Row
    return con

def _sched_conn():
    con = sqlite3.connect(SCHEDULE_DB)
    con.row_factory = sqlite3.Row
    return con


# ── Init ──────────────────────────────────────────────────────────────────────

def init_players_db():
    with _players_conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS skater_stats (
                player_id   INTEGER PRIMARY KEY,
                name        TEXT, team TEXT, position TEXT, gp INTEGER,
                G REAL, A REAL, FOW REAL, PIM REAL,
                PP REAL, S REAL, HIT REAL, BLK REAL,
                points REAL, fetched_at TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS roster_membership (
                player_id   INTEGER PRIMARY KEY,
                team_number INTEGER,
                team_name   TEXT,
                is_fa       INTEGER,
                status      TEXT,
                injury_note TEXT,
                fetched_at  TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS game_logs (
                player_id   INTEGER,
                game_date   TEXT,
                G  REAL, A REAL, PP REAL, S REAL, PIM REAL,
                fetched_at  TEXT,
                PRIMARY KEY (player_id, game_date)
            )
        """)


def init_schedule_db():
    with _sched_conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS games (
                game_id    INTEGER PRIMARY KEY,
                game_date  TEXT,
                home_team  TEXT,
                away_team  TEXT,
                fetched_at TEXT
            )
        """)


# ── Skater stats ──────────────────────────────────────────────────────────────

def save_skaters(rows: list[dict]):
    now = datetime.now(timezone.utc).isoformat()
    with _players_conn() as con:
        con.executemany("""
            INSERT INTO skater_stats
              (player_id,name,team,position,gp,G,A,FOW,PIM,PP,S,HIT,BLK,points,fetched_at)
            VALUES (:player_id,:name,:team,:position,:gp,:G,:A,:FOW,:PIM,:PP,:S,:HIT,:BLK,:points,:fetched_at)
            ON CONFLICT(player_id) DO UPDATE SET
              name=excluded.name, team=excluded.team, position=excluded.position,
              gp=excluded.gp, G=excluded.G, A=excluded.A, FOW=excluded.FOW,
              PIM=excluded.PIM, PP=excluded.PP, S=excluded.S,
              HIT=excluded.HIT, BLK=excluded.BLK, points=excluded.points,
              fetched_at=excluded.fetched_at
        """, [{**r, "fetched_at": now} for r in rows])


def load_skaters() -> list[dict]:
    with _players_conn() as con:
        return [dict(r) for r in con.execute("SELECT * FROM skater_stats").fetchall()]


def skaters_stale(ttl_hours: int) -> bool:
    with _players_conn() as con:
        row = con.execute("SELECT fetched_at FROM skater_stats LIMIT 1").fetchone()
    if not row:
        return True
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["fetched_at"])).total_seconds() / 3600
    return age > ttl_hours


# ── Roster membership ─────────────────────────────────────────────────────────

def save_roster_membership(rows: list[dict]):
    now = datetime.now(timezone.utc).isoformat()
    with _players_conn() as con:
        con.execute("DELETE FROM roster_membership")
        con.executemany("""
            INSERT OR REPLACE INTO roster_membership
              (player_id, team_number, team_name, is_fa, status, injury_note, fetched_at)
            VALUES (:player_id, :team_number, :team_name, :is_fa, :status, :injury_note, :fetched_at)
        """, [{**r, "fetched_at": now} for r in rows])


def load_roster_membership() -> dict[int, dict]:
    with _players_conn() as con:
        rows = con.execute("SELECT * FROM roster_membership").fetchall()
    return {r["player_id"]: dict(r) for r in rows}


def roster_stale(ttl_hours: int = 4) -> bool:
    with _players_conn() as con:
        row = con.execute("SELECT fetched_at FROM roster_membership LIMIT 1").fetchone()
    if not row:
        return True
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["fetched_at"])).total_seconds() / 3600
    return age > ttl_hours


# ── Game logs (recent form + injury detection) ────────────────────────────────

def save_game_logs(logs: dict[int, list[dict]]):
    """logs = {player_id: [{game_date, G, A, PP, S, PIM}]}"""
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        {**game, "player_id": pid, "fetched_at": now}
        for pid, games in logs.items()
        for game in games
    ]
    with _players_conn() as con:
        pids = list(logs.keys())
        if pids:
            con.execute(
                f"DELETE FROM game_logs WHERE player_id IN ({','.join('?'*len(pids))})",
                pids,
            )
        con.executemany("""
            INSERT OR REPLACE INTO game_logs
              (player_id, game_date, G, A, PP, S, PIM, fetched_at)
            VALUES (:player_id, :game_date, :G, :A, :PP, :S, :PIM, :fetched_at)
        """, rows)


def load_game_logs(player_ids: list[int] | None = None) -> dict[int, list[dict]]:
    """Returns {player_id: [game_dicts sorted by date asc]}."""
    with _players_conn() as con:
        if player_ids:
            ph   = ",".join("?" * len(player_ids))
            rows = con.execute(
                f"SELECT * FROM game_logs WHERE player_id IN ({ph}) ORDER BY game_date",
                player_ids,
            ).fetchall()
        else:
            rows = con.execute("SELECT * FROM game_logs ORDER BY game_date").fetchall()

    result: dict[int, list[dict]] = {}
    for r in rows:
        d   = dict(r)
        pid = d.pop("player_id")
        d.pop("fetched_at", None)
        result.setdefault(pid, []).append(d)
    return result


def game_logs_stale(player_ids: list[int], ttl_hours: int = 4) -> bool:
    if not player_ids:
        return False
    with _players_conn() as con:
        ph  = ",".join("?" * len(player_ids))
        row = con.execute(
            f"SELECT fetched_at FROM game_logs WHERE player_id IN ({ph}) LIMIT 1",
            player_ids,
        ).fetchone()
    if not row:
        return True
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["fetched_at"])).total_seconds() / 3600
    return age > ttl_hours


# ── Schedule ──────────────────────────────────────────────────────────────────

def save_schedule(rows: list[dict]):
    now = datetime.now(timezone.utc).isoformat()
    with _sched_conn() as con:
        con.execute("DELETE FROM games")
        con.executemany(
            "INSERT OR REPLACE INTO games (game_id, game_date, home_team, away_team, fetched_at) "
            "VALUES (:game_id, :game_date, :home_team, :away_team, :fetched_at)",
            [{**r, "fetched_at": now} for r in rows],
        )


def load_schedule() -> list[dict]:
    with _sched_conn() as con:
        return [
            dict(r) for r in
            con.execute(
                "SELECT game_id, game_date, home_team, away_team FROM games ORDER BY game_date"
            ).fetchall()
        ]


def schedule_stale(ttl_days: int = 7) -> bool:
    with _sched_conn() as con:
        row = con.execute("SELECT fetched_at FROM games LIMIT 1").fetchone()
    if not row:
        return True
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["fetched_at"])).total_seconds() / 86400
    return age > ttl_days
