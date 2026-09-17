"""
Goal 14 Step 2: Streamlit entry point for the live draft web app.

This is a scaffold: it loads (or seeds) the `DraftState` model from Step 1,
confirms the app runs and hot-reloads via `streamlit run`, and renders a
minimal player board. Later steps will replace the placeholder sections
with the real "remove drafted players" UI (Step 3), live shadow-price
recompute (Step 4), and the optimal-team panel (Step 5).

Run locally with:

    streamlit run webapp/app.py

Streamlit reruns this whole script top-to-bottom on every interaction, so we
keep the mutable `DraftState` in `st.session_state` rather than re-seeding
it from disk each rerun (we still persist to disk via `DraftState.save()`
so the draft survives an app restart).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# Allow `python -m` / `streamlit run` from any cwd to find the package.
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from webapp.draft_state import DraftState, DEFAULT_STATE_PATH, DEFAULT_WTP_CSV
from webapp.board_chart import build_board_chart
from webapp.live_optimizer import compute_optimal_team, find_max_profitable_bid

st.set_page_config(
    page_title="Live Draft Board",
    layout="wide",
)


@st.cache_resource(show_spinner=False)
def _get_draft_state() -> DraftState:
    """Load/seed the DraftState exactly once per server process.

    Cached with `cache_resource` (not stored purely in `session_state`) so
    that the same in-memory state is shared across a solo user's tabs, and
    is not accidentally reset if `session_state` gets cleared.
    """
    return DraftState.load_or_seed(
        state_path=DEFAULT_STATE_PATH,
        csv_path=DEFAULT_WTP_CSV,
    )


def _players_dataframe(state: DraftState) -> pd.DataFrame:
    rows = [p.to_dict() for p in state.players]
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    cols = [
        "player_name", "position", "positional_rank", "roster_slot",
        "expected_points", "wtp_price", "original_wtp_price", "espn_av",
        "drafted", "drafted_by", "price_paid",
    ]
    cols = [c for c in cols if c in df.columns]
    return df[cols].sort_values("wtp_price", ascending=False)


def _position_filter(df: pd.DataFrame, key: str) -> pd.DataFrame:
    """Render a position multiselect and return the filtered DataFrame."""
    if df.empty or "position" not in df.columns:
        return df
    positions = sorted(df["position"].dropna().unique().tolist())
    selected = st.multiselect(
        "Filter by position", options=positions, default=positions, key=key,
    )
    if not selected:
        return df
    return df[df["position"].isin(selected)]


def _draft_player_form(state: DraftState) -> None:
    """'Mark as drafted' action: search/select a player, enter price, submit."""
    available = state.available_players
    if not available:
        st.info("All players have been drafted.")
        return

    available_sorted = sorted(available, key=lambda p: -p.wtp_price)
    options = [p.player_id for p in available_sorted]
    labels = {
        p.player_id: f"{p.player_name} ({p.position}) — ${p.wtp_price:.0f}"
        for p in available_sorted
    }

    form_col, comparables_col = st.columns([3, 2])

    with form_col:
        # Kept outside the form: forms only rerun/propagate widget changes on
        # submit, so a selectbox inside a form wouldn't update the
        # comparables table live as the user browses different players.
        player_id = st.selectbox(
            "Player",
            options=options,
            format_func=lambda pid: labels[pid],
            key="draft_player_select",
        )
        with st.form("draft_player_form", clear_on_submit=True):
            default_price = state.find_player(player_id).wtp_price if player_id else 0.0
            price_paid = st.number_input(
                "Price paid ($)", min_value=0, step=1,
                value=int(round(default_price)), key="draft_price_input",
            )
            drafted_by = st.radio(
                "Drafted by", options=[state.my_label, "other"],
                index=1, key="draft_by_select", horizontal=True,
            )
            submitted = st.form_submit_button("Mark as drafted", use_container_width=True)

            if submitted and player_id:
                try:
                    state.draft_player(player_id, price_paid, drafted_by=drafted_by)
                    st.toast(f"Drafted {player_id} for ${price_paid:.0f}")
                    # Note: shadow prices are intentionally NOT recomputed here.
                    # Recomputing live made "steal" prices hard to spot, since
                    # every remaining player's estimate drifted downward relative
                    # to ESPN's price as the league-wide budget/slots shrank.
                    # Force an immediate rerun so the dropdown/board reflect the
                    # updated `available_players` list right away (the form's
                    # own submit-rerun can otherwise render before dependent
                    # widgets like `draft_player_select` pick up the mutation).
                    st.rerun()
                except (KeyError, ValueError) as e:
                    st.error(str(e))

    with comparables_col:
        if player_id:
            _comparable_players_table(state, player_id)
            _max_profitable_bid_section(state, player_id)


def _comparable_players_table(state: DraftState, player_id: str) -> None:
    """Show the top 10 available players at the selected player's position
    by wtp_price, with the selected player highlighted (⭐) if present."""
    selected = state.find_player(player_id)
    if selected is None:
        return

    same_position = [
        p for p in state.available_players if p.position == selected.position
    ]
    top10 = sorted(same_position, key=lambda p: -p.wtp_price)[:10]
    if not top10:
        return

    rows = []
    for p in top10:
        rows.append({
            "": "⭐" if p.player_id == player_id else "",
            "player_name": p.player_name,
            "wtp_price": round(p.wtp_price, 0),
            "espn_av": round(p.espn_av, 0),
        })
    st.caption(f"Top available {selected.position}s")
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _max_profitable_bid_section(state: DraftState, player_id: str) -> None:
    """Button-triggered (not auto-recomputed every rerun, since each price
    checked is a full MILP solve): shows the highest price I could pay for
    the selected player before my optimal lineup's projected score would
    actually drop below today's optimal (without them)."""
    player = state.find_player(player_id)
    if player is None or player.drafted:
        return

    use_adjusted_cost = st.session_state.get("use_adjusted_cost", False)

    if st.button("Find max profitable bid", key="max_bid_button"):
        with st.spinner("Solving…"):
            try:
                result = find_max_profitable_bid(
                    state, player_id, use_adjusted_cost=use_adjusted_cost,
                )
                st.session_state["max_bid_result"] = (player_id, result)
            except (ValueError, RuntimeError) as e:
                st.session_state["max_bid_result"] = None
                st.error(str(e))

    cached = st.session_state.get("max_bid_result")
    if cached and cached[0] == player_id:
        result = cached[1]
        st.metric(
            f"Max profitable bid for {player.player_name}",
            f"${result['max_bid']:.0f}",
            help=(
                f"Above this, my optimal starting lineup's projected "
                f"points/week would drop below today's baseline of "
                f"{result['baseline_ppg']:.1f} (without this player)."
            ),
        )


