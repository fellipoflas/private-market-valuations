"""Shared look-and-feel and helpers for every dashboard page.

Everything visual that more than one page needs lives here: the palette,
chart chrome, number formatting, the card wrapper and the cached data loader.
"""

import html
import math
from decimal import Decimal
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from pcd.db import fetch_analysis
from pcd.transform.stages import STAGE_ORDER, stage_bucket

# --- palette -------------------------------------------------------------
# Checked against the card surface for >=3:1 contrast. The first six are also
# separable under protanopia/deuteranopia (worst pair dE ~25); slots 7-10 are
# only separable with normal colour vision, so the chart draws those dashed and
# every line carries a direct label. Slot order matters: rank 1 gets slot 1.
SERIES = [
    "#4ea8ff", "#ff7f50", "#2ed6a1", "#ffd84d", "#e8edf5", "#8fd3ff",
    "#c39bff", "#ff8fc8", "#b7e36b", "#d4a373",
]  # fmt: skip
CVD_SAFE_SLOTS = 6

# Ordered stages get an ordered-feeling but distinct trio, always with a text label.
STAGE_COLORS = {"Early": "#5b8def", "Growth": "#2ec4b6", "Late": "#ffb347"}
UP, DOWN = "#2ed6a1", "#ff6b6b"
IPO = "#ffd84d"

PAGE, CARD, CARD_2, BORDER = "#0d1726", "#152238", "#1b2a44", "#24344d"
INK, MUTED, CONTEXT = "#e7edf5", "#8b9bb2", "#3a4d6b"
FONT = "Inter, system-ui, -apple-system, sans-serif"

DECISION = (
    "Which private companies are worth the most, how fast are they compounding, "
    "and which marks are stale or nearing an IPO exit?"
)


def inject_css() -> None:
    css = (Path(__file__).parent / "style.css").read_text()
    st.html(f"<style>{css}</style>")


# --- data ----------------------------------------------------------------
@st.cache_data(ttl=600, show_spinner=False)
def load(name: str, **params) -> pd.DataFrame:
    df = fetch_analysis(name, params or None)
    # Postgres NUMERIC arrives as Decimal; charts and arithmetic want floats.
    for col in df.columns:
        sample = df[col].dropna()
        if df[col].dtype == object and len(sample) and isinstance(sample.iloc[0], Decimal):
            df[col] = df[col].astype(float)
    return df


def leaderboard() -> pd.DataFrame:
    lb = load("leaderboard").copy()
    lb["stage"] = lb.last_round_stage.map(stage_bucket)
    lb["ipo_watch"] = lb.ipo_watch_url.notna()
    lb["valuation_path_b"] = lb.valuation_path_b.map(lambda a: [float(x) for x in a] if a else [])
    return lb


def header_chip() -> str:
    """'Data through <date> · news pipeline last ran <when>' - freshness, always visible."""
    rounds = load("funding_rounds")
    runs = load("pipeline_runs")
    through = pd.to_datetime(rounds.announced_date).max()
    ok = runs[(runs.command == "ingest") & (runs.status == "success")]
    last = ok.finished_at.max() if len(ok) else None
    fresh = last is not None and (pd.Timestamp.now(tz="UTC") - last).total_seconds() < 48 * 3600
    dot = f'<span class="dot" style="background:{UP if fresh else IPO}"></span>'
    return (
        f"{dot}Data through <b>{through:%b %d, %Y}</b> · news pipeline last ran <b>{ago(last)}</b>"
    )


# --- formatting ----------------------------------------------------------
def money(value, digits: int | None = None) -> str:
    """$965B, $1.2T, $450M. Sig-fig aware so small and huge values both read cleanly."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    v = float(value)
    for div, unit in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(v) >= div:
            x = v / div
            d = (
                digits
                if digits is not None
                else (2 if x < 10 and unit == "T" else 1 if x < 100 else 0)
            )
            return f"${x:,.{d}f}{unit}"
    return f"${v:,.0f}"


def change(value) -> str:
    """Year-over-year change: +43% for normal moves, 5.3x once something has doubled.

    "+427%" is technically right but hard to read; multiples are how people talk
    about valuations that have gone up several times over.
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    return f"{value + 1:.1f}×" if value >= 1 else f"{value:+.0%}"


def pct(value, signed: bool = True) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    return f"{value:+.0%}" if signed else f"{value:.0%}"


