"""
Draft recap: compares your actual drafted roster against the theoretical
best-possible starting lineup, given the final `draft_state.json`.

"Best possible" = the highest-scoring legal starting lineup (1 QB, 2 RB,
3 WR, 1 TE, 1 FLEX) obtainable under a given budget, where:
  - each player you actually drafted (regardless of who drafted them) is
    available at cost = price_paid (+$1 if drafted by someone else, since
    you can't have "stolen" a division rival's exact price — you'd have had
    to bid $1 more to win it. Your own picks keep their real price.)
  - every undrafted player is assumed available for $1.

This mirrors the ad-hoc analysis worked out in chat; see it there for the
full derivation. Rerun any time after a draft (or mid-draft) to regenerate
the comparison table.

Usage:
    python -m analysis.draft_recap
    python -m analysis.draft_recap --budget 195
    python -m analysis.draft_recap --state-path data/draft_state.json --my-label me

Notes:
  - `--budget` is the amount available to spend on the 7 STARTING slots only
    (defaults to 195: the $200 total auction budget minus $4 reserved for
    4 bench slots minus $1 reserved for a DST slot — tweak as needed).
  - Points are `expected_points` from the state file (i.e. whatever
    projection source seeded it, typically blended + schedule-adjusted).
  - Requires `pulp` (already a project dependency; see requirements.txt).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pulp

DEFAULT_STATE_PATH = Path(__file__).parent.parent / "data" / "draft_state.json"
DEFAULT_BUDGET = 195  # $200 - $4 bench - $1 DST reserve
NUM_WEEKS = 17

STARTER_SLOTS = {"QB": 1, "RB": 2, "WR": 3, "TE": 1}
FLEX_SLOTS = 1
FLEX_POSITIONS = {"RB", "WR", "TE"}
SLOT_ORDER = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "FLEX": 4}


def load_players(state_path: Path) -> list[dict]:
    with open(state_path) as f:
        state = json.load(f)
    return state["players"]


def build_pool(players: list[dict], my_label: str = "me") -> list[dict]:
    """Build the MILP input pool with the cost-basis rules described above."""
    pool = []
    for p in players:
        if p.get("drafted"):
            if p.get("drafted_by") == my_label:
                cost = p["price_paid"]
            else:
                cost = p["price_paid"] + 1
        else:
            cost = 1
        pool.append({
            "name": p["player_name"],
            "position": p["position"],
            "points": p["expected_points"],
            "cost": cost,
            "mine": bool(p.get("drafted") and p.get("drafted_by") == my_label),
        })
    return pool


def solve_lineup(
    pool: list[dict],
    budget: float | None = None,
    restrict_to_mine: bool = False,
) -> tuple[list[tuple], float, float]:
    """
    Solve the MILP for the best legal starting lineup from `pool`.

    If `restrict_to_mine` is True, only players with pool[i]['mine'] == True
    are eligible (used to compute your *actual* best lineup, with no budget
    constraint since those players are already paid for).

    Returns (rows, total_points, total_cost) where rows is a list of
    (slot, name, points, cost, mine) tuples in display order.
    """
    candidates = [p for p in pool if (not restrict_to_mine or p["mine"])]
    n = len(candidates)

    prob = pulp.LpProblem("lineup_opt", pulp.LpMaximize)
    y = [pulp.LpVariable(f"y_{i}", cat="Binary") for i in range(n)]  # positional starter
    z = [pulp.LpVariable(f"z_{i}", cat="Binary") for i in range(n)]  # FLEX

    for i, p in enumerate(candidates):
        if p["position"] not in FLEX_POSITIONS:
            prob += z[i] == 0
        prob += y[i] + z[i] <= 1

    for pos, cnt in STARTER_SLOTS.items():
        prob += pulp.lpSum(y[i] for i, p in enumerate(candidates) if p["position"] == pos) == cnt
    prob += pulp.lpSum(z) == FLEX_SLOTS

    prob += pulp.lpSum(p["points"] * (y[i] + z[i]) for i, p in enumerate(candidates))

    if budget is not None:
        prob += pulp.lpSum(p["cost"] * (y[i] + z[i]) for i, p in enumerate(candidates)) <= budget

    status = prob.solve(pulp.PULP_CBC_CMD(msg=0))
    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError(f"Solver did not find an optimal solution: {pulp.LpStatus[status]}")

    rows = []
    total_pts = 0.0
    total_cost = 0.0
    for i, p in enumerate(candidates):
        slot = None
        if y[i].value() == 1:
            slot = p["position"]
        elif z[i].value() == 1:
            slot = "FLEX"
        if slot is not None:
            rows.append((slot, p["name"], p["points"], p["cost"], p["mine"]))
            total_pts += p["points"]
            total_cost += p["cost"]

    rows.sort(key=lambda r: SLOT_ORDER.get(r[0], 9))
    return rows, total_pts, total_cost


def print_lineup(rows: list[tuple], total_pts: float, total_cost: float) -> None:
    for slot, name, pts, cost, mine in rows:
        tag = " (mine)" if mine else ""
        print(f"{slot:5} {name:25} ppg={pts / NUM_WEEKS:5.1f}  cost=${cost:.0f}{tag}")
    print(f"Total ppg: {total_pts / NUM_WEEKS:.1f}   cost=${total_cost:.0f}")


def print_comparison_table(
    actual_rows: list[tuple],
    optimal_rows: list[tuple],
    actual_total: float,
    optimal_total: float,
) -> None:
    actual_by_slot = {slot: (name, pts, cost) for slot, name, pts, cost, _ in actual_rows}
    optimal_by_slot = {slot: (name, pts, cost) for slot, name, pts, cost, _ in optimal_rows}
    slots = sorted(set(actual_by_slot) | set(optimal_by_slot), key=lambda s: SLOT_ORDER.get(s, 9))

    header = f"{'Pos':5} {'Actual':22}{'ppg':>7}{'cost':>7}   {'Optimal':22}{'ppg':>7}{'cost':>7}"
    print(header)
    print("-" * len(header))
    for slot in slots:
        a_name, a_pts, a_cost = actual_by_slot.get(slot, ("--", 0.0, 0.0))
        o_name, o_pts, o_cost = optimal_by_slot.get(slot, ("--", 0.0, 0.0))
        print(
            f"{slot:5} {a_name:22}{a_pts / NUM_WEEKS:7.1f}{a_cost:7.0f}   "
            f"{o_name:22}{o_pts / NUM_WEEKS:7.1f}{o_cost:7.0f}"
        )
    print("-" * len(header))
    print(
        f"{'TOTAL':5} {'':22}{actual_total / NUM_WEEKS:7.1f}{'':7}   "
        f"{'':22}{optimal_total / NUM_WEEKS:7.1f}"
    )
    if optimal_total:
        pct = (optimal_total - actual_total) / optimal_total * 100
        print(f"\nActual lineup is {pct:.1f}% below the theoretical ceiling.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--state-path", type=Path, default=DEFAULT_STATE_PATH, help="Path to draft_state.json")
    parser.add_argument("--budget", type=float, default=DEFAULT_BUDGET, help="Budget available for the 7 starting slots")
    parser.add_argument("--my-label", type=str, default="me", help="drafted_by value that identifies your picks")
    args = parser.parse_args()

    players = load_players(args.state_path)
    pool = build_pool(players, my_label=args.my_label)

    mine = [p for p in pool if p["mine"]]
    print(f"=== My actual roster ({len(mine)} players) ===")
    for p in sorted(mine, key=lambda p: SLOT_ORDER.get(p["position"], 9)):
        print(f"{p['position']:4} {p['name']:25} ppg={p['points'] / NUM_WEEKS:5.1f}  cost=${p['cost']:.0f}")
    print()

    print("=== Best starting lineup from MY roster only (actual) ===")
    actual_rows, actual_total, actual_cost = solve_lineup(pool, budget=None, restrict_to_mine=True)
    print_lineup(actual_rows, actual_total, actual_cost)
    print()

    print(f"=== Theoretical optimal lineup (budget=${args.budget:.0f}) ===")
    optimal_rows, optimal_total, optimal_cost = solve_lineup(pool, budget=args.budget, restrict_to_mine=False)
    print_lineup(optimal_rows, optimal_total, optimal_cost)
    print()

    print("=== Actual vs. Optimal comparison ===")
    print_comparison_table(actual_rows, optimal_rows, actual_total, optimal_total)


if __name__ == "__main__":
    main()
