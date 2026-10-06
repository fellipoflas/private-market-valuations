"""Market overview: who is worth the most, and how fast are they compounding?"""

import html
import math
from datetime import date

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import ui

from pcd.transform.stages import STAGE_ORDER

TODAY = pd.Timestamp(date.today())
YEAR_AGO = TODAY - pd.Timedelta(days=365)
TWO_YEARS_AGO = TODAY - pd.Timedelta(days=730)
MAX_HIGHLIGHT = 10

lb = ui.leaderboard()
history = ui.load("valuation_history")
rounds = ui.load("funding_rounds")
steps = ui.load("round_step_ups")

ui.page_header(
    "Private Market Valuations", ui.DECISION, ui.header_chip(), eyebrow="Secondary-market view"
)
# The summary is computed at the bottom (it depends on the filters below) but
# drawn here, so the conclusions are the first thing a reader sees.
summary_slot = st.container()


# --- filters: one slim row --------------------------------------------------
# Sector and stage live in popovers so the row stays one line high; the button
# label shows what's selected, so you can see active filters without opening them.
def filter_label(name: str, key: str) -> str:
    chosen = st.session_state.get(key) or []
    if not chosen:
        return f"{name}: All"
    return f"{name}: {chosen[0]}" if len(chosen) == 1 else f"{name}: {len(chosen)} selected"


def reset_filters() -> None:
    for key, empty in (("f_sector", []), ("f_stage", []), ("f_ipo", False), ("f_exited", False)):
        st.session_state[key] = empty


with st.container(horizontal=True, vertical_alignment="center", gap="small", key="filterbar"):
    st.markdown('<span class="filter-h">Filters</span>', unsafe_allow_html=True)
    with st.popover(filter_label("Sector", "f_sector"), icon=":material/category:"):
        pick_sectors = st.pills(
            "Sector",
            sorted(lb.sector.dropna().unique()),
            selection_mode="multi",
            key="f_sector",
            label_visibility="collapsed",
        )
    with st.popover(filter_label("Stage", "f_stage"), icon=":material/stairs:"):
        pick_stages = st.pills(
            "Stage",
            STAGE_ORDER,
            selection_mode="multi",
            key="f_stage",
            label_visibility="collapsed",
        )
    ipo_only = st.toggle("IPO watch only", key="f_ipo")
    show_exited = st.toggle("Include IPO'd / acquired", key="f_exited")
    if pick_sectors or pick_stages or ipo_only or show_exited:
        st.button("Reset", on_click=reset_filters, type="tertiary", icon=":material/close:")

f = lb if show_exited else lb[lb.status == "private"]
if pick_sectors:
    f = f[f.sector.isin(pick_sectors)]
if pick_stages:
    f = f[f.stage.isin(pick_stages)]
if ipo_only:
    f = f[f.ipo_watch]
marked = f.dropna(subset=["latest_valuation_usd"]).sort_values(
    "latest_valuation_usd", ascending=False
)
if marked.empty:
    st.info("No companies match these filters.")
    st.stop()
ids = set(marked.company_id)

# --- KPI row -------------------------------------------------------------
hist = history[history.company_id.isin(ids)].copy()
hist["valid_from"] = pd.to_datetime(hist.valid_from)
hist["valid_to"] = pd.to_datetime(hist.valid_to.astype(str).replace("9999-12-31", "2262-01-01"))
quarter_ends = pd.date_range(end=TODAY, periods=12, freq="QE")


def combined_at(d: pd.Timestamp) -> float:
    """Sum of every company's mark as it stood on date d - an SCD2 point-in-time query."""
    live = hist[(hist.valid_from <= d) & (hist.valid_to >= d)]
    return live.valuation_usd.sum() / 1e9


r = rounds[rounds.company_id.isin(ids) & rounds.is_priced].copy()
r["d"] = pd.to_datetime(r.announced_date)
s = steps[steps.company_id.isin(ids)].copy()
s["d"] = pd.to_datetime(s.announced_date)
s12, sprev = s[s.d > YEAR_AGO], s[(s.d > TWO_YEARS_AGO) & (s.d <= YEAR_AGO)]


