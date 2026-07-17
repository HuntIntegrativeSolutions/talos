# TALOS P3 Pre-Build Decisions

> **Historical note:** this document predates the `platform/` → `talos/` rename; current code
> lives in `talos/`. Retained as written for the historical record.

**Purpose:** This is the handoff to the session that writes the P3 implementation prompt.
Every decision below was made in the P3 pre-interview (2026-06-14). Do not re-read ADR prose
to understand these constraints — the material below is written to be sufficient on its own.

The P3 build phases are:
- **P3a** — PostgresSaver + dead-worker reclaim
- **P3b** — Multi-worker dispatcher + claim-racing + model config + LLM call spans
- **P3c** — Docker FS sandbox
- **P3d** — PM hooks + observability (webhook alerting, task_spans table)

---

## P3a — PostgresSaver + Dead-Worker Reclaim

**ADR-019: Persistence backend**

- Postgres is a **hard requirement everywhere**. No SQLite fallback. No swappable checkpointer.
- `PostgresSaver` is injected at **worker startup** into `build_graph(checkpointer=saver)`.
- `build_graph(checkpointer=None)` remains the default for **unit tests only** (MemorySaver falls
  through).
- Integration tests (`test_checkpoint_recovery.py`) use PostgresSaver via testcontainers.

**ADR-020: Reclaim thresholds**

- `TALOS_HEARTBEAT_INTERVAL_S` env var, default `30`
- `TALOS_RECLAIM_AFTER_MISSES` env var, default `3` → 90s local reclaim window
- At claim time: copy `tasks.max_runtime_seconds → task_runs.max_runtime_seconds`
- `task_runs.max_runtime_seconds` is an **advisory ceiling** (backup if heartbeat mechanism
  fails). Heartbeat misses are the primary reclaim signal.
- Heartbeat fires at **LangGraph node boundaries** (no background thread in P3a).
- Long-running operations: operators set higher `tasks.max_runtime_seconds` on the task.
  There is no false-reclaim protection other than this. Document in operator guide.

**Schema changes needed for P3a:**
- `boards.model_config JSONB` (new column, no default) — needed for ADR-018 board-level model config
- The `rules` table and `rule_ingestion_log` table are **P4**, not P3.
- The `task_spans` table is **P3d** — add it before P3d instrumentation work begins.

---

## P3b — Multi-Worker Dispatcher + Model Config

**ADR-018: Model configuration**

Config cascade (high-to-low, later overrides earlier):
1. `talos.toml [models]` — 12 values (6 ladder steps × primary + fallback)
2. `boards.model_config JSONB` — board-level override for any slot
3. `tasks.model_override TEXT` (existing column) — all-step override for one task

The 6 ladder steps and their slots: `triage`, `research`, `plan`, `gate`, `execute`, `crystallize`.

Model strings are **opaque** (e.g., `"claude-sonnet-4-6"`). TALOS does not validate them. The LLM
client resolves provider/endpoint from its own config. TALOS passes the string through.

**Failure behavior:**
1. Try primary model
2. Try fallback model
3. Both fail → **escalate to human review immediately** (not crash, not increment attempt_no)

**Implementation note:** P3b must add a `platform/config.py` module that loads `talos.toml [models]`,
resolves the effective model for a given step and board, and returns the primary + fallback pair.
This is called at task claim time, not per LLM call.

**LLM call spans (ADR-022, P3b):** Every model call must emit a span with `model_id` (opaque
string) AND `provider` (resolved from the LLM client at call time). This satisfies ADR-017
egress auditing.

---

## P3c — Docker FS Sandbox

**ADR-010 Clarification: Two-level containment**

```
[TALOS worker process]       — has network (Postgres, NEXUS MCP, model APIs)
    └── [Docker subprocess]  — network:none, readOnlyRoot
         └── [agent-generated code runs here]
```

`network:none` + `readOnlyRoot` applies **only to the untrusted code execution subprocess**.
The worker process itself is NOT sandboxed at the network level.

**Soft requirement with durable warning:**
- `TALOS_SANDBOX_MODE=none` bypasses Docker subprocess.
- Must emit a **CRITICAL** log entry to a durable **file** (not stderr only) at startup.
- Warning text must explicitly say this is a security risk and production use is not recommended.
- No concrete deployment scenario currently requires this mode.

---

## P3d — PM Hooks + Observability

**ADR-022: Observability and span-level tracing**

**Schema: new `task_spans` table** (add to `schema-p3.sql` or a migration file)
- Columns: `id`, `board_id`, `task_id`, `run_id`, `parent_span_id`, `span_name`, `model_id`,
  `provider`, `prompt_tokens`, `completion_tokens`, `latency_ms`, `started_at`, `ended_at`,
  `payload JSONB`, `otlp_exported_at TIMESTAMPTZ`
- RLS: **same two policies as `task_events`** — `board_isolation` + `admin_bypass` for `talos_admin`.
  Without `admin_bypass`, spans are a cross-board data leak.

**P3 minimum span set (all woven into P3, not a standalone pass):**

| When | Span name |
|---|---|
| Task claim succeeds | `worker.claim` |
| Another worker claimed first | `worker.claim_race_loss` |
| Dispatcher detects missed heartbeat | `worker.heartbeat_miss` |
| Dead-worker reclaim fires | `worker.reclaim` |
| Spine node starts/ends | `spine.node.{name}.entry` / `.exit` |
| Each critic runs | `spine.critic.{critic_name}` |
| Gate interrupt fires | `spine.gate.interrupt` |
| Gate resumes after human decision | `spine.gate.resume` |
| Post-gate side effects written | `spine.post_gate.write` |
| Any LLM call | `llm.call` (with model_id, provider, prompt_tokens, completion_tokens, latency_ms) |

