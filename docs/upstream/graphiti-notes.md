# Graphiti — Technical Deep-Dive Notes

> Research date: 2026-06-11  
> Source: `getzep/graphiti` — Apache 2.0 License
>
> **Historical note:** predates ADR-039 — "TALOS's Neo4j memory layer" referenced below was later
> cancelled (recursive CTEs / Apache AGE in Postgres is the current direction for any future
> bi-temporal graph work). Graphiti's own Neo4j dependency (a third-party fact) is unaffected.
> Retained as written for the historical record.  
> Purpose: Evaluate Graphiti as the TALOS temporal knowledge graph layer. Code eligible for adoption.

---

## Executive Summary

Graphiti is a **bi-temporal knowledge graph library** built on Neo4j (or FalkorDB/Kuzu/Neptune). It adds what plain Neo4j lacks for agent memory: automatic entity extraction, deduplication, contradiction detection with fact invalidation, episodic provenance, and sub-second hybrid search. It is the right technology for TALOS's Neo4j memory layer.

**Bi-temporal** means every fact carries two independent time dimensions:
- **Valid time** (`valid_at` / `invalid_at`): When the fact was true in the real world
- **Transaction time** (`created_at` / `expired_at`): When the system learned it / when it was superseded

This allows queries like "what was true about task X on 2026-04-15?" even after the world changed.

| Concern | Plain Neo4j | Graphiti |
| :--- | :--- | :--- |
| Temporal awareness | Manual (you manage timestamps) | Automatic bi-temporal model |
| Contradiction handling | Overwrite (lose history) | Auto-invalidation (history preserved) |
| Entity extraction | You implement | Turnkey LLM pipeline |
| Deduplication | Manual | 3-pass (exact → fuzzy → LLM) |
| Episode provenance | Must track separately | Built-in MENTIONS edges |
| Community detection | Custom algorithm | Label propagation + LLM summary |
| Hybrid search | Custom Cypher + plugins | Vector + BM25 + RRF + cross-encoder |
| MCP server | None | 14 tools built-in |
| Multi-tenancy | Schema-design responsibility | `group_id` partitioning |
| Coexists with NEXUS graph | N/A | Yes — separate labels/group_id |

**NEXUS coexistence:** NEXUS's PLC knowledge graph is already in Neo4j. Graphiti adds new node labels (`:Entity`, `:Episodic`, `:Community`, `:Saga`) and edge types (`:RELATES_TO`, `:MENTIONS`, etc.). These do not collide with NEXUS's schema. Use a separate `group_id` per client for isolation.

**Cost reality:** Graphiti calls 4–15 LLM functions per episode ingested. Budget ~5k–15k tokens per task step. Run cost estimates on real TALOS task traces before committing to continuous ingestion.

---

## 1. Graph Data Model

**File:** `graphiti_core/nodes.py`, `graphiti_core/edges.py`

### Node Types

| Node | Purpose | Key Fields |
| :--- | :--- | :--- |
| **Entity** | Real-world entities (equipment, people, tasks, parameters) | `uuid`, `name`, `name_embedding` (vector), `summary`, `attributes` (dict), `labels` (type tags: Equipment, Person, Task…), `group_id`, `created_at` |
| **Episodic** | Raw source data — one agent turn, one log entry, one document | `uuid`, `content`, `source` (message/json/text/fact_triple), `source_description`, `valid_at` (event time), `created_at` (ingest time), `entity_edges` (list of edge UUIDs), `episode_metadata`, `group_id` |
| **Community** | Cluster of related entities (auto-detected) | `uuid`, `name`, `name_embedding`, `summary`, `group_id`, `created_at` |
| **Saga** | Multi-episode narrative (a task, a conversation, a project) | `uuid`, `name`, `summary`, `last_summarized_at`, `group_id`, `created_at` |

### Edge Types

