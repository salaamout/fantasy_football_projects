"""
Step 4 of Goal 3: Aggregate PAR by roster slot and convert to auction values.

Runs both variants automatically:
  Standard replacement ranks : QB13, RB32, WR42, TE13, DST13  (suffix: "")
  Waiver   replacement ranks : QB19, RB56, WR66, TE19, DST13  (suffix: "_waiver")
"""

from __future__ import annotations

import sys
from pathlib import Path
import pandas as pd

try:
    from .load_fantasy_data import load_and_clean_data
    from .calculate_par import calculate_par
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    from load_fantasy_data import load_and_clean_data
    from calculate_par import calculate_par

# --- League settings ---
NUM_TEAMS = 12
TOTAL_BUDGET_PER_TEAM = 200
BENCH_RESERVE_PER_TEAM = 10  # ~$2.50 × 4 bench spots
STARTER_DOLLARS_PER_TEAM = TOTAL_BUDGET_PER_TEAM - BENCH_RESERVE_PER_TEAM
TOTAL_STARTER_DOLLARS = STARTER_DOLLARS_PER_TEAM * NUM_TEAMS  # $2,280

# Starter slots included in the auction value conversion (replacement rank = first excluded rank)
STARTER_SLOTS = {
    "QB": range(1, 13),   # ranks 1–12
    "RB": range(1, 32),   # ranks 1–31
    "WR": range(1, 42),   # ranks 1–41
    "TE": range(1, 13),   # ranks 1–12
    "DST": range(1, 13),  # ranks 1–12
}

MIN_SEASONS = 2  # Trim tiers with fewer than this many seasons of data

# --- Waiver-wire replacement ranks ---
# Replacement rank = starters + (12 teams × bench per team) + 1
# Bench per team: QB 0.5, RB 2.0, WR 2.0, TE 0.5, DST 0.0
REPLACEMENT_RANKS_WAIVER = {"QB": 19, "RB": 56, "WR": 66, "TE": 19, "DST": 13}
# Auction value is still distributed across starter slots only
STARTER_SLOTS_WAIVER = {
    "QB":  range(1, 13),
    "RB":  range(1, 32),
    "WR":  range(1, 42),
    "TE":  range(1, 13),
    "DST": range(1, 13),
}


def summarize_par_by_tier(df: pd.DataFrame) -> pd.DataFrame:
    """
    Step A: Summarize PAR by (position, positional_rank) across all seasons.
    Returns a tier summary DataFrame with one row per (position, positional_rank).
    """
    groups = df.groupby(["position", "positional_rank"])["par"]

    tier_df = groups.agg(
        mean_par="mean",
        median_par="median",
        std_par="std",
        min_par="min",
        max_par="max",
        seasons_observed="count",
    ).reset_index()

    # Trim tiers with insufficient data
    thin_tiers = tier_df[tier_df["seasons_observed"] < MIN_SEASONS]
    if not thin_tiers.empty:
        for _, row in thin_tiers.iterrows():
            print(
                f"WARNING: Trimming {row['position']} rank {row['positional_rank']} "
                f"— only {row['seasons_observed']} season(s) of data."
            )
    tier_df = tier_df[tier_df["seasons_observed"] >= MIN_SEASONS].copy()

    # Roster slot label (e.g. "RB1", "WR3")
    tier_df["roster_slot"] = tier_df["position"] + tier_df["positional_rank"].astype(str)

    return tier_df


def convert_par_to_auction_values(tier_df: pd.DataFrame, starter_slots: dict | None = None) -> pd.DataFrame:
    """
    Step B: Convert mean PAR to auction dollar values.
    Distributes $2,280 (total starter dollars) proportional to positive PAR
    across all defined starter slots.

    If starter_slots is provided, it overrides the module-level STARTER_SLOTS.
    """
    if starter_slots is None:
        starter_slots = STARTER_SLOTS

    tier_df = tier_df.copy()

    # Flag rows that are starter slots per league settings
    def is_starter(row):
        return row["positional_rank"] in starter_slots.get(row["position"], range(0))

    tier_df["is_starter"] = tier_df.apply(is_starter, axis=1)

    starter_df = tier_df[tier_df["is_starter"]].copy()

    # Total positive PAR among starter slots
    total_positive_par = starter_df.loc[starter_df["mean_par"] > 0, "mean_par"].sum()

    if total_positive_par == 0:
        raise ValueError("Total positive PAR across starter slots is 0 — cannot convert to auction values.")

    # Compute auction value
    def auction_value(mean_par):
        if mean_par <= 0:
            return 1  # minimum bid
        return round((mean_par / total_positive_par) * TOTAL_STARTER_DOLLARS)

    tier_df["auction_value"] = tier_df.apply(
        lambda row: auction_value(row["mean_par"]) if row["is_starter"] else None,
        axis=1,
    )

    return tier_df


