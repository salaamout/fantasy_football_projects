"""
Goal 9: Principled "Willingness to Pay" (WTP) by roster slot.

Shadow prices are computed via LP relaxation of the lineup optimizer modelled
as a **12-team competitive market**:

    WTP(position, rank) = (par_points - slot_dual) / λ

where λ is the dual variable (shadow price) on the *market* budget constraint
in the LP relaxation.  λ has units of "points per dollar" and reflects the
true competitive market clearing rate (what it costs to outbid other teams),
not just what points are worth to a single team.

The LP simultaneously fills all 12 rosters (QB×12, RB×24, WR×36, etc.) using
a combined market budget of $199 × 12 = $2,388.  This forces mid-tier players
into the allocation and produces meaningful shadow prices for them.  The WTP
formula still outputs a single-team dollar value — there is no division by 12.
The slot dual already represents the per-slot scarcity premium.

Three expected-points sources are supported:
  historical  (default) — 5-year average half-PPR points (2021–2025)
  espn                  — ESPN 2026 projected points
  blended               — blended ESPN + Sleeper + historical projections
                           (see analysis/load_multi_source_projections.py)

League / lineup settings (single team)
    Budget              : $200 total, $1 reserved for DST → $199 skill budget
    Lineup slots        : QB×1, RB×2, WR×3, TE×1, FLEX×1 (RB/WR/TE)
    Bench               : 4 spots (RB/WR only; ≤3 RB, ≤2 WR; no QB/TE)
    DST                 : pre-assigned at $1 (not modelled in the LP)

Usage
    python -m analysis.willingness_to_pay               # historical points
    python -m analysis.willingness_to_pay --espn        # ESPN projected points
    python -m analysis.willingness_to_pay --blended     # blended projected points
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
BLENDED_DATA_PATH     = DATA_DIR / "blended_projected_values.csv"
METHOD1_AV_PATH       = DATA_DIR / "cross_position_ranking.csv"
WTP_CSV_PATH          = DATA_DIR / "willingness_to_pay.csv"

# Point-source display labels / output-filename suffixes
SOURCE_LABELS = {
    "historical": "historical avg (2021–2025)",
    "espn":       "ESPN 2026 projected",
    "blended":    "Blended (ESPN + Sleeper + historical)",
    "blended_sched": "Blended, schedule-adjusted (weeks 1–11)",
}
SOURCE_SUFFIXES = {
    "historical": "_historical",
    "espn":       "_espn",
    "blended":    "_blended",
    "blended_sched": "_blended_sched",
}

SKILL_BUDGET          = 199          # $200 − $1 for DST (single-team budget)
NUM_TEAMS             = 12           # league size — LP fills all 12 rosters simultaneously
MARKET_BUDGET         = SKILL_BUDGET * NUM_TEAMS   # $2,388 — calibrates λ to the competitive market rate
SKILL_POSITIONS       = {"QB", "RB", "WR", "TE"}

# Pool depth: how many players per position to include in the LP pool.
# HARD REQUIREMENT for a 12-team market: the pool must cover all 12 rosters
# including bench (QB×~20, RB×~60, WR×~60+, TE×~20).  If the pool is too
# shallow the LP will be infeasible because bench constraints can't be satisfied.
#   12 teams × (2 RB starters + 3 RB bench) = 60 RBs minimum
#   12 teams × (3 WR starters + 2 WR bench) = 60 WRs minimum
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

    Columns: position, positional_rank, expected_points, par_points,
             flex_expected_points, flex_par_points

    par_points = expected_points - replacement_baseline[position]
    where replacement_baseline is the avg points of the REPLACEMENT_RANKS player.
    """
    avg_points, avg_flex_points = build_avg_points_lookup(
        seasons=(2021, 2022, 2023, 2024, 2025)
    )

    # Compute positional replacement baselines
    replacement_pts = {
        pos: avg_points.get((pos, REPLACEMENT_RANKS[pos]), 0.0)
        for pos in POOL_DEPTH
    }

    rows = []
    for pos, depth in POOL_DEPTH.items():
        rep = replacement_pts[pos]
        for rank in range(1, depth + 1):
            ep   = avg_points.get((pos, rank))
            fep  = avg_flex_points.get((pos, rank)) if pos in FLEX_POSITIONS else 0.0
            if ep is None:
                break    # no data beyond this rank
            rows.append({
                "position":             pos,
                "positional_rank":      rank,
                "expected_points":      ep,
                "par_points":           ep - rep,
                "flex_expected_points": fep if fep is not None else 0.0,
                "flex_par_points":      (fep - rep) if (fep is not None and fep > 0) else 0.0,
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
            "par_points":           0.0,           # filled in after loop
            "flex_expected_points": fep,
            "flex_par_points":      0.0,           # filled in after loop
        })

    df_out = pd.DataFrame(rows)

    # Compute replacement baseline from the ESPN data itself
    for pos in df_out["position"].unique():
        rep_rank = REPLACEMENT_RANKS.get(pos)
        rep_rows = df_out[(df_out["position"] == pos) & (df_out["positional_rank"] == rep_rank)]
        rep_pts  = rep_rows["expected_points"].values[0] if not rep_rows.empty else 0.0
        mask = df_out["position"] == pos
        df_out.loc[mask, "par_points"]      = df_out.loc[mask, "expected_points"] - rep_pts
        df_out.loc[mask, "flex_par_points"] = df_out.loc[mask, "flex_expected_points"].apply(
            lambda x: (x - rep_pts) if x > 0 else 0.0
        )

    return df_out


