# ADR-037 — Worker Reclaim System Role

**Status:** Accepted  
**Date:** 2026-06-18  
**Closes:** SEC-03 (worker reclaim gap)

---

## Context

`reclaim_dead_workers` scans `task_runs` across all boards to find stale heartbeats and re-queue
their tasks. It is a system-level janitor, not a board-scoped operation: it must see rows from
every board to function correctly.

After SEC-03 (see `docs/security-review-rt01-findings.md`) flipped `TALOS_DB_DSN` to
`talos_app` (a `NOSUPERUSER` role with board-level RLS enforcement), `reclaim_dead_workers` began
returning zero rows for boards other than the one the current connection is scoped to. Running
reclaim under a `board_scope`d connection defeats its purpose — the whole point is to find stale
runs on any board.

Separately, the heartbeat callback (`make_heartbeat_callback`) used `autocommit=True` + no
`board_scope`, which means `SET LOCAL app.board_id` was never applied and the `UPDATE task_runs`
was rejected by RLS. This is a distinct but related correctness bug.

---

## Decision

### Reclaim: dedicated `talos_system` role with `BYPASSRLS`

Create a `talos_system` Postgres role with `BYPASSRLS NOSUPERUSER NOINHERIT LOGIN`. The reclaim
path gets its own connection via `get_system_conn()` in `talos/db.py`, which reads
`TALOS_RECLAIM_DSN`. This connection is used exclusively by `reclaim_dead_workers`; all other
application code continues to use `talos_app`.

`talos_system` is granted:
- `SELECT` on all tables — required because `UPDATE tasks` fires the `pm_recompute_scheduling`
  trigger, which reads `v_critical_path` and underlying tables (`task_links`, etc.). Triggers run
  under the caller's privileges (SECURITY INVOKER), so `talos_system` must be able to SELECT
  from views the trigger accesses. SELECT alone is not a sensitive privilege given BYPASSRLS.
- `UPDATE` on `task_runs` and `tasks` — the only write paths in reclaim
- `INSERT` on `task_spans` (for `emit_span` on reclaim events)
- `USAGE, SELECT` on `task_spans_id_seq`

`get_system_conn()` is **fail-closed**: if `TALOS_RECLAIM_DSN` is unset, it raises
`RuntimeError` rather than falling back to a non-BYPASSRLS DSN. A silent fallback would
reintroduce the zero-row cross-board bug under a missing env var.

### Heartbeat: stays as `talos_app` + `board_scope`

The heartbeat UPDATE is board-specific — it modifies a single `task_runs` row for a known
`board_id`. It does not need cross-board visibility; it needs the correct `app.board_id` GUC.
`make_heartbeat_callback` is updated to accept `board_id`, drop `autocommit=True`, and wrap the
UPDATE in `board_scope(conn, board_id)`.

---

## Alternatives considered

**B — `SECURITY DEFINER` SQL function.** Moves the cross-board scan into plpgsql owned by the
table owner. Surgical for RLS bypass, but reclaim logic moves to SQL, losing Python-level
`emit_span` calls per reclaimed run. Harder to extend and test. Rejected.

**C — Per-board reclaim.** Scope reclaim to the current board's task_runs only. Simple, but
semantic regression: a board with no active dispatcher never has its stale runs reclaimed by
another board's dispatcher. Rejected.

---

## Consequences

- `TALOS_RECLAIM_DSN` is a required environment variable for production deployments (see
  `docs/install.md` step 3).
- `talos_system` has `BYPASSRLS` — its grants must be minimal and audited carefully. Any new
  cross-board system operation that needs BYPASS must be explicitly justified before using this
  role.
- The `admin_bypass` RLS policy (`current_user = 'talos_admin'`) remains vestigial: nothing
  connects as `talos_admin`. Removing it is tracked as a separate open item.
