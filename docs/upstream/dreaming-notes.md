# Dreaming — Upstream Research Notes

> **Historical note:** this file predates ADR-039 (which replaced Chroma with pgvector and
> cancelled Neo4j/Redis). Its memory-store mapping reflects the architecture as of when it was
> written. Retained as written for the historical record; see README.md / ADR-039 for current state.

_TALOS upstream research file. Covers what external systems call "Dreaming" (Anthropic Managed Agents), "AutoDream" (Claude Code), and analogous consolidation patterns. All findings are mapped to TALOS constraints at the end._

---

## 1. What It Is

Dreaming is the name Anthropic gives to a scheduled, asynchronous, offline memory consolidation process for AI agents. When an agent is not actively handling a task, Dreaming reads accumulated session logs and the existing memory store, produces a reorganized representation — merging duplicates, resolving contradictions, pruning stale entries, surfacing new patterns — and writes the result as a separate output store rather than overwriting the original. [SOURCE: softpage-dreaming, kenhuang-substack, mindstudio-agent-memory]

The lifecycle position matters. Raw episodic capture happens continuously during active sessions: conversation turns, tool calls, outcomes, corrections. Dreaming is a maintenance layer that operates between sessions on that accumulated episodic material, converting it into denser semantic knowledge. This places it after per-session capture and before long-term memory retrieval. The biological analogy, which Anthropic uses deliberately, is REM-sleep consolidation: the brain replays recent episodes, extracts stable patterns, and prunes connections that did not prove useful — all offline, without conscious direction. [SOURCE: softpage-dreaming, mindstudio-agent-memory, search:graphiti-dreaming]

In TALOS terms, Dreaming is distinct from Crystallize (P5). Crystallize is task-triggered and synchronous: it runs at the end of a specific task execution, extracting rules from that task's trace and ingesting them per ADR-023. Dreaming is cross-session and scheduled: it runs periodically across many sessions, looking for patterns that only become visible in aggregate. Crystallize is per-task; Dreaming is per-deployment. The two are complementary, not redundant — Crystallize writes fresh episodic content into the stores; Dreaming maintains the quality of what has accumulated across many Crystallize runs.

A third relevant process exists in Claude Code: AutoDream (`/dream` command, `auto_dream: true` config), which consolidates Claude Code's local memory files on a per-developer schedule. This is not a multi-tenant agent platform feature and differs substantially in scope; it is covered in Section 3.

---

## 2. Anthropic Dreaming Technical Details

> **Note on sourcing**: No primary Anthropic documentation was available. All technical details below derive from secondary reporting in [SOURCE: kenhuang-substack] and [SOURCE: softpage-dreaming], both of which cite Anthropic's Claude Managed Agents documentation and the May 2026 Code with Claude developer conference. Claims are labeled "per secondary sources" where they originate from this reporting, not from primary docs.

### Trigger Mechanism

Dreaming is a scheduled offline consolidation process. Per secondary sources, it is asynchronous and designed to run between active sessions rather than during them. [SOURCE: kenhuang-substack, mindstudio-managed-agents] It does not fire in real time based on individual session events. Claude Code AutoDream (a related but distinct product) triggers "every 24 hours after 5+ accumulated sessions" [SOURCE: zenvanriel-autodream]; whether the same cadence governs Anthropic Managed Agents Dreaming is not confirmed in available sources.

### Pipeline Steps

Per secondary sources [SOURCE: kenhuang-substack, softpage-dreaming], the pipeline is:

1. **Input snapshot**: takes existing memory store plus transcripts of past sessions (documented as up to 100 sessions per run [SOURCE: softpage-dreaming, kenhuang-substack]).
2. **Pattern detection across sessions**: identifies recurring errors, successful strategies, stable preferences, and generalizable patterns that did not surface within any single session.
3. **Memory restructuring**: generates candidate updates — new entries for emergent patterns, updates to entries where newer information supersedes older, deletions for entries that proved consistently unhelpful, promotions for frequently-retrieved entries.
4. **Output**: writes a **separate output memory store**, not overwriting the original. The original is preserved intact (copy-on-write semantics). [SOURCE: kenhuang-substack, softpage-dreaming]

