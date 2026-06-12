# Contract — `nexus-federation`

> **What this is:** the frozen read-through contract by which TALOS's own memory federates to the
> NEXUS graph **without duplicating it**. It pins the read-through interface, system-of-record per node
> type, how Graphiti's labels/`group_id` coexist with NEXUS facts, how contradictions are handled, and
> the propose-only / confirmed-only citation edge.
> **What this is not:** the physical store topology (that is **CR-08, NEEDS-HUMAN-DECISION** — see Open
> questions), NEXUS's internal schema, or new architecture. It encodes ADR-001, ADR-003, ADR-007,
> ADR-014, CR-07, and CR-18. The contract is written **topology-agnostic on purpose** so it holds
> whether NEXUS is a separate store reached over MCP or a co-located Neo4j.
>
> **Citation key:** `02 §N` = `docs/integration/02_unified_architecture.md` (`§8 #n` = numbered
> invariants); `ADR-0NN` = `docs/decisions/`; `CR-NN` = `docs/integration/01_conflicts_and_resolutions.md`.

**Status:** Draft for freeze · **Date:** 2026-06-12 · **Deciders:** Hunt Integrative Solutions LLC
**Seam:** memory ↔ capability / NEXUS (`02 §6`, row 3) · **Freeze order:** before Phase 4 (CR-23)

---

## Purpose

NEXUS is the system-of-record for PLC knowledge — tags, routines, rungs, devices. TALOS owns the
operational/episodic/business memory **on top** and must **read through** to NEXUS, never copy its
facts into a second store, or the two drift (ADR-003: "memory federates by contract, not by copy";
`02 §4`). This contract fixes how that read-through works and, critically, the **one-way** rule:
TALOS reads NEXUS; TALOS never writes or invalidates a NEXUS fact (ADR-001, CR-07).

## The two sides it decouples

| Side | Owner | Builds against this contract to… |
| :--- | :--- | :--- |
| **TALOS memory** (consumer) | `memory/` — Postgres + TALOS Neo4j/Graphiti + pgvector | read PLC facts through, seed PageRank, capture contradictions as findings, cite confirmed facts |
| **NEXUS graph** (provider, behind MCP) | NEXUS pack — system-of-record for PLC knowledge | expose read-through queries, the node/edge/finding output schema, and finding status |

The NEXUS side is reached as a capability behind MCP — its tool surface is declared by
[`capability-manifest.md`](./capability-manifest.md). This contract specifies *what TALOS reads and
the rules that govern the reads*; the manifest specifies *how the tools are declared and enforced*.

## Interface

### 1. Read-through query interface **[D] — topology-agnostic**

TALOS issues reads in **MCP-tool terms**, because read-through over MCP is valid whether NEXUS is a
separate store or co-located (this is what makes the contract survive the CR-08 decision). Representative
read tools (from the NEXUS surface): `tag_context`, `tag_full_chain`, `rung_search`,
`address_trace_chain`, `routine_call_tree`, and a bounded subgraph-extraction read for PageRank.

- TALOS treats every NEXUS read as **reference data it does not own**. It may cache for a session; it
  may not persist NEXUS facts as TALOS-owned truth (see Forbidden ops).
- **PageRank subgraph read [D shape / Open bound]:** Phase 4 runs personalized PageRank over a
  **bounded** NEXUS-query subgraph — NetworkX, **k-hop + a hard node budget** — falling back to Cypher
  GDS or edge-weight truncation at scale; the dominant seed is the 50× chat-context boost (CR-10,
  `02 §4`). The bound's exact `k` and node budget are Open.

### 2. System-of-record per node type **[D]**

Verbatim from the federation store table (`02 §4`):

| Store | System-of-record for | TALOS access |
| :--- | :--- | :--- |
| **NEXUS graph** (behind MCP) | **PLC knowledge** — tags, routines, rungs, devices | **read-through only** |
| **Graph — Neo4j + Graphiti** | TALOS **episodic** graph (entities/edges/communities/sagas) | TALOS read+write (own scope) |
| **Postgres** | board, project, event log, gate results, schedule | TALOS read+write |
| **pgvector** | recall index | TALOS read+write |

The boundary TALOS couples to is **NEXUS's structured node/edge output contract, never Rockwell's L5X
or any input format** (ADR-007). NEXUS owns the parsers; TALOS consumes their output.

### 3. Graphiti / NEXUS coexistence **[D]**

- The TALOS episodic graph carries its **own labels** and a **`group_id`** per client scope
  (`{client_scope}` or `shared`); NEXUS facts live under their **own labels**. They coexist without
  schema collision (graphiti-notes: "separate labels, no schema collision; use separate `group_id`").
- Every TALOS graph read/write passes the **`group_id` mediation chokepoint** that injects `group_id ∈
  {client_scope, shared}` and logs the rewrite (`02 §5`, CR-03). A **cross-scope MERGE is forbidden**
  (leak vector, ADR-014).

### 4. Contradiction handling — clash becomes a finding **[D]**

