"""
P4b — /promote_rule endpoint + RT-06 critic (ADR-005/ADR-023).
"""
from __future__ import annotations

import uuid

import psycopg2.extras
import pytest
from fastapi.testclient import TestClient

from talos.critics.citations_resolvable import CriticResult
from talos.critics.no_client_identifiers_in_shared import no_client_identifiers_in_shared
from talos.critics.registry import CriticSpec, register


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _seed_board(admin_conn, board_id: str) -> None:
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO boards (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (board_id, f"Board {board_id}"),
        )
    admin_conn.commit()


def _gate(client, board_id, task_id, human_jwt, **payload):
    return client.post(
        f"/boards/{board_id}/tasks/{task_id}/gate",
        json=payload,
        headers={"X-Human-Session": human_jwt},
    )


# ---------------------------------------------------------------------------
# RT-06 unit tests (pure, no DB)
# ---------------------------------------------------------------------------

def test_no_client_identifiers_in_shared_clean_deliverable_passes():
    deliverable = {"summary": "generic engineering content, no client detail"}
    result = no_client_identifiers_in_shared(
        deliverable, client_identifiers=["ACME", "CH75"],
    )
    assert result.passed is True
    assert result.waivable is False


def test_no_client_identifiers_in_shared_seeded_leak_fails_with_match_positions():
    deliverable = {"summary": "the tag CH75-001 on line 3 needs review"}
    result = no_client_identifiers_in_shared(deliverable, client_identifiers=["CH75"])
    assert result.passed is False
    assert result.waivable is False
    assert "CH75" in result.reason
    assert "pos" in result.reason


def test_no_client_identifiers_in_shared_ip_and_hostname_detection():
    deliverable = {"summary": "connects to 192.168.1.10 and plc01.acme.local"}
    result = no_client_identifiers_in_shared(deliverable, client_identifiers=[])
    assert result.passed is False
    assert "ip_address" in result.reason
    assert "hostname" in result.reason


def test_no_client_identifiers_in_shared_skips_when_not_promotion_context():
    deliverable = {"summary": "192.168.1.10 CH75 acme.local — none of this matters"}
    result = no_client_identifiers_in_shared(deliverable, client_identifiers=None)
    assert result.passed is True
    assert result.waivable is False


def test_registry_enforces_safety_class_non_waivable_invariant_for_rt06():
    with pytest.raises(ValueError, match="waivable=False"):
        register(CriticSpec(
            name="bad_rt06_variant",
            fn=no_client_identifiers_in_shared,
            required=True,
            safety_class=True,
            waivable=True,
        ))


# ---------------------------------------------------------------------------
# /promote_rule endpoint
# ---------------------------------------------------------------------------

