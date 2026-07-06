# ADR-005: Cross-client memory split — `[client]` by default, one promotion gate to `[shared]`

**Status:** Accepted
**Date:** 2026-06-11
**Deciders:** Hunt Integrative Solutions LLC

## Context

TALOS serves multiple clients and learns reusable artifacts — graph nodes, skills, and crystallized
strategy paths. Some of that is generic vendor/device knowledge (how to configure a 1756-L8x, a
GuardLogix signature workflow); some is a client's competitive process IP (burner sequencing, alarm
philosophy). Mixing the two is an IP-leak vector. The system needs a default that protects client IP
and one consistent way to promote genuinely generic knowledge for reuse (`BLUEPRINT.md` §152–158).

## Decision

Tag every *learned artifact* with `client_scope: [shared]` or `[client]`; **all default to
`[client]`**. **Promotion to `[shared]` passes one gate, regardless of artifact type** — graph node,
skill, or strategy path alike — where sanitize = abstract the shape, strip the instance
(`BLUEPRINT.md` §152–158). "Generic engineering vs. the client's competitive method" is a judgment
call with IP weight, so it is always human-reviewed, never automatic.

This unifies what an upstream source leaves loose: Agent Zero auto-extracts "verified solutions" from
chat with **no human review** — BLUEPRINT overrides that (`01_conflicts_and_resolutions.md` CR-05).
The area is renamed **CRYSTALLIZED**, and nothing graduates to trusted/`[shared]` status without the
promotion gate; auto-extraction may populate FRAGMENTS only.

## Options considered

- **A — Global shared memory pool** (one store, reuse everywhere). Rejected: cross-client IP leak by
  default.
- **B — Per-artifact-type promotion rules** (skills gated one way, paths another). Rejected:
  inconsistent and easy to leave a gap on one artifact type.
- **C — Default `[client]`, one unified promotion gate across all artifact types.** Chosen.

## Trade-off analysis

One gate is simpler to reason about and audit than per-type rules, and a single default (`[client]`)
makes the safe choice the lazy choice. The cost is that every genuinely-shared artifact needs a human
promotion step (sanitize + review) — and that cost is the point: promotion is exactly where IP
judgment belongs. Storage-layer isolation comes from `group_id` multi-tenancy
(`graphiti-notes.md` → "Multi-Tenancy"); the gate adds the human judgment on top
(`agent-zero-notes.md` → "Memory Consolidation", whose auto-"verified" naming BLUEPRINT overrides).

## Consequences

- **Easier:** the safe default is automatic; one promotion path to learn; client IP cannot silently
  reach another client.
- **Harder:** a human must sanitize and review each shared promotion; "shared vs client" is a
  recurring judgment call.
- **Revisit:** the sensitivity threshold and the sanitization checklist; the relationship to the
  consolidation boundaries in ADR-014 (the promotion gate and the consolidation gate are the same
  gate seen from two sides).

## Action items

1. [ ] Add `client_scope` to graph nodes, skills, and strategy paths; default `[client]`.
2. [ ] Implement the single promotion gate (sanitize → review → `[shared]`) across all three artifact
      types.
3. [ ] Rename the Agent-Zero "verified solutions" area to CRYSTALLIZED; auto-extraction populates
      FRAGMENTS only.

**Implemented (P4b), for the `rules` artifact type only:** `POST /boards/{board_id}/promote_rule`
creates a `rules` row (`client_scope='client'`) and a promotion task that flows through the
identical gate/critic/human-approval pipeline as any other task — the one promotion gate this ADR
calls for, not a parallel path. `talos/critics/no_client_identifiers_in_shared.py` (RT-06) is the
deterministic sanitization enforcer this ADR's "strip the instance" step lacked; it is required and
non-waivable, so a leak can only be resolved by a second human reviewer (escalate), never a waiver,
and escalate itself does not flip scope. Graph-node/skill/strategy-path promotion (action items #1
and #3) remain unimplemented — this only closes the `rules` (project-context/factual/procedural,
ADR-023) artifact type.
