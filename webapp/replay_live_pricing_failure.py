"""
Replay a `recompute_shadow_prices` failure snapshot for offline diagnosis.

When `webapp.live_pricing.recompute_shadow_prices` fails, it writes a JSON
snapshot of the exact LP inputs (overrides + available-player pool + pick
history) to `output/live_pricing_debug/`. This script reloads one of those
snapshots and re-runs `solve_lp_relaxation` against it directly, with full
tracebacks and warnings surfaced (unlike the live app, which swallows them
into a UI warning string).

Usage:
    python -m webapp.replay_live_pricing_failure [path/to/snapshot.json]

If no path is given, the most recently modified snapshot in
`output/live_pricing_debug/` is used.
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from analysis.willingness_to_pay import compute_wtp, solve_lp_relaxation
from webapp.live_pricing import DEBUG_DUMP_DIR


def _find_snapshot(arg: str | None) -> Path:
    if arg:
        path = Path(arg)
        if not path.is_absolute():
            path = _ROOT / path
        if not path.exists():
            raise SystemExit(f"Snapshot not found: {path}")
        return path

    if not DEBUG_DUMP_DIR.exists():
        raise SystemExit(f"No debug dumps found — {DEBUG_DUMP_DIR} does not exist yet.")
    candidates = sorted(DEBUG_DUMP_DIR.glob("recompute_failure_*.json"))
    if not candidates:
        raise SystemExit(f"No debug dumps found in {DEBUG_DUMP_DIR}.")
    return candidates[-1]


def main(argv: list[str]) -> None:
    snapshot_path = _find_snapshot(argv[0] if argv else None)
    print(f"Replaying snapshot: {snapshot_path}")

    payload = json.loads(snapshot_path.read_text())
    print(f"Original error ({payload.get('error_type')}): {payload.get('error')}")
    print(f"Recorded at: {payload.get('timestamp')}")
    print(f"Overrides: {json.dumps(payload.get('overrides'), indent=2)}")
    print(f"Pool size: {payload.get('pool_len')}  by position: {payload.get('pool_position_counts')}")
    print(f"Drafted players at time of failure: {len(payload.get('drafted_players', []))}")
    for pick in payload.get("pick_history", []):
        print(f"  - {pick['player_id']}: ${pick['price_paid']} ({pick['drafted_by']})")

    pool = pd.DataFrame(payload["pool"])
    overrides = payload["overrides"]

    print("\nRe-running solve_lp_relaxation with full warnings visible...\n")
    with warnings.catch_warnings():
        warnings.simplefilter("always")
        try:
            pool_with_duals, lambda_eq, slot_duals = solve_lp_relaxation(pool, overrides=overrides)
        except (ValueError, RuntimeError) as e:
            print(f"Reproduced failure: {e}")
            return

    priced = compute_wtp(pool_with_duals, lambda_eq, slot_duals)
    print(f"Solve succeeded this time. lambda_eq={lambda_eq:.4f}  slot_duals={slot_duals}")
    print(priced.sort_values("wtp_price", ascending=False).head(10).to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1:])
