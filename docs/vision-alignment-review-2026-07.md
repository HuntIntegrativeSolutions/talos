# TALOS Vision-Alignment Review — July 2026

**Purpose:** Audit the build (P0–P4 complete, P5 in flight, P7a shipped) against the owner's
vision: *a harness that blends the best of Hermes Agent, OpenClaw, Agent Zero, Space Agent,
and other top harnesses into one system, focused on industrial automation, with NEXUS leading
the way and the Space Agent modern feel.* Inputs: full repo/docs sweep, and fresh web research
on upstream movement since the June 10 design freeze.
**Date:** 2026-07-06 · **Author:** vision review session (Claude + owner)

---

## 1. Verdict

**The vision and the plan are aligned.** Every load-bearing element of the vision has a home
in the architecture, an ADR, and (for most) shipped code. The gaps are not directional — they
are (a) a set of good ideas that fell through the cracks between the upstream studies and the
roadmap, (b) documentation hygiene that has fallen behind the build rate, and (c) four upstream
developments since the freeze that warrant action. All are catalogued below with dispositions.

**Where each upstream's "best of" stands:**

| Upstream | Its best idea | TALOS status |
|---|---|---|
| Hermes | Kanban board + claim/heartbeat/reclaim dispatcher | **Shipped** (P0–P3, hardened in P3.5). Ralph/goal-mode judge loop → gate-bound evaluator: designed, not yet built (Strategy Ladder). |
| OpenClaw | Gateway-in-front + 7-layer tool policy + Docker sandbox | Sandbox **shipped** (P3c); layered policy in ADR-009 with runtime layer partial (ADR-033 gap); gateway loops = P8. |
| Agent Zero | Memory consolidation w/ gates + lifecycle hooks | Consolidation boundaries **shipped in doctrine** (ADR-014, enforced in P4b/P5); hooks shipped as `HookRegistry` — thinner than the `@extensible` ambition (see §4). |
| Space Agent | Spaces/widgets canvas, time-travel, the modern feel | **Deliberately v1.x (P7b).** v1 has the minimal gate UI (P7a, shipped). The "modern feel" is the single largest *not-yet-built* piece of the vision — by plan, not by drift. |
| LangGraph | Checkpointed BSP engine + interrupt gating | **Shipped end-to-end** (spine, PostgresSaver, 5-way gate, Send fan-out + reducers). |
| Aider | PageRank context map | Designed (CR-10), **not built**, deliberately unnumbered ADR. Post-v1 with Neo4j. |
| Graphiti | Bi-temporal memory w/ contradiction handling | Post-v1 with Neo4j; P5 ships a Postgres/Chroma adaptation of its boundaries (ADR-023 amendment). |
| Claude Agent SDK | query()/MCP/hooks complement | **Shipped** (ADR-029/031/038); PreToolUse hooks still unused (ties to ADR-033 gap). |

**Industrial focus with NEXUS leading:** intact and strengthening — v1 charter (full PLC
documentation), RT-14's 90-tool dispositioned manifest, live HTTP wiring (ADR-038), and the
read cache (ADR-035) all put NEXUS at the center. New CISA/Five Eyes agentic-AI guidance for
critical infrastructure (Apr 2026) maps almost 1:1 onto the Guardian doctrine — see §3.

---

## 2. Vision statement (recorded)

TALOS is **one** harness — not a federation of tools — that takes:
- from **Hermes**: the task board as source of truth and the disciplined dispatcher;
- from **OpenClaw**: the gateway pattern, layered tool policy, sandbox defaults, and the
  lesson of its skill-security crisis (gate everything a model can load);
- from **Agent Zero**: hierarchical memory with consolidation, and hooks on every lifecycle step;
- from **Space Agent**: the spaces/widgets cockpit and its modern, time-travel-native feel;
- from the wider field (LangGraph, Graphiti, Aider, Agent SDK, Omnigent, gh-aw): checkpointed
  execution, bi-temporal memory, ranked context, and deterministic enforcement seams —

all subordinated to the **Guardian doctrine** and aimed at **industrial automation**, with
**NEXUS** as the flagship capability behind the MCP boundary. Business-ops capabilities are
explicitly post-v1 (they extend the platform later; they do not shape it now).

---

## 3. Upstream movement since the June 10 freeze (web research, 2026-07-06)

**Validations** (no action; recorded for confidence):
- LangGraph HITL middleware is now first-class → validates gate-on-checkpoint (ADR-011/019).
- Hermes v0.18 shipped "completion contracts" (verify-against-evidence) → converges on the
  verifier-critic design (ADR-021). Their `/learn` skill distillation parallels Crystallize.
