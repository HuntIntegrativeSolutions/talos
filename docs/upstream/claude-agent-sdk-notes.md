# Claude Agent SDK — Technical Deep-Dive Notes

> Research date: 2026-06-14
>
> **Historical note:** these notes predate the `platform/` → `talos/` rename; current code lives
> in `talos/`. Retained as written for the historical record.
> Sources: `code.claude.com/docs/en/agent-sdk/*` (7 pages, all live), web searches, `docs/upstream/langgraph-notes.md`
> Purpose: Evaluate the Claude Agent SDK as a potential replacement or complement to LangGraph in TALOS. Answer: replace, complement, or ignore?

---

## Executive Summary

The Claude Agent SDK is a library for embedding Claude Code's agent loop in your own Python or TypeScript process. It wraps the familiar `claude -p` execution model behind a `query()` async generator: send a prompt, receive a stream of messages while Claude reads files, runs tools, calls MCP servers, and produces a result.

**What it is not:** a graph execution engine. It has no typed channels, no BSP/Pregel superstep model, no conditional routing, and no checkpoint that can pause mid-node and resume days later across process death. Its session persistence (JSONL on disk, mirrorable to Postgres/S3/Redis via `SessionStore`) stores the *conversation transcript* — it is not equivalent to LangGraph's `PostgresSaver`, which serializes typed `SpineState` with full channel version tracking and resumes a graph mid-execution.

**TALOS headline:** The Agent SDK **cannot replace LangGraph**. The five-outcome gate (`interrupt()` + `Command(resume=...)`) has no SDK equivalent; a direct port would require reimplementing Pregel from scratch. The natural use is **complement**: LangGraph owns the spine, gate, typed state, and Postgres checkpoint; the Agent SDK runs *inside* LangGraph nodes as the actual strategy-ladder execution engine (NEXUS calls, deliverable construction, multi-step tool use). This is a clean seam but carries a reconciliation cost — two state stores — that must be managed explicitly.

