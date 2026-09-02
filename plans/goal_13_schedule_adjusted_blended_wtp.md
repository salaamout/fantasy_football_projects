# Goal 13: Schedule-Adjusted Blended WTP (Weeks 1–11 vs. Rest of Season)

## Motivation

`goal_12_playoff_odds_projections.md` (approach #2) already identifies the
gap: the pipeline currently prices players off season-long (or 5-year
historical) average points, but only weeks 1–11 matter for playoff odds.
`analysis/opponent_matchup_analysis.py` was built as the prototype to answer
*"does Sleeper already bake a strength-of-schedule signal into its weekly
projections?"* — and per
`output/opponent_matchup_effects_report.md`, the answer is yes for
projections, and actual 2025 results show the effect is real and *larger*
than what projections currently encode (4–10x bigger swings), especially at
RB/WR/TE.

This plan wires that per-opponent effect into the **blended** WTP pipeline
(`analysis/willingness_to_pay.py --blended`) so that a player's priced
`expected_points` reflect how hard/easy their specific weeks 1–11 schedule
is versus their weeks 12–18 schedule, instead of treating all 17(ish) games
as equally likely to happen and equally hard.

## Current state / gaps

1. `data/blended_projected_values.csv` (built by
   `analysis/load_multi_source_projections.py`) has one row per player with
   a single season-total `projected_points` — no per-week breakdown, no
   `team` column.
2. `analysis/opponent_matchup_analysis.py` already computes, per position and
   opponent, an `avg_residual` (points/game a position over/under-performs
   its own average against that opponent) — but only writes an aggregated
   opponent-level table (`output/opponent_matchup_effects.csv`), not
   anything joined back to individual players or teams.
3. Nothing in the repo currently maps `player_name` → `team` → weekly
   schedule (opponent per week) in a form other code can reuse. The raw
   per-team schedule *is* implicitly available in the cached Sleeper
   projections (`data/sleeper_projections_cache/2026_XX.json`, which has
   `team` + `opponent` per entry) but isn't extracted into a standalone
   lookup.
4. `willingness_to_pay.py`'s `_build_blended_points_table()` treats
   `expected_points` as a full-season total and derives PAR/replacement
   baselines directly from it — there's no hook for a schedule multiplier.

## Proposed approach

### Step 1 — Extract a team schedule lookup
Add a small function (new module `analysis/team_schedule.py`, or a function
inside `opponent_matchup_analysis.py` that gets imported) that:
- Reads the cached `sleeper_projections_cache/2026_*.json` files (reusing
  `fetch_week_projections`, which already caches/returns cleanly).
- Builds a `{team: {week: opponent}}` dict for season 2026, weeks 1–18, by
  taking the first `(team, opponent)` pair seen per week (schedule is the
  same for every player on a team, so this is cheap — no need to iterate
  every player row).
- Handles bye weeks (no entry for that team/week — already naturally
  excluded since no players get an opponent entry that week).
- Splits into `EARLY_WEEKS = range(1, 12)` (weeks 1–11) and
  `LATE_WEEKS = range(12, 19)` (weeks 12–18), matching goal 12's "playoff
  push" framing.

### Step 2 — Get a player → team mapping for the blended pool
- `data/blended_projected_values.csv` has `player_name` + `position` but no
  `team`. Reuse `analysis/player_id_matching.py`'s alias/normalization
  helpers to join blended player names against Sleeper's player cache
  (`data/sleeper_players_cache.json`, which has `team` per `player_id`) to
  get a `player_name → team` map.
- Players who can't be matched to a team fall back to *no schedule
  adjustment* (multiplier = 1.0) rather than being dropped — log them to
  `output/unmatched_players_report.csv` (existing convention) under a new
  reason code, e.g. `no_team_match_for_schedule_adjustment`.

### Step 3 — Compute a per-player schedule adjustment factor
For each player (position, team):
1. Look up their team's weeks-1–11 opponents and weeks-12–18 opponents from
   Step 1.
2. Build the **rank→value adjustment curve** per position (once, shared
   across all players/teams at that position):
   - From `output/opponent_matchup_effects_actual_2025.csv`, sort the 32
     opponents by `avg_residual` (toughest → easiest) and record the
     `avg_residual` value at each rank position 1–32.
   - Smooth this rank-ordered sequence of 32 values (e.g. rolling average
     across neighboring ranks) to remove single-team noise while
     preserving the overall toughest→easiest shape.
   - Multiply the smoothed curve by 0.5 (shrinkage factor) to get the
     final `rank_adjustment_curve[position][rank]` lookup.
   - From this year's forward-looking signal (`opponent_matchup_effects.csv`
     projections-based residuals, regenerated via `run_analysis(...)` if
     missing/stale), rank the 32 opponents toughest → easiest per position
     to get `predicted_rank[position][team]`.
   - For a given `(position, opponent)` this week, the adjustment value is
     `rank_adjustment_curve[position][predicted_rank[position][opponent]]`
     — i.e. "this opponent is predicted to be the Nth toughest matchup, so
     apply the value last year's Nth-toughest matchup actually produced."
