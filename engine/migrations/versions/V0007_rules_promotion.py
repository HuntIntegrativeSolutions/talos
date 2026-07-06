"""Add rules + rule_ingestion_log tables (ADR-023 P4 schema stub) and
boards.client_identifiers (RT-06 enforcement input).

rules/rule_ingestion_log postdate V0003's hardcoded FORCE-RLS table list, so
this migration self-forces rather than extending that list. rule_ingestion_log
is schema-stubbed per ADR-023 action item #1 but not exercised by any dedup
logic yet -- extraction (auto-populating it) is P5 scope.

Revision ID: V0007
Revises: V0006
Create Date: 2026-07-06
"""
from typing import Sequence, Union

from alembic import op

revision: str = "V0007"
down_revision: Union[str, None] = "V0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    bind.exec_driver_sql("ALTER TABLE boards ADD COLUMN client_identifiers TEXT[] NOT NULL DEFAULT '{}'")

    bind.exec_driver_sql("""
        CREATE TABLE rules (
            id                TEXT PRIMARY KEY,
            board_id          TEXT NOT NULL REFERENCES boards(id),
            rule_type         TEXT NOT NULL CHECK (rule_type IN ('factual', 'procedural', 'project_context')),
            content           TEXT NOT NULL,
            client_scope      TEXT NOT NULL DEFAULT 'client' CHECK (client_scope IN ('client', 'shared')),
            source_task_id    TEXT REFERENCES tasks(id),
            promotion_task_id TEXT REFERENCES tasks(id),
            status            TEXT NOT NULL DEFAULT 'pending_review'
                              CHECK (status IN ('pending_review', 'approved_client', 'approved_shared', 'rejected')),
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    bind.exec_driver_sql("ALTER TABLE rules ENABLE ROW LEVEL SECURITY")
    bind.exec_driver_sql("""
        CREATE POLICY rules_board_isolation ON rules
            USING     (board_id = current_setting('app.board_id', true))
            WITH CHECK (board_id = current_setting('app.board_id', true))
    """)
    bind.exec_driver_sql("""
        CREATE POLICY rules_admin_bypass ON rules USING (current_user = 'talos_admin')
    """)
    bind.exec_driver_sql("ALTER TABLE rules FORCE ROW LEVEL SECURITY")

    bind.exec_driver_sql("""
        CREATE TABLE rule_ingestion_log (
            id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            board_id    TEXT NOT NULL REFERENCES boards(id),
            dedup_key   TEXT NOT NULL,
            rule_id     TEXT REFERENCES rules(id),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (board_id, dedup_key)
        )
    """)
    bind.exec_driver_sql("ALTER TABLE rule_ingestion_log ENABLE ROW LEVEL SECURITY")
    bind.exec_driver_sql("""
        CREATE POLICY rule_ingestion_log_board_isolation ON rule_ingestion_log
            USING     (board_id = current_setting('app.board_id', true))
            WITH CHECK (board_id = current_setting('app.board_id', true))
    """)
    bind.exec_driver_sql("""
        CREATE POLICY rule_ingestion_log_admin_bypass ON rule_ingestion_log
            USING (current_user = 'talos_admin')
    """)
    bind.exec_driver_sql("ALTER TABLE rule_ingestion_log FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("DROP TABLE IF EXISTS rule_ingestion_log")
    bind.exec_driver_sql("DROP TABLE IF EXISTS rules")
    bind.exec_driver_sql("ALTER TABLE boards DROP COLUMN IF EXISTS client_identifiers")
