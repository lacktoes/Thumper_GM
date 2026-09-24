# Handover — Thumpers GM / PWW Hockey

**Written:** 2026-09-19 · **For:** a local Claude Code session with working network access.

This session ran in a sandbox whose egress policy blocked every external API we
need. Everything below was derived from reading the two repos and analysing the
one real dataset available (`pww-hockey/docs/data.json`). Pick this up locally
where Yahoo and the NHL APIs are reachable.

---

## 1. State of play

Two jobs, both live:

1. **Year filter on the PWW site** — the new season needs to land somewhere, and
   the site currently has no season dimension at all.
2. **Draft decision machine** — snake draft with 2 keepers per team, on a
   Saturday. *Confirm the actual date:* 2026-09-19 is itself a Saturday, so
   "Saturday" is either today or 2026-09-26. The whole schedule below assumes
   the latter. **If the draft is sooner, say so immediately and cut scope to
   §7.4 (minimum viable draft board) only.**

Background: 12 years in this league, never won, frequently third. Last season
was the worst of them — 10th, 6-14-2.

### What was blocked here (and should just work locally)

| Host | Needed for |
|---|---|
| `fantasysports.yahooapis.com` | league discovery, rosters, weekly stats |
| `api.nhle.com` | season skater/goalie stats |
| `api-web.nhle.com` | schedule, standings, per-game logs |
| `docs.google.com` | the keeper spreadsheet |

Consequence: **no code in this handover has been run against a live API.** The
league-discovery parser is unit-tested against synthetic Yahoo payloads, and the
diagnostics script is verified against real data. Everything else is design.

### Also missing locally

`data/players.db` and `data/schedule.db` are gitignored, so this session had no
player stats at all. First thing to do locally: run the app once and let it
populate the caches.

---

## 2. Decisions already made

| Question | Answer |
|---|---|
| Draft format | **Snake, 2 keepers per team**, keepers already assigned |
| Keeper list | Google Sheet `1TLe-wqmcusP4EoJdr94Bw76lda1GzYh-XD90NfUUpr0` — **not yet read** |
| Historical backfill | **Yes — backfill all ~12 seasons** |
| New league key | Not known. League URL `hockey.fantasysports.yahoo.com/hockey/1809/10` |
| Priority | Both tracks in parallel |

**League ID `1809`, team number `10`** (matches `my_team_number: 10` in
`config.yaml`). Yahoo issues a new *game key* each season, so the full league key
is `{game_key}.l.1809` and the game key must be discovered, not guessed.
`CLAUDE.md` implies 2025-26 was `449` — treat that as unconfirmed.

The user has heard Yahoo may be restricting API access. **Test this first** — it
invalidates large parts of both tracks if true, and it is a 30-second check.

---

## 3. Repo map

### `pww-hockey` — the league results site

Static site on GitHub Pages. Hourly Action → Yahoo → `docs/data.json` → vanilla JS.

```
scripts/fetch_data.py        Yahoo -> docs/data.json. Skips cached completed weeks.
scripts/yahoo_oauth.py       Token refresh + auto-rotates the GH Actions secret via PyNaCl
scripts/discover_leagues.py  NEW (this session) — see §5
docs/data.json               ALL the data. Flat, single season. 192 KB.
docs/app.js                  962 lines, vanilla. Weekly + Season tabs.
docs/matchup_overrides.json  Manual week-22 pairing fixes
.github/workflows/update-dashboard.yml   Hourly 9am-3pm AEST, Oct-Apr only
```

**12 scoring categories, positional order matters everywhere:**

```
G, A, PIM, PPP, SOG, FW, HIT, BLK, W, SV, SV%, SHO
```

Four are goalie categories (W, SV, SV%, SHO) — a third of the league.

`data.json` shape today:

```jsonc
{
  "meta":  { "last_updated": "...", "current_week": 23 },
  "teams": { "<team name>": { "logo": "...", "manager": "..." } },
  "weeks": { "1": { "is_current": false, "matchups": [...], "stats": {...},
                    "pww": {...}, "deltas": {...}, "leaders": {...},
                    "leaderboard": [...] } },
  "standings": { "<team name>": { "W": 11, "L": 6, "T": 4 } }
}
```

**Teams are keyed by display name, not by team ID.** This is the single biggest
trap for multi-season work: managers rename teams between seasons, so name-keyed
data cannot be joined across years. See §6.

