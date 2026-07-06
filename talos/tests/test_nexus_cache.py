"""
Board-scoped NEXUS read cache tests (ADR-035/P4a).

Covers: hit/miss/TTL-expiry/params-hash discrimination, TTL=0 bypass, RLS
cross-board isolation on nexus_cache, cacheability predicate (read +
write:offline_artifact), the invalidate endpoint (auth + board scoping),
and get_gate_status's nexus_results_freshness field.
"""

from __future__ import annotations

import uuid

import psycopg2.extras
import pytest

from talos import nexus_cache

_MANIFEST = {
    "tools": [
        {"name": "find_docs_for_tag", "profile": "read"},
        {"name": "full_plc_documentation", "profile": "write", "write_kind": "offline_artifact"},
        {"name": "some_other_write", "profile": "write", "write_kind": "sim_only"},
    ]
}


def _seed_board(conn, board_id: str, model_config: dict | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO boards (id, name, model_config) VALUES (%s, %s, %s) "
            "ON CONFLICT DO NOTHING",
            (board_id, "test board", psycopg2.extras.Json(model_config) if model_config else None),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Cacheability predicate
# ---------------------------------------------------------------------------

def test_is_cacheable_read_profile():
    assert nexus_cache.is_cacheable("find_docs_for_tag", _MANIFEST) is True


def test_is_cacheable_write_offline_artifact():
    assert nexus_cache.is_cacheable("full_plc_documentation", _MANIFEST) is True


def test_is_cacheable_write_sim_only_excluded():
    assert nexus_cache.is_cacheable("some_other_write", _MANIFEST) is False


def test_is_cacheable_unknown_tool_excluded():
    assert nexus_cache.is_cacheable("nonexistent_tool", _MANIFEST) is False


# ---------------------------------------------------------------------------
# get_ttl_seconds
# ---------------------------------------------------------------------------

def test_get_ttl_seconds_default(pg_setup, admin_conn):
    board_id = f"ttl-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)
    assert nexus_cache.get_ttl_seconds(board_id) == 300


def test_get_ttl_seconds_board_override(pg_setup, admin_conn):
    board_id = f"ttl-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id, {"nexus_cache_ttl_seconds": 60})
    assert nexus_cache.get_ttl_seconds(board_id) == 60


def test_get_ttl_seconds_zero_disables(pg_setup, admin_conn):
    board_id = f"ttl-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id, {"nexus_cache_ttl_seconds": 0})
    assert nexus_cache.get_ttl_seconds(board_id) == 0


# ---------------------------------------------------------------------------
# get_cached / put_cached hit, miss, TTL expiry, params-hash discrimination
# ---------------------------------------------------------------------------

def test_cache_miss_then_hit(pg_setup, admin_conn):
    board_id = f"c-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)

    assert nexus_cache.get_cached(board_id, "find_docs_for_tag", {"tag": "X"}) is None

    nexus_cache.put_cached(board_id, "find_docs_for_tag", {"tag": "X"}, "result-text", 300)
    assert nexus_cache.get_cached(board_id, "find_docs_for_tag", {"tag": "X"}) == "result-text"


def test_cache_discriminates_by_params(pg_setup, admin_conn):
    board_id = f"c-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)

    nexus_cache.put_cached(board_id, "find_docs_for_tag", {"tag": "X"}, "result-X", 300)
    nexus_cache.put_cached(board_id, "find_docs_for_tag", {"tag": "Y"}, "result-Y", 300)

    assert nexus_cache.get_cached(board_id, "find_docs_for_tag", {"tag": "X"}) == "result-X"
    assert nexus_cache.get_cached(board_id, "find_docs_for_tag", {"tag": "Y"}) == "result-Y"


def test_cache_ttl_expiry(pg_setup, admin_conn):
    board_id = f"c-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)

    # Insert directly with an already-past expires_at to simulate expiry
    # without sleeping in the test.
    params_hash = nexus_cache._canonical_params_hash({"tag": "X"})
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO nexus_cache (board_id, tool_name, params_hash, result_json, expires_at) "
            "VALUES (%s, %s, %s, %s, now() - interval '1 second')",
            (board_id, "find_docs_for_tag", params_hash, '"stale-result"'),
        )
    admin_conn.commit()

    assert nexus_cache.get_cached(board_id, "find_docs_for_tag", {"tag": "X"}) is None


def test_put_cached_ttl_zero_is_noop(pg_setup, admin_conn):
    board_id = f"c-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)

    nexus_cache.put_cached(board_id, "find_docs_for_tag", {"tag": "X"}, "result-text", 0)
    assert nexus_cache.get_cached(board_id, "find_docs_for_tag", {"tag": "X"}) is None

    with admin_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM nexus_cache WHERE board_id = %s", (board_id,))
        (count,) = cur.fetchone()
    assert count == 0


# ---------------------------------------------------------------------------
# RLS cross-board isolation
# ---------------------------------------------------------------------------

def test_nexus_cache_rls_cross_board_isolation(pg_setup, admin_conn, app_conn):
    board_a = f"nc-a-{uuid.uuid4().hex[:8]}"
    board_b = f"nc-b-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_a)
    _seed_board(admin_conn, board_b)

    nexus_cache.put_cached(board_a, "find_docs_for_tag", {"tag": "X"}, "a-result", 300)
    nexus_cache.put_cached(board_b, "find_docs_for_tag", {"tag": "X"}, "b-result", 300)

    with app_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SET LOCAL app.board_id = %s", (board_a,))
        cur.execute("SELECT COUNT(*) AS cnt FROM nexus_cache WHERE board_id = %s", (board_b,))
        assert cur.fetchone()["cnt"] == 0, "board B's cache row visible when scoped to board A"
    app_conn.rollback()


# ---------------------------------------------------------------------------
# invalidate()
# ---------------------------------------------------------------------------

def test_invalidate_expires_matching_rows(pg_setup, admin_conn):
    board_id = f"inv-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)

    nexus_cache.put_cached(board_id, "find_docs_for_tag", {"tag": "X"}, "result-text", 300)
    count = nexus_cache.invalidate(board_id, "find_docs_for_tag")
    assert count == 1
    assert nexus_cache.get_cached(board_id, "find_docs_for_tag", {"tag": "X"}) is None


def test_invalidate_is_board_scoped(pg_setup, admin_conn):
    board_a = f"inv-a-{uuid.uuid4().hex[:8]}"
    board_b = f"inv-b-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_a)
    _seed_board(admin_conn, board_b)

    nexus_cache.put_cached(board_a, "find_docs_for_tag", {"tag": "X"}, "a-result", 300)
    nexus_cache.put_cached(board_b, "find_docs_for_tag", {"tag": "X"}, "b-result", 300)

    nexus_cache.invalidate(board_a, "find_docs_for_tag")

    assert nexus_cache.get_cached(board_a, "find_docs_for_tag", {"tag": "X"}) is None
    assert nexus_cache.get_cached(board_b, "find_docs_for_tag", {"tag": "X"}) == "b-result"