When a TALOS episodic observation disagrees with a NEXUS-documented fact, TALOS **captures a candidate
finding** routed to the audit→PM loop — it does **not** write or invalidate anything in NEXUS, and it
does not auto-write a TALOS `verified` node (CR-07, ADR-014). The finding then rides the normal
lifecycle.

### 5. Propose-only / confirmed-only citation edge **[D]**

A NEXUS finding cited in a TALOS **approved deliverable** must be **`confirmed`-status**. The finding
lifecycle and status surface are declared by [`capability-manifest.md`](./capability-manifest.md)
(`findings.states = queued|proposed|confirmed|dismissed`, `citable_states = [confirmed]`); this
contract **consumes** that surface. Enforcement is the deterministic **`citations-resolvable`** critic,
not a second human gate — NEXUS confirms *facts*, TALOS approves *deliverables*; the two gates are
distinct and nested (CR-18, `02 §8 #4`).

## Invariants & forbidden operations

1. **TALOS never writes or invalidates NEXUS facts.** — anchor **02 §8 #4** ("NEXUS is propose-only at
   its edge and is system-of-record") + **ADR-001** (MCP boundary is the security boundary) +
   **ADR-003** (federate, not duplicate); CR-07 is the resolution detail. *Forbidden:* any TALOS write,
   update, delete, or invalidate against a NEXUS node/edge/fact; routing Graphiti's
   contradiction-invalidation at a NEXUS label.

2. **Graphiti contradiction-invalidation runs only inside the TALOS-owned episodic graph.** — anchor
   **ADR-014** ("anything touching a verified-solution or safety node is a proposal to the gate") +
   CR-07. *Forbidden:* setting `invalid_at` / expiring an edge that lives under a NEXUS label; a NEXUS
   clash must become a finding, never a memory write.

3. **No duplication of NEXUS domain knowledge into a second store.** — anchor **ADR-003** ("Duplicating
   domain knowledge into a second store would create drift — so memory federates by contract, not by
   copy"). *Forbidden:* persisting NEXUS tags/routines/rungs/devices as TALOS-owned truth. Bootstrapping
   TALOS `:Entity` nodes from NEXUS via `add_triplet()` is permitted only as TALOS-scoped episodic
   seed, never as a claim to own the fact — and **does not survive a separate-store CR-08 outcome**
   (`02 §4` gap note).

4. **Unconfirmed findings may never enter an approved deliverable.** — anchor **02 §8 #4** + **ADR-011**;
   CR-18. *Forbidden:* citing a `proposed`/`queued`/`dismissed` NEXUS finding as evidence in an
   approved deliverable. Only `confirmed` is citable; the `citations-resolvable` critic blocks the rest.

5. **Cross-scope graph access is impossible, not merely discouraged.** — anchor **02 §8 #3** (the
   `group_id` chokepoint; one NEXUS per client, CR-11) + CR-03. *Forbidden:* a PageRank traversal or
   read that crosses client scope; the thick/air-gapped edge runs its own NEXUS instance so this is
   physically true, not configured (`02 §7`).

## Versioning rule

The federation seam is the **NEXUS node/edge/finding output-contract**, which "must be specified and
frozen before the memory phase" (ADR-007). Rules:

- The output schema carries an explicit **schema version**; TALOS **pins the version it couples to** and
  reads only fields that version guarantees.
- Evolution is **additive** — new node/edge attributes or finding fields may be added; the meaning of a
  node type's system-of-record ownership and the finding states never changes within a major.
- A NEXUS major-version bump (breaking output schema) requires TALOS to re-pin and a written migration
  note before the new version is read in any approved deliverable.
- Because TALOS couples to the **output contract, not the input format** (ADR-007), a Rockwell L5X
  schema change does not break this contract as long as NEXUS holds the output version.

## Open questions for a human

1. **CR-08 — physical store topology** is **NEEDS-HUMAN-DECISION** and **blocks the final freeze of the
   read-through transport**: (a) a separate TALOS Neo4j + NEXUS read-through over MCP — *the recommended
   default, and the topology this contract is written against* — vs (b) one co-located `talos-neo4j`
   with label-scoped roles (the upstream "Coexistence Contract"). Recommendation: **separate**, because
   it keeps the MCP boundary load-bearing and makes "NEXUS is system-of-record" physically true; the
   in-graph `add_triplet()` cross-link design does not survive that choice (CR-08, `02 §4`/`§9`). The
   *contract above holds under either*; only whether reads are MCP-tool calls vs direct Cypher is
   pinned by this decision.
2. **NEXUS-unavailable behavior** — fail-closed (block the read and surface a finding) vs serve a
   stamped stale cache. No ADR pins this; ROADMAP Phase 2 §268 lists it as a contract question.
3. **PageRank subgraph bound** — the exact `k`-hop depth and hard node budget, and the NetworkX→Cypher
   GDS crossover point (CR-10).
4. **Thick/air-gapped edge pull** — cadence and format of the versioned, read-only, sanitized shared
   pack (git bundle? rsync?). *Mechanism is decided* (graph-as-linker on the mothership; versioned pull
   on the edge, ADR-008); only cadence/format is open (Parking-Lot #4).