def _build_blended_points_table(schedule_adjust: bool = False) -> pd.DataFrame:
    """
    Return a tidy DataFrame of blended (ESPN + Sleeper + historical) projected
    points indexed by (position, positional_rank), mirroring the schema used
    by `_build_espn_points_table`.

    Columns: position, positional_rank, player_name, expected_points,
             par_points, flex_expected_points, flex_par_points

    `player_name` is carried through so that auction-value pricing (Step 2)
    can join to ESPN's salary-cap data by name — the blended source's own
    positional_rank does not line up with ESPN's positional_rank, since it's
    re-ranked by the blended point value.

    Goal 13, Step 4: if `schedule_adjust` is True, points are re-ranked by
    `schedule_adjusted_points` (weeks 1–11 strength-of-schedule-adjusted
    per-player total, see `analysis/schedule_adjustment.py`) instead of the
    raw blended `projected_points`. `expected_points`/`par_points` etc. are
    then computed off this adjusted value, so positional ranks — and
    therefore the whole downstream WTP pricing — shift to reflect each
    player's specific early-season schedule difficulty.
    """
    if not BLENDED_DATA_PATH.exists():
        raise FileNotFoundError(
            f"Blended projections not found at {BLENDED_DATA_PATH}. "
            "Run `python -m analysis.load_multi_source_projections` first."
        )

    blended = pd.read_csv(BLENDED_DATA_PATH)
    blended = blended[blended["position"].isin(SKILL_POSITIONS)].copy()
    blended = blended.dropna(subset=["projected_points", "positional_rank"])

    points_col = "projected_points"
    if schedule_adjust:
        try:
            from .schedule_adjustment import compute_schedule_adjusted_points
        except ImportError:
            sys.path.insert(0, str(_HERE))
            from schedule_adjustment import compute_schedule_adjusted_points

        blended = compute_schedule_adjusted_points(blended)
        points_col = "schedule_adjusted_points"

    # Re-rank per position by the chosen points column (positional_rank from
    # the source CSV no longer applies once we're using a schedule-adjusted
    # value, since players can move up/down based on early-season matchups).
    blended = blended.sort_values(["position", points_col], ascending=[True, False])
    blended["positional_rank"] = blended.groupby("position").cumcount() + 1
    blended = blended.reset_index(drop=True)

    rows = []
    for _, row in blended.iterrows():
        pos  = row["position"]
        rank = int(row["positional_rank"])
        ep   = float(row[points_col])
        fep  = ep if pos in FLEX_POSITIONS else 0.0
        rows.append({
            "position":             pos,
            "positional_rank":      rank,
            "player_name":          row["player_name"],
            "expected_points":      ep,
            "par_points":           0.0,           # filled in after loop
            "flex_expected_points": fep,
            "flex_par_points":      0.0,           # filled in after loop
        })

    df_out = pd.DataFrame(rows)

    # Compute replacement baseline from the blended data itself
    for pos in df_out["position"].unique():
        rep_rank = REPLACEMENT_RANKS.get(pos)
        rep_rows = df_out[(df_out["position"] == pos) & (df_out["positional_rank"] == rep_rank)]
        rep_pts  = rep_rows["expected_points"].values[0] if not rep_rows.empty else 0.0
        mask = df_out["position"] == pos
        df_out.loc[mask, "par_points"]      = df_out.loc[mask, "expected_points"] - rep_pts
        df_out.loc[mask, "flex_par_points"] = df_out.loc[mask, "flex_expected_points"].apply(
            lambda x: (x - rep_pts) if x > 0 else 0.0
        )

    return df_out


def build_points_table(point_source: str = "historical", schedule_adjust: bool = False) -> pd.DataFrame:
    """
    Build the expected-points table for the LP.

    Parameters
    ----------
    point_source : "historical" (default), "espn", or "blended"
    schedule_adjust : if True (only valid when point_source == "blended"),
        rank/price players off `schedule_adjusted_points` (weeks 1–11
        strength-of-schedule adjusted, see `analysis/schedule_adjustment.py`)
        instead of the raw blended `projected_points`.

    Returns
    -------
    DataFrame with columns:
        position, positional_rank, expected_points, flex_expected_points
    """
    if schedule_adjust and point_source != "blended":
        raise ValueError(
            "schedule_adjust=True is only valid with point_source='blended' "
            "(historical/ESPN tables are position-rank aggregates with no "
            "per-player identity to attach a schedule to)."
        )
    if point_source == "espn":
        return _build_espn_points_table()
    elif point_source == "historical":
        return _build_historical_points_table()
    elif point_source == "blended":
        return _build_blended_points_table(schedule_adjust=schedule_adjust)
    else:
        raise ValueError(
            f"Unknown point_source: {point_source!r}. Choose 'historical', 'espn', or 'blended'."
        )


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


