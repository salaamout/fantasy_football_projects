"""
Diagnostic: compare λ (budget shadow price) and replacement-level points
between the two WTP point sources — historical (2021–2025 avg) and ESPN 2026
projected.

Run:
    python -m analysis.diagnose_wtp_sources
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).parent
_ROOT = _HERE.parent

try:
    from .willingness_to_pay import (
        build_points_table,
        load_method1_auction_values,
        attach_prices,
        solve_lp_relaxation,
        REPLACEMENT_RANKS as WTP_REPLACEMENT_RANKS,
        SKILL_BUDGET,
        STARTER_CUTOFFS,
        POOL_DEPTH,
    )
    from .calculate_par import REPLACEMENT_RANKS
except ImportError:
    sys.path.insert(0, str(_HERE))
    from willingness_to_pay import (
        build_points_table,
        load_method1_auction_values,
        attach_prices,
        solve_lp_relaxation,
        SKILL_BUDGET,
        STARTER_CUTOFFS,
        POOL_DEPTH,
    )
    from calculate_par import REPLACEMENT_RANKS


SOURCES = ["historical", "espn"]


def _get_replacement_pts(points_table: pd.DataFrame, source: str = "") -> dict[str, float]:
    """
    Return the expected_points value at each position's replacement rank.
    """
    result = {}
    for pos, rep_rank in REPLACEMENT_RANKS.items():
        if pos not in POOL_DEPTH:
            continue
        row = points_table[
            (points_table["position"] == pos) &
            (points_table["positional_rank"] == rep_rank)
        ]
        if not row.empty:
            result[pos] = float(row["expected_points"].values[0])
        else:
            # Closest rank available
            pos_rows = points_table[points_table["position"] == pos].sort_values("positional_rank")
            closest = pos_rows.iloc[-1] if not pos_rows.empty else None
            result[pos] = float(closest["expected_points"]) if closest is not None else float("nan")
            print(
                f"  WARNING: No data at replacement rank {rep_rank} for {pos} "
                f"({source}). Using rank {int(closest['positional_rank'])} instead."
            )
    return result


def run_diagnostics() -> None:
    method1_avs = load_method1_auction_values()

    summary_rows = []

    for source in SOURCES:
        label = "ESPN 2026 projected" if source == "espn" else "Historical avg (2021–2025)"
        print(f"\n{'='*60}")
        print(f"  Source: {label}")
        print(f"{'='*60}")

        # --- Points table ---
        points_table = build_points_table(source)

        # --- Replacement-level points ---
        rep_pts = _get_replacement_pts(points_table, source=source)

        print(f"\n  Replacement-level points (rank used):")
        print(f"  {'Position':<10} {'Rep Rank':>10} {'Rep Points':>12}")
        print(f"  {'-'*34}")
        for pos in ["QB", "RB", "WR", "TE"]:
            rep_rank = REPLACEMENT_RANKS.get(pos, "?")
            pts = rep_pts.get(pos, float("nan"))
            print(f"  {pos:<10} {rep_rank:>10} {pts:>12.2f}")
            summary_rows.append({
                "source": source,
                "position": pos,
                "replacement_rank": rep_rank,
                "replacement_pts": round(pts, 2),
            })

        # --- Top starter points for context ---
        print(f"\n  Top starter expected_points (rank 1):")
        print(f"  {'Position':<10} {'Rank-1 Pts':>12} {'Rank-1 PAR':>12}")
        print(f"  {'-'*36}")
        for pos in ["QB", "RB", "WR", "TE"]:
            r1 = points_table[
                (points_table["position"] == pos) & (points_table["positional_rank"] == 1)
            ]
            if not r1.empty:
                ep = float(r1["expected_points"].values[0])
                par = float(r1["par_points"].values[0])
                print(f"  {pos:<10} {ep:>12.2f} {par:>12.2f}")

        # --- Solve LP relaxation → λ ---
        player_pool = attach_prices(points_table, method1_avs)
        _, lambda_val = solve_lp_relaxation(player_pool, budget=SKILL_BUDGET)

        print(f"\n  LP relaxation result:")
        print(f"    λ (shadow price)  = {lambda_val:.4f} pts per $")
        print(f"    1/λ ($ per point) = {1/lambda_val:.4f}")
        print(f"    Interpretation: each extra $1 of budget buys {lambda_val:.3f} expected points")

        # Add lambda to summary rows for this source
        for row in summary_rows:
            if row["source"] == source and "lambda" not in row:
                row["lambda"] = round(lambda_val, 6)

    # --- Side-by-side comparison ---
    print(f"\n{'='*60}")
    print("  Side-by-side Comparison")
    print(f"{'='*60}")

    hist_rep = {r["position"]: r["replacement_pts"] for r in summary_rows if r["source"] == "historical"}
    espn_rep = {r["position"]: r["replacement_pts"] for r in summary_rows if r["source"] == "espn"}
    hist_lam = next(r["lambda"] for r in summary_rows if r["source"] == "historical")
    espn_lam = next(r["lambda"] for r in summary_rows if r["source"] == "espn")

    print(f"\n  {'Position':<10} {'Hist Rep Pts':>14} {'ESPN Rep Pts':>14} {'Diff':>10}")
    print(f"  {'-'*50}")
    for pos in ["QB", "RB", "WR", "TE"]:
        h = hist_rep.get(pos, float("nan"))
        e = espn_rep.get(pos, float("nan"))
        diff = e - h
        sign = "+" if diff >= 0 else ""
        print(f"  {pos:<10} {h:>14.2f} {e:>14.2f} {sign}{diff:>9.2f}")

    print(f"\n  {'Metric':<30} {'Historical':>12} {'ESPN':>12} {'Ratio E/H':>12}")
    print(f"  {'-'*68}")
    print(f"  {'λ (pts/$)':<30} {hist_lam:>12.4f} {espn_lam:>12.4f} {espn_lam/hist_lam:>12.3f}")
    print(f"  {'1/λ ($/pt)':<30} {1/hist_lam:>12.4f} {1/espn_lam:>12.4f} {(1/espn_lam)/(1/hist_lam):>12.3f}")

    # --- WTP for QB1 under each source to illustrate QB inflation ---
    print(f"\n  QB value spotlight (WTP = PAR / λ):")
    print(f"  {'Slot':<10} {'Source':<20} {'EP':>8} {'PAR':>8} {'λ':>8} {'WTP ($)':>10}")
    print(f"  {'-'*68}")
    for source in SOURCES:
        label = "ESPN" if source == "espn" else "Historical"
        pt = build_points_table(source)
        lam = espn_lam if source == "espn" else hist_lam
        for rank in [1, 6, 12]:
            row = pt[(pt["position"] == "QB") & (pt["positional_rank"] == rank)]
            if not row.empty:
                ep = float(row["expected_points"].values[0])
                par = float(row["par_points"].values[0])
                wtp = par / lam
                print(f"  {'QB' + str(rank):<10} {label:<20} {ep:>8.1f} {par:>8.1f} {lam:>8.4f} {wtp:>10.2f}")


if __name__ == "__main__":
    run_diagnostics()
