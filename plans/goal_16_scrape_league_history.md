# Goal 16: Scrape Historical League Data & Analysis

## 1. Data Source & Access ✅ (completed 2026-09-16)
- Use ESPN Fantasy scoreboard/boxscore pages (e.g. league `1117113`) as source.
- Target URL pattern: `scoreboard?seasonId={YEAR}&leagueId={ID}&matchupPeriodId={WEEK}`.
- Check if ESPN Fantasy API (`fantasy.espn.com/apis/v3/...`) works instead of HTML.
- Confirm league visibility/auth needs (cookies `espn_s2`, `SWID` for private leagues).
- Determine earliest available season for this league ID.

### Findings
- **The read-only JSON API works and is preferred over scraping HTML.** Use host
  `https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{YEAR}/segments/0/leagues/{LEAGUE_ID}`
  (the older `fantasy.espn.com/apis/v3/...` host 302-redirects to the marketing homepage and
  does not work for this purpose).
- **No auth cookies required for 2020–2025.** Requests without `espn_s2`/`SWID` cookies
  returned `200 OK` with full data for seasons 2020, 2021, 2022, 2023, 2024, 2025.
- **2018 and 2019 return `401 Unauthorized`** — the league existed but appears to have been
  private (or ESPN's older data requires auth) for those seasons. Would need `espn_s2` and
  `SWID` cookies from a logged-in league-member session to fetch these; can be added later if
  desired since it's optional/bonus history.
- **2013–2017 return `404 Not Found`** — league `1117113` has no data at this ID for those
  seasons (either didn't exist yet or was created under a different league ID).
- **Conclusion: earliest reliably-scrapable season without auth is 2020.** Recommend starting
  the scraper range at `seasonId=2020` through the current season, with 2018/2019 as an
  optional stretch goal requiring cookie auth.
- Useful `view` query params confirmed via testing:
  - `view=mStandings` → matchup `schedule` (home/away teamId, totalPoints, matchupPeriodId) —
    good for building `league_matchup_history.csv`.
  - `view=mTeam` → team names, `owners` (SWID-style owner IDs), abbreviations, records, logos —
    good for team/owner normalization and `league_standings_history.csv`.
  - `view=mBoxscore&scoringPeriodId={WEEK}` → per-team `schedule[].away/home` include
    `rosterForCurrentScoringPeriod` and `rosterForMatchupPeriod` with full lineup/roster
    entries — good for weekly roster/lineup extraction.
- Combine multiple `view` params in one request (e.g. `?view=mStandings&view=mTeam`) to reduce
  request count per season.
- No rate-limiting/blocking observed during this exploration; still recommend adding delay +
  retry logic in the scraper per plan step 2.

## 2. Scraper Setup ✅ (completed 2026-09-16)
- Add `scrapers/scrape_espn_league_history.py` following `parse_espn_html.py` style.
- Loop over `seasonId` and `matchupPeriodId` (week) ranges per season.
- Use `requests` with auth cookies, or Playwright if JS-rendered content required.
- Add delay/retry logic to avoid rate-limiting or blocking.

### Implementation notes
- CLI: `python scrapers/scrape_espn_league_history.py [--league-id 1117113]
  [--start-season 2020] [--end-season 2026] [--weeks 1-17] [--delay 1.0]
  [--espn-s2 ...] [--swid ...] [--skip-boxscores] [--force]`.
- Per season, fetches combined `view=mStandings&view=mTeam` in a single
  request and saves to `data/league_history_raw/{season}/season.json`.
- Per (season, week), fetches `view=mBoxscore&scoringPeriodId={week}` and
  saves to `data/league_history_raw/{season}/week_{NN}_boxscore.json`. An
  empty `schedule` response is treated as "season ended" and that week is
  skipped without aborting the whole run.
- Requests are cached on disk by default (re-running skips existing files);
  use `--force` to re-fetch.
- Retry/backoff wraps each request (3 retries, exponential-ish backoff) for
  transient network errors, 429s, and 5xxs; 401/404 responses are logged and
  treated as "unavailable for this season" without retrying.
- `--espn-s2`/`--swid` accepted for optional auth against private/older
  (2018-2019) seasons, per Step 1 findings.
- Smoke-tested against season 2025 (season.json + week 1 boxscore) — both
  fetched successfully with no auth cookies.

## 3. Data Extraction ✅ (completed 2026-09-16)
- Parse matchup scores, team names, owners, and weekly points per team.
- Capture playoff/regular-season flags and final standings per season.
- Extract roster/lineup details if available on boxscore pages.
- Normalize team/owner names across seasons (handle renames).

### Implementation notes
- Added `scrapers/extract_league_history.py`, which reads the raw JSON under
  `data/league_history_raw/{season}/` and writes three CSVs into `data/`
  (see Step 4 for exact schemas): `league_matchup_history.csv`,
  `league_standings_history.csv`, and `league_weekly_rosters.csv`.
- `season.json` (`mStandings`+`mTeam`) gives `teams` (name/abbrev/owners/
  record/final rank/playoff seed) and `schedule` (per-matchup home/away
  teamId + totalPoints). `members` gives SWID -> displayName, used as the
  stable identity key for owners since team names/nicknames are renamed
  across seasons (e.g. "javi javi" in 2020) - `owner_id` (SWID) is stored
  alongside `owner_name` in the standings CSV specifically to support
  normalization across renames.
- The `mStandings`/`mTeam` views alone don't expose the regular-season vs.
  playoff boundary, so an additional `view=mSettings` call was fetched per
  season (cached to `data/league_history_raw/{season}/settings.json`,
  same on-disk-cache convention as the other raw files) to read
  `settings.scheduleSettings.matchupPeriodCount`. `is_playoff` in the
  matchup CSV is simply `week > matchupPeriodCount` for that season.
- Roster/lineup rows come from each cached `week_{NN}_boxscore.json`'s
  `schedule[].{home,away}.rosterForCurrentScoringPeriod.entries`, using a
  standard ESPN `lineupSlotId` -> label lookup table (QB/RB/WR/TE/D-ST/K/
  FLEX/BENCH/IR/etc.) baked into the script; `is_starter` is derived as
  `lineup_slot_id not in {BENCH=20, IR=21}`.
- `validate_coverage()` checks for gaps in `matchupPeriodId` within each
  season's observed min/max week range and logs a warning; none were found
  for seasons 2020-2026 (678 matchup rows, 84 team-seasons, 18,358 roster
  entries extracted).
- Team name lookups gracefully fall back from `team.name` to
  `location + nickname` for older ESPN payload shapes that split the name.

## 4. Details on playoffs/beanpot ✅ (completed 2026-09-16, best-effort heuristic)
- Divide season into three sections: regular, beanpot, playoffs
- The actual regular season is only weeks 1-11
- Weeks 12 and 13 are the beanpot. Top 4 in standing are in top beanpot, next 4 are in middle, bottom 4 are in bottom beanpot
- The next 3 weeks are the playoffs. There is a winners bracket and a losers bracket. 4 top beanpot teams get in always, winner of middle beanpot gets in, last is a wild card that changes sometimes
- For winner's bracket, the top 2 seeds get byes for the first round, winner moves on
- For loser's bracket:
 - Winner of bottom beanpot is immune, assuming they do not get the wild card slot
 - Loser always moves on
 - If immune player DOES get wild card, format is the following:
    - Bottom 3 teams (the bottom beanpot losers) get a bye in round 1
 - If immune player DOES NOT get wild card, format is the following:
    - Bottom 2 teams get a bye in round 1
- Some analysis will likely be needed to back calculate who is in which beanpot, and who is in which loser's bracket. I think this should be doable by code

### Implementation notes
- Added `analysis/beanpot_playoff_bracket_analysis.py`, which reads
  `data/league_matchup_history.csv` and writes
  `data/beanpot_playoff_brackets.csv` (one row per season/team).
- Week boundaries are hard-coded rather than derived from ESPN's own
  `is_playoff`/`matchupPeriodCount` settings: regular season is always weeks
  1-11, beanpot is always weeks 12-13, and playoffs are whatever weeks
  remain after that (normally 3, but 4 in seasons where the final round is a
  two-week combined-score round). This was a deliberate correction after
  initially trusting ESPN's `matchupPeriodCount` (which reported 14 instead
  of 13 for 2021-2023) — the league rules always use an 11+2 regular
  season/beanpot split, so the flag from ESPN doesn't reflect that.
  2026 is skipped (in progress, hasn't reached week 12/13 yet).
- Regular-season standings (weeks 1-11 only) are ranked by wins desc, then
  points-for desc, and split into equal thirds to assign `top_beanpot` /
  `middle_beanpot` / `bottom_beanpot` tiers.
- Beanpot winner/last-place per tier is derived by modeling the beanpot as
  an actual two-round, 4-team single-elimination bracket: week 12 is round
  1 (the two within-tier games), and week 13 is round 2, where the two
  round-1 winners play each other for the tier title (`winner`/`runner_up`)
  and the two round-1 losers play each other to avoid the tier's cellar
  (`third`/`last_place`). A round-2 pairing that doesn't match
  winners-vs-winners / losers-vs-losers is logged as a warning.
- Playoff bracket membership (`winners` vs `losers`) is inferred by treating
  "played each other during a playoff week" as a graph and taking connected
  components (a true single-elimination bracket never mixes brackets); the
  component with more top-beanpot teams is labeled `winners`. Bye weeks
  (playoff rows with no away team) are recorded per team.
- **Validation results**: 2020 reproduces perfectly (0 cross-tier beanpot
  games, exactly 2 playoff bracket components), confirming the heuristic is
  sound. 2021-2025 each show a handful (~2-4 of 12) of beanpot games that
  cross the predicted tier boundary, and 2023 collapses to a single playoff
  component (all 12 teams end up connected — likely due to a wildcard/
  reseeding rule not modeled here) — meaning the wins/points-for tiebreak used here doesn't
  always match ESPN's true seeding (likely differs by head-to-head record or
  another tiebreak ESPN applied that isn't recoverable from this data), and
  the wildcard/immunity rules aren't separately modeled. These are logged as
  warnings when the script runs. The output is a best-effort approximation,
  good enough for trend analysis in Step 6, but exact bracket
  reconstruction for 2021-2025 has known inaccuracies noted above.

## 5. Storage & Cleaning
- Save raw HTML/JSON snapshots to `data/league_history_raw/`.
- Output cleaned CSV: `data/league_matchup_history.csv` (season, week, teams, scores).
- Output cleaned CSV: `data/league_standings_history.csv` (season, team, record, rank).
- Validate no missing weeks/seasons and log any parsing failures.

## 6. Downstream Analysis
- Add `analysis/league_history_analysis.py` for trends (scoring, records over time).
- Compute head-to-head records and rivalry stats between owners.
- Visualize scoring trends and playoff appearances per owner over seasons.
- Identify closest matchups, biggest blowouts, and luck-based metrics.
