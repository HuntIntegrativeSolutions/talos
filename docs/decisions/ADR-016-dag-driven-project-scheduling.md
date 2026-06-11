# ADR-016: DAG-driven project scheduling — the board, Gantt, and dispatcher are one system

**Status:** Proposed
**Date:** 2026-06-11
**Deciders:** Hunt Integrative Solutions LLC

## Context

The BLUEPRINT establishes project management as the spine of TALOS and the Strategy Ladder as the
task-level execution pattern. But the connection between the two — how the board's task DAG drives
project scheduling, worker dispatch, and Gantt visualization — has been stated as intent without a
mechanism. The question: how do the kanban board, the Gantt chart, and the dispatcher relate to each
other, and what automation can TALOS provide?

The core insight is that all three are projections of one data structure: the task dependency DAG.

- The **board** is the DAG filtered by status (kanban columns).
- The **Gantt** is the DAG topologically sorted and projected onto a calendar.
- The **dispatcher** is the DAG sorted by critical-path priority to claim the next task.

A delay on one task shifts the critical path, which reorders the board's ready column, redraws the
Gantt, and reprioritizes the dispatcher — all from one event, without any worker touching PM state.

## Decision

**Implement DAG-driven project scheduling as four PM hooks that fire on task status transitions,**
backed by computed scheduling columns on `tasks` and a materialized critical-path view. The Gantt
is a SQL view, not a separate data store. The dispatcher sorts by `on_critical_path` before
`priority`. Workers deliver structured results (duration, status) and the PM layer reacts — never
the reverse.

### Mechanism

1. **Scheduling columns on tasks** — `estimated_hours`, `actual_hours`, `deadline`, `earliest_start`,
   `latest_finish`, `float_hours`, `on_critical_path`. All computed columns (except `estimated_hours`
   and `deadline`, which are human-set). Updated by a trigger when any task in the DAG changes
   status or actual duration exceeds estimate.

2. **Milestones table** — named checkpoints with a deadline and a set of task IDs they depend on.
   Status is computed (`pending` | `on_track` | `at_risk` | `missed` | `met`).

3. **Critical path view** (`v_critical_path`) — forward pass (earliest start/finish) + backward pass
   (latest start/finish) via recursive CTE. Float = latest_start − earliest_start. Critical = float
   ≈ 0. The Gantt renderer consumes this view directly.

4. **Four PM hooks** (same pattern as critics — deterministic functions on status transition events):

   - **Dependency unblocker** — when a task reaches `done` or `approved`, unblocks all dependent
     tasks (`status → 'ready'`).
   - **Critical path re-computer** — on any task's duration exceeding estimate by >20%, or any
     status change, recompute the forward/backward pass and update milestone status.
   - **Milestone risk escalator** — within 48 hours of a milestone deadline with incomplete
     dependencies, emit a MEDIUM finding → auto-create issue → auto-dispatch a remediation task.
   - **Status report data compiler** — on demand or schedule, compile milestone status, completed
     vs. planned, critical path health, gate results, and budget burn into the data section of a
     status report.

### The Gantt is a view, not a store

```sql
CREATE VIEW v_gantt AS
SELECT t.id, t.title, t.status, t.estimated_hours, t.actual_hours,
       t.earliest_start, t.latest_finish, t.on_critical_path,
       m.id AS milestone_id, m.name AS milestone_name, m.deadline
FROM tasks t
LEFT JOIN milestones m ON t.id = ANY(m.depends_on)
ORDER BY t.earliest_start;
```

The cockpit's Gantt widget consumes `v_gantt`. No separate Gantt data model. No sync. One source of
truth.

### Dispatcher priority sort

The dispatcher loop sorts ready tasks by:

```
ORDER BY on_critical_path DESC, priority DESC, earliest_start ASC
```

Critical path tasks are claimed first because a delay on a critical task delays the project. This
means the board's ready column, the Gantt's next-up bar, and the dispatcher's claim target are all
the same task — derived from the same query, not three different systems.

### Human override with audit trail

