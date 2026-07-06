"""
ADR-031: OpenAI-compatible driver — owns its own tool-call loop.

The Claude Agent SDK's native MCP dispatch is Anthropic-only, so any
OpenAI-compatible model (Ollama, self-hosted vLLM, DeepSeek's API, etc.) needs its
own loop: translate the manifest-filtered NEXUS tool list into OpenAI
function-calling schemas, execute tool calls through talos.nexus_client's real MCP
tools/call, feed results back as "tool" messages, and repeat until the model stops
calling tools or budget_check raises.

Registers two provider names against the same class:
  "openai_compatible" — generic; requires TALOS_LLM_OPENAI_COMPATIBLE_BASE_URL and
                         TALOS_LLM_OPENAI_COMPATIBLE_API_KEY. DeepSeek and other
                         hosted/self-hosted OpenAI-compatible endpoints use this
                         with their own base_url — no separate driver per vendor.
  "ollama"            — no credential required; default base_url
                         http://localhost:11434/v1 (overridable).
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import requests

from talos.nexus_client import allowed_nexus_tool_names, call_nexus_tool_raw, list_nexus_tools_raw


def _to_openai_tool_schema(raw_tool) -> dict:
    return {
        "type": "function",
        "function": {
            "name": raw_tool.name,
            "description": raw_tool.description or "",
            "parameters": raw_tool.inputSchema or {"type": "object", "properties": {}},
        },
    }


def _stringify_tool_result(result) -> str:
    content = getattr(result, "content", None)
    if content:
        return "\n".join(getattr(part, "text", str(part)) for part in content)
    return str(result)


class OpenAICompatibleDriver:
    def __init__(self, provider_name: str, default_base_url: str | None, requires_api_key: bool):
        self.provider_name = provider_name
        self.default_base_url = default_base_url
        self.requires_api_key = requires_api_key

    def _call_tool_cached(
        self, board_id: str | None, manifest: dict | None, nexus_url: str, name: str, args: dict
    ) -> str:
        """
        Board-scoped NEXUS read cache (ADR-035/P4a). Only reachable from this
        driver's explicit tool loop — the Anthropic driver's MCP dispatch is
        opaque and has no equivalent interception point.
        """
        from talos import nexus_cache

        cacheable = board_id is not None and manifest is not None and nexus_cache.is_cacheable(name, manifest)
        if cacheable:
            cached = nexus_cache.get_cached(board_id, name, args)
            if cached is not None:
                return cached

        result = asyncio.run(call_nexus_tool_raw(nexus_url, name, args))
        content = _stringify_tool_result(result)

        if cacheable:
            ttl = nexus_cache.get_ttl_seconds(board_id)
            nexus_cache.put_cached(board_id, name, args, content, ttl)

        return content

    def _credentials(self) -> tuple[str | None, str]:
        from talos.llm import ModelCallError

        prefix = self.provider_name.upper()
        api_key = os.environ.get(f"TALOS_LLM_{prefix}_API_KEY")
        base_url = os.environ.get(f"TALOS_LLM_{prefix}_BASE_URL", self.default_base_url)
        if self.requires_api_key and not api_key:
            raise ModelCallError(
                f"missing TALOS_LLM_{prefix}_API_KEY for provider {self.provider_name!r}"
            )
        if not base_url:
            raise ModelCallError(f"no base_url configured for provider {self.provider_name!r}")
        return api_key, base_url

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
        api_key, base_url = self._credentials()
        nexus_url = (mcp_servers or {}).get("nexus", {}).get("url")

        tools_schema: list[dict] = []
        if manifest is not None and nexus_url:
            raw_tools = asyncio.run(list_nexus_tools_raw(nexus_url))
            allowed = set(allowed_nexus_tool_names(manifest))
            tools_schema = [_to_openai_tool_schema(t) for t in raw_tools if t.name in allowed]

        messages = [{"role": "user", "content": prompt}]
        session_id = resume or str(uuid.uuid4())
        tokens_total = 0
        tool_calls_made = 0
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

        while True:
            resp = requests.post(
                f"{base_url}/chat/completions",
                json={"model": model, "messages": messages, "tools": tools_schema or None},
                headers=headers,
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
            msg = data["choices"][0]["message"]
            tokens_total += (data.get("usage") or {}).get("completion_tokens", 0)

            # Budget checkpoint: right after learning this round's cost, before
            # acting on any tool_calls or spending another HTTP round trip. This
            # is what makes a budget overrun escalate instead of spin.
            if budget_check is not None:
                budget_check(tokens_total, tool_calls_made)

            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                return msg.get("content") or "", session_id, tokens_total

            messages.append(msg)
            for tc in tool_calls:
                tool_calls_made += 1
                if budget_check is not None:
                    budget_check(tokens_total, tool_calls_made)
                name = tc["function"]["name"]
                args = json.loads(tc["function"].get("arguments") or "{}")
                content = self._call_tool_cached(board_id, manifest, nexus_url, name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": content,
                })
            # loop back and call the model again with tool results appended


def install() -> None:
    from talos.llm_providers.base import register
    register("openai_compatible", OpenAICompatibleDriver("openai_compatible", None, requires_api_key=True))
    register("ollama", OpenAICompatibleDriver("ollama", "http://localhost:11434/v1", requires_api_key=False))
