"""
analysis/schedule_adjustment.py

Goal 13, Step 3: compute a per-player schedule adjustment factor for the
blended WTP pipeline, wiring together:
  - `analysis/team_schedule.py` (per-team weekly opponents + bye weeks)
  - `analysis/player_id_matching.py` (blended player_name -> team)
  - `analysis/opponent_matchup_analysis.py` (rank-based adjustment curve +
    predicted opponent difficulty ranking)

For each player in the blended projections pool, this produces a
`schedule_adjusted_points` column reflecting how their specific weeks 1-11
schedule compares to a flat, schedule-agnostic per-game baseline, per
`plans/goal_13_schedule_adjusted_blended_wtp.md`.

Usage
    from analysis.schedule_adjustment import compute_schedule_adjusted_points

    adjusted_df = compute_schedule_adjusted_points(blended_df)

    python -m analysis.schedule_adjustment
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from analysis.opponent_matchup_analysis import (
    DEFAULT_SEASON,
    build_rank_adjustment_curve,
    get_rank_adjustment,
    rank_opponents_by_predicted_difficulty,
)
from analysis.player_id_matching import (
    load_alias_map,
    load_sleeper_player_team_lookup,
    map_blended_players_to_team,
    report_unmatched_team_players,
)
from analysis.team_schedule import (
    EARLY_WEEKS,
    build_team_schedule,
    count_games_in_range,
)

_HERE = Path(__file__).parent
_ROOT = _HERE.parent
DATA_DIR = _ROOT / "data"
BLENDED_CSV = DATA_DIR / "blended_projected_values.csv"

# Full-season game count assumption for the per-game baseline when a team's
# real schedule can't be resolved (17-game NFL regular season).
DEFAULT_SEASON_GAMES = 17

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def _early_schedule_residual(
    team: str | None,
    position: str,
    team_schedule: dict[str, dict[int, str]],
    rank_curves: dict[str, list[float]],
    predicted_ranks: dict[str, dict[str, int]],
) -> tuple[float, int]:
    """
    Average the per-week rank-based adjustment value across a team's weeks
    1-11 opponents for the given position.

    Returns (early_schedule_residual, n_early_games). If the team can't be
    resolved (no schedule data), returns (0.0, len(EARLY_WEEKS)) as a no-op
    fallback (assumes the full 11-week slate with no bye known).
    """
    if not team:
        return 0.0, len(list(EARLY_WEEKS))

    team_weeks = team_schedule.get(team, {})
    early_opponents = [team_weeks[w] for w in EARLY_WEEKS if w in team_weeks]
    n_early_games = len(early_opponents)
    if n_early_games == 0:
        return 0.0, 0

    adjustments = [
        get_rank_adjustment(position, opp, rank_curves, predicted_ranks)
        for opp in early_opponents
    ]
    avg_adjustment = sum(adjustments) / len(adjustments)
    return avg_adjustment, n_early_games


def compute_schedule_adjusted_points(
    blended_df: pd.DataFrame,
    season: int = DEFAULT_SEASON,
    alias_map: dict[str, str] | None = None,
    team_lookup: dict[str, str] | None = None,
    team_schedule: dict[str, dict[int, str]] | None = None,
    rank_curves: dict[str, list[float]] | None = None,
    predicted_ranks: dict[str, dict[str, int]] | None = None,
    report_unmatched: bool = True,
) -> pd.DataFrame:
    """
    Add schedule-adjustment columns to a blended projections DataFrame
    (expects `player_name`, `position`, `projected_points` columns, e.g.
    `data/blended_projected_values.csv`).

    New columns:
      - `team`: resolved via the Sleeper players cache (None if unmatched).
      - `n_early_games`: number of weeks 1-11 the player's team actually
        plays (accounts for a bye week landing in that range).
      - `n_season_games`: total scheduled weeks for the team across the
        full season (used for the per-game baseline); falls back to
        `DEFAULT_SEASON_GAMES` if the team can't be resolved.
      - `per_game_points`: `projected_points / n_season_games`.
      - `early_schedule_residual`: average per-game rank-based matchup
        adjustment (points/game) across the player's weeks 1-11 opponents;
        0.0 for unmatched players (no-op adjustment).
      - `schedule_adjusted_points`: `n_early_games * (per_game_points +
        early_schedule_residual)`, scaled back up to a full-season-comparable
        total via the ratio of a full season's games to early games so it's
        directly comparable to `projected_points`
        (see note below on scaling).

    Players who can't be matched to a team fall back to a 1.0/no-op
    adjustment (schedule_adjusted_points == projected_points) rather than
    being dropped, and are logged to `output/unmatched_players_report.csv`
    (reason=`no_team_match_for_schedule_adjustment`) when `report_unmatched`
    is True.
    """
    if alias_map is None:
        alias_map = load_alias_map()
    if team_lookup is None:
        team_lookup = load_sleeper_player_team_lookup(alias_map)

    mapped = map_blended_players_to_team(blended_df, alias_map, team_lookup)

    if report_unmatched:
        report_unmatched_team_players(mapped)

    if team_schedule is None:
        team_schedule = build_team_schedule(season=season)
    if rank_curves is None:
        rank_curves = build_rank_adjustment_curve()
    if predicted_ranks is None:
        predicted_ranks = rank_opponents_by_predicted_difficulty()

    n_season_games_col = []
    n_early_games_col = []
    early_residual_col = []

    for team in mapped["team"]:
        if team and team in team_schedule:
            n_season = count_games_in_range(team_schedule, team, range(1, 19))
            if n_season == 0:
                n_season = DEFAULT_SEASON_GAMES
        else:
            n_season = DEFAULT_SEASON_GAMES
        n_season_games_col.append(n_season)

    for team, position in zip(mapped["team"], mapped["position"]):
        residual, n_early = _early_schedule_residual(
            team, position, team_schedule, rank_curves, predicted_ranks
        )
        early_residual_col.append(residual)
        n_early_games_col.append(n_early if n_early > 0 else len(list(EARLY_WEEKS)))

    mapped["n_season_games"] = n_season_games_col
    mapped["n_early_games"] = n_early_games_col
    mapped["early_schedule_residual"] = early_residual_col
    mapped["per_game_points"] = mapped["projected_points"] / mapped["n_season_games"]

    mapped["schedule_adjusted_points"] = (
        mapped["n_early_games"]
        * (mapped["per_game_points"] + mapped["early_schedule_residual"])
        * (mapped["n_season_games"] / mapped["n_early_games"])
    )

    # Players with no team match get a pure no-op: adjusted == original.
    unmatched_mask = mapped["team"].isna()
    mapped.loc[unmatched_mask, "schedule_adjusted_points"] = mapped.loc[
        unmatched_mask, "projected_points"
    ]

    return mapped


def main() -> None:
    if not BLENDED_CSV.exists():
        log.error(f"Blended projections CSV not found at {BLENDED_CSV} — nothing to adjust.")
        return

    blended = pd.read_csv(BLENDED_CSV)
    adjusted = compute_schedule_adjusted_points(blended)

    print("\nSample schedule-adjusted rows:")
    cols = [
        "player_name",
        "position",
        "team",
        "projected_points",
        "n_season_games",
        "n_early_games",
        "early_schedule_residual",
        "schedule_adjusted_points",
    ]
    print(adjusted[cols].head(15).to_string(index=False))

    biggest_movers = adjusted.copy()
    biggest_movers["delta"] = (
        biggest_movers["schedule_adjusted_points"] - biggest_movers["projected_points"]
    )
    print("\nBiggest gainers (schedule-adjusted > projected):")
    print(
        biggest_movers.sort_values("delta", ascending=False)
        .head(10)[["player_name", "position", "team", "delta"]]
        .to_string(index=False)
    )
    print("\nBiggest losers (schedule-adjusted < projected):")
    print(
        biggest_movers.sort_values("delta")
        .head(10)[["player_name", "position", "team", "delta"]]
        .to_string(index=False)
    )


if __name__ == "__main__":
    main()
