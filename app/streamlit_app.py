"""Private company valuation dashboard.

Four tabs, each answering a stated question rather than just showing a table.
The headline view is mark staleness - how long since a company's valuation was
actually set by a priced round - because that's the question that matters when
you're looking at private marks and it's the one generic dashboards never ask.

Chart colours come from a validated palette (see the constants below). Status
colours always ship with a text label, never carrying meaning on their own.
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from pcd import checks
from pcd.db import fetch_analysis

# --- palette -------------------------------------------------------------
# Validated for contrast and colour-vision deficiency against the #fcfcfb
# surface pinned in .streamlit/config.toml. Don't reorder: the slot order is
# what keeps adjacent series distinguishable.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]

STATUS = {"Fresh": "#0ca30c", "Aging": "#fab219", "Stale": "#d03b3b", "No mark": "#898781"}

UP, DOWN, NEUTRAL = "#2a78d6", "#d03b3b", "#c3c2b7"

INK, INK_2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, SURFACE = "#e1e0d9", "#fcfcfb"

st.set_page_config(page_title="Private Company Tracker", layout="wide")


@st.cache_data(ttl=600)
def load(name: str) -> pd.DataFrame:
    return fetch_analysis(name)


def style(fig: go.Figure, height: int = 420, xtitle: str = "", ytitle: str = "") -> go.Figure:
    """Shared chart chrome: recessive grid, muted axes, no chart junk."""
    fig.update_layout(
        height=height,
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(family="system-ui, -apple-system, sans-serif", size=13, color=INK_2),
        margin=dict(l=8, r=24, t=8, b=8),
        hoverlabel=dict(bgcolor="white", font_size=13),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title=""),
    )
    axis = dict(gridcolor=GRID, zeroline=False, linecolor="#c3c2b7", tickfont=dict(color=MUTED))
    fig.update_xaxes(title=xtitle, **axis)
    fig.update_yaxes(title=ytitle, **axis)
    return fig


def billions(value) -> str:
    if pd.isna(value):
        return "—"
    return f"${float(value) / 1e9:,.1f}B"


# =========================================================================
st.title("Private Company Tracker")
st.caption(
    "Funding and valuation history for privately held companies, modelled as a "
    "Type 2 slowly changing dimension. Every figure traces to a published source."
)

positions = load("current_positions")
history = load("valuation_history")
stepups = load("round_step_ups")

# Public/acquired companies stay in the data but are excluded from the default
# views - a *private* company tracker that quietly includes public ones is lying.
private = positions[positions.status == "private"].copy()

tab_overview, tab_traj, tab_news, tab_quality = st.tabs(
    ["Portfolio", "Valuation trajectories", "News coverage", "Data quality"]
)

# --- Portfolio -----------------------------------------------------------
with tab_overview:
    marked = private.dropna(subset=["latest_valuation_usd"])
    stale_n = int((private.staleness_band == "Stale").sum())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Private companies", len(private))
    c2.metric("Aggregate valuation", billions(marked.latest_valuation_usd.astype(float).sum()))
    c3.metric("Median mark age", f"{int(private.days_since_mark.median())} days")
    c4.metric("Marks over 18 months old", stale_n)

    st.subheader("How stale is each valuation mark?")
    st.caption(
        "A private valuation is a point-in-time mark, not a live price. The older "
        "the mark, the less it tells you about what the company is worth today."
    )

    sl = private.dropna(subset=["days_since_mark"]).sort_values("days_since_mark", ascending=True)
    fig = go.Figure()
    for band in ["Fresh", "Aging", "Stale"]:
        sub = sl[sl.staleness_band == band]
        if sub.empty:
            continue
        fig.add_bar(
            y=sub.company_name,
            x=sub.days_since_mark,
            name=f"{band} ({len(sub)})",
            orientation="h",
            marker=dict(color=STATUS[band], line=dict(color=SURFACE, width=2)),
            customdata=sub[["latest_valuation_usd", "valuation_as_of", "latest_round_stage"]],
            hovertemplate=(
                "<b>%{y}</b><br>%{x} days since mark<br>"
                "Last priced: %{customdata[1]|%d %b %Y} (%{customdata[2]})<extra></extra>"
            ),
        )
    fig.update_layout(barmode="stack", bargap=0.35)
    st.plotly_chart(
        style(fig, height=760, xtitle="Days since last priced round"),
        use_container_width=True,
    )
    st.caption(
        f"**Read:** {stale_n} companies haven't been repriced in over 18 months. Those marks "
        "are the least likely to reflect fair value today — and in a secondaries context, "
        "the ones most worth a second look."
    )

    st.subheader("Where the value sits")
    by_sector = (
        marked.assign(v=marked.latest_valuation_usd.astype(float))
        .groupby("sector", as_index=False)
        .agg(total=("v", "sum"), companies=("company_id", "count"))
        .sort_values("total")
    )
    fig2 = go.Figure(
        go.Bar(
            x=by_sector.total / 1e9,
            y=by_sector.sector,
            orientation="h",
            marker=dict(color=SERIES[0], line=dict(color=SURFACE, width=2)),
            text=[f"{n} co." for n in by_sector.companies],
            textposition="outside",
            textfont=dict(color=MUTED, size=12),
            hovertemplate="<b>%{y}</b><br>$%{x:,.0f}B across %{text}<extra></extra>",
        )
    )
    fig2.update_layout(bargap=0.4)
    st.plotly_chart(
        style(fig2, height=320, xtitle="Combined valuation ($B)"),
        use_container_width=True,
    )

    with st.expander("Table view — all tracked companies"):
        st.dataframe(
            positions[
                ["company_name", "sector", "status", "latest_valuation_usd",
                 "latest_round_stage", "valuation_as_of", "days_since_mark", "staleness_band"]
            ],
            use_container_width=True,
            hide_index=True,
        )

# --- Trajectories --------------------------------------------------------
with tab_traj:
    st.subheader("Valuation over time")
    st.caption(
        "Drawn straight from the SCD2 dimension — each flat segment is one row's "
        "validity window, each vertical jump is a new priced round. Nothing is "
        "recomputed here, so if the steps are right, the model is right."
    )

    hist_priv = history[history.status == "private"]
    ranked = (
        hist_priv[hist_priv.is_current]
        .sort_values("valuation_usd", ascending=False)
        .company_name.tolist()
    )
    # Six is the cap on purpose: past that, adjacent series stop being reliably
    # distinguishable for colour-vision-deficient readers.
    picked = st.multiselect(
        "Companies (max 6)", ranked, default=ranked[:5], max_selections=6
    )

    if not picked:
        st.info("Pick at least one company to draw the chart.")
    else:
        fig3 = go.Figure()
        for i, name in enumerate(picked):
            rows = hist_priv[hist_priv.company_name == name].sort_values("valid_from")
            xs, ys = [], []
            for _, r in rows.iterrows():
                # two points per version draws the flat period explicitly
                xs += [r.valid_from, r.valid_to_plot]
                ys += [float(r.valuation_usd) / 1e9] * 2
            fig3.add_trace(
                go.Scatter(
                    x=xs, y=ys, mode="lines", name=name,
                    line=dict(color=SERIES[i % len(SERIES)], width=2, shape="hv"),
                    hovertemplate=(
                        f"<b>{name}</b><br>$%{{y:,.1f}}B"
                        "<br>%{x|%d %b %Y}<extra></extra>"
                    ),
                )
            )
            # direct label at the series end - required relief for the lighter hues
            fig3.add_annotation(
                x=xs[-1], y=ys[-1], text=f" {name}", showarrow=False,
                xanchor="left", font=dict(size=12, color=INK_2),
            )
        fig3.update_layout(showlegend=True)
        st.plotly_chart(
            style(fig3, height=460, ytitle="Post-money valuation ($B)"), use_container_width=True
        )

    st.subheader("Which rounds were up, and which were down?")
    st.caption(
        "Step-up multiple is this round's post-money divided by the previous "
        "round's. Below 1.0 is a down round — the company raised at a lower "
        "valuation than it previously held."
    )

    su = stepups[stepups.status == "private"].copy()
    su["mult"] = su.step_up_multiple.astype(float)
    su = su.sort_values("mult")
    su["label"] = su.company_name + " · " + su.announced_date.astype(str)

    show_down = st.checkbox("Show only down rounds", value=False)
    view = su[su.mult < 1] if show_down else su

    fig4 = go.Figure(
        go.Bar(
            x=view.mult - 1,  # centre the diverging scale on 1.0x
            y=view.label,
            orientation="h",
            marker=dict(
                color=[DOWN if m < 1 else UP for m in view.mult],
                line=dict(color=SURFACE, width=2),
            ),
            customdata=view[["mult", "post_money_usd", "prev_post_money"]],
            hovertemplate=(
                "<b>%{y}</b><br>%{customdata[0]:.2f}× previous valuation<extra></extra>"
            ),
        )
    )
    fig4.add_vline(x=0, line=dict(color=NEUTRAL, width=1))
    fig4.update_layout(bargap=0.3)
    fig4.update_xaxes(
        tickvals=[-0.5, 0, 1, 2, 3, 4, 5, 6, 7],
        ticktext=["0.5×", "1.0×", "2×", "3×", "4×", "5×", "6×", "7×", "8×"],
    )
    st.plotly_chart(
        style(fig4, height=max(320, 22 * len(view)), xtitle="Step-up multiple"),
        use_container_width=True,
    )
    n_down = int((su.mult < 1).sum())
    st.caption(
        f"**Read:** {n_down} of {len(su)} priced rounds were down rounds. They cluster in "
        "2023 and 2025–26 — repricings after the 2021 peak, and more recently in "
        "fintech and AI hardware."
    )

# --- News ----------------------------------------------------------------
with tab_news:
    news = load("news_mentions")
    st.subheader("News coverage picked up from GDELT")
    st.caption(
        "GDELT's search window is only about fifteen minutes wide, so this table "
        "accumulates a little on every scheduled run rather than being backfillable. "
        "Expect it to look thin early on."
    )

    if news.empty:
        st.info("No mentions captured yet. Run `python -m pcd.cli ingest`.")
    else:
        a, b, c = st.columns(3)
        a.metric("Mentions captured", len(news))
        b.metric("Companies covered", news.company_name.nunique())
        c.metric("Funding-related", int(news.is_funding_related.sum()))

        st.dataframe(
            news[["company_name", "domain", "published_at", "is_funding_related", "sentence"]]
            .rename(columns={"is_funding_related": "funding?"}),
            use_container_width=True,
            hide_index=True,
            column_config={
                "sentence": st.column_config.TextColumn("Matched sentence", width="large")
            },
        )
        st.caption(
            "**Note:** only the company that is the *subject* of a sentence is flagged "
            "funding-related. A company named in passing (\"founded by former OpenAI "
            "researchers\") is recorded as coverage but never credited with the round."
        )

# --- Data quality --------------------------------------------------------
with tab_quality:
    st.subheader("Data quality checks")
    st.caption("Run live against the database every time this page loads.")

    results = checks.run_all()
    failed = [r for r in results if not r.passed]
    (st.error if failed else st.success)(
        f"{len(results) - len(failed)} of {len(results)} checks passing"
    )
    st.dataframe(
        pd.DataFrame(
            [{"check": r.name, "result": "PASS" if r.passed else "FAIL", "detail": r.detail}
             for r in results]
        ),
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("Where the numbers came from")
    st.caption(
        "No free structured source of private-company valuations exists — Crunchbase "
        "closed its free API, and SEC Form D reports capital raised but never "
        "valuation. So history is hand-curated with a source link on every row, and "
        "new rounds are detected from news going forward. Publishing that split is "
        "more honest than hiding it."
    )
    st.dataframe(load("provenance"), use_container_width=True, hide_index=True)

    st.subheader("Companies no longer private")
    graduated = positions[positions.status != "private"][
        ["company_name", "status", "status_note"]
    ]
    st.caption(
        f"{len(graduated)} of {len(positions)} tracked companies have since IPO'd or been "
        "acquired. Their private history stays in the dataset; they're excluded from the "
        "portfolio views above."
    )
    st.dataframe(graduated, use_container_width=True, hide_index=True)
