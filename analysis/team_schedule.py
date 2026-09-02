"""
analysis/team_schedule.py

Goal 13, Step 1: build a lightweight {team: {week: opponent}} schedule lookup
from the already-cached Sleeper weekly projections
(`data/sleeper_projections_cache/<season>_<week>.json`), reusing the
existing `fetch_week_projections` helper from `opponent_matchup_analysis.py`
so repeated runs don't re-hit the API.

Since every player on a team plays the same opponent in a given week, we
only need the first (team, opponent) pair seen per week per team — no need
to iterate every player row for every team.

Usage
    from analysis.team_schedule import (
        build_team_schedule,
        get_team_bye_week,
        EARLY_WEEKS,
        LATE_WEEKS,
    )

    schedule = build_team_schedule(season=2026)
    schedule["SEA"][3]   # -> opponent abbreviation Seattle plays in week 3
    get_team_bye_week(schedule, "SEA")  # -> bye week number, or None
"""

from __future__ import annotations

import logging
from pathlib import Path

from analysis.opponent_matchup_analysis import (
    DEFAULT_SEASON,
    DEFAULT_WEEKS,
    fetch_week_projections,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# Weeks 1-11 ("playoff push" early schedule) vs. weeks 12-18 (rest of season),
# per goal_12_playoff_odds_projections.md's framing.
EARLY_WEEKS = range(1, 12)
LATE_WEEKS = range(12, 19)


def build_team_schedule(
    season: int = DEFAULT_SEASON,
    weeks: range = DEFAULT_WEEKS,
) -> dict[str, dict[int, str]]:
    """Build a {team: {week: opponent}} schedule lookup for a season.

    Reads (and relies on caching from) `fetch_week_projections`, taking the
    first (team, opponent) pair seen per week. Teams on a bye in a given
    week simply have no entry for that week (no players get an opponent
    entry that week), which naturally excludes bye weeks.
    """
    schedule: dict[str, dict[int, str]] = {}

    for week in weeks:
        entries = fetch_week_projections(season, week)
        if not entries:
            continue

        seen_teams_this_week: set[str] = set()
        for entry in entries:
            team = entry.get("team")
            opponent = entry.get("opponent")
            if not team or not opponent:
                continue
            if team in seen_teams_this_week:
                continue
            seen_teams_this_week.add(team)
            schedule.setdefault(team, {})[week] = opponent

    if not schedule:
        log.warning(
            f"season={season}: no schedule data found across weeks {weeks} — "
            "check that projections have been cached (run "
            "opponent_matchup_analysis first) or that the season/week range "
            "is valid."
        )

    return schedule


def get_team_bye_week(
    schedule: dict[str, dict[int, str]],
    team: str,
    weeks: range = DEFAULT_WEEKS,
) -> int | None:
    """Return the bye week for a team, inferred as the first week in `weeks`
    for which the team has no scheduled opponent. Returns None if the team
    is scheduled every week in `weeks` (or the team isn't in the schedule
    at all, in which case there's no data to infer a bye from).
    """
    team_weeks = schedule.get(team)
    if not team_weeks:
        return None
    for week in weeks:
        if week not in team_weeks:
            return week
    return None


def count_games_in_range(
    schedule: dict[str, dict[int, str]],
    team: str,
    weeks: range,
) -> int:
    """Count how many weeks in `weeks` a team actually has a scheduled
    opponent (i.e. excludes their bye week if it falls within `weeks`)."""
    team_weeks = schedule.get(team)
    if not team_weeks:
        return 0
    return sum(1 for week in weeks if week in team_weeks)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build and inspect the team schedule lookup.")
    parser.add_argument("--season", type=int, default=DEFAULT_SEASON)
    parser.add_argument("--team", type=str, default=None, help="Print the schedule for a single team (e.g. SEA).")
    args = parser.parse_args()

    sched = build_team_schedule(season=args.season)
    log.info(f"Built schedule for {len(sched)} teams, season={args.season}")

    if args.team:
        team = args.team.upper()
        team_sched = sched.get(team, {})
        bye = get_team_bye_week(sched, team)
        early_games = count_games_in_range(sched, team, EARLY_WEEKS)
        late_games = count_games_in_range(sched, team, LATE_WEEKS)
        print(f"{team}: bye week = {bye}")
        print(f"{team}: weeks 1-11 opponents = {[team_sched.get(w) for w in EARLY_WEEKS]}")
        print(f"{team}: weeks 12-18 opponents = {[team_sched.get(w) for w in LATE_WEEKS]}")
        print(f"{team}: early games = {early_games}, late games = {late_games}")
    else:
        for team in sorted(sched):
            bye = get_team_bye_week(sched, team)
            print(f"{team}: bye={bye}, weeks_scheduled={sorted(sched[team].keys())}")
