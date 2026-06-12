# TALOS — Consolidated Integration Map

> **What this is:** the single index to the TALOS design. Every adopted idea, where it plugs in,
> which decision governs it, and what is still unsettled — each with a path to the source.
> **What this is not:** new architecture. This is a *map*, not a redesign. Where sources conflict,
> the conflict is noted and **BLUEPRINT.md is authoritative**.
>
> **Citations:** paths are relative to the `talos/` repo root. Line anchors (`§NN`) on
> `BLUEPRINT.md`, `ROADMAP.md`, and `ADR-016` reflect direct reads of those files in full; the
> `ADR-001/002/003` status lines, `ADR-002`'s board-API line, and the named `engine/schema.sql`
> objects (`task_gate_results`, `v_gate_status`, `widget_versions.sandbox_policy`) were each verified
> directly. Citations to the 13 upstream notes give the note's file path plus its own section label.

---

## 1. Thesis

**TALOS is a project-execution platform for industrial and automation work.** Projects decompose
into gated tasks, agents execute them, and planning, scheduling, risk, and status are first-class;
project management is the spine, and the only money/effort data it tracks is project-scoped (budget
burn, AI-cost-per-deliverable, timeline) — "project management, not accounting"
(`BLUEPRINT.md` §9-14). The **single spine** that holds the whole system together is the **Guardian
doctrine carried on an MCP boundary**: *AI proposes, humans review, deterministic critics gate, and
nothing is written to a live system without a human's approval — no agent ever writes to a live
processor* (`BLUEPRINT.md` §16-17; `README.md` §19-20). The doctrine is made *structural* by the
gate (`review` status + `task_gate_results` + human `approved_at`), and made *secure* by the MCP
boundary: "the orchestrator can be fully compromised and still cannot reach a live system, because
each capability enforces its own propose-only doctrine at its edge" (`BLUEPRINT.md` §238-239).
Everything below is a projection of that spine onto components, decisions, and contracts.

---

## 2. Adopted ideas — one row per upstream project

Adopt/reject calls are pulled straight from each note; this table does not re-decide them. License
basis (who contributes *code* vs. *patterns only*) is in `ROADMAP.md` §45-59.

