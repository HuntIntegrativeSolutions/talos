"""
Chroma documentation-chunk store tests (P4a).

Covers: chunking (heading + oversize fallback), board isolation, approve
triggers ingest, ingest failure doesn't block approve, and the air-gap rule
(embedding function never silently downloads/reaches the network).

The embedding function is mocked throughout — no real model load, no network,
CI-safe. Only test_get_embedding_function_air_gap exercises the real
sentence-transformers loading path, and only to assert it fails cleanly
without a network fetch (local_files_only=True).
"""

from __future__ import annotations

import os
import uuid
from unittest import mock

import pytest

from chromadb.api.types import EmbeddingFunction

from talos.hooks import HookRegistry
from talos.memory import chroma_store


# ---------------------------------------------------------------------------
# Chunking tests live in test_chunking.py (backend-agnostic, ADR-039 #3)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Board isolation (mocked embedding function — deterministic per input text)
# ---------------------------------------------------------------------------

class _FakeEmbeddingFunction(EmbeddingFunction):
    def __call__(self, input):
        return [[float(len(t)), float(sum(map(ord, t)) % 997)] for t in input]

    def name(self):
        return "fake"


@pytest.fixture()
def fake_embedder(monkeypatch):
    monkeypatch.setattr(chroma_store, "_get_embedding_function", lambda: _FakeEmbeddingFunction())


@pytest.fixture()
def tmp_chroma_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TALOS_CHROMA_DIR", str(tmp_path / "chroma_data"))


def test_ingest_and_query_board_isolation(fake_embedder, tmp_chroma_dir):
    board_a = f"board-a-{uuid.uuid4().hex[:8]}"
    board_b = f"board-b-{uuid.uuid4().hex[:8]}"

    chroma_store.ingest_deliverable(
        board_a, "task-a", "# A Doc\nContent unique to board A about widgets.", {}
    )
    chroma_store.ingest_deliverable(
        board_b, "task-b", "# B Doc\nContent unique to board B about gadgets.", {}
    )

    results_a = chroma_store.query(board_a, "widgets", k=5)
    results_b = chroma_store.query(board_b, "widgets", k=5)

    assert len(results_a) >= 1
    assert all("task_id" in r["metadata"] and r["metadata"]["task_id"] == "task-a" for r in results_a)
    assert all(r["metadata"]["task_id"] != "task-b" for r in results_a)

    # board B's collection never contains board A's chunks either.
    assert all(r["metadata"]["task_id"] != "task-a" for r in results_b)


def test_ingest_empty_markdown_is_noop(fake_embedder, tmp_chroma_dir):
    board_id = f"board-{uuid.uuid4().hex[:8]}"
    chroma_store.ingest_deliverable(board_id, "task-x", "", {})
    results = chroma_store.query(board_id, "anything", k=5)
    assert results == []


# ---------------------------------------------------------------------------
# Approve triggers ingest / ingest failure doesn't block approve
# ---------------------------------------------------------------------------

def _seed(conn, board_id: str, task_id: str) -> None:
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


def test_approve_triggers_ingest(pg_setup, admin_conn, test_graph, fake_embedder, tmp_chroma_dir):
    from langgraph.types import Command
    from talos.worker import claim_and_run
    from talos import hooks as hooks_module

    os.environ["TALOS_NEXUS_STUB"] = "1"
    board_id, task_id = f"b-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    _seed(admin_conn, board_id, task_id)

    test_registry = HookRegistry()
    test_registry.register("on_task_approved", chroma_store._on_task_approved)

    session_key = claim_and_run(board_id, task_id, graph=test_graph)

    with mock.patch.object(hooks_module, "default_registry", test_registry):
        test_graph.invoke(
            Command(resume={"outcome": "approve", "approved_by": "test-human"}),
            config={"configurable": {"thread_id": session_key}},
        )

    results = chroma_store.query(board_id, "task", k=5)
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
    test_registry.register("on_task_approved", chroma_store._on_task_approved)

    session_key = claim_and_run(board_id, task_id, graph=test_graph)

    with mock.patch.object(chroma_store, "ingest_deliverable", side_effect=RuntimeError("boom")):
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

    with mock.patch.object(chroma_store, "ingest_deliverable") as mock_ingest:
        asyncio.run(chroma_store._on_task_approved({
            "board_id": board_id, "task_id": task_id, "outcome": "approve",
        }))
    mock_ingest.assert_not_called()


def test_hook_noop_on_non_approve_outcome():
    import asyncio

    with mock.patch.object(chroma_store, "ingest_deliverable") as mock_ingest:
        asyncio.run(chroma_store._on_task_approved({
            "board_id": "b", "task_id": "t", "outcome": "waive",
        }))
    mock_ingest.assert_not_called()


