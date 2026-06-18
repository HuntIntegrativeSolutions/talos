# ADR-027: NEXUS federation contract — read-through, SoR split, and contradiction resolution

**Status:** Accepted (with one contract-change proposal — see CR-07 section)
**Date:** 2026-06-17
**Deciders:** Hunt Integrative Solutions LLC

## Context

`docs/contracts/nexus-federation.md` defines how TALOS memory federates to the NEXUS graph
without duplicating it. It pins the read-through interface, system-of-record ownership per
node type, Graphiti/NEXUS coexistence, and contradiction handling. This ADR formalizes the
contract as a binding decision record and records one explicit contract-change proposal that
arose in the 2026-06-16 requirements interview.

This ADR is a pointer-and-rationale record; the normative text lives in
`docs/contracts/nexus-federation.md`. One section of that contract is superseded by this ADR's
CR-07 override.

## Decision

The nexus-federation contract is accepted as binding, subject to the CR-07 contract-change
proposal below. Key invariants:

1. **NEXUS is system-of-record for PLC knowledge.** Tags, routines, rungs, devices, finding
   provenance — NEXUS owns them. TALOS reads through; it never writes or invalidates NEXUS facts.
2. **TALOS is system-of-record for episodic graph.** Entities/edges/communities/sagas,
   board records, event log, crystallized rules — TALOS owns them.
3. **TALOS couples to NEXUS's structured output contract, never its input format.** NEXUS owns
   all parsers (ADR-007). TALOS consumes the output.
4. **Graphiti and NEXUS coexist under separate `group_id` per client scope.** No schema collision;
   cross-scope MERGE is forbidden.
5. **NEXUS findings cited in approved deliverables must be `confirmed`-status.** The deterministic
   `citations-resolvable` critic enforces this.

## CR-07 contract-change proposal

**Original CR-07 decision (nexus-federation.md):** When TALOS episodic memory disagrees with a
NEXUS fact, NEXUS wins. TALOS captures the disagreement as a candidate finding routed to the
audit–PM loop. TALOS does not invalidate the NEXUS fact.

**2026-06-16 interview override (replaces CR-07 for contradiction handling at the gate):**
When a TALOS observation contradicts a NEXUS fact, both are surfaced at the human gate. The
reviewer sees both claims and chooses. The winning fact is recorded with the approval event
(who chose, which fact, when).

**Rationale:** A controls engineer reviewing a documentation deliverable is the most authoritative
arbiter of fact correctness for their own plant. Automatically deferring to NEXUS in all cases
removes a critical human check that the guardian doctrine exists to protect. NEXUS wins for
unsupervised, pre-gate context; the human arbitrates at the gate.

**Effect on nexus-federation.md:** The contradiction-handling section is superseded by this ADR.
`docs/contracts/nexus-federation.md` should be updated to reference this ADR-027 override.
This is an explicit, logged contract-change per the interview ground rules.

**Implementation:** The gate view must surface both facts when a contradiction is detected. The
approval event (`task_events`) must record `contradiction_resolution: {nexus_fact, talos_observation,
winner, chosen_by}` when the reviewer resolves a contradiction.

## Open items (from contract)

- NEXUS-unavailable behavior — fail-closed (block + surface finding) vs stale-cache. Not yet pinned.
  Default to fail-closed on the citation critic path.
- PageRank node-budget threshold (CR-10, NEEDS-PROTOTYPE).
- Thick/air-gapped edge vault pull format (mechanism decided; cadence/format open).

## v1 topology (from 2026-06-16 interview)

- NEXUS MCP server runs as a local stdio subprocess on the same workstation as TALOS.
- Neo4j (NEXUS graph) is deferred to post-v1. v1 uses Postgres for NEXUS read cache (ADR-035).
- CR-08 confirmed: separate TALOS Neo4j instance (post-v1) + NEXUS read-through over MCP.

## Consequences

- The contradiction-handling change is load-bearing for the gate UX: the reviewer UI must show
  both facts when a contradiction is flagged during artifact review.
- `nexus-federation.md` is not changed in-place until this ADR is approved; it carries a note
  referencing this ADR-027 override.