def per_quarter(df: pd.DataFrame, agg, empty_is_zero: bool = True) -> list[float]:
    """One value per quarter for the sparklines. Counts default to 0 in a quiet
    quarter; a median of nothing is unknown, not zero, so those get dropped."""
    q = df.groupby(df.d.dt.to_period("Q")).apply(agg) if len(df) else pd.Series(dtype=float)
    q = q.reindex(quarter_ends.to_period("Q"))
    return (q.fillna(0) if empty_is_zero else q.dropna()).tolist()


def vs_prior(now: float, before: float, fmt: str = "{:+d}") -> dict:
    """Delta kwargs for st.metric. No change gets no arrow and no colour."""
    if pd.isna(now) or pd.isna(before):
        return {}
    if now == before:
        return dict(delta="Same as prior yr", delta_color="off", delta_arrow="off")
    return dict(delta=f"{fmt.format(now - before)} vs prior yr")


top = marked.iloc[0]
total = marked.latest_valuation_usd.sum()
top2 = marked.latest_valuation_usd.head(2).sum() / total
n12, nprev = int((r.d > YEAR_AGO).sum()), int(((r.d > TWO_YEARS_AGO) & (r.d <= YEAR_AGO)).sum())
med12, medprev = s12.step_up_multiple.median(), sprev.step_up_multiple.median()
down12, downprev = int((s12.step_up_multiple < 1).sum()), int((sprev.step_up_multiple < 1).sum())

k = st.columns(5)
k[0].metric(
    "Most valuable",
    ui.money(top.latest_valuation_usd),
    delta=f"{top.company_name} · {ui.change(top.change_1y)} in 1 yr"
    if pd.notna(top.change_1y)
    else top.company_name,
    chart_data=top.valuation_path_b,
    chart_type="area",
    border=True,
    help="Latest post-money valuation from a priced round or tender offer.",
)
k[1].metric(
    "Combined latest marks",
    ui.money(total),
    delta=f"Top 2 hold {top2:.0%}",
    delta_color="off",
    delta_arrow="off",
    chart_data=[combined_at(d) for d in quarter_ends],
    chart_type="area",
    border=True,
    help=(
        "Sum of each company's latest post-money valuation, i.e. the market size of this "
        "list. It is NOT a portfolio value: nobody owns 100% of these companies."
    ),
)
k[2].metric(
    "Priced rounds · 12 mo",
    n12,
    **vs_prior(n12, nprev),
    chart_data=per_quarter(r, len),
    chart_type="bar",
    border=True,
    help="Funding rounds or tender offers that disclosed a valuation, in the last 365 days.",
)
k[3].metric(
    "Median step-up · 12 mo",
    f"{med12:.2f}×" if pd.notna(med12) else "—",
    **vs_prior(round(med12, 2), round(medprev, 2), "{:+.2f}×"),
    chart_data=per_quarter(s, lambda g: g.step_up_multiple.median(), empty_is_zero=False),
    chart_type="line",
    border=True,
    help="Each round's post-money ÷ the previous round's. 2.0× means the valuation doubled.",
)
k[4].metric(
    "Down rounds · 12 mo",
    down12,
    **({"delta_color": "inverse"} | vs_prior(down12, downprev)),
    chart_data=per_quarter(s, lambda g: (g.step_up_multiple < 1).sum()),
    chart_type="bar",
    border=True,
    help="Rounds priced below the company's previous mark.",
)

# --- hero row: trajectories + ranked list ---------------------------------
left, right = st.columns([2.75, 1], gap="medium")

