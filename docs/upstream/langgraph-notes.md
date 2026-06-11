# LangGraph — Technical Deep-Dive Notes

> Research date: 2026-06-11  
> Source: `langchain-ai/langgraph` — MIT License  
> Purpose: Evaluate LangGraph as the TALOS execution engine. Patterns + code eligible for adoption.

---

## Executive Summary

LangGraph is a low-level orchestration framework built on the **Pregel / Bulk Synchronous Parallel** execution model. It wraps a `StateGraph` builder around typed channels, compiling down to a `Pregel` engine that handles checkpointing, concurrency, streaming, and human-in-the-loop interrupts out of the box.

**The headline fit for TALOS:** LangGraph's `interrupt()` + `Command(resume=...)` is a direct, battle-tested implementation of the TALOS 5-way gate. Its Postgres checkpointer, time-travel API, and streaming modes give TALOS capabilities that would take months to hand-roll correctly.

**The critical gotcha:** When a node calls `interrupt()` and is later resumed, **the entire node re-executes from the start.** Any side effects (DB writes, API calls, audit-log entries) placed before the `interrupt()` call fire again on Approve. This is a hard architectural constraint, not a bug.

| Feature | Hand-Rolled Hermes-based Dispatcher | LangGraph |
| :--- | :--- | :--- |
| Checkpointing | Manual checkpoint rows in Postgres | Built-in `Checkpoint` TypedDict + langgraph-checkpoint-postgres |
| State merging | Manual dict merge with race conditions | Typed channels with reducers (atomic per superstep) |
| Human-in-the-loop | Custom pause/resume logic + DB rows | `interrupt()` + `Command(resume=...)` |
| Streaming | Manual SSE event emission | 7 stream modes built-in |
| Time-travel | If you saved the state rows | `get_state_history()` + fork via checkpoint_id |
| Fan-out / fan-in | Manual asyncio gather | `Send` API + channel reducers |
| Subgraph isolation | Manual scoping | Namespace isolation + state pass-through |
| Retry / timeout | Ad-hoc try/except | Retry policies + timeout policies per node |
| Type safety | Loose dicts | TypedDict + Pydantic schemas |

**Recommendation for TALOS:** LangGraph is the right execution engine. The Strategy Ladder maps naturally to a StateGraph. The 5-way gate maps to `interrupt()` + `Command`. Replace the hand-rolled Hermes dispatcher with LangGraph; keep the Hermes Postgres schema for the board view (they can coexist — LangGraph checkpoints and Hermes task rows are separate concerns).

---

## 1. Core Graph Model

**File:** `libs/langgraph/langgraph/graph/state.py`

### StateGraph

```python
class StateGraph(Generic[StateT, ContextT, InputT, OutputT])
```

`StateGraph` is a **builder** that compiles to a `Pregel` execution engine. It is not itself executable.

**State (`StateT`):** A TypedDict or Pydantic BaseModel. Each key maps to a **channel**. Optional **reducer annotation** on a key merges concurrent writes:

```python
class State(TypedDict):
    messages: Annotated[list[str], operator.add]  # reducers merge concurrent writes
    status: str                                    # LastValue: last writer wins
    gate_result: str | None                        # LastValue
```

**Context (`ContextT`, optional):** Immutable runtime values injected at invocation, not stored in checkpoint:

```python
class Context(TypedDict):
    board_id: str
    user_id: str
    db_conn: object
```

**Channels (what State keys compile to):**

| Channel Type | Behavior | Use Case |
| :--- | :--- | :--- |
| `LastValue[T]` | Stores most recent value | Most state keys |
| `Topic[T]` | PubSub accumulator | Multi-writer event streams |
| `BinaryOperatorAggregate` | Folds updates via operator (e.g., `+`) | Aggregating parallel results |
| `EphemeralValue` | Single-use, cleared each step | Signals between nodes |

### Nodes

