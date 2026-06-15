# P3 Implementation Prompt — Full Dispatcher

You are implementing **P3** of TALOS, a pre-alpha multi-agent industrial project-execution
platform. P0 (schema + contracts), P1 (single-worker spine), and P2 (critics + five-outcome
gate) are all complete with 27 passing tests.

**Read before you write a single line of code:**

1. `/mnt/i/talos/CLAUDE.md` — project overview, architecture, Guardian Doctrine, directory layout
2. `/mnt/i/talos/docs/p3-pre-build-decisions.md` — **the canonical pre-build decisions document**;
   every P3 sub-phase decision is recorded here; do not re-derive them
3. `/mnt/i/talos/docs/decisions/ADR-018-model-configuration.md`
4. `/mnt/i/talos/docs/decisions/ADR-019-persistence-backend.md`
5. `/mnt/i/talos/docs/decisions/ADR-020-reclaim-thresholds.md`
6. `/mnt/i/talos/docs/decisions/ADR-021-verifier-critic-type.md`
7. `/mnt/i/talos/docs/decisions/ADR-022-observability-tracing.md`
8. `/mnt/i/talos/docs/decisions/ADR-029-agent-sdk-integration.md`
9. `/mnt/i/talos/talos/graph/spine.py` — the existing 4-node spine you will extend
10. `/mnt/i/talos/talos/worker.py` — the existing single-worker claim loop you will evolve
11. `/mnt/i/talos/talos/api.py` — the FastAPI board API (do not break its existing endpoints)
12. `/mnt/i/talos/engine/schema.sql` and `/mnt/i/talos/engine/schema-p2.sql` — existing schema

After reading, you will know:
- The code lives in `talos/` (not `platform/`)
- The 4-node spine already has `read_node`, `deliverable_node`, `gate_node`, `post_gate_node`
- The gate already handles all five outcomes (approve/reject/waive/edit/escalate)
- `build_graph(checkpointer=None)` accepts an injected checkpointer (P3a wires PostgresSaver)
- `TALOS_NEXUS_STUB=1` stubs out all MCP calls — keep this working throughout P3

---

## Build sequence within P3

Implement in strict sub-phase order. Do not begin P3b until P3a tests pass. Do not begin P3c
until P3b tests pass. Do not begin P3d until P3c tests pass. Mark each sub-phase complete by
running the full test suite at `talos/`.

```
P3a — PostgresSaver + dead-worker reclaim + schema additions
P3b — Multi-worker dispatcher + model config + Agent SDK integration in nodes
P3c — Docker FS sandbox for code-exec subprocess
P3d — PM hooks + observability (task_spans table + webhook alerting)
```

---

## P3a — PostgresSaver + Dead-Worker Reclaim

### What to build

**Schema file: `engine/schema-p3.sql`**

Add these to the new schema file (do not modify `schema.sql` or `schema-p2.sql`):

```sql
-- Board-level model config override (ADR-018)
ALTER TABLE boards ADD COLUMN IF NOT EXISTS model_config JSONB;

-- Heartbeat tracking (ADR-020)
ALTER TABLE task_runs ADD COLUMN IF NOT EXISTS last_heartbeat_at TIMESTAMPTZ;
ALTER TABLE task_runs ADD COLUMN IF NOT EXISTS max_runtime_seconds INTEGER;

-- task_spans table (ADR-022 — add now so schema is ready for P3d instrumentation)
CREATE TABLE IF NOT EXISTS task_spans (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    board_id        UUID NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
    task_id         UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    run_id          UUID,
    parent_span_id  UUID,
    span_name       TEXT NOT NULL,
    model_id        TEXT,
    provider        TEXT,
    prompt_tokens   INTEGER,
    completion_tokens INTEGER,
    latency_ms      INTEGER,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at        TIMESTAMPTZ,
    payload         JSONB,
    otlp_exported_at TIMESTAMPTZ
);

-- RLS for task_spans — identical to task_events policy structure (ADR-022)
ALTER TABLE task_spans ENABLE ROW LEVEL SECURITY;

CREATE POLICY board_isolation ON task_spans
    USING (board_id = current_setting('app.board_id', true)::UUID);

CREATE POLICY admin_bypass ON task_spans
    TO talos_admin USING (true);
```

