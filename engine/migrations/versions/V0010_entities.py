"""Add entities + note_entity_links tables (ADR-039 action item #4).

NEXUS entities (controllers, programs/routines, tags) become graph nodes in
this same Postgres, so notes and entities are joinable -- the query this
enables is "which vault notes discuss tags affected by this change." Per
ADR-027 NEXUS remains system-of-record for PLC facts: entities rows are
pointers (identity + external_ref) only, never a copy of NEXUS domain
knowledge (no description/logic/value columns), same discipline as
nexus_cache's read-through caching (V0005).

note_entity_links reuses V0009's links bi-temporal idiom (valid_from,
valid_until, ingested_at) -- close+insert on supersession, never UPDATE a
row's entity_id in place.

Both tables use the same flat board_id = current_setting('app.board_id',
true) policy idiom as every table since V0001 -- no subquery-through-parent
RLS pattern.

Revision ID: V0010
Revises: V0009
Create Date: 2026-07-12
"""
from typing import Sequence, Union

from alembic import op

revision: str = "V0010"
down_revision: Union[str, None] = "V0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    bind.exec_driver_sql("""
        CREATE TABLE entities (
            id           TEXT PRIMARY KEY,
            board_id     TEXT NOT NULL REFERENCES boards(id),
            entity_type  TEXT NOT NULL CHECK (entity_type IN ('controller', 'program', 'routine', 'tag')),
            name         TEXT NOT NULL,
            external_ref TEXT NOT NULL,
            metadata     JSONB NOT NULL DEFAULT '{}',
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (board_id, entity_type, external_ref)
        )
    """)
    bind.exec_driver_sql("ALTER TABLE entities ENABLE ROW LEVEL SECURITY")
    bind.exec_driver_sql("""
        CREATE POLICY entities_board_isolation ON entities
            USING     (board_id = current_setting('app.board_id', true))
            WITH CHECK (board_id = current_setting('app.board_id', true))
    """)
    bind.exec_driver_sql("""
        CREATE POLICY entities_admin_bypass ON entities USING (current_user = 'talos_admin')
    """)
    bind.exec_driver_sql("ALTER TABLE entities FORCE ROW LEVEL SECURITY")

    bind.exec_driver_sql("""
        CREATE TABLE note_entity_links (
            id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            board_id    TEXT NOT NULL REFERENCES boards(id),
            note_id     TEXT NOT NULL REFERENCES notes(id),
            entity_id   TEXT NOT NULL REFERENCES entities(id),
            link_type   TEXT NOT NULL CHECK (link_type IN ('mentions', 'documents')),
            valid_from  TIMESTAMPTZ NOT NULL DEFAULT now(),
            valid_until TIMESTAMPTZ,
            ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    bind.exec_driver_sql("ALTER TABLE note_entity_links ENABLE ROW LEVEL SECURITY")
    bind.exec_driver_sql("""
        CREATE POLICY note_entity_links_board_isolation ON note_entity_links
            USING     (board_id = current_setting('app.board_id', true))
            WITH CHECK (board_id = current_setting('app.board_id', true))
    """)
    bind.exec_driver_sql("""
        CREATE POLICY note_entity_links_admin_bypass ON note_entity_links
            USING (current_user = 'talos_admin')
    """)
    bind.exec_driver_sql("ALTER TABLE note_entity_links FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("DROP TABLE IF EXISTS note_entity_links")
    bind.exec_driver_sql("DROP TABLE IF EXISTS entities")
