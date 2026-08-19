# Goal 9: Principled "Willingness to Pay" by Position/Rank

## Problem Statement

Two existing methods produce different signals about player value:

1. **`aggregate_par.py` auction values** — Distributes a fixed league-wide starter budget
   ($2,280 = 12 teams × $190) proportionally to positive PAR across all starter slots.
   Formula: `auction_value = (mean_par / Σ positive_PAR) × $2,280`

2. **Cross-source optimizer** (`lineup_optimizer.py`) — Given *external* market prices
   (ESPN / Ringer), finds the starting lineup that maximises expected PAR within a
   $200 personal budget using a MILP.

These disagree because they answer **different questions**:
- Method 1 asks: *"What fraction of the league's total dollars should flow to this slot?"*
- Method 2 asks: *"Given market prices, which players maximise my lineup?"*

Neither method answers: **"What is the most I should be willing to pay for RB1 (or QB4, etc.)
in order to maximise my own starting lineup, ignoring market prices entirely?"**

---

## What "Willingness to Pay" Really Means Here

For a personal auction budget of $200 with lineup slots
`QB×1, RB×2, WR×3, TE×1, FLEX×1(RB/WR/TE), DST×1`:

> **Max WTP for slot S** = the highest price at which buying slot S is still part of an
> optimal starting lineup, given that all other slots are bought at their own max WTP prices.

This is a **shadow-price / dual-value** concept. It can also be framed as:
*"At what price P does this slot break even — i.e., the incremental expected points it adds
equals the expected points I could buy with P dollars allocated to other slots?"*

This is fundamentally different from Method 1's proportional PAR allocation, which doesn't
model the constraint that **you personally have $200 and specific slot requirements**.

---

## Root Cause of the Disagreement

| Dimension | Method 1 (PAR Auction Value) | WTP Approach |
|---|---|---|
| Budget scope | League-wide ($2,280) | Per-team ($200) |
| Slot structure | Proportional, no FLEX | Explicit QB/RB/WR/TE/FLEX constraints |
| Replacement baseline | Starter or waiver | Opportunity cost of next-best dollar spend |
| Output | "Fair-market price" for each slot | "Max you should bid to stay in an optimal lineup" |
| DST | Included in pool | Typically pre-assigned cheap |

Because Method 1 uses a **global proportional allocation**, it will over-value scarce
high-PAR positions (e.g. elite QBs) relative to what a single manager can actually exploit
(you only start 1 QB). The cross-source MILP already implicitly knows this — it will never
draft two QBs — which is why the two methods diverge most noticeably at QB and TE.

---

## Plan

### Step 1 — Build a `HistoricalPointsTable`
*File: `analysis/willingness_to_pay.py` (new)*

- Reuse `build_avg_points_lookup()` from `calculate_par.py` to get
  `avg_points[(position, rank)]` and `avg_flex_points[(position, rank)]`
  from seasons 2021–2025 (17-game seasons only).
- Store as a tidy DataFrame:
  `position | positional_rank | avg_points | avg_flex_points`
- This is the "expected value" we're pricing.

### Step 2 — Generate a Candidate Player Pool
- For each `(position, rank)` pair within starter + bench range, create one "slot player"
  with their historical average points attached.
- This is the same structure as the rankings passed into `optimize_lineup()` today,
  but with prices initially unknown (we're solving for them, not given them).

### Step 3 — Compute Shadow Prices via MILP Dual Solution

The MILP from `optimize_lineup()` already maximises expected PAR subject to a budget.
When you solve an LP relaxation, the **dual variable on the budget constraint** gives you
the marginal value of one additional dollar — the system's implicit "exchange rate."

For each slot `(position, rank)`:

1. Solve the LP relaxation of the lineup optimizer with:
   - **Prices set to the historical PAR-proportional values** (Method 1) as a starting point.
   - Record the dual variable `λ` (shadow price of the $200 budget constraint).
2. **Max WTP for slot S** = `expected_points[S] / λ`
   - Interpretation: slot S is worth buying at any price below this;
     above it, a dollar is better spent elsewhere.

This gives a true per-slot price that is **internally consistent** with your lineup
structure and budget.

### Step 4 — Iterative / Sensitivity Approach (alternative/complement)

If the LP dual is hard to extract cleanly from PuLP, use a sensitivity sweep:

For each slot `(position, rank)`:
1. Fix all *other* slots at their shadow prices.
2. Binary search on the price of this slot until the MILP just stops selecting it.
3. That price is the max WTP.

This is more computationally expensive but more interpretable and doesn't require
accessing dual variables.

### Step 5 — Compare to Existing Methods

Produce a table and chart:

| Roster Slot | Method 1 AV | Cross-Source Price | Shadow Price (WTP) |
|---|---|---|---|
| RB1 | $X | $Y | $Z |
| QB1 | $X | $Y | $Z |
| ... | | | |

Key diagnostic questions to answer:
- Where do Method 1 and WTP agree? (These are correctly priced slots.)
- Where does Method 1 overprice? (High-PAR scarce positions like elite QB/TE.)
- Where does Method 1 underprice? (High-volume positions like RB/WR mid-tiers.)
- Does the cross-source optimizer exploit the overpriced slots (suggesting market
  mispricing) or the underpriced ones?

### Step 6 — Output

- **CSV**: `data/willingness_to_pay.csv` — `position | positional_rank | roster_slot | avg_points | wtp_price | method1_av`
- **Chart A**: Horizontal bar chart of WTP by roster slot (same style as `plot_auction_value_top20`)
- **Chart B**: Scatter plot — WTP (x-axis) vs. Method 1 AV (y-axis), coloured by position,
  with a y=x reference line to show over/underpricing.
- Add `plot_wtp_comparison()` to `visualize_par.py`.

---

## Files to Create / Modify

| File | Change |
|---|---|
| `analysis/willingness_to_pay.py` | **New.** Contains `build_wtp_table()`, `compute_shadow_prices()`, and `run_sensitivity_sweep()`. |
| `analysis/visualize_par.py` | Add `plot_wtp_comparison()` and `plot_wtp_top20()`. |
| `analysis/lineup_optimizer.py` | Extract LP relaxation helper or expose dual variable retrieval. |
| `data/willingness_to_pay.csv` | **New output.** |
| `output/wtp_vs_method1.png` | **New chart.** |
| `output/wtp_top20.png` | **New chart.** |

---

## Open Questions / Decisions Needed

1. **DST handling**: Pre-assign DST at $1 (as the ESPN optimizer does) to keep the
   skill-position budget clean, or include DST in the WTP model?
   Answer: Pre-assign at $1

2. **Bench slots**: Include bench in the WTP model (affects how many "roster spots"
   compete for dollars) or starters-only?
   Yes

3. **Season window**: 2021–2025 (17-game seasons) or include 2020? Currently
   `build_avg_points_lookup` uses 2021–2025.
   21-25

4. **Points vs. PAR**: The MILP currently maximises PAR (relative to replacement).
   WTP based on raw expected points is arguably cleaner for bidding — you bid on
   what a player actually scores, not their margin over a baseline.
   Recommendation: use raw `avg_points` for WTP, keep PAR for the cross-position
   ranking/tier visualisation.
   I prefer avg points
