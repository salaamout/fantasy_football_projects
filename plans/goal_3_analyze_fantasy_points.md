# Goal 3: Analyze Points Above Replacement (PAR)

1. **Define replacement level** — For each position, identify the replacement-level player based on the league's roster size and composition.

   **League roster settings:**
   - **Starters:** 1 QB, 2 RB, 3 WR, 1 TE, 1 FLEX (RB/WR/TE), 1 D/ST → 8 starters per team
   - **Bench:** 4 players
   - **Total roster size:** 12 players per team

   **Assumptions for FLEX:**
   - The FLEX slot can be filled by an RB, WR, or TE.
   - For the purpose of determining replacement level, assume the FLEX is most commonly filled by the best available RB or WR (in practice, treat as an additional RB/WR slot when estimating positional scarcity).

   **Replacement-level definition:**
   - Replacement level = the best player at a position who is freely available on the waiver wire in a 12-team league (i.e., not rostered).
   - For each position, the number of rostered starters across all teams determines how deep the pool is before hitting the replacement tier.

   **Starter slots per position across the league (12 teams):**
   | Position | Slots/Team | Total Starters (12 teams) | Notes |
   |----------|-----------|--------------------------|-------|
   | QB       | 1         | 12                       | Replacement = QB13 |
   | RB       | 2         | 24                       | +FLEX share (see below) |
   | WR       | 3         | 36                       | +FLEX share (see below) |
   | TE       | 1         | 12                       | Replacement = TE13 |
   | D/ST     | 1         | 12                       | Replacement = DST13 |

   **FLEX allocation:**
   - With 12 FLEX slots across the league, assume a typical split of ~7 RB / 5 WR (based on historical usage patterns; this can be tuned later).
   - This gives effective totals of ~31 RB starters and ~41 WR starters before hitting replacement level.
   - Replacement level ≈ RB32, WR42, with bench depth pushing the practical cutline slightly deeper.

   **Bench consideration:**
   - Each team carries 4 bench players. Across 12 teams, that's 48 bench spots.
   - Bench players are still rostered, so the true "freely available" replacement player sits beyond the total rostered pool.
   - Bench spots are typically filled with positional depth (handcuffs, upside plays), making the effective replacement level slightly deeper than the starter count alone suggests.
   - For simplicity, calculate replacement level from starter slots only first, then optionally adjust by factoring in bench depth.

   **Replacement-level player (starting point):**
   | Position | Replacement Rank |
   |----------|-----------------|
   | QB       | QB13            |
   | RB       | RB32            |
   | WR       | WR42            |
   | TE       | TE13            |
   | D/ST     | DST13           |

2. **Load and clean scraped data** — Import the last five years of half-PPR data and normalize it for consistent analysis.

   **Data source:**
   - File: `data/fantasy_half_ppr.csv`
   - Columns: `player_display_name`, `position`, `season`, `half_ppr_points`
   - Seasons covered: 2020–2024 (5 years)

   **Loading steps:**
   - Read the CSV into a pandas DataFrame.
   - Verify expected columns are present; raise an error if any are missing.
   - Confirm the expected seasons (2020–2024) are all represented.

   **Cleaning steps:**
   - Filter to only the relevant positions: `QB`, `RB`, `WR`, `TE`, `DST`.
   - Drop any rows with null values in `player_display_name`, `position`, `season`, or `half_ppr_points`.
   - Cast `season` to integer and `half_ppr_points` to float to ensure correct types.
   - Standardize position strings to uppercase to guard against inconsistent casing.
   - Remove any duplicate rows (same player, position, and season).

   **Normalization / enrichment:**
   - For each position + season combination, rank players by `half_ppr_points` descending to assign a positional rank (e.g., `QB1`, `QB2`, `RB1`, etc.). Store this as a `positional_rank` integer column.
   - Add a `rank_label` string column (e.g., `"QB1"`, `"RB12"`) for display purposes.

   **Validation checks:**
   - Confirm no player appears more than once per position/season (after deduplication).
   - Print a quick summary: row count, seasons present, and player count per position per season to sanity-check the data before moving on.

   **Output:**
   - A clean, enriched DataFrame ready for PAR calculation in step 3.

