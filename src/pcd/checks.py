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


def _expect_no_rows(name: str, sql: str, describe: str) -> CheckResult:
    """Generic shape: this query should return nothing. If it returns rows, we have a bug."""
    rows = fetch_all(sql)
    if not rows:
        return CheckResult(name, True, "ok")
    sample = "; ".join(str(dict(r)) for r in rows[:3])
    return CheckResult(name, False, f"{len(rows)} {describe}: {sample}")


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
]


def run_all() -> list[CheckResult]:
    return [check() for check in ALL_CHECKS]
