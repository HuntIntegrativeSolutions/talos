"""
ADR-031: in-repo LLMProvider protocol, ModelRef, and driver registry.

No LangChain, no litellm — a driver is any object implementing LLMProvider.call().
Unknown-provider lookups raise UnknownProviderError, and callers are expected to
call get_driver() at config-resolution time (talos.config.resolve_model) so a typo'd
provider name surfaces before any NEXUS/model I/O, not deep inside a spine node.
"""

from __future__ import annotations

from typing import Callable, NamedTuple, Protocol


class ModelRef(NamedTuple):
    provider: str   # e.g. "anthropic" | "ollama" | "openai_compatible"
    model: str      # opaque model string, passed through unchanged


class UnknownProviderError(Exception):
    pass


# budget_check(tokens_used_so_far_this_call, tool_calls_so_far_this_call) -> None
# May raise talos.errors.BudgetExhaustedError. Drivers whose tool loop is opaque
# (anthropic, via the Agent SDK's internal MCP dispatch) never invoke it; the
# post-hoc budget check in spine.read_node still applies to them unchanged.
BudgetCheck = Callable[[int, int], None]


class LLMProvider(Protocol):
    def call(
        self,
        model: str,
        prompt: str,
        resume: str | None,
        *,
        allowed_tools: list[str] | None = None,
        mcp_servers: dict | None = None,
        manifest: dict | None = None,
        budget_check: BudgetCheck | None = None,
    ) -> tuple[str, str, int]:
        ...


_REGISTRY: dict[str, LLMProvider] = {}


def register(name: str, driver: LLMProvider) -> None:
    _REGISTRY[name] = driver


def get_driver(provider: str) -> LLMProvider:
    try:
        return _REGISTRY[provider]
    except KeyError:
        raise UnknownProviderError(
            f"unknown LLM provider {provider!r}; registered providers: {sorted(_REGISTRY)}"
        ) from None
