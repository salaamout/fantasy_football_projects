"""
Goal 14 Step 1: In-memory / persisted draft-state data model for the live
draft web app.

This module is intentionally UI-agnostic (no Streamlit imports) so it can be
unit-tested and reused by whatever front-end is built in later steps.

Data model
----------
``Player``
    One row of the pre-draft WTP board, plus live-draft fields:
    ``drafted`` (bool), ``price_paid`` (float | None), ``drafted_by``
    (str | None, e.g. "me" or a rival team's label).

``DraftState``
    The full board (list of ``Player``) plus metadata: which WTP source CSV
    it was seeded from, my remaining budget/roster slots, and a pick
    history (for "undo last pick").

Seeding
-------
``DraftState.from_wtp_csv()`` seeds the board from one of the existing WTP
output CSVs (default: ``data/willingness_to_pay_blended_sched.csv``), which
already contains one row per player with position, price, and
starter/flex WTP prices.

Persistence
-----------
``DraftState.save()`` / ``DraftState.load()`` round-trip the state to a flat
JSON file (default: ``data/draft_state.json``) so the app can be restarted
mid-draft without losing progress. Every mutating method
(``draft_player``, ``undo_last_pick``) automatically calls ``save()`` when a
``state_path`` is set on the instance, so callers don't have to remember to
persist explicitly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import pandas as pd

_HERE = Path(__file__).parent
_ROOT = _HERE.parent
DATA_DIR = _ROOT / "data"

DEFAULT_WTP_CSV = DATA_DIR / "willingness_to_pay_blended_sched.csv"
DEFAULT_STATE_PATH = DATA_DIR / "draft_state.json"

# My league settings (mirrors analysis/willingness_to_pay.py / lineup_optimizer.py)
DEFAULT_BUDGET = 200
DEFAULT_ROSTER_SLOTS = {
    "QB": 1, "RB": 2, "WR": 3, "TE": 1, "FLEX": 1, "BENCH": 4, "DST": 1,
}


@dataclass
class Player:
    """One player on the draft board."""

    position: str
    positional_rank: int
    player_name: str
    expected_points: float = 0.0
    par_points: float = 0.0
    wtp_price: float = 0.0        # current shadow price (recomputed live in Step 4)
    original_wtp_price: float = 0.0  # pre-draft baseline, never mutated
    method1_av: float = 0.0       # Ringer/auction-value reference price
    espn_av: float = 0.0          # ESPN's estimated real auction price (actual $ cost)
    roster_slot: str = ""

    # --- live draft fields ---
    drafted: bool = False
    price_paid: Optional[float] = None
    drafted_by: Optional[str] = None   # "me" or a label for the rival team

    @property
    def player_id(self) -> str:
        """Stable identifier: name is unique enough for this single-draft tool."""
        return self.player_name

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Player":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class DraftPick:
    """One entry in the pick history, used to support 'undo last pick'."""

    player_id: str
    price_paid: float
    drafted_by: str


@dataclass
class DraftState:
    """Full draft-state model: the board plus my budget/roster and history."""

    players: list[Player] = field(default_factory=list)
    source_csv: str = ""
    my_budget_total: int = DEFAULT_BUDGET
    my_budget_spent: float = 0.0
    my_roster_slots: dict = field(default_factory=lambda: dict(DEFAULT_ROSTER_SLOTS))
    my_label: str = "me"
    pick_history: list[DraftPick] = field(default_factory=list)
    state_path: Optional[Path] = None  # if set, mutating methods auto-save

    # ------------------------------------------------------------------
    # Seeding
    # ------------------------------------------------------------------
    @classmethod
    def from_wtp_csv(
        cls,
        csv_path: Path | str = DEFAULT_WTP_CSV,
        my_budget_total: int = DEFAULT_BUDGET,
    ) -> "DraftState":
        """Seed a fresh DraftState from a WTP output CSV."""
        csv_path = Path(csv_path)
        df = pd.read_csv(csv_path)

        players = []
        for _, row in df.iterrows():
            players.append(
                Player(
                    position=row.get("position", ""),
                    positional_rank=int(row.get("positional_rank", 0)),
                    player_name=row.get("player_name", ""),
                    expected_points=float(row.get("expected_points", 0.0) or 0.0),
                    par_points=float(row.get("par_points", 0.0) or 0.0),
                    wtp_price=float(row.get("wtp_price", row.get("price", 0.0)) or 0.0),
                    original_wtp_price=float(
                        row.get("wtp_price", row.get("price", 0.0)) or 0.0
                    ),
                    method1_av=float(row.get("method1_av", 0.0) or 0.0),
                    espn_av=float(row.get("espn_av", 0.0) or 0.0),
                    roster_slot=row.get("roster_slot", "") or "",
                )
            )

        return cls(
            players=players,
            source_csv=str(csv_path),
            my_budget_total=my_budget_total,
        )

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------
    @property
    def my_budget_remaining(self) -> float:
        return self.my_budget_total - self.my_budget_spent

    @property
    def available_players(self) -> list[Player]:
        return [p for p in self.players if not p.drafted]

    @property
    def drafted_players(self) -> list[Player]:
        return [p for p in self.players if p.drafted]

    @property
    def my_players(self) -> list[Player]:
        return [p for p in self.players if p.drafted and p.drafted_by == self.my_label]

    def find_player(self, player_id: str) -> Optional[Player]:
        for p in self.players:
            if p.player_id == player_id:
                return p
        return None

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------
    def draft_player(
        self,
        player_id: str,
        price_paid: float,
        drafted_by: str = "other",
    ) -> Player:
        """Mark a player as drafted at the given actual price."""
        player = self.find_player(player_id)
        if player is None:
            raise KeyError(f"Unknown player: {player_id!r}")
        if player.drafted:
            raise ValueError(f"{player_id!r} is already drafted")

        player.drafted = True
        player.price_paid = price_paid
        player.drafted_by = drafted_by

        if drafted_by == self.my_label:
            self.my_budget_spent += price_paid

        self.pick_history.append(
            DraftPick(player_id=player_id, price_paid=price_paid, drafted_by=drafted_by)
        )

        self._maybe_save()
        return player

    def undo_last_pick(self) -> Optional[Player]:
        """Revert the most recent pick, restoring the player to available."""
        if not self.pick_history:
            return None

        pick = self.pick_history.pop()
        player = self.find_player(pick.player_id)
        if player is not None:
            if player.drafted_by == self.my_label:
                self.my_budget_spent -= player.price_paid or 0.0
            player.drafted = False
            player.price_paid = None
            player.drafted_by = None

        self._maybe_save()
        return player

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _maybe_save(self) -> None:
        if self.state_path is not None:
            self.save(self.state_path)

    def to_dict(self) -> dict:
        return {
            "players": [p.to_dict() for p in self.players],
            "source_csv": self.source_csv,
            "my_budget_total": self.my_budget_total,
            "my_budget_spent": self.my_budget_spent,
            "my_roster_slots": self.my_roster_slots,
            "my_label": self.my_label,
            "pick_history": [asdict(p) for p in self.pick_history],
        }

    def save(self, path: Path | str = DEFAULT_STATE_PATH) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: Path | str = DEFAULT_STATE_PATH) -> "DraftState":
        path = Path(path)
        with open(path) as f:
            d = json.load(f)

        state = cls(
            players=[Player.from_dict(p) for p in d.get("players", [])],
            source_csv=d.get("source_csv", ""),
            my_budget_total=d.get("my_budget_total", DEFAULT_BUDGET),
            my_budget_spent=d.get("my_budget_spent", 0.0),
            my_roster_slots=d.get("my_roster_slots", dict(DEFAULT_ROSTER_SLOTS)),
            my_label=d.get("my_label", "me"),
            pick_history=[DraftPick(**p) for p in d.get("pick_history", [])],
        )
        state.state_path = path
        return state

    @classmethod
    def load_or_seed(
        cls,
        state_path: Path | str = DEFAULT_STATE_PATH,
        csv_path: Path | str = DEFAULT_WTP_CSV,
        my_budget_total: int = DEFAULT_BUDGET,
    ) -> "DraftState":
        """Resume from a saved state file if present, else seed fresh from CSV."""
        state_path = Path(state_path)
        if state_path.exists():
            return cls.load(state_path)

        state = cls.from_wtp_csv(csv_path=csv_path, my_budget_total=my_budget_total)
        state.state_path = state_path
        state.save(state_path)
        return state

    def reset(
        self,
        csv_path: Path | str = DEFAULT_WTP_CSV,
        my_budget_total: int = DEFAULT_BUDGET,
    ) -> None:
        """Clear the board: re-seed all players/budget/history from the WTP
        CSV, in place, so any existing references (e.g. a cached
        ``st.cache_resource`` instance) see the reset immediately.

        Persists to ``self.state_path`` afterward if one is set, mirroring
        the auto-save behavior of the other mutating methods.
        """
        fresh = self.from_wtp_csv(csv_path=csv_path, my_budget_total=my_budget_total)
        self.players = fresh.players
        self.source_csv = fresh.source_csv
        self.my_budget_total = fresh.my_budget_total
        self.my_budget_spent = fresh.my_budget_spent
        self.my_roster_slots = fresh.my_roster_slots
        self.my_label = fresh.my_label
        self.pick_history = fresh.pick_history
        if self.state_path is not None:
            self.save(self.state_path)
