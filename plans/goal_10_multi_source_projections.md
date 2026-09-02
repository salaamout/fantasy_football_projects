# Goal 10: Multi-Source Point Projections + Updated Willingness-to-Pay

## Problem Statement

`willingness_to_pay.py` currently supports two mutually-exclusive expected-points sources
(`historical` and `espn`). We want a single blended projection that combines **ESPN**,
**historical (5-yr avg half-PPR)**, and **Sleeper** projections, then re-run the WTP
pipeline against that blended source.

---

## Step 1: Acquire Sleeper Projection Data
- The saved `~/Downloads/Sleeper_projections.html` is a client-rendered React page — it has
  no static `<table>`/`<tr>` markup, so it **cannot** be scraped like the ESPN/PFR HTML files.
- Preferred option: hit Sleeper's public read-only API (e.g. `https://api.sleeper.app/projections/nfl/2026/<week>` or season-long endpoint) to pull structured JSON directly — no HTML parsing needed.
- Fallback option: manually export/copy the rendered table into a CSV (e.g. via browser dev tools "Copy as HTML" after full render, or Sleeper's own export/share feature).
- New scraper module: `scrapers/parse_sleeper.py`, output to `data/sleeper_projected_values.csv`.
- Normalize output columns to match the existing convention: `player_name, position, positional_rank, projected_points` (see `scrapers/parse_espn_html.py` for the target schema).

## Step 2: Normalize & Align Player Identities Across Sources
- Each source (ESPN, historical, Sleeper) uses different name formats (suffixes, nicknames, team tags) — build a shared name-normalization function (lowercase, strip Jr./Sr./III, strip punctuation).
- Handle position mismatches (e.g., a player flagged FLEX-only in one source vs. RB/WR in another).
- Produce a single "player key" (normalized name + position) usable as a join key across all three sources.
- Document any players who fail to match across sources (log/report unmatched rows for manual review).
- Consider storing a small manual alias-mapping file (`data/player_aliases.csv`) for stubborn mismatches.

## Step 3: Build a Combined Projection Loader
- New module `analysis/load_multi_source_projections.py` (or extend `load_fantasy_data.py`) that loads all three sources and joins them on the player key from Step 2.
- Decide and implement a blending formula — start simple (e.g., weighted average with configurable weights per source) with weights as a named constant/CLI arg.
- Handle missing data gracefully (a player projected by only 1-2 sources should not be dropped, but should be flagged/weighted accordingly).
- Output a single `data/blended_projected_values.csv` with columns matching existing `positional_rank`/`projected_points` conventions so downstream code needs minimal changes.
- Add basic sanity-check prints/tests (row counts per position, top-20 by position) similar to existing loader scripts.

## Step 4: Wire the Blended Source into Existing Analysis Pipeline
- Add a new `--blended` (or `--source blended`) option to `willingness_to_pay.py`, `calculate_par.py`, and `lineup_optimizer.py`, following the existing `--espn` flag pattern.
- Confirm `build_avg_points_lookup` / `attach_expected_points` accept the new source without structural changes (may just need a new CSV path constant).
- Re-run `aggregate_par.py` and `visualize_par.py` against the blended source to confirm the full pipeline runs end-to-end.
- Update `REPLACEMENT_RANKS` or other source-specific constants only if the blended data materially changes positional depth.

## Step 5: Generate & Compare Updated WTP Outputs
- Run `willingness_to_pay.py` with the new blended source and generate `output/wtp_by_position_blended.md` + associated plots (mirroring existing `_espn`/`_historical` outputs).
- Add a comparison view (table or chart) showing WTP deltas: blended vs. ESPN-only vs. historical-only, to sanity-check whether blending meaningfully changes recommendations.
- Spot-check a handful of known players (e.g., top-5 RBs/QBs) to confirm blended values look reasonable relative to the individual sources.
- Update `README.md` / relevant `plans/` doc to note the blended source is now available and how to invoke it.
- Capture open questions (e.g., final source weighting, whether to add FantasyPros/Yahoo later per `future_plans.md`) back into `plans/future_plans.md`.
