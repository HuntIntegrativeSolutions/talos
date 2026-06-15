"""
Claude Agent SDK × LangGraph integration prototype — five scenarios.

These tests make real API calls to Anthropic. They are skipped automatically when
ANTHROPIC_API_KEY is not set, so they will not run in CI unless the key is injected.

Run:
    TALOS_NEXUS_STUB=1 ANTHROPIC_API_KEY=<key> \\
        .venv/bin/python -m pytest talos/tests/test_agent_sdk_prototype.py -v -s

Purpose: answer the seven questions in ADR-029.
Scenario 2 is the critical test — all others are supporting evidence.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

# ---------------------------------------------------------------------------
# Skip marker — applied to every test in this module
# ---------------------------------------------------------------------------

import shutil as _shutil

# Skip if neither ANTHROPIC_API_KEY nor the claude CLI (OAuth) is available.
# OAuth users: the claude subprocess picks up stored credentials automatically.
# CI: set ANTHROPIC_API_KEY to run these tests in a pipeline.
REQUIRES_API = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY") and _shutil.which("claude") is None,
    reason="Neither ANTHROPIC_API_KEY nor claude CLI found — skipping live Agent SDK tests",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _initial_state(prompt: str) -> dict:
    return {
        "prompt": prompt,
        "sdk_result": "",
        "gate_outcome": None,
        "attempt_count": 0,
        "_spans_captured": [],
    }


def _config(label: str) -> dict:
    """Each call gets a unique thread_id so checkpoints don't collide."""
    return {"configurable": {"thread_id": f"{label}-{uuid.uuid4().hex[:8]}"}}


# ---------------------------------------------------------------------------
# Scenario 1 — Happy path
# ---------------------------------------------------------------------------

@REQUIRES_API
@pytest.mark.asyncio
async def test_scenario_1_happy_path():
    """
    query() inside an async LangGraph node with a trivial prompt.

    Assertions:
    - sdk_result is non-empty
    - attempt_count == 1
    - graph is paused at gate_node after ainvoke returns
    """
    from talos.experiments.agent_sdk_node import build_experiment_graph

    graph = build_experiment_graph(MemorySaver())
    config = _config("s1")

    result = await graph.ainvoke(_initial_state("Reply with exactly: HELLO_WORLD"), config)

    print(f"\n[Scenario 1] sdk_result: {result['sdk_result']!r}")
    print(f"[Scenario 1] attempt_count: {result['attempt_count']}")

    assert result["sdk_result"], "sdk_result must be non-empty"
    assert result["attempt_count"] == 1, (
        f"sdk_node should execute exactly once; got attempt_count={result['attempt_count']}"
    )

    snap = graph.get_state(config)
    assert snap.next == ("gate_node",), (
        f"graph should be paused at gate_node; got snap.next={snap.next!r}. "
        f"If ainvoke raised instead of returning, switch Scenarios 1-4 to astream()."
    )


# ---------------------------------------------------------------------------
# Scenario 2 — No double-execution on resume  ← THE CRITICAL TEST
# ---------------------------------------------------------------------------

@REQUIRES_API
@pytest.mark.asyncio
async def test_scenario_2_no_double_execution():
    """
    THE CRITICAL TEST for ADR-029.

    If attempt_count is 1 after resume → sdk_node placement is SAFE.
    If attempt_count is 2 after resume → sdk_node re-executed (double-execution bug).

    The VERDICT message in the assertion explains the consequence.
    """
    from talos.experiments.agent_sdk_node import build_experiment_graph

    graph = build_experiment_graph(MemorySaver())
    config = _config("s2")

    # First invocation — should pause at gate_node after sdk_node completes.
    result1 = await graph.ainvoke(_initial_state("Reply with exactly: CHECK_ONCE"), config)
    print(f"\n[Scenario 2] attempt_count before resume: {result1['attempt_count']}")
    assert result1["attempt_count"] == 1, (
        f"sdk_node should run exactly once before the interrupt; "
        f"got attempt_count={result1['attempt_count']}"
    )

    # Resume — gate_node re-executes from line 1; sdk_node must NOT re-execute.
    result2 = await graph.ainvoke(Command(resume={"outcome": "approve"}), config)
    print(f"[Scenario 2] attempt_count after resume: {result2['attempt_count']}")

    assert result2["attempt_count"] == 1, (
        f"VERDICT: UNSAFE for this node placement. "
        f"sdk_node re-executed on resume (attempt_count={result2['attempt_count']}). "
        f"Move query() to post_gate_node or remove Agent SDK from this node position. "
        f"ADR-029 Q1: sdk_node is NOT safe before gate_node."
    )


# ---------------------------------------------------------------------------
# Scenario 3 — State persists across interrupt
# ---------------------------------------------------------------------------

