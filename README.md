# Fantasy Football Projects

Scraping and analyzing half-PPR fantasy football data from Pro Football Reference.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
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
`output/wtp_source_comparison.png`.

Each `willingness_to_pay` run also generates an interactive HTML version of
the "WTP vs. ESPN Estimates" scatter chart, e.g.
`output/wtp_vs_espn_estimates_blended.html`. Open it directly in a web
browser (double-click the file, or `open output/wtp_vs_espn_estimates_blended.html`)
to zoom/pan and hover over points to see player names.

## Structure

```
fantasy_football_projects/
├── data/               # Raw and cleaned output files (CSV, SQLite, etc.)
├── scrapers/
│   └── pfr_fantasy.py  # Main scraping script
├── requirements.txt
└── README.md
```
