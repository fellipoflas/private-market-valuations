"""Pipeline & data quality: is the data fresh, does it pass its checks, and how was it built?"""

import html

import pandas as pd
import streamlit as st
import ui

from pcd import checks

ui.page_header(
    "Pipeline & data quality",
    "How fresh the data is, whether it passes its checks, and exactly how every metric on this "
    "dashboard is defined.",
    ui.header_chip(),
)

runs = ui.load("pipeline_runs")
links = ui.load("source_links")
news = ui.load("news_mentions")
runs["started_at"] = pd.to_datetime(runs.started_at, utc=True)
week = runs[runs.started_at > pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=7)]
ingest_ok = runs[(runs.command == "ingest") & (runs.status == "success")]

# --- status cards --------------------------------------------------------
k = st.columns(5)
k[0].metric(
    "Last successful ingest",
    ui.ago(ingest_ok.finished_at.max() if len(ingest_ok) else None),
    border=True,
    help="Most recent GDELT funding-news pass that completed without error.",
)
k[1].metric("Runs · 7 days", len(week), border=True)
k[2].metric(
    "Failed runs · 7 days", int((week.status == "failed").sum()), delta_color="inverse", border=True
)
k[3].metric(
    "News mentions captured",
    len(news),
    delta=f"{int(news.is_funding_related.sum())} funding-related",
    delta_color="off",
    delta_arrow="off",
    border=True,
)
k[4].metric(
    "Sources reachable",
    f"{int(links.reachable.sum())}/{len(links)}" if len(links) else "Not checked",
    delta=f"checked {ui.ago(links.checked_at.max())}" if len(links) else "run pcd.cli check-links",
    delta_color="off",
    delta_arrow="off",
    border=True,
)

# --- checks --------------------------------------------------------------
results = checks.run_all()
errors = [r for r in results if not r.passed and r.severity == "error"]
warns = [r for r in results if not r.passed and r.severity == "warn"]


def check_rows(group: str) -> str:
    out = []
    for r in (r for r in results if r.group == group):
        if r.passed:
            badge, color = "PASS", ui.UP
        elif r.severity == "warn":
            badge, color = "WARN", ui.IPO
        else:
            badge, color = "FAIL", ui.DOWN
        detail = "" if r.passed else f'<div class="dt">{html.escape(r.detail[:400])}</div>'
        doc = (getattr(checks, r.name).__doc__ or "").strip().split("\n")[0]
        out.append(
            f'<div class="status-row"><div><div class="nm">{r.name}</div>'
            f'<div class="dt">{html.escape(doc)}</div>{detail}</div>'
            f'<span class="pill" style="--c:{color};height:fit-content">{badge}</span></div>'
        )
    return "".join(out)


summary = (
    f"{len(results) - len(errors) - len(warns)} of {len(results)} checks pass · "
    f"{len(warns)} warning{'s' * (len(warns) != 1)} · "
    f"{len(errors)} error{'s' * (len(errors) != 1)}. "
    "Errors fail CI; warnings are flagged for a human."
)
left, right = st.columns(2, gap="medium")
with left, ui.card("checks_struct"):
    ui.card_title("Is the model internally consistent?", f"Structural checks. {summary}")
    st.markdown(check_rows("structural"), unsafe_allow_html=True)
with right, ui.card("checks_acc"):
    ui.card_title(
        "Do the numbers look like real financial facts?",
        "Accuracy checks. Passing every database constraint doesn't make a figure correct, so "
        "these test plausibility: impossible dates, implausible jumps, duplicate entries, "
        "unsourced claims and a silently dead pipeline.",
    )
    st.markdown(check_rows("accuracy"), unsafe_allow_html=True)

# --- runs + links --------------------------------------------------------
left, right = st.columns([1.3, 1], gap="medium")
with left, ui.card("runs"):
    ui.card_title(
        "What has the pipeline done recently?",
        "Every command logs a row to <code>etl_run</code> before it starts and updates it when it "
        "finishes, so a crashed run stays visible as 'running' or 'failed'.",
    )
    if runs.empty:
        st.markdown('<p class="card-sub">No runs logged yet.</p>', unsafe_allow_html=True)
    else:
        st.dataframe(
            runs[
                [
                    "started_at",
                    "command",
                    "status",
                    "duration_s",
                    "rows_fetched",
                    "rows_inserted",
                    "error",
                ]
            ].head(25),
            hide_index=True,
            placeholder="—",
            column_config={
                "started_at": st.column_config.DatetimeColumn("Started", format="MMM D, h:mm a"),
                "command": "Command",
                "status": "Status",
                "duration_s": st.column_config.NumberColumn("Duration", format="%.1f s"),
                "rows_fetched": "Fetched",
                "rows_inserted": "Inserted",
                "error": st.column_config.TextColumn("Error", width="medium"),
            },
        )