@REQUIRES_API
@pytest.mark.asyncio
async def test_scenario_3_state_persists():
    """
    sdk_result must survive the interrupt boundary unchanged.

    If this fails: Agent SDK result cannot be used as a deliverable because it
    disappears across the interrupt. ADR-029 Q2 would record a blocker.
    """
    from talos.experiments.agent_sdk_node import build_experiment_graph

    marker = f"MARKER_{uuid.uuid4().hex[:8]}"
    graph = build_experiment_graph(MemorySaver())
    config = _config("s3")

    result1 = await graph.ainvoke(
        _initial_state(f"Include the exact token {marker} somewhere in your reply."),
        config,
    )
    print(f"\n[Scenario 3] sdk_result before resume: {result1['sdk_result']!r}")
    assert marker in result1["sdk_result"], (
        f"marker {marker!r} must appear in sdk_result before interrupt; "
        f"got: {result1['sdk_result']!r}"
    )

    result2 = await graph.ainvoke(Command(resume={"outcome": "approve"}), config)
    print(f"[Scenario 3] sdk_result after resume: {result2['sdk_result']!r}")
    assert marker in result2["sdk_result"], (
        f"sdk_result must persist across the interrupt boundary; "
        f"got: {result2['sdk_result']!r}"
    )


# ---------------------------------------------------------------------------
# Scenario 4 — Concurrent sessions are isolated
# ---------------------------------------------------------------------------

@REQUIRES_API
@pytest.mark.asyncio
async def test_scenario_4_concurrent_isolation():
    """
    Two graph threads run concurrently with different markers.
    Each thread's sdk_result must contain only its own marker.

    If isolation fails: multi-worker P3 needs additional per-session isolation.
    ADR-029 Q3.
    """
    from talos.experiments.agent_sdk_node import build_experiment_graph

    marker_a = f"ALPHA_{uuid.uuid4().hex[:8]}"
    marker_b = f"BETA_{uuid.uuid4().hex[:8]}"

    graph = build_experiment_graph(MemorySaver())
    config_a = _config("s4a")
    config_b = _config("s4b")

    res_a, res_b = await asyncio.gather(
        graph.ainvoke(
            _initial_state(f"Include the exact token {marker_a} in your reply."),
            config_a,
        ),
        graph.ainvoke(
            _initial_state(f"Include the exact token {marker_b} in your reply."),
            config_b,
        ),
    )

    print(f"\n[Scenario 4] thread A sdk_result: {res_a['sdk_result']!r}")
    print(f"[Scenario 4] thread B sdk_result: {res_b['sdk_result']!r}")

    assert marker_a in res_a["sdk_result"], f"thread A must contain its own marker"
    assert marker_b not in res_a["sdk_result"], (
        f"thread A must NOT contain thread B's marker — sessions are not isolated"
    )
    assert marker_b in res_b["sdk_result"], f"thread B must contain its own marker"
    assert marker_a not in res_b["sdk_result"], (
        f"thread B must NOT contain thread A's marker — sessions are not isolated"
    )
    assert res_a["attempt_count"] == 1, f"thread A: attempt_count should be 1"
    assert res_b["attempt_count"] == 1, f"thread B: attempt_count should be 1"


# ---------------------------------------------------------------------------
# Scenario 5 — Hook fires and is observable
# ---------------------------------------------------------------------------

@REQUIRES_API
@pytest.mark.asyncio
async def test_scenario_5_hooks():
    """
    PostToolUse hook is registered. With allowed_tools=[], Claude produces
    a text-only response and no tool calls fire — so _spans_captured may be
    empty. EMPTY SPANS IS A VALID FINDING, not a test failure.

    Finding for ADR-029 Q4: if _spans_captured is empty, PostToolUse hooks
    do not fire for raw text-only LLM inference. ADR-022 tracing for
    model-call spans would need a different mechanism (e.g. wrapping the
    query() call itself rather than relying on tool hooks).
    """
    from talos.experiments.agent_sdk_node import build_experiment_graph

    graph = build_experiment_graph(MemorySaver(), use_hooks=True)
    config = _config("s5")

    result = await graph.ainvoke(_initial_state("Reply with exactly: HOOK_TEST"), config)

    print(f"\n[Scenario 5] _spans_captured count: {len(result['_spans_captured'])}")
    print(f"[Scenario 5] _spans_captured: {result['_spans_captured']}")
    print(f"[Scenario 5] sdk_result: {result['sdk_result']!r}")

    # attempt_count and sdk_result are mandatory — span count is a finding.
    assert result["attempt_count"] == 1, (
        f"sdk_node_with_hooks should execute exactly once; "
        f"got attempt_count={result['attempt_count']}"
    )
    assert result["sdk_result"], "sdk_result must be non-empty even with hooks registered"
    # Do NOT assert len(result["_spans_captured"]) > 0 — empty is the expected finding
    # for text-only responses. Document the actual count in ADR-029.