def load_espn_auction_values() -> dict[tuple[str, int], float]:
    """
    Load ESPN estimated auction values from espn_projected_values.csv.
    Returns a dict keyed by (position, positional_rank) → auction_value.
    """
    df = pd.read_csv(ESPN_DATA_PATH)
    df = df[df["position"].isin(SKILL_POSITIONS)]
    return {
        (row["position"], int(row["positional_rank"])): float(row["auction_value"])
        for _, row in df.iterrows()
    }


def load_espn_auction_values_by_name() -> dict[str, float]:
    """
    Load ESPN estimated auction values keyed by player_name instead of
    (position, positional_rank). Needed for the blended source, whose
    positional_rank is re-derived from the blended point value and no longer
    lines up with ESPN's own positional_rank.
    """
    df = pd.read_csv(ESPN_DATA_PATH)
    df = df[df["position"].isin(SKILL_POSITIONS)]
    return {row["player_name"]: float(row["auction_value"]) for _, row in df.iterrows()}


def attach_prices(
    points_table: pd.DataFrame,
    method1_avs: dict[tuple[str, int], float],
    min_price: float = 1.0,
    name_avs: dict[str, float] | None = None,
) -> pd.DataFrame:
    """
    Attach a `price` column to the points table using auction values.

    By default prices are looked up by (position, positional_rank) via
    `method1_avs`. If `name_avs` is provided and the table has a
    `player_name` column, prices are instead looked up by player name — this
    is required for the blended source (see `load_espn_auction_values_by_name`).

    Players beyond the starter range / without a match default to min_price.
    """
    df = points_table.copy()
    if name_avs is not None and "player_name" in df.columns:
        df["method1_av"] = df["player_name"].map(name_avs).fillna(0.0)
    else:
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
    num_teams: int = NUM_TEAMS,
    overrides: dict | None = None,
) -> tuple[pd.DataFrame, float, dict[str, float]]:
    """
    Solve the LP relaxation of the lineup optimizer (continuous vars ∈ [0,1]).

    Models a *12-team competitive market* by scaling the budget and all slot
    constraints by ``num_teams``.  This forces the LP to allocate players
    across all 12 rosters simultaneously, so mid-tier players become binding
    and the shadow prices reflect true market clearing rates.

    Budget used in LP  : budget × num_teams  (e.g. $199 × 12 = $2,388)
    Slot counts in LP  : LINEUP_SLOTS[pos] × num_teams  (e.g. QB×12, RB×24, …)
    Bench count in LP  : CROSS_BENCH_SLOTS × num_teams  (e.g. 4×12 = 48)

    The WTP formula is applied player-by-player *unchanged*:
        WTP_i (starter) = (par_points_i − slot_duals[pos]) / λ
        WTP_i (FLEX)    = (flex_par_points_i − slot_duals["FLEX"]) / λ
    There is no division by num_teams — the slot dual already captures the
    per-slot scarcity premium, and the scaled budget ensures λ reflects the
    true market clearing rate.

    Variables for each player i:
        y_i ∈ [0,1]  — fills a dedicated positional starter slot
        z_i ∈ [0,1]  — fills the FLEX slot (RB/WR/TE only)
        b_i ∈ [0,1]  — fills a bench slot (no QB/TE; ≤3 RB, ≤2 WR)

    Parameters
    ----------
    overrides : dict | None
        Goal 14 Step 4 (live draft recompute). When provided, replaces the
        ``budget × num_teams`` / ``LINEUP_SLOTS[pos] × num_teams`` market
        sizing with explicit remaining-market quantities — used when
        re-solving the LP over only the still-available players mid-draft,
        after some slots/budget have already been consumed league-wide.
        Recognized keys (all required if ``overrides`` is given):
        ``market_budget``, ``qb_slots``, ``rb_slots``, ``wr_slots``,
        ``te_slots``, ``flex_slots``, ``bench_total``. ``bench_rb_max`` /
        ``bench_wr_max`` are optional (pool-aware caps are still computed
        and the tighter of the two is used).

    Returns
    -------
    (player_pool_with_duals, lambda_shadow_price, slot_duals)

    lambda_shadow_price : float
        Dual variable on the budget constraint (points per dollar).

    slot_duals : dict[str, float]
        Dual variables for each positional slot constraint and the FLEX slot.
        Keys: "QB", "RB", "WR", "TE", "FLEX".
    """
    players = player_pool.to_dict("records")
    n = len(players)

    # --- Resolve market sizing: overrides (live recompute) or num_teams (batch) ---
    if overrides is not None:
        market_budget       = overrides["market_budget"]
        qb_need              = overrides["qb_slots"]
        rb_starters_needed   = overrides["rb_slots"]
        wr_starters_needed   = overrides["wr_slots"]
        te_need              = overrides["te_slots"]
        flex_need            = overrides["flex_slots"]
        bench_total_needed   = overrides["bench_total"]
        bench_rb_max_override = overrides.get("bench_rb_max")
        bench_wr_max_override = overrides.get("bench_wr_max")
    else:
        market_budget        = budget * num_teams
        qb_need              = LINEUP_SLOTS["QB"] * num_teams
        rb_starters_needed   = LINEUP_SLOTS["RB"] * num_teams
        wr_starters_needed   = LINEUP_SLOTS["WR"] * num_teams
        te_need              = LINEUP_SLOTS["TE"] * num_teams
        flex_need            = LINEUP_SLOTS["FLEX"] * num_teams
        bench_total_needed   = CROSS_BENCH_SLOTS * num_teams
        bench_rb_max_override = None
        bench_wr_max_override = None

    # --- Pre-flight pool depth validation ---
    # The LP assigns each player to at most one slot.  For the market LP to
    # be feasible the pool must cover all remaining starter + bench slots.
    pool_counts = {
        pos: sum(1 for p in players if p["position"] == pos)
        for pos in ("QB", "RB", "WR", "TE")
    }

    # Pool-aware bench limits
    avail_rb_bench = pool_counts.get("RB", 0) - rb_starters_needed
    avail_wr_bench = pool_counts.get("WR", 0) - wr_starters_needed
    max_bench_rb = min(bench_rb_max_override if bench_rb_max_override is not None else 3 * num_teams, max(avail_rb_bench, 0))
    max_bench_wr = min(bench_wr_max_override if bench_wr_max_override is not None else 2 * num_teams, max(avail_wr_bench, 0))

    errors: list[str] = []
    # Starters-only minimums (QB / TE have no bench)
    for pos, need in [
        ("QB", qb_need),
        ("TE", te_need),
        ("RB", rb_starters_needed),
        ("WR", wr_starters_needed),
    ]:
        if pool_counts.get(pos, 0) < need:
            errors.append(f"  {pos}: need ≥{need} starters, have {pool_counts.get(pos, 0)}")

    # Bench feasibility: can we fill bench_total_needed from the available pool?
    if not errors and max_bench_rb + max_bench_wr < bench_total_needed:
        errors.append(
            f"  Bench: need {bench_total_needed} bench spots "
            f"(RB+WR), but pool only supports "
            f"max {max_bench_rb} RB bench + {max_bench_wr} WR bench "
            f"= {max_bench_rb + max_bench_wr}.\n"
            f"  Need ≥{rb_starters_needed + bench_total_needed - max_bench_wr} RBs "
            f"(have {pool_counts.get('RB', 0)}) and "
            f"≥{wr_starters_needed + bench_total_needed - max_bench_rb} WRs "
            f"(have {pool_counts.get('WR', 0)})."
        )

    if errors:
        raise ValueError(
            f"Player pool is too shallow for a {num_teams}-team LP.\n"
            + "\n".join(errors)
            + "\nIncrease POOL_DEPTH or provide a deeper ESPN CSV."
        )

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

    # Objective: maximise PAR (points above replacement) for starter slots.
    # Using PAR instead of raw points ensures that positional replacement baselines
    # are factored out, so the shadow price correctly reflects marginal value of
    # spending an extra dollar (rather than being inflated by QB raw-point totals).
    prob += pulp.lpSum(
        p["par_points"]      * y[i] +
        p["flex_par_points"] * z[i]
        for i, p in enumerate(players)
    )

    # Named budget constraint (we need its dual)
    # `market_budget` is either `budget × num_teams` (batch mode) or the
    # explicit remaining-market budget passed via `overrides` (live recompute).
    budget_constraint = pulp.lpSum(
        p["price"] * (y[i] + z[i] + b[i])
        for i, p in enumerate(players)
    ) <= market_budget
    prob += budget_constraint, "budget"

    # Positional slot counts — either scaled by num_teams (batch) or the
    # explicit remaining quotas from `overrides` (live recompute).
    prob += (pulp.lpSum(y[i] for i, p in enumerate(players) if p["position"] == "QB")
             == qb_need), "qb_slots"
    prob += (pulp.lpSum(y[i] for i, p in enumerate(players) if p["position"] == "RB")
             == rb_starters_needed), "rb_slots"
    prob += (pulp.lpSum(y[i] for i, p in enumerate(players) if p["position"] == "WR")
             == wr_starters_needed), "wr_slots"
    prob += (pulp.lpSum(y[i] for i, p in enumerate(players) if p["position"] == "TE")
             == te_need), "te_slots"

    # FLEX slots
    prob += pulp.lpSum(z) == flex_need, "flex_slot"

    # Bench
    prob += pulp.lpSum(b) == bench_total_needed, "bench_count"

    # Bench composition.
    # Cap bench maxima against actual pool size so we never add an infeasible
    # constraint when the pool is shallower than ideal.  The effective
    # maximum bench spots for a position is (pool_count − starters), ensuring
    # the LP can always satisfy the bench_count equality.
    n_rbs = sum(1 for p in players if p["position"] == "RB")
    n_wrs = sum(1 for p in players if p["position"] == "WR")
    rb_starters = rb_starters_needed
    wr_starters = wr_starters_needed
    bench_total = bench_total_needed
    default_bench_rb_cap = bench_rb_max_override if bench_rb_max_override is not None else 3 * num_teams
    default_bench_wr_cap = bench_wr_max_override if bench_wr_max_override is not None else 2 * num_teams

    # Pool-aware caps: no more bench than (pool − starters) per position, and
    # no more than what still leaves enough room for the other position's bench.
    bench_rb_max = min(default_bench_rb_cap, n_rbs - rb_starters)
    bench_wr_max = min(default_bench_wr_cap, n_wrs - wr_starters)
    # Ensure both mins are satisfiable given pool-aware maxima
    bench_rb_min = max(0, bench_total - bench_wr_max)
    bench_wr_min = max(0, bench_total - bench_rb_max)

    prob += (pulp.lpSum(b[i] for i, p in enumerate(players) if p["position"] == "RB")
             <= bench_rb_max), "bench_rb_max"
    prob += (pulp.lpSum(b[i] for i, p in enumerate(players) if p["position"] == "WR")
             <= bench_wr_max), "bench_wr_max"
    prob += (pulp.lpSum(b[i] for i, p in enumerate(players) if p["position"] == "RB")
             >= bench_rb_min), "bench_rb_min"
    prob += (pulp.lpSum(b[i] for i, p in enumerate(players) if p["position"] == "WR")
             >= bench_wr_min), "bench_wr_min"

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
    lambda_lp = float(budget_dual)
    if lambda_lp <= 0:
        raise RuntimeError(
            f"Budget shadow price is {lambda_lp:.4f} ≤ 0. "
            "The budget constraint may not be binding — try increasing pool depth or reducing budget."
        )

    # Attach LP variable values to player pool for inspection
    pool = player_pool.copy()
    pool["lp_y"] = [pulp.value(y[i]) for i in range(n)]
    pool["lp_z"] = [pulp.value(z[i]) for i in range(n)]
    pool["lp_b"] = [pulp.value(b[i]) for i in range(n)]

    # --- Extract slot constraint duals ---
    # These represent the shadow price of each positional starting slot.
    # WTP_i (starter role) = (par_points_i - slot_duals[pos]) / λ
    # Intuitively: the slot dual is the "floor value" that the marginal player
    # at that position contributes per slot.  Only players above that floor
    # are worth a non-trivial starting premium.
    slot_duals: dict[str, float] = {
        "QB":   float(prob.constraints["qb_slots"].pi  or 0.0),
        "RB":   float(prob.constraints["rb_slots"].pi  or 0.0),
        "WR":   float(prob.constraints["wr_slots"].pi  or 0.0),
        "TE":   float(prob.constraints["te_slots"].pi  or 0.0),
        "FLEX": float(prob.constraints["flex_slot"].pi or 0.0),
    }

    # --- Calibrate λ to the competitive equilibrium rate ---
    #
    # The LP shadow price (λ_lp) is set by the FRACTIONAL BOUNDARY PLAYER —
    # the most expensive-per-PAR player the LP is forced to include due to slot
    # equality constraints.  When that player has a low par/price ratio (e.g.,
    # an overpaid mid-tier RB), λ_lp is pulled below the true market exchange
    # rate, inflating WTP for all other players by 30–50%.
    #
    # The correct equilibrium λ (λ_eq) comes from the market-clearing condition:
    # at equilibrium, every drafted starter satisfies  par_i = λ × price_i + μ_pos.
    # Summing over all N allocated starters:
    #   Σ par_i  = λ_eq × Σ price_i  + Σ μ_pos_i
    #   λ_eq = (Σ par_i − Σ μ_pos_i) / Σ price_i
    #        = (total_LP_par − total_slot_dual_contrib) / total_starter_price
    #
    # This is the average points-per-dollar exchange rate across the full
    # allocation (not just the marginal player), and it is what each team
    # actually "pays" per PAR point in the competitive market.
    n_pos_slots = {
        "QB":   qb_need,
        "RB":   rb_starters_needed,
        "WR":   wr_starters_needed,
        "TE":   te_need,
        "FLEX": flex_need,
    }
    total_slot_dual_contrib = sum(slot_duals[pos] * n_pos_slots[pos] for pos in n_pos_slots)
    total_par_starters = float(
        (pool["par_points"]      * pool["lp_y"] +
         pool["flex_par_points"] * pool["lp_z"]).sum()
    )
    total_price_starters = float(
        (pool["price"] * (pool["lp_y"] + pool["lp_z"])).sum()
    )

    if total_price_starters <= 0:
        raise RuntimeError("Total price of allocated starters is zero — LP allocation is degenerate.")

    lambda_eq = (total_par_starters - total_slot_dual_contrib) / total_price_starters

    if lambda_eq <= 0:
        # Fall back to LP shadow price with a warning
        import warnings
        warnings.warn(
            f"Calibrated equilibrium λ = {lambda_eq:.4f} ≤ 0; "
            f"falling back to LP shadow price λ = {lambda_lp:.4f}.",
            RuntimeWarning,
        )
        lambda_eq = lambda_lp

    return pool, lambda_eq, slot_duals


