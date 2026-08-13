# Fantasy Football Projects

Scraping and analyzing half-PPR fantasy football data from Pro Football Reference.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Structure

```
fantasy_football_projects/
├── data/               # Raw and cleaned output files (CSV, SQLite, etc.)
├── scrapers/
│   └── pfr_fantasy.py  # Main scraping script
├── requirements.txt
└── README.md
```
