"""
Step 5 of Goal 3: Visualize PAR curves and auction values, and export results.
"""

import os
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from typing import Optional

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "output")
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

# Position-specific starter slot cutoffs (last rostered rank per position)
REPLACEMENT_RANKS = {
    "QB": 12,
    "RB": 31,
    "WR": 41,
    "TE": 12,
    "DST": 12,
}

POSITION_COLORS = {
    "QB": "#0072B2",   # blue
    "RB": "#E69F00",   # orange
    "WR": "#009E73",   # green
    "TE": "#D55E00",   # red/vermillion
    "DST": "#CC79A7",  # purple/pink
}

POSITION_ORDER = ["QB", "RB", "WR", "TE", "DST"]


def _ensure_output_dir():
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def plot_par_curves(tier_summary_df: pd.DataFrame) -> None:
    """
    Chart 1 — Multi-line PAR curve by positional rank for all positions.
    Saves to output/par_curves_by_position.png.
    """
    _ensure_output_dir()

    fig, ax = plt.subplots(figsize=(12, 7))

    for position in POSITION_ORDER:
        max_rank = REPLACEMENT_RANKS.get(position, 12)
        pos_df = tier_summary_df[
            (tier_summary_df["position"] == position)
            & (tier_summary_df["positional_rank"] <= max_rank)
        ].sort_values("positional_rank")

        if pos_df.empty:
            print(f"WARNING: No data for position {position} in tier summary.")
            continue

        ax.plot(
            pos_df["positional_rank"],
            pos_df["mean_par"],
            color=POSITION_COLORS[position],
            marker="o",
            markersize=4,
            linewidth=2,
            label=position,
        )

    # Reference line at y=0 (replacement level)
    ax.axhline(y=0, color="black", linestyle="--", linewidth=1, alpha=0.7, label="Replacement Level (PAR=0)")

    ax.set_title("Mean PAR by Positional Rank (2020–2024, Half-PPR)", fontsize=14, fontweight="bold")
    ax.set_xlabel("Positional Rank", fontsize=12)
    ax.set_ylabel("Mean Points Above Replacement (PAR)", fontsize=12)
    ax.legend(fontsize=11)
    ax.grid(True, color="lightgray", alpha=0.3)

    plt.tight_layout()
    out_path = os.path.join(OUTPUT_DIR, "par_curves_by_position.png")
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out_path}")

    # Validation: confirm all 5 positions appear
    positions_in_legend = [position for position in POSITION_ORDER
                           if not tier_summary_df[
                               (tier_summary_df["position"] == position)
                               & (tier_summary_df["positional_rank"] <= REPLACEMENT_RANKS.get(position, 12))
                           ].empty]
    print(f"Chart 1 validation — Positions plotted: {positions_in_legend}")

    for pos in ["RB", "WR"]:
        max_r = REPLACEMENT_RANKS.get(pos, 0)
        for shorter_pos in ["QB", "TE"]:
            short_r = REPLACEMENT_RANKS.get(shorter_pos, 0)
            if max_r > short_r:
                print(f"  OK: {pos} line (rank 1–{max_r}) extends further right than {shorter_pos} (rank 1–{short_r}).")


def _build_player_lookup(fantasy_df: pd.DataFrame) -> dict:
    """
    Build a dict mapping (position, positional_rank) -> list of (season, player_name)
    sorted from most recent season to oldest, for all seasons in fantasy_df.
    """
    ranked = fantasy_df.copy()
    ranked = ranked.sort_values(["season", "position", "half_ppr_points"], ascending=[True, True, False])
    ranked["positional_rank"] = ranked.groupby(["season", "position"]).cumcount() + 1

    lookup: dict = {}
    for _, row in ranked.iterrows():
        key = (row["position"], int(row["positional_rank"]))
        lookup.setdefault(key, []).append((int(row["season"]), row["player_display_name"]))

    # Sort each entry most recent → oldest
    for key in lookup:
        lookup[key].sort(key=lambda x: x[0], reverse=True)

    return lookup


