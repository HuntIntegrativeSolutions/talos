"""
Thin psycopg2 connection helper for TALOS.

Provides get_conn() for one-off connections and board_scope() for all
board-scoped transactions. Every Postgres write in the engine must go
through board_scope so the RLS policy (app.board_id) is always set.
"""

from __future__ import annotations

import contextlib
import os

import psycopg2
import psycopg2.extras

DEFAULT_DSN = "postgresql://localhost/talos"


def get_conn(dsn: str | None = None) -> psycopg2.extensions.connection:
    dsn = dsn or os.environ.get("TALOS_DB_DSN", DEFAULT_DSN)
    conn = psycopg2.connect(dsn)
    conn.autocommit = False  # SET LOCAL requires a transaction block
    return conn


@contextlib.contextmanager
def board_scope(conn: psycopg2.extensions.connection, board_id: str):
    """
    Context manager that sets app.board_id for the lifetime of one transaction.

    SET LOCAL resets the GUC at transaction end, so every block is automatically
    un-scoped after commit or rollback. Callers must not commit/rollback inside
    this block — board_scope owns the transaction boundary.
    """
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SET LOCAL app.board_id = %s", (board_id,))
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
