# ADR-003: Polyglot memory — four stores, one per job

**Status:** Superseded by [ADR-039](ADR-039-unified-postgres-memory.md) (2026-07-12)
**Date:** 2026-06-10
**Deciders:** Hunt Integrative Solutions LLC

## Context

Agent memory has four distinct jobs — transactional records, relationships/topology, semantic and
episodic recall, and fast working state. No single database does all four well. Agent Zero's
FAISS-only memory is exactly the limitation this avoids.

## Decision

Use **four stores**, each for the job it is best at, federated (not duplicated) with the NEXUS graph:

| Store | Role | Memory type |
|-------|------|-------------|
| **Postgres** | system of record: board, business records, event log | transactional + episodic log |
| **Graph** (e.g. Neo4j) | knowledge & topology; client→equipment→instance; tag relationships | semantic / structural |
| **Vector** (e.g. Chroma / pgvector) | recall over docs, prior audits, verified solutions | semantic + episodic recall |
| **Redis** | working memory, live-dashboard pub/sub, dispatcher coordination, locks | working |

## Options considered

- **A — One vector store for everything** (Agent Zero style). Simple, but loses relational integrity,
  graph traversal, and fast working state. Rejected.
- **B — One big relational DB.** Strong records, weak at semantic recall and graph traversal. Rejected.
- **C — Polyglot, federated to the NEXUS graph.** Chosen.

## Trade-off analysis

Polyglot adds operational surface (four services), but each query goes to the store that answers it
cheaply and correctly. The NEXUS graph stays the **system of record for PLC knowledge**; TALOS reads
through to it and owns only the operational/episodic/business memory on top. Duplicating domain
knowledge into a second store would create drift — so memory federates by contract, not by copy.

## Consequences

- **Easier:** the right query shape for each question; clean mapping of memory types to stores.
- **Harder:** four services to run and back up; a federation contract to maintain with NEXUS.
- **Revisit:** whether Chroma vs pgvector; whether the graph stays Neo4j or consolidates.

## Action items
1. [ ] Define the read-through federation contract to the NEXUS graph.
2. [ ] Pick the vector store (standalone Chroma vs pgvector in the system-of-record DB).
