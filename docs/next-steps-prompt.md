# TALOS — Next Steps Prompt

> **Historical note:** this prompt predates ADR-039 (which replaced Chroma with pgvector and
> cancelled Neo4j/Redis). Retained as written for the historical record; see README.md / ADR-039
> for current state.

Paste this into a new Claude Code session. Working directory: `/mnt/i/talos/`

---

You are continuing work on **TALOS**, a multi-agent project-execution platform for industrial and
automation work. The design phase is complete. Six architecture decisions were closed on 2026-06-14.
Five open items remain — your job is to close them in the order listed below.

**Do not start by summarizing what TALOS is.** Read the open items, read the relevant source files,
then act.

---

## What was just decided (context only — do not re-open)

| Item | Decision |
|------|----------|
| RT-07 Air-gap claim | Dropped. Model inference egresses to hosted endpoints. Live-processor write isolation is structural and still holds. |
| CR-08 Neo4j topology | Separate TALOS Neo4j instance. NEXUS read-through over MCP only. MCP boundary is load-bearing. |
| RT-03 Escalation | Solo waiver with mandatory audit note. No cool-off, no co-signer. Every waiver is permanent and visible in the audit log. |
| RT-18 Gate count | Two separate approvals: plan-gate then deliverable-gate. Distinct UI moments, distinct audit records. |
| CR-16 PLC emulation | Dual-track: NEXUS (MCP program structure) + pylogix (live emulator reads / active testing) + Logix Echo SDK (UDT/download). Full docs + skills + hooks package for Phase 6. |
| CR-20 Widget sandbox | Defer allowlist to P7 prototype. Four-type starting point stands; not frozen yet. |

---

## Open items — work through these in order

### 1. RT-09 · Enable RLS DDL in the schema `[implementation]`

**File:** `engine/schema.sql`

The schema comments mention Row-Level Security (`board_id` + Postgres RLS) but the DDL never
actually enables it. No `ALTER TABLE … ENABLE ROW LEVEL SECURITY` statements exist, and no
policies are defined.

**Done when:**
- Every table that carries `board_id` has `ALTER TABLE <name> ENABLE ROW LEVEL SECURITY;`
- A `CREATE POLICY` for each such table allows `SELECT | INSERT | UPDATE | DELETE` only where
  `board_id = current_setting('app.board_id')::uuid`
- A superuser bypass row (`USING (current_user = 'talos_admin')`) is included so migrations can run
- The additions land in `engine/schema.sql` (or `engine/schema-additions.sql` if that file is
  already the convention for incremental additions — check what exists)
- A brief comment above each policy block explains *why* (client isolation, not just what)

Read `engine/schema.sql` fully before writing anything. Check `engine/schema-additions.sql` to
see if there is already a convention for incremental DDL. Match the existing style.

---

### 2. RT-14 · Manifest validator for all 85 NEXUS tools `[implementation]`

**Files:** `docs/contracts/capability-manifest.md`, `docs/contracts/nexus-federation.md`

The capability manifest contract is defined but there is no validator. When NEXUS publishes a
manifest, TALOS needs to reject it at attach-time if it is malformed — before any tool is ever
granted. The validator is pure and deterministic: no LLM, no network.

**Done when:**
- A Python module exists (propose a path, confirm with the user before writing) that accepts a
  manifest dict and returns `ValidationResult(ok: bool, errors: list[str])`
- It validates: `manifest_version` present and `"1.0"`, `capability.name/version/content_hash`
  all present, every tool entry has `name`, `profile ∈ {read, write}`, `safety: bool`; write tools
  additionally require `write_kind ∈ {offline_artifact, sim_only}`; sim_only write tools require
  `sim_target.kind` and `sim_target.verify_critic`
- `resumable_cursor.supported` and `findings.exposes_status` are validated if present
- The module includes a `__main__` block: `python -m talos.validator <manifest.json>` prints
  PASS or FAIL + errors
- At least 5 unit tests cover: a valid manifest, missing `content_hash`, a write tool missing
  `write_kind`, a sim_only tool missing `verify_critic`, an unknown profile value

Before proposing a path, ask the user one question: where should this module live (`engine/`,
`gateway/`, or a new `tools/` directory)?

---

### 3. RT-20 · Thread_id ↔ attempt spec `[documentation]`

**Files:** `engine/schema.sql`, `docs/decisions/ADR-010-worker-isolation.md`

ADR-010 defines session keys as `task:{board_id}:{task_id}:{attempt}` and says workers are
isolated by session key. The schema has `task_runs` with a `run_id` but the relationship between
`thread_id` (if it exists), `run_id`, `attempt`, and the session key is not formally specified.

**Done when:**
- You have read `engine/schema.sql` and `ADR-010` fully
- You add a short section to `ADR-010` titled `## Session key ↔ schema mapping` that specifies:
  - Which schema column is the attempt counter and how it increments
  - How `run_id` relates to a single attempt
  - Whether `thread_id` exists or if that concept maps to `run_id` + `board_id`
  - What happens on crash recovery (checkpoint resume vs. new attempt)
- If `thread_id` is missing from the schema and belongs there, add it to
  `engine/schema-additions.sql` with a migration comment

Do not invent new concepts — derive the spec from what the schema and ADR already say, then close
the gap.

---

### 4. CR-15 · GitHub Agentic Workflows license `[research]`

**Context:** TALOS studied five open-source harnesses. The integration notes reference
"GitHub Agentic Workflows" as a potential pattern source. Before using any patterns or code from
it, the license must be confirmed.

**Done when:**
- You have looked up the current license of GitHub's Agentic Workflows (or whatever the correct
  name is for GitHub's agent/workflow automation framework)
- You record your finding in `docs/integration/01_conflicts_and_resolutions.md` under CR-15:
  - The exact license (MIT, Apache-2.0, proprietary, etc.)
  - The source URL or repo where you confirmed it
  - A one-line recommendation: patterns-only safe / code-usable / avoid
- If it turns out "GitHub Agentic Workflows" is ambiguous or refers to multiple things, note what
  you found and ask the user to clarify before writing anything

---

### 5. ADR-016 · Promote from Proposed to Accepted `[documentation]`

**File:** `docs/decisions/ADR-016-dag-driven-project-scheduling.md`

ADR-016 is in `Proposed` status pending a severity-gating action item. The interview confirmed
severity gating was resolved (HIGH findings auto-create issue + stage for review; MEDIUM
auto-dispatch with shortened gate; LOW log only).

**Done when:**
- You have read ADR-016 in full
- You update its status from `Proposed` to `Accepted`
- You add or update any section that references the severity-gating open question to reflect the
  confirmed behavior from the interview
- The date field reflects today (2026-06-14)

---

## Rules

- Work through items in order: 1 → 2 → 3 → 4 → 5.
- For item 2, ask the one specified question before writing any code.
- Read each target file before editing it. Match existing style.
- Do not refactor, rename, or rewrite existing content beyond what each item requires.
- Do not create new documents unless an item explicitly calls for one.
- When all five are done, report a one-line status per item and list any new open questions that
  emerged.

Start with item 1.