`PWW score` is a cosmetic 0-1150 min-max normalisation (`SHO` weighted 50, rest
100). It is not the Yahoo standings and should not drive any decision.

### `Thumper_GM` — the in-season GM dashboard

Streamlit. **Skaters only, 8 categories** — no goalie valuation at all.

```
app.py                  Orchestration, caching, home page. 375 lines.
src/nhl_api.py          NHL Stats API. Season + per-game. Goalie fetch exists.
src/schedule.py         Full season schedule via api-web.nhle.com
src/yahoo_fantasy.py    OAuth, rosters, FA positions, live weekly matchup
src/cache.py            SQLite. skater_stats, goalie_stats, roster_membership, game_logs
src/analytics.py        Z-scores, VORP, schedule density, recent form. 630 lines.
pages/1_Streamers.py    FA pickups, schedule heatmap
pages/2_Auditor.py      Drop suggestions
pages/3_Heatmap.py      7-day gameday calendar
pages/4_Teams.py        Team schedule density
pages/5_Flippers.py     ** THE GOOD ONE ** — see below
```

**`pages/5_Flippers.py` is the asset to build the draft machine on.** It already
implements, correctly:

- Skater stats as Poisson(λ) per game, variance = mean
- `P(win cat) = Φ(gap_μ / √gap_σ²)` including both rosters' remaining uncertainty
- `xWA` = Σ_c weight_c × [P(win_c | +player) − P(win_c | base)]
- Goalie wins via `log5(pyth_wp(my team), pyth_wp(opp))` off live NHL standings,
  scaled by start rate = goalie GP / team GP

That xWA formulation is *exactly* the draft-day question, over a longer horizon.
Do not rewrite it — lift it.

### Hardcoded season values to bump for 2026-27

| File | Value |
|---|---|
| `config.yaml` | `season_id: 20252026` |
| `src/nhl_api.py` | `SEASON_START = "2025-10-01"` |
| `src/schedule.py` | `SEASON = "20252026"` |

Worth replacing with a single derived value rather than bumping three constants
every year.

### Known gotchas (documented in `CLAUDE.md`, verified in code)

- Yahoo and NHL use **the same numeric player IDs**. Look up by ID first; name
  matching is a fallback and is brittle (accents, nicknames, `Mathew` vs `Matthew`).
- `status` / `injury_note` keys are **absent**, not empty, for healthy players.
- `total_z_recent` reads 0 for many players because `DataFrame.sum(axis=1)`
  treats NaN as 0, and players with `recent_gp < 2` get all-NaN. Intentional,
  looks like a bug.
- The sidebar "Recent Form Window" slider must be threaded all the way to
  `add_recent_form()` — it is *not* `cfg["recent_form_days"]`.
- `_migrate_table()` in `cache.py` **drops and recreates** a table when required
  columns are missing. Fine for a cache, destructive if anything durable is ever
  stored there.

---

## 4. The analysis — why we lose

Reproduce with:

```bash
python analysis/league_diagnostics.py --data ../pww-hockey/docs/data.json
```

Committed as `analysis/league_diagnostics.py`. It answers three questions.

### 4.1 Which categories decide this league?

Average weekly league rank (1 = best of 12), top-4 teams vs bottom-4:

| Cat | top-4 | bottom-4 | **gap** | **Thumpers** |
|---|---|---|---|---|
| **FW** | 3.42 | 8.61 | **5.19** | **10.32** |
| HIT | 4.08 | 6.61 | 2.53 | 6.55 |
| PPP | 6.15 | 7.72 | 1.57 | 5.45 |
| BLK | 5.50 | 7.03 | 1.53 | 7.41 |
| W | 5.86 | 7.32 | 1.45 | 6.27 |
| SOG | 6.05 | 7.43 | 1.39 | 4.64 |
| G | 6.08 | 7.02 | 0.94 | 5.68 |
| A | 6.59 | 7.40 | 0.81 | 6.14 |
| SV / SHO / PIM / SV% | ~6.2 | ~6.8 | <0.8 | — |

**Faceoffs decide this league by a factor of two over anything else, and
Thumpers is last in it.** FW win rate was **9.1%** — two wins in twenty-two
weeks. That is not a punt: a punt means rank 12 *and* the freed capital spent
elsewhere. Rank 10 with nothing gained is a leak.

