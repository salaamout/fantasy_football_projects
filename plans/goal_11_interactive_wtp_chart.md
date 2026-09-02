# Goal 11: Interactive "WTP vs. ESPN Estimates" Chart

## Motivation

`output/wtp_vs_espn_estimates_blended.png` (produced by `plot_wtp_vs_espn()` in
`analysis/visualize_par.py`) is a static Matplotlib scatter plot. It's hard to:

1. **Zoom in** on the dense cluster of low-value roster slots (most points sit
   near the origin, so it's hard to distinguish them at the default zoom
   level).
2. **Identify individual players** — points are currently labeled with
   `roster_slot` (e.g. `RB14`) in tiny 7pt text directly on the chart, which
   gets cluttered/overlapping and doesn't show the actual player name.

We want an interactive version of this specific chart that supports mouse-driven
zoom/pan and hover tooltips with player names (and ideally other stats like
position, WTP price, ESPN AV).

## Proposed Approach

Use **Plotly** to generate an interactive HTML chart alongside (not
replacing) the existing static PNG. Plotly is well-suited here because:

- It renders a standalone, self-contained `.html` file that opens in any
  browser — no server or notebook required.
- Built-in zoom/pan/box-select/reset-zoom controls out of the box.
- Native hover tooltips (`hover_data` / `customdata`) — easy to show player
  name, position, roster slot, WTP price, and ESPN AV on mouseover.
- Output can still be saved as a static PNG/SVG image too (via `kaleido`) if
  we want a drop-in replacement for the existing static chart, but the plan
  below treats the HTML as an additional artifact so the existing PNG-based
  workflow/report links are not broken.

### Alternative considered
- **mplcursors / matplotlib interactive backend**: keeps everything in
  Matplotlib and adds hover annotations, but doesn't work for a saved PNG —
  it only works in a live interactive matplotlib window (e.g. via `%matplotlib
  widget` in a notebook or a GUI backend), which doesn't fit this project's
  headless "Agg" backend / script-based workflow. Rejected in favor of
  Plotly, which produces a shareable, standalone file.
- **Bokeh**: similar capabilities to Plotly, but the codebase has no existing
  dependency on it and Plotly has a slightly simpler one-call API
  (`plotly.express.scatter`) for this use case.

## Implementation Steps

1. **Add dependency**: add `plotly` (and `kaleido` only if we also want
   static image export from Plotly) to `requirements.txt`. Install into the
   project venv.

2. **New function** `plot_wtp_vs_espn_interactive()` in
   `analysis/visualize_par.py`, mirroring the existing `plot_wtp_vs_espn()`
   signature (`wtp_df`, `suffix`, `source_label`):
   - Reuse the same filtering logic (starter cutoffs, `espn_av > 0`).
   - Build a Plotly scatter (`plotly.express.scatter` or `go.Figure` with one
     `go.Scatter` trace per position, to preserve the existing color-by-position
     legend behavior and per-position toggle-in-legend feature that Plotly
     gives for free).
   - Set `hover_data`/`customdata` + `hovertemplate` to show just the
     `player_name` on hover (no other fields).
   - Add the same `y = x` reference line as a shape/trace, and keep the
     red/green shaded "overpriced"/"underpriced" regions (via
     `add_shape`/`add_trace` with fill).
   - Set axis titles/chart title to match the static version.
   - Enable default zoom/pan (Plotly does this automatically) and consider
     `fig.update_layout(dragmode="zoom")` plus a "reset axes" button
     (`modebar` is included by default).
   - Save output to `output/wtp_vs_espn_estimates{suffix}.html` via
     `fig.write_html(out_path, include_plotlyjs="cdn")` to keep file size
     small.

3. **Wire into the pipeline**: in `analysis/willingness_to_pay.py`
   `run_wtp()`, call the new function right after the existing
   `plot_wtp_vs_espn(...)` call, so both the static PNG and interactive HTML
   are generated together for every point source (`historical`, `espn`,
   `blended`).

4. **Output location**: save alongside existing outputs in `output/`, e.g.
   `output/wtp_vs_espn_estimates_blended.html`. No changes to existing PNG
   filenames/paths, so nothing else that depends on the PNGs breaks.

5. **Docs**: add a short note to `README.md` (or wherever chart outputs are
   documented) pointing out the new interactive HTML file and how to open it
   (double-click / open in browser).

## Open Questions for You

1. **Scope**: Do you want this treatment *only* for the blended
   `wtp_vs_espn_estimates` chart, or should the same interactive pattern be
   applied to the other WTP charts (`wtp_top20_*`, `wtp_source_comparison`,
   etc.) as a follow-up? This plan currently scopes to just
   `plot_wtp_vs_espn` for all three sources (historical/espn/blended) since
   they share one function.
2. **Keep or drop the static PNG?** Plan assumes we keep generating both the
   PNG (unchanged) and a new HTML file side-by-side. Let me know if you'd
   rather replace the PNG entirely.
3. **Tooltip fields**: hover will show just the player name (`player_name`)
   — no other fields.
4. **Dependency footprint**: `plotly` is approved for use (no `kaleido`
   needed since we're keeping the static PNG generated by Matplotlib as-is).

## Estimated Effort

Small — one new function (~60-80 lines) in `visualize_par.py`, one new call
site in `willingness_to_pay.py`, one dependency addition, and a README note.
No changes needed to upstream data-building code (`build_wtp_table`, etc.).