def test_promote_rule_endpoint_creates_rule_and_promotion_task(pg_setup, admin_conn, human_jwt):
    from talos import api as api_module

    board_id = _uid("promo-board")
    _seed_board(admin_conn, board_id)

    client = TestClient(api_module.app)
    resp = client.post(
        f"/boards/{board_id}/promote_rule",
        json={"rule_type": "project_context", "content": "generic tagging convention"},
        headers={"X-Human-Session": human_jwt},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "pending_review"

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM rules WHERE id = %s", (body["rule_id"],))
        rule_row = dict(cur.fetchone())
        cur.execute("SELECT * FROM tasks WHERE id = %s", (body["promotion_task_id"],))
        task_row = dict(cur.fetchone())

    assert rule_row["status"] == "pending_review"
    assert rule_row["client_scope"] == "client"
    assert task_row["status"] == "ready"
    assert '"talos_origin": "rule_promotion"' in task_row["body"]


# ---------------------------------------------------------------------------
# Deliverable built from rule content, not the NEXUS scaffold
# ---------------------------------------------------------------------------

def test_promotion_task_deliverable_contains_rule_content_under_stub_mode(pg_setup, admin_conn, human_jwt, monkeypatch):
    monkeypatch.setenv("TALOS_NEXUS_STUB", "1")
    from talos import api as api_module
    from talos.worker import claim_and_run

    board_id = _uid("promo-board")
    _seed_board(admin_conn, board_id)

    client = TestClient(api_module.app)
    resp = client.post(
        f"/boards/{board_id}/promote_rule",
        json={"rule_type": "factual", "content": "PLC tag T_PUMP_01 maps to motor M-100"},
        headers={"X-Human-Session": human_jwt},
    )
    promotion_task_id = resp.json()["promotion_task_id"]

    claim_and_run(board_id, promotion_task_id)

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT deliverable FROM tasks WHERE id = %s", (promotion_task_id,))
        deliverable = cur.fetchone()["deliverable"]

    assert deliverable["summary"] == "PLC tag T_PUMP_01 maps to motor M-100"
    assert deliverable["rule_type"] == "factual"


# ---------------------------------------------------------------------------
# End-to-end: clean content -> approve -> client_scope flips to shared
# ---------------------------------------------------------------------------

def test_promotion_with_clean_content_passes_rt06_and_approve_flips_client_scope(
    pg_setup, admin_conn, test_graph, human_jwt, monkeypatch,
):
    monkeypatch.setenv("TALOS_NEXUS_STUB", "1")
    from talos import api as api_module
    from talos.worker import claim_and_run

    board_id = _uid("promo-board")
    _seed_board(admin_conn, board_id)

    # TestClient(app) without a `with` block does not run the lifespan startup
    # event, so the rule_promotion hook isn't registered automatically in tests
    # (mirrors test_chroma_store.py's own registration pattern for the same reason).
    from talos.rule_promotion import register_rule_promotion_hook
    register_rule_promotion_hook()

    client = TestClient(api_module.app)
    resp = client.post(
        f"/boards/{board_id}/promote_rule",
        json={"rule_type": "procedural", "content": "always verify interlock before setpoint change"},
        headers={"X-Human-Session": human_jwt},
    )
    rule_id = resp.json()["rule_id"]
    promotion_task_id = resp.json()["promotion_task_id"]

    claim_and_run(board_id, promotion_task_id, graph=test_graph)

    resp = _gate(client, board_id, promotion_task_id, human_jwt, outcome="approve")
    assert resp.status_code == 200, resp.text

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT client_scope, status FROM rules WHERE id = %s", (rule_id,))
        rule_row = dict(cur.fetchone())

    assert rule_row["client_scope"] == "shared"
    assert rule_row["status"] == "approved_shared"


# ---------------------------------------------------------------------------
# End-to-end: leaked identifier -> RT-06 fails -> waive 409 -> escalate does
# NOT flip scope
# ---------------------------------------------------------------------------

def test_promotion_with_leaked_identifier_fails_rt06_and_waive_returns_409(
    pg_setup, admin_conn, test_graph, human_jwt, monkeypatch,
):
    monkeypatch.setenv("TALOS_NEXUS_STUB", "1")
    from talos import api as api_module
    from talos.worker import claim_and_run

    from talos.rule_promotion import register_rule_promotion_hook
    register_rule_promotion_hook()

    board_id = _uid("promo-board")
    _seed_board(admin_conn, board_id)
    with admin_conn.cursor() as cur:
        cur.execute(
            "UPDATE boards SET client_identifiers = %s WHERE id = %s",
            (["CH75"], board_id),
        )
    admin_conn.commit()

    client = TestClient(api_module.app)
    resp = client.post(
        f"/boards/{board_id}/promote_rule",
        json={"rule_type": "project_context", "content": "tag CH75-001 uses Wrk_ prefix"},
        headers={"X-Human-Session": human_jwt},
    )
    rule_id = resp.json()["rule_id"]
    promotion_task_id = resp.json()["promotion_task_id"]

    claim_and_run(board_id, promotion_task_id, graph=test_graph)

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT verdict, waivable FROM task_gate_results "
            "WHERE task_id = %s AND critic_name = 'no_client_identifiers_in_shared'",
            (promotion_task_id,),
        )
        rt06_row = dict(cur.fetchone())

    assert rt06_row["verdict"] == "fail"
    assert rt06_row["waivable"] is False

    resp = _gate(client, board_id, promotion_task_id, human_jwt,
                 outcome="waive", justification="ignore the leak")
    assert resp.status_code == 409, resp.text

    resp = _gate(client, board_id, promotion_task_id, human_jwt,
                 outcome="escalate", justification="second reviewer will check")
    assert resp.status_code == 200, resp.text

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT client_scope, status FROM rules WHERE id = %s", (rule_id,))
        rule_row = dict(cur.fetchone())

    assert rule_row["client_scope"] == "client", (
        "escalate must NOT flip client_scope — RT-06 exists to stop this exact leak"
    )
    assert rule_row["status"] == "pending_review"


# ---------------------------------------------------------------------------
# RLS
# ---------------------------------------------------------------------------

def test_rules_and_rule_ingestion_log_rls_cross_board_isolation(pg_setup, admin_conn, app_conn):
    board_a = _uid("promo-board-a")
    board_b = _uid("promo-board-b")
    _seed_board(admin_conn, board_a)
    _seed_board(admin_conn, board_b)

    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO rules (id, board_id, rule_type, content) VALUES (%s, %s, %s, %s)",
            ("rule-rls-test", board_a, "factual", "x"),
        )
        cur.execute(
            "INSERT INTO rule_ingestion_log (board_id, dedup_key, rule_id) VALUES (%s, %s, %s)",
            (board_a, "dedup-1", "rule-rls-test"),
        )
    admin_conn.commit()

    with app_conn.cursor() as cur:
        cur.execute("SET LOCAL app.board_id = %s", (board_b,))
        cur.execute("SELECT COUNT(*) FROM rules")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT COUNT(*) FROM rule_ingestion_log")
        assert cur.fetchone()[0] == 0


def test_boards_client_identifiers_read_write(pg_setup, admin_conn, app_conn):
    board_id = _uid("promo-board")
    _seed_board(admin_conn, board_id)

    with app_conn.cursor() as cur:
        cur.execute(
            "UPDATE boards SET client_identifiers = %s WHERE id = %s",
            (["ACME", "CH75"], board_id),
        )
        cur.execute("SELECT client_identifiers FROM boards WHERE id = %s", (board_id,))
        assert cur.fetchone()[0] == ["ACME", "CH75"]
    app_conn.commit()