with left, ui.card("hero"):
    ui.card_title(
        "How fast are the most valuable companies compounding?",
        "Each dot is a priced round. The dotted tail runs from the latest mark to today: "
        "the longer the tail, the staler the number. Grey lines are the rest of the list.",
    )
    names = marked.company_name.tolist()
    # Keep the highlight selection valid when filters remove companies.
    if "hl" not in st.session_state:
        st.session_state.hl = names[:5]
    else:
        st.session_state.hl = [n for n in st.session_state.hl if n in names]
    st.session_state.hl_prev = list(st.session_state.hl)

    def set_highlight(selection: list[str]) -> None:
        st.session_state.hl = selection[:MAX_HIGHLIGHT]

    def cap_highlight() -> None:
        """Pills have no max_selections, so refuse the click that would make it 11."""
        if len(st.session_state.hl) > MAX_HIGHLIGHT:
            st.session_state.hl = st.session_state.hl_prev
            st.session_state.hl_capped = True

    ctl1, ctl2 = st.columns([1, 1.6], vertical_alignment="bottom")
    scale = (
        ctl1.segmented_control(
            "Scale",
            ["Log", "Linear", "Indexed"],
            default="Log",
            key="scale",
            help="Log keeps $5B and $900B companies readable on one chart. "
            "Indexed rebases every company to its first tracked mark.",
        )
        or "Log"
    )
    with ctl2, st.container(horizontal=True, horizontal_alignment="right", gap="small"):
        st.button("Top 5", on_click=set_highlight, args=(names[:5],), key="hl_top5")
        st.button("Top 10", on_click=set_highlight, args=(names[:10],), key="hl_top10")
        ipo_names = marked[marked.ipo_watch].company_name.tolist()
        st.button(
            "IPO watch",
            on_click=set_highlight,
            args=(ipo_names,),
            key="hl_ipo",
            disabled=not ipo_names,
        )
        st.button("Clear", on_click=set_highlight, args=([],), key="hl_clear")

    # colours follow rank order, so a company keeps its colour as others come and go
    picked = [n for n in names if n in st.session_state.hl]

    h = hist.sort_values(["company_id", "valid_from"]).copy()
    h["first"] = h.groupby("company_id").valuation_usd.transform("first")
    h["prev"] = h.groupby("company_id").valuation_usd.shift()
    h["y"] = h.valuation_usd / h["first"] if scale == "Indexed" else h.valuation_usd / 1e9

    fig = go.Figure()
    for name, g in h[~h.company_name.isin(picked)].groupby("company_name"):
        fig.add_scatter(
            x=[*g.valid_from, TODAY],
            y=[*g.y, g.y.iloc[-1]],
            mode="lines",
            line=dict(color=ui.CONTEXT, width=1.1),
            hovertemplate=f"{html.escape(name)}<extra></extra>",
        )

    ends = []
    for i, name in enumerate(picked):
        g = h[h.company_name == name]
        if g.empty:
            continue
        color = ui.SERIES[i % len(ui.SERIES)]
        # slots 7-10 are dashed: their colours aren't all separable for colour-blind
        # readers, so line style carries the difference too
        dash = "dash" if i >= ui.CVD_SAFE_SLOTS else "solid"
        raised = g.amount_raised_usd.map(
            lambda v: f" · raised {ui.money(v)}" if pd.notna(v) else ""
        )
        step = (g.valuation_usd / g.prev).map(
            lambda v: f"{v:.2f}× previous mark" if pd.notna(v) else "First tracked mark"
        )
        custom = np.column_stack(
            [
                g.round_stage.fillna("Round"),
                g.valuation_usd.map(ui.money),
                raised,
                step,
                g.source_url.map(ui.domain),
            ]
        )
        fig.add_scatter(
            x=g.valid_from,
            y=g.y,
            mode="lines+markers",
            customdata=custom,
            line=dict(color=color, width=2.4, dash=dash),
            marker=dict(size=7, color=color, line=dict(color=ui.CARD, width=1.5)),
            hovertemplate=(
                f"<b>{html.escape(name)}</b><br>%{{customdata[0]}} · %{{x|%b %d, %Y}}<br>"
                "Post-money <b>%{customdata[1]}</b>%{customdata[2]}<br>%{customdata[3]}<br>"
                f"<span style='color:{ui.MUTED}'>source: %{{customdata[4]}}</span><extra></extra>"
            ),
        )
        fig.add_scatter(
            x=[g.valid_from.iloc[-1], TODAY],
            y=[g.y.iloc[-1]] * 2,
            mode="lines",
            line=dict(color=color, width=1.6, dash="dot"),
            hoverinfo="skip",
        )
        last = g.y.iloc[-1]
        label = f"{last:.1f}×" if scale == "Indexed" else ui.money(g.valuation_usd.iloc[-1])
        ends.append((name, last, label, color))

    is_log = scale != "Linear"
    lo, hi = h.y.min(), h.y.max()
    to_axis = (lambda v: math.log10(v)) if is_log else (lambda v: v)
    # explicit y-range so end labels can be kept inside it
    pad = 0.08 * ((to_axis(hi) - to_axis(lo)) or 1)
    y_range = [to_axis(lo) - pad, to_axis(hi) + pad]
    if ends:
        span = y_range[1] - y_range[0]
        placed = ui.spread_labels(
            [to_axis(e[1]) for e in ends], min_gap=span * 0.052, hi=y_range[1]
        )
        for (name, _, label, color), y in zip(ends, placed, strict=True):
            fig.add_annotation(
                x=TODAY,
                y=y,
                xanchor="left",
                xshift=8,
                showarrow=False,
                align="left",
                text=f"<b>{html.escape(name)}</b> <span style='color:{ui.MUTED}'>{label}</span>",
                font=dict(color=color, size=12),
            )

    # "today" marker - the x-axis always runs to today, so stale marks are visible
    fig.add_shape(
        type="line",
        x0=TODAY,
        x1=TODAY,
        y0=0,
        y1=1,
        yref="paper",
        line=dict(color=ui.MUTED, width=1, dash="dot"),
    )
    fig.add_annotation(
        x=TODAY,
        y=1,
        yref="paper",
        yanchor="bottom",
        showarrow=False,
        text="today",
        font=dict(color=ui.MUTED, size=11),
    )

    longest = max((len(e[0]) + len(e[2]) for e in ends), default=10)
    ui.style(fig, height=640)
    fig.update_layout(margin=dict(r=min(40 + longest * 7, 230), t=28), hovermode="closest")
    fig.update_xaxes(
        range=[h.valid_from.min() - pd.Timedelta(days=45), TODAY + pd.Timedelta(days=20)],
        rangeselector=dict(
            buttons=[
                dict(count=1, label="1Y", step="year", stepmode="backward"),
                dict(count=3, label="3Y", step="year", stepmode="backward"),
                dict(count=5, label="5Y", step="year", stepmode="backward"),
                dict(step="all", label="All"),
            ],
            bgcolor=ui.CARD_2,
            activecolor="#2b4166",
            bordercolor=ui.BORDER,
            borderwidth=1,
            font=dict(color=ui.INK, size=11),
            x=0,
            y=1.0,
            yanchor="bottom",
        ),
    )
    if scale == "Indexed":
        ticks = [t for t in (1, 2, 5, 10, 20, 50, 100, 200, 500) if lo / 1.5 <= t <= hi * 1.5]
        fig.update_yaxes(
            type="log", tickvals=ticks, ticktext=[f"{t}×" for t in ticks], title="", range=y_range
        )
    elif is_log:
        ticks = [t for t in (0.1, 0.3, 1, 3, 10, 30, 100, 300, 1000, 3000) if lo / 3 <= t <= hi * 3]
        fig.update_yaxes(
            type="log",
            tickvals=ticks,
            ticktext=[ui.money(t * 1e9, 0) for t in ticks],
            title="",
            range=y_range,
        )
    else:
        fig.update_yaxes(tickprefix="$", ticksuffix="B", title="", range=y_range)
    ui.plot(fig, key="hero")

    # Every company as a clickable chip, in rank order: one click adds or removes
    # a line. Easier to scan than a dropdown, and you can see the whole list.
    st.pills(
        f"Highlight companies (up to {MAX_HIGHLIGHT}): click to add or remove",
        names,
        selection_mode="multi",
        key="hl",
        on_change=cap_highlight,
    )
    # Colour each selected chip like its line, so the chip row doubles as the legend.
    # Chips render in `names` order, which is what nth-of-type counts.
    chip_css = []
    for slot, name in enumerate(picked):
        color = ui.SERIES[slot % len(ui.SERIES)]
        border = "dashed" if slot >= ui.CVD_SAFE_SLOTS else "solid"
        nth = names.index(name) + 1
        chip_css.append(
            f'.st-key-hl [role="toolbar"] > button:nth-of-type({nth}) {{'
            f"border: 1.5px {border} {color} !important; color: {color} !important;"
            f"background: color-mix(in srgb, {color} 16%, transparent) !important; }}"
            f'.st-key-hl [role="toolbar"] > button:nth-of-type({nth}) p {{ color: {color}; }}'
        )
    st.html(f"<style>{''.join(chip_css)}</style>")
    if st.session_state.pop("hl_capped", False):
        st.toast(f"Up to {MAX_HIGHLIGHT} companies at once. Remove one to add another.")