| Feature | Claude Agent SDK | LangGraph |
| :--- | :--- | :--- |
| Execution model | Linear `query()` loop (while-loop Claude Code didn't make you write) | BSP/Pregel StateGraph — typed channels, supersteps, conditional edges |
| State persistence | JSONL conversation transcript on local disk or via `SessionStore` adapter | `Checkpoint` TypedDict → `PostgresSaver` (typed, versioned, per-channel) |
| Human gate / interrupt | `AskUserQuestion` tool — in-loop, same process, same query (resolved before `ResultMessage`) | `interrupt()` raises `GraphInterrupt` — pauses graph mid-node, serializes to Postgres, waits indefinitely for `Command(resume=...)` |
| Durable cross-process pause | No — session resume is end-query + new-query; mid-node pause is not supported | Yes — `PostgresSaver` writes checkpoint before interrupt; resume re-executes node from line 1 |
| Conditional routing | Not native — model decides what to do next via tool calls | `add_conditional_edges()` with router functions and explicit `path_map` |
| Fan-out / fan-in | Subagents (background + foreground) or TypeScript `Workflow` tool | `Send` API + channel reducers (typed, parallel, atomic per superstep) |
| MCP integration | Native — `mcp_servers` dict in options; tools appear as `mcp__<server>__<tool>` | Via adapters (not native to LangGraph) |
| Hooks | In-process Python/TypeScript callbacks; block, modify, or log any tool call | Not built-in — use node side-effects or LangSmith |
| Skills | Filesystem `SKILL.md` files; model invokes autonomously | Not applicable |
| Subagent isolation | Fresh context per subagent; inherits tool config; separate JSONL transcript | Subgraph namespacing; inherited `PostgresSaver`; isolated checkpoint namespace |

---

## 1. What It Is

The Claude Agent SDK (`pip install claude-agent-sdk` / `npm install @anthropic-ai/claude-agent-sdk`) packages Claude Code as a library. Its primary surface is a single async generator:

```python
async for message in query(
    prompt="Find and fix the auth bug",
    options=ClaudeAgentOptions(allowed_tools=["Read", "Edit", "Bash"]),
):
    print(message)
```

Claude takes as many turns as needed — reading files, calling tools, reasoning — and emits a stream of typed messages (`SystemMessage`, `AssistantMessage`, `ToolUseBlock`, `ResultMessage`, etc.). When the task is done, a `ResultMessage` arrives and the generator exits.

Unlike the Anthropic Client SDK (which gives you raw API access and requires you to implement the tool loop), the Agent SDK runs the entire agent loop, MCP connections, permission enforcement, hooks, and session tracking automatically.

The SDK ships in Python and TypeScript. Python requires 3.10+. The TypeScript SDK bundles a native Claude Code binary. Both support the same core feature set; TypeScript has a few additional hooks (`SessionStart`, `SessionEnd`, `TaskCompleted`, `ConfigChange`, `WorktreeCreate/Remove`, `MessageDisplay`).

---

## 2. Runtime Model

**Execution model:** a while-loop, not a graph. Each `query()` call runs a single conversation from start to `ResultMessage`. Claude takes as many LLM turns (and tool calls per turn) as needed. There is no step/superstep concept, no typed channels, no version tracking. State is the conversation transcript — an ordered log of messages.

**Process model:** the SDK spawns a Claude Code subprocess and communicates via IPC. The subprocess writes the conversation transcript to local disk as JSONL (`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`). The parent process streams messages from the subprocess and passes them to the `query()` generator.

**State persistence:**
- Primary: filesystem JSONL at `~/.claude/projects/…`
- Optional mirror: `SessionStore` adapter (`append()` / `load()`) — the SDK writes to disk first, then mirrors to the store
- Reference adapters available (TypeScript only, copy from `examples/session-stores/`): `PostgresSessionStore` (one row per entry in `jsonb` table, ordered by `BIGSERIAL`), `S3SessionStore`, `RedisSessionStore`
- The store holds the *conversation transcript*, not typed application state

**Session lifecycle:**
- Each `query()` call starts a new session (new UUID, new JSONL file) unless `resume=session_id` or `continue=True` is passed
- Session ID is available on `ResultMessage.session_id` and on the `SystemMessage(subtype="init")` at query start
- Resume: `query(options=ClaudeAgentOptions(resume=session_id))` — the agent continues the conversation with full context
- Fork: `fork_session=True` — creates a new session from a copy of the original's history; original unchanged
- `persistSession: false` (TypeScript only) — session exists only in memory; cannot be combined with `sessionStore`

**Multi-turn within a `query()` call:** handled automatically. The agent takes as many turns as needed without ending the call. `AskUserQuestion` and permission prompts are resolved in-loop (they do not end the `query()` call).

---

## 3. Human-in-the-Loop

**The deciding axis for "replace":** does the SDK offer a durable pause that survives process death, preserves typed SpineState in Postgres, and resumes mid-graph after a human review that may take hours or days?

**Answer: No.**

The SDK's human-in-the-loop mechanism is `AskUserQuestion`, described as "ask the user clarifying questions with multiple choice options." From the sessions documentation:

> Within a single `query()` call, the agent already takes as many turns as it needs, and permission prompts and `AskUserQuestion` are handled in-loop (they don't end the call).

This means `AskUserQuestion` resolves within the active `query()` execution. If the process dies while the query is running, the question is gone. This is the opposite of LangGraph's `interrupt()`, which writes a checkpoint to Postgres *before* surfacing the interrupt value, so a dead process can be recovered by a new process resuming the graph from that checkpoint.

**LangGraph `interrupt()` vs SDK `AskUserQuestion`:**

| Aspect | LangGraph `interrupt()` | SDK `AskUserQuestion` |
| :--- | :--- | :--- |
| Pause scope | Mid-node, mid-graph; graph execution suspended | Within the same `query()` call; query does not end |
| State serialization | Full `SpineState` written to `PostgresSaver` before pause | Conversation transcript written to disk (JSONL) |
| Survives process death | Yes — `PostgresSaver` persists the checkpoint; new process resumes with `Command(resume=...)` | No — the active `query()` call dies with the process |
| Resume mechanism | `graph.stream(Command(resume={...}), config={"thread_id": task_id})` | Start a new `query(resume=session_id)` with the decision in the prompt |
| State on resume | Full typed `SpineState` channels exactly as at pause | Conversation history; LLM re-reads and infers what to do |
| Gate routing | `_route_after_gate(state)` deterministic router — five typed outcomes | Not available; model decides what to do with the human input |

**Simulating a gate with the SDK:** you can build an approximation by ending the `query()` call after generating the deliverable, storing the session ID and deliverable state externally, waiting for human input (hours/days), then starting a new `query(resume=session_id)` with the decision injected as a new user message. This works for simple cases but:
- The five-outcome routing (`approve/reject/waive/edit/escalate`) must be re-implemented in the LLM prompt, not in typed conditional edges
- Typed `critic_results`, `gate_outcome`, `approved_by`, `gate_justification` are all conversation context, not typed state
- The `post_gate_node` idempotency pattern (UNIQUE constraint + same transaction) cannot be replicated by the SDK
- Re-execution on resume cannot distinguish "fresh execution" from "resuming after gate" without explicit prompt engineering

For TALOS, this is a non-starter: the gate is the doctrine's load-bearing boundary. Rewriting it around SDK session continuations would remove the structural guarantee that critic results and approval cannot be circumvented.

---

## 4. Subagents and Worker Isolation

**SDK subagent model:**

Subagents are separate agent instances spawned via the `Agent` tool. They receive:
- Their own system prompt (`AgentDefinition.prompt`)
- The Agent tool's prompt string (the only parent→child channel)
- A fresh conversation context (no parent history)
- An inherited tool subset (or the `tools` field in `AgentDefinition`)

Subagents do NOT receive:
- The parent's conversation history or tool results
- The parent's system prompt
- Preloaded skill content (unless listed in `AgentDefinition.skills`)

Each subagent runs its own `query()` loop, writes to its own transcript under `subagents/agent-<id>`, and returns its final message as the Agent tool result.

**Comparison to LangGraph `Send` API:**

| Aspect | SDK Subagents | LangGraph `Send` |
| :--- | :--- | :--- |
| Dispatch mechanism | Agent tool in Claude's context | `Send("node_name", {"state": ...})` — programmatic, typed |
| State to children | Agent tool's prompt string (text only) | Typed dict matching the target node's input schema |
| State from children | Final message text | Dict updates → channel reducers (e.g., `Annotated[list, operator.add]`) |
| Parallelism model | Multiple subagents run concurrently | All `Send` tasks execute in the same superstep; results aggregate atomically |
| Checkpoint per task | Separate JSONL transcript per subagent | Each `Send` node execution is checkpointed in `PostgresSaver` under namespace |
| Resume after crash | Resume main session + re-specify agent ID | LangGraph resumes the superstep; all in-flight `Send` tasks re-execute from last checkpoint |
| Depth limit | Foreground: unlimited; background: 5 levels | No built-in limit |

**ADR-010 session key equivalence:**

ADR-010 mints `task:{board_id}:{task_id}:{attempt}` to scope worker workspace, tool policy, and memory. The SDK subagent model provides similar scoping (each subagent has its own context and tool set), but:
- The SDK has no equivalent of `board_id` + Postgres RLS as the hard authorization boundary
- The SDK's `AgentDefinition.tools` restriction is "restrict-only" inheritance (matches ADR-010's rule)
- SDK subagent transcripts persist independently (matches the spirit of worker session isolation)
- Crash recovery: resuming a subagent requires both the main session ID and the agent ID — more state to track than a LangGraph checkpoint, which only needs `thread_id`

**P3 implication:** the Agent SDK subagent model is a credible alternative to LangGraph `Send` for fan-out tasks at P3, IF the parallel workers do not need typed state merging or atomic reducer semantics. For NEXUS tag-trace fan-out (P3 scope), where results accumulate in a list, the channel reducer `Annotated[list, operator.add]` provides guarantees the SDK cannot replicate without application-level locking.

---

## 5. Sessions and Checkpointing

**Session JSONL contents:** an ordered log of `SessionStoreEntry` objects — typed as `{ type: string; ... }`. These include every message exchanged during the `query()` call: system init, user prompts, assistant responses, tool use blocks, tool results, and metadata. The entries are opaque to the application; treat them as a conversation replay log.

**Postgres session storage (TypeScript only):**

A `PostgresSessionStore` reference adapter is available (not on npm — copy from `examples/session-stores/postgres/` in the TypeScript SDK repo). It stores one row per entry in a `jsonb` table ordered by `BIGSERIAL`. It implements `append()`, `load()`, `listSessions()`, `delete()`, and `listSubkeys()`.

Important constraints:
- **Dual-write architecture:** the subprocess writes to local disk first; the SDK mirrors to the store. The store is a mirror, not the primary.
- **Mirror writes are best-effort:** if `append()` rejects, the query continues and a `mirror_error` message is emitted. Local transcript is already durable.
- **Python SDK:** no published Postgres adapter (TypeScript only). Python always writes to local disk; SessionStore is also TypeScript-only in the reference implementations. (The Python `SessionStore` protocol is defined but reference adapters are only in the TS repo.)
- **Not `PostgresSaver`:** the Postgres session store holds conversation transcript rows, not typed SpineState channels. A `PostgresSessionStore` row cannot tell you `state["gate_outcome"]` or drive conditional routing.

**Cross-host recovery:**

The sessions documentation is explicit about the limitation:

> Session files are local to the machine that created them. To resume a session on a different host (CI workers, ephemeral containers, serverless), you have two options:
> - Move the session file. Persist `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl` from the first run and restore it to the same path on the new host before calling `resume`. The `cwd` must match.
> - Don't rely on session resume. Capture the results you need as application state and pass them into a fresh session's prompt.

With `SessionStore`, a third option exists (resume from the Postgres-backed store on any host), but the store is still a conversation transcript mirror, not a typed checkpoint. Dead-worker recovery is the caller's responsibility — there is no equivalent of LangGraph's automatic checkpoint-on-superstep that lets a new worker claim a task and continue from exactly where the old one died.

---

## 6. Hooks

Hooks are in-process Python/TypeScript callbacks that run at lifecycle events. They can block, modify, approve, or log any tool call.

**Available hooks:**

| Hook | Both SDKs | What triggers it | Key capability |
| :--- | :--- | :--- | :--- |
| `PreToolUse` | Yes | Before a tool executes | Block (`deny`), modify (`updatedInput`), approve (`allow`) |
| `PostToolUse` | Yes | After a tool returns | Inject context (`additionalContext`), replace output (`updatedToolOutput`) |
| `PostToolUseFailure` | Yes | After a tool fails | Log or handle errors |
| `PostToolBatch` | TS only | Full batch of tool calls resolves | Inject context once per batch |
| `UserPromptSubmit` | Yes | User prompt submission | Inject additional context into prompts |
| `Stop` | Yes | Agent execution stops | Save state, send notifications |
| `SubagentStart` | Yes | Subagent spawned | Track parallel task delegation |
| `SubagentStop` | Yes | Subagent completes | Aggregate subagent results |
| `PreCompact` | Yes | Conversation compaction | Archive full transcript before summarizing |
| `PermissionRequest` | Yes | Permission dialog would show | Custom permission handling |
| `Notification` | Yes | Agent status messages | Forward to Slack, PagerDuty, tracing systems |
| `SessionStart` / `SessionEnd` | TS only | Session init / termination | Initialize logging; clean up resources |

**Hook execution:** hooks run in parallel when multiple hooks match. For permission decisions, `deny` beats `defer` beats `ask` beats `allow` — if any hook returns `deny`, the tool is blocked.

**Async hooks:** returning `{async: true, asyncTimeout: 30000}` lets the agent continue without waiting for the hook — for fire-and-forget side effects (logging, metrics).

**TALOS relevance — ADR-022 span tracing:** the `Notification` hook fires on every status message. `PreToolUse` and `PostToolUse` fire on every tool call. Together these provide the lifecycle granularity needed for span tracing at P3: open a span in `PreToolUse`, close it in `PostToolUse`, record exceptions in `PostToolUseFailure`. This is a cleaner alternative to custom LangGraph stream events if the Agent SDK runs inside LangGraph nodes.

**TALOS relevance — critic registry alternative:** `PreToolUse` hooks run before any tool executes and can block based on tool input. This is NOT a substitute for TALOS critics, which evaluate the *deliverable* (a structured output from the strategy ladder), not individual tool calls. Critics gate a completed work product; hooks gate individual tool executions. The analogy breaks down at the boundary.

---

## 7. Skills

Skills are `SKILL.md` files in `.claude/skills/` directories. Claude discovers and invokes them automatically based on the `description` field when the task seems to match.

**SDK integration:**
- Must be filesystem artifacts — no programmatic API to define skills in code
- Loaded via `settingSources: ["user", "project"]`
- Controlled via `skills: "all"` | `["name1", "name2"]` | `[]` on `ClaudeAgentOptions`
- The `skills` option is a context filter, not a sandbox — the files remain on disk

**TALOS relevance:** TALOS already uses skills (the `/fds`, `/soo`, `/bom`, etc. skill library). Inside an Agent SDK node, TALOS skills would be automatically available to the agent loop. This is a benefit of the complement architecture: the existing skill library loads into the within-node agent without any extra wiring.

The `allowed-tools` frontmatter in `SKILL.md` does NOT apply when using the SDK — tool access is controlled by `allowedTools` in `ClaudeAgentOptions`. A node that loads skills must explicitly allow the tools those skills need.

---

## 8. MCP Integration

**Configuration:** `mcp_servers` dict in `ClaudeAgentOptions`. Three transport types: `stdio` (local process, `command` + `args` + `env`), `http`/`sse` (remote URL + optional `headers`), and SDK in-process (custom tools — separate guide).

**Tool naming:** `mcp__<server-name>__<tool-name>`. Wildcard permission: `allowed_tools=["mcp__nexus__*"]`.

**Tool search:** for large tool sets (like NEXUS's 85+ tools), the SDK withholds tool definitions from context and loads only needed ones per turn. Enabled by default.

**Authentication:** env vars in the `env` field (stdio) or `headers` dict (HTTP/SSE). OAuth2: pass access token via headers after your own OAuth flow.

**Subagent MCP inheritance:** subagents inherit parent MCP servers unless overridden via `AgentDefinition.mcpServers`. This means a subagent can reach NEXUS without re-specifying the server config.

**TALOS + NEXUS via MCP:** the SDK's MCP integration is native and straightforward. A `read_node` implemented using the Agent SDK would configure NEXUS as an MCP server, allow the relevant read-profile tools, and let Claude autonomously call `mcp__nexus__tag_context` or `mcp__nexus__find_interlocks` — exactly the multi-step read pattern NEXUS requires. The MCP boundary (ADR-001) is preserved: the SDK makes calls over MCP, not directly.

**Comparison to LangGraph + MCP:** LangGraph requires MCP adapters; MCP integration is not native. The Agent SDK has deeper, more integrated MCP support. This is a point in favor of using the SDK for the NEXUS-calling layer inside nodes.

---

## 9. Key TALOS Findings

### Finding 1 — `gate_node` cannot be replaced

`gate_node` in `platform/graph/spine.py` calls `interrupt({"task_id": ..., "deliverable": ..., "critic_results": ...})`. This:
1. Raises `GraphInterrupt` (halts node execution)
2. Writes full `SpineState` to `PostgresSaver` with all channel versions
3. Surfaces the interrupt value in the stream under `__interrupt__`
4. Waits indefinitely — the graph thread is suspended, not busy-waiting
5. On `graph.stream(Command(resume={...}), config)`, the node re-executes from line 1 and `interrupt()` returns the resume value

The Agent SDK has no equivalent. `AskUserQuestion` resolves within the active `query()` call. If you want to hold for human input across process death, you end the `query()` call and resume later — but then you've lost the mid-node execution position, and the five-outcome routing must be re-implemented at the LLM layer. The typed `gate_outcome` driving `_route_after_gate` has no equivalent.

### Finding 2 — Session JSONL ≠ SpineState

The Postgres `SessionStore` adapter (TypeScript only) stores `SessionStoreEntry` objects — opaque JSONL entries. This is the conversation transcript, not typed application state. You cannot extract `state["critic_results"]` from a session store entry. The `PostgresSaver` in LangGraph stores `channel_values` (the serialized `SpineState` dict), `channel_versions`, and `versions_seen` — enough to replay exactly which node last ran and what channels contain.

### Finding 3 — The complement seam is plausible but unverified at the composition level

An `async` LangGraph node *should* be able to call `async for message in query(...)` directly since both use asyncio. However, the SDK spawns a subprocess and communicates over IPC (Section 2), and subprocess lifecycle + nested asyncio event handling + `PostgresSaver`'s connection management inside a checkpointed node is precisely where this kind of integration breaks in practice. This composition is a hypothesis, not a verified fact — prototype it before committing at P3. The logical seam, if it holds:

```
LangGraph SpineState (PostgresSaver)
    │
    ├── read_node: calls query() → NEXUS via MCP → returns nexus_result
    │       stored in SpineState["nexus_result"]
    │       SDK session ID stored in SpineState["sdk_session_ids"]["read"]
    │
    ├── deliverable_node: calls query() → drafts deliverable using NEXUS result
    │       stored in SpineState["deliverable"]
    │
    ├── gate_node: interrupt() — LangGraph-only, not SDK
    │
    └── post_gate_node: Postgres side-effects — LangGraph-only
```

The cost is two state stores:
- `PostgresSaver` holds typed SpineState (drives routing, idempotency, audit)
- SDK session JSONL holds conversation history (drives context continuity within a node)

On crash recovery: LangGraph resumes from its checkpoint; the node re-executes; if the SDK session ID is in `SpineState`, the node can `resume=sdk_session_id` to continue mid-NEXUS-analysis rather than restarting from scratch. This works but requires storing SDK session IDs in `SpineState` — a managed reconciliation cost, not a fundamental incompatibility.

### Finding 4 — Subagent model is not a `Send` API replacement at P3

At P3 (full dispatcher), LangGraph's `Send` API provides typed fan-out where each worker's output is folded into a channel via a reducer (`Annotated[list, operator.add]`). The reducer is atomic per superstep. SDK subagents communicate via text (Agent tool prompt → final message result), with no typed accumulation. For the NEXUS trace fan-out pattern (many tags in parallel, results aggregated), `Send` provides stronger guarantees. Subagents are suitable for delegation of whole sub-tasks, not for typed parallel aggregation.

### Finding 5 — Hooks are the strongest point of the complement

If the Agent SDK runs inside LangGraph nodes, its hooks provide instrumentation that LangGraph lacks natively:
- `PreToolUse` / `PostToolUse`: span tracing for every NEXUS tool call (ADR-022)
- `SubagentStart` / `SubagentStop`: delegate tracking when a node spawns sub-tasks
- `Notification`: forward agent status to the board's event stream
- `PreCompact`: archive full transcript before compaction (compliance)

None of this requires changing `spine.py`. The hooks attach at the SDK layer inside the node function.

---

## 10. What TALOS Should NOT Take

- **The SDK's session resume as a gate pattern.** Ending a `query()` call and resuming it later is not equivalent to `interrupt()` + `Command(resume=...)`. Do not attempt to implement the five-outcome gate at the SDK layer.
- **`AskUserQuestion` as a gate UI.** It is an in-loop clarifying question, not a durable human-approval point. The gate UI belongs in the FastAPI board endpoint that sends `Command(resume=...)` to the LangGraph graph.
- **`PostgresSessionStore` as a checkpoint.** It is a conversation transcript mirror. It does not replace `PostgresSaver`. TALOS already has the correct checkpoint store for gate-durability.
- **Subagents as a replacement for `Send` at P3.** The typed accumulation and per-worker checkpoint semantics of `Send` are not replicated by SDK subagents. Keep `Send` for the fan-out dispatcher.
- **Skills as a critic registry.** Skills are invocation hints for Claude. Critics are deterministic Python functions with typed verdicts (`pass/fail/warn`) and structural `safety_class`/`waivable` bindings enforced by the meta-critic. These are different mechanisms for different purposes.

---

## 11. Answer: Replace, Complement, or Ignore?

**Complement — but only inside LangGraph nodes, starting at P3, not before.**

The Agent SDK is the wrong tool for the spine, gate, and state management layers — those are LangGraph's domain, and the decision to use LangGraph was correct. However, the strategy-ladder steps (research, plan-relay, execute) that will grow substantially at P3 are exactly where the SDK shines: autonomous multi-step tool use, native MCP integration, built-in hook instrumentation, and skill invocation — all without implementing a tool loop. Embedding `query()` inside async LangGraph nodes is a clean composition: LangGraph drives routing and checkpointing, the SDK drives the within-node agent execution. The reconciliation cost (storing SDK session IDs in `SpineState` for crash recovery) is manageable and explicit. For P1 and P2, which are complete and working, the SDK adds nothing; do not retrofit it. Evaluate adoption at P3 when the strategy-ladder nodes expand beyond the current NEXUS stub.

---

## 12. Open Questions

1. **Python `SessionStore` reference implementation.** The Postgres/S3/Redis adapters are in the TypeScript SDK repo only. If TALOS adopts the complement pattern in Python, a Python Postgres adapter must be implemented against the `SessionStore` protocol, or the pattern uses disk-based JSONL recovery (store `sdk_session_id` in SpineState; on node re-execution, pass `resume=` pointing at the local JSONL).

2. **SDK session ID in SpineState — schema change.** Storing `sdk_session_ids: dict` in `SpineState` (a TypedDict) is a P3 schema addition. It must be handled carefully: optional key, defaults to `{}`, so P2 checkpoints deserialize cleanly. Test before P3.

3. **NEXUS MCP server config inside nodes vs at the worker level.** The MCP server config (`nexus: {command: ..., env: ...}`) needs to be available inside the LangGraph node that calls `query()`. This is either injected via LangGraph `Context` (immutable runtime values) or from environment variables — both are clean, but the pattern should be established before P3 implements multiple strategy-ladder nodes.

4. **Hook vs LangGraph stream events for span tracing.** ADR-022 scope is P3. The choice is: hook callbacks in the SDK layer (attached inside the node function) vs LangGraph's `stream_mode="custom"` with `StreamWriter`. Hooks give finer granularity (per-tool-call); LangGraph stream events give coarser but unified visibility across all nodes. If the SDK is adopted inside nodes, prefer hooks for within-node spans and LangGraph stream events for cross-node events (node entry/exit, gate outcome).

5. **`Workflow` tool (TypeScript SDK v0.3.149+).** The subagents documentation mentions a `Workflow` tool for orchestrating many agents outside the conversation context. This is TypeScript-only. Not relevant to TALOS's Python-first implementation, but worth re-evaluating at P7 (cockpit) if TypeScript web components need multi-agent orchestration.

---

## 13. Build-Phase Impact

| Phase | Impact | Action |
| :--- | :--- | :--- |
| **P1 (complete)** | None. Gate built correctly on LangGraph `interrupt()`; no SDK involvement needed. | No change. |
| **P2 (complete)** | None. Critic registry is deterministic Python; SDK hooks are not a substitute. | No change. |
| **P3 (full dispatcher)** | **Evaluate SDK for strategy-ladder nodes.** `read_node` and `deliverable_node` will expand from stubs to real multi-step NEXUS calls. The SDK's `query()` + MCP is a natural fit. Decide: embed SDK inside nodes or call NEXUS directly via MCP adapter in LangGraph. If adopting: add `sdk_session_ids` to `SpineState`, implement Python `SessionStore` or rely on local JSONL resume. Do NOT use SDK for `Send`-based fan-out dispatcher. | Decision point at P3 pre-interview. ADR-024 candidate. |
| **P4 (memory + PageRank)** | Low. Memory federation is Python/Postgres/Neo4j; no SDK involvement. SDK subagents inside memory-query nodes are possible but unlikely to add value over direct API calls. | No action unless P3 adoption proves the pattern. |
| **P5 (crystallize + promotion)** | Low. The de-identification critic and promotion gate are deterministic Python + the existing LangGraph gate. | No action. |
| **P6 (sim-execute capability)** | Low-medium. If the sim-execute step uses the Agent SDK to invoke `plc_test_bridge`, the MCP-native model works well. The `target-ip-is-emulator` critic remains a LangGraph-layer concern, not an SDK hook. | Evaluate at P6 scope. |
| **P7 (cockpit)** | None. Cockpit is a board-API consumer (ADR-002); not involved in SDK execution. | No action. |
| **P8 (gateway / proactivity)** | Low. Gateway proactive loops might use `query()` for short autonomous turns. SDK session state for gateway runs would be separate from TALOS's main board sessions. Policy enforcement still lives in the LangGraph/critics layer. | Evaluate at P8 scope. |

---

*Research by Claude Code (Sonnet 4.6), 2026-06-14. Sources: Claude Agent SDK documentation (code.claude.com/docs/en/agent-sdk/*), TALOS codebase (platform/graph/spine.py, docs/decisions/ADR-010, ADR-011), third-party comparisons (morphllm.com, turion.ai, qubittool.com).*
