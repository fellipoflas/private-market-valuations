"""Command line entry point.

    python -m pcd.cli setup     # create tables
    python -m pcd.cli load-seed # config + curated rounds + SCD2 history
    python -m pcd.cli ingest      # one pass over the GDELT firehose
    python -m pcd.cli check       # data quality gate (exits 1 on any error-level failure)
    python -m pcd.cli check-links # HTTP-check every source_url (slow, run on demand)
    python -m pcd.cli status      # what's in the database right now
"""

import argparse
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager

from pcd import checks
from pcd.db import connect, fetch_all, fetch_one, run_sql_file
from pcd.transform.load_seed import bootstrap_dim_company, load_ref_company, load_seed_rounds


@contextmanager
def tracked_run(command: str) -> Iterator[dict]:
    """Log a pipeline run to etl_run: a 'running' row up front, then success or failure.

    Writing the row *before* the work starts matters - if the process gets killed
    mid-run, the row stays 'running' forever, which is itself a visible signal.
    """
    row = fetch_one(
        "INSERT INTO etl_run (command) VALUES (%(c)s) RETURNING run_id", {"c": command}
    )
    counts: dict = {"fetched": None, "inserted": None}
    try:
        yield counts
    except Exception as exc:
        with connect() as conn:
            conn.execute(
                """UPDATE etl_run SET status = 'failed', finished_at = now(), error = %(e)s
                   WHERE run_id = %(id)s""",
                {"e": f"{type(exc).__name__}: {exc}"[:2000], "id": row["run_id"]},
            )
        raise
    with connect() as conn:
        conn.execute(
            """UPDATE etl_run SET status = 'success', finished_at = now(),
                      rows_fetched = %(f)s, rows_inserted = %(i)s
               WHERE run_id = %(id)s""",
            {"f": counts["fetched"], "i": counts["inserted"], "id": row["run_id"]},
        )


def cmd_setup() -> int:
    run_sql_file("01_schema.sql")
    print("schema applied")
    return 0


def cmd_load_seed() -> int:
    with tracked_run("load-seed") as counts:
        n_companies = load_ref_company()
        print(f"ref_company:        {n_companies} companies")
        n_rounds = load_seed_rounds()
        print(f"fct_funding_round:  {n_rounds} rounds")
        n_dates = bootstrap_dim_company()
        print(f"dim_company:        replayed {n_dates} historical dates")
        versions = fetch_one("SELECT COUNT(*) AS n FROM dim_company")
        print(f"                    {versions['n']} SCD2 versions created")
        counts["fetched"], counts["inserted"] = n_rounds, versions["n"]
    return 0


def cmd_ingest() -> int:
    """One pass over the GDELT funding firehose.

    Cheap by design: a single API request per run. Meant to be run on a
    schedule - GDELT's window is only minutes wide, so frequency beats depth.
    """
    from pcd.ingest.gdelt import FIREHOSE_QUERY, fetch_funding_firehose, sentences, to_articles
    from pcd.ingest.store import ingest_payload

    with tracked_run("ingest") as counts:
        payload = fetch_funding_firehose()
        stats = ingest_payload(
            payload,
            to_articles(payload),
            sentences(payload),
            source="gdelt_context",
            query_key=FIREHOSE_QUERY,
        )
        counts["fetched"], counts["inserted"] = stats.articles_seen, stats.mentions_inserted
    print(stats)
    return 0


def cmd_check() -> int:
    results = checks.run_all()
    errors = [r for r in results if not r.passed and r.severity == "error"]
    warnings = [r for r in results if not r.passed and r.severity == "warn"]
    for r in results:
        mark = "PASS" if r.passed else ("WARN" if r.severity == "warn" else "FAIL")
        print(f"[{mark}] {r.group:<10} {r.name}: {r.detail}")
    passed = len(results) - len(errors) - len(warnings)
    print(f"\n{passed}/{len(results)} passed, {len(warnings)} warnings, {len(errors)} errors")
    # Warnings are for humans to look at; only errors should break CI.
    return 1 if errors else 0


def cmd_check_links() -> int:
    """Request every distinct source_url and record whether it still resolves.

    Run on demand (or weekly in CI), never on page load - 100+ HTTP requests
    would make the dashboard crawl. Some publishers 403 bots even when the page
    is fine, so 'unreachable' means 'go look', not 'definitely broken'.
    """
    import requests

    urls = [
        r["u"]
        for r in fetch_all(
            """SELECT source_url AS u FROM fct_funding_round
               UNION SELECT ipo_watch_url FROM ref_company WHERE ipo_watch_url IS NOT NULL"""
        )
    ]
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/126"}
    ok = 0
    with tracked_run("check-links") as counts:
        for url in urls:
            status, err = None, None
            try:
                # GET not HEAD: plenty of news sites answer HEAD with 405/403.
                resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
                status = resp.status_code
            except requests.RequestException as exc:
                err = type(exc).__name__
            reachable = status is not None and status < 400
            ok += reachable
            with connect() as conn:
                conn.execute(
                    """
                    INSERT INTO source_link_status (source_url, http_status, reachable, error)
                    VALUES (%(u)s, %(s)s, %(r)s, %(e)s)
                    ON CONFLICT (source_url) DO UPDATE SET
                        http_status = EXCLUDED.http_status, reachable = EXCLUDED.reachable,
                        error = EXCLUDED.error, checked_at = now()
                    """,
                    {"u": url, "s": status, "r": reachable, "e": err},
                )
            print(f"[{status or err}] {url}")
        counts["fetched"], counts["inserted"] = len(urls), ok
    print(f"\n{ok}/{len(urls)} sources reachable")
    return 0


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
    "check-links": cmd_check_links,
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
