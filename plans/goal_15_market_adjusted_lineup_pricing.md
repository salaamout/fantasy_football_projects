# Goal 15: Market-Adjusted Pricing Toggle for Live Lineup Optimizer

## Motivation

Overpays/underpays vs. estimated price shift the real remaining market.
This adds a toggleable "market-adjusted" cost basis (separate from
`wtp_price`) used only by `webapp.live_optimizer`, leaving all other
$ values (classic ESPN AV, WTP CSVs/exports, other analysis modules)
untouched.

## Plan

### Step 1: Implement tiered redistribution primitive
- New module `webapp/adjusted_cost.py` with a pure `peel(prices: dict, delta: float, floor=1.0) -> dict`.
- `delta > 0` (overpay): peel from the cheapest tier upward ($2→1, then $3→2→1, ...).
- `delta < 0` (underpay): peel from the priciest tier downward (top-N get +$1, cascade if N > pool size).
- Deterministic tie-break on partial tiers: sort by `player_id` (name) ascending.
- No player price ever goes below `floor` ($1).

### Step 2: Replay-based adjusted cost computation
- Add `compute_adjusted_espn_av(state: DraftState) -> dict[player_id, float]` in `adjusted_cost.py`.
- Start every player at their original `espn_av`; walk `state.pick_history` in order.
- Per pick: `delta = price_paid - current_adjusted_price[player]`, then `peel()` over the still-available pool at that point.
- Recomputed fresh every call (no stored mutable state) so undo/reset stay correct automatically.

### Step 3: Wire toggle into optimizer functions
- Add `use_adjusted_cost: bool = False` param to `_build_pool`, `compute_optimal_team`, `find_max_profitable_bid` in `webapp/live_optimizer.py`.
- When `True`, available-player cost = `compute_adjusted_espn_av(state)[player_id]` instead of raw `espn_av`.
- Locked/drafted players remain cost-0 regardless (sunk cost, unaffected by toggle).
- DST placeholder reserve/logic unchanged in both modes.

### Step 4: Surface toggle in the Streamlit UI
- Add a checkbox (default off/"Classic") in `app.py`, e.g. near `_optimal_team_panel`, stored in `st.session_state`.
- Pass the checkbox value as `use_adjusted_cost` into `compute_optimal_team` and `find_max_profitable_bid` calls.
- Label clearly: "Classic (ESPN AV)" vs. "Market-adjusted (overpay/underpay redistribution)".
- No changes to WTP price display, CSVs, or other panels.

### Step 5: Tests
- Unit tests for `peel()`: whole-tier overpay, partial-tier tie-break, underpay cascade beyond pool size, floor clamping.
- Unit tests for `compute_adjusted_espn_av`: single pick, multi-pick chained deltas, the $25→$35→$25 re-estimate scenario, and undo-last-pick correctness.
- Regression test confirming `use_adjusted_cost=False` reproduces current `compute_optimal_team`/`find_max_profitable_bid` output exactly.
