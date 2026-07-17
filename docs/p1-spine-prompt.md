# TALOS — P1 Gate Spine Prompt

> **Historical note:** this prompt predates the `platform/` → `talos/` rename; current code lives
> in `talos/`. Retained as written for the historical record.

Paste this into a new Claude Code session. Working directory: `/mnt/i/talos/`

---

You are building **Phase 1 of TALOS** — the single-worker vertical slice that proves the Guardian
doctrine end-to-end with the least code possible. P0 is complete: the Postgres schema with RLS is
live, the manifest validator exists, both P0 CI tests pass. Now prove the spine.

**The doctrine:** AI proposes, humans review, deterministic critics gate, and nothing reaches a live
system without a human's approval.

**The proof:** one task → one NEXUS read (over MCP) → one trivial deliverable → one deterministic
critic → authenticated human gate → approved → one idempotent post-gate side-effect.

That path, working end-to-end, is P1 done.

---

## Read these files before writing a single line of code

Read all of them. They are the source of truth. Do not guess at column names, table structures,
session key formats, or gate outcomes — everything is specified.

- `engine/schema.sql` — column names, table structure, `task_status` enum, `task_gate_results`
  shape, the `v_gate_status` view, how `attempt_no` relates to `task_runs`
- `engine/schema-additions.sql` — `attempt_no` column and its unique index on `task_runs`
- `docs/integration/04_build_sequence.md` §4 — the exact 7-step P1 slice and the 6 verification
  checks that define "done"
- `docs/contracts/board-api.md` — the frozen API contract; payload shapes are **[D]** (derived,
  must match exactly); REST paths are **[I]** (illustrative, your choice)
- `docs/decisions/ADR-010-worker-isolation.md` — session key format, scope model
- `docs/decisions/ADR-011-gate-outcomes.md` — the five gate outcomes; P1 implements Approve and
  Reject only
- `pyproject.toml` — existing test deps (`pytest`, `psycopg2-binary`, `testcontainers[postgres]`)
- `platform/validators/capability_manifest.py` — existing code style and structure to match

---

## What P1 builds (in this order)

### Step 1 — Database connection helper

**File:** `platform/db.py`

A thin wrapper around `psycopg2` that:
- Opens a connection from env vars (`TALOS_DB_DSN`, defaulting to
  `postgresql://localhost/talos`)
- Provides a context manager `board_scope(conn, board_id)` that runs
  `SET LOCAL app.board_id = %s` so every statement inside the block executes under RLS.
  Use `SET LOCAL` (not `SET`) so the scope resets at transaction end.
- Provides `get_conn()` for one-off connections.

No ORM. Raw `psycopg2` with `RealDictCursor` for all reads.

### Step 2 — Minimal board API

**File:** `platform/api.py`

A FastAPI application. Implement exactly these five endpoints and nothing more:

```
POST   /boards                      → create board, return {id, name}
POST   /boards/{board_id}/tasks     → create task in board, status=ready
GET    /boards/{board_id}/tasks/{task_id}        → task projection (board-api.md §1)
PATCH  /boards/{board_id}/tasks/{task_id}/status → move status (validate enum)
GET    /boards/{board_id}/tasks/{task_id}/gate   → GateStatus from v_gate_status view
POST   /boards/{board_id}/tasks/{task_id}/gate   → submitGateOutcome (see constraints below)
```

**submitGateOutcome constraints (RT-01 — read ADR-011):**
- `outcome` ∈ `{approve, reject}` (only these two in P1)
- `approved_by` is **always set from the request's `X-Human-Session` header on the server side**.
  It is never a field in the POST body. If the header is absent or equals `service` / `worker` /
  `agent` / `system`, return HTTP 403 with body `{"error": "human session required"}`.
- On approve: set `tasks.approved_at = NOW()`, `tasks.approved_by = <session>`,
  `tasks.status = approved`.
- All gate writes happen inside `board_scope(conn, board_id)`.

**Task status projection** must match `board-api.md §1` exactly — hide `claim_lock`,
`worker_pid`, `session_id`, `idempotency_key`, `model_override`, `last_failure_error`.

### Step 3 — Deterministic critic: `citations_resolvable`

**File:** `platform/critics/citations_resolvable.py`

A pure function — no LLM, no network in the normal path. Signature:

```python
def citations_resolvable(deliverable: dict, nexus_client) -> CriticResult:
    ...
```

Where `CriticResult` is a dataclass: `passed: bool`, `reason: str`, `waivable: bool = True`.

The critic inspects `deliverable["citations"]` — a list of `{"finding_id": str, "status": str}`
items the worker placed there. It passes if and only if every cited finding has
`status == "confirmed"`. If any finding is not `confirmed` (or the field is missing), it fails
with a reason naming the offending finding_id.

In P1, the `nexus_client` is a stub that takes a finding_id and returns a dict with a `status`
field. Wire it so the test can inject a stub that returns `{"status": "confirmed"}` or
`{"status": "proposed"}` as needed.

`waivable = True` here. Safety-class critics (P2) get `waivable = False`. Do not add that
complexity now.

### Step 4 — LangGraph spine graph

