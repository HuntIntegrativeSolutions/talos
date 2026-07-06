"""
TALOS LLM wrapper (P3b, ADR-018/ADR-022/ADR-029; multi-provider ADR-031).

call_model(model_ref, prompt, resume) -> (text, session_id, tokens)

Synchronous entry point that dispatches to the driver registered for
model_ref.provider (talos.llm_providers). Spine and worker import ModelRef and
call_model from this module only — never reach into talos.llm_providers directly.

Primary → fallback retry is handled by the caller (worker slot / spine's
_call_with_fallback), not here. This module raises ModelCallError on failure so
the caller can decide to try the fallback model or escalate to the gate.
BudgetExhaustedError (raised mid-call by a driver's budget_check, ADR-030/031)
is NOT wrapped into ModelCallError — it must propagate unchanged so the caller
escalates instead of retrying the fallback against an already-exhausted budget.

Under TALOS_NEXUS_STUB=1: returns canned values immediately; no driver is touched.
"""

from __future__ import annotations

import logging
import time

from talos.llm_providers import ModelRef, get_driver

log = logging.getLogger(__name__)

__all__ = ["ModelCallError", "ModelRef", "call_model"]


class ModelCallError(Exception):
    pass


def call_model(
    model_ref: ModelRef,
    prompt: str,
    resume: str | None = None,
    *,
    span_ctx=None,
    allowed_tools: list[str] | None = None,
    mcp_servers: dict | None = None,
    manifest: dict | None = None,
    budget_check=None,
) -> tuple[str, str, int]:
    """
    Call the driver registered for model_ref.provider.

    Returns (text, session_id, tokens).
    Raises ModelCallError on any driver error.
    Raises BudgetExhaustedError unchanged if the driver's budget_check aborts
    mid-call (ADR-030/031) — never wrapped into ModelCallError.

    Under TALOS_NEXUS_STUB=1: returns stub values without dispatching to any driver.
    If span_ctx (SpanContext) is provided, emits an llm.call span with latency.

    allowed_tools/mcp_servers (ADR-038): optional NEXUS MCP wiring, built by
    talos.nexus_client from the pinned capability manifest. manifest is passed
    through as well so non-Anthropic drivers can build their own tool schemas
    (the Agent SDK only needs allowed_tools/mcp_servers). Ignored entirely
    under TALOS_NEXUS_STUB=1.
    """
    import os
    if os.environ.get("TALOS_NEXUS_STUB") == "1":
        _maybe_emit_llm_span(span_ctx, model_ref, 0, 0, 0)
        return "stub-response", "stub-session-id", 0

    from talos.errors import BudgetExhaustedError

    driver = get_driver(model_ref.provider)
    t0 = time.monotonic()
    try:
        result = driver.call(
            model_ref.model, prompt, resume,
            allowed_tools=allowed_tools, mcp_servers=mcp_servers,
            manifest=manifest, budget_check=budget_check,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        _maybe_emit_llm_span(span_ctx, model_ref, result[2], 0, latency_ms)
        return result
    except BudgetExhaustedError:
        raise
    except Exception as exc:
        raise ModelCallError(f"model {model_ref!r} call failed: {exc}") from exc


def _maybe_emit_llm_span(span_ctx, model_ref: ModelRef, tokens: int, prompt_tokens: int, latency_ms: int) -> None:
    if span_ctx is None:
        return
    try:
        from talos.spans import emit_span
        emit_span(
            span_ctx,
            "llm.call",
            model_id=model_ref.model,
            provider=model_ref.provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=tokens,
            latency_ms=max(latency_ms, 1),
        )
    except Exception:
        pass
