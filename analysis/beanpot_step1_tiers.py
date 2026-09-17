"""
analysis/beanpot_step1_tiers.py

Step 1 of plans/goal_17_revalidate_playoff_brackets.md.

For each season in data/league_matchup_history.csv, reconstructs beanpot
tier (top/middle/bottom) membership from scratch:

  1. Build weeks 1-11 regular-season standings (wins, then points_for as an
     informational tiebreak only -- ties on wins are NOT resolved by
     points, they're left as `TIES`).
  2. Any team with an unambiguous position (i.e. its win total is unique,
     so its 1-12 standings position is certain regardless of how any other
     ties shake out) gets a "hard seed" and is bucketed into a beanpot by
     that seed: 1-4 top, 5-8 middle, 9-12 bottom.
  3. Teams left as `TIES` (shared a win total with at least one other team)
     get pulled into a beanpot via connected components over the week
     12 + week 13 matchup graph: any team that (transitively, via playing
     each other in weeks 12-13) shares a component with a hard-seeded team
     inherits that team's beanpot.
  4. Any teams still unresolved after step 3: if exactly one beanpot is
     short of its 4 members, the leftover team(s) must belong to it. If
     more than one beanpot is short, the season is flagged `UNRESOLVED`
     rather than guessed.
  5. All week 12-13 games among the 12 teams are collected. A week-13 game
     is marked `meaningful` only if both participants won their week-12
     game; otherwise it's marked meaningless (per the plan, since it does
     not participate in determining the tier's top-line bracket winner).

Outputs:
    data/beanpot_step1_tiers.csv  -- season, team, beanpot tier, resolution info
    data/beanpot_step1_games.csv  -- season, week 12-13 games, meaningful flag

Usage:
    python analysis/beanpot_step1_tiers.py
"""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from pathlib import Path

