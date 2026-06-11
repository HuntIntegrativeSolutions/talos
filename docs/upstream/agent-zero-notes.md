# Agent Zero — Technical Deep-Dive Notes

> Research date: 2026-06-11  
> Source: `agent0ai/agent-zero` (frdel/agent-zero) — MIT License  
> Purpose: Inform TALOS harness design. Patterns only — zero ported code.

---

## Executive Summary

| Area | Key Finding | TALOS Disposition |
| :--- | :--- | :--- |
| **Tool loading** | Filesystem path search; auto-trusted; no gate | Must add gate — this is the attack surface TALOS closes |
| **Skill system** | SKILL.md = documentation only, not code | Adopt: skill-as-instruction-doc pattern is elegant |
| **Agent hierarchy** | Synchronous subordinate delegation, shared context | Adopt: profile-override pattern; reject shared context for TALOS |
| **Memory areas** | MAIN / FRAGMENTS / SOLUTIONS in single FAISS index | Adopt: three-area model, but swap FAISS for pgvector |
| **Consolidation** | Background LLM task: MERGE/REPLACE/KEEP_SEPARATE/UPDATE/SKIP | Adopt with scope guard (no cross-client MERGE) |
| **Desktop driving** | Screenshot + xdotool, pure pixel observation | Not applicable to TALOS (no desktop control needed) |
| **Plugin system** | `plugin.yaml` + `@extensible` decorator for hook injection | Adopt: `@extensible` pattern is architecture gold |
| **Project isolation** | `.a0proj/` folder + 5-tier path search | Adopt: layered-override pattern for all asset types |
| **Scheduler** | Cron/adhoc/planned tasks — NOT a kanban board | Partial adopt: background job model; board lives in Hermes |
| **Web UI** | Alpine.js + Flask + FAISS + LiteLLM | Patterns only; TALOS uses Space Agent widgets instead |

---

## 1. Tool Loading — The Auto-Trust Hole

**Files:** `agent.py:1005-1040`, `helpers/modules.py:12-24`

Agent Zero does **not** create tools dynamically at runtime. Instead, it **discovers** tools at call time by searching a 5-tier path hierarchy for a `.py` file containing a `Tool` subclass:

```
Search order:
1. project/.a0proj/tools/<name>.py
2. usr/plugins/*/tools/<name>.py
3. usr/tools/<name>.py
4. plugins/*/tools/<name>.py     (core plugins)
5. tools/<name>.py               (default)
```

Dynamic import on every call (no caching):

```python
# helpers/modules.py
spec = importlib.util.spec_from_file_location(module_name, abs_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
```

**Critical security observation:** Any `.py` `Tool` subclass found in the search path is **auto-executed with no validation, no signature check, no review gate.** This is the "loaded skill = trusted" hole that TALOS explicitly closes.

**TALOS disposition:** The layered path search is the valuable pattern. Auto-trust is replaced by the TALOS gate — a tool file proposed at the user or project level enters `proposed` state first; a critic pass and human approval is required before it moves to `pinned` and becomes loadable.

---

## 2. Skill System — Skill-as-Documentation

**Files:** `tools/skills_tool.py`, `helpers/skills.py`, `skills/a0-create-plugin/SKILL.md`

Skills in Agent Zero are **SKILL.md files, not code files.** A skill is a recipe — it tells the agent *what steps to take*, not *what code to run*.

**SKILL.md frontmatter:**

```yaml
---
name: a0-create-plugin
description: Create, extend, or modify Agent Zero plugins...
version: 1.0.0
tags: ["plugins", "create", "build", "develop", "extend"]
trigger_patterns:
  - "create plugin"
  - "build plugin"
  - "new plugin"
allowed_tools: [code_execution, file_write]
---

# Skill Body (Markdown)

To accomplish X, follow these steps:
1. Parse input using...
2. Validate constraints...
3. Return result in format...
```

**Discovery roots (11 total, recursive scan):**

```
skills/                                    # built-in
usr/skills/                               # user custom
agents/*/skills/                          # per-agent
plugins/*/skills/                         # plugin-provided
usr/projects/*/.a0proj/skills/            # project-scoped
```

