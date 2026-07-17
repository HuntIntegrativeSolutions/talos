"""
TALOS P1 verification — six tests that prove the Guardian doctrine end-to-end.

All six must pass before P2 begins.
"""

from __future__ import annotations

import os
import uuid

import psycopg2.extras
import pytest
from fastapi.testclient import TestClient

import talos.graph.spine as spine_module
from talos.critics.citations_resolvable import CriticResult, citations_resolvable
from talos.graph.spine import STUB_DOCUMENT, _derive_summary, post_gate_node, read_node


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_board_and_task(conn, board_id: str, task_id: str) -> None:
    """Insert a board and one ready task using the superuser (owner) connection."""
    conn.autocommit = False
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO boards (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (board_id, f"Board {board_id}"),
        )
        cur.execute(
            """
            INSERT INTO tasks (id, board_id, title, status)
            VALUES (%s, %s, %s, 'ready')
            ON CONFLICT DO NOTHING
            """,
            (task_id, board_id, f"Task {task_id}"),
        )
    conn.commit()


def _query_task(admin_conn, board_id: str, task_id: str) -> dict:
    """Read a task row as admin (bypasses RLS — used only for assertions)."""
    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM tasks WHERE id = %s AND board_id = %s",
            (task_id, board_id),
        )
        return dict(cur.fetchone())


def _count_rows(admin_conn, table: str, task_id: str, **extra_filters) -> int:
    where = "task_id = %s"
    params = [task_id]
    for col, val in extra_filters.items():
        where += f" AND {col} = %s"
        params.append(val)
    with admin_conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params)
        return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# Test 1: full happy path
# ---------------------------------------------------------------------------