| Idea | Upstream source | What TALOS adopts | What TALOS rejects / changes | Source note path |
| :--- | :--- | :--- | :--- | :--- |
| Hardened task board + lifecycle | **Hermes** (NousResearch, MIT) | Kanban board schema, append-only `task_events`, claim/heartbeat/circuit-breaker, rate-limit exit-code-75, `goal_mode` judge loop (→ gate-bound evaluator), worker profiles, `model_override`, `skills` column, `idempotency_key`, workspace kinds | Epoch ints → `timestamptz`; `review`-without-gate → add `task_gate_results`+`approved_at`; multi-board-via-separate-DBs → `board_id`+RLS; file/SQLite memory → 4-store polyglot; skills auto-load → propose→review→pin gate | `docs/upstream/hermes-notes.md` (§9 decision table) |
| Self-reshaping UI + time-travel | **Space Agent** (agent0ai, MIT) | `spaces`/`space_versions` model, widget system (JS fn + YAML manifest), 1-24 grid engine, time-travel/version rollback, agent-authored widgets, Alpine.js cockpit shell, DOX hierarchy | Kanban/Gantt not built-in → first-party TALOS widgets; open widget sandbox → strict CSP+postMessage (Contract 4); file API backing store → Postgres + graph | `docs/upstream/space-agent-notes.md` (Exec Summary) |
| Hierarchy + memory areas | **Agent Zero** (agent0ai, MIT) | Skill-as-documentation (`SKILL.md`), profile-override sub-agents, three-area memory (MAIN/FRAGMENTS/CRYSTALLIZED), scope-bounded consolidation (MERGE/REPLACE/KEEP), `@extensible` decorator, 5-tier path search, agent-number lineage | Tool auto-trust → mandatory gate; shared `AgentContext` → session keys + per-task scope; FAISS → pgvector; LLM-extracted SOLUTIONS → human-review gate + rename to CRYSTALLIZED | `docs/upstream/agent-zero-notes.md` (§1-4) |
| Hierarchical doc contracts | **DOX Framework** (convention; agent0ai + NEXUS + TALOS) | `AGENTS.md` root→child chain as binding contracts, agent reading protocol, `generate_dox_tree` read-only rendering, inlined-vs-live-routed split, Child DOX Index | None rejected — adopted wholesale as convention (impl. work: write TALOS `AGENTS.md` tree + `generate_talos_dox_tree`) | `docs/upstream/dox-framework-notes.md` (§8) |
| Graph-seeded context map | **Aider repo-map** (Aider-AI, Apache 2.0) | Personalized PageRank seed selection, edge-weight formula (mentioned 10×, well-named 10×, private 0.1×, chat-context 50×, freq √N), binary-search token budget (~1k tokens), three-level caching | File-level nodes → routine-level nodes; NetworkX single graph for <500 nodes, defer Cypher GDS for scale; Tree-sitter → NEXUS parsers emit nodes/edges | `docs/upstream/aider-pagerank-notes.md` (§3-10) |
| Gateway + tool policy | **OpenClaw** (MIT) | 7-layer tool-policy pipeline, hardcoded dangerous-tool deny list (→ global floor), cron/heartbeat proactive loops, Docker sandbox (`network:none`), `security audit` pattern (→ `talos audit`) | Session-key-as-routing → `board_id`+RLS (hard auth); skill loading w/o gate → propose→critics→approve; `tools.elevated` host bypass → not adopted; single-operator trust → adversarial multi-client isolation; no budgets → 3-axis budget | `docs/upstream/openclaw-notes.md` (§3-9) |
| Execution engine + HITL gate | **LangGraph** (LangChain AI, MIT) | Postgres checkpointer, `interrupt()`+`Command(resume)` for the 5-way gate, StateGraph→Strategy Ladder, `Send` fan-out, streaming modes, `get_state_history()`+fork time-travel, subgraph isolation | LangGraph Platform → run as embedded library in TALOS FastAPI. **Gotcha adopted as rule:** node re-executes from line 1 on resume → all side effects in a *separate post-gate node* | `docs/upstream/langgraph-notes.md` (§4-12) |
| Bi-temporal knowledge graph | **Graphiti** (Zep AI, Apache 2.0) | Bi-temporal model (valid/transaction time, non-destructive contradiction), entity/edge LLM extraction, 3-pass dedup, hybrid search (vector+BM25+RRF+rerank), `group_id` multi-tenancy, MCP server, per-task sagas, custom entity/edge types | FAISS → pgvector; Graphiti ≠ pgvector (coexists in NEXUS Neo4j under separate labels + `group_id`) | `docs/upstream/graphiti-notes.md` (§4-11) |
| Cockpit + profile/plugin UX | **Hermes Profile Builder** (NousResearch v0.16.0) | 5-step profile wizard UX, profile directory isolation, React 19+FastAPI+Uvicorn stack, 3-layer plugin model, REST endpoints (→ board-API reference), token locks, non-loopback fail-closed bind | Profiles w/o FS sandbox → add Docker sandbox; skill/MCP change next-session → gate forces it; no plugin gate → critics+approve+pin before render | `docs/upstream/hermes-profile-builder-notes.md` (Exec Summary) |
| Token-efficient context plumbing | **OpenLumara** (Rose22, GPL-3.0) | Binary-search context trimming, summarization cutoff signal, ghost messages, module-level prompt-fragment injection, `on_end_prompt()` hook, per-command container sandbox, code-level (not prompt) security, pure mode | None rejected — positioned as *complementary* (personal-agent vs. project-execution); GPL-3.0 ⇒ patterns only, no ported code | `docs/upstream/openlumara-notes.md` |
| Compile-time security harness | **GitHub Agentic Workflows** (github/gh-aw) | Compile-time plan validation, zero-secret chroot jail, Safe-Outputs + `max_writes` volume limit, MCP Gateway (cred isolation), Agent Workflow Firewall (Squid), threat-detection critic, lock-file plan compilation, ChatOps triggers, OpenTelemetry cost export, Markdown-first authoring | None rejected — all 10 mechanisms mapped to TALOS phases 2-6 | `docs/upstream/github-agentic-workflows-notes.md` (§11 grid) |
| ML under the doctrine | **ML Integration** (TALOS internal analysis) | Learned critics (same verdict shape as deterministic), anomaly/alarm-flood/ISA-101-palette/narrative critics, orchestrator evaluator, consolidation + PageRank (Phase 4), crystallize trajectory learning, domain ML inside NEXUS packs, PM-layer prediction | None rejected — every model is gated: "a model proposes, deterministic critics gate, a human approves, no model writes live" | `docs/upstream/ml-integration-notes.md` (§1-7) |
| PLC simulation / test bridge | **Rockwell Emulation + EtherNet/IP** (Logix Emulate 5000 v35, FT Linx, pylogix) | EtherNet/IP bridge via FT Linx, pylogix CIP client, 4-phase test pipeline (index→download→test→report), L5X parsing, emulator snapshots for repeatable tests | PlantPAx UDT access via Linx → **BLOCKED** (privilege violation on `P_*` UDTs); use Path C (auditable ACD modification) / Path A (Logix Echo SDK) | `docs/upstream/rockwell-emulation-etherNetIP-notes.md` (§4-5) |

