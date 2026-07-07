# TALOS — Vision

> *AI proposes, humans review, deterministic critics gate, and nothing is written to a live
> system without a human's approval.*

## What TALOS is

TALOS is an agent harness for industrial automation — a platform where AI agents do the
heavy analytical work of controls engineering (documentation, cross-reference, impact
analysis, migration planning) while a structural guarantee, not a policy suggestion, keeps
them away from live equipment. It is named for the bronze guardian of Greek myth: a tireless
watchman that lets nothing flawed through.

The first capability behind the boundary is **NEXUS**, an ICS analysis engine that reads PLC
programs (ControlLogix, PLC-5, FactoryTalk, Ignition) and answers questions no single
engineer can hold in their head at once: every rung a tag touches, every HMI screen that
displays it, every interlock between two areas, the full documentation set for a processor
that hasn't had accurate drawings in fifteen years.

## Why it's built this way

Agent frameworks in 2026 are powerful and careless in equal measure. TALOS was designed by
studying the best open harnesses in the field and taking each one's strongest idea — and
each one's hardest lesson:

- **From Hermes** (NousResearch): the task board as the source of truth — every unit of work
  is a card with a lifecycle, an append-only event log, and a disciplined dispatcher with
  heartbeats and dead-worker reclaim. Work is never invisible.
- **From OpenClaw**: the gateway pattern and layered tool policy — every layer can only
  *restrict* what an agent may do, never expand it. And the lesson of its marketplace
  crisis (1,400+ malicious skills pulled): anything a model can load gets gated, hashed,
  and signed. TALOS gates capability manifests the way OpenClaw now signs skills.
- **From Agent Zero**: memory that consolidates and learns — but where Agent Zero auto-trusts
  what it extracts, TALOS routes every promotion of learned knowledge through a human gate,
  and client knowledge can never silently leak into another client's context.
- **From Space Agent**: the modern cockpit — spaces, widgets, time-travel. The operator's
  view of TALOS will feel like a living control room, not a log file. (v1 ships a minimal
  gate-approval UI; the full cockpit is the v1.x centerpiece.)
- **From LangGraph**: checkpointed execution where the human gate is a first-class
  interrupt in the graph — a task literally cannot proceed past review; there is no code
  path around it.

None of these ideas is decoration. Each is bound by an Architecture Decision Record, frozen
contracts at every seam, and a test suite that proves the boundaries hold.

## The Guardian doctrine, structurally

Two hard boundaries make the doctrine real rather than aspirational:

1. **The MCP boundary is the security boundary.** Domain capabilities like NEXUS live behind
   a Model Context Protocol edge with a validated, content-hashed manifest declaring every
   tool as read-only or offline-artifact-write. There is *no live-write tool kind at all* —
   a fully compromised orchestrator still cannot reach a processor.
2. **The review gate is a human-owned state transition.** Deterministic critics (pure code,
   no LLM judgment) must pass, and a human must approve, before any deliverable leaves
   review. Safety-class critics cannot be waived by anyone. Five outcomes — approve,
   reject, waive, edit, escalate — and the audit trail is append-only.

This maps directly onto the CISA/Five Eyes guidance for agentic AI in critical
infrastructure (April 2026): least-privilege tool access, human intervention points,
inspectable decision logs. TALOS didn't retrofit that guidance — it was designed on the
same convictions before the guidance existed.

## Where it's going

- **v1 — the proving ground:** a controls engineer triggers full PLC documentation; NEXUS
  analyzes read-only; critics gate the artifact; the engineer approves it. On-prem, at the
  workstation, air-gap capable, any LLM provider (Claude, DeepSeek, local Ollama). Zero
  unauthorized writes, ever. Success is measured in *time-to-confident-approval*.
- **v1.x — the cockpit:** the full Space Agent-style web cockpit — kanban and Gantt as
  first-party widgets, time-travel over the event log, the board as a living space.
- **Beyond:** simulation-gated write artifacts (generated ladder logic proven against an
  emulator before a human ever loads it), proactive documentation-freshness loops that
  propose but never approve, a bi-temporal knowledge graph that remembers what was true
  *when* — and capability packs beyond NEXUS, each behind the same manifest gate.

The harness learns — every approved deliverable crystallizes rules that make the next task
smarter — but what it learns stays with the client it learned from, and nothing it learns
ever outranks the human at the gate.

---

*TALOS is built by Hunt Integrative Solutions LLC. Design record: BLUEPRINT.md, ADR-001–038,
and the four frozen contracts in docs/contracts/. Current status: docs/vision-alignment-review-2026-07.md.*
