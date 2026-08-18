# Goal 7: Scrape ESPN Salary Cap Draft List → Best-Team Optimizer

## Background

The ESPN default salary-cap draft rankings page was saved manually as:

```
data/ESPN Salary Cap Draft List 26_08_17.html
```

The page is a React/JS-rendered app; player data may live in embedded `<script>`
JSON blobs or require JS parsing of the supporting `_shHN.js` bundle files in
the `data/ESPN Salary Cap Draft List 26_08_17_files/` directory.

Each player entry is expected to contain:
- Player name, position, and NFL team
- Salary-cap dollar value (auction cost)
- Projected fantasy points (half-PPR)

---

## Part 1 — Scrape ESPN HTML into CSV

### Target Schema (`data/espn_projected_values.csv`)

| Column | Type | Notes |
|---|---|---|
| `player_name` | str | e.g. `"Ja'Marr Chase"` |
| `position` | str | Uppercase: `QB`, `RB`, `WR`, `TE`, `K`, `DST` |
| `team` | str | e.g. `"Bengals"` |
| `overall_rank` | int | ESPN overall draft rank |
| `positional_rank` | int | ESPN positional rank |
| `auction_value` | int | Salary-cap dollar value |
| `projected_points` | float | ESPN projected half-PPR fantasy points |

### Implementation Plan

- Inspect `data/ESPN Salary Cap Draft List 26_08_17.html` for embedded JSON
  `<script>` tags (e.g. `window.__espnfbc__` or `__NEXT_DATA__` patterns).
- If data is in JS bundles, parse the relevant `_shHN.js` file in the
  `_files/` directory for player data arrays.
- Prefer `BeautifulSoup` + `json` for static extraction; fall back to
  `selenium` (headless) only if values are fully client-side rendered.
- Normalize names and positions to match the `ringer_2026_rankings.csv` schema;
  strip `$` signs and cast numeric fields.
- Save output to `data/espn_projected_values.csv`; log row count and any
  players dropped due to missing projected points.

### Deliverable

**`scrapers/parse_espn_html.py`** — reads the saved HTML, extracts player data,
writes `data/espn_projected_values.csv`.

---

## Part 2 — Assemble the Best Possible Team (ILP Optimizer)

### Lineup Definition

Reference `plans/goal_5_ringer_rankings_optimizer.md` for the full roster-slot
definition.  Expected slots (half-PPR salary-cap league):

| Slot | Count | Eligible Positions |
|---|---|---|
| QB | 1 | QB |
| RB | 2 | RB |
| WR | 3 | WR |
| TE | 1 | TE |
| FLEX | 1 | RB / WR / TE |
| DST | 1 | DST |
| Bench | 4 | Any |

Total starters: 9 (plus bench depending on league settings).

### Optimizer Logic

- Load `data/espn_projected_values.csv`; filter out players with missing
  `projected_points` or `auction_value`.
- Formulate an ILP: maximize `sum(projected_points[i] * x[i])` subject to
  salary-cap budget and per-slot roster constraints.
- Use `pulp` (already lightweight); add to `requirements.txt` if not present.
- Enforce one-player-per-slot assignment; FLEX slot pulls from RB/WR/TE pool
  without double-counting.
- Output chosen roster, total cost, and projected points to
  `output/espn_optimized_roster.csv`; print summary table to stdout.

### Deliverable

Extend  **`analysis/lineup_optimizer.py`** with an ESPN-data code
path that accepts `data/espn_projected_values.csv` and a salary-cap budget
argument.

---

## Assumptions & Open Questions

- Dollar values in the ESPN HTML represent a fixed salary-cap budget (e.g. $200
  default); confirm total budget from the HTML before hard-coding.
- If projected points are not embedded in the static HTML, a Selenium scrape or
  a secondary ESPN API call may be needed.

---

## Dependencies

- `beautifulsoup4`, `lxml` (already in `requirements.txt`)
- `pulp` — add if not present
- `selenium` — optional fallback; document in README if required
