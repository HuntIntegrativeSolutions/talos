# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

**Pre-alpha, P0–P3 core complete, RT-01 closed, SEC-01 resolved, P7a closed, P4a closed, P4b closed, P5 closed, P5.5-LoopHardening closed, P6-Sim Landing 1 closed, P6-Sim Landing 2 closed.** TALOS is an agent harness for industrial and business operations. The engine port has not been built; the P7a minimal gate UI (below) is the only web view implemented so far — the full Space Agent cockpit (P7b) is still not built. Runnable code:
- `talos/validators/` — capability-manifest validator (P0)
- `talos/critics/` — deterministic gate critics and registry (P2), including RT-06 `no_client_identifiers_in_shared` (P4b)
- `talos/graph/spine.py` — LangGraph spine with five-outcome gate and a 4-branch read fan-out (read_node + read_branch_nexus_secondary + read_branch_chroma + read_branch_rules, merged via `talos/graph/reducers.py`) (P1/P2/P4b/P5)
- `talos/graph/reducers.py` — commutative/associative reducers (`merge_budget`, `merge_disjoint_dicts`) for the spine's multi-writer channels (P4b/RT-21)
- `talos/worker.py` — asyncio dispatcher (`run_dispatcher`, `_worker_slot`), heartbeat, and dead-worker reclaim (P3a/b)
- `talos/api.py` — FastAPI board API with full gate endpoint, JWT auth, review-queue/SLA endpoints, NEXUS cache staleness + invalidate endpoint, `/promote_rule`, and the P7a static UI mount (P1/P2/RT-01/P7a/P4a/P4b)
- `talos/auth/` — local JWT auth: `issue_token`, `validate_token`, `add_user`, `verify_user`, CLI bootstrap (RT-01/ADR-036)
- `talos/pm_escalator.py` — milestone risk escalator: auto-stages an issue-task (HIGH/missed) or auto-dispatches a shortened-gate remediation task (MEDIUM/at_risk) from `task_events` (ADR-016 action item #7 / P4b)
- `talos/rule_promotion.py` — flips `rules.client_scope` to `shared` on promotion-task approval only (P4b)
- `talos/task_origin.py` — shared `tasks.body` origin-marker parser used by the escalator, promotion, spine, and gate API (P4b)
- `talos/crystallize.py` — post-approval rule extraction (factual/procedural/project_context, ADR-023 v1 amendment): dedup via `rule_ingestion_log`, contradiction handling via `superseded_by` (routine, auto) or a `rule_contradiction_review` gate task (verified/safety rows only); sequential, not fanned out (P5)
- `engine/migrations/` — Alembic baseline (V0001) + users table (V0002) + FORCE RLS (V0003) + gate-UI columns (V0004) + NEXUS read cache (V0005) + milestone escalation log (V0006) + rules/rule_ingestion_log + boards.client_identifiers (V0007) + rules.verified/safety/superseded_by (V0008); all future schema changes go here (ADR-034)
- `talos/llm_providers/` — multi-provider LLM abstraction: `LLMProvider` protocol, `ModelRef`, driver registry, `anthropic`/`openai_compat` (aliases `ollama`) drivers (ADR-031)
- `talos/nexus_client.py` — NEXUS MCP wiring over Streamable HTTP: SDK config builders plus real `tools/list`/`tools/call` for non-Anthropic providers (ADR-038/ADR-031)
- `talos/nexus_cache.py` — board-scoped NEXUS read cache: TTL from `boards.model_config`, params-hash keying, cacheable for read + write:offline_artifact tool profiles; wired into the `openai_compat` tool loop only (the Anthropic Agent SDK's MCP dispatch is opaque and uncached) (ADR-035/P4a)
- `talos/memory/` — pgvector-backed stores (ADR-039 replaced Chroma as the primary vector backend): a documentation-chunk store (heading-based chunking, ingested on gate approval, `query()` wired into the spine's read fan-out) and a rule store (`upsert_rule`/`query_rules`, cosine space, wired into `read_branch_rules`); board-scoped under Postgres RLS, local-only embeddings by default (P4a/P4b/P5). `talos/memory/chroma_store.py` still exists as an interim `TALOS_MEMORY_BACKEND=chroma` toggle pending removal (ADR-039 action item #7) — pgvector is the default and the one to build against
- `web/gate/` — the P7a minimal gate-approval web UI: static HTML/vanilla JS/CSS (no build system), served by `talos/api.py` via `StaticFiles` at `/gate`. Login, polling review queue with SLA-overdue highlighting, task review page (Markdown deliverable preview + critic verdicts + NEXUS cache staleness/re-fetch + all five ADR-011 gate outcomes)
- `talos/verifiers/` — deterministic (non-LLM) verifier critics dispatched via `VerifierSpec.deterministic=True`: `emulator.py`'s `emulator_consistency` cross-checks a read-only pylogix reading of a FactoryTalk Logix Echo emulator against NEXUS's documented tag/program inventory for the same PLC, allow-listed via `talos.toml [emulators]` (P6 Landing 2)
- `talos/tests/` — 300+ tests passing, count moves with every landing (run `pytest talos/ -v` for the current total): P1 spine, P2 gate, critic unit tests, P3a/b/c/d suites in `test_p3*.py`, PM scheduling, auth, SEC-01 regression, P3.5 harness, ADR-031 provider tests, P7a gate-UI + outcome-matrix tests, P4a migration/nexus-cache/pgvector-store tests, P4b reducer/fan-out + milestone-escalator + promote_rule/RT-06 tests, P5 extraction/retrieval/fan-out-order-independence tests, P5.5 loop-hardening + budget-enforcement tests, P6 verifier-critic-registry tests + P6 Landing 2 emulator-consistency verifier tests
- `talos/experiments/` — Agent SDK prototype (ADR-029)

## Running tests

Tests require Docker (testcontainers spins up Postgres 16).

Run all tests:
```bash
TALOS_JWT_SECRET=test-secret-dev-only-not-for-prod-use TALOS_NEXUS_STUB=1 .venv/bin/python -m pytest talos/ -v
```

Run P1 spine tests only:
```bash
.venv/bin/python -m pytest talos/tests/test_spine.py -v
```

Run P2 gate tests only:
```bash
.venv/bin/python -m pytest talos/tests/test_p2_gate.py -v
```

Validate a capability manifest JSON file:
```bash
python -m talos.validators.capability_manifest <path/to/manifest.json>
```

There is no build system, Docker setup, or server to start yet. `web/`, `gateway/`, and `memory/` contain only documentation and schema files.

## Repository layout

```
engine/        Postgres schema (schema.sql + schema-additions.sql + schema-p2.sql)
talos/         Python modules; critics/, graph/, validators/, worker, api, tests all implemented
  experiments/ Agent SDK × LangGraph prototype (ADR-029)
web/           Placeholder — Space Agent cockpit (not built)
gateway/       Placeholder — sandboxed proactive loops (not built)
memory/        Placeholder — polyglot memory adapters (not built)
docs/
  ARCHITECTURE.md          High-level system overview
  decisions/               ADR-001 through ADR-029 — binding design decisions
  contracts/               Four frozen seam contracts (board-api, capability-manifest, nexus-federation, widget-sandbox)
  integration/             Five reconciliation documents (integration map, conflicts, unified architecture, red-team, build sequence)
  upstream/                Research notes (harness studies, Agent SDK, Omnigent, Dreaming, PLC connectivity)
BLUEPRINT.md               The authoritative living design document (v0.6)
ROADMAP.md                 Phase-ordered roadmap
```

**Authority rule:** `BLUEPRINT.md` is the authoritative design document. On any conflict between it and other docs, BLUEPRINT wins.

## Architecture

### The Guardian doctrine
> *AI proposes, humans review, deterministic critics gate, and nothing is written to a live system without a human's approval.*

This doctrine is structural, not advisory. It is enforced by two hard boundaries:
1. **MCP boundary = security boundary.** Domain capabilities (NEXUS first) live behind an MCP edge. The orchestrator can be fully compromised and still cannot reach a live processor.
2. **Review gate = human-owned state transition.** A task cannot leave `review` status until every required critic in `task_gate_results` returns `pass` AND `tasks.approved_at` is set by a human.

### Layers (top to bottom)
| Layer | Directory | Role |
|---|---|---|
| Cockpit (view) | `web/` | Web Space Agent board — consumes engine via board-api only; never touches DB |
| Board engine | `engine/` | Postgres source of truth: tasks, DAG, event log, gate, spaces/widgets |
| Critics | `talos/critics/` | Deterministic gate functions (verdict: pass/fail/warn); safety critics are escalate-only, never waivable |
| Orchestration | — | Strategy Ladder: triage → research → plan → gate → execute → crystallize |
| Memory | `memory/` | Unified Postgres (ADR-039, supersedes ADR-003's four-store split): one DB is the system of record, pgvector holds vector search, and a markdown vault holds human-facing docs — no separate graph store or Redis; both were cancelled |
| Gateway | `gateway/` | Sandboxed cron/proactive loops; may notify/propose, never approve |
| Capabilities (MCP) | external | NEXUS (PLC analysis) and future packs — behind MCP; propose-only doctrine at their own edge |

### Database schema
`engine/schema.sql` is the primary schema. `engine/schema-additions.sql` adds PM/scheduling on top.

Key tables:
- `boards` — hard isolation boundary (`board_id` + Postgres RLS, set per-connection via `SET app.board_id = '...'`)
- `tasks` — the core card; lifecycle: `backlog → ready → running → blocked → review → approved | rejected | done | archived`
- `task_gate_results` — one row per critic evaluation; the gate is satisfied only when all required critics pass AND `tasks.approved_at` is set
- `v_gate_status` — read model for gate state
- `task_events` — append-only event log (the board is scrubbable / replayable)
- `spaces` / `space_versions` / `widgets` / `widget_versions` — Space Agent view layer; time-travel versions layout only, never task records
- `milestones` — DAG-driven PM checkpoints; status is computed by trigger, never set directly
- `v_critical_path` / `v_gantt` — DAG forward/backward pass + Gantt projection as SQL views

RLS: every board-scoped table has two policies — `board_isolation` (using `current_setting('app.board_id', true)`) and `admin_bypass` (for `talos_admin`). Cross-`board_id` reads return 0 rows by design.

### Capability manifest contract
`docs/contracts/capability-manifest.md` defines the frozen JSON contract every capability pack must publish before attaching behind MCP. The validator at `talos/validators/capability_manifest.py` enforces it deterministically (no LLM, no network).

Key rules baked into the validator:
- `profile` must be `read` or `write`; unknown = treated as `write`, fail-closed
- `write` tools require `write_kind` ∈ `{"offline_artifact", "sim_only"}` — there is no live-write kind; live ops are not in any agent's reach at all
- `sim_only` tools require a `sim_target` with `kind` and `verify_critic`
- `safety: true` tools escalate-only; a safety critic can never be waived

### Build sequence
The build follows ADR-015's reorder (gate before full dispatcher):
`P0 (schema+contracts) → P1 (single-worker spine) → P2 (critics + 5-outcome gate) → P3 (full dispatcher) → P4 (memory) → P5 (crystallize) → P6 (sim capability) → P7 (cockpit) → P8 (gateway)`

Full detail is in `docs/integration/04_build_sequence.md`.

## Design decisions

All binding decisions are in `docs/decisions/` as ADRs (ADR-001 through ADR-029). Before proposing changes to any design boundary, check whether an ADR already governs it. The four frozen contracts in `docs/contracts/` define the seams between major components and must not be changed unilaterally.

Critical ADRs to know:
- **ADR-001** — TALOS is a platform; NEXUS is a capability behind MCP (not merged)
- **ADR-002** — Board-as-Space; view never touches DB directly
- **ADR-004** — Capability tool profiles; write = offline/sim only; no live-device action exists in any profile
- **ADR-009** — Layered tool policy (intersection-only; each layer can only restrict, never expand)
- **ADR-010** — Worker isolation via session keys (`task:{board_id}:{task_id}:{attempt}`)
- **ADR-011** — Five gate outcomes: Approve / Reject-with-reason / Waive-with-justification / Edit-inline / Escalate
- **ADR-015** — Phase reorder (gate + critics before full distributed dispatcher)
- **ADR-016** — DAG-driven project scheduling (the schema-additions.sql PM layer)
- **ADR-018** — Per-ladder-step model mapping (6 slots × primary+fallback); cascade: talos.toml → boards → tasks
- **ADR-019** — Postgres hard requirement everywhere; PostgresSaver injected at worker startup
- **ADR-020** — Heartbeat and reclaim thresholds (TALOS_HEARTBEAT_INTERVAL_S, TALOS_RECLAIM_AFTER_MISSES)
- **ADR-021** — Verifier critic type; advisory:bool field; safety_class verifiers must be advisory=True
- **ADR-022** — Observability: span-level tracing, task_spans table, RLS, webhook on gate escalation
- **ADR-023** — Rule extraction in Crystallize: factual/procedural/project-context; gate for shared promotion; P5 amendment: Postgres+Chroma only (no Graphiti in v1; Chroma since replaced by pgvector, ADR-039), `superseded_by` + verified/safety-gated review task for contradictions
- **ADR-024** — PLC connectivity: pylogix for initial read; nexus-logix fork of pycomm3 for P6 prep (NEXUS only)
- **ADR-029** — Claude Agent SDK integration: complement pattern; query() safe in pre-gate nodes (empirically verified)
