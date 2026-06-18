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