# ---------------------------------------------------------------------------
# Step 4 – Compute WTP and assemble output table
# ---------------------------------------------------------------------------

def compute_wtp(
    player_pool: pd.DataFrame,
    lambda_val: float,
    slot_duals: dict[str, float],
) -> pd.DataFrame:
    """
    Compute willingness-to-pay for each (position, rank) entry using LP duality.

    The correct WTP for a player accounts for the scarcity of starting slots,
    not just their raw PAR.  From LP complementary slackness:

        WTP_i (starter role) = (par_points_i − μ_pos_slot) / λ
        WTP_i (FLEX role)    = (flex_par_points_i − μ_flex_slot) / λ

    where μ_pos_slot and μ_flex_slot are the dual variables (shadow prices) of
    the positional slot constraints.  The dual captures the "floor value" that
    the LP's marginal player at each position provides per slot.  Only players
    above that floor deserve a positive premium.

    A player's final WTP is the best they can do across all roles they're
    eligible for, floored at $1 (min bid):

        wtp_price = max(starter_wtp, flex_wtp, 1.0)

    Also adds a `roster_slot` label (e.g. "QB1", "RB3").
    """
    df = player_pool.copy()

    def _starter_wtp(row: pd.Series) -> float:
        return (row["par_points"] - slot_duals[row["position"]]) / lambda_val

    def _flex_wtp(row: pd.Series) -> float:
        if row["position"] not in FLEX_POSITIONS:
            return 0.0
        return (row["flex_par_points"] - slot_duals["FLEX"]) / lambda_val

    df["starter_wtp"] = df.apply(_starter_wtp, axis=1).round(2)
    df["flex_wtp"]    = df.apply(_flex_wtp,    axis=1).round(2)

    # Best role WTP, floored at $1
    df["wtp_price"] = df[["starter_wtp", "flex_wtp"]].max(axis=1).clip(lower=1.0).round(2)

    # Keep flex_wtp_price column for backward compat with output tables
    df["flex_wtp_price"] = df["flex_wtp"]
    df["roster_slot"]    = df["position"] + df["positional_rank"].astype(str)
    return df


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def _effective_source_key(point_source: str, schedule_adjust: bool = False) -> str:
    """
    Resolve the SOURCE_LABELS/SOURCE_SUFFIXES key to use, accounting for the
    Goal 13 `--schedule-adjust` flag (only meaningful with point_source ==
    "blended"): produces "blended_sched" so schedule-adjusted runs write to
    parallel output files instead of clobbering the unadjusted blended ones.
    """
    if schedule_adjust and point_source == "blended":
        return "blended_sched"
    return point_source


