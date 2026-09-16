"""Command line entry point.

    python -m pcd.cli setup     # create tables
    python -m pcd.cli load-seed # config + curated rounds + SCD2 history
    python -m pcd.cli check     # data quality gate (exits 1 on failure)
    python -m pcd.cli status    # what's in the database right now
"""

import argparse
import logging
import sys

from pcd import checks
from pcd.db import fetch_all, fetch_one, run_sql_file
from pcd.transform.load_seed import bootstrap_dim_company, load_ref_company, load_seed_rounds


def cmd_setup() -> int:
    run_sql_file("01_schema.sql")
    print("schema applied")
    return 0


def cmd_load_seed() -> int:
    n_companies = load_ref_company()
    print(f"ref_company:        {n_companies} companies")
    n_rounds = load_seed_rounds()
    print(f"fct_funding_round:  {n_rounds} rounds")
    n_dates = bootstrap_dim_company()
    print(f"dim_company:        replayed {n_dates} historical dates")
    versions = fetch_one("SELECT COUNT(*) AS n FROM dim_company")
    print(f"                    {versions['n']} SCD2 versions created")
    return 0


def cmd_ingest() -> int:
    """One pass over the GDELT funding firehose.

    Cheap by design: a single API request per run. Meant to be run on a
    schedule - GDELT's window is only minutes wide, so frequency beats depth.
    """
    from pcd.ingest.gdelt import FIREHOSE_QUERY, fetch_funding_firehose, sentences, to_articles
    from pcd.ingest.store import ingest_payload

    payload = fetch_funding_firehose()
    stats = ingest_payload(
        payload,
        to_articles(payload),
        sentences(payload),
        source="gdelt_context",
        query_key=FIREHOSE_QUERY,
    )
    print(stats)
    return 0


def cmd_check() -> int:
    results = checks.run_all()
    failed = [r for r in results if not r.passed]
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        print(f"[{mark}] {r.name}: {r.detail}")
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


def cmd_status() -> int:
    rows = fetch_all(
        """
        SELECT r.status,
               COUNT(DISTINCT r.company_id)  AS companies,
               COUNT(f.round_id)             AS rounds
        FROM ref_company r
        LEFT JOIN fct_funding_round f ON f.company_id = r.company_id
        GROUP BY r.status
        ORDER BY r.status
        """
    )
    for row in rows:
        print(f"{row['status']:<10} {row['companies']:>3} companies  {row['rounds']:>4} rounds")

    top = fetch_all(
        """
        SELECT company_name, latest_valuation_usd, valuation_as_of
        FROM dim_company
        WHERE is_current AND latest_valuation_usd IS NOT NULL
        ORDER BY latest_valuation_usd DESC
        LIMIT 10
        """
    )
    if top:
        print("\ntop 10 by current valuation:")
        for row in top:
            # Postgres NUMERIC comes back as Decimal, which won't divide by a
            # float. Cast for display only - the stored value stays exact.
            billions = float(row["latest_valuation_usd"]) / 1e9
            name = row["company_name"]
            print(f"  {name:<24} ${billions:>8,.1f}B   as of {row['valuation_as_of']}")
    return 0


COMMANDS = {
    "setup": cmd_setup,
    "load-seed": cmd_load_seed,
    "ingest": cmd_ingest,
    "check": cmd_check,
    "status": cmd_status,
}


def main() -> int:
    parser = argparse.ArgumentParser(prog="pcd")
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return COMMANDS[args.command]()


if __name__ == "__main__":
    sys.exit(main())
