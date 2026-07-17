# Hermes Agent — Technical Notes

> **Source:** NousResearch/hermes-agent (MIT)
> **Purpose:** Ground truth for TALOS board engine design. Documents what we take, what we
> improve, and the exact mechanics so we don't re-derive it from scratch.
>
> **Historical note:** predates ADR-039 (the four-store polyglot memory referenced below was
> cancelled/replaced). Retained as written for the historical record; see README.md / ADR-039 for
> current state.
> **Key files:** `hermes_cli/kanban_db.py`, `gateway/kanban_watchers.py`, `agent/conversation_loop.py`,
> `tools/skills_guard.py`, `hermes_cli/goals.py`, `hermes_cli/profiles.py`

---

## 1. Board Schema (SQLite → Postgres port)

The real schema from `hermes_cli/kanban_db.py` — this is what `engine/schema.sql` was ported from.

### tasks table
```sql
CREATE TABLE IF NOT EXISTS tasks (
    id                   TEXT PRIMARY KEY,
    title                TEXT NOT NULL,
    body                 TEXT,
    assignee             TEXT,
    status               TEXT NOT NULL,
    priority             INTEGER DEFAULT 0,
    created_by           TEXT,
    created_at           INTEGER NOT NULL,       -- epoch int → TALOS uses timestamptz
    started_at           INTEGER,
    completed_at         INTEGER,
    workspace_kind       TEXT NOT NULL DEFAULT 'scratch',
    workspace_path       TEXT,
    branch_name          TEXT,
    claim_lock           TEXT,
    claim_expires        INTEGER,
    tenant               TEXT,
    result               TEXT,
    idempotency_key      TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    worker_pid           INTEGER,
    last_failure_error   TEXT,
    max_runtime_seconds  INTEGER,
    last_heartbeat_at    INTEGER,
    current_run_id       INTEGER,
    workflow_template_id TEXT,
    current_step_key     TEXT,
    skills               TEXT,                  -- JSON array of skill names
    model_override       TEXT,
    max_retries          INTEGER,
    goal_mode            INTEGER NOT NULL DEFAULT 0,
    goal_max_turns       INTEGER,
    session_id           TEXT
);
```

### Supporting tables
- `task_links(parent_id, child_id)` — dependency DAG
- `task_events(id, task_id, run_id, kind, payload, created_at)` — **append-only** event log
- `task_comments(id, task_id, author, body, created_at)` — human + agent thread
- `task_runs(id, task_id, profile, step_key, status, claim_lock, claim_expires, worker_pid, max_runtime_seconds, last_heartbeat_at, started_at, ended_at, outcome, summary, metadata, error)` — one row per claim attempt
- `task_attachments(...)` — file attachments
- `kanban_notify_subs(task_id, platform, chat_id, thread_id, user_id, notifier_profile, created_at, last_event_id)` — multi-channel notify subscriptions

### Valid task statuses
```python
VALID_STATUSES = {"triage", "todo", "scheduled", "ready", "running",
                  "blocked", "review", "done", "archived"}
```
**Note:** `review` already exists in Hermes but has no gate behind it — Hermes just parks tasks there
manually. TALOS adds the deterministic critics + `approved_at` enforcement.

### task_runs outcome values
`completed` | `blocked` | `crashed` | `timed_out` | `spawn_failed` | `gave_up` |
`reclaimed` | `rate_limited` | `null` (still running)

### Indexes
```sql
idx_tasks_assignee_status ON tasks(assignee, status)
idx_tasks_status          ON tasks(status)
idx_links_child           ON task_links(child_id)
idx_links_parent          ON task_links(parent_id)
idx_comments_task         ON task_comments(task_id, created_at)
idx_events_task           ON task_events(task_id, created_at)
idx_events_run            ON task_events(run_id, id)
idx_runs_task             ON task_runs(task_id, started_at)
idx_runs_status           ON task_runs(status)
idx_attachments_task      ON task_attachments(task_id, created_at)
idx_notify_task           ON kanban_notify_subs(task_id)
```

