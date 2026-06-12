# ADR-013: Coherence model — coherence at the planner, isolation at the workers

**Status:** Accepted
**Date:** 2026-06-11
**Deciders:** Hunt Integrative Solutions LLC

## Context

A single coherent agent session keeps a clean plan but can't scale across parallel work; fully
distributed workers scale but lose plan coherence and risk mixing clients in a shared context. TALOS
must keep both properties at once — "single-session coherence vs. distributed workers" is a real
tension, not a preference (`BLUEPRINT.md` §99–105).

## Decision

**Split the axis** (`BLUEPRINT.md` §101–105): the **planner holds one coherent context** for the
project plan and ingests **only structured worker results** — deliverable + critic verdicts +
event-log delta — **never raw worker transcripts**. Coherence survives parallelism because the noise
never enters the planner's context. Workers are isolated by **session keys** (the mechanism is recorded
in ADR-010). Folded in from the reconciliation pass: the planner context is **itself board-scoped**,
all its memory reads RLS-bound, so a single planner instance can never mix two clients — switching
client re-scopes everything (CR-01).

## Options considered

- **A — One shared agent context for planner + workers** (Agent Zero style). Rejected: a cross-client
  leak vector, and raw transcripts drown the planner.
- **B — Fully isolated workers with no shared planner context.** Rejected: loses plan coherence across
  parallel work.
- **C — Coherence at the planner (structured results in), isolation at the workers (session keys),
  planner board-scoped.** Chosen.

## Trade-off analysis

Feeding the planner only structured results keeps its context small and coherent regardless of how many
workers run, and board-scoping the planner makes the no-cross-client property *structural* rather than
disciplinary. The cost is that workers must emit well-structured results (deliverable + verdicts +
event-log delta) and the planner can't see raw worker reasoning — by design, since that reasoning is
exactly the noise being excluded (it remains available in the cockpit drill-down trail). Upstream:
`langgraph-notes.md` → "Core Graph Model" (state channels, immutable injected context);
`openclaw-notes.md` → "Session Management" (the session key is routing, not auth → RLS is the boundary).

## Consequences

- **Easier:** the plan stays coherent under heavy parallelism; one planner can't mix clients; the
  cockpit's three-level drill-down maps to the result structure.
- **Harder:** workers must produce structured results; raw worker reasoning lives only in the drill-down
  trail, not the planner's context.
- **Revisit:** the structured worker-result schema (deliverable + critic verdicts + event-log delta).

## Action items

1. [ ] Feed the planner only structured worker results; never raw transcripts.
2. [ ] Board-scope the planner context; bind all its memory reads to RLS.
3. [ ] Define the structured worker-result schema.
