-- =============================================================================
-- TALOS — DAG-driven project scheduling schema additions
-- =============================================================================
-- Adds PM automation to the existing engine/schema.sql:
--   * Scheduling columns on tasks (computed by trigger on status/duration change)
--   * Milestones table (named checkpoints with dependency sets)
--   * v_critical_path (forward/backward pass via recursive CTE)
--   * v_gantt (the Gantt chart as a SQL view — no separate data store)
--   * PM hook triggers (dependency unblocker, critical path recomputer)
--
-- Applies on top of engine/schema.sql. All timestamps are timestamptz.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. SCHEDULING COLUMNS ON TASKS
-- ---------------------------------------------------------------------------
-- estimated_hours and deadline are human-set (or agent-proposed, human-approved).
-- All other scheduling columns are COMPUTED by the critical-path engine.
-- overridden_by / override_reason record manual scheduling interventions.

ALTER TABLE tasks ADD COLUMN estimated_hours    numeric;
ALTER TABLE tasks ADD COLUMN actual_hours       numeric;          -- computed from run duration
ALTER TABLE tasks ADD COLUMN deadline           timestamptz;      -- hard date, nullable
ALTER TABLE tasks ADD COLUMN earliest_start     timestamptz;      -- computed: max(finish of dependencies)
ALTER TABLE tasks ADD COLUMN latest_finish      timestamptz;      -- computed: min(deadline, start of dependents)
ALTER TABLE tasks ADD COLUMN float_hours        numeric;          -- computed: latest_start - earliest_start
ALTER TABLE tasks ADD COLUMN on_critical_path   boolean NOT NULL DEFAULT false;  -- computed: float ≈ 0
ALTER TABLE tasks ADD COLUMN overridden_by      TEXT;             -- who manually overrode earliest_start
ALTER TABLE tasks ADD COLUMN override_reason    TEXT;             -- why (shutdown window, staffing, etc.)

COMMENT ON COLUMN tasks.actual_hours IS 'Computed from the most recent task_runs duration for this task. Updated by trigger on run completion.';
COMMENT ON COLUMN tasks.earliest_start IS 'Computed: max(finish time of all dependency tasks). Overridable by human with audit trail.';
COMMENT ON COLUMN tasks.latest_finish IS 'Computed: min(deadline, earliest_start of all dependent tasks).';
COMMENT ON COLUMN tasks.float_hours IS 'Computed: latest_start - earliest_start. Negative = already behind schedule.';
COMMENT ON COLUMN tasks.on_critical_path IS 'Computed: true when float_hours <= threshold (default 0). Drives dispatcher priority.';

-- ---------------------------------------------------------------------------
-- 2. MILESTONES — named checkpoints with dependency sets
-- ---------------------------------------------------------------------------
-- A milestone is a named deliverable checkpoint with a deadline.
-- Status is COMPUTED by the PM hooks; never manually set.
-- depends_on is a text array of task IDs. All must be 'done' or 'approved'
-- for the milestone to be 'met'.

CREATE TABLE milestones (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    board_id    TEXT NOT NULL REFERENCES boards(id),
    name        TEXT NOT NULL,
    description TEXT,
    deadline    timestamptz NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'on_track', 'at_risk', 'missed', 'met')),
    depends_on  TEXT[] NOT NULL DEFAULT '{}',  -- task IDs required for this milestone
    met_at      timestamptz,                   -- when the milestone was met
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_milestones_board   ON milestones(board_id);
CREATE INDEX idx_milestones_project ON milestones(project_id);
CREATE INDEX idx_milestones_status  ON milestones(board_id, status);

COMMENT ON TABLE milestones IS 'Named checkpoints. Status is COMPUTED by PM hooks — never set directly.';
COMMENT ON COLUMN milestones.depends_on IS 'Array of task IDs. All must be done/approved for status to reach "met".';

-- ---------------------------------------------------------------------------
-- 3. CRITICAL PATH VIEW (forward/backward pass via recursive CTE)
-- ---------------------------------------------------------------------------
-- For projects under ~500 tasks, this runs fast enough on-demand.
-- For larger projects, materialize via trigger into the computed columns and
-- run the full CTE only on status/duration transitions.
--
-- Forward pass:  earliest_start  = max(finish of all dependencies)
--                earliest_finish = earliest_start + estimated_hours
-- Backward pass: latest_finish   = min(deadline, earliest_start of dependents)
--                latest_start    = latest_finish - estimated_hours
-- Float:         float_hours     = latest_start - earliest_start
-- Critical:      float_hours <= 0

