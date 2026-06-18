# Contract — `board-api`

> **What this is:** the frozen specification of the **only** channel between the board engine
> (Postgres system-of-record) and the Space Agent cockpit view. It pins the read projections, the
> gate-outcome write path, the widget-upsert request, the time-travel rule, and the RLS scope — enough
> that the engine team and the view team build the two sides without further conversation.
> **What this is not:** an HTTP framework choice, a component-internals spec, or new architecture. It
> encodes only what the ADRs and the unified architecture already require. Wire-transport details are
> marked *illustrative*; where the architecture genuinely does not pin a detail it is escalated under
> **Open questions**, not decided here.
>
> **Citation key:** `02 §N` = `docs/integration/02_unified_architecture.md` (the `§8 #n` items are the
> numbered invariants); `ADR-0NN` = `docs/decisions/`; `CR-NN` =
> `docs/integration/01_conflicts_and_resolutions.md`; schema objects quote `engine/schema.sql` and
> `engine/schema-additions.sql` verbatim.

**Status:** Draft for freeze · **Date:** 2026-06-12 · **Deciders:** Hunt Integrative Solutions LLC
**Seam:** engine ↔ view (`02 §6`, row 1) · **Freeze order:** before Phase 0/5 (CR-23)

---

## Purpose

The board engine owns the truth: `tasks`, the dependency DAG, the append-only `task_events` log, the
review gate, and the Space/widget layout tables. The cockpit must render all of that, let a human act
on the gate, and let an agent propose new layout — **without ever touching Postgres directly**
(ADR-002: "The view reaches the engine only through the board API"). `board-api` is that surface. It
is also the *parent* surface that `widget-sandbox` exposes a restricted subset of (see
[`widget-sandbox.md`](./widget-sandbox.md)).

## The two sides it decouples

| Side | Owner | Builds against this contract to… |
| :--- | :--- | :--- |
| **Engine** (producer) | `engine/` — Postgres + dispatcher | expose the read projections, accept gate outcomes, accept widget-upsert requests, stream event deltas |
| **View** (consumer) | `web/` — cockpit; and, restricted, sandboxed widgets | render board/Gantt/gate state, submit the human gate decision, propose widgets, scrub layout history |

The engine never imports view code; the view never imports a DB driver. The only shared artifact is
this contract.

## Interface

Operations are grouped by capability. Each is tagged **[D]** *derived-from-artifact* (the payload/
columns/lifecycle are fixed by schema or an ADR — both sides must honor it) or **[I]** *illustrative*
(a representative shape; the wire form is an Open question). REST verbs/paths are **[I]** throughout,
following the `api.py` GET-only Starlette precedent and the Hermes-Profile-Builder REST surface cited
as the "board-API reference" (`00_integration_map.md` §2). What is frozen is the **payload shape,
backing columns, scope, and ordering key** — not the verb.

### 1. Reads — board state projections **[D]**

All reads are board-scoped (see Invariants). Column sources are verbatim from `engine/schema.sql` /
`engine/schema-additions.sql`.

**`getTasks(board_id, [status?, assignee?, tenant?])` → Task[]** — projection of `tasks`:

```json
{
  "id": "t-1042", "board_id": "acme", "tenant": "line1-pack",
  "title": "Audit ACME-SCAN-01 SCANNER outputs", "status": "review",
  "assignee": "auditor-profile", "priority": 3,
  "created_at": "2026-06-12T14:02:00Z", "started_at": "…", "completed_at": null,
  "gate_required": true, "approved_at": null, "approved_by": null,
  "rejected_at": null, "rejection_reason": null,
  "estimated_hours": 6, "actual_hours": 2.5, "deadline": "…",
  "on_critical_path": true, "float_hours": -3.0
}
```
`status` ∈ `backlog | ready | running | blocked | review | approved | rejected | done | archived`
(`schema.sql` lines 38-40). Worker-internal columns (`claim_lock`, `worker_pid`, `session_id`,
`idempotency_key`, `model_override`, `last_failure_error`) are hidden under a **least-privilege
projection default** — a contract *recommendation*, not a source-pinned invariant; the exact allowlist
is escalated (Open questions #5).

**`getGateStatus(board_id, task_id)` → GateStatus** — projection of the `v_gate_status` view verbatim:

```json
{ "task_id": "t-1042", "board_id": "acme",
  "all_required_pass": false, "human_approved": false, "gate_satisfied": false }
```
Plus the per-critic detail rows from `task_gate_results` (`critic_name`, `required`, `verdict ∈
pass|fail|warn`, `evidence_uri`, `details`, `created_at`).

**`getGantt(board_id)` → GanttRow[]** — projection of `v_gantt` (one row per task bar +
milestone context: `earliest_start`, `earliest_finish`, `latest_finish`, `float_hours`,
`on_critical_path`, `already_late`, `milestone_{id,name,deadline,status}`).

**`getSpace(board_id, space_id, [version_no?])` → Space + SpaceVersion** — `spaces`
(`current_version_id`, `status`, `kind`) joined to `space_versions` (`version_no`, `definition`
JSONB, `source_ref`, `author`). Omitting `version_no` returns `current_version_id`; supplying an older
one is **layout time-travel** (read-only replay).

**`getWidgets(board_id, [space_id?, task_id?])` → Widget + WidgetVersion** — `widgets` joined to
`widget_versions` (`version_no`, `source`, `sandbox_policy` JSONB). `sandbox_policy` semantics are
owned by [`widget-sandbox.md`](./widget-sandbox.md).

**`getMilestones(board_id, [project_id?])` → Milestone[]** — `milestones` (`status ∈ pending |
on_track | at_risk | missed | met`, computed; `depends_on` task-id array).

### 2. Event-delta stream — `subscribe(board_id, [task_id?], after_cursor)` **[D shape / I transport]**

The live cockpit tails the append-only `task_events` table. The **record shape and the monotone
cursor are frozen**; the push transport is Open.

```json
{ "cursor": 88123, "board_id": "acme", "task_id": "t-1042", "run_id": 5519,
  "kind": "gate", "payload": { "critic": "citations-resolvable", "verdict": "fail" },
  "created_at": "2026-06-12T14:09:31Z" }
```
- **Cursor = `task_events.id`** (`BIGINT GENERATED ALWAYS AS IDENTITY`, monotone) — **[D]**. A client
  resumes by passing the last `cursor` it saw. `kind` ∈ `created | claimed | heartbeat |
  status_change | gate | comment | milestone_risk | …` (`schema.sql` line 113).
- Transport (WebSocket vs SSE; the LangGraph `custom` stream mode) is **[I] / Open** — see Open
  questions. The schema comment names WebSocket ("The live dashboard tails this table … over
  WebSocket", `schema.sql` line 107) as precedent, not a freeze.

### 3. Gate-outcome submission — the trusted human path **[D]**

The five gate outcomes (ADR-011) are the **only** writes the view may cause to task truth, and only a
human may invoke them. This path is **not** reachable by sandboxed widgets (see `widget-sandbox.md`).

| Outcome | Effect on truth | Columns written |
| :--- | :--- | :--- |
| **Approve** | sets human approval | `tasks.approved_by`, `tasks.approved_at = now()` |
| **Reject-with-reason** | returns to worker | `tasks.rejected_by`, `tasks.rejected_at`, `tasks.rejection_reason` |
| **Waive-with-justification** | overrides a *waivable* critic, recorded not skipped | a `task_gate_results` row / annotation carrying `waived_by` + `justification` |
| **Edit-inline** | fix deliverable, then approve | deliverable write + Approve |
| **Escalate** | second reviewer | status/assignment change; no `approved_at` |

```json
// submitGateOutcome — illustrative envelope [I]; the field set is [D]
{ "board_id": "acme", "task_id": "t-1042", "outcome": "waive",
  "actor": "thunt", "critic_name": "isa101-palette",
  "justification": "ISA-101 palette deviation accepted for legacy panel parity" }
```
Engine-side rule (frozen): a task **cannot leave `review`** until every *required* critic's latest
verdict is `pass` **AND** `tasks.approved_at` is set (`schema.sql` lines 13-15, 168-169; surfaced as
`v_gate_status.gate_satisfied`). The API submits the *outcome*; the engine enforces the transition.

> **Schema note:** `approved_by/at`, `rejected_by/at`, `rejection_reason` are live columns on `tasks`
> today. The Waive fields `waivable` / `waived_by` / `justification` are **specified by ADR-011** ("`task_gate_results`
> carries `waivable`, `waived_by`, and `justification`") but are **not yet in `engine/schema.sql`** —
> they land as additive `ALTER TABLE … ADD COLUMN` on `task_gate_results`, consistent with the
> additive-only Versioning rule below. The contract requires them; the DDL catches up.

**`requestGate(board_id, task_id)` — raise the gate UI, *not* an outcome [D].** A distinct operation
from `submitGateOutcome`: it asks the cockpit to surface the human gate panel for a task and returns
the gate **context** (the `getGateStatus` projection plus the deliverable diff to review). It **writes
nothing to task truth** and **cannot set `approved_at`**. This is the one gate-related operation
exposed (read/request class) to sandboxed widgets via the postMessage bridge (see
[`widget-sandbox.md`](./widget-sandbox.md) §1); `submitGateOutcome` is **not** in the widget's reach.

### 4. Widget-upsert **request** — `requestWidgetUpsert(...)` **[D lifecycle]**

The view (or an agent on its behalf) proposes a new/edited widget. This **creates a `widgets` row +
`widget_versions` row at `status='proposed'`** and returns; it never renders or pins. The
propose→render→critics→approve→pin lifecycle and the sandbox policy are owned by
[`widget-sandbox.md`](./widget-sandbox.md). `board-api` carries only the *request*; widget-sandbox owns
the *lifecycle*. (This split is the engine↔view / view↔widget seam boundary.)

## Invariants & forbidden operations

Each forbidden operation leads with its structural anchor.

1. **Time-travel versions layout only, never task truth.** — anchor **ADR-002** ("time-travel versions
   the layout, never task records … A UI rollback may restyle the board; it must never mutate task
   truth"); reinforced by the `schema.sql` header (lines 208, 7). *Forbidden:* any API call that, in
   the name of a layout/version rollback, writes `tasks`, `task_gate_results`, or `task_events`.
   Restyling acts on `space_versions.definition` / `widget_versions` only.

2. **No write to task truth except the human gate path.** — anchor **02 §8 #2** ("a human always sets
   `approved_at`; a 'shortened' gate never skips the human") + **ADR-011**. *Forbidden:* the view
   writing `tasks.status='approved'`/`approved_at` by any route other than the human Approve outcome;
   any API token (non-human service, gateway loop, widget) setting `approved_at`. A gateway/cron
   consumer "may notify and propose, never approve or write" (`02 §7`).

3. **The view cannot cross `board_id`.** — anchor **02 §8 #3** ("`board_id` + Postgres RLS is the only
   hard boundary") + **CR-02**. Every operation takes a `board_id` and is scoped to it; switching
   client re-scopes everything (ADR-013). *Enforcement is the database:* the engine sets `SET
   talos.board_id = '<board>'` per connection and each table carries `CREATE POLICY … USING (board_id =
   current_setting('talos.board_id', true))`. **Note:** in `engine/schema.sql` (lines 260-271) this RLS
   block is the **documented/intended** enforcement pattern (commented), not yet live DDL — cite it as
   the contract's required enforcer, to be enabled before multi-board production.

4. **Raw diagnostic trail is consumer-scoped, not blanket-forbidden.** — anchor **ADR-013** (the
   *planner* ingests only structured worker results — "deliverable + critic verdicts + event-log delta
   — never raw worker transcripts"). The human cockpit's **level-3 drill-down may** surface the raw
   trail (BLUEPRINT cockpit: "planner summary → structured worker result → raw diagnostic trail"). So:
   `board-api` *may* serve the raw trail to the **human view**, but the **planner** consumer must not
   ingest it. This is a consumer rule, not an endpoint ban.

## Versioning rule

The board-API surface is **additive-only**, mirroring the DB Integration Contract that already governs
this schema (`_Tools/CLAUDE.md` → "idempotent `ALTER TABLE ADD COLUMN` only (no DROP, no RENAME)";
"`nexus-ui/src/lib/nexus-db.ts` directly encodes column names — column renames silently break the
UI"). Concretely:

- New read fields, new operations, and new event `kind`s may be added at any time.
- A column or field the view binds is **never renamed or removed**; a deprecation ships as an additive
  alias first.
- The event `cursor` (`task_events.id`) ordering semantics never change.
- Breaking changes require a new contract major (`board-api/v2`) and a written migration note; the
  freeze is `v1`.

## Open questions for a human

1. **Push transport** — WebSocket (schema's own precedent) vs SSE vs the LangGraph `custom` stream
   mode for gate/analysis progress. The *record shape and cursor* are frozen; only the transport is
   open.
2. **Pagination / cursor envelope** — page size, max look-back window, and whether reads other than
   the event stream are cursor-paginated.
3. **Scope of the first freeze** — is `getGantt`/`v_critical_path` in `board-api/v1`, or an additive
   `v1.x`? `v_critical_path`'s full backward pass is itself marked "Phase 2" in
   `schema-additions.sql` (lines 175-177), so the Gantt projection may stabilize after the core reads.
4. **Service (non-human) callers** — ~~exactly which read operations the gateway/cron layer may call~~
   **CLOSED by ADR-036 / RT-01.** The `X-Human-Session` header must carry a valid TALOS JWT with
   `token_class: "human"` (signed with `TALOS_JWT_SECRET`, HMAC-HS256). The engine rejects anything
   else with HTTP 403 `{"error": "human session required"}`. Gateway/cron callers may never hold a
   `token_class: "human"` token; they may propose/notify, never approve. Per-read-operation
   authorization for non-human callers (gateway/cron) is deferred to a later phase (ROADMAP P4+).
   The `actor` field in illustrative gate-outcome envelopes is the JWT `sub` claim extracted
   server-side — it is never a field the caller supplies in the request body.
5. **Read-projection column allowlist** — exactly which `tasks` columns the view may read. The spec
   *recommends* a least-privilege projection that hides worker/credential bookkeeping (`claim_lock`,
   `worker_pid`, `session_id`, `idempotency_key`, `model_override`, `last_failure_error`), but **no
   ADR pins read-column hiding** — ROADMAP §256 lists only "raw task state writes, direct DB access"
   as *not exposed*. Confirm the projection allowlist (engine team) rather than treat the default as
   frozen.