def _undo_button(state: DraftState) -> None:
    if not state.pick_history:
        st.button("Undo last pick", disabled=True)
        return

    last = state.pick_history[-1]
    if st.button(f"Undo last pick ({last.player_id})"):
        undone = state.undo_last_pick()
        if undone is not None:
            st.toast(f"Undid pick: {undone.player_name}")
            st.rerun()


def _reset_button(state: DraftState) -> None:
    """Clear the board: re-seed all players/budget/history from the WTP CSV."""
    with st.popover("Reset draft"):
        st.write("This clears **all** picks and restores every player's original WTP price. This cannot be undone.")
        if st.button("Yes, reset the draft board", key="confirm_reset_button", type="primary"):
            state.reset(csv_path=DEFAULT_WTP_CSV)
            st.toast("Draft board reset.")
            st.rerun()


def _optimal_team_panel(state: DraftState) -> None:
    """Step 5: live optimal-team panel, with a 'what-if I win this player'
    preview that never mutates `state`."""
    st.subheader("Optimal team (live)")

    use_adjusted_cost = st.checkbox(
        "Market-adjusted (overpay/underpay redistribution)",
        value=st.session_state.get("use_adjusted_cost", False),
        key="use_adjusted_cost",
        help=(
            "Classic (unchecked): cost basis = ESPN AV, unaffected by "
            "what's actually been paid so far. Market-adjusted (checked): "
            "overpays/underpays vs. ESPN AV are redistributed across the "
            "remaining player pool (cheap players get cheaper after an "
            "overpay; priciest players get pricier after an underpay), so "
            "this panel reflects the real remaining market rather than the "
            "static pre-draft estimate."
        ),
    )

    try:
        result = compute_optimal_team(
            state, hypothetical_player_id=None, use_adjusted_cost=use_adjusted_cost,
        )
    except (ValueError, RuntimeError) as e:
        st.warning(f"Could not compute optimal team: {e}")
        return

    m1, m2 = st.columns(2)
    m1.metric("Projected starter points/week", f"{result['total_ppg']:.1f}")
    m2.metric("Budget remaining after", f"${result['budget_remaining_after']:.0f}")

    lineup = result["lineup"].copy()
    lineup["points/week"] = (lineup["expected_points"] / 17).round(1)
    lineup["cost"] = lineup["cost"].round(0)
    display_cols = ["slot", "position", "player_name", "points/week", "cost"]
    # Size the table to fit all rows without an internal scrollbar. Streamlit's
    # dataframe adds ~35px per row plus ~38px for the header; pad a little
    # extra to avoid clipping the last row/border.
    table_height = 38 + 35 * len(lineup) + 3
    st.dataframe(
        lineup[display_cols],
        use_container_width=True, hide_index=True,
        height=table_height,
    )


