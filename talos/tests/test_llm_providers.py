"""
talos.llm_providers unit tests (ADR-031).

Covers: resolve_model provider cascade + legacy backward-compat, driver
dispatch + unknown-provider-at-resolution, the openai_compatible tool-call
loop (mocked HTTP, mocked nexus_client) including mid-loop budget-cap
early-exit, and cross-provider fallback call order.

No real network. Drivers registered by fake tests are restored via the
clean_registry fixture so they never leak into other tests/modules.
"""

from __future__ import annotations

import unittest.mock as mock

import pytest

import talos.llm
import talos.llm_providers.openai_compat as openai_compat
from talos.config import resolve_model
from talos.errors import BudgetExhaustedError
from talos.graph.spine import _call_with_fallback
from talos.llm_providers.base import _REGISTRY, ModelRef, UnknownProviderError, register


@pytest.fixture()
def clean_registry():
    """Snapshot/restore the global driver registry so fake drivers registered
    in one test never leak into another."""
    snapshot = dict(_REGISTRY)
    yield
    _REGISTRY.clear()
    _REGISTRY.update(snapshot)


@pytest.fixture()
def nexus_live_mode(monkeypatch):
    """call_model() short-circuits under TALOS_NEXUS_STUB=1 (set in the test
    environment) before any driver is touched. Tests that need to observe
    actual dispatch must force the non-stub branch."""
    monkeypatch.delenv("TALOS_NEXUS_STUB", raising=False)


# ---------------------------------------------------------------------------
# (1) resolve_model cascade + provider keys + legacy backward-compat
# ---------------------------------------------------------------------------

def test_resolve_model_hardcoded_defaults_are_anthropic():
    primary, fallback = resolve_model("research")
    assert primary == ModelRef("anthropic", "claude-sonnet-4-6")
    assert fallback == ModelRef("anthropic", "claude-haiku-4-5-20251001")


def test_resolve_model_board_override_with_provider_fallback_inherits_primary():
    board = {
        "model_config": {
            "research_primary": "llama3.1:70b",
            "research_primary_provider": "ollama",
            "research_fallback": "claude-haiku-4-5-20251001",
            # no research_fallback_provider key set
        }
    }
    primary, fallback = resolve_model("research", board=board)
    assert primary == ModelRef("ollama", "llama3.1:70b")
    # Missing fallback_provider inherits primary_provider, not a hardcoded
    # "anthropic" — a partial air-gap override must not silently reach for
    # Anthropic on fallback.
    assert fallback == ModelRef("ollama", "claude-haiku-4-5-20251001")


def test_resolve_model_board_override_explicit_fallback_provider():
    board = {
        "model_config": {
            "research_primary_provider": "ollama",
            "research_fallback_provider": "anthropic",
        }
    }
    primary, fallback = resolve_model("research", board=board)
    assert primary.provider == "ollama"
    assert fallback.provider == "anthropic"


def test_resolve_model_task_override_provider():
    task = {"model_override": "mixtral", "model_override_provider": "ollama"}
    primary, fallback = resolve_model("research", task=task)
    assert primary == fallback == ModelRef("ollama", "mixtral")


def test_resolve_model_task_override_defaults_anthropic():
    task = {"model_override": "gpt-x"}
    primary, fallback = resolve_model("research", task=task)
    assert primary == fallback == ModelRef("anthropic", "gpt-x")


# ---------------------------------------------------------------------------
# (2) driver dispatch + unknown-provider raised at resolution time
# ---------------------------------------------------------------------------

def test_resolve_model_unknown_provider_raises_at_resolution():
    board = {"model_config": {"research_primary_provider": "bogus"}}
    with pytest.raises(UnknownProviderError):
        resolve_model("research", board=board)


def test_call_model_dispatches_to_registered_driver(clean_registry, nexus_live_mode):
    calls = []

    class FakeDriver:
        def call(self, model, prompt, resume, *, allowed_tools=None, mcp_servers=None,
                  manifest=None, budget_check=None):
            calls.append((model, prompt))
            return "fake text", "fake-session", 7

    register("fake_provider", FakeDriver())
    text, session_id, tokens = talos.llm.call_model(ModelRef("fake_provider", "m"), "hi")
    assert (text, session_id, tokens) == ("fake text", "fake-session", 7)
    assert calls == [("m", "hi")]


# ---------------------------------------------------------------------------
# (3) openai_compatible tool-call loop + mid-loop budget cap
# ---------------------------------------------------------------------------