with right, ui.card("ranked"):
    ui.card_title("Who is worth the most right now?", "Latest mark. Bar colour = stage.")
    st.markdown(ui.stage_legend(), unsafe_allow_html=True)
    top_n = marked.head(15)
    peak = top_n.latest_valuation_usd.max()
    rows = []
    for n, row in enumerate(top_n.itertuples(), start=1):
        width = max(row.latest_valuation_usd / peak * 100, 1.5)
        color = ui.STAGE_COLORS.get(row.stage, ui.MUTED)
        if pd.isna(row.change_1y):
            chg = '<span class="flat">new</span>'
        elif row.change_1y == 0:
            chg = '<span class="flat">no new mark in 1y</span>'
        else:
            tone = ui.UP if row.change_1y > 0 else ui.DOWN
            chg = (
                f'<span style="color:{tone};font-size:.75rem">{ui.change(row.change_1y)} 1y</span>'
            )
        rows.append(
            f'<li><div class="row"><span class="name"><span class="n">{n}</span>'
            f"{html.escape(row.company_name)}</span>"
            f'<span class="val">{ui.money(row.latest_valuation_usd)}</span></div>'
            f'<div class="row2"><div class="bar">'
            f'<span style="width:{width:.1f}%;background:{color}"></span></div>'
            f'<div class="meta">{ui.stage_pill(row.stage)}'
            f'{ui.ipo_pill() if row.ipo_watch else ""}{chg}</div></div></li>'
        )
    st.markdown(f'<ul class="rank">{"".join(rows)}</ul>', unsafe_allow_html=True)

