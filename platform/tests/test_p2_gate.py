"""
TALOS P2 gate tests — Definition of Done.

Four tests that prove the complete gate works:
  1. Meta-critic invariant (CI blocker)
  2. All five outcomes write correct columns
  3. Fail-closed on NEXUS unavailable
  4. Contradiction filter deduplicates a flood
"""

from __future__ import annotations

import os
import uuid

import psycopg2.extras
import pytest
from fastapi.testclient import TestClient

from platform.critics.citations_resolvable import citations_resolvable


# ---------------------------------------------------------------------------
# Helpers (mirrors test_spine.py to keep each file self-contained)
# ---------------------------------------------------------------------------

def _seed(conn, board_id: str, task_id: str) -> None:
    # Do not set conn.autocommit — the admin_conn fixture already creates it with
    # autocommit=False. Calling set_session inside an open transaction (e.g. after
    # a read) raises ProgrammingError. Callers must commit/rollback first.
    conn.rollback()  # close any open read-transaction before writing
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


def _query_task(conn, board_id: str, task_id: str) -> dict:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM tasks WHERE id = %s AND board_id = %s",
            (task_id, board_id),
        )
        return dict(cur.fetchone())


def _count_rows(conn, table: str, task_id: str, **filters) -> int:
    where = "task_id = %s"
    params = [task_id]
    for col, val in filters.items():
        where += f" AND {col} = %s"
        params.append(val)
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params)
        return cur.fetchone()[0]


def _gate(client, board_id, task_id, headers=None, **payload) -> object:
    return client.post(
        f"/boards/{board_id}/tasks/{task_id}/gate",
        json=payload,
        headers=headers or {"X-Human-Session": "thunt"},
    )


# ---------------------------------------------------------------------------
# Test 1: Meta-critic invariant (CI blocker — structural enforcer for RT-02)
# ---------------------------------------------------------------------------

def test_meta_critic_safety_not_waivable():
    from platform.critics.registry import CriticSpec, register

    def _dummy(deliverable, nexus_client=None):
        from platform.critics.citations_resolvable import CriticResult
        return CriticResult(passed=True, reason="x")

    with pytest.raises(ValueError, match="waivable=False"):
        register(CriticSpec(
            name="bad_safety_critic_p2",
            fn=_dummy,
            required=True,
            safety_class=True,
            waivable=True,
        ))


# ---------------------------------------------------------------------------
# Test 2: All five outcomes write correct columns
# ---------------------------------------------------------------------------

