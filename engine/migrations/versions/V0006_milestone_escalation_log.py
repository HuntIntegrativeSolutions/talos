"""Add milestone_escalation_log table (ADR-016 action item #7 / P4b DoD #5).

Also fixes a latent bug found during P4b planning: task_events.task_id was
NOT NULL, but pm_escalate_milestone_risk() (engine/schema-additions.sql)
inserts task_id=NULL for milestone-scoped events ("milestone events aren't
tied to one task") -- this violated the constraint and was masked only
because no prior test exercised the missed/at_risk transition.

milestone_escalation_log postdates V0003's hardcoded FORCE-RLS table list,
so this migration self-forces rather than extending that list.

Revision ID: V0006
Revises: V0005
Create Date: 2026-07-06
"""
from typing import Sequence, Union

from alembic import op

revision: str = "V0006"
down_revision: Union[str, None] = "V0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    bind.exec_driver_sql("ALTER TABLE task_events ALTER COLUMN task_id DROP NOT NULL")

    bind.exec_driver_sql("""
        CREATE TABLE milestone_escalation_log (
            id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            board_id         TEXT NOT NULL REFERENCES boards(id),
            task_event_id    BIGINT NOT NULL,
            milestone_id     TEXT NOT NULL,
            severity         TEXT NOT NULL CHECK (severity IN ('HIGH', 'MEDIUM')),
            created_task_id  TEXT,
            handled_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (task_event_id)
        )
    """)
    bind.exec_driver_sql("ALTER TABLE milestone_escalation_log ENABLE ROW LEVEL SECURITY")
    bind.exec_driver_sql("""
        CREATE POLICY milestone_escalation_log_board_isolation ON milestone_escalation_log
            USING     (board_id = current_setting('app.board_id', true))
            WITH CHECK (board_id = current_setting('app.board_id', true))
    """)
    bind.exec_driver_sql("""
        CREATE POLICY milestone_escalation_log_admin_bypass ON milestone_escalation_log
            USING (current_user = 'talos_admin')
    """)
    bind.exec_driver_sql("ALTER TABLE milestone_escalation_log FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("DROP TABLE IF EXISTS milestone_escalation_log")
    bind.exec_driver_sql("ALTER TABLE task_events ALTER COLUMN task_id SET NOT NULL")