```python
def my_node(state: State, config: RunnableConfig) -> dict[str, Any] | State | Command:
    # Returns a dict of state updates, a full State replacement, or a Command
    return {"status": "done"}
```

Nodes can be sync or async. Added via:

```python
graph.add_node("my_node", my_node)
graph.add_node("my_node", my_node, input_schema=MyInputSchema)  # custom input schema
graph.add_node("my_node", my_node, defer=True)  # execute at end of step (post side-effects)
```

### Edges

```python
graph.add_edge(START, "plan")           # Entry point
graph.add_edge("plan", "gate")          # Deterministic sequence
graph.add_edge(["worker_a", "worker_b"], "merge")  # Join: wait for both
graph.add_edge("execute", END)          # Terminal
```

---

## 2. Conditional Routing

**File:** `libs/langgraph/langgraph/graph/_branch.py`

```python
def router(state: State) -> Literal["approve", "reject", "escalate"]:
    if state["gate_result"] == "APPROVE":
        return "approve"
    elif state["gate_result"] == "ESCALATE":
        return "escalate"
    return "reject"

graph.add_conditional_edges("gate_node", router, {
    "approve": "execute",
    "reject": "rejection_handler",
    "escalate": "escalation_node",
})
```

If the router's return type uses `Literal` type hints, LangGraph infers the `path_map` automatically — name your nodes to match the string values.

**Fan-out routing via `Send`:**

```python
def map_reducer(state: State):
    return [
        Send("process_worker", {"task": t, "worker_id": i})
        for i, t in enumerate(state["subtasks"])
    ]

graph.add_conditional_edges("dispatch", map_reducer)
```

All `Send`-dispatched tasks execute in parallel; their results aggregate via the channel reducer on the target key.

---

## 3. Human-in-the-Loop — The 5-Way Gate

**File:** `libs/langgraph/langgraph/types.py`

### `interrupt()`

```python
def gate_node(state: State) -> dict:
    # Present the plan for human review
    decision = interrupt({
        "plan": state["plan"],
        "task_id": state["task_id"],
        "reviewer_hint": "Review the proposed execution plan"
    })
    # Execution pauses here. Resume with Command(resume=...).
    # WARNING: entire gate_node re-executes from the top when resumed.
    return {"gate_result": decision}
```

On the first call, `interrupt()` raises `GraphInterrupt`. Execution pauses; the checkpoint is written. The interrupt value (the dict passed to `interrupt()`) is surfaced in the stream output under `__interrupt__`.

On resume, the node **re-executes from the start** and `interrupt()` returns the resume value (`Command.resume`).

### TALOS 5-Way Gate Mapping

| Gate Outcome | LangGraph Action |
| :--- | :--- |
| **Approve** | `Command(resume="APPROVE")` → gate_node returns, routes to execute |
| **Reject** | `Command(goto="rejection_handler", update={"rejection_reason": "..."})` |
| **Waive** | `Command(resume="WAIVE")` → gate_node returns, routes to execute with waiver flag |
| **Edit** | `Command(update={"plan": new_plan}, goto="re_plan")` → mutate state + restart plan |
| **Escalate** | `Command(goto="escalation_node")` → separate escalation path |

**Safety rule for TALOS:** Put ALL side effects (audit log writes, operator notifications) in a **separate node after** the gate node, not inside the gate node before `interrupt()`. Otherwise they fire twice (once on pause, once on resume).

```python
# WRONG — fires twice:
def gate_node(state):
    write_audit_log("gate opened")      # ← fires on initial call AND on resume
    decision = interrupt(state["plan"])
    return {"gate_result": decision}

# CORRECT — gate node is pure:
def gate_node(state):
    decision = interrupt(state["plan"])
    return {"gate_result": decision}

# Audit write is in a separate node:
def post_gate_node(state):
    write_audit_log(state["gate_result"])
    return {}

graph.add_edge("gate", "post_gate")
```

### Setting Interrupt Points at Compile Time

