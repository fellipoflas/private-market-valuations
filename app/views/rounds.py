"""Rounds & repricing: is momentum slowing, who repriced down, and which marks are stale?"""

import html

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import ui

from pcd.transform.stages import STAGE_ORDER

ui.page_header(
    "Rounds & repricing risk",
    "Down rounds, round-over-round momentum and mark staleness: the signals that a headline "
    "valuation may no longer be what shares actually trade for.",
    ui.header_chip(),
)
summary_slot = st.container()  # filled at the bottom, once everything is computed

show_exited = st.toggle(
    "Include companies that have since IPO'd or been acquired",
    key="r_exited",
    help="Their private rounds still happened. Leaving them out understates momentum, because "
    "the companies that exited tend to be the ones that compounded fastest.",
)

lb = ui.leaderboard()
steps = ui.load("round_step_ups")
activity = ui.load("round_activity_by_year")
scope = lb if show_exited else lb[lb.status == "private"]
s = steps[steps.company_id.isin(scope.company_id)].copy()
down = s[s.step_up_multiple < 1].sort_values("pct_change")

# --- KPI row -------------------------------------------------------------
repeat = down.groupby("company_name").size()
worst = down.iloc[0] if len(down) else None
k = st.columns(4)
k[0].metric(
    "Down rounds on record",
    len(down),
    delta=f"of {len(s)} priced rounds with a prior mark",
    delta_color="off",
    delta_arrow="off",
    border=True,
)
k[1].metric(
    "Down-round rate",
    ui.pct(len(down) / len(s), signed=False) if len(s) else "—",
    delta="share of rounds priced below the last mark",
    delta_color="off",
    delta_arrow="off",
    border=True,
)
k[2].metric(
    "Largest decline",
    ui.pct(worst["pct_change"]) if worst is not None else "—",
    delta=f"{worst.company_name}, {pd.Timestamp(worst.announced_date):%b %Y}"
    if worst is not None
    else None,
    delta_color="off",
    delta_arrow="off",
    border=True,
)
k[3].metric(
    "Repeat down-rounders",
    int((repeat >= 2).sum()),
    delta=", ".join(repeat[repeat >= 2].index) or "none",
    delta_color="off",
    delta_arrow="off",
    border=True,
)

# --- momentum + down-round table ----------------------------------------
left, right = st.columns([1.25, 1], gap="medium")
with left, ui.card("momentum"):
    ui.card_title(
        "Is round momentum slowing?",
        "Bars: median step-up per year (post-money ÷ previous post-money). Line: number of priced "
        "rounds. Always includes since-exited companies, to avoid survivorship bias.",
    )
    a = activity[activity.year >= activity.year.max() - 5]
    fig = go.Figure()
    fig.add_bar(
        x=a.year,
        y=a.median_step_up,
        name="Median step-up",
        marker_color=ui.STAGE_COLORS["Growth"],
        text=a.median_step_up.map(lambda v: f"{v:.2f}×" if pd.notna(v) else ""),
        textposition="inside",
        insidetextanchor="end",
        textfont=dict(color=ui.PAGE, size=12),
        hovertemplate="%{x}: median %{y:.2f}×<extra></extra>",
    )
    fig.add_scatter(
        x=a.year,
        y=a.priced_rounds,
        name="Priced rounds",
        yaxis="y2",
        mode="lines+markers",
        line=dict(color=ui.SERIES[1], width=2),
        marker=dict(size=7),
        hovertemplate="%{x}: %{y} priced rounds<extra></extra>",
    )
    fig.add_hline(y=1, line=dict(color=ui.DOWN, width=1, dash="dot"))
    ui.style(fig, height=340)
    fig.update_layout(
        showlegend=True,
        bargap=0.45,
        margin=dict(t=36),
        yaxis=dict(title="", ticksuffix="×", rangemode="tozero"),
        # right-hand axis for the round count, coloured to match its line
        yaxis2=dict(
            overlaying="y",
            side="right",
            showgrid=False,
            dtick=10,
            tickfont=dict(color=ui.SERIES[1]),
            range=[0, a.priced_rounds.max() * 1.25],
        ),
    )
    fig.update_xaxes(dtick=1)
    ui.plot(fig, key="momentum")

with right, ui.card("downrounds"):
    ui.card_title(
        "Which rounds were priced below the last mark?",
        "Each row is a down round: what the company was marked at before, and what it raised at.",
    )
    if down.empty:
        st.markdown(
            '<p class="card-sub">No down rounds in this selection.</p>', unsafe_allow_html=True
        )
    else:
        st.dataframe(
            pd.DataFrame(
                {
                    "Company": down.company_name,
                    "Date": pd.to_datetime(down.announced_date),
                    "Previous mark": down.prev_post_money / 1e9,
                    "New mark": down.post_money_usd / 1e9,
                    "Change": down["pct_change"] * 100,
                    "Source": down.source_url,
                }
            ),
            hide_index=True,
            column_config={
                "Date": st.column_config.DateColumn(format="MMM YYYY"),
                "Previous mark": st.column_config.NumberColumn(format="$%.1fB"),
                "New mark": st.column_config.NumberColumn(format="$%.1fB"),
                "Change": st.column_config.NumberColumn(format="%+.0f%%"),
                "Source": st.column_config.LinkColumn(display_text=r"https?://(?:www\.)?([^/]+).*"),
            },
        )
        st.markdown(
            '<p class="card-sub">A down round resets the mark, but it is also a buying signal: '
            "it is the one time a private valuation is forced back toward what investors will "
            "actually pay.</p>",
            unsafe_allow_html=True,
        )

