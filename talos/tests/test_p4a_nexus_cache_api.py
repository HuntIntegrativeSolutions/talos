"""
P4a API surface tests: nexus_results_freshness on GET .../gate, and the
POST .../nexus_cache/invalidate endpoint (ADR-035 amendment).
"""

from __future__ import annotations

import os
import uuid

from fastapi.testclient import TestClient

from talos import nexus_cache


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


def test_gate_status_includes_nexus_results_freshness(
    pg_setup, admin_conn, test_graph, human_jwt
):
    os.environ["TALOS_NEXUS_STUB"] = "1"
    from talos import api as api_module
    from talos.worker import claim_and_run

    client = TestClient(api_module.app)
    b, t = f"b-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    _seed(admin_conn, b, t)
    claim_and_run(b, t, graph=test_graph)

    nexus_cache.put_cached(b, "find_docs_for_tag", {"tag": "X"}, "cached-result", 300)

    resp = client.get(f"/boards/{b}/tasks/{t}/gate")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "nexus_results_freshness" in body
    entries = body["nexus_results_freshness"]
    assert len(entries) == 1
    assert entries[0]["tool_name"] == "find_docs_for_tag"
    assert entries[0]["nexus_cache_age_seconds"] >= 0


def test_gate_status_excludes_expired_cache_entries(
    pg_setup, admin_conn, test_graph, human_jwt
):
    os.environ["TALOS_NEXUS_STUB"] = "1"
    from talos import api as api_module
    from talos.worker import claim_and_run

    client = TestClient(api_module.app)
    b, t = f"b-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    _seed(admin_conn, b, t)
    claim_and_run(b, t, graph=test_graph)

    params_hash = nexus_cache._canonical_params_hash({"tag": "X"})
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO nexus_cache (board_id, tool_name, params_hash, result_json, expires_at) "
            "VALUES (%s, %s, %s, %s, now() - interval '1 second')",
            (b, "find_docs_for_tag", params_hash, '"stale"'),
        )
    admin_conn.commit()

    resp = client.get(f"/boards/{b}/tasks/{t}/gate")
    assert resp.status_code == 200, resp.text
    assert resp.json()["nexus_results_freshness"] == []


def test_invalidate_requires_auth(pg_setup, admin_conn):
    from talos import api as api_module

    client = TestClient(api_module.app)
    b = f"b-{uuid.uuid4().hex[:8]}"
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO boards (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (b, "Board"),
        )
    admin_conn.commit()

    resp = client.post(f"/boards/{b}/nexus_cache/invalidate", params={"tool_name": "find_docs_for_tag"})
    assert resp.status_code == 403


def test_invalidate_is_board_scoped_via_api(pg_setup, admin_conn, human_jwt):
    from talos import api as api_module

    client = TestClient(api_module.app)
    board_a = f"b-a-{uuid.uuid4().hex[:8]}"
    board_b = f"b-b-{uuid.uuid4().hex[:8]}"
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO boards (id, name) VALUES (%s, %s), (%s, %s) ON CONFLICT DO NOTHING",
            (board_a, "A", board_b, "B"),
        )
    admin_conn.commit()

    nexus_cache.put_cached(board_a, "find_docs_for_tag", {"tag": "X"}, "a-result", 300)
    nexus_cache.put_cached(board_b, "find_docs_for_tag", {"tag": "X"}, "b-result", 300)

    resp = client.post(
        f"/boards/{board_a}/nexus_cache/invalidate",
        params={"tool_name": "find_docs_for_tag"},
        headers={"X-Human-Session": human_jwt},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["invalidated"] == 1

    assert nexus_cache.get_cached(board_a, "find_docs_for_tag", {"tag": "X"}) is None
    assert nexus_cache.get_cached(board_b, "find_docs_for_tag", {"tag": "X"}) == "b-result"
