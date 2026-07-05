# TALOS — Research & Documentation Roadmap

> **Purpose:** Organizes everything decided in the Jun 10 design conversation into a clear picture
> of what's done, what needs research, what needs documenting, and in what order.
> **Status:** Living document. Update as phases complete.

---

## What is already done

These things are decided, documented, and do not need revisiting.

### Identity & branding
- **Name:** TALOS — the bronze guardian of Greek myth, a tireless watchman who lets nothing
  flawed through. Pairs with NEXUS as a family.
- **Mascot:** Bronze automaton sentinel. Antique-gold + charcoal-black palette.
  Single amber glowing core/eyes is the constant signature across all variants.
  Prompts generated for Gemini — hero, emblem, sticker, gate-scene.
  **Action:** generate images, drop files into `assets/` named `talos-hero.png`,
  `talos-emblem.png`, `talos-sticker.png`.
- **License:** MIT © Hunt Integrative Solutions LLC.

### Design decisions (BLUEPRINT.md v0.6)
Everything in BLUEPRINT.md is settled at the design level:
- TALOS is a **project-execution platform**, not an accounting tool. PM is the spine.
- **The Guardian doctrine** — AI proposes, humans review, critics gate, nothing reaches a live
  system without human approval. No agent ever writes to a live processor.
- The **Strategy Ladder** (triage → research → plan relay → gated plan → execute → crystallize).
- The **five gate outcomes** (Approve / Reject / Waive / Edit / Escalate; safety critics escalate-only).
- The **cockpit's KPI**: time-to-confident-approval.
- The **four-store memory** (Postgres, Neo4j, vector, Redis) federated to the NEXUS graph.
- **Hub-and-spoke deployment**: mothership + thin/thick edges per client. Acme = thick.
- **NEXUS is propose-only ICS analysis** behind MCP. Never "device gateway." Never live writes.
- The **five foundational mechanisms** (layered policy, session-key isolation, structured worker
  results, scope/safety-gated consolidation, PageRank-seeded graph context).

### Artifacts in this repo
- `BLUEPRINT.md` v0.6 — the living design doc (authoritative over all other docs when they conflict)
- `docs/ARCHITECTURE.md` — stable public-facing summary (regenerate from BLUEPRINT when they drift)
- `docs/decisions/ADR-001` — platform-not-merge
- `docs/decisions/ADR-002` — board-as-Space
- `docs/decisions/ADR-003` — polyglot memory
- `engine/schema.sql` — Hermes board ported to Postgres with the review gate and spaces tables added
- `docs/decisions/ADR-025` — board-api contract formalization ✓ (2026-06-17)
- `docs/decisions/ADR-026` — capability-manifest contract formalization ✓ (2026-06-17)
- `docs/decisions/ADR-027` — nexus-federation contract formalization + CR-07 override ✓ (2026-06-17)
- `docs/decisions/ADR-028` — widget-sandbox contract formalization ✓ (2026-06-17)
- `docs/decisions/ADR-031` — multi-provider LLM config + OAuth + air-gap ✓ (2026-06-17)
- `docs/decisions/ADR-032` — manifest pin storage (boards table, SHA-256) ✓ (2026-06-17)
- `docs/decisions/ADR-033` — runtime tool-policy enforcement (hook + proxy) ✓ (2026-06-17)
- `docs/decisions/ADR-034` — schema migration versioning (Alembic, raw SQL) ✓ (2026-06-17)
- `docs/decisions/ADR-035` — board-scoped NEXUS read cache (TTL, staleness at gate) ✓ (2026-06-17)

### License audit (from Jun 10 session — repos cloned and verified)
| Upstream | License | What TALOS takes |
| :--- | :--- | :--- |
| Hermes (NousResearch) | MIT | **Ported code** — kanban schema, adapted to Postgres |
| Space Agent (agent0ai) | MIT | **Patterns + code** — spaces/widgets model, time-travel |
| Agent Zero (agent0ai) | MIT | **Patterns only** — hierarchy, memory areas, consolidation |
| OpenClaw | MIT | **Patterns only** — gateway-in-front-of-model, proactivity |
| Claude Code (Anthropic) | Proprietary | **Patterns only** — structured results, PreToolUse rewrite |
| Codex CLI (OpenAI) | MIT | **Patterns only** — exec category bans, session keys |
| Aider | Apache 2.0 | **Patterns only** — PageRank context map |
| LangGraph (LangChain AI) | MIT | **Patterns + code** — BSP execution engine, interrupt/Command gate |
| Graphiti (Zep AI) | Apache 2.0 | **Patterns + code** — bi-temporal graph, episode ingestion |

