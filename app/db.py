"""Postgres access via psycopg3. A tiny helper layer instead of an ORM: the
schema is the contract, and every query stays visible and auditable."""
import atexit

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import config

_pool: ConnectionPool | None = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            config.DATABASE_URL, min_size=1, max_size=10, open=True,
            kwargs={"row_factory": dict_row},
        )
        atexit.register(_pool.close)
    return _pool


def init_schema() -> None:
    schema = (config.REPO_ROOT / "app" / "schema.sql").read_text(encoding="utf-8")
    with pool().connection() as conn:
        conn.execute(schema)


def q(sql: str, params: tuple | dict = ()) -> list[dict]:
    """Run a query, return all rows as dicts (empty list for non-SELECT)."""
    with pool().connection() as conn:
        cur = conn.execute(sql, params)
        if cur.description is None:
            return []
        return cur.fetchall()


def one(sql: str, params: tuple | dict = ()) -> dict | None:
    rows = q(sql, params)
    return rows[0] if rows else None
