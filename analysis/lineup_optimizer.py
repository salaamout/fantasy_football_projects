"""
Goal 5 Part 2: Lineup Value Optimizer
Uses Ringer 2026 auction values as costs and historical average PAR-per-slot
as expected value to find the best starting lineup within a budget.

Pass --waiver (or -w) to use outside-of-bench / waiver-wire replacement level
instead of the default starter replacement level.

  Starter replacement ranks  : QB13, RB32, WR42, TE13  | flex rank 85
  Waiver  replacement ranks  : QB19, RB56, WR66, TE19  | flex rank 139
    (waiver = first player NOT rostered on any of the 12 teams' bench)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
import pandas as pd
import pulp

# Allow imports from the analysis directory when run directly
try:
    from .load_fantasy_data import load_and_clean_data
    from .calculate_par import calculate_par, REPLACEMENT_RANKS, build_avg_points_lookup
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    from load_fantasy_data import load_and_clean_data
    from calculate_par import calculate_par, REPLACEMENT_RANKS, build_avg_points_lookup

RANKINGS_PATH = Path(__file__).parent.parent / "data" / "ringer_2026_rankings.csv"
BUDGET        = 200
LINEUP_SLOTS  = {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "FLEX": 1}
FLEX_POSITIONS = {"RB", "WR", "TE"}

# Starter replacement level
# 12*(2 RB + 3 WR + 1 TE) = 72 starters + 12 FLEX = 84 total → rank 85
FLEX_REPLACEMENT_RANK = 85

# Outside-of-bench (waiver-wire) replacement level
# Starters + bench across 12 teams + 1:
#   QB:  12 + 6  + 1 = 19   RB: 31 + 24 + 1 = 56
#   WR:  41 + 24 + 1 = 66   TE: 12 + 6  + 1 = 19
# Combined flex-eligible (RB55 + WR65 + TE18) = 138 → rank 139
REPLACEMENT_RANKS_WAIVER = {"QB": 19, "RB": 56, "WR": 66, "TE": 19, "DST": 13}
FLEX_REPLACEMENT_RANK_WAIVER = 139


def build_avg_par_lookup(
    seasons=(2020, 2021, 2022, 2023, 2024),
    replacement_ranks: dict | None = None,
    flex_replacement_rank: int = FLEX_REPLACEMENT_RANK,
) -> tuple[dict, dict]:
    """
    Returns two dicts, both keyed by (position, positional_rank):
      - pos_lookup:  avg PAR above positional replacement
      - flex_lookup: avg PAR above combined RB/WR/TE flex replacement

    Pass replacement_ranks=REPLACEMENT_RANKS_WAIVER and
    flex_replacement_rank=FLEX_REPLACEMENT_RANK_WAIVER to use outside-of-bench
    (waiver-wire) replacement level instead of the default starter level.
    """
    if replacement_ranks is None:
        replacement_ranks = REPLACEMENT_RANKS

    df = load_and_clean_data()
    df = df[df["season"].isin(seasons)]
    df = calculate_par(df, replacement_ranks=replacement_ranks)

    # Positional rank within each (position, season) — used as lookup key
    df["pos_rank"] = (
        df.groupby(["position", "season"])["half_ppr_points"]
          .rank(method="first", ascending=False)
          .astype(int)
    )

    pos_lookup = (
        df.groupby(["position", "pos_rank"])["par"]
          .mean()
          .to_dict()
    )

    # --- Flex PAR: pool all RB/WR/TE, find combined replacement baseline ---
    flex_df = df[df["position"].isin(FLEX_POSITIONS)].copy()

    # Combined flex rank within each season (across all RB/WR/TE)
    flex_df["flex_rank"] = (
        flex_df.groupby("season")["half_ppr_points"]
               .rank(method="first", ascending=False)
               .astype(int)
    )

    # Flex replacement baseline per season
    flex_baselines = {}
    for season, grp in flex_df.groupby("season"):
        rep = grp[grp["flex_rank"] == flex_replacement_rank]
        if rep.empty:
            rep = grp.nsmallest(1, "flex_rank")  # deepest available
        flex_baselines[season] = rep["half_ppr_points"].mean()

    flex_df["flex_par"] = (
        flex_df["half_ppr_points"] - flex_df["season"].map(flex_baselines)
    )

    # Average flex_par keyed by (position, positional_rank) — same key as pos_lookup
    flex_lookup = (
        flex_df.groupby(["position", "pos_rank"])["flex_par"]
               .mean()
               .to_dict()
    )

    return pos_lookup, flex_lookup


def attach_expected_par(
    rankings: pd.DataFrame,
    pos_lookup: dict,
    flex_lookup: dict,
    rank_window: int = 0,
) -> pd.DataFrame:
    """
    Attach two PAR columns to rankings:
      expected_par      — value when filling a dedicated positional slot
      flex_expected_par — value when filling the FLEX slot (RB/WR/TE only)
    Both fall back to the minimum value for that position when rank exceeds the table.

    rank_window : if > 0, average PAR over [max(1, rank - window) .. rank + window]
                  to model uncertainty around a player's projected rank.
    """
    def _min_per_pos(lookup):
        mins = {}
        for (pos, _rank), val in lookup.items():
            mins[pos] = min(mins.get(pos, val), val)
        return mins

    min_pos  = _min_per_pos(pos_lookup)
    min_flex = _min_per_pos(flex_lookup)

    def _avg_lookup(lookup, pos, rank, fallback):
        if rank_window == 0:
            return lookup.get((pos, rank), fallback)
        lo = max(1, rank - rank_window)
        hi = rank + rank_window
        vals = [lookup[(pos, r)] for r in range(lo, hi + 1) if (pos, r) in lookup]
        return sum(vals) / len(vals) if vals else fallback

    rankings = rankings.copy()

    rankings["expected_par"] = rankings.apply(
        lambda r: _avg_lookup(
            pos_lookup, r["position"], r["positional_rank"],
            min_pos.get(r["position"], 0.0),
        ),
        axis=1,
    )

    rankings["flex_expected_par"] = rankings.apply(
        lambda r: _avg_lookup(
            flex_lookup, r["position"], r["positional_rank"],
            min_flex.get(r["position"], 0.0),
        ) if r["position"] in FLEX_POSITIONS else 0.0,
        axis=1,
    )

    return rankings


def optimize_lineup(rankings: pd.DataFrame) -> pd.DataFrame:
    """
    Two-variable MILP formulation:
      y[i] = 1 if player i fills a dedicated positional slot
      z[i] = 1 if player i fills the FLEX slot   (RB/WR/TE only)
      y[i] + z[i] <= 1  (can't fill two slots)

    Objective:
      max Σ pos_par[i]*y[i]  +  Σ flex_par[i]*z[i]
    """
    prob = pulp.LpProblem("lineup_optimizer", pulp.LpMaximize)

    players = rankings.to_dict("records")
    n = len(players)

    y = [pulp.LpVariable(f"y_{i}", cat="Binary") for i in range(n)]  # positional slot
    z = [pulp.LpVariable(f"z_{i}", cat="Binary") for i in range(n)]  # FLEX slot

    # FLEX only available to RB/WR/TE
    for i, p in enumerate(players):
        if p["position"] not in FLEX_POSITIONS:
            prob += z[i] == 0

    # Each player fills at most one slot
    for i in range(n):
        prob += y[i] + z[i] <= 1

    # Objective: positional PAR for positional slots, flex PAR for the FLEX slot
    prob += pulp.lpSum(
        p["expected_par"]      * y[i] +
        p["flex_expected_par"] * z[i]
        for i, p in enumerate(players)
    )

    # Budget
    prob += pulp.lpSum(
        p["auction_value"] * (y[i] + z[i])
        for i, p in enumerate(players)
    ) <= BUDGET

    # Positional slot counts
    prob += pulp.lpSum(y[i] for i, p in enumerate(players) if p["position"] == "QB") == LINEUP_SLOTS["QB"]
    for pos in ("RB", "WR", "TE"):
        prob += pulp.lpSum(
            y[i] for i, p in enumerate(players) if p["position"] == pos
        ) == LINEUP_SLOTS[pos]

    # Exactly one FLEX slot
    prob += pulp.lpSum(z) == LINEUP_SLOTS["FLEX"]

    status = prob.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError(f"No optimal solution found. Status: {pulp.LpStatus[status]}")

    rows = []
    for i, p in enumerate(players):
        yv = pulp.value(y[i])
        zv = pulp.value(z[i])
        if yv == 1:
            rows.append({**p, "slot": p["position"]})
        elif zv == 1:
            rows.append({**p, "slot": "FLEX"})

    result = pd.DataFrame(rows).sort_values(["position", "positional_rank"])
    return result


ESPN_DATA_PATH   = Path(__file__).parent.parent / "data" / "espn_projected_values.csv"
BLENDED_DATA_PATH   = Path(__file__).parent.parent / "data" / "blended_projected_values.csv"
RINGER_DATA_PATH = Path(__file__).parent.parent / "data" / "ringer_2026_rankings.csv"
ESPN_OUTPUT_PATH = Path(__file__).parent.parent / "output" / "espn_optimized_roster.csv"
BLENDED_OUTPUT_PATH = Path(__file__).parent.parent / "output" / "blended_optimized_roster.csv"
ESPN_LINEUP_SLOTS  = {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "FLEX": 1}
ESPN_BENCH_SLOTS   = 4
ESPN_FLEX_POSITIONS = {"RB", "WR", "TE"}


def load_blended_player_pool() -> pd.DataFrame:
    """
    Build a player pool DataFrame for the blended (ESPN + Sleeper + historical)
    projections (Goal 10), with `team` / `overall_rank` / `auction_value`
    merged in from ESPN's salary-cap data by player_name.

    The blended CSV's `player_name` prefers the ESPN name when available (see
    `analysis.player_id_matching.build_player_key_table`), so a name join
    covers the vast majority of players; anyone without an ESPN auction value
    (e.g. Sleeper-only players) is dropped since `optimize_espn_lineup`
    requires an auction_value to price them.

    Returns a DataFrame with the same columns `optimize_espn_lineup` expects:
        player_name, position, team, overall_rank, positional_rank,
        auction_value, projected_points
    """
    blended = pd.read_csv(BLENDED_DATA_PATH)
    espn = pd.read_csv(ESPN_DATA_PATH)
    price_cols = espn[["player_name", "team", "overall_rank", "auction_value"]]
    df = blended.merge(price_cols, on="player_name", how="left")
    return df

# Pre-assigned DST: Texans defence at $1, 7.7 projected pts/game (≈ 130.9 over 17 games)
TEXANS_DST = {
    "player_name": "Texans",
    "position": "DST",
    "team": "HOU",
    "overall_rank": None,
    "positional_rank": None,
    "auction_value": 1,
    "projected_points": round(7.7 * 17, 1),  # 130.9
    "slot": "DST",
}


def optimize_espn_lineup(players_df: pd.DataFrame, budget: int = 200) -> pd.DataFrame:
    """
    ILP optimizer for the ESPN salary-cap draft.

    Slots: QB×1, RB×2, WR×3, TE×1, FLEX×1 (RB/WR/TE), DST×1, Bench×4 (any).
    Objective: maximise sum of projected_points for all selected players.
    Constraint: total auction_value <= budget.

    DST is pre-assigned to the Texans at $1 / 130.9 projected pts (7.7 pts/game × 17).
    The remaining $budget-1 is available for the skill-position roster.
    """
    # Pre-assign Texans DST; deduct their cost from the available budget
    dst_row  = TEXANS_DST.copy()
    skill_budget = budget - dst_row["auction_value"]

    df = players_df.dropna(subset=["projected_points", "auction_value"]).copy()
    # Exclude DST positions — handled separately
    df = df[df["position"] != "DST"]
    df = df[df["auction_value"] > 0].reset_index(drop=True)

    prob = pulp.LpProblem("espn_lineup_optimizer", pulp.LpMaximize)

    players = df.to_dict("records")
    n = len(players)

    # Decision variables
    # y[i] = 1 if player fills a dedicated positional starter slot
    # z[i] = 1 if player fills the FLEX slot (RB/WR/TE only)
    # b[i] = 1 if player fills a bench slot
    y = [pulp.LpVariable(f"y_{i}", cat="Binary") for i in range(n)]
    z = [pulp.LpVariable(f"z_{i}", cat="Binary") for i in range(n)]
    b = [pulp.LpVariable(f"b_{i}", cat="Binary") for i in range(n)]

    # FLEX only for RB/WR/TE
    for i, p in enumerate(players):
        if p["position"] not in ESPN_FLEX_POSITIONS:
            prob += z[i] == 0

    # Each player can fill at most one slot
    for i in range(n):
        prob += y[i] + z[i] + b[i] <= 1

    # Objective: maximise projected points for STARTERS only (y = positional, z = FLEX)
    # Bench players are not included in the objective so the ILP picks the
    # highest-scoring starting lineup first, then fills bench slots within budget.
    prob += pulp.lpSum(
        p["projected_points"] * (y[i] + z[i])
        for i, p in enumerate(players)
    )

    # Budget constraint (DST cost already deducted from skill_budget)
    prob += pulp.lpSum(
        p["auction_value"] * (y[i] + z[i] + b[i])
        for i, p in enumerate(players)
    ) <= skill_budget

    # Positional starter slot counts
    for pos, count in ESPN_LINEUP_SLOTS.items():
        if pos == "FLEX":
            continue
        prob += pulp.lpSum(
            y[i] for i, p in enumerate(players) if p["position"] == pos
        ) == count

    # Exactly one FLEX slot
    prob += pulp.lpSum(z) == ESPN_LINEUP_SLOTS["FLEX"]

    # Exactly ESPN_BENCH_SLOTS bench players
    prob += pulp.lpSum(b) == ESPN_BENCH_SLOTS

    # Bench composition: 1-2 WRs, 1-3 RBs, 0 QBs, 0 TEs
    bench_wr = pulp.lpSum(b[i] for i, p in enumerate(players) if p["position"] == "WR")
    bench_rb = pulp.lpSum(b[i] for i, p in enumerate(players) if p["position"] == "RB")
    prob += bench_wr >= 1
    prob += bench_wr <= 2
    prob += bench_rb >= 1
    prob += bench_rb <= 3
    for i, p in enumerate(players):
        if p["position"] in ("QB", "TE"):
            prob += b[i] == 0

    status = prob.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError(f"No optimal solution found. Status: {pulp.LpStatus[status]}")

    rows = []
    for i, p in enumerate(players):
        yv = pulp.value(y[i])
        zv = pulp.value(z[i])
        bv = pulp.value(b[i])
        if yv == 1:
            rows.append({**p, "slot": p["position"]})
        elif zv == 1:
            rows.append({**p, "slot": "FLEX"})
        elif bv == 1:
            rows.append({**p, "slot": "Bench"})

    slot_order = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "FLEX": 4, "DST": 5, "Bench": 6}
    result = pd.DataFrame(rows)
    result["_slot_order"] = result["slot"].map(slot_order)
    result = result.sort_values(["_slot_order", "positional_rank"]).drop(columns=["_slot_order"])

    # Prepend the pre-assigned Texans DST
    dst_df = pd.DataFrame([dst_row])
    result = pd.concat([dst_df, result], ignore_index=True)

    # Points per game (projected season total / 17 regular-season games)
    result["ppg"] = (result["projected_points"] / 17).round(1)

    return result



# ---------------------------------------------------------------------------
# Cross-source optimizer (Goal 8)
# ---------------------------------------------------------------------------

DST_PLACEHOLDER_POINTS = 7 * 17  # 119 pts (7 pts/game × 17 games)
CROSS_SOURCE_SKILL_POSITIONS = {"QB", "RB", "WR", "TE"}
CROSS_BENCH_SLOTS = 4  # bench spots (no QB/TE; ≤3 RB, ≤2 WR)


def _abbrev_name(name: str) -> str:
    """Return 'F. Lastname' abbreviation for a player name."""
    parts = name.strip().split()
    if len(parts) >= 2:
        return f"{parts[0][0]}. {' '.join(parts[1:])}"
    return name


def build_combo_rankings(rank_source: str, price_source: str) -> pd.DataFrame:
    """
    Build a combined rankings DataFrame for one of the 4 cross-source combos.

    rank_source  ∈ {"espn", "ringer"}  — determines positional_rank
    price_source ∈ {"espn", "ringer"}  — determines auction_value

    When the two sources differ, player names are fuzzy-matched with rapidfuzz
    (score cutoff 85). Unmatched players are dropped with a log message.

    Returns DataFrame with columns:
        player_name, position, positional_rank, auction_value
    """
    try:
        from rapidfuzz import process as rf_process, fuzz as rf_fuzz
        _HAS_RAPIDFUZZ = True
    except ImportError:
        _HAS_RAPIDFUZZ = False

    # Load rank source
    if rank_source == "espn":
        rank_df = pd.read_csv(ESPN_DATA_PATH)
    else:
        rank_df = pd.read_csv(RINGER_DATA_PATH)
    rank_df = rank_df[rank_df["position"].isin(CROSS_SOURCE_SKILL_POSITIONS)].copy()

    # Load price source
    if price_source == "espn":
        price_df = pd.read_csv(ESPN_DATA_PATH)
    else:
        price_df = pd.read_csv(RINGER_DATA_PATH)
    price_df = price_df[price_df["position"].isin(CROSS_SOURCE_SKILL_POSITIONS)].copy()

    if rank_source == price_source:
        # Same source — direct merge; columns already consistent
        result = rank_df[["player_name", "position", "positional_rank"]].copy()
        result = result.merge(
            price_df[["player_name", "auction_value"]],
            on="player_name",
            how="inner",
        )
        return result.reset_index(drop=True)

    # Different sources — fuzzy name matching
    if not _HAS_RAPIDFUZZ:
        raise ImportError(
            "rapidfuzz is required for cross-source name matching. "
            "Install with: pip install rapidfuzz"
        )

    price_names = price_df["player_name"].tolist()
    matched_rows = []
    unmatched = []

    for _, row in rank_df.iterrows():
        match = rf_process.extractOne(
            row["player_name"],
            price_names,
            scorer=rf_fuzz.token_sort_ratio,
            score_cutoff=85,
        )
        if match is None:
            unmatched.append(row["player_name"])
            continue
        matched_name = match[0]
        price_row = price_df[price_df["player_name"] == matched_name].iloc[0]
        matched_rows.append({
            "player_name": row["player_name"],
            "position": row["position"],
            "positional_rank": row["positional_rank"],
            "auction_value": price_row["auction_value"],
        })

    if unmatched:
        print(
            f"  [build_combo_rankings rank={rank_source} price={price_source}] "
            f"Dropped {len(unmatched)} unmatched players: {unmatched[:10]}"
            + (" ..." if len(unmatched) > 10 else "")
        )

    return pd.DataFrame(matched_rows).reset_index(drop=True)


def attach_expected_points(
    rankings: pd.DataFrame,
    avg_points: dict,
    avg_flex_points: dict,
    rank_window: int = 0,
) -> pd.DataFrame:
    """
    Attach two expected-points columns to rankings:
      expected_points      — value when filling a dedicated positional slot
      flex_expected_points — value when filling the FLEX slot (RB/WR/TE only)

    Falls back to the minimum historical value for the position when rank exceeds
    the lookup table. rank_window mirrors the smoothing in attach_expected_par.
    """
    def _min_per_pos(lookup):
        mins: dict = {}
        for (pos, _r), val in lookup.items():
            mins[pos] = min(mins.get(pos, val), val)
        return mins

    min_pos  = _min_per_pos(avg_points)
    min_flex = _min_per_pos(avg_flex_points)

    def _avg_lookup(lookup, pos, rank, fallback):
        if rank_window == 0:
            return lookup.get((pos, rank), fallback)
        lo = max(1, rank - rank_window)
        hi = rank + rank_window
        vals = [lookup[(pos, r)] for r in range(lo, hi + 1) if (pos, r) in lookup]
        return sum(vals) / len(vals) if vals else fallback

    rankings = rankings.copy()

    rankings["expected_points"] = rankings.apply(
        lambda r: _avg_lookup(
            avg_points, r["position"], r["positional_rank"],
            min_pos.get(r["position"], 0.0),
        ),
        axis=1,
    )

    rankings["flex_expected_points"] = rankings.apply(
        lambda r: _avg_lookup(
            avg_flex_points, r["position"], r["positional_rank"],
            min_flex.get(r["position"], 0.0),
        ) if r["position"] in FLEX_POSITIONS else 0.0,
        axis=1,
    )

    return rankings


def optimize_lineup_points(rankings: pd.DataFrame, budget: int = 200) -> pd.DataFrame:
    """
    Three-variable MILP formulation maximising historical average points:
      y[i] = 1 if player fills a dedicated positional slot
      z[i] = 1 if player fills the FLEX slot   (RB/WR/TE only)
      b[i] = 1 if player fills a bench slot     (no QB/TE; ≤3 RB, ≤2 WR)
      y[i] + z[i] + b[i] <= 1

    Objective: max Σ expected_points[i]*y[i] + Σ flex_expected_points[i]*z[i]
    (bench players are selected within budget but not counted toward objective)

    DST placeholder (119 pts) is NOT included in the MILP — caller adds it.
    """
    prob = pulp.LpProblem("lineup_points_optimizer", pulp.LpMaximize)

    players = rankings.to_dict("records")
    n = len(players)

    y = [pulp.LpVariable(f"yp_{i}", cat="Binary") for i in range(n)]
    z = [pulp.LpVariable(f"zp_{i}", cat="Binary") for i in range(n)]
    b = [pulp.LpVariable(f"bp_{i}", cat="Binary") for i in range(n)]

    # FLEX only for RB/WR/TE
    for i, p in enumerate(players):
        if p["position"] not in FLEX_POSITIONS:
            prob += z[i] == 0

    # Each player fills at most one slot
    for i in range(n):
        prob += y[i] + z[i] + b[i] <= 1

    # Objective: starter points only (bench not counted)
    prob += pulp.lpSum(
        p["expected_points"]      * y[i] +
        p["flex_expected_points"] * z[i]
        for i, p in enumerate(players)
    )

    # Budget includes bench cost
    prob += pulp.lpSum(
        p["auction_value"] * (y[i] + z[i] + b[i])
        for i, p in enumerate(players)
    ) <= budget

    # Starter slot counts
    prob += pulp.lpSum(y[i] for i, p in enumerate(players) if p["position"] == "QB") == LINEUP_SLOTS["QB"]
    for pos in ("RB", "WR", "TE"):
        prob += pulp.lpSum(
            y[i] for i, p in enumerate(players) if p["position"] == pos
        ) == LINEUP_SLOTS[pos]

    # Exactly one FLEX slot
    prob += pulp.lpSum(z) == LINEUP_SLOTS["FLEX"]

    # Exactly CROSS_BENCH_SLOTS bench players
    prob += pulp.lpSum(b) == CROSS_BENCH_SLOTS

    # Bench composition: no QB/TE; ≤3 RB, ≤2 WR
    for i, p in enumerate(players):
        if p["position"] in ("QB", "TE"):
            prob += b[i] == 0
    prob += pulp.lpSum(b[i] for i, p in enumerate(players) if p["position"] == "RB") <= 3
    prob += pulp.lpSum(b[i] for i, p in enumerate(players) if p["position"] == "WR") <= 2

    status = prob.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError(f"No optimal solution found. Status: {pulp.LpStatus[status]}")

    rows = []
    for i, p in enumerate(players):
        if pulp.value(y[i]) == 1:
            rows.append({**p, "slot": p["position"]})
        elif pulp.value(z[i]) == 1:
            rows.append({**p, "slot": "FLEX"})
        elif pulp.value(b[i]) == 1:
            rows.append({**p, "slot": "Bench"})

    slot_order = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "FLEX": 4, "Bench": 5}
    result = pd.DataFrame(rows)
    result["_slot_order"] = result["slot"].map(slot_order)
    result = result.sort_values(["_slot_order", "positional_rank"]).drop(columns=["_slot_order"])
    return result.reset_index(drop=True)


def run_cross_source_optimization(budget: int = 200) -> dict:
    """
    Run all 4 cross-source optimizer combinations and return a results dict.

    Combinations:
      A: rank=ESPN,   price=ESPN
      B: rank=ESPN,   price=Ringer
      C: rank=Ringer, price=ESPN
      D: rank=Ringer, price=Ringer

    Each result dict entry contains:
      'lineup'        — optimized DataFrame
      'total_points'  — projected lineup points incl. DST placeholder (119 pts)
      'total_cost'    — total auction spend
      'label'         — combo label string

    Also saves output CSVs and a comparison bar chart.
    """
    try:
        from .visualize_par import plot_cross_source_comparison
    except ImportError:
        sys.path.insert(0, str(Path(__file__).parent))
        from visualize_par import plot_cross_source_comparison

    OUTPUT_DIR = Path(__file__).parent.parent / "output"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Building historical average points lookup (2021–2025)…")
    avg_points, avg_flex_points = build_avg_points_lookup()

    combos = [
        ("A", "espn",   "espn",   "ESPN/ESPN"),
        ("B", "espn",   "ringer", "ESPN/Ringer"),
        ("C", "ringer", "espn",   "Ringer/ESPN"),
        ("D", "ringer", "ringer", "Ringer/Ringer"),
    ]

    results = {}
    for combo_id, rank_src, price_src, label in combos:
        print(f"\n--- Combo {combo_id}: rank={rank_src.upper()}, price={price_src.upper()} ---")
        try:
            rankings = build_combo_rankings(rank_src, price_src)
            rankings = attach_expected_points(rankings, avg_points, avg_flex_points)
            # Enforce $1 minimum bid; reserve $1 for DST
            rankings["auction_value"] = rankings["auction_value"].clip(lower=1)
            skill_budget = budget - 1  # $1 reserved for DST
            lineup   = optimize_lineup_points(rankings, budget=skill_budget)

            lineup["slot_points"] = lineup.apply(
                lambda r: r["flex_expected_points"] if r["slot"] == "FLEX" else r["expected_points"],
                axis=1,
            )
            lineup["ppg"] = (lineup["slot_points"] / 17).round(1)
            starters_df = lineup[lineup["slot"] != "Bench"]
            skill_pts   = starters_df["slot_points"].sum()
            total_pts   = skill_pts + DST_PLACEHOLDER_POINTS
            total_ppg   = skill_pts / 17 + DST_PLACEHOLDER_POINTS / 17
            total_cost  = lineup["auction_value"].sum() + 1  # +$1 for DST

            results[combo_id] = {
                "label":        label,
                "lineup":       lineup,
                "total_points": total_pts,
                "skill_points": skill_pts,
                "total_ppg":    total_ppg,
                "total_cost":   total_cost,
            }

            # Save CSV
            csv_path = OUTPUT_DIR / f"cross_source_{combo_id.lower()}.csv"
            lineup.to_csv(csv_path, index=False)
            print(f"  Saved: {csv_path}")
            print(f"  Total projected points (incl. DST): {total_pts:.1f}  |  Pts/game: {total_ppg:.1f}  |  Cost: ${total_cost}")

        except Exception as e:
            print(f"  ERROR in combo {combo_id}: {e}")

    # Print one table per combo, then a summary
    SLOT_DISPLAY_ORDER = [
        ("QB",    "QB"),
        ("RB",    "RB"),
        ("RB",    "RB"),
        ("WR",    "WR"),
        ("WR",    "WR"),
        ("WR",    "WR"),
        ("TE",    "TE"),
        ("FLEX",  "FLEX"),
        ("Bench", "Bench"),
        ("Bench", "Bench"),
        ("Bench", "Bench"),
        ("Bench", "Bench"),
    ]

    COL_WIDTH_NAME = 24
    COL_WIDTH_NUM  = 10

    def _combo_table_rows(lineup: pd.DataFrame) -> list[dict]:
        """Return one display-row dict per lineup slot in display order."""
        starters  = lineup[lineup["slot"] != "Bench"].copy()
        bench_df  = lineup[lineup["slot"] == "Bench"].sort_values("positional_rank").reset_index(drop=True)
        slot_counts: dict[str, int] = {}
        bench_idx = 0
        rows_out = []
        for slot_key, _pos_label in SLOT_DISPLAY_ORDER:
            if slot_key == "Bench":
                if bench_idx < len(bench_df):
                    p = bench_df.iloc[bench_idx]
                    ppg = p["expected_points"] / 17
                    rows_out.append({
                        "slot":   f"Bench {bench_idx + 1}",
                        "pos":    p["position"],
                        "name":   p["player_name"],
                        "cost":   int(p["auction_value"]),
                        "points": ppg,
                    })
                    bench_idx += 1
                else:
                    rows_out.append({"slot": f"Bench {bench_idx + 1}", "pos": "—", "name": "—", "cost": 0, "points": 0.0})
                    bench_idx += 1
            else:
                nth = slot_counts.get(slot_key, 0) + 1
                slot_counts[slot_key] = nth
                matching = starters[starters["slot"] == slot_key].sort_values("positional_rank")
                if len(matching) >= nth:
                    p = matching.iloc[nth - 1]
                    pts = p["flex_expected_points"] if slot_key == "FLEX" else p["expected_points"]
                    ppg = pts / 17
                    label = slot_key if nth == 1 else f"{slot_key}{nth}"
                    rows_out.append({
                        "slot":   label,
                        "pos":    p["position"],
                        "name":   p["player_name"],
                        "cost":   int(p["auction_value"]),
                        "points": ppg,
                    })
                else:
                    rows_out.append({"slot": slot_key, "pos": "—", "name": "—", "cost": 0, "points": 0.0})
        return rows_out

    print("\n=== Cross-Source Optimization Results ===")
    for combo_id, *_ in combos:
        if combo_id not in results:
            continue
        r = results[combo_id]
        table_rows = _combo_table_rows(r["lineup"])

        header_line = (
            f"  {'Slot':<8}  {'Pos':<6}  {'Player':<{COL_WIDTH_NAME}}  "
            f"{'Cost':>{COL_WIDTH_NUM}}  {'Pts/Gm':>{COL_WIDTH_NUM}}"
        )
        separator = "  " + "-" * (len(header_line) - 2)

        print(f"\n--- Combo {combo_id}: {r['label']} ---")
        print(header_line)
        print(separator)
        for tr in table_rows:
            cost_str = f"${tr['cost']}" if tr["cost"] else "—"
            pts_str  = f"{tr['points']:.1f}" if tr["points"] else "—"
            print(
                f"  {tr['slot']:<8}  {tr['pos']:<6}  {tr['name']:<{COL_WIDTH_NAME}}  "
                f"{cost_str:>{COL_WIDTH_NUM}}  {pts_str:>{COL_WIDTH_NUM}}"
            )
        print(separator)
        skill_cost = sum(tr["cost"] for tr in table_rows if "Bench" not in tr["slot"])
        print(
            f"  {'TOTAL':<8}  {'':6}  {'(+ $1 DST)':<{COL_WIDTH_NAME}}  "
            f"${r['total_cost']:>{COL_WIDTH_NUM - 1}}  {r['total_ppg']:>{COL_WIDTH_NUM}.1f}"
        )

    # Summary across all combos
    print("\n=== Summary ===")
    sum_header = f"  {'Combo':<20}  {'Proj Pts':>10}  {'Pts/Gm':>8}  {'Cost':>6}"
    print(sum_header)
    print("  " + "-" * (len(sum_header) - 2))
    for combo_id, *_ in combos:
        if combo_id not in results:
            continue
        r = results[combo_id]
        combo_label = f"Combo {combo_id}: {r['label']}"
        print(f"  {combo_label:<20}  {r['total_points']:>10.1f}  {r['total_ppg']:>8.1f}  ${r['total_cost']:>5}")

    # Bar chart
    plot_cross_source_comparison(results)

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fantasy football lineup optimizer.")
    parser.add_argument(
        "--cross",
        action="store_true",
        help=(
            "Run the cross-source optimizer (Goal 8). "
            "Runs all 4 rank × price combos and saves output CSVs and comparison chart."
        ),
    )
    parser.add_argument(
        "--espn",
        action="store_true",
        help=(
            "Run the ESPN salary-cap optimizer instead of the Ringer PAR optimizer. "
            "Reads data/espn_projected_values.csv and maximises projected_points."
        ),
    )
    parser.add_argument(
        "--blended",
        action="store_true",
        help=(
            "Run the salary-cap optimizer against the blended ESPN + Sleeper + "
            "historical projection instead (Goal 10). "
            "Reads data/blended_projected_values.csv, joined to ESPN auction "
            "values by player_name. "
            "Run `python -m analysis.load_multi_source_projections` first if it's missing."
        ),
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=200,
        metavar="DOLLARS",
        help="Salary-cap budget for the ESPN optimizer (default: $200).",
    )
    parser.add_argument(
        "--waiver", "-w",
        action="store_true",
        help=(
            "Use outside-of-bench (waiver-wire) replacement level "
            "(QB19, RB56, WR66, TE19 / flex rank 139) "
            "instead of the default starter replacement level."
        ),
    )
    parser.add_argument(
        "--uncertainty", "-u",
        type=int,
        default=0,
        metavar="WINDOW",
        help=(
            "Model rank uncertainty by averaging PAR over "
            "[max(1, rank - WINDOW) .. rank + WINDOW] for each player. "
            "E.g. --uncertainty 5 averages ranks ±5 around the projected rank. "
            "Defaults to 0 (use exact rank)."
        ),
    )
    parser.add_argument(
        "--boost", "-b",
        type=float,
        default=0.0,
        metavar="PCT",
        help=(
            "Increase all player auction costs (auction_value) by PCT%% before "
            "optimizing. E.g. --boost 10 raises every cost by 10%%. Models a more "
            "expensive draft environment where players cost more relative to the "
            "fixed budget. Defaults to 0 (no boost)."
        ),
    )
    args = parser.parse_args()

    # -------------------------------------------------------------- Cross --
    if args.cross:
        run_cross_source_optimization(budget=args.budget)
        sys.exit(0)

    # ------------------------------------------------------------------ ESPN --
    if args.espn or args.blended:
        if args.blended:
            print(f"Loading blended projected values from {BLENDED_DATA_PATH}…")
            players_df = load_blended_player_pool()
            output_path = BLENDED_OUTPUT_PATH
            label = "Blended (ESPN + Sleeper + historical)"
        else:
            print(f"Loading ESPN projected values from {ESPN_DATA_PATH}…")
            players_df = pd.read_csv(ESPN_DATA_PATH)
            output_path = ESPN_OUTPUT_PATH
            label = "ESPN"

        before = len(players_df)
        players_df = players_df.dropna(subset=["projected_points", "auction_value"])
        dropped = before - len(players_df)
        if dropped:
            print(f"  Dropped {dropped} players with missing projected_points or auction_value.")
        print(f"  {len(players_df)} players available.")

        if args.boost:
            players_df = players_df.copy()
            players_df["auction_value"] = (players_df["auction_value"] * (1 + args.boost / 100)).round(1)
            print(f"  Boost applied: auction_value increased by {args.boost:g}%.")

        print(f"\nRunning {label} salary-cap optimizer (budget: ${args.budget})…")
        roster = optimize_espn_lineup(players_df, budget=args.budget)

        starters = roster[roster["slot"] != "Bench"]
        bench    = roster[roster["slot"] == "Bench"]
        total_cost   = roster["auction_value"].sum()
        total_pts    = roster["projected_points"].sum()
        starter_pts  = starters["projected_points"].sum()

        print(f"\n=== {label} Salary-Cap Optimal Roster (Budget: ${args.budget}) ===")
        display_cols = ["slot", "position", "player_name", "team",
                        "overall_rank", "auction_value", "projected_points", "ppg"]
        print("\n--- Starters ---")
        print(starters[display_cols].to_string(index=False))
        print("\n--- Bench ---")
        print(bench[display_cols].to_string(index=False))
        starter_ppg  = starters["ppg"].sum()
        print(f"\nTotal auction cost        : ${total_cost}")
        print(f"Remaining budget          : ${args.budget - total_cost}")
        print(f"Total projected pts (all) : {total_pts:.1f}")
        print(f"Starter projected pts     : {starter_pts:.1f}")
        print(f"Starter projected pts/gm  : {starter_ppg:.1f}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        roster.to_csv(output_path, index=False)
        print(f"\nRoster saved to {output_path}")
        sys.exit(0)

    # --------------------------------------------------------------- Ringer --
    if args.waiver:
        repl_ranks      = REPLACEMENT_RANKS_WAIVER
        flex_repl_rank  = FLEX_REPLACEMENT_RANK_WAIVER
        mode_label      = "waiver-wire (outside-of-bench)"
    else:
        repl_ranks      = REPLACEMENT_RANKS
        flex_repl_rank  = FLEX_REPLACEMENT_RANK
        mode_label      = "starter"

    rank_window = args.uncertainty
    uncertainty_label = (
        f"±{rank_window} rank window" if rank_window > 0 else "exact rank"
    )
    boost_label = f"+{args.boost:g}% cost boost" if args.boost else "no cost boost"

    print(f"Replacement level mode : {mode_label}")
    print(f"Rank uncertainty       : {uncertainty_label}")
    print(f"Player cost boost      : {boost_label}")
    print("Building historical average PAR lookup tables…")
    pos_lookup, flex_lookup = build_avg_par_lookup(
        replacement_ranks=repl_ranks,
        flex_replacement_rank=flex_repl_rank,
    )

    print(f"\nLoading Ringer 2026 rankings from {RANKINGS_PATH}…")
    rankings = pd.read_csv(RANKINGS_PATH)
    # Keep only the four skill positions the optimizer handles
    rankings = rankings[rankings["position"].isin({"QB", "RB", "WR", "TE"})].reset_index(drop=True)
    print(f"  {len(rankings)} players loaded.")

    rankings = attach_expected_par(rankings, pos_lookup, flex_lookup, rank_window=rank_window)

    # Historical average raw points (not PAR) for projected points-per-game display
    print("Building historical average points lookup (2021–2025)…")
    avg_points, avg_flex_points = build_avg_points_lookup()
    rankings = attach_expected_points(rankings, avg_points, avg_flex_points, rank_window=rank_window)

    if args.boost:
        rankings["auction_value"] = (rankings["auction_value"] * (1 + args.boost / 100)).round(1)
        print(f"  Boost applied: auction_value increased by {args.boost:g}%.")

    print("\nRunning lineup optimizer…")
    lineup = optimize_lineup(rankings)

    # Show the PAR that actually counts for each player's slot
    lineup["slot_par"] = lineup.apply(
        lambda r: r["flex_expected_par"] if r["slot"] == "FLEX" else r["expected_par"],
        axis=1,
    )
    # Projected points per game for each player's slot (season total / 17 games)
    lineup["slot_points"] = lineup.apply(
        lambda r: r["flex_expected_points"] if r["slot"] == "FLEX" else r["expected_points"],
        axis=1,
    )
    lineup["ppg"] = (lineup["slot_points"] / 17).round(1)

    total_cost = lineup["auction_value"].sum()
    total_par  = lineup["slot_par"].sum()
    total_ppg  = lineup["ppg"].sum()

    print(f"\n=== Optimal Starting Lineup (Budget: ${BUDGET}) — {mode_label} PAR | {uncertainty_label} | {boost_label} ===")
    print(
        lineup[["slot", "position", "player_name", "team", "positional_rank",
                 "auction_value", "slot_par", "ppg"]]
        .rename(columns={"slot_par": "expected_par"})
        .to_string(index=False)
    )
    print(f"\nTotal auction cost         : ${total_cost}")
    print(f"Total expected PAR         : {total_par:.1f}")
    print(f"Starting lineup pts/game   : {total_ppg:.1f}")
    print(f"Remaining budget           : ${BUDGET - total_cost}")
