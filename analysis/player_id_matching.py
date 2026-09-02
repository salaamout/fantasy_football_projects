"""
Goal 10, Step 2: Normalize & align player identities across the two
per-player projection sources (ESPN and Sleeper).

Each source uses a slightly different name format (suffixes like Jr./Sr./III,
punctuation, nicknames). This module builds a shared "player key" (normalized
name + position) that can be used to join ESPN and Sleeper, plus a manual
alias-mapping file for stubborn mismatches.

NOTE: Historical data (`fantasy_half_ppr.csv`) is intentionally NOT joined by
player identity here. It's used downstream as a *positional rank curve*
(avg points at RB1, RB2, ... WR1, WR2, ...) that gets applied to whatever
rank a player lands at in the blended ESPN/Sleeper projection — see
`_build_historical_points_table` in `willingness_to_pay.py` for the existing
rank-indexed version of that curve.

Usage:
    python -m analysis.player_id_matching
"""

from __future__ import annotations

import csv
import json
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).parent
_ROOT = _HERE.parent
DATA_DIR = _ROOT / "data"
OUTPUT_DIR = _ROOT / "output"

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ESPN_CSV = DATA_DIR / "espn_projected_values.csv"
SLEEPER_CSV = DATA_DIR / "sleeper_projected_values.csv"
ALIASES_CSV = DATA_DIR / "player_aliases.csv"
UNMATCHED_REPORT_CSV = OUTPUT_DIR / "unmatched_players_report.csv"
BLENDED_CSV = DATA_DIR / "blended_projected_values.csv"
SLEEPER_PLAYERS_CACHE = DATA_DIR / "sleeper_players_cache.json"

# Reason code used when a blended-pool player can't be resolved to an NFL
# team via the Sleeper players cache (used by goal 13's schedule
# adjustment, which needs a team to look up weekly opponents).
NO_TEAM_MATCH_REASON = "no_team_match_for_schedule_adjustment"

# Positions we care about for cross-source matching.
VALID_POSITIONS = {"QB", "RB", "WR", "TE", "DST", "K"}

# Suffixes to strip during name normalization.
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


# ---------------------------------------------------------------------------
# Name normalization
# ---------------------------------------------------------------------------

def normalize_name(name: str) -> str:
    """
    Normalize a player name for cross-source matching:
      - lowercase
      - strip punctuation (periods, apostrophes, commas)
      - collapse hyphens to spaces
      - drop generational suffixes (Jr., Sr., II, III, IV, V)
      - collapse repeated whitespace
    """
    if not isinstance(name, str):
        return ""

    n = name.lower().strip()
    n = n.replace("-", " ")
    n = re.sub(r"[.,'’]", "", n)
    n = re.sub(r"\s+", " ", n).strip()

    tokens = [t for t in n.split(" ") if t not in _SUFFIXES]
    return " ".join(tokens)


def normalize_position(position: str) -> str:
    if not isinstance(position, str):
        return ""
    return position.strip().upper()


def load_alias_map(path: Path = ALIASES_CSV) -> dict[str, str]:
    """
    Load a manual alias-mapping file with columns: alias_name, canonical_name
    (both raw, pre-normalization strings). Returns a dict mapping the
    normalized alias -> normalized canonical name. Missing file returns {}.
    """
    if not path.exists():
        return {}

    alias_map: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            alias = normalize_name(row.get("alias_name", ""))
            canonical = normalize_name(row.get("canonical_name", ""))
            if alias and canonical:
                alias_map[alias] = canonical
    return alias_map


def build_player_key(name: str, position: str, alias_map: dict[str, str] | None = None) -> str:
    """
    Build a canonical join key of "normalized_name|POSITION".

    If an alias map is supplied and the normalized name matches a known
    alias, the canonical name is substituted before building the key.
    """
    norm_name = normalize_name(name)
    if alias_map and norm_name in alias_map:
        norm_name = alias_map[norm_name]
    norm_pos = normalize_position(position)
    return f"{norm_name}|{norm_pos}"


# ---------------------------------------------------------------------------
# Per-source loaders → tidy DataFrame with a `player_key` column
# ---------------------------------------------------------------------------

def load_espn_source(alias_map: dict[str, str] | None = None) -> pd.DataFrame:
    df = pd.read_csv(ESPN_CSV)
    df = df[df["position"].isin(VALID_POSITIONS)].copy()
    df["player_key"] = [
        build_player_key(n, p, alias_map) for n, p in zip(df["player_name"], df["position"])
    ]
    return df[["player_key", "player_name", "position", "projected_points"]].rename(
        columns={"player_name": "espn_player_name", "projected_points": "espn_projected_points"}
    )