3. **Calculate PAR per player** — Subtract the replacement-level baseline from each player's points to get points above replacement.

   **Identify replacement-level baselines:**
   - Using the clean DataFrame from step 2, for each position + season, look up the points scored by the replacement-level player (by positional rank):
     | Position | Replacement Rank |
     |----------|-----------------|
     | QB       | 13              |
     | RB       | 32              |
     | WR       | 42              |
     | TE       | 13              |
     | D/ST     | 13              |
   - If the replacement rank exceeds the number of players in that position/season (e.g., fewer than 32 RBs in the data), fall back to the last-ranked player at that position for that season and log a warning.
   - Store baselines in a lookup structure keyed by `(position, season)` → `replacement_points`.

   **Calculate PAR:**
   - For each player row, compute: `PAR = half_ppr_points - replacement_points` where `replacement_points` is looked up by `(position, season)`.
   - Add a `par` column to the DataFrame.
   - PAR can be negative (a player who scored below replacement level).

   **Handle edge cases:**
   - Players with 0 or very low point totals (e.g., injured for the season) should still get a PAR value — do not drop them, as they inform roster depth analysis.
   - If a replacement-level player is missing for a given position/season, raise a descriptive error rather than silently producing NaN values.

   **Validation checks:**
   - Confirm the replacement-level player themselves has PAR ≈ 0 for each position/season (within floating-point tolerance).
   - Print the replacement-level points per position per season as a sanity check.
   - Confirm no unexpected NaN values appear in the `par` column.

   **Output:**
   - The DataFrame now has a `par` column alongside `half_ppr_points`, `positional_rank`, and `rank_label`, ready for aggregation in step 4.

4. **Aggregate PAR by roster slot and convert to auction values** — Summarize PAR distributions by positional tier and translate PAR into dollar values to inform auction budget allocation.

   **Goal:**
   - Answer the core auction draft question: *how much should I spend on a given roster slot?* For example, is the RB3 on my roster worth more than the QB1? Where does spending drop off sharply within a position?
   - Enable cross-position comparisons on a single dollar scale so every player and tier can be evaluated against each other.

   **Step A — Summarize PAR by positional tier:**
   - Use the `positional_rank` column to group players by `(position, positional_rank)` across all seasons (2020–2024).
   - For each group, calculate:
     - `mean_par` — average PAR across seasons
     - `median_par` — median PAR (more robust to outliers)
     - `std_par` — standard deviation (boom/bust risk)
     - `min_par` / `max_par` — floor and ceiling across seasons
     - `seasons_observed` — number of seasons with a player at that rank
   - Trim any tiers with fewer than 2 seasons of data and log a warning.
   - This produces a tier summary DataFrame: one row per `(position, positional_rank)`.

   **Step B — Convert PAR to auction dollar values:**
   - Use a standard auction value conversion: distribute the total league auction budget across all rostered starter slots, proportional to each slot's PAR contribution.
   - **League auction settings:**
     - 12 teams, $200 budget per team → $2,400 total league spend
     - Reserve ~$10 for bench spots (4 bench players × ~$2.50 average). Use **$190 per team toward starters**, so **$2,280 total starter dollars**.
   - **Conversion formula:**
     1. Compute total positive PAR across all starter slots (using `mean_par`; exclude slots with `mean_par ≤ 0` — these are below-replacement and worth the minimum bid).
     2. For each starter slot with positive PAR: `auction_value = (mean_par / total_positive_par) * total_starter_dollars`
     3. Slots with `mean_par ≤ 0` receive a nominal `$1` (minimum bid).
   - **Starter slots to include in the conversion** (mirrors the replacement-level definitions from step 1):
     | Position | Starter Slots Included |
     |----------|----------------------|
     | QB       | ranks 1–12           |
     | RB       | ranks 1–31           |
     | WR       | ranks 1–41           |
     | TE       | ranks 1–12           |
     | D/ST     | ranks 1–12           |
   - Add an `auction_value` column (rounded to nearest dollar) to the tier summary DataFrame.

   **Step C — Cross-position roster slot comparison:**
   - Produce a unified, cross-position ranking by sorting all starter slots by `auction_value` descending.
   - This directly answers questions like: "Is the RB3 worth more than the QB1?" — you can read off the answer from the sorted list.
   - Flag each row with a `roster_slot` label (e.g., `"RB1"`, `"WR3"`, `"QB1"`) for readability.

   **Validation checks:**
   - Confirm total `auction_value` across all starter slots sums to approximately `$2,280` (within rounding).
   - Confirm that rank 1 at each position has the highest `auction_value` within that position.
   - Print the top 10 most valuable roster slots across all positions as a sanity check.

   **Output:**
   - `tier_summary_df`: one row per `(position, positional_rank)` with `mean_par`, `median_par`, `std_par`, `min_par`, `max_par`, `seasons_observed`, and `auction_value`.
   - `cross_position_ranking_df`: all starter slots sorted by `auction_value` descending, ready for visualization and export in step 5.

