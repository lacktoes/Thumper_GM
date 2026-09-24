# Handoff brief — local session

**Written:** 2026-09-24 · **For:** a Claude Code session on Shane's PC, where the
Yahoo credentials and the old project folders live.

Read this first, then `HANDOVER.md` for the deeper background on the league
analysis and the draft machine design. This file covers what changed since, and
the one thing currently blocking everything.

---

## 1. The blocker: Yahoo API access is restricted to league reads

Established by running the real API from Shane's machine. **This is the single
most important fact in this document** — several hours went into narrowing it.

| Endpoint | Result |
|---|---|
| OAuth token refresh | **works** — credentials are fine |
| `/users;use_login=1/games;game_codes=nhl/leagues` | **403** |
| `/game/nhl` | **403** |
| `/league/465.l.43125/` | **403** |
| `/league/...` generally | believed to work — `Update_FA.bat` relies on it |

**This is not a credentials problem and not the Yahoo clampdown we feared.** The
token refreshes cleanly. The app is simply scoped to league-scoped reads: anything
that reads a *user record* or the *game catalogue* is refused.

Consequence: there is no way to ask Yahoo "which leagues does this account belong
to". League history has to be walked league by league.

### What we know

- **Current NHL game key is `465`** — learned from `YAHOO_LEAGUE_KEY` in Shane's
  env file, since `/game/nhl` is refused.
- `YAHOO_LEAGUE_KEY` is `465.l.43125`, and **that league returns 403**. It is
  well-formed and current-season, but this token cannot read it.
- Shane's league URL is `hockey.fantasysports.yahoo.com/hockey/1809/10`, which
  implies **league ID `1809`, team `10`** (team 10 matches `my_team_number` in
  `Thumper_GM/config.yaml`, so 1809 is probably right).
- **`43125` vs `1809` is unresolved.** Either the env file points at a different
  league, or one of the two numbers is stale. Resolving this is step one.

### First thing to run

```
cd C:\Users\shane\OneDrive\Fant\PWWWW\web\pww-hockey
git pull
python scripts/find_league_keys.py C:\Users\shane\OneDrive\Fant\YFAPI C:\Users\shane\OneDrive\Fant\PWWWW
```

Shane says those folders hold "a tonne of keys in old python files". The script
scans for `{game_key}.l.{league_id}` patterns and credential assignments, tests
each key against the API, and prints which are readable plus the exact next
command. It prints league keys (identifiers, not secrets) but **never** prints
client secrets or refresh tokens — only the file holding them.

Then:

```
python scripts/discover_leagues.py --start-key <a readable key>
python scripts/fetch_data.py --list
python scripts/fetch_data.py --backfill --skip-complete
```

### If nothing is readable

Open the league in a browser while logged in as Shane and take the league ID
from the URL. If `{465}.l.{that id}` still 403s, the app registration itself
lacks league read scope for that league, and the options narrow to:

1. Re-register the Yahoo app with `fspt-r` scope and redo the OAuth flow.
2. Check whether `Update_FA.bat` genuinely still works — it is the only evidence
   that league reads work at all, and that evidence is now second-hand.
3. Scrape what the browser shows, as a last resort.

**Verify option 2 before assuming anything.** Everything below depends on league
reads working.

### A note on those folders

Credentials are sitting in plain text across old project files inside OneDrive,
which syncs them to Microsoft's servers and to every linked device. Not urgent
and not today's problem, but worth consolidating into a single `.env` outside
the synced folder at some point. If the refresh token ever needs rotating, that
sprawl is what makes it painful.

---

## 2. What was built since the last handover

All on branch `claude/quirky-dijkstra-iryhl2` in **both** repos. Nothing merged
to `main`/`master`. No PRs opened.

### `pww-hockey` — multi-season support, shipped

The year filter is **done and browser-tested**; it just has one season of data in
it until the backfill runs.

```
docs/league_keys.json     season -> league key    (discover_leagues.py)
docs/data-<season>.json   one file per season     (fetch_data.py)
docs/seasons.json         front-end manifest      (fetch_data.py)
docs/data.json            current-season mirror, kept for compatibility
```

