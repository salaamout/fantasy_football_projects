"""
Goal 5 Part 2: Lineup Value Optimizer
Uses Ringer 2026 auction values as costs and historical average PAR-per-slot
as expected value to find the best starting lineup within a budget.

Pass --waiver (or -w) to use outside-of-bench / waiver-wire replacement level
instead of the default starter replacement level.

  Starter replacement ranks  : QB13, RB32, WR42, TE13  | flex rank 85
  Waiver  replacement ranks  : QB19, RB56, WR66, TE19  | flex rank 139
    (waiver = first player NOT rostered on any of the 12 teams' bench)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
import pandas as pd
import pulp

# Allow imports from the analysis directory when run directly
sys.path.insert(0, str(Path(__file__).parent))
from load_fantasy_data import load_and_clean_data
from calculate_par import calculate_par, REPLACEMENT_RANKS

RANKINGS_PATH = Path("data/ringer_2026_rankings.csv")
BUDGET        = 200
LINEUP_SLOTS  = {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "FLEX": 1}
FLEX_POSITIONS = {"RB", "WR", "TE"}

# Starter replacement level
# 12*(2 RB + 3 WR + 1 TE) = 72 starters + 12 FLEX = 84 total → rank 85
FLEX_REPLACEMENT_RANK = 85

# Outside-of-bench (waiver-wire) replacement level
# Starters + bench across 12 teams + 1:
#   QB:  12 + 6  + 1 = 19   RB: 31 + 24 + 1 = 56
#   WR:  41 + 24 + 1 = 66   TE: 12 + 6  + 1 = 19
# Combined flex-eligible (RB55 + WR65 + TE18) = 138 → rank 139
REPLACEMENT_RANKS_WAIVER = {"QB": 19, "RB": 56, "WR": 66, "TE": 19, "DST": 13}
FLEX_REPLACEMENT_RANK_WAIVER = 139


def build_avg_par_lookup(
    seasons=(2020, 2021, 2022, 2023, 2024),
    replacement_ranks: dict | None = None,
    flex_replacement_rank: int = FLEX_REPLACEMENT_RANK,
) -> tuple[dict, dict]:
    """
    Returns two dicts, both keyed by (position, positional_rank):
      - pos_lookup:  avg PAR above positional replacement
      - flex_lookup: avg PAR above combined RB/WR/TE flex replacement

    Pass replacement_ranks=REPLACEMENT_RANKS_WAIVER and
    flex_replacement_rank=FLEX_REPLACEMENT_RANK_WAIVER to use outside-of-bench
    (waiver-wire) replacement level instead of the default starter level.
    """
    if replacement_ranks is None:
        replacement_ranks = REPLACEMENT_RANKS

    df = load_and_clean_data()
    df = df[df["season"].isin(seasons)]
    df = calculate_par(df, replacement_ranks=replacement_ranks)

    # Positional rank within each (position, season) — used as lookup key
    df["pos_rank"] = (
        df.groupby(["position", "season"])["half_ppr_points"]
          .rank(method="first", ascending=False)
          .astype(int)
    )

    pos_lookup = (
        df.groupby(["position", "pos_rank"])["par"]
          .mean()
          .to_dict()
    )

    # --- Flex PAR: pool all RB/WR/TE, find combined replacement baseline ---
    flex_df = df[df["position"].isin(FLEX_POSITIONS)].copy()

    # Combined flex rank within each season (across all RB/WR/TE)
    flex_df["flex_rank"] = (
        flex_df.groupby("season")["half_ppr_points"]
               .rank(method="first", ascending=False)
               .astype(int)
    )

    # Flex replacement baseline per season
    flex_baselines = {}
    for season, grp in flex_df.groupby("season"):
        rep = grp[grp["flex_rank"] == flex_replacement_rank]
        if rep.empty:
            rep = grp.nsmallest(1, "flex_rank")  # deepest available
        flex_baselines[season] = rep["half_ppr_points"].mean()

    flex_df["flex_par"] = (
        flex_df["half_ppr_points"] - flex_df["season"].map(flex_baselines)
    )

    # Average flex_par keyed by (position, positional_rank) — same key as pos_lookup
    flex_lookup = (
        flex_df.groupby(["position", "pos_rank"])["flex_par"]
               .mean()
               .to_dict()
    )

    return pos_lookup, flex_lookup


def attach_expected_par(
    rankings: pd.DataFrame,
    pos_lookup: dict,
    flex_lookup: dict,
    rank_window: int = 0,
) -> pd.DataFrame:
    """
    Attach two PAR columns to rankings:
      expected_par      — value when filling a dedicated positional slot
      flex_expected_par — value when filling the FLEX slot (RB/WR/TE only)
    Both fall back to the minimum value for that position when rank exceeds the table.

    rank_window : if > 0, average PAR over [max(1, rank - window) .. rank + window]
                  to model uncertainty around a player's projected rank.
    """
    def _min_per_pos(lookup):
        mins = {}
        for (pos, _rank), val in lookup.items():
            mins[pos] = min(mins.get(pos, val), val)
        return mins

    min_pos  = _min_per_pos(pos_lookup)
    min_flex = _min_per_pos(flex_lookup)

    def _avg_lookup(lookup, pos, rank, fallback):
        if rank_window == 0:
            return lookup.get((pos, rank), fallback)
        lo = max(1, rank - rank_window)
        hi = rank + rank_window
        vals = [lookup[(pos, r)] for r in range(lo, hi + 1) if (pos, r) in lookup]
        return sum(vals) / len(vals) if vals else fallback

    rankings = rankings.copy()

    rankings["expected_par"] = rankings.apply(
        lambda r: _avg_lookup(
            pos_lookup, r["position"], r["positional_rank"],
            min_pos.get(r["position"], 0.0),
        ),
        axis=1,
    )

    rankings["flex_expected_par"] = rankings.apply(
        lambda r: _avg_lookup(
            flex_lookup, r["position"], r["positional_rank"],
            min_flex.get(r["position"], 0.0),
        ) if r["position"] in FLEX_POSITIONS else 0.0,
        axis=1,
    )

    return rankings


def optimize_lineup(rankings: pd.DataFrame) -> pd.DataFrame:
    """
    Two-variable MILP formulation:
      y[i] = 1 if player i fills a dedicated positional slot
      z[i] = 1 if player i fills the FLEX slot   (RB/WR/TE only)
      y[i] + z[i] <= 1  (can't fill two slots)

    Objective:
      max Σ pos_par[i]*y[i]  +  Σ flex_par[i]*z[i]
    """
    prob = pulp.LpProblem("lineup_optimizer", pulp.LpMaximize)

    players = rankings.to_dict("records")
    n = len(players)

    y = [pulp.LpVariable(f"y_{i}", cat="Binary") for i in range(n)]  # positional slot
    z = [pulp.LpVariable(f"z_{i}", cat="Binary") for i in range(n)]  # FLEX slot

    # FLEX only available to RB/WR/TE
    for i, p in enumerate(players):
        if p["position"] not in FLEX_POSITIONS:
            prob += z[i] == 0

    # Each player fills at most one slot
    for i in range(n):
        prob += y[i] + z[i] <= 1

    # Objective: positional PAR for positional slots, flex PAR for the FLEX slot
    prob += pulp.lpSum(
        p["expected_par"]      * y[i] +
        p["flex_expected_par"] * z[i]
        for i, p in enumerate(players)
    )

    # Budget
    prob += pulp.lpSum(
        p["auction_value"] * (y[i] + z[i])
        for i, p in enumerate(players)
    ) <= BUDGET

    # Positional slot counts
    prob += pulp.lpSum(y[i] for i, p in enumerate(players) if p["position"] == "QB") == LINEUP_SLOTS["QB"]
    for pos in ("RB", "WR", "TE"):
        prob += pulp.lpSum(
            y[i] for i, p in enumerate(players) if p["position"] == pos
        ) == LINEUP_SLOTS[pos]

    # Exactly one FLEX slot
    prob += pulp.lpSum(z) == LINEUP_SLOTS["FLEX"]

    status = prob.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError(f"No optimal solution found. Status: {pulp.LpStatus[status]}")

    rows = []
    for i, p in enumerate(players):
        yv = pulp.value(y[i])
        zv = pulp.value(z[i])
        if yv == 1:
            rows.append({**p, "slot": p["position"]})
        elif zv == 1:
            rows.append({**p, "slot": "FLEX"})

    result = pd.DataFrame(rows).sort_values(["position", "positional_rank"])
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fantasy football lineup optimizer.")
    parser.add_argument(
        "--waiver", "-w",
        action="store_true",
        help=(
            "Use outside-of-bench (waiver-wire) replacement level "
            "(QB19, RB56, WR66, TE19 / flex rank 139) "
            "instead of the default starter replacement level."
        ),
    )
    parser.add_argument(
        "--uncertainty", "-u",
        type=int,
        default=0,
        metavar="WINDOW",
        help=(
            "Model rank uncertainty by averaging PAR over "
            "[max(1, rank - WINDOW) .. rank + WINDOW] for each player. "
            "E.g. --uncertainty 5 averages ranks ±5 around the projected rank. "
            "Defaults to 0 (use exact rank)."
        ),
    )
    args = parser.parse_args()

    if args.waiver:
        repl_ranks      = REPLACEMENT_RANKS_WAIVER
        flex_repl_rank  = FLEX_REPLACEMENT_RANK_WAIVER
        mode_label      = "waiver-wire (outside-of-bench)"
    else:
        repl_ranks      = REPLACEMENT_RANKS
        flex_repl_rank  = FLEX_REPLACEMENT_RANK
        mode_label      = "starter"

    rank_window = args.uncertainty
    uncertainty_label = (
        f"±{rank_window} rank window" if rank_window > 0 else "exact rank"
    )

    print(f"Replacement level mode : {mode_label}")
    print(f"Rank uncertainty       : {uncertainty_label}")
    print("Building historical average PAR lookup tables…")
    pos_lookup, flex_lookup = build_avg_par_lookup(
        replacement_ranks=repl_ranks,
        flex_replacement_rank=flex_repl_rank,
    )

    print(f"\nLoading Ringer 2026 rankings from {RANKINGS_PATH}…")
    rankings = pd.read_csv(RANKINGS_PATH)
    # Keep only the four skill positions the optimizer handles
    rankings = rankings[rankings["position"].isin({"QB", "RB", "WR", "TE"})].reset_index(drop=True)
    print(f"  {len(rankings)} players loaded.")

    rankings = attach_expected_par(rankings, pos_lookup, flex_lookup, rank_window=rank_window)

    print("\nRunning lineup optimizer…")
    lineup = optimize_lineup(rankings)

    # Show the PAR that actually counts for each player's slot
    lineup["slot_par"] = lineup.apply(
        lambda r: r["flex_expected_par"] if r["slot"] == "FLEX" else r["expected_par"],
        axis=1,
    )

    total_cost = lineup["auction_value"].sum()
    total_par  = lineup["slot_par"].sum()

    print(f"\n=== Optimal Starting Lineup (Budget: ${BUDGET}) — {mode_label} PAR | {uncertainty_label} ===")
    print(
        lineup[["slot", "position", "player_name", "team", "positional_rank",
                 "auction_value", "slot_par"]]
        .rename(columns={"slot_par": "expected_par"})
        .to_string(index=False)
    )
    print(f"\nTotal auction cost : ${total_cost}")
    print(f"Total expected PAR : {total_par:.1f}")
    print(f"Remaining budget   : ${BUDGET - total_cost}")