---

## 2. Dispatcher Loop

### Entry point
`gateway/kanban_watchers.py` → `GatewayKanbanWatchersMixin._kanban_dispatcher_watcher()`

Runs every **60 seconds** (configurable via `dispatch_interval_seconds`).

### Per-tick sequence (`dispatch_once()` in `kanban_db.py`)
1. Zombie reap — clean up dead child processes
2. `release_stale_claims()` — TTL + heartbeat staleness
3. `detect_stale_running()` — configurable stale threshold
4. `detect_crashed_workers()` — PID liveness check
5. `enforce_max_runtime()` — kill workers over `max_runtime_seconds`
6. `recompute_ready()` — `todo` → `ready` where all parents are `done`/`archived`
7. Spawn loop — for each `ready` task (by priority DESC, created_at ASC):
   - Check `max_spawn`, `max_in_progress`, `max_in_progress_per_profile` caps
   - Claim atomically (`claim_task()`)
   - `spawn_fn(task, workspace_path, board)` → returns worker PID

### Claim mechanism
- `claim_task()` atomically transitions `ready` → `running`
- `claim_lock` = `<hostname>:<pid>:<random>` — claimer identity
- `claim_expires` = now + TTL (default **15 minutes**)
- Refuses to claim if any parent is not `done`/`archived` (CAS, zero-rows = lost)

### Stale reclaim logic (in priority order)
1. `claim_expires < now` → reclaim
2. `last_heartbeat_at` > 1 hour stale → reclaim even if PID alive (catches logic loops)
3. PID alive and local → extend claim (emit `claim_extended` event)
4. PID dead → reclaim, reset to `ready`

### Circuit breaker
- Threshold: `DEFAULT_FAILURE_LIMIT = 2` consecutive failures
- Per-task override: `max_retries` column
- Failure sources: spawn failure, timeout, crash, non-zero exit (except code 75)
- Trip action: auto-block with `gave_up` event
- Reset: only on successful `completed` outcome
- Rate-limit special case: exit code **75** (`EX_TEMPFAIL`) — NOT a failure, task re-queues with 60s cooldown

### Key timing constants
```python
DEFAULT_CLAIM_TTL_SECONDS = 900           # 15 minutes
DEFAULT_CLAIM_HEARTBEAT_MAX_STALE_SECONDS = 3600   # 1 hour
DEFAULT_FAILURE_LIMIT = 2
DEFAULT_CRASH_GRACE_SECONDS = 30          # fork-to-visible window
DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS = 60
DEFAULT_BUSY_TIMEOUT_MS = 120_000         # SQLite busy timeout (2 minutes)
```

### Concurrency
- SQLite WAL mode + `BEGIN IMMEDIATE` + CAS semantics — no distributed locks needed
- Each board is a separate SQLite DB (`~/.hermes/kanban/boards/<slug>/kanban.db`)
- Board isolation via separate DB files (not RLS) — **TALOS promotes to `board_id` + Postgres RLS**

### Worker environment variables injected at spawn
```
HERMES_KANBAN_DB        — DB file path
HERMES_KANBAN_BOARD     — board slug
HERMES_KANBAN_WORKSPACES_ROOT
HERMES_TASK_ID
HERMES_SESSION_ID
```

### `DispatchResult` fields
`spawned`, `reclaimed`, `stale`, `crashed`, `timed_out`, `promoted`, `rate_limited`,
`auto_blocked`, `skipped_unassigned`, `auto_assigned_default`, `skipped_per_profile_capped`

---

## 3. Skill Generation & Crystallization

### Auto-proposal mechanism
`agent/background_review.py` — fires after every conversation turn (daemon thread):
1. Spawns a forked AIAgent with `agent_context="curator"` and restricted tool whitelist
2. Evaluates heuristic signals: user corrected style, complex debugging pattern (5+ tool calls), loaded skill proved incomplete, iterative refinement required
3. If signals fire → calls `skill_manage(action="create")` to propose skill
4. **No human review gate** — proposals go through automated security scan only