def build_wtp_table(
    point_source: str = "historical",
    budget: int = SKILL_BUDGET,
    verbose: bool = True,
    schedule_adjust: bool = False,
) -> pd.DataFrame:
    """
    Build the full WTP table.

    Parameters
    ----------
    point_source : "historical", "espn", or "blended"
    budget       : skill-position budget (default $199, i.e. $200 − $1 DST)
    verbose      : print progress messages
    schedule_adjust : Goal 13, Step 4 — only valid with point_source="blended".
        Ranks/prices players off `schedule_adjusted_points` (weeks 1–11
        strength-of-schedule adjusted) instead of raw blended projections.

    Returns
    -------
    DataFrame with columns:
        position, positional_rank, roster_slot,
        expected_points, flex_expected_points,
        method1_av, price,
        wtp_price, flex_wtp_price,
        lp_y, lp_z, lp_b   (LP variable values — fractional in the relaxation)
    """
    source_key = _effective_source_key(point_source, schedule_adjust)
    source_label = SOURCE_LABELS.get(source_key, source_key)
    if verbose:
        print(f"\n=== Willingness-to-Pay Calculator (12-Team Market) ===")
        print(f"  Point source  : {source_label}")
        print(f"  Single-team   : ${budget} skill + $1 DST = ${budget + 1} total")
        print(f"  Market budget : ${budget} × {NUM_TEAMS} teams = ${budget * NUM_TEAMS} (calibrates λ)")
        print(f"  LP slots      : QB×{NUM_TEAMS}, RB×{2*NUM_TEAMS}, WR×{3*NUM_TEAMS}, TE×{NUM_TEAMS}, FLEX×{NUM_TEAMS}, Bench×{CROSS_BENCH_SLOTS*NUM_TEAMS}")
        print(f"  WTP output    : single-team dollar values (no division by {NUM_TEAMS})")

    # 1. Build points table
    if verbose:
        print("\nStep 1 — Building expected-points table…")
    points_table = build_points_table(point_source, schedule_adjust=schedule_adjust)
    if verbose:
        for pos in ["QB", "RB", "WR", "TE"]:
            n = len(points_table[points_table["position"] == pos])
            print(f"  {pos}: {n} ranks loaded")

    # 2. Attach auction values as prices.
    # In a 12-team competitive market, prices must reflect what the market actually
    # charges — i.e., ESPN auction values, which are calibrated to real 12-team
    # auction drafts.  Method 1 values sum to only ~$199 per team / ~$2,354 for
    # the whole pool, which is LESS than the $2,388 market budget, making the
    # budget constraint non-binding and λ = 0.  ESPN values sum to ~$2,527 and
    # produce a binding budget constraint so that λ correctly reflects the
    # competitive market clearing rate.
    #
    # For the "blended" source, positional_rank no longer lines up with ESPN's
    # own positional_rank (it's re-derived from the blended point value), so
    # prices must be joined by player_name instead of (position, rank).
    if verbose:
        print("\nStep 2 — Attaching ESPN auction values as prices (competitive market calibration)…")
    method1_avs = load_method1_auction_values()   # still needed for the output column
    if point_source == "blended":
        name_avs = load_espn_auction_values_by_name()
        player_pool = attach_prices(points_table, method1_avs, name_avs=name_avs)
    else:
        price_avs = load_espn_auction_values()
        player_pool = attach_prices(points_table, price_avs)
    # Restore the method1_av column to real Method 1 values (attach_prices names
    # its lookup column "method1_av" regardless of which price source was passed).
    if True:
        player_pool["method1_av"] = player_pool.apply(
            lambda r: method1_avs.get((r["position"], int(r["positional_rank"])), 0.0),
            axis=1,
        )

    # 3. Solve LP relaxation
    if verbose:
        print(f"\nStep 3 — Solving LP relaxation (market budget=${budget * NUM_TEAMS}, {NUM_TEAMS} teams)…")
    pool_with_duals, lambda_val, slot_duals = solve_lp_relaxation(player_pool, budget=budget)
    if verbose:
        print(f"  LP status : Optimal")
        print(f"  λ (equilibrium calibrated) = {lambda_val:.4f} pts/$")
        print(f"  Interpretation: each $1 of budget is worth {lambda_val:.2f} expected points")
        print(f"  Slot duals (μ): " + ", ".join(f"{k}={v:.2f}" for k, v in slot_duals.items()))

    # 4. Compute WTP
    if verbose:
        print("\nStep 4 — Computing willingness-to-pay per slot…")
    wtp_table = compute_wtp(pool_with_duals, lambda_val, slot_duals)

    # Keep only starter + bench range (trim very deep ranks with no price signal)
    starters = wtp_table[wtp_table["positional_rank"] <= wtp_table["position"].map(STARTER_CUTOFFS)].copy()
    deep     = wtp_table[wtp_table["positional_rank"] > wtp_table["position"].map(STARTER_CUTOFFS)].copy()
    wtp_table = pd.concat([starters, deep], ignore_index=True)

    # Attach ESPN auction values for comparison plots.
    # For "blended" source, positional_rank doesn't line up with ESPN's own
    # rank, so join by player_name instead.
    if point_source == "blended" and "player_name" in wtp_table.columns:
        espn_avs_by_name = load_espn_auction_values_by_name()
        wtp_table["espn_av"] = wtp_table["player_name"].map(espn_avs_by_name).fillna(0.0)
    else:
        espn_avs = load_espn_auction_values()
        wtp_table["espn_av"] = wtp_table.apply(
            lambda r: espn_avs.get((r["position"], int(r["positional_rank"])), 0.0),
            axis=1,
        )

    return wtp_table


