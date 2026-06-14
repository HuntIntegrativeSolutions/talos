# TALOS — Blueprint

> **Status:** living draft · v0.6 · 2026-06-11
> **Purpose:** the reconciled design of record. Not an implementation — a map to argue with.
> **Open:** ideas are still landing. See the [Parking Lot](#parking-lot) at the end.

---

## Thesis

**TALOS is a project-execution platform for industrial and automation work.** Projects decompose
into gated tasks, agents execute them, and planning, scheduling, risk, and status are first-class.
The only money/effort data it tracks is *project-scoped* — budget burn, AI-cost-per-deliverable,
timeline — which is project management, not accounting.

> **The Guardian doctrine** — AI proposes, humans review, deterministic critics gate, and nothing
> is written to a live system without a human's approval. No agent ever writes to a live processor.

---

## The stack, top to bottom

| Layer | Role |
| --- | --- |
| **Project** | projects, milestones, deliverables (gated artifacts), risks/issues, change control, status reports, project budgets |
| **Board engine** | the execution surface: tasks, dependency DAG, runs, append-only event log, claim/heartbeat/breaker. Ported from Hermes, on Postgres |
| **Gate** | `review` status + deterministic critics + human approval. The doctrine, made structural |
| **Orchestration** | the Strategy Ladder: triage → research → plan relay → gated plan → execute → crystallize |
| **View** | the **cockpit** — a task-centric web Space Agent surface: kanban, Gantt, burndown, risk heatmap, status digest, plus the gate UI and a NEXUS review canvas. Agent-reshapeable, time-travel on layout |
| **Memory** | four stores + graph/Obsidian projection, federated to the NEXUS graph |
| **Gateway** | sandboxed proactive loops: status digests, reminders, audit-freshness, notify |
| **Capabilities (behind MCP)** | NEXUS (propose-only ICS analysis); others attach the same way |

---

## Project management layer

The spine. Your work already *is* projects, so everything hangs from here.

- **Project → tasks.** A project groups boards/tasks toward a client deliverable. Tasks are
  execution; the project is the plan.
- **Deliverable = gated artifact.** Not "done" until its critic set passes and you approve. PM and
  the gate are the same mechanism.
- **Findings → issues, tiered** (from the audit→PM loop):
  - **HIGH** (safety/security): auto-create issue, **stage for your review** before any remediation task.
  - **MEDIUM**: auto-create issue + auto-dispatch a remediation task with a shortened gate.
  - **LOW**: log to the issue register only; no automatic task.
- **Change control.** Scope changes are first-class proposals: review → approved → versioned.
- **Status reporting.** Agent compiles the data section from board state + event log + gate status;
  **you write the commentary, risk read, and what's-ahead, and sign.**
- **Project budgets.** Per-project effort + AI-cost ceiling, enforced by the three-axis budget
  (time / cost / iterations). Cost-per-deliverable is the metric; revenue dashboards are not.

### Deliverable templates = versioned critic sets

Each deliverable type carries a named, **versioned** critic set (provenance matters when the
deliverable is itself audit evidence):

| Deliverable | Critic set (illustrative) |
| --- | --- |
| Audit report | no-hallucinated-tags · citations-resolvable · severity-justified |
| HMI screen set | navigation-complete · alarm-banner-present · ISA-101-palette |
| FDS | tag-exists-in-NEXUS · rung-references-resolvable · safety-function-mapped |
| SOO | state-transition-complete · permissive-enumerated · failure-mode-addressed |
| FAT/SAT evidence | procedure-matched · timestamped · deviation-documented |
| Progress PDF | board-state-consistent · milestone-gate-reflected |

---

## Orchestration — the Strategy Ladder

A first-class, declarable pattern (the runtime form of your AutoAdapt/Strategy Graph). A hard task
climbs the ladder; an easy one short-circuits.

1. **Triage** — estimate complexity; choose ladder depth.
2. **Research** — ground in retrieved knowledge before planning (never plan from priors alone).
3. **Plan relay** — a cheaper model drafts the plan; a stronger model refines it (spend the strong
   model on critique-and-finish, not a cold start). The relay is *within a task*, architect→editor:
   a reasoning model proposes the solution, a precise editing model translates it to exact edits.
4. **Gate the plan** — *mandatory* the moment the plan would call a write-capable tool or touch a
   safety system; auto-approve under a complexity threshold otherwise.
5. **Execute** — inside the approved envelope.
6. **Crystallize** — turn a successful trajectory into a **gated** skill and a new path in the
   Strategy Graph. A new skill is a proposal, not a trusted instruction; the new **path is also IP**
   and defaults to `client_scope: [client]`. Skills, paths, and memory nodes all ride the **one
   promotion gate** (see Memory) — abstract the shape, strip the instance, review before `[shared]`.

**Guardrails:** iteration caps per task type (code-gen ≤5, analysis ≤3, any offline/sim write ≤1,
**no auto-retry on anything live**), all under the three-axis budget as a hard ceiling.

This gives **two compounding loops**: *knowledge* (graph/vault) and *procedure* (skill/strategy
library). Same shape, different axis.

**Runtime control — a gate-bound evaluator.** A cheap evaluator model judges after each turn and
picks the *next ladder step* (not just done/not-done). It is gate-bound: free to self-advance through
research → plan → refine, it **stops dead at the human gate** the instant the next step would call a
write-capable tool or touch a safety system. The three-axis budget is the hard ceiling on the loop.

### Coordination — coherence at the planner, isolation at the workers

The resolution to "single-session coherence vs. distributed workers": keep both, at different levels.

- **Planner coherence.** The planner holds one coherent context for the project plan and ingests only
  **structured worker results** — deliverable + critic verdicts + event-log delta — never raw worker
  transcripts. Coherence survives parallelism because the noise never enters the planner's context.
- **Worker isolation via session keys.** A task claim mints a session key
  (`task:{board_id}:{task_id}:{attempt}`) scoping the worker's workspace, tool policy, and memory.
  Crash-recoverable from the checkpoint log.
- **Config inheritance.** A spawned worker inherits the parent's NEXUS connection, client scope, and
  tool policy, overlaid with role restrictions that can **only further restrict** (a tag-audit worker
  gets `nexus:read` on `UNIT_*` only). Define profiles once, not per task.

---

## Gate & critics

- A `review` status sits between `running` and `done`; `task_gate_results` records each critic's
  verdict; `v_gate_status` is the read model. A gated task can't advance until every required critic
  is `pass` **and** a human sets `approved_at`.
- **Gate outcomes** are five, not two: **Approve** · **Reject-with-reason** (back to the worker with
  your note) · **Waive-with-justification** (a critic failed but you override it — recorded, not
  skipped) · **Edit-inline** (fix the deliverable yourself, then approve) · **Escalate** (second
  reviewer). `task_gate_results` carries `waivable`, `waived_by`, and `justification`; **safety
  critics are `waivable: false → escalate-only`**, so a waiver can never become a doctrine bypass.
- **Capability tool profiles.** A capability exposes `read` and `write` profiles. Tasks get `read`
  by default. A `write` scope requires the plan to be gate-approved.
- **The hard line on "write":** for NEXUS, `write` means **offline artifacts and simulation only** —
  generated ladder, HMI screens, the OpenPLC sandbox. It **never** means download, online edit, mode
  change, or tag write to a live device. Those operations are not gated; they are *not in any agent's
  reach at all*. A human performs them by hand from NEXUS's proposal. Live writes may also warrant a
  second, at-the-moment human confirmation (two-step).
- Self-authored widgets and skills ride the same propose → review → pin/load gate.
- **The OpenPLC sandbox** is a **NEXUS-side capability**, not a TALOS sandbox — running PLC logic is
  domain work. It is the execution target for offline writes: generated ladder loads into OpenPLC,
  is exercised, and the **sim results feed the critics and your review** before any human deploys to
  hardware. It sits between "write (offline)" and "human deploys" — the proving ground that lets you
  validate generated logic in simulation before anyone touches a live processor.

---

## Memory

Four stores, each for the job it does best, tiered hot → warm → cold:

| Store | Role | Tier |
| --- | --- | --- |
| Postgres | system of record: board, project, event log | warm |
| Graph (Neo4j) | knowledge & topology; **federated** to the NEXUS graph (read-through, never duplicated) | cold/reference |
| Vector (pgvector/Chroma) | semantic + episodic recall | warm |
| Redis | working memory, live-dashboard pub/sub, locks, dispatcher coordination | hot |

- **Cross-client split + unified promotion gate.** Tag every *learned artifact* — graph node, skill,
  and crystallized strategy path — with `client_scope: [shared]` (vendor/device knowledge: how to
  configure a 1756-L8x, GuardLogix signature workflow) or `[client]` (process/application IP: a
  client's burner sequencing, alarm philosophy). All learned artifacts default to `[client]`.
  **Promotion to `[shared]` passes one gate, regardless of artifact type** — "generic engineering
  vs. the client's competitive method" is a judgment call with IP weight. Sanitize = abstract the
  shape, strip the instance.
- **Graphify pattern, ICS-flavored.** `corpus → graph → Obsidian vault + report`. Vanilla Graphify is
  Tree-sitter and won't parse an L5X; **NEXUS's parsers emit the nodes/edges instead**, projected
  into Obsidian for a navigable map (the agent reads the structured output; Obsidian is the human view).
- **Vault topology.** A **shared reference vault + one vault per client**; client vaults may
  read-link the shared vault, never the reverse. The cross-scope link lives in the **graph, not in
  Obsidian wikilinks** — the graph holds one copy of each shared node and the vaults are per-scope
  *projections*, so there is nothing to "sync." The exception is a **thick edge** that
  can't query the graph live (Acme): it gets a **versioned, read-only, sanitized shared pack
  pulled down git-style** on its own cadence. Mothership = graph-as-linker, no copies; edge =
  versioned pull.
- **Consolidation, bounded by scope** *(from Agent Zero)*. Memory isn't store-and-search; an
  LLM-mediated pipeline finds similar entries and decides MERGE / REPLACE / UPDATE / KEEP-SEPARATE,
  with a similarity floor before any REPLACE. **The boundaries are ours:** consolidation runs
  autonomously only *within one client scope and below a sensitivity threshold*; a MERGE across
  `[client]`/`[shared]` is **forbidden** (leak vector); anything touching a verified-solution or
  safety node is a **proposal to the gate**, not an auto-write.
- **PageRank context map** *(from Aider)*. Personalized PageRank over the NEXUS graph, seeded by a
  task's tags/routines, yields a compact (~1k-token) relevance map injected into the planner — Aider's
  repo-map trick, but NEXUS emits the graph instead of Tree-sitter. This is what makes the graph pay
  rent in tokens. *(foundational mechanism; lands Phase 4.)*
- **Hybrid search** *(from OpenClaw)*. Over-fetch, then re-rank with combined FTS5-keyword + vector
  scores. Tags are exact strings (`PIT_UNIT_01_TEMP`); alarm text is natural language ("ignition
  timeout") — industrial memory needs both. *(lands Phase 4.)*

---

## Cockpit (the View layer)

The cockpit replaces the chatbot — chat is the training wheels, the board is the tool. It is a **web**
Space Agent surface (resolved against native WinUI: one codebase serves every deployment, runs locally
on a thick edge, and matches the board-as-Space decision). A thin WebView2 shell is optional if
you want a desktop feel; native GUI-driving (Studio 5000 / FactoryTalk with no API) is a *worker
capability*, not a cockpit concern.

- **Task-centric, not agent-centric.** The board is the unit. You see "GLOBEX-UNIT-01 FDS at gate,
  2/4 critics passed" — not "worker-7 alive, holding `nexus:read`." The agent is an implementation
  detail; the only agent-flavored panel that survives is a **project-economics gauge** (budget burn,
  AI-cost, workers active — the three-axis budget made visible).
- **Single responder.** Only the planner addresses you; workers emit structured results, never chat.
  Drill-down is three levels, default collapsed: planner summary → structured worker result → raw
  diagnostic trail (which tags checked, what was eliminated — verification evidence, anchored to the
  evidence-requiring critics).
- **The gate UI** renders the five outcomes (Approve / Reject / Waive / Edit / Escalate) inline in the
  stream, with diff viewers for changed deliverables.
- **The NEXUS review canvas.** You review a deliverable *against the live system*, not in isolation:
  the deliverable beside the PageRank-selected slice of the NEXUS graph (the tags and routines it
  references). PageRank picks the slice.
- **Temporal replay.** The append-only event log makes the board *scrubbable* — replay how a
  deliverable reached the gate and which critic flipped when. Timelines become a replay, not a list.
- **Scope & locale.** The cockpit is per-board-scoped (RLS); switching client re-scopes everything.
  It is served per deployment, so a thick edge runs its own cockpit with no mothership.

**The cockpit's one KPI is time-to-confident-approval.** You are the gate, so the human is the
system's rate-limiter *by design*. The surface optimizes how fast you reach a *trustworthy* yes/no —
evidence, the NEXUS slice, and the diff one keystroke away; keyboard-driven approve/waive; batch
review for low-risk deliverables — not agent observability.

---

## Deployment

**Hub-and-spoke.**

- **Mothership (control plane):** PM, board engine, memory, dispatcher, view, gateway. Where you work
  across all clients.
- **Edges (per client):** client-dependent.
  - *Thin edge* (data may leave): claims tasks, forwards heavy analysis to the mothership.
  - *Thick edge* (on-premises analysis): runs analysis locally on a dedicated network line.
    **Acme is thick** — its isolated workstation runs the DeepSeek API on its own line.
    Note: model inference egresses to hosted API endpoints; "thick" refers to local execution and
    live-processor write isolation, not network air-gap. See ADR-017.
- **Sync** over Tailscale: edges push only non-sensitive coordination state up.
- **Isolation:** `board_id` + Postgres Row-Level Security; boards are the hard boundary, tenants soft.
- **Long-task resilience:** workers write **checkpoint events** to the log; on re-claim they resume
  from the last checkpoint. This requires capability tools to expose a **resumable cursor** (progress
  + resume token). Heartbeat 30s local / 120s edge; breaker trips after 3 misses.

---

## Trust boundaries

- **MCP boundary = security boundary.** The orchestrator can be fully compromised and still cannot
  reach a live system, because each capability enforces its own propose-only doctrine at its edge.
- **Widget sandbox:** agent-authored UI runs in a locked iframe, reaching the engine only through the
  board API.
- **Memory promotion gate:** client → shared reclassification is reviewed, not automatic.
- **Layered tool policy, intersection-only** *(from OpenClaw + Codex)*. Effective policy = global ∩
  client ∩ task; each layer can **only restrict, never expand**. Global layer: "no live writes, ever."
  This is how the gate/scope system is *implemented*, not a feature on top — the safety spine.
- **PreToolUse rewrite** *(from Claude Code)*. A hook can approve, deny, or **transparently modify** a
  tool call before it runs — e.g. inject the current client scope into a query. Silent to the model,
  **never silent to the audit log**: every rewrite is logged. Belt-and-suspenders with RLS.
- **Exec category bans** *(from Codex)*. Guardrails, not gates — whole command classes (`sudo`,
  `python -c`, `ssh` to a production network) are refused regardless of what any gate allows.

---

## Build phasing

Dependency-ordered; each phase independently demoable. **NEXUS stays a separate MCP capability
throughout — never merged.**

| Phase | What | Note |
| --- | --- | --- |
| **0** | Foundations: Postgres + board schema + RLS + board API contract | reuse the Phase-B query core |
| **2** | **Gate + critics** (built before the full dispatcher) | a single-worker harness is enough to prove the doctrine |
| **1** | Full distributed dispatcher (claim/heartbeat/breaker/checkpoint) | builds in parallel toward the gate's target |
| **3** | PM layer: projects/milestones/deliverables/risks; findings→issues loop | |
| **4** | Memory + graph/Obsidian; NEXUS federation; corpus→graph→vault | |
| **5** | Cockpit (web): task-centric board as Spaces; 5-outcome gate UI; NEXUS review canvas; temporal replay; widget gate | task-centric, not agent-centric |
| **6** | Gateway + proactivity (sandboxed) | |
| **7** | Edge + sync; thin/thick per client; model routing | |

Stack: Python + FastAPI engine, Postgres/Redis, Neo4j + pgvector/Chroma, a Space-Agent-derived JS
view over the board API, MCP for capabilities, Docker + Tailscale.

---

## Borrowed mechanisms (five-harness study)

Adopted from Agent Zero, OpenClaw, Claude Code, Codex CLI, and Aider. **Foundational** = load-bearing,
hard to retrofit, design in now. **Later** = phase-appropriate optimization.

| Mechanism | Source | Lands in | When |
| --- | --- | --- | --- |
| Layered tool policy (intersection, restrict-only) | OpenClaw + Codex | Trust / gate | **Foundational** |
| Session-key worker isolation + config inheritance | OpenClaw + Codex | Coordination | **Foundational** |
| Structured worker results (not transcripts) | Claude Code | Coordination | **Foundational** |
| Consolidation boundaries (scope/safety gated) | Agent Zero | Memory | **Foundational** |
| PageRank context map (NEXUS graph) | Aider | Memory | Phase 4 |
| Hybrid FTS5 + vector search | OpenClaw | Memory | Phase 4 |
| Gate-bound evaluator (picks next ladder step) | Claude Code | Orchestration | Phase 2–3 |
| Architect→editor within-task relay | Aider | Ladder step 3 | Phase 2 |
| PreToolUse rewrite (logged) | Claude Code | Trust | Phase 2 |
| Exec category bans | Codex | Gateway / sandbox | Phase 6 |
| Drop-in capability files (agent-proposed → gated) | Agent Zero | Capabilities | Phase 5 |
| Skill Workshop (propose→review→apply, versioned) | OpenClaw | Crystallize gate | already in design |
| Append-only prompt + structured compaction | Codex | build-time | later |
| CSV batch fan-out (parallel audits) | Codex + Hermes | Coordination | later |

**The rule over all of them:** anything that mutates ground truth (memory), expands capability
(skills/extensions), or self-advances across the gate must itself pass the gate.

---

## Decision records

Formalized: [ADR-001](docs/decisions/ADR-001-platform-vs-nexus.md) (platform, not merge) ·
[ADR-002](docs/decisions/ADR-002-board-as-space.md) (board-as-Space) ·
[ADR-003](docs/decisions/ADR-003-polyglot-memory.md) (polyglot memory).

To formalize next: capability read/write tool profiles · cross-client memory split + **unified**
promotion gate (memory / skill / path) · phase reorder (gate before dispatcher) · the Strategy Ladder
· **parser ownership** (NEXUS owns parsers; TALOS couples to NEXUS's output contract, not Rockwell's
L5X format) · **vault topology** (graph-as-linker on the mothership; versioned read-only pull to thick edges) · **layered tool policy** (intersection, restrict-only) · **consolidation boundaries** (scope/safety-gated memory writes) · **worker isolation** (session keys + config inheritance) · **PageRank context** (graph-seeded relevance map) · **view platform** (web Space Agent, not native WinUI) · **gate outcomes** (approve / reject / waive / edit / escalate; safety critics escalate-only).

---

## Changelog

- **v0.6** (2026-06-11) — Added the Cockpit (View layer): task-centric board over agent cards, the
  five gate outcomes with the safety-waiver→escalate rule, the NEXUS review canvas (deliverable vs.
  PageRank graph slice), three-level drill-down, temporal replay from the event log, and the
  time-to-confident-approval KPI. Resolved the view platform as web (not native WinUI).
- **v0.5** (2026-06-11) — Folded in the five-harness study (Agent Zero, OpenClaw, Claude Code, Codex,
  Aider). Foundational: layered tool policy, session-key isolation + config inheritance, structured
  worker results, scope/safety-gated memory consolidation. Added the gate-bound evaluator and the
  coherence-at-planner / isolation-at-workers split, plus a "Borrowed mechanisms" triage table.
- **v0.4** (2026-06-11) — Unified the promotion gate across memory nodes, skills, and strategy paths.
  Defined the OpenPLC sandbox as a NEXUS-side sim target whose results feed the gate. Resolved vault
  sync (graph-as-linker on the mothership; versioned read-only pull to thick edges). Added two ADRs
  to formalize: parser ownership and vault topology.
- **v0.3** (2026-06-11) — First reconciled blueprint: PM thesis, Strategy Ladder, four-store memory,
  read/write capability profiles with the live-write doctrine locked, checkpoint/resume, phase reorder.

---

## Parking Lot

Open questions and a place for incoming ideas.

- Planner-autonomy complexity threshold — how is it computed, and who tunes it?
- Status-report authorship — exact boundary between agent-drafted data and human commentary.
- Two-step confirmation for live ops — UX and where it lives (it's outside the agent by design).
- Vault pull cadence/format for thick edges — how often the sanitized shared pack refreshes and what
  it ships as (the mechanism is decided: graph-as-linker on the mothership, versioned pull on the edge).

**More ideas — to be added:**
- _…_

**External knowledge commons (P5+):**
- **Stack Overflow for Agents** (`agents.stackoverflow.com`, REST + MCP server, public beta June
  2026) — an agent-queryable corpus of Questions, TILs (debugging traces), and Blueprints (design
  patterns). Human approval is required before any agent-drafted post is published; every
  contribution is tied to a human operator's account via OAuth 2.1/PKCE. Evaluate as an optional
  MCP capability for the Research phase of the Strategy Ladder on **code-shaped tasks** (building
  TALOS itself, not operational client work). The Blueprints post type aligns with the crystallize
  step — when TALOS crystallizes a novel solution, it could propose a Blueprint for gated
  publication. Domain fit for PLC/HMI client work is weak; the corpus is software-focused. Defer
  to P5+.
- **Mozilla cq** (open-source, local + org tiers, March 2026) — same concept, private by default,
  runs on customer infrastructure with no egress. Better fit for **thick-edge / air-gapped client
  deployments** where data cannot leave the network. Evaluate as a local knowledge commons for P6.
  Defer to P6.
