# Prototype Task — Claude Agent SDK + LangGraph Integration

> **Historical note:** this prompt predates the `platform/` → `talos/` rename; current code lives
> in `talos/`. Retained as written for the historical record.

**Purpose:** Determine whether the Claude Agent SDK's `query()` can be safely called inside
a checkpointed LangGraph node. The result becomes ADR-029, which the P3 implementation
prompt depends on.

**Do not modify any existing production files** (`platform/graph/spine.py`,
`platform/critics/`, `platform/api.py`, `platform/worker.py`, `platform/db.py`, engine
SQL, or any ADR already written). Everything you build goes in `platform/experiments/` and
`platform/tests/test_agent_sdk_prototype.py`. These are clearly marked experimental and
will be cleaned up or promoted after ADR-029 is confirmed.

**Time budget:** This should be completable in one session (~2 hours). If a scenario
cannot be resolved cleanly, document the failure mode and continue — the point is a verdict,
not a perfect implementation.

---

## Context — read these files before writing any code

- `/mnt/i/talos/CLAUDE.md` — project state, test commands, repo layout
- `/mnt/i/talos/platform/graph/spine.py` — the FULL production LangGraph spine (read it
  completely; understand the 4-node graph, `interrupt()` in `gate_node`, `_route_after_gate`,
  `build_graph()`, and how `MemorySaver` is passed in)
- `/mnt/i/talos/docs/decisions/ADR-011.md` — five gate outcomes
- `/mnt/i/talos/docs/decisions/ADR-010.md` — worker isolation and session keys
- `/mnt/i/talos/docs/decisions/ADR-022.md` — observability/tracing (hooks are the candidate
  mechanism for spans inside Agent SDK calls)
- `/mnt/i/talos/docs/upstream/claude-agent-sdk-notes.md` — the research findings this
  prototype is validating; pay particular attention to the "hypothesis" note in Finding 3

After reading, you will understand: TALOS's spine uses LangGraph's BSP/Pregel model.
Nodes re-execute from their first line on resume after an `interrupt()`. All side effects
must be in `post_gate_node`, never in `gate_node`. The Agent SDK spawns a Claude Code
subprocess via `claude_agent_sdk.query()`. If that subprocess starts, dies, and LangGraph
retries the node, a second subprocess spawns. Whether this is safe depends on which node
the call lives in and whether the checkpoint guards against double-execution.

---

## Prerequisites — install before writing any code

1. Add `claude-agent-sdk` to `pyproject.toml` under `[project.optional-dependencies]` →
   `experiment`:

   ```toml
   [project.optional-dependencies]
   experiment = [
       "claude-agent-sdk",
   ]
   ```

   Install it:
   ```bash
   cd /mnt/i/talos && .venv/bin/pip install -e ".[experiment]"
   ```

2. Verify the `claude` CLI is available on `PATH` (the Agent SDK spawns it as a subprocess):
   ```bash
   claude --version
   ```
   If not found, install via `npm install -g @anthropic-ai/claude-code` or the appropriate
   method for this environment. Document what you find.

3. Verify `ANTHROPIC_API_KEY` is set. The Agent SDK makes real API calls. These prototype
   tests are **NOT for CI** — they require a live API key and will consume tokens. Mark every
   test in this file with `@pytest.mark.requires_api_key` and add the following at the top
   of the test file so they skip automatically in CI:

   ```python
   import os
   import pytest

   requires_api_key = pytest.mark.skipif(
       not os.environ.get("ANTHROPIC_API_KEY"),
       reason="requires ANTHROPIC_API_KEY — not for CI",
   )
   ```

