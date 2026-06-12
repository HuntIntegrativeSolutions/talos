# ADR-010: Worker isolation — session keys + restrict-only config inheritance

**Status:** Accepted
**Date:** 2026-06-11
**Deciders:** Hunt Integrative Solutions LLC

## Context

TALOS runs many workers in parallel across clients. Each must be isolated (workspace, tool policy,
memory) and resumable after a crash, without being asked to be a security boundary it can't bear.
Upstream harnesses conflate routing, identity, and capability — Hermes worker profiles mix isolation
with capability routing and provide no filesystem sandbox; OpenClaw session keys are routing-only and
explicitly *"cannot be used as a security boundary"* (`BLUEPRINT.md` §106–111).

## Decision

A task claim mints a **session key** `task:{board_id}:{task_id}:{attempt}` scoping the worker's
workspace, tool policy, and memory; the worker is **crash-recoverable from the checkpoint log**
(`BLUEPRINT.md` §106–108). A spawned worker inherits the parent's NEXUS connection, client scope, and
tool policy, overlaid with role restrictions that can **only further restrict** (a tag-audit worker
gets `nexus:read` on `UNIT_*` only). Define profiles once, not per task. Folded in from the
reconciliation pass:

- The session key **scopes but is never the authorization boundary** — the hard boundary is
  `board_id` + Postgres RLS; PreToolUse scope injection is belt-and-suspenders (CR-02).
- Separate the **profile** (identity / secrets / isolation) from the **capability selector** (tool
  policy + model routing), and add a Docker FS sandbox on top of session-key isolation (CR-21).
- Every write-class side effect carries an **idempotency key** (`…:{attempt}:{step}`) so a re-claim
  from checkpoint cannot double-apply it (CR-12).

## Options considered

- **A — A shared worker context, or the session key as the auth boundary.** Rejected: a shared context
  leaks across clients, and OpenClaw shows the key can't bear authorization.
- **B — Session keys for scoping + RLS as the hard boundary + restrict-only inheritance + an FS
  sandbox.** Chosen.

## Trade-off analysis

Session keys give cheap per-worker scoping and clean crash-recovery; RLS underneath gives the boundary
that no policy mistake can bypass. Restrict-only inheritance means a spawned worker is always ≤ its
parent's reach. The cost is two mechanisms (keys + RLS) plus an idempotency discipline on every side
effect — the redundancy is deliberate. Upstream: `agent-zero-notes.md` → "Agent Hierarchy" (reject the
shared `AgentContext`); `hermes-profile-builder-notes.md` → "Profile vs Workspace vs Sandbox" (state
isolation but *not* filesystem isolation, which is why TALOS adds the sandbox).

## Consequences

- **Easier:** define worker profiles once; resume from checkpoint; spawned workers are always narrower
  than their parents.
- **Harder:** idempotency keys on every write-class side effect; the profile-vs-capability separation
  to maintain.
- **Revisit:** the resumable-cursor interface that capabilities must expose (`capability-manifest`).

## Action items

1. [ ] Mint session keys `task:{board_id}:{task_id}:{attempt}`; scope workspace / tool-policy / memory.
2. [ ] Enforce RLS as the hard isolation boundary (keys never authorize).
3. [ ] Implement restrict-only config inheritance + a Docker FS sandbox.
4. [ ] Attach idempotency keys to every write-class side effect.
