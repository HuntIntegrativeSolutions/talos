"""
pgvector documentation-chunk + rules store (ADR-039 action item #3).

Replaces talos.memory.chroma_store's talos-board-{board}/talos-rules-{board}
Chroma collections with the V0009 `chunks` table (engine/migrations/versions/
V0009_unified_memory.py). Same public-function contract as chroma_store.py
(drop-in swap via talos.memory.get_store()) but backed by Postgres.

Isolation: RLS (chunks_board_isolation, FORCE ROW LEVEL SECURITY), not
adapter-enforced collection naming -- every write/read goes through
talos.db.get_conn()/board_scope() like every other table in TALOS, so a
board_id mismatch is rejected by the same guarantee protecting tasks/rules/etc.

Chunk identity: `chunks` has no external-id column (only an auto bigint PK),
unlike Chroma's collection.upsert(ids=...). This module stores the caller's
external id in metadata (`ext_id` for docs -- f"{task_id}-{i}"; `rule_id` for
rules) and implements upsert as delete-then-insert scoped by that key, inside
one board_scope transaction. query()/query_rules() echo metadata's ext_id/
rule_id back as "id" so callers see the same id shape chroma_store produced.

No pgvector Python dependency: embeddings are formatted as '[...]'::vector(384)
string literals in parameterized SQL; the embedding column is never SELECTed
back out, only used via the <=> cosine-distance operator.

Air-gap rule (same as chroma_store.py): embeddings are computed via
talos.memory.embedding.get_embed_fn(), which never silently downloads a model.
"""

from __future__ import annotations

import logging

from talos.memory.chunking import chunk_by_heading  # noqa: F401 -- re-export

log = logging.getLogger(__name__)


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def _reshape(rows: list[dict]) -> list[dict]:
    return [
        {
            "id": row["metadata"].get("ext_id") or row["metadata"].get("rule_id"),
            "document": row["chunk_text"],
            "metadata": row["metadata"],
            "distance": row["distance"],
        }
        for row in rows
    ]


def ingest_deliverable(board_id: str, task_id: str, markdown: str, metadata: dict) -> None:
    chunks = chunk_by_heading(markdown)
    if not chunks:
        return

    from talos.memory.embedding import get_embed_fn
    embeddings = get_embed_fn()(chunks)

    from talos.db import board_scope, get_conn

    conn = get_conn()
    try:
        with board_scope(conn, board_id) as cur:
            cur.execute(
                "DELETE FROM chunks WHERE board_id = %s AND source = 'doc' "
                "AND metadata->>'task_id' = %s",
                (board_id, task_id),
            )
            for i, (chunk_text, embedding) in enumerate(zip(chunks, embeddings)):
                chunk_metadata = {**metadata, "task_id": task_id, "chunk_index": i, "ext_id": f"{task_id}-{i}"}
                cur.execute(
                    """
                    INSERT INTO chunks (board_id, source, chunk_text, embedding, metadata)
                    VALUES (%s, 'doc', %s, %s::vector, %s)
                    """,
                    (board_id, chunk_text, _vector_literal(embedding), _json(chunk_metadata)),
                )
    finally:
        conn.close()