-- Helper: dependency graph edges with computed finish times.
-- A dependency task's "finish" is its earliest_start + estimated_hours, or now() if running.
CREATE OR REPLACE VIEW v_task_finish AS
SELECT
    t.id,
    t.board_id,
    t.estimated_hours,
    t.deadline,
    COALESCE(t.overridden_by IS NOT NULL, false) AS has_override,
    CASE
        WHEN t.status IN ('done', 'approved') THEN t.completed_at
        WHEN t.status IN ('running', 'review') THEN
            -- Running or in review: finish = now + remaining estimated hours
            -- (pessimistic: assume full remaining duration)
            now() + (t.estimated_hours - COALESCE(t.actual_hours, 0)) * interval '1 hour'
        ELSE
            -- Not started: earliest_start + estimated_hours
            t.earliest_start + COALESCE(t.estimated_hours, 0) * interval '1 hour'
    END AS projected_finish
FROM tasks t;

-- Critical path CTE: forward pass.
-- Walks the DAG from root tasks (no dependencies) forward.
CREATE OR REPLACE VIEW v_forward_pass AS
WITH RECURSIVE forward_pass AS (
    -- Root tasks: no dependencies → earliest_start = now() (or manual override)
    SELECT
        t.id,
        t.board_id,
        t.estimated_hours,
        t.deadline,
        t.overridden_by,
        t.override_reason,
        COALESCE(
            t.earliest_start,  -- human override takes precedence
            CASE WHEN t.status IN ('done', 'approved') THEN t.started_at
                 ELSE now() END
        ) AS earliest_start,
        COALESCE(t.estimated_hours, 0) AS remaining_hours,
        0 AS depth
    FROM tasks t
    WHERE t.board_id = current_setting('talos.board_id', true)
      AND NOT EXISTS (
          SELECT 1 FROM task_links tl WHERE tl.child_id = t.id
      )

    UNION ALL

    -- Walk forward: child's earliest_start = max(parent's finish)
    SELECT
        child.id,
        child.board_id,
        child.estimated_hours,
        child.deadline,
        child.overridden_by,
        child.override_reason,
        GREATEST(
            COALESCE(child.earliest_start, '1970-01-01'::timestamptz),
            fp.earliest_start + fp.remaining_hours * interval '1 hour'
        ) AS earliest_start,
        COALESCE(child.estimated_hours, 0) AS remaining_hours,
        fp.depth + 1
    FROM tasks child
    JOIN task_links tl ON tl.child_id = child.id
    JOIN forward_pass fp ON fp.id = tl.parent_id
    WHERE child.board_id = current_setting('talos.board_id', true)
)
SELECT DISTINCT ON (id)
    id, earliest_start, remaining_hours, depth
FROM forward_pass
ORDER BY id, earliest_start DESC;  -- take the latest (constrained) earliest_start

-- Full critical path: forward pass + backward pass = float
CREATE OR REPLACE VIEW v_critical_path AS
WITH forward AS (
    SELECT * FROM v_forward_pass
),
-- Backward pass: walk from leaf tasks backward.
-- A leaf task's latest_finish = its deadline (or ∞ if none).
-- A parent's latest_finish = min(latest_start of all children).
backward_leaves AS (
    SELECT
        t.id,
        t.board_id,
        COALESCE(t.deadline, '2099-12-31'::timestamptz) AS latest_finish
    FROM tasks t
    WHERE t.board_id = current_setting('talos.board_id', true)
      AND NOT EXISTS (
          SELECT 1 FROM task_links tl WHERE tl.parent_id = t.id
      )
),
backward_pass AS (
    SELECT * FROM backward_leaves
    -- For a full backward pass, we'd recurse up the DAG. For the initial
    -- implementation, we compute float from the forward pass + deadline only.
    -- Full backward recursion is Phase 2 — see action items.
),
scheduling AS (
    SELECT
        f.id,
        f.earliest_start,
        f.earliest_start + f.remaining_hours * interval '1 hour' AS earliest_finish,
        t.deadline,
        COALESCE(t.deadline, '2099-12-31'::timestamptz) AS latest_finish,
        t.estimated_hours,
        t.status,
        f.depth,
        -- Float in hours: (latest_finish - earliest_finish) in hours
        EXTRACT(EPOCH FROM (
            COALESCE(t.deadline, '2099-12-31'::timestamptz)
            - (f.earliest_start + f.remaining_hours * interval '1 hour')
        )) / 3600.0 AS float_hours
    FROM forward f
    JOIN tasks t ON t.id = f.id
)
SELECT
    id,
    earliest_start,
    earliest_finish,
    latest_finish,
    deadline,
    estimated_hours,
    float_hours,
    float_hours <= 0 AS on_critical_path,
    float_hours < 0  AS already_late,
    status,
    depth
