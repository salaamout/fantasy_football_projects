# Goal 6: Tech Debt — Low-Hanging Fruit

A collection of five targeted, low-effort improvements to make the codebase
easier to read, run, and extend.

---

## Item 1 — Merge `aggregate_par_waiver.py` into `aggregate_par.py`

**Problem:** `aggregate_par_waiver.py` is a near-duplicate of `aggregate_par.py`.
It re-runs the same pipeline with different replacement-rank parameters and saves
output with a `_waiver` suffix.  Any future change to the core logic (e.g. new
chart type, different budget) must be made in both files.

**Fix:** Follow the pattern already used in `lineup_optimizer.py`: add a `--waiver`
/ `-w` CLI flag to `aggregate_par.py` that swaps in the waiver-wire replacement
ranks and file suffixes.  Delete `aggregate_par_waiver.py` once the flag is in
place.

**Why it's easy:** `lineup_optimizer.py` already has a working example of exactly
this pattern — the `argparse` block and the `REPLACEMENT_RANKS_WAIVER` constant
can be copied almost verbatim.

---

## Item 2 — Standardise all data paths with `Path(__file__)`

**Problem:** Several files use bare relative paths that only work when the script
is invoked from the repo root:

- `load_fantasy_data.py`: `DATA_PATH = "data/fantasy_half_ppr.csv"`
- `scrapers/parse_ringer_html.py`: `HTML_PATH = Path("data/2026 Ringer …")`
- `scrapers/parse_pfr_html.py`: similar pattern

`visualize_par.py` correctly anchors paths to `Path(__file__).parent`, but the
other files do not.  Running any of them from inside their own directory (e.g.
`cd analysis && python calculate_par.py`) silently uses the wrong path.

**Fix:** Replace every bare relative `"data/…"` string with an anchored path:

```python
# Before
DATA_PATH = "data/fantasy_half_ppr.csv"

# After
DATA_PATH = Path(__file__).parent.parent / "data" / "fantasy_half_ppr.csv"
```

Apply the same pattern in both `scrapers/` files.

---

## Item 3 — Add a `verbose` parameter to noisy loader/calculator functions

**Problem:** `load_and_clean_data()` always prints a multi-line summary table
(row count, seasons, player counts per position/season) to stdout.  `calculate_par()`
always prints replacement-level baselines.  Every downstream script that calls
these functions floods the terminal with diagnostic output, making it hard to see
the script's own output.

**Fix:** Add `verbose: bool = False` to both function signatures, guard the
`print(…)` calls behind `if verbose:`, and update all existing call sites that
are intended to be diagnostic (e.g. the `if __name__ == "__main__"` blocks) to
pass `verbose=True`.

```python
def load_and_clean_data(path: str = DATA_PATH, verbose: bool = False) -> pd.DataFrame:
    ...
    if verbose:
        print(f"Total rows: {len(df)}")
        ...
```

---

## Item 4 — Add `__init__.py` files and fix bare sibling imports

**Problem:** `calculate_par.py` does `from load_fantasy_data import …` and
`aggregate_par_waiver.py` relies on a `sys.path.insert` hack to make the same
import work.  Neither `analysis/` nor `scrapers/` have `__init__.py`, so they
are not recognised as packages by Python tooling (linters, type-checkers, test
runners).

**Fix:**
1. Add empty `analysis/__init__.py` and `scrapers/__init__.py`.
2. Replace bare sibling imports with relative imports:
   ```python
   # Before (in calculate_par.py)
   from load_fantasy_data import load_and_clean_data
   
   # After
   from .load_fantasy_data import load_and_clean_data
   ```
3. Remove all `sys.path.insert(0, …)` hacks.  Scripts that need to be runnable
   directly can keep a small `if __name__ == "__main__"` guard that does the
   path fix only in that case, or they can be invoked with `python -m analysis.aggregate_par`.

---

## Item 5 — Pin missing / incomplete dependencies in `requirements.txt`

**Problem:**
- `pulp` is listed without a version pin (`pulp` instead of e.g. `pulp==2.9.0`),
  meaning a future `pip install` could pull in a breaking release.
- `matplotlib` is used extensively in `visualize_par.py` but is **not listed in
  `requirements.txt` at all**, so a fresh install would fail at runtime with no
  obvious error message.

**Fix:**
1. Run `pip show pulp matplotlib` to capture current versions.
2. Add pinned entries for both:
   ```
   matplotlib==3.x.x
   pulp==2.x.x
   ```
3. Optionally run `pip freeze > requirements.txt` and audit the full output to
   catch any other silent omissions (e.g. `scipy`, `seaborn` if added later).
