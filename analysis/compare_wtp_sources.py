"""
Goal 10, Step 5: Compare Willingness-to-Pay (WTP) outputs across point sources.

Builds WTP tables for all three point sources (historical, espn, blended),
merges them on roster slot (position + positional_rank), and generates:

  - output/wtp_source_comparison.md   — full comparison table + deltas +
                                         spot-check of top players by position
  - output/wtp_source_comparison.png  — grouped bar chart (top N by blended WTP)

Usage
    python -m analysis.compare_wtp_sources
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).parent
_ROOT = _HERE.parent
OUTPUT_DIR = _ROOT / "output"

try:
    from .willingness_to_pay import build_wtp_table, STARTER_CUTOFFS, SOURCE_LABELS
    from .visualize_par import plot_wtp_source_comparison
except ImportError:
    sys.path.insert(0, str(_HERE))
    from willingness_to_pay import build_wtp_table, STARTER_CUTOFFS, SOURCE_LABELS
    from visualize_par import plot_wtp_source_comparison

SOURCES = ["historical", "espn", "blended"]

# Known top players to spot-check (position -> rank -> label used in prose).
SPOT_CHECK_SLOTS = {
    "QB": [1, 2, 3, 4, 5],
    "RB": [1, 2, 3, 4, 5],
    "WR": [1, 2, 3, 4, 5],
    "TE": [1, 2, 3],
}


def build_merged_comparison() -> pd.DataFrame:
    """
    Build WTP tables for each source and merge into a single DataFrame keyed
    on (position, positional_rank), with a wtp_<source> column per source.
    """
    merged: pd.DataFrame | None = None
    for source in SOURCES:
        table = build_wtp_table(point_source=source, verbose=False)
        starters = table[
            table["positional_rank"] <= table["position"].map(STARTER_CUTOFFS)
        ].copy()
        cols = starters[["position", "positional_rank", "roster_slot", "wtp_price"]].rename(
            columns={"wtp_price": f"wtp_{source}"}
        )
        if merged is None:
            merged = cols
        else:
            merged = merged.merge(
                cols.drop(columns=["roster_slot"]),
                on=["position", "positional_rank"],
                how="outer",
            )
    assert merged is not None
    merged = merged.sort_values(["position", "positional_rank"]).reset_index(drop=True)
    merged["roster_slot"] = merged["position"] + merged["positional_rank"].astype(str)

    merged["delta_blended_vs_espn"] = (merged["wtp_blended"] - merged["wtp_espn"]).round(2)
    merged["delta_blended_vs_historical"] = (
        merged["wtp_blended"] - merged["wtp_historical"]
    ).round(2)
    return merged


def _md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    widths = [
        max(len(headers[i]), max((len(r[i]) for r in rows), default=0))
        for i in range(len(headers))
    ]
    def fmt_row(cells: list[str]) -> str:
        return "| " + " | ".join(c.rjust(widths[i]) for i, c in enumerate(cells)) + " |"
    lines = [fmt_row(headers), "| " + " | ".join("-" * w for w in widths) + " |"]
    lines.extend(fmt_row(r) for r in rows)
    return lines


def write_comparison_doc(merged: pd.DataFrame) -> Path:
    lines: list[str] = []
    lines.append("# WTP Source Comparison: Historical vs. ESPN vs. Blended")
    lines.append("")
    lines.append(
        "Compares willingness-to-pay (WTP) shadow prices across the three "
        "expected-points sources, matched by roster slot (position + "
        "positional rank). Positive deltas mean the blended source values "
        "that slot **more** than the comparison source."
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Full comparison table (by position) ---
    for pos in ["QB", "RB", "WR", "TE"]:
        pos_df = merged[merged["position"] == pos].sort_values("positional_rank")
        if pos_df.empty:
            continue
        lines.append(f"## {pos}")
        lines.append("")
        rows = []
        for _, r in pos_df.iterrows():
            rows.append([
                r["roster_slot"],
                f"{r['wtp_historical']:.1f}" if pd.notna(r["wtp_historical"]) else "—",
                f"{r['wtp_espn']:.1f}" if pd.notna(r["wtp_espn"]) else "—",
                f"{r['wtp_blended']:.1f}" if pd.notna(r["wtp_blended"]) else "—",
                f"{r['delta_blended_vs_espn']:+.1f}" if pd.notna(r["delta_blended_vs_espn"]) else "—",
                f"{r['delta_blended_vs_historical']:+.1f}" if pd.notna(r["delta_blended_vs_historical"]) else "—",
            ])
        lines.extend(_md_table(
            ["Slot", "Hist WTP ($)", "ESPN WTP ($)", "Blended WTP ($)",
             "Δ Blend-ESPN", "Δ Blend-Hist"],
            rows,
        ))
        lines.append("")

    # --- Spot-check section ---
    lines.append("---")
    lines.append("")
    lines.append("## Spot-Check: Known Top Players")
    lines.append("")
    lines.append(
        "Sanity-checking top QBs/RBs/WRs/TEs — blended WTP should sit "
        "roughly between (or near) the historical and ESPN values, not wildly "
        "outside either."
    )
    lines.append("")
    rows = []
    for pos, ranks in SPOT_CHECK_SLOTS.items():
        for rank in ranks:
            match = merged[(merged["position"] == pos) & (merged["positional_rank"] == rank)]
            if match.empty:
                continue
            r = match.iloc[0]
            rows.append([
                r["roster_slot"],
                f"{r['wtp_historical']:.1f}" if pd.notna(r["wtp_historical"]) else "—",
                f"{r['wtp_espn']:.1f}" if pd.notna(r["wtp_espn"]) else "—",
                f"{r['wtp_blended']:.1f}" if pd.notna(r["wtp_blended"]) else "—",
            ])
    lines.extend(_md_table(["Slot", "Hist WTP ($)", "ESPN WTP ($)", "Blended WTP ($)"], rows))
    lines.append("")

    # --- Largest deltas (where blending changes the recommendation the most) ---
    lines.append("---")
    lines.append("")
    lines.append("## Largest Deltas (Blended vs. ESPN)")
    lines.append("")
    biggest = merged.reindex(
        merged["delta_blended_vs_espn"].abs().sort_values(ascending=False).index
    ).head(15)
    rows = []
    for _, r in biggest.iterrows():
        rows.append([
            r["roster_slot"],
            f"{r['wtp_espn']:.1f}" if pd.notna(r["wtp_espn"]) else "—",
            f"{r['wtp_blended']:.1f}" if pd.notna(r["wtp_blended"]) else "—",
            f"{r['delta_blended_vs_espn']:+.1f}",
        ])
    lines.extend(_md_table(["Slot", "ESPN WTP ($)", "Blended WTP ($)", "Δ"], rows))
    lines.append("")

    out_path = OUTPUT_DIR / "wtp_source_comparison.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def run_comparison() -> pd.DataFrame:
    print("=== WTP Source Comparison (historical vs. espn vs. blended) ===")
    print("\nBuilding WTP tables for each source…")
    merged = build_merged_comparison()

    print("Writing comparison table…")
    doc_path = write_comparison_doc(merged)
    print(f"Saved: {doc_path}")

    print("Generating comparison chart…")
    plot_wtp_source_comparison(merged)

    return merged


if __name__ == "__main__":
    run_comparison()
