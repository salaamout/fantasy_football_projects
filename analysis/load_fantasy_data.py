"""
Step 2 of Goal 3: Load, clean, and normalize fantasy half-PPR data.
"""

import pandas as pd

DATA_PATH = "data/fantasy_half_ppr.csv"
EXPECTED_COLUMNS = {"player_display_name", "position", "season", "half_ppr_points"}
EXPECTED_SEASONS = {2020, 2021, 2022, 2023, 2024}
VALID_POSITIONS = {"QB", "RB", "WR", "TE", "DST"}


def load_and_clean_data(path: str = DATA_PATH) -> pd.DataFrame:
    # --- Load ---
    df = pd.read_csv(path)

    # --- Verify columns ---
    missing_cols = EXPECTED_COLUMNS - set(df.columns)
    if missing_cols:
        raise ValueError(f"Missing expected columns: {missing_cols}")

    # --- Clean types ---
    df["season"] = df["season"].astype(int)
    df["half_ppr_points"] = df["half_ppr_points"].astype(float)
    df["position"] = df["position"].str.upper().str.strip()

    # --- Filter positions ---
    df = df[df["position"].isin(VALID_POSITIONS)]

    # --- Drop nulls ---
    df = df.dropna(subset=["player_display_name", "position", "season", "half_ppr_points"])

    # --- Remove duplicates ---
    df = df.drop_duplicates(subset=["player_display_name", "position", "season"])

    # --- Confirm expected seasons ---
    seasons_present = set(df["season"].unique())
    missing_seasons = EXPECTED_SEASONS - seasons_present
    if missing_seasons:
        raise ValueError(f"Missing expected seasons: {missing_seasons}")

    # --- Positional rank per position + season ---
    df["positional_rank"] = (
        df.groupby(["position", "season"])["half_ppr_points"]
        .rank(ascending=False, method="min")
        .astype(int)
    )

    # --- Rank label (e.g. "QB1", "RB12") ---
    df["rank_label"] = df["position"] + df["positional_rank"].astype(str)

    # --- Validation: no duplicate player/position/season ---
    dupes = df.duplicated(subset=["player_display_name", "position", "season"])
    if dupes.any():
        raise ValueError(f"Duplicate rows found after deduplication:\n{df[dupes]}")

    # --- Summary ---
    print(f"Total rows: {len(df)}")
    print(f"Seasons present: {sorted(df['season'].unique())}")
    print("\nPlayer count per position per season:")
    summary = (
        df.groupby(["position", "season"])["player_display_name"]
        .count()
        .unstack(level="season")
    )
    print(summary.to_string())

    return df


if __name__ == "__main__":
    df = load_and_clean_data()
    print("\nSample rows:")
    print(df.head(10).to_string(index=False))