> **Adopt-source gap (noted honestly):** **Claude Code** (PreToolUse rewrite; structured worker
> results) and **Codex CLI** (exec-category bans; session keys) are credited as *Foundational*
> adopt-sources in `BLUEPRINT.md` §275-298 and `ROADMAP.md` §52-53 but have **no upstream note** of
> their own. They are surfaced in §3 below and cited to the BLUEPRINT borrowed-mechanisms table.

---

## 3. Component → source map

Each TALOS component, the upstream idea(s) that feed it, and the ADR(s) that govern it. Every
component has at least one source and a governing decision.

| Component | Dir / state | Upstream feed(s) | Governing ADR(s) |
| :--- | :--- | :--- | :--- |
| **Engine** (board, source of truth) | `engine/` — schema present, dispatcher loop planned (`engine/README.md`) | Hermes board port (`docs/upstream/hermes-notes.md`) · Space Agent `spaces`/`widgets` tables (`docs/upstream/space-agent-notes.md`) · LangGraph Postgres checkpointer (`docs/upstream/langgraph-notes.md`) | **ADR-002** (board-as-Space); **ADR-016** adds scheduling columns / milestones / PM hooks (`engine/schema-additions.sql`) |
| **Critics** (the gate) | `critics/` — stub README only | LangGraph `interrupt()`+`Command` 5-way gate · GitHub-AWF threat-detection critic + compile-time validation + `max_writes` · ML-integration learned critics · Hermes `review`-without-gate gap closed in `engine/schema.sql` (`task_gate_results`, `v_gate_status`) | **ADR-011** (gate outcomes: Approve/Reject/Waive/Edit/Escalate; safety critics escalate-only) |
| **Memory** (polyglot, federated) | `memory/` — stub README only | ADR-003 four stores · Agent Zero 3-area + consolidation · Graphiti bi-temporal + episodic · Aider PageRank map · OpenClaw hybrid FTS5+vector search (`BLUEPRINT.md` §142-181) | **ADR-003** (polyglot memory); **ADR-005** (cross-client split + promotion gate); **ADR-008** (vault topology); **ADR-014** (consolidation boundaries) |
| **Gateway** (sandboxed proactivity) | `gateway/` — stub README only | OpenClaw cron/heartbeat loops + Docker sandbox · OpenLumara per-command container · GitHub-AWF firewall + MCP Gateway + ChatOps · Codex exec-category bans (`BLUEPRINT.md` §31, §249-250) | **ADR-009** (layered tool policy — the gateway sandbox depends on it; `ROADMAP.md` §313-326) |
| **Web / Cockpit** (the View) | `web/` — stub README only | Space Agent spaces/widgets/grid/time-travel · Hermes-Profile-Builder React+FastAPI stack + wizard + REST · LangGraph `get_state_history()` timeline + `custom` stream (`BLUEPRINT.md` §185-214) | **ADR-012** (view platform = web, not native WinUI); **ADR-002** (board-as-Space) |
| **Dispatcher** (claim/coordinate) | part of `engine/` — loop planned | Hermes claim/heartbeat/breaker/checkpoint · LangGraph checkpointer + `Send` fan-out (`BLUEPRINT.md` §99-111, §230-232) | **ADR-016** (sort by `on_critical_path` before `priority`); **ADR-015** (dispatcher is Phase 1, *after* the gate); **ADR-010** (session-key worker isolation); **ADR-013** (coherence model) |
| **Strategy Ladder** (orchestration) | conceptual — `BLUEPRINT.md` §70-98 | Hermes `goal_mode` → gate-bound evaluator · Aider architect→editor relay + research step · LangGraph StateGraph · Claude Code gate-bound evaluator + structured worker results (`BLUEPRINT.md` §284, §288-290) | **ADR-006** (Strategy Ladder: triage→research→plan-relay→gate→execute→crystallize) |
| **Capability / MCP layer** | NEXUS attached behind MCP | NEXUS (first, most-privileged pack) · GitHub-AWF MCP Gateway credential isolation · Rockwell-emulation NEXUS PLC-test capability + OpenPLC sim target · Agent Zero drop-in capability→gated (`BLUEPRINT.md` §32, §125-137, §238-245) | **ADR-001** (platform, not merge); **ADR-004** (capability read/write tool profiles); **ADR-007** (parser ownership — NEXUS owns parsers) |

