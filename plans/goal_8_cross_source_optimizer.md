# Goal 8: Cross-Source Lineup Points Optimizer

## Background

We have two independent data sources for **rank** (ESPN, Ringer) and **price**
(ESPN, Ringer), plus a historical record of average points scored by the Nth-best
player at each position over previous seasons (`data/fantasy_half_ppr.csv`).

The goal is to answer: **what is the highest-scoring starting lineup I can build
for $200, if the player in each slot is assumed to score the historical average
for that positional slot?**

We run this 4 times — one for each combination of rank source × price source —
and compare the results.

---

## Design Decisions

| Question | Decision |
|---|---|
| **Objective metric** | Raw average historical points per slot (not PAR) |
| **Seasons window** | 2021–2025 (2020 excluded — 16-game season; all others are 17 games) |
| **Name matching (cross-source)** | Fuzzy match (e.g. `rapidfuzz`) when joining ESPN ↔ Ringer on player name |
| **FLEX slot** | Combined RB/WR/TE flex rank, same approach as current optimizer |
| **DST / K** | Excluded from optimization; placeholder of **7 points/game × 17 games = 119 points** added to every lineup total |

---

## The 4 Combinations

| Combo | Rank source | Price source | Positional rank used | Auction cost used |
|---|---|---|---|---|
| A | ESPN | ESPN | `espn_projected_values.csv` → `positional_rank` | `espn_projected_values.csv` → `auction_value` |
| B | ESPN | Ringer | `espn_projected_values.csv` → `positional_rank` | `ringer_2026_rankings.csv` → `auction_value` (joined by name) |
| C | Ringer | ESPN | `ringer_2026_rankings.csv` → `positional_rank` | `espn_projected_values.csv` → `auction_value` (joined by name) |
| D | Ringer | Ringer | `ringer_2026_rankings.csv` → `positional_rank` | `ringer_2026_rankings.csv` → `auction_value` |

---

## Implementation Plan

### Step 1 — Build Historical Slot → Average Points Lookup

**File:** `analysis/calculate_par.py`

Add a new function `build_avg_points_lookup(seasons=(2021, 2022, 2023, 2024, 2025))`:

- Load `data/fantasy_half_ppr.csv` and filter to the target seasons.
- Within each `(position, season)`, rank players by `half_ppr_points` descending
  — this is the same `pos_rank` logic already used in `build_avg_par_lookup`.
- For the **FLEX** lookup, compute a combined `flex_rank` across all RB/WR/TE
  within each season (same approach as `build_avg_par_lookup`).
- Return two dicts:
  - `avg_points[(position, positional_rank)] → float`  — mean raw points across seasons
  - `avg_flex_points[(position, positional_rank)] → float` — mean raw points when
    evaluated as part of the combined flex pool
- Unlike `build_avg_par_lookup`, **do not subtract a replacement baseline** —
  we want total projected points, not PAR.

---

### Step 2 — Build the 4 Combined Rankings DataFrames

**File:** `analysis/lineup_optimizer.py`

Add a helper `build_combo_rankings(rank_source, price_source)`:

- `rank_source` ∈ `{"espn", "ringer"}` — determines which CSV to read for
  `positional_rank` (and `overall_rank`).
- `price_source` ∈ `{"espn", "ringer"}` — determines which CSV to read for
  `auction_value`.
- When the two sources differ (combos B and C), join on player name using
  `rapidfuzz.process.extractOne` with a score cutoff (e.g. 85). Log any
  players that fail to match and drop them from the pool.
- Output columns: `player_name`, `position`, `positional_rank`, `auction_value`.

---

### Step 3 — Attach Expected Points to Each Player

**File:** `analysis/lineup_optimizer.py`

Add `attach_expected_points(rankings, avg_points, avg_flex_points)`:

- For each row, look up `avg_points[(position, positional_rank)]` and attach
  as `expected_points`.
- For FLEX-eligible positions (RB/WR/TE), look up `avg_flex_points[(position,
  positional_rank)]` and attach as `flex_expected_points`.