```python
graph.compile(
    checkpointer=postgres_saver,
    interrupt_before=["gate_node"],    # Pause before (human sees pre-gate state)
    interrupt_after=["plan_node"],     # Pause after (human reviews plan output)
)
```

---

## 4. Checkpointing and Persistence

**File:** `libs/checkpoint/langgraph/checkpoint/base/__init__.py`

### Checkpoint Structure

```python
class Checkpoint(TypedDict):
    v: int                              # Schema version (currently 4)
    id: str                             # Monotonic checkpoint ID
    ts: str                             # ISO 8601 timestamp
    channel_values: dict[str, Any]      # Serialized state
    channel_versions: ChannelVersions   # Per-channel version counter
    versions_seen: dict[str, ChannelVersions]  # Per-node: which channel versions it read
    updated_channels: list[str] | None
```

**`channel_versions`** tracks when each channel was last updated. A node only re-executes if a channel it subscribes to has a newer version than what it last saw. This is the **incremental execution model** — unchanged parts of the graph don't re-run.

### Checkpointer Interface

```python
class BaseCheckpointSaver:
    def get_tuple(self, config) -> CheckpointTuple | None
    def list(self, config, *, filter=None, before=None, limit=None)
    def put(self, config, checkpoint, metadata, new_versions) -> RunnableConfig
    def put_writes(self, config, writes, task_id)
    def delete_thread(self, thread_id)
    def copy_thread(self, source, target)
    def prune(self, thread_ids, strategy="keep_latest")
```

### Backends

| Backend | Package | Use Case |
| :--- | :--- | :--- |
| In-memory | `langgraph.checkpoint.memory.InMemorySaver` | Dev / testing |
| SQLite | `langgraph-checkpoint-sqlite` | Single-machine, persistent |
| **PostgreSQL** | `langgraph-checkpoint-postgres` | **Production — TALOS choice** |
| Redis | `langgraph-checkpoint-redis` | High-throughput, session cache |

**Durability modes:**

| Mode | When checkpoint is written | Use Case |
| :--- | :--- | :--- |
| `"sync"` | Before next step starts (blocks) | Human-in-the-loop (safety critical) |
| `"async"` | Background task (default) | High-throughput |
| `"exit"` | Only on graph completion | Analytics / non-critical |

**TALOS recommendation:** `"sync"` for any graph that contains a gate node. The Postgres checkpoint row IS the audit trail.

### Thread ID and Task ID Mapping

LangGraph checkpoints are keyed by `thread_id` (in `config["configurable"]["thread_id"]`). For TALOS:
- `thread_id` = `task.id` (Hermes task UUID)
- Each task has its own execution timeline in the checkpoint store
- Gate decisions are written as checkpoint metadata with `source: "update"`

---

## 5. Subgraphs

Subgraphs allow complex nesting — a compiled StateGraph can be used as a node in a parent graph.

```python
# Worker subgraph
worker_graph = StateGraph(WorkerState)
worker_graph.add_node("analyze", analyze_node)
worker_graph.add_node("draft", draft_node)
worker_graph.add_edge(START, "analyze")
worker_graph.add_edge("analyze", "draft")
worker_graph.add_edge("draft", END)
worker_compiled = worker_graph.compile()

# Coordinator graph uses worker as a node
coordinator = StateGraph(CoordinatorState)
coordinator.add_node("worker", worker_compiled)
coordinator.add_node("gate", gate_node)
coordinator.add_edge(START, "worker")
coordinator.add_edge("worker", "gate")
coordinator.add_edge("gate", END)
```

**State pass-through:** Subgraph input is hydrated from parent state keys matching the subgraph's `input_schema`. Subgraph output merges back into parent state via the subgraph's `output_schema`.

**Namespace isolation:** Each subgraph execution gets its own checkpoint namespace:
`"parent_thread_id::worker_node::<invocation_id>::analyze"`. Time-travel works independently per subgraph.

**Checkpointer inheritance:** Subgraph inherits parent's checkpointer unless overridden at compile time.

---

## 6. Streaming

