"""
TALOS P3d tests — PM hooks and observability (spans, webhook alerting).

All tests run with TALOS_NEXUS_STUB=1 and inspect talos.spans._STUB_BUFFER.
Tests that need a real DB use the testcontainers fixtures from conftest.py.
"""

from __future__ import annotations

import asyncio
import json
import unittest.mock as mock
from typing import Any

import psycopg2.extras
import pytest

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

import talos.spans as spans_module
from talos.spans import SpanContext, clear_stub_buffer, emit_span
from talos.graph.spine import build_graph, default_budget
from talos.worker import claim_and_run


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_stub_buffer():
    """Clear the in-memory span buffer before each test."""
    clear_stub_buffer()
    yield
    clear_stub_buffer()


def _seed(cur, board_id: str, task_id: str):
    cur.execute(
        "INSERT INTO boards (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (board_id, f"board-{board_id}"),
    )
    cur.execute(
        """
        INSERT INTO tasks (id, board_id, title, status)
        VALUES (%s, %s, 'obs test', 'ready')
        ON CONFLICT DO NOTHING
        """,
        (task_id, board_id),
    )


# ---------------------------------------------------------------------------
# Test 1: all span types present on happy path
# ---------------------------------------------------------------------------

def test_all_spans_emitted_on_happy_path(pg_setup, admin_conn):
    """
    Run a full approve flow; verify each of the 11 minimum span types is present
    in the stub buffer.
    """
    board_id = "obs-board-1"
    task_id = "obs-task-1"

    with admin_conn.cursor() as cur:
        _seed(cur, board_id, task_id)
    admin_conn.commit()

    graph = build_graph(checkpointer=MemorySaver())

    session_key = claim_and_run(board_id, task_id, graph=graph)

    config = {"configurable": {"thread_id": session_key}}
    graph.invoke(
        Command(resume={"outcome": "approve", "approved_by": "test-human"}),
        config=config,
    )

    span_names = [s["span_name"] for s in spans_module._STUB_BUFFER]

    required_spans = [
        "worker.claim",
        "spine.node.read_node.entry",
        "spine.node.read_node.exit",
        "spine.node.deliverable_node.entry",
        "spine.node.deliverable_node.exit",
        "spine.node.gate_node.entry",
        "spine.node.gate_node.exit",
        "spine.node.post_gate_node.entry",
        "spine.node.post_gate_node.exit",
        "spine.gate.interrupt",
        "spine.gate.resume",
        "spine.post_gate.write",
        "llm.call",
    ]

    for span in required_spans:
        # Use prefix match for critic spans (spine.critic.{name})
        if span.startswith("spine.critic."):
            assert any(s.startswith("spine.critic.") for s in span_names), (
                f"No spine.critic.* spans found; buffer: {span_names}"
            )
        else:
            assert span in span_names, (
                f"Expected span {span!r} not found; buffer: {span_names}"
            )


# ---------------------------------------------------------------------------
# Test 2: escalation webhook called on gate_outcome=escalate
# ---------------------------------------------------------------------------

def test_escalation_webhook_called(pg_setup, admin_conn, monkeypatch):
    """
    Trigger gate_outcome=escalate; verify exactly one POST to the webhook URL
    with the required body fields.
    """
    board_id = "obs-board-2"
    task_id = "obs-task-2"

    with admin_conn.cursor() as cur:
        _seed(cur, board_id, task_id)
    admin_conn.commit()

    webhook_url = "http://test-webhook.internal/alert"
    monkeypatch.setenv("TALOS_ESCALATION_WEBHOOK_URL", webhook_url)

    posted_payloads: list[dict] = []

    def fake_post(url, json=None, timeout=None):
        posted_payloads.append(json or {})
        resp = mock.MagicMock()
        resp.status_code = 200
        return resp

    graph = build_graph(checkpointer=MemorySaver())

    session_key = claim_and_run(board_id, task_id, graph=graph)

    config = {"configurable": {"thread_id": session_key}}
    with mock.patch("requests.post", side_effect=fake_post):
        graph.invoke(
            Command(resume={
                "outcome": "escalate",
                "approved_by": "test-human",
                "justification": "Testing escalation webhook",
            }),
            config=config,
        )

    assert len(posted_payloads) == 1, f"Expected 1 webhook POST, got {len(posted_payloads)}"
    payload = posted_payloads[0]
    assert payload["event"] == "gate_escalated"
    assert payload["board_id"] == board_id
    assert payload["task_id"] == task_id
    assert "escalated_at" in payload


