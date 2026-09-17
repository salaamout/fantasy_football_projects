

# Goal 17: Revalidate Beanpot/Playoff Bracket Reconstruction

## Background

`analysis/beanpot_playoff_bracket_analysis.py` (Step 4 of
`goal_16_scrape_league_history.md`) reconstructs, per season, the
beanpot tiering/bracket (weeks 1-13) and the playoff bracket (winners
vs. losers pods). This plan lays out a straightforward, two-step
process for revalidating that logic from scratch.

## Ground truth for validation

Real names (via `data/owner_aliases.csv`) mapped to owner usernames:

| owner_name (username) | real_name |
|---|---|
| salaamout | Kyle |
| Christopher3205 | Chris |
| Fine Cutlery | AJ |
| Freeman0929 | Josh |
| NUHusky2014 | Garret |
| ZTurk88 | Turcotte (Turk) |
| azorn11 | Zorn |
| cheezhead430 | Javi |
| dporcello419 | Dean |
| jackhammersfan | Taylor |
| jordan_rand17 | Jordan |
| rigbyjp | Joe |

Known champions (winners bracket) and true last place (losers bracket):

| Season | Champion | Last place |
|---|---|---|
| 2020 | Joe (rigbyjp) | Josh (Freeman0929) |
| 2021 | Jordan (jordan_rand17) | Kyle (salaamout) |
| 2022 | Chris (Christopher3205) | Taylor (jackhammersfan) |
| 2023 | Chris (Christopher3205) | Zorn (azorn11) |
| 2024 | AJ (Fine Cutlery) | Kyle (salaamout) |
| 2025 | Turk (ZTurk88) | Jordan (jordan_rand17) |

These will be the acceptance criteria for Step 2.

## Step 1: Beanpot (weeks 1-13)

For each year, independently:

1. **Regular season standings (weeks 1-11).** Build a standings table
   (wins/losses/points, whatever the existing tiebreak rules are) for
   each team using only weeks 1-11. If two or more teams are tied and
   cannot be resolved with the data at hand, do not guess — list them
   as `TIES` instead of a hard seed. We'll resolve ties later.

2. **Sort into beanpots by hard seed.** Take all teams that got a clean
   (non-tied) seed from step 1 and bucket them by seed:
   - Seeds 1-4 → **top** beanpot
   - Seeds 5-8 → **middle** beanpot
   - Seeds 9-12 → **bottom** beanpot

   Teams left as `TIES` are not assigned a beanpot yet.

3. **Cluster via week 11/12/13 matchups.** Use the week 12-13 games
   themselves to pull in any remaining (tied) teams and confirm/extend
   each beanpot cluster:
   - If a team has already been assigned to beanpot X, and it plays
     Team A in week 12, then Team A also belongs to beanpot X.
   - The winner of that week 12 game plays Team C in week 13, so Team C
     also belongs to beanpot X.
   - Team C played Team D in week 12, so Team D also belongs to beanpot X.
   - Apply this transitively until no more teams can be pulled into a
     beanpot this way.

4. **Resolve leftovers.**
   - If, after step 3, exactly one beanpot is still short of its 4
     teams, the remaining unassigned team(s) must belong to that
     beanpot — assign them.
   - If two or three beanpots are still short of their 4 teams after
     step 3, do **not** guess. Mark that season/beanpot as
     **unresolved** and flag it for manual debugging later.

5. **Collect beanpot games.** For each year, gather all games in weeks
   12-13 for the 12 teams involved — these are "the beanpot games" for
   that season (used for later analysis, e.g. beanpot winner/runner-up
   within each tier). Games in week 13 that are not between the winners of week 12 should be marked as meaningless

## Step 2: Playoffs

- Known champions list (per season, from ground truth table above).
- Known last-place/losers list (per season, from ground truth table
  above).
- Real names list (owner username → real name mapping, from
  `data/owner_aliases.csv`, per table above).

Work backwards from the known championship-week results, using the
game log itself to trace each bracket back through earlier weeks
(week N = championship week, e.g. week 16/17 depending on season
length).

> **Note:** Week N is sometimes a two-week matchup (e.g. the
> championship is decided by combined score across weeks N-1 and N
> rather than a single week). Check the schedule/matchup data for each
> season before assuming week N is a single-week game, and treat the
> two weeks as one combined "final" when tracing opponents backwards.

1. **Week N (final week) — winners bracket, from the champion.** The
   champion played some Team A in week N. Team A is in the winners
   bracket.

2. **Week N-1 — winners bracket, from the champion's final
   opponent.** Whoever the champion played in week N-1 is also in the
   winners bracket, and whoever Team A (the champion's week N
   opponent) played in week N-1 is also in the winners bracket.

3. **Week N (final week) — losers bracket, from the true last place
   team.** The last-place team played some Team B in week N. Team B is
   also in the losers bracket.

4. **Week N-1 — losers bracket, from the last place team's final
   opponent.** Whoever the last-place team played in week N-1 is in
   the losers bracket, and whoever Team B played in week N-1 is also
   in the losers bracket.

5. **Week N-2 — winners bracket, from the middle-beanpot winner.**
   Week N-2 is more complicated because of byes — especially in the
   losers bracket, which has two different bye structures depending on
   whether the "immune" team (the winner of the bottom beanpot) ends
   up in the winners bracket or not. Start with the winners bracket
   since it's unambiguous: the winner of the middle beanpot always
   enters the winners bracket and never has a bye. Whoever that team
   played in week N-2 is also in the winners bracket.

6. **Week N-2 — winners bracket, from the bottom-beanpot winner (if
   applicable).** Check whether the winner of the bottom beanpot has
   already been identified (via steps 1-2) as being in the winners
   bracket. If so, whoever they played in week N-2 is also in the
   winners bracket.

7. **Mark remaining teams/games as unresolved.** Any teams or week
   N-2-and-earlier games not accounted for by steps 1-6 (including the
   two possible losers-bracket bye configurations) should be marked
   `UNRESOLVED` and handled on a case-by-case basis rather than guessed
   at.
