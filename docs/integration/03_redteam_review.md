# TALOS — Red-Team Review (Adversarial Pre-Build Pass)

> **What this is:** a skeptical principal-engineer review of the settled TALOS integration design,
> conducted **before any code is written**, looking for the reasons it will fail at build time or in
> production. It is deliberately adversarial: for each axis a failure scenario was constructed and
> walked through the design; an axis is marked clean only if the walk-through genuinely could not be
> made to fail. None could.
>
> **What this is not:** a redesign, a re-litigation of adopt/reject calls, or praise. Every finding
> names a concrete scenario or a specific under-specified clause, and cites the doc + anchor where the
> hole lives.
>
> **Inputs reviewed in full:** `BLUEPRINT.md` (v0.6); `docs/integration/02_unified_architecture.md`;
> `docs/integration/01_conflicts_and_resolutions.md` (CR-01…CR-26); the four contracts (`board-api`,
> `capability-manifest`, `nexus-federation`, `widget-sandbox`); ADRs 003/004/005/007/008/009/010/011/
> 013/014/016 + the decisions README; upstream license/scale facts from `docs/upstream/*`.
>
> **Method note — the design is self-aware.** It flags ~10 of its own gaps (CR-08, CR-10, CR-20,
> CR-25, the two-gate human count, PageRank-has-no-ADR). A credible red-team must therefore separate
> *what the design genuinely missed* from *what it already flagged*. Every finding below carries a
> **Bucket**:
> - **MISSED** — the design does not see this hole. *Lead with these.*
> - **SHARPENING** — the design saw the area but not the specific rule or consequence stated here.
> - **CONFIRM** — already escalated by the design; this review verifies it must stay open.
>
> **Weighting (per charter):** safety-spine and isolation findings are weighted highest — they are the
> project's reason for existing. The BLOCKER list leads with them.

**Status:** Review · **Date:** 2026-06-12 · **Reviewer role:** skeptical principal engineer
**Verdict:** **5 BLOCKER · 9 MAJOR · 13 MEDIUM · 1 MINOR + 12 unstated assumptions.** No axis clean.

---

## 1. Findings table

Citation key: `02 §N` = `02_unified_architecture.md`; `CR-NN` = `01_conflicts_and_resolutions.md`;
`ADR-0NN` = `docs/decisions/`; `BP §N` = `BLUEPRINT.md`; contract names = `docs/contracts/`.