**Rule:** anything marked "patterns only" means ideas and mechanisms only — zero ported code.
Only Hermes and Space Agent contribute actual code to the repo; those carry MIT notices.

---

## Phase 0 — Upstream deep-dive research

> **Goal:** Document what we actually know about each upstream so future development doesn't
> build on assumptions. Partially done in the Jun 10 session; needs write-up.

### 0A — Hermes board deep-dive
**What the session confirmed:**
- `hermes_cli/kanban_db.py` holds the SQLite schema — reviewed and ported to `engine/schema.sql`
- Key tables: `tasks` (with tenant, claim_lock, heartbeat, circuit-breaker, goal_mode,
  model_override, session_id, skills), `task_links`, `task_events` (append-only),
  `task_comments`, `task_runs`, `kanban_notify_subs`
- **Gap TALOS adds:** `review` status, `task_gate_results`, `approved_by/at`, `board_id`+RLS,
  `spaces`/`space_versions`/`widgets` tables

**Still to document:** dispatcher loop mechanics, heartbeat/breaker intervals, the goal_mode
("Ralph") judge loop, and the WebSocket event streaming pattern.

**Output:** `docs/upstream/hermes-notes.md`

### 0B — Space Agent deep-dive ✓ COMPLETE
**Documented:** Spaces YAML schema, widget lifecycle (create→mount→render→patch→cleanup),
grid system (1-24 col×row, ~85px/cell, infinite pan canvas), agent-authored widget API
(`upsertWidget`/`patchWidget`, turn-staged editing protocol), time-travel via
`isomorphic-git` (whole-`~/` versioning, rollback/revert with conflict detection), NO
built-in kanban or Gantt — both must be first-party TALOS widgets, widget sandbox gap
(NOT strict — main browser context; TALOS must add CSP+postMessage bridge per Contract 4),
tech stack (Alpine.js + Node.js + Electron, no external DB for basic ops), DOX mandatory.

**Key TALOS findings:**
- Widget sandbox is intentionally open — TALOS adds the gate (propose→sandbox-render→critics→pin)
- `space.api` is the clean abstraction layer; TALOS swaps the implementation for a board-API proxy
- Time-travel maps to: space layout versions via `space_versions` table + task event log replay
- L0/L1/L2 layer model maps to: TALOS core / client-scoped / user-scoped widgets

**Output:** `docs/upstream/space-agent-notes.md`

### 0C — Agent Zero deep-dive ✓ COMPLETE
**Documented:** Tool auto-trust hole (5-tier path search, dynamic import, no gate — the hole
TALOS closes), SKILL.md as documentation-only format (agent performs the recipe; no auto-exec),
synchronous subordinate delegation (shared AgentContext — TALOS rejects shared context; adopts
profile-override), three memory areas (MAIN/FRAGMENTS/SOLUTIONS in one FAISS index), background
consolidation pipeline (MERGE/REPLACE/KEEP_SEPARATE/UPDATE/SKIP; 60s timeout; 0.75 replace
threshold), `@extensible` decorator for transparent hook injection (architecture gold), 5-tier
project isolation (.a0proj/ + path-override hierarchy), scheduler model (cron/adhoc/planned).

**Key TALOS findings:**
- `@extensible` pattern: use on every major lifecycle step (task_claim, gate_evaluate, critic_run, etc.)
- Name SOLUTIONS → CRYSTALLIZED in TALOS to avoid confusion with human-verified
- FAISS → pgvector swap; consolidation gets client-scope hard block
- SKILL.md format is the right TALOS skill format; add `propose→review→pin` gate over it

**Output:** `docs/upstream/agent-zero-notes.md`

