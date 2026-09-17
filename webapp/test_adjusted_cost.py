"""Tests for Goal 15: market-adjusted pricing (`webapp.adjusted_cost`)."""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from webapp.adjusted_cost import peel, compute_adjusted_espn_av
from webapp.draft_state import DraftState, DraftPick, Player


# ---------------------------------------------------------------------------
# peel()
# ---------------------------------------------------------------------------

def test_peel_whole_tier_overpay():
    # Two players tied at the cheapest price ($2): a $2 overpay should take
    # $1 off each of them (a whole tier), never dipping below floor.
    prices = {"a": 2.0, "b": 2.0, "c": 5.0}
    result = peel(prices, delta=2.0)
    assert result["a"] == 1.0
    assert result["b"] == 1.0
    assert result["c"] == 5.0  # untouched, wasn't in the cheapest tier


def test_peel_partial_tier_tiebreak_ascending_player_id():
    # Three players tied at $2; only enough delta to peel $1 off ONE of
    # them. Tie-break: ascending player_id.
    prices = {"charlie": 2.0, "alice": 2.0, "bob": 2.0}
    result = peel(prices, delta=1.0)
    assert result["alice"] == 1.0
    assert result["bob"] == 2.0
    assert result["charlie"] == 2.0


def test_peel_overpay_cascades_up_tiers():
    # Cheapest tier is a single player at floor ($1) already -- can't peel
    # from them, so it must move up to the next tier.
    prices = {"a": 1.0, "b": 3.0, "c": 3.0, "d": 10.0}
    result = peel(prices, delta=2.0)
    assert result["a"] == 1.0  # already at floor, untouched
    assert result["b"] == 2.0
    assert result["c"] == 2.0
    assert result["d"] == 10.0


def test_peel_underpay_single_player_gets_dollar():
    prices = {"a": 5.0, "b": 10.0, "c": 3.0}
    result = peel(prices, delta=-1.0)
    assert result["b"] == 11.0
    assert result["a"] == 5.0
    assert result["c"] == 3.0


def test_peel_underpay_cascades_beyond_pool_size():
    # Only 2 players, but the underpay is $3 -- cascades: priciest tier
    # gets +$1 each pass, repeating once all players have received a pass.
    prices = {"a": 5.0, "b": 5.0}
    result = peel(prices, delta=-3.0)
    # $3 spread across a 2-player tied-tier: first pass gives both +$1
    # (uses $2), second pass only $1 left -> ascending tie-break -> "a".
    assert result["a"] == 7.0
    assert result["b"] == 6.0


def test_peel_floor_clamping_never_goes_below_floor():
    prices = {"a": 1.0, "b": 1.0}
    result = peel(prices, delta=5.0, floor=1.0)
    # Nobody can be peeled since both are already at floor.
    assert result == {"a": 1.0, "b": 1.0}


def test_peel_zero_delta_is_noop():
    prices = {"a": 5.0, "b": 2.0}
    result = peel(prices, delta=0.0)
    assert result == prices


def test_peel_does_not_mutate_input():
    prices = {"a": 5.0, "b": 2.0}
    original = dict(prices)
    peel(prices, delta=1.0)
    assert prices == original


# ---------------------------------------------------------------------------
# compute_adjusted_espn_av()
# ---------------------------------------------------------------------------

def _make_state(espn_avs: dict[str, float]) -> DraftState:
    players = [
        Player(position="WR", positional_rank=i + 1, player_name=name, espn_av=av)
        for i, (name, av) in enumerate(espn_avs.items())
    ]
    return DraftState(players=players, my_budget_total=200)


def test_compute_adjusted_single_pick_overpay():
    state = _make_state({"A": 20.0, "B": 5.0, "C": 5.0, "D": 30.0})
    # A drafted for $30 (a $10 overpay vs its $20 espn_av).
    state.pick_history.append(DraftPick(player_id="A", price_paid=30.0, drafted_by="other"))

    adjusted = compute_adjusted_espn_av(state)
    assert adjusted["A"] == 30.0  # frozen at price paid
    # $10 overpay peeled from the cheapest remaining tier (B, C tied at $5)
    # upward; D ($30) untouched until cheaper tiers are exhausted.
    assert adjusted["B"] + adjusted["C"] == 5.0 + 5.0 - 8.0  # both floored at $1 (8 removed)
    assert adjusted["B"] == 1.0
    assert adjusted["C"] == 1.0
    assert adjusted["D"] == 30.0 - 2.0  # remaining $2 of the $10 peeled from D


def test_compute_adjusted_multi_pick_chained_deltas():
    state = _make_state({"A": 20.0, "B": 10.0, "C": 10.0})
    state.pick_history.append(DraftPick(player_id="A", price_paid=20.0, drafted_by="other"))
    # No delta from A's pick (paid exactly espn_av).
    state.pick_history.append(DraftPick(player_id="B", price_paid=15.0, drafted_by="other"))
    # $5 overpay on B, peeled from remaining pool (just C).

    adjusted = compute_adjusted_espn_av(state)
    assert adjusted["A"] == 20.0
    assert adjusted["B"] == 15.0
    assert adjusted["C"] == 5.0  # 10 - 5


def test_compute_adjusted_reestimate_scenario_25_35_25():
    # A player estimated at $25 goes for $35 (overpay), which then should
    # push remaining prices down; a later underpay elsewhere pushes them
    # back up. Sanity check the mechanism chains correctly across picks.
    state = _make_state({"A": 25.0, "B": 20.0, "C": 20.0, "D": 3.0})
    state.pick_history.append(DraftPick(player_id="A", price_paid=35.0, drafted_by="other"))
    adjusted_after_overpay = compute_adjusted_espn_av(state)
    assert adjusted_after_overpay["A"] == 35.0
    total_remaining_after = adjusted_after_overpay["B"] + adjusted_after_overpay["C"] + adjusted_after_overpay["D"]
    assert total_remaining_after == (20.0 + 20.0 + 3.0) - 10.0

    # Now B is drafted at $5 under its adjusted price -> underpay flows back
    # to the priciest remaining player (C).
    price_before_b = adjusted_after_overpay["B"]
    state.pick_history.append(
        DraftPick(player_id="B", price_paid=price_before_b - 5.0, drafted_by="other")
    )
    adjusted_final = compute_adjusted_espn_av(state)
    assert adjusted_final["B"] == price_before_b - 5.0
    # The $5 underpay flows to whichever of C/D is priciest at that point.
    assert (adjusted_final["C"] + adjusted_final["D"]) == total_remaining_after - adjusted_after_overpay["B"] + 5.0


def test_compute_adjusted_undo_last_pick_correctness():
    # Since compute_adjusted_espn_av is a pure function of pick_history,
    # popping the last pick (as DraftState.undo_last_pick does) must fully
    # revert the adjustment with no leftover state.
    state = _make_state({"A": 20.0, "B": 10.0, "C": 10.0})
    baseline = compute_adjusted_espn_av(state)

    state.pick_history.append(DraftPick(player_id="A", price_paid=30.0, drafted_by="other"))
    _ = compute_adjusted_espn_av(state)  # simulate a call mid-draft

    state.pick_history.pop()  # undo_last_pick's core behavior
    reverted = compute_adjusted_espn_av(state)
    assert reverted == baseline


def test_compute_adjusted_no_picks_returns_original_espn_av():
    state = _make_state({"A": 20.0, "B": 10.0})
    adjusted = compute_adjusted_espn_av(state)
    assert adjusted == {"A": 20.0, "B": 10.0}
