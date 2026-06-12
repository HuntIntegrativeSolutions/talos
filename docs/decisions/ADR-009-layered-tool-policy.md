# ADR-009: Layered tool policy — intersection-only, restrict-never-expand, global no-live-writes floor

**Status:** Accepted
**Date:** 2026-06-11
**Deciders:** Hunt Integrative Solutions LLC

## Context

TALOS composes tool access from several sources — a global doctrine, a client deployment, a per-task
role, and (for self-authored skills) a per-skill manifest. It needs a composition rule that can never
accidentally grant *more* than intended, and a non-negotiable floor under everything
(`BLUEPRINT.md` §243–245).

## Decision

Effective policy = **global ∩ client ∩ task**; each layer can **only restrict, never expand**. The
global layer is **"no live writes, ever."** This is how the gate/scope system is *implemented*, not a
feature on top — the safety spine (`BLUEPRINT.md` §243–245, from OpenClaw + Codex). Folded in from the
reconciliation pass:

- A per-skill **capabilities-manifest layer** checks each tool call against the *pinned* manifest at
  invocation time and denies anything not declared, even if the session policy would allow it
  (CR-04, the 8th layer).
- Proactive cron/webhook turns inherit the **same floor** — no more privilege than a user turn; they
  may notify and propose, never approve or write — and the gateway itself is sandboxed (CR-19).
- Workers run in a **Docker sandbox** (`network:none`, `readOnlyRoot`) on top of session-key isolation
  (CR-21).
- **No safety invariant may rest on an AGENTS.md prose statement alone** — every safety rule has a
  structural enforcer: a deterministic critic, RLS, or a tool-policy layer (CR-17).

## Options considered

- **A — Additive/union policy** (layers can grant). Rejected: any layer can widen the blast radius and
  the floor is no longer guaranteed.
- **B — Intersection-only, restrict-never-expand, with a global no-live-writes floor.** Chosen.

## Trade-off analysis

Intersection makes the safe direction the only direction — no layer can escalate. The cost is that
over-restriction can block legitimate work (debugged by inspecting which layer denied), which is the
right failure mode on a safety system. Making this the *implementation* of the gate/scope system
(not an add-on) is what lets ADR-001's "a compromised orchestrator still can't reach a processor" hold
by construction. Upstream: `openclaw-notes.md` → "Tool Policy Pipeline" (a 7-layer narrow-only
pipeline with a global floor); `github-agentic-workflows-notes.md` (MCP-gateway credential isolation
and firewall allowlist) reinforce the floor at the network edge.

## Consequences

- **Easier:** the floor is guaranteed regardless of misconfiguration; one mental model (intersection)
  governs all access.
- **Harder:** more layers to evaluate per call; over-restriction needs good per-layer denial logging
  (the PreToolUse rewrite is logged, never silent to the audit log).
- **Revisit:** the per-skill pinned-manifest layer (CR-04) and the gateway/worker sandbox specifics
  (CR-19, CR-21).

## Action items

1. [ ] Implement the intersection-only evaluator (global ∩ client ∩ task ∩ pinned-skill-manifest).
2. [ ] Set the global "no live writes, ever" floor as non-overridable.
3. [ ] Sandbox the gateway and workers (`network:none` / `readOnlyRoot`); proactive turns inherit the
      floor.
4. [ ] Add a test that no safety invariant depends solely on AGENTS.md prose.
