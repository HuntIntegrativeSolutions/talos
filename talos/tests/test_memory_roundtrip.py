"""
Dual-backend round-trip test (ADR-039 action item #3): store -> retrieve
returns the same top-k for one fixture corpus on both talos.memory.chroma_store
and talos.memory.pgvector_store.

Correctness trap this test guards against: chroma_store's docs collection
never sets hnsw:space, so Chroma defaults to L2 distance there, while V0009's
chunks.embedding index is cosine. If the fake embedder below returned
non-unit vectors, L2 and cosine could rank differently and this test would
flake. The fixture embedder MUST stay unit-normalized -- do not "simplify"
that away later. (The rules path is unaffected either way: chroma_store sets
hnsw:space="cosine" explicitly on its rules collection, matching pgvector.)
"""

from __future__ import annotations

import hashlib
import uuid

import pytest

from chromadb.api.types import EmbeddingFunction

from talos.memory import chroma_store, pgvector_store


def _unit_vector(text: str, dim: int = 384) -> list[float]:
    h = hashlib.sha256(text.encode()).digest()
    raw = (h * ((dim // len(h)) + 1))[:dim]
    vec = [b / 255.0 + 0.01 for b in raw]
    norm = sum(x * x for x in vec) ** 0.5
    return [x / norm for x in vec]


class _ChromaFakeEmbedder(EmbeddingFunction):
    def __call__(self, input):  # noqa: A002
        return [_unit_vector(t) for t in input]

    def name(self):
        return "roundtrip-fake"


def _pgvector_fake_embed(texts: list[str]) -> list[list[float]]:
    return [_unit_vector(t) for t in texts]


@pytest.fixture()
def chroma_fake_embedder(monkeypatch, tmp_path):
    monkeypatch.setattr(chroma_store, "_get_embedding_function", lambda: _ChromaFakeEmbedder())
    monkeypatch.setenv("TALOS_CHROMA_DIR", str(tmp_path / "chroma_data"))


@pytest.fixture()
def pgvector_fake_embedder(monkeypatch):
    monkeypatch.setattr("talos.memory.embedding.get_embed_fn", lambda: _pgvector_fake_embed)


_CORPUS = [
    "# Widgets\nThe widget assembly line runs at 60 hertz.",
    "# Gadgets\nThe gadget packaging station uses conveyor belt B-2.",
    "# Sprockets\nSprocket inspection happens every shift on line 3.",
]


def test_store_retrieve_same_top_k_on_both_backends(
    pg_setup, admin_conn, chroma_fake_embedder, pgvector_fake_embedder,
):
    board_id = f"board-{uuid.uuid4().hex[:8]}"
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO boards (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (board_id, f"Board {board_id}"),
        )
    admin_conn.commit()

    for i, doc in enumerate(_CORPUS):
        chroma_store.ingest_deliverable(board_id, f"task-{i}", doc, {})
        pgvector_store.ingest_deliverable(board_id, f"task-{i}", doc, {})

    query_text = "widget assembly hertz"
    chroma_results = chroma_store.query(board_id, query_text, k=3)
    pgvector_results = pgvector_store.query(board_id, query_text, k=3)

    chroma_task_ids = [r["metadata"]["task_id"] for r in chroma_results]
    pgvector_task_ids = [r["metadata"]["task_id"] for r in pgvector_results]

    assert chroma_task_ids == pgvector_task_ids
    assert chroma_task_ids[0] == "task-0"  # the widgets doc is the clear top match