from beanpot_playoff_bracket_analysis import (
    REGULAR_WEEKS,
    BEANPOT_WEEKS,
    compute_regular_standings,
    load_matchups,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
OWNER_ALIASES_CSV = REPO_ROOT / "data" / "owner_aliases.csv"
TIERS_OUTPUT_CSV = REPO_ROOT / "data" / "beanpot_step1_tiers.csv"
GAMES_OUTPUT_CSV = REPO_ROOT / "data" / "beanpot_step1_games.csv"

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

TIER_ORDER = ["top_beanpot", "middle_beanpot", "bottom_beanpot"]
TIER_SIZE = 4


def load_owner_real_names() -> dict[str, str]:
    mapping = {}
    with open(OWNER_ALIASES_CSV, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            mapping[row["owner_name"]] = row["real_name"]
    return mapping


def compute_position_groups(standings: dict[int, dict]) -> list[dict]:
    """Sort teams by wins desc (points_for desc as a secondary, informational
    sort only within a group). Return a list of groups, each:
        {"start": int, "end": int, "team_ids": [...]}
    where start/end are the 1-based standings-position range the group
    occupies. A group of size 1 has a fully-determined position; a group of
    size > 1 is a tie (position within the range is unresolved).
    """
    by_wins: dict[int, list[int]] = defaultdict(list)
    for team_id, stats in standings.items():
        by_wins[stats["wins"]].append(team_id)

    groups = []
    pos = 1
    for wins in sorted(by_wins, reverse=True):
        team_ids = sorted(by_wins[wins], key=lambda t: -standings[t]["points_for"])
        size = len(team_ids)
        groups.append({"start": pos, "end": pos + size - 1, "team_ids": team_ids})
        pos += size
    return groups


def assign_hard_seeds(groups: list[dict]) -> dict[int, dict]:
    """team_id -> {"hard_seed": int|None, "position_range": (start, end)}."""
    result: dict[int, dict] = {}
    for group in groups:
        is_hard = len(group["team_ids"]) == 1
        for team_id in group["team_ids"]:
            result[team_id] = {
                "hard_seed": group["start"] if is_hard else None,
                "position_range": (group["start"], group["end"]),
            }
    return result


def seed_to_tier(seed: int) -> str:
    if seed <= 4:
        return "top_beanpot"
    elif seed <= 8:
        return "middle_beanpot"
    else:
        return "bottom_beanpot"


BUCKET_BOUNDS = {
    "top_beanpot": (1, 4),
    "middle_beanpot": (5, 8),
    "bottom_beanpot": (9, 12),
}


def range_to_tier(start: int, end: int) -> str | None:
    """If [start, end] fits entirely within one beanpot's position range,
    return that tier name; otherwise None (the range straddles a boundary,
    so it can't be resolved without guessing)."""
    for tier_name, (lo, hi) in BUCKET_BOUNDS.items():
        if lo <= start and end <= hi:
            return tier_name
    return None


class UnionFind:
    def __init__(self, items):
        self.parent = {i: i for i in items}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def cluster_via_beanpot_games(
    rows: list[dict], beanpot_weeks: list[int], team_ids: set[int]
) -> dict[int, set[int]]:
    """Union teams that play each other in weeks 12-13. Returns component-root
    -> set of team_ids (only over the given team_ids)."""
    uf = UnionFind(team_ids)
    for r in rows:
        if r["week"] not in beanpot_weeks or r["away_team_id"] is None:
            continue
        h, a = r["home_team_id"], r["away_team_id"]
        if h in team_ids and a in team_ids:
            uf.union(h, a)
    components: dict[int, set[int]] = defaultdict(set)
    for t in team_ids:
        components[uf.find(t)].add(t)
    return components


def resolve_season_tiers(
    season: int, standings: dict[int, dict], rows: list[dict]
) -> tuple[dict[int, str], dict[int, str]]:
    """Returns (team_id -> tier, team_id -> resolution_method)."""
    all_team_ids = set(standings.keys())
    groups = compute_position_groups(standings)
    seed_info = assign_hard_seeds(groups)

    tier: dict[int, str] = {}
    resolution: dict[int, str] = {}

    # Step 2: hard-seeded teams get bucketed directly.
    for team_id, info in seed_info.items():
        if info["hard_seed"] is not None:
            tier[team_id] = seed_to_tier(info["hard_seed"])
            resolution[team_id] = f"hard_seed_{info['hard_seed']}"

    # Step 3: cluster via week 12/13 matchup graph; pull in tied teams that
    # share a component with a hard-seeded team. If a component has no
    # hard-seeded anchor, but the *combined* standings-position range of all
    # its members still fits entirely within one beanpot's position range
    # (e.g. positions 9-12 for a 4-team component), that's still a safe,
    # non-guessing deduction -- assign the whole component to that tier.
    components = cluster_via_beanpot_games(rows, BEANPOT_WEEKS, all_team_ids)
    for _root, members in components.items():
        known_tiers = {tier[t] for t in members if t in tier}
        if len(known_tiers) > 1:
            log.warning(
                f"[{season}] beanpot week 12/13 component spans multiple "
                f"tiers via clustering: {known_tiers} for teams {members} "
                "-- tier heuristic may be wrong for this season."
            )
            continue
        if len(known_tiers) == 1:
            (the_tier,) = known_tiers
            for t in members:
                if t not in tier:
                    tier[t] = the_tier
                    resolution[t] = "clustered_week12_13"
            continue

        # No hard-seeded anchor in this component at all -- fall back to
        # checking whether the component's combined position range is
        # entirely contained within a single beanpot's range.
        starts = [seed_info[t]["position_range"][0] for t in members]
        ends = [seed_info[t]["position_range"][1] for t in members]
        range_tier = range_to_tier(min(starts), max(ends))
        if range_tier is not None:
            for t in members:
                tier[t] = range_tier
                resolution[t] = "component_position_range"

    # Step 4: resolve leftovers if exactly one tier is short.
    unresolved = [t for t in all_team_ids if t not in tier]
    if unresolved:
        counts = {tname: sum(1 for t in tier.values() if t == tname) for tname in TIER_ORDER}
        short_tiers = [tname for tname in TIER_ORDER if counts[tname] < TIER_SIZE]
        if len(short_tiers) == 1:
            (only_short,) = short_tiers
            needed = TIER_SIZE - counts[only_short]
            if needed == len(unresolved):
                for t in unresolved:
                    tier[t] = only_short
                    resolution[t] = "leftover_single_short_tier"
            else:
                log.warning(
                    f"[{season}] single short tier {only_short} needs "
                    f"{needed} teams but {len(unresolved)} remain "
                    f"unresolved -- marking UNRESOLVED: {unresolved}"
                )
                for t in unresolved:
                    tier[t] = "UNRESOLVED"
                    resolution[t] = "unresolved_count_mismatch"
        else:
            log.warning(
                f"[{season}] {len(short_tiers)} beanpot tiers still short "
                f"of {TIER_SIZE} teams after clustering ({short_tiers}); "
                f"cannot resolve remaining teams without guessing: "
                f"{unresolved} -- marking UNRESOLVED."
            )
            for t in unresolved:
                tier[t] = "UNRESOLVED"
                resolution[t] = "unresolved_multiple_short_tiers"

    return tier, resolution


def collect_beanpot_games(rows: list[dict], season: int) -> list[dict]:
    """All week 12-13 games, with week-13 games flagged meaningful only if
    both participants won their week-12 game."""
    round1_week, round2_week = BEANPOT_WEEKS

    round1_games = [
        r for r in rows if r["week"] == round1_week and r["away_team_id"] is not None
    ]
    round1_winners: set[int] = set()
    for r in round1_games:
        h, a, hs, as_ = r["home_team_id"], r["away_team_id"], r["home_score"], r["away_score"]
        round1_winners.add(h if hs >= as_ else a)

    out = []
    for r in rows:
        if r["week"] not in BEANPOT_WEEKS or r["away_team_id"] is None:
            continue
        h, a = r["home_team_id"], r["away_team_id"]
        if r["week"] == round1_week:
            meaningful = True
        else:
            meaningful = h in round1_winners and a in round1_winners
        out.append({
            "season": season,
            "week": r["week"],
            "home_team_id": h,
            "home_team_name": r["home_team_name"],
            "home_owner_name": r["home_owner_name"],
            "home_score": r["home_score"],
            "away_team_id": a,
            "away_team_name": r["away_team_name"],
            "away_owner_name": r["away_owner_name"],
            "away_score": r["away_score"],
            "meaningful": meaningful,
        })
    return out


def main() -> None:
    real_names = load_owner_real_names()
    by_season = load_matchups()

    tier_rows: list[dict] = []
    game_rows: list[dict] = []

    for season in sorted(by_season):
        rows = by_season[season]
        max_week = max((r["week"] for r in rows), default=0)
        if max_week < BEANPOT_WEEKS[-1]:
            log.info(f"[{season}] Beanpot weeks (12-13) not yet reached; skipping.")
            continue

        standings = compute_regular_standings(rows, REGULAR_WEEKS)
        if len(standings) < 3:
            log.warning(f"[{season}] Too few teams ({len(standings)}); skipping.")
            continue

        tier, resolution = resolve_season_tiers(season, standings, rows)

        ranked = sorted(standings.items(), key=lambda kv: (-kv[1]["wins"], -kv[1]["points_for"]))
        rank_lookup = {team_id: i + 1 for i, (team_id, _s) in enumerate(ranked)}

        for team_id, stats in standings.items():
            tier_rows.append({
                "season": season,
                "team_id": team_id,
                "team_name": stats["name"],
                "owner_name": stats["owner"],
                "real_name": real_names.get(stats["owner"], ""),
                "reg_wins": stats["wins"],
                "reg_losses": stats["losses"],
                "reg_ties": stats["ties"],
                "reg_points_for": round(stats["points_for"], 2),
                "reg_season_rank": rank_lookup[team_id],
                "beanpot_tier": tier.get(team_id, "UNRESOLVED"),
                "resolution_method": resolution.get(team_id, "unresolved"),
            })

        game_rows.extend(collect_beanpot_games(rows, season))

    tier_rows.sort(key=lambda r: (r["season"], r["reg_season_rank"]))
    game_rows.sort(key=lambda r: (r["season"], r["week"], r["home_team_id"]))

    with open(TIERS_OUTPUT_CSV, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(tier_rows[0].keys()))
        writer.writeheader()
        writer.writerows(tier_rows)
    log.info(f"Wrote {len(tier_rows)} rows to {TIERS_OUTPUT_CSV}")

    with open(GAMES_OUTPUT_CSV, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(game_rows[0].keys()))
        writer.writeheader()
        writer.writerows(game_rows)
    log.info(f"Wrote {len(game_rows)} rows to {GAMES_OUTPUT_CSV}")


if __name__ == "__main__":
    main()
