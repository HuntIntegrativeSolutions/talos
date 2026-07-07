# ADR-023: Rule extraction in Crystallize — three types, ADR-014 scope boundary, P4 schema / P5 extraction

**Status:** Accepted
**Date:** 2026-06-14
**Deciders:** Hunt Integrative Solutions LLC

## Context

The Crystallize step (P5) consolidates completed task knowledge into long-term memory. The
builder wants Crystallize to also extract **generalizable rules and patterns** from completed
tasks — e.g., "always verify interlock before modifying setpoint on this equipment type" — and
store them in the knowledge layer for future task context. This is the TALOS analogue of the
`/dream` pattern used in chase-ai: post-task knowledge pruning and promotion.

This involves ADR-014 (consolidation boundaries), ADR-003 (four memory stores), and RT-22
(Graphiti `add_episode()` is not idempotent). The key questions: what rule types are in scope,
what requires a human gate, how contradictions are handled, and when the schema lands.

## Decision

**Extract all three rule types in P5. Auto-ingest at client scope per ADR-014. Gate at
promotion to shared/verified/safety scope. Graphiti native bi-temporal contradiction detection
with a surfacing layer for verified/safety edges. Schema stub in P4.**

### Rule types (all three in scope for P5)

| Type | Example | Storage |
|---|---|---|
| **Factual** | "PLC tag T_PUMP_01 maps to motor M-100 in area West" | Graphiti triplet + pgvector |
| **Procedural** | "Always verify interlock Z before modifying setpoint on equipment type X" | Graphiti triplet + pgvector |
| **Project-context** | "On the Acme project, tags follow ISA-5.1 with Wrk_ prefix" | Postgres `rules` table |

Factual and procedural rules are stored as Graphiti triplets (Neo4j graph edges with bi-temporal
metadata) AND indexed into pgvector for semantic retrieval. Project-context rules are stored in a
Postgres `rules` table (board-scoped, fast lookup by project/client) because they are operational
config rather than knowledge-graph entities.

### Extraction gate (ADR-014 boundary)

