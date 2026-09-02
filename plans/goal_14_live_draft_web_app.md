# Goal 14: Live Draft Web App

## Motivation

From `plans/future_plans.md`:

> Web app that shows the willingness to pay plot but allows me to update as
> the players are chosen
> - Should remove chosen players from the board
> - Should update the shadow price with the REAL price as the price becomes
>   known, and iterate the plot
> - Should constantly show my "optimal" team assuming everyone goes at the
>   estimated price

The existing WTP workflow (`analysis/willingness_to_pay.py`,
`analysis/visualize_par.py`, `analysis/lineup_optimizer.py`) is entirely
batch/offline — it's run once before the draft to produce static CSVs, PNGs,
and HTML charts. During a live salary-cap auction draft, prices and player
availability change every few seconds, so a static pre-draft chart quickly
goes stale. This goal is to turn the offline analysis into a small
interactive web app that can be kept open on a second screen during the
draft and updated in real time as picks happen.

## Plan

### Step 1: Define scope and data model
- Decide MVP scope: single-user, single-draft, local-only (no auth/multi-user
  needed since it's just for personal use during the draft).
- Design an in-memory/session "draft state" model: list of all players with
  current WTP price, drafted/available flag, actual price paid (if drafted),
  and which roster slot/team they belong to.
- Decide how initial state is seeded — reuse existing
  `willingness_to_pay_blended_sched.csv` (or latest WTP output) as the
  starting board.
- Decide on persistence: simple flat file (JSON/CSV) written after each
  update so the draft can be resumed if the app restarts.
- Identify which existing `analysis/` modules can be imported directly
  (`willingness_to_pay.py`, `lineup_optimizer.py`) vs. need refactoring into
  reusable functions (currently many are script-style with `if __name__`
  blocks and CLI args).

### Step 2: Choose and scaffold the tech stack
- Evaluate lightweight Python web frameworks that fit a solo/local tool:
  **Streamlit** (fastest to build, good for data-app + Plotly charts,
  built-in rerun-on-interaction model) vs. **Flask/FastAPI + small JS
  frontend** (more control, more work).
- Lean toward Streamlit given the project is already Python/pandas/Plotly-
  centric and there's no need for multi-user concurrency.
- Scaffold a new `webapp/` (or `app/`) directory with an entry-point script,
  add `streamlit` (or chosen framework) to `requirements.txt`.
- Confirm it can run locally via `streamlit run` and hot-reload during
  development.

### Step 3: Build the "remove drafted players" board
- Build a UI table/list of all players with current WTP price, position,
  and a "Mark as drafted" action (dropdown/search + button, or click-to-
  select on the chart itself).
- On marking a player drafted, remove/gray them out from the WTP scatter
  plot (`plot_wtp_vs_espn`-style chart) and the selectable player list.
- Add an "undo last pick" action in case of misclicks during a live, fast-
  moving draft.
- Persist the updated draft state after each action (per Step 1's
  persistence choice) so nothing is lost mid-draft.

### Step 4: Recompute shadow prices from real prices
- When a player is marked drafted, prompt for the actual price paid.
- Feed the actual price back into the WTP/shadow-price calculation (likely
  reusing or extending `analysis/willingness_to_pay.py`'s optimization logic)
  so remaining players' shadow prices reflect updated remaining
  budget/roster needs across the league, not just the original static
  computation.
- Re-render the WTP chart and price table after each recompute so the board
  always reflects "current" market conditions, not the pre-draft snapshot.
- Add basic guardrails: handle edge cases like remaining budget going
  negative for a competitor, or a position being fully drafted league-wide.

### Step 5: Live "optimal team" panel
- Wire in `analysis/lineup_optimizer.py` to continuously compute my optimal
  roster given: players already drafted by me (locked in at actual price),
  players still available (at current shadow price), and my remaining
  budget/roster slots.
- Display this as a persistent side panel showing projected optimal lineup,
  total projected points, and remaining budget — refreshed after every pick.
- Add a lightweight "what-if" mode: let me preview how the optimal team
  changes if I hypothetically win a specific available player at their
  current shadow price, without actually marking them drafted.
- Polish pass: performance (recompute should feel instant during a live
  draft), and a quick manual test running through a mock draft end-to-end
  before using it in a real auction.
