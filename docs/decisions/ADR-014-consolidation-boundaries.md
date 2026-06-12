# ADR-014: Consolidation boundaries — autonomous within one client scope; cross-scope MERGE forbidden

**Status:** Accepted
**Date:** 2026-06-11
**Deciders:** Hunt Integrative Solutions LLC

## Context

Memory isn't store-and-search; an LLM-mediated consolidation pipeline finds similar entries and decides
MERGE / REPLACE / UPDATE / KEEP-SEPARATE. Left unbounded, consolidation could merge across client
scopes (an IP leak) or silently rewrite a verified/safety fact. The boundaries must be TALOS's, not the
upstream library's defaults (`BLUEPRINT.md` §169–174).

## Decision

Consolidation runs autonomously **only within one client scope and below a sensitivity threshold**,
with a **similarity floor before any REPLACE** (`BLUEPRINT.md` §169–174). A **MERGE across
`[client]`/`[shared]` is forbidden** (leak vector). Anything touching a **verified-solution or safety
node is a proposal to the gate**, not an auto-write. Folded in from the reconciliation pass: raw
episodic capture flows freely *under one scope*; the gate guards promotion, cross-scope merges, and
verified/safety nodes — drawing the "ground-truth writes pass the gate" line exactly here, not at every
episodic write (CR-09). A disagreement between an episodic observation and a NEXUS fact is captured as a
*candidate finding*, never auto-written to a `verified` node.

## Options considered

- **A — Gate every memory write.** Rejected: defeats episodic memory; unworkable.
- **B — Gate nothing (fully autonomous consolidation).** Rejected: a cross-scope MERGE leak plus
  autonomously-minted "verified" ground truth is a doctrine breach.
- **C — Autonomous within one scope below a sensitivity threshold; gate cross-scope / verified /
  safety.** Chosen.

## Trade-off analysis

Scoping autonomy to one client below a sensitivity line keeps memory useful and cheap for the common
case, while the three hard edges (cross-scope MERGE, verified nodes, safety nodes) stay gated. The
forbidden cross-`[client]`/`[shared]` MERGE makes the leak boundary *absolute*, not
threshold-dependent. Upstream: `agent-zero-notes.md` → "Memory Consolidation" (the MERGE / REPLACE /
UPDATE / KEEP_SEPARATE decision and the client-scope guard); `graphiti-notes.md` → "Ingestion Pipeline"
(dedup + contradiction handling, old edge preserved with history rather than deleted).

## Consequences

- **Easier:** routine within-scope consolidation needs no human; the leak boundary is absolute.
- **Harder:** a sensitivity classifier and a similarity floor to tune; verified/safety touches must
  route to the gate.
- **Revisit / NEEDS-PROTOTYPE:** Graphiti ingestion cost (CR-25). Ingest full episodes **only at
  crystallize / post-gate**, use zero-extraction `add_triplet()` for facts already structured in NEXUS,
  and **run cost estimates on real task traces before enabling any pre-gate episodic capture** — the
  three-axis budget is a hard ceiling, so continuous capture is not adopted until measured.

## Action items

1. [ ] Scope autonomous consolidation to one client + below a sensitivity threshold; add the similarity
      floor.
2. [ ] Hard-block cross-`[client]`/`[shared]` MERGE; route verified/safety touches to the gate as
      proposals.
3. [ ] Prototype Graphiti ingestion cost before enabling pre-gate capture; ingest at crystallize /
      post-gate only.
