"""
Goal 14 Step 4: Recompute shadow prices ("live WTP") from real draft prices.

As players get drafted (at real, observed prices), the 12-team competitive
market modelled by `analysis.willingness_to_pay.solve_lp_relaxation` has less
remaining budget and fewer remaining roster slots to fill. This module
re-solves that LP over just the *remaining* (undrafted) player pool, with the
market budget and positional slot quotas shrunk to reflect what's actually
still available league-wide — so the shadow price (WTP) of every player still
on the board updates to reflect current market conditions, not the static
pre-draft snapshot.

Simplifying assumptions (documented, MVP-appropriate for a single live draft):
  * Every drafted player is assumed to have filled a real roster need at its
    own position (starters first, then that position's bench allocation).
    We don't track which literal team/slot a rival's pick filled, only the
    league-wide counts, so this is an approximation.
  * FLEX slots (12 total) are left unreduced by drafted counts — with only
    12 FLEX slots league-wide their marginal effect on shadow prices is
    small, and we don't reliably know which drafted players "used" a FLEX
    slot vs. a dedicated positional slot.
  * The market budget is reduced by the total amount actually spent by
    *everyone* (me + rivals) so far, since that money has left the
    12-team combined market.

This module has no Streamlit dependency so it can be unit-tested standalone.
"""

from __future__ import annotations

import json
import sys
import time
import traceback
import warnings
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Where to dump a snapshot of the LP inputs whenever `recompute_shadow_prices`
# fails, so a failure caught live in the running app can be inspected/replayed
# after the fact instead of only seeing the error string in the UI.
DEBUG_DUMP_DIR = _ROOT / "output" / "live_pricing_debug"

from analysis.willingness_to_pay import (
    MARKET_BUDGET,
    NUM_TEAMS,
    compute_wtp,
    solve_lp_relaxation,
)
from analysis.lineup_optimizer import LINEUP_SLOTS, CROSS_BENCH_SLOTS, FLEX_POSITIONS

from webapp.draft_state import DraftState, Player

# Minimum bid floor — mirrors `attach_prices(min_price=1.0)` in the batch WTP pipeline.
MIN_PRICE = 1.0

# Guardrail floors so the LP never gets a degenerate (<=0) budget or goes
# infeasible once a position is nearly exhausted league-wide.
MIN_MARKET_BUDGET = 12.0  # $1/team floor across the league


def compute_remaining_market_overrides(state: DraftState) -> dict:
    """
    Derive the `overrides` dict for `solve_lp_relaxation`, reflecting the
    league-wide budget and roster slots still remaining given the players
    already drafted (at any price, by anyone).
    """
    drafted = state.drafted_players
    drafted_counts = {"QB": 0, "RB": 0, "WR": 0, "TE": 0}
    total_spent = 0.0
    for p in drafted:
        if p.position in drafted_counts:
            drafted_counts[p.position] += 1
        total_spent += p.price_paid or 0.0

    qb_starters = LINEUP_SLOTS["QB"] * NUM_TEAMS
    te_starters = LINEUP_SLOTS["TE"] * NUM_TEAMS
    rb_starters = LINEUP_SLOTS["RB"] * NUM_TEAMS
    wr_starters = LINEUP_SLOTS["WR"] * NUM_TEAMS
    bench_rb_cap = 3 * NUM_TEAMS
    bench_wr_cap = 2 * NUM_TEAMS
    flex_slots = LINEUP_SLOTS["FLEX"] * NUM_TEAMS

    qb_slots = max(0, qb_starters - drafted_counts["QB"])
    te_slots = max(0, te_starters - drafted_counts["TE"])

    # RB/WR: assume drafted players fill starters first, then bench.
    rb_starters_filled = min(drafted_counts["RB"], rb_starters)
    rb_slots = rb_starters - rb_starters_filled
    bench_rb_used = min(max(0, drafted_counts["RB"] - rb_starters), bench_rb_cap)
    bench_rb_max = bench_rb_cap - bench_rb_used

    wr_starters_filled = min(drafted_counts["WR"], wr_starters)
    wr_slots = wr_starters - wr_starters_filled
    bench_wr_used = min(max(0, drafted_counts["WR"] - wr_starters), bench_wr_cap)
    bench_wr_max = bench_wr_cap - bench_wr_used

    bench_total = bench_rb_max + bench_wr_max

    market_budget = max(MIN_MARKET_BUDGET, MARKET_BUDGET - total_spent)

    return {
        "market_budget": market_budget,
        "qb_slots": qb_slots,
        "rb_slots": rb_slots,
        "wr_slots": wr_slots,
        "te_slots": te_slots,
        "flex_slots": flex_slots,
        "bench_total": bench_total,
        "bench_rb_max": bench_rb_max,
        "bench_wr_max": bench_wr_max,
    }