**File:** `libs/langgraph/langgraph/pregel/main.py`

Seven stream modes, composable:

```python
for event in graph.stream(input, config, stream_mode=["values", "updates"]):
    print(event)
```

| Mode | Output | Use Case |
| :--- | :--- | :--- |
| `"values"` | Full state after each step | Cockpit status panel |
| `"updates"` | `{node_name: {changed_keys}}` | Efficient incremental updates |
| `"messages"` | LLM token-by-token chunks | Real-time chat streaming |
| `"tasks"` | Task start/result events | Debugging, progress tracking |
| `"checkpoints"` | Checkpoint payload per step | Audit trail display |
| `"custom"` | Node-emitted via `StreamWriter` | TALOS gate events, NEXUS progress |
| `"debug"` | tasks + checkpoints combined | Development |

**Custom stream events (TALOS use):**

```python
def nexus_analysis_node(state, *, stream_writer):
    for tag in state["tags_to_analyze"]:
        result = nexus.tag_context(tag)
        stream_writer({"type": "nexus_progress", "tag": tag, "result": result})
    return {"analysis_complete": True}
```

The cockpit subscribes to the SSE stream and updates the progress widget in real time.

---

## 7. Time-Travel and Replay

**File:** `libs/langgraph/langgraph/pregel/main.py:1391-2556`

### List Execution History

```python
config = {"configurable": {"thread_id": task_id}}

# All checkpoints for this task, newest first
for snapshot in graph.get_state_history(config, limit=20):
    print(snapshot.metadata.step, snapshot.metadata.source)
    print(snapshot.values)  # State at that point
    print(snapshot.next)    # Nodes that would run next
```

### Resume From Prior Checkpoint (Replay)

```python
# Get the checkpoint before the gate decision
history = list(graph.get_state_history(config))
pre_gate_snapshot = next(s for s in history if "gate" in s.next)

# Re-run forward from that point
for output in graph.stream(None, pre_gate_snapshot.config):
    print(output)
```

### Fork (Divergent Branch)

```python
# Inject a modified plan and create a new execution branch
new_config = graph.update_state(
    config,
    values={"plan": "revised plan after human edit"},
    as_node="plan_node"         # Pretend this node wrote it
)
# new_config points to a new checkpoint with metadata source="update"

for output in graph.stream(None, new_config):
    print(output)
```

**TALOS cockpit scrubber:** `get_state_history()` drives the timeline UI. The scrubber position selects a `StateSnapshot.config`; pressing "replay" calls `stream(None, snapshot.config)`.

---

## 8. The `Send` API — Fan-Out / Fan-In

**File:** `libs/langgraph/langgraph/types.py`

```python
class Send:
    node: str       # Target node name
    arg: Any        # Input state for that node
    timeout: TimeoutPolicy | None
```

**Map-reduce pattern:**

```python
class State(TypedDict):
    tags_to_trace: list[str]
    trace_results: Annotated[list[dict], operator.add]  # reducer accumulates

def dispatch_traces(state: State):
    return [
        Send("trace_single_tag", {"tag": tag, "plc_id": state["plc_id"]})
        for tag in state["tags_to_trace"]
    ]

async def trace_single_tag(state: dict) -> dict:
    result = await nexus.tag_context(state["plc_id"], state["tag"])
    return {"trace_results": [result]}

graph.add_conditional_edges("dispatch", dispatch_traces)
graph.add_node("trace_single_tag", trace_single_tag)
graph.add_edge("trace_single_tag", "merge")
```

All `Send` tasks run in parallel. Results accumulate in `trace_results` via `operator.add`. The `merge` node sees the combined list.

---

## 9. The `Command` Primitive

**File:** `libs/langgraph/langgraph/types.py`

`Command` gives nodes explicit control over routing, state updates, and resumes:

```python
class Command(Generic[N]):
    graph: str | None          # None = current; Command.PARENT = parent graph
    update: Any | None         # State update dict
    resume: dict | Any | None  # Resume value for interrupt()
    goto: str | list[str] | Send | ...  # Next node(s)
```

