"""
ADR-031: Anthropic driver — wraps the existing Claude Agent SDK path.

This module owns the SDK dispatch outright (moved from talos/llm.py verbatim); it
must not import from talos.llm, which imports talos.llm_providers at module load
to re-export ModelRef and reach get_driver() — importing back would cycle.

manifest/budget_check are accepted for protocol conformance but intentionally
ignored: the Agent SDK's own MCP tool loop is opaque to us, so mid-call budget
checks aren't possible for this driver. ADR-030 enforcement for anthropic stays
entirely post-hoc in spine.read_node, unchanged from pre-ADR-031 behavior.

board_id (ADR-035/P4a) is likewise accepted but unused here: the NEXUS read
cache wraps openai_compat's explicit tool-call site only. The SDK's internal
MCP dispatch gives this driver no per-tool-call interception point, so this
path is never cached.
"""

from __future__ import annotations

import asyncio
import logging
import time

log = logging.getLogger(__name__)


class AnthropicDriver:
    def call(
        self,
        model: str,
        prompt: str,
        resume: str | None,
        *,
        allowed_tools: list[str] | None = None,
        mcp_servers: dict | None = None,
        manifest: dict | None = None,
        budget_check=None,
        board_id: str | None = None,
    ) -> tuple[str, str, int]:
        return asyncio.run(
            _async_call(model, prompt, resume, allowed_tools=allowed_tools, mcp_servers=mcp_servers)
        )


async def _async_call(
    model: str,
    prompt: str,
    resume: str | None,
    *,
    allowed_tools: list[str] | None = None,
    mcp_servers: dict | None = None,
) -> tuple[str, str, int]:
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query  # type: ignore[import]

    t0 = time.monotonic()
    text = ""
    session_id = ""
    tokens = 0

    options = ClaudeAgentOptions(
        model=model,
        allowed_tools=allowed_tools or [],
        mcp_servers=mcp_servers or {},
    )

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


def install() -> None:
    from talos.llm_providers.base import register
    register("anthropic", AnthropicDriver())
