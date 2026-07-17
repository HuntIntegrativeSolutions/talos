# TALOS P3 Pre-Interview — Customizability, Extension Points & New Capabilities

> **Historical note:** this prompt predates the `platform/` → `talos/` rename and ADR-039 (which
> replaced Chroma with pgvector and cancelled Neo4j/Redis). Retained as written for the historical
> record; see README.md / ADR-039 for current state.

Paste this entire prompt into a new Claude Code session to conduct the interview.

---

## What you are doing and why

TALOS is a pre-alpha multi-agent project-execution platform at `/mnt/i/talos/`. P0 (schema,
contracts, validators), P1 (single-worker LangGraph spine), and P2 (critics registry,
five-outcome gate) are complete. 27 tests pass. The repo is live at
`github.com/HuntIntegrativeSolutions/talos.git`.

The next phase is **P3 — Full Distributed Dispatcher**. Before a P3 implementation prompt
can be written, a set of architectural decisions must be nailed down — because they affect
P3's design directly. If these go unanswered, the P3 implementer will bake in assumptions
that will require painful refactoring when real operators deploy the system.

Your job is to interview the builder on **exactly the topics listed below and no others**.
Do not conduct a general architecture review. Do not re-ask what the documents already
answer. Stay on scope.

---

## Before you ask a single question

**Read the following files silently** to understand what is already decided so you don't
re-ask it:

- `/mnt/i/talos/BLUEPRINT.md` — authoritative design doc
- `/mnt/i/talos/CLAUDE.md` — current implementation state and repo layout
- `/mnt/i/talos/docs/decisions/ADR-003.md` — four-store memory
- `/mnt/i/talos/docs/decisions/ADR-010.md` — worker isolation (session keys + Docker sandbox)
- `/mnt/i/talos/docs/decisions/ADR-011.md` — five gate outcomes
- `/mnt/i/talos/docs/decisions/ADR-014.md` — consolidation boundaries
- `/mnt/i/talos/docs/decisions/ADR-015.md` — phase reorder (gate before dispatcher)
- `/mnt/i/talos/docs/integration/04_build_sequence.md` — P0–P8 build sequence with P3 scope
- `/mnt/i/talos/platform/critics/registry.py` — current critic registry implementation

After reading, plan your interview silently. For each topic, decide: does a binding decision
already exist in the ADRs, or is there a genuine open question? Only ask about genuine gaps.

---

## Topic areas — in priority order

Topics 1–4 are **P3-blocking**: the P3 implementation prompt cannot be written without
answers to these. Topics 5–8 are **P4+ preview**: useful to decide now, not blocking P3.
Topics 9–11 are **new capability decisions**: architectural choices for features the builder
wants to add; the right build-phase for each is itself a question you should surface.

---

### P3-blocking topics

**Topic 1 — Model configuration**

The P3 dispatcher will invoke AI models as part of task execution. Nothing in the current
ADRs says how the model is selected, configured, or overridden.

- Is there one default model for all tasks, or different models per Strategy Ladder step
  (triage uses a fast model, plan uses a strong model, etc.)?
- Where does the configuration live? Environment variable? Board-level config in the DB?
  Capability-manifest declaration? Per-task override?
- Can individual boards or clients override the default model?
- Is the model identity opaque to the engine (just a string passed through) or does TALOS
  validate it against a known registry?
- What happens on model failure or rate-limit mid-task — retry same model, fall back to a
  different one, or fail closed?

This decision becomes **ADR-018**.

**Topic 2 — Checkpointer / persistence backend**

P3a will replace `MemorySaver` (in-memory, lost on restart) with `PostgresSaver` (persists
across restarts). Postgres is already required for the board schema, so PostgresSaver costs
nothing extra. But:

- Is Postgres a hard requirement for all TALOS deployments, or must the checkpointer be
  swappable (e.g., SQLite for a single-operator edge node with no Postgres server)?
- If swappable: what's the minimum viable deployment's persistence story?
- Are there deployment scenarios where even the board schema should use something other than
  Postgres?

Probe the builder's actual deployment plans — not hypotheticals. If Postgres is already
required everywhere they would actually run TALOS, making the checkpointer pluggable is
unnecessary abstraction. If real edge scenarios exist, pluggability matters.

