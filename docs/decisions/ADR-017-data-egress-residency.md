# ADR-017: Data-egress and residency — hosted model endpoints, not air-gapped

**Status:** Accepted
**Date:** 2026-06-14
**Deciders:** Hunt Integrative Solutions LLC

## Context

The BLUEPRINT and prior integration documents described the Acme thick edge as "air-gapped; data never
leaves." At the same time, the same documents documented that Acme "runs the DeepSeek API on its own
line." RT-07 (red-team review, 2026-06-12) identified this as a BLOCKER-class contradiction: "its own
line" is a network line, not an air gap. The existing `deepseek-chat`/`deepseek-reasoner` model wiring
and `DEEPSEEK_API_KEY` configuration corroborate that the hosted `api.deepseek.com` endpoint is in use,
meaning client PLC IP, tag names, and process logic egress to a third-party model provider on every
analysis turn.

All isolation guarantees in CR-11, CR-03, and the integration architecture that were derived from "data
never leaves" were therefore resting on a false premise. This ADR resolves the contradiction and
establishes an accurate, honest data-egress model.

## Decision

**Drop the air-gap claim.** TALOS and its edge deployments do not provide air-gap isolation. Client
PLC context (tag names, rung logic, program structure) egresses to hosted model API endpoints on every
analysis turn. This is the accurate description of how the system operates today and for the foreseeable
future.

All references in BLUEPRINT, integration documents, and any future documentation to "air-gapped; data
never leaves" are superseded by this ADR. The architecture does not guarantee that client data stays
on-premises.

## Options considered

### Option A: Self-host DeepSeek weights on the thick edge
| Dimension | Assessment |
|-----------|------------|
| Air-gap preservation | Yes — if the model runs on-premises, no client data egresses |
| Cost/ops | Requires GPU or sufficient CPU at the edge; significant hardware investment |
| Model quality | Self-hosted quantized weights may underperform hosted API on complex reasoning |
| Feasibility today | Not in place; would require procurement and deployment work |

### Option B: Drop the air-gap claim (chosen)
| Dimension | Assessment |
|-----------|------------|
| Accuracy | Honest — matches the actual wiring (`DEEPSEEK_API_KEY`, hosted endpoints) |
| Ops impact | Zero — no change to existing infrastructure |
| Client disclosure | Required — any client contractual guarantee of "data never leaves" must be revised |
| Risk | Model providers receive PLC context; governed by their data-handling terms |

### Option C: Tiered egress — strip sensitive fields before sending
| Dimension | Assessment |
|-----------|------------|
| Partial isolation | Redacts specific identifiers (tag addresses, CH-numbers) before model call |
| Correctness | A PII/sensitivity classifier would need to identify what to strip; classification errors leak |
| Complexity | High — a strip/reconstruct pipeline for every model turn |
| Verdict | Not adopted; adds complexity without a strong guarantee |

## Trade-off analysis

The decisive factor is honesty over the false comfort of a stated guarantee that isn't structurally
enforced. An "air-gapped" edge that calls `api.deepseek.com` is not air-gapped. Building a safety
architecture on top of a false premise would be more dangerous than acknowledging the accurate model.

Self-hosting (Option A) is the right long-term path if clients require true data residency, and this
ADR does not foreclose it. If a future client requires data residency, the edge deployment for that
client should use self-hosted weights, and this ADR should be extended with a per-client residency
tier. For the current state of the system, Option B is the accurate and correct choice.

## Consequences

- **Easier:** no change to the existing model configuration or infrastructure.
- **Harder:** any prior written or implied guarantee of air-gap isolation to Acme or any other client
  must be corrected; the architecture documentation must not use the phrase "air-gapped" without
  specifying that it refers to live-processor write isolation (no agent writes to a live controller),
  not network isolation (data stays on-premises).
- **Residual isolation guarantees that still hold:**
  - *Live-processor write isolation* — no TALOS agent ever writes to a live PLC. This guarantee is
    structural (no tool exists; capability profiles are propose-only; the gate gates all write-class
    tools). It is independent of model egress.
  - *Per-client data isolation within TALOS* — RLS, `board_id` scoping, session-key isolation, and
    the `group_id` chokepoint still prevent cross-client data leaks *inside* TALOS. These guarantees
    survive this ADR.
  - *NEXUS system-of-record isolation* — ADR-003 and the separate-TALOS-Neo4j decision (CR-08) still
    keep TALOS episodic data separate from NEXUS's structural data.
- **What does NOT hold:** the phrase "data never leaves the facility" applied to model-inference
  traffic. Client PLC context egresses to hosted model endpoints. This is the accepted operating
  model.

## Data-egress inventory by edge class

| Edge class | External endpoints that receive client data | Data class egressed |
|------------|---------------------------------------------|---------------------|
| Thin edge (cloud-connected) | Hosted model API (DeepSeek, Anthropic, or similar) | Task context, rung excerpts, tag names |
| Thick edge (Acme-class) | Hosted model API on a dedicated network line | Same — not air-gapped at the model layer |
| Future: on-premises edge | Self-hosted model weights only | None — true data residency |

## Action items
1. [x] Drop all "air-gapped; data never leaves" language from BLUEPRINT, integration docs, and README.
2. [ ] Update `BLUEPRINT.md §220-229` to describe thick-edge data flows accurately.
3. [ ] Update `docs/integration/02_unified_architecture.md §7` to remove or qualify the isolation claims.
4. [ ] If Acme (or any client) has a contractual or verbal data-residency expectation, disclose this decision.
5. [ ] If a future client requires true data residency: self-host model weights and extend this ADR with a per-client residency tier.
