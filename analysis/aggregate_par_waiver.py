"""
Waiver-wire replacement level analysis.

Replacement level = the best available player on the waiver wire, i.e. the
first player NOT rostered by any team.  Total rostered = starters + bench.

Starter counts (from the standard league settings in aggregate_par.py):
  QB:  12 starters  (1 per team × 12 teams)
  RB:  31 starters  (~2.58 per team × 12 teams)
  WR:  41 starters  (~3.42 per team × 12 teams)
  TE:  12 starters  (1 per team × 12 teams)
  DST: 12 starters  (1 per team × 12 teams)

Bench depth per team (user-provided):
  QB:  0.5  →  6 bench QBs  across 12 teams
  RB:  2.0  → 24 bench RBs  across 12 teams
  WR:  2.0  → 24 bench WRs  across 12 teams
  TE:  0.5  →  6 bench TEs  across 12 teams
  DST: 0.0  →  0 bench DSTs (not specified)

  (The IR slot accounts for the one "extra" bench player per team.)

Replacement ranks (starters + bench + 1):
  QB:  12 + 6  + 1 = 19  → replacement = QB19
  RB:  31 + 24 + 1 = 56  → replacement = RB56
  WR:  41 + 24 + 1 = 66  → replacement = WR66
  TE:  12 + 6  + 1 = 19  → replacement = TE19
  DST: 12 + 0  + 1 = 13  → replacement = DST13 (unchanged)

Auction values are still distributed only across starter slots (same as the
standard analysis) — bench players are above replacement but not drafted
as "starters" for dollar-allocation purposes.

Outputs are saved with a "_waiver" suffix so they don't overwrite the
standard plots.
"""

import sys
import os

# Allow imports from this directory when run as a script
sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd
from load_fantasy_data import load_and_clean_data
from calculate_par import calculate_par
from aggregate_par import aggregate_par
from visualize_par import visualize_and_export

NUM_TEAMS = 12

# --- Starters per position across the whole league (from aggregate_par.py defaults) ---
STARTERS_TOTAL = {
    "QB":  12,   # 1 per team
    "RB":  31,   # ~2.58 per team
    "WR":  41,   # ~3.42 per team
    "TE":  12,   # 1 per team
    "DST": 12,   # 1 per team
}

# --- Bench depth per team (user-provided) ---
BENCH_PER_TEAM = {
    "QB":  0.5,
    "RB":  2.0,
    "WR":  2.0,
    "TE":  0.5,
    "DST": 0.0,
}

# --- Replacement rank = starters + (NUM_TEAMS × bench per team) + 1 ---
WAIVER_REPLACEMENT_RANKS = {
    pos: STARTERS_TOTAL[pos] + int(NUM_TEAMS * BENCH_PER_TEAM[pos]) + 1
    for pos in STARTERS_TOTAL
}

# --- Starter slots for auction value are the same as the standard analysis ---
WAIVER_STARTER_SLOTS = {
    "QB":  range(1, 13),
    "RB":  range(1, 32),
    "WR":  range(1, 42),
    "TE":  range(1, 13),
    "DST": range(1, 13),
}

OUTPUT_SUFFIX = "_waiver"


if __name__ == "__main__":
    print("=== Waiver-Wire Replacement Level Analysis ===")
    print("\nReplacement-level ranks (starters + bench + 1):")
    for pos in sorted(WAIVER_REPLACEMENT_RANKS):
        starters = STARTERS_TOTAL[pos]
        bench    = int(NUM_TEAMS * BENCH_PER_TEAM[pos])
        rep_rank = WAIVER_REPLACEMENT_RANKS[pos]
        print(f"  {pos}: {starters} starters + {bench} bench = {starters + bench} rostered  →  replacement = {pos}{rep_rank}")

    print("\n--- Loading and cleaning data ---")
    df = load_and_clean_data()

    print("\n--- Calculating PAR with waiver-wire replacement levels ---")
    df = calculate_par(df, replacement_ranks=WAIVER_REPLACEMENT_RANKS)

    print("\n--- Aggregating PAR and computing auction values ---")
    tier_summary_df, cross_position_ranking_df = aggregate_par(
        df, starter_slots=WAIVER_STARTER_SLOTS
    )

    print("\n=== Tier Summary (starter slots only, sorted by position + rank) ===")
    starter_tiers = (
        tier_summary_df[tier_summary_df["is_starter"]]
        .sort_values(["position", "positional_rank"])
    )
    print(
        starter_tiers[
            [
                "roster_slot", "mean_par", "median_par", "std_par",
                "min_par", "max_par", "seasons_observed", "auction_value",
            ]
        ]
        .round(1)
        .to_string(index=False)
    )

    print("\n=== Cross-Position Ranking (all starter slots by auction value) ===")
    print(
        cross_position_ranking_df[
            ["roster_slot", "mean_par", "median_par", "std_par", "seasons_observed", "auction_value"]
        ]
        .round(1)
        .to_string(index=False)
    )

    print("\n--- Generating waiver-wire plots ---")
    visualize_and_export(
        tier_summary_df,
        cross_position_ranking_df,
        fantasy_df=df,
        replacement_ranks=WAIVER_REPLACEMENT_RANKS,
        suffix=OUTPUT_SUFFIX,
    )

    print("\nDone. Waiver-wire outputs saved with '_waiver' suffix.")