def test_spine_happy_path(pg_setup, admin_conn, test_graph, human_jwt):
    board_id = f"b-{uuid.uuid4().hex[:8]}"
    task_id = f"t-{uuid.uuid4().hex[:8]}"
    _seed_board_and_task(admin_conn, board_id, task_id)

    # Run the worker under the NEXUS stub — graph pauses at gate_node.
    os.environ["TALOS_NEXUS_STUB"] = "1"
    from talos.worker import claim_and_run

    session_key = claim_and_run(board_id, task_id, graph=test_graph)

    # Task must now be in 'review'.
    task = _query_task(admin_conn, board_id, task_id)
    assert task["status"] == "review", f"expected review, got {task['status']}"

    # Simulate human approval via the gate API.
    from talos import api as api_module
    client = TestClient(api_module.app)

    resp = client.post(
        f"/boards/{board_id}/tasks/{task_id}/gate",
        json={"outcome": "approve"},
        headers={"X-Human-Session": human_jwt},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["approved_by"] == "thunt"

    # Task must be approved; exactly one gate result row and one event row.
    task = _query_task(admin_conn, board_id, task_id)
    assert task["status"] == "approved"
    assert task["approved_by"] == "thunt"
    assert task["approved_at"] is not None

    gate_rows = _count_rows(admin_conn, "task_gate_results", task_id)
    # Registry runs 3 critics (citations_resolvable + no_live_write_in_deliverable +
    # no_client_identifiers_in_shared, added P4b/RT-06 — a no-op pass for this
    # non-promotion deliverable since client_identifiers is None).
    assert gate_rows == 3, f"expected 3 task_gate_results rows (one per critic), got {gate_rows}"

    event_rows = _count_rows(admin_conn, "task_events", task_id, kind="gate_outcome")
    assert event_rows == 1, f"expected 1 gate_outcome event, got {event_rows}"


# ---------------------------------------------------------------------------
# Test 2: read_node stub — no Postgres writes
# ---------------------------------------------------------------------------

def test_nexus_stub_read(pg_setup, admin_conn):
    board_id = f"b-{uuid.uuid4().hex[:8]}"
    task_id = f"t-{uuid.uuid4().hex[:8]}"
    _seed_board_and_task(admin_conn, board_id, task_id)

    os.environ["TALOS_NEXUS_STUB"] = "1"

    # Call read_node directly with a minimal state dict.
    state = {
        "board_id": board_id,
        "task_id": task_id,
        "attempt_no": 1,
        "run_id": 0,
        "session_key": f"task:{board_id}:{task_id}:1",
        "nexus_result": {},
        "deliverable": {},
        "critic_results": [],
        "gate_outcome": None,
        "approved_by": None,
    }
    result = read_node(state)

    assert result["nexus_result"] == {"document": STUB_DOCUMENT, "status": "confirmed"}

    # read_node must not write anything to the DB.
    assert _count_rows(admin_conn, "task_events", task_id) == 0
    assert _count_rows(admin_conn, "task_gate_results", task_id) == 0


# ---------------------------------------------------------------------------
# Test 3: gate endpoint rejects non-human callers
# ---------------------------------------------------------------------------

def test_gate_rejects_non_human_caller(pg_setup, admin_conn, test_graph, human_jwt):
    board_id = f"b-{uuid.uuid4().hex[:8]}"
    task_id = f"t-{uuid.uuid4().hex[:8]}"
    _seed_board_and_task(admin_conn, board_id, task_id)

    os.environ["TALOS_NEXUS_STUB"] = "1"
    from talos.worker import claim_and_run
    from talos import api as api_module
    import jwt as _jwt_lib
    import os as _os

    claim_and_run(board_id, task_id, graph=test_graph)

    client = TestClient(api_module.app)
    gate_url = f"/boards/{board_id}/tasks/{task_id}/gate"

    # No header → 403
    resp = client.post(gate_url, json={"outcome": "approve"})
    assert resp.status_code == 403, resp.text

    # Invalid JWT string → 403
    resp = client.post(
        gate_url,
        json={"outcome": "approve"},
        headers={"X-Human-Session": "not.a.valid.jwt"},
    )
    assert resp.status_code == 403, "expected 403 for malformed JWT"

    # Service-class JWT (not human) → 403
    from datetime import datetime, timedelta, timezone
    _secret = _os.environ["TALOS_JWT_SECRET"]
    _now = datetime.now(timezone.utc)
    service_token = _jwt_lib.encode(
        {"sub": "svc", "token_class": "service", "iat": _now, "exp": _now + timedelta(hours=1)},
        _secret, algorithm="HS256",
    )
    resp = client.post(
        gate_url,
        json={"outcome": "approve"},
        headers={"X-Human-Session": service_token},
    )
    assert resp.status_code == 403, "expected 403 for service-class JWT"

    # Legitimate human JWT → 200
    resp = client.post(
        gate_url,
        json={"outcome": "approve"},
        headers={"X-Human-Session": human_jwt},
    )
    assert resp.status_code == 200, resp.text

    # approved_by must come from the JWT sub claim, never self-asserted.
    task = _query_task(admin_conn, board_id, task_id)
    assert task["approved_by"] == "thunt"


# ---------------------------------------------------------------------------
# Test 4: post_gate_node idempotency
# ---------------------------------------------------------------------------

def test_post_gate_idempotency(pg_setup, admin_conn, test_graph):
    board_id = f"b-{uuid.uuid4().hex[:8]}"
    task_id = f"t-{uuid.uuid4().hex[:8]}"
    _seed_board_and_task(admin_conn, board_id, task_id)

    # We need a real task_runs row to satisfy the FK on task_events.run_id.
    # Insert one manually as admin (bypasses RLS).
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO task_runs (board_id, task_id, status, attempt_no) "
            "VALUES (%s, %s, 'running', 1) RETURNING id",
            (board_id, task_id),
        )
        run_id = cur.fetchone()[0]
        # Also move task to 'review' so the approved_at check is meaningful.
        cur.execute(
            "UPDATE tasks SET status='review' WHERE id=%s AND board_id=%s",
            (task_id, board_id),
        )
    admin_conn.commit()

    state = {
        "board_id": board_id,
        "task_id": task_id,
        "attempt_no": 1,
        "run_id": run_id,
        "session_key": f"task:{board_id}:{task_id}:1",
        "nexus_result": {"tag": "MOCK_TAG", "status": "confirmed"},
        "deliverable": {"citations": [{"finding_id": "MOCK_TAG", "status": "confirmed"}]},
        "critic_results": [{"passed": True, "reason": "all citations confirmed", "waivable": True}],
        "gate_outcome": "approve",
        "approved_by": "thunt",
    }

    # First call — should write the event and update the task.
    post_gate_node(state)

    task_after_first = _query_task(admin_conn, board_id, task_id)
    approved_at_first = task_after_first["approved_at"]
    assert approved_at_first is not None

    # Second call — idempotency guard should short-circuit, no new writes.
    post_gate_node(state)

    assert _count_rows(admin_conn, "task_events", task_id, kind="gate_outcome") == 1, (
        "second post_gate_node call must not insert a second event row"
    )

    task_after_second = _query_task(admin_conn, board_id, task_id)
    assert task_after_second["approved_at"] == approved_at_first, (
        "approved_at must not be overwritten on second call"
    )


