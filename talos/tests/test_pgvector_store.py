"""
pgvector documentation-chunk + rules store tests (ADR-039 action item #3).

Mirrors talos/tests/test_chroma_store.py's coverage 1:1 (board isolation,
empty-input no-op, hook wiring end-to-end, ingest-failure-doesn't-block-
approve, air-gap RuntimeError, where-filtered query_rules, metadata
round-trip) but against the real Postgres chunks table (RLS-enforced, not
adapter-enforced) via the existing pg_setup/admin_conn fixtures.

The embedding function is faked throughout with a deterministic, unit-
normalized 384-dim vectorizer (chunks.embedding is vector(384) NOT NULL, so
any fake vector must match that dimension exactly) — no real model load, no
network, CI-safe.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from unittest import mock

import pytest

from talos.hooks import HookRegistry
from talos.memory import pgvector_store


def _fake_embed(texts: list[str]) -> list[list[float]]:
    """Deterministic, unit-normalized 384-dim vectors — same shape contract
    talos/memory/embedding.get_embed_fn() returns, so pgvector_store's
    <=> cosine-distance queries behave sensibly against fixture text."""
    vecs = []
    for t in texts:
        h = hashlib.sha256(t.encode()).digest()
        raw = (h * ((384 // len(h)) + 1))[:384]
        vec = [b / 255.0 + 0.01 for b in raw]
        norm = sum(x * x for x in vec) ** 0.5
        vecs.append([x / norm for x in vec])
    return vecs


@pytest.fixture()
def fake_embedder(monkeypatch):
    monkeypatch.setattr(
        "talos.memory.embedding.get_embed_fn", lambda: _fake_embed
    )


def _seed_board(conn, board_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO boards (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (board_id, f"Board {board_id}"),
        )
    conn.commit()


def _seed(conn, board_id: str, task_id: str) -> None:
    _seed_board(conn, board_id)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tasks (id, board_id, title, status)
            VALUES (%s, %s, %s, 'ready')
            ON CONFLICT DO NOTHING
            """,
            (task_id, board_id, f"Task {task_id}"),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Board isolation
# ---------------------------------------------------------------------------

def test_ingest_and_query_board_isolation(pg_setup, admin_conn, fake_embedder):
    board_a = f"board-a-{uuid.uuid4().hex[:8]}"
    board_b = f"board-b-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_a)
    _seed_board(admin_conn, board_b)

    pgvector_store.ingest_deliverable(
        board_a, "task-a", "# A Doc\nContent unique to board A about widgets.", {}
    )
    pgvector_store.ingest_deliverable(
        board_b, "task-b", "# B Doc\nContent unique to board B about gadgets.", {}
    )

    results_a = pgvector_store.query(board_a, "widgets", k=5)
    results_b = pgvector_store.query(board_b, "widgets", k=5)

    assert len(results_a) >= 1
    assert all("task_id" in r["metadata"] and r["metadata"]["task_id"] == "task-a" for r in results_a)
    assert all(r["metadata"]["task_id"] != "task-b" for r in results_a)
    assert all(r["metadata"]["task_id"] != "task-a" for r in results_b)


def test_ingest_empty_markdown_is_noop(pg_setup, admin_conn, fake_embedder):
    board_id = f"board-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)
    pgvector_store.ingest_deliverable(board_id, "task-x", "", {})
    results = pgvector_store.query(board_id, "anything", k=5)
    assert results == []


def test_ingest_deliverable_is_idempotent_on_reingest(pg_setup, admin_conn, fake_embedder):
    """Re-ingesting the same task_id must not accumulate stale chunk rows
    (delete-then-insert scoped by task_id, per the upsert design)."""
    board_id = f"board-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)

    pgvector_store.ingest_deliverable(board_id, "task-y", "# Doc\nFirst version about widgets.", {})
    pgvector_store.ingest_deliverable(board_id, "task-y", "# Doc\nSecond version about widgets.", {})

    with admin_conn.cursor() as cur:
        cur.execute(
            "SET LOCAL app.board_id = %s", (board_id,)
        )
        cur.execute(
            "SELECT chunk_text FROM chunks WHERE board_id = %s AND source = 'doc' AND metadata->>'task_id' = %s",
            (board_id, "task-y"),
        )
        rows = cur.fetchall()
    admin_conn.rollback()
    assert len(rows) == 1
    assert "Second version" in rows[0][0]


