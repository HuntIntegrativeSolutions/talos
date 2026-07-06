"""Add gate-UI columns: persisted deliverable, review-entry timestamp, board SLA (P7a).

Revision ID: V0004
Revises: V0003
Create Date: 2026-07-05
"""
from typing import Sequence, Union

from alembic import op

revision: str = "V0004"
down_revision: Union[str, None] = "V0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("ALTER TABLE tasks ADD COLUMN deliverable JSONB")
    bind.exec_driver_sql("ALTER TABLE tasks ADD COLUMN review_entered_at timestamptz")
    # Backfill: any task already sitting in 'review' needs a non-null
    # review_entered_at or it sorts as NULL / shows no time-in-review.
    bind.exec_driver_sql(
        "UPDATE tasks SET review_entered_at = COALESCE(started_at, created_at) "
        "WHERE status = 'review' AND review_entered_at IS NULL"
    )
    bind.exec_driver_sql("ALTER TABLE boards ADD COLUMN sla_minutes INTEGER")


def downgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("ALTER TABLE boards DROP COLUMN IF EXISTS sla_minutes")
    bind.exec_driver_sql("ALTER TABLE tasks DROP COLUMN IF EXISTS review_entered_at")
    bind.exec_driver_sql("ALTER TABLE tasks DROP COLUMN IF EXISTS deliverable")
