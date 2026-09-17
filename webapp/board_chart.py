"""
Goal 14 Step 3: Plotly WTP scatter chart for the live draft board.

Kept separate from `app.py` so it can be unit-tested / reused without a
Streamlit runtime. Mirrors the styling of
`analysis.visualize_par.plot_wtp_vs_espn_interactive`, plotting ESPN's
estimated real auction price (x) vs. our computed willingness-to-pay shadow
price (y) for *available* players only — drafted players are expected to
already be filtered out of the input DataFrame by the caller (that's how
"remove chosen players from the board" is implemented).
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

POSITION_COLORS = {
    "QB": "#0072B2",
    "RB": "#E69F00",
    "WR": "#009E73",
    "TE": "#D55E00",
    "DST": "#CC79A7",
}
POSITION_ORDER = ["QB", "RB", "WR", "TE", "DST"]


def build_board_chart(
    available_df: pd.DataFrame,
    highlight_name: str | None = None,
    height: int = 650,
) -> go.Figure:
    """Build an ESPN-price-vs-WTP-price scatter for available players.

    Parameters
    ----------
    available_df:
        DataFrame of *available* (not-yet-drafted) players. Must contain
        columns: player_name, position, wtp_price, espn_av,
        positional_rank, roster_slot.
    highlight_name:
        Optional (case-insensitive, substring) player name to highlight on
        the chart — matching points are drawn larger/outlined in red so
        they stand out from the rest of the board.
    height:
        Chart height in pixels (default 650). Pass a smaller value to fit
        the chart alongside shorter neighboring panels.
    """
    fig = go.Figure()

    if available_df.empty:
        fig.update_layout(
            title="No players available",
            template="plotly_white",
        )
        return fig

    for pos in POSITION_ORDER:
        sub = available_df[available_df["position"] == pos]
        if sub.empty:
            continue
        fig.add_trace(
            go.Scatter(
                x=sub["espn_av"],
                y=sub["wtp_price"],
                mode="markers",
                name=pos,
                marker=dict(
                    color=POSITION_COLORS.get(pos, "gray"),
                    size=11,
                    line=dict(color="white", width=0.5),
                ),
                customdata=sub[
                    ["player_name", "roster_slot", "positional_rank"]
                ],
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    "Slot: %{customdata[1]} (#%{customdata[2]})<br>"
                    "ESPN price: $%{x:.1f}<br>"
                    "WTP: $%{y:.1f}"
                    "<extra></extra>"
                ),
            )
        )

    # y=x reference line: players plotting above this line have a WTP
    # (our computed fair value) higher than ESPN's estimated auction price
    # — i.e. players ESPN's crowd-sourced pricing is undervaluing.
    axis_max = float(
        max(available_df["espn_av"].max(), available_df["wtp_price"].max())
    ) * 1.05
    axis_max = max(axis_max, 1.0)
    fig.add_trace(
        go.Scatter(
            x=[0, axis_max],
            y=[0, axis_max],
            mode="lines",
            name="y = x (fair value)",
            line=dict(color="gray", width=1.5, dash="dash"),
            hoverinfo="skip",
            showlegend=True,
        )
    )

    # Highlight a searched-for player: draw a big red-outlined marker on
    # top of any matching rows (case-insensitive substring match).
    if highlight_name:
        needle = highlight_name.strip().lower()
        if needle:
            matches = available_df[
                available_df["player_name"].str.lower().str.contains(needle, na=False)
            ]
            if not matches.empty:
                # Big, opaque, high-contrast marker + bold label so a
                # single point among hundreds is impossible to miss, plus
                # an arrow annotation in case it's lost near other points.
                fig.add_trace(
                    go.Scatter(
                        x=matches["espn_av"],
                        y=matches["wtp_price"],
                        mode="markers+text",
                        name="Highlighted",
                        text=matches["player_name"],
                        textposition="top center",
                        textfont=dict(size=14, color="black", family="Arial Black"),
                        marker=dict(
                            color="yellow",
                            size=26,
                            symbol="star",
                            line=dict(color="red", width=3),
                        ),
                        customdata=matches[["player_name", "espn_av", "wtp_price"]],
                        hovertemplate=(
                            "<b>%{customdata[0]}</b><br>"
                            "ESPN price: $%{customdata[1]:.1f}<br>"
                            "WTP: $%{customdata[2]:.1f}"
                            "<extra></extra>"
                        ),
                        showlegend=False,
                    )
                )
                for _, row in matches.iterrows():
                    fig.add_annotation(
                        x=row["espn_av"],
                        y=row["wtp_price"],
                        text=(
                            f"⭐ {row['player_name']}<br>"
                            f"ESPN: ${row['espn_av']:.1f}  |  WTP: ${row['wtp_price']:.1f}"
                        ),
                        showarrow=True,
                        arrowhead=2,
                        arrowcolor="red",
                        ax=40,
                        ay=-40,
                        font=dict(color="red", size=13, family="Arial Black"),
                        bgcolor="white",
                        bordercolor="red",
                    )

    fig.update_xaxes(title_text="ESPN Estimated Price ($)")
    fig.update_yaxes(title_text="Willingness to Pay ($)")
    fig.update_layout(
        title="Live Draft Board — Available Players",
        template="plotly_white",
        legend_title_text="Position",
        dragmode="zoom",
        height=height,
        margin=dict(t=60),
    )
    return fig
