"""
Goal 9: Principled "Willingness to Pay" (WTP) by roster slot.

Shadow prices are computed via LP relaxation of the lineup optimizer:

    WTP(position, rank) = expected_points(position, rank) / λ

where λ is the dual variable (shadow price) on the personal budget constraint
in the LP relaxation.  λ has units of "points per dollar", so WTP has units
of dollars — the most you should pay to keep that slot in an optimal lineup.

Two expected-points sources are supported:
  historical  (default) — 5-year average half-PPR points (2021–2025)
  espn                  — ESPN 2026 projected points

League / lineup settings
    Budget              : $200 total, $1 reserved for DST → $199 skill budget
    Lineup slots        : QB×1, RB×2, WR×3, TE×1, FLEX×1 (RB/WR/TE)
    Bench               : 4 spots (RB/WR only; ≤3 RB, ≤2 WR; no QB/TE)
    DST                 : pre-assigned at $1 (not modelled in the LP)

Usage
    python -m analysis.willingness_to_pay               # historical points
    python -m analysis.willingness_to_pay --espn        # ESPN projected points
    python -m analysis.willingness_to_pay --help
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import pulp

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
_HERE = Path(__file__).parent
_ROOT = _HERE.parent
DATA_DIR   = _ROOT / "data"
OUTPUT_DIR = _ROOT / "output"

try:
    from .calculate_par import build_avg_points_lookup, REPLACEMENT_RANKS
    from .lineup_optimizer import (
        FLEX_POSITIONS,
        LINEUP_SLOTS,
        CROSS_BENCH_SLOTS,
        attach_expected_points,
    )
except ImportError:
    sys.path.insert(0, str(_HERE))
    from calculate_par import build_avg_points_lookup, REPLACEMENT_RANKS
    from lineup_optimizer import (
        FLEX_POSITIONS,
        LINEUP_SLOTS,
        CROSS_BENCH_SLOTS,
        attach_expected_points,
    )

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ESPN_DATA_PATH        = DATA_DIR / "espn_projected_values.csv"
METHOD1_AV_PATH       = DATA_DIR / "cross_position_ranking.csv"
WTP_CSV_PATH          = DATA_DIR / "willingness_to_pay.csv"

SKILL_BUDGET          = 199          # $200 − $1 for DST
SKILL_POSITIONS       = {"QB", "RB", "WR", "TE"}

# Pool depth: how many players per position to include in the LP pool.
# Must be at least (starters + bench) deep; extra depth gives the LP room.
POOL_DEPTH = {"QB": 20, "RB": 60, "WR": 70, "TE": 20}

# Replacement ranks (1-indexed): positional_rank at which the starter pool ends.
STARTER_CUTOFFS = {"QB": 12, "RB": 31, "WR": 41, "TE": 12}


# ---------------------------------------------------------------------------
# Step 1 – Build expected-points table
# ---------------------------------------------------------------------------

def _build_historical_points_table() -> pd.DataFrame:
    """
    Return a tidy DataFrame of historical average points (2021–2025) indexed
    by (position, positional_rank).

    Columns: position, positional_rank, expected_points, flex_expected_points
    """
    avg_points, avg_flex_points = build_avg_points_lookup(
        seasons=(2021, 2022, 2023, 2024, 2025)
    )

    rows = []
    for pos, depth in POOL_DEPTH.items():
        for rank in range(1, depth + 1):
            ep   = avg_points.get((pos, rank))
            fep  = avg_flex_points.get((pos, rank)) if pos in FLEX_POSITIONS else 0.0
            if ep is None:
                break    # no data beyond this rank
            rows.append({
                "position":          pos,
                "positional_rank":   rank,
                "expected_points":   ep,
                "flex_expected_points": fep if fep is not None else 0.0,
            })

    return pd.DataFrame(rows)


def _build_espn_points_table() -> pd.DataFrame:
    """
    Return a tidy DataFrame of ESPN 2026 projected points indexed by
    (position, positional_rank).

    Columns: position, positional_rank, expected_points, flex_expected_points

    flex_expected_points mirrors expected_points for flex-eligible positions
    (RB/WR/TE) because ESPN projects raw totals (not relative-to-replacement).
    """
    espn = pd.read_csv(ESPN_DATA_PATH)
    espn = espn[espn["position"].isin(SKILL_POSITIONS)].copy()
    espn = espn.dropna(subset=["projected_points", "positional_rank"])
    espn["positional_rank"] = espn["positional_rank"].astype(int)
    espn = espn.sort_values(["position", "positional_rank"]).reset_index(drop=True)

    rows = []
    for _, row in espn.iterrows():
        pos  = row["position"]
        rank = int(row["positional_rank"])
        ep   = float(row["projected_points"])
        fep  = ep if pos in FLEX_POSITIONS else 0.0
        rows.append({
            "position":             pos,
            "positional_rank":      rank,
            "expected_points":      ep,
            "flex_expected_points": fep,
        })

    return pd.DataFrame(rows)


def build_points_table(point_source: str = "historical") -> pd.DataFrame:
    """
    Build the expected-points table for the LP.

    Parameters
    ----------
    point_source : "historical" (default) or "espn"

    Returns
    -------
    DataFrame with columns:
        position, positional_rank, expected_points, flex_expected_points
    """
    if point_source == "espn":
        return _build_espn_points_table()
    elif point_source == "historical":
        return _build_historical_points_table()
    else:
        raise ValueError(f"Unknown point_source: {point_source!r}. Choose 'historical' or 'espn'.")


# ---------------------------------------------------------------------------
# Step 2 – Attach Method 1 auction values as prices
# ---------------------------------------------------------------------------

def load_method1_auction_values() -> dict[tuple[str, int], float]:
    """
    Load Method 1 (PAR-proportional) auction values from cross_position_ranking.csv.
    Returns a dict keyed by (position, positional_rank) → auction_value.
    Players beyond the starter range have $0 or no entry.
    """
    df = pd.read_csv(METHOD1_AV_PATH)
    df = df[df["position"].isin(SKILL_POSITIONS)]
    return {
        (row["position"], int(row["positional_rank"])): float(row["auction_value"])
        for _, row in df.iterrows()
    }


def attach_prices(
    points_table: pd.DataFrame,
    method1_avs: dict[tuple[str, int], float],
    min_price: float = 1.0,
) -> pd.DataFrame:
    """
    Attach a `price` column to the points table using Method 1 auction values.
    Players beyond the starter range default to min_price.
    """
    df = points_table.copy()
    df["method1_av"] = df.apply(
        lambda r: method1_avs.get((r["position"], int(r["positional_rank"])), 0.0),
        axis=1,
    )
    df["price"] = df["method1_av"].clip(lower=min_price)
    return df


# ---------------------------------------------------------------------------
# Step 3 – LP relaxation → shadow prices
# ---------------------------------------------------------------------------

def solve_lp_relaxation(
    player_pool: pd.DataFrame,
    budget: int = SKILL_BUDGET,
) -> tuple[pd.DataFrame, float]:
    """
    Solve the LP relaxation of the lineup optimizer (continuous vars ∈ [0,1]).

    Variables for each player i:
        y_i ∈ [0,1]  — fills a dedicated positional starter slot
        z_i ∈ [0,1]  — fills the FLEX slot (RB/WR/TE only)
        b_i ∈ [0,1]  — fills a bench slot (no QB/TE; ≤3 RB, ≤2 WR)

    Returns
    -------
    (player_pool_with_duals, lambda_shadow_price)

    lambda_shadow_price : float
        Dual variable on the budget constraint (points per dollar).
        WTP_i = expected_points_i / lambda_shadow_price.
    """
    players = player_pool.to_dict("records")
    n = len(players)

    prob = pulp.LpProblem("wtp_lp_relaxation", pulp.LpMaximize)

    # Continuous relaxation of binary variables
    y = [pulp.LpVariable(f"y_{i}", lowBound=0, upBound=1, cat="Continuous") for i in range(n)]
    z = [pulp.LpVariable(f"z_{i}", lowBound=0, upBound=1, cat="Continuous") for i in range(n)]
    b = [pulp.LpVariable(f"b_{i}", lowBound=0, upBound=1, cat="Continuous") for i in range(n)]

    # FLEX only for RB/WR/TE
    for i, p in enumerate(players):
        if p["position"] not in FLEX_POSITIONS:
            prob += z[i] == 0

    # No QB or TE on bench
    for i, p in enumerate(players):
        if p["position"] in ("QB", "TE"):
            prob += b[i] == 0

    # Each player fills at most one slot
    for i in range(n):
        prob += y[i] + z[i] + b[i] <= 1

    # Objective: maximise expected starter points
    prob += pulp.lpSum(
        p["expected_points"]      * y[i] +
        p["flex_expected_points"] * z[i]
        for i, p in enumerate(players)
    )

    # Named budget constraint (we need its dual)
    budget_constraint = pulp.lpSum(
        p["price"] * (y[i] + z[i] + b[i])
        for i, p in enumerate(players)
    ) <= budget
    prob += budget_constraint, "budget"

    # Positional slot counts
    prob += (pulp.lpSum(y[i] for i, p in enumerate(players) if p["position"] == "QB")
             == LINEUP_SLOTS["QB"]), "qb_slots"
    prob += (pulp.lpSum(y[i] for i, p in enumerate(players) if p["position"] == "RB")
             == LINEUP_SLOTS["RB"]), "rb_slots"
    prob += (pulp.lpSum(y[i] for i, p in enumerate(players) if p["position"] == "WR")
             == LINEUP_SLOTS["WR"]), "wr_slots"
    prob += (pulp.lpSum(y[i] for i, p in enumerate(players) if p["position"] == "TE")
             == LINEUP_SLOTS["TE"]), "te_slots"

    # Exactly one FLEX slot
    prob += pulp.lpSum(z) == LINEUP_SLOTS["FLEX"], "flex_slot"

    # Exactly CROSS_BENCH_SLOTS bench players
    prob += pulp.lpSum(b) == CROSS_BENCH_SLOTS, "bench_count"

    # Bench composition
    prob += (pulp.lpSum(b[i] for i, p in enumerate(players) if p["position"] == "RB")
             <= 3), "bench_rb_max"
    prob += (pulp.lpSum(b[i] for i, p in enumerate(players) if p["position"] == "WR")
             <= 2), "bench_wr_max"
    prob += (pulp.lpSum(b[i] for i, p in enumerate(players) if p["position"] == "RB")
             >= 1), "bench_rb_min"
    prob += (pulp.lpSum(b[i] for i, p in enumerate(players) if p["position"] == "WR")
             >= 1), "bench_wr_min"

    # Solve
    status = prob.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError(f"LP relaxation did not reach an optimal solution. Status: {pulp.LpStatus[status]}")

    # --- Extract shadow price of budget constraint ---
    # In PuLP, constraint.pi is the dual variable.
    # For a ≤ constraint in a maximisation LP, pi >= 0 (increasing the RHS
    # increases the objective, i.e. more budget → more expected points).
    budget_dual = prob.constraints["budget"].pi
    if budget_dual is None:
        raise RuntimeError(
            "Could not retrieve dual variable for budget constraint. "
            "Ensure you are using an LP (not MIP) solver."
        )
    lambda_val = float(budget_dual)
    if lambda_val <= 0:
        raise RuntimeError(
            f"Budget shadow price is {lambda_val:.4f} ≤ 0. "
            "The budget constraint may not be binding — try increasing pool depth or reducing budget."
        )

    # Attach LP variable values to player pool for inspection
    pool = player_pool.copy()
    pool["lp_y"] = [pulp.value(y[i]) for i in range(n)]
    pool["lp_z"] = [pulp.value(z[i]) for i in range(n)]
    pool["lp_b"] = [pulp.value(b[i]) for i in range(n)]

    return pool, lambda_val


# ---------------------------------------------------------------------------
# Step 4 – Compute WTP and assemble output table
# ---------------------------------------------------------------------------

def compute_wtp(
    player_pool: pd.DataFrame,
    lambda_val: float,
) -> pd.DataFrame:
    """
    Compute willingness-to-pay for each (position, rank) entry.

        wtp_price          = expected_points / λ
        flex_wtp_price     = flex_expected_points / λ   (RB/WR/TE only)

    Also adds a `roster_slot` label (e.g. "QB1", "RB3").
    """
    df = player_pool.copy()
    df["wtp_price"]      = (df["expected_points"]      / lambda_val).round(2)
    df["flex_wtp_price"] = (df["flex_expected_points"] / lambda_val).round(2)
    df["roster_slot"]    = df["position"] + df["positional_rank"].astype(str)
    return df


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def build_wtp_table(
    point_source: str = "historical",
    budget: int = SKILL_BUDGET,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    Build the full WTP table.

    Parameters
    ----------
    point_source : "historical" or "espn"
    budget       : skill-position budget (default $199, i.e. $200 − $1 DST)
    verbose      : print progress messages

    Returns
    -------
    DataFrame with columns:
        position, positional_rank, roster_slot,
        expected_points, flex_expected_points,
        method1_av, price,
        wtp_price, flex_wtp_price,
        lp_y, lp_z, lp_b   (LP variable values — fractional in the relaxation)
    """
    source_label = "ESPN 2026 projected" if point_source == "espn" else "historical avg (2021–2025)"
    if verbose:
        print(f"\n=== Willingness-to-Pay Calculator ===")
        print(f"  Point source : {source_label}")
        print(f"  Budget       : ${budget} (skill positions) + $1 DST = ${budget + 1} total")
        print(f"  Lineup slots : QB×1, RB×2, WR×3, TE×1, FLEX×1, Bench×{CROSS_BENCH_SLOTS}")

    # 1. Build points table
    if verbose:
        print("\nStep 1 — Building expected-points table…")
    points_table = build_points_table(point_source)
    if verbose:
        for pos in ["QB", "RB", "WR", "TE"]:
            n = len(points_table[points_table["position"] == pos])
            print(f"  {pos}: {n} ranks loaded")

    # 2. Attach Method 1 auction values as prices
    if verbose:
        print("\nStep 2 — Attaching Method 1 auction values as prices…")
    method1_avs = load_method1_auction_values()
    player_pool = attach_prices(points_table, method1_avs)

    # 3. Solve LP relaxation
    if verbose:
        print(f"\nStep 3 — Solving LP relaxation (budget=${budget})…")
    pool_with_duals, lambda_val = solve_lp_relaxation(player_pool, budget=budget)
    if verbose:
        print(f"  LP status : Optimal")
        print(f"  λ (shadow price of budget) = {lambda_val:.4f} pts/$")
        print(f"  Interpretation: each $1 of budget is worth {lambda_val:.2f} expected points")

    # 4. Compute WTP
    if verbose:
        print("\nStep 4 — Computing willingness-to-pay per slot…")
    wtp_table = compute_wtp(pool_with_duals, lambda_val)

    # Keep only starter + bench range (trim very deep ranks with no price signal)
    starters = wtp_table[wtp_table["positional_rank"] <= wtp_table["position"].map(STARTER_CUTOFFS)].copy()
    deep     = wtp_table[wtp_table["positional_rank"] > wtp_table["position"].map(STARTER_CUTOFFS)].copy()
    wtp_table = pd.concat([starters, deep], ignore_index=True)

    return wtp_table