### Skill format (SKILL.md with YAML frontmatter)
```yaml
---
name: <skill-name>            # ≤64 chars, lowercase, alphanumeric + hyphens
description: <description>    # ≤1,024 chars
version: <semver>
author: <agent-or-user>
license: <license>
platforms: [linux, macos, windows]
tags: [tag1, tag2]
related_skills: [skill1, skill2]
---
# Markdown content
```

Storage: `~/.hermes/skills/<category>/<skill-name>/`
Limits: max 50 files, max 1 MB total, max 256 KB per file

### Skill security gate (`tools/skills_guard.py`)
100+ regex patterns across 9 threat categories:
1. Exfiltration (env vars, SSH keys, DNS lookups)
2. Prompt injection (role hijacking, instruction override)
3. Destructive ops (rm -rf, format commands)
4. Persistence (cron, .bashrc modification, SSH backdoors)
5. Network threats (reverse shells, tunneling, hardcoded IPs)
6. Obfuscation (base64 pipes, eval/exec, character encoding)
7. Supply chain (unpinned deps, curl-to-shell)
8. Privilege escalation (sudo, setuid/setgid)
9. Credential exposure (hardcoded keys)

**Trust levels:**
| Source | Gate |
| :--- | :--- |
| `builtin` (Hermes-shipped) | Auto-trust, no scan |
| `trusted` (known vendors) | Block on `dangerous` verdict only |
| `community` | Block on `caution` OR `dangerous` |
| `agent-created` | Permit `dangerous` with **user confirmation** |

**Verdict:** `safe` | `caution` | `dangerous`

### Skill lifecycle (`agent/curator.py`)
- `active` (in regular use)
- `stale` (30 days no use)
- `archived` (90 days no use)
- `pinned` (exempt from auto-archival)

Curator runs on idle (`agent.is_idle()` >= `min_idle_hours`) — can merge/patch redundant skills.

### TALOS improvement
Hermes auto-loads agent-created skills after only a security scan. TALOS gates every crystallized
artifact (skill, strategy path, memory node) through `propose → review → pin`. A self-authored
skill is a proposal, not a trusted instruction — this closes the "loaded skill = trusted" hole
from OpenClaw.

---

## 4. Goal Mode (Ralph-Style Judge Loop)

### Schema columns
```sql
goal_mode       INTEGER NOT NULL DEFAULT 0   -- boolean
goal_max_turns  INTEGER                       -- NULL = use DEFAULT_MAX_TURNS (20)
```

