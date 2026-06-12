# ADR-006: The Strategy Ladder — a declarable six-step task execution pattern

**Status:** Accepted
**Date:** 2026-06-11
**Deciders:** Hunt Integrative Solutions LLC

## Context

Tasks vary wildly in difficulty. Running every task through a heavyweight plan-and-critique loop
wastes budget; running a hard task in a single shot fails. TALOS needs a first-class, declarable
execution pattern — the runtime form of the AutoAdapt/Strategy Graph — that scales effort to task
complexity and stops at the human gate whenever a step would touch a write-capable tool or a safety
system (`BLUEPRINT.md` §70–97).

## Decision

Adopt the six-step **Strategy Ladder** (`BLUEPRINT.md` §75–86):

1. **Triage** — estimate complexity; choose ladder depth.
2. **Research** — ground in retrieved knowledge before planning (never plan from priors alone).
3. **Plan relay** — a cheaper model drafts the plan; a stronger model refines it (architect→editor,
   *within a task*: a reasoning model proposes, a precise editing model translates to exact edits).
4. **Gate the plan** — *mandatory* the moment the plan would call a write-capable tool or touch a
   safety system; auto-approve under a complexity threshold otherwise.
5. **Execute** — inside the approved envelope.
6. **Crystallize** — turn a successful trajectory into a **gated** skill and a new Strategy-Graph
   path (both default `client_scope: [client]`, both ride the one promotion gate — see ADR-005).

A hard task climbs; an easy one short-circuits. **Guardrails:** per-task-type iteration caps
(code-gen ≤5, analysis ≤3, any offline/sim write ≤1, **no auto-retry on anything live**), all under
the three-axis budget as a hard ceiling. A cheap **gate-bound evaluator** picks the next ladder step
after each turn; it is free to self-advance research → plan → refine but **stops dead at the human
gate** the instant the next step would call a write-capable tool or touch a safety system. That
write/safety classification is a declared deterministic capability property — not an LLM judgment —
and fails closed on unknowns (`01_conflicts_and_resolutions.md` CR-13, ADR-004).

## Options considered

- **A — A fixed pipeline for all tasks** (single-shot, or always heavyweight). Rejected: mis-scales
  effort in one direction or the other.
- **B — An LLM agent free-runs and self-judges when to stop.** Rejected: a non-deterministic model on
  the safety path can self-advance past the gate.
- **C — A declarable ladder with a gate-bound evaluator and deterministic write/safety stops.**
  Chosen.

## Trade-off analysis

The ladder gives two compounding loops — *knowledge* (graph/vault) and *procedure* (skill/strategy
library) — and lets the strong model spend on critique-and-finish rather than a cold start. The
evaluator adds a cheap per-turn cost but is what makes the loop both adaptive and safe: because the
write/safety stop is deterministic, the auto-approve-under-threshold path is sound (auto-approval is
permitted *only* when no step touches a declared write/safety capability, CR-13). Upstream shapes:
`hermes-notes.md` → "Goal Mode" (judge/executor split), `langgraph-notes.md` → "TALOS Integration"
(ladder as a `StateGraph`), `aider-pagerank-notes.md` (the architect→editor relay).

## Consequences

- **Easier:** effort scales to complexity; successful trajectories become reusable skills + paths;
  the strong model is spent where it pays.
- **Harder:** triage and the evaluator must be tuned; the auto-approve threshold is an open parameter.
- **Revisit:** the auto-approve complexity threshold (a standing parking-lot item); the iteration
  caps per task type.

## Action items

1. [ ] Implement the six ladder steps as a declarable pattern (`StateGraph`).
2. [ ] Implement the gate-bound evaluator that picks the next step and hard-stops on declared
      write/safety.
3. [ ] Encode the iteration caps and the three-axis budget ceiling.
4. [ ] Define the auto-approve complexity threshold (escalated parking-lot item).