def load_sleeper_source(alias_map: dict[str, str] | None = None) -> pd.DataFrame:
    df = pd.read_csv(SLEEPER_CSV)
    df = df[df["position"].isin(VALID_POSITIONS)].copy()
    df["player_key"] = [
        build_player_key(n, p, alias_map) for n, p in zip(df["player_name"], df["position"])
    ]
    return df[["player_key", "player_name", "position", "projected_points"]].rename(
        columns={"player_name": "sleeper_player_name", "projected_points": "sleeper_projected_points"}
    )


def load_historical_position_rank_curve(
    seasons: tuple[int, ...] = (2021, 2022, 2023, 2024, 2025)
) -> pd.DataFrame:
    """
    Return the historical *positional rank curve*: average half-PPR points
    at each (position, positional_rank) combo across the given seasons
    (e.g. RB1, RB2, ... WR1, WR2, ...).

    This is NOT joined by player identity — it's a shape/curve that gets
    applied to whatever rank a player lands at in the blended ESPN/Sleeper
    projection (Step 3/4). This mirrors `_build_historical_points_table` in
    `willingness_to_pay.py` but returns the raw per-rank average rather than
    par_points relative to a replacement baseline.
    """
    try:
        from .load_fantasy_data import load_and_clean_data
    except ImportError:
        sys.path.insert(0, str(_HERE))
        from load_fantasy_data import load_and_clean_data

    raw = load_and_clean_data()
    raw = raw[raw["season"].isin(seasons)]

    curve = (
        raw.groupby(["position", "positional_rank"], as_index=False)["half_ppr_points"]
        .mean()
        .rename(columns={"half_ppr_points": "historical_avg_points"})
        .sort_values(["position", "positional_rank"])
        .reset_index(drop=True)
    )
    return curve


# ---------------------------------------------------------------------------
# Goal 13, Step 2: player_name -> team lookup via Sleeper players cache
# ---------------------------------------------------------------------------

def load_sleeper_player_team_lookup(
    alias_map: dict[str, str] | None = None,
    cache_path: Path = SLEEPER_PLAYERS_CACHE,
) -> dict[str, str]:
    """
    Build a `player_key ("normalized_name|POSITION") -> team abbreviation`
    lookup from the cached Sleeper players dict (`sleeper_players_cache.json`).

    Only players with a non-null `team` and a `position` in
    `VALID_POSITIONS` are included. If multiple cached entries collide on
    the same player_key (e.g. duplicate/retired records), the first entry
    with a non-null team wins.
    """
    if not cache_path.exists():
        log.warning(f"Sleeper players cache not found at {cache_path} — team lookup will be empty.")
        return {}

    with open(cache_path, encoding="utf-8") as fh:
        players = json.load(fh)

    lookup: dict[str, str] = {}
    for info in players.values():
        team = info.get("team")
        position = normalize_position(info.get("position", ""))
        full_name = info.get("full_name")
        if not team or position not in VALID_POSITIONS or not full_name:
            continue
        key = build_player_key(full_name, position, alias_map)
        lookup.setdefault(key, team)
    return lookup


def map_blended_players_to_team(
    blended_df: pd.DataFrame,
    alias_map: dict[str, str] | None = None,
    team_lookup: dict[str, str] | None = None,
) -> pd.DataFrame:
    """
    Add a `team` column to a blended projections DataFrame
    (`player_name`, `position`, ...) by joining against the Sleeper
    players-cache team lookup.

    Players that can't be resolved to a team get `team = None` (schedule
    adjustment should fall back to a 1.0/no-op multiplier for these rather
    than dropping them).
    """
    if alias_map is None:
        alias_map = load_alias_map()
    if team_lookup is None:
        team_lookup = load_sleeper_player_team_lookup(alias_map)

    out = blended_df.copy()
    out["player_key"] = [
        build_player_key(n, p, alias_map) for n, p in zip(out["player_name"], out["position"])
    ]
    out["team"] = out["player_key"].map(team_lookup)
    return out


