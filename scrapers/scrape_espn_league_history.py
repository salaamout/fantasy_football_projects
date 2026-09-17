"""
scrapers/scrape_espn_league_history.py

Fetches historical ESPN Fantasy Football league data (matchups, standings,
team/owner info, and weekly boxscores/rosters) via ESPN's read-only Fantasy
API and saves raw JSON snapshots to data/league_history_raw/.

See plans/goal_16_scrape_league_history.md for background/findings.

Key findings baked into this script (from Step 1 exploration):
  - Use host `lm-api-reads.fantasy.espn.com`, NOT `fantasy.espn.com`
    (the latter 302-redirects to the marketing homepage).
  - No auth cookies required for seasons 2020-2025 for this league. 2018/2019
    returned 401 (private/older data) - pass --espn-s2/--swid to attempt
    those seasons.
  - `view=mStandings` -> matchup schedule (scores, matchupPeriodId).
  - `view=mTeam` -> team names/owners/records/logos.
  - `view=mBoxscore&scoringPeriodId={WEEK}` -> per-team weekly
    rosters/lineups.
  - Multiple `view` params can be combined in one request to cut down on
    request count (used here for the season-level mStandings+mTeam call).

Usage:
    python scrapers/scrape_espn_league_history.py
    python scrapers/scrape_espn_league_history.py --start-season 2020 --end-season 2025
    python scrapers/scrape_espn_league_history.py --league-id 1117113 --weeks 1-17
    python scrapers/scrape_espn_league_history.py --skip-boxscores
    python scrapers/scrape_espn_league_history.py --espn-s2 "$ESPN_S2" --swid "$SWID"
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DATA_DIR = REPO_ROOT / "data" / "league_history_raw"

API_HOST = "https://lm-api-reads.fantasy.espn.com"
API_PATH_TMPL = "/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league_id}"

DEFAULT_LEAGUE_ID = 1117113
DEFAULT_START_SEASON = 2020
DEFAULT_END_SEASON = 2026
DEFAULT_WEEKS = "1-17"

DEFAULT_DELAY_SECONDS = 1.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 5.0
REQUEST_TIMEOUT = 30

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def parse_week_range(spec: str) -> list[int]:
    """Parse a "1-17" or "1,2,3" style week spec into a sorted list of ints."""
    weeks: set[int] = set()
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start_s, end_s = chunk.split("-", 1)
            weeks.update(range(int(start_s), int(end_s) + 1))
        else:
            weeks.add(int(chunk))
    return sorted(weeks)


def build_session(espn_s2: str | None, swid: str | None) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept": "application/json",
        }
    )
    if espn_s2 and swid:
        # SWID must be wrapped in curly braces per ESPN's convention.
        swid_val = swid if swid.startswith("{") else "{" + swid.strip("{}") + "}"
        session.cookies.set("espn_s2", espn_s2, domain=".fantasy.espn.com")
        session.cookies.set("SWID", swid_val, domain=".fantasy.espn.com")
    return session


def fetch_with_retry(
    session: requests.Session,
    url: str,
    params: dict,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
) -> requests.Response | None:
    """GET url with simple retry/backoff. Returns None on permanent failure."""
    attempt = 0
    while attempt <= max_retries:
        attempt += 1
        try:
            resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            log.warning(f"Request error (attempt {attempt}/{max_retries + 1}) for {url}: {exc}")
            if attempt > max_retries:
                return None
            time.sleep(backoff_seconds * attempt)
            continue

        if resp.status_code == 200:
            return resp

        if resp.status_code == 401:
            log.warning(f"401 Unauthorized for {url} (params={params}) - likely needs auth cookies.")
            return resp

        if resp.status_code == 404:
            log.warning(f"404 Not Found for {url} (params={params}) - no data at this ID/season.")
            return resp

        if resp.status_code == 429 or resp.status_code >= 500:
            log.warning(
                f"HTTP {resp.status_code} (attempt {attempt}/{max_retries + 1}) for {url}; retrying..."
            )
            if attempt > max_retries:
                return resp
            time.sleep(backoff_seconds * attempt)
            continue

        # Other 4xx: not retryable.
        log.warning(f"HTTP {resp.status_code} for {url} (params={params}); not retrying.")
        return resp

    return None


def season_dir(season: int) -> Path:
    d = RAW_DATA_DIR / str(season)
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_json(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    log.info(f"Saved {path.relative_to(REPO_ROOT)}")


def fetch_season_snapshot(
    session: requests.Session,
    league_id: int,
    season: int,
    delay_seconds: float,
    force: bool,
) -> bool:
    """
    Fetch combined mStandings+mTeam data for a season and save to disk.

    Returns True if data was fetched/saved (or already cached), False if the
    season is unavailable (401/404) so callers can skip weekly boxscores.
    """
    out_path = season_dir(season) / "season.json"
    if out_path.exists() and not force:
        log.info(f"[{season}] season.json already cached, skipping fetch.")
        return True

    url = API_HOST + API_PATH_TMPL.format(season=season, league_id=league_id)
    params = [("view", "mStandings"), ("view", "mTeam")]
    log.info(f"[{season}] Fetching season standings/team data...")
    resp = fetch_with_retry(session, url, params)
    time.sleep(delay_seconds)

    if resp is None:
        log.error(f"[{season}] Failed to fetch season data after retries.")
        return False
    if resp.status_code != 200:
        log.warning(f"[{season}] Season data unavailable (HTTP {resp.status_code}); skipping.")
        return False

    save_json(out_path, resp.json())
    return True


def fetch_week_boxscore(
    session: requests.Session,
    league_id: int,
    season: int,
    week: int,
    delay_seconds: float,
    force: bool,
) -> bool:
    out_path = season_dir(season) / f"week_{week:02d}_boxscore.json"
    if out_path.exists() and not force:
        log.info(f"[{season} wk{week}] boxscore already cached, skipping fetch.")
        return True

    url = API_HOST + API_PATH_TMPL.format(season=season, league_id=league_id)
    params = {"view": "mBoxscore", "scoringPeriodId": week}
    log.info(f"[{season} wk{week}] Fetching boxscore...")
    resp = fetch_with_retry(session, url, params)
    time.sleep(delay_seconds)

    if resp is None:
        log.error(f"[{season} wk{week}] Failed to fetch boxscore after retries.")
        return False
    if resp.status_code != 200:
        log.warning(f"[{season} wk{week}] Boxscore unavailable (HTTP {resp.status_code}); skipping.")
        return False

    data = resp.json()
    schedule = data.get("schedule") or []
    if not schedule:
        log.info(f"[{season} wk{week}] Empty schedule (likely past season end); stopping season early.")
        return False

    save_json(out_path, data)
    return True


def scrape_league_history(
    league_id: int,
    start_season: int,
    end_season: int,
    weeks: list[int],
    delay_seconds: float,
    espn_s2: str | None,
    swid: str | None,
    skip_boxscores: bool,
    force: bool,
) -> None:
    session = build_session(espn_s2, swid)
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)

    for season in range(start_season, end_season + 1):
        ok = fetch_season_snapshot(session, league_id, season, delay_seconds, force)
        if not ok:
            log.info(f"[{season}] Skipping weekly boxscores due to missing season data.")
            continue

        if skip_boxscores:
            continue

        for week in weeks:
            got_data = fetch_week_boxscore(session, league_id, season, week, delay_seconds, force)
            if not got_data:
                # Either an error or an empty schedule (season likely ended).
                # Keep going to next season rather than aborting entirely,
                # since a single bad week shouldn't stop the whole run.
                continue

    log.info(f"Done. Raw snapshots saved under {RAW_DATA_DIR.relative_to(REPO_ROOT)}/")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape historical ESPN Fantasy league data (standings, teams, boxscores)."
    )
    parser.add_argument("--league-id", type=int, default=DEFAULT_LEAGUE_ID)
    parser.add_argument("--start-season", type=int, default=DEFAULT_START_SEASON)
    parser.add_argument("--end-season", type=int, default=DEFAULT_END_SEASON)
    parser.add_argument(
        "--weeks",
        type=str,
        default=DEFAULT_WEEKS,
        help='Week range/list, e.g. "1-17" or "1,2,3" (default: %(default)s).',
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help="Seconds to sleep between requests (default: %(default)s).",
    )
    parser.add_argument(
        "--espn-s2",
        type=str,
        default=None,
        help="espn_s2 auth cookie value (needed for private/older seasons, e.g. 2018-2019).",
    )
    parser.add_argument(
        "--swid",
        type=str,
        default=None,
        help="SWID auth cookie value (needed for private/older seasons, e.g. 2018-2019).",
    )
    parser.add_argument(
        "--skip-boxscores",
        action="store_true",
        help="Only fetch season-level standings/team data; skip weekly boxscore/roster calls.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch and overwrite even if a cached snapshot already exists on disk.",
    )
    args = parser.parse_args()

    weeks = parse_week_range(args.weeks)

    scrape_league_history(
        league_id=args.league_id,
        start_season=args.start_season,
        end_season=args.end_season,
        weeks=weeks,
        delay_seconds=args.delay,
        espn_s2=args.espn_s2,
        swid=args.swid,
        skip_boxscores=args.skip_boxscores,
        force=args.force,
    )


if __name__ == "__main__":
    main()