def query(board_id: str, text: str, k: int = 5) -> list[dict]:
    """Scoped retrieval for the doc chunks (source='doc')."""
    from talos.memory.embedding import get_embed_fn
    [embedding] = get_embed_fn()([text])

    from talos.db import board_scope, get_conn

    conn = get_conn()
    try:
        with board_scope(conn, board_id) as cur:
            # ivfflat.probes (default 1, lists=100 per V0009) trades recall for
            # speed -- if retrieval looks like it's missing obviously-relevant
            # rows once real (non-trivial) data lands, tune probes here first.
            cur.execute(
                """
                SELECT chunk_text, metadata, embedding <=> %s::vector AS distance
                FROM chunks
                WHERE board_id = %s AND source = 'doc'
                ORDER BY distance
                LIMIT %s
                """,
                (_vector_literal(embedding), board_id, k),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return _reshape([dict(r) for r in rows])


def upsert_rule(board_id: str, rule_id: str, content: str, metadata: dict) -> None:
    """Embed one rule into board-scoped chunks (source='rule').

    metadata should carry rule_type, verified, safety, status, created_at
    (ISO string), and source_task_id -- read_branch_rules/format_rules_context
    and the contradiction heuristic both depend on these being present."""
    from talos.memory.embedding import get_embed_fn
    [embedding] = get_embed_fn()([content])

    rule_metadata = {**metadata, "rule_id": rule_id}

    from talos.db import board_scope, get_conn

    conn = get_conn()
    try:
        with board_scope(conn, board_id) as cur:
            cur.execute(
                "DELETE FROM chunks WHERE board_id = %s AND source = 'rule' "
                "AND metadata->>'rule_id' = %s",
                (board_id, rule_id),
            )
            cur.execute(
                """
                INSERT INTO chunks (board_id, source, chunk_text, embedding, metadata)
                VALUES (%s, 'rule', %s, %s::vector, %s)
                """,
                (board_id, content, _vector_literal(embedding), _json(rule_metadata)),
            )
    finally:
        conn.close()


def query_rules(board_id: str, text: str, k: int = 5, where: dict | None = None) -> list[dict]:
    """Scoped semantic retrieval over rule chunks (source='rule'). `where` is
    an exact-match metadata filter -- only ever called with a single key
    today (rule_type), so the filter stays this narrow rather than
    generalizing to a multi-key/operator builder no caller needs."""
    from talos.memory.embedding import get_embed_fn
    [embedding] = get_embed_fn()([text])

    conditions = ["board_id = %s", "source = 'rule'"]
    params: list = [board_id]
    if where:
        for key, value in where.items():
            conditions.append("metadata->>%s = %s")
            params.extend([key, str(value)])

    from talos.db import board_scope, get_conn

    conn = get_conn()
    try:
        with board_scope(conn, board_id) as cur:
            cur.execute(
                f"""
                SELECT chunk_text, metadata, embedding <=> %s::vector AS distance
                FROM chunks
                WHERE {' AND '.join(conditions)}
                ORDER BY distance
                LIMIT %s
                """,
                (_vector_literal(embedding), *params, k),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return _reshape([dict(r) for r in rows])


def exclude_superseded_and_rejected(board_id: str, rules: list[dict]) -> list[dict]:
    """
    P5.5: query_rules() applies no where-filter on rules.status or
    rules.superseded_by -- and can't for superseded_by, since it's never
    embedded in vector-store metadata (only the Postgres `rules` row is
    updated when a rule is superseded; the vector chunk is never deleted or
    re-embedded, per talos.crystallize's supersede paths). This cross-checks
    each retrieved rule's id against the Postgres `rules` table and drops
    rows where superseded_by IS NOT NULL or status = 'rejected'.
    Backend-agnostic by design -- rules.superseded_by/status are Postgres-only
    truth regardless of which vector store served query_rules, and both
    stores' query_rules echo the rule id back as the row's top-level "id"
    (pgvector's _reshape via metadata.rule_id; chroma's collection id, which
    was set to rule_id at upsert time).
    """
    rule_ids = [r.get("id") for r in rules if r.get("id")]
    if not rule_ids:
        return rules

    from talos.db import board_scope, get_conn

    conn = get_conn()
    try:
        with board_scope(conn, board_id) as cur:
            cur.execute(
                "SELECT id FROM rules WHERE board_id = %s AND id = ANY(%s) "
                "AND superseded_by IS NULL AND status != 'rejected'",
                (board_id, rule_ids),
            )
            live_ids = {row["id"] for row in cur.fetchall()}
    finally:
        conn.close()

    return [r for r in rules if r.get("id") in live_ids]


def _json(obj: dict) -> str:
    import json
    return json.dumps(obj)


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
            "pgvector_store ingestion failed for board_id=%s task_id=%s", board_id, task_id
        )


def register_ingest_hook() -> None:
    from talos.hooks import default_registry

    default_registry.register("on_task_approved", _on_task_approved)