def save_wtp_position_tables(
    wtp_table: pd.DataFrame,
    point_source: str = "historical",
    budget: int = SKILL_BUDGET,
    schedule_adjust: bool = False,
) -> Path:
    """
    Write a single Markdown document containing a WTP table for every slot of
    every position (QB, RB, WR, TE), ordered by positional_rank.

    Saved to: output/wtp_by_position_<source>.md

    Returns the path of the saved file.
    """
    source_key   = _effective_source_key(point_source, schedule_adjust)
    source_label = SOURCE_LABELS.get(source_key, source_key)
    suffix       = SOURCE_SUFFIXES.get(source_key, f"_{source_key}")
    out_path     = OUTPUT_DIR / f"wtp_by_position{suffix}.md"

    lines: list[str] = []
    lines.append(f"# Willingness-to-Pay by Position and Roster Slot")
    lines.append(f"")
    lines.append(f"**Point source:** {source_label}  ")
    lines.append(f"**Skill budget:** ${budget} ($1 reserved for DST → ${budget + 1} total)  ")
    lines.append(f"**Lineup:** QB×1, RB×2, WR×3, TE×1, FLEX×1, Bench×{CROSS_BENCH_SLOTS}  ")
    lines.append(f"")
    lines.append("---")
    lines.append("")

    # Helper: build a fixed-width Markdown table from a list of row dicts.
    # col_specs is a list of (header, key, align) where align is 'l', 'r', or 'c'.
    def _md_table(col_specs: list[tuple[str, str, str]], rows: list[dict]) -> list[str]:
        # Collect all cell strings first so we can measure max widths.
        headers = [h for h, _, _ in col_specs]
        col_cells: list[list[str]] = [
            [h] + [str(row[k]) for row in rows]
            for h, k, _ in col_specs
        ]
        widths = [max(len(c) for c in col) for col in col_cells]

        def _pad(text: str, w: int, align: str) -> str:
            if align == "r":
                return text.rjust(w)
            elif align == "c":
                return text.center(w)
            else:
                return text.ljust(w)

        def _row_line(cells: list[str]) -> str:
            padded = [_pad(c, widths[i], col_specs[i][2]) for i, c in enumerate(cells)]
            return "| " + " | ".join(padded) + " |"

        sep_parts = []
        for w, (_, _, align) in zip(widths, col_specs):
            if align == "r":
                sep_parts.append("-" * (w - 1) + ":")
            elif align == "c":
                sep_parts.append(":" + "-" * (w - 2) + ":")
            else:
                sep_parts.append("-" * w)
        sep_line = "| " + " | ".join(
            p.ljust(widths[i]) if col_specs[i][2] == "l" else p.rjust(widths[i])
            for i, p in enumerate(sep_parts)
        ) + " |"

        result = [_row_line(headers), sep_line]
        for row in rows:
            result.append(_row_line([str(row[k]) for _, k, _ in col_specs]))
        return result

    position_order = ["QB", "RB", "WR", "TE"]
    for pos in position_order:
        pos_df = (
            wtp_table[wtp_table["position"] == pos]
            .sort_values("positional_rank")
            .reset_index(drop=True)
        )
        if pos_df.empty:
            continue

        starter_cutoff = STARTER_CUTOFFS.get(pos, 0)
        lines.append(f"## {pos}")
        lines.append("")

        # Build row dicts with pre-formatted strings
        table_rows: list[dict] = []
        for _, r in pos_df.iterrows():
            wtp      = r["wtp_price"]
            m1_av    = r["method1_av"]
            diff     = wtp - m1_av
            flex_wtp = r["flex_wtp_price"] if pos in FLEX_POSITIONS else None
            table_rows.append({
                "slot":     r["roster_slot"],
                "exp_pts":  f"{r['expected_points']:.1f}",
                "par_pts":  f"{r['par_points']:.1f}",
                "wtp":      f"{wtp:.1f}",
                "flex_wtp": f"{flex_wtp:.1f}" if flex_wtp is not None else "—",
                "m1_av":    f"{m1_av:.1f}",
                "diff":     f"+{diff:.1f}" if diff >= 0 else f"{diff:.1f}",
                "note":     "starter" if int(r["positional_rank"]) <= starter_cutoff else "bench/deep",
            })

        col_specs = [
            ("Slot",         "slot",     "l"),
            ("Exp Pts",      "exp_pts",  "r"),
            ("PAR Pts",      "par_pts",  "r"),
            ("WTP ($)",      "wtp",      "r"),
            ("Flex WTP ($)", "flex_wtp", "r"),
            ("Method1 AV",   "m1_av",    "r"),
            ("Diff ($)",     "diff",     "r"),
            ("Notes",        "note",     "l"),
        ]
        lines.extend(_md_table(col_specs, table_rows))
        lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def run_wtp(point_source: str = "historical", budget: int = SKILL_BUDGET, schedule_adjust: bool = False) -> pd.DataFrame:
    """
    Build the WTP table, save output CSV and charts, and print a summary.

    Parameters
    ----------
    point_source : "historical", "espn", or "blended"
    budget       : skill-position budget (default $199)
    schedule_adjust : Goal 13, Step 4 — only valid with point_source="blended".
        Writes to parallel "_blended_sched" output files instead of the
        unadjusted blended ones.
    """
    try:
        from .visualize_par import (
            plot_wtp_top20, plot_wtp_comparison, plot_wtp_vs_espn,
            plot_wtp_vs_espn_interactive,
        )
    except ImportError:
        sys.path.insert(0, str(_HERE))
        from visualize_par import (
            plot_wtp_top20, plot_wtp_comparison, plot_wtp_vs_espn,
            plot_wtp_vs_espn_interactive,
        )

    wtp_table = build_wtp_table(
        point_source=point_source, budget=budget, verbose=True, schedule_adjust=schedule_adjust
    )

    source_key = _effective_source_key(point_source, schedule_adjust)
    suffix     = SOURCE_SUFFIXES.get(source_key, f"_{source_key}")

    # Save CSV (schedule-adjusted runs write to a parallel file so they don't
    # clobber the unadjusted blended WTP CSV).
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = DATA_DIR / f"willingness_to_pay{suffix}.csv" if schedule_adjust else WTP_CSV_PATH
    wtp_table.to_csv(csv_path, index=False)
    print(f"\nWTP table saved to {csv_path}")

    # Save per-position tables document
    doc_path = save_wtp_position_tables(
        wtp_table, point_source=point_source, budget=budget, schedule_adjust=schedule_adjust
    )
    print(f"WTP position tables saved to {doc_path}")

    # --- Print starter-range summary ---
    source_label = SOURCE_LABELS.get(source_key, source_key)
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
    plot_wtp_top20(wtp_table, suffix=suffix, source_label=source_label)
    plot_wtp_comparison(wtp_table, suffix=suffix, source_label=source_label)
    plot_wtp_vs_espn(wtp_table, suffix=suffix, source_label=source_label)
    plot_wtp_vs_espn_interactive(wtp_table, suffix=suffix, source_label=source_label)

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
            "use --espn for ESPN 2026 projected points, or --blended for the "
            "blended ESPN + Sleeper + historical projection."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument(
        "--espn",
        action="store_true",
        help="Use ESPN 2026 projected points instead of 5-year historical averages.",
    )
    source_group.add_argument(
        "--blended",
        action="store_true",
        help=(
            "Use the blended ESPN + Sleeper + historical projection "
            "(data/blended_projected_values.csv). "
            "Run `python -m analysis.load_multi_source_projections` first if it's missing."
        ),
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=SKILL_BUDGET,
        metavar="DOLLARS",
        help=f"Skill-position budget (default: ${SKILL_BUDGET}, i.e. $200 − $1 DST).",
    )
    parser.add_argument(
        "--schedule-adjust",
        action="store_true",
        help=(
            "Goal 13: re-rank/price blended players off weeks 1–11 "
            "strength-of-schedule-adjusted points instead of raw blended "
            "projections. Only valid together with --blended. Writes to "
            "parallel '_blended_sched' output files."
        ),
    )
    args = parser.parse_args()

    if args.schedule_adjust and not args.blended:
        parser.error("--schedule-adjust is only valid together with --blended.")

    if args.blended:
        source = "blended"
    elif args.espn:
        source = "espn"
    else:
        source = "historical"
    run_wtp(point_source=source, budget=args.budget, schedule_adjust=args.schedule_adjust)
