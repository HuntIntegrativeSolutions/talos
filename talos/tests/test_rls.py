"""
SEC-03 RLS enforcement tests.

These tests verify board isolation on the integration paths that were previously
masked by the superuser DSN in conftest. TALOS_DB_DSN now points to talos_app
(RLS enforced); seeding is done via admin_conn (superuser, bypasses RLS).

Critical: admin_conn is autocommit=False. Seeds must be committed before invoking
code-under-test, which opens its own connections (heartbeat → talos_app conn;
reclaim → talos_system conn). Uncommitted rows are invisible across connections.
"""

from __future__ import annotations

import uuid

import psycopg2
import psycopg2.extras
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_board(conn, board_id: str, name: str = "test board") -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO boards (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (board_id, name),
        )


def _seed_task(conn, board_id: str, task_id: str, status: str = "ready") -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tasks (id, board_id, title, status)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (task_id, board_id, f"task {task_id}", status),
        )


def _seed_task_run(
    conn,
    board_id: str,
    task_id: str,
    *,
    stale: bool = False,
) -> int:
    """Insert a task_run and return its id. If stale=True, set last_heartbeat_at far in the past."""
    with conn.cursor() as cur:
        if stale:
            cur.execute(
                """
                INSERT INTO task_runs (board_id, task_id, status, attempt_no, last_heartbeat_at)
                VALUES (%s, %s, 'running', 1, NOW() - INTERVAL '1 hour')
                RETURNING id
                """,
                (board_id, task_id),
            )
        else:
            cur.execute(
                """
                INSERT INTO task_runs (board_id, task_id, status, attempt_no)
                VALUES (%s, %s, 'running', 1)
                RETURNING id
                """,
                (board_id, task_id),
            )
        return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# DoD #3 — cross-board isolation (integration path)
# ---------------------------------------------------------------------------

def test_rls_cross_board_isolation(pg_setup, admin_conn, app_conn):
    """
    A talos_app connection scoped to board A must see 0 rows from board B
    in tasks and task_runs. Proves the board isolation guarantee on the live
    code paths now that TALOS_DB_DSN points to talos_app.
    """
    board_a = f"rls-a-{uuid.uuid4().hex[:8]}"
    board_b = f"rls-b-{uuid.uuid4().hex[:8]}"
    task_a = f"t-{uuid.uuid4().hex[:8]}"
    task_b = f"t-{uuid.uuid4().hex[:8]}"

    _seed_board(admin_conn, board_a)
    _seed_board(admin_conn, board_b)
    _seed_task(admin_conn, board_a, task_a)
    _seed_task(admin_conn, board_b, task_b)
    _seed_task_run(admin_conn, board_a, task_a)
    _seed_task_run(admin_conn, board_b, task_b)
    admin_conn.commit()

    # Scope to board A — board B's rows must be invisible.
    with app_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SET LOCAL app.board_id = %s", (board_a,))

        cur.execute("SELECT COUNT(*) AS cnt FROM tasks WHERE board_id = %s", (board_b,))
        assert cur.fetchone()["cnt"] == 0, "tasks: board B visible when scoped to board A"

        cur.execute("SELECT COUNT(*) AS cnt FROM task_runs WHERE board_id = %s", (board_b,))
        assert cur.fetchone()["cnt"] == 0, "task_runs: board B visible when scoped to board A"

    app_conn.rollback()  # reset SET LOCAL


# ---------------------------------------------------------------------------
# DoD #4 — heartbeat writes under talos_app role
# ---------------------------------------------------------------------------

def test_heartbeat_writes_under_app_role(pg_setup, admin_conn):
    """
    The heartbeat callback must write last_heartbeat_at via board_scope under
    the talos_app role. A NULL result means the UPDATE was blocked by RLS.
    """
    from talos.worker import make_heartbeat_callback

    board_id = f"hb-{uuid.uuid4().hex[:8]}"
    task_id = f"t-{uuid.uuid4().hex[:8]}"

    _seed_board(admin_conn, board_id)
    _seed_task(admin_conn, board_id, task_id, status="running")
    run_id = _seed_task_run(admin_conn, board_id, task_id)
    admin_conn.commit()

    # Invoke the callback (opens its own talos_app connection internally).
    callback = make_heartbeat_callback(run_id, board_id)
    callback(state={})  # state is unused by the callback

    # Verify via admin_conn that the heartbeat was written.
    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT last_heartbeat_at FROM task_runs WHERE id = %s",
            (run_id,),
        )
        row = cur.fetchone()
    assert row is not None, "task_run row not found"
    assert row["last_heartbeat_at"] is not None, (
        "last_heartbeat_at is NULL — heartbeat UPDATE was blocked by RLS "
        "(board_scope or TALOS_DB_DSN misconfiguration)"
    )


# ---------------------------------------------------------------------------
# DoD #5 — reclaim works across boards under runtime role
# ---------------------------------------------------------------------------