- **Auto-extract at client scope:** Routine extraction (all three rule types from a completed
  task, within one client's board) runs autonomously. No human gate.
- **Gate required for promotion:** When an extracted rule is proposed for promotion to `[shared]`
  scope, a verified node, a safety node, or a cross-client target, a gate proposal is created.
  A human with admin role on the **target** scope reviews and approves or rejects promotion.
  Cross-`[client]`/`[shared]` autonomous MERGE remains forbidden (ADR-014).

This is consistent with ADR-014's "autonomous within one client scope below sensitivity threshold"
rule. Extraction itself is below the threshold; promotion crosses it.

### Contradiction handling

1. **Routine contradictions (non-verified, non-safety edges):** Rely on Graphiti's native
   bi-temporal model. When a new extraction contradicts an existing triplet, Graphiti sets
   `invalid_at` on the old edge and creates a new edge with `valid_from = NOW()`. The old edge is
   preserved in history, not deleted.

2. **Verified or safety edge contradictions:** Before `invalid_at` is set on a `verified` or
   `safety`-tagged edge, TALOS **surfaces the conflict as a proposal to a human reviewer**. The
   reviewer sees: the existing verified/safety edge, the proposed replacement, and the task it
   came from. The human approves or rejects before invalidation is written. This is consistent
   with ADR-014's requirement that verified/safety node writes route to the gate.

### Dedup key (RT-22 idempotency)

Graphiti's `add_episode()` is not idempotent (RT-22). The post-gate node must be idempotent.
Each extraction episode uses a composite dedup key:

```
dedup_key = hash(board_id + task_id + crystallize_run_id + rule_content)
```

`crystallize_run_id` scopes the key to one crystallize execution. `rule_content` prevents the
same rule extracted from two different tasks from being treated as the same episode. Before
calling `add_episode()`, TALOS checks whether a row with this key already exists in a
`rule_ingestion_log` table; if so, it skips the call.

### Cross-client promotion path

Cross-client promotion (a safety procedure from Acme's board promoted to `[shared]` scope for
all clients) requires:

1. The extractor flags the rule as a candidate for cross-scope promotion.
2. A gate proposal is created with the candidate rule, the source task, and the target scope.
3. A **human with admin role on the target scope** (shared scope) reviews and approves.
4. On approval, the rule is ingested into the shared-scope Graphiti namespace and pgvector index.

This path does not require TALOS to process the promotion autonomously. The gate is the
authorization boundary; the human approval is what crosses the ADR-014 line.

### Rule retrieval

Future tasks retrieve extracted rules via:

1. **Primary:** semantic vector search (pgvector). The task's deliverable or goal is embedded;
   nearest-neighbor search returns the most relevant extracted rules.
2. **Secondary:** graph traversal (Graphiti/Neo4j). When the task context includes known
   equipment entity IDs or tag names, a graph traversal from those entities to connected rules
   surfaces procedural and factual rules specific to that equipment.

The dispatcher passes retrieved rules into the task context before the Strategy Ladder begins.

### Build phase

- **P4 (memory federation):** Schema stub — add `rules` table (Postgres, board-scoped) and
  `rule_ingestion_log` table (for dedup tracking). Define Graphiti triplet schema for rule types.
  VerifierSpec and CriticSpec declared stable in P4 as well.
- **P5 (Crystallize):** Implement the extraction agent (LLM sub-agent that analyzes the completed
  task and produces rule candidates), ingestion pipeline (dedup check → `add_episode()` for
  factual/procedural → Postgres insert for project-context), and the surfacing layer for
  verified/safety edge contradictions.

## Options considered

- **A — Gate every extraction.** Conservative, defeats episodic memory. Rejected per ADR-014.
- **B — Gate-free extraction including cross-scope.** Violates ADR-014 cross-scope MERGE
  prohibition. Rejected.
- **C — Factual and procedural only; skip project-context (too brittle).** Rejected: all three
  types were requested and project-context rules (naming conventions, team preferences) are
  valuable for tool-use correctness even if they have shorter lifetimes.
- **D — Auto-ingest at client scope, gate at promotion (chosen).** Consistent with ADR-014.

## Consequences

- **Easier:** routine extraction is fully autonomous; only promotion requires human attention;
  Graphiti's bi-temporal model handles contradiction history without custom deletion logic.
- **Harder:** dedup key and `rule_ingestion_log` table required for idempotency; surfacing
  layer for verified/safety edge contradictions adds P5 complexity; P4 must include rule schema
  even though extraction ships in P5.
- **Revisit:** Graphiti ingestion cost (ADR-014 CR-25). Running 4–15 LLM calls per crystallize
  for extraction could be expensive at scale. Measure actual cost on real task traces in P5 before
  making always-automatic the default; add a circuit-breaker if cost exceeds the three-axis
  budget cost ceiling.

## What this closes

- Defines the P5 rule extraction scope and gate boundaries.
- Establishes the P4 schema stub requirement.
- Resolves RT-22 (non-idempotent `add_episode()`) via composite dedup key.
- Closes ADR-014's "revisit: Graphiti ingestion cost" action item with a "measure before
  committing to always-automatic" stance.

## Action items

1. [x] Add `rules` table and `rule_ingestion_log` table to P4 schema migration.
2. [~] Define Graphiti triplet schema for factual and procedural rule types in P4. — retired as
   moot for v1; see amendment below.
3. [x] Implement extraction agent, ingestion pipeline, and dedup logic in P5.
4. [x] Implement the verified/safety edge surfacing layer in P5 (pre-invalidation gate proposal).
5. [x] Wire retrieved rules into task context in the dispatcher (P5 or P6).
6. [~] Measure Graphiti ingestion cost on real traces before enabling always-automatic extraction.
   — retired as moot for v1; no Graphiti path exists to measure.

## Amendment (2026-07-06, P5-Crystallize implementation)

P5 implements all three rule types against **Postgres + Chroma only** — the Graphiti/Neo4j
bi-temporal design above (factual/procedural as graph triplets with `invalid_at`) is deferred
post-v1 per ROADMAP.md and was never built. This amendment records what actually shipped,
without weakening the boundaries this ADR established:

- **Storage:** all three rule types (factual, procedural, project-context) are stored
  identically, in the Postgres `rules` table (V0007, verified/safety/superseded_by columns added
  V0008) plus a Chroma `talos-rules-{board}` collection (`hnsw:space: cosine`) for semantic
  retrieval. There is no separate Graphiti-triplet path for factual/procedural in v1.
- **Contradiction handling** replaces Graphiti's native bi-temporal model with a deterministic
  heuristic available from Postgres + Chroma alone: same `rule_type` AND (identical normalized
  content OR Chroma cosine distance < 0.15 against the existing rule's embedding), excluding
  rows inserted earlier in the same crystallize run. A contradiction against an existing row with
  `superseded_by IS NULL` and `verified=false AND safety=false` auto-supersedes
  (`UPDATE rules SET superseded_by = <new_id>`, no gate) — the "routine" path this ADR already
  authorized. A contradiction against a `verified=true` OR `safety=true` row does **not**
  auto-supersede; it creates a review task (`talos_origin: "rule_contradiction_review"`,
  `talos/crystallize.py`) that flows through the same gate/critic/human-approval pipeline as
  `/promote_rule` — including a dedicated `deliverable_node` branch
  (`build_contradiction_review_deliverable`) that shows the reviewer both the existing rule and
  the proposed replacement side by side. Only a subsequent `approve` outcome sets
  `superseded_by`. This preserves this ADR's "verified/safety edge writes route to the gate"
  requirement without Graphiti.
- **Dedup key and gate boundaries are unchanged**: `dedup_key = hash(board_id + task_id +
  crystallize_run_id + rule_content)` still gates every insert via `rule_ingestion_log` before
  any `rules` row is written; cross-client/cross-scope autonomous promotion is still forbidden;
  promotion to `[shared]` still requires the existing `/promote_rule` + RT-06 gate (untouched by
  P5). `crystallize_run_id` is derived deterministically as `f"{task_id}:{run_id}"` (not a fresh
  UUID per call), so a replayed `on_task_approved` hook produces the same dedup keys and cannot
  re-insert.
- **Retrieval** is implemented as a 4th branch (`read_branch_rules`) in the spine's read fan-out
  (`talos/graph/spine.py`), querying the Chroma rules collection with the task body, `k` from
  `talos.toml [memory] retrieval_k` (default 5). Retrieved rules are labeled with `rule_type`,
  `verified`, and age (`created_at`) and carry a header (`RULE_CONTEXT_HEADER`) stating unverified
  memory is a suggestion, not an instruction. There is no execution-stage LLM prompt in the spine
  yet to consume this context live — `rule_context` is staged as a `SpineState` field for a
  future execution step to read.
- **Extraction is sequential**, not fanned out in parallel — there is no write-side reducer to
  prove commutative. What `talos/tests/test_p5_fanout.py` proves order-independent is the final
  live-content set of the dedup/contradiction candidate pipeline.
- **Budget accounting**: extraction fires post-gate, detached from the spine's per-task budget
  reducer channel — spend is observable via `call_model`'s `llm.call` span, but there is no
  post-gate per-task budget cap enforcement for extraction calls.
