"""Add board-scoped NEXUS read cache table (ADR-035).

nexus_cache postdates V0003's hardcoded FORCE-RLS table list, so this
migration self-forces rather than extending that list.

Revision ID: V0005
Revises: V0004
Create Date: 2026-07-05
"""
from typing import Sequence, Union

from alembic import op

revision: str = "V0005"
down_revision: Union[str, None] = "V0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("""
        CREATE TABLE nexus_cache (
            id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            board_id    TEXT NOT NULL REFERENCES boards(id),
            tool_name   TEXT NOT NULL,
            params_hash TEXT NOT NULL,
            result_json JSONB NOT NULL,
            fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at  TIMESTAMPTZ NOT NULL,
            UNIQUE (board_id, tool_name, params_hash)
        )
    """)
    bind.exec_driver_sql("ALTER TABLE nexus_cache ENABLE ROW LEVEL SECURITY")
    bind.exec_driver_sql("""
        CREATE POLICY nexus_cache_board_isolation ON nexus_cache
            USING     (board_id = current_setting('app.board_id', true))
            WITH CHECK (board_id = current_setting('app.board_id', true))
    """)
    bind.exec_driver_sql("""
        CREATE POLICY nexus_cache_admin_bypass ON nexus_cache
            USING (current_user = 'talos_admin')
    """)
    bind.exec_driver_sql("ALTER TABLE nexus_cache FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.get_bind().exec_driver_sql("DROP TABLE IF EXISTS nexus_cache")
