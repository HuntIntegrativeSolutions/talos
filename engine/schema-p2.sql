-- =============================================================================
-- TALOS P2 schema additions — Critics & five-outcome gate
-- =============================================================================

-- 1. Extend verdict types to include 'waived'
--    Allows the registry to record waivers as a verdict, not just a details blob.
ALTER TABLE task_gate_results DROP CONSTRAINT task_gate_results_verdict_check;
ALTER TABLE task_gate_results ADD CONSTRAINT task_gate_results_verdict_check
    CHECK (verdict IN ('pass', 'fail', 'warn', 'waived'));

-- 2. Critic metadata columns (set at insert time by the registry)
ALTER TABLE task_gate_results ADD COLUMN safety_class BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE task_gate_results ADD COLUMN waivable     BOOLEAN NOT NULL DEFAULT true;
-- INVARIANT (enforced by registry + meta-test): safety_class = true → waivable = false.

-- 3. Rebuild v_gate_status to honour waivers.
--    A required critic is "satisfied" when its latest verdict is 'pass'
--    OR it is 'waived' AND waivable = true.
--    Safety critics (waivable=false) are NEVER satisfied by a 'waived' verdict —
--    they require the escalation path.
CREATE OR REPLACE VIEW v_gate_status AS
WITH latest AS (
    SELECT DISTINCT ON (task_id, critic_name)
           task_id, critic_name, required, verdict, waivable, safety_class
    FROM   task_gate_results
    ORDER  BY task_id, critic_name, created_at DESC
)
SELECT
    t.id        AS task_id,
    t.board_id,
    bool_and(
        l.verdict = 'pass'
        OR (l.verdict = 'waived' AND l.waivable = true)
    ) FILTER (WHERE l.required)                              AS all_required_pass,
    (t.approved_at IS NOT NULL)                              AS human_approved,
    (
        bool_and(
            l.verdict = 'pass'
            OR (l.verdict = 'waived' AND l.waivable = true)
        ) FILTER (WHERE l.required)
        AND t.approved_at IS NOT NULL
    )                                                        AS gate_satisfied
FROM tasks t
LEFT JOIN latest l ON l.task_id = t.id
GROUP BY t.id, t.board_id, t.approved_at;

-- 4. Permanent escalation audit table.
--    Solo-operator escalation (RT-03): operator writes a justification to resolve
--    an escalated safety finding. This record is PERMANENT and NEVER DELETED.
--    A safety critic is satisfied after escalation by inserting a new
--    task_gate_results row with verdict='pass' + details carrying escalation_id.
CREATE TABLE task_gate_escalations (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    board_id        TEXT NOT NULL REFERENCES boards(id),
    task_id         TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    critic_name     TEXT NOT NULL,
    escalated_by    TEXT NOT NULL,          -- human session identity
    justification   TEXT NOT NULL,          -- mandatory; cannot be empty
    escalated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_escalations_task ON task_gate_escalations(task_id);

ALTER TABLE task_gate_escalations ENABLE ROW LEVEL SECURITY;
CREATE POLICY task_gate_escalations_board_isolation ON task_gate_escalations
    USING     (board_id = current_setting('app.board_id', true))
    WITH CHECK (board_id = current_setting('app.board_id', true));
CREATE POLICY task_gate_escalations_admin_bypass ON task_gate_escalations
    USING (current_user = 'talos_admin');
