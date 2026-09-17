"""
analysis/beanpot_step2_playoffs.py

Step 2 of plans/goal_17_revalidate_playoff_brackets.md.

Reconstructs playoff bracket membership (winners vs. losers) per season by
working backwards from the known championship-week result and known
true-last-place result, tracing opponents through the game log week by week,
per the algorithm laid out in Step 2 of the plan:

  1. Champion + champion's final-week opponent -> winners bracket.
  2. N-1 week: champion's opponent, and the champion's final-week opponent's
     opponent -> winners bracket.
  3. Last-place team + last-place's final-week opponent -> losers bracket.
  4. N-1 week: last-place's opponent, and last-place's final-week opponent's
     opponent -> losers bracket.
  5. N-2 week, from the middle-beanpot winner (never has a bye) -> winners
     bracket, plus whoever they played in N-2.
  6. N-2 week, from the bottom-beanpot winner ("immune" team) IF already
     known (from steps 1-2/5) to be in the winners bracket -> plus whoever
     they played in N-2.
  7. Anything left over (including all losers-bracket N-2 byes/pairings,
     which are ambiguous per the plan) is marked UNRESOLVED.

Season 2026 is excluded -- it's still in progress and has no known
champion/last-place yet.

Note on two-week combined-score finals: some seasons (2021-2023) have 4
playoff weeks instead of 3 because the championship round is decided by
combined score across the last two weeks. In those seasons the "final week"
opponent is computed by pairing teams across both of those weeks (a team's
final-round opponent is whoever they were paired with in either of the last
two weeks).

Output: data/beanpot_step2_playoffs.csv, one row per (season, team), plus a
log of unresolved teams/games at the end of the run.

Usage:
    python analysis/beanpot_step2_playoffs.py
"""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MATCHUP_CSV = REPO_ROOT / "data" / "league_matchup_history.csv"
GAMES_CSV = REPO_ROOT / "data" / "beanpot_step1_games.csv"
TIERS_CSV = REPO_ROOT / "data" / "beanpot_step1_tiers.csv"
OUTPUT_CSV = REPO_ROOT / "data" / "beanpot_step2_playoffs.csv"

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# Ground truth from plans/goal_17_revalidate_playoff_brackets.md
CHAMPIONS = {
    2020: "rigbyjp",
    2021: "jordan_rand17",
    2022: "Christopher3205",
    2023: "Christopher3205",
    2024: "Fine Cutlery",
    2025: "ZTurk88",
}
LAST_PLACE = {
    2020: "Freeman0929",
    2021: "salaamout",
    2022: "jackhammersfan",
    2023: "azorn11",
    2024: "salaamout",
    2025: "jordan_rand17",
}

# User-provided ground truth: the 4 winners-bracket teams that actually
# played a game in round 1 (week 14) that season (the other 2 winners-
# bracket teams had a week-14 bye). Owner names.
KNOWN_ROUND1_WINNERS_BRACKET = {
    2020: ["jackhammersfan", "salaamout", "rigbyjp", "NUHusky2014"],
    2021: ["Christopher3205", "jackhammersfan", "cheezhead430", "Fine Cutlery"],
    2022: ["Fine Cutlery", "salaamout", "Christopher3205", "jordan_rand17"],
    2023: ["NUHusky2014", "ZTurk88", "cheezhead430", "dporcello419"],
    2024: ["azorn11", "rigbyjp", "NUHusky2014", "cheezhead430"],
    2025: ["salaamout", "rigbyjp", "Fine Cutlery", "cheezhead430"],
}

# User-provided ground truth: winners-bracket teams known to have had a
# round-1 (week 14) bye (so they have no round-1 opponent to trace).
KNOWN_ROUND1_BYE_WINNERS_BRACKET = {
    2023: ["rigbyjp"],
}


