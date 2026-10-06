"""Thin wrapper over psycopg. Everything that talks to Postgres goes through here."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pandas is only needed by the analysis helper, not the pipeline
    import pandas as pd

import psycopg
from psycopg.rows import dict_row

from pcd.settings import SQL_DIR, get_database_url


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    """Open a connection, commit on success, roll back if anything raises."""
    conn = psycopg.connect(get_database_url(), row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run_sql_file(filename: str) -> None:
    """Execute a .sql file from the sql/ directory.

    Used for schema setup and the SCD2 upsert. Those files manage their own
    transactions, so we hand the whole thing to psycopg in one go.
    """
    path: Path = SQL_DIR / filename
    sql = path.read_text()
    with connect() as conn:
        conn.execute(sql)


def fetch_all(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Run a SELECT and get back a list of dicts."""
    with connect() as conn:
        cur = conn.execute(sql, params or {})
        return cur.fetchall()


def fetch_one(sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    with connect() as conn:
        cur = conn.execute(sql, params or {})
        return cur.fetchone()


def fetch_analysis(name: str, params: dict[str, Any] | None = None) -> "pd.DataFrame":
    """Run a query from sql/analysis/ and return it as a DataFrame.

    Keeping analysis SQL in files rather than Python strings means the queries
    are readable on their own, diffable, and runnable in any SQL client.
    Parameterized files use %(name)s placeholders - never string formatting.
    """
    import pandas as pd

    sql = (SQL_DIR / "analysis" / f"{name}.sql").read_text()
    with connect() as conn:
        cur = conn.execute(sql, params or None)
        rows = cur.fetchall()
        columns = [c.name for c in cur.description]
    # Pass columns explicitly so an empty result still has the right shape.
    return pd.DataFrame(rows, columns=columns)


def ping() -> str:
    """Cheap connectivity check - used by the CLI to verify setup."""
    row = fetch_one("SELECT version() AS version")
    return row["version"] if row else "unknown"