| Edge | Between | Purpose | Key Fields |
| :--- | :--- | :--- | :--- |
| **RELATES_TO** | Entity ↔ Entity | A **fact**: a named relationship with temporal bounds | `uuid`, `name` (relation type), `fact` (natural language), `fact_embedding` (vector), `valid_at`, `invalid_at`, `expired_at`, `episodes` (source UUIDs), `attributes`, `group_id` |
| **MENTIONS** | Episodic → Entity | Provenance: which entities appeared in this episode | `uuid`, `source_node_uuid`, `target_node_uuid`, `created_at`, `group_id` |
| **HAS_MEMBER** | Community → Entity | Community membership | `uuid`, `source_node_uuid`, `target_node_uuid` |
| **HAS_EPISODE** | Saga → Episodic | Links episodes to their saga | `uuid`, `source_node_uuid`, `target_node_uuid` |
| **NEXT_EPISODE** | Episodic → Episodic | Ordering within a saga | `uuid`, `source_node_uuid` (prev), `target_node_uuid` (next) |

### Temporal Fields on RELATES_TO

| Field | Type | Who Sets It | Meaning |
| :--- | :--- | :--- | :--- |
| `valid_at` | datetime | LLM extraction | When the fact became true in the real world |
| `invalid_at` | datetime | System (on contradiction) | When the fact stopped being true |
| `expired_at` | datetime | System (wall-clock) | When the system learned of the invalidation |
| `reference_time` | datetime | Episode `valid_at` | Fallback timestamp for the episode |

---

## 2. Ingestion Pipeline

**File:** `graphiti_core/utils/maintenance/node_operations.py`, `edge_operations.py`, `graphiti_core/graphiti.py:980-1230`

```python
await graphiti.add_episode(
    name="ACME-PACK-01 Packager 5 Fault Session",
    episode_body="Motor PACK01_M500 tripped on overload at 15:42. Reset attempted. Timer T4:23 at PRE...",
    source_description="TALOS task execution log",
    reference_time=datetime(2026, 6, 11, 15, 42),
    source=EpisodeType.message,
    group_id="acme",
    saga="ACME-PACK-01-migration-q2",
)
```

**Phase 1 — Node Extraction (LLM Call 1):**
- Prompt: episode content + last N episodes as context + `entity_types` schema.
- LLM returns: list of `{name, entity_type, summary}`.
- Dedup pass 1 (deterministic): exact name match → same node.
- Dedup pass 2 (MinHash/Jaccard): fuzzy similarity > 0.9 AND entropy > 1.5 bits → merge.
- Dedup pass 3 (LLM Call 2, if uncertain): "Is 'Motor 500' the same as 'PACK01_M500'?"

**Phase 2 — Attribute Extraction (LLM Call 3, if custom types):**
- For each entity type with custom Pydantic fields, LLM extracts typed attributes.
- Attributes merged with size caps.

**Phase 3 — Edge (Fact) Extraction (LLM Call 4):**
- Prompt: node list + episode content + `edge_types` schema + reference_time.
- LLM returns: list of `(source, relation, target, fact_text, valid_at, invalid_at)`.
- Timestamp extraction (LLM Call 5, small model, if relative time): "next quarter" → ISO date.

**Phase 4 — Edge Deduplication + Contradiction Detection (LLM Call 6 if uncertain):**
- Fast path: exact (source, target, normalized_fact) match → reuse.
- Fuzzy pass: BM25 + vector cosine similarity between candidate and existing edges.
- LLM arbitration if uncertain: "Is 'Motor tripped' the same as 'Motor faulted'?"
- Contradiction detection:
  - If new fact's `valid_at` > old fact's `valid_at` for same relationship → old fact invalidated.
  - Set `old_edge.invalid_at = new_edge.valid_at` (event time).
  - Set `old_edge.expired_at = now()` (transaction time).
  - Old edge preserved with full history; NOT deleted.

**Phase 5 — Community Detection (LLM Calls 7+, optional):**
- Label propagation over entity graph → clusters.
- LLM summarizes each cluster's community node.
- Only run when `update_communities=True` (expensive).

**Phase 6 — Saga Association (optional):**
- Get or create `SagaNode` with `name=saga`.
- Link episode via HAS_EPISODE; link consecutive episodes via NEXT_EPISODE.
- On-demand `summarize_saga()` for LLM summary of accumulated episodes.

