# Fantasy Football Projects

Scraping, analysis, and a live draft-day tool for a 12-team half-PPR ESPN
salary-cap fantasy football auction league.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Live Draft Web App

`webapp/` is a Streamlit app you run *during* the live auction draft to track
picks and get real-time pricing/lineup guidance.

```bash
streamlit run webapp/app.py
```

It seeds the player pool from `data/willingness_to_pay_blended_sched.csv`
(run the WTP analysis below first) and persists draft state to
`data/draft_state.json` as you go, so you can close/reopen the app mid-draft
without losing progress.

Features:

- **Draft a player** — record who was drafted, at what price, and by whom
  (you or another team). Shows the top comparable available players at that
  position while you decide.
- **Find max profitable bid** — binary-searches the highest price you could
  pay for a given player without your optimal lineup's projected points
  dropping below your current baseline.
- **Live shadow-price recompute** — re-solves the market LP relaxation
  (`analysis.willingness_to_pay.solve_lp_relaxation`) over just the
  remaining players and remaining budget/roster-slot quotas, so WTP prices
  update as the draft progresses. Solver failures are auto-captured to
  `output/live_pricing_debug/` and can be replayed offline with
  `python -m webapp.replay_live_pricing_failure <path>` for debugging.
- **Optimal team (live)** — a MILP (PuLP/CBC) recomputes your best possible
  roster from locked (drafted) + available players after every pick, with a
  toggle between ESPN auction values and a **market-adjusted cost basis**:
  as owners overpay or underpay for players, `webapp/adjusted_cost.py`
  redistributes ("peels") that premium/discount across the remaining pool so
  the optimizer reflects real market drift, not just static pre-draft
  values.
- **WTP board** — an interactive Plotly scatter of ESPN price vs. WTP price
  per position, with a fair-value reference line and player search/highlight.
- **Full board** — sortable tables of available/drafted players.
- **Undo last pick** / **Reset draft**.

After the draft, compare your actual roster to the theoretical best-possible
lineup at the same budget:

```bash
python -m analysis.draft_recap [--budget 195] [--state-path data/draft_state.json]
```

## Willingness-to-Pay Analysis

`analysis/willingness_to_pay.py` computes principled auction-value shadow
prices for every roster slot via LP relaxation of a 12-team competitive
market. Three expected-points sources are supported:

```bash
python -m analysis.willingness_to_pay             # historical avg (2021–2025)
python -m analysis.willingness_to_pay --espn       # ESPN 2026 projected points
python -m analysis.willingness_to_pay --blended    # blended ESPN + Sleeper + historical
```

The `--blended` source requires `data/blended_projected_values.csv`, generated
by:

```bash
python -m analysis.load_multi_source_projections
```

To compare WTP outputs across all three sources side-by-side (deltas, spot
checks on known top players, and a comparison chart), run:

```bash
python -m analysis.compare_wtp_sources
```

This writes `output/wtp_source_comparison.md` and
`output/wtp_source_comparison.png`. `analysis/diagnose_wtp_sources.py` is a
smaller diagnostic that compares the budget shadow price (λ) and
replacement-level points between the historical and ESPN point sources.

Each `willingness_to_pay` run also generates an interactive HTML version of
the "WTP vs. ESPN Estimates" scatter chart, e.g.
`output/wtp_vs_espn_estimates_blended.html`. Open it directly in a web
browser (double-click the file, or `open output/wtp_vs_espn_estimates_blended.html`)
to zoom/pan and hover over points to see player names.

### Schedule-adjusted WTP

`analysis/team_schedule.py` and `analysis/schedule_adjustment.py` build a
per-team weekly opponent lookup and a per-player schedule-strength
adjustment factor, and `analysis/opponent_matchup_analysis.py` weights
projections by the first 11 weeks of the schedule (regular season only).
Passing `--sched`/schedule-adjusted inputs into the WTP pipeline produces the
`*_blended_sched` outputs used to seed the live draft app (e.g.
`data/willingness_to_pay_blended_sched.csv`,
`output/wtp_by_position_blended_sched.md`,
`output/wtp_vs_espn_estimates_blended_sched.html`).

