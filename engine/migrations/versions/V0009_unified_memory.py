"""Add pgvector extension + notes/links/tags/chunks tables (ADR-039 action item #1).

ADR-039 supersedes ADR-003's four-store design: one Postgres instance + a markdown
vault replace Chroma/Neo4j/Redis. notes/links/tags/chunks are the Postgres-native
replacement for the Chroma talos-board-*/talos-rules-* collections and the future
markdown-vault projection target (ADR-039 action item #2, not yet built). Schema-only
here -- nothing reads/writes these tables yet, matching how V0007 schema-stubbed
rules/rule_ingestion_log a full phase ahead of the code that uses them.

chunks.embedding is vector(384), matching the default local embedding model
(sentence-transformers/all-MiniLM-L6-v2, see talos/config.py's _MEMORY_DEFAULTS
embedding_dimension) -- migrations are static raw SQL and can't read talos.toml at
migration time, so the dimension is hardcoded and documented in both places.

tags.board_id is denormalized from notes.board_id (the vault indexer always knows
the board when writing tags) so every one of the four tables uses the same flat
board_id = current_setting('app.board_id', true) policy idiom as every table in
V0001-V0008 -- no subquery-through-parent RLS pattern introduced.

Index is ivfflat (cosine), not hnsw, per ADR-039's CPU-only-friendly deployment
target (talos.toml [resources], action item #5).

Revision ID: V0009
Revises: V0008
Create Date: 2026-07-12
"""
from typing import Sequence, Union

from alembic import op

revision: str = "V0009"
down_revision: Union[str, None] = "V0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    bind.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")

    bind.exec_driver_sql("""
        CREATE TABLE notes (
            id            TEXT PRIMARY KEY,
            board_id      TEXT NOT NULL REFERENCES boards(id),
            path          TEXT NOT NULL,
            title         TEXT NOT NULL,
            frontmatter   JSONB NOT NULL DEFAULT '{}',
            content_hash  TEXT NOT NULL,
            mtime         TIMESTAMPTZ NOT NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (board_id, path)
        )
    """)
    bind.exec_driver_sql("ALTER TABLE notes ENABLE ROW LEVEL SECURITY")
    bind.exec_driver_sql("""
        CREATE POLICY notes_board_isolation ON notes
            USING     (board_id = current_setting('app.board_id', true))
            WITH CHECK (board_id = current_setting('app.board_id', true))
    """)
    bind.exec_driver_sql("""
        CREATE POLICY notes_admin_bypass ON notes USING (current_user = 'talos_admin')
    """)
    bind.exec_driver_sql("ALTER TABLE notes FORCE ROW LEVEL SECURITY")

    # target is EITHER a resolved note (target_note_id) OR an unresolved
    # wikilink/tag slug (target_slug); exactly one is set.
    bind.exec_driver_sql("""
        CREATE TABLE links (
            id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            board_id       TEXT NOT NULL REFERENCES boards(id),
            src_note_id    TEXT NOT NULL REFERENCES notes(id),
            target_note_id TEXT REFERENCES notes(id),
            target_slug    TEXT,
            link_type      TEXT NOT NULL CHECK (link_type IN ('wikilink', 'embed', 'tag_ref')),
            valid_from     TIMESTAMPTZ NOT NULL DEFAULT now(),
            valid_until    TIMESTAMPTZ,
            ingested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK ((target_note_id IS NULL) <> (target_slug IS NULL))
        )
    """)
    bind.exec_driver_sql("ALTER TABLE links ENABLE ROW LEVEL SECURITY")
    bind.exec_driver_sql("""
        CREATE POLICY links_board_isolation ON links
            USING     (board_id = current_setting('app.board_id', true))
            WITH CHECK (board_id = current_setting('app.board_id', true))
    """)
    bind.exec_driver_sql("""
        CREATE POLICY links_admin_bypass ON links USING (current_user = 'talos_admin')
    """)
    bind.exec_driver_sql("ALTER TABLE links FORCE ROW LEVEL SECURITY")

    bind.exec_driver_sql("""
        CREATE TABLE tags (
            note_id  TEXT NOT NULL REFERENCES notes(id),
            board_id TEXT NOT NULL REFERENCES boards(id),
            tag      TEXT NOT NULL,
            PRIMARY KEY (note_id, tag)
        )
    """)
    bind.exec_driver_sql("ALTER TABLE tags ENABLE ROW LEVEL SECURITY")
    bind.exec_driver_sql("""
        CREATE POLICY tags_board_isolation ON tags
            USING     (board_id = current_setting('app.board_id', true))
            WITH CHECK (board_id = current_setting('app.board_id', true))
    """)
    bind.exec_driver_sql("""
        CREATE POLICY tags_admin_bypass ON tags USING (current_user = 'talos_admin')
    """)
    bind.exec_driver_sql("ALTER TABLE tags FORCE ROW LEVEL SECURITY")

    bind.exec_driver_sql("""
        CREATE TABLE chunks (
            id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            board_id    TEXT NOT NULL REFERENCES boards(id),
            note_id     TEXT REFERENCES notes(id),
            source      TEXT NOT NULL CHECK (source IN ('vault', 'doc', 'rule')),
            chunk_text  TEXT NOT NULL,
            embedding   vector(384) NOT NULL,
            metadata    JSONB NOT NULL DEFAULT '{}',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    bind.exec_driver_sql("ALTER TABLE chunks ENABLE ROW LEVEL SECURITY")
    bind.exec_driver_sql("""
        CREATE POLICY chunks_board_isolation ON chunks
            USING     (board_id = current_setting('app.board_id', true))
            WITH CHECK (board_id = current_setting('app.board_id', true))
    """)
    bind.exec_driver_sql("""
        CREATE POLICY chunks_admin_bypass ON chunks USING (current_user = 'talos_admin')
    """)
    bind.exec_driver_sql("ALTER TABLE chunks FORCE ROW LEVEL SECURITY")
    bind.exec_driver_sql(
        "CREATE INDEX chunks_embedding_ivfflat ON chunks "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("DROP TABLE IF EXISTS chunks")
    bind.exec_driver_sql("DROP TABLE IF EXISTS tags")
    bind.exec_driver_sql("DROP TABLE IF EXISTS links")
    bind.exec_driver_sql("DROP TABLE IF EXISTS notes")
