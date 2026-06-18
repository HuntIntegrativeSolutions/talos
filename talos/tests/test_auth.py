"""
TALOS RT-01 auth tests — 7 tests proving JWT auth end-to-end.

Closes RT-01 (forged approval BLOCKER) per ADR-036.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt
import psycopg2.extras
import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed(conn, board_id: str, task_id: str) -> None:
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO boards (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (board_id, f"Board {board_id}"),
        )
        cur.execute(
            "INSERT INTO tasks (id, board_id, title, status) VALUES (%s, %s, %s, 'ready') ON CONFLICT DO NOTHING",
            (task_id, board_id, f"Task {task_id}"),
        )
    conn.commit()


def _add_user(username: str, password: str) -> None:
    from talos.auth.users import add_user
    try:
        add_user(username, password)
    except Exception:
        pass  # user already exists


# ---------------------------------------------------------------------------
# Test 1: issue_token + validate_token happy path
# ---------------------------------------------------------------------------

def test_issue_token_valid_user(pg_setup):
    from talos.auth.users import add_user
    from talos.auth.tokens import issue_token, validate_token

    uname = f"u-{uuid.uuid4().hex[:8]}"
    add_user(uname, "hunter2")
    token = issue_token(uname, "hunter2")
    assert token and isinstance(token, str)

    claims = validate_token(token)
    assert claims["sub"] == uname
    assert claims["token_class"] == "human"


# ---------------------------------------------------------------------------
# Test 2: issue_token raises ValueError on wrong password
# ---------------------------------------------------------------------------

def test_issue_token_bad_password(pg_setup):
    from talos.auth.users import add_user
    from talos.auth.tokens import issue_token

    uname = f"u-{uuid.uuid4().hex[:8]}"
    add_user(uname, "hunter2")
    with pytest.raises(ValueError, match="invalid credentials"):
        issue_token(uname, "wrong_password")


# ---------------------------------------------------------------------------
# Test 3: gate rejects missing X-Human-Session header
# ---------------------------------------------------------------------------

def test_gate_rejects_missing_token(pg_setup, admin_conn, test_graph):
    os.environ["TALOS_NEXUS_STUB"] = "1"
    from talos import api as api_module
    from talos.worker import claim_and_run

    b, t = f"b-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    _seed(admin_conn, b, t)
    claim_and_run(b, t, graph=test_graph)

    client = TestClient(api_module.app)
    resp = client.post(f"/boards/{b}/tasks/{t}/gate", json={"outcome": "approve"})
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["error"] == "human session required"


# ---------------------------------------------------------------------------
# Test 4: gate rejects service-class JWT
# ---------------------------------------------------------------------------

def test_gate_rejects_service_token(pg_setup, admin_conn, test_graph):
    os.environ["TALOS_NEXUS_STUB"] = "1"
    from talos import api as api_module
    from talos.worker import claim_and_run

    b, t = f"b-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    _seed(admin_conn, b, t)
    claim_and_run(b, t, graph=test_graph)

    secret = os.environ["TALOS_JWT_SECRET"]
    now = datetime.now(timezone.utc)
    service_token = jwt.encode(
        {"sub": "svc", "token_class": "service", "iat": now, "exp": now + timedelta(hours=1)},
        secret, algorithm="HS256",
    )

    client = TestClient(api_module.app)
    resp = client.post(
        f"/boards/{b}/tasks/{t}/gate",
        json={"outcome": "approve"},
        headers={"X-Human-Session": service_token},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["error"] == "human session required"


# ---------------------------------------------------------------------------
# Test 5: gate accepts valid human JWT and approved_by = JWT sub
# ---------------------------------------------------------------------------

def test_gate_accepts_human_token(pg_setup, admin_conn, test_graph, human_jwt):
    os.environ["TALOS_NEXUS_STUB"] = "1"
    from talos import api as api_module
    from talos.worker import claim_and_run

    b, t = f"b-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    _seed(admin_conn, b, t)
    claim_and_run(b, t, graph=test_graph)

    client = TestClient(api_module.app)
    resp = client.post(
        f"/boards/{b}/tasks/{t}/gate",
        json={"outcome": "approve"},
        headers={"X-Human-Session": human_jwt},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["approved_by"] == "thunt"


# ---------------------------------------------------------------------------
# Test 6: CLI smoke — add-user bootstrap
# ---------------------------------------------------------------------------

def test_add_user_cli_smoke(pg_setup, admin_conn):
    import sys
    from io import StringIO
    from talos.auth.users import verify_user

    uname = f"cli-{uuid.uuid4().hex[:8]}"

    with patch("getpass.getpass", return_value="cli_password"), \
         patch.object(sys, "argv", ["talos.auth", "add-user", uname]):
        from talos.auth.__main__ import main
        main()

    # Verify the row exists in users.
    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT username, hashed_password FROM users WHERE username = %s", (uname,))
        row = cur.fetchone()
    assert row is not None
    assert row["hashed_password"]

    # verify_user must return True for correct password, False for wrong.
    assert verify_user(uname, "cli_password") is True
    assert verify_user(uname, "wrong") is False


# ---------------------------------------------------------------------------
# Test 7: approved_by comes from JWT sub, never from request body
# ---------------------------------------------------------------------------

def test_approved_by_not_from_body(pg_setup, admin_conn, test_graph, human_jwt):
    os.environ["TALOS_NEXUS_STUB"] = "1"
    from talos import api as api_module
    from talos.worker import claim_and_run

    b, t = f"b-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    _seed(admin_conn, b, t)
    claim_and_run(b, t, graph=test_graph)

    client = TestClient(api_module.app)
    # Include an "approved_by" field in the body — it must be ignored.
    resp = client.post(
        f"/boards/{b}/tasks/{t}/gate",
        json={"outcome": "approve", "approved_by": "attacker"},
        headers={"X-Human-Session": human_jwt},
    )
    assert resp.status_code == 200, resp.text

    # Fetch the task as admin to verify the DB value.
    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT approved_by FROM tasks WHERE id = %s AND board_id = %s", (t, b))
        row = cur.fetchone()
    assert row is not None
    assert row["approved_by"] == "thunt", (
        f"approved_by should be JWT sub 'thunt', got {row['approved_by']!r}"
    )
