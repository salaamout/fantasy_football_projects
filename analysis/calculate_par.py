"""
Step 3 of Goal 3: Calculate Points Above Replacement (PAR) per player.
"""

import math
import pandas as pd
from load_fantasy_data import load_and_clean_data

# Replacement-level positional rank thresholds (1-indexed)
REPLACEMENT_RANKS = {
    "QB": 13,
    "RB": 32,
    "WR": 42,
    "TE": 13,
    "DST": 13,
}


def get_replacement_baselines(df: pd.DataFrame) -> dict:
    """
    For each (position, season), look up the points of the replacement-level player.
    Falls back to the last-ranked player if the replacement rank exceeds the pool size.

    Returns a dict keyed by (position, season) -> replacement_points (float).
    """
    baselines = {}

    for (position, season), group in df.groupby(["position", "season"]):
        rep_rank = REPLACEMENT_RANKS[position]
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


def calculate_par(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add a 'par' column to the DataFrame representing Points Above Replacement.
    PAR = half_ppr_points - replacement_points for the player's (position, season).
    """
    baselines = get_replacement_baselines(df)

    # Print replacement-level baselines as a sanity check
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
            & (df["positional_rank"] == REPLACEMENT_RANKS.get(position))
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


if __name__ == "__main__":
    df = load_and_clean_data()
    df = calculate_par(df)

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
