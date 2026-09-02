"""
Step 5 of Goal 3: Visualize PAR curves and auction values, and export results.
"""

from __future__ import annotations
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


def plot_par_curves(tier_summary_df: pd.DataFrame, replacement_ranks: dict | None = None, suffix: str = "") -> None:
    """
    Chart 1 — Multi-line PAR curve by positional rank for all positions.
    Saves to output/par_curves_by_position{suffix}.png.
    """
    if replacement_ranks is None:
        replacement_ranks = REPLACEMENT_RANKS

    _ensure_output_dir()

    fig, ax = plt.subplots(figsize=(12, 7))

    for position in POSITION_ORDER:
        max_rank = replacement_ranks.get(position, 12)
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
    out_path = os.path.join(OUTPUT_DIR, f"par_curves_by_position{suffix}.png")
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out_path}")

    # Validation: confirm all 5 positions appear
    positions_in_legend = [position for position in POSITION_ORDER
                           if not tier_summary_df[
                               (tier_summary_df["position"] == position)
                               & (tier_summary_df["positional_rank"] <= replacement_ranks.get(position, 12))
                           ].empty]
    print(f"Chart 1 validation — Positions plotted: {positions_in_legend}")

    for pos in ["RB", "WR"]:
        max_r = replacement_ranks.get(pos, 0)
        for shorter_pos in ["QB", "TE"]:
            short_r = replacement_ranks.get(shorter_pos, 0)
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
    suffix: str = "",
) -> None:
    """
    Chart 2 — Horizontal bar chart for the top 20 roster slots by auction value.
    If fantasy_df is provided, each bar is annotated with the players who held
    that positional rank across all seasons, from most recent to oldest, separated by " | ".
    Saves to output/auction_value_top20{suffix}.png.
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
                # Reverse so most recent season appears rightmost (right-aligned text)
                names_str = " | ".join(name for _, name in reversed(season_players))
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
    out_path = os.path.join(OUTPUT_DIR, f"auction_value_top20{suffix}.png")
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out_path}")


def plot_auction_value_curves(
    cross_position_ranking_df: pd.DataFrame,
    replacement_ranks: dict | None = None,
    suffix: str = "",
) -> None:
    """
    Chart 4 — Multi-line auction value curve by positional rank, overlaid for QB/RB/WR/TE.
    Mirrors the PAR curves chart but plots auction_value vs positional_rank.
    Saves to output/auction_value_curves{suffix}.png.
    """
    if replacement_ranks is None:
        replacement_ranks = REPLACEMENT_RANKS

    _ensure_output_dir()

    positions_to_plot = [p for p in ["QB", "RB", "WR", "TE"] if p in POSITION_ORDER]

    fig, ax = plt.subplots(figsize=(12, 7))

    for position in positions_to_plot:
        max_rank = replacement_ranks.get(position, 12)
        pos_df = cross_position_ranking_df[
            (cross_position_ranking_df["position"] == position)
            & (cross_position_ranking_df["positional_rank"] <= max_rank)
        ].sort_values("positional_rank")

        if pos_df.empty:
            print(f"WARNING: No auction value data for position {position}.")
            continue

        ax.plot(
            pos_df["positional_rank"],
            pos_df["auction_value"],
            color=POSITION_COLORS[position],
            marker="o",
            markersize=4,
            linewidth=2,
            label=position,
        )

    ax.axhline(y=0, color="black", linestyle="--", linewidth=1, alpha=0.7, label="$0")

    ax.set_title("Auction Value by Positional Rank (2020–2024, Half-PPR, 12-Team)", fontsize=14, fontweight="bold")
    ax.set_xlabel("Positional Rank", fontsize=12)
    ax.set_ylabel("Auction Value ($)", fontsize=12)
    ax.legend(fontsize=11)
    ax.grid(True, color="lightgray", alpha=0.3)

    plt.tight_layout()
    out_path = os.path.join(OUTPUT_DIR, f"auction_value_curves{suffix}.png")
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out_path}")

    positions_plotted = [
        p for p in positions_to_plot
        if not cross_position_ranking_df[
            (cross_position_ranking_df["position"] == p)
            & (cross_position_ranking_df["positional_rank"] <= replacement_ranks.get(p, 12))
        ].empty
    ]
    print(f"Chart 4 validation — Positions plotted: {positions_plotted}")


def plot_par_heatmap(tier_summary_df: pd.DataFrame, replacement_ranks: dict | None = None, suffix: str = "") -> None:
    """
    Chart 3 (optional) — Heatmap of mean PAR by position and rank bins.
    Saves to output/par_heatmap{suffix}.png.
    """
    if replacement_ranks is None:
        replacement_ranks = REPLACEMENT_RANKS

    _ensure_output_dir()

    bin_size = 5
    max_rank = max(replacement_ranks.values())
    bins = list(range(1, max_rank + 2, bin_size))
    bin_labels = [f"{b}–{b + bin_size - 1}" for b in bins[:-1]]

    heatmap_data = []
    for position in POSITION_ORDER:
        row_vals = []
        max_r = replacement_ranks.get(position, 12)
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
    out_path = os.path.join(OUTPUT_DIR, f"par_heatmap{suffix}.png")
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out_path}")


def plot_cross_source_comparison(results_dict: dict) -> None:
    """
    Bar chart comparing projected lineup points across the 4 cross-source combos.
    Bars are annotated with total auction cost.
    Saves to output/cross_source_comparison.png.
    """
    _ensure_output_dir()

    labels  = [v["label"]        for v in results_dict.values()]
    points  = [v["total_points"] for v in results_dict.values()]
    costs   = [v["total_cost"]   for v in results_dict.values()]

    x = range(len(labels))
    colors = ["#0072B2", "#E69F00", "#009E73", "#D55E00"]

    fig, ax = plt.subplots(figsize=(10, 6))
    bars = ax.bar(x, points, color=colors[: len(labels)], edgecolor="white", width=0.5)

    for bar, cost, pts in zip(bars, costs, points):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 5,
            f"${cost}",
            ha="center", va="bottom", fontsize=11, fontweight="bold",
        )
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() / 2,
            f"{pts:.1f} pts",
            ha="center", va="center", fontsize=10, color="white", fontweight="bold",
        )

    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=12)
    ax.set_ylabel("Projected Lineup Points (incl. 119-pt DST)", fontsize=11)
    ax.set_title(
        "Cross-Source Optimizer: Projected Points by Rank × Price Combo",
        fontsize=13, fontweight="bold",
    )
    ax.grid(True, axis="y", color="lightgray", alpha=0.4)
    ax.set_ylim(0, max(points) * 1.12)

    plt.tight_layout()
    out_path = os.path.join(OUTPUT_DIR, "cross_source_comparison.png")
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out_path}")


def plot_wtp_top20(
    wtp_df: pd.DataFrame,
    suffix: str = "_historical",
    source_label: str = "Hist avg",
) -> None:
    """
    Horizontal bar chart of the top-20 roster slots by WTP price.
    Mirrors the style of plot_auction_value_top20.

    Bars are split into:
      - solid fill  : WTP price (what you should be willing to pay)
      - hatched overlay : Method 1 auction value (for comparison)

    Saves to output/wtp_top20{suffix}.png.
    """
    _ensure_output_dir()

    # Restrict to starter range
    STARTER_CUTOFFS_LOCAL = {"QB": 12, "RB": 31, "WR": 41, "TE": 12}
    starters = wtp_df[
        wtp_df["positional_rank"] <= wtp_df["position"].map(STARTER_CUTOFFS_LOCAL)
    ].copy()
    top20 = starters.nlargest(20, "wtp_price").iloc[::-1].reset_index(drop=True)

    colors = [POSITION_COLORS.get(pos, "gray") for pos in top20["position"]]

    fig, ax = plt.subplots(figsize=(14, 8))

    bars_wtp = ax.barh(
        top20["roster_slot"],
        top20["wtp_price"],
        color=colors,
        edgecolor="white",
        height=0.7,
        label="WTP (shadow price)",
    )
    # Method 1 AV as a thin outline bar behind
    ax.barh(
        top20["roster_slot"],
        top20["method1_av"],
        color="none",
        edgecolor="black",
        linewidth=1.2,
        height=0.7,
        linestyle="--",
        label="Method 1 Auction Value",
    )

    for bar, (_, row) in zip(bars_wtp, top20.iterrows()):
        bar_width  = bar.get_width()
        bar_mid_y  = bar.get_y() + bar.get_height() / 2
        ax.text(
            bar_width + 0.5, bar_mid_y,
            f"${row['wtp_price']:.0f}",
            va="center", ha="left", fontsize=9,
        )
        pts_str = f"{row['expected_points']:.0f} pts"
        ax.text(
            bar_width * 0.97, bar_mid_y,
            pts_str,
            va="center", ha="right",
            fontsize=8, color="white", fontweight="bold",
            clip_on=True,
        )

    legend_patches = [
        mpatches.Patch(color=POSITION_COLORS[pos], label=pos)
        for pos in POSITION_ORDER
        if pos in top20["position"].values
    ]
    from matplotlib.lines import Line2D
    legend_patches.append(
        Line2D([0], [0], color="black", linewidth=1.2, linestyle="--", label="Method 1 AV")
    )
    ax.legend(handles=legend_patches, fontsize=9, loc="lower right")

    ax.set_title(
        f"Top 20 Roster Slots by Willingness-to-Pay ({source_label} points, 12-Team, Half-PPR)",
        fontsize=13,
        fontweight="bold",
    )
    ax.set_xlabel("Willingness to Pay ($)", fontsize=11)
    ax.set_ylabel("Roster Slot", fontsize=11)
    ax.grid(True, axis="x", color="lightgray", alpha=0.3)
    ax.set_xlim(0, max(top20["wtp_price"].max(), top20["method1_av"].max()) * 1.18)

    plt.tight_layout()
    out_path = os.path.join(OUTPUT_DIR, f"wtp_top20{suffix}.png")
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out_path}")


def plot_wtp_comparison(
    wtp_df: pd.DataFrame,
    suffix: str = "_historical",
    source_label: str = "Hist avg",
) -> None:
    """
    Scatter plot: WTP price (x-axis) vs. Method 1 Auction Value (y-axis),
    coloured by position, with a y=x reference line.

    Points above the y=x line → Method 1 overprices this slot relative to WTP.
    Points below the y=x line → Method 1 underprices this slot relative to WTP.

    Saves to output/wtp_vs_method1{suffix}.png.
    """
    _ensure_output_dir()

    STARTER_CUTOFFS_LOCAL = {"QB": 12, "RB": 31, "WR": 41, "TE": 12}
    plot_df = wtp_df[
        (wtp_df["positional_rank"] <= wtp_df["position"].map(STARTER_CUTOFFS_LOCAL))
        & (wtp_df["method1_av"] > 0)
    ].copy()

    fig, ax = plt.subplots(figsize=(10, 8))

    # y = x reference line
    axis_max = max(plot_df["wtp_price"].max(), plot_df["method1_av"].max()) * 1.08
    ax.plot([0, axis_max], [0, axis_max], color="gray", linewidth=1.2, linestyle="--",
            label="y = x  (WTP = Method 1 AV)", zorder=1)

    for pos in POSITION_ORDER:
        sub = plot_df[plot_df["position"] == pos]
        if sub.empty:
            continue
        ax.scatter(
            sub["wtp_price"],
            sub["method1_av"],
            color=POSITION_COLORS[pos],
            label=pos,
            s=60,
            edgecolors="white",
            linewidths=0.5,
            zorder=3,
        )
        # Annotate each point with its roster_slot label
        for _, row in sub.iterrows():
            ax.annotate(
                row["roster_slot"],
                xy=(row["wtp_price"], row["method1_av"]),
                xytext=(4, 2),
                textcoords="offset points",
                fontsize=7,
                color=POSITION_COLORS[pos],
            )

    # Shade overpriced / underpriced regions
    ax.fill_between(
        [0, axis_max], [0, axis_max], axis_max,
        alpha=0.04, color="red",
        label="Method 1 overprices (above line)",
    )
    ax.fill_between(
        [0, axis_max], 0, [0, axis_max],
        alpha=0.04, color="green",
        label="Method 1 underprices (below line)",
    )

    ax.set_xlim(0, axis_max)
    ax.set_ylim(0, axis_max)
    ax.set_xlabel(f"WTP Shadow Price ($)  [{source_label} points]", fontsize=11)
    ax.set_ylabel("Method 1 Auction Value ($)", fontsize=11)
    ax.set_title(
        f"WTP vs. Method 1 Auction Value by Roster Slot\n({source_label} points, 12-Team, Half-PPR)",
        fontsize=13,
        fontweight="bold",
    )
    ax.legend(fontsize=9, loc="upper left")
    ax.grid(True, color="lightgray", alpha=0.3)

    plt.tight_layout()
    out_path = os.path.join(OUTPUT_DIR, f"wtp_vs_method1{suffix}.png")
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out_path}")


def plot_wtp_vs_espn(
    wtp_df: pd.DataFrame,
    suffix: str = "_historical",
    source_label: str = "Hist avg",
) -> None:
    """
    Scatter plot: WTP price (x-axis) vs. ESPN Estimated Auction Value (y-axis),
    coloured by position, with a y=x reference line.

    Points above the y=x line → ESPN overprices this slot relative to WTP.
    Points below the y=x line → ESPN underprices this slot relative to WTP.

    Saves to output/wtp_vs_espn_estimates{suffix}.png.
    """
    _ensure_output_dir()

    STARTER_CUTOFFS_LOCAL = {"QB": 12, "RB": 31, "WR": 41, "TE": 12}
    plot_df = wtp_df[
        (wtp_df["positional_rank"] <= wtp_df["position"].map(STARTER_CUTOFFS_LOCAL))
        & (wtp_df["espn_av"] > 0)
    ].copy()

    fig, ax = plt.subplots(figsize=(10, 8))

    # y = x reference line
    axis_max = max(plot_df["wtp_price"].max(), plot_df["espn_av"].max()) * 1.08
    ax.plot([0, axis_max], [0, axis_max], color="gray", linewidth=1.2, linestyle="--",
            label="y = x  (WTP = ESPN AV)", zorder=1)

    for pos in POSITION_ORDER:
        sub = plot_df[plot_df["position"] == pos]
        if sub.empty:
            continue
        ax.scatter(
            sub["wtp_price"],
            sub["espn_av"],
            color=POSITION_COLORS[pos],
            label=pos,
            s=60,
            edgecolors="white",
            linewidths=0.5,
            zorder=3,
        )
        for _, row in sub.iterrows():
            ax.annotate(
                row["roster_slot"],
                xy=(row["wtp_price"], row["espn_av"]),
                xytext=(4, 2),
                textcoords="offset points",
                fontsize=7,
                color=POSITION_COLORS[pos],
            )

    # Shade overpriced / underpriced regions
    ax.fill_between(
        [0, axis_max], [0, axis_max], axis_max,
        alpha=0.04, color="red",
        label="ESPN overprices (above line)",
    )
    ax.fill_between(
        [0, axis_max], 0, [0, axis_max],
        alpha=0.04, color="green",
        label="ESPN underprices (below line)",
    )

    ax.set_xlim(0, axis_max)
    ax.set_ylim(0, axis_max)
    ax.set_xlabel(f"WTP Shadow Price ($)  [{source_label} points]", fontsize=11)
    ax.set_ylabel("ESPN Estimated Auction Value ($)", fontsize=11)
    ax.set_title(
        f"WTP vs. ESPN Estimated Auction Value by Roster Slot\n({source_label} points, 12-Team, Half-PPR)",
        fontsize=13,
        fontweight="bold",
    )
    ax.legend(fontsize=9, loc="upper left")
    ax.grid(True, color="lightgray", alpha=0.3)

    plt.tight_layout()
    out_path = os.path.join(OUTPUT_DIR, f"wtp_vs_espn_estimates{suffix}.png")
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out_path}")


def plot_wtp_vs_espn_interactive(
    wtp_df: pd.DataFrame,
    suffix: str = "_historical",
    source_label: str = "Hist avg",
) -> None:
    """
    Interactive (Plotly) version of `plot_wtp_vs_espn`: WTP price (x-axis) vs.
    ESPN Estimated Auction Value (y-axis), coloured by position, with a y=x
    reference line, zoom/pan controls, and player-name hover tooltips.

    Saves to output/wtp_vs_espn_estimates{suffix}.html.
    """
    try:
        import plotly.graph_objects as go
    except ImportError:
        print("plotly is not installed; skipping interactive WTP vs ESPN chart. "
              "Install with `pip install plotly`.")
        return

    _ensure_output_dir()

    STARTER_CUTOFFS_LOCAL = {"QB": 12, "RB": 31, "WR": 41, "TE": 12}
    plot_df = wtp_df[
        (wtp_df["positional_rank"] <= wtp_df["position"].map(STARTER_CUTOFFS_LOCAL))
        & (wtp_df["espn_av"] > 0)
    ].copy()

    axis_max = max(plot_df["wtp_price"].max(), plot_df["espn_av"].max()) * 1.08

    fig = go.Figure()

    # Shaded overpriced / underpriced regions
    fig.add_shape(
        type="rect", x0=0, y0=0, x1=axis_max, y1=axis_max,
        fillcolor="green", opacity=0.04, line_width=0, layer="below",
    )
    fig.add_shape(
        type="rect", x0=0, y0=0, x1=axis_max, y1=axis_max,
        fillcolor="red", opacity=0.04, line_width=0, layer="below",
    )

    # y = x reference line
    fig.add_trace(go.Scatter(
        x=[0, axis_max], y=[0, axis_max],
        mode="lines",
        line=dict(color="gray", width=1.2, dash="dash"),
        name="y = x  (WTP = ESPN AV)",
        hoverinfo="skip",
    ))

    for pos in POSITION_ORDER:
        sub = plot_df[plot_df["position"] == pos]
        if sub.empty:
            continue
        fig.add_trace(go.Scatter(
            x=sub["wtp_price"],
            y=sub["espn_av"],
            mode="markers",
            name=pos,
            marker=dict(
                color=POSITION_COLORS[pos],
                size=10,
                line=dict(color="white", width=0.5),
            ),
            customdata=sub[["player_name", "wtp_price", "espn_av"]],
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "WTP: $%{customdata[1]:.1f}<br>"
                "ESPN Est.: $%{customdata[2]:.1f}"
                "<extra></extra>"
            ),
        ))

    fig.update_xaxes(range=[0, axis_max], title_text=f"WTP Shadow Price ($)  [{source_label} points]")
    fig.update_yaxes(range=[0, axis_max], title_text="ESPN Estimated Auction Value ($)")
    fig.update_layout(
        title=dict(
            text=f"WTP vs. ESPN Estimated Auction Value by Roster Slot"
                 f"<br><sub>{source_label} points, 12-Team, Half-PPR</sub>",
        ),
        dragmode="zoom",
        template="plotly_white",
        legend_title_text="Position",
        width=900,
        height=750,
    )

    div_id = "wtp-chart"
    chart_html = fig.to_html(include_plotlyjs="cdn", full_html=True, div_id=div_id)

    search_ui = f"""
    <div style="max-width:900px; margin:12px auto 0 auto; font-family:sans-serif;">
      <label for="player-search"><b>Highlight player:</b></label>
      <input type="text" id="player-search" placeholder="Type a player name..."
             style="padding:6px 10px; width:300px; font-size:14px; margin-left:8px;
                    border:1px solid #ccc; border-radius:4px;" />
      <span id="player-search-count" style="margin-left:10px; color:#555; font-size:13px;"></span>
    </div>
    <script>
    (function() {{
        var gd = document.getElementById("{div_id}");
        var input = document.getElementById("player-search");
        var countLabel = document.getElementById("player-search-count");

        function applyHighlight() {{
            var query = input.value.trim().toLowerCase();
            var traceIndices = [];
            var sizeUpdates = [];
            var opacityUpdates = [];
            var lineWidthUpdates = [];
            var lineColorUpdates = [];
            var matchCount = 0;
            var hoverPoints = [];

            gd.data.forEach(function(trace, i) {{
                if (!trace.customdata) {{ return; }}
                traceIndices.push(i);
                var sizes = [], opacities = [], lineWidths = [], lineColors = [];
                trace.customdata.forEach(function(row, pointIndex) {{
                    var name = String(row[0]).toLowerCase();
                    var isMatch = query.length > 0 && name.indexOf(query) !== -1;
                    if (isMatch) {{
                        matchCount += 1;
                        hoverPoints.push({{ curveNumber: i, pointNumber: pointIndex }});
                    }}
                    sizes.push(isMatch ? 20 : 10);
                    opacities.push(query.length === 0 ? 1 : (isMatch ? 1 : 0.12));
                    lineWidths.push(isMatch ? 2.5 : 0.5);
                    lineColors.push(isMatch ? "black" : "white");
                }});
                sizeUpdates.push(sizes);
                opacityUpdates.push(opacities);
                lineWidthUpdates.push(lineWidths);
                lineColorUpdates.push(lineColors);
            }});

            Plotly.restyle(gd, {{
                "marker.size": sizeUpdates,
                "marker.opacity": opacityUpdates,
                "marker.line.width": lineWidthUpdates,
                "marker.line.color": lineColorUpdates,
            }}, traceIndices);

            countLabel.textContent = query.length === 0
                ? ""
                : (matchCount + " match" + (matchCount === 1 ? "" : "es"));

            if (hoverPoints.length > 0 && hoverPoints.length <= 25) {{
                Plotly.Fx.hover(gd, hoverPoints);
            }} else {{
                Plotly.Fx.hover(gd, []);
            }}
        }}

        input.addEventListener("input", applyHighlight);
    }})();
    </script>
    """

    chart_html = chart_html.replace("</body>", search_ui + "</body>")

    out_path = os.path.join(OUTPUT_DIR, f"wtp_vs_espn_estimates{suffix}.html")
    with open(out_path, "w") as f:
        f.write(chart_html)
    print(f"Saved: {out_path}")


def plot_wtp_source_comparison(merged_df: pd.DataFrame, top_n: int = 25) -> None:
    """
    Grouped horizontal bar chart comparing WTP price across the three point
    sources (historical / espn / blended) for the top-N roster slots (by
    blended WTP). Used to sanity-check whether blending meaningfully changes
    recommendations (Goal 10, Step 5).

    Expects `merged_df` with columns:
        roster_slot, position, wtp_historical, wtp_espn, wtp_blended

    Saves to output/wtp_source_comparison.png.
    """
    _ensure_output_dir()

    top = merged_df.nlargest(top_n, "wtp_blended").iloc[::-1].reset_index(drop=True)

    y = np.arange(len(top))
    height = 0.25

    fig, ax = plt.subplots(figsize=(12, max(6, len(top) * 0.35)))
    ax.barh(y - height, top["wtp_historical"], height=height, color="#0072B2", label="Historical")
    ax.barh(y,          top["wtp_espn"],       height=height, color="#E69F00", label="ESPN")
    ax.barh(y + height, top["wtp_blended"],    height=height, color="#009E73", label="Blended")

    ax.set_yticks(y)
    ax.set_yticklabels(top["roster_slot"], fontsize=9)
    ax.set_xlabel("Willingness to Pay ($)", fontsize=11)
    ax.set_title(
        f"WTP by Roster Slot: Historical vs. ESPN vs. Blended (Top {top_n} by Blended WTP)",
        fontsize=13, fontweight="bold",
    )
    ax.legend(fontsize=10, loc="lower right")
    ax.grid(True, axis="x", color="lightgray", alpha=0.3)

    plt.tight_layout()
    out_path = os.path.join(OUTPUT_DIR, "wtp_source_comparison.png")
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out_path}")


def export_csv(tier_summary_df: pd.DataFrame, cross_position_ranking_df: pd.DataFrame, suffix: str = "") -> None:
    """Export tier summary and cross-position ranking to CSV files."""
    os.makedirs(DATA_DIR, exist_ok=True)

    tier_path = os.path.join(DATA_DIR, f"tier_summary{suffix}.csv")
    cross_path = os.path.join(DATA_DIR, f"cross_position_ranking{suffix}.csv")

    tier_summary_df.to_csv(tier_path, index=False)
    print(f"Saved: {tier_path}")

    cross_position_ranking_df.to_csv(cross_path, index=False)
    print(f"Saved: {cross_path}")


def visualize_and_export(
    tier_summary_df: pd.DataFrame,
    cross_position_ranking_df: pd.DataFrame,
    fantasy_df: Optional[pd.DataFrame] = None,
    replacement_ranks: dict | None = None,
    suffix: str = "",
) -> None:
    """
    Main entry point for step 5.
    Generates all charts and exports CSV files.

    suffix: appended to all output filenames (e.g. "_waiver" -> par_curves_by_position_waiver.png)
    replacement_ranks: override for positional rank cutoffs used in chart axes/labels
    """
    print("\n=== Step 5: Visualize and Export Results ===")

    print("\n--- Chart 1: PAR curves by position ---")
    plot_par_curves(tier_summary_df, replacement_ranks=replacement_ranks, suffix=suffix)

    print("\n--- Chart 2: Auction value top 20 ---")
    plot_auction_value_top20(cross_position_ranking_df, fantasy_df=fantasy_df, suffix=suffix)

    print("\n--- Chart 3: PAR heatmap ---")
    plot_par_heatmap(tier_summary_df, replacement_ranks=replacement_ranks, suffix=suffix)

    print("\n--- Chart 4: Auction value curves by position ---")
    plot_auction_value_curves(cross_position_ranking_df, replacement_ranks=replacement_ranks, suffix=suffix)

    print("\n--- Exporting CSVs ---")
    export_csv(tier_summary_df, cross_position_ranking_df, suffix=suffix)
