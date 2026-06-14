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

import psycopg2
import psycopg2.extras
import pytest
from testcontainers.postgres import PostgresContainer

from langgraph.checkpoint.memory import MemorySaver

SCHEMA_FILES = [
    "/mnt/i/talos/engine/schema.sql",
    "/mnt/i/talos/engine/schema-additions.sql",
    "/mnt/i/talos/engine/schema-p2.sql",
]


# ---------------------------------------------------------------------------
# Container + schema bootstrap (session scope — one DB per test run)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def pg_container():
    with PostgresContainer("postgres:16") as pg:
        yield pg


@pytest.fixture(scope="session")
def pg_setup(pg_container):
    """
    Bootstrap the schema and the talos_app role.

    CRITICAL: also sets TALOS_DB_DSN so every subsequent db.get_conn() call
    (from worker, api, spine nodes) connects to this container rather than
    falling back to postgresql://localhost/talos.
    """
    admin_dsn = pg_container.get_connection_url().replace("+psycopg2", "")

    # Expose the DSN to db.get_conn() before any test code runs.
    os.environ["TALOS_DB_DSN"] = admin_dsn

    conn = psycopg2.connect(admin_dsn)
    conn.autocommit = True
    cur = conn.cursor()

    # Apply schema files in order.
    for path in SCHEMA_FILES:
        with open(path) as f:
            cur.execute(f.read())

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

    cur.close()
    conn.close()

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
    from platform.graph.spine import build_graph
    from platform import api as api_module

    graph = build_graph(MemorySaver())
    api_module.set_graph(graph)
    return graph