**`talos/config.py` (new module)**

Load `talos.toml [models]` and resolve the effective model for a given ladder step and board.
Return `(primary: str, fallback: str)`. The cascade is:

1. `talos.toml [models]` → defaults for all 6 steps
2. `boards.model_config JSONB` → board-level override for any step
3. `tasks.model_override TEXT` → per-task override (all steps for this task use this string)

The 6 ladder step names: `triage`, `research`, `plan`, `gate`, `execute`, `crystallize`.

Example `talos.toml` shape:
```toml
[models]
triage_primary   = "claude-haiku-4-5-20251001"
triage_fallback  = "claude-haiku-4-5-20251001"
research_primary = "claude-sonnet-4-6"
research_fallback = "claude-haiku-4-5-20251001"
plan_primary     = "claude-sonnet-4-6"
plan_fallback    = "claude-haiku-4-5-20251001"
gate_primary     = "claude-sonnet-4-6"
gate_fallback    = "claude-haiku-4-5-20251001"
execute_primary  = "claude-sonnet-4-6"
execute_fallback = "claude-haiku-4-5-20251001"
crystallize_primary  = "claude-opus-4-8"
crystallize_fallback = "claude-sonnet-4-6"
```

Model strings are opaque — TALOS does not validate them. Pass through to LLM client.

**`talos/worker.py` updates for P3a**

Add `TALOS_HEARTBEAT_INTERVAL_S` (env var, int, default 30) and `TALOS_RECLAIM_AFTER_MISSES`
(env var, int, default 3). At task claim time:

1. Copy `tasks.max_runtime_seconds` into `task_runs.max_runtime_seconds`.
2. Write `last_heartbeat_at = now()` on the `task_runs` row at each LangGraph node boundary.
   Use a `node_callback` passed into `build_graph()` rather than a background thread (no
   background threads in P3a — heartbeat fires only at node transitions).

Add a reclaim function: at claim time, first scan for dead runs:
```sql
SELECT id, task_id FROM task_runs
WHERE completed_at IS NULL
  AND last_heartbeat_at < now() - (TALOS_HEARTBEAT_INTERVAL_S * TALOS_RECLAIM_AFTER_MISSES) * interval '1 second'
```
For each dead run: set `task_runs.completed_at = now()`, set `tasks.status = 'ready'` (re-queues
the task), increment `task_runs.attempt_no + 1` for the new claim. Log the reclaim action.

**Inject PostgresSaver**

At `worker.py` startup, create a `PostgresSaver` instance using the `DATABASE_URL` env var.
Pass it to `build_graph(checkpointer=saver)`. The spine's `build_graph(checkpointer=None)`
default stays intact for unit tests.

### P3a tests — `talos/tests/test_p3a_postgres.py`

Use `testcontainers` (Postgres 16) for all P3a integration tests. Parallel to the existing
`conftest.py` that already uses testcontainers.

Required tests:
1. `test_postgres_saver_checkpoint_survives_restart` — graph runs to gate interrupt, state
   is dumped, a NEW graph instance reloads from PostgresSaver, gate resumes, task completes.
2. `test_dead_worker_reclaim` — simulate a dead run by inserting a `task_runs` row with a stale
   `last_heartbeat_at`. A new worker claim triggers reclaim: verify `tasks.status = 'ready'` and
   a new `task_runs` row is created.
3. `test_heartbeat_fires_at_node_boundary` — run the spine with a test checkpointer that tracks
   heartbeat calls; verify at least one heartbeat fires per node.
4. `test_model_config_cascade` — verify that `tasks.model_override` overrides `boards.model_config`
   which overrides `talos.toml [models]` defaults.
5. `test_task_spans_table_rls` — insert a span for board A, verify board B session cannot read it.

---

## P3b — Multi-Worker Dispatcher + Model Config + Agent SDK Integration

### What to build

**Multi-worker dispatcher**

