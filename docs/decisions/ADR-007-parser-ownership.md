# ADR-007: Parser ownership — NEXUS owns parsers; TALOS couples to the output contract

**Status:** Accepted
**Date:** 2026-06-11
**Deciders:** Hunt Integrative Solutions LLC

## Context

TALOS builds a navigable knowledge map (`corpus → graph → Obsidian vault + report`) and runs graph
algorithms over plant data. The raw inputs are Rockwell formats (L5X, PC5, FT View XML). The question
is who owns parsing them. Vanilla Graphify/Aider assume Tree-sitter over source code and won't parse
an L5X (`BLUEPRINT.md` §159–161).

## Decision

**NEXUS owns the parsers.** NEXUS's parsers emit the nodes/edges; **TALOS couples to NEXUS's
structured output contract, never to Rockwell's L5X (or any input) format**
(`BLUEPRINT.md` §159–161, §310). The agent reads the structured output; Obsidian is the human
projection. TALOS builds the graph and runs PageRank on NEXUS-provided data, respecting this
boundary — `aider-pagerank-notes.md` → "Caching Architecture" names it explicitly: *"NEXUS provides
the raw data… TALOS builds the graph… this respects the ADR-007 boundary (TALOS couples to NEXUS's
output contract, not its internals)."*

## Options considered

- **A — TALOS parses Rockwell formats itself** (or via Tree-sitter). Rejected: duplicates NEXUS's
  mature parser surface, couples TALOS to a brittle vendor format, and splits the system-of-record.
- **B — NEXUS owns parsers; TALOS consumes the output contract.** Chosen.

## Trade-off analysis

This keeps the ADR-001 platform/capability boundary clean — NEXUS is the PLC-format expert, TALOS is
format-agnostic and survives a Rockwell schema change as long as the output contract holds. The cost
is that TALOS depends on NEXUS exposing the right structured output (a contract to maintain in
`nexus-federation`). That is worth it: format-coupling would re-merge the two systems ADR-001
deliberately split.

## Consequences

- **Easier:** TALOS is insulated from vendor-format churn; one parser owner; the `corpus → graph`
  pipeline reads structured nodes/edges, not raw XML.
- **Harder:** the `nexus-federation` output contract (node/edge schema, subgraph-extraction API) must
  be specified and frozen before the memory phase.
- **Revisit / dependency:** CR-16 Path C (modify the ACD to expose UDT internals as BOOLs for
  simulation) would inject bypass rungs *through the NEXUS L5X parser* — a parser-side dependency that
  rides the same escalated Rockwell-test-path decision tracked in ADR-004.

## Action items

1. [ ] Specify the NEXUS → TALOS output contract (nodes/edges schema, subgraph-extraction API) in
      `nexus-federation`.
2. [ ] Confirm TALOS holds no Rockwell-format parsing code.
3. [ ] Track the CR-16 Path C parser dependency against the escalated test-path decision (ADR-004).
