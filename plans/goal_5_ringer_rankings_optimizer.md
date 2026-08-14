# Goal 5: Parse Ringer 2026 Rankings → Lineup Value Optimizer

## Background

The 2026 Ringer Preseason Fantasy Football Rankings page was saved manually as:

```
data/2026 Ringer Preseason Fantasy Football Rankings _ The Ringer Fantasy Football.html
```

The page embeds all player data as escaped JSON inside a `<script>` tag (Next.js
`__NEXT_DATA__` or equivalent hydration payload).  Each player object contains:

- Player name and slug
- Position (`qb`, `rb`, `wr`, `te`)
- NFL team
- `positionalRankings` — per-format positional rank (we care about `half-ppr`)
- `rankings` — overall cross-position rank (per format)
- `auctionValues` — per-format auction dollar value, stored as strings like
  `"$$65"` (strip the leading `$` to get an integer)

This plan describes two deliverables:

1. **`scrapers/parse_ringer_html.py`** — parse the HTML → `data/ringer_2026_rankings.csv`
2. **`analysis/lineup_optimizer.py`** — use auction values as cost and
   historical average PAR-per-slot as expected value to find the best starting
   lineup within a budget

---

## Part 1 — Parse Ringer HTML

### Target Schema (`data/ringer_2026_rankings.csv`)

| Column | Type | Notes |
|---|---|---|
| `player_name` | str | e.g. `"Jahmyr Gibbs"` |
| `position` | str | Uppercase: `QB`, `RB`, `WR`, `TE` |
| `team` | str | e.g. `"Lions"` |
| `overall_rank` | int | `rankings["half-ppr"]` from the JSON |
| `positional_rank` | int | `positionalRankings["half-ppr"]` from the JSON |
| `auction_value` | int | `auctionValues["half-ppr"]` stripped of `$` signs |
| `bye_week` | int | `playerMeta["byeWeek"]` |

Players ranked `9999` (dynasty-only or irrelevant format placeholder) in
`rankings["half-ppr"]` should be **excluded** — they are dynasty-only entries
with no meaningful redraft rank.

### HTML Structure

The player data lives inside a large `<script>` block.  The content is a JSON
object rendered server-side and HTML-escaped; each player appears as an object
matching this shape (after unescaping):

```json
{
  "name": "Jahmyr Gibbs",
  "slug": "jahmyr_gibbs",
  "position": "rb",
  "team": "Lions",
  "positionalRankings": { "half-ppr": 1, "ppr": 1, "zero-ppr": 1, ... },
  "rankings":           { "half-ppr": 1, "ppr": 1, "zero-ppr": 1, ... },
  "auctionValues":      { "half-ppr": "$$65", "ppr": "$$65", ... },
  "playerMeta":         { "byeWeek": 6, ... }
}
```

**Parsing approach:**

1. Load the HTML with `BeautifulSoup`.
2. Find all `<script>` tags.  The relevant one contains the string
   `"auctionValues"` — use that as a filter.
3. The script content is a large escaped JSON string.  Use Python's
   `html.unescape()` to convert `&quot;` / `\"` entities, then locate the JSON
   array of player objects.  Alternatively, use a regex to extract repeated
   player-shaped JSON blobs (each containing `"auctionValues"`).
4. Parse each blob with `json.loads()`.
5. Filter and normalize into a flat DataFrame.

### Implementation Plan

#### Step 1 — Create `scrapers/parse_ringer_html.py`

```
scrapers/parse_ringer_html.py
```

Logic outline:

```python
import re, json, html
from pathlib import Path
from bs4 import BeautifulSoup
import pandas as pd

HTML_PATH = Path("data/2026 Ringer Preseason Fantasy Football Rankings _ The Ringer Fantasy Football.html")
OUT_PATH  = Path("data/ringer_2026_rankings.csv")
FORMAT    = "half-ppr"

def parse_auction_value(raw: str) -> int:
    """'$$65' → 65.  Returns 0 if blank or unparseable."""
    cleaned = raw.replace("$", "").strip()
    return int(cleaned) if cleaned.isdigit() else 0

def extract_players(html_path: Path) -> pd.DataFrame:
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "lxml")

    # Find the script tag that contains the player data
    target_script = None
    for tag in soup.find_all("script"):
        if tag.string and "auctionValues" in tag.string:
            target_script = tag.string
            break

    if target_script is None:
        raise RuntimeError("Could not find player data in HTML")

    # Unescape HTML entities so we can work with raw JSON
    raw = html.unescape(target_script)

    # Each player JSON blob begins with '"name":' and ends at the matching '}'
    # Use a broader regex to grab individual player-shaped objects
    pattern = re.compile(r'\{[^{}]*"auctionValues"[^{}]*\}', re.DOTALL)
    blobs = pattern.findall(raw)

    rows = []
    for blob in blobs:
        try:
            obj = json.loads(blob)
        except json.JSONDecodeError:
            continue

        overall_rank = obj.get("rankings", {}).get(FORMAT, 9999)
        if overall_rank == 9999:
            continue  # dynasty-only or no redraft rank

        rows.append({
            "player_name":     obj.get("name", ""),
            "position":        obj.get("position", "").upper(),
            "team":            obj.get("team", ""),
            "overall_rank":    overall_rank,
            "positional_rank": obj.get("positionalRankings", {}).get(FORMAT, 9999),
            "auction_value":   parse_auction_value(
                                   obj.get("auctionValues", {}).get(FORMAT, "")
                               ),
            "bye_week":        obj.get("playerMeta", {}).get("byeWeek", 0),
        })

    df = pd.DataFrame(rows).sort_values("overall_rank").drop_duplicates(
        subset=["player_name", "position"]
    ).reset_index(drop=True)
    return df

if __name__ == "__main__":
    df = extract_players(HTML_PATH)
    df.to_csv(OUT_PATH, index=False)
    print(f"Saved {len(df)} players to {OUT_PATH}")
    print(df.head(20).to_string())
```

