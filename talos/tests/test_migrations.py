"""
Proves `alembic upgrade head` runs cleanly against a genuinely empty database.

This is distinct from `conftest.py::pg_setup`, which never calls `command.upgrade()`
in-process (documented hang against a psycopg2 connection inside a testcontainers
session) and instead hand-applies schema SQL then `stamp`s head. That workaround
meant the real Alembic upgrade path (what a fresh install actually runs) had never
been exercised at all — `V0001_baseline.py`'s `_ENGINE_DIR` path bug (resolved to
`engine/migrations/` instead of `engine/`) went undetected because of it.

This test runs Alembic as a real subprocess (sidestepping the in-process hang)
against a brand-new, empty database created inside the same session-scoped
Postgres container used by every other test.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import uuid

import psycopg2
import pytest

_HERE = pathlib.Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
_ALEMBIC_INI = str(_REPO_ROOT / "engine" / "alembic.ini")


@pytest.fixture
def empty_db_dsn(pg_container):
    """Create and drop a genuinely empty database in the shared container."""
    host = pg_container.get_container_host_ip()
    port = pg_container.get_exposed_port(5432)
    admin_dsn = pg_container.get_connection_url().replace("+psycopg2", "")

    db_name = f"alembic_empty_{uuid.uuid4().hex[:12]}"

    conn = psycopg2.connect(admin_dsn)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(f'CREATE DATABASE "{db_name}"')
    cur.close()
    conn.close()

    dsn = f"postgresql://test:test@{host}:{port}/{db_name}"
    try:
        yield dsn
    finally:
        conn = psycopg2.connect(admin_dsn)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (db_name,),
        )
        cur.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
        cur.close()
        conn.close()


def test_alembic_upgrade_head_on_empty_db(empty_db_dsn):
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", _ALEMBIC_INI, "upgrade", "head"],
        env={**os.environ, "TALOS_DB_DSN": empty_db_dsn},
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"alembic upgrade head failed on an empty database:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    conn = psycopg2.connect(empty_db_dsn)
    cur = conn.cursor()

    cur.execute("SELECT version_num FROM alembic_version")
    (version,) = cur.fetchone()
    assert version == "V0007"

    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'boards' AND column_name = 'model_config'"
    )
    assert cur.fetchone() is not None, "boards.model_config missing after upgrade head"

    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'tasks' AND column_name = 'deliverable'"
    )
    assert cur.fetchone() is not None, "tasks.deliverable missing after upgrade head"

    cur.execute(
        "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'task_spans'"
    )
    (force_rls,) = cur.fetchone()
    assert force_rls is True, "task_spans FORCE ROW LEVEL SECURITY not applied (V0003 didn't run)"

    cur.close()
    conn.close()
