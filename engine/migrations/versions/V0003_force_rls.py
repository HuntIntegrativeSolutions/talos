"""Add FORCE ROW LEVEL SECURITY to all board-scoped tables (SEC-03 / ADR-037).

Without FORCE, the table owner (postgres, which runs Alembic) bypasses board_isolation
policies silently. This migration makes RLS structural — owner connections are also
bound by the policies, closing the silent bypass gap.

Revision ID: V0003
Revises: V0002
Create Date: 2026-06-18
"""
from typing import Sequence, Union

from alembic import op

revision: str = "V0003"
down_revision: Union[str, None] = "V0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RLS_TABLES = [
    "tasks",
    "task_links",
    "task_comments",
    "task_events",
    "task_runs",
    "task_attachments",
    "notify_subs",
    "task_gate_results",
    "spaces",
    "space_versions",
    "widgets",
    "widget_versions",
    "task_gate_escalations",
    "milestones",
    "task_spans",
]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _RLS_TABLES:
        bind.exec_driver_sql(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    bind = op.get_bind()
    for table in _RLS_TABLES:
        bind.exec_driver_sql(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