Ken Huang's pseudocode from [SOURCE: kenhuang-substack] distills the minimal form:

```
def dream(memory_store, sessions, policy):
    snapshot = freeze_inputs(memory_store, sessions)
    candidates = extract_memory_candidates(snapshot, policy)
    canonical = resolve_duplicates_and_conflicts(candidates)
    compressed = compress_with_provenance(canonical)
    scored = evaluate_memory_quality(compressed, snapshot, policy)
    if scored.passes_all_gates:
        return CandidateStore("ready_for_review", scored.entries)
    return CandidateStore("quarantined", scored.entries, scored.failures)
```

This is the author's synthesis, not literal Anthropic implementation code, but it accurately reflects the described semantics.

### Job States

Per secondary sources [SOURCE: kenhuang-substack], dream jobs expose states: `pending`, `running`, `completed`, `failed`, `canceled`. Typical run time is "minutes to tens of minutes."

### Inputs

- Existing memory store (the agent's current long-term memory)
- Session transcripts (up to 100 prior sessions per run [SOURCE: kenhuang-substack, softpage-dreaming])
- Optional steering instructions allowing focus on specific memory categories [SOURCE: kenhuang-substack]

Tool call traces, task outcomes, correction events, and retrieval-hit patterns are implied inputs based on the described output types, but their exact structure in Anthropic's API is not documented in available sources.

### Outputs

- Merged duplicate entries
- Contradictory entries resolved in favor of most recent value [SOURCE: softpage-dreaming]
- Stale references pruned (e.g., notes about files that no longer exist) [SOURCE: softpage-dreaming]
- New pattern insights surfaced as fresh entries [SOURCE: softpage-dreaming, mindstudio-agent-memory]
- Output is a separate store; original is unmodified [SOURCE: kenhuang-substack, softpage-dreaming]

### Human Gate vs. Autonomy

The feature shipped as a research preview, not GA, with access gated behind a developer application. [SOURCE: softpage-dreaming] Per [SOURCE: mindstudio-agent-memory]: "Depending on configuration, memory updates can be applied automatically or held for human review. In fully autonomous deployments, the agent commits updates on its own. In human-in-the-loop setups, a summary of proposed changes gets surfaced for approval before they're written to the memory store." The copy-on-write design (separate output store) is the structural mechanism enabling human review before promotion. [SOURCE: kenhuang-substack, softpage-dreaming]

The 6x improvement in task completion cited for Harvey is from [SOURCE: softpage-dreaming], with the caveat from that same source: it reflects a specific class of failures with an unusually clear failure mode; no external benchmark has replicated it.

### What Is Not Documented

- The exact data format of session logs fed into a dream run.
- Specific contradiction-resolution logic beyond "most recent wins" for non-safety entries.
- How steering instructions interact with the consolidation policy.
- Whether the output store attaches automatically to future sessions or requires an explicit developer promotion step.

---

## 3. Chase-AI /dream Details

No chase-ai-specific information was found in any available source. The source material covers Anthropic Claude Managed Agents Dreaming, Claude Code AutoDream, Xiaomi MiMo Code, Letta, Mem0, Zep/Graphiti, Reflexion, and MemGPT. No system named "chase-ai" appears in any source.

The only documented `/dream` command is Claude Code AutoDream [SOURCE: zenvanriel-autodream], which is a distinct product targeting individual developer memory files in `~/.claude/`, not a multi-tenant agent deployment platform. Key properties:

- **Trigger**: "every 24 hours after 5+ accumulated sessions"; also available on demand via `/dream`.
- **Objective**: keep the main memory index under 200 lines.
- **Pipeline phases**:
  1. Orientation — maps existing memory files before modification.
  2. Gather Signal — identifies high-value information prioritized by long-term relevance over recency.
  3. Consolidation — merges duplicates, resolves contradictions, converts relative timestamps to absolute dates (e.g., "yesterday we decided X" → "On 2026-03-15 we decided X").
  4. Prune and Index — moves detailed notes to thematic files; maintains pointers in main index.
- **Scope**: `~/.claude/` memory directories only; does not touch project code.
- **Retention**: backward-looking only; does not predict future queries.
- **Multi-developer**: maintains individual memory stores without requiring team coordination.
- One documented run consolidated 913 sessions of memory in approximately 8–9 minutes. [SOURCE: zenvanriel-autodream]

Differences from Anthropic Managed Agents Dreaming:
- AutoDream is single-developer, not multi-tenant; no board isolation or cross-client concerns.
- The 200-line index constraint is a hard output-size target; Managed Agents dreaming targets quality/signal, not file length.
- AutoDream's "absolute date conversion" (relative → absolute timestamp normalization) is an explicitly documented step; this is not called out in Managed Agents sources.
- AutoDream does not mention a copy-on-write / separate output store; Managed Agents dreaming does.

---

## 4. Other Implementations

### Letta / MemGPT (Sleep-Time Compute)

Letta's "sleep-time compute" shifts some inference from the user-facing critical path to idle time. Sleep-time processes reason over available context before queries arrive, creating superior representations for future responses. [SOURCE: kenhuang-substack] Letta distinguishes asynchronous specialized memory agents from systems that bundle memory work into the responding agent — a separation TALOS should note.

Architecture: three-tier OS-inspired architecture; consolidation requires agent-directed tool calls; tier-transition eviction (moving entries between tiers rather than deleting them). [SOURCE: search:graphiti-dreaming, search:consolidation-2026]

Caveat: offline compute helps most when "future queries are somewhat predictable from existing context." Unrelated subsequent queries make dreamed insights potentially irrelevant. [SOURCE: kenhuang-substack]

Contradiction handling: not specifically documented in available sources for Letta's sleep-time compute.

### Reflexion / Meta-Policy Reflexion

Original Reflexion (Shinn et al.): after a failed task, the agent writes a natural-language post-mortem, stores it in a memory buffer, and prepends it to the prompt on subsequent attempts. Achieved 91% pass@1 on HumanEval coding tasks vs. 80% for GPT-4 without reflection. [SOURCE: search:langmem-reflexion]

Meta-Policy Reflexion (MPR, arXiv:2509.03990): consolidates agent-generated reflections into a structured Meta-Policy Memory (MPM) reusable across tasks, not just task-specific traces. Applies soft memory-guided decoding (relevant past reflections bias next-action selection) and hard rule admissibility checks (filters unsafe or invalid actions before execution). No parameter updates required. [SOURCE: search:langmem-reflexion]

Key distinction for TALOS: Reflexion operates within a single task session (verbal reinforcement loop); MPR's MPM is cross-task — closer to Dreaming's cross-session abstraction. The hard rule admissibility check in MPR is structurally analogous to TALOS's deterministic critics gate.

Contradiction handling: MPR marks old reflections as superseded; does not address bi-temporal validity windows.

Multi-tenancy: not addressed in available sources.

### Zep / Graphiti (Temporal Knowledge Graph)

Graphiti uses explicit temporal validity windows (`valid_at` / `invalid_at` / `expired_at`) for every fact. Contradiction resolution: new fact with `valid_at > old_fact.valid_at` for the same relationship sets `invalid_at` on the old edge; history is preserved, not deleted. This is the strongest temporal modeling of any compared system. [SOURCE: search:graphiti-dreaming]

Graphiti is called out specifically in the Hindsight comparison [SOURCE: search:graphiti-dreaming] as best-in-class for temporal validity: it can answer "What was the customer's address before they moved?" — a query no system with replace-on-update semantics can answer.

Graphiti does not appear to have a scheduled offline consolidation process analogous to Dreaming. Its consolidation is per-`add()` operation, not batched asynchronously. Whether Graphiti's community detection (graph-level clustering) can substitute for session-level dreaming is not addressed in available sources.

Multi-tenancy: `group_id` partitioning per client (per ADR-003's documented Graphiti usage).

### Mem0

Hybrid vector + graph with adaptive deduplication. Importance and merge driven by LLM (ADD/UPDATE/DELETE operations). No decay. Explicit DELETE eviction. [SOURCE: search:graphiti-dreaming] Scores 94.4 on LongMemEval at ~6,787 tokens/query. [SOURCE: search:consolidation-2026]

Consolidation pattern: single-pass ADD-only extraction (agent-generated facts equal weight to user-stated facts); multi-signal retrieval (semantic similarity + keyword + entity matching, normalized and fused). [SOURCE: search:consolidation-2026]

No scheduled offline consolidation step analogous to Dreaming described in available sources.

### Hindsight (from Vectorize)

Argues explicitly against designing agent forgetting to mirror human memory loss: "The goal is not to mimic biological limits; it is to get consolidation right so what is in memory is always the right thing to retrieve." Recommends eviction only for compliance (GDPR, PII redaction), not performance optimization. [SOURCE: search:graphiti-dreaming]

Four consolidation levers: importance, merge, decay, eviction. Recommends fact-level storage at write time, entity resolution at writes not queries, decay only for temporal claims, eviction only for compliance. [SOURCE: search:graphiti-dreaming]

### LangChain / LangMem

LangChain supports hot-path (explicit tool calls) and background-path memory updates; consolidation requires developer implementation via ConversationSummaryMemory. No merge, no decay, window eviction. [SOURCE: search:consolidation-2026, search:graphiti-dreaming] LangMem as a named library did not surface as a distinct paper in the search results; it appears as one of the retrieval-augmented store tools in the 2026 survey (arXiv:2603.07670). [SOURCE: search:langmem-reflexion]

### Mem0 2026 State-of-Agent-Memory Report

Benchmarks (April 2026): LoCoMo 92.5, LongMemEval 94.4, BEAM@1M 64.1, BEAM@10M 48.6. Token cost ~6,900 tokens/query vs. ~26,000 for full-context. Largest gains from 2025→2026: temporal reasoning +29.6 pts, multi-hop reasoning +23.1 pts. Open problems: temporal abstraction at scale (BEAM drops ~25% from 1M to 10M tokens), cross-session identity resolution, memory staleness. [SOURCE: search:consolidation-2026]

---

## 5. Key TALOS Findings

| Finding | TALOS Layer | Verdict | Reason |
|---------|-------------|---------|--------|
| Scheduled offline/idle trigger | P8 Gateway scheduler | ADOPT | Fits Gateway's sandboxed cron role (propose-never-approve); aligns with ADR-015 build sequence placing Gateway at P8 |
| Copy-on-write / separate output store | P5 Crystallize + gate | ADOPT | Directly implements ADR-014 "route to gate as proposals, never auto-write"; Guardian doctrine satisfied by design |
| Reviewable artifacts with provenance | P5 gate integration | ADOPT | ADR-023 requires provenance on all ingestion; Dreaming's separate output store is the mechanism |
| Contradiction resolution "most recent wins" for routine edges | Graphiti bi-temporal (P4/P5) | ADOPT (routine) | Matches Graphiti's invalid_at semantics for non-verified, non-safety edges per ADR-023 |
| Contradiction resolution "most recent wins" for verified/safety edges | Gate (P5) | REJECT (auto) — ADAPT (gate) | ADR-023 explicitly forbids auto-invalidation of verified/safety edges; must surface as proposal to human reviewer before invalid_at is set |
| N-session detection threshold before triggering | P8 scheduler + P5 sensitivity classifier | ADAPT | Must fail-closed; specific threshold TBD per CR-25 sensitivity classifier tuning |
| Cross-agent shared memory synthesis (multi-agent) | ADR-014 leak boundary | REJECT (cross-scope); ADOPT (intra-scope) | Cross-scope merge forbidden per ADR-014 absolute boundary; within one client scope, intra-scope Dreaming is permitted |
| Eviction for compliance only, not performance (Hindsight recommendation) | ADR-023 history-preserved-not-deleted | ADOPT | ADR-023 explicitly states "history preserved, NOT deleted" for routine edges; Hindsight's rationale aligns with TALOS's bi-temporal model |
| Sleep-time compute / idle-time inference budget (Letta) | P8 Gateway + CR-25 | ADAPT | Idle-time compute useful for consolidation but cost must be bounded; CR-25 (Graphiti ingestion cost at scale) governs; do not enable always-automatic without measurement |
| Temporal claim decay (decay only for temporal claims, not all facts) | P5 Crystallize / Graphiti | ADOPT | Graphiti's validity windows implement exactly this; absolute-date normalization (AutoDream) should be adopted as part of Crystallize episodic capture |
| Hard rule admissibility checks (MPR) | P2 critics gate | ADOPT | Meta-Policy Reflexion's hard admissibility check is structurally identical to TALOS deterministic critics; ADR-011 five-outcome gate governs |
| Autonomy knob: developer-configurable human gate vs. auto-commit | P2/P5 gate configuration | ADAPT | TALOS has no "fully autonomous" promotion path; the gate is structural per ADR-011. Auto-commit is only permitted below sensitivity threshold within one scope (ADR-014). The autonomy knob exists but is constrained. |
| Dedup key at ingest time | P5 rule_ingestion_log | ADOPT | ADR-023 specifies hash(board_id + task_id + crystallize_run_id + rule_content) dedup check before add_episode(); same principle applies to Dreaming output |
| Session-level episode as Dreaming input | P5 / P4 schema | ADOPT | Graphiti group_id partitioning per client; session checkpoints as dream inputs align with TALOS's per-board isolation (ADR-010 session keys) |
| Token cost ~6,900 tokens/query for memory retrieval | P4 memory layer | ADOPT as target | 26,000 for full-context vs. 6,900 with proper memory is the cost case for investing in P4 infrastructure |

### Prose Expansion of Key Verdicts

**Copy-on-write / separate output store (ADOPT)**
This is the most directly adoptable finding. Anthropic's design — Dreaming produces a separate output store, never overwrites the original [SOURCE: kenhuang-substack, softpage-dreaming] — is structurally identical to what ADR-014 requires: "Verified and safety nodes route to gate as proposals, NEVER auto-write." TALOS Dreaming should produce a candidate store that enters the gate as a proposal object. The gate evaluates it; a human approves promotion; only then does the candidate store's content merge into the active memory state. This is the Guardian doctrine applied to the Dreaming pipeline.

**Contradiction resolution split by edge type (ADOPT routine / ADAPT verified-safety)**
External sources uniformly describe contradiction resolution as "most recent wins" [SOURCE: softpage-dreaming, search:graphiti-dreaming]. For routine (non-verified, non-safety) Graphiti edges, this matches TALOS's ADR-023 model: old edge gets `invalid_at` set, new edge created, history preserved. However, for verified and safety edges, ADR-023 is explicit: surface as proposal to human reviewer BEFORE `invalid_at` is set. The Dreaming pipeline must classify contradictions by edge type before resolving them — any contradiction touching a verified or safety edge must be quarantined into a gate proposal rather than auto-resolved. This is the single most important behavioral difference between external Dreaming implementations and TALOS's required behavior.

**Eviction for compliance only (ADOPT)**
Hindsight's explicit rejection of biological forgetting mimicry [SOURCE: search:graphiti-dreaming] aligns with ADR-023's "history preserved, NOT deleted." TALOS does not delete memory entries for performance. Graphiti's `invalid_at` semantics preserve history; physical deletion is permitted only for GDPR/PII compliance operations. The Dreaming pipeline must not implement decay-based eviction.

**Cross-scope synthesis (REJECT cross-scope / ADOPT intra-scope)**
MindStudio [SOURCE: mindstudio-agent-memory] and MiMo Code [SOURCE: kenhuang-substack] describe multi-agent shared memory consolidation as a key benefit of Dreaming — learnings from multiple subagents synthesized into a unified shared memory. This is the exact pattern ADR-014 forbids at the cross-scope boundary: "Cross-scope MERGE ([client]→[shared] or cross-client) is FORBIDDEN — absolute leak boundary." TALOS Dreaming may synthesize across sessions within one client scope. It may not promote to shared scope without a human-approved gate proposal.

---

## 6. What TALOS Should NOT Take

### Autonomous cross-scope memory merge

**What external systems do**: MindStudio describes multi-agent Dreaming that synthesizes learnings across the agent network into shared memory accessible to all agents. [SOURCE: mindstudio-agent-memory] MiMo Code maintains a "global memory" that dreams update automatically across all sessions. [SOURCE: kenhuang-substack] Anthropic's "fully autonomous" mode commits updates directly to the shared store without human review. [SOURCE: mindstudio-agent-memory]

**Which TALOS rule it violates**: ADR-014 consolidation boundaries — "Cross-scope MERGE ([client]→[shared] or cross-client) is FORBIDDEN — absolute leak boundary."

**What TALOS does instead**: Cross-scope promotion follows the ADR-023 cross-client path: flag as promotion candidate → generate gate proposal → human with admin role on TARGET scope approves → ingest into target scope. No autonomous path exists for cross-scope merge regardless of confidence scores.

---

### Auto-invalidation of verified/safety edges without human review

**What external systems do**: All documented Dreaming implementations resolve contradictions automatically — "most recent value wins" [SOURCE: softpage-dreaming], `invalid_at` set programmatically by the dream worker [SOURCE: search:graphiti-dreaming]. No external system in available sources distinguishes between edge types when applying this resolution.

**Which TALOS rule it violates**: ADR-023 — "Contradiction on verified/safety edges: SURFACE AS PROPOSAL to human reviewer BEFORE invalid_at is set. Human approves or rejects."

**What TALOS does instead**: Dreaming pipeline classifies every detected contradiction by edge type. Routine edge contradictions are auto-resolved via Graphiti bi-temporal semantics. Verified or safety edge contradictions are quarantined — surfaced as gate proposals — and do not modify the knowledge graph until a human reviewer approves or rejects the update.

---

### Dreaming that writes to live systems or production state

**What external systems do**: MindStudio's "fully autonomous" mode writes memory updates directly to the active store. [SOURCE: mindstudio-agent-memory] Anthropic's AutoDream merges output into the active `~/.claude/` memory files (no separate store mentioned). [SOURCE: zenvanriel-autodream]

**Which TALOS rule it violates**: Guardian doctrine — "nothing is written to a live system without a human's approval." Also ADR-014 — "raw episodic capture flows freely under one scope; the gate guards promotion."

**What TALOS does instead**: Dreaming produces a candidate store object. It never writes directly to the active Graphiti graph, the rules table, or the pgvector index. The candidate store enters the gate as a proposal. Gate critics evaluate it. Human approves promotion. Promotion then triggers the standard ADR-023 ingestion pipeline (dedup check → add_episode() / add_triplet() → rule_ingestion_log).

---

### Decay-based eviction for performance

**What some systems do**: Various agent memory frameworks apply Ebbinghaus-style exponential decay to reduce the importance score of older memories, eventually evicting them from storage. [SOURCE: search:consolidation-2026]

**Which TALOS rule it violates**: ADR-023 — "History preserved, NOT deleted" for routine edges. Graphiti bi-temporal semantics preserve all historical edges via `invalid_at`; they do not delete.

**What TALOS does instead**: Facts are invalidated (old edge gets `invalid_at` set) when superseded by newer facts. Physical deletion is reserved for compliance operations (GDPR/PII). The Hindsight rationale is adopted: "The goal is not to mimic biological limits; it is to get consolidation right." [SOURCE: search:graphiti-dreaming]

---

## 7. Open Questions for the Builder

1. **Where does the Dreaming scheduler live — P5 Crystallize sub-phase, a new P5a, or P8 Gateway?**
   Affects: Gateway (P8), Crystallize (P5). This is the scheduling ownership question. Crystallize is task-triggered and synchronous; Dreaming is cross-session and scheduled. Functionally, the scheduler (cron trigger, idle detection, session-count threshold) belongs in P8 Gateway, which is defined as "sandboxed cron/proactive loops; may notify/propose, never approve." The consolidation logic itself (the dream worker) may be a P5 capability that Gateway calls. Does this split the implementation across two phases? **Blocks P5 design; must be resolved before P5 starts.**

2. **Does TALOS Dreaming run on session-level episodes, task-level Crystallize summaries, or both?**
   Affects: P4 schema stubs, P5 Crystallize pipeline. Crystallize (P5) already extracts rules from individual task runs. Dreaming operates across multiple runs. The input to Dreaming is likely the Crystallize output accumulated across N sessions, not raw task transcripts — but this is not decided. If Dreaming ingests Crystallize outputs, the schema needs a `crystallize_run_log` that Dreaming can query. If Dreaming ingests raw episodic events, it needs access to the task_events table scoped by board. **Blocks P4 schema design; must be resolved before P4 starts.**

3. **What is the sensitivity threshold for routing a dreamed insight to the gate vs. auto-ingest?**
   Affects: P5 sensitivity classifier. ADR-014 permits autonomous consolidation within one client scope below the sensitivity threshold. The threshold value is explicitly listed as an open item: "Sensitivity classifier tuning — similarity floor values not yet set." Claude Code AutoDream uses a 200-line index constraint as a proxy; Anthropic Managed Agents uses configurable steering instructions; Mem0's 2026 report uses "memory depth config" per-project. TALOS needs a classifier that distinguishes routine-intra-scope from verified/safety/cross-scope before any consolidation runs. **Blocks P5; must be decided (or provisionally stubbed with fail-closed defaults) before P5 starts.**

4. **How does TALOS Dreaming interact with Graphiti's native community detection — redundant or complementary?**
   Affects: P4 Graphiti integration, P5 Dreaming pipeline. Graphiti performs entity linking and relationship extraction on `add()`. Whether it includes graph-level community detection (clustering related entities into themes) is not confirmed in available sources. If it does, Dreaming's pattern-detection step may overlap with what Graphiti already computes. If they are complementary — Graphiti finds entity-level structure, Dreaming finds session-level behavioral patterns — both are needed. **Should be confirmed against Graphiti documentation before P4 implementation; does not block P4 start but affects P4 design.**

5. **What is the token cost model: per-session vs. batched, and what budget triggers a run?**
   Affects: P8 scheduler, CR-25. Graphiti ingestion is 4–15 LLM calls per ingest (4k to 40k tokens). [SOURCE: kenhuang-substack, from ADR-003 notes] A dream run over 100 sessions with N patterns extracted will multiply that cost. CR-25 is open: "Graphiti ingestion cost at scale — measure on real traces before enabling always-automatic." The scheduler must have a cost budget (token budget per run, maximum run frequency) that prevents Dreaming from consuming resources meant for active tasks. Batching (accumulate N sessions then run once) is cheaper than per-session; the threshold N is unknown. **Does not block P4 or P5 start but must be resolved before P8 Gateway is implemented.**

6. **How does Dreaming handle deduplication against existing Crystallize outputs?**
   Affects: P5 rule_ingestion_log, P4 schema. ADR-023 specifies a dedup key of `hash(board_id + task_id + crystallize_run_id + rule_content)` for Crystallize. Dreaming produces cross-session patterns that may or may not match any single Crystallize run's content. A new dedup key scheme is needed for Dreaming outputs — likely `hash(board_id + dream_run_id + rule_content)`. The `rule_ingestion_log` schema stub (P4) should accommodate both key types. **Blocks P4 schema stub; must be decided before P4 starts.**

7. **Does the candidate store from Dreaming require a full five-outcome gate evaluation, or a lighter approval path?**
   Affects: P2 critics gate, P5. The five-outcome gate (ADR-011: Approve / Reject-with-reason / Waive-with-justification / Edit-inline / Escalate) was designed for task-level execution proposals. A Dreaming candidate store is a batch of memory reorganization proposals — potentially dozens of individual memory updates in one review object. Whether a single gate evaluation covers the whole batch, or each proposed memory entry gets its own gate row in `task_gate_results`, is not decided. **Does not block P4; should be decided before P5 gate integration.**

---

## 8. Build-Phase Impact

**P4 impact**: P4 wires the four memory stores (Postgres SoR, Neo4j/Graphiti, pgvector/Chroma, Redis). Dreaming research implies the following schema stubs must be added in P4 even though Dreaming runs in P8:
- `dream_runs` table: `(dream_run_id, board_id, trigger_type, session_count, status, started_at, completed_at, token_cost)` — records each Dreaming job.
- `dream_candidate_stores` table: `(candidate_id, dream_run_id, board_id, entry_type, content_hash, proposed_action [ADD|UPDATE|INVALIDATE], target_edge_id, edge_scope, edge_type [routine|verified|safety], gate_status, created_at)` — the candidate store produced by each dream run.
- Extend `rule_ingestion_log` to accept `dream_run_id` as an alternative to `crystallize_run_id` in the dedup key.
- Confirm whether `task_events` table is accessible to the Dreaming worker scoped by `board_id` — this determines whether Dreaming reads raw episodic events or only Crystallize summaries.

**P5 impact**: P5 implements Crystallize (rule extraction, ingestion pipeline, surfacing layer for verified/safety contradictions). Dreaming research confirms that P5's surfacing layer must classify contradictions by edge type before resolving them — this is not an optional enhancement but a correctness requirement. The sensitivity classifier (ADR-014, currently untuned) must be implemented in P5 as a fail-closed stub even if threshold values are provisional. P5's `rule_ingestion_log` dedup logic must be generalized to accept Dreaming-origin dedup keys.

**P5a (Dreaming sub-phase)?**: TBD. The consolidation logic (the dream worker: pattern detection across sessions, candidate generation, provenance tracking) is most naturally a P5a sub-phase, building directly on P5's Crystallize pipeline infrastructure. The scheduler (cron trigger) belongs in P8. This suggests a P5a that implements the dream worker as a callable function with no scheduler — testable in isolation — and P8 that adds the scheduler and idle-time trigger. Whether this is called "P5a" in the roadmap or absorbed into P5 as a late deliverable is a roadmap decision, not an implementation constraint.

**P8 impact**: P8 (Gateway) implements sandboxed cron/proactive loops. Dreaming research implies P8 must include:
- A configurable session-count trigger (fire after N sessions accumulated since last dream run).
- A time-based trigger (minimum interval between runs, e.g., 24 hours) to prevent runaway cost.
- A token budget check before launching a dream run (per CR-25).
- Output: the dream worker produces a candidate store; Gateway's role ends there. It does not approve. It surfaces the candidate store to the gate as a proposal.

**ADRs to write before implementation**:
- **ADR-024**: Dreaming scheduler ownership — Gateway (P8) as trigger, P5 dream worker as callable; defines the interface between them.
- **ADR-025**: Dreaming input scope — session-level episodes vs. Crystallize summaries vs. both; defines what the dream worker reads.
- **ADR-026**: Dreaming candidate store schema and gate integration — defines how candidate stores enter the five-outcome gate and how batch proposals are structured in `task_gate_results`.
- **ADR-027** (or update to ADR-023): Sensitivity classifier values — sets provisional similarity floor and edge-type classification rules; must be fail-closed at P5 stub time.
