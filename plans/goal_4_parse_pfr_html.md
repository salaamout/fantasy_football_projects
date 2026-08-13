# Goal 4: Parse 2025 PFR Fantasy HTML → fantasy_half_ppr.csv

## Background

The existing `data/fantasy_half_ppr.csv` contains seasons 2020–2024, sourced via
the nflverse-data API (see `scrapers/pfr_fantasy.py`).  The 2025 season data is
missing.  Pro Football Reference blocks automated requests, so the page was saved
manually as:

```
data/2025 NFL Fantasy Rankings _ Pro-Football-Reference.com.html
```

This plan describes how to parse that file and append the 2025 rows into
`fantasy_half_ppr.csv` in the same format as the existing data.

---

## Target Schema

The CSV must match the existing columns exactly:

| Column | Type | Notes |
|---|---|---|
| `player_display_name` | str | Full name, e.g. `"Josh Allen"` |
| `position` | str | `QB`, `RB`, `WR`, or `TE` |
| `season` | int | `2025` for all rows from this file |
| `half_ppr_points` | float | `(fantasy_points + fantasy_points_ppr) / 2` |

---

## HTML Structure

The table of interest lives inside `<table id="fantasy">` (standard PFR
register table).  Relevant `data-stat` attributes per `<td>`:

| `data-stat` | Meaning |
|---|---|
| `player` | Player display name (text inside `<a>` tag) |
| `fantasy_pos` | Fantasy position string: QB / RB / WR / TE |
| `fantasy_points` | Standard scoring total (float) |
| `fantasy_points_ppr` | Full-PPR scoring total (float) |

**Gotchas to handle:**
- PFR repeats the header row every ~30 data rows.  These rows have
  `class="thead"` on the `<tr>` — skip them.
- Some cells are empty (e.g. a QB with no receiving stats).  Treat empty
  `fantasy_points` or `fantasy_points_ppr` as `0.0`.
- The `<td data-stat="player">` contains an `<a>` tag; pull `.get_text()`.
- Filter to only rows where `fantasy_pos` is in `{QB, RB, WR, TE}` — kickers
  and defenses appear in the table.
- Players who appeared on multiple teams have a row per team **plus** a "2TM"
  totals row.  Keep only the totals row (team value will be `"2TM"`, `"3TM"`,
  etc.) and drop the per-team rows.  Alternatively, keep only unique player
  names by taking the first occurrence after sorting by rank — inspect the HTML
  to confirm which approach PFR uses.

---

## Implementation Plan

### Step 1 — Install dependency (if needed)

```
pip install beautifulsoup4 lxml
```

`beautifulsoup4` and `lxml` are likely already present (check
`requirements.txt`).

### Step 2 — Create `scrapers/parse_pfr_html.py`

```
scrapers/parse_pfr_html.py
```

Logic outline:

```python
from bs4 import BeautifulSoup
import pandas as pd, os

HTML_PATH = "data/2025 NFL Fantasy Rankings _ Pro-Football-Reference.com.html"
OUT_CSV   = "data/fantasy_half_ppr.csv"
SEASON    = 2025
POSITIONS = {"QB", "RB", "WR", "TE"}

def parse_pfr_html(html_path: str, season: int) -> pd.DataFrame:
    with open(html_path, encoding="utf-8") as f:
        soup = BeautifulSoup(f, "lxml")

    table = soup.find("table", {"id": "fantasy"})
    rows  = []

    for tr in table.find("tbody").find_all("tr"):
        # Skip repeated header rows
        if "thead" in tr.get("class", []):
            continue

        def cell(stat):
            td = tr.find(attrs={"data-stat": stat})
            return td.get_text(strip=True) if td else ""

        pos  = cell("fantasy_pos")
        if pos not in POSITIONS:
            continue

        name = cell("player")   # text inside the <a>
        std  = float(cell("fantasy_points")     or 0)
        ppr  = float(cell("fantasy_points_ppr") or 0)

        rows.append({
            "player_display_name": name,
            "position":            pos,
            "season":              season,
            "half_ppr_points":     round((std + ppr) / 2, 2),
        })

    return pd.DataFrame(rows)

def main():
    new_df = parse_pfr_html(HTML_PATH, SEASON)

    existing = pd.read_csv(OUT_CSV)
    # Drop any stale 2025 rows in case this script is re-run
    existing = existing[existing["season"] != SEASON]

    combined = pd.concat([existing, new_df], ignore_index=True)
    combined.to_csv(OUT_CSV, index=False)
    print(f"Wrote {len(new_df)} 2025 rows → {OUT_CSV}")

if __name__ == "__main__":
    main()
```

### Step 3 — Validate output

After running the script:

1. **Row count:** `grep -c "^" data/fantasy_half_ppr.csv` — expect ~300 new
   rows (skill-position players with meaningful stats).
2. **Season present:** `cut -d, -f3 data/fantasy_half_ppr.csv | sort -u` —
   should show 2020–2025.
3. **Spot-check top players:** Confirm known 2025 leaders (e.g. Lamar Jackson)
   appear at the top of QB half-PPR.
4. **No duplicate players:** `python -c "import pandas as pd; df =
   pd.read_csv('data/fantasy_half_ppr.csv'); print(df[df.season==2025].groupby('player_display_name').size().sort_values(ascending=False).head())"` —
   each player should appear exactly once per position.

### Step 4 — Update requirements.txt

Add `beautifulsoup4` and `lxml` if not already listed.

### Step 5 — Re-run downstream analysis

After the CSV is updated, run the following from the project root to regenerate
all plots and CSVs:

```
python analysis/aggregate_par.py
```

This single script orchestrates the full pipeline — it calls `load_and_clean_data`,
`calculate_par`, `aggregate_par`, and `visualize_and_export` internally —
regenerating:

- `output/par_curves_by_position.png`
- `output/auction_value_top20.png`
- `output/par_heatmap.png`
- `data/tier_summary.csv`
- `data/cross_position_ranking.csv`

---

## Files Changed / Created

| File | Action |
|---|---|
| `scrapers/parse_pfr_html.py` | **Create** — HTML parser script |
| `data/fantasy_half_ppr.csv` | **Append** — 2025 season rows added |
| `requirements.txt` | **Update** — add `beautifulsoup4`, `lxml` if missing |

---

## Open Questions

- Do multi-team players have a totals row (e.g. `"2TM"`) in this page's HTML?
  Inspect a known mid-season trade player to confirm before deciding the
  deduplication strategy.
- Does PFR's 2025 page use the same `<table id="fantasy">` structure as prior
  years, or was the page layout changed?  (Visually confirmed from the saved
  HTML — `data-stat="fantasy_points_ppr"` is present.)
