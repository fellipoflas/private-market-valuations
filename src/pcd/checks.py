"""Data quality assertions.

This is what dbt's `tests` would do for us if we were using dbt. Each check is
a plain function that returns pass/fail plus a human-readable detail string.
`python -m pcd.cli check` runs them all and exits non-zero on any failure, so
CI fails loudly instead of shipping a broken dashboard.

The SCD2 checks are the important ones. A Type 2 dimension with overlapping
date ranges silently returns duplicate rows for point-in-time queries, and
that's the kind of bug you don't notice until a chart looks subtly wrong.
"""

from collections.abc import Callable
from dataclasses import dataclass

from pcd.db import fetch_all


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str
    # "error" fails CI. "warn" is surfaced on the dashboard but doesn't block -
    # e.g. a 20x step-up is suspicious, not necessarily wrong.
    severity: str = "error"
    # "structural" = the model is internally consistent.
    # "accuracy"   = the numbers themselves look plausible.
    group: str = "structural"


def _expect_no_rows(
    name: str, sql: str, describe: str, severity: str = "error", group: str = "structural"
) -> CheckResult:
    """Generic shape: this query should return nothing. If it returns rows, we have a problem."""
    rows = fetch_all(sql)
    if not rows:
        return CheckResult(name, True, "ok", severity, group)
    # readable for humans on the dashboard: "company_id=rippling, a_date=2025-05-09"
    sample = "; ".join(", ".join(f"{k}={v}" for k, v in r.items()) for r in rows[:3])
    return CheckResult(name, False, f"{len(rows)} {describe}: {sample}", severity, group)


def no_overlapping_scd2() -> CheckResult:
    """Two versions of the same company must never claim the same day."""
    return _expect_no_rows(
        "no_overlapping_scd2",
        """
        SELECT a.company_id, a.valid_from AS a_from, a.valid_to AS a_to,
               b.valid_from AS b_from, b.valid_to AS b_to
        FROM dim_company a
        JOIN dim_company b
          ON a.company_id = b.company_id
         AND a.company_sk < b.company_sk
        WHERE a.valid_from <= b.valid_to
          AND b.valid_from <= a.valid_to
        """,
        "overlapping version pairs",
    )


def exactly_one_current_per_company() -> CheckResult:
    """A company has exactly one 'now'. Enforced by a partial unique index too."""
    return _expect_no_rows(
        "exactly_one_current_per_company",
        """
        SELECT company_id, COUNT(*) FILTER (WHERE is_current) AS n_current
        FROM dim_company
        GROUP BY company_id
        HAVING COUNT(*) FILTER (WHERE is_current) <> 1
        """,
        "companies without exactly one current row",
    )


def no_scd2_gaps() -> CheckResult:
    """Version N must end the day before version N+1 begins - no missing days."""
    return _expect_no_rows(
        "no_scd2_gaps",
        """
        SELECT * FROM (
            SELECT company_id, valid_to,
                   LEAD(valid_from) OVER (PARTITION BY company_id ORDER BY valid_from) AS next_from
            FROM dim_company
        ) t
        WHERE next_from IS NOT NULL
          AND next_from <> valid_to + 1
        """,
        "gaps or overlaps in version history",
    )


def current_rows_are_open_ended() -> CheckResult:
    """is_current and the end-date sentinel must agree with each other."""
    return _expect_no_rows(
        "current_rows_are_open_ended",
        """
        SELECT company_sk, company_id, is_current, valid_to
        FROM dim_company
        WHERE (is_current AND valid_to <> DATE '9999-12-31')
           OR (NOT is_current AND valid_to = DATE '9999-12-31')
        """,
        "rows where is_current disagrees with valid_to",
    )


def valuations_positive() -> CheckResult:
    return _expect_no_rows(
        "valuations_positive",
        """
        SELECT round_id, company_id, amount_raised_usd, post_money_usd
        FROM fct_funding_round
        WHERE post_money_usd <= 0
           OR amount_raised_usd <= 0
           OR amount_raised_usd > post_money_usd
        """,
        "rounds with impossible amounts",
    )


def no_null_source_url() -> CheckResult:
    """Provenance is the whole point. A figure with no source is not usable."""
    return _expect_no_rows(
        "no_null_source_url",
        """
        SELECT round_id, company_id
        FROM fct_funding_round
        WHERE source_url IS NULL OR source_url !~ '^https?://'
        """,
        "rounds without a usable source_url",
    )


def referential_integrity() -> CheckResult:
    """Every fact must point at a company we actually track."""
    return _expect_no_rows(
        "referential_integrity",
        """
        SELECT 'fct_funding_round' AS tbl, f.company_id
        FROM fct_funding_round f
        LEFT JOIN ref_company r ON r.company_id = f.company_id
        WHERE r.company_id IS NULL
        UNION ALL
        SELECT 'fct_news_mention', m.company_id
        FROM fct_news_mention m
        LEFT JOIN ref_company r ON r.company_id = m.company_id
        WHERE r.company_id IS NULL
        UNION ALL
        SELECT 'dim_company', d.company_id
        FROM dim_company d
        LEFT JOIN ref_company r ON r.company_id = d.company_id
        WHERE r.company_id IS NULL
        """,
        "orphaned company_id references",
    )


def no_duplicate_canonical_urls() -> CheckResult:
    """Dedup should have collapsed these before they hit the table."""
    return _expect_no_rows(
        "no_duplicate_canonical_urls",
        """
        SELECT company_id, url_canonical, COUNT(*) AS n
        FROM fct_news_mention
        GROUP BY company_id, url_canonical
        HAVING COUNT(*) > 1
        """,
        "duplicate canonical URLs",
    )