def test_all_five_outcomes_write_correct_columns(pg_setup, admin_conn, test_graph):
    os.environ["TALOS_NEXUS_STUB"] = "1"
    from platform import api as api_module
    from platform.worker import claim_and_run

    client = TestClient(api_module.app)

    # ------------------------------------------------------------------ approve
    b, t = f"b-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    _seed(admin_conn, b, t)
    claim_and_run(b, t, graph=test_graph)
    resp = _gate(client, b, t, outcome="approve")
    assert resp.status_code == 200, resp.text
    task = _query_task(admin_conn, b, t)
    assert task["status"] == "approved"
    assert task["approved_by"] == "thunt"
    assert task["approved_at"] is not None
    assert _count_rows(admin_conn, "task_events", t, kind="gate_outcome") == 1

    # ------------------------------------------------------------------ reject
    b, t = f"b-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    _seed(admin_conn, b, t)
    claim_and_run(b, t, graph=test_graph)
    resp = _gate(client, b, t, outcome="reject", reason="needs more detail")
    assert resp.status_code == 200, resp.text
    task = _query_task(admin_conn, b, t)
    assert task["status"] == "rejected"
    assert task["rejected_by"] == "thunt"
    assert task["rejection_reason"] == "needs more detail"
    # Re-run post_gate_node — must be idempotent on rejected tasks.
    from platform.graph.spine import post_gate_node
    state = {
        "board_id": b, "task_id": t, "run_id": 0,
        "gate_outcome": "reject", "approved_by": "thunt",
        "gate_justification": "needs more detail",
        "edited_deliverable": None, "session_key": "",
        "nexus_result": {}, "deliverable": {}, "critic_results": [], "attempt_no": 1,
    }
    post_gate_node(state)  # must not raise or write again
    assert _count_rows(admin_conn, "task_events", t, kind="gate_outcome") == 1

    # ------------------------------------------------------------------ waive
    b, t = f"b-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    _seed(admin_conn, b, t)
    claim_and_run(b, t, graph=test_graph)
    # All critics pass on the default stub, so nothing to waive.
    # Force a waivable critic to fail by injecting a proposed citation via edit.
    resp = _gate(client, b, t, outcome="edit",
                 new_deliverable={"citations": [{"finding_id": "X", "status": "proposed"}],
                                  "summary": "edited"})
    assert resp.status_code == 200, resp.text
    # citations_resolvable now fails (waivable=True). Waive it.
    resp = _gate(client, b, t, outcome="waive", justification="risk accepted")
    assert resp.status_code == 200, resp.text
    task = _query_task(admin_conn, b, t)
    assert task["status"] == "approved"
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM task_gate_results WHERE task_id=%s AND verdict='waived'",
            (t,),
        )
        assert cur.fetchone()[0] >= 1
    assert _count_rows(admin_conn, "task_events", t, kind="gate_waiver") == 1

    # ------------------------------------------------------------------ edit → approve
    b, t = f"b-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    _seed(admin_conn, b, t)
    claim_and_run(b, t, graph=test_graph)
    resp = _gate(client, b, t, outcome="edit",
                 new_deliverable={"citations": [{"finding_id": "REAL_TAG",
                                                 "status": "confirmed"}],
                                  "summary": "updated"})
    assert resp.status_code == 200, resp.text
    task = _query_task(admin_conn, b, t)
    assert task["status"] == "review", "edit should leave task in review"
    assert _count_rows(admin_conn, "task_events", t, kind="gate_edit") == 1
    # New critic rows from the re-run must exist.
    assert _count_rows(admin_conn, "task_gate_results", t) >= 2
    # Now approve.
    resp = _gate(client, b, t, outcome="approve")
    assert resp.status_code == 200, resp.text
    task = _query_task(admin_conn, b, t)
    assert task["status"] == "approved"

    # ------------------------------------------------------------------ escalate
    # The escalate path requires a failing safety critic. The default stub
    # deliverable has no live_write=True, so all critics pass. Force the safety
    # critic to fail via an edit that injects live_write=True, then escalate.
    b, t = f"b-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    _seed(admin_conn, b, t)
    claim_and_run(b, t, graph=test_graph)
    # Step 1: edit to inject live_write=True — forces no_live_write_in_deliverable to fail.
    resp = _gate(client, b, t, outcome="edit",
                 new_deliverable={
                     "live_write": True,
                     "citations": [{"finding_id": "MOCK_TAG", "status": "confirmed"}],
                 })
    assert resp.status_code == 200, resp.text
    task = _query_task(admin_conn, b, t)
    assert task["status"] == "review"
    # Waive must be blocked because no_live_write_in_deliverable has waivable=False.
    resp = _gate(client, b, t, outcome="waive", justification="try to waive safety critic")
    assert resp.status_code == 409, f"expected 409 from safety critic block, got {resp.status_code}"
    # Step 2: escalate with justification.
    resp = _gate(client, b, t, outcome="escalate",
                 justification="accepted after full manual review")
    assert resp.status_code == 200, resp.text
    task = _query_task(admin_conn, b, t)
    assert task["status"] == "approved"
    # task_gate_escalations must have 1 row with the justification.
    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM task_gate_escalations WHERE task_id = %s", (t,)
        )
        esc_rows = [dict(r) for r in cur.fetchall()]
    assert len(esc_rows) >= 1
    assert esc_rows[0]["justification"] == "accepted after full manual review"
    # Synthetic pass row must exist for no_live_write_in_deliverable.
    with admin_conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) FROM task_gate_results
            WHERE task_id = %s AND critic_name = 'no_live_write_in_deliverable'
              AND verdict = 'pass' AND safety_class = true
            """,
            (t,),
        )
        assert cur.fetchone()[0] >= 1
    assert _count_rows(admin_conn, "task_events", t, kind="gate_escalation") == 1


# ---------------------------------------------------------------------------
# Test 3: Fail-closed on NEXUS unavailable
# ---------------------------------------------------------------------------

def test_citations_resolvable_fail_closed_on_nexus_unavailable(pg_setup, admin_conn):
    def bad_client(citations):
        raise ConnectionError("timeout")

    deliverable = {"citations": [{"finding_id": "TAG-001", "status": "confirmed"}]}
    result = citations_resolvable(deliverable, nexus_client=bad_client)
    assert result.passed is False
    assert "fail closed" in result.reason


# ---------------------------------------------------------------------------
# Test 4: Contradiction filter deduplicates a flood
# ---------------------------------------------------------------------------

def test_contradiction_filter_dedupes_flood():
    from platform.critics.contradiction_filter import filter_contradictions

    base_time = 1000.0
    findings = [
        {"finding_id": "F1", "kind": "nexus_vs_episodic", "severity": "HIGH",
         "detected_at": base_time + i}
        for i in range(100)
    ]
    result = filter_contradictions(findings, window_seconds=300)
    assert len(result) == 1
    assert result[0]["detected_at"] == base_time + 99
