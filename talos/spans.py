"""
TALOS span emission utility (P3d, ADR-022).

emit_span() writes one row to task_spans. In TALOS_NEXUS_STUB=1 mode,
spans are buffered in-memory (_STUB_BUFFER) so tests can inspect them
without a database connection.

The 12 P3 minimum span names:
  worker.claim              worker.claim_race_loss
  worker.heartbeat_miss     worker.reclaim
  spine.node.{name}.entry   spine.node.{name}.exit
  spine.critic.{name}       spine.gate.interrupt
  spine.gate.resume         spine.post_gate.write
  llm.call                  spine.budget.soft_threshold
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# In-memory buffer used when TALOS_NEXUS_STUB=1.
_STUB_BUFFER: list[dict] = []


def clear_stub_buffer() -> None:
    """Clear the in-memory span buffer. Call in test fixtures before each test."""
    _STUB_BUFFER.clear()


@dataclass
class SpanContext:
    board_id: str
    task_id: str
    run_id: int | None = None


def emit_span(
    ctx: SpanContext,
    span_name: str,
    *,
    model_id: str | None = None,
    provider: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    latency_ms: int | None = None,
    parent_span_id: int | str | None = None,
    payload: dict | None = None,
    db_conn=None,
) -> int | str:
    """
    Emit one observability span.

    In TALOS_NEXUS_STUB=1 mode: appends to _STUB_BUFFER; returns a stub ID.
    In live mode: inserts into task_spans via db_conn; returns the new row id.
    """
    record: dict[str, Any] = {
        "board_id": ctx.board_id,
        "task_id": ctx.task_id,
        "run_id": ctx.run_id,
        "span_name": span_name,
        "model_id": model_id,
        "provider": provider,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "latency_ms": latency_ms,
        "parent_span_id": parent_span_id,
        "payload": payload,
    }

    if os.environ.get("TALOS_NEXUS_STUB") == "1":
        _STUB_BUFFER.append(record)
        return f"stub-{len(_STUB_BUFFER)}"

    if db_conn is None:
        log.warning("emit_span called without db_conn in live mode; span dropped: %s", span_name)
        return -1

    try:
        import json
        import psycopg2.extras
        with db_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO task_spans
                    (board_id, task_id, run_id, parent_span_id, span_name,
                     model_id, provider, prompt_tokens, completion_tokens,
                     latency_ms, payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                RETURNING id
                """,
                (
                    ctx.board_id, ctx.task_id, ctx.run_id,
                    parent_span_id, span_name,
                    model_id, provider, prompt_tokens, completion_tokens,
                    latency_ms,
                    json.dumps(payload) if payload is not None else None,
                ),
            )
            return cur.fetchone()["id"]
    except Exception:
        log.exception("failed to emit span %r", span_name)
        return -1
