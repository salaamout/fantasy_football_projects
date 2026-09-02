# Schedule Adjustment Report (Goal 13, Step 5)

Comparison of the **blended** Willingness-to-Pay run with and without the
weeks 1–11 strength-of-schedule adjustment described in
`plans/goal_13_schedule_adjusted_blended_wtp.md`.

- **Baseline:** `data/willingness_to_pay_blended.csv` (raw blended
  full-season points, no schedule adjustment) — regenerated via
  `python -m analysis.willingness_to_pay --blended`.
- **Adjusted:** `data/willingness_to_pay_blended_sched.csv` (blended points
  re-ranked/re-priced off `schedule_adjusted_points`, weeks 1–11 only) —
  regenerated via `python -m analysis.willingness_to_pay --blended --schedule-adjust`.
- 470 players matched by name between the two runs (100% match rate).

## Overall magnitude

| Metric | Value |
|---|---|
| Mean WTP price delta (adjusted − base) | −$0.01 |
| Std. dev. of WTP price delta | $0.85 |
| Max single-player price gain | +$8.31 (Jalen Hurts) |
| Max single-player price loss | −$2.52 (Brock Bowers) |
| Mean absolute points delta (season total) | 2.7 pts |

As expected per the plan's validation note, the swings are **modest** —
nowhere near the ~4 pt/game single-opponent effect seen in
`opponent_matchup_effects_actual_2025.csv`, since each player's adjustment
is an *average* across 11 weeks of opponents (some tough, some easy),
which mutes the net effect considerably. No starter-cutoff crossings
occurred (no player moved from bench-tier to starter-tier or vice versa at
their position) — the adjustment shifts value at the margins rather than
reshuffling roster construction.

## By position (mean absolute WTP price delta)

| Position | Mean Δ | Mean \|Δ\| |
|---|---|---|
| QB | +$1.08 | $1.25 |
| RB | −$0.03 | $0.25 |
| WR | −$0.11 | $0.17 |
| TE | −$0.12 | $0.14 |

QB shows the largest and most directionally consistent shift. This tracks
with QBs having a single every-week-matters starter role (no bye-week
replacement/committee dilution the way RB/WR/TE bench depth can mask), so
a favorable/unfavorable early schedule shows up more cleanly in their
per-game averages.

## Biggest $ gainers (favorable weeks 1–11 schedule)

| Player | Pos | Rank (base → sched) | WTP (base → sched) | Δ$ |
|---|---|---|---|---|
| Jalen Hurts | QB | 3 → 3 | $18.87 → $27.18 | +$8.31 |
| Lamar Jackson | QB | 2 → 2 | $20.50 → $27.40 | +$6.90 |
| Jaxson Dart | QB | 8 → 5 | $8.51 → $15.31 | +$6.80 |
| Joe Burrow | QB | 4 → 4 | $15.94 → $20.89 | +$4.95 |
| Dak Prescott | QB | 10 → 9 | $4.89 → $8.23 | +$3.34 |
| Tua Tagovailoa | QB | 9 → 10 | $4.97 → $7.71 | +$2.74 |
| Brock Purdy | QB | 7 → 8 | $9.22 → $11.85 | +$2.63 |
| Jayden Daniels | QB | 6 → 7 | $12.33 → $14.83 | +$2.50 |
| David Montgomery | RB | 21 → 20 | $22.48 → $24.60 | +$2.12 |
| Derrick Henry | RB | 7 → 7 | $49.95 → $51.94 | +$1.99 |

## Biggest $ losers (tough weeks 1–11 schedule)

| Player | Pos | Rank (base → sched) | WTP (base → sched) | Δ$ |
|---|---|---|---|---|
| Brock Bowers | TE | 1 → 1 | $37.54 → $35.02 | −$2.52 |
| Quinshon Judkins | RB | 20 → 21 | $26.07 → $23.72 | −$2.35 |
| Trey McBride | TE | 2 → 2 | $31.04 → $28.75 | −$2.29 |
| Ladd McConkey | WR | 24 → 25 | $17.45 → $15.17 | −$2.28 |
| Colston Loveland | TE | 3 → 3 | $23.48 → $21.28 | −$2.20 |
| Brian Thomas Jr. | WR | 37 → 39 | $3.66 → $1.51 | −$2.15 |
| Chase Brown | RB | 8 → 9 | $44.35 → $42.40 | −$1.96 |
| Josh Allen | QB | 1 → 1 | $35.75 → $33.94 | −$1.95 |
| D'Andre Swift | RB | 22 → 23 | $21.14 → $19.34 | −$1.81 |
| Omarion Hampton | RB | 15 → 16 | $36.62 → $35.04 | −$1.58 |

Notably, the entire top of the TE position (Bowers, McBride, Loveland) all
show early-schedule headwinds, while several top QBs (Hurts, Jackson,
Burrow, Dart) benefit from favorable early slates — enough to shift the
QB draft-value ordering meaningfully at the margins even though the
absolute rank order is mostly preserved (only Jaxson Dart moves up 3 full
positional ranks, from QB8 to QB5).

## Rank movers (rostered-tier players, WTP > $1)

Among the ~100 rosterable players (WTP price > $1), the largest positional
rank swing was **Jaxson Dart (QB8 → QB5, +3 ranks)**. All other rostered-tier
movers shifted by only 1–2 positional ranks — consistent with the plan's
expectation that this is a fine-tuning adjustment rather than a wholesale
re-ranking. Deep waiver-wire players (rank 100+, WTP == $1 floor) show
larger *rank* swings in raw rank-position terms, but these are noise among
players already valued at the price floor and have no practical draft-day
impact.

## Conclusion

The schedule adjustment behaves as designed: it nudges valuations by
roughly ±$1–2 for most rostered players (occasionally up to ~$8 for QBs
with unusually favorable/unfavorable weeks 1–11 slates), without
reshuffling which players are startable vs. bench/waiver. This validates
the ×0.5 shrinkage and rank-based smoothing chosen in the plan's "Decisions
(settled)" section — the adjustment is present and directionally sensible
but appropriately conservative given the underlying noise in opponent
matchup residuals.