### 0F — DOX Framework deep-dive ✓ COMPLETE (bonus — not in original plan)
**Documented:** DOX as hierarchical doc/contracts convention (no library, markdown AGENTS.md
only), parent→child inheritance rules (child strict, never weaker), `generate_dox_tree` as
a one-way rendering function (live DB → AGENTS.md tree; read-only; atomic swap; crash-safe),
inlined-vs-live-routed distinction, SKILL.md/DOX relationship (orthogonal — DOX owns the
module; SKILL.md is a deliverable artifact), enforcement mechanisms (protocol + CI test),
TALOS adoption plan (AGENTS.md tree needed for each component dir; `generate_talos_dox_tree`
for project-level summaries).

**Output:** `docs/upstream/dox-framework-notes.md`

### 0D — Aider PageRank mechanism ✓ COMPLETE
**Documented:** Full pipeline from symbol extraction (tree-sitter AST, not ctags) through
graph construction (NetworkX MultiDiGraph, file-level nodes, identifier-reference edges),
Personalized PageRank (alpha=0.85, NetworkX default; personalization vector seeded from chat
context + mentioned identifiers), edge weight formula (6 factors: mentioned=10×,
well-named=10×, private=0.1×, generic=0.1×, chat-context=50×, freq=√N), binary search for
token budget fitting (15% tolerance), three-level caching (diskcache + in-memory tree cache
+ final map cache).

**Key TALOS findings:**
- Graph is routine-level (not rung-level) — analogous to Aider's file-level nodes
- 50× chat-context seed boost is the dominant factor; seed selection matters most
- ISA-5.1 tag naming already satisfies the "well-named" 10× boost heuristic
- `Wrk_` prefix tags map directly to Aider's `_` private-identifier 0.1× penalty
- Full pipeline runnable from existing NEXUS tools (no new indexing needed)
- Start with NetworkX on NEXUS subgraph (<500 nodes per area); defer native Cypher GDS PageRank
- Recommended token budget: 1500 tokens (~25 ranked rung references)

**Output:** `docs/upstream/aider-pagerank-notes.md`