### Judge orchestration (`hermes_cli/goals.py`)
- **Executor model:** main task model (`model_override` or profile default)
- **Judge model:** separate auxiliary model (`config: auxiliary.goal_judge`) — completely independent
- **Judge context:** receives ONLY the original goal text + agent's last response (NOT full history)
- **Judge output budget:** 4,096 tokens (accommodates reasoning models' hidden reasoning)
- **Judge verdict:**
  ```json
  {"done": true/false, "reason": "explanation"}
  ```

### Loop iteration
1. Agent turn completes
2. Check `turns_used >= goal_max_turns` → auto-pause if true (before calling judge)
3. Call judge with goal + last response
4. `done: true` → emit `completed`, move to `done`
5. `done: false` → inject continuation prompt, loop
6. Auto-pause also on 3 consecutive judge parse failures (prevents budget burn on weak models)

### Stopping conditions
- Judge returns `done: true`
- Turn budget exhausted (`goal_max_turns`)
- 3 consecutive judge parse failures
- User explicitly pauses/clears
- Real user message preempts the continuation

**CLI:** `hermes kanban create "Task" --goal --goal-max-turns 15`

### TALOS note
Hermes' judge runs outside the conversation loop (post-hoc). TALOS embeds the gate-bound evaluator
inside the Strategy Ladder — it can self-advance through triage/research/plan/refine steps but stops
dead at the human gate before any write-capable tool or safety system.

---

## 5. Worker Profiles

### What a profile is
A **complete isolated Hermes home directory** (`~/.hermes/profiles/<name>/`) with:
- `config.yaml` — model, API keys, tool policies
- `.env` — environment secrets
- `SOUL.md` — agent identity and personality
- `profile.yaml` — metadata (name, description, role)
- Independent state: `memories/`, `sessions/`, `skills/`, `logs/`, `workspace/`, `cron/`

A profile is NOT a job role — it's a full runtime environment with independent state and credentials.

### Profile assignment
- At task creation: `--assignee <profile_name>`
- Task schema: `assignee TEXT` holds the profile name
- Dispatcher passes `-p <profile_name>` to worker subprocess
- `default_assignee` config key for unassigned tasks

### model_override interaction
- `tasks.model_override` overrides the profile's configured model for that one task
- Dispatcher: if `model_override IS NOT NULL` → pass `-m <model>` to worker
- Profile model is the fallback
- Enables heterogeneous compute: same profile, different models per task

### TALOS improvement
Hermes profiles conflate **environment isolation** with **capability routing**. TALOS separates them:
- Profile = identity/secrets/isolation
- Capability selector = tool policy + model routing (declared at task level, layered)
- A task can declare `nexus:read` on `UNIT_*` without needing a new profile

---

## 6. Memory System

### Built-in memory (always active)
Three curated Markdown files per profile:
- **SOUL.md** — persona/identity (immutable, set at profile creation)
- **MEMORY.md** — agent-curated session notes (post-turn background daemon)
- **USER.md** — "deepening model" of the user (populated by `on_memory_write` hooks)

**No vector store in the builtin.** Search is via **SQLite FTS5** over conversation history.

Cross-session recall: LLM-summarized (selected prior sessions summarized and injected as context).

### Conversation loop integration (`agent/conversation_loop.py`)
- **Pre-turn:** `memory_manager.prefetch_all(user_message)` — FTS5 query → LLM scoring → inject top matches
- **Post-turn:** `memory_manager.sync_all()` — write turn to session DB, queue background nudges

### External memory providers (optional)
`plugins/memory/` — one at a time:
`honcho` | `hindsight` | `mem0` | `supermemory` | `byterover` | `holographic` | `openviking` | `retaindb`

Abstract base class `MemoryProvider` in `agent/memory_provider.py` — methods:
`is_available()`, `initialize()`, `system_prompt_block()`, `prefetch()`, `sync_turn()`,
`queue_prefetch()`, `get_tool_schemas()`, `handle_tool_call()`

### TALOS note
Hermes memory is entirely file/SQLite-based with no graph or relational store. TALOS replaces this
with the four-store polyglot memory (Postgres + Neo4j + vector + Redis) federated to the NEXUS graph.
The SOUL.md / MEMORY.md pattern is preserved as the Obsidian vault projection layer — the human-readable
view of what the graph stores structurally.

---

## 7. WebSocket Dashboard

### Tech stack
- **Frontend:** React 19 + Vite + TypeScript, Tailwind CSS v4
- **Backend:** FastAPI + Uvicorn (`hermes_cli/web_server.py`)
- **Port:** 9119 (default)
- **Entry:** `hermes dashboard`

### WebSocket endpoints
| Endpoint | Purpose |
| :--- | :--- |
| `/api/pty` | PTY bridge for embedded terminal (/chat tab) |
| `/api/ws` | Gateway proxy for TUI agent loop |
| `/api/pub` | Inbound-only from tui_gateway process (dispatcher frames) |
| `/api/events` | Outbound-only to browser — fan-out of pub frames |

**Channel model:** `/api/pub` and `/api/events` share an opaque `channel` ID. PTY sidecar publishes;
browser tabs subscribe. Auto-evict when last subscriber + publisher disconnect.

### REST API highlights
- `GET /api/config` / `POST /api/config` — live config management
- `GET /api/status` — health + auth state
- `GET /api/sessions` — active/recent conversations
- `GET /api/skills` — skill browser
- `GET /api/mcp/catalog` — MCP server definitions
- `POST /api/gateway/restart` — restart gateway

### Auth model
- **Loopback (default):** ephemeral `_SESSION_TOKEN` injected into HTML
- **Gated OAuth:** single-use WS tickets via `/api/auth/ws-ticket` + cookie auth
- DNS rebinding defense, loopback-only CORS by default

### TALOS note
TALOS does not fork the dashboard; it borrows the **patterns**: pub/sub channel fan-out,
per-channel subscriber management, ticket-based WS auth, REST + WS split (REST for CRUD, WS for
live events). The cockpit will be Space Agent widgets over the board API rather than a Hermes fork.

---

## 8. Architecture Overview

### Module map
| Module | Purpose |
| :--- | :--- |
| `agent/` | Core conversation loop, tool dispatch, memory/compression |
| `tools/` | 40+ built-in tools (terminal, web, file, git, coding) |
| `skills/` | Bundled procedural skills (GitHub, Gmail, YouTube, etc.) |
| `plugins/memory/` | Pluggable memory providers |
| `gateway/` | Multi-platform messaging (Telegram, Discord, Slack, Matrix) |
| `hermes_cli/kanban_db.py` | Board schema + all DB operations |
| `gateway/kanban_watchers.py` | Dispatcher loop + notification watcher |
| `hermes_cli/goals.py` | Goal mode judge orchestration |
| `hermes_cli/profiles.py` | Profile lifecycle |
| `hermes_cli/web_server.py` | FastAPI + WebSocket dashboard server |
| `web/` | React 19 SPA |
| `cron/` | Scheduler + job storage |
| `providers/` | Model adapters (Anthropic, OpenAI, OpenRouter, Gemini, Ollama) |

### Entry points
```python
hermes          → hermes_cli/main.py       # CLI + TUI
hermes-agent    → run_agent.py             # Programmatic
hermes-acp      → acp_adapter/entry.py    # ACP server
```

### Dependencies (core)
`openai`, `pydantic`, `fastapi`, `uvicorn`, `croniter`, `prompt_toolkit`, `Pillow`

Lazy-installed on first use: `anthropic`, `mistralai`, `boto3`, model-specific, messaging platform SDKs

---

## 9. What TALOS Takes vs. Improves

| Hermes mechanism | TALOS action | Rationale |
| :--- | :--- | :--- |
| SQLite board schema (9 tables) | Port to Postgres, add `board_id` + RLS | Multi-client hard isolation; Postgres features |
| Epoch ints for timestamps | Replace with `timestamptz` | Standard; timezone-safe |
| `review` status (exists, no gate) | Add `task_gate_results` + `approved_at` enforcement | Doctrine made structural |
| Append-only `task_events` | Keep as-is — it's already right | Clean; supports replay and WebSocket |
| Claim/heartbeat/circuit-breaker | Keep mechanics, tune intervals | Battle-tested |
| Rate-limit exit code 75 | Keep — it's correct protocol design | Prevents penalizing transient quota hits |
| Multi-board via separate DBs | Replace with `board_id` column + Postgres RLS | One DB, hard-walled per board |
| `goal_mode` judge loop | Embed in Strategy Ladder as gate-bound evaluator | Doctrine: stops at human gate |
| Skills auto-load after security scan | Gate all crystallized artifacts through `propose → review → pin` | Closes "loaded skill = trusted" hole |
| Profiles = full isolated environments | Separate env isolation from capability/tool routing | Cleaner per-task policy |
| File/SQLite memory (SOUL.md, FTS5) | Replace with 4-store polyglot memory | Graph traversal, vector recall, real-time working memory |
| WebSocket pub/sub pattern | Adopt pattern in cockpit | Clean live-update model |
| `model_override` per task | Keep; add capability profile as separate orthogonal concept | Both matter independently |
| `skills` JSON column | Keep; add gated skill-load lifecycle | Still need per-task skill injection |
| `idempotency_key` | Keep — safe task dedup is table stakes | Webhook retries, replay safety |
| Workspace kinds (scratch/worktree/dir) | Keep | Covers all execution contexts |
