# ADR-035: Board-scoped NEXUS read cache — TTL, staleness visibility, and force-refresh

**Status:** Accepted
**Date:** 2026-06-17
**Deciders:** Hunt Integrative Solutions LLC

## Context

In the v1 topology, the NEXUS MCP server runs as a local stdio subprocess on the same
workstation as TALOS. NEXUS reads PLC data from an ingested L5X database. Because NEXUS
runs on-process, network latency is not a concern — but `full_plc_documentation` can still
be slow (30–120 seconds) for large PLC programs.

The 2026-06-16 interview decision: board-scoped cache with configurable TTL. Staleness must
be visible at the gate so the human can force a re-fetch.

In v1, Neo4j and Redis are deferred. The board-scoped cache lives in Postgres.

## Decision

### Cache storage

NEXUS read results are cached in a new `nexus_cache` table:

```sql
CREATE TABLE nexus_cache (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    board_id    UUID NOT NULL REFERENCES boards(id),
    tool_name   TEXT NOT NULL,
    params_hash TEXT NOT NULL,          -- SHA-256 of tool params JSON
    result_json JSONB NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    UNIQUE (board_id, tool_name, params_hash)
);
```

RLS: same `board_isolation` + `admin_bypass` policies as all board-scoped tables.

### TTL

Default TTL is configurable per board in `boards.model_config`:
```json
{ "nexus_cache_ttl_seconds": 300 }
```

Default: 300 seconds (5 minutes). A value of `0` disables caching (always fetch live).

### Staleness visibility

When the gate view renders task context, the API returns `nexus_cache_age_seconds` for each
cached NEXUS result used. The reviewer sees, for each NEXUS tool result:
- "Last fetched N minutes ago"
- A "Re-fetch" button that triggers a forced cache invalidation + re-run of the NEXUS call.

The force-refresh does not require a new task; it is a board-API call
(`POST /boards/{id}/nexus_cache/invalidate?tool=full_plc_documentation&task_id={t}`).

### Cache invalidation

- **TTL expiry:** `expires_at < now()` — worker ignores expired rows and re-fetches live.
- **Manual force-refresh:** the reviewer triggers via the gate UI (see above).
- **L5X re-ingestion:** when NEXUS ingests a new L5X (detected via `ingest_l5x` tool call),
  all cached results for that board are invalidated. The proxy (ADR-033) detects `ingest_l5x`
  tool calls (excluded from v1 manifest) and emits an invalidation event.

### Post-v1 (Redis)

When Redis is introduced (post-v1), the `nexus_cache` table is superseded by Redis with the
same board-scoped key namespace and TTL semantics. The Postgres table becomes the durable
fallback for cold starts.

## Consequences

- A new `nexus_cache` table is added to `engine/schema.sql` via an Alembic migration (ADR-034).
- `talos/worker.py` read path checks `nexus_cache` before calling NEXUS MCP; writes result to
  cache on cache miss.
- The gate view API (`/tasks/{id}/gate`) includes `nexus_results_freshness` in its response.
- `TALOS_NEXUS_STUB=1` bypasses cache logic (stubs return immediately).

## Amendment (P4a, 2026-07-05)

Implementation surfaced several corrections and scope decisions against the design above:

1. **`board_id`/`id` types were wrong.** The DDL sketch declared `board_id UUID` and a UUID
   PK. Every real board-scoped table (`boards.id`, `tasks.board_id`,
   `task_gate_escalations.board_id`) is `TEXT` — `boards.id` is a human-chosen slug (`'acme'`,
   `'his-internal'`), not a UUID. The migration (`V0005_nexus_cache.py`) uses
   `board_id TEXT NOT NULL REFERENCES boards(id)` and `id BIGINT GENERATED ALWAYS AS IDENTITY`,
   matching the codebase-wide convention — no table anywhere uses a UUID primary key.
2. **NEXUS runs over HTTP, not local stdio.** This ADR's Context section assumed a local
   stdio subprocess; v1 actually runs NEXUS over Streamable HTTP (ADR-038, `10.0.0.80:8765`).
   This strengthens rather than weakens the caching rationale — real network latency applies
   in addition to NEXUS-side compute time. The design is otherwise unchanged.
3. **Coverage gap — Anthropic path is not cached.** The Claude Agent SDK's `query()` performs
   MCP tool calls to NEXUS internally/opaquely (`talos/llm_providers/anthropic.py`); there is
   no Python interception point for individual tool calls on that path. Caching is implemented
   only in `talos/llm_providers/openai_compat.py`'s explicit tool loop. This is a real,
   documented asymmetry, not an oversight — do not assume full coverage.
4. **Cacheability predicate: read + write:offline_artifact, not read-only.** This ADR's own
   motivating example, `full_plc_documentation` (30–120s), is `profile: "write"`,
   `write_kind: "offline_artifact"` in `capabilities/nexus/manifest.json` — not `profile:
   "read"`. A strict read-only cache would never speed up the tool this ADR was written for.
   `talos/nexus_cache.py::is_cacheable()` caches both `profile == "read"` and
   (`profile == "write"` and `write_kind == "offline_artifact"`) results — both are
   non-live/idempotent per ADR-026's classification, matching the existing `write_grant`
   semantics in `talos.nexus_client.allowed_nexus_tool_names()`. Other write profiles
   (`sim_only`) are never cached.
5. **`nexus_results_freshness` is board-wide, not per-task.** `nexus_cache` has no `task_id`
   column, matching this ADR's own DDL. `GET .../gate` returns every non-expired cache row for
   the board, not scoped to "what this specific task run used." Per-task attribution is
   deferred — not designed here.
6. **L5X-re-ingestion invalidation is deferred.** No proxy exists yet to detect `ingest_l5x`
   calls and emit an invalidation event (that's ADR-033's gateway proxy, not built). Only TTL
   expiry and the manual `POST /boards/{id}/nexus_cache/invalidate?tool_name=...` endpoint are
   implemented in P4a.