with right, ui.card("links"):
    ui.card_title(
        "Do the cited sources still load?",
        "Result of the last link check. Some publishers block automated requests even when "
        "the page is fine, so 'check' means a human should look, not that the figure is wrong.",
    )
    if links.empty:
        st.markdown(
            '<p class="card-sub">Not checked yet. Run '
            "<code>python -m pcd.cli check-links</code>.</p>",
            unsafe_allow_html=True,
        )
    else:
        by_domain = (
            links.groupby("domain")
            .agg(sources=("source_url", "count"), reachable=("reachable", "sum"))
            .sort_values("sources", ascending=False)
            .reset_index()
        )
        st.dataframe(
            by_domain,
            hide_index=True,
            height=300,
            column_config={
                "domain": "Publisher",
                "sources": "Sources",
                "reachable": st.column_config.ProgressColumn(
                    "Loads", format="%d", min_value=0, max_value=int(by_domain.sources.max())
                ),
            },
        )

# --- news ----------------------------------------------------------------
with ui.card("news"):
    ui.card_title(
        "What has the news pipeline captured?",
        "Articles from the scheduled GDELT funding-news query, deduplicated by canonical URL and "
        "then by near-identical headline (wire syndication produces many copies). GDELT's search "
        "window is only ~15 minutes wide, so this table can only grow forward, never backfill.",
    )
    if news.empty:
        st.markdown('<p class="card-sub">No mentions captured yet.</p>', unsafe_allow_html=True)
    else:
        st.dataframe(
            pd.DataFrame(
                {
                    "Published": pd.to_datetime(news.published_at),
                    "Company": news.company_name,
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

# --- methodology ---------------------------------------------------------
lb = ui.leaderboard()
exited = lb[lb.status != "private"]
with ui.card("method"):
    ui.card_title("How is every metric defined?")
    st.markdown(
        """
<div class="method">
<h4>Valuation (the "mark")</h4>
<p>The post-money valuation from the company's most recent priced event: a funding round, or a
tender offer / secondary sale that set a price. It is a point-in-time mark, not a live price.
Rounds that didn't disclose a valuation are kept as funding events but don't move the mark.</p>

<h4>1-year change</h4>
<p>Latest mark vs the mark that was in force exactly 365 days ago, read from the Type 2
dimension with a point-in-time join. "No new mark in 1y" means no priced round happened.</p>

<h4>Step-up and down rounds</h4>
<p>Step-up = a round's post-money ÷ the previous priced round's post-money. Below 1.0× is a down
round. Median step-up and activity-by-year include companies that later exited, to avoid
survivorship bias.</p>

<h4>Combined latest marks</h4>
<p>The sum of every tracked company's latest mark: the size of this list, <b>not</b> a portfolio
value. Nobody owns 100% of these companies.</p>

<h4>Sectors</h4>
<p>One sector per company, mutually exclusive, assigned by hand in <code>config/companies.yml</code>
(e.g. Anduril is Defense &amp; Space, not AI). Totals never double-count.</p>

<h4>Stage</h4>
<p>From the latest round label: Seed–Series B → Early; Series C–D → Growth; Series E+ → Late.
Unlettered late labels ("Growth", "Tender Offer", "Secondary") → Late, because in this dataset
they only occur at late stage.</p>

<h4>IPO watch</h4>
<p>Only set when a published source reports concrete preparation: banks hired, a confidential
filing, or an executive stating a timeline. Each flag links to its source; speculation
doesn't count.</p>

<h4>Mark age</h4>
<p>Days since the last priced round. Under 6 months is fresh, 6–18 months aging,
over 18 months stale.</p>

<h4>Why these numbers differ from Yahoo Finance</h4>
<p>Yahoo's private-company page shows an "Estimated Valuation", an estimate rather than the last
disclosed round. This dashboard only shows valuations actually set by a disclosed round, each
linked to its source. The gap between the two is itself useful: a secondary buyer is pricing
exactly that difference.</p>

<h4>Why there is no free data feed</h4>
<p>No free structured source of private valuations exists: Crunchbase closed its free API, and
SEC Form D reports capital raised but never valuation. So history is hand-curated with a source
on every row, and new rounds are detected from news going forward.</p>
</div>
""",
        unsafe_allow_html=True,
    )
    if len(exited):
        # HTML, not markdown: "$250B ... $60B" in a note would otherwise render as LaTeX
        notes = "; ".join(
            f"<b>{html.escape(r.company_name)}</b> ({html.escape(r.status_note or r.status)})"
            for r in exited.itertuples()
        )
        st.markdown(
            f'<div class="method"><h4>No longer private</h4><p>{notes}. Their private history '
            "stays in the data; they're excluded from default views.</p></div>",
            unsafe_allow_html=True,
        )
