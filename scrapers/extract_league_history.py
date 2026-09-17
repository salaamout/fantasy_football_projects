"""
scrapers/extract_league_history.py

Step 3/4 of plans/goal_16_scrape_league_history.md.

Parses the raw JSON snapshots saved under data/league_history_raw/ (produced
by scrape_espn_league_history.py) and produces cleaned CSV outputs:

  - data/league_matchup_history.csv    (season, week, home/away teams, scores,
                                         is_playoff flag)
  - data/league_standings_history.csv  (season, team, owner, record, rank)
  - data/league_weekly_rosters.csv     (season, week, team, player, slot,
                                         points) -- built from boxscore files

Also normalizes team/owner identity across seasons using the ESPN owner SWID
as the stable key (team names/abbrevs can change year to year), and logs any
missing weeks/seasons or parsing issues encountered along the way.

Usage:
    python scrapers/extract_league_history.py
    python scrapers/extract_league_history.py --skip-rosters
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DATA_DIR = REPO_ROOT / "data" / "league_history_raw"
DATA_DIR = REPO_ROOT / "data"

MATCHUP_CSV = DATA_DIR / "league_matchup_history.csv"
STANDINGS_CSV = DATA_DIR / "league_standings_history.csv"
ROSTERS_CSV = DATA_DIR / "league_weekly_rosters.csv"

# Standard ESPN fantasy football lineup slot id -> label mapping.
LINEUP_SLOT_MAP = {
    0: "QB",
    1: "TQB",
    2: "RB",
    3: "RB/WR",
    4: "WR",
    5: "WR/TE",
    6: "TE",
    7: "OP",
    8: "DT",
    9: "DE",
    10: "LB",
    11: "DL",
    12: "CB",
    13: "S",
    14: "DB",
    15: "DP",
    16: "D/ST",
    17: "K",
    18: "P",
    19: "HC",
    20: "BENCH",
    21: "IR",
    22: "UNKNOWN",
    23: "FLEX",
    24: "EDR",
}

PRO_TEAM_MAP = {
    0: "FA", 1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN",
    8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV", 14: "LAR", 15: "MIA",
    16: "MIN", 17: "NE", 18: "NO", 19: "NYG", 20: "NYJ", 21: "PHI", 22: "ARI", 23: "PIT",
    24: "LAC", 25: "SF", 26: "SEA", 27: "TB", 28: "WSH", 29: "CAR", 30: "JAX", 33: "BAL",
    34: "HOU",
}

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        log.error(f"Failed to parse {path}: {exc}")
        return None


def available_seasons() -> list[int]:
    if not RAW_DATA_DIR.exists():
        return []
    seasons = []
    for child in RAW_DATA_DIR.iterdir():
        if child.is_dir() and child.name.isdigit():
            seasons.append(int(child.name))
    return sorted(seasons)


def team_display_name(team: dict) -> str:
    """ESPN teams may have name split across location/nickname or a single `name`."""
    name = team.get("name")
    if name:
        return name
    location = (team.get("location") or "").strip()
    nickname = (team.get("nickname") or "").strip()
    combined = f"{location} {nickname}".strip()
    return combined or f"Team {team.get('id')}"


def build_team_lookup(season_data: dict) -> dict[int, dict]:
    """teamId -> {name, owner_id, abbrev}"""
    lookup = {}
    for team in season_data.get("teams", []):
        owners = team.get("owners") or []
        lookup[team["id"]] = {
            "name": team_display_name(team),
            "abbrev": team.get("abbrev", ""),
            "owner_id": owners[0] if owners else "",
        }
    return lookup


def build_member_lookup(season_data: dict) -> dict[str, str]:
    """owner SWID -> display name"""
    lookup = {}
    for member in season_data.get("members", []):
        lookup[member["id"]] = member.get("displayName") or (
            f"{member.get('firstName', '')} {member.get('lastName', '')}".strip()
        )
    return lookup


def regular_season_length(settings_data: dict | None) -> int | None:
    if not settings_data:
        return None
    return (
        settings_data.get("settings", {})
        .get("scheduleSettings", {})
        .get("matchupPeriodCount")
    )


def extract_matchups_and_standings(seasons: list[int]) -> tuple[list[dict], list[dict]]:
    matchup_rows: list[dict] = []
    standings_rows: list[dict] = []

    for season in seasons:
        season_dir = RAW_DATA_DIR / str(season)
        season_data = load_json(season_dir / "season.json")
        if season_data is None:
            log.warning(f"[{season}] No season.json found; skipping matchups/standings.")
            continue

        settings_data = load_json(season_dir / "settings.json")
        reg_season_weeks = regular_season_length(settings_data)
        if reg_season_weeks is None:
            log.warning(
                f"[{season}] No settings.json / matchupPeriodCount found; "
                "is_playoff flag will be blank for this season."
            )

        team_lookup = build_team_lookup(season_data)
        member_lookup = build_member_lookup(season_data)

        # --- Standings ---
        for team in season_data.get("teams", []):
            record = team.get("record", {}).get("overall", {})
            owners = team.get("owners") or []
            owner_id = owners[0] if owners else ""
            standings_rows.append(
                {
                    "season": season,
                    "team_id": team["id"],
                    "team_name": team_display_name(team),
                    "owner_id": owner_id,
                    "owner_name": member_lookup.get(owner_id, ""),
                    "wins": record.get("wins"),
                    "losses": record.get("losses"),
                    "ties": record.get("ties"),
                    "points_for": record.get("pointsFor"),
                    "points_against": record.get("pointsAgainst"),
                    "final_rank": team.get("rankCalculatedFinal") or team.get("rankFinal"),
                    "playoff_seed": team.get("playoffSeed"),
                }
            )

        # --- Matchups ---
        schedule = season_data.get("schedule", [])
        if not schedule:
            log.warning(f"[{season}] Empty schedule in season.json.")
        for game in schedule:
            week = game.get("matchupPeriodId")
            home = game.get("home") or {}
            away = game.get("away") or {}
            home_id = home.get("teamId")
            away_id = away.get("teamId")
            is_playoff = None
            if reg_season_weeks is not None and week is not None:
                is_playoff = week > reg_season_weeks

            # A "bye" matchup in playoffs has no away team.
            if not away:
                away_id = None

            matchup_rows.append(
                {
                    "season": season,
                    "week": week,
                    "is_playoff": is_playoff,
                    "home_team_id": home_id,
                    "home_team_name": team_lookup.get(home_id, {}).get("name", ""),
                    "home_owner_name": member_lookup.get(
                        team_lookup.get(home_id, {}).get("owner_id", ""), ""
                    ),
                    "home_score": home.get("totalPoints"),
                    "away_team_id": away_id,
                    "away_team_name": team_lookup.get(away_id, {}).get("name", "") if away_id else "",
                    "away_owner_name": member_lookup.get(
                        team_lookup.get(away_id, {}).get("owner_id", ""), ""
                    )
                    if away_id
                    else "",
                    "away_score": away.get("totalPoints") if away else None,
                }
            )

    return matchup_rows, standings_rows


def extract_weekly_rosters(seasons: list[int]) -> list[dict]:
    roster_rows: list[dict] = []

    for season in seasons:
        season_dir = RAW_DATA_DIR / str(season)
        season_data = load_json(season_dir / "season.json")
        team_lookup = build_team_lookup(season_data) if season_data else {}
        member_lookup = build_member_lookup(season_data) if season_data else {}

        boxscore_files = sorted(season_dir.glob("week_*_boxscore.json"))
        if not boxscore_files:
            log.info(f"[{season}] No boxscore files found; skipping roster extraction.")
            continue

        for box_path in boxscore_files:
            week_str = box_path.stem.split("_")[1]
            week = int(week_str)
            box_data = load_json(box_path)
            if box_data is None:
                continue

            for game in box_data.get("schedule", []):
                for side in ("home", "away"):
                    side_data = game.get(side)
                    if not side_data:
                        continue
                    team_id = side_data.get("teamId")
                    roster = side_data.get("rosterForCurrentScoringPeriod") or {}
                    entries = roster.get("entries", [])
                    for entry in entries:
                        slot_id = entry.get("lineupSlotId")
                        pool_entry = entry.get("playerPoolEntry", {})
                        player = pool_entry.get("player", {})
                        applied_total = pool_entry.get("appliedStatTotal")
                        roster_rows.append(
                            {
                                "season": season,
                                "week": week,
                                "team_id": team_id,
                                "team_name": team_lookup.get(team_id, {}).get("name", ""),
                                "owner_name": member_lookup.get(
                                    team_lookup.get(team_id, {}).get("owner_id", ""), ""
                                ),
                                "player_id": player.get("id"),
                                "player_name": player.get("fullName"),
                                "pro_team": PRO_TEAM_MAP.get(player.get("proTeamId"), player.get("proTeamId")),
                                "lineup_slot_id": slot_id,
                                "lineup_slot": LINEUP_SLOT_MAP.get(slot_id, str(slot_id)),
                                "is_starter": slot_id not in (20, 21) if slot_id is not None else None,
                                "points": applied_total,
                            }
                        )

    return roster_rows


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    if not rows:
        log.warning(f"No rows to write for {path.name}; skipping file write.")
        return
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    log.info(f"Wrote {len(rows)} rows to {path.relative_to(REPO_ROOT)}")


def validate_coverage(seasons: list[int], matchup_rows: list[dict]) -> None:
    """Log any seasons with unexpected gaps in week coverage."""
    weeks_by_season: dict[int, set[int]] = {}
    for row in matchup_rows:
        weeks_by_season.setdefault(row["season"], set()).add(row["week"])

    for season in seasons:
        weeks = weeks_by_season.get(season, set())
        if not weeks:
            log.warning(f"[{season}] No matchup weeks extracted at all.")
            continue
        expected = set(range(min(weeks), max(weeks) + 1))
        missing = expected - weeks
        if missing:
            log.warning(f"[{season}] Missing matchupPeriodIds within range: {sorted(missing)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract cleaned CSVs from raw ESPN league history JSON snapshots."
    )
    parser.add_argument(
        "--skip-rosters",
        action="store_true",
        help="Skip the (slower) weekly roster/lineup extraction from boxscore files.",
    )
    args = parser.parse_args()

    seasons = available_seasons()
    if not seasons:
        log.error(
            f"No raw data found under {RAW_DATA_DIR}. "
            "Run scrapers/scrape_espn_league_history.py first."
        )
        return
    log.info(f"Found raw data for seasons: {seasons}")

    matchup_rows, standings_rows = extract_matchups_and_standings(seasons)
    validate_coverage(seasons, matchup_rows)

    write_csv(
        MATCHUP_CSV,
        matchup_rows,
        fieldnames=[
            "season", "week", "is_playoff",
            "home_team_id", "home_team_name", "home_owner_name", "home_score",
            "away_team_id", "away_team_name", "away_owner_name", "away_score",
        ],
    )
    write_csv(
        STANDINGS_CSV,
        standings_rows,
        fieldnames=[
            "season", "team_id", "team_name", "owner_id", "owner_name",
            "wins", "losses", "ties", "points_for", "points_against",
            "final_rank", "playoff_seed",
        ],
    )

    if not args.skip_rosters:
        roster_rows = extract_weekly_rosters(seasons)
        write_csv(
            ROSTERS_CSV,
            roster_rows,
            fieldnames=[
                "season", "week", "team_id", "team_name", "owner_name",
                "player_id", "player_name", "pro_team",
                "lineup_slot_id", "lineup_slot", "is_starter", "points",
            ],
        )

    log.info("Done.")


if __name__ == "__main__":
    main()