def dimension_matches_facts() -> CheckResult:
    """Each company's current valuation must equal its latest disclosed round.

    This is the end-to-end check: it proves the SCD2 replay actually landed on
    the right answer, not just that the date ranges are tidy.
    """
    return _expect_no_rows(
        "dimension_matches_facts",
        """
        WITH latest AS (
            SELECT DISTINCT ON (company_id)
                   company_id, post_money_usd, announced_date
            FROM fct_funding_round
            WHERE post_money_usd IS NOT NULL
            ORDER BY company_id, announced_date DESC, post_money_usd DESC
        )
        SELECT d.company_id,
               d.latest_valuation_usd AS in_dimension,
               l.post_money_usd       AS in_facts
        FROM dim_company d
        JOIN latest l ON l.company_id = d.company_id
        WHERE d.is_current
          AND d.latest_valuation_usd IS DISTINCT FROM l.post_money_usd
        """,
        "companies whose dimension disagrees with their latest round",
    )


# --- accuracy checks -------------------------------------------------------
# The structural checks above prove the model is consistent with itself. These
# ask a different question: do the numbers look like real financial facts?
# A dashboard can pass every constraint and still be wrong.


def no_future_dated_rounds() -> CheckResult:
    """A round announced tomorrow is a typo, not a scoop."""
    return _expect_no_rows(
        "no_future_dated_rounds",
        """
        SELECT round_id, company_id, announced_date
        FROM fct_funding_round
        WHERE announced_date > CURRENT_DATE
        """,
        "rounds dated in the future",
        group="accuracy",
    )


def implausible_step_up() -> CheckResult:
    """Flag >15x jumps or >80% drops between consecutive priced rounds.

    These can be real (early AI rounds really did 10x+), so it's a warning:
    the point is that a human looked at each one, not that they're banned.
    A units mistake (millions typed as billions) shows up here first.
    """
    return _expect_no_rows(
        "implausible_step_up",
        """
        SELECT company_id, announced_date, post_money_usd, prev,
               ROUND(post_money_usd / prev, 1) AS x
        FROM (
            SELECT company_id, announced_date, post_money_usd,
                   LAG(post_money_usd) OVER (
                       PARTITION BY company_id ORDER BY announced_date
                   ) AS prev
            FROM fct_funding_round
            WHERE post_money_usd IS NOT NULL
        ) t
        WHERE prev IS NOT NULL
          AND (post_money_usd / prev > 15 OR post_money_usd / prev < 0.2)
        """,
        "round-over-round jumps outside 0.2x-15x",
        severity="warn",
        group="accuracy",
    )


def no_near_duplicate_rounds() -> CheckResult:
    """Two rounds for one company within 14 days is usually one round entered twice
    (e.g. reported once as 'Series F' and once as 'Growth')."""
    return _expect_no_rows(
        "no_near_duplicate_rounds",
        """
        SELECT a.company_id, a.announced_date AS a_date, a.round_stage AS a_stage,
               b.announced_date AS b_date, b.round_stage AS b_stage
        FROM fct_funding_round a
        JOIN fct_funding_round b
          ON a.company_id = b.company_id
         AND a.round_id < b.round_id
         AND ABS(a.announced_date - b.announced_date) <= 14
        """,
        "round pairs within 14 days of each other",
        severity="warn",
        group="accuracy",
    )


def sector_assigned() -> CheckResult:
    """Every company needs exactly one sector, or the sector chart silently drops it."""
    return _expect_no_rows(
        "sector_assigned",
        "SELECT company_id FROM ref_company WHERE sector IS NULL OR sector = ''",
        "companies without a sector",
        group="accuracy",
    )


def ipo_watch_has_source() -> CheckResult:
    """An IPO-watch badge is a claim, so it needs a link like every other claim."""
    return _expect_no_rows(
        "ipo_watch_has_source",
        """
        SELECT company_id
        FROM ref_company
        WHERE ipo_watch_note IS NOT NULL
          AND (ipo_watch_url IS NULL OR ipo_watch_url !~ '^https?://')
        """,
        "IPO-watch flags without a source",
        group="accuracy",
    )


def ingest_freshness() -> CheckResult:
    """Catches a silently dead cron: the last successful ingest should be < 48h old."""
    return _expect_no_rows(
        "ingest_freshness",
        """
        SELECT MAX(finished_at) AS last_success
        FROM etl_run
        WHERE command = 'ingest' AND status = 'success'
        HAVING MAX(finished_at) IS NULL OR MAX(finished_at) < now() - INTERVAL '48 hours'
        """,
        "- no successful ingest in the last 48 hours",
        severity="warn",
        group="accuracy",
    )


ALL_CHECKS: list[Callable[[], CheckResult]] = [
    no_overlapping_scd2,
    exactly_one_current_per_company,
    no_scd2_gaps,
    current_rows_are_open_ended,
    valuations_positive,
    no_null_source_url,
    referential_integrity,
    no_duplicate_canonical_urls,
    dimension_matches_facts,
    no_future_dated_rounds,
    implausible_step_up,
    no_near_duplicate_rounds,
    sector_assigned,
    ipo_watch_has_source,
    ingest_freshness,
]


def run_all() -> list[CheckResult]:
    return [check() for check in ALL_CHECKS]