- Season `<select>` in the header, styled to the existing dark theme.
- Season + week in the URL hash (`#season=2024-25&week=12`), so links are shareable.
- **Degrades gracefully**: no `seasons.json` means it loads `data.json` and hides
  the picker. The live site keeps working before any backfill.
- 2025-26 migrated to `docs/data-2025-26.json`; `matchup_overrides.json` re-keyed
  by season.
- Verified in Chromium: season switching re-renders both tabs, deep links resolve,
  week nav works afterwards, no-manifest fallback is clean, no JS errors.

`fetch_data.py` rewritten season-aware: `--list`, `--season`, `--backfill`,
`--skip-complete`, `--refetch-all`. Pacing via `YAHOO_CALL_DELAY` (default 0.12s).

`discover_leagues.py` now resolves a start key cheapest-first, then walks each
league's `renew` field backwards through history:

1. `--start-key`
2. `$YAHOO_LEAGUE_KEY`
3. **game key from `$YAHOO_LEAGUE_KEY` recombined with `--league`** ← the one that
   matters; a refused key still reveals the current game key
4. `/game/nhl` + `--league`
5. probing game keys against `--league`

`find_league_keys.py` is new — see §1.

`yahoo_oauth.py` rewritten: searches `$YAHOO_ENV`, repo root, then parent dirs;
has a built-in `KEY=VALUE` parser so it does not depend on `python-dotenv`;
shell/CI values always win over files; failures name the missing variables, every
path searched, and three fixes. Token-refresh failures now surface Yahoo's own
response body, because 400/401 (stale token) and 403 (revoked access) need
different responses.

### `Thumper_GM` — analysis only

`analysis/league_diagnostics.py` reworked. No app changes yet.

---

## 3. Four bugs found by building, all of which would have corrupted the backfill

Worth reading — each was silent, and three only appear on historical data.

1. **`is_current` derived from `week == current_week`.** Yahoo keeps reporting a
   `current_week` for seasons that ended years ago, so **every backfilled season
   would have marked its final week as in progress and excluded it from the
   standings.** Now read from the league's `is_finished` flag, which also
   retro-clears the flag on stored weeks.

2. **League size taken from `TOTAL_TEAMS`.** Shane confirms the league had **10
   teams until two seasons ago, 12 since**. Per-season `num_teams` from the
   manifest now wins.

3. **Average league rank is not comparable across league sizes.** Mid-table is
   5.5 in a 10-team league and 6.5 in a 12-team one. Comparing raw ranks across
   the backfill would have made every pre-2024-25 season read as systematically
   stronger than it was — wrong in exactly the comparison the draft strategy
   depends on. All cross-season figures are now percentiles:
   `pct = (n_teams - rank) / (n_teams - 1)`.

4. **Renewed leagues get a new league ID every season**, not just a new game key.
   The original code assumed `1809` was stable and only the game key rotated, so
   `449.l.1809` for last season would likely never have resolved. The renew chain
   is the only reliable link. `--league` therefore only filters a `/users`
   listing, and chain results are grouped as one league rather than split per ID.

Also: `api_get` no longer retries 4xx other than 429 — a 403 is a policy answer,
not a blip, and retrying it three times only delayed the real error.

---

## 4. The analysis, and the question that governs the draft

Full detail in `HANDOVER.md` §4. In short, from **2025-26 only**:

- **Faceoffs decide this league and Thumpers is last in them.** FW had the largest
  top-vs-bottom gap of any category by a factor of two, and Thumpers' FW win rate
  was **9.1%** — two wins in twenty-two weeks.
- **FW and HIT are the most *lockable* categories** (stable roster properties,
  driven by ice time), while goals are near the noisy end. This contradicts
  Shane's stated intuition that hits/blocks/faceoffs are the unpredictable ones.
- Average category score **5.64/12** with **8 narrow losses** — about one
  category short, every week.

**This rests on one season, and it was Shane's worst in twelve** (he normally
finishes around third). Do not let it drive the draft unchallenged.

