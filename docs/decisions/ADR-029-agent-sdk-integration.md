# ADR-029: Claude Agent SDK integration with LangGraph — placement, safety, and tracing

**Status:** Accepted
**Date:** 2026-06-14
**Deciders:** Hunt Integrative Solutions LLC

## Context

`docs/upstream/claude-agent-sdk-notes.md` (Finding 3) identifies embedding `query()` inside async
LangGraph nodes as "plausible but unverified." P3 strategy-ladder nodes (`read_node`,
`deliverable_node`) will expand from stubs to real multi-step NEXUS calls, making the
Agent SDK the natural execution engine for within-node work. Before P3 begins, the composition
must be verified empirically.

The critical risk: LangGraph's BSP/Pregel model re-executes a node from its first line when
resuming after `interrupt()`. If `query()` lives in a node that gets re-run, a second Claude
subprocess spawns. This is the double-execution bug.

The prototype (`talos/experiments/agent_sdk_node.py`, `talos/tests/test_agent_sdk_prototype.py`)
runs five scenarios against the real SDK and real API to produce empirical answers.

**Prerequisites verified:**
- `claude-agent-sdk` 0.2.101 installed
- `claude` CLI v2.1.177 at `/home/hiscontrols24/.local/bin/claude`
- Authentication via OAuth (stored Claude Code credentials); API key not required.
  Skip guard updated to: skip only if neither `ANTHROPIC_API_KEY` nor `claude` CLI is present.

---

## Seven questions

### Q1 — Is `query()` safe to call inside a checkpointed LangGraph node, and in which placement?

> *Based on Scenarios 1 and 2.*

**[Fill after test run]**

The experiment tests one specific placement: `sdk_node` (a node that runs *before* `gate_node`,
completing in a prior LangGraph superstep). On resume, only `gate_node` re-executes from
line 1 (it contains `interrupt()`). `sdk_node`, having completed and been checkpointed in the
prior superstep, should NOT re-run.

- If `attempt_count == 1` after Scenario 2 resume → **SAFE** for pre-gate placement.
  `query()` may be used in `read_node` and `deliverable_node`.
- If `attempt_count > 1` after resume → **UNSAFE**. `query()` must be moved to `post_gate_node`
  or removed from the spine entirely.

Note: this prototype does NOT test placing `query()` inside `gate_node` (before `interrupt()`).
That placement is unsafe by LangGraph semantics regardless of this experiment — code before
`interrupt()` in the same node runs twice by design (see spine.py gate_node docstring).

**Actual result:** SAFE. `attempt_count` = 1 before resume and 1 after resume. `sdk_node`
did not re-execute. `query()` is confirmed safe in pre-gate node placement (`read_node`,
`deliverable_node`). All 27 s1+s2 assertions passed. Run time: 28.45s total for all five scenarios.

---

### Q2 — Does `sdk_result` persist correctly across the interrupt boundary?

> *Based on Scenario 3.*

If `sdk_result` is absent or empty after resume, the LangGraph checkpoint is not preserving
the Agent SDK's output across the interrupt. This would mean the Agent SDK cannot be used for
deliverable generation: the human reviewer would see the deliverable at interrupt time, but
post-gate processing would not have access to it.

**Actual result:** Persists. `sdk_result` was byte-for-byte identical before and after resume
(`"MARKER_88c1fc37\n\nHello! I'm Claude..."` in both snapshots). The LangGraph checkpoint
preserved the full value across the interrupt boundary without truncation or mutation.

---

### Q3 — Are concurrent Agent SDK sessions isolated?

> *Based on Scenario 4.*

Two threads run concurrently with different marker strings. If thread A's `sdk_result` contains
thread B's marker, the Claude subprocesses are sharing state. For P3 multi-worker, this would
require additional per-session isolation (e.g. separate working directories or `cwd` per worker).

**Actual result:** Fully isolated. Thread A result contained only `ALPHA_1dd98c80`, Thread B
only `BETA_05118805`. No cross-contamination in either direction. Both threads reported
`attempt_count = 1`. LangGraph `thread_id` provides complete session isolation at no extra cost.

---

### Q4 — Do hooks work inside an async LangGraph node?

> *Based on Scenario 5.*

The `PostToolUse` hook is registered via `ClaudeAgentOptions(hooks={"PostToolUse": [HookMatcher(...)]})`.
With `allowed_tools=[]`, Claude produces a text-only response — no tool calls fire, so
`_spans_captured` is expected to be empty. That is a valid finding.

**Note on `allowed_tools=[]`:** SDK introspection revealed that an empty list passes no
`--allowedTools` flag to the `claude` CLI — Claude retains its default toolset. Tools are not
disabled. The prompts in Scenario 5 ("Reply with exactly: HOOK_TEST") are trivial enough that
Claude is unlikely to invoke a tool, but tool invocation is not architecturally blocked.

**Expected finding:** `_spans_captured` is likely empty because the trivial prompt did not
trigger a tool call — not because `allowed_tools=[]` disabled tools. `PostToolUse` hooks
instrument tool executions, not raw LLM inference. If no tool fires, no hook fires. For
ADR-022's `llm.call` span, the correct instrumentation point is a timing wrapper around the
`query()` call itself (entry/exit timing) — not a PostToolUse hook. PreToolUse/PostToolUse
hooks remain valuable for NEXUS tool call spans when tools are actually allowed
(e.g. `allowed_tools=["mcp__nexus__*"]`).

**Actual result:** `_spans_captured` count = 0. Hook registered without error. `sdk_result` = `'HOOK_TEST'`
confirming the response came through the hooked path. Zero spans because the prompt produced a
text-only response with no tool invocations — confirming the Q4 pre-analysis exactly. `PostToolUse`
is the correct hook for NEXUS tool spans (when tools fire); `llm.call` spans must be implemented
as a timing wrapper around the `async for message in query(...)` loop.

