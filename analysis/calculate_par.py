"""
Step 3 of Goal 3: Calculate Points Above Replacement (PAR) per player.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
import pandas as pd

try:
    from .load_fantasy_data import load_and_clean_data
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    from load_fantasy_data import load_and_clean_data

# Replacement-level positional rank thresholds (1-indexed)
REPLACEMENT_RANKS = {
    "QB": 13,
    "RB": 32,
    "WR": 42,
    "TE": 13,
    "DST": 13,
}


def get_replacement_baselines(df: pd.DataFrame, replacement_ranks: dict | None = None) -> dict:
    """
    For each (position, season), look up the points of the replacement-level player.
    Falls back to the last-ranked player if the replacement rank exceeds the pool size.

    Returns a dict keyed by (position, season) -> replacement_points (float).
    """
    if replacement_ranks is None:
        replacement_ranks = REPLACEMENT_RANKS

    baselines = {}

    for (position, season), group in df.groupby(["position", "season"]):
        rep_rank = replacement_ranks[position]
        max_rank = group["positional_rank"].max()

        if rep_rank > max_rank:
            print(
                f"WARNING: {position} {season} — replacement rank {rep_rank} exceeds "
                f"pool size {max_rank}. Falling back to rank {max_rank}."
            )
            rep_rank = max_rank

        matching = group[group["positional_rank"] == rep_rank]

        if matching.empty:
            raise ValueError(
                f"Could not find replacement-level player for {position} {season} "
                f"at rank {rep_rank}. Check the data."
            )

        # If multiple players share the same rank (tied), take the mean of their points
        replacement_points = matching["half_ppr_points"].mean()
        baselines[(position, season)] = replacement_points

    return baselines


def calculate_par(df: pd.DataFrame, replacement_ranks: dict | None = None, verbose: bool = False) -> pd.DataFrame:
    """
    Add a 'par' column to the DataFrame representing Points Above Replacement.
    PAR = half_ppr_points - replacement_points for the player's (position, season).

    If replacement_ranks is provided, it overrides the module-level REPLACEMENT_RANKS.
    """
    if replacement_ranks is None:
        replacement_ranks = REPLACEMENT_RANKS

    baselines = get_replacement_baselines(df, replacement_ranks)

    # Print replacement-level baselines as a sanity check
    if verbose:
        print("\nReplacement-level points per position per season:")
        baseline_rows = [
            {"position": pos, "season": season, "replacement_points": pts}
            for (pos, season), pts in sorted(baselines.items())
        ]
        baseline_df = pd.DataFrame(baseline_rows)
        print(
            baseline_df.pivot(index="position", columns="season", values="replacement_points")
            .round(2)
            .to_string()
        )

    # Map replacement points onto each player row
    df = df.copy()
    df["replacement_points"] = df.apply(
        lambda row: baselines[(row["position"], row["season"])], axis=1
    )

    df["par"] = df["half_ppr_points"] - df["replacement_points"]

    # --- Validation: replacement-level players should have PAR ≈ 0 ---
    tolerance = 1e-6
    for (position, season), rep_pts in baselines.items():
        rep_players = df[
            (df["position"] == position)
            & (df["season"] == season)
            & (df["positional_rank"] == replacement_ranks.get(position))
        ]
        if not rep_players.empty:
            for _, row in rep_players.iterrows():
                if abs(row["par"]) > tolerance:
                    print(
                        f"WARNING: Replacement-level player {row['player_display_name']} "
                        f"({position} {season}) has PAR={row['par']:.4f}, expected ≈ 0."
                    )

    # --- Validation: no NaN in par column ---
    nan_count = df["par"].isna().sum()
    if nan_count > 0:
        raise ValueError(f"Unexpected NaN values in 'par' column: {nan_count} rows affected.")

    print(f"\nPAR calculation complete. {len(df)} rows processed, no NaN values in 'par'.")

    return df


def build_avg_points_lookup(
    seasons=(2021, 2022, 2023, 2024, 2025),
) -> tuple[dict, dict]:
    """
    Returns two dicts keyed by (position, positional_rank):
      - avg_points[(position, positional_rank)] → mean raw half-PPR points across seasons
      - avg_flex_points[(position, positional_rank)] → mean raw points when ranked within
        the combined RB/WR/TE flex pool

    Unlike build_avg_par_lookup, no replacement baseline is subtracted — these are
    total projected points, not PAR.

    Seasons window: 2021–2025 (2020 excluded — 16-game season).
    """
    FLEX_POSITIONS_LOCAL = {"RB", "WR", "TE"}

    df = load_and_clean_data()
    df = df[df["season"].isin(seasons)].copy()

    # Positional rank within each (position, season)
    df["pos_rank"] = (
        df.groupby(["position", "season"])["half_ppr_points"]
          .rank(method="first", ascending=False)
          .astype(int)
    )

    avg_points = (
        df.groupby(["position", "pos_rank"])["half_ppr_points"]
          .mean()
          .to_dict()
    )

    # Flex: combined rank across all RB/WR/TE within each season
    flex_df = df[df["position"].isin(FLEX_POSITIONS_LOCAL)].copy()
    flex_df["flex_rank"] = (
        flex_df.groupby("season")["half_ppr_points"]
               .rank(method="first", ascending=False)
               .astype(int)
    )

    # Rebuild per-position positional rank (same as pos_rank above, already in flex_df)
    avg_flex_points = (
        flex_df.groupby(["position", "pos_rank"])["half_ppr_points"]
               .mean()
               .to_dict()
    )

    return avg_points, avg_flex_points


if __name__ == "__main__":
    df = load_and_clean_data(verbose=True)
    df = calculate_par(df, verbose=True)

    print("\nSample rows with PAR (top 5 per position for 2024):")
    sample = (
        df[df["season"] == 2024]
        .sort_values(["position", "positional_rank"])
        .groupby("position")
        .head(5)
    )
    print(
        sample[["player_display_name", "position", "season", "half_ppr_points", "par", "rank_label"]]
        .to_string(index=False)
    )
