"""
Chroma documentation-chunk store (P4a; ROADMAP P4-Memory).

Scope: storage + indexing only. Retrieval-at-task-start is P5's job (Crystallize)
— query() is exposed here for P5 to consume later, but nothing in this repo
calls it from talos/graph/spine.py yet.

SECURITY: Chroma has no RLS-equivalent. Isolation here is purely adapter-enforced
— one Chroma collection per board_id, and every public function in this module
takes board_id explicitly and never exposes a cross-board query surface. This is
weaker than the Postgres RLS guarantee used everywhere else in TALOS (ADR-003
does not yet pin a board-isolation design for the vector store). Flagged here
for the next security review.

Air-gap rule: this module must never silently download an embedding model or
call a cloud endpoint at runtime unless talos.toml's [memory] section explicitly
names a cloud embedding_provider. The default ("local") only ever loads a
pre-downloaded sentence-transformers model from local cache; if it isn't
present, _get_embedding_function() raises a clear, operator-facing error
naming the model and the cache directory to pre-populate — it never falls
through to Chroma's own default embedding function, which auto-downloads
ONNX weights on first use.
"""

from __future__ import annotations

import logging
import os
import re

from chromadb.api.types import EmbeddingFunction

from talos.memory.chunking import DEFAULT_MAX_TOKENS, chunk_by_heading, _split_oversize

log = logging.getLogger(__name__)


def _collection_name(board_id: str) -> str:
    # boards.id is a human-chosen TEXT slug ('acme', 'his-internal'), not a UUID.
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", board_id)
    return f"talos-board-{sanitized}"


def _rules_collection_name(board_id: str) -> str:
    # Separate collection namespace from the docs collection above (P5) --
    # rules and deliverable chunks are never mixed in one collection.
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", board_id)
    return f"talos-rules-{sanitized}"


def _get_client():
    import chromadb

    path = os.environ.get("TALOS_CHROMA_DIR", "./chroma_data")
    return chromadb.PersistentClient(path=path)


class _SentenceTransformerEmbeddingFunction(EmbeddingFunction):
    """Chroma EmbeddingFunction wrapping talos.memory.embedding's shared encode callable."""

    def __init__(self, encode_fn):
        self._encode_fn = encode_fn

    def __call__(self, input):  # noqa: A002 — name required by Chroma's protocol
        return self._encode_fn(list(input))

    def name(self) -> str:
        return "talos-sentence-transformers"


def _get_embedding_function():
    from talos.memory.embedding import get_embed_fn

    return _SentenceTransformerEmbeddingFunction(get_embed_fn())


def ingest_deliverable(board_id: str, task_id: str, markdown: str, metadata: dict) -> None:
    chunks = chunk_by_heading(markdown)
    if not chunks:
        return

    client = _get_client()
    collection = client.get_or_create_collection(
        name=_collection_name(board_id),
        embedding_function=_get_embedding_function(),
    )
    ids = [f"{task_id}-{i}" for i in range(len(chunks))]
    metadatas = [{**metadata, "task_id": task_id, "chunk_index": i} for i in range(len(chunks))]
    collection.upsert(ids=ids, documents=chunks, metadatas=metadatas)


def query(board_id: str, text: str, k: int = 5) -> list[dict]:
    """Scoped retrieval for P5 to consume later. NOT wired into talos/graph/spine.py."""
    client = _get_client()
    collection = client.get_or_create_collection(
        name=_collection_name(board_id),
        embedding_function=_get_embedding_function(),
    )
    result = collection.query(query_texts=[text], n_results=k)

    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]
    ids = (result.get("ids") or [[]])[0]
    return [
        {"id": i, "document": d, "metadata": m, "distance": dist}
        for i, d, m, dist in zip(ids, documents, metadatas, distances)
    ]


def _get_rules_collection(client, board_id: str):
    # hnsw:space explicit as cosine -- Chroma's collection default is L2, and
    # talos.crystallize's contradiction heuristic reasons in terms of a
    # cosine-distance threshold. Only set at creation time; this is a no-op
    # metadata request on an already-existing collection (Chroma keeps the
    # space a collection was created with).
    return client.get_or_create_collection(
        name=_rules_collection_name(board_id),
        embedding_function=_get_embedding_function(),
        metadata={"hnsw:space": "cosine"},
    )


def upsert_rule(board_id: str, rule_id: str, content: str, metadata: dict) -> None:
    """Embed one rule into the board's rules_{board} collection (P5).

    metadata should carry rule_type, verified, safety, status, created_at
    (ISO string), and source_task_id -- read_branch_rules/format_rules_context
    and the contradiction heuristic both depend on these being present."""
    client = _get_client()
    collection = _get_rules_collection(client, board_id)
    collection.upsert(ids=[rule_id], documents=[content], metadatas=[metadata])


def query_rules(board_id: str, text: str, k: int = 5, where: dict | None = None) -> list[dict]:
    """Scoped semantic retrieval over the rules collection (P5). Same reshape
    as query() above, against rules_{board} instead of docs_{board}."""
    client = _get_client()
    collection = _get_rules_collection(client, board_id)
    result = collection.query(query_texts=[text], n_results=k, where=where)

    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]
    ids = (result.get("ids") or [[]])[0]
    return [
        {"id": i, "document": d, "metadata": m, "distance": dist}
        for i, d, m, dist in zip(ids, documents, metadatas, distances)
    ]


async def _on_task_approved(payload: dict) -> None:
    """on_task_approved hook (talos/hooks.py). Fires for approve/waive/escalate
    outcomes (see spine.py::post_gate_node) — filtered here to strict 'approve'
    only, per this feature's own scope ('on gate outcome approve')."""
    if payload.get("outcome") != "approve":
        return

    board_id = payload["board_id"]
    task_id = payload["task_id"]

    try:
        from talos.db import board_scope, get_conn

        conn = get_conn()
        try:
            with board_scope(conn, board_id) as cur:
                cur.execute(
                    "SELECT deliverable FROM tasks WHERE id = %s AND board_id = %s",
                    (task_id, board_id),
                )
                row = cur.fetchone()
        finally:
            conn.close()

        if row is None or row["deliverable"] is None:
            # No-op: error-escalation entries into review never produced a
            # deliverable. Not an error — nothing to ingest.
            return

        d = row["deliverable"]
        markdown = d.get("document") or d.get("summary", "")
        if not markdown:
            return

        ingest_deliverable(board_id, task_id, markdown, {"board_id": board_id})
    except Exception:
        # Ingestion failure must never affect an already-committed approval.
        log.exception(
            "chroma_store ingestion failed for board_id=%s task_id=%s", board_id, task_id
        )


def register_ingest_hook() -> None:
    from talos.hooks import default_registry

    default_registry.register("on_task_approved", _on_task_approved)