**File:** `platform/graph/spine.py`

A LangGraph `StateGraph` with four nodes, executed in order:

```
read_node → deliverable_node → gate_node → post_gate_node
```

**State schema:**
```python
class SpineState(TypedDict):
    board_id: str
    task_id: str
    attempt_no: int
    session_key: str          # "task:{board_id}:{task_id}:{attempt_no}"
    nexus_result: dict        # what tag_context returned
    deliverable: dict         # {"citations": [...], "summary": str}
    critic_results: list      # list of CriticResult
    gate_outcome: str | None  # "approve" | "reject" | None
    approved_by: str | None
```

**`read_node`:** calls `tag_context` over MCP. In CI this is stubbed via an env var
`TALOS_NEXUS_STUB=1` which returns `{"tag": "MOCK_TAG", "status": "confirmed"}`. When the real
MCP is available (`TALOS_NEXUS_STUB` unset), it calls the live tool. Store result in
`state["nexus_result"]`. Do not persist it to Postgres — reference data only (ADR-003/007).

**`deliverable_node`:** emits a trivial structured deliverable that cites the NEXUS read:
```python
state["deliverable"] = {
    "citations": [{"finding_id": state["nexus_result"].get("tag", "unknown"), "status": state["nexus_result"].get("status", "proposed")}],
    "summary": f"Tag context retrieved: {state['nexus_result']}"
}
```
Runs `citations_resolvable` critic. Stores results in `state["critic_results"]`. Moves task
to `review` status via a direct Postgres UPDATE inside `board_scope`. If the critic fails, task
stays in `review` — the gate blocks forward progress until it passes.

**`gate_node`:** contains `interrupt()` **and nothing else**. All side-effects go in
`post_gate_node`. This is the critical constraint from the LangGraph interrupt() re-entry behavior:
the gate node body re-executes on resume; anything stateful before the interrupt fires twice.

```python
def gate_node(state: SpineState) -> SpineState:
    outcome = interrupt({
        "task_id": state["task_id"],
        "deliverable": state["deliverable"],
        "critic_results": [asdict(r) for r in state["critic_results"]],
    })
    return {**state, "gate_outcome": outcome["outcome"], "approved_by": outcome["approved_by"]}
```

**`post_gate_node`:** all side-effects, in one transaction. This node must be idempotent.
Runs inside `board_scope(conn, board_id)`:

```python
# 1. Insert idempotency row first — UNIQUE on (task_id, attempt_no)
#    If this INSERT fails with UniqueViolation, the node has already run. Catch and return.
INSERT INTO task_gate_results
    (task_id, board_id, attempt_no, outcome, approved_by, approved_at, critic_results)
VALUES (%s, %s, %s, %s, %s, NOW(), %s::jsonb)
ON CONFLICT (task_id, attempt_no) DO NOTHING;

# 2. Check if we wrote the row (rowcount == 0 means already ran → idempotent no-op)
if cursor.rowcount == 0:
    return state  # already ran, no-op

# 3. Append to task_events (append-only audit log)
INSERT INTO task_events (task_id, board_id, run_id, event_type, payload)
VALUES (%s, %s, %s, 'gate_outcome', %s::jsonb);

# 4. Update task status
UPDATE tasks SET status = 'approved', approved_at = NOW(), approved_by = %s
WHERE id = %s AND board_id = %s;
```

All four statements in one `conn` block. If any fails, the transaction rolls back and no partial
state is written.

**`approved_by` in `post_gate_node`** comes from `state["approved_by"]` — which was set in
`gate_node` from the interrupt payload. The interrupt payload is populated by the gate API
endpoint (Step 2), which extracts it from the authenticated session header. The graph node never
reads `approved_by` from a request body.

**Graph construction:**
```python
builder = StateGraph(SpineState)
builder.add_node("read_node", read_node)
builder.add_node("deliverable_node", deliverable_node)
builder.add_node("gate_node", gate_node)
builder.add_node("post_gate_node", post_gate_node)
builder.add_edge(START, "read_node")
builder.add_edge("read_node", "deliverable_node")
builder.add_edge("deliverable_node", "gate_node")
builder.add_edge("gate_node", "post_gate_node")
builder.add_edge("post_gate_node", END)

checkpointer = PostgresSaver(...)  # or MemorySaver() in tests
graph = builder.compile(checkpointer=checkpointer, interrupt_before=["gate_node"])
```

Use `MemorySaver` in tests. Use `PostgresSaver` from `langgraph-checkpoint-postgres` in
production. Add `langgraph`, `langgraph-checkpoint-postgres` to `pyproject.toml [test]` extras.

### Step 5 — Single-worker claim loop

**File:** `platform/worker.py`

A simple `claim_and_run(board_id: str)` function (not a dispatcher — no claim-racing, no
heartbeat, no reclaim — those are P3).