| ID | Axis | Severity | Bucket | The hole | Doc to fix | Suggested fix |
| :-- | :-- | :-- | :-- | :-- | :-- | :-- |
| RT-01 | Safety spine | **BLOCKER** | MISSED | `board-api §3` gate-outcome envelope carries `actor:"thunt"` as a **self-asserted string**; no authn binds the caller to a human identity. Invariant `02 §8 #2` ("a human always sets `approved_at`") has **no structural enforcer** distinguishing a human from a service token / compromised orchestrator. CR-17 demands one. | `board-api.md`; `ADR-011` | `approved_by` derives from an authenticated human-session identity, never a request field; a distinct human-auth token class; engine rejects `submitGateOutcome` from non-human credentials. |
| RT-02 | Safety spine | MAJOR | MISSED | `waivable:false` on safety critics is a **manually-set per-template flag** (`ADR-011` action #2). A mis-flag (`waivable:true`) silently makes a safety critic waivable → a waiver becomes a doctrine bypass. No meta-check binds safety-class ↔ non-waivable. CR-17 self-violation. | `ADR-011` / critics contract | Bind safety-class ⇒ `waivable:false` in a critic **registry**; a meta-critic/test fails the build if any safety-class critic is waivable. Not a free template field. |
| RT-03 | Safety spine | MAJOR | MISSED | Escalate-only safety critics assume **≥2 reviewers** (`ADR-011` §15); the deployment is single-operator ("thunt", Hunt Integrative Solutions LLC). Safety fail → Escalate → no second reviewer → deadlock, or the one human double-roles (defeats the protection). | `ADR-011` | **DECIDED 2026-06-14:** Solo waiver with mandatory audit note. Solo operator may waive an escalated finding with a written justification that enters the permanent audit log. No cool-off period; no external co-signer required. Every waiver is permanently visible and reviewable. Update `ADR-011`. |
| RT-04 | Safety spine | MEDIUM | SHARPENING | `ADR-016` Mechanism §53 emits **MEDIUM → auto-dispatch unconditionally**; the "safety-significant → HIGH → no auto-dispatch" rule (CR-22) is `ADR-016` **action item #7, unbuilt**, and ADR-016 is **"Proposed."** Until #7 lands the escalator auto-dispatches safety remediation (still gated — no live write — but autonomous budget spend on un-staged safety work). | `ADR-016` | Implement severity-gating before the escalator ships; promote ADR-016 to Accepted. |
| RT-05 | Safety spine | MEDIUM | CONFIRM | `nexus-federation` Open Q#2: NEXUS-unavailable behavior (fail-closed vs stale cache) is unpinned. `citations-resolvable` needs confirmed-status; a stale cache could let a since-`dismissed` finding cite as `confirmed`. | `nexus-federation.md` | Pin **fail-closed** on the citation/evidence path; never stale cache for confirmed-status checks. |
| RT-06 | Isolation | **BLOCKER** | MISSED | Promotion `[client]→[shared]` has **no deterministic de-identification critic**. `ADR-005` "strip the instance" is human-only; the sanitization checklist is explicitly **"Revisit" (undefined)**. A leak propagates to **every** client incl. air-gapped via the shared pack (`ADR-008`). Direct CR-17 violation (isolation invariant on human judgment alone). | `ADR-005` / critics contract | Required, **non-waivable** `no-client-identifiers-in-shared` critic scanning for client tag-prefixes / CH-numbers / instance identifiers on every promotion. |
| RT-07 | Isolation | **BLOCKER** (conditional) | MISSED | `BP §226-227` + `02 §1` call Acme **"air-gapped; data never leaves"** *and* "runs the DeepSeek API on its own line." "Its own line" = a network line ≠ air gap. CR-11/CR-03/§7 guarantees all derive from "data never leaves." Discriminating fact: hosted `api.deepseek.com` vs self-hosted weights (existing `deepseek-chat`/`DEEPSEEK_API_KEY` wiring corroborates **hosted** → client PLC IP egresses every turn). | `BP §220-229` + new data-egress/residency ADR (none exists) | **DECIDED 2026-06-14:** Drop the air-gap claim. PLC context egresses to hosted model endpoints; the air-gap framing is dropped. All architecture guarantees that derived from "data never leaves" must be restated accurately. ADR-017 to be written capturing this decision and enumerating data-egress per edge class. |
| RT-08 | Isolation | MAJOR | SHARPENING | Graphiti community/saga **rollups are write-side** ops; the `group_id` chokepoint filters **reads**, but no rule pins detection to a single `group_id`. Since every query admits `[shared]` alongside `[client]`, a community spanning `[clientA]+[shared]` blends into a node readable by clientB (also admits `[shared]`). CR-03 lists rollups under NEEDS-PROTOTYPE but states **no design rule**. | `nexus-federation.md` / `ADR-014` | Pin: community/saga detection scoped to exactly one `group_id`, never spanning client+shared; a shared community is built only from shared nodes. |
| RT-09 | Isolation | MAJOR | MISSED | The "only hard boundary" (RLS) is **commented-out DDL today** — `board-api` invariant #3 admits the RLS block (`engine/schema.sql` 260-271) is "documented/intended (commented), not yet live." Session keys are "scoping, not auth" (CR-02); `group_id` is app-enforced (CR-03). **No live hard isolation boundary exists right now.** | `engine/schema.sql` + `ADR-010`/`board-api` | Enable RLS DDL + a test that fails if any board-scoped table lacks an active policy, **before** any multi-board/client data coexists. |
| RT-10 | Isolation | MAJOR | MISSED | `ADR-013` keeps "one coherent planner context" and "switching client re-scopes everything," enforced by "memory reads RLS-bound." The **LLM context window is not a memory read** — a reused planner process carries board-A IP in-context into board-B. RLS does not reach the context window. | `ADR-013` | Pin: planner context is per-(board,session), **destroyed on scope switch**; never a reused LLM session across scopes. |
| RT-11 | Isolation | MEDIUM | SHARPENING | `ADR-014` calls the cross-scope MERGE prohibition "absolute," but it is a **soft check in consolidation code**: the chokepoint admits `[shared]` next to `[client]`, so both nodes are visible in one scope; only pipeline logic forbids the MERGE (same weaker app-enforced regime as CR-03). | `ADR-014`/`nexus-federation` | Make the consolidation candidate-set query structurally unable to return mixed scopes; pairs with RT-12. |
| RT-12 | Isolation | MEDIUM | MISSED | Consolidation's 60s LLM timeout → "insert without consolidation" (agent-zero). The **sensitivity classifier** (autonomous-vs-gate, `ADR-014`) fail-direction on timeout/error is unspecified. | `ADR-014` | Sensitivity classification **fail-closed** (timeout/error ⇒ treat as sensitive ⇒ gate). |
| RT-13 | Isolation | MEDIUM | MISSED | The live dashboard tails events over **Redis pub/sub** (`02 §4`); RLS doesn't reach Redis and the `group_id` chokepoint is Neo4j-only. If board channels aren't namespace-scoped, a subscriber receives another board's stream — an isolation surface outside every named enforcer. | `ADR-003` / a Redis note | Namespace every Redis channel/key by `board_id`; document Redis as an isolation surface with its own enforcer. |
| RT-14 | Contract | **BLOCKER** | MISSED | `capability-manifest` lists **5 example tools**; NEXUS has **85**, several of which **write the SoR** (`tag_annotate`, `ingest_*`, `promote_raw_addresses_to_tags`, `reconcile_descriptions`, `nexus_reindex`, `backfill_*`, `generate_dox_tree`). `nexus-federation` invariant #1 forbids TALOS writing NEXUS facts — but exposing these tools enables exactly that. Two teams must negotiate the disposition of all 85; the contract enumerates none. | `capability-manifest.md` | Manifest must disposition **every** NEXUS tool; SoR-mutating tools are **excluded from the attachable manifest** (not exposed); validation rejects any manifest exposing a NEXUS-fact-write tool. |
| RT-15 | Contract | MAJOR | MISSED | `nexus-federation` lacks the concrete **node/edge/finding output schema** `ADR-007` action #1 demands "before the memory phase." It references a versioned schema without defining it; subgraph-extraction API is Open Q#3. PageRank/federation cannot be built against it. | `nexus-federation.md` | Include (or normatively reference a frozen) NEXUS output schema: node types, edge types, finding fields, subgraph-extraction params; version pinned. |
| RT-16 | Contract | MAJOR | MISSED | `board-api §3` freezes the gate-outcome write as **[D]** while "human vs service caller" is Open Q#4. You cannot freeze "only a human may invoke" without specifying how "human" is established (the contract face of RT-01). | `board-api.md` | Specify the authn/authz model for the gate write before freeze. |
| RT-17 | Contract | MEDIUM | CONFIRM | `board-api` read-projection column allowlist is "a recommendation, not a source-pinned invariant" (Open Q#5); worker/credential columns (`session_id`, `idempotency_key`, `claim_lock`, `worker_pid`) may reach the view. | `board-api.md` | Freeze the least-privilege projection allowlist as **[D]**. |
| RT-18 | Contract | MEDIUM | CONFIRM | Two-gate human count unspecified (`02 §3` step 6 NEW GAP; `02 §9`): plan-gate **and** deliverable-gate = two approvals or one fused review? Affects UX and whether the plan envelope is re-confirmed. | `ADR-011`/`capability-manifest` | **DECIDED 2026-06-14:** Two separate approvals. Plan-gate fires first (human approves approach and scope); deliverable-gate fires after execution (human approves output). Distinct UI moments, distinct audit records. Update `ADR-011`. |
| RT-19 | Contract | MEDIUM | SHARPENING | `widget-sandbox` freezes the 4-type allowlist but the **enforcing critics** (CSP-conformance, allowlist-only) are "not yet enumerated" (Open Q#3) and the CSP byte-set is NEEDS-PROTOTYPE. A widget could pin without a CSP-conformance critic existing. | `widget-sandbox.md` | Enumerate required widget critics before Phase 5; CSP/allowlist-conformance critic = required + non-waivable. |
| RT-20 | Resume / idempotency | **BLOCKER** | MISSED | The idempotency key embeds `{attempt}` (`ADR-010`, CR-12: `task:{board_id}:{task_id}:{attempt}:{step}`) **and** nothing defines whether reclaim reuses or increments `attempt`, nor that the key-check is atomic with the side effect. **Two unreconciled resume mechanisms**: LangGraph `thread_id` checkpoint-resume vs Hermes `attempt` reclaim ("reclaim even if PID alive at >1h stale" → split-brain reachable). NOT a spine breach (no live tool) — a **correctness / cost / double-dispatch** BLOCKER. | `ADR-010`, CR-12 (`01`), `ADR-016` | (1) Define `thread_id`↔`attempt` mapping; (2) make the logical-step idempotency key **attempt-independent**; (3) idempotency-row insert + side effect in one transaction under a UNIQUE constraint. |
| RT-21 | Resume / idempotency | MAJOR | MISSED | CR-12 addresses only LangGraph **Gotcha 1**. **Gotcha 2** (multi-writer channel reducers must be commutative/associative) is unaddressed; parallel workers / CSV batch fan-out into a shared `operator.add` channel → order-dependent, non-deterministic state → undermines the gate's "reproducible blocking decision." | LangGraph-integration ADR / `ADR-011` | Require all multi-writer reducers commutative+associative; fold into the gate-reproducibility invariant. |
| RT-22 | Resume / idempotency | MAJOR | SHARPENING | Crystallize ingestion (`02 §3` step 7; 4-15 LLM calls, ~5k-40k tokens) is a **post-gate node** subject to re-execution, but Graphiti `add_episode` is **not idempotent by default** → resume re-ingests → duplicate `:Entity/:Episodic` nodes pollute the graph + corrupt PageRank + double cost. The three-axis budget (hard ceiling) can also truncate mid-episode → partial bi-temporal graph. CR-25 flags only cost. | `ADR-014`/`capability-manifest`/CR-25 | Per-episode dedup key + **atomic all-or-nothing** ingestion (complete within budget or roll back; never partial). |
| RT-23 | License / build | MAJOR (**verify**) | MISSED | The license-policy ADR (CR-14/CR-15) covers adopted **source patterns** (OpenLumara GPL-3.0, gh-aw unstated) but never the **runtime engines** the design hard-depends on — Neo4j, and specifically the **GDS library CR-10 names as the at-scale PageRank fallback**. Neo4j edition + GDS terms must be **verified** vs TALOS's MIT posture. Air-gapped edge compounds it (a licensed GDS with no license-server path). *This review asserts no license string — it flags the unverified dependency.* | new license/dependency-policy ADR | Extend the ADR to enumerate every runtime-engine license (Neo4j edition, GDS, Graphiti transitive deps, pylogix, Logix Echo SDK) with MIT-compatibility recorded. |
| RT-24 | License / build | MEDIUM | CONFIRM | Logix Echo SDK path/cost (CR-16) and gh-aw license (CR-15) remain open human decisions that block the PLC-test capability and any gh-aw code reuse. | license ADR / `capability-manifest` | Keep escalated; confirm before build. |
| RT-25 | License / build | MINOR | SHARPENING | "Binary-search context trimming" (OpenLumara, clean-room per CR-14) and PageRank are booked as "adopt patterns" but are **non-trivial reimplementations** + a new NetworkX (BSD) dependency. | license ADR / build-effort note | Track as build tasks with effort, not zero-cost adoptions. |
| RT-26 | Scale / ops | MAJOR | SHARPENING | PageRank **silent truncation → incomplete NEXUS review canvas**. CR-10 bounds the subgraph (k-hop + node budget, "truncate by edge weight"); NEXUS `address_xref` is dense (~127k entries; one rack-word touches many rungs) → first-hop budget blowout → silent truncation of the **exact slice the human approves against** (could drop a safety interlock rung). CR-10 flags the threshold, not this consequence; PageRank has no ADR owner. | new PageRank ADR / `nexus-federation` | Truncation must be **visible** on the canvas ("N nodes dropped"); minimum-coverage guarantee for safety-relevant edges; give PageRank an ADR home. |
| RT-27 | Scale / ops | MEDIUM | SHARPENING | Hermes "last_heartbeat_at > 1h stale → reclaim **even if PID alive**" reclaims legitimately long ops (large Graphiti ingest, slow edge DeepSeek call, big PageRank) → split-brain (feeds RT-20). Breaker trips at `DEFAULT_FAILURE_LIMIT=2`. | `BP §230-232`/`ADR-016` | Emit heartbeat during long capability calls (resumable-cursor progress doubles as heartbeat); reconcile with RT-20. |
| RT-28 | Scale / ops | MEDIUM | MISSED | **No DB snapshot/rollback** for the four polyglot stores. `ADR-003` lists "back up" as a cost only; space-agent git versions the UI shell, `task_events` is append-only, but a corrupting write to Neo4j/pgvector (e.g., a leak slipping RT-06/RT-11) is **irreversible**. nexus-agents' snapshot-before-write pattern is not carried to TALOS stores. | new backup/rollback ADR / `ADR-003` | Snapshot before write-class ops (consolidation, promotion, crystallize) on graph+vector stores; bound retention. |
| RT-29 | Scale / ops | MEDIUM | MISSED | CR-07 turns **every** episodic-vs-NEXUS disagreement into a candidate finding; at scale this floods the human whose KPI is "time-to-confident-approval" → alert fatigue → rubber-stamping erodes the gate. | `ADR-014`/`nexus-federation` | Rate-limit/dedupe/cluster + severity-tier contradiction findings before the human queue. |
| RT-30 | Scale / ops | MEDIUM | MISSED | Air-gapped edge **breaks the compounding-knowledge loop**: the thick edge pulls the shared pack read-only and pushes nothing, so its crystallized skills/paths/episodes never propagate. Intended? Unstated. Physical pull across a true air gap (sneakernet?) is undefined (and conflicts with RT-07 if there *is* a line). | `ADR-008` / mothership↔edge contract (NEW GAP, unfrozen) | State whether edges are learning-isolated by design; if not, define a sanitized, gated push path. |

---

## 2. Unstated assumptions

Each is something the design relies on that no ADR or contract actually guarantees. The arrow names the
finding it feeds.

- **UA-1** — NEXUS exposes finding-status over MCP. No such tool exists in the current 85;
  `capability-manifest`'s `findings.exposes_status:true` and the entire CR-18 citation bridge are
  aspirational. → RT-14 / RT-15.
- **UA-2** — "One NEXUS per client" conflates per-**client** with per-**plant**. Acme is one of
  each today; the CR-11 isolation guarantee needs restating if one client ever spans two plants or the
  mothership needs cross-client analytics.
- **UA-3** — Graphiti `add_episode`/`add_triplet` are idempotent. They are not, by default. → RT-22.
- **UA-4** — A human reviewer can spot a leaked client identifier *inside* a "shared" artifact. → RT-06.
- **UA-5** — ≥2 reviewers exist for the Escalate outcome. → RT-03.
- **UA-6** — DeepSeek egress is acceptable, or the edge can self-host the weights. → RT-07.
- **UA-7** — RLS will be enabled before production (it is commented out today). → RT-09.
- **UA-8** — The gate `actor` field corresponds to an authenticated human. → RT-01.
- **UA-9** — The plan-gate complexity-threshold metric exists and is non-gameable (parking-lot;
  undefined). Bounded — it cannot bypass the deliverable gate or the write/safety hard-stop — but it is
  undefined on a safety-adjacent path.
- **UA-10** — A defined LangGraph `thread_id` ↔ Hermes `attempt` mapping. → RT-20.
- **UA-11** — Neo4j / GDS licensing is MIT-compatible. → RT-23.
- **UA-12** — The raw-trail endpoint won't be planner-ingested. `board-api` invariant #4 is "a consumer
  rule, not an endpoint ban"; nothing *structurally* stops the planner from ingesting raw transcripts
  (an `ADR-013` / CR-01 coherence + isolation soft spot).

---

## 3. BLOCKER walk-throughs

Each BLOCKER below is a concrete failure scenario walked through the design as written.

### RT-01 — Forged approval *(safety spine)*

A prompt-injected or buggy orchestrator — the exact threat model `ADR-001` designs for ("the
orchestrator can be fully compromised") — issues
`submitGateOutcome {outcome:"approve", actor:"thunt", task_id:T}` to the board API. The contract says
"only a human may invoke" and forbids "any API token … setting `approved_at`," but specifies **no
mechanism** to authenticate that the caller is the human `thunt` rather than a service credential the
orchestrator already holds. The engine sets `approved_at` (every required critic having passed) and the
post-gate node runs. It cannot reach a live processor — live ops have no tool, so the spine's last line
holds — **but** the approval is the trust event for *capability expansion*: the same path approves a
**crystallized skill** (CR-04) and a **promotion-to-`[shared]`** (`ADR-005`). A forged approval thus
mints a trusted self-authored skill, or leaks client IP to the shared scope, with no human in the loop.
The spine's load-bearing claim — "a human always sets `approved_at`" — rests on an unauthenticated
string. *Fix: RT-01 / RT-16.*

### RT-06 — Incomplete sanitization leaks IP to every client *(isolation)*

An agent crystallizes a strategy path from an Acme burner-sequencing audit. At the promotion
gate a human reviews the **abstracted** artifact and approves it `[shared]` — but the abstraction left
a client-specific tag prefix (`CH75…`) or an alarm-philosophy detail embedded in prose. No
deterministic critic checks for client identifiers (`ADR-005`'s "strip the instance" is human
judgment; its sanitization checklist is "Revisit / undefined"). The shared node now carries Acme
IP. Via graph-as-linker every other client's vault read-links it (`ADR-008`), and the **air-gapped edge
pulls it inside the sanitized shared pack** — so the leak crosses to a *different* client's site. The
human who approved it saw the abstracted form; the leaked token is *inside* the artifact they were
judging, which is precisely why human review can't catch it. This is the exact CR-17 failure mode: an
isolation invariant resting on human review with no structural enforcer. *Fix: RT-06 (deterministic
de-id critic).*

### RT-07 — Client process IP egresses to a third party *(isolation, conditional)*

Every analysis turn on the Acme edge sends rung text, tag names, and process logic to the model
the docs name as "the DeepSeek API on its own line." The same docs call this edge "air-gapped; data
never leaves," and CR-11 / CR-03 / `02 §7` derive their isolation guarantees from that phrase. If
"DeepSeek API" is the hosted `api.deepseek.com` — corroborated by the existing
`deepseek-chat`/`deepseek-reasoner` + `DEEPSEEK_API_KEY` wiring — then the most sensitive client IP, the
very thing the `[client]` scope, the `group_id` chokepoint, and the per-client NEXUS instance exist to
contain, leaves the site to a third-party LLM on every turn. The terminological contradiction ("its own
line" = a network line ≠ an air gap) stands on its own regardless of hosting; the egress *consequence*
is conditional on hosted-vs-self-hosted, which the design never states. Either the weights are
self-hosted (then say so, and the air-gap claim survives) or the air-gap isolation guarantees are
false. *Fix: RT-07 (decide hosting; add a data-egress ADR).*

### RT-14 — An agent mutates the system-of-record *(contract / spine-adjacent)*

NEXUS's real MCP surface includes `tag_annotate`, `promote_raw_addresses_to_tags`,
`reconcile_descriptions`, `nexus_reindex`, and the `ingest_*` family — all of which **write `nexus.db`**,
NEXUS's own system-of-record. The `capability-manifest` example declares five read/offline-write tools
and says nothing about these. If the attached manifest exposes them (the contract neither lists nor
forbids them by name), a worker under the **read** profile can call `tag_annotate` and **mutate the
SoR** — directly violating `nexus-federation` invariant #1 ("TALOS never writes or invalidates NEXUS
facts") and CR-07's entire "NEXUS wins" guarantee. The NEXUS pack author and the TALOS platform team
cannot build this boundary without negotiating the disposition of all 85 tools, and the contract that
is supposed to be frozen pins none of it. *Fix: RT-14 (disposition every tool; exclude SoR-writers).*

### RT-20 — Double-applied post-gate side effects *(resume / idempotency — correctness)*

A worker passes the deliverable gate; the human approves. The worker begins the post-gate node (persist
artifact, write episode, notify, emit `milestone_risk`/finding) but its DeepSeek call on the edge runs
long and its heartbeat lapses past one hour. Hermes reclaims the task "**even if PID alive**" (its
stale-reclaim rule), minting a new claim. Now two executions exist. The idempotency key is
`task:{board_id}:{task_id}:{attempt}:{step}` — and the design never says whether reclaim **reuses** or
**increments** `attempt`, nor reconciles Hermes reclaim with LangGraph `thread_id` resume:

- If `attempt` **increments**, the two executions have **different keys** and both run the post-gate
  node — two episodes ingested (RT-22 doubles the cost and pollutes the graph), two notifications, and,
  because a MEDIUM finding auto-dispatches a remediation task (`ADR-016`), **two remediation tasks** for
  one defect.
- If `attempt` is **reused**, the two share a key and race — unless the idempotency row is inserted in
  the same transaction as the side effect under a UNIQUE constraint, which CR-12 does not require.

No live processor is touched (the spine holds), but the "exactly once after the gate" guarantee — the
explicit purpose of CR-12 — fails in the very crash-and-reclaim scenario CR-12 cites. The root cause is
two unreconciled resume mechanisms with an attempt-dependent idempotency key. *Fix: RT-20 (define the
mapping; attempt-independent key; atomic check).*

---

## 4. Per-axis verdict

No axis is clean — the right outcome for an adversarial pass. For each, the failure scenario walked
through the design; in every case it could be made to fail.

| Axis | Findings | Worst | Clean? |
| :-- | :-- | :-- | :-- |
| 1 — Safety spine | RT-01..05 | **BLOCKER** (RT-01) | No — the gate write is unauthenticated. |
| 2 — Multi-client isolation | RT-06..13 | **BLOCKER** ×2 (RT-06, RT-07) | No — promotion has no de-id critic; air-gap claim contradicts DeepSeek egress; RLS is off today. |
| 3 — Contract completeness | RT-14..19 | **BLOCKER** (RT-14) | No — `capability-manifest` doesn't disposition NEXUS's real tools; `nexus-federation` lacks its output schema. |
| 4 — Resume / idempotency | RT-20..22 | **BLOCKER** (RT-20) | No — attempt-keyed idempotency + two unreconciled resume paths. |
| 5 — License / build risk | RT-23..25 | MAJOR (RT-23) | No — engine/GDS licensing unverified vs MIT. |
| 6 — Scale / operability | RT-26..30 | MAJOR (RT-26) | No — PageRank silent truncation; no snapshot/rollback; finding-flood. |
| 7 — Unstated assumptions | UA-1..12 | — | No — twelve load-bearing assumptions no contract guarantees. |

---

## 5. Shortlists

### Must fix before build

Spine, isolation, and the contract freezes / models everything else depends on. Listed
spine-and-isolation first per charter weighting.

1. **RT-01** — human-actor authentication on the gate write *(spine BLOCKER)*.
2. **RT-06** — deterministic de-identification critic on promotion *(isolation BLOCKER)*.
3. **RT-07** — resolve air-gap vs DeepSeek egress; add a data-egress ADR *(isolation BLOCKER)*.
4. **RT-14** — disposition all 85 NEXUS tools in `capability-manifest`; exclude SoR-writers *(contract BLOCKER)*.
5. **RT-20** — define `thread_id`↔`attempt`; make the idempotency key attempt-independent + atomic *(correctness BLOCKER)*.
6. **RT-09** — enable RLS DDL + a policy-presence test *(the only hard boundary is off today)*.
7. **RT-02** — bind safety-class ⇒ non-waivable via a registry + meta-check *(spine)*.
8. **RT-15** — define the NEXUS node/edge/finding output schema in `nexus-federation` *(blocks Phase 4)*.
9. **RT-23** — **verify** Neo4j / GDS (and the rest) licensing vs MIT before committing the engine choice.

### Fix during build

Real, but closable as the dependent phase lands:
RT-03 (single-reviewer escalation policy) · RT-04 (escalator severity-gating + Accept ADR-016) ·
RT-05 (NEXUS-unavailable fail-closed) · RT-08 (rollup `group_id` scoping) · RT-10 (planner context reset
on scope switch) · RT-11 (structural cross-scope MERGE block) · RT-12 (sensitivity fail-closed) ·
RT-13 (Redis channel namespacing) · RT-16 / RT-17 (board-api authn + projection freeze) ·
RT-19 (widget critic set) · RT-21 (commutative reducers) · RT-22 (idempotent/atomic ingestion) ·
RT-26 (visible PageRank truncation + PageRank ADR) · RT-27 (heartbeat during long calls) ·
RT-28 (snapshot/rollback for graph + vector) · RT-29 (contradiction-finding rate-limit) ·
RT-30 (state edge learning-isolation or define a push path) · RT-25 (book reimplementation effort).

### Already escalated — confirm it stays open

RT-24 (gh-aw license) · CR-15 (GitHub Agentic Workflows license).

*Closed 2026-06-14:* RT-18 (two-gate count → two separate approvals) · RT-07 (air-gap claim → dropped, ADR-017 to be written) · CR-08 (graph topology → separate TALOS Neo4j, option b).

---

## 6. Honest close

The design's safety spine is genuinely strong where it is *structural* — live operations have no tool,
so even every BLOCKER above stops short of a live-processor write. That is the architecture working as
intended, and it should be said plainly.

**External validation of the Guardian doctrine:** Stack Overflow for Agents (public beta, June 2026)
independently converged on the same gate pattern: agents propose, a multi-agent quality screen runs
first, then a human must approve before anything is published — and `approved_by` is tied to a
verified human identity via OAuth, never a request field. This is the Guardian doctrine operating at
production scale on a different problem. It confirms the pattern is implementable and that the
human-identity-from-session (not from body) requirement is the right structural enforcer for RT-01.

The danger is concentrated where the spine quietly **degrades from structural to procedural**: a gate
that trusts a string for "human," an isolation boundary (RLS) that is commented out, a de-identification
rule that is a human checklist, an "air-gapped" edge that calls a hosted API, a frozen contract that
doesn't enumerate the tools it governs, and an "exactly-once" guarantee keyed on a value that changes
on the exact event it must survive. None of these is a redesign to fix — each needs a structural
enforcer the design already says (CR-17) it requires but, in these six places, did not supply. Close
those before the first line of code, and the doctrine holds by construction rather than by discipline.

---

*Reviewed against `BLUEPRINT.md` v0.6 (authoritative) and CR-01…CR-26. This is an adversarial pass; it
introduces no architecture and resolves nothing — it names the seams most likely to fail and where each
fix belongs. For the seam map see `00_integration_map.md`; for the resolved conflicts,
`01_conflicts_and_resolutions.md`; for the system synthesis, `02_unified_architecture.md`.*