Replace the single `while True: claim_one_task()` loop with a dispatcher that launches N
workers concurrently. The architecture:

```
dispatcher (main process)
├── WorkerSlot-1  (asyncio task or thread)
├── WorkerSlot-2
└── WorkerSlot-N
```

Worker count: `TALOS_WORKER_COUNT` env var, int, default 1.

Claim racing is handled at the DB level — `task_runs.worker_id` is set atomically in
the claim query. Two workers racing for the same task: only one wins; the other loops
back and tries the next available task. No application-level locking needed.

Use `asyncio.gather` with individual worker coroutines for the P3b implementation. Each
worker coroutine is an infinite loop: claim → run spine → release → repeat. On unhandled
exception from the spine, log the error, mark the task_run as failed, and loop.

**Budget enforcement (from Omnigent research)**

Add a 4-axis budget object to `SpineState`:
```python
class TaskBudget(TypedDict):
    max_spend_usd: float        # hard cap; 0.0 = unlimited
    max_tokens: int             # hard cap; 0 = unlimited
    max_tool_calls: int         # hard cap; 0 = unlimited
    max_elapsed_seconds: int    # hard cap; 0 = unlimited
    soft_spend_usd: float       # soft threshold → escalate for human confirmation
    spent_usd: float            # running total
    tokens_used: int            # running total
    tool_calls: int             # running total
```

When any hard cap is exceeded: transition task to `review` status and emit a
`gate_outcome = escalate` (not a crash, not a silent termination). This creates a durable
gate record the human can waive or extend. When soft threshold is crossed: continue but
emit a `spine.budget.soft_threshold` span (P3d).

**Agent SDK integration in spine nodes (ADR-029)**

`read_node` and `deliverable_node` will call `query()` from the Claude Agent SDK.
ADR-029 Q1 confirmed `sdk_node` placement is safe in pre-gate nodes (attempt_count stays 1
after gate resume — no double-execution).

Required additions to `SpineState`:
```python
sdk_session_ids: dict  # default {}; keys = node name, value = SDK session ID string
```

Each call to `query()` stores its session ID: `state["sdk_session_ids"]["read_node"] = session_id`.
On re-entry (new attempt after crash), pass `resume=state["sdk_session_ids"].get("read_node")`
to continue mid-analysis rather than restart.

In `TALOS_NEXUS_STUB=1` mode: `read_node` and `deliverable_node` must NOT call `query()`.
Stub the return values directly. All existing tests must continue to pass.

Model selection for nodes: call `config.resolve_model(step="research", board_id=..., task=...)`
to get `(primary, fallback)`. Pass `primary` to `query()`. On SDK error: retry once with
`fallback`. If both fail: transition task to `review` with `gate_outcome = escalate`.

**`talos/llm.py` (new module)**

Thin wrapper around `query()` that:
1. Accepts a model string and prompt.
2. Resolves the session ID from `SpineState` for continuity.
3. Emits the `llm.call` span (P3d) as a timing wrapper around the `async for message in query(...)`
   loop — entry time before loop, exit time after, latency_ms = exit - entry.
4. Returns `(text: str, session_id: str, tokens: int)`.

### P3b tests — `talos/tests/test_p3b_dispatcher.py`

Required tests:
1. `test_concurrent_workers_no_double_claim` — start 3 workers, enqueue 2 tasks; verify each
   task is claimed exactly once (no double-execution).
2. `test_claim_race_resolution` — simulate two workers racing by inserting a task and having
   two coroutines attempt to claim simultaneously; only one should succeed.
3. `test_model_fallback_on_primary_failure` — mock the LLM client to fail on the primary model;
   verify the fallback is used and no exception propagates.
4. `test_both_models_fail_escalates` — mock both models to fail; verify task transitions to
   `review` with `gate_outcome = escalate`.
5. `test_budget_hard_cap_escalates` — set `max_tokens = 1` on a task; verify budget exhaustion
   produces a `review` state transition, not a crash.
6. `test_sdk_session_id_persists` — run spine with stub SDK; verify `sdk_session_ids` key is
   populated in SpineState checkpoint after `read_node` completes.

