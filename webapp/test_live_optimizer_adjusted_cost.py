"""Regression test for Goal 15 Step 3: `use_adjusted_cost=False` must
reproduce classic (pre-Goal-15) `compute_optimal_team` /
`find_max_profitable_bid` output exactly."""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from webapp.draft_state import DraftState, DraftPick, Player
from webapp.live_optimizer import compute_optimal_team, find_max_profitable_bid


def _make_full_state() -> DraftState:
    """Enough players at every position to fill a full roster (starters +
    FLEX + bench), with a couple of picks already made so `pick_history` is
    non-empty (exercising the adjusted-cost replay path when enabled)."""
    players = []

    def add(pos, n, base_points, base_av):
        for i in range(n):
            players.append(
                Player(
                    position=pos,
                    positional_rank=i + 1,
                    player_name=f"{pos}{i + 1}",
                    expected_points=base_points - i * 5,
                    espn_av=max(base_av - i * 2, 1.0),
                )
            )

    add("QB", 3, 300, 20)
    add("RB", 8, 250, 30)
    add("WR", 8, 240, 28)
    add("TE", 3, 150, 15)

    state = DraftState(players=players, my_budget_total=400)

    # A couple of picks: one by me, one by a rival at a different price than
    # espn_av (introduces a nonzero delta, relevant when adjusted cost is
    # toggled on -- but must NOT affect the classic/False path at all).
    state.draft_player("RB1", price_paid=30.0, drafted_by="me")
    state.draft_player("WR1", price_paid=35.0, drafted_by="rival")  # overpay vs espn_av=28
    return state


def test_compute_optimal_team_use_adjusted_cost_false_matches_classic():
    state = _make_full_state()

    classic = compute_optimal_team(state, hypothetical_player_id=None)
    explicit_false = compute_optimal_team(
        state, hypothetical_player_id=None, use_adjusted_cost=False,
    )

    assert classic["total_points"] == explicit_false["total_points"]
    assert classic["total_cost"] == explicit_false["total_cost"]
    assert classic["budget_remaining_after"] == explicit_false["budget_remaining_after"]
    assert classic["lineup"].equals(explicit_false["lineup"])


def test_find_max_profitable_bid_use_adjusted_cost_false_matches_classic():
    state = _make_full_state()
    player_id = "WR2"

    classic = find_max_profitable_bid(state, player_id)
    explicit_false = find_max_profitable_bid(state, player_id, use_adjusted_cost=False)

    assert classic == explicit_false


def test_use_adjusted_cost_true_diverges_when_picks_have_deltas():
    # Sanity check the toggle actually does something when True (otherwise
    # the regression tests above would be vacuous).
    state = _make_full_state()

    classic = compute_optimal_team(state, hypothetical_player_id=None, use_adjusted_cost=False)
    adjusted = compute_optimal_team(state, hypothetical_player_id=None, use_adjusted_cost=True)

    # Total cost among selected available (non-locked, non-placeholder)
    # players should differ, since WR1's overpay redistributes cost across
    # the rest of the pool.
    assert classic["total_cost"] != adjusted["total_cost"] or classic["lineup"]["cost"].tolist() != adjusted["lineup"]["cost"].tolist()
