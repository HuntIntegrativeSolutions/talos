"""Add users table for local JWT authentication (ADR-036 / RT-01).

Revision ID: V0002
Revises: V0001
Create Date: 2026-06-17
"""
from typing import Sequence, Union

from alembic import op

revision: str = "V0002"
down_revision: Union[str, None] = "V0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql("""
        CREATE TABLE users (
            username         TEXT PRIMARY KEY,
            hashed_password  TEXT NOT NULL,
            created_at       timestamptz NOT NULL DEFAULT now()
        )
    """)


def downgrade() -> None:
    op.get_bind().exec_driver_sql("DROP TABLE IF EXISTS users")
