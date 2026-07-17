"""
TALOS P7a gate-UI endpoint tests.

Covers the additive read/write surface the minimal gate-approval UI needs:
  1. deliverable persisted on review entry, refreshed on edit re-entry
  2. worker.py's error-escalation paths also set review_entered_at
  3. GET .../gate includes waivable/safety_class per critic + top-level deliverable
  4. GET .../review-queue orders oldest-first, computes overdue, requires human JWT
  5. GET/PATCH .../sla round-trip including explicit null-clear, requires human JWT
  6. SMTP notification: no-op without TALOS_SMTP_HOST, swallows errors when configured
"""

from __future__ import annotations

import os
import uuid
from unittest import mock

import jwt as pyjwt
import psycopg2.extras
import pytest
from fastapi.testclient import TestClient


def _seed(conn, board_id: str, task_id: str) -> None:
    conn.rollback()
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


def _gate(client, board_id, task_id, human_jwt, headers=None, **payload):
    return client.post(
        f"/boards/{board_id}/tasks/{task_id}/gate",
        json=payload,
        headers=headers or {"X-Human-Session": human_jwt},
    )


# ---------------------------------------------------------------------------
# 1. Deliverable persistence
# ---------------------------------------------------------------------------

def test_deliverable_persisted_on_review_entry(pg_setup, admin_conn, test_graph, human_jwt):
    os.environ["TALOS_NEXUS_STUB"] = "1"
    from talos import api as api_module
    from talos.worker import claim_and_run

    client = TestClient(api_module.app)
    b, t = f"b-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    _seed(admin_conn, b, t)
    claim_and_run(b, t, graph=test_graph)

    resp = client.get(f"/boards/{b}/tasks/{t}/gate")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deliverable"] is not None
    assert "citations" in body["deliverable"]
    assert "summary" in body["deliverable"]
    assert "document" in body["deliverable"]


def test_deliverable_and_review_entered_at_refresh_on_edit_reentry(
    pg_setup, admin_conn, test_graph, human_jwt
):
    os.environ["TALOS_NEXUS_STUB"] = "1"
    from talos import api as api_module
    from talos.worker import claim_and_run

    client = TestClient(api_module.app)
    b, t = f"b-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    _seed(admin_conn, b, t)
    claim_and_run(b, t, graph=test_graph)

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT review_entered_at FROM tasks WHERE id = %s AND board_id = %s", (t, b)
        )
        first_entered_at = cur.fetchone()["review_entered_at"]
    assert first_entered_at is not None

    resp = _gate(
        client, b, t, human_jwt, outcome="edit",
        new_deliverable={"citations": [{"finding_id": "X", "status": "confirmed"}],
                          "summary": "edited content"},
    )
    assert resp.status_code == 200, resp.text

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT deliverable, review_entered_at FROM tasks WHERE id = %s AND board_id = %s",
            (t, b),
        )
        row = cur.fetchone()
    assert row["deliverable"]["summary"] == "edited content"
    assert row["review_entered_at"] >= first_entered_at


# ---------------------------------------------------------------------------
# 2. worker.py error-escalation paths also set review_entered_at
# ---------------------------------------------------------------------------

def test_budget_exhaustion_sets_review_entered_at_with_null_deliverable(pg_setup, admin_conn):
    from talos.errors import BudgetExhaustedError
    from talos.worker import _handle_budget_exhaustion

    b, t = f"b-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    _seed(admin_conn, b, t)

    exc = BudgetExhaustedError(task_id=t, run_id=0, board_id=b, reason="test")
    _handle_budget_exhaustion(exc)

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT status, deliverable, review_entered_at FROM tasks "
            "WHERE id = %s AND board_id = %s",
            (t, b),
        )
        row = cur.fetchone()
    assert row["status"] == "review"
    assert row["review_entered_at"] is not None
    assert row["deliverable"] is None


def test_model_failure_sets_review_entered_at_with_null_deliverable(pg_setup, admin_conn):
    from talos.errors import ModelFailureError
    from talos.worker import _handle_model_failure

    b, t = f"b-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    _seed(admin_conn, b, t)

    exc = ModelFailureError(task_id=t, run_id=0, board_id=b, reason="test")
    _handle_model_failure(exc)

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT status, deliverable, review_entered_at FROM tasks "
            "WHERE id = %s AND board_id = %s",
            (t, b),
        )
        row = cur.fetchone()
    assert row["status"] == "review"
    assert row["review_entered_at"] is not None
    assert row["deliverable"] is None


# ---------------------------------------------------------------------------
# 3. GET .../gate includes waivable/safety_class + deliverable
# ---------------------------------------------------------------------------

def test_gate_status_includes_waivable_and_safety_class(
    pg_setup, admin_conn, test_graph, human_jwt
):
    os.environ["TALOS_NEXUS_STUB"] = "1"
    from talos import api as api_module
    from talos.worker import claim_and_run

    client = TestClient(api_module.app)
    b, t = f"b-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    _seed(admin_conn, b, t)
    claim_and_run(b, t, graph=test_graph)

    resp = client.get(f"/boards/{b}/tasks/{t}/gate")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["critics"]) > 0
    for c in body["critics"]:
        assert "waivable" in c
        assert "safety_class" in c


