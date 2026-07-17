# Research Prompt — Dreaming & Memory Consolidation

> **Historical note:** this prompt predates ADR-039 (which replaced Chroma with pgvector and
> cancelled Neo4j/Redis). Retained as written for the historical record; see README.md / ADR-039
> for current state.

You are doing a deep technical research dive for TALOS, a pre-alpha multi-agent industrial
project-execution platform. TALOS is NOT a coding assistant. It orchestrates agents for
operations work (PLC audits, maintenance programs, project management) behind a hard
human-review gate. Nothing reaches a live system without human approval.

P0–P2 are complete. ADR-023 (rule extraction in Crystallize, P5) is decided. The builder
wants TALOS to implement a **Dreaming** subsystem — a scheduled memory consolidation
pipeline that runs between active sessions, reviews session history, extracts patterns,
prunes stale or contradictory knowledge, and updates the long-term memory layer.

This research session produces one output file. Do not modify any other files.

---

## Context — what TALOS already decided about memory and Crystallize

Read these files silently before fetching any external sources:

- `/mnt/i/talos/BLUEPRINT.md` — four-store memory design (Postgres SoR, Neo4j/Graphiti graph,
  pgvector vector store, Redis working memory)
- `/mnt/i/talos/docs/decisions/ADR-003.md` — memory backend decisions
- `/mnt/i/talos/docs/decisions/ADR-014.md` — consolidation boundaries (autonomous only within
  client scope; cross-scope MERGE forbidden without gate)
- `/mnt/i/talos/docs/decisions/ADR-023.md` — rule extraction in Crystallize (all three rule
  types: factual, procedural, project-context; auto-extract at client scope; gate for shared
  promotion; Graphiti bi-temporal contradiction detection)
- `/mnt/i/talos/docs/upstream/graphiti-notes.md` — Graphiti deep-dive (bi-temporal graph,
  contradiction handling, `add_triplet()`, episode ingestion pipeline)
- `/mnt/i/talos/docs/integration/04_build_sequence.md` — P5 is Crystallize; P4 is memory
  federation (ships the Graphiti + pgvector layer Dreaming will write to)

---

## Sources to fetch and study

Fetch each URL and extract all technical content:

1. `https://www.mindstudio.ai/blog/what-is-claude-dreaming-anthropic-agent-memory`
2. `https://www.mindstudio.ai/blog/what-is-claude-dreaming-anthropic-managed-agents`
3. `https://thenewstack.io/anthropic-managed-agents-dreaming-outcomes/`
4. `https://zenvanriel.com/ai-engineer-blog/claude-code-autodream-memory-consolidation-guide/`
5. `https://kenhuangus.substack.com/p/why-ai-agents-are-starting-to-dream`
6. `https://www.softpagecms.com/2026/05/23/anthropic-claude-dreaming-agent-memory-consolidation/`

Also search the web for:
- "agent memory consolidation implementation 2026 pattern extraction"
- "LangMem Reflexion self-reflection memory agent pattern 2025 2026"
- "Graphiti dreaming memory consolidation episodic"

---

## What to extract from each source

For each source, answer these questions where the source speaks to them:

**Pipeline architecture:**
- What triggers a dream run? (schedule, session-end hook, manual, token threshold?)
- What is the exact sequence of steps?
- What does it read as input? (session logs, tool call traces, existing memory entries?)
- What does it write as output? (new entries, updates, deletions, promotions?)
- How are updates applied — atomically or incrementally?
- Is there a human approval step before memory is committed, or is it autonomous?

**Pattern detection:**
- What signals indicate a pattern worth extracting? (frequency, recency, outcome correlation?)
- What signals indicate a memory entry should be deleted or demoted?
- How many sessions does the system need before patterns emerge?

**Contradiction resolution:**
- How does the system detect a contradiction between a new finding and an existing memory?
- What is the resolution mechanism — automatic invalidation, merge, flag-for-human-review?
- Does it preserve history (bi-temporal model) or overwrite?

**Configuration and control:**
- Can operators scope the consolidation to specific task types or topic areas?
- Can the dream be interrupted mid-run?
- What is the cost model (LLM calls per run)?

**Comparison to Graphiti:**
- Graphiti already handles bi-temporal contradiction resolution via Neo4j. How does
  Anthropic's Dreaming complement or overlap with this?
- Does Dreaming run on top of external stores (Graphiti/Neo4j) or replace them?

**Chase-ai `/dream` comparison:**
- How does chase-ai's `/dream` differ from Anthropic's Dreaming in mechanism?
- What does chase-ai prune that Anthropic doesn't, or vice versa?

---

## What to produce

Write a single file: `/mnt/i/talos/docs/upstream/dreaming-notes.md`

Follow the same format as the existing notes in `docs/upstream/` (e.g., graphiti-notes.md):

```
# Dreaming & Memory Consolidation — Research Notes

## What it is
[one paragraph]

## Anthropic Dreaming — technical details
[pipeline, triggers, steps, I/O, contradiction handling, configuration]

## Chase-ai /dream — technical details
[how it differs]

## Other implementations reviewed
[LangMem, Reflexion, etc. — what the pattern looks like across frameworks]

## Key TALOS findings
[bulleted list — what TALOS should adopt, adapted to TALOS's architecture]
- Each finding starts with the mechanism, then maps it to TALOS's existing layer
  (e.g., "Contradiction surfacing → extend Graphiti's bi-temporal detection to surface
   invalidated verified/safety edges to the gate for human review (ADR-023)")

## What TALOS should NOT take
[patterns that conflict with the Guardian doctrine or ADR-014 consolidation boundaries]

## Open questions for the builder
[anything the research couldn't settle — phrased as specific questions, not vague concerns]

## Build-phase impact
[Which existing ADRs does this affect? Does anything need to land earlier than currently
 planned to support Dreaming in P5?]
```

Write the file. Do not modify any other file.