**LLM call count per episode:**

| Scenario | Calls | Approximate tokens |
| :--- | :--- | :--- |
| Minimal (no custom types, no community) | 4 | ~4k |
| Typical (custom types, 5 entities, 3 edges) | 6–7 | ~8k–12k |
| Heavy (community update, saga, dedup arbitration) | 10–15 | ~20k–40k |

---

## 3. Temporal Mechanics — Contradiction and Invalidation

**File:** `graphiti_core/utils/maintenance/edge_operations.py:538-573`

### Contradiction Scenario

- Episode 1 (2026-01-15): "PACK01_M500 motor configured for speed 1750 RPM"
  - Creates: `PACK01_M500 -CONFIGURED_AT-> 1750 RPM` with `valid_at=2026-01-15`
- Episode 2 (2026-04-01): "Motor speed setpoint changed to 1800 RPM"
  - Creates: `PACK01_M500 -CONFIGURED_AT-> 1800 RPM` with `valid_at=2026-04-01`

**`resolve_edge_contradictions()` logic:**

```
new_edge.valid_at (2026-04-01) > old_edge.valid_at (2026-01-15)
→ old fact was valid from Jan 15; new fact supersedes it from Apr 1
→ Set old_edge.invalid_at = 2026-04-01 (event time: when reality changed)
→ Set old_edge.expired_at = 2026-06-11T15:43:00Z (transaction time: when system learned)
→ Both edges preserved
```

### Time-Scoped Queries

```python
# Current state: what is the motor's configured speed?
results = await graphiti.search("PACK01_M500 configured speed", group_ids=["acme"])
# Returns: 1800 RPM (1750 RPM has invalid_at set)

# Historical query: what was the speed on March 1?
results = await graphiti.search_(
    "PACK01_M500 configured speed",
    search_filter=SearchFilters(
        valid_at=[DateFilter(start="2026-01-01", end="2026-03-31")]
    )
)
# Returns: 1750 RPM (valid within that window)
```

---

## 4. Search and Retrieval

**File:** `graphiti_core/search/search.py`, `search_config_recipes.py`

### Basic Search

```python
results = await graphiti.search(
    "What failed on ACME-PACK-01 last week?",
    center_node_uuid=ch750005_entity_uuid,  # rerank by graph proximity
    group_ids=["acme"],
    num_results=10,
)
# Returns: list[EntityEdge] — the top-ranked facts
```

### Advanced Search with Config Recipes

```python
results = await graphiti.search_(
    "Motor fault conditions",
    config=COMBINED_HYBRID_SEARCH_CROSS_ENCODER,
    center_node_uuid=motor_uuid,
    search_filter=SearchFilters(entity_types=["Equipment", "Fault"]),
)
# Returns: SearchResults(edges=[], nodes=[], episodes=[], communities=[])
```

### Search Methods

| Method | Mechanism | Speed |
| :--- | :--- | :--- |
| Vector similarity | Embed query; cosine distance on `fact_embedding` | ~5–50ms |
| BM25 keyword | Full-text on `fact` text | ~10–100ms |
| RRF (hybrid) | Merge vector + BM25 rankings | ~100–200ms |
| Graph BFS | Traverse from `center_node_uuid`; rank by hop count | +200–500ms |
| Cross-encoder rerank | LLM re-scores top-K (bool + confidence) | +100–200ms |

**Built-in search config recipes:** `COMBINED_HYBRID_SEARCH_CROSS_ENCODER`, `EDGE_HYBRID_SEARCH_RRF`, `NODE_HYBRID_SEARCH_RRF`, and several others. Use recipes rather than tuning raw parameters.

**Total typical query latency:** 150–500ms (sub-second, ~10× faster than GraphRAG).

---

## 5. The `Graphiti` Class — Core API

**File:** `graphiti_core/graphiti.py`