**Lifecycle:**

1. Agent sees available skills listed in its system prompt.
2. Agent calls `skills_tool:load` to pull a skill's body into context.
3. Agent reads the instructions and executes them manually — calling `file_write` or `code_execution` to materialize any artifacts.
4. No code generation happens automatically. The skill is a navigation doc, not a program.

**Key distinction from Hermes:** Hermes auto-generates SKILL.md files via `background_review.py` when a successful solution is found, then auto-trusts them. TALOS takes Agent Zero's documentation structure but adds Hermes' auto-crystallization concept with a mandatory `propose → review → pin` gate before a skill becomes executable.

**TALOS disposition:** Adopt SKILL.md as the canonical skill format. Every skill starts as a documentation artifact in `proposed` state. Critics review the allowed_tools list and content for safety. Human approval moves it to `pinned` and adds it to the path search.

---

## 3. Agent Hierarchy — Synchronous Subordinate Delegation

**Files:** `tools/call_subordinate.py:10-37`, `agent.py:283-302`

**Creation:**

```python
# call_subordinate.py
sub = Agent(self.agent.number + 1, config, self.agent.context)  # A0 → A1 → A2…
sub.set_data(Agent.DATA_NAME_SUPERIOR, self.agent)
self.agent.set_data(Agent.DATA_NAME_SUBORDINATE, sub)
```

**Key characteristics:**

- **Shared AgentContext**: parent and child share the same memory, log, and data dictionary. No isolation between levels.
- **Profile override**: subordinate can run a different system prompt profile (e.g., parent is "Default," child is "Code Reviewer").
- **Synchronous**: parent blocks on `await subordinate.monologue()`. No parallel execution.
- **Result passback**: child result injected as a fake `call_subordinate` tool result in parent's history.

**Recursion depth:** Arbitrarily deep. A2 can spawn A3, which returns to A2, which returns to A1, which returns to A0.

**Limitation:** All agents in a chain share the same history. There is no way for a subordinate to have a private context. For TALOS, this is unsuitable for multi-client deployment where strict scope isolation is required.

**TALOS disposition:**

- **Adopt:** profile-override pattern — specialized sub-agents run different instruction sets from the same harness.
- **Reject:** shared AgentContext — TALOS uses session keys + per-task scope boundaries. Each worker gets its own context window; results are passed as structured output through the gate, not injected into parent history.
- **Adopt:** agent-number as lineage identifier — useful for tracing execution provenance.

---

## 4. Memory System — Three-Area Model

**Files:** `plugins/_memory/helpers/memory.py:54-95`, `plugins/_memory/helpers/memory_consolidation.py:53-111`

### Memory Areas (Enum)

```python
class Memory.Area(Enum):
    MAIN      = "main"       # General facts, notes, learned context
    FRAGMENTS = "fragments"  # Short ephemeral snapshots of intermediate reasoning
    SOLUTIONS = "solutions"  # LLM-extracted problem→solution pairs from completed runs
```

All three areas live in **one FAISS index** at `usr/memory/[subdir]/index.faiss`, distinguished by metadata `area` field.

### Storage

- **Vector database:** FAISS (in-process, persisted to disk)
- **Embedding model:** Sentence Transformers (local, configurable)
- **Path:** `usr/memory/<project>/<profile>/index.faiss` + `docstore.pkl`

### Retrieval

Two parallel searches at `monologue_start`:

```python
# extension: message_loop_prompts_after/_50_recall_memories.py
main_results = search_by_similarity_threshold(query, threshold=0.4, filter="area != 'solutions'")
soln_results = search_by_similarity_threshold(query, threshold=0.5, filter="area == 'solutions'")
```

Results injected into system prompt under "Relevant Memories" — agent decides whether to use them.

### Memory Lifecycle (INSERT path)

1. User triggers `memory_save`, or `monologue_end` extension fires.
2. New memory inserted via `insert_documents()`.
3. Consolidation triggered asynchronously (background task).