**Use patterns:**

```python
# 1. Resume interrupt with approval
return Command(resume="APPROVE")

# 2. Route to rejection with reason
return Command(goto="rejection_handler", update={"rejection_reason": "plan too risky"})

# 3. Edit state and re-plan
return Command(update={"plan": edited_plan}, goto="plan_node")

# 4. Parallel dispatch
return Command(goto=["worker_a", "worker_b"])

# 5. Return to parent from subgraph
return Command(graph=Command.PARENT, update={"subgraph_result": state["output"]}, goto="next_step")
```

---

## 10. LangGraph Platform / Server

**Not in this repo.** The self-hostable/cloud LangGraph Platform is **closed-source commercial software**.

**What IS in the repo:**
- `libs/cli/` — `langgraph dev` for local development
- `libs/sdk-py/` — Python client SDK for the hosted Platform
- `libs/langgraph/langgraph/pregel/remote.py` — `RemoteGraph` client for invoking graphs on Platform

**TALOS implication:** Do not depend on LangGraph Platform. Run LangGraph as a Python library inside TALOS's own FastAPI server. The LangGraph checkpointer (Postgres) integrates directly.

---

## 11. Architecture and Key Classes

### Execution Flow

```
StateGraph.compile(checkpointer)
  → CompiledStateGraph (Pregel subclass)
  
graph.stream(input, config) / graph.ainvoke(input, config)
  [1] Load checkpoint (if thread_id in config and prior checkpoint exists)
  [2] Hydrate channel values from checkpoint
  [3] Plan: determine which nodes to run (compare versions_seen vs channel_versions)
  [4] Execute: run ready nodes in parallel (up to max_concurrency)
  [5] Update: apply writes to channels, increment channel_versions
  [6] Checkpoint: checkpointer.put() (sync/async per durability setting)
  [7] Emit stream events (per stream_mode)
  [8] Repeat from [3] until no nodes scheduled or recursion limit
```

### Key Classes

| Class | File | Role |
| :--- | :--- | :--- |
| `StateGraph` | `graph/state.py` | Builder API |
| `CompiledStateGraph` | `graph/state.py` | Executable (inherits Pregel) |
| `Pregel` | `pregel/main.py` | Core BSP execution engine |
| `BaseChannel` | `channels/base.py` | State channel abstraction |
| `BaseCheckpointSaver` | `checkpoint/base/__init__.py` | Checkpoint storage interface |
| `Checkpoint` | `checkpoint/base/__init__.py` | State snapshot TypedDict |
| `Command[N]` | `types.py` | Explicit routing + resume |
| `Send` | `types.py` | Fan-out packet (node + arg) |
| `Interrupt` | `types.py` | Human-in-the-loop pause |
| `ToolNode` | `prebuilt/tool_node.py` | Prebuilt tool-calling node |

### Tech Stack

| Layer | Choice |
| :--- | :--- |
| Language | Python 3.11+ |
| Type validation | Pydantic 2 + TypedDict |
| Async | asyncio (native) |
| LLM interface | LangChain core Runnable |
| Hashing | xxHash (fast version tracking) |
| Checkpointer | langgraph-checkpoint-postgres (TALOS) |

---

## 12. Critical Gotchas for TALOS

### Gotcha 1 — Node Re-Execution on Resume (Most Important)

When `interrupt()` pauses a node and `Command(resume=...)` resumes it, **the entire node function re-executes from line 1.** Any code before `interrupt()` fires twice.

**Pattern:** Move all side effects (DB writes, notifications) to a **separate node** that runs AFTER the gate node, not inside it.

### Gotcha 2 — Channel Reducer Must Be Order-Independent

If using `Annotated[list, operator.add]` for a multi-writer channel, concurrent writes can arrive in any order. The reducer must be commutative and associative, or results are non-deterministic.

