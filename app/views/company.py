"""Company lineage: one company's full history, and the source behind every figure.

This page exists to answer the question a skeptical reader should ask of any
private-market dashboard: how do you know these numbers are right?
"""

import html
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import ui

from pcd.transform.stages import stage_bucket

TODAY = pd.Timestamp(date.today())

lb = ui.leaderboard()
options = lb.sort_values("latest_valuation_usd", ascending=False, na_position="last")
ids = options.company_id.tolist()
names = dict(zip(options.company_id, options.company_name, strict=True))

# The leaderboard links here with ?company=<id>; keep the URL in sync so a
# company view is shareable.
wanted = st.query_params.get("company", ids[0])
current = wanted if wanted in ids else ids[0]

ui.page_header(
    "Company lineage",
    "Every funding event for one company, how it entered the dataset, and the published "
    "source behind each figure. Click any source to check it yourself.",
    ui.header_chip(),
)

pick_col, _ = st.columns([1.2, 2.8])
company_id = pick_col.selectbox(
    "Company", ids, index=ids.index(current), format_func=names.get, key="company_pick"
)
if company_id != st.query_params.get("company"):
    st.query_params["company"] = company_id

c = lb[lb.company_id == company_id].iloc[0]
rounds = ui.load("company_rounds", company_id=company_id)
history = ui.load("valuation_history")
h = history[history.company_id == company_id].copy()
h["valid_from"] = pd.to_datetime(h.valid_from)
news = ui.load("news_mentions")
news = news[news.company_id == company_id]

# --- identity strip ------------------------------------------------------
badges = ui.stage_pill(c.stage) + (" " + ui.ipo_pill() if c.ipo_watch else "")
status = (
    ""
    if c.status == "private"
    else (f' <span class="pill" style="--c:{ui.MUTED}">{html.escape(c.status.title())}</span>')
)
st.markdown(
    f'<div class="card-sub" style="font-size:.95rem">{html.escape(c.sector or "")} · '
    f"{html.escape(c.subsector or '')} &nbsp; {badges}{status}</div>",
    unsafe_allow_html=True,
)
if c.status != "private" and c.status_note:
    st.caption(f"No longer private: {c.status_note}")

# --- KPIs ----------------------------------------------------------------
priced = rounds.dropna(subset=["post_money_usd"]).sort_values("announced_date")
multiples = (priced.post_money_usd / priced.post_money_usd.shift()).dropna()
k = st.columns(5)
k[0].metric(
    "Latest mark",
    ui.money(c.latest_valuation_usd),
    delta=f"{ui.change(c.change_1y)} in 1 yr"
    if pd.notna(c.change_1y) and c.change_1y != 0
    else None,
    border=True,
)
k[1].metric("Raised (tracked rounds)", ui.money(c.raised_tracked_usd), border=True)
k[2].metric(
    "Funding events",
    len(rounds),
    delta=f"{len(priced)} priced",
    delta_color="off",
    delta_arrow="off",
    border=True,
)
k[3].metric(
    "Mark age",
    f"{int(c.days_since_mark)} days" if pd.notna(c.days_since_mark) else "—",
    delta="Stale (> 1 yr)" if c.days_since_mark > 365 else "Recent",
    delta_arrow="off",
    delta_color="inverse" if c.days_since_mark > 365 else "normal",
    border=True,
)
k[4].metric(
    "Median step-up",
    f"{multiples.median():.2f}×" if len(multiples) else "—",
    delta=f"{int((multiples < 1).sum())} down rounds",
    delta_color="off",
    delta_arrow="off",
    border=True,
)

# --- chart + IPO evidence -------------------------------------------------
left, right = st.columns([2.2, 1], gap="medium")
with left, ui.card("company_chart"):
    ui.card_title(
        f"How has {c.company_name}'s valuation moved?",
        "Each step is one version of the company in the SCD Type 2 dimension: the mark holds "
        "flat until the next priced round replaces it.",
    )
    if h.empty:
        st.info("No priced rounds on record for this company.")
    else:
        xs = [*h.valid_from, TODAY]
        ys = [*(h.valuation_usd / 1e9), h.valuation_usd.iloc[-1] / 1e9]
        fig = go.Figure()
        fig.add_scatter(
            x=xs,
            y=ys,
            mode="lines",
            line=dict(color=ui.SERIES[0], width=2.6, shape="hv"),
            fill="tozeroy",
            fillcolor="rgba(78,168,255,0.08)",
            hoverinfo="skip",
        )
        fig.add_scatter(
            x=h.valid_from,
            y=h.valuation_usd / 1e9,
            mode="markers+text",
            marker=dict(size=9, color=ui.SERIES[0], line=dict(color=ui.CARD, width=2)),
            text=[
                f"{s or ''}<br>{ui.money(v)}"
                for s, v in zip(h.round_stage, h.valuation_usd, strict=True)
            ],
            textposition="top left",
            textfont=dict(color=ui.INK, size=11),
            customdata=h.source_url.map(ui.domain),
            hovertemplate=(
                "%{x|%b %d, %Y}<br><b>$%{y:,.1f}B</b><br>source: %{customdata}<extra></extra>"
            ),
        )
        ui.style(fig, height=380)
        fig.update_layout(margin=dict(t=40, r=24))
        fig.update_yaxes(tickprefix="$", ticksuffix="B", rangemode="tozero", tickformat=",.0f")
        fig.update_xaxes(
            range=[h.valid_from.min() - pd.Timedelta(days=120), TODAY + pd.Timedelta(days=30)]
        )
        ui.plot(fig, key="company_chart")