4. Set `TALOS_NEXUS_STUB=1` for all test runs (the existing spine's `read_node` requires it).

---

## What to build

### File 1: `platform/experiments/agent_sdk_node.py`

A minimal experimental LangGraph node that calls `query()`. This is NOT a replacement for
any production node — it is an isolated experiment.

```python
"""
Experimental: Agent SDK inside a LangGraph node.
Validates ADR-029 before P3 implementation begins.
NOT production code.
"""
from __future__ import annotations

import asyncio
from typing import TypedDict

from claude_agent_sdk import query, ClaudeAgentOptions
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt


class ExperimentState(TypedDict):
    prompt: str
    sdk_result: str | None
    gate_outcome: str | None
    attempt_count: int  # incremented each time the sdk_node fires — detects double-execution


async def sdk_node(state: ExperimentState) -> dict:
    """
    The node under test. Calls query() with a minimal prompt, collects the result,
    and increments attempt_count so tests can detect double-execution on retry.
    """
    attempt = state.get("attempt_count", 0) + 1
    result_parts = []
    async for message in query(
        prompt=state["prompt"],
        options=ClaudeAgentOptions(allowed_tools=[]),  # no tools — pure text response
    ):
        if hasattr(message, "result"):
            result_parts.append(message.result)
    return {
        "sdk_result": "\n".join(result_parts) if result_parts else "",
        "attempt_count": attempt,
    }


def gate_node(state: ExperimentState) -> dict:
    """Minimal gate using interrupt() — mirrors production gate_node."""
    outcome = interrupt({"sdk_result": state["sdk_result"]})
    return {"gate_outcome": outcome.get("outcome")}


def _route(state: ExperimentState) -> str:
    return END


def build_experiment_graph(checkpointer=None):
    if checkpointer is None:
        checkpointer = MemorySaver()
    builder = StateGraph(ExperimentState)
    builder.add_node("sdk_node", sdk_node)
    builder.add_node("gate_node", gate_node)
    builder.add_edge(START, "sdk_node")
    builder.add_edge("sdk_node", "gate_node")
    builder.add_conditional_edges("gate_node", _route)
    return builder.compile(checkpointer=checkpointer)
```

---

### File 2: `platform/tests/test_agent_sdk_prototype.py`

Five scenarios. Run them in order. Each has a clear pass/fail verdict to document.

```python
"""
Agent SDK + LangGraph integration prototype tests.
All tests require ANTHROPIC_API_KEY and are skipped in CI.
Results feed ADR-029.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from platform.experiments.agent_sdk_node import build_experiment_graph

requires_api_key = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="requires ANTHROPIC_API_KEY — not for CI",
)
```

---

#### Scenario 1 — Happy path: `query()` inside an async LangGraph node completes

**What it tests:** The basic async integration. Can an async LangGraph node call
`query()` with `async for`, collect the result, and return it to LangGraph state?

```python
@requires_api_key
@pytest.mark.asyncio
async def test_scenario_1_happy_path():
    """
    sdk_node calls query() with a trivial prompt. Graph runs to interrupt().
    Verify: sdk_result is non-empty, attempt_count == 1.
    """
    graph = build_experiment_graph()
    thread = {"configurable": {"thread_id": str(uuid.uuid4())}}
    state_snapshot = None

    async for event in graph.astream(
        {"prompt": "Reply with exactly: TALOS_TEST_OK", "attempt_count": 0},
        config=thread,
    ):
        pass  # run until interrupt

    state_snapshot = graph.get_state(thread)
    assert state_snapshot.next == ("gate_node",), "Graph should be paused at gate_node"
    assert state_snapshot.values["sdk_result"], "sdk_result should be non-empty"
    assert state_snapshot.values["attempt_count"] == 1, "sdk_node should have run exactly once"
    # VERDICT: if assertions pass, async query() inside a LangGraph node works.
```

---

#### Scenario 2 — Checkpoint isolation: `sdk_node` does NOT re-run after gate resume

**What it tests:** The core BSP safety question. After `interrupt()` pauses at `gate_node`,
and the human sends `Command(resume=...)`, does LangGraph re-run `sdk_node`? It must NOT —
`sdk_node` is already checkpointed. If `attempt_count` increments on resume, that is a
double-execution bug.

```python
@requires_api_key
@pytest.mark.asyncio
async def test_scenario_2_no_double_execution_on_resume():
    """
    Run graph to interrupt, then resume. sdk_node must NOT re-execute on resume.
    attempt_count must remain 1 after resume.
    """
    graph = build_experiment_graph()
    thread = {"configurable": {"thread_id": str(uuid.uuid4())}}

    # Phase 1: run to interrupt
    async for _ in graph.astream(
        {"prompt": "Reply with exactly: TALOS_TEST_OK", "attempt_count": 0},
        config=thread,
    ):
        pass

    attempt_before_resume = graph.get_state(thread).values["attempt_count"]
    assert attempt_before_resume == 1

    # Phase 2: resume the gate
    async for _ in graph.astream(
        Command(resume={"outcome": "approve"}),
        config=thread,
    ):
        pass

    final_state = graph.get_state(thread)
    assert final_state.values["attempt_count"] == 1, (
        f"sdk_node re-executed on resume — attempt_count is "
        f"{final_state.values['attempt_count']}, expected 1. "
        f"This is the double-execution bug. VERDICT: UNSAFE for this node placement."
    )
    # VERDICT: if attempt_count == 1 after resume, checkpoint isolation works correctly.
```

---

#### Scenario 3 — State persistence: `sdk_result` survives across the interrupt boundary

**What it tests:** After `interrupt()`, does `sdk_result` remain in LangGraph state?
The gate operator needs to read the deliverable from state. If state is dropped or
corrupted across the interrupt, the gate cannot work.

```python
@requires_api_key
@pytest.mark.asyncio
async def test_scenario_3_state_persists_across_interrupt():
    """
    sdk_result written by sdk_node must be readable after interrupt() and after resume.
    """
    graph = build_experiment_graph()
    thread = {"configurable": {"thread_id": str(uuid.uuid4())}}
    marker = "TALOS_PERSIST_TEST_" + str(uuid.uuid4())[:8]

    async for _ in graph.astream(
        {"prompt": f"Reply with exactly: {marker}", "attempt_count": 0},
        config=thread,
    ):
        pass

    # State at interrupt point
    paused_state = graph.get_state(thread)
    sdk_result_at_pause = paused_state.values.get("sdk_result", "")
    assert marker in sdk_result_at_pause, (
        f"sdk_result does not contain expected marker '{marker}'. "
        f"Got: {sdk_result_at_pause!r}"
    )

    # Resume and check state is still intact
    async for _ in graph.astream(
        Command(resume={"outcome": "approve"}),
        config=thread,
    ):
        pass

    final_state = graph.get_state(thread)
    assert marker in final_state.values.get("sdk_result", ""), (
        "sdk_result was lost or mutated after resume."
    )
    # VERDICT: if both assertions pass, sdk_result persists correctly across interrupt.
```

---

#### Scenario 4 — Concurrent sessions: two `query()` calls with different thread_ids do not interfere

**What it tests:** P3 will run multiple workers concurrently, each handling a different task.
Each task has its own LangGraph `thread_id`. Two simultaneous `query()` calls must produce
independent results and not cross-contaminate state.

```python
@requires_api_key
@pytest.mark.asyncio
async def test_scenario_4_concurrent_sessions_isolated():
    """
    Two graph runs with different thread_ids fire query() concurrently.
    Each must see only its own result. attempt_count must be 1 in each.
    """
    graph = build_experiment_graph()
    thread_a = {"configurable": {"thread_id": str(uuid.uuid4())}}
    thread_b = {"configurable": {"thread_id": str(uuid.uuid4())}}
    marker_a = "ALPHA_" + str(uuid.uuid4())[:6]
    marker_b = "BETA_" + str(uuid.uuid4())[:6]

    async def run_thread(thread_cfg, marker):
        async for _ in graph.astream(
            {"prompt": f"Reply with exactly: {marker}", "attempt_count": 0},
            config=thread_cfg,
        ):
            pass
        return graph.get_state(thread_cfg)

    state_a, state_b = await asyncio.gather(
        run_thread(thread_a, marker_a),
        run_thread(thread_b, marker_b),
    )

    assert marker_a in state_a.values.get("sdk_result", ""), (
        f"Thread A result missing marker_a. Got: {state_a.values.get('sdk_result')!r}"
    )
    assert marker_b in state_b.values.get("sdk_result", ""), (
        f"Thread B result missing marker_b. Got: {state_b.values.get('sdk_result')!r}"
    )
    assert marker_b not in state_a.values.get("sdk_result", ""), (
        "Thread A result contains Thread B marker — sessions are NOT isolated."
    )
    assert marker_a not in state_b.values.get("sdk_result", ""), (
        "Thread B result contains Thread A marker — sessions are NOT isolated."
    )
    assert state_a.values["attempt_count"] == 1
    assert state_b.values["attempt_count"] == 1
    # VERDICT: if all assertions pass, concurrent sessions are isolated.
```

---

#### Scenario 5 — Hook instrumentation: Agent SDK `PostToolUse` hook fires and is observable from LangGraph

**What it tests:** ADR-022 requires span-level tracing on every LLM call. The Agent SDK
research found that `PostToolUse` hooks can carry `model_id` and token counts. This scenario
verifies that a hook registered on the `query()` call actually fires and that its data can
be captured into LangGraph state (for inclusion in a `task_spans` row later in P3d).

Modify `platform/experiments/agent_sdk_node.py` to add a hook-instrumented variant:

```python
# Add to platform/experiments/agent_sdk_node.py

from claude_agent_sdk import HookMatcher

async def sdk_node_with_hooks(state: ExperimentState) -> dict:
    """Variant of sdk_node that captures hook events into state."""
    attempt = state.get("attempt_count", 0) + 1
    spans = []

    async def capture_span(input_data, tool_use_id, context):
        spans.append({
            "tool": input_data.get("tool_name"),
            "tool_use_id": tool_use_id,
        })
        return {}

    result_parts = []
    async for message in query(
        prompt=state["prompt"],
        options=ClaudeAgentOptions(
            allowed_tools=[],
            hooks={
                "PostToolUse": [
                    HookMatcher(matcher=".*", hooks=[capture_span])
                ]
            },
        ),
    ):
        if hasattr(message, "result"):
            result_parts.append(message.result)

    return {
        "sdk_result": "\n".join(result_parts) if result_parts else "",
        "attempt_count": attempt,
        # In production, spans would go to task_spans table via post_gate_node.
        # Here we just capture them to verify the hook fires.
        "_spans_captured": spans,
    }
```

Then write the test:

```python
@requires_api_key
@pytest.mark.asyncio
async def test_scenario_5_hook_fires_and_is_observable():
    """
    sdk_node_with_hooks runs query() with a PostToolUse hook.
    Verify: the hook fires at least once (or zero times if no tools are called in a
    text-only response — that is also a valid and important finding to document).
    The key question is whether hook registration works inside an async LangGraph node.
    """
    from platform.experiments.agent_sdk_node import (
        ExperimentState,
        gate_node,
        _route,
        sdk_node_with_hooks,
    )

    builder = StateGraph(ExperimentState)
    builder.add_node("sdk_node", sdk_node_with_hooks)
    builder.add_node("gate_node", gate_node)
    builder.add_edge(START, "sdk_node")
    builder.add_edge("sdk_node", "gate_node")
    builder.add_conditional_edges("gate_node", _route)
    graph = builder.compile(checkpointer=MemorySaver())

    thread = {"configurable": {"thread_id": str(uuid.uuid4())}}
    async for _ in graph.astream(
        {"prompt": "Reply with exactly: HOOK_TEST_OK", "attempt_count": 0},
        config=thread,
    ):
        pass

    state = graph.get_state(thread)
    spans = state.values.get("_spans_captured", [])

    # Document the finding — hooks may or may not fire for a no-tool text response.
    # The test passes either way; the VERDICT section must explain what was observed.
    print(f"\nScenario 5 observation: {len(spans)} hook events captured: {spans}")
    assert state.values["attempt_count"] == 1  # sdk_node ran exactly once
    assert state.values["sdk_result"]  # some result was returned
    # VERDICT: Document whether hooks fire, and whether the data structure is
    # suitable for populating a task_spans row.
```

---

## Running the prototype

```bash
cd /mnt/i/talos
TALOS_NEXUS_STUB=1 ANTHROPIC_API_KEY=<your-key> \
  .venv/bin/python -m pytest platform/tests/test_agent_sdk_prototype.py -v -s
```

The `-s` flag is important — Scenario 5 prints its observation to stdout.

---

## What to produce: ADR-029

After all five scenarios complete (pass or fail), write
`/mnt/i/talos/docs/decisions/ADR-029-agent-sdk-integration.md`.

Follow the format of existing ADRs in `docs/decisions/`. The ADR must answer:

**1. Is `query()` inside an async LangGraph node safe?**
Based on Scenario 1: does it run at all? Based on Scenario 2: does it double-execute?
If Scenario 2 fails (attempt_count > 1 on resume), `query()` must NOT be placed in nodes
that precede `gate_node`. State which nodes are safe and which are not.

**2. Does `sdk_result` persist correctly across the interrupt boundary?**
Based on Scenario 3. If this fails, the deliverable cannot be read from state by the gate
operator, and the Agent SDK cannot be used for deliverable generation at all.

**3. Are concurrent Agent SDK sessions isolated?**
Based on Scenario 4. If this fails, multi-worker P3 cannot use the Agent SDK without a
session isolation mechanism beyond thread_id.

**4. Do hooks work inside an async LangGraph node, and what data do they expose?**
Based on Scenario 5. If hooks fire and expose tool_use_id + tool_name, ADR-022 span tracing
can use hooks inside Agent SDK calls. If they don't fire for text-only responses, note the
limitation and what trigger (tool call, SubagentCall) would be needed to capture spans.

**5. Subprocess cleanup: was any subprocess leaked?**
After the test run, check for orphaned `claude` processes:
```bash
ps aux | grep claude | grep -v grep
```
Document what you find. If processes linger, note whether they are background Claude Code
sessions or transient subprocesses.

**6. The layering recommendation (one paragraph):**
Given all findings, state: which nodes in the TALOS spine should use Agent SDK `query()`
(if any), which should not, and what the composition model is (LangGraph outer state
machine + Agent SDK execution inside specific nodes, or something different).

**7. What this closes:**
List the specific P3 sub-phases (P3a, P3b, P3c, P3d) that this ADR constrains, and how.

---

## Verification checklist

- [ ] `platform/experiments/agent_sdk_node.py` exists and imports cleanly
- [ ] `platform/tests/test_agent_sdk_prototype.py` exists with all 5 scenarios
- [ ] `claude-agent-sdk` added to `pyproject.toml` under `[experiment]` extras
- [ ] All 5 scenarios run (pass OR documented failure — either is a valid result)
- [ ] `ps aux | grep claude` output documented in ADR-029
- [ ] `docs/decisions/ADR-029-agent-sdk-integration.md` written with all 7 questions answered
- [ ] Existing 27 tests still pass: `TALOS_NEXUS_STUB=1 .venv/bin/python -m pytest platform/ -v`
  (the prototype tests are separate and do not affect the CI suite)
