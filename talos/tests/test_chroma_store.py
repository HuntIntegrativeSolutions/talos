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
# Chunking
# ---------------------------------------------------------------------------

def test_chunk_by_heading_splits_on_headings():
    markdown = "# Title\nintro text\n\n## Section A\ncontent A\n\n## Section B\ncontent B\n"
    chunks = chroma_store.chunk_by_heading(markdown, max_tokens=500)
    assert len(chunks) == 3
    assert chunks[0].startswith("# Title")
    assert chunks[1].startswith("## Section A")
    assert chunks[2].startswith("## Section B")


def test_chunk_by_heading_no_headings_returns_single_chunk():
    markdown = "just plain text with no headings at all"
    chunks = chroma_store.chunk_by_heading(markdown, max_tokens=500)
    assert chunks == [markdown]


def test_chunk_by_heading_empty_returns_no_chunks():
    assert chroma_store.chunk_by_heading("", max_tokens=500) == []
    assert chroma_store.chunk_by_heading("   \n  ", max_tokens=500) == []


def test_chunk_by_heading_oversize_fallback_splits_by_paragraph():
    para_a = "wordA " * 300
    para_b = "wordB " * 300
    markdown = f"# Big Section\n{para_a}\n\n{para_b}"
    chunks = chroma_store.chunk_by_heading(markdown, max_tokens=100)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c.split()) <= 350  # roughly bounded, allows heading overhead


def test_chunk_by_heading_oversize_no_paragraphs_splits_by_word_count():
    markdown = "# H\n" + " ".join(f"w{i}" for i in range(1000))
    chunks = chroma_store.chunk_by_heading(markdown, max_tokens=200)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c.split()) <= 201  # +1 for the heading token


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
    # No local model present at this cache path — must raise a clear,
    # operator-facing error, never attempt a network fetch.
    monkeypatch.setenv("SENTENCE_TRANSFORMERS_HOME", str(tmp_path / "empty-cache"))
    with pytest.raises(RuntimeError, match="not pre-downloaded"):
        chroma_store._get_embedding_function()


def test_cloud_provider_not_silently_reachable(monkeypatch):
    monkeypatch.setattr(
        "talos.config.get_memory_config",
        lambda: {"embedding_provider": "openai", "embedding_model": "text-embedding-3-small"},
    )
    with pytest.raises(NotImplementedError):
        chroma_store._get_embedding_function()
