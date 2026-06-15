# ADR-020: Dead-worker reclaim — env-var-configurable thresholds; per-task absolute ceiling

**Status:** Accepted
**Date:** 2026-06-14
**Deciders:** Hunt Integrative Solutions LLC

## Context

P3a implements the dead-worker reclaim mechanism: if a worker's heartbeat goes stale past a
threshold, the in-flight task is released back to the queue and a new `task_runs` row is minted
(`attempt_no += 1`). The open questions before P3a:

1. Are heartbeat intervals and miss thresholds hardcoded constants or operator-configurable?
2. What are the initial values?
3. How does the system handle long-running operations (Graphiti ingest, slow model calls,
   PageRank) that may go silent on heartbeat and trigger a false reclaim (RT-27)?

The schema already has `tasks.max_runtime_seconds`, `task_runs.max_runtime_seconds`,
`tasks.last_heartbeat_at`, and `task_runs.last_heartbeat_at`, establishing that per-task and
per-run time bounds are schema-supported.

## Decision

**Heartbeat intervals and miss thresholds are env-var-configurable with Blueprint-specified
defaults. Long-running operations are accommodated by setting a higher `tasks.max_runtime_seconds`.
The elapsed-time ceiling is advisory (backup to the heartbeat mechanism).**

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `TALOS_HEARTBEAT_INTERVAL_S` | `30` | Worker emits a heartbeat every N seconds |
| `TALOS_RECLAIM_AFTER_MISSES` | `3` | Dispatcher reclaims after N consecutive missed heartbeats |

Default reclaim window: 30s × 3 = **90 seconds** (local). For edge deployments where the
Blueprint specifies 120s heartbeat: set `TALOS_HEARTBEAT_INTERVAL_S=120` → 360s reclaim window.

### Claim-time ceiling copy

At claim time, P3a copies `tasks.max_runtime_seconds` into `task_runs.max_runtime_seconds`. This
gives each run row an absolute ceiling independent of the task's potentially-changed value after
claim.

### Reclaim trigger

The dispatcher uses heartbeat misses as the **primary** reclaim signal:

```
consecutive_misses = (NOW() - last_heartbeat_at) / TALOS_HEARTBEAT_INTERVAL_S
if consecutive_misses >= TALOS_RECLAIM_AFTER_MISSES → reclaim
```

`task_runs.max_runtime_seconds` is an **advisory ceiling** — a last-resort backstop used only if
the heartbeat mechanism itself fails (e.g., the dispatcher process restarts and loses heartbeat
state). It is not the primary reclaim trigger.

### Long-running operations (RT-27)

Operations such as Graphiti ingest (15+ LLM calls), slow DeepSeek batches, and PageRank on a
large graph can take 3–10+ minutes, exceeding the default 90s reclaim window. The solution is
**operator-configured `tasks.max_runtime_seconds`** for long-running tasks, giving the worker
more runway before the ceiling fires. Operators who create tasks expected to run long must set
this column explicitly. There is no background heartbeat thread in P3a — the heartbeat fires at
LangGraph node boundaries.

## Options considered

- **A — Hardcoded named constants.** Simple, but requires a code change to tune for production.
  Rejected: operators need to adjust for edge vs. local deployments without redeployment.
- **B — Env-var-configurable (chosen).** Blueprint values as defaults; operators tune via env
  vars. Named constants remain in the source as fallback defaults.
- **C — Background heartbeat thread.** A `threading.Thread` fires heartbeat independently of
  main execution, preventing false reclaims during long blocking calls. Rejected for P3a:
  the worker is synchronous; adding thread safety has non-trivial complexity. Revisit in P4 if
  false reclaims at real workloads require it.

## Consequences

- **Easier:** operators tune intervals without code changes; per-task ceiling is already in schema.
- **Harder:** long-running tasks require explicit `max_runtime_seconds` on each task; operators
  who forget this will see false reclaims.
- **Revisit:** background heartbeat thread if false reclaims become a real operational problem
  at P4+ workloads. If added, it must handle the synchronous worker safely.

## What this closes

- Closes P3a's reclaim threshold design.
- Documents the RT-27 resolution (per-task ceiling, not background thread, in P3a).

## Action items

1. [ ] Add `TALOS_HEARTBEAT_INTERVAL_S` and `TALOS_RECLAIM_AFTER_MISSES` to the worker
   startup config loader.
2. [ ] Copy `tasks.max_runtime_seconds → task_runs.max_runtime_seconds` at claim time.
3. [ ] Implement the consecutive-miss reclaim check in the P3a dispatcher loop.
4. [ ] Document that long-running tasks must set `tasks.max_runtime_seconds` explicitly in
   the operator guide.
