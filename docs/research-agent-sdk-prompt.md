# Research Prompt — Claude Agent SDK & Embeddable Agent Runtime

> **Historical note:** this prompt predates the `platform/` → `talos/` rename; current code lives
> in `talos/`. Retained as written for the historical record.

You are doing a deep technical research dive for TALOS, a pre-alpha multi-agent industrial
project-execution platform at `/mnt/i/talos/`. TALOS is NOT a coding assistant. It
orchestrates agents for operations work behind a hard human-review gate.

**The core question this research must answer:**

> TALOS uses LangGraph as its execution engine (BSP/Pregel, typed channels, PostgresSaver
> checkpointing, `interrupt()` + `Command` for the five-outcome gate). The Claude Agent SDK
> (Anthropic's embeddable runtime, previously called Claude Code SDK) is now available.
> Should TALOS replace LangGraph with the Agent SDK? Use both? Use each for different
> layers? Or ignore the Agent SDK entirely?

This research session produces one output file. Do not modify any other files.

---

## Context — what TALOS has already built and decided

Read these files silently before fetching any external sources:

- `/mnt/i/talos/CLAUDE.md` — current implementation state (P1+P2 complete, LangGraph spine)
- `/mnt/i/talos/platform/graph/spine.py` — the actual LangGraph spine (read the full file)
- `/mnt/i/talos/docs/decisions/ADR-010.md` — worker isolation (session keys, Docker sandbox)
- `/mnt/i/talos/docs/decisions/ADR-011.md` — five gate outcomes (Approve/Reject/Waive/Edit/Escalate)
- `/mnt/i/talos/docs/decisions/ADR-015.md` — phase reorder rationale
- `/mnt/i/talos/docs/upstream/langgraph-notes.md` — existing LangGraph deep-dive
- `/mnt/i/talos/docs/integration/04_build_sequence.md` — P3 scope (dispatcher, heartbeat,
  reclaim, PostgresSaver, Docker sandbox)

After reading, you will know: TALOS's spine is a 4-node StateGraph with `interrupt()` inside
`gate_node` as the sole pause point, `PostgresSaver` for production checkpointing, and
LangGraph's `Command(resume=...)` for handling the five gate outcomes. The entire gate
mechanism is built on LangGraph-specific primitives.

---

## Sources to fetch and study

Fetch every page listed. Read each one completely.

1. `https://code.claude.com/docs/en/agent-sdk/overview` — overview and compare tables
2. `https://code.claude.com/docs/en/agent-sdk/subagents` — subagent spawning, AgentDefinition
3. `https://code.claude.com/docs/en/agent-sdk/sessions` — session management, resume, fork
4. `https://code.claude.com/docs/en/agent-sdk/hooks` — lifecycle hooks (PreToolUse,
   PostToolUse, Stop, SessionStart, SessionEnd, UserPromptSubmit)
5. `https://code.claude.com/docs/en/agent-sdk/skills` — skills system
6. `https://code.claude.com/docs/en/agent-sdk/permissions` — tool allowlist, permission modes
7. `https://code.claude.com/docs/en/agent-sdk/mcp` — MCP server integration

Also search the web for:
- "Claude Agent SDK vs LangGraph comparison 2026"
- "Claude Agent SDK human in the loop interrupt resume 2026"
- "Claude Agent SDK session persistence checkpoint state 2026"
- "claude_agent_sdk PostgresSaver checkpointing state machine"

---

## What to extract from each source

**Core runtime model:**
- What is the execution model? (event loop? graph? linear?)
- How does the Agent SDK handle multi-step tasks? (Does it have a concept of nodes/edges?)
- How does state persist between steps? (session JSONL on filesystem vs. Postgres?)
- Can the agent loop be paused mid-execution and resumed later? What is the mechanism?
- How does this compare to LangGraph's BSP/Pregel model and `interrupt()` + `Command`?

**Human-in-the-loop:**
- Does the Agent SDK have a mechanism equivalent to LangGraph's `interrupt()` (pause
  execution, wait for human input, resume with a specific command)?
- How does `AskUserQuestion` work in the SDK? Is it synchronous or async?
- Can the five gate outcomes (approve/reject/waive/edit/escalate) be implemented naturally
  in the Agent SDK, or does this require LangGraph's conditional edge semantics?