Once the backfill lands:

```
python analysis\league_diagnostics.py --dir ..\pww-hockey\docs --team "Thumpers"
```

`--dir` runs every season and ends with a verdict: which categories are decisive
in *every* season **and** lockable. That is the question that governs draft
strategy, and one season cannot answer it.

`--team` accepts a comma-separated list of manager GUID, nickname, or team names,
tried in order — team names change across twelve years. Season files fetched
before manager-guid capture have no guid, so a list may be needed until 2025-26
is refetched. Tested against a fixture with a renamed team and a 10→12 size change.

---

## 5. Draft machine — not started

Design is in `HANDOVER.md` §7. Key points:

- **Not a ranked player list.** A ranked list is what produces a flat team with no
  identity. Build a category-portfolio optimiser: target 3-4 categories to own,
  stay live in 4-5, genuinely punt 2-3 (SHO and SV% are near coin-flips).
- **Put ΔECW on screen** — the change in expected weekly category wins from adding
  a player, given the current roster. Actionable in a way a Z-score is not.
- **Lift the engine from `Thumper_GM/pages/5_Flippers.py`**, which already does
  Poisson/normal category win probability and log5 goalie wins properly. Same
  maths, longer horizon.
- **`Thumper_GM` scores none of the four goalie categories** — a third of the
  league. `build_goalie_df()` and `fetch_goalies()` exist; valuation does not.
- **24 players are off the board before pick one** (2 keepers × 12 teams). Load
  the keeper list and remove them before computing anything.
- **Pre-bake everything the night before.** Draft day is hostile to live API calls,
  and that is doubly true given §1.

### Still needed from Shane

- **The keeper sheet** — `docs.google.com` was blocked in the cloud session, so it
  has never been read. Sheet ID `1TLe-wqmcusP4EoJdr94Bw76lda1GzYh-XD90NfUUpr0`.
  A local session can just open it.
- **The actual draft date.** He said "Saturday"; 2026-09-19 was itself a Saturday,
  so it is either 26 Sep or 3 Oct. **Confirm this before planning anything.**
- **Roster slots** (C/LW/RW/D/G/bench/IR) — drives positional scarcity, and none
  of it is in `config.yaml`.
- **Projection source.** Last season's rates are a poor projection (no rookies, no
  age curves, no team changes). Everything downstream inherits these errors, and
  the machine will look confident regardless. Ask before building this layer.

---

## 6. Repo state

| Repo | Branch | Contents |
|---|---|---|
| `lacktoes/pww-hockey` | `claude/quirky-dijkstra-iryhl2` | multi-season pipeline, season selector, discovery + key finder, auth rework |
| `lacktoes/Thumper_GM` | `claude/quirky-dijkstra-iryhl2` | `analysis/league_diagnostics.py`, `HANDOVER.md`, this file |

Neither default branch touched. No PRs. Local clone is at
`C:\Users\shane\OneDrive\Fant\PWWWW\web\pww-hockey`.

Note `Thumper_GM`'s hardcoded season values still need bumping for 2026-27:
`config.yaml` `season_id: 20252026`, `src/nhl_api.py` `SEASON_START`,
`src/schedule.py` `SEASON`. Worth deriving from one value.

---

## 7. Order of work

1. **Resolve league access** (§1). Nothing else moves until a league key reads.
2. Run the backfill. It is slow (~500 calls/season) and resumable — start it early
   and let it run.
3. **Re-run `league_diagnostics.py --dir` across all seasons.** This decides
   whether the draft is built around faceoffs or something else.
4. Refetch 2025-26 once (`--season 2025-26`) — its week 23 is still flagged
   `is_current` from the final April fetch, so it shows as "(live)" in the picker
   and is excluded from that season's standings.
5. Get the keeper list and confirm the draft date.
6. Build the draft machine, in the order in `HANDOVER.md` §7.4.

If the draft is sooner than expected, **skip straight to §7.4 of `HANDOVER.md`** —
a projection table plus an offline board beats a half-built optimiser.
