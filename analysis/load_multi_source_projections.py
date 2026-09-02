"""
Goal 10, Step 3: Build a combined (blended) point projection from three
sources — ESPN, Sleeper, and historical (5-yr avg half-PPR) — and write it
out to `data/blended_projected_values.csv` using the same column conventions
(`player_name`, `position`, `positional_rank`, `projected_points`) as the
existing per-source CSVs so downstream code needs minimal changes.

Blending approach
------------------
1. Join ESPN + Sleeper on the shared `player_key` (via
   `analysis.player_id_matching.build_player_key_table`), which already
   handles name normalization / aliasing / unmatched reporting.
2. Compute a first-pass blended point value per player as a weighted
   average of whatever of {espn, sleeper} are present, with weights
   renormalized over the available sources (a player missing one source is
   not dropped or penalized — see `_weighted_average`).
3. Rank players within each position by that first-pass value to get a
   provisional `positional_rank`, then look up the historical positional
   rank curve (`analysis.player_id_matching.load_historical_position_rank_curve`)
   at that rank to get a historical point estimate for that player.
4. Compute the FINAL blended point value as a weighted average across all
   three sources (espn, sleeper, historical), again renormalizing weights
   over whichever sources are actually available for that player/rank.
5. Re-rank by the final blended value to get the final `positional_rank`.

Weights are configurable via `DEFAULT_WEIGHTS` or the `weights` argument /
`--espn-weight` / `--sleeper-weight` / `--historical-weight` CLI flags.

Usage
    python -m analysis.load_multi_source_projections
    python -m analysis.load_multi_source_projections --espn-weight 0.5 --sleeper-weight 0.3 --historical-weight 0.2
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).parent
_ROOT = _HERE.parent
DATA_DIR = _ROOT / "data"

try:
    from .player_id_matching import (
        build_player_key_table,
        load_alias_map,
        load_historical_position_rank_curve,
    )
except ImportError:
    sys.path.insert(0, str(_HERE))
    from player_id_matching import (
        build_player_key_table,
        load_alias_map,
        load_historical_position_rank_curve,
    )

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

BLENDED_CSV_PATH = DATA_DIR / "blended_projected_values.csv"

# Default source weights. Must be positive; they are renormalized per-row
# over whichever sources actually have data for that player.
DEFAULT_WEIGHTS: dict[str, float] = {
    "espn": 0.45,
    "sleeper": 0.35,
    "historical": 0.20,
}

VALID_POSITIONS = {"QB", "RB", "WR", "TE"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _weighted_average(values: dict[str, float | None], weights: dict[str, float]) -> tuple[float | None, int]:
    """
    Compute a weighted average over whatever values are not None, with
    weights renormalized over the available sources.

    Returns (weighted_value_or_None, num_sources_used).
    """
    available = {src: v for src, v in values.items() if v is not None and pd.notna(v)}
    if not available:
        return None, 0

    total_weight = sum(weights[src] for src in available)
    if total_weight <= 0:
        # Fall back to a plain average if weights are degenerate.
        return sum(available.values()) / len(available), len(available)

    blended = sum(v * (weights[src] / total_weight) for src, v in available.items())
    return blended, len(available)


# ---------------------------------------------------------------------------
# Main blending pipeline
# ---------------------------------------------------------------------------

def build_blended_points_table(
    weights: dict[str, float] | None = None,
    verbose: bool = False,
) -> pd.DataFrame:
    """
    Build the blended projection table across ESPN, Sleeper, and historical.

    Returns a DataFrame with columns:
        player_name, position, positional_rank, projected_points,
        espn_projected_points, sleeper_projected_points, historical_avg_points,
        sources_used
    """
    if weights is None:
        weights = DEFAULT_WEIGHTS

    alias_map = load_alias_map()
    merged = build_player_key_table(alias_map)
    merged = merged[merged["position"].isin(VALID_POSITIONS)].copy()

    # --- Pass 1: blend ESPN + Sleeper only, to get a provisional rank ---
    pass1_weights = {"espn": weights["espn"], "sleeper": weights["sleeper"]}
    prelim_values = []
    for _, row in merged.iterrows():
        val, _ = _weighted_average(
            {"espn": row["espn_projected_points"], "sleeper": row["sleeper_projected_points"]},
            pass1_weights,
        )
        prelim_values.append(val)
    merged["_prelim_points"] = prelim_values

    # Players with no ESPN/Sleeper data at all can't be ranked or blended —
    # drop them (they contribute nothing to a projected-points table).
    merged = merged.dropna(subset=["_prelim_points"]).copy()

    merged["_prelim_rank"] = (
        merged.groupby("position")["_prelim_points"]
        .rank(ascending=False, method="first")
        .astype(int)
    )

    # --- Attach historical avg points at the provisional rank ---
    historical_curve = load_historical_position_rank_curve()
    hist_lookup = {
        (row["position"], int(row["positional_rank"])): row["historical_avg_points"]
        for _, row in historical_curve.iterrows()
    }
    merged["historical_avg_points"] = [
        hist_lookup.get((pos, rank)) for pos, rank in zip(merged["position"], merged["_prelim_rank"])
    ]

    # --- Pass 2: final blend across all 3 sources ---
    final_values = []
    sources_used = []
    for _, row in merged.iterrows():
        val, n_used = _weighted_average(
            {
                "espn": row["espn_projected_points"],
                "sleeper": row["sleeper_projected_points"],
                "historical": row["historical_avg_points"],
            },
            weights,
        )
        final_values.append(val)
        sources_used.append(n_used)
    merged["projected_points"] = final_values
    merged["sources_used"] = sources_used

    # --- Final rank, based on the fully-blended value ---
    merged["positional_rank"] = (
        merged.groupby("position")["projected_points"]
        .rank(ascending=False, method="first")
        .astype(int)
    )

    out = merged[
        [
            "player_name",
            "position",
            "positional_rank",
            "projected_points",
            "espn_projected_points",
            "sleeper_projected_points",
            "historical_avg_points",
            "sources_used",
        ]
    ].sort_values(["position", "positional_rank"]).reset_index(drop=True)

    if verbose:
        print(f"Total blended rows: {len(out)}")
        print("\nRow counts per position:")
        print(out.groupby("position")["player_name"].count().to_string())
        print("\nTop 20 by position:")
        for pos in sorted(out["position"].unique()):
            print(f"\n-- {pos} --")
            print(
                out[out["position"] == pos]
                .head(20)[["positional_rank", "player_name", "projected_points", "sources_used"]]
                .to_string(index=False)
            )

    return out


def save_blended_points_table(
    df: pd.DataFrame, output_path: Path = BLENDED_CSV_PATH
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    log.info(f"Wrote {len(df)} rows → {output_path}")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a blended ESPN + Sleeper + historical point projection."
    )
    parser.add_argument("--espn-weight", type=float, default=DEFAULT_WEIGHTS["espn"])
    parser.add_argument("--sleeper-weight", type=float, default=DEFAULT_WEIGHTS["sleeper"])
    parser.add_argument("--historical-weight", type=float, default=DEFAULT_WEIGHTS["historical"])
    args = parser.parse_args()

    weights = {
        "espn": args.espn_weight,
        "sleeper": args.sleeper_weight,
        "historical": args.historical_weight,
    }

    df = build_blended_points_table(weights=weights, verbose=True)
    save_blended_points_table(df)


if __name__ == "__main__":
    main()