This decision becomes **ADR-019**.

**Topic 3 — Docker sandbox requirement**

P3c will implement the Docker sandbox (ADR-010: `network:none`, `readOnlyRoot`). The
question is whether Docker is absolute:

- Is Docker a **hard runtime requirement** for all TALOS deployments, or is there a
  non-Docker fallback for environments where Docker is unavailable?
- If there is a fallback: what's the security model for the non-Docker path, and who is
  responsible for warning operators about reduced isolation?
- CI already requires Docker (testcontainers). Is that sufficient justification for a hard
  requirement?

This decision is a binding clarification to **ADR-010**, not a new ADR.

**Topic 4 — Reclaim and heartbeat thresholds**

P3a implements dead-worker reclaim: if a heartbeat goes stale past a threshold, the
in-flight task is released back to the queue.

- Should the heartbeat interval and reclaim timeout be **hardcoded constants** or
  **operator-configurable**?
- What are the builder's initial target values? (e.g., heartbeat every 30s, reclaim after
  3 missed = 90s)
- Should per-task or per-board timeout overrides be possible (e.g., a long-running NEXUS
  analysis gets a longer window)?

This affects whether a `timeout_seconds` column needs to exist on `task_runs` or `boards`.

This decision becomes **ADR-020** or folds into ADR-019.

---

### P4+ preview topics

**Topic 5 — Critic extensibility**

The current registry has a hardcoded list. For TALOS to be a general-purpose harness,
operators must be able to add domain-specific critics.

- How do operators register custom critics — Python entry points? A configured directory
  TALOS scans at startup? Explicit import path in board config?
- Is the `CriticSpec` dataclass considered stable and public API?
- Can critics be board-scoped (only runs for that board) or always global?
- Must custom critics go through a propose → review → pin lifecycle before they can run in
  the gate, consistent with the Guardian doctrine?

**Topic 6 — Gate outcome configurability**

ADR-011 defines five outcomes. The question is whether deployments can restrict the set:

- Should operators be able to disable `waive` (enforce escalate-or-reject only)?
- Should operators be able to disable `escalate` (solo-reviewer mode)?
- If restrictions are possible: where do they live — board config, capability manifest, or
  hardcoded per deployment?

**Topic 7 — Memory backend flexibility (P4 preview)**

P4 will build the four-store memory layer (Postgres SoR, Neo4j graph, pgvector/Chroma
vector, Redis working memory).

