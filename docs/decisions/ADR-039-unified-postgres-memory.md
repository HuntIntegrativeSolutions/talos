# ADR-039: Unified Postgres memory — one database + one markdown vault

**Status:** Accepted
**Date:** 2026-07-12
**Deciders:** Hunt Integrative Solutions LLC
**Supersedes:** ADR-003 (polyglot memory)

## Context

ADR-003 chose four stores (Postgres, Neo4j, Chroma/pgvector, Redis) on the theory that no
single database does all four memory jobs well. Since then:

- Everything shipped (P0–P5) runs on **Postgres + Chroma only**; Neo4j and Redis were
  deferred and nothing has missed them. The dispatcher coordinates via Postgres claims and
  heartbeats; there is no hot-cache or pub/sub need Postgres can't meet at v1 scale.
- The 2026-07-12 research pass (vault: knowledge-graph DB study, agent-OS study) found the
  PG-native landscape matured: **pgvector** is production-grade at our scale (single
  plant, thousands–low-millions of chunks); **Apache AGE** puts openCypher inside
  Postgres; **Kuzu is archived** (Apple acqui-hire, Oct 2025) and Graphiti deprecated its
  backend; LightRAG ships a full GraphRAG engine on PG + AGE + pgvector.
- Deployment target is an **air-gapped single box** (reference: 4-core i5, 16 GB, no
  GPU). Every extra service is another thing to patch, back up, and license offline.
- `board_id` RLS is the tenancy mechanism. It only protects rows **in Postgres** —
  Chroma/Neo4j/Redis each need duplicate app-level tenancy logic (Chroma's
  collection-per-board adapter already exists only as such a workaround).
- Engineers must be able to **audit what the agent knows**. Graph rows and vector blobs
  aren't auditable; diffable markdown is.

## Decision

**One Postgres instance + one markdown vault** replace the four-store design:

| Job (ADR-003) | New home |
|---|---|
| Transactional / event log | Postgres (unchanged) |
| Semantic + episodic recall | **pgvector** in the same Postgres (replaces Chroma) |
| Knowledge & topology graph | `notes/links/tags/chunks` tables + recursive CTEs; **Apache AGE** (Cypher) as the upgrade path when traversal outgrows CTEs. Edges carry Graphiti-style bi-temporal columns (`valid_from`, `valid_until`, `ingested_at`). NEXUS entities seeded as graph nodes via known-fact injection (no LLM calls). NEXUS remains the system of record for PLC knowledge — federation by contract (ADR-027) is unchanged. |
| Working memory / pub-sub / locks | Postgres (claims, `FOR UPDATE SKIP LOCKED`, LISTEN/NOTIFY). Redis cancelled. |
| Human-auditable knowledge | **Markdown vault** (Obsidian-compatible: frontmatter + wikilinks) as the human-facing truth; a Python indexer (file-watcher + frontmatter/wikilink parser) projects it into the PG tables under `board_id` RLS. The DB layer is a derived, rebuildable projection — like Obsidian's own index. |

Backup story: `pg_dump` + rsync of the vault. That is the whole system.

## Options considered

- **A — Keep ADR-003 as written** (add Neo4j + Redis post-v1). Four services, three
  duplicate tenancy layers, JVM on an air-gapped box, and shipped code already proves two
  of the four stores unnecessary. Rejected.
- **B — Chroma + Graphiti/FalkorDB.** Graphiti's bi-temporal model is right, but it has
  no Postgres backend and drags Neo4j/FalkorDB back in. Copy its schema, not its stack.
  Rejected.
- **C — Kuzu embedded sidecar.** Archived upstream; fork immature. Rejected.
- **D — Unified Postgres + vault (this ADR).** Chosen.

## Trade-off analysis

We trade best-of-breed query engines for one operational surface. At v1 scale the trade
is one-sided: RLS covers every row, one backup regime, one service to patch offline, and
CPU-only friendly (HNSW builds capped via `talos.toml [resources]`, or IVFFlat). AGE's
Cypher ergonomics are clunkier than Neo4j's and `pg_upgrade` across major versions needs
dump/restore — accepted; CTEs cover ~80% of vault-shaped queries (backlinks, n-hop
neighborhoods) before AGE is even needed. If graph traversal ever outgrows AGE, this ADR
gets superseded with data already in exportable, open formats.

## Consequences

- **Easier:** single backup/restore; RLS everywhere; vectors joinable to rules, tasks,
  and NEXUS entities in one query; knowledge auditable as text diffs; one fewer (then
  three fewer) services on the air-gapped box.
- **Harder:** we own the indexer (~small: parser + watcher); Chroma→pgvector migration
  (mechanical — same vectors+metadata); AGE learning curve when CTEs run out.
- **Migration:** (1) add pgvector + `notes/links/tags/chunks` migrations; (2) port
  `talos-board-*` / `talos-rules-*` collections from Chroma; (3) point the two Chroma
  read branches at pgvector; (4) delete the Chroma adapter; (5) build the vault indexer.
- **Unchanged:** ADR-005 promotion gate, ADR-014 consolidation boundaries, ADR-023
  crystallize, ADR-027 NEXUS federation — they now operate over PG rows + vault files.

## Action items
1. [x] Alembic migrations: pgvector extension + notes/links/tags/chunks tables (RLS'd).
2. [x] Vault indexer service (owned parser + watcher; `talos vault index`) — commit e95ae77.
3. [x] Chroma → pgvector port of both read branches, via an **interim** `MEMORY_BACKEND`
   toggle (`talos.config.get_memory_backend()`, default `"pgvector"`, `"chroma"` still
   selectable) rather than the one-shot cutover this item originally described. The
   toggle exists solely so a dual-backend round-trip test (`test_memory_roundtrip.py`)
   can prove pgvector and Chroma agree on the same fixture corpus before Chroma is
   actually removed — see action item #7. `scripts/migrate_chroma_to_pgvector.py`
   backfills existing Chroma collections into `chunks`.
4. [x] Bi-temporal edge schema (V0009 links) + NEXUS entity seeding (`talos entities seed`,
   V0010 entities/note_entity_links) — commit 09c9d7e.
5. [x] `talos.toml [resources]` knobs honored by index builds (R8) — `embed_threads` is
   now consumed by `talos.memory.embedding.get_embed_fn()`; `index_type`/
   `hnsw_build_workers` remain documented-but-unconsumed (V0009's ivfflat index is
   static; no runtime index-rebuild tooling exists yet).
6. [ ] Evaluate AGE only when a real query outgrows recursive CTEs.
7. [ ] Remove `talos/memory/chroma_store.py`, the `chromadb`/interim-toggle-adjacent
   dependency, and the `MEMORY_BACKEND` toggle itself, once pgvector has run in
   production long enough to trust the round-trip evidence from action item #3.