# ---------------------------------------------------------------------------
# Air-gap rule
# ---------------------------------------------------------------------------

def test_get_embedding_function_air_gap_raises_clear_error(monkeypatch, tmp_path):
    # Isolate from BOTH caches sentence-transformers consults: its own
    # SENTENCE_TRANSFORMERS_HOME and the shared HF hub cache (a model
    # pre-downloaded to ~/.cache/huggingface -- e.g. by an operator following
    # docs/install.md on a connected box -- would otherwise satisfy
    # local_files_only=True and mask the air-gap error). Also clear
    # get_embed_fn's cache (a lock-guarded module cache, not lru_cache --
    # see talos/memory/embedding.py): a successful load cached by an earlier
    # test would be returned without any load attempt at all.
    from talos.memory import embedding as _emb
    _emb.get_embed_fn.cache_clear()
    for var in ("SENTENCE_TRANSFORMERS_HOME", "HF_HOME", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE"):
        monkeypatch.setenv(var, str(tmp_path / "empty-cache"))
    with pytest.raises(RuntimeError, match="not pre-downloaded"):
        chroma_store._get_embedding_function()


def test_cloud_provider_not_silently_reachable(monkeypatch):
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
        chroma_store._get_embedding_function()


# ---------------------------------------------------------------------------
# Rules collection (P5) — separate namespace from the docs collection above
# ---------------------------------------------------------------------------

def test_rules_collection_name_distinct_from_docs_collection():
    board_id = "acme"
    assert chroma_store._rules_collection_name(board_id) != chroma_store._collection_name(board_id)
    assert chroma_store._rules_collection_name(board_id) == "talos-rules-acme"
    assert chroma_store._collection_name(board_id) == "talos-board-acme"


def test_upsert_rule_and_query_rules_board_isolation(fake_embedder, tmp_chroma_dir):
    board_a = f"board-a-{uuid.uuid4().hex[:8]}"
    board_b = f"board-b-{uuid.uuid4().hex[:8]}"

    chroma_store.upsert_rule(
        board_a, "rule-a-1", "widgets are assembled on line 3",
        {"rule_type": "factual", "verified": False, "safety": False, "status": "approved_client", "created_at": "2026-07-06T00:00:00", "source_task_id": "task-a"},
    )
    chroma_store.upsert_rule(
        board_b, "rule-b-1", "gadgets are assembled on line 5",
        {"rule_type": "factual", "verified": False, "safety": False, "status": "approved_client", "created_at": "2026-07-06T00:00:00", "source_task_id": "task-b"},
    )

    results_a = chroma_store.query_rules(board_a, "widgets", k=5)
    results_b = chroma_store.query_rules(board_b, "widgets", k=5)

    assert len(results_a) >= 1
    assert all(r["id"] == "rule-a-1" for r in results_a)
    # board B's collection never contains board A's rules either.
    assert all(r["id"] != "rule-a-1" for r in results_b)


def test_upsert_rule_metadata_roundtrips(fake_embedder, tmp_chroma_dir):
    board_id = f"board-{uuid.uuid4().hex[:8]}"
    metadata = {
        "rule_type": "procedural", "verified": True, "safety": False,
        "status": "approved_client", "created_at": "2026-07-06T00:00:00",
        "source_task_id": "task-x",
    }
    chroma_store.upsert_rule(board_id, "rule-x", "always verify interlock Z", metadata)

    results = chroma_store.query_rules(board_id, "interlock", k=5)
    assert len(results) == 1
    assert results[0]["document"] == "always verify interlock Z"
    assert results[0]["metadata"]["rule_type"] == "procedural"
    assert results[0]["metadata"]["verified"] is True


def test_query_rules_where_filters_by_rule_type(fake_embedder, tmp_chroma_dir):
    board_id = f"board-{uuid.uuid4().hex[:8]}"
    chroma_store.upsert_rule(
        board_id, "rule-fact", "a factual statement",
        {"rule_type": "factual", "verified": False, "safety": False, "status": "approved_client", "created_at": "x", "source_task_id": "t"},
    )
    chroma_store.upsert_rule(
        board_id, "rule-proc", "a procedural statement",
        {"rule_type": "procedural", "verified": False, "safety": False, "status": "approved_client", "created_at": "x", "source_task_id": "t"},
    )

    results = chroma_store.query_rules(board_id, "statement", k=5, where={"rule_type": "factual"})
    assert len(results) == 1
    assert results[0]["id"] == "rule-fact"