def load_matchups() -> dict[int, list[dict]]:
    by_season: dict[int, list[dict]] = defaultdict(list)
    with open(MATCHUP_CSV, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            season = int(row["season"])
            row["week"] = int(row["week"])
            row["home_team_id"] = int(row["home_team_id"])
            row["away_team_id"] = int(row["away_team_id"]) if row["away_team_id"] else None
            row["home_score"] = float(row["home_score"]) if row["home_score"] else None
            row["away_score"] = float(row["away_score"]) if row["away_score"] else None
            by_season[season].append(row)
    return by_season


def load_tiers() -> dict[int, dict[int, dict]]:
    by_season: dict[int, dict[int, dict]] = defaultdict(dict)
    with open(TIERS_CSV, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            by_season[int(row["season"])][int(row["team_id"])] = row
    return by_season


def load_beanpot_games() -> dict[int, list[dict]]:
    by_season: dict[int, list[dict]] = defaultdict(list)
    with open(GAMES_CSV, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            season = int(row["season"])
            row["week"] = int(row["week"])
            row["home_team_id"] = int(row["home_team_id"])
            row["away_team_id"] = int(row["away_team_id"])
            row["home_score"] = float(row["home_score"])
            row["away_score"] = float(row["away_score"])
            row["meaningful"] = row["meaningful"] == "True"
            by_season[season].append(row)
    return by_season


def team_id_for_owner(rows: list[dict], owner_name: str) -> int | None:
    for r in rows:
        if r["home_owner_name"] == owner_name:
            return r["home_team_id"]
        if r["away_owner_name"] == owner_name:
            return r["away_team_id"]
    return None


def find_tier_winner(games: list[dict], tier_team_ids: set[int]) -> int | None:
    """Winner of the round-2 (week 13) meaningful game between the two
    round-1 winners of a given beanpot tier."""
    for g in games:
        if g["week"] != 13 or not g["meaningful"]:
            continue
        if g["home_team_id"] in tier_team_ids and g["away_team_id"] in tier_team_ids:
            return g["home_team_id"] if g["home_score"] > g["away_score"] else g["away_team_id"]
    return None


def build_round_opponents(rows: list[dict], weeks: set[int]) -> dict[int, int]:
    """team_id -> opponent_id, aggregating across `weeks` (for two-week
    combined-score finals). Assumes the same pairing holds across all weeks
    given."""
    opp: dict[int, int] = {}
    for r in rows:
        if r["week"] not in weeks or r["away_team_id"] is None:
            continue
        h, a = r["home_team_id"], r["away_team_id"]
        opp[h] = a
        opp[a] = h
    return opp


def name_for(tiers: dict[int, dict], team_id: int) -> str:
    info = tiers.get(team_id)
    if not info:
        return str(team_id)
    return f"{info.get('real_name', '')} ({info.get('owner_name', '')})"


def main() -> None:
    by_season = load_matchups()
    tiers_by_season = load_tiers()
    games_by_season = load_beanpot_games()

    out_rows: list[dict] = []
    unresolved_teams_log: list[str] = []
    unresolved_games_log: list[str] = []

    for season in sorted(CHAMPIONS):
        rows = by_season.get(season, [])
        if not rows:
            continue
        max_week = max(r["week"] for r in rows)
        playoff_weeks = list(range(14, max_week + 1))
        n_playoff_weeks = len(playoff_weeks)

        if n_playoff_weeks == 3:
            final_weeks = {playoff_weeks[2]}
            prev_week = {playoff_weeks[1]}
            prevprev_week = {playoff_weeks[0]}
        elif n_playoff_weeks == 4:
            final_weeks = {playoff_weeks[2], playoff_weeks[3]}
            prev_week = {playoff_weeks[1]}
            prevprev_week = {playoff_weeks[0]}
        else:
            log.warning(f"[{season}] unexpected playoff week count {n_playoff_weeks}; skipping.")
            continue

        tiers = tiers_by_season.get(season, {})
        all_team_ids = set(tiers.keys())
        if not all_team_ids:
            log.warning(f"[{season}] no tier data; skipping.")
            continue

        champion_id = team_id_for_owner(rows, CHAMPIONS[season])
        last_place_id = team_id_for_owner(rows, LAST_PLACE[season])
        if champion_id is None or last_place_id is None:
            log.warning(f"[{season}] could not resolve champion/last-place team_id; skipping.")
            continue

        final_opp = build_round_opponents(rows, final_weeks)
        prev_opp = build_round_opponents(rows, prev_week)
        prevprev_opp = build_round_opponents(rows, prevprev_week)

        winners: set[int] = set()
        losers: set[int] = set()
        trace: dict[int, str] = {}

        def mark(team_id: int | None, bucket: str, note: str) -> None:
            if team_id is None:
                return
            (winners if bucket == "winners" else losers).add(team_id)
            trace.setdefault(team_id, note)

        # Steps 1-2: winners bracket from the champion.
        mark(champion_id, "winners", "champion")
        team_a = final_opp.get(champion_id)
        mark(team_a, "winners", "final_week_opponent_of_champion")
        mark(prev_opp.get(champion_id), "winners", "N-1_opponent_of_champion")
        mark(prev_opp.get(team_a), "winners", "N-1_opponent_of_champions_final_opponent")

        # Steps 3-4: losers bracket from true last place.
        mark(last_place_id, "losers", "last_place")
        team_b = final_opp.get(last_place_id)
        mark(team_b, "losers", "final_week_opponent_of_last_place")
        mark(prev_opp.get(last_place_id), "losers", "N-1_opponent_of_last_place")
        mark(prev_opp.get(team_b), "losers", "N-1_opponent_of_last_places_final_opponent")

        # Step 5: N-2 winners bracket, from the middle-beanpot winner.
        middle_ids = {tid for tid, t in tiers.items() if t["beanpot_tier"] == "middle_beanpot"}
        games = games_by_season.get(season, [])
        middle_winner = find_tier_winner(games, middle_ids)
        if middle_winner is not None:
            mark(middle_winner, "winners", "middle_beanpot_winner")
            mark(prevprev_opp.get(middle_winner), "winners", "N-2_opponent_of_middle_beanpot_winner")
        else:
            log.warning(f"[{season}] could not identify middle-beanpot winner.")

        # Step 6: N-2 winners bracket, from the bottom-beanpot winner, only
        # if already confirmed to be in the winners bracket.
        bottom_ids = {tid for tid, t in tiers.items() if t["beanpot_tier"] == "bottom_beanpot"}
        bottom_winner = find_tier_winner(games, bottom_ids)
        if bottom_winner is not None and bottom_winner in winners:
            mark(prevprev_opp.get(bottom_winner), "winners", "N-2_opponent_of_bottom_beanpot_winner")

        # User-provided ground truth: known round-1 (week 14 / N-2) winners-
        # bracket teams (the 4 non-bye winners-bracket teams), plus whoever
        # they played in round 1.
        for owner in KNOWN_ROUND1_WINNERS_BRACKET.get(season, []):
            tid = team_id_for_owner(rows, owner)
            if tid is None:
                log.warning(f"[{season}] could not resolve known round-1 winner owner {owner!r}.")
                continue
            if tid in losers:
                log.warning(
                    f"[{season}] CONFLICT: {name_for(tiers, tid)} was previously traced into the "
                    "losers bracket, but is listed as a known round-1 winners-bracket team."
                )
                continue
            mark(tid, "winners", "known_round1_winners_bracket_member")
            mark(prevprev_opp.get(tid), "winners", "N-2_opponent_of_known_round1_winners_bracket_member")

        # User-provided ground truth: known round-1 winners-bracket bye teams.
        for owner in KNOWN_ROUND1_BYE_WINNERS_BRACKET.get(season, []):
            tid = team_id_for_owner(rows, owner)
            if tid is None:
                log.warning(f"[{season}] could not resolve known round-1 bye owner {owner!r}.")
                continue
            if tid in losers:
                log.warning(
                    f"[{season}] CONFLICT: {name_for(tiers, tid)} was previously traced into the "
                    "losers bracket, but is listed as a known round-1 winners-bracket bye team."
                )
                continue
            mark(tid, "winners", "known_round1_winners_bracket_bye")

        # Step 7: the winners and losers brackets are each 6 teams (12-team
        # league). Once the winners bracket has all 6 confirmed members
        # (true for every season given the ground truth above), any
        # remaining unassigned teams must be the losers bracket's two
        # round-1-bye teams -- confirmed directly for 2025, and inferred by
        # the same 6/6 bracket-size logic for the other seasons.
        if len(winners) == 6:
            for tid in all_team_ids - winners - losers:
                mark(tid, "losers", "losers_bracket_by_elimination_winners_bracket_full")

        # Anything still left over is UNRESOLVED.
        unresolved_teams = all_team_ids - winners - losers
        for tid in unresolved_teams:
            trace[tid] = "UNRESOLVED"
            unresolved_teams_log.append(f"{season}: {name_for(tiers, tid)}")

        for tid in sorted(all_team_ids):
            info = tiers[tid]
            bracket = "winners" if tid in winners else ("losers" if tid in losers else "UNRESOLVED")
            out_rows.append({
                "season": season,
                "team_id": tid,
                "team_name": info.get("team_name", ""),
                "owner_name": info.get("owner_name", ""),
                "real_name": info.get("real_name", ""),
                "beanpot_tier": info.get("beanpot_tier", ""),
                "playoff_bracket": bracket,
                "resolution": trace.get(tid, ""),
            })

        # Flag any playoff-week game involving an unresolved team.
        for w in sorted(prevprev_week | prev_week | final_weeks):
            for r in rows:
                if r["week"] != w or r["away_team_id"] is None:
                    continue
                h, a = r["home_team_id"], r["away_team_id"]
                if h in unresolved_teams or a in unresolved_teams:
                    unresolved_games_log.append(
                        f"{season} week {w}: {name_for(tiers, h)} vs {name_for(tiers, a)}"
                    )

    out_rows.sort(key=lambda r: (r["season"], r["team_id"]))
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(out_rows[0].keys()))
        writer.writeheader()
        writer.writerows(out_rows)
    log.info(f"Wrote {len(out_rows)} rows to {OUTPUT_CSV}")

    def dedupe(seq: list[str]) -> list[str]:
        seen = set()
        out = []
        for item in seq:
            if item not in seen:
                seen.add(item)
                out.append(item)
        return out

    unresolved_teams_log = dedupe(unresolved_teams_log)
    unresolved_games_log = dedupe(unresolved_games_log)

    log.info("=== UNRESOLVED TEAMS ===")
    for item in unresolved_teams_log:
        log.info(item)
    log.info("=== UNRESOLVED / AMBIGUOUS GAMES (involving an unresolved team) ===")
    for item in unresolved_games_log:
        log.info(item)


if __name__ == "__main__":
    main()
