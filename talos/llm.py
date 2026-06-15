"""
TALOS LLM wrapper (P3b, ADR-018/ADR-022/ADR-029).

call_model(model, prompt, resume) -> (text, session_id, tokens)

Synchronous entry point that wraps async for query() from the Claude Agent SDK.
Uses asyncio.run() internally so spine nodes stay synchronous.

Primary → fallback retry is handled by the caller (worker slot), not here.
This module raises ModelCallError on failure so the caller can decide to
try the fallback model or escalate to the gate.

Under TALOS_NEXUS_STUB=1: returns canned values immediately; query() never called.
"""

from __future__ import annotations

import asyncio
import logging
import time

log = logging.getLogger(__name__)


class ModelCallError(Exception):
    pass


def call_model(
    model: str,
    prompt: str,
    resume: str | None = None,
    *,
    span_ctx=None,
) -> tuple[str, str, int]:
    """
    Call the Claude Agent SDK synchronously.

    Returns (text, session_id, tokens).
    Raises ModelCallError on any SDK error.

    Under TALOS_NEXUS_STUB=1: returns stub values without calling query().
    If span_ctx (SpanContext) is provided, emits an llm.call span with latency.
    """
    import os
    if os.environ.get("TALOS_NEXUS_STUB") == "1":
        _maybe_emit_llm_span(span_ctx, model, 0, 0, 0)
        return "stub-response", "stub-session-id", 0

    t0 = time.monotonic()
    try:
        result = asyncio.run(_async_call(model, prompt, resume))
        latency_ms = int((time.monotonic() - t0) * 1000)
        _maybe_emit_llm_span(span_ctx, model, result[2], 0, latency_ms)
        return result
    except Exception as exc:
        raise ModelCallError(f"model {model!r} call failed: {exc}") from exc


def _maybe_emit_llm_span(span_ctx, model: str, tokens: int, prompt_tokens: int, latency_ms: int) -> None:
    if span_ctx is None:
        return
    try:
        from talos.spans import emit_span
        emit_span(
            span_ctx,
            "llm.call",
            model_id=model,
            provider="anthropic",
            prompt_tokens=prompt_tokens,
            completion_tokens=tokens,
            latency_ms=max(latency_ms, 1),
        )
    except Exception:
        pass


async def _async_call(
    model: str,
    prompt: str,
    resume: str | None,
) -> tuple[str, str, int]:
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query  # type: ignore[import]

    t0 = time.monotonic()
    text = ""
    session_id = ""
    tokens = 0

    options = ClaudeAgentOptions(model=model, allowed_tools=[])

    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, ResultMessage):
            text = msg.result or ""
            # SDK v0.2.x exposes session_id on the stream object, not ResultMessage.
            # Capture via str representation as a safe fallback.
            session_id = getattr(msg, "session_id", "") or ""
            tokens = getattr(msg, "usage", {})
            if isinstance(tokens, dict):
                tokens = tokens.get("output_tokens", 0) or 0
            else:
                tokens = 0

    latency_ms = int((time.monotonic() - t0) * 1000)
    log.debug("llm.call model=%s tokens=%s latency_ms=%s", model, tokens, latency_ms)

    return text, session_id, tokens