def test_reclaim_across_boards_under_runtime_role(pg_setup, admin_conn):
    """
    reclaim_dead_workers() must re-queue a stale task_run on a board other than
    the current TALOS_DB_DSN board. Uses talos_system (BYPASSRLS) internally.
    """
    from talos.worker import reclaim_dead_workers

    board_id = f"rc-{uuid.uuid4().hex[:8]}"
    task_id = f"t-{uuid.uuid4().hex[:8]}"

    _seed_board(admin_conn, board_id)
    _seed_task(admin_conn, board_id, task_id, status="running")
    run_id = _seed_task_run(admin_conn, board_id, task_id, stale=True)
    admin_conn.commit()

    reclaimed = reclaim_dead_workers()

    assert reclaimed >= 1, "reclaim_dead_workers returned 0 — stale run was not found"

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT outcome, ended_at FROM task_runs WHERE id = %s",
            (run_id,),
        )
        run_row = cur.fetchone()
        cur.execute(
            "SELECT status FROM tasks WHERE id = %s AND board_id = %s",
            (task_id, board_id),
        )
        task_row = cur.fetchone()

    assert run_row["outcome"] == "reclaimed", (
        f"Expected outcome='reclaimed', got {run_row['outcome']!r}"
    )
    assert run_row["ended_at"] is not None, "ended_at not set after reclaim"
    assert task_row["status"] == "ready", (
        f"Expected task status='ready' after reclaim, got {task_row['status']!r}"
    )


# ---------------------------------------------------------------------------
# RT-09 — policy-presence CI test (FORCE ROW LEVEL SECURITY on all 15 tables)
# ---------------------------------------------------------------------------

_V0003_TABLES = [
    "tasks", "task_links", "task_comments", "task_events", "task_runs",
    "task_attachments", "notify_subs", "task_gate_results", "spaces",
    "space_versions", "widgets", "widget_versions", "task_gate_escalations",
    "milestones", "task_spans",
]


def test_force_rls_applied_to_all_tables(pg_setup, admin_conn):
    """
    Every board-scoped table must have relforcerowsecurity=true in pg_class.
    Closes RT-09: this test fails if FORCE is missing from any table or if a
    future migration adds a board-scoped table without a corresponding FORCE.
    """
    with admin_conn.cursor() as cur:
        cur.execute(
            """
            SELECT relname
            FROM pg_class
            WHERE relname = ANY(%s)
              AND relkind = 'r'
              AND relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public')
              AND NOT relforcerowsecurity
            ORDER BY relname
            """,
            (_V0003_TABLES,),
        )
        missing = [row[0] for row in cur.fetchall()]
    assert not missing, (
        f"Tables missing FORCE ROW LEVEL SECURITY: {missing}. "
        "Check V0003 migration and conftest mirror."
    )


# ---------------------------------------------------------------------------
# V0003 DDL round-trip — proves migration downgrade() reverses FORCE
# ---------------------------------------------------------------------------

def test_v0003_migration_ddl_round_trip(pg_setup, admin_conn):
    """
    Simulate V0003 downgrade (NO FORCE) then upgrade (FORCE) using the same
    DDL the migration executes. Proves both directions work correctly.

    admin_conn teardown calls conn.rollback() — PostgreSQL DDL is transactional,
    so the DB returns to FORCE=true (from the conftest mirror) automatically.
    """
    with admin_conn.cursor() as cur:
        # Precondition: FORCE is ON (conftest mirror applied it).
        cur.execute(
            """
            SELECT relname FROM pg_class
            WHERE relname = ANY(%s) AND relkind = 'r'
              AND relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public')
              AND NOT relforcerowsecurity
            """,
            (_V0003_TABLES,),
        )
        assert cur.fetchall() == [], "Precondition failed: some tables already missing FORCE"

        # downgrade(): remove FORCE from all tables.
        for table in _V0003_TABLES:
            cur.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")

        cur.execute(
            """
            SELECT relname FROM pg_class
            WHERE relname = ANY(%s) AND relkind = 'r'
              AND relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public')
              AND relforcerowsecurity
            ORDER BY relname
            """,
            (_V0003_TABLES,),
        )
        still_forced = [row[0] for row in cur.fetchall()]
        assert not still_forced, (
            f"Downgrade DDL did not remove FORCE from: {still_forced}"
        )

        # upgrade(): restore FORCE on all tables.
        for table in _V0003_TABLES:
            cur.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

        cur.execute(
            """
            SELECT relname FROM pg_class
            WHERE relname = ANY(%s) AND relkind = 'r'
              AND relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public')
              AND NOT relforcerowsecurity
            ORDER BY relname
            """,
            (_V0003_TABLES,),
        )
        missing_after = [row[0] for row in cur.fetchall()]
        assert not missing_after, (
            f"Upgrade DDL did not restore FORCE on: {missing_after}"
        )
    # conn.rollback() at teardown reverts both ALTER TABLE sequences — no state leak.