# ---------------------------------------------------------------------------
# 4. Review queue
# ---------------------------------------------------------------------------

def test_review_queue_orders_oldest_first_and_flags_overdue(pg_setup, admin_conn, human_jwt):
    from talos import api as api_module

    client = TestClient(api_module.app)
    board_id = f"b-{uuid.uuid4().hex[:8]}"
    t_old, t_new = f"t-old-{uuid.uuid4().hex[:8]}", f"t-new-{uuid.uuid4().hex[:8]}"
    _seed(admin_conn, board_id, t_old)
    _seed(admin_conn, board_id, t_new)

    with admin_conn.cursor() as cur:
        cur.execute(
            "UPDATE tasks SET status = 'review', "
            "review_entered_at = NOW() - INTERVAL '2 hours' WHERE id = %s",
            (t_old,),
        )
        cur.execute(
            "UPDATE tasks SET status = 'review', "
            "review_entered_at = NOW() - INTERVAL '1 minute' WHERE id = %s",
            (t_new,),
        )
        cur.execute("UPDATE boards SET sla_minutes = 30 WHERE id = %s", (board_id,))
    admin_conn.commit()

    resp = client.get(
        f"/boards/{board_id}/review-queue", headers={"X-Human-Session": human_jwt}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sla_minutes"] == 30
    task_ids = [r["task_id"] for r in body["tasks"]]
    assert task_ids == [t_old, t_new], "expected oldest-first ordering"

    by_id = {r["task_id"]: r for r in body["tasks"]}
    assert by_id[t_old]["overdue"] is True
    assert by_id[t_new]["overdue"] is False


def test_review_queue_requires_human_session(pg_setup, admin_conn):
    from talos import api as api_module

    client = TestClient(api_module.app)
    board_id = f"b-{uuid.uuid4().hex[:8]}"
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO boards (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (board_id, "board"),
        )
    admin_conn.commit()

    resp = client.get(f"/boards/{board_id}/review-queue")
    assert resp.status_code == 403

    non_human = pyjwt.encode(
        {"sub": "svc", "token_class": "service"}, os.environ["TALOS_JWT_SECRET"],
        algorithm="HS256",
    )
    resp = client.get(
        f"/boards/{board_id}/review-queue", headers={"X-Human-Session": non_human}
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 5. SLA GET/PATCH round-trip
# ---------------------------------------------------------------------------

def test_sla_get_default_null_and_patch_roundtrip(pg_setup, admin_conn, human_jwt):
    from talos import api as api_module

    client = TestClient(api_module.app)
    board_id = f"b-{uuid.uuid4().hex[:8]}"
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO boards (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (board_id, "board"),
        )
    admin_conn.commit()

    headers = {"X-Human-Session": human_jwt}
    resp = client.get(f"/boards/{board_id}/sla", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["sla_minutes"] is None

    resp = client.patch(f"/boards/{board_id}/sla", json={"sla_minutes": 30}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["sla_minutes"] == 30

    resp = client.get(f"/boards/{board_id}/sla", headers=headers)
    assert resp.json()["sla_minutes"] == 30

    resp = client.patch(f"/boards/{board_id}/sla", json={"sla_minutes": None}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["sla_minutes"] is None


def test_sla_endpoints_require_human_session(pg_setup, admin_conn):
    from talos import api as api_module

    client = TestClient(api_module.app)
    board_id = f"b-{uuid.uuid4().hex[:8]}"
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO boards (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (board_id, "board"),
        )
    admin_conn.commit()

    assert client.get(f"/boards/{board_id}/sla").status_code == 403
    assert client.patch(f"/boards/{board_id}/sla", json={"sla_minutes": 5}).status_code == 403


# ---------------------------------------------------------------------------
# 6. Optional SMTP notification
# ---------------------------------------------------------------------------

def test_review_email_noop_without_smtp_host(pg_setup, admin_conn, test_graph, monkeypatch):
    monkeypatch.delenv("TALOS_SMTP_HOST", raising=False)
    os.environ["TALOS_NEXUS_STUB"] = "1"
    from talos.worker import claim_and_run

    b, t = f"b-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    _seed(admin_conn, b, t)

    with mock.patch("smtplib.SMTP") as mock_smtp:
        claim_and_run(b, t, graph=test_graph)
        mock_smtp.assert_not_called()


def test_review_email_swallows_smtp_error(pg_setup, admin_conn, test_graph, monkeypatch):
    monkeypatch.setenv("TALOS_SMTP_HOST", "smtp.example.invalid")
    monkeypatch.setenv("TALOS_SMTP_TO", "ops@example.invalid")
    os.environ["TALOS_NEXUS_STUB"] = "1"
    from talos.worker import claim_and_run

    b, t = f"b-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    _seed(admin_conn, b, t)

    with mock.patch("smtplib.SMTP", side_effect=OSError("connection refused")):
        claim_and_run(b, t, graph=test_graph)  # must not raise

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT status FROM tasks WHERE id = %s AND board_id = %s", (t, b))
        assert cur.fetchone()["status"] == "review"