---

## P3c — Docker FS Sandbox

### What to build

**Two-level containment (ADR-010 clarification):**

```
[TALOS worker process]       ← has network: Postgres, NEXUS MCP, model APIs
    └── [Docker subprocess]  ← network:none, readOnlyRoot
         └── [agent-generated code runs here]
```

The Docker subprocess is invoked ONLY when TALOS needs to execute untrusted agent-generated
code (code-exec ladder step). The worker process itself is not sandboxed.

**`talos/sandbox.py` (new module)**

```python
class Sandbox:
    def run(self, code: str, timeout_s: int = 30) -> SandboxResult: ...
```

`SandboxResult` is a dataclass: `(stdout: str, stderr: str, exit_code: int, timed_out: bool)`.

Docker run flags required (this is the exhaustive list — do not add or remove):
```
--network none
--read-only
--tmpfs /tmp:size=64m
--memory 256m
--cpus 0.5
--rm
--user nobody
--security-opt no-new-privileges
```

Write a Dockerfile at `talos/sandbox/Dockerfile` that starts FROM `python:3.11-slim` and
creates a minimal execution environment (no pip packages beyond stdlib, no network at runtime).

**`TALOS_SANDBOX_MODE` env var:**
- Default: `docker` — use Docker subprocess.
- `none` — bypass Docker entirely; run code in-process (UNSAFE).
  On startup with `TALOS_SANDBOX_MODE=none`, emit a `CRITICAL` log line and write a
  one-line warning to `./talos-sandbox-bypass.log`. The warning must say:
  `"TALOS_SANDBOX_MODE=none: code-exec sandbox is disabled. This is a security risk. Do not use in production."`

**Write-paths allowlist (from Omnigent research):**

Expose a `write_paths` config in `talos.toml [sandbox]` listing directories the Docker container
may write to via `--tmpfs` mounts. Paths outside this list are covered by `--read-only`.
Default: `["/tmp"]`.

### P3c tests — `talos/tests/test_p3c_sandbox.py`

These tests require Docker. Add a `TALOS_DOCKER_AVAILABLE` skip marker:
```python
import shutil
REQUIRES_DOCKER = pytest.mark.skipif(
    shutil.which("docker") is None or os.environ.get("TALOS_SKIP_DOCKER") == "1",
    reason="Docker not available — skipping sandbox tests"
)
```

Required tests:
1. `test_sandbox_runs_hello_world` — run `print("hello")` in the sandbox; verify stdout.
2. `test_sandbox_network_blocked` — attempt `import urllib.request; urllib.request.urlopen("http://1.1.1.1")`;
   verify the call fails (network:none).
3. `test_sandbox_readonly_fs` — attempt to write a file at `/etc/pwned`; verify PermissionError.
4. `test_sandbox_timeout` — run `import time; time.sleep(60)` with `timeout_s=2`; verify
   `timed_out=True` in result.
5. `test_sandbox_bypass_mode_logs_warning` — set `TALOS_SANDBOX_MODE=none`, create a Sandbox,
   verify warning file is created and contains the exact warning string.

---

## P3d — PM Hooks + Observability

### What to build

**Span emission utility — `talos/spans.py` (new module)**

```python
class SpanContext:
    board_id: str
    task_id: str
    run_id: str | None

def emit_span(ctx: SpanContext, span_name: str, *, model_id: str | None = None,
              provider: str | None = None, prompt_tokens: int | None = None,
              completion_tokens: int | None = None, latency_ms: int | None = None,
              parent_span_id: str | None = None, payload: dict | None = None,
              db_conn) -> str:  # returns span ID
    ...
```

`emit_span` inserts one row into `task_spans`. In `TALOS_NEXUS_STUB=1` mode: buffer spans
in-memory (a module-level list) instead of writing to Postgres — tests can inspect the buffer.

**Weave spans into the spine and worker**

The full P3 minimum span set (from ADR-022 and `p3-pre-build-decisions.md`):