- Is Neo4j a hard requirement, or should the graph store be swappable (e.g., Apache Age
  on Postgres for operators who can't run Neo4j)?
- Is Redis a hard requirement for working memory, or is an in-memory dict acceptable for
  single-worker deployments?
- Should the vector store be swappable (pgvector vs. Chroma vs. Qdrant)?
- Probe real deployment plans: if Neo4j and Redis are already in the stack, swappability
  is premature abstraction. Only pursue flexibility where a real scenario requires it.

**Topic 8 — Capability pack loading**

NEXUS is the first domain capability. The question is how future packs attach:

- How does an operator register a new pack — static config file, API call, Python entry
  point?
- What is the discovery mechanism at startup?
- Should pack loading require a human approval step (propose → review → activate) consistent
  with the Guardian doctrine, or is it admin-only config?

---

### New capability decisions

**Topic 9 — Verifier critic type**

The builder wants a new critic variant: a **Verifier** that runs an LLM sub-agent against
the deliverable plus a rubric, and fires **before** the deterministic critics. Think of it
as a built-in eval layer — the LLM judge checks the deliverable meets the rubric, then the
deterministic critics check the structural invariants.

This is architecturally significant because it introduces nondeterminism and LLM cost into
the gate path. Ask:

- **Rubric format**: How is the rubric defined? Free text? Structured scoring guide (e.g.,
  JSON with criteria and weights)? Is the rubric part of `CriticSpec` or attached to the
  task itself?
- **Model**: Does the verifier use the same model as the task execution, or a different
  (potentially stronger) model? Is this configurable per verifier?
- **Verdict semantics**: Does the verifier's verdict have the same pass/fail/waivable
  structure as deterministic critics, or does it produce a score or confidence band that
  gets thresholded?
- **Failure behavior**: If the verifier LLM is unavailable, does the gate fail closed
  (block the task), fail open (skip the verifier), or surface as a `warn` verdict? This
  is a safety question — press hard on it.
- **Safety class**: Can a verifier be `safety_class=True` (escalate-only, never waivable)?
  Or are verifiers inherently advisory since they are nondeterministic?
- **Ordering guarantee**: Is the ordering always "all verifiers → all deterministic critics"
  or can a critic spec declare its own position in the pipeline?
- **Cost control**: Multiple verifiers on one task could be expensive. Is there a concurrency
  cap, a token budget per gate run, or a circuit-breaker if a verifier LLM call exceeds a
  time limit?
- **Build phase**: Should the verifier type land in P2 (already built), P3, or P5?
  The answer affects the P3 implementation scope.

This decision becomes **ADR-021**.

**Topic 10 — Observability and span-level tracing**

The builder wants real-time, actionable instrumentation at span level — not just logs, but
structured traces where each strategy ladder step, each critic run, each LLM call, and each
gate transition is a span with timing, token counts, and outcome.

The key distinction from chase-ai's Agentic OS research: most teams build dashboards first
and miss the underlying instrumentation infrastructure. TALOS must instrument first and
surface second.

Ask:

- **Tracing backend**: Is this OpenTelemetry (OTLP to Jaeger, Honeycomb, Datadog)? Spans
  written into TALOS's own `task_events` table? Both? The choice determines whether TALOS
  carries an external dependency.
- **Span scope**: What is the minimum viable span set for P3?
  Candidates: worker claim, spine node entry/exit, critic run (per critic), gate interrupt,
  gate resume, post-gate write, LangGraph checkpoint write. Which are required vs. nice-to-have?
- **LLM call spans**: Should every model call (including verifier critics) carry a child span
  with `model_id`, `prompt_tokens`, `completion_tokens`, `latency_ms`?
- **Storage**: Are traces stored in TALOS's Postgres (`task_events`?), in a separate spans
  table, or forwarded to an external collector only?
- **Cockpit access**: Should traces be queryable from the cockpit (P7) — e.g., "show me the
  timeline of this task with each step's latency" — or are they operational/DevOps only?
- **Alerting**: What signals trigger an alert? Span timeout? Critic failure rate above a
  threshold? Who is notified and how (Slack, email, webhook)?
- **Build phase**: What level of tracing lands in P3 vs. P7 (cockpit) vs. a dedicated P? Is
  this a standalone phase between P3 and P4, or woven into each phase?

This decision becomes **ADR-022**.

**Topic 11 — Rule extraction in Crystallize**

The Crystallize step (P5) is where completed task knowledge is consolidated into long-term
memory. The builder wants to add **rule extraction**: after crystallization, a sub-agent
analyzes the completed task and extracts generalizable rules or patterns (e.g., "always
check interlock Z before modifying setpoint on this equipment type") that get stored in
the knowledge layer for future task context.

This connects directly to the Graphiti bi-temporal graph (from the upstream deep-dive) and
ADR-014 (consolidation boundaries). Chase-ai's `/dream` feature — which performs
contradiction resolution and data pruning on accumulated knowledge — is the closest analog
in production use today.

Ask:

- **Rule types**: What categories of rules should be extracted?
  - **Factual**: "PLC tag T_PUMP_01 maps to motor M-100 in area West." (Known-entity facts)
  - **Procedural**: "Always verify interlock before modifying setpoint." (Behavioral patterns)
  - **Project-context**: "On the Acme project, tag naming follows ISA-5.1 with Wrk_ prefix."
  - Are all three in scope, or only some?
- **Extraction trigger**: Is rule extraction automatic (always runs after every crystallize)
  or operator-triggered? Is there a confidence threshold below which extraction is skipped?
- **Validation gate**: Must extracted rules pass through a human review gate before they
  enter the knowledge base? Or is extraction autonomous within ADR-014's boundary (single
  client scope, below sensitivity threshold)?
- **Storage**: Where do extracted rules live? `add_triplet()` into Graphiti's Neo4j graph?
  A dedicated `rules` table in Postgres? Both, based on rule type?
- **Contradiction handling**: If a newly extracted rule contradicts an existing rule (e.g.,
  "don't modify setpoint X" vs. a previously stored "modify setpoint X is safe"),
  what is the resolution mechanism? Graphiti handles this bi-temporally (old edge gets
  `invalid_at` set, not deleted). Does TALOS rely on Graphiti's native contradiction
  detection, or add its own?
- **Cross-client promotion**: ADR-014 says cross-scope MERGE is forbidden autonomously. If
  a rule extracted from one client's task would be universally useful (a safety procedure),
  what is the path to promoting it from `client` scope to `shared` scope? Is it a gate?
  Who approves it?
- **Rule retrieval**: How do extracted rules surface to future tasks? Semantic search
  (vector similarity)? Graph traversal (follow equipment entity edges)? Explicit lookup
  by tag/equipment ID? What's the retrieval interface the dispatcher will call?
- **Build phase**: Crystallize is P5 in the build sequence. Does rule extraction land fully
  in P5, or should a rule storage schema and retrieval stub land in P4 (memory federation)
  so P5 can populate it?

This decision becomes **ADR-023**.

---

## Conducting the interview

- Use `AskUserQuestion` for every question — never ask in free text.
- Work through **one topic at a time**, in order. Do not jump ahead.
- Within a topic, ask up to 3 questions per round. Probe follow-ups before closing the topic.
- Topics 9, 10, and 11 require an extra question in each: **"Which build phase should this
  land in?"** — the answer may move work into or out of P3.
- Give your recommendation when you have a strong opinion. Flag over-engineering risks.
- Do not touch any file during the interview.

---

## What to produce when the interview is complete

1. **Summarize every decision** back to the builder and get confirmation before writing
   anything.

2. **Draft new ADRs** for each binding decision, following the format in `docs/decisions/`:
   - `docs/decisions/ADR-018-model-configuration.md`
   - `docs/decisions/ADR-019-persistence-backend.md`
   - `docs/decisions/ADR-020-reclaim-thresholds.md`
   - `docs/decisions/ADR-021-verifier-critic-type.md`
   - `docs/decisions/ADR-022-observability-tracing.md`
   - `docs/decisions/ADR-023-rule-extraction-crystallize.md`
   - Any clarification records needed for ADR-010 (Docker) and ADR-011 (gate restrictions)

   Each ADR needs: title, status, context, decision, consequences, and if applicable a
   "what this closes" line referencing red-team items or build-sequence phases.

3. **Write a handoff note** at `/mnt/i/talos/docs/p3-pre-build-decisions.md` — a plain-English
   summary of every decision made, organized by P3 sub-phase:
   - **P3a** (PostgresSaver + reclaim): persistence backend, reclaim thresholds
   - **P3b** (dispatcher + model): model configuration, heartbeat
   - **P3c** (Docker sandbox): Docker requirement decision
   - **P3d** (PM hooks + observability): tracing minimum for P3
   - **P4 preview**: memory backend flexibility, capability pack loading
   - **Future phases**: verifier critic, rule extraction — phase assigned + constraints noted
   - **Out of P3 scope**: anything explicitly deferred

   This file is the handoff to the next Claude session that will write the P3 prompt. It
   must be specific enough that the P3 prompt author doesn't need to re-read ADR prose to
   know which constraints apply to each sub-phase.

4. **Do not write the P3 implementation prompt.** That is a separate session.

---

## Rules

- Do not re-ask what the ADRs already answer.
- Do not conduct a general architecture review. Stay on topic.
- Do not write code or generate SQL.
- Do not touch any existing file until the builder has confirmed the summary.
- Ask about real deployment scenarios, not hypotheticals. If the builder says "it would be
  nice to support X," probe whether any actual planned deployment requires X.
- Flag over-engineering explicitly. Pluggability has a cost; unnecessary abstraction is a
  bug.
- On Topics 9, 10, and 11: always surface the build-phase question. If the builder wants
  something "in P3," flag what it displaces or extends in the current P3 scope.

---

Start by reading the files listed above silently. Plan your questions. Then begin with
Topic 1.
