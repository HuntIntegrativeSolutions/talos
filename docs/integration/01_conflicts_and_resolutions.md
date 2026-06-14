# TALOS — Conflicts & Resolutions (Adversarial Reconciliation Pass)

> **What this is:** the seam-closing pass over the *already-chosen* TALOS pieces. The pieces are fixed
> (see `docs/integration/00_integration_map.md` and `BLUEPRINT.md`); this document hunts the places
> where two of them **collide, overlap, or leave a gap** at a boundary, and resolves each with a
> decision grounded in the existing design.
> **What this is not:** new architecture, and not a re-litigation of adopt/reject calls. Where a
> resolution would weaken the safety spine, it is rejected and said so.
>
> **Authority rule:** on any conflict between sources, **`BLUEPRINT.md` is authoritative**
> (`00_integration_map.md` §6).
>
> **The safety spine every resolution must hold to** (`BLUEPRINT.md` §16-17, §125-137, §236-251,
> §297-298): *AI proposes, humans review, deterministic critics gate, and nothing is written to a
> live system without a human's approval — no agent ever writes to a live processor.* The MCP edge is
> the security boundary; the tool policy is intersection-only with a global "no live writes" floor;
> anything that mutates ground truth, expands capability, or self-advances across the gate must itself
> pass the gate.
>
> **Citations.** `BLUEPRINT.md` / ADR / `00_integration_map.md` carry verified `§` line-anchors (read
> in full this session). Upstream notes are cited by **path + section-heading name** (e.g.
> `graphiti-notes.md` → "NEXUS Coexistence Contract"), not by `§`-number. The escalation-driving
> passages (Graphiti co-location; Rockwell UDT/download gaps) were re-read firsthand.
>
> **Legend.** Severity: **CRITICAL** (safety / isolation / license-blocker) · **HIGH**
> (correctness / scale-blocker) · **MEDIUM** · **LOW**. Confidence: **RESOLVED** ·
> **NEEDS-HUMAN-DECISION** · **NEEDS-PROTOTYPE**.

---

## Summary table

| ID | Name | Severity | Confidence | Touches ADR / contract |
| :--- | :--- | :--- | :--- | :--- |
| CR-01 | Shared `AgentContext` vs hard multi-client isolation | HIGH | RESOLVED | ADR-013, ADR-010 |
| CR-02 | "session key" semantic collision (routing vs auth vs scope) | MEDIUM | RESOLVED | ADR-010 |
| CR-03 | `group_id` (app-enforced) vs RLS (DB-enforced) isolation | CRITICAL | RESOLVED / NEEDS-PROTOTYPE | ADR-003, ADR-009, nexus-federation |
| CR-04 | "loaded skill = trusted" vs propose→critics→pin | CRITICAL | RESOLVED | ADR-009, ADR-011, capability-manifest |
| CR-05 | Agent Zero "verified solutions" vs TALOS "verified = gated" | MEDIUM | RESOLVED | ADR-005, ADR-014 |
| CR-06 | Learned (LLM) critics vs "the gate is pure / deterministic" | HIGH | RESOLVED | ADR-011, critics contract |
| CR-07 | Graphiti contradiction-invalidation vs NEXUS system-of-record | HIGH | RESOLVED | ADR-003, nexus-federation |
| CR-08 | Graphiti on the **same** Neo4j as NEXUS vs federation | HIGH | **RESOLVED** | nexus-federation, ADR-003 |
| CR-09 | Episodic autonomous writes vs "ground-truth writes pass the gate" | HIGH | RESOLVED | ADR-014, ADR-003 |
| CR-10 | Aider NetworkX (<500 nodes) vs 127 k-entry NEXUS graph | MEDIUM | RESOLVED / NEEDS-PROTOTYPE | ADR-003 (+ PageRank ADR) |
| CR-11 | PageRank seeding leak across a shared NEXUS graph | HIGH | RESOLVED | ADR-001, capability-manifest |
| CR-12 | LangGraph node re-exec on resume vs side-effects-once-after-gate | HIGH | RESOLVED | ADR-011, ADR-016, ADR-010 |
| CR-13 | Gate-bound evaluator self-advance vs stop-at-gate (fail-closed) | HIGH | RESOLVED | ADR-006, ADR-004, capability-manifest |
| CR-14 | OpenLumara **GPL-3.0** vs TALOS **MIT** | CRITICAL | RESOLVED | new license-policy ADR |
| CR-15 | GitHub Agentic Workflows — license unstated | MEDIUM | **RESOLVED** | new license-policy ADR |
| CR-16 | Emulate 5000: no download API + UDT privilege violations | HIGH | **RESOLVED** | ADR-004, ADR-007, capability-manifest, critics |
| CR-17 | DOX soft (prose) enforcement vs structural safety | MEDIUM | RESOLVED | DOX/AGENTS.md convention (test) |
| CR-18 | NEXUS findings lifecycle (confirmed-only) vs TALOS task gate | MEDIUM | RESOLVED | capability-manifest, ADR-011, nexus-federation |
| CR-19 | Proactive cron turns + unsandboxed gateway vs no-autonomous-write | HIGH | RESOLVED | ADR-009 |
| CR-20 | Space Agent widgets (no CSP) vs multi-client cockpit | HIGH | RESOLVED / NEEDS-PROTOTYPE | widget-sandbox, ADR-002, ADR-011 |
| CR-21 | Hermes profiles conflate isolation/capability + no FS sandbox | MEDIUM | RESOLVED | ADR-010, ADR-009 |
| CR-22 | DAG auto-dispatch (ADR-016) vs HIGH-safety-stages-for-review | MEDIUM | RESOLVED | ADR-016 |
| CR-23 | Contract-freeze sequencing blocks phases | MEDIUM | RESOLVED (process) | all four contracts |
| CR-24 | Doc drift (ARCHITECTURE.md business layer; two phase axes) | LOW | RESOLVED | — (regenerate per `00_integration_map` §6) |
| CR-25 | Graphiti ingestion cost vs the three-axis budget | MEDIUM | RESOLVED / NEEDS-PROTOTYPE | ADR-014, ADR-003, capability-manifest |
| CR-26 | "Shortened gate" is undefined — latent doctrine hole | HIGH | RESOLVED | ADR-011 |