```python
graphiti = Graphiti(
    uri="bolt://localhost:7687",
    user="neo4j",
    password="...",
    llm_client=AnthropicClient(config=LLMConfig(model="claude-haiku-4-5-20251001")),
    embedder=OpenAIEmbedder(),
    cross_encoder=OpenAIRerankerClient(),
)
await graphiti.build_indices_and_constraints()  # Once per database
```

**Primary methods:**

```python
# Ingest one episode
result: AddEpisodeResults = await graphiti.add_episode(
    name: str,
    episode_body: str,
    source_description: str,
    reference_time: datetime,
    source: EpisodeType = EpisodeType.message,
    group_id: str | None = None,
    entity_types: dict[str, type[BaseModel]] | None = None,  # custom schema
    edge_types: dict[str, type[BaseModel]] | None = None,
    update_communities: bool = False,
    saga: str | SagaNode | None = None,
)

# Ingest a batch (parallelized)
results: AddBulkEpisodeResults = await graphiti.add_episode_bulk(
    episodes: list[dict],
    group_id: str | None = None,
)

# Basic hybrid search → list of facts
edges: list[EntityEdge] = await graphiti.search(query, ...)

# Advanced search → full result set
results: SearchResults = await graphiti.search_(query, config, ...)

# Add a known fact directly (bypass extraction)
result: AddTripletResults = await graphiti.add_triplet(source, edge, target)

# Get recent episodes
episodes: list[EpisodicNode] = await graphiti.retrieve_episodes(reference_time, last_n=10)

# Detect + summarize communities (expensive, run periodically)
communities = await graphiti.build_communities(group_ids=["acme"])

# Summarize a saga (multi-episode narrative)
saga = await graphiti.summarize_saga(saga_id)

await graphiti.close()
```

**Token tracking (cost monitoring):**

```python
graphiti.token_tracker.print_summary()
# Prints per-prompt type and total token usage
```

---

## 6. Entity and Edge Type Customization

Graphiti's extraction pipeline is schema-driven. Define custom types as Pydantic models:

```python
from pydantic import BaseModel, Field

class Equipment(BaseModel):
    """Industrial equipment item (motor, valve, conveyor, etc.)"""
    equipment_type: str = Field(description="Type: motor, valve, sensor, conveyor, etc.")
    manufacturer: str | None = Field(default=None)
    plc_tag: str | None = Field(description="Associated PLC tag, e.g. PACK01_M500")

class FaultEvent(BaseModel):
    """A fault, alarm, or abnormal condition"""
    fault_code: str | None = Field(default=None)
    severity: str = Field(description="critical, warning, info")

await graphiti.add_episode(
    ...,
    entity_types={
        "Equipment": Equipment,
        "FaultEvent": FaultEvent,
        "Task": Task,          # TALOS-defined
        "Parameter": Parameter,
    },
    edge_types={
        "TRIPPED_ON": TripFault,
        "CONTROLS": ControlRelation,
    }
)
```

The LLM uses these models as structured output schemas during extraction, producing typed, validated entities and edges.

**TALOS entity type recommendations:**

| Entity Type | Pydantic Fields | Maps To |
| :--- | :--- | :--- |
| `Equipment` | equipment_type, plc_tag, manufacturer | NEXUS equipment tags |
| `Parameter` | engineering_units, setpoint_type | PLC N-file values, timer PREs |
| `Task` | task_id, ladder_step, status | TALOS Hermes task rows |
| `Fault` | fault_code, severity, plc_address | OTL/OTU ladder rungs |
| `MaintenanceAction` | action_type, performed_by | Task execution logs |

---

## 7. MCP Server

**File:** `mcp_server/`

Graphiti ships an MCP server exposable to Claude, Cursor, and other clients:

```bash
# Docker (recommended for TALOS edge deployment)
cd graphiti/mcp_server && docker compose up
# Listens at http://localhost:8000/mcp/

# stdio transport (Claude Desktop)
uv run main.py --transport stdio
```

**14 available tools:**

