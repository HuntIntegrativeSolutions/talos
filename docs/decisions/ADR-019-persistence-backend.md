# ADR-019: Persistence backend — Postgres is a hard requirement; PostgresSaver injected at startup

**Status:** Accepted
**Date:** 2026-06-14
**Deciders:** Hunt Integrative Solutions LLC

## Context

P1 used `MemorySaver` as the LangGraph checkpointer. `MemorySaver` is in-process and volatile:
state is lost on restart, which means a crashed worker cannot resume from its last checkpoint. P3a
replaces `MemorySaver` with `PostgresSaver` to enable crash recovery.

The question before P3a: is Postgres a hard runtime requirement, or must the checkpointer be
pluggable (e.g., SQLite for a "lite" deployment with no Postgres server)?

The hub-and-spoke deployment model specifies that even thin edges run Postgres and schema-sync
upward to the mothership. The board schema, RLS, gate results, task DAG, and event log all require
Postgres. Making the checkpointer swappable would require abstracting all of those as well — that
is not planned, and no deployment scenario has been identified that lacks Postgres.

## Decision

**Postgres is a hard requirement for all TALOS deployments. `PostgresSaver` is the only
checkpointer. There is no SQLite fallback and no checkpointer plugin interface.**

### Wiring

`PostgresSaver` is constructed by the worker entrypoint and injected into `build_graph()` via its
existing `checkpointer` parameter. `build_graph()` retains `checkpointer=None` as its default so
unit tests can continue using `MemorySaver` without a running Postgres instance.

```python
# In the worker entrypoint (P3a):
saver = PostgresSaver(conn)
graph = build_graph(checkpointer=saver)
```

### Test strategy

- **Unit tests** (test_spine.py, test_p2_gate.py, critic unit tests): keep `MemorySaver` via
  `build_graph()` default. These test logic, not persistence.
- **Integration tests** (new `test_checkpoint_recovery.py` and equivalents): use real
  `PostgresSaver` via testcontainers. These prove the full persistence path including crash
  recovery, checkpoint resume, and session key re-mint after reclaim.

## Options considered

- **A — Pluggable checkpointer with SQLite fallback.** A factory reads environment config and
  constructs the right checkpointer. Rejected: no deployment scenario lacks Postgres; the
  abstraction adds code with no real beneficiary.
- **B — PostgresSaver only, injected at startup (chosen).** Simplest path consistent with the
  hard Postgres requirement already established by the board schema.

## Consequences

- **Easier:** crash recovery and checkpoint resumption work without special configuration; the
  testcontainers pattern already in use covers the integration test tier.
- **Harder:** every integration test environment must have a Postgres instance (already satisfied
  by testcontainers).
- **Revisit:** if a future deployment scenario (e.g., truly air-gapped embedded node with no
  Postgres) emerges, write a new ADR rather than adding the abstraction preemptively.

## What this closes

- Closes P3a's checkpointer decision.
- Eliminates "swappable checkpointer" as a P3 design concern.

## Action items

1. [ ] Add `PostgresSaver` wiring to the P3a worker entrypoint.
2. [ ] Add `test_checkpoint_recovery.py` using testcontainers and PostgresSaver.
3. [ ] Document that `build_graph(checkpointer=None)` is unit-test-only in a code comment.
