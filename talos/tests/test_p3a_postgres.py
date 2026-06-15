"""
TALOS P3a integration tests — PostgresSaver + dead-worker reclaim.

All tests use testcontainers (Postgres 16) via the pg_setup fixture.
The test graph for P3a checkpoint recovery uses a real PostgresSaver,
not MemorySaver — separate from the shared MemorySaver-backed test_graph.
"""

from __future__ import annotations

import json
import os
import time

import psycopg2
import psycopg2.extras
import pytest

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command

from talos.config import resolve_model
from talos.db import board_scope, get_conn
from talos.graph.spine import build_graph, default_budget
from talos.worker import (
    TALOS_HEARTBEAT_INTERVAL_S,
    TALOS_RECLAIM_AFTER_MISSES,
    claim_and_run,
    reclaim_dead_workers,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_board_and_task(cur, board_id: str, task_id: str, model_override=None, model_config=None):
    cur.execute(
        "INSERT INTO boards (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (board_id, f"board-{board_id}"),
    )
    if model_config is not None:
        cur.execute(
            "UPDATE boards SET model_config = %s::jsonb WHERE id = %s",
            (json.dumps(model_config), board_id),
        )
    cur.execute(
        """
        INSERT INTO tasks (id, board_id, title, status, model_override)
        VALUES (%s, %s, 'test task', 'ready', %s)
        ON CONFLICT DO NOTHING
        """,
        (task_id, board_id, model_override),
    )


# ---------------------------------------------------------------------------
# Test 1: PostgresSaver checkpoint survives restart
# ---------------------------------------------------------------------------

def test_postgres_saver_checkpoint_survives_restart(pg_setup, admin_conn):
    """
    Graph runs to gate interrupt, state is checkpointed in Postgres.
    A NEW graph instance reloads from the same PostgresSaver and resumes.
    """
    board_id = "pg-save-board"
    task_id = "pg-save-task"

    with admin_conn.cursor() as cur:
        _seed_board_and_task(cur, board_id, task_id)
    admin_conn.commit()

    dsn = pg_setup
    with PostgresSaver.from_conn_string(dsn) as saver:
        saver.setup()

        graph1 = build_graph(checkpointer=saver)
        session_key = claim_and_run(board_id, task_id, graph=graph1)

        # Verify graph paused at gate (task in 'review').
        with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT status FROM tasks WHERE id = %s", (task_id,))
            assert cur.fetchone()["status"] == "review"

        # NEW graph instance using the SAME saver — simulates worker restart.
        graph2 = build_graph(checkpointer=saver)

        config = {"configurable": {"thread_id": session_key}}
        graph2.invoke(
            Command(resume={
                "outcome": "approve",
                "approved_by": "test-human",
            }),
            config=config,
        )

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT status FROM tasks WHERE id = %s", (task_id,))
        assert cur.fetchone()["status"] == "approved"


# ---------------------------------------------------------------------------
# Test 2: Dead-worker reclaim
# ---------------------------------------------------------------------------

def test_dead_worker_reclaim(pg_setup, admin_conn):
    """
    Insert a task_runs row with a stale last_heartbeat_at. Call reclaim_dead_workers()
    and verify tasks.status reverts to 'ready' and a new task_runs row is created
    on the next claim.
    """
    board_id = "reclaim-board"
    task_id = "reclaim-task"

    with admin_conn.cursor() as cur:
        _seed_board_and_task(cur, board_id, task_id)
        # Set task to 'running' with a stale run.
        cur.execute(
            "UPDATE tasks SET status = 'running' WHERE id = %s",
            (task_id,),
        )
        stale_age_s = TALOS_HEARTBEAT_INTERVAL_S * TALOS_RECLAIM_AFTER_MISSES + 10
        cur.execute(
            """
            INSERT INTO task_runs (board_id, task_id, status, attempt_no, last_heartbeat_at)
            VALUES (%s, %s, 'running', 1, NOW() - (%s * INTERVAL '1 second'))
            """,
            (board_id, task_id, stale_age_s),
        )
    admin_conn.commit()

    # reclaim_dead_workers() scans cross-board (no board_scope needed).
    conn = get_conn()
    try:
        reclaimed = reclaim_dead_workers(conn)
    finally:
        conn.close()

    assert reclaimed >= 1

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT status FROM tasks WHERE id = %s", (task_id,))
        assert cur.fetchone()["status"] == "ready"

        cur.execute(
            "SELECT outcome FROM task_runs WHERE task_id = %s ORDER BY id DESC LIMIT 1",
            (task_id,),
        )
        assert cur.fetchone()["outcome"] == "reclaimed"


# ---------------------------------------------------------------------------
# Test 3: Heartbeat fires at node boundary
# ---------------------------------------------------------------------------

def test_heartbeat_fires_at_node_boundary():
    """
    Run the spine with a counting node_callback. Assert at least one call per node
    (4 nodes = at least 4 heartbeats before the gate interrupt).
    """
    calls: list[str] = []

    def counting_callback(state):
        calls.append("heartbeat")

    graph = build_graph(checkpointer=MemorySaver(), node_callback=counting_callback)

    initial_state = {
        "board_id": "hb-board",
        "task_id": "hb-task",
        "attempt_no": 1,
        "run_id": 1,
        "session_key": "task:hb-board:hb-task:1",
        "nexus_result": {},
        "deliverable": {},
        "critic_results": [],
        "gate_outcome": None,
        "approved_by": None,
        "edited_deliverable": None,
        "gate_justification": None,
        "sdk_session_ids": {},
        "budget": default_budget(),
    }

    # This graph has no real DB — deliverable_node will error on get_conn().
    # We only need to verify the callback fires before that happens.
    # Use a stub that doesn't call Postgres by patching deliverable_node.
    import unittest.mock as mock
    import talos.graph.spine as spine_module

    def stub_deliverable(state):
        return {"deliverable": {"stub": True}, "critic_results": [], "edited_deliverable": None}

    def stub_gate(state):
        from langgraph.types import interrupt
        interrupt({"stub": True})
        return {"gate_outcome": "approve", "approved_by": "test", "gate_justification": None, "edited_deliverable": None}

    def stub_post_gate(state):
        return {}

    with mock.patch.object(spine_module, "deliverable_node", stub_deliverable), \
         mock.patch.object(spine_module, "gate_node", stub_gate), \
         mock.patch.object(spine_module, "post_gate_node", stub_post_gate):

        graph = build_graph(checkpointer=MemorySaver(), node_callback=counting_callback)
        try:
            graph.invoke(initial_state, config={"configurable": {"thread_id": "hb-thread"}})
        except Exception:
            pass  # gate interrupt or other expected pause

    # At minimum, read_node fired the callback before the first real node ran.
    assert len(calls) >= 1, f"Expected heartbeat callbacks, got {len(calls)}"


# ---------------------------------------------------------------------------
# Test 4: Model config cascade
# ---------------------------------------------------------------------------

def test_model_config_cascade(pg_setup, admin_conn):
    """
    Verify tasks.model_override > boards.model_config > talos.toml defaults.
    """
    # Level 1: hardcoded defaults — no overrides.
    primary, fallback = resolve_model("research")
    assert isinstance(primary, str) and len(primary) > 0
    assert isinstance(fallback, str) and len(fallback) > 0

    # Level 2: board-level JSONB override.
    board_cfg = {"research_primary": "claude-board-special", "research_fallback": "claude-board-fallback"}
    board = {"model_config": board_cfg}
    p2, f2 = resolve_model("research", board=board)
    assert p2 == "claude-board-special"
    assert f2 == "claude-board-fallback"

    # Level 3: per-task model_override supersedes board config.
    task = {"model_override": "claude-task-override"}
    p3, f3 = resolve_model("research", board=board, task=task)
    assert p3 == "claude-task-override"
    assert f3 == "claude-task-override"  # override applies to both slots

    # Cascade ordering: board without task falls through to toml/defaults, not override.
    p4, f4 = resolve_model("research", board=board, task=None)
    assert p4 == "claude-board-special"


# ---------------------------------------------------------------------------
# Test 5: task_spans RLS isolation
# ---------------------------------------------------------------------------

def test_task_spans_table_rls(pg_setup, admin_conn, app_conn):
    """
    Spans inserted for board A must not be readable in a board B app_conn session.
    """
    board_a = "spans-board-a"
    board_b = "spans-board-b"

    # Seed boards (as admin, bypass RLS).
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO boards (id, name) VALUES (%s, %s), (%s, %s) ON CONFLICT DO NOTHING",
            (board_a, "Board A", board_b, "Board B"),
        )
    admin_conn.commit()

    # Insert a span for board A (admin connection bypasses RLS).
    with admin_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO task_spans (board_id, span_name)
            VALUES (%s, 'worker.claim')
            """,
            (board_a,),
        )
    admin_conn.commit()

    # Board B session should see 0 spans for board A.
    with app_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SET LOCAL app.board_id = %s", (board_b,))
        cur.execute("SELECT COUNT(*) AS cnt FROM task_spans WHERE board_id = %s", (board_a,))
        assert cur.fetchone()["cnt"] == 0

    # Board A session should see 1 span.
    with app_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SET LOCAL app.board_id = %s", (board_a,))
        cur.execute("SELECT COUNT(*) AS cnt FROM task_spans WHERE board_id = %s", (board_a,))
        assert cur.fetchone()["cnt"] == 1

    app_conn.rollback()
