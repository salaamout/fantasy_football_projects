# pfr_fantasy.py
# Downloads season-level fantasy stats from the nflverse-data GitHub releases.
# Source: https://github.com/nflverse/nflverse-data
#
# NOTE: Pro Football Reference blocks all automated requests (403 at the CDN
# level), so we use nflverse-data instead. It publishes the same underlying
# nflfastR play-by-play derived stats — including standard and PPR fantasy
# points — as plain CSVs with no authentication required.
#
# Half-PPR = average of fantasy_points (standard) and fantasy_points_ppr.

import io
import logging
import os

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# Base URL for per-season CSVs
NFLVERSE_BASE = (
    "https://github.com/nflverse/nflverse-data/releases/download/player_stats"
)

# Skill positions to keep
SKILL_POSITIONS = {"QB", "RB", "WR", "TE"}

# Only regular season totals
SEASON_TYPE = "REG"

# Output directory (relative to this file)
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def fetch_season(year: int) -> pd.DataFrame:
    """Download and return the season-level stats CSV for *year*."""
    url = f"{NFLVERSE_BASE}/player_stats_season_{year}.csv"
    logger.info("Fetching %s", url)
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return pd.read_csv(io.StringIO(resp.text), low_memory=False)


def build_half_ppr(years: list[int]) -> pd.DataFrame:
    """
    Download season stats for each year in *years*, filter to skill positions
    and regular season, compute half-PPR points, and return a clean DataFrame
    with columns: player_display_name, position, season, half_ppr_points.
    """
    frames = []
    for year in years:
        df = fetch_season(year)

        # Keep only regular season rows (column may be 'season_type')
        if "season_type" in df.columns:
            df = df[df["season_type"] == SEASON_TYPE]

        # Keep only skill positions
        df = df[df["position"].isin(SKILL_POSITIONS)]

        # Compute half-PPR as the average of standard and full-PPR
        df = df.copy()
        df["half_ppr_points"] = (
            df["fantasy_points"].fillna(0) + df["fantasy_points_ppr"].fillna(0)
        ) / 2

        frames.append(
            df[["player_display_name", "position", "season", "half_ppr_points"]]
        )
        logger.info("  %d  →  %d players", year, len(frames[-1]))

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values(
        ["season", "half_ppr_points"], ascending=[True, False]
    ).reset_index(drop=True)
    return combined


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    seasons = list(range(2020, 2025))  # 2020 – 2024 (last 5 years)
    print(f"Fetching seasons: {seasons}")

    df = build_half_ppr(seasons)

    os.makedirs(DATA_DIR, exist_ok=True)
    out_path = os.path.join(DATA_DIR, "fantasy_half_ppr.csv")
    df.to_csv(out_path, index=False)

    print(f"\nSaved {len(df)} rows to {out_path}")
    print("\nTop 10 players (2024):")
    print(
        df[df["season"] == 2024]
        .head(10)
        .to_string(index=False)
    )

