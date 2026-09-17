"""
Goal 15: Market-adjusted pricing for the live lineup optimizer.

Overpays/underpays vs. estimated (`espn_av`) price shift the real remaining
market: if a rival pays $10 over "value" for a player, that $10 has to come
from *somewhere* — the market's implicit valuation of the remaining player
pool shifts down (cheap players get cheaper) to compensate, and vice versa
for underpays.

This module implements a pure, replay-based "adjusted cost basis" derived
from `DraftState.pick_history`, kept entirely separate from `wtp_price` (the
theoretical fair-value shadow price) and from raw `espn_av` (untouched
everywhere else in the codebase — CSVs, other analysis modules, classic
mode). It's opt-in and used only by `webapp.live_optimizer` when
`use_adjusted_cost=True`.

No Streamlit dependency — unit-testable standalone.
"""

from __future__ import annotations

from typing import Dict

FLOOR = 1.0


def peel(prices: Dict[str, float], delta: float, floor: float = FLOOR) -> Dict[str, float]:
    """
    Redistribute `delta` dollars across `prices` (player_id -> price) by
    peeling from tiers of the price distribution, returning a *new* dict
    (input is not mutated).

    delta > 0 (overpay): the excess spend has to be "recovered" from the
        rest of the market, so the cheapest players get cheaper first —
        peel $1 off every player at the current price floor, then move up
        to the next-cheapest tier, and so on, until `delta` dollars have
        been removed in total (never going below `floor`).

    delta < 0 (underpay): the market "saved" money, so it flows back to the
        priciest players first — the single most expensive player gets +$1,
        then (if more remains) the next-most-expensive, cascading down
        through cheaper tiers if `abs(delta)` exceeds the number of
        distinct/available players.

    Deterministic tie-break on partial tiers: within a tier of equal price,
    players are chosen in ascending `player_id` (name) order.

    No player's price ever moves below `floor`.
    """
    if not prices:
        return dict(prices)

    result = dict(prices)
    remaining = round(delta, 6)
    if remaining == 0:
        return result

    if remaining > 0:
        # Overpay: peel $1 off the cheapest tier upward, one dollar of total
        # reduction per (tier, pass), skipping anyone already at the floor.
        while remaining > 1e-9:
            eligible = [pid for pid, price in result.items() if price > floor]
            if not eligible:
                break  # everyone's at the floor already; can't peel further
            min_price = min(result[pid] for pid in eligible)
            tier = sorted(
                (pid for pid in eligible if result[pid] == min_price)
            )
            for pid in tier:
                if remaining <= 1e-9:
                    break
                take = min(1.0, remaining, result[pid] - floor)
                if take <= 0:
                    continue
                result[pid] -= take
                remaining -= take
    else:
        # Underpay: give $1 to the priciest tier downward.
        need = -remaining
        while need > 1e-9:
            max_price = max(result.values())
            tier = sorted(
                (pid for pid, price in result.items() if price == max_price)
            )
            for pid in tier:
                if need <= 1e-9:
                    break
                give = min(1.0, need)
                result[pid] += give
                need -= give
            if not tier:
                break

    return result


def compute_adjusted_espn_av(state) -> Dict[str, float]:
    """
    Replay `state.pick_history` in order, starting every player at their
    original `espn_av`, redistributing the over/underpay delta of each pick
    across the pool of players still available *at that point in the
    replay* via `peel()`.

    Recomputed fresh from scratch on every call (no stored mutable state),
    so `DraftState.undo_last_pick()` / `DraftState.reset()` are reflected
    automatically the next time this is called — there's nothing to
    invalidate or keep in sync.

    Returns a dict of player_id -> adjusted price for every player in
    `state.players` (drafted players simply keep their final adjusted price
    frozen at the time they were picked; only picks *after* a player is
    drafted no longer affect them).
    """
    current = {p.player_id: float(p.espn_av) for p in state.players}
    drafted_ids: set[str] = set()

    for pick in state.pick_history:
        pid = pick.player_id
        price_before = current.get(pid, 0.0)
        delta = pick.price_paid - price_before

        drafted_ids.add(pid)
        pool = {k: v for k, v in current.items() if k not in drafted_ids}

        if delta != 0 and pool:
            adjusted_pool = peel(pool, delta)
            current.update(adjusted_pool)

        # Freeze the drafted player's adjusted price at the price actually
        # paid (it no longer participates in future peels either way, since
        # it's excluded from `pool` above).
        current[pid] = pick.price_paid

    return current