# ---------------------------------------------------------------------------
# Approve triggers ingest / ingest failure doesn't block approve
# ---------------------------------------------------------------------------

def test_approve_triggers_ingest(pg_setup, admin_conn, test_graph, fake_embedder):
    from langgraph.types import Command
    from talos.worker import claim_and_run
    from talos import hooks as hooks_module

    os.environ["TALOS_NEXUS_STUB"] = "1"
    board_id, task_id = f"b-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    _seed(admin_conn, board_id, task_id)

    test_registry = HookRegistry()
    test_registry.register("on_task_approved", pgvector_store._on_task_approved)

    session_key = claim_and_run(board_id, task_id, graph=test_graph)

    with mock.patch.object(hooks_module, "default_registry", test_registry):
        test_graph.invoke(
            Command(resume={"outcome": "approve", "approved_by": "test-human"}),
            config={"configurable": {"thread_id": session_key}},
        )

    results = pgvector_store.query(board_id, "task", k=5)
    assert len(results) >= 1
    assert all(r["metadata"]["task_id"] == task_id for r in results)


def test_ingest_failure_does_not_block_approve(pg_setup, admin_conn, test_graph):
    from langgraph.types import Command
    from talos.worker import claim_and_run
    from talos import hooks as hooks_module

    os.environ["TALOS_NEXUS_STUB"] = "1"
    board_id, task_id = f"b-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    _seed(admin_conn, board_id, task_id)

    test_registry = HookRegistry()
    test_registry.register("on_task_approved", pgvector_store._on_task_approved)

    session_key = claim_and_run(board_id, task_id, graph=test_graph)

    with mock.patch.object(pgvector_store, "ingest_deliverable", side_effect=RuntimeError("boom")):
        with mock.patch.object(hooks_module, "default_registry", test_registry):
            test_graph.invoke(
                Command(resume={"outcome": "approve", "approved_by": "test-human"}),
                config={"configurable": {"thread_id": session_key}},
            )

    with admin_conn.cursor() as cur:
        cur.execute("SELECT status FROM tasks WHERE id = %s AND board_id = %s", (task_id, board_id))
        (status,) = cur.fetchone()
    assert status == "approved"


def test_hook_noop_on_null_deliverable(pg_setup, admin_conn):
    import asyncio

    board_id, task_id = f"b-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    _seed(admin_conn, board_id, task_id)

    with mock.patch.object(pgvector_store, "ingest_deliverable") as mock_ingest:
        asyncio.run(pgvector_store._on_task_approved({
            "board_id": board_id, "task_id": task_id, "outcome": "approve",
        }))
    mock_ingest.assert_not_called()


def test_hook_noop_on_non_approve_outcome():
    import asyncio

    with mock.patch.object(pgvector_store, "ingest_deliverable") as mock_ingest:
        asyncio.run(pgvector_store._on_task_approved({
            "board_id": "b", "task_id": "t", "outcome": "waive",
        }))
    mock_ingest.assert_not_called()


# ---------------------------------------------------------------------------
# Air-gap rule (shared talos.memory.embedding module)
# ---------------------------------------------------------------------------