### 0E — OpenClaw gateway pattern ✓ COMPLETE
**Documented:** Full gateway architecture (control-plane diagram, auth, routing, tool
resolution, surface-based dangerous-tool deny list), proactive loop model (cron/heartbeat
as agent turns through same policy pipeline — NOT separately privileged), skill injection
vulnerability (SKILL.md body injected to system prompt with no capability mediation, no
signing, no manifest enforcement — OpenClaw's own RFC says Phase 1-3 is unimplemented),
7-layer tool policy pipeline, optional Docker sandboxing (network:none + readOnlyRoot),
session management (routing, NOT auth — session key provides no authorization boundary),
auth-attempt rate limiting only (no token/cost budgets), threat model atlas (5 trust
boundaries, 5 documented attack vectors including prompt injection and skill injection).

**Key TALOS findings:**
- OpenClaw is explicitly NOT a multi-tenant security boundary (single-operator personal-assistant only)
- Session key = routing label; TALOS replaces with board_id + Postgres RLS (hard auth)
- "Loaded skill = trusted" hole is documented by OpenClaw itself; their fix (Phase 1-3 RFC)
  is unimplemented. TALOS implements it: propose → critics → human approve → pin
- tools.elevated host bypass: NOT adopted. No agent escapes the sandbox in TALOS
- 7-layer policy pipeline is the right structure; TALOS adds an 8th layer (per-skill grants)
- Docker sandbox defaults (network:none, readOnlyRoot:true) adopted wholesale
- 3-axis budget (tokens, time, tool-calls) fills the gap OpenClaw leaves in cost controls
- `openclaw security audit` pattern → TALOS `talos audit` command (capability manifest drift,
  orphaned session keys, unsigned skills, overdue gate decisions)

**Output:** `docs/upstream/openclaw-notes.md`

### 0G — LangGraph deep-dive ✓ COMPLETE
**Documented:** BSP/Pregel execution model (StateGraph compiles to Pregel, not directly executable),
typed channel system (LastValue, Topic, BinaryOperatorAggregate, EphemeralValue), `interrupt()` +
`Command(resume/goto/update)` for human-in-the-loop gating, critical gotcha (node re-executes from
start on resume — all side effects must be in a separate post-gate node), 5-way gate mapping to
Command outcomes, Checkpoint TypedDict (channel_values + channel_versions + versions_seen),
langgraph-checkpoint-postgres backend, 7 stream modes (values, updates, messages, tasks,
checkpoints, custom, debug), time-travel via `get_state_history()` + `update_state()` + fork by
checkpoint_id, Send API for fan-out/fan-in parallel tasks with channel reducers, subgraph namespace
isolation with checkpointer inheritance, LangGraph Platform = closed-source commercial (not in repo).

**Key TALOS findings:**
- Strategy Ladder maps directly to a StateGraph — replace hand-rolled Hermes dispatcher with LangGraph
- 5-way gate is `interrupt()` + `Command`; safety rule: NO side effects before `interrupt()` in gate node
- langgraph-checkpoint-postgres integrates with TALOS's existing Postgres stack; each `thread_id` = task UUID
- Hermes task rows and LangGraph checkpoints coexist — loosely coupled through `task_id`
- `custom` stream mode is the right channel for NEXUS analysis progress and gate events to the cockpit
- `get_state_history()` + fork drives the cockpit timeline scrubber (time-travel replay)
- LangGraph Platform not needed — run as embedded Python library inside TALOS's FastAPI server

**Output:** `docs/upstream/langgraph-notes.md`

### 0H — Graphiti deep-dive ✓ COMPLETE
**Documented:** Bi-temporal graph model (valid_at/invalid_at = event time; created_at/expired_at =
transaction time), 4 node types (Entity, Episodic, Community, Saga), 5 edge types (RELATES_TO facts,
MENTIONS provenance, HAS_MEMBER, HAS_EPISODE, NEXT_EPISODE), full ingestion pipeline (4-15 LLM calls
per episode: extract nodes → 3-pass dedup → attribute extraction → edge extraction → contradiction
detection → community → saga), automatic contradiction handling (old edge gets `invalid_at` set; NOT
deleted; full history preserved), 3-pass entity dedup (exact string → MinHash/Jaccard >0.9 → LLM
arbitration), hybrid search (vector + BM25 + RRF + cross-encoder reranking + BFS graph traversal;
150-500ms typical), `add_triplet()` for known-fact injection (bypasses LLM pipeline), MCP server
with 14 tools, group_id multi-tenancy (logical or per-database), Apache 2.0 license.

**Key TALOS findings:**
- Runs ON TOP of existing NEXUS Neo4j instance — separate labels, no schema collision; use separate group_id
- 4–15 LLM calls per episode is real cost; use `add_triplet()` for NEXUS-sourced known facts
- Contradiction detection is automatic and non-destructive — critical for parameter-change audits
- `SagaNode` maps directly to TALOS tasks (one saga per task); sagas roll up to project sagas
- Bootstrapping pattern: seed Graphiti `:Entity` nodes from NEXUS equipment tags via `add_triplet()`
- Custom entity types (Equipment, Fault, Parameter, Task) drive typed LLM extraction
- MCP server (14 tools) is how the TALOS agent reads/writes Graphiti during task execution
- Temporal query support enables "what was the setpoint on date X?" — directly useful for incident RCA

**Output:** `docs/upstream/graphiti-notes.md`

---

## Phase 1 — Formalize pending ADRs

> **Goal:** Convert the 12 items on BLUEPRINT's "To formalize next" list into proper ADRs.
> These are all **decided** in the BLUEPRINT — the work is writing the formal record, not
> re-deciding. Each ADR follows the same format as ADR-001/002/003.

| ADR | Title | Key decision to formalize |
| :--- | :--- | :--- |
| ADR-004 | Capability read/write tool profiles | `read` by default; `write` = offline/sim only; live writes not in any profile |
| ADR-005 | Cross-client memory split + unified promotion gate | `client` default, one gate for memory/skill/path promotion to `shared` |
| ADR-006 | Strategy Ladder | 6-step ladder + gate-bound evaluator + gate placement rule |
| ADR-007 | Parser ownership | NEXUS owns all parsers; TALOS couples to output contract, never input format |
| ADR-008 | Vault topology | Graph-as-linker on mothership; versioned read-only pull to thick edges |
| ADR-009 | Layered tool policy | Intersection-only, restrict-never-expand; global "no live writes" is the floor |
| ADR-010 | Worker isolation | Session keys + config inheritance (child only restricts, never expands) |
| ADR-011 | Gate outcomes | 5-way: Approve/Reject/Waive/Edit/Escalate; safety critics `waivable: false` |
| ADR-012 | View platform | Web (Space Agent), not native WinUI; thin WebView2 shell is optional |
| ADR-013 | Coherence model | Planner coherence on structured results; worker isolation via session keys |
| ADR-014 | Consolidation boundaries | Autonomous only within one client scope below sensitivity threshold; cross-scope MERGE forbidden |
| ADR-015 | Phase reorder | Gate + critics before full dispatcher (Phase 2 before Phase 1) |

**Output directory:** `docs/decisions/ADR-NNN-title.md`

---

## Phase 2 — Write the contracts

> **Goal:** Specify the four boundaries that de-risk live decisions. These are the "freeze these
> now" items from ADR action lists. Component internals (critics algorithms, gateway scheduler,
> memory adapter implementations) stay as stubs until code lands.