FROM scheduling
ORDER BY earliest_start;

COMMENT ON VIEW v_critical_path IS 'Forward/backward pass over the task DAG. Float ≤ 0 = critical path. Consumed by v_gantt and the PM hooks.';

-- ---------------------------------------------------------------------------
-- 4. GANTT VIEW — the Gantt chart as a SQL projection
-- ---------------------------------------------------------------------------
-- The cockpit's Gantt widget consumes this view. One row per task bar.
-- Milestones render as diamonds on the timeline.
-- Critical path tasks render with a red border/line.
-- No separate data store. No sync.

CREATE OR REPLACE VIEW v_gantt AS
SELECT
    t.id,
    t.board_id,
    t.title,
    t.status,
    t.priority,
    t.assignee,
    cp.earliest_start,
    cp.earliest_finish,
    cp.latest_finish,
    cp.deadline,
    COALESCE(t.estimated_hours, 0) AS estimated_hours,
    COALESCE(t.actual_hours, 0)   AS actual_hours,
    cp.float_hours,
    cp.on_critical_path,
    cp.already_late,
    t.overridden_by,
    t.override_reason,
    -- Milestone data (null if not a milestone dependency)
    m.id    AS milestone_id,
    m.name  AS milestone_name,
    m.deadline AS milestone_deadline,
    m.status   AS milestone_status
FROM tasks t
LEFT JOIN v_critical_path cp ON cp.id = t.id
LEFT JOIN milestones m ON t.id = ANY(m.depends_on) AND m.board_id = t.board_id
ORDER BY cp.earliest_start;

COMMENT ON VIEW v_gantt IS 'Gantt chart as a SQL projection. One row per task bar with milestone context. The cockpit renders this directly — no separate data store.';

-- ---------------------------------------------------------------------------
-- 5. PM HOOK: DEPENDENCY UNBLOCKER
-- ---------------------------------------------------------------------------
-- Trigger fires after task status transitions to 'done' or 'approved'.
-- Unblocks all tasks that depend on this one, setting their status to 'ready'
-- IF all their other dependencies are also done/approved.

CREATE OR REPLACE FUNCTION pm_unblock_dependents()
RETURNS TRIGGER AS $$
DECLARE
    child RECORD;
    all_deps_done BOOLEAN;
BEGIN
    -- Only fire on transition TO 'done' or 'approved'
    IF NEW.status NOT IN ('done', 'approved') THEN
        RETURN NEW;
    END IF;

    -- Walk each task that depends on this one
    FOR child IN
        SELECT tl.child_id, t.board_id, t.status
        FROM task_links tl
        JOIN tasks t ON t.id = tl.child_id
        WHERE tl.parent_id = NEW.id
          AND t.status IN ('backlog', 'blocked')
    LOOP
        -- Check: are ALL dependencies of this child done or approved?
        SELECT bool_and(t.status IN ('done', 'approved'))
        INTO all_deps_done
        FROM task_links tl
        JOIN tasks t ON t.id = tl.parent_id
        WHERE tl.child_id = child.child_id;

        IF all_deps_done THEN
            UPDATE tasks
            SET status = 'ready', earliest_start = now()
            WHERE id = child.child_id;

            -- Log the unblock event
            INSERT INTO task_events (board_id, task_id, kind, payload)
            VALUES (child.board_id, child.child_id, 'status_change',
                    jsonb_build_object(
                        'from', child.status,
                        'to', 'ready',
                        'reason', 'all dependencies completed',
                        'unblocked_by', NEW.id
                    ));
        END IF;
    END LOOP;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Fire AFTER the status change commits