### 4.2 Which categories can actually be owned?

Weekly values normalised by that week's league mean (removes schedule density),
then variance split into between-team spread vs within-team week-to-week noise.
`ratio = between_sd / within_sd`:

```
FW    1.69   LOCKABLE — build around it
HIT   0.94   LOCKABLE
PPP   0.70   LOCKABLE
SOG   0.69   LOCKABLE
A     0.68   LOCKABLE
BLK   0.47   moderate
W     0.41   moderate
G     0.41   moderate
SV    0.33   NOISY
PIM   0.30   NOISY
SV%   0.24   NOISY
SHO   0.19   NOISY — effectively a coin flip
```

**This directly contradicts the user's stated intuition.** They said hits,
blocks and faceoffs are hard to predict and that they get drawn to goals
instead. The opposite is true: FW and HIT are the *most* stable properties of a
roster (driven by ice time and role, which barely move), while goals are near
the noisy end at 0.41. They have been spending draft capital on the
unpredictable categories and calling the predictable ones unpredictable.

FW and HIT being both **the most decisive** and **the most lockable** is the
central finding of this analysis.

### 4.3 How the losses actually happened

Average category score **5.64 / 12**. Needs >6 to win.

- **8 narrow losses** (4.5–5.5 cats) — roughly one category short
- 6 blowout losses, 3 narrow wins, 3 blowout wins, 2 ties

Not a bad roster. A roster about one category short, every week. Owning FW alone
plausibly converts most of those eight.

### 4.4 Spiky vs flat — the user's own hypothesis

`corr(rank spread, points) = +0.294`, n=12. **Not significant.** Do not present
this as a finding.

But the per-team detail is suggestive: The Cougars were by far the spikiest team
(spread 2.76, three top-3 categories, **FW rank 1.4**) and went 14-4-3, the best
record in the league. Thumpers: spread 1.44, **zero** top-3 categories, one
bottom-3.

So the hypothesis is half right. Flat is not automatically fatal — Adam's
Assassins won the points title with spread 1.33. **Flat with a hole in the most
decisive category is.**

### 4.5 The caveat that governs everything above

**This is one season, and it was the user's worst in twelve.** Twelve teams,
twenty-two weeks. The FW finding is strong *within* this sample but could be an
artefact of one bad year.

**Re-run `league_diagnostics.py` across every backfilled season before
committing the draft to a faceoff-centric build.** If FW is decisive in most
years, build around it with confidence. If 2025-26 was an outlier, say so
plainly and rethink. Better to revise on the Tuesday than to draft three
faceoff centres on a one-season artefact.

---

## 5. What was actually built this session

`pww-hockey/scripts/discover_leagues.py`, on branch
`claude/quirky-dijkstra-iryhl2` (pushed; **not** merged to `main`).

```bash
cd pww-hockey
python scripts/discover_leagues.py --league 1809
```

Walks `/users;use_login=1/games;game_codes=nhl/leagues`, prints every NHL league
the account has joined across all seasons with its game key, and writes
`docs/seasons.json`:

```jsonc
{
  "league_id": "1809",
  "current":   "2026-27",
  "seasons": {
    "2025-26": { "league_key": "449.l.1809", "game_key": "449", "num_teams": 12 },
    "2026-27": { "league_key": "???.l.1809", "game_key": "???", "num_teams": 12 }
  }
}
```

**This one command unblocks both tracks** — it yields the new season's key and
the full backfill map. It is also the cheapest possible test of whether Yahoo
still serves the API.

Parsing is defensive against the shapes Yahoo actually returns: game nodes as
list *or* dict, seasons with no `leagues` collection at all, league nodes as
bare dicts, multiple leagues in one season. Unit-tested against synthetic
payloads covering all four.

`Thumper_GM/analysis/league_diagnostics.py` — §4, reproducible, handles both the
current flat `data.json` and the multi-season format from §6.

---

## 6. Track 1 — year filter (PWW site) — **BUILT**

Shipped on `pww-hockey` branch `claude/quirky-dijkstra-iryhl2`. See
`scripts/README.md` in that repo for the full pipeline docs.

### Data model (implemented)

