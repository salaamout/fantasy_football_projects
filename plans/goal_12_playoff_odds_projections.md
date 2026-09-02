# Goal 12: Optimize for Playoff Odds (Weeks 1–11), Not Season-Long Average Points

## Motivation
Most of the current pipeline (`calculate_par.py`, `willingness_to_pay.py`,
`lineup_optimizer.py`) is designed to maximize *predicted points on average
over a full season*. But the real goal is to **win as many of the first 11
games as possible**, to maximize the odds of making the playoffs. Average
season points is a proxy for the real objective, and it's an imperfect one —
it ignores variance, matchup timing, and the fact that points earned in
weeks 12–17 are worthless.

This doc captures 5 candidate approaches to close that gap, for future
implementation.

## 1. Model variance/upside, not just expected points (mean-variance optimization)
Right now the pipeline works purely off average points. For winning
individual head-to-head matchups, variance matters a lot — a player with
high week-to-week volatility helps you win some weeks big and lose others
big, which is good if you're an underdog needing to hit spikes, and bad if
you're the favorite who just needs to avoid a bust.

- Compute a standard deviation (or better, a full distribution) of weekly
  points per player from historical game logs, not just season totals.
- Add a `volatility` / `boom_rate` / `bust_rate` column (e.g., % of games
  above X points, % below Y).
- Let WTP incorporate a risk-adjusted score: `score = mean + k * std` where
  `k` is tunable (positive if you want to draft high-variance players as an
  underdog, negative/zero if you want steady "safe floor" players). This
  also answers the open QB question — if QB scoring is inherently
  lower-variance/more predictable, that changes how much premium you should
  pay for a "safe floor" position vs. spending scarce dollars on volatile
  RB/WR upside.

## 2. Weight projections by weeks 1–11 only, and add strength-of-schedule adjustments
The pipeline currently uses season-long or 5-year historical averages. Only
the first 11 weeks matter for playoff odds.

- Pull ESPN's (or another source's) per-week projections and restrict the
  "expected points" calculation to weeks 1–11 instead of the full season.
- Layer in a per-week opponent-defense adjustment (ESPN/FantasyPros
  defense-vs-position rankings, or built from historical points-allowed-by-
  position data) to boost/discount a player's weekly projection based on
  matchup difficulty in that specific week.
- Produces a "weeks 1–11 weighted average" that down-weights players who are
  strong later in the season (bye-week timing, easier Weeks 12–17 schedules)
  in favor of players who are strong early.

## 3. Simulate actual matchup win probability instead of optimizing raw points
Maximizing total points doesn't necessarily maximize win probability, because
points aren't dollars — there are diminishing returns once you're already
winning by a lot, and marginal returns matter most in close matchups.

- Build a Monte Carlo simulator: for each week, sample each rostered
  player's score from their projected distribution (mean + variance from
  #1), sum to a team score, and compare against a simulated "average
  opponent" distribution (built from league-wide projected rosters, or just
  the field of players not on your team).
- Optimize roster construction to maximize **P(team score > opponent
  score)** across weeks 1–11 rather than maximizing `sum(expected points)`.
  This could replace or supplement the current LP-based objective function
  in `lineup_optimizer.py`.
- Lets you directly answer "is a high-variance player worth it" empirically
  per roster context, rather than guessing at a variance weight.

## 4. Prioritize floor/consistency for locked-in starters, upside for flex/bench, based on marginal win impact
Not all roster spots have equal leverage on win probability — your RB1/WR1
"floor" protects you from disaster losses, while your FLEX/bench upside
swings you from "close loss" to "close win."

- Segment the roster optimization: use a low-variance-preferring objective
  for slots that are almost always your best player at that position
  (locking in a "floor"), and a high-variance-preferring objective for the
  1–2 marginal roster spots that decide close games (FLEX, WR3, last bench
  spot).
- Could be implemented as a two-pass optimization in `lineup_optimizer.py`:
  first lock top-tier "safe" starters by `(mean − penalty*std)`, then fill
  flex/bench by `(mean + bonus*std)`.

## 5. Build a schedule-aware "playoff odds" backtest / simulation to validate roster choices
Rather than just trusting a single projection number, actually simulate the
season.

- Use existing per-player weekly projections + a simple league schedule
  (round-robin against other teams' likely rosters, or against historical
  league scores if available) to run thousands of season simulations for
  weeks 1–11.
- Output each candidate roster/draft strategy's estimated **P(making
  playoffs)**, not just total points. This becomes the real objective
  function and evaluation metric — rank draft strategies (e.g., "high-
  variance handcuff strategy" vs. "safe floor strategy") by simulated
  playoff odds instead of by average points, and it directly validates
  whether ideas #1–#4 are actually helping.
- Ties back into `willingness_to_pay.py` — WTP could ultimately be redefined
  as "dollars per unit of playoff-odds improvement" rather than "dollars per
  point," which is a more direct translation of the actual goal.

## Next steps
- Pick one idea to prototype first (variance calculation from historical
  game logs is likely the simplest starting point and unlocks #1, #3, and
  #4).
- Revisit `future_plans.md` open item: "Model uncertainty" and "Model
  strength of schedule" — this doc expands on both.