# --- staleness + sector concentration -----------------------------------
left, right = st.columns([1.25, 1], gap="medium")
marked = scope.dropna(subset=["latest_valuation_usd", "days_since_mark"])
with left, ui.card("stale"):
    ui.card_title(
        "Which large marks are going stale?",
        "Every company by size and time since its last priced round. Top-right is the watchlist: "
        "big valuations that haven't been tested by a new round in over 18 months.",
    )
    fig = go.Figure()
    # Shapes on a log axis take data values; annotations take log10 values. (Yes, really.)
    xmax = marked.days_since_mark.max() * 1.12
    fig.add_shape(
        type="rect",
        x0=548,
        x1=xmax,
        y0=10,
        y1=marked.latest_valuation_usd.max() / 1e9 * 1.6,
        yref="y",
        fillcolor="rgba(255,107,107,0.07)",
        line=dict(color="rgba(255,107,107,0.35)", width=1, dash="dot"),
        layer="below",
    )
    fig.add_annotation(
        x=548,
        y=3.2,
        xanchor="left",
        yanchor="top",
        showarrow=False,
        xshift=6,
        yshift=-4,
        text="Watchlist: > $10B and > 18 months",
        font=dict(color=ui.DOWN, size=11),
    )
    for stage in STAGE_ORDER:
        g = marked[marked.stage == stage]
        if g.empty:
            continue
        big = g.latest_valuation_usd >= 10e9
        fig.add_scatter(
            x=g.days_since_mark,
            y=g.latest_valuation_usd / 1e9,
            mode="markers+text",
            name=stage,
            marker=dict(size=11, color=ui.STAGE_COLORS[stage], line=dict(color=ui.CARD, width=1.5)),
            # label only the watchlist, so the dense cluster near the origin stays readable
            text=np.where(big & (g.days_since_mark > 548), g.company_name, ""),
            textposition=[
                "middle left" if d > xmax * 0.7 else "middle right" for d in g.days_since_mark
            ],
            textfont=dict(color=ui.INK, size=11),
            customdata=g.company_name,
            hovertemplate=(
                "<b>%{customdata}</b><br>$%{y:,.1f}B · %{x} days since mark<extra></extra>"
            ),
        )
    ui.style(fig, height=380)
    fig.update_layout(showlegend=True, margin=dict(t=36, r=40))
    fig.update_yaxes(
        type="log",
        tickvals=[1, 3, 10, 30, 100, 300, 1000],
        ticktext=["$1B", "$3B", "$10B", "$30B", "$100B", "$300B", "$1T"],
    )
    fig.update_xaxes(title="Days since last priced round", range=[0, xmax])
    ui.plot(fig, key="stale")

with right, ui.card("sectors"):
    ui.card_title(
        "Where does the value sit?",
        "Share of combined latest marks by sector. Each company belongs to exactly one sector, "
        "assigned by hand, so nothing is double-counted.",
    )
    by = (
        marked.groupby("sector")
        .agg(
            value=("latest_valuation_usd", "sum"),
            n=("company_id", "count"),
            top=("company_name", "first"),
        )
        .assign(share=lambda d: d.value / d.value.sum())
        .sort_values("share")
    )
    fig = go.Figure(
        go.Bar(
            x=by.share,
            y=by.index,
            orientation="h",
            marker_color=ui.SERIES[0],
            text=[f"{sh:.0%} · {n} co." for sh, n in zip(by.share, by.n, strict=True)],
            textposition="outside",
            textfont=dict(color=ui.INK, size=12),
            cliponaxis=False,
            customdata=np.column_stack([by.value.map(ui.money), by.top]),
            hovertemplate=(
                "<b>%{y}</b><br>%{customdata[0]} combined<br>"
                "largest: %{customdata[1]}<extra></extra>"
            ),
        )
    )
    ui.style(fig, height=380)
    fig.update_layout(bargap=0.35, margin=dict(r=70))
    fig.update_xaxes(tickformat=".0%", range=[0, by.share.max() * 1.25])
    ui.plot(fig, key="sectors")

watch = marked[(marked.days_since_mark > 548) & (marked.latest_valuation_usd >= 10e9)]
recent = activity.dropna(subset=["median_step_up"]).tail(2)
tiles = []
if len(s):
    tiles.append(
        (
            f"{len(down) / len(s):.0%}",
            f"of priced rounds were down rounds ({len(down)} of {len(s)}). Repricing is rare in "
            "this group, so each one is a meaningful signal.",
            ui.DOWN,
        )
    )
if len(recent) == 2:
    now, before = recent.iloc[1], recent.iloc[0]
    direction = "down from" if now.median_step_up < before.median_step_up else "up from"
    tiles.append(
        (
            f"{now.median_step_up:.2f}×",
            f"median step-up in {int(now.year)}, {direction} {before.median_step_up:.2f}× in "
            f"{int(before.year)}. Rounds still price well above the previous mark.",
            ui.STAGE_COLORS["Growth"],
        )
    )
tiles.append(
    (
        str(len(watch)),
        "large marks (over $10B) untested by a new round in 18+ months"
        + (f": {', '.join(html.escape(n) for n in watch.company_name)}" if len(watch) else "")
        + ". That is where headline and real price are most likely to have drifted apart.",
        ui.SERIES[1],
    )
)
with summary_slot:
    ui.summary(tiles)