> **Cross-cutting trust spine:** layered tool policy (intersection, restrict-only), session-key
> isolation, structured worker results, and PreToolUse-rewrite logging are the *safety spine* under
> every component, not a feature on top (`BLUEPRINT.md` §236-251, §275-298). These are where the
> note-less **Claude Code** and **Codex CLI** sources land (ADR-009, ADR-010, ADR-013).

---

## 4. Decision inventory (all ADRs)

ADR titles for 004-015 are taken from the only numbered list that exists, `ROADMAP.md` §225-240.
These are **decided** at the design level; the remaining work is writing the formal record, not
re-deciding (`ROADMAP.md` §221-222). This section is distinct from §6 — nothing here is "open."

| ADR | Title | Status |
| :--- | :--- | :--- |
| ADR-001 | Platform, not merge (TALOS is the platform; NEXUS a capability behind MCP) | **Written** (Accepted) |
| ADR-002 | Board-as-Space (board engine + Space Agent view; seam = board API) | **Written** (Accepted) |
| ADR-003 | Polyglot memory (four stores, federated to NEXUS graph) | **Written** (Accepted) |
| ADR-004 | Capability read/write tool profiles | Decided, not formalized |
| ADR-005 | Cross-client memory split + unified promotion gate | Decided, not formalized |
| ADR-006 | Strategy Ladder | Decided, not formalized |
| ADR-007 | Parser ownership (NEXUS owns parsers; TALOS couples to output contract) | Decided, not formalized |
| ADR-008 | Vault topology (graph-as-linker on mothership; versioned pull to thick edges) | Decided, not formalized |
| ADR-009 | Layered tool policy (intersection-only, restrict-never-expand; global "no live writes" floor) | Decided, not formalized |
| ADR-010 | Worker isolation (session keys + config inheritance) | Decided, not formalized |
| ADR-011 | Gate outcomes (5-way; safety critics `waivable: false`) | Decided, not formalized |
| ADR-012 | View platform (web Space Agent, not native WinUI) | Decided, not formalized |
| ADR-013 | Coherence model (planner coherence on structured results; worker isolation) | Decided, not formalized |
| ADR-014 | Consolidation boundaries (autonomous only within one client scope below sensitivity threshold) | Decided, not formalized |
| ADR-015 | Phase reorder (gate + critics before full dispatcher) | Decided, not formalized |
| ADR-016 | DAG-driven project scheduling (board, Gantt, dispatcher are one DAG) | **Proposed** (`docs/decisions/ADR-016-dag-driven-project-scheduling.md` §3) |

**Notes (observations only):**
- *To-formalize set mismatch:* BLUEPRINT's prose to-formalize list (`BLUEPRINT.md` §308-311)
  includes **"PageRank context"**, but ROADMAP's numbered ADR table substitutes **"ADR-013
  Coherence model"** and assigns PageRank no ADR number. Numbering here follows ROADMAP (the only
  numbered source); the discrepancy is noted; **defer to BLUEPRINT.md** as authoritative.
- *ADR-016 numbering gap:* ADR-016 is written and Proposed, but is **not referenced** by BLUEPRINT's
  "Decision records" (`BLUEPRINT.md` §302-311) or ROADMAP's ADR list (`ROADMAP.md` §225-240), which
  stop at ADR-015. The numbering jumps 015 → 016. Observation only.

---

## 5. Contract inventory

The four boundaries that "de-risk live decisions" (`ROADMAP.md` §244-293). The `docs/contracts/`
directory does not yet exist, so **no contract is frozen.**