- Microsoft Agent Governance Toolkit (Apr 2026) — deterministic policy eval, require-approval
  outcomes, MCP security gateway → the industry arriving where ADR-009/ADR-011 already are.
- CISA + Five Eyes "Careful Adoption of Agentic AI Services" (critical infrastructure): its five
  risk categories (privilege escalation, config failure, misalignment, brittleness,
  accountability) map onto MCP-boundary / manifest gating / gate / budget caps / task_events
  respectively. **Action: cite this mapping in ARCHITECTURE.md — it is a sales asset.**

**Challenges → tracked actions:**
| # | Finding | Action | When |
|---|---|---|---|
| U1 | **OpenClaw shipped skill signing** (post-crisis: scanning, author verification, required code-signing, trust-envelope verify). TALOS manifests are content-hashed but unsigned. | New ADR: capability-manifest signing/provenance (sign the pinned manifest; verify at attach + worker startup). Cheap now, painful later. | Before any third-party capability pack; pair with ADR-032/033 hardening pass. |
| U2 | **MCP spec 2.0 RC lands 2026-07-28** — stateless core (session-ID servers must migrate), OAuth hardening, Tasks/Extensions. NEXUS edge is Streamable HTTP with session IDs. | Watch the final spec; plan a NEXUS + `nexus_client.py` conformance pass. Not urgent (1.x won't vanish), but budget it. | Post-P5 checkpoint. |
| U3 | **Hermes dispatcher re-spawn guards** (no re-spawn after quota errors / recent success window / PR-linked comment — "worker storm" suppression). TALOS reclaim (ADR-020/037) has no equivalent failure-class predicates. | Add re-dispatch suppression predicates to the reclaim path (e.g. no re-dispatch after N consecutive model-failure escalations on the same task). | P6-window hardening item. |
| U4 | **Agent SDK subscription credit metering** (Jun 15) — SDK usage draws from separate monthly credits on subscriptions. | Cost-modeling note in ADR-031: worker deployments should run API-key/OAuth with awareness of credit pools; revisit before multi-seat pilots. | Note now; revisit at v1 pilot. |

---

## 4. Fell-through-the-cracks ideas — re-evaluated now

These were named in upstream studies but landed on no roadmap phase. Re-evaluated against the
current build state (several fit *now* that didn't fit before):

| Idea (source) | Re-evaluation | Disposition |
|---|---|---|
| **`talos audit` command** (OpenClaw) — manifest drift, orphaned session keys, overdue gate decisions, unsigned artifacts | Fits **now** — every object it audits exists post-P4 (pinned manifest, session keys, gate SLAs from P7a, rules table). Natural companion to the ADR-032/033 hardening pass and U1 signing. | **Adopt → hardening pass** (with U1). |
| **DOX `AGENTS.md` tree + `generate_talos_dox_tree`** (DOX 0F) | Fits now; cheap; directly improves every future agent session on this repo. One-way render from live docs, read-only, CI-checked (CR-17 invariant already stated). | **Adopt → next docs pass.** |
| **`@extensible`-on-every-lifecycle-step ambition** (Agent Zero) | Current `HookRegistry` covers approve/milestone events and is honest fire-and-forget. Full decorator-injection on every step is an abstraction tax with no current consumer. | **Downgrade deliberately:** extend `HookRegistry` events as consumers appear (P8 gateway will want claim/dispatch/escalate). Record so the ambition stops haunting reviews. |
| **PII/sensitivity scan on NEXUS outputs** (Omnigent) — before event-log/crystallize persistence | Fits **now** — P5 crystallize is the exact seam (rules persist + embed). RT-06's identifier scanner is the same shape; generalize it into a pre-persistence hygiene critic at client scope. | **Adopt → P5 follow-up** (small task once P5 lands). |
| **gh-aw `plan_schemas/`** — JSON-Schema validation of plans pre-gate | Fits when plan artifacts become structured (Strategy Ladder plan step, P6+). Deterministic, cheap, on-doctrine. | **Adopt → Strategy Ladder implementation.** |
| **Egress-proxy credential brokering** (Omnigent) | Real value only when workers call third-party services beyond LLM+NEXUS. Not yet. | Defer post-v1; parking lot. |
| **Dreaming batch-gate granularity** (one gate row vs many per candidate batch) | Was "decide before P5 gate integration" — P5 is *now*. v1 P5 creates per-conflict review tasks (one row each), which answers it de facto for v1; formal batch UX belongs to P8 Dreaming loops. | **Record decision note; revisit at P8.** |
| **Mothership↔edge sync contract** (RT-30) | v1 is single-workstation; no edge sync exists to specify. But it is the *only* red-team gap with no artifact at all. | Parking lot with an explicit trigger: "first second-site deployment." |
| **License/dependency-policy ADR** (CR-14/CR-15/RT-23) | Overdue — the rule set already exists in practice (MIT-compatible only; GPL = clean-room reimplement; check Neo4j/GDS before P4-post-v1). Write it down. | **Adopt → next docs pass.** |
| **DB snapshot/rollback ADR for polyglot stores** (RT-28) | Postgres + Chroma both hold state now; ADR should at least pin "Postgres is authoritative; Chroma is rebuildable from it" (which the P4a/P5 design already implies). | **Adopt → next docs pass** (short ADR). |

**Parking Lot items (BLUEPRINT §336+) — unchanged status, still correctly parked:** planner-
autonomy threshold (needs the ML complexity-estimator prototype), status-report authorship,
two-step live-op confirmation UX, vault pull cadence, Stack Overflow for Agents (weak PLC fit),
Mozilla cq (thick-edge, P6+). None are blocked; none block v1.

---

## 5. Documentation health (the plan is good; the *record* is behind)

| Issue | Fix | Effort |
|---|---|---|
| `docs/decisions/README.md` indexes only ADR-001–017; **40 ADR files exist** (through 038) | Regenerate the index table | trivial |
| Duplicate numbers: two ADR-010s, two ADR-011s (`-clarification-*` companions) | Renumber clarifications as 010a/011a or fold into parents; fix cross-refs | small |
| Missing ADRs demanded by accepted decisions: license policy, snapshot/rollback, manifest signing (U1) | Write the three short ADRs (039–041) | small |
| Stale contexts in accepted ADRs: ADR-035 said stdio (amended ✓); sweep found integration docs still calling contracts "unwritten" (they exist since Jun 17) | One-pass sweep of `docs/integration/*` stale claims → pointer notes, not rewrites | small |
| Red-team ledger: ~14 RT items open with no single current-status view (some *are* closed in code but not marked) | Add `docs/integration/rt-status.md` — one line per RT item, status + evidence link; update as part of each phase's docs commit | small, high leverage |
| ROADMAP.md front half still reads as the June 10 research plan; current state lives in the bottom third | Move "Current status and next phase sequence" to the top; archive the research-phase sections below a fold | trivial |
| CISA/Five Eyes mapping absent from public-facing docs | Add §3's doctrine↔guidance mapping to ARCHITECTURE.md | small |

---

## 6. Recommended sequence (fits between/after P5)

1. **Docs-hygiene pass** (one Sonnet session): ADR index regen, 010/011 renumber, three short
   ADRs (license policy, snapshot/rollback, manifest signing), rt-status ledger, ROADMAP
   restructure, ARCHITECTURE CISA mapping, integration-doc stale-claim sweep, DOX AGENTS.md
   tree + generator. All mechanical; no design decisions.
2. **P5 follow-up:** PII/hygiene critic at the crystallize persistence seam (generalize RT-06).
3. **Hardening pass** (pre-any-external-user, can precede P6): ADR-032 DB pinning, ADR-033
   PreToolUse hook + proxy (also fixes the Anthropic-path cache asymmetry), manifest signing
   implementation (U1), `talos audit` command, Hermes-style re-dispatch suppression (U3).
4. **MCP 2.0 conformance check** after the spec finalizes Jul 28 (U2).
5. **P6-Sim scoping decision** (owner): the one v1 phase whose scope is still marked TBD.
6. **P7b cockpit** (v1.x) — where the "Space Agent modern feel" arrives; until then the vision
   is deliberately running on the P7a minimal UI.

---

*Sources: repo sweep (BLUEPRINT, ROADMAP, docs/upstream/×14, docs/integration/×5, ADR-001–038,
code cross-checks) and July 2026 web research (Hermes v0.15–0.18 releases; OpenClaw 2026.6.x +
ClawHub signing; Agent Zero v2.0/2.1; Space Agent v0.66; LangGraph 1.x HITL; Graphiti v0.29.x;
Microsoft Agent Governance Toolkit; MCP 2026-07-28 RC; CISA/ASD joint guidance; NIST CAISI;
Claude Agent SDK sandboxing/credits). URLs in the research transcript; primary ones inline above.*