### Contract 1 — Board API surface
*ADR-002 action item: "Freeze the board API surface the view is allowed to call."*

The web view may only read and request through this API — never touch Postgres directly. Spec:
- What GET endpoints exist (tasks, gate status, events, spaces, widgets)
- What POST/PATCH actions the view can trigger (gate outcomes, widget proposals)
- What is explicitly NOT exposed (raw task state writes, direct DB access)
- Sandbox scope: what a widget's postMessage bridge can request

**Output:** `docs/contracts/board-api.md`

### Contract 2 — NEXUS federation read-through
*ADR-001 and ADR-003 action item: "Define the read-through contract from TALOS memory to the NEXUS graph."*

How TALOS memory federates to the NEXUS graph without duplicating it:
- What TALOS reads from NEXUS vs. stores locally
- How the PageRank query is issued (NEXUS MCP tool? direct Neo4j query?)
- What happens when NEXUS is unavailable
- Federation contract for thick/air-gapped edges

**Output:** `docs/contracts/nexus-federation.md`

### Contract 3 — MCP capability manifest
*ADR-001 action item: "Define the MCP capability manifest TALOS expects from a domain pack."*

What any capability (NEXUS or future) must expose to TALOS:
- Required MCP tools (read profile, write profile)
- Resumable cursor interface (required for checkpoint/resume in Phase 1)
- How the capability declares its tool policy restrictions
- How TALOS validates a pack before it can claim tasks

**Output:** `docs/contracts/capability-manifest.md`

### Contract 4 — Widget sandbox policy
*ADR-002 action item: "Define the widget sandbox policy and the propose→review→pin lifecycle."*

The exact sandbox rules for agent-authored widgets:
- CSP header set (no network, no DB, strict)
- Allowed board-API scopes (what a widget can call via postMessage)
- The propose → sandbox-render → critics-run → approve → pin state machine
- How pinned widgets are versioned and rolled back
- `waivable` flag on widget critics

**Output:** `docs/contracts/widget-sandbox.md`

---

## Phase 3 — Resolve the Parking Lot

These are genuinely open questions — not decided yet, unlike the "to formalize" list.

| Item | Question | What's needed |
| :--- | :--- | :--- |
| Planner autonomy threshold | How is it computed? Who tunes it? | Research: how other systems compute task complexity scores. Propose a metric. |
| Status report authorship | Exact boundary between agent-drafted data and human commentary | Prototype with a real project status and iterate. |
| Two-step live-op confirmation | UX for the second human confirmation before a live operation | Design the UX interaction; define what "a human performs by hand" looks like step-by-step. |
| Vault pull cadence | How often the sanitized shared pack refreshes to thick edges; what format | Research: git bundle? rsync? Define the pack format. |

