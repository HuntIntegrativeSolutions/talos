# ADR-002: Board engine + Space Agent view (the board *is* a Space)

**Status:** Accepted
**Date:** 2026-06-10
**Deciders:** Hunt Integrative Solutions LLC

## Context

Hermes' board has the strongest task lifecycle; Space Agent has the nicest, self-reshaping UI. We
want both — without inheriting Space Agent's "all logic client-side, light backend" model for data
that must be server-authoritative and audited.

## Decision

Keep the **board engine** (Postgres truth, dispatcher, event log, gate) and render the **view** with
Space Agent's space/widget system. The kanban board becomes a **Space**: columns, cards, and
per-task widgets are agent-rendered and time-travel-versioned. The view reaches the engine only
through the **board API**.

## Options considered

- **A — Build the board inside Space Agent.** Nicer UI fast, but imports a frontend-centric model and
  puts task truth in client-side logic. Rejected.
- **B — Keep Hermes' dashboard.** Robust, but it's the part you wanted to replace for looks. Rejected.
- **C — Engine from Hermes, view from Space Agent, joined at the board API.** Chosen.

## Trade-off analysis

The seam is the board API (already built as "Phase B"). With it, the view is just one consumer of
the engine, exactly as Hermes already treats its dashboard/CLI/worker surfaces. The one rule that
keeps the blend safe: **time-travel versions the layout, never task records.** A UI rollback may
restyle the board; it must never mutate task truth.

## Consequences

- **Easier:** a beautiful, self-reshaping board on a hardened lifecycle; agent-authored widgets.
- **Harder:** the system is polyglot (Python engine + JS view); the board API contract must be strict
  about what the sandboxed view can read and request.
- **Revisit:** the sandbox policy (CSP + allowed board-API scopes) for agent-authored widgets.

## Action items
1. [ ] Freeze the board API surface the view is allowed to call.
2. [ ] Define the widget sandbox policy and the propose → review → pin lifecycle.