```
docs/league_keys.json      season -> league key    (discover_leagues.py)
docs/data-<season>.json    one file per season     (fetch_data.py)
docs/seasons.json          front-end manifest      (fetch_data.py)
docs/data.json             current-season mirror, kept for compatibility
```

Per-season files rather than one nested document: twelve seasons at ~190 KB
each would be a 2 MB blocking fetch on a phone, and the page only ever shows
one season at a time.

2025-26 has been migrated to `docs/data-2025-26.json`, and
`matchup_overrides.json` re-keyed as `{season: {week: [...]}}` with a
backwards-compatible read for the old week-keyed shape.

### `fetch_data.py` (rewritten, season-aware)

```bash
python scripts/fetch_data.py                            # current season (what the Action runs)
python scripts/fetch_data.py --list                     # what is configured / already fetched
python scripts/fetch_data.py --season 2024-25           # one season
python scripts/fetch_data.py --backfill --skip-complete # full history, resumable
```

Pacing via `YAHOO_CALL_DELAY` (default 0.12s). A season is roughly
`weeks × teams × 2` calls — about 500 — so twelve years is 6,000+. Run it
locally, not in the Action.

### Two bugs fixed that only surface on historical data

- **`is_current` was derived from `week == current_week`.** Yahoo keeps
  reporting a `current_week` for seasons that ended years ago, so every
  backfilled season would have marked its final week as in progress and
  **excluded it from the standings**. Now read from the league's `is_finished`
  flag, which also retro-clears the flag on stored weeks.
- **League size was taken from `TOTAL_TEAMS`.** The league has not always had
  twelve teams; `num_teams` from the manifest now wins.

### Front end

Season `<select>` in the header, styled to the existing dark theme. Season and
week are reflected in the URL hash (`#season=2024-25&week=12`) so links are
shareable, with a `hashchange` listener for back/forward.

**Degrades gracefully:** with no `seasons.json` it loads `data.json` and hides
the picker, so the live site keeps working before the backfill runs.

Verified in Chromium: selector switches seasons, the Season tab re-renders on
switch, deep links resolve, week navigation works afterwards, and the
no-manifest fallback renders with no JS errors.

### The team-identity trap — partially addressed

`data.json` keys teams by **display name**, which managers change between
seasons. Manager `guid` and `team_key` are now captured into each season's
`teams` block during the fetch, so the identifier is being collected. Nothing
consumes it yet.