# ---------------------------------------------------------------------------
# Test 5: RLS isolation — board-A scope returns zero rows for board-B data
# ---------------------------------------------------------------------------

def test_rls_isolation(pg_setup, admin_conn, app_conn):
    board_a = f"b-{uuid.uuid4().hex[:8]}"
    board_b = f"b-{uuid.uuid4().hex[:8]}"
    task_a = f"t-{uuid.uuid4().hex[:8]}"
    task_b = f"t-{uuid.uuid4().hex[:8]}"

    # Seed both boards as admin (owner — bypasses RLS).
    _seed_board_and_task(admin_conn, board_a, task_a)
    _seed_board_and_task(admin_conn, board_b, task_b)

    # Query with talos_app role scoped to board-A.
    # The board_isolation policy uses current_setting('app.board_id', true).
    app_conn.autocommit = False
    with app_conn.cursor() as cur:
        cur.execute("SET LOCAL app.board_id = %s", (board_a,))
        cur.execute("SELECT COUNT(*) FROM tasks WHERE board_id = %s", (board_b,))
        count = cur.fetchone()[0]
    app_conn.rollback()  # reset SET LOCAL

    assert count == 0, (
        f"RLS should block board-B rows when app.board_id=board-A; got {count}"
    )


# ---------------------------------------------------------------------------
# Test 6: critic blocks unconfirmed citation
# ---------------------------------------------------------------------------

def test_critic_blocks_unconfirmed_citation(pg_setup, admin_conn):
    board_id = f"b-{uuid.uuid4().hex[:8]}"
    task_id = f"t-{uuid.uuid4().hex[:8]}"
    _seed_board_and_task(admin_conn, board_id, task_id)

    # Deliverable with a 'proposed' citation — not yet confirmed.
    deliverable = {
        "citations": [{"finding_id": "F1", "status": "proposed"}],
        "summary": "stub",
    }

    result: CriticResult = citations_resolvable(deliverable, nexus_client=None)

    assert result.passed is False
    assert "proposed" in result.reason
    assert "F1" in result.reason


# ---------------------------------------------------------------------------
# _derive_summary — pure function, direct unit tests
# ---------------------------------------------------------------------------

def test_derive_summary_prefers_first_heading():
    doc = "Some preamble text\n\n# Real Title\n\nBody."
    assert _derive_summary(doc) == "Real Title"


def test_derive_summary_falls_back_to_first_nonempty_line_when_no_heading():
    doc = "\n\nFirst real line.\nSecond line."
    assert _derive_summary(doc) == "First real line."


def test_derive_summary_collapses_whitespace():
    doc = "# Title   with\n   extra   spaces"
    assert _derive_summary(doc) == "Title with"


def test_derive_summary_truncates_with_ellipsis_over_160_chars():
    long_line = "x" * 200
    result = _derive_summary(f"# {long_line}")
    assert len(result) == 160
    assert result.endswith("…")


def test_derive_summary_empty_document_returns_empty_string():
    assert _derive_summary("") == ""


# ---------------------------------------------------------------------------
# deliverable_node — empty live document must not produce a silently blank gate
# ---------------------------------------------------------------------------

def test_deliverable_node_empty_live_document_gets_alarming_summary(
    pg_setup, admin_conn, test_graph, monkeypatch
):
    board_id = f"b-{uuid.uuid4().hex[:8]}"
    task_id = f"t-{uuid.uuid4().hex[:8]}"
    _seed_board_and_task(admin_conn, board_id, task_id)

    os.environ["TALOS_NEXUS_STUB"] = "1"
    monkeypatch.setattr(spine_module, "STUB_DOCUMENT", "")

    from talos.worker import claim_and_run

    claim_and_run(board_id, task_id, graph=test_graph)

    task = _query_task(admin_conn, board_id, task_id)
    assert task["status"] == "review"
    deliverable = task["deliverable"]
    assert deliverable["document"] == ""
    assert deliverable["summary"] == "(model returned an empty document — nothing to review)"

    # Task must still have approved_at = NULL (gate not satisfied).
    task = _query_task(admin_conn, board_id, task_id)
    assert task["approved_at"] is None, (
        "approved_at must remain NULL when critic fails"
    )