### Gotcha 3 — Checkpointing Overhead at Scale

With `durability="sync"` and Postgres, every step incurs a network round-trip. For high-throughput steps (trace 50 tags in parallel), use `durability="async"` except for the gate node itself.

### Gotcha 4 — Type Hint Inference for Conditional Edges

If a router returns `Literal["left", "right"]` with no explicit `path_map`, LangGraph infers that `"left"` routes to a node named `"left"`. Name nodes to match the literal values, or always provide an explicit `path_map` dict.

### Gotcha 5 — LangGraph Platform Not Needed

TALOS runs LangGraph as an embedded Python library. No LangGraph Platform account required. The `langgraph-checkpoint-postgres` package plus TALOS's own FastAPI/SSE layer replaces the Platform's hosting function.

---

## 13. TALOS Integration Design

### Strategy Ladder as a StateGraph

```python
class TalosState(TypedDict):
    task_id: str
    task_description: str
    triage_result: dict | None
    research_findings: list[dict]
    plan: str | None
    gate_result: str | None
    gate_waiver_reason: str | None
    execution_log: Annotated[list[dict], operator.add]
    crystallized_skill: dict | None

ladder = StateGraph(TalosState)
ladder.add_node("triage",       triage_node)
ladder.add_node("research",     research_node)         # calls nexus_context_map()
ladder.add_node("plan_relay",   plan_relay_node)
ladder.add_node("gate",         gate_node)             # interrupt() here
ladder.add_node("post_gate",    post_gate_node)        # side effects after gate
ladder.add_node("execute",      execute_node)
ladder.add_node("crystallize",  crystallize_node)
ladder.add_node("rejection",    rejection_handler)
ladder.add_node("escalation",   escalation_handler)

ladder.add_edge(START, "triage")
ladder.add_edge("triage", "research")
ladder.add_edge("research", "plan_relay")
ladder.add_edge("plan_relay", "gate")
ladder.add_edge("gate", "post_gate")
ladder.add_conditional_edges("post_gate", gate_router, {
    "execute": "execute",
    "rejection": "rejection",
    "escalation": "escalation",
    "re_plan": "plan_relay",
})
ladder.add_edge("execute", "crystallize")
ladder.add_edge("crystallize", END)

compiled = ladder.compile(
    checkpointer=PostgresSaver(conn_string),
    interrupt_before=["gate"],
)
```

### Connecting to the Hermes Board

LangGraph checkpoints and Hermes task rows coexist in the same Postgres database. When a LangGraph step completes:
1. LangGraph writes its checkpoint (via checkpointer).
2. A TALOS adapter node writes a corresponding row to `task_events` (Hermes event log).
3. The Hermes kanban widget reads from `task_events`; the LangGraph state is the execution truth.

The board sees task status changes; LangGraph drives the actual execution. They are loosely coupled through the `task_id` / `thread_id` join.

### 5-Way Gate Implementation

```python
def gate_node(state: TalosState) -> dict:
    # Pure: no side effects before interrupt
    decision = interrupt({
        "task_id": state["task_id"],
        "plan": state["plan"],
        "research_findings": state["research_findings"],
    })
    return {"gate_result": decision.get("outcome"), "gate_waiver_reason": decision.get("reason")}

def post_gate_node(state: TalosState) -> dict:
    # Side effects happen here (after gate, before routing)
    write_audit_log(state["task_id"], state["gate_result"])
    if state["gate_result"] == "WAIVE":
        write_waiver_record(state["gate_waiver_reason"])
    return {}

def gate_router(state: TalosState) -> str:
    match state["gate_result"]:
        case "APPROVE" | "WAIVE":  return "execute"
        case "REJECT":             return "rejection"
        case "ESCALATE":           return "escalation"
        case "EDIT":               return "re_plan"
```

The cockpit sends the gate decision by calling:
```python
graph.stream(
    Command(resume={"outcome": "APPROVE", "reason": None}),
    {"configurable": {"thread_id": task_id}}
)
```