### What a "Verified Solution" Is

SOLUTIONS area entries are **not user-verified** — they are **LLM-extracted** from completed chat history at `monologue_end`. A utility LLM reads the session and extracts `{"problem": "...", "solution": "..."}` pairs. There is no human review step. The name "verified solutions" is misleading in the context of TALOS's Guardian doctrine.

**TALOS disposition:**

- **Adopt:** three-area conceptual model (working notes / fragments / crystallized solutions).
- **Rename** SOLUTIONS → CRYSTALLIZED to avoid confusion with "verified" in TALOS's gating sense.
- **Swap:** FAISS → pgvector (Postgres, already in the stack; avoids separate vector store).
- **Add:** human-review gate before any entry graduates from FRAGMENTS to CRYSTALLIZED.
- **Scope guard:** memory areas are per-client. Cross-client MERGE is forbidden at the consolidation layer.

---

## 5. Memory Consolidation — LLM-Mediated Deduplication

**File:** `plugins/_memory/helpers/memory_consolidation.py:53-111`

When a new memory is saved, a background task:

1. Searches for up to `max_similar_memories=10` existing entries (threshold: 0.5 cosine).
2. Sends new entry + top matches to a utility LLM with a consolidation prompt.
3. LLM returns one of five actions:

| Action | Meaning |
| :--- | :--- |
| `MERGE` | Combine new + old into one entry, delete old |
| `REPLACE` | Delete old entry, insert new (if similarity > 0.75) |
| `KEEP_SEPARATE` | Insert new alongside similar (different contexts) |
| `UPDATE` | Modify existing entry with new information |
| `SKIP` | Don't insert (duplicate / low quality) |

**Configuration:**

```python
@dataclass
class ConsolidationConfig:
    similarity_threshold: float = 0.5
    max_similar_memories: int = 10
    replace_similarity_threshold: float = 0.75
    processing_timeout_seconds: int = 60  # safety cutoff
```

**Key properties:**

- **Async and non-blocking**: consolidation does not delay agent response.
- **Timeout-safe**: if LLM exceeds 60 seconds, the new memory is inserted without consolidation.
- **The 0.75 replacement threshold** prevents accidental high-similarity overwrites during ambiguous matches.

**TALOS additions:**

- **Client-scope guard**: consolidation MERGE across different client scopes is hard-blocked (cross-client memory is a data leak vector).
- **Sensitivity threshold**: memories above a sensitivity level require human approval before consolidation actions MERGE or REPLACE are applied. Below the threshold, autonomous consolidation proceeds.
- **Audit log**: every consolidation action (including SKIP) is appended to a `memory_events` table.

---

## 6. Plugin and Extension System — The `@extensible` Pattern

**Files:** `helpers/extension.py:25-150`, `plugins/README.md`

### The `@extensible` Decorator (Architecture Gold)

Any async function decorated with `@extension.extensible` automatically gets two implicit extension points without any registration:

```python
# Before and after ANY decorated function:
# _functions/<module>/<classname>/<methodname>/start
# _functions/<module>/<classname>/<methodname>/end

@extension.extensible
async def monologue(self):
    # Extensions at: _functions/agent/Agent/monologue/start
    # Extensions at: _functions/agent/Agent/monologue/end
    ...
```

Extension context `data` dict:

```python
data = {
    "args": args,
    "kwargs": kwargs,
    "result": _UNSET,    # Set to short-circuit the original function
    "exception": None,   # Set to suppress or replace an exception
}
```

Start extensions can **short-circuit** the original function by setting `data["result"]`. End extensions can **mutate the return value** or clear exceptions.

**Named lifecycle extension points (core):**

| Extension Point | Timing |
| :--- | :--- |
| `agent_init` | After agent constructed |
| `message_loop_start` / `end` | Each iteration |
| `message_loop_prompts_before` / `after` | Around prompt assembly |
| `monologue_start` / `end` | Around inner thinking loop |
| `tool_execute_before` / `after` | Around every tool call |
| `process_chain_end` | After top-level message processed |

### Plugin Manifest