def ago(ts) -> str:
    """'3 hours ago' style, for pipeline timestamps."""
    if ts is None or pd.isna(ts):
        return "never"
    secs = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(ts).tz_convert("UTC")).total_seconds()
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if secs >= size:
            n = int(secs // size)
            return f"{n} {unit}{'s' if n != 1 else ''} ago"
    return "just now"


def domain(url: str | None) -> str:
    if not url:
        return ""
    return url.split("//")[-1].split("/")[0].removeprefix("www.")


# --- layout pieces -------------------------------------------------------
def card(key: str):
    """A bordered, filled panel. The key gives CSS a stable class to style."""
    return st.container(border=True, key=f"card_{key}")


def card_title(question: str, subtitle: str | None = None) -> None:
    """Every chart is titled with the question it answers."""
    st.markdown(f'<div class="card-title">{html.escape(question)}</div>', unsafe_allow_html=True)
    if subtitle:
        st.markdown(f'<div class="card-sub">{subtitle}</div>', unsafe_allow_html=True)


def page_header(
    title: str, subtitle: str, chip: str | None = None, eyebrow: str | None = None
) -> None:
    """Page title block. Passing an eyebrow marks it as the app's main page: bigger title."""
    chip_html = f'<div class="chip">{chip}</div>' if chip else ""
    brow = f'<div class="eyebrow">{html.escape(eyebrow)}</div>' if eyebrow else ""
    cls = "page-head hero" if eyebrow else "page-head"
    st.markdown(
        f'<div class="{cls}"><div>{brow}<h1>{html.escape(title)}</h1>'
        f'<p class="lede">{subtitle}</p></div>{chip_html}</div>',
        unsafe_allow_html=True,
    )


def stage_pill(stage: str | None) -> str:
    if not stage:
        return ""
    color = STAGE_COLORS.get(stage, MUTED)
    return f'<span class="pill" style="--c:{color}">{html.escape(stage)}</span>'


def ipo_pill() -> str:
    return f'<span class="pill pill-ipo" style="--c:{IPO}">IPO watch</span>'


def stage_legend() -> str:
    return " ".join(stage_pill(s) for s in STAGE_ORDER) + " " + ipo_pill()


def summary(tiles: list[tuple[str, str, str]], note: str | None = None) -> None:
    """The page's conclusions as big-number tiles: (stat, sentence, accent colour).

    Sits at the top of the page. A reader who stops here should still leave
    with the main findings.
    """
    cells = "".join(
        f'<div class="tile" style="--c:{color}"><div class="stat">{html.escape(stat)}</div>'
        f'<div class="txt">{text}</div></div>'
        for stat, text, color in tiles
    )
    note_html = f'<span class="summary-note">{html.escape(note)}</span>' if note else ""
    st.markdown(
        f'<div class="summary"><div class="summary-h">What this tells a buyer{note_html}</div>'
        f'<div class="tiles">{cells}</div></div>',
        unsafe_allow_html=True,
    )


# --- charts --------------------------------------------------------------
def style(fig: go.Figure, height: int = 420, xtitle: str = "", ytitle: str = "") -> go.Figure:
    """Shared chart chrome: transparent so the card shows through, recessive grid."""
    fig.update_layout(
        height=height,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, size=13, color=MUTED),
        margin=dict(l=8, r=16, t=8, b=8),
        hoverlabel=dict(
            bgcolor=CARD_2, bordercolor=BORDER, font=dict(family=FONT, size=13, color=INK)
        ),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title="", font=dict(color=INK)),
        showlegend=False,
    )
    axis = dict(
        gridcolor=BORDER,
        zeroline=False,
        linecolor=BORDER,
        tickfont=dict(color=MUTED),
        title_font=dict(color=MUTED, size=12),
    )
    fig.update_xaxes(title=xtitle, automargin=True, **axis)
    fig.update_yaxes(title=ytitle, automargin=True, **axis)
    return fig


def plot(fig: go.Figure, key: str | None = None) -> None:
    # theme=None: we style charts ourselves; Streamlit's theme would override colours.
    st.plotly_chart(fig, theme=None, key=key, config={"displayModeBar": False})


def spread_labels(ys: list[float], min_gap: float, hi: float | None = None) -> list[float]:
    """Nudge end-of-line labels apart so they never overlap.

    Works in whatever units the axis uses (log10 units for a log axis). Labels
    keep their order; each is pushed up just enough to clear the one below.
    If that pushes the top label past `hi` (the top of the chart), the stack is
    pushed back down from the top instead.
    """
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    out = list(ys)
    for prev, cur in zip(order, order[1:], strict=False):
        if out[cur] - out[prev] < min_gap:
            out[cur] = out[prev] + min_gap
    if hi is not None and order and out[order[-1]] > hi:
        out[order[-1]] = hi
        down = order[::-1]
        for above, cur in zip(down, down[1:], strict=False):
            if out[above] - out[cur] < min_gap:
                out[cur] = out[above] - min_gap
    return out