CREATE TRIGGER trg_pm_unblock_dependents
    AFTER UPDATE OF status ON tasks
    FOR EACH ROW
    WHEN (NEW.status IN ('done', 'approved') AND OLD.status NOT IN ('done', 'approved'))
    EXECUTE FUNCTION pm_unblock_dependents();

-- ---------------------------------------------------------------------------
-- 6. PM HOOK: CRITICAL PATH RECOMPUTER
-- ---------------------------------------------------------------------------
-- Trigger fires on any task status change or when actual_hours exceeds
-- estimated_hours by >20%. Recomputes the critical path and updates milestone
-- status. Runs AFTER the transaction commits to avoid holding locks.

CREATE OR REPLACE FUNCTION pm_recompute_scheduling()
RETURNS TRIGGER AS $$
DECLARE
    cp RECORD;
    milestone RECORD;
    deps_complete BOOLEAN;
BEGIN
    -- Only fire on meaningful changes
    IF TG_OP = 'UPDATE' THEN
        -- Recompute on status change
        IF NEW.status <> OLD.status THEN
            -- continue
        -- Recompute on significant duration overage
        ELSIF NEW.actual_hours IS NOT NULL
          AND NEW.estimated_hours IS NOT NULL
          AND NEW.actual_hours > NEW.estimated_hours * 1.2
          AND (OLD.actual_hours IS NULL OR NEW.actual_hours > OLD.actual_hours)
        THEN
            -- continue
        ELSE
            RETURN NEW;
        END IF;
    END IF;

    -- Update computed columns from v_critical_path
    FOR cp IN
        SELECT id, float_hours, on_critical_path, earliest_start, earliest_finish, latest_finish
        FROM v_critical_path
        WHERE id IS NOT NULL
    LOOP
        UPDATE tasks
        SET float_hours      = cp.float_hours,
            on_critical_path = cp.on_critical_path,
            earliest_start   = COALESCE(tasks.earliest_start, cp.earliest_start), -- don't clobber overrides
            latest_finish    = cp.latest_finish
        WHERE tasks.id = cp.id
          AND tasks.board_id = current_setting('talos.board_id', true);
    END LOOP;

    -- Update milestone status based on recomputed critical path
    FOR milestone IN
        SELECT m.id, m.deadline, m.depends_on
        FROM milestones m
        WHERE m.board_id = current_setting('talos.board_id', true)
          AND m.status NOT IN ('met', 'missed')
    LOOP
        -- Check if all dependency tasks are done/approved
        SELECT bool_and(t.status IN ('done', 'approved'))
        INTO deps_complete
        FROM tasks t
        WHERE t.id = ANY(milestone.depends_on);

        IF deps_complete THEN
            UPDATE milestones SET status = 'met', met_at = now(), updated_at = now()
            WHERE id = milestone.id;
        ELSIF milestone.deadline < now() THEN
            UPDATE milestones SET status = 'missed', updated_at = now()
            WHERE id = milestone.id;
        ELSIF milestone.deadline < now() + interval '48 hours' THEN
            -- Within 48h of deadline → at_risk
            UPDATE milestones SET status = 'at_risk', updated_at = now()
            WHERE id = milestone.id AND status <> 'at_risk';
        ELSE
            UPDATE milestones SET status = 'on_track', updated_at = now()
            WHERE id = milestone.id AND status <> 'on_track';
        END IF;
    END LOOP;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_pm_recompute_scheduling
    AFTER UPDATE OF status, actual_hours, estimated_hours ON tasks
    FOR EACH ROW
    EXECUTE FUNCTION pm_recompute_scheduling();

-- ---------------------------------------------------------------------------
-- 7. PM HOOK: MILESTONE RISK ESCALATOR
-- ---------------------------------------------------------------------------
-- Trigger fires when a milestone transitions to 'at_risk' or 'missed'.
-- Emits a MEDIUM finding → auto-creates an issue → auto-dispatches a
-- remediation task. Maps to BLUEPRINT's findings→issues loop.