**Alerting (P3 only):**
- `TALOS_ESCALATION_WEBHOOK_URL` — configured in `talos.toml` or env var
- When `gate_outcome = escalate` → HTTP POST to webhook URL
- No other P3 alerting. Thresholds and multi-channel alerting are P7.

---

## P4 Preview — Memory, Critics, Capability Packs

These decisions are not P3 work but must be accounted for in schema and interface design during P3.

**Graph-store adapter abstraction (ADR-003 update):**
- Mothership: Neo4j + Graphiti (`Neo4jAdapter`)
- Thin edges: Apache Age on Postgres (`ApacheAgeAdapter`)
- P4 defines a `GraphStore` interface with two implementations. Graphiti is wrapped by the
  Neo4j adapter. This is **significant P4 scope** — plan for it.

**Vector store: pgvector** (closed; Chroma/Qdrant not in scope)

**Redis:** Required for multi-worker deployments. In-process dict acceptable for single-worker
edge nodes only.

**Critic extensibility (P4):**
- Discovery: Python entry points (setuptools), `talos.critics` entry point group
- Scope: board-scoped via `boards.gate_config JSONB` declaring required critic names
- `safety_class=True` critics require human approval before first gate run
- CriticSpec **not declared stable until P4**, after VerifierSpec fields are finalized

**Capability pack loading (P4):**
- Discovery: entry points for packaged packs + `talos.toml [packs]` section for local/dev packs
- Admin-only activation, restart-to-activate
- Tool name collision: `talos.toml` pack load order determines precedence (first listed wins)

**Gate outcome configurability (ADR-011 clarification — already decided):**
- `waive` is always available for non-safety critics; no operator restriction possible
- Shortened gate = smaller required-critic set only; all five outcomes remain available
- No `boards.gate_config` outcome restriction mechanism needed

---

## Future Phases — Verifier Critic (P4 spec + P5 impl) and Rule Extraction (P4 schema + P5 impl)

**ADR-021: Verifier critic type → P5 implementation, P4 spec**

P4 delivers:
- `VerifierSpec` dataclass definition
- `register_verifier()` registry function with invariant: `safety_class=True → advisory=True`
- CriticSpec declared stable (with VerifierSpec alongside it)

P5 delivers:
- Verifier runner integrated into gate path
- `VerifierVerdict` dataclass (score: float, passed: bool, reasoning: str)
- score + reasoning stored in `task_gate_results.payload JSONB`

Key constraints the P4/P5 implementer must know:
- **advisory=False** means a nondeterministic LLM blocks before the human gate fires. The
  Guardian doctrine is preserved (human still gets five outcomes) but a nondeterministic LLM
  is the pre-screener. ADR-021 flags this explicitly; operators must set this knowingly.
- **fail_open** is ignored when `advisory=False`. Auto-blocking verifiers always fail closed.
- Rubrics are **per-task** (in task body/metadata), not per-verifier type. `rubric_field` on
  VerifierSpec names the task field holding the rubric text.
- Build LLM call spans for verifier calls (ADR-022) with the same model_id + provider fields.

**ADR-023: Rule extraction in Crystallize → P4 schema stub, P5 extraction logic**

P4 delivers:
- `rules` Postgres table (board-scoped, fast lookup by project/client)
- `rule_ingestion_log` table (dedup tracking via composite key)
- Graphiti triplet schema for factual and procedural rule types

P5 delivers:
- Extraction agent (LLM sub-agent analyzing completed task → rule candidates)
- Ingestion pipeline: dedup check → `add_episode()` for factual/procedural → Postgres insert
  for project-context
- Surfacing layer for verified/safety edge contradictions (gate proposal before invalidation)

Key constraints:
- Auto-extract at client scope; gate required for promotion to shared/verified/safety scope
- Dedup key: `hash(board_id + task_id + crystallize_run_id + rule_content)`
- Cross-scope promotion: gate proposal approved by admin-role human on the **target** scope
- Graphiti's native bi-temporal model handles routine contradictions; surfacing layer fires only
  when a `verified` or `safety` edge is about to be invalidated
- Measure Graphiti ingestion cost before making always-automatic extraction the default (ADR-014 CR-25)

---

## Out of P3 Scope — Explicitly Deferred

| Item | Deferred to |
|---|---|
| VerifierSpec definition and `register_verifier()` | P4 |
| Rule storage schema (`rules`, `rule_ingestion_log`) | P4 |
| Graph-store adapter abstraction (Neo4jAdapter, ApacheAgeAdapter) | P4 |
| Memory federation (Graphiti, pgvector, Redis wiring) | P4 |
| Capability pack loading mechanism | P4 |
| CriticSpec stability declaration | P4 |
| Verifier runner implementation | P5 |
| Rule extraction agent and ingestion pipeline | P5 |
| OTLP exporter (`otlp_exported_at` column wired) | P7 |
| Cockpit span queries and latency views | P7 |
| Multi-channel alerting (thresholds, Slack, email) | P7 |
| Background heartbeat thread (for long-running operation protection) | Revisit at P4 if needed |

---

## Files Written During This Interview Session

- `docs/decisions/ADR-018-model-configuration.md`
- `docs/decisions/ADR-019-persistence-backend.md`
- `docs/decisions/ADR-020-reclaim-thresholds.md`
- `docs/decisions/ADR-021-verifier-critic-type.md`
- `docs/decisions/ADR-022-observability-tracing.md`
- `docs/decisions/ADR-023-rule-extraction-crystallize.md`
- `docs/decisions/ADR-010-clarification-docker-sandbox.md`
- `docs/decisions/ADR-011-clarification-gate-outcomes.md`
- `docs/p3-pre-build-decisions.md` (this file)