| Contract | State | Basis / intended path |
| :--- | :--- | :--- |
| **board-api** | **Partial** | Engine query-core seam already built ("Phase B", `ADR-002` §29); the *surface the view may call* is not yet frozen as a written spec. Action item: `ROADMAP.md` §250-259 → `docs/contracts/board-api.md` |
| **nexus-federation** | **Named only** | Read-through contract from TALOS memory to the NEXUS graph; action item from ADR-001 + ADR-003. `ROADMAP.md` §261-270 → `docs/contracts/nexus-federation.md` |
| **capability-manifest** | **Named only** | What any MCP domain pack must expose (read/write tools, resumable cursor, policy restrictions, validation); action item from ADR-001. `ROADMAP.md` §272-281 → `docs/contracts/capability-manifest.md` |
| **widget-sandbox** | **Named only** | CSP set + allowed board-API scopes + propose→render→critics→approve→pin lifecycle; action item from ADR-002. A schema placeholder exists (`widget_versions.sandbox_policy`, `engine/schema.sql`) but the policy is unwritten. `ROADMAP.md` §283-293 → `docs/contracts/widget-sandbox.md` |

---

## 6. Open threads (genuinely unresolved)

Unlike §4, these are **not decided** — `ROADMAP.md` §299 calls them "genuinely open … unlike the
to-formalize list."

**BLUEPRINT Parking Lot** (`BLUEPRINT.md` §334-345; expanded in `ROADMAP.md` §297-309):
1. **Planner-autonomy complexity threshold** — how is it computed, and who tunes it? (Needs a metric;
   research how other systems score task complexity.)
2. **Status-report authorship** — exact boundary between agent-drafted data and human commentary
   ("you write the commentary, risk read, and what's-ahead, and sign", `BLUEPRINT.md` §49-50). Needs
   a prototype against a real project status.
3. **Two-step confirmation for live ops** — UX and where it lives; by design it sits *outside* any
   agent's reach (`BLUEPRINT.md` §129-131). Needs interaction design.
4. **Vault pull cadence/format for thick edges** — how often the sanitized shared pack refreshes and
   what it ships as (git bundle? rsync?). *Mechanism is decided* (graph-as-linker on the mothership;
   versioned read-only pull on the edge); only cadence/format is TBD.

**Still-to-document** (research gap, not a design gap):
5. **Hermes dispatcher internals** — loop mechanics, heartbeat/breaker intervals, the `goal_mode`
   ("Ralph") judge loop, and the WebSocket event-streaming pattern are flagged as not-yet-written
   (`ROADMAP.md` §77-78). Needed before Phase-0 board implementation begins.

**Cross-source conflicts** (observations; **BLUEPRINT.md is authoritative** on each):
6. **ARCHITECTURE.md drift.** `docs/ARCHITECTURE.md` still frames TALOS as an "agent harness for
   business and industrial operations" with a **Business layer** (invoicing, time, per-client P&L,
   QuickBooks). BLUEPRINT v0.6 walked this back: the only money data is project-scoped, "which is
   project management, not accounting" (`BLUEPRINT.md` §13-14). ROADMAP already carries the standing
   remedy — *"when they conflict, BLUEPRINT.md wins. Regenerate ARCHITECTURE.md from BLUEPRINT
   whenever a major section changes"* (`ROADMAP.md` §334-335). No new fix proposed here.
7. **Two phase-numbering systems.** BLUEPRINT "Build phasing" (`BLUEPRINT.md` §259-268) is the
   *implementation* axis (0 = Foundations, then **2 = Gate** before **1 = Dispatcher** per the
   ADR-015 reorder, … 7 = Edge). ROADMAP phases (`ROADMAP.md` §63-326) are the *documentation* axis
   (0 = research, 1 = ADRs, 2 = contracts, 3 = parking lot, 4 = gateway-docs). Same word "Phase,"
   different axes; git history's "Phase 0I" tracks the ROADMAP/documentation axis. Both are valid in
   their own frame — do not collapse them.
8. **ADR-016 not yet reflected upstream** (see §4 note) — the BLUEPRINT/ROADMAP decision inventories
   predate it.

---

*Sources mapped: `BLUEPRINT.md` (v0.6), `ROADMAP.md`, `README.md`, `docs/ARCHITECTURE.md`,
`docs/decisions/ADR-001…003` + `ADR-016`, `docs/upstream/*.md` (13 notes), `engine/schema.sql` +
`engine/schema-additions.sql`, component READMEs under `critics/ gateway/ memory/ web/ engine/`.
On any conflict, `BLUEPRINT.md` is the design of record.*