def plot_auction_value_top20(
    cross_position_ranking_df: pd.DataFrame,
    fantasy_df: Optional[pd.DataFrame] = None,
) -> None:
    """
    Chart 2 — Horizontal bar chart for the top 20 roster slots by auction value.
    If fantasy_df is provided, each bar is annotated with the players who held
    that positional rank across all seasons, from most recent to oldest, separated by " | ".
    Saves to output/auction_value_top20.png.
    """
    _ensure_output_dir()

    top20 = cross_position_ranking_df.head(20).copy()
    # Reverse so highest value appears at top
    top20 = top20.iloc[::-1].reset_index(drop=True)

    colors = [POSITION_COLORS.get(pos, "gray") for pos in top20["position"]]

    # Build player lookup for all seasons
    player_lookup = {}
    if fantasy_df is not None:
        player_lookup = _build_player_lookup(fantasy_df)

    fig, ax = plt.subplots(figsize=(14, 8))
    bars = ax.barh(top20["roster_slot"], top20["auction_value"], color=colors, edgecolor="white", height=0.7)

    # Value labels and player name annotations on bars
    for bar, (_, row) in zip(bars, top20.iterrows()):
        val = row["auction_value"]
        bar_width = bar.get_width()
        bar_mid_y = bar.get_y() + bar.get_height() / 2

        # Auction value label to the right of each bar
        ax.text(bar_width + 0.5, bar_mid_y,
                f"${int(val)}", va="center", ha="left", fontsize=9)

        # Player names inside bar: "Most Recent | 2nd Recent | ..." for all seasons
        if fantasy_df is not None:
            season_players = player_lookup.get((row["position"], int(row["positional_rank"])), [])
            if season_players:
                names_str = " | ".join(name for _, name in season_players)
                ax.text(
                    bar_width * 0.98, bar_mid_y,
                    names_str,
                    va="center", ha="right",
                    fontsize=7.5, color="white", fontweight="bold",
                    clip_on=True,
                )

    # Legend
    legend_patches = [
        mpatches.Patch(color=POSITION_COLORS[pos], label=pos)
        for pos in POSITION_ORDER if pos in top20["position"].values
    ]
    ax.legend(handles=legend_patches, fontsize=10, loc="lower right")

    ax.set_title(
        "Top 20 Roster Slots by Auction Value (Half-PPR, 12-Team)",
        fontsize=13, fontweight="bold",
    )
    ax.set_xlabel("Auction Value ($)", fontsize=11)
    ax.set_ylabel("Roster Slot", fontsize=11)
    ax.grid(True, axis="x", color="lightgray", alpha=0.3)
    ax.set_xlim(0, top20["auction_value"].max() * 1.15)

    plt.tight_layout()
    out_path = os.path.join(OUTPUT_DIR, "auction_value_top20.png")
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out_path}")


def plot_par_heatmap(tier_summary_df: pd.DataFrame) -> None:
    """
    Chart 3 (optional) — Heatmap of mean PAR by position and rank bins.
    Saves to output/par_heatmap.png.
    """
    _ensure_output_dir()

    bin_size = 5
    max_rank = max(REPLACEMENT_RANKS.values())
    bins = list(range(1, max_rank + 2, bin_size))
    bin_labels = [f"{b}–{b + bin_size - 1}" for b in bins[:-1]]

    heatmap_data = []
    for position in POSITION_ORDER:
        row_vals = []
        max_r = REPLACEMENT_RANKS.get(position, 12)
        pos_df = tier_summary_df[
            (tier_summary_df["position"] == position)
            & (tier_summary_df["positional_rank"] <= max_r)
        ]
        for b in bins[:-1]:
            bin_df = pos_df[
                (pos_df["positional_rank"] >= b) & (pos_df["positional_rank"] < b + bin_size)
            ]
            row_vals.append(bin_df["mean_par"].mean() if not bin_df.empty else np.nan)
        heatmap_data.append(row_vals)

    heatmap_array = np.array(heatmap_data, dtype=float)

    fig, ax = plt.subplots(figsize=(14, 4))
    im = ax.imshow(heatmap_array, aspect="auto", cmap="YlOrRd",
                   vmin=np.nanmin(heatmap_array), vmax=np.nanmax(heatmap_array))

    ax.set_xticks(range(len(bin_labels)))
    ax.set_xticklabels(bin_labels, rotation=45, ha="right", fontsize=8)
    ax.set_yticks(range(len(POSITION_ORDER)))
    ax.set_yticklabels(POSITION_ORDER, fontsize=11)

    plt.colorbar(im, ax=ax, label="Mean PAR")
    ax.set_title("Mean PAR Heatmap by Position and Rank Bin (2020–2024, Half-PPR)", fontsize=13, fontweight="bold")
    ax.set_xlabel("Positional Rank Bin", fontsize=11)

    plt.tight_layout()
    out_path = os.path.join(OUTPUT_DIR, "par_heatmap.png")
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out_path}")


def export_csv(tier_summary_df: pd.DataFrame, cross_position_ranking_df: pd.DataFrame) -> None:
    """Export tier summary and cross-position ranking to CSV files."""
    os.makedirs(DATA_DIR, exist_ok=True)

    tier_path = os.path.join(DATA_DIR, "tier_summary.csv")
    cross_path = os.path.join(DATA_DIR, "cross_position_ranking.csv")

    tier_summary_df.to_csv(tier_path, index=False)
    print(f"Saved: {tier_path}")

    cross_position_ranking_df.to_csv(cross_path, index=False)
    print(f"Saved: {cross_path}")


def visualize_and_export(
    tier_summary_df: pd.DataFrame,
    cross_position_ranking_df: pd.DataFrame,
    fantasy_df: Optional[pd.DataFrame] = None,
) -> None:
    """
    Main entry point for step 5.
    Generates all charts and exports CSV files.
    """
    print("\n=== Step 5: Visualize and Export Results ===")

    print("\n--- Chart 1: PAR curves by position ---")
    plot_par_curves(tier_summary_df)

    print("\n--- Chart 2: Auction value top 20 ---")
    plot_auction_value_top20(cross_position_ranking_df, fantasy_df=fantasy_df)

    print("\n--- Chart 3: PAR heatmap ---")
    plot_par_heatmap(tier_summary_df)

    print("\n--- Exporting CSVs ---")
    export_csv(tier_summary_df, cross_position_ranking_df)