def test_get_embed_fn_air_gap_raises_clear_error(monkeypatch, tmp_path):
    # Isolate from BOTH caches sentence-transformers consults: its own
    # SENTENCE_TRANSFORMERS_HOME and the shared HF hub cache (a model
    # pre-downloaded to ~/.cache/huggingface -- e.g. by an operator following
    # docs/install.md on a connected box -- would otherwise satisfy
    # local_files_only=True and mask the air-gap error). Also clear
    # get_embed_fn's cache (a lock-guarded module cache, not lru_cache --
    # see talos/memory/embedding.py): a successful load cached by an earlier
    # test would be returned without any load attempt at all.
    from talos.memory import embedding
    embedding.get_embed_fn.cache_clear()
    for var in ("SENTENCE_TRANSFORMERS_HOME", "HF_HOME", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE"):
        monkeypatch.setenv(var, str(tmp_path / "empty-cache"))
    with pytest.raises(RuntimeError, match="not pre-downloaded"):
        embedding.get_embed_fn()


def test_cloud_provider_not_silently_reachable(monkeypatch):
    from talos.memory import embedding
    # Clear get_embed_fn's cache first -- a locally-loaded fn cached by an
    # earlier test would be returned without re-reading the (monkeypatched)
    # provider config, masking the NotImplementedError.
    from talos.memory import embedding as _emb
    _emb.get_embed_fn.cache_clear()
    monkeypatch.setattr(
        "talos.config.get_memory_config",
        lambda: {"embedding_provider": "openai", "embedding_model": "text-embedding-3-small"},
    )
    with pytest.raises(NotImplementedError):
        embedding.get_embed_fn()


# ---------------------------------------------------------------------------
# Rules (P5) — source='rule' chunks, separate from source='doc'
# ---------------------------------------------------------------------------

def test_upsert_rule_and_query_rules_board_isolation(pg_setup, admin_conn, fake_embedder):
    board_a = f"board-a-{uuid.uuid4().hex[:8]}"
    board_b = f"board-b-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_a)
    _seed_board(admin_conn, board_b)

    pgvector_store.upsert_rule(
        board_a, "rule-a-1", "widgets are assembled on line 3",
        {"rule_type": "factual", "verified": False, "safety": False, "status": "approved_client", "created_at": "2026-07-06T00:00:00", "source_task_id": "task-a"},
    )
    pgvector_store.upsert_rule(
        board_b, "rule-b-1", "gadgets are assembled on line 5",
        {"rule_type": "factual", "verified": False, "safety": False, "status": "approved_client", "created_at": "2026-07-06T00:00:00", "source_task_id": "task-b"},
    )

    results_a = pgvector_store.query_rules(board_a, "widgets", k=5)
    results_b = pgvector_store.query_rules(board_b, "widgets", k=5)

    assert len(results_a) >= 1
    assert all(r["id"] == "rule-a-1" for r in results_a)
    assert all(r["id"] != "rule-a-1" for r in results_b)


def test_upsert_rule_is_idempotent(pg_setup, admin_conn, fake_embedder):
    board_id = f"board-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)
    meta = {"rule_type": "factual", "verified": False, "safety": False, "status": "approved_client", "created_at": "x", "source_task_id": "t"}

    pgvector_store.upsert_rule(board_id, "rule-z", "first content", meta)
    pgvector_store.upsert_rule(board_id, "rule-z", "second content", meta)

    results = pgvector_store.query_rules(board_id, "content", k=10)
    matches = [r for r in results if r["id"] == "rule-z"]
    assert len(matches) == 1
    assert matches[0]["document"] == "second content"


def test_upsert_rule_metadata_roundtrips(pg_setup, admin_conn, fake_embedder):
    board_id = f"board-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)
    metadata = {
        "rule_type": "procedural", "verified": True, "safety": False,
        "status": "approved_client", "created_at": "2026-07-06T00:00:00",
        "source_task_id": "task-x",
    }
    pgvector_store.upsert_rule(board_id, "rule-x", "always verify interlock Z", metadata)

    results = pgvector_store.query_rules(board_id, "interlock", k=5)
    assert len(results) == 1
    assert results[0]["document"] == "always verify interlock Z"
    assert results[0]["metadata"]["rule_type"] == "procedural"
    assert results[0]["metadata"]["verified"] is True


def test_query_rules_where_filters_by_rule_type(pg_setup, admin_conn, fake_embedder):
    board_id = f"board-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)
    pgvector_store.upsert_rule(
        board_id, "rule-fact", "a factual statement",
        {"rule_type": "factual", "verified": False, "safety": False, "status": "approved_client", "created_at": "x", "source_task_id": "t"},
    )
    pgvector_store.upsert_rule(
        board_id, "rule-proc", "a procedural statement",
        {"rule_type": "procedural", "verified": False, "safety": False, "status": "approved_client", "created_at": "x", "source_task_id": "t"},
    )

    results = pgvector_store.query_rules(board_id, "statement", k=5, where={"rule_type": "factual"})
    assert len(results) == 1
    assert results[0]["id"] == "rule-fact"