```python
def claim_and_run(board_id: str, task_id: str):
    # 1. Fetch task, assert status == "ready"
    # 2. Claim: UPDATE tasks SET status='running', session_id=<session_key> WHERE id=task_id
    # 3. Mint session key: "task:{board_id}:{task_id}:{attempt_no}"
    #    attempt_no = INSERT INTO task_runs(task_id, board_id, ...) RETURNING attempt_no
    # 4. Run the spine graph: graph.invoke(initial_state, config={"thread_id": session_key})
    # 5. Return the thread_id so the caller can resume after the gate
```

The `thread_id` for LangGraph checkpoint purposes is the session key. The caller (the test or the
API route that triggers the worker) must save this to resume the graph after the human gate fires.

---

## File layout

Create these files and no others:

```
platform/
  db.py
  api.py
  worker.py
  graph/
    __init__.py
    spine.py
  critics/
    __init__.py
    citations_resolvable.py
  tests/
    test_spine.py
```

Do not create `platform/tests/conftest.py` unless the Postgres container fixture requires it
(the existing one is in `engine/tests/conftest.py` — you may reuse its pattern but do not import
from it directly; duplicate the fixture in `platform/tests/conftest.py` if needed).

---

## Tests — all six P1 verification checks

**File:** `platform/tests/test_spine.py`

Write one test per check. All six must pass before P2 begins.

```
1. test_spine_happy_path
   Seed board + task. Run worker (TALOS_NEXUS_STUB=1). Verify task is in review.
   Simulate human approval via the gate API endpoint (X-Human-Session: thunt).
   Resume graph. Verify: task.status == approved, one task_gate_results row,
   one task_events row with event_type=gate_outcome.

2. test_nexus_stub_read
   Run read_node in isolation. Assert nexus_result is populated and nothing
   is written to task_events or task_gate_results (reference data, not persisted).

3. test_gate_rejects_non_human_caller
   POST to /boards/{id}/tasks/{id}/gate with no X-Human-Session header → HTTP 403.
   POST with X-Human-Session: worker → HTTP 403.
   POST with X-Human-Session: thunt → HTTP 200.
   Assert approved_by in task_gate_results equals "thunt", not any request body field.

4. test_post_gate_idempotency
   Run post_gate_node twice with the same (task_id, attempt_no).
   Assert exactly one row in task_gate_results and exactly one row in task_events.
   (Second execution hits ON CONFLICT DO NOTHING and returns early.)

5. test_rls_isolation  [already passing — import and re-assert as a P1 smoke test]
   With board_scope set to board-A, SELECT from tasks returns 0 rows for board-B data.

6. test_critic_blocks_unconfirmed_citation
   Construct a deliverable with a citation where status == "proposed".
   Run citations_resolvable. Assert result.passed is False.
   Simulate what deliverable_node does: task stays in review (no approved_at set).
```

Use `MemorySaver` for the graph checkpointer in all tests. Use `testcontainers[postgres]` for
the Postgres instance (same pattern as `engine/tests/conftest.py`).

---

## What P1 deliberately excludes — do not build these

- Dispatcher (claim-racing, heartbeat, breaker, checkpoint reclaim) — P3
- Memory stores (Neo4j, pgvector, Redis) — P4
- Five-outcome gate (Waive, Edit-inline, Escalate) — P2
- Cockpit / view — P7
- Write profile / sim-execute — P6
- The full `board-api.md` surface (widget upsert, space versions, Gantt) — P7

If you find yourself reaching for any of these, stop and ask.

---

## Rules

- Read every file listed above before writing. Column names and payload shapes come from the
  schema and contracts — do not guess.
- `gate_node` must contain only `interrupt()`. No Postgres writes, no critic runs, no logging.
  Everything stateful goes in `post_gate_node`. This is not optional — it prevents double-writes
  on LangGraph resume.
- `approved_by` is always set server-side from `X-Human-Session`. Never read it from a POST body.
- Every Postgres write in `post_gate_node` happens in a single transaction. Partial writes are
  not acceptable.
- Add new dependencies to `pyproject.toml` under `[project.optional-dependencies] test`. Do not
  add application (non-test) deps there — create a `[project.optional-dependencies] app` section
  for `fastapi`, `uvicorn`, `psycopg2-binary`, `langgraph`, `langgraph-checkpoint-postgres`.
- Do not modify `engine/schema.sql` or any existing file in `engine/tests/`. P0 is sealed.
- Match the code style of `platform/validators/capability_manifest.py` (dataclasses, type hints,
  no third-party ORMs, no magic).
- After writing all files, run `python -m pytest platform/tests/test_spine.py -v` and show the
  output. If Docker is not running, say so explicitly.

---

## P1 is done when

- [ ] All 6 tests pass
- [ ] `GET /boards/{id}/tasks/{id}` returns the correct projection (no worker-internal columns)
- [ ] `POST /boards/{id}/tasks/{id}/gate` with no `X-Human-Session` returns HTTP 403
- [ ] Running `post_gate_node` twice with the same `(task_id, attempt_no)` produces exactly one
      `task_gate_results` row and one `task_events` row
- [ ] The spine graph can be resumed after `interrupt()` by replaying with the gate outcome
- [ ] `TALOS_NEXUS_STUB=1` runs the full path without a live MCP connection

Start by reading the files in the order listed, then implement Step 1 through Step 5.
