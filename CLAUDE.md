# Thumpers GM Dashboard — Claude Context

Streamlit app for managing a Yahoo Fantasy Hockey team. Python only.

## Running Locally

```bash
streamlit run app.py
```

Credentials go in `.env` (gitignored):
```
YAHOO_CLIENT_ID="..."
YAHOO_CLIENT_SECRET="..."
YAHOO_REFRESH_TOKEN="..."
YAHOO_LEAGUE_KEY="449.l.XXXXXX"
```

---

## Architecture

Two external APIs feed into a local SQLite cache (`data/players.db`, `data/schedule.db`).

```
NHL Stats API  →  season skater stats (G, A, PP, S, PIM, HIT, BLK, FOW)
Yahoo Fantasy  →  roster membership + injury status
NHL Web API    →  per-game stats (recent form) + full season schedule
                        ↓
                  SQLite cache
                        ↓
            analytics.py: Z-scores, VORP, form, schedule density
                        ↓
                  Streamlit pages
```

### Data files
| File | Contents |
|------|----------|
| `data/players.db` | `skater_stats`, `roster_membership`, `game_logs` tables |
| `data/schedule.db` | `games` table (all NHL games for the season) |
| `data/name_overrides.yaml` | Manual NHL→Yahoo name mappings (Mathew→Matthew, etc.) |
| `data/user_prefs.json` | Persisted category weight sliders |

---

## Key Design Decisions

### Player ID consistency
Yahoo Fantasy and the NHL Stats API use **the same numeric player IDs**. Always look up roster/injury data by `player_id` first; fall back to name matching only if the ID isn't found. Name matching is brittle (accents, nicknames, multi-position strings).

In `analytics.py build_player_df()`, `_rlookup(pid, name)` does `roster.get(pid)` first.

### Recent form window
The sidebar "Recent Form Window" slider sets `recent_days` at runtime. This value must be forwarded all the way to `add_recent_form()` — it is **not** the same as `cfg["recent_form_days"]` (which is the config-file default).

In `app.py build_df()`, a local `_cfg` dict overrides `recent_form_days` with the slider value before calling `build_player_df()`.

### Incremental game log fetch
`fetch_per_game_stats(since, until)` is called with only the date range not yet in the DB. `latest_game_log_date()` returns the most recent stored date; the next fetch starts the day after. "♻️ Reset Game Logs" clears the table and re-fetches from `SEASON_START`.

---

## NHL Stats API Quirks

Base: `https://api.nhle.com/stats/rest/en/skater/{report}`

Reports used: `summary`, `realtime`, `faceoffwins`

**Season aggregate filter:**
```
cayenneExp=seasonId=20252026 and gameTypeId=2
```

**Per-game filter (date range):**
```
isGame=true
isAggregate=false
cayenneExp=gameDate>="YYYY-MM-DD" and gameDate<="YYYY-MM-DD 23:59:59" and gameTypeId=2
factCayenneExp=gamesPlayed>=1
```

- FOW (faceoff wins) **is** available per-game from the `faceoffwins` report with `isGame=true`
- Game types: 2 = regular season, 3 = playoffs
- `limit=-1` returns all results; project paginates manually with `PAGE_SIZE=100`

---

## Yahoo Fantasy API Quirks

Base: `https://fantasysports.yahooapis.com/fantasy/v2`

**Roster fetch:**
```
GET /team/{league_key}.t.{team_num}/roster/players?format=json
```

Response: `fantasy_content.team[1].roster["0"].players` → dict with `"count"` and `"0"`, `"1"`, ... keys.

Each player entry is `players["N"]["player"]` — a list where `[0]` is a list of flat metadata dicts:
```python
[{"player_id": "12345"}, {"name": {"full": "Connor McDavid"}}, {"display_position": "C"}, ...]
```
`status` and `injury_note` keys are **absent** (not empty string) for healthy players.

**Free agent positions:**
```
GET /league/{league_key}/players;status=A;start={n};count=25?format=json
```
Paginate until `count < page_size`. Used to get multi-position strings for FA players.

**Refresh token rotation:** Yahoo sometimes issues a new refresh token on exchange. `_persist_refresh_token()` writes it back to `.env` and `secrets.toml` automatically.

---

## Z-Score / Scoring Pipeline

1. `calculate_z_scores()` — season per-game Z-scores, universe = top 350 players (≥10 GP)
2. `apply_vorp()` — VORP relative to replacement-level forward (180th) or defenseman (80th)
3. `add_schedule_density()` — games in 3d/7d/14d windows; `value_Xd = total_z × density_Xd`
4. `add_recent_form()` — Z-scores from per-game averages over last N days (all 8 cats available from game logs); players with `recent_gp < 2` get NaN → `total_z_recent = 0.0`

### Why total_z_recent shows 0 for many players
`DataFrame.sum(axis=1)` treats NaN as 0. Players with fewer than 2 games in the form window get all stat columns set to NaN, which sums to 0. This is intentional — not enough sample — but can look like missing data.

---

## Pages

| Page | File | Purpose |
|------|------|---------|
| Home | `app.py` | My roster, injury alerts, data refresh controls |
| Streamers | `pages/1_Streamers.py` | Top FA pickups ranked by schedule-adjusted Z-score; heatmap with logos |
| Auditor | `pages/2_Auditor.py` | Drop suggestions (roster vs FA comparison) |
| Heatmap | `pages/3_Heatmap.py` | 7-day gameday calendar for rostered players |
| Teams | `pages/4_Teams.py` | NHL team schedule density view |
| Flippers | `pages/5_Flippers.py` | Live H2H matchup category analysis + flip targets |

All pages read `df`, `cfg`, `weights`, `schedule`, `today_str`, `recent_days` from `st.session_state` (set by `app.py`).

---

## NHL Team Logos

Pattern: `https://assets.nhle.com/logos/nhl/svg/{TEAM_ABBREV}_light.svg`

- Works in Streamlit `st.column_config.ImageColumn` and HTML `<img>` tags
- `_LOGO = lambda t: f"https://assets.nhle.com/logos/nhl/svg/{t}_light.svg"` used in Streamers and Flippers

---

## Flippers Page — H2H Matchup Analysis

### Yahoo API calls required
- `GET /league/{key}/` → `current_week`
- `GET /team/{key}.t.{n}/stats;type=week;week={w}` → cumulative weekly stats (positional, 8 skater cats)
- `GET /team/{key}.t.{n}/matchups;weeks={w}` → opponent team key (slot "0"=mine, slot "1"=opp)
- `GET /league/{key}/scoreboard;week={w}` → `week_start`, `week_end` dates (best-effort)

### Stats positional mapping (Yahoo team stats order, first 8 indices)
`G(0), A(1), PIM(2), PPP→PP(3), SOG→S(4), FW→FOW(5), HIT(6), BLK(7)`

Each stat entry: `stats_list[i]["stat"]["value"]` (not stat_id based — positional only).

### Flip logic
- **Flip target**: trailing a category AND gap within `close_pct`% of leader's total
- **Defend**: leading a category AND gap within `close_pct`% of leader's total
- **Projected contribution**: FA's per-game rate (recent if `recent_gp>=2`, else season avg) × remaining games this week
- **Flip Score**: weighted sum across all close trailing categories (uses sidebar weights)