| Tool | Action |
| :--- | :--- |
| `add_memory` | Ingest new episode |
| `add_triplet` | Add known fact directly |
| `search_nodes` | Find entities |
| `search_memory_facts` | Find edges (facts) |
| `get_episodes` | List recent episodes |
| `get_episode_entities` | Trace episode → entities + facts (provenance) |
| `summarize_saga` | Refresh saga summary |
| `build_communities` | Detect and summarize communities |
| `delete_entity_edge` | Remove a fact |
| `delete_episode` | Remove episode (cascade delete) |
| `get_entity_edge` | Retrieve edge by UUID |
| `clear_graph` | Wipe all data for a group |
| `get_status` | Health check |

For TALOS, the MCP server is how the TALOS agent interacts with Graphiti during task execution — same pattern as NEXUS tools, but for episodic memory rather than PLC analysis.

---

## 8. Multi-Tenancy

```python
# Per-client isolation via group_id
await graphiti.add_episode(..., group_id="acme")
await graphiti.add_episode(..., group_id="globex")

# Queries automatically scoped
results = await graphiti.search(..., group_ids=["acme"])
```

**Implementation:** All Cypher queries include `WHERE n.group_id IN $group_ids`. Indices include `(group_id, created_at)` for fast filtering.

**Two isolation levels:**

1. **Logical partitioning (same database):** Use `group_id` as tenant ID. Cheaper, faster. Default for TALOS.
2. **Database per tenant:** Pass different `group_id` to trigger driver auto-switch to a separate Neo4j database. Full isolation; use for clients with strict data-residency requirements.

**NEXUS graph coexistence:** NEXUS nodes use labels like `:Tag`, `:Program`, `:Routine`. Graphiti uses `:Entity`, `:Episodic`, `:Community`, `:Saga`. No label collision. Use a separate `group_id` for Graphiti data (`"acme-episodic"`) vs. NEXUS data (`"acme-nexus"`).

---

## 9. Performance Characteristics

| Metric | Value | Notes |
| :--- | :--- | :--- |
| LLM calls per episode | 4–15 | Minimum 4; dedup + community add calls |
| Tokens per episode | ~5k–40k | Scales with entity count and dedup complexity |
| Search latency (hybrid) | 150–500ms | No LLM during query; vector + BM25 + rerank |
| Search latency (vector only) | 5–50ms | No reranking |
| Max entities per group (tested) | 10k–100k | BFS traversal slows above 100k |
| Concurrent ingestion | ~10 eps/sec | SEMAPHORE_LIMIT=10, OpenAI Tier 3 |

**Optimization levers:**
- Increase `SEMAPHORE_LIMIT` (default 10) for faster parallel ingestion.
- Disable `update_communities` for most episodes; run periodically instead.
- Use `add_episode_bulk()` for batch ingestion; tasks parallelize internally.
- Use `add_triplet()` for known facts — bypasses extraction pipeline entirely.
- Use smaller LLM model (Claude Haiku, GPT-4o-mini) for extraction; full model for reranking only.

---

## 10. Integration with LangGraph

**Example:** `graphiti/examples/langgraph-agent/agent.ipynb`

The pattern is straightforward — Graphiti is a tool callable from within a LangGraph node:

```python
@tool
async def search_task_memory(query: str) -> str:
    """Search TALOS task memory for relevant facts and prior findings."""
    results = await graphiti.search(
        query,
        group_ids=["acme"],
        num_results=10,
    )
    return "\n".join([e.fact for e in results])

@tool
async def record_finding(finding: str, equipment_tag: str, reference_time: str) -> str:
    """Record a verified finding to task memory."""
    await graphiti.add_triplet(
        source_node=EntityNode(name=equipment_tag, group_id="acme", labels=["Equipment"]),
        edge=EntityEdge(
            name="HAS_FINDING",
            fact=finding,
            group_id="acme",
            valid_at=datetime.fromisoformat(reference_time),
        ),
        target_node=EntityNode(name="verified", group_id="acme"),
    )
    return "Finding recorded."

# In the LangGraph node:
def research_node(state: TalosState):
    context = search_task_memory.invoke(state["task_description"])
    # ... use context in LLM call ...
    return {"research_findings": [...]}

# After task execution, record the episode:
async def crystallize_node(state: TalosState):
    await graphiti.add_episode(
        name=f"Task {state['task_id']} crystallization",
        episode_body="\n".join([f["summary"] for f in state["execution_log"]]),
        reference_time=datetime.now(timezone.utc),
        group_id="acme",
        saga=f"project-{state['project_id']}",
    )
    return {"crystallized_skill": build_skill(state)}
```