| Emitted when | Span name |
|---|---|
| Task claim succeeds | `worker.claim` |
| Another worker claimed first | `worker.claim_race_loss` |
| Dispatcher detects missed heartbeat | `worker.heartbeat_miss` |
| Dead-worker reclaim fires | `worker.reclaim` |
| Spine node starts | `spine.node.{name}.entry` |
| Spine node ends | `spine.node.{name}.exit` |
| Each critic runs | `spine.critic.{critic_name}` |
| Gate interrupt fires | `spine.gate.interrupt` |
| Gate resumes after human decision | `spine.gate.resume` |
| Post-gate side effects written | `spine.post_gate.write` |
| Any LLM call | `llm.call` (model_id, provider, prompt_tokens, completion_tokens, latency_ms) |
| Budget soft threshold crossed | `spine.budget.soft_threshold` |

Important: `llm.call` spans are emitted by the timing wrapper in `talos/llm.py` (built in P3b),
NOT by a PostToolUse hook. PostToolUse hooks are reserved for NEXUS MCP tool call spans (future).

**Escalation webhook**

`TALOS_ESCALATION_WEBHOOK_URL` — read from `talos.toml [alerts]` or env var.

When `gate_outcome = escalate`:
1. Emit `spine.gate.interrupt` span with `payload.outcome = "escalate"`.
2. If webhook URL is configured: HTTP POST within 5 seconds with body:
   ```json
   {
     "event": "gate_escalated",
     "board_id": "...",
     "task_id": "...",
     "run_id": "...",
     "escalated_at": "ISO8601"
   }
   ```
3. If POST fails (timeout or non-2xx): log a warning but do not fail the gate transition.
   Never let webhook failure block the gate state transition.

**PM hooks**

`talos/hooks.py` (new module). PM hooks fire on task lifecycle events. In P3d, only one hook
matters: `on_task_approved` (fires after `gate_outcome = approve` + `post_gate_node` completes).

```python
HookFn = Callable[[dict], Awaitable[None]]

class HookRegistry:
    def register(self, event: str, fn: HookFn): ...
    async def fire(self, event: str, payload: dict): ...
```

Hooks are registered at worker startup from `talos.toml [hooks]` entries. If a hook raises:
log the error but do not re-raise (hooks are fire-and-forget, not task-blocking).

### P3d tests — `talos/tests/test_p3d_observability.py`

Required tests:
1. `test_all_spans_emitted_on_happy_path` — run a full approve flow with stub mode; inspect
   the in-memory span buffer; verify each of the 11 span types in the minimum span set is
   present (at least one of each).
2. `test_escalation_webhook_called` — set a mock webhook URL; trigger `gate_outcome = escalate`;
   verify the webhook received exactly one POST with the correct body fields.
3. `test_webhook_failure_does_not_block_gate` — mock webhook to return HTTP 500; verify gate
   transition still completes and task reaches correct final state.
4. `test_hook_on_task_approved` — register an `on_task_approved` hook; run an approve flow;
   verify the hook was called with the correct payload.
5. `test_span_rls_isolation` — verify spans emitted for board A are not readable in a board B
   Postgres session.
6. `test_llm_call_span_has_latency` — run a flow with TALOS_NEXUS_STUB=1 and a mock LLM;
   verify the `llm.call` span has `latency_ms > 0`.

---

## Omnigent-derived patterns to incorporate

From `docs/upstream/omnigent-notes.md` (research completed, decisions made):

1. **Intersection-only policy stack (ADR-009 alignment):** the dispatcher already has a
   layered tool policy. Make sure the claim loop checks policies in order: session-level first,
   then board-level, then server-level. A DENY at any level is terminal and logged. Do not
   build a new policy framework in P3 — just ensure the existing intersection model is
   correctly sequenced at claim time.

2. **4-axis budget object:** implemented in P3b above (spend_usd, tokens, tool_calls,
   elapsed_seconds). On hard cap: escalate to gate (not crash). On soft threshold: continue
   and emit a `spine.budget.soft_threshold` span.