# ---------------------------------------------------------------------------
# Test 3: webhook failure does not block gate transition
# ---------------------------------------------------------------------------

def test_webhook_failure_does_not_block_gate(pg_setup, admin_conn, monkeypatch):
    """
    Webhook returns HTTP 500 — gate transition still completes and task is approved.
    """
    board_id = "obs-board-3"
    task_id = "obs-task-3"

    with admin_conn.cursor() as cur:
        _seed(cur, board_id, task_id)
    admin_conn.commit()

    monkeypatch.setenv("TALOS_ESCALATION_WEBHOOK_URL", "http://bad-webhook.internal/fail")

    def fake_post_fail(url, json=None, timeout=None):
        raise ConnectionError("webhook unreachable")

    graph = build_graph(checkpointer=MemorySaver())

    session_key = claim_and_run(board_id, task_id, graph=graph)

    config = {"configurable": {"thread_id": session_key}}
    with mock.patch("requests.post", side_effect=fake_post_fail):
        # Should not raise even though webhook fails.
        graph.invoke(
            Command(resume={
                "outcome": "escalate",
                "approved_by": "test-human",
                "justification": "Webhook failure test",
            }),
            config=config,
        )

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT status FROM tasks WHERE id = %s", (task_id,))
        assert cur.fetchone()["status"] == "approved"


# ---------------------------------------------------------------------------
# Test 4: on_task_approved hook is called
# ---------------------------------------------------------------------------

def test_hook_on_task_approved(pg_setup, admin_conn):
    """
    Register an on_task_approved hook; run an approve flow; verify hook was called
    with the correct payload fields.
    """
    board_id = "obs-board-4"
    task_id = "obs-task-4"

    with admin_conn.cursor() as cur:
        _seed(cur, board_id, task_id)
    admin_conn.commit()

    hook_payloads: list[dict] = []

    async def my_hook(payload: dict) -> None:
        hook_payloads.append(payload)

    from talos.hooks import default_registry, HookRegistry

    # Use a fresh registry per test to avoid cross-test contamination.
    test_registry = HookRegistry()
    test_registry.register("on_task_approved", my_hook)

    graph = build_graph(checkpointer=MemorySaver())

    session_key = claim_and_run(board_id, task_id, graph=graph)

    config = {"configurable": {"thread_id": session_key}}
    with mock.patch("talos.hooks.default_registry", test_registry):
        graph.invoke(
            Command(resume={"outcome": "approve", "approved_by": "test-human"}),
            config=config,
        )

    assert len(hook_payloads) == 1, f"Expected 1 hook call, got {len(hook_payloads)}"
    payload = hook_payloads[0]
    assert payload["board_id"] == board_id
    assert payload["task_id"] == task_id
    assert payload["outcome"] == "approve"
    assert payload["approved_by"] == "test-human"


# ---------------------------------------------------------------------------
# Test 5: span RLS isolation (Postgres)
# ---------------------------------------------------------------------------

