"""
scrapers/parse_sleeper.py

Pulls season-long projected fantasy points from Sleeper's public read-only
API and writes data/sleeper_projected_values.csv.

Sleeper exposes two relevant endpoints:
  - https://api.sleeper.app/v1/players/nfl
        Full player metadata dump (player_id -> {full_name, position, team, ...}).
        Large (~5MB); should be cached locally after first fetch.
  - https://api.sleeper.com/projections/nfl/<season>?season_type=regular&position[]=QB...
        A genuine SEASON-LONG projection endpoint (one row per player, with
        game_id == "season" / week == null). This is the same data Sleeper's
        own draft-board UI displays (verified byte-for-byte against a saved
        draft page: Josh Allen 361.5 pts_half_ppr both places).

NOTE (2026-09-01): this script previously built season totals by SUMMING
weekly projections across weeks 1-18 from
`api.sleeper.app/projections/nfl/<season>/<week>`. That approach
systematically over-projected QBs (e.g. Jalen Hurts 368.9 summed-weekly vs.
310.5 from the real season endpoint / draft board) because weekly
projections don't discount for things the season model already accounts for.
We now call the season endpoint directly instead.

Usage:
    python scrapers/parse_sleeper.py
    python scrapers/parse_sleeper.py --season 2026
"""

import argparse
import csv
import json
import logging
from collections import defaultdict
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_CSV = REPO_ROOT / "data" / "sleeper_projected_values.csv"
PLAYERS_CACHE = REPO_ROOT / "data" / "sleeper_players_cache.json"

PLAYERS_URL = "https://api.sleeper.app/v1/players/nfl"
SEASON_PROJECTIONS_URL_TMPL = (
    "https://api.sleeper.com/projections/nfl/{season}"
    "?season_type=regular"
    "&position[]=QB&position[]=RB&position[]=WR&position[]=TE"
    "&position[]=K&position[]=DEF"
    "&order_by=adp_ppr"
)

DEFAULT_SEASON = 2026

FANTASY_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF"}

# Normalise Sleeper's "DEF" to the convention used elsewhere in this repo.
POSITION_MAP = {
    "DEF": "DST",
}

FIELDNAMES = [
    "player_name",
    "position",
    "team",
    "positional_rank",
    "projected_points",
]

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def fetch_players(use_cache: bool = True) -> dict:
    """
    Fetch (and cache) the full Sleeper player directory.

    Returns a dict of player_id -> player metadata dict.
    """
    if use_cache and PLAYERS_CACHE.exists():
        log.info(f"Loading cached player directory from {PLAYERS_CACHE}")
        with open(PLAYERS_CACHE, "r") as fh:
            return json.load(fh)

    log.info(f"Fetching player directory from {PLAYERS_URL}")
    resp = requests.get(PLAYERS_URL, timeout=30)
    resp.raise_for_status()
    players = resp.json()

    PLAYERS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(PLAYERS_CACHE, "w") as fh:
        json.dump(players, fh)
    log.info(f"Cached {len(players)} players → {PLAYERS_CACHE}")
    return players


def fetch_season_projections(season: int) -> list[dict]:
    """
    Fetch the season-long projections in a single call. Returns a list of
    per-player projection dicts (Sleeper's response shape), one row per
    player, with stats["pts_half_ppr"] etc. already reflecting a full
    season (bye weeks / usage regression already baked in by Sleeper).
    """
    url = SEASON_PROJECTIONS_URL_TMPL.format(season=season)
    log.info(f"Fetching season projections: {url}")
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError:
        log.warning("Non-JSON response from season projections endpoint.")
        return []
    if not data:
        log.warning("Empty response from season projections endpoint.")
        return []
    return data


def build_season_projections(season: int = DEFAULT_SEASON) -> dict:
    """
    Fetch season-long fantasy-point projections per player_id.

    Returns dict: player_id -> projected_points (float)
    """
    entries = fetch_season_projections(season)
    totals: dict[str, float] = {}

    for entry in entries:
        player_id = entry.get("player_id")
        stats = entry.get("stats") or {}
        if not player_id or not stats:
            continue
        pts = stats.get("pts_half_ppr")
        if pts is None:
            pts = stats.get("pts_ppr")
        if pts is None:
            pts = stats.get("pts_std")
        if pts is None:
            continue
        totals[player_id] = float(pts)

    log.info(f"Fetched season projections for {len(totals)} players")
    return totals


def build_player_rows(totals: dict, players: dict) -> list[dict]:
    """
    Join per-player point totals with player metadata, filter to fantasy-relevant
    positions, and compute positional rank.
    """
    rows = []
    for player_id, projected_points in totals.items():
        meta = players.get(player_id)
        if meta is None:
            continue
        position = meta.get("position")
        if position not in FANTASY_POSITIONS:
            continue
        player_name = meta.get("full_name") or " ".join(
            filter(None, [meta.get("first_name"), meta.get("last_name")])
        )
        if not player_name:
            continue
        team = meta.get("team") or ""
        rows.append(
            {
                "player_name": player_name,
                "position": POSITION_MAP.get(position, position),
                "team": team,
                "projected_points": round(projected_points, 2),
            }
        )

    # Sort by position then descending points to assign positional_rank
    rows.sort(key=lambda r: (r["position"], -r["projected_points"]))
    pos_counter: defaultdict[str, int] = defaultdict(int)
    for row in rows:
        pos_counter[row["position"]] += 1
        row["positional_rank"] = pos_counter[row["position"]]

    return rows


def write_csv(rows: list[dict], output_path: Path = OUTPUT_CSV) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    log.info(f"Wrote {len(rows)} rows → {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull Sleeper season projections.")
    parser.add_argument("--season", type=int, default=DEFAULT_SEASON)
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Force re-download of the player directory instead of using the cache.",
    )
    args = parser.parse_args()

    players = fetch_players(use_cache=not args.no_cache)
    totals = build_season_projections(season=args.season)

    if not totals:
        log.error(
            "No projections found. Sleeper likely hasn't published season "
            "projections yet, or the endpoint/params changed. "
            "See plans/goal_10_multi_source_projections.md Step 1 fallback option."
        )

    rows = build_player_rows(totals, players)

    pos_counts: defaultdict[str, int] = defaultdict(int)
    for r in rows:
        pos_counts[r["position"]] += 1
    log.info("Players by position: " + ", ".join(f"{k}={v}" for k, v in sorted(pos_counts.items())))

    write_csv(rows)


if __name__ == "__main__":
    main()