`analysis/player_id_matching.py` normalizes and aligns player identities
across the ESPN and Sleeper projection sources so they can be blended
correctly.

## Points Above Replacement (PAR) & Lineup Optimizer

An earlier, non-auction analysis pipeline built from raw Pro Football
Reference data:

- `analysis/load_fantasy_data.py` — load/clean/normalize historical half-PPR
  data.
- `analysis/calculate_par.py` — compute Points Above Replacement per player.
- `analysis/aggregate_par.py` — aggregate PAR by roster slot and convert to
  auction values.
- `analysis/visualize_par.py` — plot PAR curves/auction values and export
  results.
- `analysis/lineup_optimizer.py` — use Ringer auction values as cost and
  historical PAR-per-slot as value to solve for the best starting lineup
  within a budget.

## League History & Beanpot/Playoff Bracket Reconstruction

Tools for pulling and reconstructing the league's full multi-season history
directly from ESPN's read-only Fantasy API (league ID hardcoded to this
league, 2020–2026 seasons).

```bash
python scrapers/scrape_espn_league_history.py     # fetch raw JSON -> data/league_history_raw/<season>/
python scrapers/extract_league_history.py          # parse raw JSON -> cleaned CSVs
```

`extract_league_history.py` produces `data/league_matchup_history.csv`,
`data/league_standings_history.csv`, and `data/league_weekly_rosters.csv`,
normalizing owner identity across seasons (team names change year to year)
via ESPN's SWID.

Because this league runs a custom playoff format (weeks 1–11 regular season,
weeks 12–13 a "beanpot" mini-bracket among tied/bordering teams, then a final
playoff bracket), reconstructing historical standings/brackets purely from
score data takes a couple of passes:

- `analysis/beanpot_playoff_bracket_analysis.py` — first-pass heuristic
  reconstruction of beanpot tiers and the winners/losers playoff bracket per
  season from `data/league_matchup_history.csv`.
- `analysis/beanpot_step1_tiers.py` — more rigorous tier reconstruction:
  seeds unambiguous standings positions, clusters ambiguous ties via the
  week 12/13 matchup graph, and flags any season it can't resolve, rather
  than guessing.
- `analysis/beanpot_step2_playoffs.py` — reconstructs winners/losers bracket
  membership by tracing backward from known champions/last-place finishers
  per season through the game log.

See `plans/goal_16_scrape_league_history.md` and
`plans/goal_17_revalidate_playoff_brackets.md` for the full design notes and
status. Downstream trend/rivalry analysis over this history data is planned
but not yet built.

## Draft Value Input

`scrapers/parse_espn_html.py` parses a saved ESPN "Salary Cap Draft List"
HTML export (`data/ESPN Salary Cap Draft List <date>.html`) into structured
auction-value data used as an input to the WTP pipeline; re-save a fresh
export from ESPN and update the filename constant before each draft season.

## Structure

```
fantasy_football_projects/
├── analysis/           # PAR pipeline, WTP/auction pricing, schedule adjustment,
│                       # multi-source blending, beanpot/playoff reconstruction, draft recap
├── data/               # Raw and cleaned inputs/outputs (CSV, JSON, HTML snapshots)
│   └── league_history_raw/   # Cached raw ESPN API responses per season
├── output/             # Generated charts, tables, and reports
│   └── live_pricing_debug/   # Auto-captured solver-failure snapshots from the webapp
├── plans/              # Design docs / running goal log (PLANNING.md, goal_*.md)
├── scrapers/           # PFR, Ringer, Sleeper, and ESPN scrapers/parsers
├── webapp/             # Streamlit live draft board (see "Live Draft Web App" above)
├── requirements.txt
└── README.md
```
