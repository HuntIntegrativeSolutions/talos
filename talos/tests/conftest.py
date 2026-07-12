"""
Pytest fixtures for TALOS P1 integration tests.

Provides:
  pg_container  — session-scoped Postgres 16 container (testcontainers)
  pg_setup      — session-scoped: initialises schema, creates talos_app role,
                  sets TALOS_DB_DSN env var so db.get_conn() works in tests
  admin_conn    — function-scoped superuser connection (bypasses RLS by ownership)
  app_conn      — function-scoped talos_app connection (RLS enforced — NOSUPERUSER)
  test_graph    — session-scoped MemorySaver-backed spine graph shared by
                  claim_and_run() calls and the API endpoint

Critical RLS note:
  Postgres superusers and table owners bypass RLS even when ENABLE ROW LEVEL
  SECURITY is set. To test RLS enforcement, tests must connect as a non-superuser
  non-owner role (talos_app). The admin_conn fixture is for seeding only.
"""

from __future__ import annotations

import os
import pathlib

# Must be set before talos.api (or anything importing it) is imported.
# Tests run with TALOS_JWT_SECRET=test-secret-dev-only; this setdefault
# is the fallback for bare `pytest` invocations without the env prefix.
os.environ.setdefault("TALOS_JWT_SECRET", "test-secret-dev-only")

import psycopg2
import psycopg2.extras
import pytest
from testcontainers.postgres import PostgresContainer

from langgraph.checkpoint.memory import MemorySaver

_HERE = pathlib.Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent

_ALEMBIC_INI = str(_REPO_ROOT / "engine" / "alembic.ini")


# ---------------------------------------------------------------------------
# Container + schema bootstrap (session scope — one DB per test run)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def pg_container():
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        yield pg