**Subagents:**
- How are subagents spawned and what is their isolation model?
- Do subagents share state with the parent, or is each subagent session-isolated?
- How does this compare to LangGraph's Send API for fan-out?
- Is there a session-key concept equivalent to TALOS's
  `task:{board_id}:{task_id}:{attempt_no}` worker isolation?

**Sessions and checkpointing:**
- What does the session JSONL contain? Is it equivalent to a LangGraph checkpoint?
- Can sessions be stored in Postgres instead of the filesystem?
- Is `resume=session_id` a direct replacement for LangGraph's `thread_id` + checkpoint?
- What happens when a worker dies mid-session — can another worker resume the session?

**Hooks as lifecycle events:**
- What hooks are available and at what granularity?
- Can hooks be used to implement the span-level tracing that ADR-022 requires?
- Can a `PreToolUse` hook implement the deterministic critic checks that run before
  each tool call in TALOS?
- Can hooks replace LangGraph's node callbacks for the critics registry?

**MCP integration:**
- How does the Agent SDK wire up MCP servers?
- Does the MCP integration handle the MCP security boundary (ADR-001) — the point at which
  NEXUS capability is isolated from the TALOS orchestrator?
- Is this MCP integration more or less capable than using LangGraph with MCP tools?

**Skills:**
- How does the Agent SDK's SKILL.md skill system work?
- Does this conflict with, complement, or replace TALOS's skill design (from Agent Zero notes)?
- Can skills be board-scoped (only active for a specific board)?

---

## The central question: replace, complement, or ignore?

After reading all sources, reason through this specific question:

**LangGraph's unique value for TALOS:**
- `interrupt()` + `Command(resume=...)` is the mechanism for the five-outcome gate. This is
  a LangGraph-specific primitive. Does the Agent SDK have an equivalent?
- PostgresSaver with `thread_id` checkpoint is TALOS's production persistence plan (P3a).
  Does the Agent SDK offer a Postgres-backed session store?
- StateGraph with typed channels (including reducers for multi-writer P3b) is how TALOS
  manages the spine's shared state. Does the Agent SDK have typed state channels?
- Conditional edges (`_route_after_gate`) drive the edit loop (gate → deliverable_node →
  gate). Does the Agent SDK support this kind of conditional routing?

**Agent SDK's unique value that LangGraph lacks:**
- What does the Agent SDK provide that LangGraph + FastAPI + custom tool dispatch does not?
- Is the Agent SDK's subagent spawning better than LangGraph's Send API for TALOS's use case?
- Are the built-in tools (Read, Write, Edit, Bash, Grep, Glob) useful inside TALOS's workers?
- Are the hooks richer than LangGraph's callbacks for implementing ADR-022 span tracing?

**The layering question:**
- Could TALOS use LangGraph for the spine/gate (where interrupt() is essential) AND use
  the Agent SDK for what runs inside each LangGraph node (the actual task execution)?
- Specifically: `deliverable_node` currently uses a stub. In P3+, it will invoke a real
  strategy-ladder agent. Could that agent be an Agent SDK `query()` call, with LangGraph
  managing the outer state machine?
- Is this a natural composition or an awkward seam?

---

## What to produce

Write a single file: `/mnt/i/talos/docs/upstream/claude-agent-sdk-notes.md`

Follow the format of existing upstream notes:

```
# Claude Agent SDK — Research Notes

## What it is
[one paragraph — what the SDK is, where it sits in Anthropic's stack]

## Runtime model
[execution model, state, persistence, comparison to LangGraph primitives]

## Human-in-the-loop
[interrupt/resume mechanism, AskUserQuestion, how the five gate outcomes map or don't]

## Subagents and worker isolation
[spawning, session isolation, comparison to TALOS session keys]

## Sessions and checkpointing
[JSONL sessions, Postgres storage?, resume, dead-worker recovery]

## Hooks
[lifecycle hooks, granularity, use for tracing and critics]

## Skills
[skill system, SKILL.md, comparison to TALOS skill design]

## MCP integration
[wiring MCP servers, security boundary handling]

## Key TALOS findings
[bulleted — concrete decisions or patterns TALOS should adopt]

## What TALOS should NOT take
[things that conflict with the gate doctrine, LangGraph primitives, or ADR constraints]

## Answer: replace, complement, or ignore?
[direct answer with reasoning — one paragraph. Which layer does what?]

## Open questions for the builder
[specific questions research couldn't settle]

## Build-phase impact
[Does anything in the Agent SDK change what P3 builds? Which sub-phase is affected?]
```

Write the file. Do not modify any other file.
