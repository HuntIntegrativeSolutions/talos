# ADR-008: Vault topology — graph-as-linker on the mothership, versioned pull to thick edges

**Status:** Accepted
**Date:** 2026-06-11
**Deciders:** Hunt Integrative Solutions LLC

## Context

TALOS projects plant knowledge into Obsidian vaults for human navigation. With a shared reference body
plus per-client knowledge, naive cross-vault Obsidian wikilinks would duplicate shared content, create
a perpetual sync problem, and risk a client vault leaking into the shared one. Air-gapped edges
(Acme) cannot query a central graph live at all (`BLUEPRINT.md` §162–168).

## Decision

A **shared reference vault + one vault per client**; client vaults may read-link the shared vault,
**never the reverse**. The cross-scope link lives in the **graph, not in Obsidian wikilinks** — the
graph holds one copy of each shared node and the vaults are per-scope *projections*, so there is
nothing to "sync." The exception is a **thick/air-gapped edge** that can't query the graph live: it
gets a **versioned, read-only, sanitized shared pack pulled down git-style** on its own cadence.
Mothership = graph-as-linker, no copies; edge = versioned pull (`BLUEPRINT.md` §162–168).

## Options considered

- **A — Obsidian wikilinks across vaults.** Rejected: duplicates shared nodes, perpetual sync drift,
  and a reverse-link leak risk.
- **B — Copy the shared pack into every vault.** Rejected: N copies to keep current; the same drift
  at larger scale.
- **C — Graph-as-linker (one node, per-scope projections) on the mothership; versioned read-only pull
  for air-gapped edges.** Chosen.

## Trade-off analysis

Graph-as-linker eliminates sync because projections are *derived*, not copied, and the one-directional
read-link (client → shared, never shared → client) makes the leak boundary structural. The thick-edge
exception trades freshness for air-gap safety: an edge pulls a sanitized, versioned pack on its own
schedule rather than querying live. Versioning of the projection follows the git-style model in
`space-agent-notes.md` → "Version History" (isomorphic-git over the home dir); the shared-node
cross-link design comes from `graphiti-notes.md` → "NEXUS Coexistence Contract."

## Consequences

- **Easier:** nothing to sync on the mothership; one copy of each shared node; air-gapped edges still
  get shared knowledge, safely and reproducibly.
- **Harder:** edges run a pull cadence plus a sanitization step; projection rendering must be
  scope-correct.
- **Revisit / related escalation:** the physical **graph store** beneath graph-as-linker — one shared
  Neo4j with label-scoped roles vs a separate TALOS Neo4j + NEXUS read-through over MCP — is
  **NEEDS-HUMAN-DECISION** (CR-08; recommend separate). That decision is **owned by `nexus-federation`
  / ADR-003**, not by this ADR; it merely *bears on* the store under this topology. The mediated-access
  pattern that always admits the `shared` scope and never a second client's (CR-03) is the
  storage-layer enforcer that makes graph-as-linker safe. The vault-topology decision here is firm;
  only the store beneath it is escalated.

## Action items

1. [ ] Implement shared + per-client vaults as per-scope graph projections (no cross-vault wikilinks).
2. [ ] Implement the versioned, sanitized, read-only shared-pack pull for thick/air-gapped edges.
3. [ ] Defer the physical graph-store topology to the CR-08 human decision (`nexus-federation` /
      ADR-003).
