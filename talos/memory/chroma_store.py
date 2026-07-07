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

log = logging.getLogger(__name__)

DEFAULT_MAX_TOKENS = 500
_HEADING_RE = re.compile(r"^#{1,6}\s", re.MULTILINE)


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
    """Chroma EmbeddingFunction wrapping a local sentence-transformers model."""

    def __init__(self, model):
        self._model = model

    def __call__(self, input):  # noqa: A002 — name required by Chroma's protocol
        return self._model.encode(list(input)).tolist()

    def name(self) -> str:
        return "talos-sentence-transformers"


def _get_embedding_function():
    from talos.config import get_memory_config

    cfg = get_memory_config()
    provider = cfg["embedding_provider"]
    model_name = cfg["embedding_model"]

    if provider != "local":
        raise NotImplementedError(
            f"embedding_provider={provider!r} is not implemented — only 'local' "
            "is supported today. A cloud provider must be added explicitly and "
            "deliberately; it is never reached silently."
        )

    from sentence_transformers import SentenceTransformer

    try:
        model = SentenceTransformer(model_name, local_files_only=True)
    except Exception as exc:
        cache_dir = os.environ.get(
            "SENTENCE_TRANSFORMERS_HOME", "~/.cache/torch/sentence_transformers"
        )
        raise RuntimeError(
            f"Local embedding model {model_name!r} is not pre-downloaded and this "
            f"module will not fetch it silently (air-gap rule, P4a). Pre-download it "
            f"on a machine with network access and place it under {cache_dir!r} "
            f"(or set SENTENCE_TRANSFORMERS_HOME to a directory that already has it), "
            f"or set talos.toml's [memory] embedding_model to a model that is present."
        ) from exc
    return _SentenceTransformerEmbeddingFunction(model)


def chunk_by_heading(markdown: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> list[str]:
    """Split on markdown headings; fallback-split any oversize chunk at
    paragraph boundaries (approximate whitespace-token count, no tokenizer dep)."""
    if not markdown.strip():
        return []

    positions = [m.start() for m in _HEADING_RE.finditer(markdown)]
    if not positions or positions[0] != 0:
        positions = [0] + positions
    positions.append(len(markdown))

    sections = [
        markdown[positions[i]:positions[i + 1]].strip()
        for i in range(len(positions) - 1)
    ]
    sections = [s for s in sections if s]

    chunks: list[str] = []
    for section in sections:
        chunks.extend(_split_oversize(section, max_tokens))
    return chunks


def _split_oversize(section: str, max_tokens: int) -> list[str]:
    words = section.split()
    if len(words) <= max_tokens:
        return [section]

    paragraphs = [p for p in section.split("\n\n") if p.strip()]
    if len(paragraphs) <= 1:
        # No paragraph breaks to split on — chunk by raw word count.
        return [
            " ".join(words[i:i + max_tokens])
            for i in range(0, len(words), max_tokens)
        ]

    out: list[str] = []
    current: list[str] = []
    current_len = 0
    for p in paragraphs:
        p_len = len(p.split())
        if current and current_len + p_len > max_tokens:
            out.append("\n\n".join(current))
            current, current_len = [], 0
        current.append(p)
        current_len += p_len
    if current:
        out.append("\n\n".join(current))
    return out


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

        markdown = row["deliverable"].get("summary", "")
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
