"""Load the curated seed data, then build SCD2 history from it.

The seed CSV is the hand-verified backbone of the dataset. Every row has a
source_url someone can click. Automated extraction adds to this later, but
never silently overwrites it - curated rows always win.
"""

import csv
import logging

from pcd.db import connect, run_sql_file
from pcd.settings import SEED_CSV, load_companies

log = logging.getLogger(__name__)


def _to_int(value: str | None) -> int | None:
    """Empty CSV cell -> NULL, not 0. A missing valuation is not a zero valuation."""
    value = (value or "").strip()
    return int(value) if value else None


def load_ref_company() -> int:
    """Push config/companies.yml into ref_company so SQL can join against it."""
    companies = load_companies()
    with connect() as conn:
        for c in companies:
            conn.execute(
                """
                INSERT INTO ref_company (company_id, company_name, sector, subsector,
                                         hq_country, fp_risk, status, status_note,
                                         status_source_url)
                VALUES (%(id)s, %(name)s, %(sector)s, %(subsector)s,
                        %(hq)s, %(fp_risk)s, %(status)s, %(status_note)s,
                        %(status_url)s)
                ON CONFLICT (company_id) DO UPDATE SET
                    company_name = EXCLUDED.company_name,
                    sector       = EXCLUDED.sector,
                    subsector    = EXCLUDED.subsector,
                    hq_country   = EXCLUDED.hq_country,
                    fp_risk      = EXCLUDED.fp_risk,
                    status       = EXCLUDED.status,
                    status_note  = EXCLUDED.status_note,
                    status_source_url = EXCLUDED.status_source_url,
                    loaded_at    = now()
                """,
                {
                    "id": c.id,
                    "name": c.display_name,
                    "sector": c.sector,
                    "subsector": c.subsector,
                    "hq": c.hq_country,
                    "fp_risk": c.fp_risk,
                    "status": c.status,
                    "status_note": c.status_note,
                    "status_url": c.status_source_url,
                },
            )
    log.info("loaded %d companies into ref_company", len(companies))
    return len(companies)


def load_seed_rounds() -> int:
    """Load valuations_seed.csv into fct_funding_round.

    Idempotent: re-running updates existing rows rather than duplicating them,
    keyed on (company_id, announced_date, round_stage).
    """
    with SEED_CSV.open() as f:
        rows = list(csv.DictReader(f))

    with connect() as conn:
        for row in rows:
            conn.execute(
                """
                INSERT INTO fct_funding_round (
                    company_id, announced_date, round_stage,
                    amount_raised_usd, post_money_usd, lead_investor,
                    source_url, confidence, extraction_method, reviewed_by_human
                )
                VALUES (
                    %(company_id)s, %(announced_date)s, %(round_stage)s,
                    %(amount_raised_usd)s, %(post_money_usd)s, %(lead_investor)s,
                    %(source_url)s, 'curated', 'seed_csv', TRUE
                )
                ON CONFLICT (company_id, announced_date, round_stage) DO UPDATE SET
                    amount_raised_usd = EXCLUDED.amount_raised_usd,
                    post_money_usd    = EXCLUDED.post_money_usd,
                    lead_investor     = EXCLUDED.lead_investor,
                    source_url        = EXCLUDED.source_url
                """,
                {
                    "company_id": row["company_id"],
                    "announced_date": row["announced_date"],
                    "round_stage": row["round_stage"] or None,
                    "amount_raised_usd": _to_int(row["amount_raised_usd"]),
                    "post_money_usd": _to_int(row["post_money_usd"]),
                    "lead_investor": row["lead_investor"] or None,
                    "source_url": row["source_url"],
                },
            )
    log.info("loaded %d seed rounds", len(rows))
    return len(rows)


# Only rounds that actually disclosed a valuation move the dimension. A round
# with an undisclosed post-money is still a real funding event (it stays in
# fct_funding_round) but it gives us no new valuation mark, so the company's
# last known valuation stands.
_STAGE_ONE_DATE = """
    INSERT INTO stg_company_current (
        company_id, company_name, sector, hq_country, founded_year,
        latest_valuation_usd, latest_round_stage, valuation_as_of, effective_date
    )
    SELECT DISTINCT ON (f.company_id)
           f.company_id,
           r.company_name,
           r.sector,
           r.hq_country,
           NULL::int,
           f.post_money_usd,
           f.round_stage,
           f.announced_date,
           f.announced_date
    FROM fct_funding_round f
    JOIN ref_company r ON r.company_id = f.company_id
    WHERE f.announced_date = %(as_of)s
      AND f.post_money_usd IS NOT NULL
    -- A company can announce two things on one day (Rippling did: a Series G
    -- and a companion tender offer at the same price). Take the larger
    -- valuation, and break any remaining tie deterministically.
    ORDER BY f.company_id, f.post_money_usd DESC, f.round_stage;
"""


def bootstrap_dim_company() -> int:
    """Replay funding history in date order to build the SCD2 dimension.

    Deliberately reuses the SAME upsert that ongoing loads use, one historical
    date at a time. If the replay and the live path used different logic, a bug
    in one wouldn't show up in the other.
    """
    with connect() as conn:
        dates = [
            r["announced_date"]
            for r in conn.execute(
                """
                SELECT DISTINCT announced_date
                FROM fct_funding_round
                WHERE post_money_usd IS NOT NULL
                ORDER BY announced_date
                """
            ).fetchall()
        ]

    log.info("replaying %d historical dates", len(dates))
    for as_of in dates:
        with connect() as conn:
            # psycopg won't accept multiple statements alongside parameters,
            # so the truncate and the insert go separately.
            conn.execute("TRUNCATE stg_company_current")
            conn.execute(_STAGE_ONE_DATE, {"as_of": as_of})
        run_sql_file("02_scd2_upsert.sql")

    return len(dates)