def _available_player_pool(state: DraftState) -> pd.DataFrame:
    """Build the LP input DataFrame (position, par_points, flex_par_points,
    price, positional_rank) from the still-available players in `state`."""
    rows = []
    for p in state.available_players:
        rows.append({
            "position": p.position,
            "positional_rank": p.positional_rank,
            "player_name": p.player_name,
            "par_points": p.par_points,
            "flex_par_points": p.par_points if p.position in FLEX_POSITIONS else 0.0,
            "price": max(p.method1_av, MIN_PRICE),
        })
    return pd.DataFrame(rows)


def _dump_debug_snapshot(
    state: DraftState,
    pool: pd.DataFrame,
    overrides: dict,
    error: Exception,
) -> Path | None:
    """
    Persist everything needed to replay a failed `recompute_shadow_prices`
    call offline: the raw error, the LP overrides, the exact available-player
    pool passed to `solve_lp_relaxation`, and the drafted-picks history that
    produced this state. Written to `output/live_pricing_debug/` with a
    timestamped filename so repeated failures don't clobber each other.

    Returns the path written, or None if the dump itself failed (in which
    case we swallow the secondary error — a debug dump must never crash the
    app on top of the original failure).
    """
    try:
        DEBUG_DUMP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dump_path = DEBUG_DUMP_DIR / f"recompute_failure_{stamp}.json"
        payload = {
            "timestamp": stamp,
            "error": str(error),
            "error_type": type(error).__name__,
            "traceback": traceback.format_exc(),
            "overrides": overrides,
            "pool_len": len(pool),
            "pool_position_counts": pool["position"].value_counts().to_dict() if not pool.empty else {},
            "pool": pool.to_dict(orient="records"),
            "drafted_players": [
                {
                    "player_name": p.player_name,
                    "position": p.position,
                    "price_paid": p.price_paid,
                    "drafted_by": p.drafted_by,
                }
                for p in state.drafted_players
            ],
            "pick_history": [
                {
                    "player_id": pick.player_id,
                    "price_paid": pick.price_paid,
                    "drafted_by": pick.drafted_by,
                }
                for pick in state.pick_history
            ],
            "my_budget_total": state.my_budget_total,
            "my_budget_spent": state.my_budget_spent,
        }
        dump_path.write_text(json.dumps(payload, indent=2, default=str))
        return dump_path
    except Exception:
        # Never let debug-dumping itself break the caller.
        return None


def recompute_shadow_prices(state: DraftState) -> str | None:
    """
    Re-solve the market LP over the remaining available players and update
    each available `Player.wtp_price` in place.

    Returns an error/warning message (str) if the recompute could not be
    performed (e.g. the remaining pool is too shallow / infeasible), leaving
    the previous `wtp_price` values untouched. Returns None on success.

    On failure, a full debug snapshot (LP overrides, pool, pick history) is
    written to `output/live_pricing_debug/` so the exact failing inputs can
    be replayed and diagnosed after the fact — see `_dump_debug_snapshot`.
    """
    pool = _available_player_pool(state)
    if pool.empty:
        return "No available players remain — nothing to recompute."

    overrides = compute_remaining_market_overrides(state)

    # Guardrail: if any position's remaining demand exceeds what's actually
    # left in the pool, shrink the quota rather than let the LP raise.
    pool_counts = pool["position"].value_counts().to_dict()
    for pos, key in [("QB", "qb_slots"), ("RB", "rb_slots"), ("WR", "wr_slots"), ("TE", "te_slots")]:
        overrides[key] = min(overrides[key], pool_counts.get(pos, 0))

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            pool_with_duals, lambda_eq, slot_duals = solve_lp_relaxation(
                pool, overrides=overrides
            )
        priced = compute_wtp(pool_with_duals, lambda_eq, slot_duals)
    except (ValueError, RuntimeError) as e:
        dump_path = _dump_debug_snapshot(state, pool, overrides, e)
        msg = f"Could not recompute shadow prices: {e}"
        if dump_path is not None:
            msg += f" (debug snapshot saved to {dump_path.relative_to(_ROOT)})"
        return msg

    wtp_by_name = dict(zip(priced["player_name"], priced["wtp_price"]))
    for p in state.available_players:
        if p.player_name in wtp_by_name:
            p.wtp_price = float(wtp_by_name[p.player_name])

    state._maybe_save()
    return None
