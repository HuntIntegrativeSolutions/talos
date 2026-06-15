# Research Prompt — Omnigent Meta-Harness

You are doing a deep technical research dive for TALOS, a pre-alpha multi-agent industrial
project-execution platform at `/mnt/i/talos/`. TALOS is NOT a coding assistant. It
orchestrates agents for operations work (PLC audits, maintenance, PM) behind a hard
human-review gate. Nothing reaches a live system without human approval.

Omnigent is a new open-source meta-harness from Databricks (Apache 2.0). It sits above
individual agent harnesses and makes them interoperable. Your job is to research Omnigent
thoroughly and extract everything TALOS should adopt.

This research session produces one output file. Do not modify any other files.

---

## Context — what TALOS has already built and decided

Read these files silently before fetching any external sources:

- `/mnt/i/talos/BLUEPRINT.md` — TALOS architecture overview
- `/mnt/i/talos/docs/decisions/ADR-001.md` — TALOS is a platform; NEXUS is a capability
  behind MCP (not merged)
- `/mnt/i/talos/docs/decisions/ADR-004.md` — capability tool profiles (read/write; no live
  writes; write = offline/sim only)
- `/mnt/i/talos/docs/decisions/ADR-009.md` — layered tool policy (intersection-only;
  each layer can only restrict, never expand; global "no live writes" is the floor)
- `/mnt/i/talos/docs/decisions/ADR-010.md` — worker isolation (session keys, Docker sandbox)
- `/mnt/i/talos/docs/decisions/ADR-011.md` — five gate outcomes
- `/mnt/i/talos/docs/upstream/openclaw-notes.md` — OpenClaw gateway deep-dive (7-layer tool
  policy, sandboxing, session management patterns)
- `/mnt/i/talos/docs/integration/04_build_sequence.md` — P0–P8 build sequence

Pay particular attention to ADR-009 (layered tool policy) and ADR-010 (worker isolation).
Omnigent's stateful control policy and OS sandbox are directly in this territory.

---

## Sources to fetch and study

1. `https://www.databricks.com/blog/introducing-omnigent-meta-harness-combine-control-and-share-your-agents`
   — the primary technical announcement
2. `https://omnigent.ai/` — official site, may have architecture diagrams and docs
3. `https://www.marktechpost.com/2026/06/13/databricks-open-sources-omnigent-a-meta-harness-that-composes-governs-and-shares-ai-agents-across-claude-code-codex-and-pi/`
   — external technical analysis
4. `https://community.databricks.com/t5/data-engineering/databricks-omnigent-points-to-the-next-layer-of-ai-engineering/td-p/158953`
   — community discussion of technical implications

Also fetch the Omnigent GitHub repository if accessible (search for
"Databricks Omnigent GitHub repo" to find the URL).

Also search the web for:
- "Omnigent Databricks stateful control policy implementation"
- "Omnigent agent runner sandboxed session API"
- "Omnigent YAML agent spec format"
- "Omnigent cost gate human approval threshold"

---

## What to extract from each source

**The runner abstraction:**
- What exactly does an Omnigent "runner" do? What is the interface it exposes?
- How does the runner wrap a terminal-based agent (like Claude Code) vs. an SDK-based agent?
- What does "messages and files in, text streams and tool calls out" look like in code?
- Is this a process wrapper, a protocol abstraction, or something else?
- How does a runner's session map to TALOS's session-key concept?

**Stateful control policies:**
- What is a control policy? Is it a function? A declarative rule? A class?
- What state does a policy have access to? (session cost, tool call history, file access log?)
- Can policies be composed? Can policy A depend on policy B's verdict?
- Can policies block actions or only log/alert?
- How is policy evaluation ordered relative to tool execution?
- Compare this to TALOS's ADR-009 layered tool policy (intersection-only, 8 layers). Is
  Omnigent's model more or less expressive? What does it enable that ADR-009 doesn't?

**Cost gate:**
- How is per-session LLM cost tracked?
- What is the mechanism for pausing an agent at a cost threshold and waiting for human
  approval to continue?
- Is this implemented as a hook, a policy, or a separate primitive?
- How does this map to TALOS's three-axis budget (tokens, time, tool-calls) from the
  OpenClaw notes?

**OS sandbox:**
- What OS-level isolation mechanism does Omnigent use? (Docker? seccomp? ptrace?)
- How does "intercept and transform network requests" work technically?
- How does "inject credentials selectively" work — is this an egress proxy, LD_PRELOAD, or
  something else?
- Is this more or less capable than TALOS's planned `network:none + readOnlyRoot` Docker
  sandbox (ADR-010)?

**Session sharing:**
- How does live session sharing via URL work technically?
- Is this WebSockets? Server-Sent Events?
- How do collaborators "steer" a live session — can they inject messages? Tool results?
  Pause the agent?
- How does this map to TALOS's planned cockpit (P7) real-time task view?

**Agent composition:**
- How does YAML-based agent spec work? What fields exist in the spec?
- How does one-line switching between harnesses (Claude Code → Codex → custom) work?
- Is the spec format general enough to define TALOS's capability manifest, or is it
  a different level of abstraction?
- What does multi-harness orchestration look like in practice?

**Governance and sharing:**
- Can Omnigent enforce TALOS-style board isolation (no cross-board data access)?
- Is there a concept of a "board" or tenant isolation?
- How does Omnigent handle secrets — are they visible to agents or only to the policy layer?

---

## The central question: meta-harness or peer?

TALOS is NOT trying to build a meta-harness that wraps Claude Code and Codex. TALOS is a
domain-specific harness for industrial operations with a hard gate doctrine. But Omnigent
may have solved specific sub-problems that TALOS should adopt rather than reinvent:

- **Stateful policies**: Omnigent's approach is likely more elegant than TALOS's planned
  hand-rolled ADR-009 enforcement. Should TALOS adopt Omnigent's policy model for ADR-009?
- **Cost gate**: Omnigent's pause-on-cost-threshold is directly useful for TALOS's
  three-axis budget enforcement. Should TALOS adopt this mechanism?
- **OS sandbox intercept**: If Omnigent's approach to network interception is more granular
  than `network:none`, should TALOS adopt it for P3c's Docker sandbox?
- **Session sharing**: If Omnigent's real-time session collaboration API is clean, should
  TALOS's P7 cockpit build on it rather than implementing its own WebSocket layer?

For each sub-problem, give a clear recommendation: adopt, adapt, or ignore.

---

## What to produce

Write a single file: `/mnt/i/talos/docs/upstream/omnigent-notes.md`

Follow the format of existing upstream notes:

```
# Omnigent — Research Notes

## What it is
[one paragraph — what Omnigent is, what problem it solves, who built it, license]

## The runner abstraction
[what a runner is, the common API, session model]

## Stateful control policies
[policy model, state access, composition, blocking vs. logging]

## Cost gate
[mechanism, human-approval pause, comparison to TALOS three-axis budget]

## OS sandbox
[isolation mechanism, network interception, credential injection, compare to ADR-010]

## Session sharing
[technical mechanism, collaboration features, comparison to P7 cockpit needs]

## Agent composition and YAML specs
[spec format, multi-harness switching, relevance to TALOS capability manifest]

## Key TALOS findings
[bulleted — for each: what Omnigent does, how it maps to a specific TALOS component,
 and what to adopt/adapt/ignore]

## What TALOS should NOT take
[things that conflict with TALOS's gate doctrine, board isolation, or ADR constraints]

## Open questions for the builder
[specific questions research couldn't settle]

## Build-phase impact
[Does anything from Omnigent change P3 scope? Which ADRs may need updating?]
```

Write the file. Do not modify any other file.