5. **Visualize and export results** — Generate summary tables and charts showing PAR by position slot for use in goal 4.

   **Goal:**
   - Communicate the PAR landscape visually so it's easy to see where value concentrates within and across positions.
   - The primary output is a multi-line curve chart showing mean PAR vs. positional rank for all positions on a single plot, making cross-position drop-off directly comparable.

   **Chart 1 — PAR curves by position (primary output):**
   - **Type:** Line chart, one line per position (QB, RB, WR, TE, D/ST).
   - **X-axis:** Positional rank (1 → replacement rank for that position). Each position line ends at its own replacement rank (e.g., QB ends at rank 13, RB at rank 32, WR at rank 42, TE at rank 13).
   - **Y-axis:** `mean_par` (average PAR across seasons 2020–2024).
   - **Reference line:** Draw a horizontal dashed line at `y = 0` (replacement level) so it's easy to see how far above replacement each tier sits.
   - **Annotations:** Mark the replacement-level rank for each position with a small dot on the x-axis or a vertical tick.
   - **Styling:**
     - Use distinct colors per position (e.g., a colorblind-friendly palette: QB=blue, RB=orange, WR=green, TE=red, DST=purple).
     - Add a legend with position labels.
     - Title: `"Mean PAR by Positional Rank (2020–2024, Half-PPR)"`.
     - X-axis label: `"Positional Rank"`. Y-axis label: `"Mean Points Above Replacement (PAR)"`.
     - Use a clean grid (light gray, alpha ~0.3) for readability.
   - **Implementation notes:**
     - Use `matplotlib` (and optionally `seaborn` for styling).
     - Filter `tier_summary_df` to only the starter slots for each position before plotting (same rank cutoffs used in the auction value conversion).
     - Plot lines with markers (`marker='o'`, `markersize=4`) so individual rank points are visible.

   **Chart 2 — Auction value bar chart by position (supplementary):**
   - **Type:** Grouped horizontal bar chart (or separate subplots per position).
   - **X-axis:** `auction_value` in dollars.
   - **Y-axis:** `roster_slot` label (e.g., `RB1`, `RB2`, …).
   - Show only the top ~20 most valuable roster slots across all positions (from `cross_position_ranking_df`).
   - Color bars by position using the same palette as Chart 1.
   - Title: `"Top 20 Roster Slots by Auction Value (Half-PPR, 12-Team)"`.

   **Chart 3 — PAR heatmap by position and rank (optional/supplementary):**
   - **Type:** Heatmap where rows = positions, columns = positional rank bins (1–5, 6–10, 11–15, etc.), cells = mean PAR.
   - Useful for quickly seeing where tiers flatten out within a position.
   - Use a sequential colormap (e.g., `YlOrRd`) so higher PAR = darker color.

   **Export — summary tables:**
   - Save `tier_summary_df` to `data/tier_summary.csv`.
   - Save `cross_position_ranking_df` to `data/cross_position_ranking.csv`.
   - Save each chart as a PNG to an `output/` directory:
     - `output/par_curves_by_position.png` (Chart 1 — primary)
     - `output/auction_value_top20.png` (Chart 2)
     - `output/par_heatmap.png` (Chart 3, if generated)
   - Use `dpi=150` and `bbox_inches='tight'` when saving figures.

   **Implementation file:**
   - Add visualization logic to `analysis/aggregate_par.py` (or a new `analysis/visualize_par.py` if the file becomes too large).
   - Wrap chart generation in a `plot_par_curves(tier_summary_df)` function so it can be called independently or as part of the full pipeline.

   **Validation checks:**
   - Confirm all five positions appear in Chart 1 (check legend entries).
   - Confirm the RB and WR lines extend further right than QB and TE (since they have more starter slots).
   - Confirm the `output/` directory is created if it does not already exist (use `os.makedirs(..., exist_ok=True)`).

   **Output:**
   - `output/par_curves_by_position.png` — the primary multi-position PAR curve (ready to drop into goal 4 analysis).
   - `output/auction_value_top20.png` — auction value bar chart for the top 20 slots.
   - `data/tier_summary.csv` and `data/cross_position_ranking.csv` — cleaned exports for further use in goal 4.
