# ADR-015: Phase reorder — gate + critics before the full dispatcher

**Status:** Accepted
**Date:** 2026-06-11
**Deciders:** Hunt Integrative Solutions LLC

## Context

The build phasing is dependency-ordered, but the gate doctrine is the entire point of TALOS and needs
to be provable as early as possible. The question is whether to build the full distributed dispatcher
first or the gate + critics first (`BLUEPRINT.md` §254–268).

## Decision

Build the **gate + critics before the full distributed dispatcher** (`BLUEPRINT.md` §259–263, §309). A
**single-worker harness is enough to prove the doctrine**; the full distributed dispatcher
(claim / heartbeat / breaker / checkpoint) builds in parallel *toward* the gate's target. So the
dependency-ordered phasing runs **0 (foundations) → 2 (gate + critics) → 1 (full dispatcher) → 3+**.
Folded in from the reconciliation pass: this is sequenced with the **contract-freeze order** — the
`capability-manifest` (read/write profiles + resumable cursor) freezes before Phase 1, so the gate is
provable on one worker before the dispatcher exists (CR-23).

## Options considered

- **A — Full dispatcher first, then the gate.** Rejected: defers the doctrine's proof and builds scale
  before proving the thing that makes TALOS safe.
- **B — Gate + critics first (a single-worker harness); the dispatcher builds in parallel toward it.**
  Chosen.

## Trade-off analysis

Proving the gate on a single-worker harness validates the safety doctrine before any distributed
complexity exists, and lets the dispatcher build toward a known target. The cost is a throwaway
single-worker harness and a phasing whose *numbered labels* read out of order (Phase 2 before
Phase 1) — accepted because the dependency, not the number, drives the build. Upstream:
`github-agentic-workflows-notes.md` → "Compilation-Time Security" (validate the plan before any agent
runs); `langgraph-notes.md` → "Human-in-the-Loop" (a pure gate node with side effects only after the
human decision).

## Consequences

- **Easier:** the safety doctrine is demoable first; the dispatcher builds toward an already-proven
  gate.
- **Harder:** the single-worker harness is interim scaffolding; the two phase-numbering axes
  (implementation vs documentation) must not be conflated.
- **Revisit:** the contract-freeze sequencing for the later phases (CR-23, across all four boundary
  contracts).

## Action items

1. [ ] Build the gate + critics on a single-worker harness (Phase 2) before the full dispatcher
      (Phase 1).
2. [ ] Freeze the `capability-manifest` (read/write profiles + resumable cursor) before Phase 1.
3. [ ] Keep the implementation and documentation phase axes distinct.
