"""
analysis/opponent_matchup_analysis.py

Goal 12, prototype for approach #2 ("weight projections by weeks 1-11 only,
and add strength-of-schedule adjustments"):

Uses Sleeper's per-week *projections* endpoint
(https://api.sleeper.app/projections/nfl/<season>/<week>) for the CURRENT
(upcoming) season to answer: does Sleeper already project fewer fantasy
points for a given position when facing a given opponent that week? e.g.
"Are RB projections systematically lower in weeks where the opponent is the
Seahawks?" This looks at forward-looking projections directly (not past
results), so it reflects Sleeper's own baked-in strength-of-schedule /
matchup view for the season we actually care about.

Approach
--------
1. Pull projections for one season (default: the current season) across a
   week range, caching each season/week response to disk so repeat runs
   don't re-hit the API.
2. Build a long DataFrame of (week, player_id, player_name, position, team,
   opponent, projected_points) rows using half-PPR scoring.
3. Because some offenses/players are just projected better than others, raw
   "average projected points allowed" comparisons are confounded by which
   players/offenses happen to play a given opponent across the season. To
   isolate the *opponent* effect, we player-center the data: for each
   player (with a minimum number of projected games), subtract their own
   average projected points across the season to get a residual. Averaging
   residuals per opponent tells you whether a position is projected to
   over/underperform its own baseline specifically against that opponent.
4. Run a one-way ANOVA (and eta-squared effect size) per position, testing
   whether opponent explains a statistically significant share of the
   residual variance. Also emit a per-opponent ranked table of average
   projected points allowed (raw and residual-adjusted) with sample sizes,
   suitable for use as a strength-of-schedule adjustment factor.

Usage
    python -m analysis.opponent_matchup_analysis
    python -m analysis.opponent_matchup_analysis --season 2026 --weeks 1-11
    python -m analysis.opponent_matchup_analysis --positions RB,WR --min-games 3
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from scipy import stats as scipy_stats

_HERE = Path(__file__).parent
REPO_ROOT = _HERE.parent
DATA_DIR = REPO_ROOT / "data"
OUTPUT_DIR = REPO_ROOT / "output"
PROJECTIONS_CACHE_DIR = DATA_DIR / "sleeper_projections_cache"
STATS_CACHE_DIR = DATA_DIR / "sleeper_stats_cache"

PROJECTIONS_URL_TMPL = "https://api.sleeper.app/projections/nfl/{season}/{week}?season_type=regular"
STATS_URL_TMPL = "https://api.sleeper.app/stats/nfl/{season}/{week}?season_type=regular"

DEFAULT_SOURCE = "projections"  # "projections" or "stats" (actual results)
DEFAULT_SEASON = 2026
DEFAULT_WEEKS = range(1, 19)
DEFAULT_POSITIONS = ["QB", "RB", "WR", "TE"]
DEFAULT_MIN_GAMES = 3  # minimum games played (across the pulled seasons) to include a player in the residual calc

# Sleeper uses "DEF" for defenses; normalize to this repo's "DST" convention
# elsewhere, but opponent team codes here are just standard team abbreviations.
POSITION_MAP = {"DEF": "DST"}

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fetching (with on-disk caching per season/week)
# ---------------------------------------------------------------------------

def fetch_week_projections(season: int, week: int, request_delay: float = 0.2) -> list[dict]:
    """Fetch (and cache) weekly projections for one season/week."""
    PROJECTIONS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = PROJECTIONS_CACHE_DIR / f"{season}_{week:02d}.json"
    if cache_path.exists():
        with open(cache_path, "r") as fh:
            return json.load(fh)

    url = PROJECTIONS_URL_TMPL.format(season=season, week=week)
    log.info(f"Fetching {url}")
    resp = requests.get(url, timeout=30)
    if resp.status_code != 200:
        log.warning(f"season={season} week={week}: HTTP {resp.status_code} — skipping.")
        return []
    try:
        data = resp.json()
    except ValueError:
        log.warning(f"season={season} week={week}: non-JSON response — skipping.")
        return []
    if not data:
        return []

    with open(cache_path, "w") as fh:
        json.dump(data, fh)
    time.sleep(request_delay)
    return data


def build_projections_dataframe(
    season: int,
    weeks: range,
    positions: list[str],
) -> pd.DataFrame:
    """Build a long DataFrame of per-player-week projected fantasy points."""
    rows = []
    for week in weeks:
        entries = fetch_week_projections(season, week)
        for entry in entries:
            player = entry.get("player") or {}
            position = player.get("position") or (player.get("fantasy_positions") or [None])[0]
            position = POSITION_MAP.get(position, position)
            if position not in positions:
                continue

            team = entry.get("team")
            opponent = entry.get("opponent")
            if not team or not opponent:
                continue

            stats = entry.get("stats") or {}
            projected_points = stats.get("pts_half_ppr")
            if projected_points is None:
                continue

            player_id = entry.get("player_id")
            player_name = " ".join(
                filter(None, [player.get("first_name"), player.get("last_name")])
            ) or player_id

            rows.append(
                {
                    "week": week,
                    "player_id": player_id,
                    "player_name": player_name,
                    "position": position,
                    "team": team,
                    "opponent": opponent,
                    "projected_points": float(projected_points),
                }
            )

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError(
            "No projection rows retrieved. Check season/week range — Sleeper may not "
            "have published projections for this range yet."
        )
    df = df.drop_duplicates(subset=["week", "player_id"])
    return df


def fetch_week_stats(season: int, week: int, request_delay: float = 0.2) -> list[dict]:
    """Fetch (and cache) weekly *actual* stats for one season/week."""
    STATS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = STATS_CACHE_DIR / f"{season}_{week:02d}.json"
    if cache_path.exists():
        with open(cache_path, "r") as fh:
            return json.load(fh)

    url = STATS_URL_TMPL.format(season=season, week=week)
    log.info(f"Fetching {url}")
    resp = requests.get(url, timeout=30)
    if resp.status_code != 200:
        log.warning(f"season={season} week={week}: HTTP {resp.status_code} — skipping.")
        return []
    try:
        data = resp.json()
    except ValueError:
        log.warning(f"season={season} week={week}: non-JSON response — skipping.")
        return []
    if not data:
        return []

    with open(cache_path, "w") as fh:
        json.dump(data, fh)
    time.sleep(request_delay)
    return data


def build_stats_dataframe(
    season: int,
    weeks: range,
    positions: list[str],
) -> pd.DataFrame:
    """Build a long DataFrame of per-player-week ACTUAL fantasy points."""
    rows = []
    for week in weeks:
        entries = fetch_week_stats(season, week)
        for entry in entries:
            player = entry.get("player") or {}
            position = player.get("position") or (player.get("fantasy_positions") or [None])[0]
            position = POSITION_MAP.get(position, position)
            if position not in positions:
                continue

            team = entry.get("team")
            opponent = entry.get("opponent")
            if not team or not opponent:
                continue

            stats = entry.get("stats") or {}
            actual_points = stats.get("pts_half_ppr")
            if actual_points is None:
                continue

            # Skip players who didn't actually play (Sleeper stats can include
            # zeroed-out rows for players who were inactive/bye).
            if stats.get("gp", 1) == 0:
                continue

            player_id = entry.get("player_id")
            player_name = " ".join(
                filter(None, [player.get("first_name"), player.get("last_name")])
            ) or player_id

            rows.append(
                {
                    "week": week,
                    "player_id": player_id,
                    "player_name": player_name,
                    "position": position,
                    "team": team,
                    "opponent": opponent,
                    "projected_points": float(actual_points),
                }
            )

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError(
            "No stats rows retrieved. Check season/week range — Sleeper may not "
            "have published stats for this range."
        )
    df = df.drop_duplicates(subset=["week", "player_id"])
    return df


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def add_player_centered_residuals(df: pd.DataFrame, min_games: int) -> pd.DataFrame:
    """
    Subtract each player's own average points (across all pulled games) to
    get a residual that isolates matchup effects from raw player quality.
    Players with fewer than `min_games` total games are dropped from the
    residual analysis (too noisy / residual would be ~0 by construction).
    """
    df = df.copy()
    game_counts = df.groupby("player_id")["projected_points"].transform("count")
    df["player_games"] = game_counts
    df = df[df["player_games"] >= min_games].copy()

    df["player_avg_points"] = df.groupby("player_id")["projected_points"].transform("mean")
    df["residual"] = df["projected_points"] - df["player_avg_points"]
    return df


def analyze_position(df_pos: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """
    For a single position's residual-augmented DataFrame, compute:
      - a per-opponent summary table (raw avg projected points, avg
        residual, n games, std error, simple z-score of the residual mean
        vs 0)
      - a one-way ANOVA of residuals grouped by opponent (does opponent
        explain a significant share of variance beyond player quality?)
    """
    groups = [g["residual"].values for _, g in df_pos.groupby("opponent") if len(g) >= 2]
    if len(groups) >= 2:
        f_stat, p_value = scipy_stats.f_oneway(*groups)
        ss_between = sum(len(g) * (g.mean() - df_pos["residual"].mean()) ** 2 for g in groups)
        ss_total = ((df_pos["residual"] - df_pos["residual"].mean()) ** 2).sum()
        eta_sq = ss_between / ss_total if ss_total > 0 else float("nan")
    else:
        f_stat, p_value, eta_sq = float("nan"), float("nan"), float("nan")

    summary = (
        df_pos.groupby("opponent")
        .agg(
            n_games=("projected_points", "count"),
            avg_projected_points=("projected_points", "mean"),
            avg_residual=("residual", "mean"),
            residual_std=("residual", "std"),
        )
        .reset_index()
    )
    summary["residual_stderr"] = summary["residual_std"] / np.sqrt(summary["n_games"])
    summary["residual_z"] = summary["avg_residual"] / summary["residual_stderr"].replace(0, np.nan)
    summary = summary.sort_values("avg_residual").reset_index(drop=True)

    anova_result = {"f_stat": f_stat, "p_value": p_value, "eta_squared": eta_sq}
    return summary, anova_result


def run_analysis(
    season: int,
    weeks: range,
    positions: list[str],
    min_games: int,
    source: str = DEFAULT_SOURCE,
) -> dict[str, tuple[pd.DataFrame, dict]]:
    if source == "stats":
        df = build_stats_dataframe(season, weeks, positions)
        log.info(f"Pulled {len(df)} player-week ACTUAL stats rows for season {season}")
    else:
        df = build_projections_dataframe(season, weeks, positions)
        log.info(f"Pulled {len(df)} player-week projection rows for season {season}")

    df = add_player_centered_residuals(df, min_games=min_games)
    log.info(f"{df['player_id'].nunique()} players retained (>= {min_games} games)")

    results = {}
    for position in positions:
        df_pos = df[df["position"] == position]
        if df_pos.empty:
            log.warning(f"No rows for position={position}, skipping.")
            continue
        summary, anova_result = analyze_position(df_pos)
        results[position] = (summary, anova_result)
    return results


# ---------------------------------------------------------------------------
# Goal 13, Step 3: rank-based adjustment curve + predicted difficulty ranking
# ---------------------------------------------------------------------------

RANK_CURVE_SHRINKAGE = 0.5  # simple overfitting guard, per goal 13 decisions
RANK_CURVE_SMOOTHING_WINDOW = 5  # rolling-average window across the 32 ranks


def build_rank_adjustment_curve(
    actual_effects_csv: Path = OUTPUT_DIR / "opponent_matchup_effects_actual_2025.csv",
    smoothing_window: int = RANK_CURVE_SMOOTHING_WINDOW,
    shrinkage: float = RANK_CURVE_SHRINKAGE,
) -> dict[str, list[float]]:
    """
    Build the smoothed, shrunk rank->value adjustment curve per position
    from last year's ACTUAL opponent matchup effects table.

    For each position, the 32 opponents are sorted by `avg_residual`
    (toughest matchup first, i.e. most negative residual = position
    suppressed most) to get a rank-ordered sequence of 32 values. That
    sequence is smoothed with a centered rolling average (to remove
    single-team noise while preserving the overall toughest->easiest
    shape) and then multiplied by `shrinkage` (default 0.5).

    Returns: {position: [value_at_rank_1, value_at_rank_2, ..., value_at_rank_32]}
    where rank 1 = toughest matchup, rank 32 = easiest.
    """
    if not actual_effects_csv.exists():
        raise FileNotFoundError(
            f"Actual opponent matchup effects file not found: {actual_effects_csv}. "
            "Run `python -m analysis.opponent_matchup_analysis --source stats` first."
        )

    df = pd.read_csv(actual_effects_csv)

    curves: dict[str, list[float]] = {}
    for position, df_pos in df.groupby("position"):
        ordered = df_pos.sort_values("avg_residual").reset_index(drop=True)
        residuals = ordered["avg_residual"]
        smoothed = (
            residuals.rolling(window=smoothing_window, center=True, min_periods=1)
            .mean()
        )
        curves[position] = (smoothed * shrinkage).tolist()
    return curves


def rank_opponents_by_predicted_difficulty(
    predicted_effects_csv: Path = OUTPUT_DIR / "opponent_matchup_effects.csv",
) -> dict[str, dict[str, int]]:
    """
    Build a {position: {opponent_team: rank}} lookup ranking each of the 32
    opponents toughest (rank 1) -> easiest (rank 32) per position, based on
    the *forward-looking* projections-based `avg_residual` in
    `opponent_matchup_effects.csv`.
    """
    if not predicted_effects_csv.exists():
        raise FileNotFoundError(
            f"Predicted opponent matchup effects file not found: {predicted_effects_csv}. "
            "Run `python -m analysis.opponent_matchup_analysis --source projections` first."
        )

    df = pd.read_csv(predicted_effects_csv)

    ranks: dict[str, dict[str, int]] = {}
    for position, df_pos in df.groupby("position"):
        ordered = df_pos.sort_values("avg_residual").reset_index(drop=True)
        ranks[position] = {
            row.opponent: idx + 1 for idx, row in enumerate(ordered.itertuples())
        }
    return ranks


def get_rank_adjustment(
    position: str,
    opponent: str,
    rank_curves: dict[str, list[float]],
    predicted_ranks: dict[str, dict[str, int]],
) -> float:
    """
    Look up the adjustment value (points/game) for a given (position,
    opponent) matchup this season: find the opponent's predicted difficulty
    rank for this position, then pull the smoothed/shrunk prior-year value
    at that same rank. Returns 0.0 if the position/opponent can't be
    resolved (e.g. missing data) rather than raising, so callers can apply
    a no-op adjustment gracefully.
    """
    curve = rank_curves.get(position)
    pos_ranks = predicted_ranks.get(position)
    if not curve or not pos_ranks:
        return 0.0
    rank = pos_ranks.get(opponent)
    if rank is None:
        return 0.0
    idx = rank - 1
    if idx < 0 or idx >= len(curve):
        return 0.0
    value = curve[idx]
    return 0.0 if pd.isna(value) else float(value)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_and_save_report(
    results: dict[str, tuple[pd.DataFrame, dict]],
    output_dir: Path = OUTPUT_DIR,
    top_n: int = 5,
    output_filename: str = "opponent_matchup_effects.csv",
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    all_rows = []

    for position, (summary, anova_result) in results.items():
        summary = summary.copy()
        summary.insert(0, "position", position)
        all_rows.append(summary)

        print(f"\n=== {position} — opponent matchup effect ===")
        print(
            f"ANOVA across opponents: F={anova_result['f_stat']:.3f}, "
            f"p={anova_result['p_value']:.4f}, eta^2={anova_result['eta_squared']:.4f}"
        )
        if anova_result["p_value"] is not None and not np.isnan(anova_result["p_value"]):
            if anova_result["p_value"] < 0.05:
                print("  -> Statistically significant opponent effect (p < 0.05).")
            else:
                print("  -> No statistically significant opponent effect at p < 0.05.")

        print(f"\nToughest {top_n} matchups (position projected BELOW own average):")
        print(
            summary.sort_values("avg_residual")
            .head(top_n)[["opponent", "n_games", "avg_projected_points", "avg_residual", "residual_z"]]
            .to_string(index=False)
        )

        print(f"\nEasiest {top_n} matchups (position projected ABOVE own average):")
        print(
            summary.sort_values("avg_residual", ascending=False)
            .head(top_n)[["opponent", "n_games", "avg_projected_points", "avg_residual", "residual_z"]]
            .to_string(index=False)
        )

    if all_rows:
        combined = pd.concat(all_rows, ignore_index=True)
        out_path = output_dir / output_filename
        combined.to_csv(out_path, index=False)
        log.info(f"Wrote combined opponent matchup table → {out_path}")


def parse_week_range(spec: str) -> range:
    if "-" in spec:
        start, end = spec.split("-", 1)
        return range(int(start), int(end) + 1)
    week = int(spec)
    return range(week, week + 1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze whether Sleeper's projected (or actual) points by position vary significantly by opponent."
    )
    parser.add_argument(
        "--source", type=str, default=DEFAULT_SOURCE, choices=["projections", "stats"],
        help="'projections' (forward-looking, current season) or 'stats' (actual historical results).",
    )
    parser.add_argument("--season", type=int, default=DEFAULT_SEASON, help="Season to analyze, e.g. 2026.")
    parser.add_argument("--weeks", type=str, default="1-18", help="Week range, e.g. '1-18' or '1-11'.")
    parser.add_argument(
        "--positions", type=str, default=",".join(DEFAULT_POSITIONS),
        help="Comma-separated positions, e.g. 'RB,WR'.",
    )
    parser.add_argument(
        "--min-games", type=int, default=DEFAULT_MIN_GAMES,
        help="Minimum projected games (within the pulled range) for a player to count toward residuals.",
    )
    parser.add_argument("--top-n", type=int, default=5)
    args = parser.parse_args()

    weeks = parse_week_range(args.weeks)
    positions = [p.strip().upper() for p in args.positions.split(",") if p.strip()]

    results = run_analysis(
        season=args.season, weeks=weeks, positions=positions, min_games=args.min_games, source=args.source,
    )
    output_filename = (
        "opponent_matchup_effects.csv" if args.source == "projections"
        else f"opponent_matchup_effects_actual_{args.season}.csv"
    )
    print_and_save_report(results, top_n=args.top_n, output_filename=output_filename)


if __name__ == "__main__":
    main()