def main() -> None:
    st.title("🏈 Live Draft Board")
    st.caption(
        "Mark players as drafted to remove them from the board below. "
        "Shadow prices recompute live as picks happen, and the optimal-team "
        "panel below refreshes after every pick."
    )

    state = _get_draft_state()

    col1, col2, col3 = st.columns(3)
    col1.metric("My budget remaining", f"${state.my_budget_remaining:.0f}")
    col2.metric("Players available", len(state.available_players))
    col3.metric("Players drafted", len(state.drafted_players))

    st.divider()

    st.subheader("Draft a player")
    _draft_player_form(state)
    _undo_button(state)

    st.divider()

    optimal_col, wtp_col = st.columns([3, 2])

    with optimal_col:
        _optimal_team_panel(state)

    with wtp_col:
        st.subheader("WTP board (available players)")
        available_df = _players_dataframe(state)
        if not available_df.empty:
            available_df = available_df[~available_df["drafted"]]
        if available_df.empty:
            st.warning(
                f"No players loaded. Check that the seed CSV exists at "
                f"`{DEFAULT_WTP_CSV}`."
            )
        else:
            available_df = _position_filter(available_df, key="chart_position_filter")
            highlight_name = st.text_input(
                "Highlight a player",
                key="chart_highlight_input",
                placeholder="Type a player name to highlight on the chart…",
            )
            needle = highlight_name.strip().lower() if highlight_name else ""
            if needle:
                n_matches = available_df["player_name"].str.lower().str.contains(
                    needle, na=False
                ).sum()
                if n_matches == 0:
                    st.warning(
                        f"No available player matches “{highlight_name}”. "
                        "They may be drafted already or filtered out by the "
                        "position filter above."
                    )
                else:
                    st.caption(f"Highlighting {n_matches} matching player(s) with a ⭐ on the chart.")
            fig = build_board_chart(available_df, highlight_name=highlight_name, height=420)
            st.plotly_chart(fig, use_container_width=True)

    st.divider()

    st.subheader("Full board")
    tab_available, tab_drafted = st.tabs(["Available", "Drafted"])
    df = _players_dataframe(state)
    df = _position_filter(df, key="board_position_filter")
    with tab_available:
        st.dataframe(
            df[~df["drafted"]] if not df.empty else df,
            use_container_width=True, hide_index=True,
        )
    with tab_drafted:
        st.dataframe(
            df[df["drafted"]] if not df.empty else df,
            use_container_width=True, hide_index=True,
        )

    st.divider()
    _reset_button(state)


if __name__ == "__main__":
    main()
