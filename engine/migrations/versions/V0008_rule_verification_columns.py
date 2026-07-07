"""Add verified/safety/superseded_by columns to rules (P5-Crystallize).

V0007 schema-stubbed the rules/rule_ingestion_log tables for ADR-023; this
migration adds the three columns P5's extraction pipeline needs: verified and
safety flags (gate contradiction handling — a verified/safety row can never be
auto-superseded, only via a human-approved review task) and superseded_by (the
v1, non-Graphiti replacement for ADR-023's bi-temporal invalid_at edge).

Revision ID: V0008
Revises: V0007
Create Date: 2026-07-06
"""
from typing import Sequence, Union

from alembic import op

revision: str = "V0008"
down_revision: Union[str, None] = "V0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    bind.exec_driver_sql("ALTER TABLE rules ADD COLUMN verified BOOLEAN NOT NULL DEFAULT false")
    bind.exec_driver_sql("ALTER TABLE rules ADD COLUMN safety BOOLEAN NOT NULL DEFAULT false")
    bind.exec_driver_sql("ALTER TABLE rules ADD COLUMN superseded_by TEXT REFERENCES rules(id)")
    # No new RLS policy needed -- these columns inherit rules' existing
    # board-isolation/admin-bypass policies (V0007); a nullable self-FK
    # doesn't change row visibility.


def downgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("ALTER TABLE rules DROP COLUMN IF EXISTS superseded_by")
    bind.exec_driver_sql("ALTER TABLE rules DROP COLUMN IF EXISTS safety")
    bind.exec_driver_sql("ALTER TABLE rules DROP COLUMN IF EXISTS verified")