def test_span_rls_isolation(pg_setup, admin_conn, app_conn):
    """
    Spans emitted for board A must not be readable from a board B session.
    """
    board_a = "obs-rls-a"
    board_b = "obs-rls-b"

    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO boards (id, name) VALUES (%s, %s), (%s, %s) ON CONFLICT DO NOTHING",
            (board_a, "A", board_b, "B"),
        )
    admin_conn.commit()

    from talos.db import board_scope, get_conn
    conn = get_conn()
    try:
        with board_scope(conn, board_a) as cur:
            cur.execute(
                "INSERT INTO task_spans (board_id, span_name) VALUES (%s, 'worker.claim')",
                (board_a,),
            )
    finally:
        conn.close()

    with app_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SET LOCAL app.board_id = %s", (board_b,))
        cur.execute("SELECT COUNT(*) AS cnt FROM task_spans WHERE board_id = %s", (board_a,))
        assert cur.fetchone()["cnt"] == 0, "Board B session should not see board A spans"

    with app_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SET LOCAL app.board_id = %s", (board_a,))
        cur.execute("SELECT COUNT(*) AS cnt FROM task_spans WHERE board_id = %s", (board_a,))
        assert cur.fetchone()["cnt"] == 1

    app_conn.rollback()


# ---------------------------------------------------------------------------
# Test 6: llm.call span has latency_ms > 0
# ---------------------------------------------------------------------------

def test_llm_call_span_has_latency(pg_setup, admin_conn):
    """
    After running a flow with TALOS_NEXUS_STUB=1, the llm.call span must have
    latency_ms > 0 in the stub buffer.
    """
    board_id = "obs-board-6"
    task_id = "obs-task-6"

    with admin_conn.cursor() as cur:
        _seed(cur, board_id, task_id)
    admin_conn.commit()

    graph = build_graph(checkpointer=MemorySaver())

    claim_and_run(board_id, task_id, graph=graph)

    llm_spans = [s for s in spans_module._STUB_BUFFER if s["span_name"] == "llm.call"]
    assert len(llm_spans) >= 1, f"No llm.call spans found; buffer: {[s['span_name'] for s in spans_module._STUB_BUFFER]}"

    for span in llm_spans:
        assert span.get("latency_ms") is not None and span["latency_ms"] > 0, (
            f"llm.call span missing latency_ms > 0: {span}"
        )


# ---------------------------------------------------------------------------
# Test 7/8 (live-mode plumbing landing): emit_span persists via a
# self-managed connection when no db_conn is passed -- the shape every real
# call site in spine.py/worker.py/llm.py uses. TALOS_NEXUS_STUB is unset so
# emit_span takes the live-mode path instead of the stub buffer.
# ---------------------------------------------------------------------------

def test_emit_span_persists_without_explicit_db_conn(pg_setup, admin_conn, monkeypatch):
    """
    Calling emit_span with no db_conn (the shape every spine.py/worker.py/
    llm.py call site uses) must open its own connection, scope it via
    board_scope so the RLS WITH CHECK passes, and land a real row -- not
    silently drop the span.
    """
    monkeypatch.delenv("TALOS_NEXUS_STUB", raising=False)

    board_id = "obs-board-7"
    task_id = "obs-task-7"
    with admin_conn.cursor() as cur:
        _seed(cur, board_id, task_id)
    admin_conn.commit()

    ctx = SpanContext(board_id=board_id, task_id=task_id, run_id=None)
    result_id = emit_span(ctx, "worker.claim", payload={"note": "no explicit db_conn"})

    assert result_id != -1

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT board_id, span_name FROM task_spans WHERE id = %s",
            (result_id,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row["board_id"] == board_id
    assert row["span_name"] == "worker.claim"


def test_emit_span_logs_and_drops_on_db_failure(monkeypatch):
    """Span persistence must never fail a task -- a DB error acquiring the
    self-managed connection (or during insert) is logged and swallowed."""
    monkeypatch.delenv("TALOS_NEXUS_STUB", raising=False)

    def _raise_get_conn(*args, **kwargs):
        raise OSError("db unreachable")

    monkeypatch.setattr("talos.db.get_conn", _raise_get_conn)

    ctx = SpanContext(board_id="doesnt-matter", task_id="t1", run_id=None)
    result = emit_span(ctx, "worker.claim")

    assert result == -1
