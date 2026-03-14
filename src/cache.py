"""
cache.py — SQLite read/write helpers for player stats and schedule data.
"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_DIR = Path(__file__).parent.parent / "data"
DB_DIR.mkdir(exist_ok=True)

PLAYERS_DB  = DB_DIR / "players.db"
SCHEDULE_DB = DB_DIR / "schedule.db"


# ── players ───────────────────────────────────────────────────────────────────

def _players_conn():
    con = sqlite3.connect(PLAYERS_DB)
    con.row_factory = sqlite3.Row
    return con


def init_players_db():
    with _players_conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS skater_stats (
                player_id   INTEGER PRIMARY KEY,
                name        TEXT,
                team        TEXT,
                position    TEXT,
                gp          INTEGER,
                G           REAL, A    REAL, FOW  REAL, PIM REAL,
                PP          REAL, S    REAL, HIT  REAL, BLK REAL,
                points      REAL,
                fetched_at  TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS roster_membership (
                player_id   INTEGER PRIMARY KEY,
                team_number INTEGER,
                team_name   TEXT,
                is_fa       INTEGER   -- 1 = free agent, 0 = rostered
            )
        """)


def save_skaters(rows: list[dict]):
    """Upsert a list of skater stat dicts."""
    now = datetime.now(timezone.utc).isoformat()
    with _players_conn() as con:
        con.executemany("""
            INSERT INTO skater_stats
              (player_id, name, team, position, gp, G, A, FOW, PIM, PP, S, HIT, BLK, points, fetched_at)
            VALUES
              (:player_id,:name,:team,:position,:gp,:G,:A,:FOW,:PIM,:PP,:S,:HIT,:BLK,:points,:fetched_at)
            ON CONFLICT(player_id) DO UPDATE SET
              name=excluded.name, team=excluded.team, position=excluded.position,
              gp=excluded.gp, G=excluded.G, A=excluded.A, FOW=excluded.FOW,
              PIM=excluded.PIM, PP=excluded.PP, S=excluded.S, HIT=excluded.HIT,
              BLK=excluded.BLK, points=excluded.points, fetched_at=excluded.fetched_at
        """, [{**r, "fetched_at": now} for r in rows])


def load_skaters() -> list[dict]:
    with _players_conn() as con:
        rows = con.execute("SELECT * FROM skater_stats").fetchall()
    return [dict(r) for r in rows]


def skaters_stale(ttl_hours: int) -> bool:
    with _players_conn() as con:
        row = con.execute("SELECT fetched_at FROM skater_stats LIMIT 1").fetchone()
    if not row:
        return True
    fetched = datetime.fromisoformat(row["fetched_at"])
    age = (datetime.now(timezone.utc) - fetched).total_seconds() / 3600
    return age > ttl_hours


def save_roster_membership(rows: list[dict]):
    """rows: [{player_id, team_number, team_name, is_fa}]"""
    with _players_conn() as con:
        con.execute("DELETE FROM roster_membership")
        con.executemany("""
            INSERT OR REPLACE INTO roster_membership (player_id, team_number, team_name, is_fa)
            VALUES (:player_id, :team_number, :team_name, :is_fa)
        """, rows)


def load_roster_membership() -> dict[int, dict]:
    """Returns {player_id: {team_number, team_name, is_fa}}"""
    with _players_conn() as con:
        rows = con.execute("SELECT * FROM roster_membership").fetchall()
    return {r["player_id"]: dict(r) for r in rows}


# ── schedule ──────────────────────────────────────────────────────────────────

def _sched_conn():
    con = sqlite3.connect(SCHEDULE_DB)
    con.row_factory = sqlite3.Row
    return con


def init_schedule_db():
    with _sched_conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS games (
                game_date   TEXT,
                home_team   TEXT,
                away_team   TEXT,
                fetched_at  TEXT
            )
        """)


def save_schedule(rows: list[dict]):
    now = datetime.now(timezone.utc).isoformat()
    with _sched_conn() as con:
        con.execute("DELETE FROM games")
        con.executemany("""
            INSERT INTO games (game_date, home_team, away_team, fetched_at)
            VALUES (:game_date, :home_team, :away_team, :fetched_at)
        """, [{**r, "fetched_at": now} for r in rows])


def load_schedule() -> list[dict]:
    with _sched_conn() as con:
        rows = con.execute("SELECT game_date, home_team, away_team FROM games ORDER BY game_date").fetchall()
    return [dict(r) for r in rows]


def schedule_stale(ttl_days: int = 7) -> bool:
    with _sched_conn() as con:
        row = con.execute("SELECT fetched_at FROM games LIMIT 1").fetchone()
    if not row:
        return True
    fetched = datetime.fromisoformat(row["fetched_at"])
    age = (datetime.now(timezone.utc) - fetched).total_seconds() / 86400
    return age > ttl_days