**Coverage of the seven known tensions:** Agent Zero context → CR-01; OpenClaw skill-trust → CR-04;
LangGraph resume → CR-12; Graphiti-on-NEXUS-Neo4j → CR-07 + CR-08; Aider PageRank scale → CR-10;
license (OpenLumara GPL etc.) → CR-14 + CR-15; Rockwell emulation gaps → CR-16; memory-ownership
boundary → CR-07 + CR-09. Nineteen further seams were found by tracing each upstream idea to its
landing point at the four hot boundaries (MCP, gate, memory, isolation).

---

## Detailed records

### CR-01 — Shared `AgentContext` vs hard multi-client isolation

- **Tension.** Agent Zero delegates to subordinates that **share one history / context** (its notes,
  "3. Agent Hierarchy — Synchronous Subordinate Delegation": *"All agents in a chain share the same
  history… no way for a subordinate to have a private context"*). TALOS requires hard per-client
  isolation: `board_id` + Postgres Row-Level Security (`BLUEPRINT.md` §229).
- **Why it matters.** A shared context window is a cross-client data-leak vector the moment two
  clients' work touches the same planner. Isolation is a safety/IP boundary, not a nicety.
- **Options.** (a) Adopt Agent Zero's shared context — **rejected**, breaks isolation. (b) Fully
  isolated workers with no shared planner context — loses plan coherence across parallel work.
  (c) Split the axis: **coherence at the planner, isolation at the workers**.
- **Resolution (RESOLVED).** BLUEPRINT already holds (c) (`BLUEPRINT.md` §99-111): the planner keeps
  one coherent context but ingests **only structured worker results** (deliverable + critic verdicts +
  event-log delta), never raw transcripts; each worker is isolated by a session key
  (`task:{board_id}:{task_id}:{attempt}`) scoping workspace/tool-policy/memory; config inheritance can
  **only further restrict**. Add the one thing the BLUEPRINT leaves implicit: the **planner context is
  itself board-scoped** — one coherent context per board/project, all memory reads RLS-bound — so a
  single planner instance can never mix two clients. This is consistent with "the cockpit is
  per-board-scoped (RLS); switching client re-scopes everything" (`BLUEPRINT.md` §208).
- **Touches.** ADR-013 (coherence model), ADR-010 (worker isolation).
- **Confidence.** RESOLVED.

### CR-02 — "session key" semantic collision

- **Tension.** Three adopted sources use "session key" with **different meanings**. OpenClaw
  (`openclaw-notes.md`, "7. Session Management"): session key is *routing, not auth* — *"cannot be
  used as a security boundary"*. Codex/TALOS (`BLUEPRINT.md` §106-108): a session key is a per-worker
  scoped credential. Same words, opposite trust semantics.
- **Why it matters.** If anyone implements TALOS session keys believing they are an auth boundary (the
  Codex framing) but inherits OpenClaw's routing semantics, isolation silently degrades to
  "discouraged," not "enforced."
- **Options.** (a) Make session keys the auth boundary — rejected; OpenClaw shows the model can't bear
  it. (b) Keep RLS as the only hard boundary and demote session keys to scoping.
- **Resolution (RESOLVED).** Session keys scope workspace, tool policy, and memory **but are never the
  authorization boundary**. The hard boundary is `board_id` + Postgres RLS (`BLUEPRINT.md` §229);
  PreToolUse rewrite injects client scope and is logged (`BLUEPRINT.md` §246-248) as belt-and-
  suspenders. Cross-client access is impossible at the DB layer, not merely policy-discouraged.
- **Touches.** ADR-010.
- **Confidence.** RESOLVED.

### CR-03 — `group_id` (app-enforced) vs RLS (DB-enforced) isolation

- **Tension.** TALOS's whole isolation story rests on RLS being a **DB-enforced** boundary. But the
  graph/episodic layer (Graphiti on Neo4j) has no RLS: isolation is a query predicate. Graphiti's
  "8. Multi-Tenancy" states *"All Cypher queries include `WHERE n.group_id IN $group_ids`"* and names
  logical partitioning the *"Default for TALOS."* One missing predicate in one query = cross-client
  leak.
- **Why it matters.** Two isolation regimes of unequal strength sit under one doctrine. The weaker one
  (graph) is exactly where the most sensitive learned IP (client process knowledge) lives.
- **Options.** (a) Trust query discipline — rejected; a single human/agent error leaks. (b) Database-
  per-tenant in Neo4j (Graphiti's own "strict data-residency" option) — hard isolation but breaks the
  **shared** scope (the vault graph-as-linker holds *one* copy of each shared node, `BLUEPRINT.md`
  §162-168), so it can't be the default. (c) A **mandatory mediation adapter** that is the only path
  to the graph and always injects `group_id ∈ [client_scope, shared]`.
- **Resolution (RESOLVED mechanism / NEEDS-PROTOTYPE on equivalence).** Adopt (c): no agent issues raw
  Cypher; every graph read/write goes through a chokepoint that injects the scope filter and **logs the
  rewrite** — the same PreToolUse-rewrite pattern already mandated for SQL (`BLUEPRINT.md` §246-248).
  The filter always admits the **shared** scope so graph-as-linker still works; it never admits a
  second client's scope. Reserve database-per-tenant (option b) for the highest-sensitivity clients
  (e.g. a thick air-gapped edge). **Prototype** whether the mediated predicate provides RLS-equivalent
  guarantees under concurrency and community/saga rollups before relying on it for sensitive scopes.
- **Touches.** ADR-003, ADR-009 (PreToolUse rewrite / layered policy), `nexus-federation` contract.
- **Confidence.** RESOLVED (mechanism) / NEEDS-PROTOTYPE (equivalence).

### CR-04 — "loaded skill = trusted" vs propose → critics → pin

- **Tension.** Every harness that loads skills/tools auto-trusts them: OpenClaw injects a skill body
  into the system prompt with *"No capability manifest enforcement, No code signing… No per-skill tool
  grant"* ("4. The 'Loaded Skill = Trusted' Hole"); Agent Zero auto-executes any `Tool` subclass on
  the path ("1. Tool Loading — The Auto-Trust Hole"); Hermes auto-loads agent-created skills after only
  a security scan ("3. Skill Generation & Crystallization"). TALOS's gate says the opposite.
- **Why it matters.** This is the single largest attack surface TALOS inherits. An auto-trusted skill
  can request any tool the policy allows — a prompt-injection-to-tool-call pipeline.
- **Options.** (a) Security-scan only (Hermes) — rejected; a scan is not a gate. (b) Sign skills —
  helps provenance but doesn't bound capability. (c) Manifest + gate + invocation-time enforcement.
- **Resolution (RESOLVED).** BLUEPRINT closes the hole structurally: self-authored skills/widgets ride
  the **propose → review → pin/load** gate, and *"anything that… expands capability (skills/extensions)
  … must itself pass the gate"* (`BLUEPRINT.md` §132, §297-298). The load-bearing details to encode:
  (1) a pinned skill is **content-addressed (hashed)** so any post-pin edit auto-reverts it to
  `proposed`; (2) the skill carries a **capabilities manifest** (tools, domains, sensitivity); (3) an
  **8th tool-policy layer** checks each tool call against the *pinned manifest* at invocation time and
  denies anything not declared, even if the session policy would allow it. The manifest is the
  reviewed contract; the injected body must match it.
- **Touches.** ADR-009 (add per-skill layer to the intersection policy), ADR-011 (gate), the
  `capability-manifest` contract.
- **Confidence.** RESOLVED.

### CR-05 — Agent Zero "verified solutions" vs TALOS "verified = gated"

- **Tension.** Agent Zero's SOLUTIONS area is LLM-extracted from chat with **no human review**
  (`agent-zero-notes.md`, "What a 'Verified Solution' Is": *"there is no human review step. The name
  'verified solutions' is misleading in the context of TALOS's Guardian doctrine"*). In TALOS,
  "verified" means *gated*.
- **Why it matters.** A naming collision becomes a doctrine hole if an "auto-verified" solution is
  later treated as ground truth and cited in a deliverable without a human ever confirming it.
- **Options.** (a) Keep the name and the auto-extraction — rejected. (b) Rename + add a gate on
  graduation.
- **Resolution (RESOLVED).** Rename the area **CRYSTALLIZED** (BLUEPRINT already uses MAIN / FRAGMENTS /
  CRYSTALLIZED), and require the promotion gate before anything graduates to trusted status: *"a new
  skill is a proposal, not a trusted instruction… anything touching a verified-solution or safety node
  is a proposal to the gate, not an auto-write"* (`BLUEPRINT.md` §85-86, §173-174). Auto-extraction may
  populate FRAGMENTS; promotion to CRYSTALLIZED/`[shared]` rides the one promotion gate.
- **Touches.** ADR-005 (promotion gate), ADR-014 (consolidation boundaries).
- **Confidence.** RESOLVED.

### CR-06 — Learned (LLM) critics vs "the gate is pure / deterministic"

- **Tension.** `critics/README.md` states *"Critics are pure and reproducible — no LLM in the gate
  itself."* But two adopted sources add **learned** critics: `ml-integration-notes.md`
  ("Integration Point 1 — Learned Critics" — same verdict shape as deterministic) and
  `github-agentic-workflows-notes.md` ("Mechanism 6: Threat Detection as a Separate Job"). A
  model-driven verdict inside a gate specified as LLM-free is a direct contradiction.
- **Why it matters.** If a learned critic can *block* or *auto-pass*, the gate's decision is no longer
  reproducible, and a non-deterministic model sits on the safety path.
- **Options.** (a) Forbid learned critics — loses real value (anomaly/ISA-101/narrative checks).
  (b) Let learned critics block like deterministic ones — rejected; non-reproducible gate. (c) Admit
  them as **advisory-only**.
- **Resolution (RESOLVED).** A learned critic returns the same verdict shape but is **advisory**: it
  may emit `warn`, or a **non-required** `fail` a human weighs — it **never** acts as a *required*
  critic, never auto-blocks, and never auto-approves. Every **required** critic — and **all safety
  critics** (`waivable:false → escalate-only`, `BLUEPRINT.md` §119-124) — stays deterministic. The gate
  records `model_name`/`model_version`/`input_snapshot` in `task_gate_results.details` for audit
  (the ML note's interface guidance). This keeps the gate's *blocking* decision reproducible while
  letting learned signals inform the human.
- **Touches.** ADR-011 (critic taxonomy: deterministic-required vs learned-advisory), critics contract.
- **Confidence.** RESOLVED.

### CR-07 — Graphiti contradiction-invalidation vs NEXUS system-of-record

- **Tension.** Graphiti auto-detects contradictions and invalidates the old edge by setting
  `invalid_at` (`graphiti-notes.md`, "3. Temporal Mechanics — Contradiction and Invalidation"). NEXUS
  is the **read-only system-of-record for PLC knowledge** (`ADR-001` §47-49; `ADR-003` §31-36 — *"the
  NEXUS graph stays the system of record… never duplicate it"*). If an agent-extracted episodic fact
  contradicts a NEXUS fact, *which wins, and may episodic memory invalidate NEXUS?*
- **Why it matters.** If episodic extraction can flip a NEXUS fact's validity, the system-of-record is
  no longer authoritative and the audit product loses its ground truth.
- **Options.** (a) Let Graphiti invalidate across both graphs — rejected; episodic memory overruling
  documented truth is backwards and violates federation. (b) NEXUS authoritative; contradictions
  become findings.
- **Resolution (RESOLVED).** **NEXUS wins.** TALOS never writes to or invalidates NEXUS facts;
  Graphiti's contradiction machinery operates **only inside the TALOS-owned episodic graph**. A
  disagreement between an episodic observation and a NEXUS-documented fact is itself a valuable
  **finding, not a memory write** — it enters the audit→PM loop as a tiered finding for a human
  (`BLUEPRINT.md` §44-47). The robustness of this rule depends on **CR-08**: if NEXUS facts live in a
  store TALOS cannot write (separate Neo4j + read-through), "never invalidate NEXUS" is *structurally*
  enforced rather than policy-enforced.
- **Touches.** ADR-003, `nexus-federation` contract (must encode "no TALOS write to NEXUS;
  contradiction → finding").
- **Confidence.** RESOLVED.

### CR-08 — Graphiti on the **same** Neo4j as NEXUS vs federation (read-through, never duplicated)

- **Tension.** The Graphiti note's **"NEXUS Coexistence Contract"** (re-read firsthand) is explicit:
  ```
  Neo4j database: talos-neo4j
  NEXUS data (existing):  Labels: :Tag, :Program, :Routine, :Rung, :Device
  Graphiti data (new):    Labels: :Entity, :Episodic, :Community, :Saga
  Shared infrastructure:  - Same Neo4j instance
                          - NEXUS entities seeded as Graphiti :Entity nodes via add_triplet()
  ```
  Its "8. Multi-Tenancy" makes logical `group_id` partitioning the *"Default for TALOS."* This
  **collides** with `ADR-001` §38-41 (*"TALOS calls NEXUS; it does not absorb it"*; the MCP edge is the
  security boundary) and `ADR-003` §31-36 (*"federated… read-through… never duplicated"*). It is a
  documented-vs-documented conflict, not a misreading — and NEXUS's own store is SQLite today
  (`_Tools/CLAUDE.md`), so "NEXUS lives in Neo4j" is itself an unverified premise.
- **Why it matters.** Co-location dissolves exactly the boundary ADR-001 buys: if TALOS holds Neo4j
  credentials to an instance that *also* holds NEXUS's system-of-record nodes, a fully-compromised
  orchestrator — which by design *"cannot reach a live processor"* — could still **poison the SoR
  graph**. It also undercuts CR-07's structural guarantee.
- **Options.**
  - **(a) One shared `talos-neo4j` with label-scoped write roles** — TALOS's role may write only
    `:Entity/:Episodic/:Community/:Saga`; NEXUS labels are read-only to TALOS. *Pro:* cheap in-graph
    cross-links via `add_triplet()` exactly as the note designs. *Con:* Neo4j label-level write
    permissions are coarse and easy to misconfigure; the MCP boundary is no longer the only path to
    NEXUS data.
  - **(b) Separate TALOS Neo4j + NEXUS read-through over MCP** — TALOS owns its episodic graph; NEXUS
    structural facts are read through NEXUS tools and never written by TALOS. *Pro:* preserves the
    federation/security boundary; makes CR-07 structural. *Con:* cross-links between episodic and NEXUS
    structural nodes become *logical* references resolved at query time (and PageRank spans two
    stores), not in-graph edges — the note's `add_triplet()` bootstrapping no longer applies as drawn.
- **Resolution (RESOLVED — option b chosen 2026-06-14).** **Separate TALOS Neo4j + NEXUS read-through
  over MCP.** TALOS owns its own Neo4j instance for Graphiti episodic/knowledge data; NEXUS structural
  facts are read through NEXUS MCP tools only — TALOS never writes to or holds credentials for a
  Neo4j instance containing NEXUS data. This keeps the MCP boundary load-bearing and makes
  "NEXUS is system-of-record" physically enforced (not just policy). Cross-links between TALOS
  episodic nodes and NEXUS structural facts are logical references resolved at query time; the upstream
  note's `add_triplet()` bootstrapping is not adopted as drawn. ADR-003 carries this.
- **Touches.** `nexus-federation` contract, ADR-003.
- **Confidence.** **RESOLVED.**

### CR-09 — Episodic autonomous writes vs "ground-truth writes pass the gate"

- **Tension.** Graphiti's ingestion auto-extracts entities/edges and writes them to the graph with no
  per-fact human review; its example even records a `HAS_FINDING` edge to a node literally named
  `verified` (`graphiti-notes.md`, "10. Integration with LangGraph"). BLUEPRINT says *"anything that
  mutates ground truth (memory)… must itself pass the gate"* (`BLUEPRINT.md` §297-298).
- **Why it matters.** Taken literally, every episodic write would need a human — unworkable; taken
  loosely, autonomous extraction could mint "verified" ground truth — doctrine breach. The line must
  be drawn precisely.
- **Options.** (a) Gate every episodic write — rejected; defeats memory. (b) Gate nothing — rejected;
  breaches doctrine. (c) Gate the **system-of-record / verified / safety layer**, not raw episodic
  capture.
- **Resolution (RESOLVED).** Draw the line where BLUEPRINT already draws it (`BLUEPRINT.md` §169-174):
  episodic capture is autonomous **only within one client scope and below a sensitivity threshold**; a
  MERGE across `[client]`/`[shared]` is forbidden; **anything touching a verified-solution or safety
  node is a proposal to the gate, not an auto-write**. So raw episodes flow freely under scope; the
  gate guards promotion, cross-scope merges, and verified/safety nodes. (Correct the upstream example:
  a finding is captured as a *candidate*, not auto-written to a `verified` node — see CR-05, CR-18.)
- **Touches.** ADR-014 (consolidation boundaries), ADR-003.
- **Confidence.** RESOLVED.

### CR-10 — Aider NetworkX (<500 nodes) vs the 127 k-entry NEXUS graph

- **Tension.** Aider runs personalized PageRank in-process with NetworkX; `aider-pagerank-notes.md`
  ("Neo4j vs. NetworkX") recommends NetworkX on *"subgraph… typically <500 nodes"* for Phase 1 and
  defers native Cypher GDS *"until graph sizes warrant it"* — while noting Acme's NEXUS graph
  has *~127 k address_xref entries* and *"Option B [GDS] is preferred"* at that scale.
- **Why it matters.** If seed expansion pulls a large neighborhood, NetworkX degrades and the
  planner's context-map step becomes the latency bottleneck — or silently truncates.
- **Options.** (a) NetworkX on the full graph — rejected at 127 k nodes. (b) Native GDS from day one —
  heavier infra than Phase 1 needs. (c) NetworkX on a **bounded** NEXUS-query subgraph, GDS later.
- **Resolution (RESOLVED / NEEDS-PROTOTYPE on the threshold).** Phase 4 uses NetworkX over a subgraph
  extracted by NEXUS queries, with **bounded neighbor expansion** (k-hop + a hard node budget); if the
  budget is exceeded, fall back to Cypher GDS or truncate by edge weight (the dominant factor is the
  50× chat-context seed boost, per the note). Defer GDS to scale. **Prototype** the node-budget
  threshold against real Acme seeds. Process note: **PageRank has no ADR number** — BLUEPRINT's
  to-formalize list names it but ROADMAP's numbered table omits it (`00_integration_map.md` §4) — so it
  needs a formal home (fold into ADR-003 or open a PageRank ADR).
- **Touches.** ADR-003 (or a new PageRank ADR), `nexus-federation` contract (subgraph extraction API).
- **Confidence.** RESOLVED / NEEDS-PROTOTYPE.

### CR-11 — PageRank seeding leak across a shared NEXUS graph

- **Tension.** PageRank output is injected into the planner before planning. Seeds are client-scoped
  by task, but graph *traversal* runs over NEXUS topology. If one NEXUS graph served multiple clients,
  a Client-A seed could surface Client-B equipment in the ranked slice. The Aider note does not
  address multi-client isolation at all.
- **Why it matters.** A silent cross-client context leak into the planner prompt — the kind of leak
  RLS exists to prevent, but at the capability layer where RLS doesn't reach.
- **Options.** (a) One shared multi-client NEXUS graph with query-time client filtering — fragile,
  same risk class as CR-03. (b) **One NEXUS instance per client deployment.**
- **Resolution (RESOLVED).** TALOS's deployment model already gives (b): hub-and-spoke with an **edge
  per client**, NEXUS attached as that client's capability (`BLUEPRINT.md` §220-229; Acme is a
  thick, air-gapped edge running its own analysis). The mothership never runs a shared multi-client
  NEXUS graph, so seed traversal cannot cross clients. Client isolation lives at the TALOS
  episodic/graph layer (`group_id`, CR-03), and the NEXUS PLC graph is inherently per-plant.
- **Touches.** ADR-001, `capability-manifest` contract (one pack instance per client scope).
- **Confidence.** RESOLVED.

### CR-12 — LangGraph node re-execution on resume vs side-effects-once-after-gate

- **Tension.** `langgraph-notes.md` ("Gotcha 1 — Node Re-Execution on Resume (Most Important)"): when
  `interrupt()` pauses a node and `Command(resume=…)` resumes it, *"the entire node function
  re-executes from line 1. Any code before `interrupt()` fires twice."* TALOS's gate is implemented
  with `interrupt()`, and the doctrine requires post-approval side effects to happen **exactly once**.
- **Why it matters.** An audit-log write, a notification, or an offline artifact write placed in the
  gate node would fire twice on resume — double-applied side effects across the safety gate.
- **Options.** (a) Put side effects in the gate node — rejected; the gotcha guarantees double-fire.
  (b) Pure gate node + separate post-gate node + idempotency.
- **Resolution (RESOLVED).** The gate node is **pure** — it only calls `interrupt()`; **all** side
  effects (audit, notify, memory writes, offline/sim writes, MCP write-profile calls) live in a
  **separate post-gate node** that runs after the human decision (the note's own corrected pattern).
  Additionally, every write-class side effect carries an **idempotency key**
  (`task:{board_id}:{task_id}:{attempt}:{step}`; the schema already has `idempotency_key`) so that a
  crash *between* the side effect and the checkpoint commit — or a dispatcher re-claim from checkpoint
  (`BLUEPRINT.md` §230-232; ADR-016) — cannot double-apply.
- **Touches.** ADR-011 (gate node is pure), ADR-016 (resume/re-claim), ADR-010 (session-keyed
  idempotency).
- **Confidence.** RESOLVED.

### CR-13 — Gate-bound evaluator self-advance vs stop-at-gate (fail-closed classification)

- **Tension.** The gate-bound evaluator is *"free to self-advance through research → plan → refine"*
  but must *"stop dead at the human gate the instant the next step would call a write-capable tool or
  touch a safety system"* (`BLUEPRINT.md` §94-97). The open question: **who classifies** "write-capable"
  or "touches a safety system," and what happens on uncertainty?
- **Why it matters.** If an LLM judges whether the next step is safety-relevant, a misjudgment lets the
  loop self-advance past the gate — the exact failure the gate exists to prevent.
- **Options.** (a) Let the evaluator (an LLM) decide — rejected; non-deterministic on the safety path.
  (b) Make write/safety a **declared, deterministic property** of the capability and fail closed on
  unknowns.
- **Resolution (RESOLVED).** "Write-capable" and "safety-touching" are **declared properties of the
  capability profile** (capability-manifest), not an LLM judgment; the evaluator checks the declared
  profile and hard-stops on `write`/`safety`. **Fail-closed:** an unknown or unprofiled capability is
  treated as `write`. This same deterministic classification is what makes the **auto-approve-under-
  complexity-threshold** path safe (`BLUEPRINT.md` §80-81): auto-approval is permitted *only* when no
  step touches a declared write/safety capability — connecting to the parking-lot "complexity
  threshold" open item (`00_integration_map.md` §6, item 1).
- **Touches.** ADR-006 (Strategy Ladder), ADR-004 (read/write profiles), `capability-manifest` contract.
- **Confidence.** RESOLVED.

### CR-14 — OpenLumara **GPL-3.0** vs TALOS **MIT**

- **Tension.** OpenLumara is GPL-3.0 (`openlumara-notes.md`, "Project Maturity & Community"). TALOS is
  MIT (`LICENSE`). GPL-3.0 is copyleft: vendoring its code would force TALOS to GPL. The adopted ideas
  (binary-search context trimming, ghost messages, module-level prompt fragments, `on_end_prompt()`)
  are valuable but cannot be copied in.
- **Why it matters.** A licensing breach is a build-time blocker and a legal one; it is not waivable by
  the gate.
- **Options.** (a) Vendor OpenLumara code — **rejected** (license-incompatible). (b) Reimplement the
  patterns clean-room in MIT/Apache code.
- **Resolution (RESOLVED).** **Reimplement, do not vendor.** The patterns are generic algorithms, not
  novel implementations, so clean-room reimplementation carries no functional risk; **no OpenLumara
  source enters the tree**, and the reimplementation must not be a line-by-line transcription of its
  code. `00_integration_map.md` §2/§4 already records OpenLumara as *"patterns only, no ported code."*
  This needs a formal home: TALOS has **no license/dependency-policy ADR** today.
- **Touches.** A new **license/dependency-policy ADR** (covers GPL-incompatible sources, vendor-vs-
  reimplement rule); also `CONTRIBUTING.md`.
- **Confidence.** RESOLVED.

### CR-15 — GitHub Agentic Workflows — license unstated

- **Tension.** TALOS adopts all ten gh-aw mechanisms (`00_integration_map.md` §2), but the note
  (`github-agentic-workflows-notes.md`, "What It Is") does **not state the license** — it attributes
  the project to GitHub Next + Microsoft Research. Whether any of it is vendorable is unknown.
- **Why it matters.** Same class as CR-14 but worse: an *unknown* license means we cannot assert
  vendorability either way. Building on copied gh-aw code without checking risks a CR-14-style breach.
- **Options.** (a) Assume permissive and vendor — rejected; unverified. (b) Treat as reference
  architecture and reimplement; verify the license before any code reuse.
- **Resolution (RESOLVED — 2026-06-14).** License confirmed as **MIT** at
  https://github.com/github/gh-aw (GitHub Agentic Workflows, GitHub Next). MIT permits commercial
  use, modification, distribution, and private use; code is vendorable in the TALOS MIT-licensed
  project with attribution (include the original copyright notice and license text). Direct code
  reuse is now permissible; reimplementation of the mechanisms remains the preferred approach to
  avoid coupling to gh-aw internals, but copying isolated utility code is no longer blocked.
- **Touches.** The same new **license/dependency-policy ADR**.
- **Confidence.** **RESOLVED.**

### CR-16 — Emulate 5000: no programmatic download API + UDT privilege violations

- **Tension.** `rockwell-emulation-etherNetIP-notes.md` ("4. Capability Matrix" → "Root cause"): the
  FT Linx bridge *"exposes atomic CIP objects only… Every P_* UDT instance returns privilege
  violation."* And ("5. Three Paths Forward"): *"Emulate 5000 has no programmatic download API. Logix
  Echo SDK does (licensed)."* Meanwhile the note documents clearing a faulted controller *"using only
  pylogix writes"* — i.e., programmatic writes to controller state.
- **Why it matters.** Two distinct problems collide with the spine: (1) a **capability gap** (can't
  simulate PlantPAx I/O, can't auto-download) that blocks the test pipeline; (2) a **safety question**
  — are agent-driven pylogix writes "writes to a live system"?
- **Options (capability).** Path A (Logix Echo SDK — full UDT access + download, **licensed**);
  Path B (BOOL-forcing via L5X analysis — native Python, *fragile*, mapping not 1:1); Path C (modify
  the ACD to expose UDT internals as BOOLs — auditable, reversible, but *"must be verified against the
  original to ensure test-mode doesn't mask real faults"*).
- **Resolution (RESOLVED — safety + path decided 2026-06-14).** **Safety (RESOLVED):** the
  emulator is a **NEXUS-side simulation target**, the same class as the OpenPLC sandbox that *"sits
  between 'write (offline)' and 'human deploys'"* (`BLUEPRINT.md` §133-137). pylogix writes to it are
  **offline/sim writes** under the write profile — gated, capped at *"any offline/sim write ≤1"* with
  *"no auto-retry on anything live"* (`BLUEPRINT.md` §88-89). Hard constraint: the test bridge must be
  **network-isolated from any live processor**, and a deterministic critic must **verify the target IP
  is the emulator**, not a real controller — this keeps "no agent writes to a live system" intact.
  Path C additionally requires an **original-vs-modified diff critic** so test-mode logic can't mask a
  real fault. **Path selection (RESOLVED 2026-06-14):** TALOS uses a **dual-track approach**:
  (1) **NEXUS** handles program structure reading over MCP (NEXUS reads the program, TALOS consumes
  the output contract per ADR-007); (2) **pylogix** reads live structure from Emulate 5000 once a
  connection is established — active testing underway; (3) **Logix Echo SDK** included for full UDT
  access and download where needed. This is a complete documentation + skills + hooks package.
- **Touches.** ADR-004 (write profile = sim target, define the IP-verification critic), ADR-007
  (parser ownership — NEXUS owns program-structure parsing; TALOS couples to the output contract),
  `capability-manifest` (the PLC-test capability), critics (target-IP-is-emulator; modified-vs-original diff).
- **Confidence.** **RESOLVED.**

### CR-17 — DOX soft (prose) enforcement vs structural safety

- **Tension.** The AGENTS.md chain is described as *binding contracts*, yet enforcement is soft:
  `dox-framework-notes.md` ("Is This Parsed Programmatically?") — *"Current state: No. Agents read the
  markdown files as prose,"* enforced by human PR review and one test. A "binding" safety rule that
  lives only in prose is not actually binding.
- **Why it matters.** If any safety invariant (e.g. "no live writes") relied solely on an agent reading
  an AGENTS.md sentence, a prompt-injection or a skipped read would defeat it.
- **Options.** (a) Make DOX the enforcement mechanism — rejected; prose can't gate safety. (b) Keep
  DOX as guidance; require a *structural* enforcer behind every safety rule.
- **Resolution (RESOLVED).** Keep DOX/AGENTS.md as soft human+agent guidance (and `generate_dox_tree`
  read-only, **test-enforced** — `dox-framework-notes.md`, "Key Invariant (Enforced by Test)"), but
  adopt the rule: **no safety invariant may depend solely on an AGENTS.md prose statement.** Each
  safety rule must have a structural enforcer — a deterministic critic, RLS, or a tool-policy layer.
  DOX documents intent; the spine enforces it.
- **Touches.** DOX/AGENTS.md convention (the no-DML-in-`generate_dox_tree` test); cross-refs the
  layered-policy ADR-009 and the critics contract as the structural enforcers.
- **Confidence.** RESOLVED.

### CR-18 — NEXUS findings lifecycle (confirmed-only) vs TALOS task gate

- **Tension.** Two propose→confirm lifecycles touch the same work. NEXUS findings run
  `queued → proposed → confirmed` and only confirmed findings render
  (`dox-framework-notes.md`, "generate_dox_tree" — confirmed-only inlining; the NEXUS findings gate is
  fail-closed). TALOS deliverables run `review → critics → approved`. When a TALOS task wraps a NEXUS
  audit, are there two human gates on the same fact?
- **Why it matters.** Double-gating the same fact is wasted human time; *under*-gating (citing a
  proposed/unconfirmed finding in an approved deliverable) puts unverified claims into audit evidence.
- **Options.** (a) Collapse to one gate — rejected; they gate different objects. (b) Keep both,
  nested, with a citation rule.
- **Resolution (RESOLVED).** They are **distinct and nested**: NEXUS confirms **facts** about the
  plant; TALOS approves **deliverables** that use them. The TALOS gate does **not** re-confirm
  findings. The bridge is a critic: `citations-resolvable` / `no-hallucinated-tags` (BLUEPRINT
  deliverable critic sets, §59-66) must require that any cited finding is **confirmed-status in NEXUS**,
  never `proposed`/`dismissed`. So the human confirms facts in NEXUS (or they are pre-confirmed) and
  approves the deliverable in TALOS — no double human gate on the same fact, and no unconfirmed claim
  escapes into evidence.
- **Touches.** `capability-manifest` contract (NEXUS must expose finding *status* over MCP), ADR-011
  (the bridging critic), `nexus-federation`.
- **Confidence.** RESOLVED.

### CR-19 — Proactive cron turns + unsandboxed gateway vs no-autonomous-write

- **Tension.** OpenClaw runs proactive loops as agent turns through the **same** tool pipeline as user
  turns, with *"no turn-origin–based privilege separation"* ("3. Privileged Tools"), and the gateway
  process itself *"is not sandboxed — it runs on the host"* ("1. The Gateway Architecture"). TALOS
  adopts the proactive-loop pattern for digests/reminders/audit-freshness (`BLUEPRINT.md` §31).
- **Why it matters.** Without origin-based limits, a cron or webhook turn could invoke a write-capable
  tool autonomously — an agent acting without a human in the loop. An unsandboxed gateway widens the
  blast radius.
- **Options.** (a) Give cron turns elevated privilege for "automation" — rejected; autonomous write.
  (b) Cron turns inherit the same floor; sandbox the gateway.
- **Resolution (RESOLVED).** Proactive turns run under the **same intersection-only tool policy** with
  the global *"no live writes, ever"* floor (`BLUEPRINT.md` §243-245); a cron turn has **no more**
  privilege than a user turn — it may **notify and propose**, never approve or write. The gateway is
  itself sandboxed (Docker `network:none`/`readOnlyRoot`; OpenClaw's `tools.elevated` host-bypass is
  **not adopted**). This closes OpenClaw's no-origin-separation gap by construction.
- **Touches.** ADR-009 (layered policy floor applies to proactive loops; gateway sandbox).
- **Confidence.** RESOLVED.

### CR-20 — Space Agent widgets (no CSP, main browser context) vs multi-client cockpit

- **Tension.** Agent-authored widgets are adopted, but Space Agent widgets *"execute directly in the
  browser's main JavaScript context"* with *"any `fetch()` URL… access `window`, `document`,
  `localStorage`"* (`space-agent-notes.md`, "8. Widget Sandbox — Security Gap"). In a multi-client
  cockpit this is an isolation and exfiltration hole.
- **Why it matters.** A self-authored (gated, but still untrusted-by-default) widget in the main
  context could read another board's data or call arbitrary URLs — cross-client leak at the view layer.
- **Options.** (a) Ship widgets in the main context — rejected for multi-client. (b) Locked iframe +
  postMessage bridge + CSP, behind the gate.
- **Resolution (RESOLVED / NEEDS-PROTOTYPE on the allowlist).** Agent-authored UI runs in a **locked
  iframe**, reaching the engine **only through the board API** (`BLUEPRINT.md` §240) via a
  **postMessage bridge** with a CSP and a small allowlist of message types (`getTasks`, `getGateStatus`,
  `requestGate`, `subscribe` — no arbitrary `fetch`, no direct DOM outside the iframe). Widgets ride
  the **propose → render → critics → approve → pin** gate (ADR-002 action items). **Prototype** the
  exact bridge allowlist and CSP set (this is the unwritten `widget-sandbox` contract; a
  `widget_versions.sandbox_policy` placeholder already exists in `engine/schema.sql`).
- **Touches.** `widget-sandbox` contract, ADR-002, ADR-011.
- **Confidence.** RESOLVED / NEEDS-PROTOTYPE.

### CR-21 — Hermes profiles conflate isolation/capability + no filesystem sandbox

- **Tension.** Hermes worker profiles are full isolated environments that *conflate environment
  isolation with capability routing* (`hermes-notes.md`, "5. Worker Profiles"), and provide *"state
  isolation but not filesystem isolation"* (`hermes-profile-builder-notes.md`, "3. Profile vs Workspace
  vs Sandbox"). TALOS wants clean separation and real sandboxing.
- **Why it matters.** Conflating the two makes per-task tool policy awkward; missing FS isolation means
  a compromised worker can read the host filesystem.
- **Options.** (a) Adopt Hermes profiles as-is — rejected (no FS sandbox). (b) Separate identity from
  capability and add a container sandbox.
- **Resolution (RESOLVED).** Separate **profile** (identity / secrets / isolation) from **capability
  selector** (tool policy + model routing, declared per task and layered). Add a **Docker sandbox**
  (`network:none`, `readOnlyRoot`) plus the gh-aw zero-secret chroot pattern on top of session-key
  isolation (`hermes-profile-builder-notes.md`, "9. TALOS-Specific Analysis"; `github-agentic-
  workflows-notes.md`, "Mechanism 2"). No worker gets a host-bypass path.
- **Touches.** ADR-010 (session keys + config inheritance), ADR-009 (sandbox + policy).
- **Confidence.** RESOLVED.

### CR-22 — DAG auto-dispatch (ADR-016) vs HIGH-safety-stages-for-review

- **Tension.** ADR-016's **milestone-risk escalator** *"emit[s] a MEDIUM finding → auto-create issue →
  auto-dispatch a remediation task"* (`ADR-016` §53). BLUEPRINT's findings tiering says **HIGH
  (safety/security)** findings *"auto-create issue, stage for your review before any remediation
  task"* — i.e. **no** auto-dispatch (`BLUEPRINT.md` §45-46).
- **Why it matters.** If a safety-significant milestone slip auto-dispatched a remediation task, an
  agent would begin safety work without the human staging that BLUEPRINT requires.
- **Options.** (a) Auto-dispatch all milestone risks — rejected for safety-significant ones.
  (b) Severity-gate the escalator.
- **Resolution (RESOLVED).** The escalator emits **MEDIUM** by default (auto-dispatch with a gated
  remediation task is fine); **safety-significant milestones emit HIGH → stage for human review, never
  auto-dispatch.** ADR-016 already flags this as action item #7 (*"HIGH for safety-significant
  milestones"*); this record pins it to the BLUEPRINT tiering. Note ADR-016 itself guarantees hooks
  *"never short-circuit a gate"* and only move status to `ready` (`ADR-016` §136-139), so even an
  auto-dispatched task still passes its own gate.
- **Touches.** ADR-016 (severity mapping action item).
- **Confidence.** RESOLVED.

### CR-23 — Contract-freeze sequencing blocks phases

- **Tension.** All four boundary contracts are unfrozen — `board-api` **Partial**, and
  `nexus-federation` / `capability-manifest` / `widget-sandbox` **Named-only** (`00_integration_map.md`
  §5; `docs/contracts/` does not yet exist) — but downstream build phases depend on them. The
  resumable-cursor interface (needed for Phase-1 checkpoint/resume, `BLUEPRINT.md` §230-232) lives in
  the capability-manifest; memory federation (Phase 4) needs `nexus-federation`; the widget gate
  (Phase 5) needs `widget-sandbox`.
- **Why it matters.** Starting a phase before its contract is frozen invites rework and lets a seam
  (CR-08, CR-18, CR-20) ship unresolved.
- **Options.** (a) Build first, freeze later — rejected; bakes in unresolved seams. (b) Freeze each
  contract before its dependent phase.
- **Resolution (RESOLVED — process).** Freeze order, mapped to BLUEPRINT build phasing
  (`BLUEPRINT.md` §259-268): **`board-api`** before Phase 0/5; **`capability-manifest`** (read/write
  profiles + resumable cursor + write-profile sim-target + finding-status exposure) before Phase 1;
  **`nexus-federation`** (read-through; no-TALOS-write; contradiction→finding; topology per CR-08)
  before Phase 4; **`widget-sandbox`** (CSP + bridge allowlist) before Phase 5. Several conflicts here
  are precisely the content those contracts must carry: CR-04/CR-13 → capability-manifest;
  CR-07/CR-08/CR-18 → nexus-federation; CR-20 → widget-sandbox.
- **Touches.** All four contracts (`board-api`, `capability-manifest`, `nexus-federation`,
  `widget-sandbox`).
- **Confidence.** RESOLVED (process).

### CR-24 — Documentation drift (ARCHITECTURE.md business layer; two phase-numbering axes)

- **Tension.** `docs/ARCHITECTURE.md` still frames TALOS with a **Business layer** (invoicing, P&L,
  QuickBooks) that BLUEPRINT v0.6 walked back to *"project management, not accounting"*
  (`BLUEPRINT.md` §13-14). Separately, "Phase N" means different things in BLUEPRINT (implementation
  axis) vs ROADMAP (documentation axis) (`00_integration_map.md` §6, items 6-7).
- **Why it matters.** A stale architecture doc and an overloaded "Phase" label cause an implementer to
  build the wrong scope or sequence — a coherence risk, not a safety one.
- **Options.** (a) Reconcile each doc by hand. (b) Apply the standing governance rule.
- **Resolution (RESOLVED).** Already governed: *"when they conflict, BLUEPRINT.md wins. Regenerate
  ARCHITECTURE.md from BLUEPRINT whenever a major section changes"* (`00_integration_map.md` §6). Keep
  the two phase axes distinct and never collapse them. Recorded here for completeness; no new fix.
- **Touches.** — (documentation governance; regenerate ARCHITECTURE.md).
- **Confidence.** RESOLVED.

### CR-25 — Graphiti ingestion cost vs the three-axis budget

- **Tension.** Graphiti ingestion is expensive: "9. Performance Characteristics" reports **4–15 LLM
  calls and ~5 k–40 k tokens per episode**, and "Episodic Memory Coverage" fires an episode at
  task-created / research / plan / gate / execution / crystallize. Continuous capture collides with
  TALOS's **three-axis budget as a hard ceiling** (`BLUEPRINT.md` §88-89).
- **Why it matters.** Six episodes per task at up to 40 k tokens each could dwarf the actual work
  budget, or get truncated unpredictably — the budget is a hard ceiling, not advisory.
- **Options.** (a) Ingest at every cadence point — rejected on cost. (b) Ingest at gate/crystallize
  only; use cheap paths for known facts.
- **Resolution (RESOLVED on placement / NEEDS-PROTOTYPE on cost).** Ingest full episodes **only at
  crystallize / post-gate**, budget-bounded; use `add_triplet()` (zero-extraction) for facts already
  structured in NEXUS (the note's "Pattern 4"); prefer a cheap extraction model and defer community
  updates. **Prototype:** the Graphiti note itself says to *run cost estimates on real TALOS task
  traces before committing to continuous ingestion* — do that before enabling any pre-gate ingestion;
  assert no token number the note doesn't give.
- **Touches.** ADR-014 (consolidation/ingestion boundaries), ADR-003, `capability-manifest` (budget
  hooks on the memory capability).
- **Confidence.** RESOLVED (placement) / NEEDS-PROTOTYPE (cost).

### CR-26 — "Shortened gate" is undefined — latent doctrine hole

- **Tension.** BLUEPRINT §46: MEDIUM findings *"auto-dispatch a remediation task with a **shortened
  gate**."* "Shortened" is never defined anywhere in the design of record.
- **Why it matters.** If "shortened" were read as "skip human approval," the audit→PM automation loop
  would quietly punch a hole in the spine — an agent-completed remediation with no human gate.
- **Options.** (a) Leave it undefined — rejected; ambiguity on the safety path. (b) Define it as a
  *narrower* gate, never a *human-less* one.
- **Resolution (RESOLVED).** **Shortened = fewer / faster critics (a smaller required set, expedited
  review), never skip-human-approval.** A human always sets `approved_at` (`BLUEPRINT.md` §119), and
  **safety critics remain `waivable:false → escalate-only`** regardless of how short the gate is
  (`BLUEPRINT.md` §119-124). A shortened gate changes the critic set's size, not the human's presence.
- **Touches.** ADR-011 (define "shortened gate" within the 5-outcome gate).
- **Confidence.** RESOLVED.

---

## Decisions that must be escalated to a human

These cannot be closed from the existing design alone; they need a human call before the dependent
contract/ADR is frozen.

| ID | Decision required | Why it can't be auto-resolved | Blocks |
| :--- | :--- | :--- | :--- |
| **CR-08** | ~~Physical graph topology: one shared Neo4j or separate TALOS Neo4j + NEXUS read-through over MCP.~~ | **RESOLVED 2026-06-14.** Separate TALOS Neo4j chosen (option b). | Closed. |
| **CR-15** | ~~Confirm the **GitHub Agentic Workflows license** before reusing any gh-aw code.~~ | **RESOLVED 2026-06-14.** License is MIT (https://github.com/github/gh-aw). Vendorable with attribution. | Closed. |
| **CR-16** | ~~Choose the Rockwell test path A / B / C and decide on Logix Echo SDK licensing cost.~~ | **RESOLVED 2026-06-14.** Dual-track: NEXUS (MCP) + pylogix + Logix Echo SDK. Complete documentation + skills + hooks package. | Closed. |

**Also needs prototyping before the dependent piece is trusted (NEEDS-PROTOTYPE):**

- **CR-03** — verify the mandatory-mediation `group_id` filter is RLS-equivalent under concurrency
  before relying on it for sensitive scopes.
- **CR-10** — tune the PageRank subgraph node-budget threshold against real Acme seeds; confirm
  the NetworkX→GDS fallback trigger.
- **CR-20** — pin the widget postMessage bridge allowlist + CSP set (the unwritten `widget-sandbox`
  contract).
- **CR-25** — run Graphiti ingestion-cost estimates on real task traces before enabling any pre-gate
  episodic capture.

**Net:** every seam between the chosen pieces now has either a grounded resolution consistent with the
safety spine, or an explicit escalation flag — and each resolution names the ADR or contract that will
carry it forward (feeding the ADR-writing and contract-writing passes that follow).