def _fake_response(payload):
    resp = mock.Mock()
    resp.raise_for_status = lambda: None
    resp.json = lambda: payload
    return resp


_TOOL_CALL_ROUND = {
    "choices": [{"message": {
        "tool_calls": [{"id": "tc1", "function": {"name": "find_docs_for_tag", "arguments": '{"tag":"X"}'}}]
    }}],
    "usage": {"completion_tokens": 5},
}

_FINAL_ROUND = {
    "choices": [{"message": {"content": "done"}}],
    "usage": {"completion_tokens": 3},
}


class _FakeTool:
    name = "find_docs_for_tag"
    description = "d"
    inputSchema = {"type": "object", "properties": {}}


def test_openai_compat_tool_loop_calls_nexus_and_returns_text(monkeypatch):
    monkeypatch.setenv("TALOS_LLM_OPENAI_COMPATIBLE_API_KEY", "k")
    driver = openai_compat.OpenAICompatibleDriver("openai_compatible", "http://x/v1", True)

    posts = [_fake_response(_TOOL_CALL_ROUND), _fake_response(_FINAL_ROUND)]
    monkeypatch.setattr(openai_compat.requests, "post", mock.Mock(side_effect=posts))

    async def fake_list(url):
        return [_FakeTool()]

    tool_calls_seen = []

    async def fake_call(url, name, args):
        tool_calls_seen.append((name, args))
        result = mock.Mock()
        result.content = [mock.Mock(text="tool result text")]
        return result

    monkeypatch.setattr(openai_compat, "list_nexus_tools_raw", fake_list)
    monkeypatch.setattr(openai_compat, "call_nexus_tool_raw", fake_call)

    text, session_id, tokens = driver.call(
        "m", "prompt", None,
        mcp_servers={"nexus": {"url": "http://nexus/mcp"}},
        manifest={"tools": [{"name": "find_docs_for_tag", "profile": "read"}]},
    )
    assert text == "done"
    assert tokens == 8  # 5 + 3
    assert tool_calls_seen == [("find_docs_for_tag", {"tag": "X"})]
    assert openai_compat.requests.post.call_count == 2


def test_openai_compat_budget_cap_stops_before_second_round(monkeypatch):
    monkeypatch.setenv("TALOS_LLM_OPENAI_COMPATIBLE_API_KEY", "k")
    driver = openai_compat.OpenAICompatibleDriver("openai_compatible", "http://x/v1", True)
    monkeypatch.setattr(
        openai_compat.requests, "post",
        mock.Mock(side_effect=[_fake_response(_TOOL_CALL_ROUND)]),
    )

    async def fake_list(url):
        return []

    monkeypatch.setattr(openai_compat, "list_nexus_tools_raw", fake_list)

    def budget_check(tokens_so_far, tool_calls_so_far):
        if tokens_so_far > 1:
            raise BudgetExhaustedError(task_id="t", run_id=1, board_id="b", reason="cap")

    with pytest.raises(BudgetExhaustedError):
        driver.call(
            "m", "prompt", None,
            mcp_servers={"nexus": {"url": "u"}},
            manifest={"tools": []},
            budget_check=budget_check,
        )

    # Proves early-exit, not spinning: exactly one HTTP round, no tool executed.
    assert openai_compat.requests.post.call_count == 1


# ---------------------------------------------------------------------------
# (4) cross-provider fallback: anthropic primary fails -> ollama fallback attempted
# ---------------------------------------------------------------------------

def test_cross_provider_fallback_anthropic_to_ollama(clean_registry, nexus_live_mode):
    call_log = []

    class FailingDriver:
        def call(self, model, prompt, resume, *, allowed_tools=None, mcp_servers=None,
                  manifest=None, budget_check=None):
            call_log.append(("anthropic", model))
            raise RuntimeError("primary down")

    class OkDriver:
        def call(self, model, prompt, resume, *, allowed_tools=None, mcp_servers=None,
                  manifest=None, budget_check=None):
            call_log.append(("ollama", model))
            return "fallback text", "session-2", 5

    register("anthropic", FailingDriver())
    register("ollama", OkDriver())

    state = {"task_id": "t1", "run_id": 1, "board_id": "b1"}
    text, session_id, tokens = _call_with_fallback(
        ModelRef("anthropic", "claude-x"), ModelRef("ollama", "llama-y"),
        prompt="p", resume=None, state=state,
    )
    assert call_log == [("anthropic", "claude-x"), ("ollama", "llama-y")]
    assert (text, session_id, tokens) == ("fallback text", "session-2", 5)