# --- leaderboard ---------------------------------------------------------
with ui.card("leaderboard"):
    ui.card_title(
        "Leaderboard",
        "Every tracked company, ranked by latest mark. Click a row to see its full funding "
        "history and the source behind each figure.",
    )
    last_round = (
        marked.last_round_stage.fillna("Round")
        + " · "
        + pd.to_datetime(marked.last_round_date).dt.strftime("%b %Y")
    )
    table = pd.DataFrame(
        {
            "company_id": marked.company_id,
            "#": range(1, len(marked) + 1),
            "Company": marked.company_name,
            "Sector": marked.sector,
            "Stage": marked.stage + np.where(marked.ipo_watch, " · IPO watch", ""),
            "Valuation": marked.latest_valuation_usd / 1e9,
            "1-yr change": marked.change_1y * 100,
            "Trajectory": marked.valuation_path_b,
            "Last round": last_round,
            "Last raise": pd.to_numeric(marked.last_raise_usd, errors="coerce") / 1e9,
            "Raised (tracked)": pd.to_numeric(marked.raised_tracked_usd, errors="coerce") / 1e9,
            "Mark age": marked.days_since_mark,
            "Source": marked.mark_source_url,
        }
    )
    event = st.dataframe(
        table,
        hide_index=True,
        column_order=[c for c in table.columns if c != "company_id"],
        column_config={
            "#": st.column_config.NumberColumn(width=36, pinned=True),
            "Company": st.column_config.TextColumn(width="medium", pinned=True),
            "Stage": st.column_config.TextColumn(width=125),
            "Valuation": st.column_config.NumberColumn(
                format="$%.1fB", help="Latest post-money mark"
            ),
            "1-yr change": st.column_config.NumberColumn(
                format="%+.0f%%", help="Latest mark vs the mark in force exactly 365 days ago"
            ),
            "Trajectory": st.column_config.LineChartColumn(y_min=0, width="small"),
            "Last raise": st.column_config.NumberColumn(format="$%.2fB", width="small"),
            "Raised (tracked)": st.column_config.NumberColumn(
                format="$%.1fB", help="Sum of the rounds in this dataset, not necessarily all-time"
            ),
            "Mark age": st.column_config.NumberColumn(
                format="%d days", help="Days since the last priced round"
            ),
            "Source": st.column_config.LinkColumn(display_text=r"https?://(?:www\.)?([^/]+).*"),
        },
        on_select="rerun",
        selection_mode="single-row",
        key="lb_table",
        placeholder="—",
        height=min(35 * (len(table) + 1) + 4, 620),
    )
    if event.selection.rows:
        chosen = table.iloc[event.selection.rows[0]].company_id
        st.switch_page("views/company.py", query_params={"company": chosen})

