# Architecture Decision Records

Formal decision records for TALOS. Each ADR captures one architecture decision in a uniform template
(Context · Decision · Options considered · Trade-off analysis · Consequences · Action items).

**Authority:** `BLUEPRINT.md` is the design of record. Where an ADR and an upstream research note
(`docs/upstream/`) disagree, BLUEPRINT wins and the ADR notes the discrepancy. Seam-level resolutions
between already-chosen pieces live in `docs/integration/01_conflicts_and_resolutions.md` (cited as
`CR-NN`); ADRs fold those resolutions in by ID rather than cross-linking each other inline.

ADR bodies are immutable historical records — a superseded or amended ADR keeps its original text;
only its Status line changes. This index is the living view: it tracks every file's current status.

## Index

| ADR | Title | Status |
| :-- | :-- | :-- |
| [001](ADR-001-platform-vs-nexus.md) | TALOS is a platform; NEXUS is a capability behind MCP | Accepted |
| [002](ADR-002-board-as-space.md) | Board engine + Space Agent view (the board *is* a Space) | Accepted |
| [003](ADR-003-polyglot-memory.md) | Polyglot memory — four stores, one per job | **Superseded by [ADR-039](ADR-039-unified-postgres-memory.md)** (2026-07-12) |
| [004](ADR-004-capability-tool-profiles.md) | Capability tool profiles — read by default, write = offline/sim only | Accepted |
| [005](ADR-005-cross-client-memory-promotion-gate.md) | Cross-client memory split — `[client]` default, one promotion gate | Accepted |
| [006](ADR-006-strategy-ladder.md) | The Strategy Ladder — a declarable six-step task execution pattern | Accepted |
| [007](ADR-007-parser-ownership.md) | Parser ownership — NEXUS owns parsers; TALOS couples to the output contract | Accepted |
| [008](ADR-008-vault-topology.md) | Vault topology — graph-as-linker on the mothership, versioned pull to thick edges | Accepted |
| [009](ADR-009-layered-tool-policy.md) | Layered tool policy — intersection-only, restrict-never-expand, no-live-writes floor | Accepted |
| [010](ADR-010-worker-isolation.md) | Worker isolation — session keys + restrict-only config inheritance | Accepted |
| [010-clar](ADR-010-clarification-docker-sandbox.md) | Clarification: Docker sandbox containment model (amends ADR-010) | Accepted (clarification record) |
| [011](ADR-011-gate-outcomes.md) | Gate outcomes — five, not two; safety critics escalate-only | Accepted |
| [011-clar](ADR-011-clarification-gate-outcomes.md) | Clarification: gate outcome configurability (amends ADR-011) | Accepted (clarification record) |
| [012](ADR-012-view-platform.md) | View platform — a web Space Agent surface, not native WinUI | Accepted |
| [013](ADR-013-coherence-model.md) | Coherence model — coherence at the planner, isolation at the workers | Accepted |
| [014](ADR-014-consolidation-boundaries.md) | Consolidation boundaries — autonomous within one client scope; cross-scope MERGE forbidden | Accepted |
| [015](ADR-015-phase-reorder.md) | Phase reorder — gate + critics before the full dispatcher | Accepted |
| [016](ADR-016-dag-driven-project-scheduling.md) | DAG-driven project scheduling — board, Gantt, and dispatcher are one system | Proposed |
| [017](ADR-017-data-egress-residency.md) | Data-egress and residency — hosted model endpoints, not air-gapped | Accepted |
| [018](ADR-018-model-configuration.md) | Model configuration — per-ladder-step model mapping (6 slots × primary+fallback); talos.toml → boards → tasks cascade | Accepted |
| [019](ADR-019-persistence-backend.md) | Persistence backend — Postgres hard requirement everywhere; PostgresSaver injected at worker startup | Accepted |
| [020](ADR-020-reclaim-thresholds.md) | Reclaim thresholds — heartbeat interval + miss-count env vars, per-task ceiling | Accepted |
| [021](ADR-021-verifier-critic-type.md) | Verifier critic type — `VerifierSpec`, `advisory:bool`; safety-class verifiers must be advisory | Accepted |
| [022](ADR-022-observability-tracing.md) | Observability — span-level tracing, `task_spans` table, RLS, escalation webhook | Accepted |
| [023](ADR-023-rule-extraction-crystallize.md) | Rule extraction in Crystallize — factual/procedural/project-context rule types; P5 amendment: Postgres + vector store only, no Graphiti in v1 | Accepted |
| [024](ADR-024-plc-connectivity.md) | PLC connectivity — pylogix for initial read; nexus-logix pycomm3 fork for P6 prep | Draft (builder has not confirmed) |
| [025](ADR-025-board-api-contract.md) | Board-API contract — the frozen engine/view seam | Accepted |
| [026](ADR-026-capability-manifest-contract.md) | Capability-manifest contract — frozen MCP capability-pack declaration | Accepted |
| [027](ADR-027-nexus-federation-contract.md) | NEXUS federation contract — read-through / system-of-record split / contradiction resolution | Accepted (transport superseded by [ADR-038](ADR-038-nexus-http-transport.md); rest of contract stands) |
| [028](ADR-028-widget-sandbox-contract.md) | Widget-sandbox contract — iframe isolation, postMessage bridge, lifecycle gate | Accepted |
| [029](ADR-029-agent-sdk-integration.md) | Claude Agent SDK integration — complement pattern; `query()` safe pre-gate (empirically verified) | Accepted |
| [030](ADR-030-budget-enforcement.md) | Budget enforcement — 4-axis budget object, threshold semantics | Accepted |
| [031](ADR-031-multi-provider-llm-config.md) | Multi-provider LLM configuration — provider abstraction, OAuth, air-gap support | Accepted |
| [032](ADR-032-manifest-pin-storage.md) | Manifest pin storage — capability-manifest pin storage and verification | Accepted |
| [033](ADR-033-runtime-tool-policy-enforcement.md) | Runtime tool-policy enforcement — PreToolUse hook + MCP gateway proxy | Accepted (transport superseded by [ADR-038](ADR-038-nexus-http-transport.md); rest of contract stands) |
| [034](ADR-034-schema-migration-versioning.md) | Schema migration versioning — Alembic adoption; all future schema changes go through migrations | Accepted |
| [035](ADR-035-board-scoped-nexus-cache.md) | Board-scoped NEXUS cache — TTL cache, staleness visibility, force-refresh | Accepted |
| [036](ADR-036-human-gate-authentication.md) | Human-gate authentication — local JWT username/password auth | Accepted |
| [037](ADR-037-worker-reclaim-system-role.md) | Worker-reclaim system role — dedicated DB role for reclaim operations | Accepted |
| [038](ADR-038-nexus-http-transport.md) | NEXUS HTTP transport — NEXUS is reached over Streamable HTTP, not stdio | Accepted |
| [039](ADR-039-unified-postgres-memory.md) | Unified Postgres memory — one Postgres DB + one markdown vault; pgvector replaces Chroma; Neo4j/Redis cancelled; supersedes ADR-003 | Accepted |

> **On the numbering:** the **PageRank context map** (`BLUEPRINT.md` §175–178) is named in BLUEPRINT's
> "to formalize next" list but was deliberately **not** assigned an ADR number — CR-10 flags it for
> folding into ADR-003 or a future dedicated PageRank ADR. ADR-017 was added 2026-06-14 to resolve the
> RT-07 air-gap contradiction (data-egress/residency decision).
>
> **On the duplicate 010/011 numbers:** each is one main decision plus a same-numbered clarification
> doc that amends it (not a numbering conflict) — `ADR-010-clarification-docker-sandbox.md` amends
> ADR-010, `ADR-011-clarification-gate-outcomes.md` amends ADR-011. Both are listed above as
> `NNN-clar` rows rather than renumbered, so existing links into either file stay valid.