Humans can override `earliest_start` manually (e.g., "don't start the burner audit until the
shutdown window"). The override is recorded with `overridden_by` and `override_reason`. The critical
path recomputes around the override. Same waiver pattern as the gate — the override is visible,
recorded, and auditable, never silently absorbed.

## Options considered

### Option A: Separate Gantt data model (PM tool in parallel to the board)

The board and Gantt are separate systems with their own data. Tasks are duplicated or linked by
reference. When a task completes, a sync job updates the Gantt.

| Dimension | Assessment |
|-----------|------------|
| Simplicity | Low — two systems to maintain, sync drift inevitable |
| Automation | Weak — hooks must cross a system boundary; each sync point is a failure mode |
| Why rejected | Duplicates the board's truth; the Gantt is always slightly wrong |

### Option B: Manual scheduling (humans set dates, no DAG computation)

Tasks have start/end dates set by hand. The Gantt is a static chart. No critical path computation.
No dependency-driven unblocking.

| Dimension | Assessment |
|-----------|------------|
| Simplicity | High — no automation to build or debug |
| Automation | None — every schedule adjustment is a manual edit |
| Why rejected | "Automate our project management" is the requirement. A static chart isn't automation. |

### Option C: DAG-driven scheduling with computed columns, views, and hooks (chosen)

| Dimension | Assessment |
|-----------|------------|
| Correctness | High — one source of truth (the DAG); all projections are read models |
| Automation | High — status transitions cascade through the hooks without human intervention |
| Auditability | High — every recomputation is logged via the append-only event log |
| Operational cost | Moderate — recursive CTE on large DAGs; needs materialization for projects >500 tasks |

## Trade-off analysis

**The Gantt-as-a-view approach means the Gantt is always current.** There is no sync lag because
there is nothing to sync. The cost is that the critical-path CTE runs a forward/backward pass every
time it's queried. Mitigations:

- Materialize `v_critical_path` results into computed columns via trigger (cheap incremental updates)
  rather than full recomputation on every cockpit render.
- For projects under ~500 tasks, the recursive CTE is fast enough to run on-demand. TALOS projects
  are task-count constrained by design (each task is a gated deliverable, not a micro-task).

**The PM hooks are deterministic, like critics.** They run on the same event stream, they're logged
to the same append-only event log, and they never mutate task data that isn't a computed scheduling
column. The dependency unblocker is the only hook that changes `tasks.status`, and it only changes
it to `ready` — which never short-circuits a gate. A hook cannot approve, waive, or complete a task.

**The human override on `earliest_start` is necessary for real projects.** Shutdown windows,
equipment availability, and staffing constraints create hard scheduling constraints that a DAG can't
infer from dependencies alone. The override is a first-class field with audit trail, not a hack.

## Consequences

- **Easier:** automated scheduling from the DAG rather than manual date-setting; auto-unblocking of
  dependent tasks; Gantt always current; dispatcher naturally prioritizes critical-path work.
- **Harder:** recursive CTE optimization for large project DAGs; materialization strategy for thick
  edges that can't query the mothership's live Postgres.
- **Revisit:** whether `v_gantt` stays a view or becomes a materialized table for thick-edge
  deployments; whether the critical path recomputation interval should be configurable (currently
  on every duration-exceeding-estimate event).

## Action items

1. [ ] Add scheduling columns to `tasks`: `estimated_hours`, `actual_hours`, `deadline`,
      `earliest_start`, `latest_finish`, `float_hours`, `on_critical_path`,
      `overridden_by`, `override_reason`.
2. [ ] Create `milestones` table.
3. [ ] Implement `v_critical_path` as a recursive CTE.
4. [ ] Implement the four PM hooks as deterministic status-transition triggers.
5. [ ] Update the dispatcher sort order to include `on_critical_path`.
6. [ ] Document the Gantt widget contract for the cockpit (consumes `v_gantt`, renders bars
      color-coded by status, highlights critical path).
7. [ ] Define the milestone-risk-to-finding severity mapping (MEDIUM → auto-dispatch remediation;
      HIGH for safety-significant milestones).
