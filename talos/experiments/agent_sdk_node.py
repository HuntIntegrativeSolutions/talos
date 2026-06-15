"""
Experimental LangGraph graph with Claude Agent SDK integration.

Purpose: verify whether query() can be safely called inside a checkpointed
LangGraph node that precedes gate_node. The critical question (Scenario 2):
does LangGraph's BSP/Pregel resume re-execute sdk_node? attempt_count answers it.

Do NOT import or use anything from talos.graph.spine, talos.critics,
talos.api, talos.worker, or talos.db — this is an isolated experiment.
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from claude_agent_sdk import ClaudeAgentOptions, HookMatcher, ResultMessage, query


class ExperimentState(TypedDict):
    prompt: str
    sdk_result: str
    gate_outcome: dict | None
    attempt_count: int  # no reducer — last-write-wins; if sdk_node re-executes, count exceeds 1
    _spans_captured: list


# ---------------------------------------------------------------------------
# sdk_node
# ---------------------------------------------------------------------------

async def sdk_node(state: ExperimentState) -> dict:
    """
    Call query() with no tools and collect the result.

    attempt_count is incremented each time this function body executes.
    On a safe resume (sdk_node was checkpointed before gate_node interrupted),
    this function does NOT re-run and attempt_count stays at 1.
    """
    attempt_count = state.get("attempt_count", 0) + 1

    sdk_result = ""
    async for message in query(
        prompt=state["prompt"],
        options=ClaudeAgentOptions(allowed_tools=[]),
    ):
        if isinstance(message, ResultMessage):
            sdk_result = message.result or ""

    return {
        "sdk_result": sdk_result,
        "attempt_count": attempt_count,
    }


# ---------------------------------------------------------------------------
# sdk_node_with_hooks
# ---------------------------------------------------------------------------

async def sdk_node_with_hooks(state: ExperimentState) -> dict:
    """
    Variant of sdk_node that registers a PostToolUse hook to capture span dicts.

    Finding for ADR-029: with allowed_tools=[], Claude produces a text-only
    response and no tool calls fire. PostToolUse hooks instrument tool calls,
    not raw LLM inference — so _spans_captured may be empty. That is a valid
    finding, not a test failure (Scenario 5 asserts nothing about span count).
    """
    attempt_count = state.get("attempt_count", 0) + 1
    captured_spans: list[dict] = []

    async def capture_span(hook_input, tool_use_id: str | None, context) -> dict:
        captured_spans.append({
            "hook_event_name": getattr(hook_input, "hook_event_name", None),
            "tool_name": getattr(hook_input, "tool_name", None),
            "tool_use_id": tool_use_id,
        })
        return {}  # pass-through; no modification to tool behaviour

    sdk_result = ""
    async for message in query(
        prompt=state["prompt"],
        options=ClaudeAgentOptions(
            allowed_tools=[],
            hooks={"PostToolUse": [HookMatcher(matcher=".*", hooks=[capture_span])]},
        ),
    ):
        if isinstance(message, ResultMessage):
            sdk_result = message.result or ""

    return {
        "sdk_result": sdk_result,
        "attempt_count": attempt_count,
        "_spans_captured": captured_spans,
    }


# ---------------------------------------------------------------------------
# gate_node — pure interrupt(), mirrors spine.py gate_node
# ---------------------------------------------------------------------------

def gate_node(state: ExperimentState) -> dict:
    """
    Human review gate. Contains ONLY interrupt().

    On resume, gate_node re-executes from line 1 and interrupt() returns
    the Command.resume value. sdk_node (which ran in a prior superstep) does
    NOT re-execute — Scenario 2 verifies this empirically.
    """
    outcome = interrupt({"sdk_result": state["sdk_result"]})
    return {"gate_outcome": outcome}


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_experiment_graph(checkpointer=None, *, use_hooks: bool = False):
    """
    Build and compile the experiment graph.

    START → sdk_node (or sdk_node_with_hooks) → gate_node → END
    """
    if checkpointer is None:
        checkpointer = MemorySaver()

    node_fn = sdk_node_with_hooks if use_hooks else sdk_node

    builder = StateGraph(ExperimentState)
    builder.add_node("sdk_node", node_fn)
    builder.add_node("gate_node", gate_node)
    builder.add_edge(START, "sdk_node")
    builder.add_edge("sdk_node", "gate_node")
    builder.add_edge("gate_node", END)

    return builder.compile(checkpointer=checkpointer)