**Output:** One "decision-deferred" note per item in `docs/decisions/` (documents the question,
the options considered, and why it's deferred).

---

## Phase 4 — Gateway / proactivity layer

> This layer is thin in the BLUEPRINT and was flagged as still needing work. Do this after
> the ADRs and contracts land, since the gateway's sandbox depends on the layered tool policy
> (ADR-009) and the board API contract.

Topics to cover:
- Cron loop architecture (how proactive loops are registered and sandboxed)
- Which loops exist: status digests, PM reminders, audit-freshness checks, overdue-deadline nudges
- Multi-channel notification: Slack, email, webhook
- How the gateway is "sandboxed away from NEXUS tools" at the implementation level
- Rate limiting and the three-axis budget applied to gateway loops

**Output:** `gateway/README.md` (expanded from stub), `docs/gateway-design.md`

---

## Ongoing — Keep the BLUEPRINT current

BLUEPRINT.md is the living design of record. ARCHITECTURE.md is a stable summary.

**Rule:** when they conflict, BLUEPRINT.md wins. Regenerate ARCHITECTURE.md from BLUEPRINT
whenever a major section changes.

**Parking Lot at the bottom of BLUEPRINT.md** is where new ideas land before they're evaluated.
Ideas → evaluated → either formalized into an ADR or marked deferred. Nothing disappears.

---

## v1 Charter (decided 2026-06-16)

**First use-case:** Full PLC documentation — a controls engineer triggers TALOS to analyze a
PLC program, TALOS uses NEXUS (read-only, stdio subprocess) to generate a complete Markdown
documentation set, deterministic critics gate the artifact, and the engineer approves it.
Zero unauthorized live writes. Deliverable: Markdown documentation package.

**v1 persona:** Controls/automation engineer.
**Deployment:** On-prem at engineer's workstation. Air-gapped by default.
**LLM:** Multi-provider configurable — Claude, Codex, Deepseek, Ollama (local), any OpenAI-
compatible endpoint. API key or OAuth. ADR-031.
**Auth:** JWT local auth server (username/password). ADR-036/RT-01.
**v1 success metric:** (1) X gated deliverables approved with zero live writes, (2)
time-to-confident-approval < target minutes, (3) N non-HIS engineers on real projects.
**v1 non-goals:** No business-ops capabilities. Doc-gen skills (fds, soo, etc.) are v1.x.
**Cockpit:** Minimal gate-approval UI (Markdown preview + critic verdicts + five outcome
buttons) in v1. Full Space Agent cockpit (P7) is v1.x, after P5/P6 in canonical order.

---

## Current status and next phase sequence

P0–P3 core are complete and tested (67 tests passing).
ADR-025 through ADR-028 formalize the four frozen contracts.
ADR-031 through ADR-036 capture the 2026-06-16 interview decisions and JWT auth.

**Phase sequence (revised after 2026-06-16 requirements interview):**

```
P0-Foundation (now)
  ├── RT-01: JWT local auth server ✓ (ADR-036; closes RT-16; 63 tests passing)
  ├── Alembic baseline migration ✓ (ADR-034; V0001+V0002 migrations live)
  ├── Security review ✓ SEC-01 resolved 2026-06-18 (67 tests; SEC-02–07 are Low/Medium, non-blocking)
  ├── SEC-03 resolved 2026-06-18 ✓ (FORCE RLS V0003; DSN flip; heartbeat fix; talos_system reclaim; 70 tests)
  ├── RT-09: Policy-presence CI test ✓ (FORCE RLS + cross-board isolation test — SEC-03 work; cover PM tables done)
  ├── RT-14: Disposition all ~85 NEXUS tools in capability manifest (SoR-writers excluded) ✓ (`capabilities/nexus/manifest.json`; 90 tools dispositioned, 18 excluded; `test_rt14_nexus_manifest.py`)
  ├── RT-20: Idempotency key spec — attempt-independent key; atomic UNIQUE-constraint insert
  ├── RT-06: no-client-identifiers-in-shared critic (non-waivable; ADR-005 enforcement)
  └── ADR-025–028, ADR-031–036 (written 2026-06-17) ✓

P3.5-Harness (prerequisite before P4) ✓ closed 2026-07-05
  ├── Prerequisite: Multi-provider LLM config (ADR-031) — Claude + Ollama + OAuth (deferred, out of P3.5 scope)
  ├── Wire real NEXUS MCP server — Streamable HTTP, not stdio (ADR-038) ✓
  │     (TALOS_NEXUS_STUB=1 remains the CI/test default; real wiring is opt-in via
  │     TALOS_NEXUS_STUB unset + TALOS_NEXUS_URL)
  ├── Run full_plc_documentation on real HIS L5X (local dev, NDA data, never CI) ✓
  │     (docs/p35-harness-results.md — 2 live runs, real PLC (identifier withheld))
  ├── [Blocker cleared] Heartbeat/reclaim correctness under talos_app RLS ✓ (SEC-03 / ADR-037)
  └── Exit criteria (stub-mode CI tests in talos/tests/test_p35_harness.py +
      live evidence in docs/p35-harness-results.md) ✓
        - reclaim no double-apply ✓
        - heartbeat during long NEXUS call ✓
        - model fallback on provider error ✓
        - budget hard-cap → escalate not crash ✓

P4-Memory (Postgres + Chroma in v1; Neo4j + Redis deferred)
  ├── Chroma: documentation chunk embeddings (configurable local/cloud embedding model)
  ├── Board-scoped NEXUS read cache in Postgres (ADR-035; TTL, staleness at gate)
  ├── Commutative/associative reducers for parallel Postgres+Chroma read fan-out (DoD #3)
  ├── Lightweight /promote_rule endpoint (ADR-005)
  ├── Deferred P3 DoD #5: milestone safety-significant gating (HIGH-severity auto-stage)
  └── [Post-v1] Neo4j episodic graph + Redis hot cache

P5-Crystallize
  ├── Automatic post-approval trigger
  ├── Three-type rule extraction: factual / procedural / project-context (ADR-023)
  ├── Chroma semantic retrieval at task start
  └── Commutative reducers for parallel crystallize fan-out

P6-Sim (scope TBD for v1)
  └── Rockwell-based PLC emulator via pylogix; custom library evaluation underway

P7a-MinimalGateUI (v1, built alongside P0 JWT auth)
  ├── Thin web page: Markdown artifact preview + critic verdicts + five gate outcome buttons
  ├── JWT local auth server wired in (RT-01)
  ├── OS desktop notification + optional SMTP email
  └── Board-level SLA config (no system-level default)

P7b-FullCockpit (v1.x, sequential after P5/P6)
  └── Full Space Agent web cockpit: spaces, widgets, time-travel, Gantt, event stream

P8-Gateway
  └── One proactive loop: documentation freshness check (propose-only, never auto-approve)
```

**Deferred to post-v1:**
- Neo4j (NEXUS graph + TALOS episodic graph) — introduced when graph traversal patterns proven
- Redis (hot task state + pub/sub) — introduced when hot-cache patterns proven
- Business-ops capabilities (QuickBooks, Drive, Asana, Gmail, Calendar, MS365)
- Doc-gen skills as gated TALOS capabilities (fds, soo, io-list, etc.) — v1.x fast-follow
- Full Space Agent cockpit (P7b) — v1.x
- OT-security network monitoring (Snort IDS triage) — read-profile capability pack + P8 Gateway propose-only loop; see docs/upstream/snort-ot-monitoring-notes.md

P3 deliverables (implemented):
- **P3a** — PostgresSaver + reclaim reconciliation (replace MemorySaver; RT-20) ✓
- **P3b** — DAG-priority dispatcher, heartbeat (RT-04, RT-10, RT-21; multi-writer reducers deferred to P4 as DoD #3) ✓
- **P3c** — Docker sandbox (`network:none`, `readOnlyRoot` per ADR-010; RT-27, RT-28) ✓
- **P3d** — ADR-016 PM hooks + severity-gated escalator (milestone safety-gating deferred to P4 as DoD #5) ✓

---

## Reference: Conversation provenance

The Jun 10, 2026 design session (claude.ai/share/717d89cc...) produced:
- BLUEPRINT v0.1 → v0.6 through iterative refinement
- Three ADRs (ADR-001, ADR-002, ADR-003)
- `engine/schema.sql` (Hermes board ported from actual GitHub source)
- License audit of all four original upstreams (all MIT)
- Name and mascot decisions

Claude was instructed to hold all ideas and not build until told — scaffold was generated as a
draft checkpoint, explicitly not locked. All docs from that session are living drafts.