---

## 11. TALOS Memory Layer Design

### Graphiti's Role in the 4-Store Architecture

| Store | Technology | Graphiti's Contribution |
| :--- | :--- | :--- |
| **Record** | Postgres | Task rows, gate results, audit trail (Graphiti doesn't touch this) |
| **Graph** | Neo4j | Graphiti runs here — episodic + entity graph on top of NEXUS's PLC graph |
| **Semantic** | pgvector | Graphiti uses its own embeddings in Neo4j; pgvector handles TALOS's cross-store vector search |
| **Working** | Redis | Task session cache (not Graphiti) |

Graphiti does NOT replace pgvector. Graphiti's vector indices live in Neo4j; pgvector in Postgres serves as the unified semantic search layer across TALOS's Postgres data. They are complementary.

### Episodic Memory Coverage

Every TALOS task execution generates episodes. The episode cadence:

| Trigger | Episode content | Saga |
| :--- | :--- | :--- |
| Task created | Task description, client, PLC target | `project-<id>` |
| Research completed | NEXUS findings, PageRank-selected context | `task-<id>` |
| Plan generated | Plan text, evidence used | `task-<id>` |
| Gate decision | Gate outcome, reviewer, reason | `task-<id>` |
| Execution completed | Execution log summary, tools called, output | `task-<id>` |
| Crystallization | Skill produced, domains covered | `project-<id>` |

**Saga per task:** Each task is a Saga in Graphiti. Task sagas roll up into project sagas. When a new task targets the same equipment as a prior task, `search_task_memory()` surfaces relevant facts from prior execution episodes automatically — without the planner needing to know the prior task ID.

### NEXUS Coexistence Contract

```
Neo4j database: talos-neo4j

NEXUS data (existing):
  Labels: :Tag, :Program, :Routine, :Rung, :Device
  No group_id (or group_id="nexus")

Graphiti data (new):
  Labels: :Entity, :Episodic, :Community, :Saga
  group_id per client: "acme", "globex", "initech"

Shared infrastructure:
  - Same Neo4j instance
  - Separate label namespaces (no collision)
  - NEXUS entities (equipment tags) seeded as Graphiti :Entity nodes via add_triplet()
    to establish cross-links between the two graphs
```

**Bootstrapping cross-links:** When TALOS first encounters a PLC tag in a task, it creates a Graphiti Entity node for that tag via `add_triplet()`. Future episodes that mention the same tag link to this entity, connecting Graphiti's episodic memory to NEXUS's structural knowledge.

---

## Key Patterns for TALOS (Summary)

### Pattern 1 — Bi-Temporal History

Store every fact change as an invalidation, not an overwrite. When a parameter changes, the old value is preserved with `invalid_at` set. Time-scoped queries can reconstruct system state at any past moment.

### Pattern 2 — Episode-Centric Ingestion

Every agent turn, log entry, and task step is an episode. Facts trace back to their source episode via MENTIONS edges. If a fact was wrong, you can trace which episode introduced it and invalidate it precisely.

### Pattern 3 — Saga Grouping

Group related episodes under a Saga (one per task, one per project). Saga summarization gives the planner a high-level narrative of what has happened without reading every individual episode.

### Pattern 4 — `add_triplet()` for Known Facts

When importing from NEXUS (which already has structured data), use `add_triplet()` to bypass LLM extraction. This seeds the Graphiti graph with zero extraction cost for facts already known.

### Pattern 5 — Custom Entity Types = ISA-88 Hierarchy

Define entity types that map to ISA-88: Equipment → Control Module → Equipment Module → Unit. Graphiti's extraction will automatically classify new entities into these types when the schema is provided.