**Any future cross-season feature** (all-time records, head-to-head across
years, a manager's history) must key on `guid`, not team name. Per-season views
are unaffected.

### Still to do

- Run `discover_leagues.py` then the backfill — **no historical data has been
  fetched**, only the plumbing exists.
- Refetch 2025-26 once (`--season 2025-26`): its week 23 is still flagged
  `is_current` from the final April fetch, so it shows as "(live)" in the
  picker and is excluded from that season's standings.
- `YAHOO_LEAGUE_KEY` in the Action is now only a fallback; once
  `league_keys.json` is committed the workflow reads the current season from it.

## 7. Track 2 — the draft machine

### 7.1 What it must not be

Not a ranked player list. A ranked list is what produces a flat team with no
identity — it optimises total value, and total value is not how H2H categories
are won.

### 7.2 The actual objective

Win 7+ of 12 categories per week. For each category, what matters is
`P(your weekly total > opponent's weekly total)`, and expected category wins is
`Σ_c P(win c)`.

Even allocation gives P≈0.5 everywhere → 6.0 expected cats → coin flip, which is
roughly where Thumpers landed (5.64). The gain from concentration comes from two
places, not from concentration per se:

1. **Cheap categories.** HIT/BLK/FW specialists go late because most managers
   chase goals. Same category rank, far less draft capital.
2. **Bundling.** A faceoff centre also delivers G/A/SOG. Categories are not
   independent axes you trade along — players are bundles, and the good buys are
   bundles the room undervalues.

So: target FW + HIT (decisive *and* lockable *and* cheap), stay live in
PPP/SOG/A/G, and **genuinely punt** SHO and SV% (ratios 0.19 / 0.24 — close to
coin flips, so paying for them buys almost nothing).

Punting properly means rank 12 and the capital spent elsewhere. Last season's
FW was rank 10 with no compensating gain — the worst of both.

### 7.3 Design

Reuse `_compute_skater_xwa` and the goalie block from `pages/5_Flippers.py`
almost verbatim; the horizon changes from "rest of week" to "per average week
over a season".

```
Projections  ->  per-player expected per-game contribution vector (12 cats)
                 x expected games per week
                     |
                     v
             Roster state (mine + all 11 opponents, updated every pick)
                     |
                     v
             dECW(player) = E[cat wins | roster + player] - E[cat wins | roster]
                     |
                     v
             Live board sorted by dECW, with positional scarcity + run detection
```

**`ΔECW` is the number to put on screen.** "Adding this player raises expected
weekly category wins by 0.31" is directly actionable in a way that a Z-score is
not.

### 7.4 Minimum viable, if time runs short

Draft day is hostile to live API calls. **Pre-bake everything the night before**
into a static CSV and make the tool work fully offline. A Streamlit page that
cannot load because Yahoo rate-limited you mid-draft is worse than a spreadsheet.

Ship in this order:

1. **Player projection table** — per-game rate per category, expected games/week.
   Export to CSV. *Do this first; everything depends on it.*
2. **Offline draft board** — mark players taken, see your projected category
   ranks update live. Even without ΔECW this beats guessing.
3. **ΔECW ranking** — the real engine.
4. **Opponent modelling / run detection** — nice to have, cut first.

### 7.5 Projections are the weakest link — needs a decision

Last season's per-game rates are a poor projection: no rookies, no age curves,
no team/line changes, no post-trade role shifts. Options, roughly in order of
effort:

- Last season's rates + a manual adjustment column (fastest, transparent, bad on rookies)
- A public projection source (most good ones are paywalled)
- Whatever source the user already trusts, pasted in as CSV

**Ask which they want before building the projection layer.** Everything
downstream inherits its errors, and the machine's output will look confident
regardless of how bad the inputs are.

### 7.6 Goalies

`Thumper_GM` scores **none** of the four goalie categories. That is a third of
the league ignored. `build_goalie_df()` and `fetch_goalies()` already exist in
`analytics.py` / `nhl_api.py` — the valuation layer is what is missing, and the
Flippers page already shows how to project wins properly (log5 + Pythagorean).

Note that SV% and SHO are the two noisiest categories in the league, so goalie
draft capital should chase **W and SV** (volume on a good team), not ratios.

### 7.7 Keepers

Two per team, already assigned — so **24 players are off the board before pick
one**, including the opponents'. This materially changes scarcity: if the room's
keepers are goal-scoring forwards, faceoff and hit specialists get *relatively*
more expensive, not less. Load the keeper list first and remove those players
from the pool before computing anything.

**The keeper sheet was never read this session** (`docs.google.com` blocked).
Get it locally.

---

## 8. Branch and commit state

| Repo | Branch | Status |
|---|---|---|
| `lacktoes/Thumper_GM` | `claude/quirky-dijkstra-iryhl2` | `analysis/` + this file |
| `lacktoes/pww-hockey` | `claude/quirky-dijkstra-iryhl2` | `discover_leagues.py`, multi-season pipeline + season selector. Pushed, **not merged** |

Neither repo's `main`/`master` has been touched. No PRs opened.

---

## 9. First five things to do locally

1. `python scripts/discover_leagues.py --league 1809` — **does the Yahoo API still work?**
   Everything branches off this answer. Then `python scripts/fetch_data.py --list`
   to confirm the seasons it found.
2. Read the keeper sheet; get all 24 keepers into a CSV.
3. Run the Streamlit app once to populate `data/players.db` and `data/schedule.db`.
4. Start the backfill — `python scripts/fetch_data.py --backfill --skip-complete`.
   Slow and resumable; begin it early and let it run in the background.
5. Re-run `league_diagnostics.py` per backfilled season and check whether the
   faceoff finding holds across years. **This governs the entire draft strategy.**

---

## 10. Open questions

- Actual draft date and time — today, or 2026-09-26?
- Roster slots: how many C / LW / RW / D / G / bench / IR? Drives positional
  scarcity, and none of it is in `config.yaml`.
- Projection source (§7.5).
- Does the league use weekly transaction limits or a games-played cap? Both
  change streaming value, and neither is modelled anywhere.
- Was 2025-26 genuinely atypical? The user says they normally come third. If the
  backfill confirms that, understanding what was different last season may
  matter more than any draft-day tool.
