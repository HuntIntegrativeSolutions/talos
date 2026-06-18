# ADR-025: Board API contract — frozen seam between engine and view

**Status:** Accepted
**Date:** 2026-06-17
**Deciders:** Hunt Integrative Solutions LLC

## Context

`docs/contracts/board-api.md` specifies the only channel between the Postgres board engine and the
Space Agent cockpit view. It was written and reviewed before the 2026-06-16 requirements interview.
This ADR formalizes it as a binding ADR so the contract is discoverable in the decision record and
cannot be changed without an explicit ADR update.

This ADR is a pointer-and-rationale record; the normative text lives in `docs/contracts/board-api.md`.

## Decision

The board-api contract is accepted as binding. Key invariants:

1. **View reaches engine only through board-api.** Never Postgres directly. Enforced structurally.
2. **Gate-outcome submission is the only trusted write path.** Approve / Reject / Waive / Edit /
   Escalate. These are the only operations that may write `approved_at` or `task_gate_results`.
3. **Event-delta stream uses a BIGINT monotone cursor.** The record shape and cursor semantics are
   frozen; push transport (WebSocket vs SSE) is an implementation choice, not a contract item.
4. **Time-travel versions layout only, never task truth.** A `space_versions` rollback restyles;
   it never mutates `tasks`, `task_gate_results`, or `task_events`.
5. **Worker bookkeeping columns are never in the view projection.** `claim_lock`, `worker_pid`,
   `session_id`, `idempotency_key`, `model_override`, `last_failure_error` are internal engine
   concerns.

## Open items (from contract)

- Push transport (WebSocket vs SSE vs LangGraph `custom` stream) — implementation choice.
- Pagination / cursor envelope semantics — implementation choice.
- `getGantt` / critical-path in v1 vs v1.x — deferred.
- Column allowlist finalization — recommendation in contract; confirm before v1 launch.

## Consequences

- Any team member proposing a change to the board-api surface must update this ADR and
  `docs/contracts/board-api.md` together.
- The four frozen contracts form a CR-23–ordered freeze sequence: board-api is frozen first
  (before Phase 0), capability-manifest before Phase 1, nexus-federation before Phase 4,
  widget-sandbox before Phase 5.