3. **`network:none` sandbox is the right call:** Omnigent uses a softer intercept-and-transform
   model. ADR-010 chose `network:none` for write-class workers. This is confirmed correct. Do
   not soften the sandbox for P3c.

---

## What NOT to build in P3

These are explicitly deferred. Do not add stubs, placeholders, or TODOs for them unless noted:

| Item | Phase |
|---|---|
| VerifierSpec and `register_verifier()` | P4 |
| `rules` and `rule_ingestion_log` tables | P4 |
| Graph-store adapter abstraction (Neo4j, Apache Age) | P4 |
| pgvector wiring | P4 |
| Redis hot store | P4 |
| Capability pack loading mechanism | P4 |
| CriticSpec stability declaration | P4 |
| Verifier runner implementation | P5 |
| Rule extraction and ingestion pipeline | P5 |
| OTLP exporter | P7 |
| Cockpit span queries and latency views | P7 |
| Multi-channel alerting (Slack, email, thresholds) | P7 |
| Background heartbeat thread | Revisit at P4 |
| Dreaming scheduler (ADR-025 through ADR-028) | P4/P5 |

The `task_spans.otlp_exported_at` column exists in the schema — leave it nullable/null. Do not
wire the OTLP exporter. The column is a P7 hook.

---

## Files to produce

**New source files:**
- `engine/schema-p3.sql`
- `talos/config.py` — model config cascade loader
- `talos/llm.py` — `query()` wrapper with session continuity and `llm.call` span emission
- `talos/spans.py` — `emit_span()` utility, in-memory stub buffer for test mode
- `talos/sandbox.py` — `Sandbox` class with Docker subprocess and bypass mode
- `talos/sandbox/Dockerfile`
- `talos/hooks.py` — `HookRegistry` and PM hook dispatch
- `talos/tests/test_p3a_postgres.py` (5 tests)
- `talos/tests/test_p3b_dispatcher.py` (6 tests)
- `talos/tests/test_p3c_sandbox.py` (5 tests)
- `talos/tests/test_p3d_observability.py` (6 tests)

**Modified files:**
- `talos/worker.py` — PostgresSaver injection, heartbeat, reclaim, multi-worker dispatcher
- `talos/graph/spine.py` — `sdk_session_ids` in SpineState, budget enforcement, span emission
  at node boundaries, model resolution in `read_node` and `deliverable_node`
- `talos/api.py` — no behavioral changes; verify existing tests still pass

**ADR to update:**
- `docs/decisions/ADR-029-agent-sdk-integration.md` — mark action items 1 and 2 as done
  after `sdk_session_ids` is added and `read_node`/`deliverable_node` use `query()`

**New ADR to write:**
- `docs/decisions/ADR-030-budget-enforcement.md` — document the 4-axis budget object, threshold
  semantics, and the decision to route budget exhaustion through the gate (not crash) derived
  from the Omnigent research

---

## Acceptance criteria

P3 is complete when ALL of the following are true:

1. `TALOS_NEXUS_STUB=1 .venv/bin/python -m pytest talos/ -v` passes — all tests including
   the 27 pre-P3 tests plus all new P3 tests.
2. The 5 Agent SDK prototype tests still pass (they are integration tests; ensure the stub guard
   still allows them to run when the `claude` CLI is available).
3. `engine/schema-p3.sql` applied to a fresh Postgres 16 instance produces no errors.
4. A `TALOS_SANDBOX_MODE=none` startup emits the CRITICAL warning to both log and file.
5. `TALOS_WORKER_COUNT=3` starts 3 concurrent worker slots without error.
6. The escalation webhook POST fires exactly once per `gate_outcome = escalate` event.
7. `ADR-030-budget-enforcement.md` is written and marked Accepted.

---

## Test execution command

```bash
TALOS_NEXUS_STUB=1 .venv/bin/python -m pytest talos/ -v -s
```

Docker tests require Docker available. If Docker is unavailable in the environment, the
`REQUIRES_DOCKER` skip marker will skip them cleanly — that is acceptable; document it.

**Do not commit until all non-Docker tests pass.** Docker tests are acceptable as skip-in-CI
with a note in the commit message.