def report_unmatched_team_players(
    mapped_df: pd.DataFrame,
    output_path: Path = UNMATCHED_REPORT_CSV,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    Append rows for blended-pool players whose `team` couldn't be resolved
    (see `map_blended_players_to_team`) to the shared unmatched-players
    report, tagged with `reason = NO_TEAM_MATCH_REASON`.

    Existing rows in the report (from `report_unmatched`, i.e. cross-source
    ESPN/Sleeper mismatches) are preserved; a `reason` column is added if
    missing, defaulting existing rows to "cross_source_mismatch".
    """
    unmatched = mapped_df[mapped_df["team"].isna()].copy()
    unmatched = unmatched[["player_key", "player_name", "position", "projected_points"]]
    unmatched["reason"] = NO_TEAM_MATCH_REASON

    if output_path.exists():
        existing = pd.read_csv(output_path)
        if "reason" not in existing.columns:
            existing["reason"] = "cross_source_mismatch"
        # Drop any stale rows from a previous run of this same check so
        # re-running doesn't duplicate entries.
        existing = existing[existing.get("reason") != NO_TEAM_MATCH_REASON]
        combined = pd.concat([existing, unmatched], ignore_index=True, sort=False)
    else:
        combined = unmatched

    output_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(output_path, index=False)

    if verbose:
        log.info(
            f"{len(unmatched)}/{len(mapped_df)} blended players could not be matched to a team "
            f"({NO_TEAM_MATCH_REASON}); appended to {output_path}"
        )

    return unmatched


# ---------------------------------------------------------------------------
# Cross-source join (ESPN ↔ Sleeper) + unmatched reporting
# ---------------------------------------------------------------------------

def build_player_key_table(alias_map: dict[str, str] | None = None) -> pd.DataFrame:
    """
    Outer-join ESPN and Sleeper sources on `player_key`.

    Returns a DataFrame with one row per unique player_key and columns from
    both sources (NaN where a source doesn't have that player), plus a
    `sources_matched` column (count of 1-2) and `position` reconciled from
    whichever source(s) provided it.
    """
    if alias_map is None:
        alias_map = load_alias_map()

    espn = load_espn_source(alias_map)
    sleeper = load_sleeper_source(alias_map)

    merged = espn.merge(sleeper, on="player_key", how="outer")

    # Reconcile a single display name + position from whichever source(s) matched.
    merged["player_name"] = merged["espn_player_name"].fillna(merged["sleeper_player_name"])
    merged["position"] = merged["position_x"].fillna(merged["position_y"])

    merged["sources_matched"] = (
        merged["espn_projected_points"].notna().astype(int)
        + merged["sleeper_projected_points"].notna().astype(int)
    )

    cols = [
        "player_key",
        "player_name",
        "position",
        "espn_projected_points",
        "sleeper_projected_points",
        "sources_matched",
    ]
    merged = merged[cols].sort_values(
        ["sources_matched", "player_key"], ascending=[True, True]
    ).reset_index(drop=True)

    return merged


def report_unmatched(
    merged: pd.DataFrame, output_path: Path = UNMATCHED_REPORT_CSV, verbose: bool = True
) -> pd.DataFrame:
    """
    Filter to rows matched by fewer than both sources, write to CSV for
    manual review, and (optionally) print a summary.
    """
    unmatched = merged[merged["sources_matched"] < 2].copy()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    unmatched.to_csv(output_path, index=False)

    if verbose:
        counts = merged["sources_matched"].value_counts().sort_index()
        log.info(f"Total unique player keys: {len(merged)}")
        for n_sources in sorted(counts.index):
            log.info(f"  matched in {n_sources}/2 sources: {counts[n_sources]}")
        log.info(f"Wrote {len(unmatched)} unmatched rows → {output_path}")

    return unmatched


def main() -> None:
    alias_map = load_alias_map()
    if alias_map:
        log.info(f"Loaded {len(alias_map)} alias mappings from {ALIASES_CSV}")
    else:
        log.info(f"No alias file found at {ALIASES_CSV} (or it's empty) — skipping alias substitution.")

    merged = build_player_key_table(alias_map)
    report_unmatched(merged)

    print("\nSample fully-matched rows (ESPN + Sleeper):")
    full = merged[merged["sources_matched"] == 2]
    print(full.head(10).to_string(index=False))

    print("\nHistorical positional rank curve (sample):")
    curve = load_historical_position_rank_curve()
    print(curve.head(10).to_string(index=False))

    if BLENDED_CSV.exists():
        print("\nGoal 13, Step 2: mapping blended players -> team...")
        blended = pd.read_csv(BLENDED_CSV)
        team_lookup = load_sleeper_player_team_lookup(alias_map)
        mapped = map_blended_players_to_team(blended, alias_map, team_lookup)
        report_unmatched_team_players(mapped)
        matched_pct = mapped["team"].notna().mean() * 100
        print(f"Matched {matched_pct:.1f}% of {len(mapped)} blended players to a team.")
    else:
        log.info(f"No blended CSV found at {BLENDED_CSV} — skipping player->team mapping demo.")


if __name__ == "__main__":
    main()