def build_cross_position_ranking(tier_df: pd.DataFrame) -> pd.DataFrame:
    """
    Step C: Sort all starter slots by auction_value descending for cross-position comparison.
    """
    cross_df = tier_df[tier_df["is_starter"]].copy()
    cross_df = cross_df.sort_values("auction_value", ascending=False).reset_index(drop=True)
    return cross_df


def validate_results(tier_df: pd.DataFrame, cross_df: pd.DataFrame, starter_slots: dict | None = None) -> None:
    """Run validation checks on the aggregated results."""
    if starter_slots is None:
        starter_slots = STARTER_SLOTS

    # Check total auction value sums to ~$2,280
    total_av = tier_df.loc[tier_df["is_starter"], "auction_value"].sum()
    print(f"\nValidation: Total auction value across starter slots = ${total_av:,} (target: ${TOTAL_STARTER_DOLLARS:,})")
    if abs(total_av - TOTAL_STARTER_DOLLARS) > 50:
        print(f"  WARNING: Total auction value deviates from target by ${abs(total_av - TOTAL_STARTER_DOLLARS)}.")

    # Check rank 1 at each position has the highest auction value within that position
    for position in starter_slots:
        pos_df = tier_df[(tier_df["position"] == position) & tier_df["is_starter"]]
        if pos_df.empty:
            continue
        top_rank = pos_df.loc[pos_df["auction_value"].idxmax(), "positional_rank"]
        if top_rank != 1:
            print(f"  WARNING: {position} — rank 1 does not have the highest auction value (top is rank {top_rank}).")
        else:
            print(f"  OK: {position}1 has the highest auction value within {position}.")

    # Print top 10 most valuable roster slots
    print("\nTop 10 most valuable roster slots across all positions:")
    top10 = cross_df.head(10)[
        ["roster_slot", "mean_par", "median_par", "std_par", "seasons_observed", "auction_value"]
    ].copy()
    top10["mean_par"] = top10["mean_par"].round(1)
    top10["median_par"] = top10["median_par"].round(1)
    top10["std_par"] = top10["std_par"].round(1)
    print(top10.to_string(index=False))


def aggregate_par(df: pd.DataFrame, starter_slots: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Main entry point for step 4.

    If starter_slots is provided, it overrides the module-level STARTER_SLOTS.

    Returns:
        tier_summary_df: one row per (position, positional_rank) with PAR stats and auction_value
        cross_position_ranking_df: all starter slots sorted by auction_value descending
    """
    print("=== Step A: Summarize PAR by positional tier ===")
    tier_df = summarize_par_by_tier(df)

    print(f"\nTier summary shape: {tier_df.shape} (position × rank combinations)")

    print("\n=== Step B: Convert PAR to auction dollar values ===")
    tier_df = convert_par_to_auction_values(tier_df, starter_slots=starter_slots)

    print("\n=== Step C: Build cross-position ranking ===")
    cross_df = build_cross_position_ranking(tier_df)

    validate_results(tier_df, cross_df, starter_slots=starter_slots)

    return tier_df, cross_df


if __name__ == "__main__":
    try:
        from .visualize_par import visualize_and_export
    except ImportError:
        from visualize_par import visualize_and_export

    # Load data once; run both standard and waiver variants.
    df_raw = load_and_clean_data(verbose=True)

    _VARIANTS = [
        {
            "label": "Standard (starter-only replacement level)",
            "replacement_ranks": None,   # use module-level REPLACEMENT_RANKS in calculate_par
            "starter_slots": None,        # use module-level STARTER_SLOTS in aggregate_par
            "suffix": "",
        },
        {
            "label": "Waiver-wire replacement level",
            "replacement_ranks": REPLACEMENT_RANKS_WAIVER,
            "starter_slots": STARTER_SLOTS_WAIVER,
            "suffix": "_waiver",
        },
    ]

    for variant in _VARIANTS:
        print(f"\n{'='*60}")
        print(f"=== {variant['label']} ===")
        print(f"{'='*60}")

        df = calculate_par(df_raw, replacement_ranks=variant["replacement_ranks"], verbose=True)
        tier_summary_df, cross_position_ranking_df = aggregate_par(df, starter_slots=variant["starter_slots"])

        print("\n=== Tier Summary (starter slots only, sorted by position + rank) ===")
        starter_tiers = (
            tier_summary_df[tier_summary_df["is_starter"]]
            .sort_values(["position", "positional_rank"])
        )
        print(
            starter_tiers[
                ["roster_slot", "mean_par", "median_par", "std_par", "min_par", "max_par", "seasons_observed", "auction_value"]
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

        visualize_and_export(
            tier_summary_df,
            cross_position_ranking_df,
            fantasy_df=df,
            replacement_ranks=variant["replacement_ranks"],
            suffix=variant["suffix"],
        )

        suffix_label = repr(variant["suffix"]) if variant["suffix"] else "no suffix"
        print(f"\nDone. Outputs saved ({suffix_label}).")

    print("\nAll variants complete.")
