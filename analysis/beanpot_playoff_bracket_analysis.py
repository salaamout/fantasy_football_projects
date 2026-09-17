"""
analysis/beanpot_playoff_bracket_analysis.py

Step 4 of plans/goal_16_scrape_league_history.md.

Back-calculates, for each historical season in data/league_matchup_history.csv,
which "beanpot" tier (top/middle/bottom, based on weeks 1-11 regular-season
standings) each team landed in, who won/lost each beanpot bracket (weeks
12-13), and which playoff bracket (winners vs. losers) each team ended up in
during the final 3 playoff weeks -- purely from the observed schedule/scores,
without hard-coding the exact ESPN seeding rules described in the plan.

Approach
--------
Week boundaries are hard-coded rather than derived from ESPN's own
`is_playoff`/`matchupPeriodCount` settings, since the league's actual rules
always use an 11+2 split regardless of what ESPN reports (2021-2023 report
14 non-playoff weeks instead of 13, likely because the final playoff round
spans two weeks with a combined score):

  - `regular_weeks` = weeks 1-11, always.
  - `beanpot_weeks` = weeks 12-13, always.
  - `playoff_weeks` = week 14 through the last observed week of the season
    (normally 3 weeks, but 4 in seasons with a two-week combined-score
    final round).

Regular-season standings (wins, then points_for as tiebreak) split the field
into three tiers of four: top / middle / bottom beanpot.

Beanpot results are computed as an actual two-round, 4-team single-elim
bracket per tier: week 12 is round 1 (the two within-tier games), and week
13 is round 2, where the two round-1 winners play each other for the tier
title and the two round-1 losers play each other to avoid the tier's last
place. Any beanpot game that crosses tiers, or a round-2 pairing that isn't
"winners play winners, losers play losers", is logged as a validation
warning (this would indicate the tier-assignment heuristic doesn't match
the true ESPN bracket for that season).

Playoff bracket membership (winners vs. losers bracket) is inferred by
building a graph of "played each other during a playoff week" and taking
connected components -- a real single-elimination bracket never lets a
winners-bracket team play a losers-bracket team, so this should always
yield exactly two components. The component containing more top-beanpot
teams is labeled the winners bracket; the other, the losers bracket. Bye
weeks (a playoff-week row with no away team) are recorded per team.

Output: data/beanpot_playoff_brackets.csv, one row per (season, team).

Usage:
    python analysis/beanpot_playoff_bracket_analysis.py
"""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MATCHUP_CSV = REPO_ROOT / "data" / "league_matchup_history.csv"
OUTPUT_CSV = REPO_ROOT / "data" / "beanpot_playoff_brackets.csv"

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def load_matchups() -> dict[int, list[dict]]:
    """season -> list of matchup row dicts (week as int, is_playoff as bool|None)."""
    by_season: dict[int, list[dict]] = defaultdict(list)
    with open(MATCHUP_CSV, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            season = int(row["season"])
            row["week"] = int(row["week"])
            row["is_playoff"] = {"True": True, "False": False}.get(row["is_playoff"])
            row["home_team_id"] = int(row["home_team_id"])
            row["away_team_id"] = int(row["away_team_id"]) if row["away_team_id"] else None
            row["home_score"] = float(row["home_score"]) if row["home_score"] else None
            row["away_score"] = float(row["away_score"]) if row["away_score"] else None
            by_season[season].append(row)
    return by_season


REGULAR_WEEKS = list(range(1, 12))  # weeks 1-11, always
BEANPOT_WEEKS = [12, 13]  # always weeks 12-13
PLAYOFF_START_WEEK = 14  # playoffs always begin week 14


def derive_week_windows(rows: list[dict]) -> dict | None:
    """Given one season's matchup rows, derive regular/beanpot/playoff week ranges.

    Regular season is always weeks 1-11 and beanpot is always weeks 12-13,
    regardless of what the ESPN `is_playoff`/matchupPeriodCount settings say
    for that season (some seasons, e.g. 2021-2023, mis-set that to 14,
    presumably because the last playoff round spanned two weeks with a
    combined score rather than the schedule actually having a longer regular
    season). Playoffs are whatever weeks remain (normally 3, but 4 in
    seasons where the final round is a two-week combined-score round).
    """
    max_week = max((r["week"] for r in rows), default=0)
    if max_week < BEANPOT_WEEKS[-1]:
        return None  # season in progress, hasn't reached beanpot yet
    playoff_weeks = list(range(PLAYOFF_START_WEEK, max_week + 1))
    return {
        "regular_weeks": REGULAR_WEEKS,
        "beanpot_weeks": BEANPOT_WEEKS,
        "playoff_weeks": playoff_weeks,
    }


def compute_regular_standings(rows: list[dict], regular_weeks: list[int]) -> dict[int, dict]:
    """team_id -> {wins, losses, ties, points_for, name, owner}"""
    stats: dict[int, dict] = defaultdict(lambda: {
        "wins": 0, "losses": 0, "ties": 0, "points_for": 0.0, "name": "", "owner": "",
    })
    for r in rows:
        if r["week"] not in regular_weeks:
            continue
        if r["away_team_id"] is None:
            continue  # bye (shouldn't occur in regular season, but be safe)
        h, a = r["home_team_id"], r["away_team_id"]
        hs, as_ = r["home_score"], r["away_score"]
        stats[h]["name"], stats[h]["owner"] = r["home_team_name"], r["home_owner_name"]
        stats[a]["name"], stats[a]["owner"] = r["away_team_name"], r["away_owner_name"]
        stats[h]["points_for"] += hs
        stats[a]["points_for"] += as_
        if hs > as_:
            stats[h]["wins"] += 1
            stats[a]["losses"] += 1
        elif as_ > hs:
            stats[a]["wins"] += 1
            stats[h]["losses"] += 1
        else:
            stats[h]["ties"] += 1
            stats[a]["ties"] += 1
    return dict(stats)


def assign_beanpot_tiers(standings: dict[int, dict]) -> dict[int, str]:
    """Rank teams by (wins desc, points_for desc); split into thirds."""
    ordered = sorted(
        standings.items(),
        key=lambda kv: (-kv[1]["wins"], -kv[1]["points_for"]),
    )
    n = len(ordered)
    third = n // 3
    tiers: dict[int, str] = {}
    for i, (team_id, _stats) in enumerate(ordered):
        if i < third:
            tiers[team_id] = "top_beanpot"
        elif i < 2 * third:
            tiers[team_id] = "middle_beanpot"
        else:
            tiers[team_id] = "bottom_beanpot"
    return tiers


def compute_beanpot_results(
    rows: list[dict], beanpot_weeks: list[int], tiers: dict[int, str], season: int
) -> dict[int, str]:
    """team_id -> 'winner' | 'runner_up' | 'third' | 'last_place' within its tier bracket.

    The beanpot is a two-round, 4-team single-elimination bracket per tier:
    round 1 (the first of `beanpot_weeks`, i.e. week 12) is the two
    within-tier games; round 2 (the second week, i.e. week 13) has the two
    round-1 winners play each other for the tier title ('winner' vs.
    'runner_up'), and the two round-1 losers play each other to decide who
    avoids the tier's cellar ('third' vs. 'last_place').
    """
    if len(beanpot_weeks) != 2:
        raise ValueError(f"expected exactly 2 beanpot weeks, got {beanpot_weeks}")
    round1_week, round2_week = beanpot_weeks

    def games_in_week(week: int) -> list[tuple[int, int, float, float]]:
        games = []
        for r in rows:
            if r["week"] != week or r["away_team_id"] is None:
                continue
            h, a = r["home_team_id"], r["away_team_id"]
            if tiers.get(h) != tiers.get(a):
                log.warning(
                    f"[{season}] beanpot week {week}: cross-tier matchup "
                    f"{r['home_team_name']} ({tiers.get(h)}) vs {r['away_team_name']} "
                    f"({tiers.get(a)}) -- tier heuristic may not match true bracket."
                )
            games.append((h, a, r["home_score"], r["away_score"]))
        return games

    def winner_loser(h, a, hs, as_):
        return (h, a) if hs >= as_ else (a, h)

    round1_games = games_in_week(round1_week)
    round1_winner_of: dict[int, int] = {}  # team_id -> round-1 winner team_id
    round1_loser_of: dict[int, int] = {}  # team_id -> round-1 loser team_id
    for h, a, hs, as_ in round1_games:
        w, l = winner_loser(h, a, hs, as_)
        round1_winner_of[h] = w
        round1_winner_of[a] = w
        round1_loser_of[h] = l
        round1_loser_of[a] = l

    round1_winners = set(round1_winner_of.values())
    round1_losers = set(round1_loser_of.values())

    round2_games = games_in_week(round2_week)
    results: dict[int, str] = {}
    for h, a, hs, as_ in round2_games:
        w, l = winner_loser(h, a, hs, as_)
        both_winners = h in round1_winners and a in round1_winners
        both_losers = h in round1_losers and a in round1_losers
        if not (both_winners or both_losers):
            log.warning(
                f"[{season}] beanpot round 2 (week {round2_week}) pairing "
                f"{h} vs {a} doesn't match winners-vs-winners / "
                "losers-vs-losers bracket structure -- tier heuristic may "
                "not match true bracket."
            )
        if both_winners:
            results[w] = "winner"
            results[l] = "runner_up"
        else:
            results[w] = "third"
            results[l] = "last_place"

    return results


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


def compute_playoff_brackets(
    rows: list[dict], playoff_weeks: list[int], tiers: dict[int, str], season: int
) -> tuple[dict[int, str], dict[int, list[int]]]:
    """team_id -> 'winners'|'losers'|'unknown', and team_id -> list of bye weeks."""
    all_teams = set(tiers.keys())
    uf = UnionFind(all_teams)
    byes: dict[int, list[int]] = defaultdict(list)

    if not playoff_weeks:
        return {t: "" for t in all_teams}, {}

    for r in rows:
        if r["week"] not in playoff_weeks:
            continue
        h = r["home_team_id"]
        a = r["away_team_id"]
        if a is None:
            byes[h].append(r["week"])
            continue
        uf.union(h, a)

    components: dict[int, list[int]] = defaultdict(list)
    for team_id in all_teams:
        components[uf.find(team_id)].append(team_id)

    if len(components) != 2:
        log.warning(
            f"[{season}] expected 2 playoff bracket components (winners/losers), "
            f"found {len(components)}: {list(components.values())}"
        )

    # Winners bracket = component with the most top_beanpot members.
    def top_count(team_ids):
        return sum(1 for t in team_ids if tiers.get(t) == "top_beanpot")

    ranked_components = sorted(components.values(), key=top_count, reverse=True)
    bracket_map: dict[int, str] = {}
    labels = ["winners", "losers"] + [f"unknown_group_{i}" for i in range(len(ranked_components) - 2)]
    for label, team_ids in zip(labels, ranked_components):
        for t in team_ids:
            bracket_map[t] = label
    return bracket_map, dict(byes)


def compute_playoff_pod_standings(
    rows: list[dict],
    playoff_weeks: list[int],
    team_ids: set[int],
    reg_season_rank: dict[int, int],
) -> list[int]:
    """Rank a set of teams (one playoff bracket/"pod") by their head-to-head
    results across all playoff weeks, from best to worst.

    The playoff schedule (both the winners and losers bracket) turns out to
    behave like a fixed round-robin-style pod schedule across the playoff
    weeks rather than a strict "winners advance" single-elimination bracket
    (round-2+ pairings do not consistently pit prior-round winners against
    each other -- see plan notes). So final standing within a pod is derived
    from cumulative (wins, points) across all playoff weeks, exactly like the
    regular-season standings computation, with each team's regular-season
    rank as the final tiebreaker (worse regular-season rank sorts worse) for
    cases where two teams end a pod dead-tied on wins and points.
    """
    wins: dict[int, int] = {t: 0 for t in team_ids}
    points: dict[int, float] = {t: 0.0 for t in team_ids}
    for r in rows:
        if r["week"] not in playoff_weeks or r["away_team_id"] is None:
            continue
        h, a = r["home_team_id"], r["away_team_id"]
        if h not in team_ids or a not in team_ids:
            continue
        hs, as_ = r["home_score"], r["away_score"]
        points[h] += hs
        points[a] += as_
        if hs > as_:
            wins[h] += 1
        elif as_ > hs:
            wins[a] += 1
    ordered = sorted(
        team_ids,
        key=lambda t: (-wins[t], reg_season_rank.get(t, 999), -points[t]),
    )
    return ordered


def main() -> None:
    by_season = load_matchups()
    out_rows: list[dict] = []

    for season in sorted(by_season):
        rows = by_season[season]
        windows = derive_week_windows(rows)
        if windows is None:
            log.info(f"[{season}] Beanpot weeks (12-13) not yet reached; skipping.")
            continue

        standings = compute_regular_standings(rows, windows["regular_weeks"])
        if len(standings) < 3:
            log.warning(f"[{season}] Too few teams ({len(standings)}); skipping.")
            continue

        tiers = assign_beanpot_tiers(standings)
        beanpot_results = compute_beanpot_results(rows, windows["beanpot_weeks"], tiers, season)
        bracket_map, byes = compute_playoff_brackets(rows, windows["playoff_weeks"], tiers, season)

        ranked = sorted(standings.items(), key=lambda kv: (-kv[1]["wins"], -kv[1]["points_for"]))
        rank_lookup = {team_id: i + 1 for i, (team_id, _s) in enumerate(ranked)}

        # Final placement within each playoff bracket ("pod"): champion is
        # the top of the winners-bracket pod standings; true last place is
        # the bottom of the losers-bracket pod standings.
        playoff_finish: dict[int, str] = {}
        if windows["playoff_weeks"]:
            for label in ("winners", "losers"):
                pod_teams = {t for t, b in bracket_map.items() if b == label}
                if not pod_teams:
                    continue
                pod_order = compute_playoff_pod_standings(
                    rows, windows["playoff_weeks"], pod_teams, rank_lookup
                )
                n_pod = len(pod_order)
                for i, team_id in enumerate(pod_order):
                    if i == 0:
                        playoff_finish[team_id] = "champion" if label == "winners" else "losers_bracket_winner"
                    elif i == n_pod - 1:
                        playoff_finish[team_id] = "last_place" if label == "losers" else "winners_bracket_last"
                    else:
                        playoff_finish[team_id] = f"{label}_pod_{i + 1}"

        for team_id, stats in standings.items():
            out_rows.append({
                "season": season,
                "team_id": team_id,
                "team_name": stats["name"],
                "owner_name": stats["owner"],
                "reg_wins": stats["wins"],
                "reg_losses": stats["losses"],
                "reg_ties": stats["ties"],
                "reg_points_for": round(stats["points_for"], 2),
                "reg_season_rank": rank_lookup[team_id],
                "beanpot_tier": tiers.get(team_id, ""),
                "beanpot_result": beanpot_results.get(team_id, ""),
                "playoff_bracket": bracket_map.get(team_id, "unknown"),
                "playoff_bye_weeks": ";".join(str(w) for w in byes.get(team_id, [])),
                "playoff_finish": playoff_finish.get(team_id, ""),
            })

    out_rows.sort(key=lambda r: (r["season"], r["reg_season_rank"]))

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(out_rows[0].keys()))
        writer.writeheader()
        writer.writerows(out_rows)

    log.info(f"Wrote {len(out_rows)} rows to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