Every plugin requires `plugin.yaml`:

```yaml
name: my_plugin
title: My Plugin
description: What this does
version: 1.0.0
settings_sections: [agent, external]
per_project_config: false
per_agent_config: false
always_enabled: false  # reserved for core plugins
```

Discovery: scans `plugins/` (core) and `usr/plugins/` (user) for `plugin.yaml`.

### Plugin Directory Layout

```
usr/plugins/<name>/
  plugin.yaml
  default_config.yaml
  hooks.py
  api/            → REST endpoint handlers
  tools/          → Tool subclasses
  helpers/        → Shared Python modules
  prompts/        → Markdown prompt templates
  extensions/python/<point>/  → Lifecycle extension scripts
  webui/          → HTML/JS/Alpine components
  agents/         → SubAgent profile definitions (agent.yaml)
  skills/         → SKILL.md files
```

### 5-Tier Path Override System

The most valuable architectural pattern in Agent Zero. Every asset type searches a layered hierarchy:

| Asset | Search Order |
| :--- | :--- |
| tools | project → user → core-plugins → default |
| skills | project → user → agent-profile → plugins → default |
| prompts | project → user → default |
| knowledge | project → user → default |
| agents/profiles | project → user → default |
| plugins | user (`usr/plugins/`) → core (`plugins/`) |

**No config required**: drop a file in the right folder and it overrides automatically. The hierarchy resolves at runtime on every call.

**TALOS disposition:** Adopt `@extensible` liberally — it prevents tight coupling between core flows and plugin logic. Adopt the 5-tier path search for all TALOS asset types (tools, skills, prompts, agents). TALOS adds the gate layer: user-level and project-level overrides enter `proposed` state before they become active.

---

## 7. Project Isolation

**File:** `helpers/projects.py:1-170`

Each project lives at `usr/projects/<name>/.a0proj/`:

```
.a0proj/
  project.json      → title, description, instructions, git_url, variables, secrets
  instructions/     → project-scoped prompts
  knowledge/        → project-scoped vector DB
  agents/           → project-scoped agent profiles
  skills/           → project-scoped skills
  tasks/            → scheduled task definitions
```

**Activation:** When a chat is associated with a project, the project name is injected into `context.data`. The path search picks up `.a0proj/` directories first.

**Git integration:** Projects can be cloned from a Git repo. The clone token is passed via `http.extraHeader` (never embedded in the URL). The `.a0proj/` folder is merged with or overlaid on the cloned repo contents.

**File structure injection:** Optional. If enabled, the agent's system prompt includes a tree-view of project files at configurable `max_depth` and `max_files`.

**Secrets vs. variables:** Variables are visible to the agent (injected into context). Secrets are encrypted, accessible only via backend API — never serialized to chat history.

**TALOS disposition:** This is the right model for client isolation. TALOS replaces the project folder with `board_id` + Postgres RLS (already in `engine/schema.sql`) for task records, and keeps the path search for skill/tool/prompt assets per client. Secrets move to Postgres encrypted column with board_id scoping.

---

## 8. Scheduler — Background Job Model

**Files:** `tools/scheduler.py:119-431`, `helpers/task_scheduler.py`

Agent Zero has a structured task scheduler — not a kanban board. Tasks are scheduled work units:

```python
class ScheduledTask:
    id: str
    project: str
    name: str
    description: str
    schedule: TaskSchedule      # Cron (5-field: min hour day month weekday)
    state: TaskState            # todo | in_progress | done | failed | waiting
    created_at: datetime
    last_run: datetime | None
    next_run: datetime | None
    runs: list[TaskRun]         # History log
```

**Three task types:**

| Type | Trigger |
| :--- | :--- |
| Scheduled | Cron expression (recurring) |
| Ad-hoc | ASAP (one-shot) |
| Planned | List of specific ISO datetimes |

Tasks are stored as JSON in `usr/tasks/` or `usr/projects/<name>/.a0proj/tasks/`. At trigger time, the scheduler creates (or reuses) a chat context and sends a message to the agent to run the associated skill.