- Fall back to the minimum historical value for the position when the rank
  exceeds the table.
- Mirror the `rank_window` smoothing parameter from `attach_expected_par` if
  desired, but default to 0.

---

### Step 4 — MILP Optimizer (Points Objective)

**File:** `analysis/lineup_optimizer.py`

Add `optimize_lineup_points(rankings, budget=200)`:

- Same two-variable MILP structure as `optimize_lineup()`:
  - `y[i]` = 1 if player fills a dedicated positional slot
  - `z[i]` = 1 if player fills the FLEX slot (RB/WR/TE only)
  - `y[i] + z[i] <= 1`
- **Objective:** `max Σ expected_points[i]*y[i] + flex_expected_points[i]*z[i]`
- **Budget constraint:** `Σ auction_value[i]*(y[i]+z[i]) <= budget`
- **Slot constraints** (same as current):
  - Exactly 1 QB, 2 RB, 3 WR, 1 TE in positional slots
  - Exactly 1 FLEX slot filled
- After solving, add the DST placeholder (7 pts/game × 17 games = **119 points**) to the total.

---

### Step 5 — Run All 4 Combos and Compare

**File:** `analysis/lineup_optimizer.py`

Add `run_cross_source_optimization()` as a new `--mode cross` entry point
(or a standalone `__main__` block):

1. Call `build_avg_points_lookup()` once (shared across all combos).
2. For each combo in `[("espn","espn"), ("espn","ringer"), ("ringer","espn"), ("ringer","ringer")]`:
   a. Build the combo rankings DataFrame.
   b. Attach expected points.
   c. Run the MILP optimizer.
   d. Store the result: selected players, total projected points (incl. DST),
      total cost.
3. Print a **summary comparison table**:

```
Combo           | Proj. Points | Cost | QB       | RB1      | RB2  | WR1 | WR2 | WR3 | TE  | FLEX
ESPN/ESPN       | 1,234.5      | $187 | ...
ESPN/Ringer     | ...
Ringer/ESPN     | ...
Ringer/Ringer   | ...
```

4. Save each optimized roster to `output/cross_source_<combo>.csv`.
5. Save a bar chart to `output/cross_source_comparison.png` showing projected
   points for the 4 combos (consistent with `visualize_par.py` style).

---

### Step 6 — Visualization

**File:** `analysis/visualize_par.py`

Add `plot_cross_source_comparison(results_dict)`:

- Simple grouped bar chart: x-axis = combo label, y-axis = projected lineup points.
- Annotate bars with total auction cost.
- Save to `output/cross_source_comparison.png`.

---

## Files to Create / Modify

| File | Change |
|---|---|
| `analysis/calculate_par.py` | Add `build_avg_points_lookup(seasons)` |
| `analysis/lineup_optimizer.py` | Add `build_combo_rankings()`, `attach_expected_points()`, `optimize_lineup_points()`, `run_cross_source_optimization()` |
| `analysis/visualize_par.py` | Add `plot_cross_source_comparison()` |
| `requirements.txt` | Add `rapidfuzz` if not already present |
| `output/cross_source_<combo>.csv` | Generated output (4 files) |
| `output/cross_source_comparison.png` | Generated output |

---

## Notes / Risks

- **Name matching:** ESPN uses abbreviated team names (`DET`) while Ringer uses
  full names (`Lions`). Player names themselves are mostly consistent but minor
  differences (e.g. `James Cook III` vs `James Cook`) will be handled by the
  fuzzy match. Review the match log carefully before trusting combo B/C results.
- **Seasons window:** Confirmed — `fantasy_half_ppr.csv` contains 2020–2025, but **2020 is excluded** (16-game season vs. 17 games for 2021–2025). Window is 2021–2025 (5 seasons).
- **DST placeholder:** Every lineup total includes **119 points** (7 pts/game × 17 games) for DST.
  This is consistent across all 4 combos so it does not affect relative rankings.