# --- summary (drawn at the top) -------------------------------------------
by_sector = marked.groupby("sector").latest_valuation_usd.sum().sort_values(ascending=False)
lead_sector, lead_share = by_sector.index[0], by_sector.iloc[0] / total
fastest = marked.head(10).dropna(subset=["change_1y"]).sort_values("change_1y", ascending=False)
stale = marked[marked.days_since_mark > 365]
ipo = marked[marked.ipo_watch]


def name_list(df: pd.DataFrame, n: int = 3) -> str:
    shown = ", ".join(html.escape(x) for x in df.company_name.head(n))
    return shown + (f" +{len(df) - n} more" if len(df) > n else "")


# Each tile's wording adapts to the filtered view, so it never states something
# trivially true ("100% of value is in Fintech" when only Fintech is shown).
if by_sector.size > 1:
    concentration = (
        f"{lead_share:.0%}",
        f"of combined value sits in <b>{html.escape(lead_sector)}</b>. The top two companies "
        f"alone hold {top2:.0%}, so sector totals mostly reflect a couple of names.",
    )
elif len(marked) > 1:
    concentration = (
        f"{top2:.0%}",
        f"of combined value in this view is just two companies "
        f"({name_list(marked.head(2), 2)}). One or two marks drive the totals.",
    )
else:
    concentration = ("1", "company in view, so there is no concentration to measure.")
tiles = [(*concentration, ui.SERIES[0])]

if len(fastest) and fastest.iloc[0].change_1y > 0:
    fx = fastest.iloc[0]
    tiles.append(
        (
            ui.change(fx.change_1y),
            f"<b>{html.escape(fx.company_name)}</b>'s mark vs a year ago: the fastest "
            "compounder in the top 10.",
            ui.UP,
        )
    )
tiles.append(
    (
        str(len(stale)),
        f"marks are over a year old ({name_list(stale)}). Those headline numbers are the "
        "least reliable guide to what shares trade for today."
        if len(stale)
        else "marks are over a year old: everything in view has been priced in the last "
        "12 months, so the headline numbers are fairly current.",
        ui.DOWN,
    )
)
tiles.append(
    (
        str(len(ipo)),
        f"companies with a sourced IPO signal ({name_list(ipo)}). For a secondary buyer, "
        "these are the nearest likely exits."
        if len(ipo)
        else "companies in view have a sourced IPO signal, so no near-term exit is on record.",
        ui.IPO,
    )
)
filtered = bool(pick_sectors or pick_stages or ipo_only or show_exited)
with summary_slot:
    ui.summary(tiles, note="Reflects the current filters." if filtered else None)