with right, ui.card("company_ipo"):
    ui.card_title("Is an exit on the horizon?")
    if c.ipo_watch:
        st.markdown(
            f'{ui.ipo_pill()}<p style="margin:.6rem 0 .4rem">{html.escape(c.ipo_watch_note)}</p>'
            f'<div class="card-sub">Reported {pd.Timestamp(c.ipo_watch_as_of):%b %d, %Y} · '
            f'<a href="{html.escape(c.ipo_watch_url)}" target="_blank">'
            f"{ui.domain(c.ipo_watch_url)}</a></div>",
            unsafe_allow_html=True,
        )
    elif c.status != "private":
        st.markdown(f"Already exited: {html.escape(c.status_note or c.status)}")
    else:
        st.markdown(
            '<p class="card-sub">No published report of concrete IPO preparation (banks hired, '
            "a confidential filing, or a stated timeline). Speculation doesn't count.</p>",
            unsafe_allow_html=True,
        )
    st.divider()
    ui.card_title("How stale is the mark?")
    if pd.notna(c.days_since_mark):
        years = c.days_since_mark / 365
        verdict = (
            "Fresh: priced within the last 6 months."
            if c.days_since_mark < 183
            else "Aging: 6-18 months since a priced round."
            if c.days_since_mark < 548
            else f"Stale: {years:.1f} years since the last priced round. Treat the headline number "
            "with suspicion."
        )
        st.markdown(f'<p class="card-sub">{verdict}</p>', unsafe_allow_html=True)

# --- lineage table -------------------------------------------------------
with ui.card("lineage"):
    ui.card_title(
        "Where did each number come from?",
        "<b>curated</b> = hand-entered from the linked article and reviewed. Rows detected "
        "automatically from news would show the exact sentence they were extracted from. "
        "<b>Link check</b> is the last result of <code>pcd.cli check-links</code>.",
    )
    link = rounds.source_reachable.map({True: "loads", False: "check"}).fillna("not checked")
    table = pd.DataFrame(
        {
            "Date": pd.to_datetime(rounds.announced_date),
            "Round": rounds.round_stage,
            "Stage": rounds.round_stage.map(stage_bucket),
            "Raised": rounds.amount_raised_usd / 1e9,
            "Post-money": rounds.post_money_usd / 1e9,
            "Lead investor": rounds.lead_investor,
            "Source": rounds.source_url,
            "Link check": link,
            "Confidence": rounds.confidence,
            "Method": rounds.extraction_method,
            "Reviewed": rounds.reviewed_by_human,
            "Notes / evidence": rounds.evidence_text.fillna(rounds.notes),
        }
    )
    st.dataframe(
        table,
        hide_index=True,
        placeholder="—",
        column_config={
            "Date": st.column_config.DateColumn(format="MMM D, YYYY"),
            "Raised": st.column_config.NumberColumn(format="$%.2fB"),
            "Post-money": st.column_config.NumberColumn(format="$%.1fB"),
            "Source": st.column_config.LinkColumn(display_text=r"https?://(?:www\.)?([^/]+).*"),
            "Reviewed": st.column_config.CheckboxColumn(),
            "Notes / evidence": st.column_config.TextColumn(width="large"),
        },
    )

# --- news ----------------------------------------------------------------
with ui.card("company_news"):
    ui.card_title(
        f"What has the news pipeline picked up on {c.company_name}?",
        "From the scheduled GDELT funding-news query. It only accumulates forward from the "
        "first run, so coverage grows over time rather than being backfilled.",
    )
    if news.empty:
        st.markdown(
            f'<p class="card-sub">No mentions of {html.escape(c.company_name)} captured yet.</p>',
            unsafe_allow_html=True,
        )
    else:
        st.dataframe(
            pd.DataFrame(
                {
                    "Published": pd.to_datetime(news.published_at),
                    "Headline": news.title,
                    "Outlet": news.domain,
                    "Link": news.article_url,
                    "Funding?": news.is_funding_related,
                    "Matched sentence": news.sentence,
                }
            ),
            hide_index=True,
            column_config={
                "Published": st.column_config.DatetimeColumn(format="MMM D, YYYY"),
                "Headline": st.column_config.TextColumn(width="large"),
                "Link": st.column_config.LinkColumn(display_text="open ↗"),
                "Matched sentence": st.column_config.TextColumn(width="large"),
            },
        )