def run_wtp(point_source: str = "historical", budget: int = SKILL_BUDGET) -> pd.DataFrame:
    """
    Build the WTP table, save output CSV and charts, and print a summary.

    Parameters
    ----------
    point_source : "historical" or "espn"
    budget       : skill-position budget (default $199)
    """
    try:
        from .visualize_par import plot_wtp_top20, plot_wtp_comparison
    except ImportError:
        sys.path.insert(0, str(_HERE))
        from visualize_par import plot_wtp_top20, plot_wtp_comparison

    wtp_table = build_wtp_table(point_source=point_source, budget=budget, verbose=True)

    # Save CSV
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    wtp_table.to_csv(WTP_CSV_PATH, index=False)
    print(f"\nWTP table saved to {WTP_CSV_PATH}")

    # --- Print starter-range summary ---
    source_label = "ESPN projected" if point_source == "espn" else "Hist avg"
    print(f"\n=== WTP Summary — Starter Slots ({source_label} points) ===")

    starter_table = wtp_table[
        wtp_table["positional_rank"] <= wtp_table["position"].map(STARTER_CUTOFFS)
    ].copy()

    # Top 20 by WTP
    top20 = starter_table.nlargest(20, "wtp_price").reset_index(drop=True)

    col_w_slot   = 8
    col_w_pts    = 12
    col_w_wtp    = 12
    col_w_m1     = 12
    col_w_diff   = 12

    header = (
        f"  {'Slot':<{col_w_slot}}  {f'{source_label} Pts':>{col_w_pts}}  "
        f"{'WTP ($)':>{col_w_wtp}}  {'Method1 AV':>{col_w_m1}}  {'Diff ($)':>{col_w_diff}}"
    )
    sep = "  " + "-" * (len(header) - 2)
    print(header)
    print(sep)
    for _, r in top20.iterrows():
        diff     = r["wtp_price"] - r["method1_av"]
        diff_str = f"+{diff:.1f}" if diff >= 0 else f"{diff:.1f}"
        print(
            f"  {r['roster_slot']:<{col_w_slot}}  "
            f"{r['expected_points']:>{col_w_pts}.1f}  "
            f"${r['wtp_price']:>{col_w_wtp - 1}.1f}  "
            f"${r['method1_av']:>{col_w_m1 - 1}.1f}  "
            f"{diff_str:>{col_w_diff}}"
        )

    # --- Charts ---
    print("\nGenerating charts…")
    suffix = "_espn" if point_source == "espn" else "_historical"
    plot_wtp_top20(wtp_table, suffix=suffix, source_label=source_label)
    plot_wtp_comparison(wtp_table, suffix=suffix, source_label=source_label)

    return wtp_table


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Goal 9 — Principled Willingness-to-Pay by roster slot.\n\n"
            "Computes shadow prices via LP relaxation of the lineup optimizer. "
            "By default uses 5-year historical average points (2021–2025); "
            "use --espn for ESPN 2026 projected points."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--espn",
        action="store_true",
        help="Use ESPN 2026 projected points instead of 5-year historical averages.",
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=SKILL_BUDGET,
        metavar="DOLLARS",
        help=f"Skill-position budget (default: ${SKILL_BUDGET}, i.e. $200 − $1 DST).",
    )
    args = parser.parse_args()

    source = "espn" if args.espn else "historical"
    run_wtp(point_source=source, budget=args.budget)