---

### Q5 — Subprocess cleanup: any orphaned processes?

> *After test run: `ps aux | grep claude | grep -v grep`*

The SDK spawns a `claude` subprocess per `query()` call. Each `sdk_node` invocation opens
one subprocess. The subprocess should exit cleanly when the `async for` loop over `query()`
completes. If subprocesses are orphaned, parallel P3 workers would accumulate them.

**ps aux output after test run:**
```
hiscont+  6719  6.1  3.2 73768072 534212 pts/0 Sl+  12:09  33:57 claude
hiscont+  24333 4.5  3.7 73924428 604220 pts/1 Sl+  12:43  23:24 claude
```

Both processes started before the test run (12:09 and 12:43; tests ran after 13:00). These
are the existing interactive Claude Code sessions, not Agent SDK subprocesses. No orphaned
processes were produced. The `async for` loop over `query()` drains the subprocess cleanly.

---

### Q6 — The layering recommendation

> *One paragraph.*

**Decision: complement pattern, LangGraph outer / Agent SDK inner.**

LangGraph owns the spine: `SpineState`, `PostgresSaver` checkpointing, conditional edge routing,
and the gate boundary (ADR-011). The Agent SDK runs inside `read_node` and `deliverable_node`
via `async for message in query(...)` — these are the nodes that will eventually call NEXUS MCP
tools as the strategy-ladder expands. `gate_node` contains only `interrupt()` and is Agent SDK-free
by design. `post_gate_node` writes to Postgres and is Agent SDK-free.

Crash recovery: add `sdk_session_ids: dict` (optional, default `{}`) to `SpineState`. Each
node that calls `query()` stores its session ID. On re-entry after a failed attempt, pass
`resume=sdk_session_ids.get("read_node")` to continue mid-analysis rather than restart. This
is safe because Q1 confirmed that re-entry only happens via a new worker claiming a new attempt,
not via LangGraph resume (which only re-runs `gate_node`).

Tracing (ADR-022): `PreToolUse`/`PostToolUse` hooks fire for NEXUS MCP tool invocations and
provide per-tool-call spans. The `llm.call` span (model_id, latency_ms, token counts) is
implemented as a timing wrapper bracketing the `async for message in query(...)` call — not
via a hook, since hooks don't fire on raw inference. `SystemMessage` carries session init data;
`ResultMessage` carries the final result and token usage if exposed by the SDK version.

---

### Q7 — What this closes

| P3 sub-phase | How ADR-029 constrains it |
|---|---|
| **P3a** — full dispatcher | No Agent SDK involvement. Dispatcher, claim loop, heartbeat, and `Send`-based fan-out are LangGraph/Postgres. ADR-029 Q3 confirms concurrent subprocess isolation is adequate for workers that each get their own `query()` session. |
| **P3b** — spine node expansion | If Q1=SAFE: `read_node` and `deliverable_node` may call `query()` with NEXUS MCP config. Each node stores its `sdk_session_id` in `SpineState` for crash recovery. Hooks attach for tool-call spans. |
| **P3c** — gate & post-gate finalization | Unaffected. `gate_node` is `interrupt()`-only; `post_gate_node` is Postgres-only. Neither uses the Agent SDK. |
| **P3d** — observability (ADR-022 spans) | If Q4 confirms hooks work: `PreToolUse`/`PostToolUse` hooks provide per-tool-call spans inside nodes. `llm.call` span is timed at the `query()` call boundary (not via hook). Cross-node spans use LangGraph's stream events. |

---

## Options considered

- **A — Do not use Agent SDK inside LangGraph nodes.** Implement tool loops directly in
  LangGraph node functions. Rejected: duplicates the tool loop, MCP integration, and session
  management that the SDK provides natively. Higher maintenance burden.
- **B — Agent SDK inside pre-gate nodes only (complement pattern).** Chosen if Q1=SAFE.
  LangGraph drives routing and checkpointing; Agent SDK drives within-node execution.
- **C — Agent SDK inside gate_node before interrupt().** Explicitly ruled out by LangGraph
  semantics (double-execution on resume). Not tested; unsafe by design.

## Consequences

- **Easier:** multi-step NEXUS calls in `read_node` / `deliverable_node` without implementing a
  tool loop; native MCP integration; hook-based instrumentation at the tool-call level.
- **Harder:** two state stores (PostgresSaver + SDK session JSONL); `sdk_session_id` must be
  added to `SpineState` as an optional key for crash recovery; Python `SessionStore` adapter
  needed for multi-host recovery (TypeScript-only reference adapters exist).
- **Revisit:** `sdk_session_ids` schema addition to `SpineState` at P3 implementation; Python
  `SessionStore` adapter (Open Question 1 from `claude-agent-sdk-notes.md`).

## Action items

1. [x] If Q1=SAFE: add `sdk_session_ids: dict` to `SpineState` (optional, default `{}`). — **Done in P3b** (`talos/graph/spine.py`)
2. [x] If Q1=SAFE: implement `read_node` and `deliverable_node` using `query()` at P3b. — **Done in P3b** (stub guard preserved for CI via `TALOS_NEXUS_STUB=1`)
3. [ ] Attach `PreToolUse`/`PostToolUse` hooks for NEXUS tool spans (ADR-022 P3b instrumentation). — deferred to P4 (NEXUS tool calls not yet live)
4. [x] Wire `llm.call` span as timing wrapper around `query()` call entry/exit. — **Done in P3d** (`talos/llm.py` + `talos/spans.py`)
5. [ ] If Q5 shows orphaned processes: add explicit subprocess cleanup or verify `async for` drains. — not yet triggered; revisit at P4