3. For each player, average the per-week adjustment values (from step 2)
   across the weeks-1–11 opponents → `early_schedule_residual` (points/game).
4. Compute a blended-season **per-game** baseline:
   `per_game_points = season_total_points / games_played_estimate`
   (count actual scheduled weeks for that team from Step 1's real bye-week
   data to handle the per-game baseline precisely, rather than assuming 17).
5. Recompute a schedule-adjusted **weeks-1–11 total**:
   `adjusted_early_points = n_early_games * (per_game_points + early_schedule_residual)`
   where `n_early_games` = number of weeks 1–11 the team actually plays
   (11 minus 1 if their real bye week falls in weeks 1–11, else the full 11).
6. Expose this as a new column, e.g. `schedule_adjusted_points`, alongside
   the original `expected_points`, rather than silently overwriting it —
   so the CLI can toggle between "full season" and "schedule-adjusted
   weeks 1–11" pricing.

### Step 4 — Wire into `willingness_to_pay.py`
- Add a `--schedule-adjust` CLI flag (only valid with `--blended`, since
  that's the only source with per-player rows to adjust; historical/ESPN
  tables are already position-rank aggregates with no per-player identity).
- In `_build_blended_points_table()`, if the flag is set:
  - Join in `schedule_adjusted_points` from Step 3.
  - Re-sort/re-rank each position by `schedule_adjusted_points` instead of
    the raw blended `projected_points` (positional_rank changes are
    expected — some players move up/down based on their early-season
    matchup difficulty).
  - Recompute `par_points`/`flex_par_points` off the new ranking and
    replacement baseline, exactly as today but using the adjusted points
    column.
- Update `SOURCE_LABELS`/`SOURCE_SUFFIXES` (or add a new suffix like
  `_blended_sched`) so output files
  (`willingness_to_pay.csv`, `wtp_by_position_*.md`, plots) don't clobber
  the existing unadjusted blended outputs — write to parallel files instead.

### Step 5 — Regenerate downstream outputs & compare
- Re-run `analysis/willingness_to_pay.py --blended --schedule-adjust` and
  diff the resulting rankings/auction values against the unadjusted
  blended run to sanity-check magnitude (should be modest — the actual
  2025 swings were up to ~4 pts/game at WR/TE for the single toughest vs.
  easiest opponent, so an 11-game average swing across a whole schedule
  should be more muted).
- Add a short comparison note (movers up/down, biggest $ deltas) to
  `output/wtp_source_comparison.md` or a new
  `output/schedule_adjustment_report.md`.

## Decisions (settled)

