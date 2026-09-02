"""
scrapers/parse_espn_html.py

Parses the locally saved ESPN Salary-Cap Draft List HTML and writes
data/espn_projected_values.csv.

Usage:
    python scrapers/parse_espn_html.py
"""

import re
import csv
import logging
from pathlib import Path
from collections import defaultdict

from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
INPUT_HTML = REPO_ROOT / "data" / "ESPN Salary Cap Draft List 26_09_01.html"
OUTPUT_CSV = REPO_ROOT / "data" / "espn_projected_values.csv"

# ---------------------------------------------------------------------------
# Schema column order
# ---------------------------------------------------------------------------
FIELDNAMES = [
    "player_name",
    "position",
    "team",
    "overall_rank",
    "positional_rank",
    "auction_value",
    "projected_points",
]

# Map ESPN position abbreviations → normalised uppercase
POSITION_MAP = {
    "qb": "QB",
    "rb": "RB",
    "wr": "WR",
    "te": "TE",
    "k":  "K",
    "dst": "DST",
    "d/st": "DST",
    "def": "DST",
}

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def normalise_position(raw: str) -> str:
    # Some players have multi-position designations like "WR, CB"; take the first.
    primary = raw.strip().split(",")[0].strip()
    return POSITION_MAP.get(primary.lower(), primary.upper())


def parse_espn_html(html_path: Path = INPUT_HTML) -> list[dict]:
    """
    Parse the ESPN Salary-Cap Draft List HTML and return a list of player dicts.
    """
    log.info(f"Reading {html_path}")
    with open(html_path, "r", errors="replace") as fh:
        soup = BeautifulSoup(fh, "lxml")

    table = soup.find("table")
    if table is None:
        raise ValueError("No <table> found in ESPN HTML — page structure may have changed.")

    rows = table.find_all("tr")
    log.info(f"Found {len(rows)} table rows (including headers)")

    pos_counter: defaultdict[str, int] = defaultdict(int)
    players: list[dict] = []
    dropped = 0

    for row in rows:
        # Only data rows carry data-idx
        raw_idx = row.get("data-idx")
        if raw_idx is None:
            continue

        overall_rank = int(raw_idx) + 1  # convert 0-based index → 1-based rank

        # --- Auction value ---------------------------------------------------
        salary_input = row.find("input", class_="salary")
        if salary_input is None or not salary_input.get("value", "").strip():
            log.warning(f"Row {overall_rank}: missing salary input — skipping.")
            dropped += 1
            continue
        try:
            auction_value = int(salary_input["value"])
        except ValueError:
            log.warning(f"Row {overall_rank}: non-numeric salary '{salary_input['value']}' — skipping.")
            dropped += 1
            continue

        # --- Player name -----------------------------------------------------
        # The first <a class="AnchorLink link clr-link pointer"> that is NOT
        # the news icon link holds the player name.
        name_links = row.find_all(
            "a",
            class_=lambda c: c and "AnchorLink" in c and "playerinfo__news" not in c,
        )
        if not name_links:
            log.warning(f"Row {overall_rank}: player name not found — skipping.")
            dropped += 1
            continue
        player_name = name_links[0].get_text(strip=True)
        if not player_name:
            log.warning(f"Row {overall_rank}: empty player name — skipping.")
            dropped += 1
            continue

        # --- Team ------------------------------------------------------------
        team_el = row.find("span", class_="playerinfo__playerteam")
        team = team_el.get_text(strip=True) if team_el else ""

        # --- Position --------------------------------------------------------
        pos_el = row.find("span", class_="playerinfo__playerpos")
        if pos_el is None:
            log.warning(f"Row {overall_rank} ({player_name}): position not found — skipping.")
            dropped += 1
            continue
        position = normalise_position(pos_el.get_text(strip=True))

        # --- Projected points (FPTS) — last <td> in the row -----------------
        tds = row.find_all("td")
        if not tds:
            log.warning(f"Row {overall_rank} ({player_name}): no <td> cells — skipping.")
            dropped += 1
            continue
        fpts_text = tds[-1].get_text(strip=True)
        try:
            projected_points = float(fpts_text)
        except ValueError:
            log.warning(
                f"Row {overall_rank} ({player_name}): non-numeric FPTS '{fpts_text}' — skipping."
            )
            dropped += 1
            continue

        # --- Positional rank -------------------------------------------------
        pos_counter[position] += 1
        positional_rank = pos_counter[position]

        players.append(
            {
                "player_name": player_name,
                "position": position,
                "team": team,
                "overall_rank": overall_rank,
                "positional_rank": positional_rank,
                "auction_value": auction_value,
                "projected_points": projected_points,
            }
        )

    log.info(f"Parsed {len(players)} players; dropped {dropped} rows.")
    return players


def write_csv(players: list[dict], output_path: Path = OUTPUT_CSV) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(players)
    log.info(f"Wrote {len(players)} rows → {output_path}")


def main() -> None:
    players = parse_espn_html()

    # Summary by position
    pos_counts: defaultdict[str, int] = defaultdict(int)
    for p in players:
        pos_counts[p["position"]] += 1
    log.info("Players by position: " + ", ".join(f"{k}={v}" for k, v in sorted(pos_counts.items())))

    write_csv(players)


if __name__ == "__main__":
    main()