@pytest.fixture(scope="session")
def pg_setup(pg_container):
    """
    Bootstrap the schema and the talos_app role.

    CRITICAL: also sets TALOS_DB_DSN so every subsequent db.get_conn() call
    (from worker, api, spine nodes) connects to this container rather than
    falling back to postgresql://localhost/talos.
    """
    # SQLAlchemy URL (postgresql+psycopg2://...) for Alembic.
    sa_url = pg_container.get_connection_url()
    # psycopg2 URL (postgresql://...) for direct psycopg2 connections.
    admin_dsn = sa_url.replace("+psycopg2", "")

    # Expose the DSN to db.get_conn() before any test code runs.
    os.environ["TALOS_DB_DSN"] = admin_dsn

    conn = psycopg2.connect(admin_dsn)
    conn.autocommit = True
    cur = conn.cursor()

    # Apply all schema files via raw psycopg2.  Alembic's command.upgrade()
    # hangs when run in-process against a psycopg2 connection inside a
    # testcontainers session (likely a thread-state / async interaction with
    # SA 2.0's connection pooling).  Raw cursor execution is the proven path.
    _SCHEMA_FILES = [
        str(_REPO_ROOT / "engine" / "schema.sql"),
        str(_REPO_ROOT / "engine" / "schema-additions.sql"),
        str(_REPO_ROOT / "engine" / "schema-p2.sql"),
        str(_REPO_ROOT / "engine" / "schema-p3.sql"),
    ]
    for path in _SCHEMA_FILES:
        with open(path) as f:
            cur.execute(f.read())

    # Apply V0002 content directly (users table for JWT auth — ADR-036).
    cur.execute("""
        CREATE TABLE users (
            username         TEXT PRIMARY KEY,
            hashed_password  TEXT NOT NULL,
            created_at       timestamptz NOT NULL DEFAULT now()
        )
    """)

    # Mirror V0003: FORCE ROW LEVEL SECURITY on all board-scoped tables (SEC-03).
    # conftest stamps rather than runs upgrade(), so FORCE must be applied manually.
    _RLS_TABLES = [
        "tasks", "task_links", "task_comments", "task_events", "task_runs",
        "task_attachments", "notify_subs", "task_gate_results", "spaces",
        "space_versions", "widgets", "widget_versions", "task_gate_escalations",
        "milestones", "task_spans",
    ]
    for _t in _RLS_TABLES:
        cur.execute(f"ALTER TABLE {_t} FORCE ROW LEVEL SECURITY")

    # Apply V0004 content directly (gate-UI columns — P7a). No pre-existing
    # 'review' rows exist yet in a fresh container, so no backfill is needed.
    cur.execute("ALTER TABLE tasks ADD COLUMN deliverable JSONB")
    cur.execute("ALTER TABLE tasks ADD COLUMN review_entered_at timestamptz")
    cur.execute("ALTER TABLE boards ADD COLUMN sla_minutes INTEGER")

    # Apply V0005 content directly (board-scoped NEXUS read cache — ADR-035 / P4a).
    # Self-contained: FORCE is applied here, not via the shared _RLS_TABLES loop,
    # since nexus_cache postdates V0003 and self-forces in the real migration too.
    cur.execute("""
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
    cur.execute("ALTER TABLE nexus_cache ENABLE ROW LEVEL SECURITY")
    cur.execute("""
        CREATE POLICY nexus_cache_board_isolation ON nexus_cache
            USING     (board_id = current_setting('app.board_id', true))
            WITH CHECK (board_id = current_setting('app.board_id', true))
    """)
    cur.execute("""
        CREATE POLICY nexus_cache_admin_bypass ON nexus_cache
            USING (current_user = 'talos_admin')
    """)
    cur.execute("ALTER TABLE nexus_cache FORCE ROW LEVEL SECURITY")

    # Apply V0006 content directly (milestone_escalation_log — ADR-016 action
    # item #7 / P4b DoD #5). Also mirrors V0006's task_events.task_id NOT NULL
    # fix (pm_escalate_milestone_risk() inserts task_id=NULL for milestone
    # events, which the original NOT NULL constraint rejected).
    cur.execute("ALTER TABLE task_events ALTER COLUMN task_id DROP NOT NULL")
    cur.execute("""
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
    cur.execute("ALTER TABLE milestone_escalation_log ENABLE ROW LEVEL SECURITY")
    cur.execute("""
        CREATE POLICY milestone_escalation_log_board_isolation ON milestone_escalation_log
            USING     (board_id = current_setting('app.board_id', true))
            WITH CHECK (board_id = current_setting('app.board_id', true))
    """)
    cur.execute("""
        CREATE POLICY milestone_escalation_log_admin_bypass ON milestone_escalation_log
            USING (current_user = 'talos_admin')
    """)
    cur.execute("ALTER TABLE milestone_escalation_log FORCE ROW LEVEL SECURITY")

    # Apply V0007 content directly (rules + rule_ingestion_log — ADR-023 P4
    # schema stub — and boards.client_identifiers — RT-06 enforcement input).
    cur.execute("ALTER TABLE boards ADD COLUMN client_identifiers TEXT[] NOT NULL DEFAULT '{}'")
    cur.execute("""
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
    cur.execute("ALTER TABLE rules ENABLE ROW LEVEL SECURITY")
    cur.execute("""
        CREATE POLICY rules_board_isolation ON rules
            USING     (board_id = current_setting('app.board_id', true))
            WITH CHECK (board_id = current_setting('app.board_id', true))
    """)
    cur.execute("""
        CREATE POLICY rules_admin_bypass ON rules USING (current_user = 'talos_admin')
    """)
    cur.execute("ALTER TABLE rules FORCE ROW LEVEL SECURITY")

    cur.execute("""
        CREATE TABLE rule_ingestion_log (
            id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            board_id    TEXT NOT NULL REFERENCES boards(id),
            dedup_key   TEXT NOT NULL,
            rule_id     TEXT REFERENCES rules(id),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (board_id, dedup_key)
        )
    """)
    cur.execute("ALTER TABLE rule_ingestion_log ENABLE ROW LEVEL SECURITY")
    cur.execute("""
        CREATE POLICY rule_ingestion_log_board_isolation ON rule_ingestion_log
            USING     (board_id = current_setting('app.board_id', true))
            WITH CHECK (board_id = current_setting('app.board_id', true))
    """)
    cur.execute("""
        CREATE POLICY rule_ingestion_log_admin_bypass ON rule_ingestion_log
            USING (current_user = 'talos_admin')
    """)
    cur.execute("ALTER TABLE rule_ingestion_log FORCE ROW LEVEL SECURITY")

    # Apply V0008 content directly (verified/safety/superseded_by columns on
    # rules -- P5-Crystallize contradiction handling).
    cur.execute("ALTER TABLE rules ADD COLUMN verified BOOLEAN NOT NULL DEFAULT false")
    cur.execute("ALTER TABLE rules ADD COLUMN safety BOOLEAN NOT NULL DEFAULT false")
    cur.execute("ALTER TABLE rules ADD COLUMN superseded_by TEXT REFERENCES rules(id)")

    # Apply V0009 content directly (pgvector extension + notes/links/tags/chunks
    # tables -- ADR-039 action item #1).
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")

    cur.execute("""
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
    cur.execute("ALTER TABLE notes ENABLE ROW LEVEL SECURITY")
    cur.execute("""
        CREATE POLICY notes_board_isolation ON notes
            USING     (board_id = current_setting('app.board_id', true))
            WITH CHECK (board_id = current_setting('app.board_id', true))
    """)
    cur.execute("""
        CREATE POLICY notes_admin_bypass ON notes USING (current_user = 'talos_admin')
    """)
    cur.execute("ALTER TABLE notes FORCE ROW LEVEL SECURITY")

    cur.execute("""
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
    cur.execute("ALTER TABLE links ENABLE ROW LEVEL SECURITY")
    cur.execute("""
        CREATE POLICY links_board_isolation ON links
            USING     (board_id = current_setting('app.board_id', true))
            WITH CHECK (board_id = current_setting('app.board_id', true))
    """)
    cur.execute("""
        CREATE POLICY links_admin_bypass ON links USING (current_user = 'talos_admin')
    """)
    cur.execute("ALTER TABLE links FORCE ROW LEVEL SECURITY")

    cur.execute("""
        CREATE TABLE tags (
            note_id  TEXT NOT NULL REFERENCES notes(id),
            board_id TEXT NOT NULL REFERENCES boards(id),
            tag      TEXT NOT NULL,
            PRIMARY KEY (note_id, tag)
        )
    """)
    cur.execute("ALTER TABLE tags ENABLE ROW LEVEL SECURITY")
    cur.execute("""
        CREATE POLICY tags_board_isolation ON tags
            USING     (board_id = current_setting('app.board_id', true))
            WITH CHECK (board_id = current_setting('app.board_id', true))
    """)
    cur.execute("""
        CREATE POLICY tags_admin_bypass ON tags USING (current_user = 'talos_admin')
    """)
    cur.execute("ALTER TABLE tags FORCE ROW LEVEL SECURITY")

    cur.execute("""
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
    cur.execute("ALTER TABLE chunks ENABLE ROW LEVEL SECURITY")
    cur.execute("""
        CREATE POLICY chunks_board_isolation ON chunks
            USING     (board_id = current_setting('app.board_id', true))
            WITH CHECK (board_id = current_setting('app.board_id', true))
    """)
    cur.execute("""
        CREATE POLICY chunks_admin_bypass ON chunks USING (current_user = 'talos_admin')
    """)
    cur.execute("ALTER TABLE chunks FORCE ROW LEVEL SECURITY")
    cur.execute(
        "CREATE INDEX chunks_embedding_ivfflat ON chunks "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )

    # Create talos_app as NOSUPERUSER so RLS applies to it.
    # The table owner (postgres) bypasses RLS; talos_app does not.
    cur.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'talos_app') THEN
                CREATE ROLE talos_app NOSUPERUSER NOINHERIT LOGIN PASSWORD 'talos_app';
            END IF;
        END $$;
        """
    )
    cur.execute("GRANT USAGE ON SCHEMA public TO talos_app")
    cur.execute(
        "GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO talos_app"
    )
    cur.execute(
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO talos_app"
    )
    # talos_app needs DELETE on chunks specifically: pgvector_store's
    # upsert_rule/ingest_deliverable implement idempotent re-embedding as
    # delete-then-insert (ADR-039 action item #3; see docs/install.md).
    cur.execute("GRANT DELETE ON chunks TO talos_app")
    # talos_app needs DELETE on notes/links/tags: talos.vault.indexer's
    # --rebuild and file-deletion handling delete this board's vault-owned
    # rows (ADR-039 action item #2; see docs/install.md).
    cur.execute("GRANT DELETE ON notes, links, tags TO talos_app")

    # Create talos_system: BYPASSRLS role for cross-board reclaim janitor (ADR-037).
    cur.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'talos_system') THEN
                CREATE ROLE talos_system BYPASSRLS NOSUPERUSER NOINHERIT LOGIN PASSWORD 'talos_system';
            END IF;
        END $$;
        """
    )
    cur.execute("GRANT USAGE ON SCHEMA public TO talos_system")
    # SELECT on all tables so pm_recompute_scheduling() trigger (fired by UPDATE tasks)
    # can read v_critical_path and underlying tables without InsufficientPrivilege.
    cur.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO talos_system")
    cur.execute("GRANT SELECT, UPDATE ON task_runs, tasks TO talos_system")
    cur.execute("GRANT INSERT ON task_spans TO talos_system")
    cur.execute("GRANT USAGE, SELECT ON SEQUENCE task_spans_id_seq TO talos_system")

    cur.close()
    conn.close()

    # Stamp head so Alembic's version table reflects the full migration state
    # without re-running any upgrade() functions.
    from alembic.config import Config
    from alembic import command as alembic_command
    alembic_cfg = Config(_ALEMBIC_INI)
    alembic_cfg.set_main_option("sqlalchemy.url", sa_url)
    alembic_command.stamp(alembic_cfg, "head")

    # Flip TALOS_DB_DSN to talos_app so product code runs under enforced RLS.
    # admin_dsn (superuser) is still yielded for the admin_conn fixture (seeding only).
    host = pg_container.get_container_host_ip()
    port = pg_container.get_exposed_port(5432)
    app_dsn = f"postgresql://talos_app:talos_app@{host}:{port}/test"
    os.environ["TALOS_DB_DSN"] = app_dsn

    # Set TALOS_RECLAIM_DSN so get_system_conn() works for cross-board reclaim (ADR-037).
    os.environ["TALOS_RECLAIM_DSN"] = f"postgresql://talos_system:talos_system@{host}:{port}/test"

    yield admin_dsn


# ---------------------------------------------------------------------------
# Per-test connections
# ---------------------------------------------------------------------------

@pytest.fixture
def admin_conn(pg_setup):
    """Superuser connection — bypasses RLS. Use for seeding test data."""
    conn = psycopg2.connect(pg_setup)
    conn.autocommit = False
    yield conn
    conn.rollback()
    conn.close()


@pytest.fixture
def app_conn(pg_setup, pg_container):
    """Non-privileged talos_app connection — RLS is enforced."""
    host = pg_container.get_container_host_ip()
    port = pg_container.get_exposed_port(5432)
    # The testcontainers default database name is 'test'
    conn = psycopg2.connect(
        host=host,
        port=port,
        dbname="test",
        user="talos_app",
        password="talos_app",
    )
    conn.autocommit = False
    yield conn
    conn.rollback()
    conn.close()


# ---------------------------------------------------------------------------
# Shared LangGraph instance (MemorySaver — in-process checkpoint store)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def test_graph(pg_setup):
    """
    A single MemorySaver-backed graph shared across the test session.

    worker.claim_and_run() and the API's submit_gate_outcome() must use the
    same graph instance — the MemorySaver holds checkpoint state in process.
    """
    from talos.graph.spine import build_graph
    from talos import api as api_module

    graph = build_graph(MemorySaver())
    api_module.set_graph(graph)
    return graph


# ---------------------------------------------------------------------------
# JWT test fixture (human token for gate tests)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def human_jwt(pg_setup):
    """Create test user 'thunt' and return a valid human JWT for gate tests."""
    from talos.auth.users import add_user
    from talos.auth.tokens import issue_token
    try:
        add_user("thunt", "hunter2")
    except Exception:
        pass  # user already exists from a prior session fixture call
    return issue_token("thunt", "hunter2")
