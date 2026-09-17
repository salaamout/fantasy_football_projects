"""
Goal 14 Step 5: Live "optimal team" panel.

Continuously recomputes my optimal roster given:
  * players I've already drafted (locked in at their real price paid — sunk
    cost, must occupy a roster slot)
  * players still available on the board (priced at their current shadow /
    WTP price — Step 4's live recompute)
  * my remaining budget and roster slots

This mirrors the MILP formulation in `analysis.lineup_optimizer`
(`optimize_lineup_points` / `optimize_espn_lineup`) but adds the notion of
"locked" players that must be included in the solution regardless of cost
(since they're already on my roster), and no DST slot/position (the WTP
board has no DST rows).

Cost basis: the MILP budget constraint uses each available player's
`espn_av` (ESPN's estimated real auction price) — the actual $ figure a
salary-cap draft is settled in — not `wtp_price` (our theoretical "fair
value" shadow price). Using `wtp_price` as the cost would make this
optimizer solve a different, incompatible problem than
`analysis.lineup_optimizer`'s blended/schedule-adjusted optimizer (which
uses `espn_av`), producing a lineup that doesn't match it even before any
picks are made.

Also supports a lightweight "what-if" mode: pass `hypothetical_player_id` to
see how the optimal team would look if I won that one additional available
player at their current espn_av price, without mutating `DraftState` at all.

No Streamlit dependency — unit-testable standalone.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pulp

_HERE = Path(__file__).parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from webapp.draft_state import DraftState, Player
from webapp.adjusted_cost import compute_adjusted_espn_av

FLEX_POSITIONS = {"RB", "WR", "TE"}

# Roster slots modeled here — mirrors DEFAULT_ROSTER_SLOTS minus a full
# DST auction (the WTP board has no DST rows to price). Instead we reserve a
# flat $1 for a placeholder DST slot (see `DST_RESERVE_COST` /
# `DST_PLACEHOLDER_PPG` below), matching the $1-minimum-bid / $1-DST-reserve
# convention used in `analysis.lineup_optimizer`. Bench composition is
# restricted to RB/WR only (1-2 WR, 1-3 RB), matching
# `analysis.lineup_optimizer.optimize_espn_lineup`'s ESPN_BENCH composition
# rules, so the "best possible bench" doesn't waste a slot on a QB/TE.
STARTER_SLOTS = {"QB": 1, "RB": 2, "WR": 3, "TE": 1}
FLEX_SLOTS = 1
BENCH_SLOTS = 4
BENCH_WR_MIN, BENCH_WR_MAX = 1, 2
BENCH_RB_MIN, BENCH_RB_MAX = 1, 3
BENCH_POSITIONS = {"RB", "WR"}

# Every roster spot costs at least $1 in a salary-cap auction (no $0 bids).
MIN_PLAYER_COST = 1.0

# DST isn't priced on the WTP board at all, so we reserve a flat $1 for it
# (mirrors `analysis.lineup_optimizer`'s "$1 reserved for DST" convention)
# and show it as a blank placeholder slot worth ~5 points/game.
DST_RESERVE_COST = 1.0
DST_PLACEHOLDER_PPG = 5.0
NUM_WEEKS = 17

SLOT_ORDER = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "FLEX": 4, "DST": 5, "Bench": 6}


def _build_pool(
    state: DraftState,
    hypothetical_player_id: str | None = None,
    hypothetical_price: float | None = None,
    use_adjusted_cost: bool = False,
) -> pd.DataFrame:
    """
    Build the MILP input pool: my already-drafted players (locked, cost 0 —
    already sunk) plus every still-available player (optional, cost =
    current espn_av — the real $ auction price, not the wtp_price shadow
    valuation). If `hypothetical_player_id` names an available player, that
    one row is also marked locked (must be selected) so the optimizer is
    forced to include it, at its current espn_av counted against my
    remaining budget — or at `hypothetical_price` if given (used by
    `find_max_profitable_bid` to test paying more/less than espn_av for it).

    If `use_adjusted_cost` is True, available players' cost basis is
    `webapp.adjusted_cost.compute_adjusted_espn_av(state)` (a
    replay-derived, overpay/underpay-redistributed price) instead of raw
    `espn_av`. Locked/drafted (sunk-cost) players remain cost-0 regardless.
    """
    rows = []

    for p in state.my_players:
        rows.append({
            "player_id": p.player_id,
            "player_name": p.player_name,
            "position": p.position,
            "expected_points": p.expected_points,
            "cost": 0.0,
            "locked": True,
            "source": "mine",
        })

    adjusted_prices = compute_adjusted_espn_av(state) if use_adjusted_cost else None

    for p in state.available_players:
        is_hypothetical = hypothetical_player_id is not None and p.player_id == hypothetical_player_id
        if is_hypothetical and hypothetical_price is not None:
            cost = max(hypothetical_price, MIN_PLAYER_COST)
        elif adjusted_prices is not None:
            cost = max(adjusted_prices.get(p.player_id, p.espn_av), MIN_PLAYER_COST)
        else:
            cost = max(p.espn_av, MIN_PLAYER_COST)
        rows.append({
            "player_id": p.player_id,
            "player_name": p.player_name,
            "position": p.position,
            "expected_points": p.expected_points,
            # $1-minimum-bid convention: no roster spot is actually won for
            # $0 in a salary-cap auction, even if the shadow price rounds
            # down to it.
            "cost": cost,
            "locked": is_hypothetical,
            "source": "hypothetical" if is_hypothetical else "available",
        })

    return pd.DataFrame(rows)


def compute_optimal_team(
    state: DraftState,
    hypothetical_player_id: str | None = None,
    hypothetical_price: float | None = None,
    use_adjusted_cost: bool = False,
) -> dict:
    """
    Solve the MILP for my current optimal roster.

    Parameters
    ----------
    state:
        The live `DraftState`.
    hypothetical_player_id:
        Optional player_id of a still-available player to force into the
        solution (at their current espn_av) — the "what-if I win this
        player" preview. `state` is never mutated.
    hypothetical_price:
        Optional override for the forced player's cost (defaults to their
        current espn_av if omitted). Lets callers ask "what if I paid $X
        for this player?" — used by `find_max_profitable_bid`.
    use_adjusted_cost:
        If True, available players are priced at their market-adjusted
        cost basis (`webapp.adjusted_cost.compute_adjusted_espn_av`) instead
        of raw `espn_av`. Default False reproduces the classic behavior
        exactly.

    Returns a dict with:
      lineup              — DataFrame, one row per roster slot filled,
                             columns: slot, position, player_name,
                             expected_points, cost, source
      total_points         — sum of expected_points for starters (QB/RB/WR/TE/FLEX)
      total_cost           — sum of cost for every selected player (locked
                             players cost 0 since already paid)
      budget_remaining_after — my_budget_remaining minus total_cost
      hypothetical_player_id — echoed back for convenience

    Raises ValueError if the remaining pool can't fill the roster (e.g. not
    enough available players left at a position), or RuntimeError if the
    MILP solver doesn't find an optimal solution.
    """
    pool = _build_pool(
        state,
        hypothetical_player_id=hypothetical_player_id,
        hypothetical_price=hypothetical_price,
        use_adjusted_cost=use_adjusted_cost,
    )
    if pool.empty:
        raise ValueError("No players (drafted or available) to build a lineup from.")

    total_starter_slots = sum(STARTER_SLOTS.values()) + FLEX_SLOTS
    total_slots = total_starter_slots + BENCH_SLOTS

    # Quick feasibility guardrail: enough locked + available players overall
    # to fill every roster slot. Per-position infeasibility (e.g. not enough
    # WRs left) is instead surfaced by the solver status check below, since
    # locked players can overflow into FLEX/bench and are hard to bound
    # cheaply up front.
    if len(pool) < total_slots:
        raise ValueError(
            f"Not enough players ({len(pool)}) to fill {total_slots} roster slots."
        )

    prob = pulp.LpProblem("live_optimal_team", pulp.LpMaximize)

    players = pool.to_dict("records")
    n = len(players)

    y = [pulp.LpVariable(f"y_{i}", cat="Binary") for i in range(n)]  # starter (positional)
    z = [pulp.LpVariable(f"z_{i}", cat="Binary") for i in range(n)]  # FLEX
    b = [pulp.LpVariable(f"b_{i}", cat="Binary") for i in range(n)]  # bench

    for i, p in enumerate(players):
        if p["position"] not in FLEX_POSITIONS:
            prob += z[i] == 0

    for i, p in enumerate(players):
        if p["locked"]:
            prob += y[i] + z[i] + b[i] == 1
        else:
            prob += y[i] + z[i] + b[i] <= 1

    # Objective: maximise expected points among starters (positional + FLEX).
    # Bench players contribute 0 to the objective but still cost budget/roster
    # a spot, matching the convention used in analysis.lineup_optimizer.
    prob += pulp.lpSum(
        p["expected_points"] * (y[i] + z[i])
        for i, p in enumerate(players)
    )

    # Budget: locked players already cost 0 (sunk), so this only constrains
    # spend on newly-selected available/hypothetical players. $1 is set
    # aside up front for the DST placeholder slot (see below), since the
    # WTP board has no DST rows to actually bid on.
    prob += pulp.lpSum(
        p["cost"] * (y[i] + z[i] + b[i])
        for i, p in enumerate(players)
    ) <= state.my_budget_remaining - DST_RESERVE_COST

    for pos, need in STARTER_SLOTS.items():
        prob += pulp.lpSum(y[i] for i, p in enumerate(players) if p["position"] == pos) == need

    prob += pulp.lpSum(z) == FLEX_SLOTS
    prob += pulp.lpSum(b) == BENCH_SLOTS

    # Best possible bench = RB/WR only (matches
    # analysis.lineup_optimizer.optimize_espn_lineup's bench composition
    # rule). Already-mine (locked) QB/TE players are exempt since they must
    # occupy *some* slot regardless of position — only newly-selected
    # available players are restricted from bench.
    for i, p in enumerate(players):
        if p["position"] in ("QB", "TE") and not p["locked"]:
            prob += b[i] == 0

    bench_wr = pulp.lpSum(b[i] for i, p in enumerate(players) if p["position"] == "WR")
    bench_rb = pulp.lpSum(b[i] for i, p in enumerate(players) if p["position"] == "RB")
    prob += bench_wr >= BENCH_WR_MIN
    prob += bench_wr <= BENCH_WR_MAX
    prob += bench_rb >= BENCH_RB_MIN
    prob += bench_rb <= BENCH_RB_MAX

    status = prob.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError(
            f"No optimal lineup found (status: {pulp.LpStatus[status]}). "
            "Remaining budget or roster slots may be too tight."
        )

    rows = []
    for i, p in enumerate(players):
        if pulp.value(y[i]) == 1:
            rows.append({**p, "slot": p["position"]})
        elif pulp.value(z[i]) == 1:
            rows.append({**p, "slot": "FLEX"})
        elif pulp.value(b[i]) == 1:
            rows.append({**p, "slot": "Bench"})

    # Placeholder DST row: the WTP board has no DST rows to actually
    # optimize over, so we just reserve $1 and assume ~5 pts/game
    # (`DST_PLACEHOLDER_PPG`), matching `analysis.lineup_optimizer`'s
    # convention. Shown blank (no real player) in the lineup table.
    rows.append({
        "player_id": None,
        "player_name": "(DST — not modeled)",
        "position": "DST",
        "expected_points": DST_PLACEHOLDER_PPG * NUM_WEEKS,
        "cost": DST_RESERVE_COST,
        "source": "placeholder",
        "slot": "DST",
    })

    lineup = pd.DataFrame(rows)
    lineup["_slot_order"] = lineup["slot"].map(SLOT_ORDER)
    lineup = lineup.sort_values(["_slot_order", "position"]).drop(columns=["_slot_order", "locked"])
    lineup = lineup.reset_index(drop=True)

    starters = lineup[lineup["slot"] != "Bench"]
    total_points = float(starters["expected_points"].sum())
    total_cost = float(lineup["cost"].sum())

    return {
        "lineup": lineup,
        "total_points": total_points,
        "total_ppg": total_points / NUM_WEEKS,
        "total_cost": total_cost,
        "budget_remaining_after": state.my_budget_remaining - total_cost,
        "hypothetical_player_id": hypothetical_player_id,
    }


def find_max_profitable_bid(
    state: DraftState,
    player_id: str,
    tolerance_ppg: float = 1e-6,
    use_adjusted_cost: bool = False,
) -> dict:
    """
    Binary-search the highest price I could pay for `player_id` (a still-
    available player) such that forcing them into my roster at that price
    still yields an optimal lineup whose `total_ppg` is >= my current
    optimal lineup's `total_ppg` *without* them.

    Rationale: forcing a player into the solution at price X only tightens
    the shared budget constraint as X increases (every other player's cost
    is unaffected), so `total_ppg` is monotonically non-increasing in X.
    That makes binary search valid — above the returned price, you'd have
    to bench/downgrade someone else and your projected score would drop
    below what it is today; at or below it, paying that much is still
    "profitable" (net neutral-or-better vs. your current plan).

    Returns a dict with:
      player_id           — echoed back
      baseline_ppg         — my optimal lineup's total_ppg *without* this player
      max_bid               — highest whole-dollar price where total_ppg >= baseline_ppg
                              (0 if even $1 already drops your score)
      ppg_at_max_bid        — total_ppg if you paid `max_bid` for this player

    Raises ValueError if the player is already drafted or not found.
    """
    player = state.find_player(player_id)
    if player is None:
        raise ValueError(f"Unknown player_id: {player_id!r}")
    if player.drafted:
        raise ValueError(f"{player.player_name} is already drafted.")

    baseline = compute_optimal_team(
        state, hypothetical_player_id=None, use_adjusted_cost=use_adjusted_cost,
    )
    baseline_ppg = baseline["total_ppg"]

    # Upper bound: can't bid more than my remaining budget (minus the $1 DST
    # reserve baked into every solve).
    budget_cap = int(state.my_budget_remaining - DST_RESERVE_COST)
    if budget_cap < 1:
        return {
            "player_id": player_id,
            "baseline_ppg": baseline_ppg,
            "max_bid": 0,
            "ppg_at_max_bid": None,
        }

    def _ppg_at_price(price: int) -> float | None:
        try:
            result = compute_optimal_team(
                state, hypothetical_player_id=player_id, hypothetical_price=float(price),
                use_adjusted_cost=use_adjusted_cost,
            )
        except (ValueError, RuntimeError):
            # Infeasible at this price (e.g. no budget left to fill other
            # slots) — treat as "score drops" for the purposes of the search.
            return None
        return result["total_ppg"]

    # If even $1 already can't beat baseline, max bid is $0.
    ppg_at_1 = _ppg_at_price(1)
    if ppg_at_1 is None or ppg_at_1 + tolerance_ppg < baseline_ppg:
        return {
            "player_id": player_id,
            "baseline_ppg": baseline_ppg,
            "max_bid": 0,
            "ppg_at_max_bid": ppg_at_1,
        }

    lo, hi = 1, budget_cap
    best_price, best_ppg = 1, ppg_at_1
    while lo <= hi:
        mid = (lo + hi) // 2
        ppg = _ppg_at_price(mid)
        if ppg is not None and ppg + tolerance_ppg >= baseline_ppg:
            best_price, best_ppg = mid, ppg
            lo = mid + 1
        else:
            hi = mid - 1

    return {
        "player_id": player_id,
        "baseline_ppg": baseline_ppg,
        "max_bid": best_price,
        "ppg_at_max_bid": best_ppg,
    }