1. **Adjustment source — rank-based, prior-year value lookup:** Instead of
   using a single-season's raw `avg_residual` directly as the adjustment,
   the approach is:
   - Rank all 32 opponents (per position) by their *predicted* difficulty
     for the upcoming season (i.e. rank order derived from whichever
     forward-looking signal we have — the projections-based
     `opponent_matchup_effects.csv` residuals, or a defense-strength
     projection if one exists) from toughest to easiest matchup.
   - For each rank (1..32), look up the **actual observed** per-game
     residual value at that same rank from *last year's*
     `opponent_matchup_effects_actual_2025.csv` (i.e. "the #1 toughest
     matchup last year suppressed RB points by X pts/game — apply that X
     to whichever team is ranked #1 toughest this year").
   - This decouples "which team is hard" (this year's prediction) from
     "how hard 'hard' actually turns out to be" (last year's realized
     magnitude), which should be more stable than trusting a single
     season's noisy per-opponent residual for both signals at once.
   - **Smooth** the rank→value curve from last year (e.g. rolling average
     or monotonic regression across ranks 1–32) before using it, so a
     single noisy rank doesn't create a cliff between adjacent ranks.
   - **Shrink by a factor of 2** (multiply the smoothed value by 0.5)
     before applying, as a simple overfitting guard.
   - Implementation note: this requires computing the rank curve once per
     position from `opponent_matchup_effects_actual_2025.csv` (32 values,
     smoothed + halved), then mapping each of this year's 32 teams to a
     rank via this year's predicted residual ordering, and pulling the
     corresponding smoothed/shrunk prior-year value as that team's
     per-game adjustment for that position.
2. **Statistical significance gating:** skipped — apply the adjustment
   uniformly across all positions, no ANOVA/significance filter.
3. **Magnitude/shrinkage:** handled via the rank-based smoothing + ×0.5
   factor described in #1 above (no separate empirical-Bayes step needed).
4. **Bye week precision:** pull the real bye week per team from the
   schedule data built in Step 1 (`team_schedule.py`) rather than assuming
   a fixed 11/7 split.
5. **Scope:** blended-only for now; ESPN/historical sources are out of
   scope for this goal.

## File-level change summary

| File | Change |
|---|---|
| `analysis/team_schedule.py` (new) | Build `{team: {week: opponent}}` from cached Sleeper projections; expose `EARLY_WEEKS`/`LATE_WEEKS` and a `get_team_bye_week()` helper. |
| `analysis/player_id_matching.py` | Add a helper to map blended `player_name` → Sleeper `team` (via `sleeper_players_cache.json` + existing alias/normalize helpers). |
| `analysis/opponent_matchup_analysis.py` | Expose `run_analysis(...)` output (or read its saved CSV) as an importable per-(position, opponent) lookup for reuse, instead of only a CLI script; add a `build_rank_adjustment_curve()` helper that produces the smoothed, ×0.5-shrunk rank→value curve per position from `opponent_matchup_effects_actual_2025.csv`, and a `rank_opponents_by_predicted_difficulty()` helper using `opponent_matchup_effects.csv`. |
| `analysis/willingness_to_pay.py` | Add `--schedule-adjust` flag; extend `_build_blended_points_table()` to compute and optionally apply `schedule_adjusted_points`; new output suffix for schedule-adjusted blended runs. |
| `output/unmatched_players_report.csv` | Extend with a new unmatched-reason bucket for players whose team couldn't be resolved. |
| `output/schedule_adjustment_report.md` (new) | Summary of biggest movers and validation notes. |

## Suggested implementation order

1. `team_schedule.py` + real bye-week handling (standalone, testable in
   isolation against the existing cache — no new API calls needed since
   2026 weeks are already cached from prior `opponent_matchup_analysis`
   runs).
2. Player → team join in `player_id_matching.py`, validated against
   `output/unmatched_players_report.csv` match-rate.
3. Rank-based adjustment curve builder (per position: smoothed + ×0.5
   shrunk prior-year rank→value curve, plus this year's predicted rank
   ordering) as a pure function, unit-testable against
   `opponent_matchup_effects_actual_2025.csv` /
   `opponent_matchup_effects.csv` fixtures.
4. Schedule-adjustment calculation as a pure function taking
   `(blended_df, team_schedule, rank_adjustment_curve, predicted_ranks)` →
   DataFrame with the new column, unit-testable with a small synthetic
   fixture.
5. CLI flag + `willingness_to_pay.py` integration last, once 1–4 are
   verified independently.

All open questions above are now settled per the "Decisions (settled)"
section. Ready to begin implementation in the suggested order.
