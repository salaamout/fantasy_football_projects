from bs4 import BeautifulSoup
import pandas as pd
import os

HTML_PATH = "data/2025 NFL Fantasy Rankings _ Pro-Football-Reference.com.html"
OUT_CSV   = "data/fantasy_half_ppr.csv"
SEASON    = 2025
POSITIONS = {"QB", "RB", "WR", "TE"}


def parse_pfr_html(html_path: str, season: int) -> pd.DataFrame:
    with open(html_path, encoding="utf-8") as f:
        soup = BeautifulSoup(f, "lxml")

    table = soup.find("table", {"id": "fantasy"})
    if table is None:
        raise ValueError("Could not find <table id='fantasy'> in the HTML file.")

    rows = []
    seen_players = set()

    for tr in table.find("tbody").find_all("tr"):
        # Skip repeated header rows
        if "thead" in tr.get("class", []):
            continue

        def cell(stat):
            td = tr.find(attrs={"data-stat": stat})
            return td.get_text(strip=True) if td else ""

        pos = cell("fantasy_pos")
        if pos not in POSITIONS:
            continue

        name = cell("player")
        if not name:
            continue

        # Handle multi-team players: PFR lists per-team rows then a totals row
        # (e.g. "2TM"). We keep only the first occurrence of each player name,
        # which on PFR is the totals row when a player was traded.
        # PFR places the totals row first, followed by per-team rows.
        if name in seen_players:
            continue
        seen_players.add(name)

        std = float(cell("fantasy_points") or 0)
        ppr = float(cell("fantasy_points_ppr") or 0)

        rows.append({
            "player_display_name": name,
            "position":            pos,
            "season":              season,
            "half_ppr_points":     round((std + ppr) / 2, 2),
        })

    return pd.DataFrame(rows)


def main():
    new_df = parse_pfr_html(HTML_PATH, SEASON)
    print(f"Parsed {len(new_df)} 2025 player rows from HTML.")

    existing = pd.read_csv(OUT_CSV)
    # Drop any stale 2025 rows in case this script is re-run
    existing = existing[existing["season"] != SEASON]

    combined = pd.concat([existing, new_df], ignore_index=True)
    combined.to_csv(OUT_CSV, index=False)
    print(f"Wrote {len(new_df)} 2025 rows → {OUT_CSV}")
    print(f"Total rows in CSV: {len(combined)}")


if __name__ == "__main__":
    main()