CREATE OR REPLACE FUNCTION pm_escalate_milestone_risk()
RETURNS TRIGGER AS $$
BEGIN
    -- Only fire on transition TO at_risk or missed
    IF NEW.status IN ('at_risk', 'missed')
       AND (OLD.status IS NULL OR OLD.status NOT IN ('at_risk', 'missed'))
    THEN
        -- Log the escalation event
        INSERT INTO task_events (board_id, task_id, kind, payload)
        VALUES (
            NEW.board_id,
            NULL,  -- milestone events aren't tied to one task
            'milestone_risk',
            jsonb_build_object(
                'milestone_id', NEW.id,
                'milestone_name', NEW.name,
                'status', NEW.status,
                'deadline', NEW.deadline,
                'depends_on', NEW.depends_on,
                'severity', CASE WHEN NEW.status = 'missed' THEN 'HIGH' ELSE 'MEDIUM' END
            )
        );

        -- TODO: When the findings→issues loop is implemented (Phase 3),
        -- this is where the auto-issue-creation and auto-dispatch happen.
        -- For now, the event log entry is sufficient — the gateway/cron
        -- layer can poll task_events for unhandled escalations.
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_pm_escalate_milestone_risk
    AFTER UPDATE OF status ON milestones
    FOR EACH ROW
    EXECUTE FUNCTION pm_escalate_milestone_risk();

-- ---------------------------------------------------------------------------
-- 8. DISPATCHER PRIORITY SORT — the ready-queue query
-- ---------------------------------------------------------------------------
-- The dispatcher uses this query to claim the next task.
-- Critical path tasks sort first. Within the same criticality, respect
-- manual priority. Within the same priority, earliest start wins.
--
-- SELECT * FROM tasks
-- WHERE status = 'ready'
--   AND board_id = current_setting('talos.board_id', true)
-- ORDER BY on_critical_path DESC, priority DESC, earliest_start ASC
-- LIMIT 1;

-- ---------------------------------------------------------------------------
-- 9. ROW-LEVEL SECURITY — milestones (client isolation, same pattern as schema.sql)
-- ---------------------------------------------------------------------------
-- milestones carries board_id; apply the same two-policy pattern:
--   board_isolation — client sessions see only their board's milestones.
--   admin_bypass    — talos_admin bypasses for migrations and internal tooling.

ALTER TABLE milestones ENABLE ROW LEVEL SECURITY;
CREATE POLICY milestones_board_isolation ON milestones
    USING     (board_id = current_setting('app.board_id', true))
    WITH CHECK (board_id = current_setting('app.board_id', true));
CREATE POLICY milestones_admin_bypass ON milestones
    USING (current_user = 'talos_admin');

-- ---------------------------------------------------------------------------
-- 11. WORKER ISOLATION — attempt_no on task_runs (RT-20 / ADR-010)
-- ---------------------------------------------------------------------------
-- The ADR-010 session key is task:{board_id}:{task_id}:{attempt}. attempt_no
-- is the per-task counter: 1 for the first claim, 2 after a crash-recovery
-- reclaim, etc. It is minted at claim time (never updated after claim).
-- The unique index enforces that no two concurrent claims share an attempt slot.

ALTER TABLE task_runs ADD COLUMN attempt_no INTEGER NOT NULL DEFAULT 1;
CREATE UNIQUE INDEX idx_runs_attempt ON task_runs(task_id, attempt_no);

-- Migration note: existing rows get attempt_no = 1; backfill monotonically
-- if multiple runs exist for the same task:
--   UPDATE task_runs r
--   SET attempt_no = sub.rn
--   FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY task_id ORDER BY started_at) AS rn
--         FROM task_runs) sub
--   WHERE r.id = sub.id;

-- ---------------------------------------------------------------------------
-- 12. MIGRATION NOTES
-- ---------------------------------------------------------------------------
-- After applying these additions:
--   1. Populate estimated_hours for existing tasks (NULL = no estimate, not zero).
--   2. Populate actual_hours from completed task_runs:
--        UPDATE tasks t SET actual_hours =
--          EXTRACT(EPOCH FROM (r.ended_at - r.started_at)) / 3600.0
--        FROM task_runs r
--        WHERE r.task_id = t.id AND r.status = 'done'
--          AND r.outcome = 'completed';
--   3. Run pm_recompute_scheduling() manually once to seed computed columns.
--   4. Set up any missing milestones with depends_on populated.
--   5. Schedule pm_recompute_scheduling() as a periodic refresh via the gateway
--      cron layer for long-running tasks that don't generate status changes.
