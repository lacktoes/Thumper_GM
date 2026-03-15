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

def _migrate_table(con, table: str, required_cols: set[str], create_sql: str):
    """Drop and recreate a table if any required columns are missing."""
    existing = {row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
    if existing and not required_cols.issubset(existing):
        con.execute(f"DROP TABLE {table}")
    con.execute(create_sql)


def init_players_db():
    with _players_conn() as con:
        _migrate_table(con, "skater_stats", {"G", "A", "FOW", "HIT", "BLK"}, """
            CREATE TABLE IF NOT EXISTS skater_stats (
                player_id   INTEGER PRIMARY KEY,
                name        TEXT, team TEXT, position TEXT, gp INTEGER,
                G REAL, A REAL, FOW REAL, PIM REAL,
                PP REAL, S REAL, HIT REAL, BLK REAL,
                points REAL, fetched_at TEXT
            )
        """)
        _migrate_table(con, "roster_membership", {"status", "injury_note", "name", "yahoo_position"}, """
            CREATE TABLE IF NOT EXISTS roster_membership (
                player_id    INTEGER PRIMARY KEY,
                team_number  INTEGER,
                team_name    TEXT,
                is_fa        INTEGER,
                yahoo_position TEXT,
                name         TEXT,
                status       TEXT,
                injury_note  TEXT,
                fetched_at   TEXT
            )
        """)
        _migrate_table(con, "game_logs", {"game_id", "HIT", "BLK", "FOW"}, """
            CREATE TABLE IF NOT EXISTS game_logs (
                player_id  INTEGER,
                game_id    INTEGER,
                game_date  TEXT,
                G   REAL, A REAL, PIM REAL, PP REAL,
                S   REAL, HIT REAL, BLK REAL, FOW REAL,
                fetched_at TEXT,
                PRIMARY KEY (player_id, game_id)
            )
        """)


def init_schedule_db():
    with _sched_conn() as con:
        # Check if game_id column exists; if not, drop and recreate (it's just a cache)
        cols = {row[1] for row in con.execute("PRAGMA table_info(games)").fetchall()}
        if cols and "game_id" not in cols:
            con.execute("DROP TABLE games")
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
              (player_id, team_number, team_name, is_fa, name, yahoo_position, status, injury_note, fetched_at)
            VALUES (:player_id, :team_number, :team_name, :is_fa, :name, :yahoo_position, :status, :injury_note, :fetched_at)
        """, [{**r, "name": r.get("name", ""), "yahoo_position": r.get("yahoo_position", ""), "fetched_at": now} for r in rows])


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

def clear_game_logs():
    """Delete all rows from game_logs so a full season re-fetch can run."""
    with _players_conn() as con:
        con.execute("DELETE FROM game_logs")


def save_game_logs(rows: list[dict]):
    """
    Upsert per-game stats rows.
    rows: [{player_id, game_id, game_date, G, A, PIM, PP, S, HIT, BLK}]
    """
    now = datetime.now(timezone.utc).isoformat()
    with _players_conn() as con:
        con.executemany("""
            INSERT OR REPLACE INTO game_logs
              (player_id, game_id, game_date, G, A, PIM, PP, S, HIT, BLK, FOW, fetched_at)
            VALUES
              (:player_id,:game_id,:game_date,:G,:A,:PIM,:PP,:S,:HIT,:BLK,:FOW,:fetched_at)
        """, [{**r, "fetched_at": now} for r in rows])


def load_game_logs(player_ids: list[int] | None = None,
                   since_date: str | None = None) -> dict[int, list[dict]]:
    """
    Returns {player_id: [game_dicts sorted by game_date asc]}.
    Optionally filter to player_ids and/or since_date.
    """
    clauses, params = [], []
    if player_ids:
        ph = ",".join("?" * len(player_ids))
        clauses.append(f"player_id IN ({ph})")
        params.extend(player_ids)
    if since_date:
        clauses.append("game_date >= ?")
        params.append(since_date)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _players_conn() as con:
        rows = con.execute(
            f"SELECT * FROM game_logs {where} ORDER BY game_date",
            params,
        ).fetchall()

    result: dict[int, list[dict]] = {}
    for r in rows:
        d   = dict(r)
        pid = d.pop("player_id")
        d.pop("fetched_at", None)
        result.setdefault(pid, []).append(d)
    return result


def latest_game_log_date() -> str | None:
    """Return the most recent game_date stored in game_logs, or None."""
    with _players_conn() as con:
        row = con.execute("SELECT MAX(game_date) AS d FROM game_logs").fetchone()
    return row["d"] if row and row["d"] else None


def game_logs_need_update(ttl_hours: int = 4) -> bool:
    """True if the game_logs table is empty or last fetched_at is older than ttl."""
    with _players_conn() as con:
        row = con.execute("SELECT MAX(fetched_at) AS t FROM game_logs").fetchone()
    if not row or not row["t"]:
        return True
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["t"])).total_seconds() / 3600
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
