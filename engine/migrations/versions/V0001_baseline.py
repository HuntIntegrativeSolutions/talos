"""Baseline — applies all schema files that existed before Alembic was adopted.

Revision ID: V0001
Revises:
Create Date: 2026-06-17
"""
from typing import Sequence, Union
import pathlib

from alembic import op

revision: str = "V0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ENGINE_DIR = pathlib.Path(__file__).resolve().parent.parent

_SCHEMA_FILES = [
    _ENGINE_DIR / "schema.sql",
    _ENGINE_DIR / "schema-additions.sql",
    _ENGINE_DIR / "schema-p2.sql",
    _ENGINE_DIR / "schema-p3.sql",
]


def upgrade() -> None:
    # Use the raw psycopg2 cursor to execute multi-statement schema files.
    # SA 2.0's exec_driver_sql() hangs on multi-statement SQL (doesn't drain
    # all result sets). conn.connection.driver_connection gives the actual
    # psycopg2 connection; its cursor() handles multi-statement SQL natively.
    conn = op.get_bind()
    raw_conn = conn.connection.driver_connection
    cursor = raw_conn.cursor()
    for path in _SCHEMA_FILES:
        cursor.execute(path.read_text())
    cursor.close()


def downgrade() -> None:
    raise NotImplementedError(
        "V0001 baseline downgrade is not supported — drop and recreate the database."
    )