**TALOS disposition:** The Hermes board already handles the task lifecycle for TALOS. The Agent Zero scheduler model is the right source for **background loops** in the TALOS gateway layer: status digests, overdue-deadline nudges, audit-freshness checks. These map to "planned" and "scheduled" task types. Gate the results the same way as manual tasks.

---

## 9. Web UI Architecture

**Stack:** Alpine.js (reactive, no bundler) + Flask (async) + SSE (streaming) + WebSocket (bidirectional control).

**Layout:**
- Left sidebar: chats, tasks, projects, settings, plugin hub.
- Right panel: message history, chat input, process groups (expandable tool/LLM logs).
- Canvas: embedded surfaces (xpra desktop, markdown editor, browser, LibreOffice).

**Frontend extension points (named injection slots):**

```html
<x-extension id="page-head"></x-extension>              <!-- scripts/CSS in <head> -->
<x-extension id="sidebar-quick-actions-main-start">    <!-- sidebar button area -->
<x-extension id="chat-top-section-end">                <!-- panel above chat history -->
```

**Alpine.js store pattern:**

```javascript
export const store = createStore("myStore", {
    data: [],
    async load() { ... }
});
```

Stores are global singletons; reactivity via `x-data`, `x-model`, `x-for`.

**TALOS disposition:** Patterns useful for the cockpit's sidebar and streaming log panel. The canvas "surface" abstraction (single right panel hosting multiple iframe-like components) is the right mental model for TALOS's widget system. TALOS uses Space Agent's grid canvas instead of Alpine.js, but the surface/surface-switching concept is borrowed.

---

## 10. Architecture Overview

### Execution Flow

```
User message → POST /api/chat/message
  → AgentContext.communicate(UserMessage)
  → Agent(0).monologue()
      Extensions: message_loop_start
      Prompt assembly (with recalled memories, loaded skills)
      LLM call
      Tool dispatch (path search → dynamic import → execute)
      Extensions: tool_execute_before/after
      Result added to history
      Loop until response tool
      Extensions: monologue_end (recall_memories, memorize_solutions)
  → Result streamed to UI via SSE
```

### Tech Stack

| Layer | Choice |
| :--- | :--- |
| LLM API | LiteLLM (unified: OpenAI, Claude, Gemini, local) |
| Web server | Flask (async) |
| Vector DB | FAISS (in-process) |
| Embeddings | Sentence Transformers (local) |
| Browser automation | Playwright |
| Desktop driving | xpra + xvfb + xdotool |
| Git integration | GitPython |
| MCP | FastMCP |
| Config / validation | PyYAML, Pydantic, DirtyJSON |
| Frontend | Alpine.js + vanilla JS + SSE |

---

## Key Patterns for TALOS (Summary)

### Pattern 1 — Layered Path Override

Drop a file in the right folder; it overrides the default with no config. Add a gate step: user/project overrides start in `proposed`, not `active`.

### Pattern 2 — `@extensible` for Transparent Hook Injection

```python
@extensible
async def any_core_function(...):
    # Auto-generates start/end extension points
    # Plugins inject without touching this function
```

Use on every major TALOS lifecycle step: task_claim, gate_evaluate, critic_run, worker_execute, memory_write.

### Pattern 3 — Three Memory Areas

MAIN (facts) + FRAGMENTS (ephemeral reasoning) + CRYSTALLIZED (gated, reviewed solutions). Separate recall strategies per area; separate promotion gates.

### Pattern 4 — Background Consolidation with Hard Client-Scope Guard

Run consolidation async, timeout-safe (60s). Block MERGE across client scopes at the consolidation layer, not just the retrieval layer. Every consolidation action appended to audit log.

### Pattern 5 — Skill-as-Documentation

SKILL.md is the recipe; the agent performs it. No code is auto-executed from a skill file. The skill's `allowed_tools` list is what critics review for safety.

### Pattern 6 — Session-Key Config Inheritance

Parent config propagates to child. Child may add restrictions. Child may never expand parent's permissions. This replaces Agent Zero's shared-AgentContext model for TALOS's multi-client deployment.