#### Step 2 — Validate output

Run the script and spot-check:

- Jahmyr Gibbs should be RB1, overall rank ~1, auction value ~65
- Ja'Marr Chase should be WR1
- There should be players for all four positions (QB, RB, WR, TE)
- No player should have `overall_rank == 9999`

---

## Part 2 — Lineup Value Optimizer

### Concept

The optimizer answers: **"Given auction values as costs, which set of players
fills my starting lineup and maximizes total expected PAR for the budget?"**

Expected PAR per player is derived from the **historical average PAR at that
player's positional slot** (e.g., the average PAR for RB4 across 2020–2025),
not from individual projections.  This treats the Ringer's positional rank as a
proxy for expected production tier.

### League Settings (configurable constants)

```python
LINEUP_SLOTS = {
    "QB":   1,
    "RB":   2,
    "WR":   3,
    "TE":   1,
    "FLEX": 1,   # RB/WR/TE
}
BUDGET = 200  # standard auction budget
```

### Data Sources

| Source | Used for |
|---|---|
| `data/ringer_2026_rankings.csv` | Player names, positions, positional ranks, auction values |
| `data/fantasy_half_ppr.csv` + existing PAR pipeline | Historical average PAR by (position, positional_rank) |

### PAR-per-Slot Lookup Table

Before running the optimizer, build a lookup table:

```
avg_par[(position, positional_rank)] → float
```

Steps:
1. Load `fantasy_half_ppr.csv` (seasons 2020–2025).
2. Run the existing `calculate_par` logic to add a `par` column.
3. For each (position, season), assign each player a `positional_rank` (1 =
   highest scorer that season).
4. Group by `(position, positional_rank)` and take the **mean PAR** across
   seasons.
5. For ranks that don't appear in every season (deep bench), use the mean of
   whatever seasons they appear in.

### Optimizer

Use a **Mixed Integer Linear Program (MILP)** via `scipy.optimize.milp` or
the lighter `PuLP` library (already installable via pip).

**Decision variable:** `x[i]` ∈ {0, 1} — whether player `i` is selected.

**Objective:** maximize `Σ avg_par[i] * x[i]`

**Constraints:**
1. Budget: `Σ auction_value[i] * x[i] ≤ BUDGET`
2. Minimum starters at each position:
   - `Σ x[i] for i ∈ QB = 1`
   - `Σ x[i] for i ∈ RB ≥ 2`
   - `Σ x[i] for i ∈ WR ≥ 3`
   - `Σ x[i] for i ∈ TE ≥ 1`
3. FLEX slot: total selected players = `QB + RB + WR + TE + FLEX` = 8;
   FLEX can be any RB/WR/TE not already filling a positional slot.
   The ≥ constraints on RB, WR, and TE combined with the total = 8 constraint
   naturally allow one extra RB, WR, or TE to fill the FLEX.
4. Each player can be selected at most once.

### Implementation Plan

#### Step 1 — Install PuLP (if not present)

```
pip install pulp
```

Add to `requirements.txt`.

#### Step 2 — Create `analysis/lineup_optimizer.py`

```
analysis/lineup_optimizer.py
```

Logic outline:

```python
import pandas as pd
import pulp
from pathlib import Path
from load_fantasy_data import load_and_clean_data
from calculate_par import calculate_par

RANKINGS_PATH = Path("data/ringer_2026_rankings.csv")
BUDGET        = 200
LINEUP_SLOTS  = {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "FLEX": 1}
FLEX_POSITIONS = {"RB", "WR", "TE"}

def build_avg_par_lookup(seasons=(2020, 2021, 2022, 2023, 2024, 2025)) -> dict:
    """Returns {(position, positional_rank): avg_par_float}."""
    df = load_and_clean_data()
    df = df[df["season"].isin(seasons)]
    df = calculate_par(df)

    # Assign positional rank within each (position, season)
    df["pos_rank"] = (
        df.groupby(["position", "season"])["half_ppr_points"]
          .rank(method="first", ascending=False)
          .astype(int)
    )

    lookup = (
        df.groupby(["position", "pos_rank"])["par"]
          .mean()
          .to_dict()
    )
    return lookup

def attach_expected_par(rankings: pd.DataFrame, lookup: dict) -> pd.DataFrame:
    """
    Map each player's positional_rank to their expected PAR from the lookup.
    Falls back to the minimum PAR in that position if the rank exceeds the table.
    """
    min_par = {}
    for (pos, rank), val in lookup.items():
        min_par[pos] = min(min_par.get(pos, val), val)

    def _get_par(row):
        key = (row["position"], row["positional_rank"])
        return lookup.get(key, min_par.get(row["position"], 0.0))

    rankings["expected_par"] = rankings.apply(_get_par, axis=1)
    return rankings

def optimize_lineup(rankings: pd.DataFrame) -> pd.DataFrame:
    prob = pulp.LpProblem("lineup_optimizer", pulp.LpMaximize)

    players = rankings.to_dict("records")
    x = [pulp.LpVariable(f"x_{i}", cat="Binary") for i in range(len(players))]

    # Objective
    prob += pulp.lpSum(p["expected_par"] * x[i] for i, p in enumerate(players))

    # Budget
    prob += pulp.lpSum(p["auction_value"] * x[i] for i, p in enumerate(players)) <= BUDGET

    # Positional constraints: QB is exact; RB, WR, TE are minimums so the
    # FLEX slot can be filled by any of those three positions.
    prob += pulp.lpSum(x[i] for i, p in enumerate(players) if p["position"] == "QB") == LINEUP_SLOTS["QB"]
    for pos in ("RB", "WR", "TE"):
        prob += pulp.lpSum(
            x[i] for i, p in enumerate(players) if p["position"] == pos
        ) >= LINEUP_SLOTS[pos]

    # FLEX: total roster size = sum of all slot counts
    total_slots = sum(LINEUP_SLOTS.values())
    prob += pulp.lpSum(x) == total_slots

    prob.solve(pulp.PULP_CBC_CMD(msg=False))

    selected = [players[i] for i, v in enumerate(x) if pulp.value(v) == 1]
    result = pd.DataFrame(selected).sort_values(["position", "positional_rank"])
    return result

if __name__ == "__main__":
    lookup   = build_avg_par_lookup()
    rankings = pd.read_csv(RANKINGS_PATH)
    rankings = attach_expected_par(rankings, lookup)
    lineup   = optimize_lineup(rankings)

    total_cost = lineup["auction_value"].sum()
    total_par  = lineup["expected_par"].sum()

    print(f"\n=== Optimal Starting Lineup (Budget: ${BUDGET}) ===")
    print(lineup[["position", "player_name", "team", "positional_rank",
                   "auction_value", "expected_par"]].to_string(index=False))
    print(f"\nTotal auction cost : ${total_cost}")
    print(f"Total expected PAR : {total_par:.1f}")
```

#### Step 3 — Validate output

- Total auction cost should be ≤ $200
- The lineup should have exactly 1 QB, 2 RB, 3 WR, 1 TE, 1 FLEX (RB/WR/TE)
- Top-auction-value players should generally appear unless a cheaper combo
  yields more PAR
- Try varying `BUDGET` from $160–$200 to see how the lineup shifts

---

## Files to Create

| File | Purpose |
|---|---|
| `scrapers/parse_ringer_html.py` | Parse HTML → `ringer_2026_rankings.csv` |
| `data/ringer_2026_rankings.csv` | Output of parser |
| `analysis/lineup_optimizer.py` | PAR lookup + MILP optimizer |

## Dependencies to Add to `requirements.txt`

- `pulp` — MILP solver wrapper (CBC backend is bundled)
- `beautifulsoup4` + `lxml` — already present from Goal 4; confirm in
  `requirements.txt`

## Open Questions / Assumptions

1. **Scoring format** — defaulting to `half-ppr` throughout, matching the
   existing PAR analysis.  Change `FORMAT = "half-ppr"` in both files to switch.
2. **Replacement level** — the optimizer uses the same `REPLACEMENT_RANKS` from
   `calculate_par.py` (QB13, RB32, WR42, TE13).  If your league's roster
   settings differ, update that constant first.
3. **Auction value meaning** — Ringer values represent a $200 budget standard
   auction.  If your league uses a different budget, scale linearly:
   `auction_value * (your_budget / 200)`.
4. **FLEX eligibility** — defaulting to RB/WR/TE.  TE-premium leagues may want
   to weight TE differently or add a SuperFlex slot.
5. **Multi-season PAR averaging** — seasons 2020–2025 are used.  Exclude older
   seasons if you believe league-wide scoring has shifted meaningfully.
