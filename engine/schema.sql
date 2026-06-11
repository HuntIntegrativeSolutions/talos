-- =============================================================================
-- TALOS — board engine schema (PostgreSQL)
-- =============================================================================
-- Ported from Hermes' SQLite kanban board (NousResearch, MIT) and extended with:
--   * board_id as a first-class HARD-isolation key (+ row-level security)
--   * a real Review gate (deterministic critics + human approval)
--   * the Space Agent layer (spaces / widgets, time-travel versioned)
--
-- Baked-in defaults (easy to change — flagged inline):
--   D1  Tenancy:  board_id + Postgres RLS  (one DB, hard-walled per board)
--                 -- alternative: schema-per-client or database-per-edge.
--   D2  Gate:     separate task_gate_results table + approval columns on tasks
--                 -- a task physically cannot leave 'review' until every required
--                    critic row is 'pass' AND approved_at is set (enforced in the
--                    engine; v_gate_status below is the read model).
--   D3  Time:     timestamptz everywhere (Hermes used epoch ints for SQLite).
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Boards (hard isolation boundary) and tenants (soft grouping within a board)
-- ---------------------------------------------------------------------------
CREATE TABLE boards (
    id          TEXT PRIMARY KEY,            -- e.g. 'acme', 'globex', 'initech', 'his-internal'
    name        TEXT NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Tasks — the core card. (Hermes parity + TALOS gate columns.)
-- ---------------------------------------------------------------------------
CREATE TABLE tasks (
    id                   TEXT PRIMARY KEY,
    board_id             TEXT NOT NULL REFERENCES boards(id),   -- D1 hard isolation
    tenant               TEXT,                                   -- soft grouping
    title                TEXT NOT NULL,
    body                 TEXT,
    assignee             TEXT,                                   -- agent profile
    -- lifecycle: backlog | ready | running | blocked | review | approved
    --            | rejected | done | archived
    status               TEXT NOT NULL DEFAULT 'backlog',
    priority             INTEGER NOT NULL DEFAULT 0,
    created_by           TEXT,
    created_at           timestamptz NOT NULL DEFAULT now(),
    started_at           timestamptz,
    completed_at         timestamptz,

    -- worker / workspace lifecycle (from Hermes)
    workspace_kind       TEXT NOT NULL DEFAULT 'scratch',
    workspace_path       TEXT,
    branch_name          TEXT,
    claim_lock           TEXT,
    claim_expires        timestamptz,
    worker_pid           INTEGER,
    last_heartbeat_at    timestamptz,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,   -- circuit breaker counter
    last_failure_error   TEXT,
    max_runtime_seconds  INTEGER,
    max_retries          INTEGER,
    idempotency_key      TEXT,
    current_run_id       BIGINT,
    result               TEXT,

    -- routing (from Hermes — fits the mothership/edge model directly)
    skills               JSONB,            -- force-loaded skills for this task
    model_override       TEXT,             -- e.g. DeepSeek on the ACME edge vs Opus on the mothership
    goal_mode            BOOLEAN NOT NULL DEFAULT false,  -- Ralph-style judge loop
    goal_max_turns       INTEGER,
    session_id           TEXT,

    -- ----- TALOS review gate (D2) -----
    gate_required        BOOLEAN NOT NULL DEFAULT true,
    approved_by          TEXT,
    approved_at          timestamptz,
    rejected_by          TEXT,
    rejected_at          timestamptz,
    rejection_reason     TEXT
);

CREATE INDEX idx_tasks_board_status   ON tasks(board_id, status);
CREATE INDEX idx_tasks_assignee       ON tasks(board_id, assignee, status);
CREATE INDEX idx_tasks_tenant         ON tasks(board_id, tenant);
CREATE INDEX idx_tasks_idempotency    ON tasks(idempotency_key);
CREATE INDEX idx_tasks_session        ON tasks(session_id);

-- ---------------------------------------------------------------------------
-- Dependency DAG, comments, append-only event log, run history, attachments
-- ---------------------------------------------------------------------------
CREATE TABLE task_links (
    board_id   TEXT NOT NULL REFERENCES boards(id),
    parent_id  TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    child_id   TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    PRIMARY KEY (parent_id, child_id)
);
CREATE INDEX idx_links_child  ON task_links(child_id);
CREATE INDEX idx_links_parent ON task_links(parent_id);

CREATE TABLE task_comments (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    board_id   TEXT NOT NULL REFERENCES boards(id),
    task_id    TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    author     TEXT NOT NULL,            -- human or agent profile
    body       TEXT NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_comments_task ON task_comments(task_id, created_at);

-- The live dashboard tails this table (append-only) over WebSocket.
CREATE TABLE task_events (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    board_id   TEXT NOT NULL REFERENCES boards(id),
    task_id    TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    run_id     BIGINT,
    kind       TEXT NOT NULL,            -- created | claimed | heartbeat | status_change | gate | comment | ...
    payload    JSONB,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_events_task ON task_events(task_id, created_at);
CREATE INDEX idx_events_run  ON task_events(run_id, id);

CREATE TABLE task_runs (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    board_id            TEXT NOT NULL REFERENCES boards(id),
    task_id             TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    profile             TEXT,
    -- status:  running | done | blocked | crashed | timed_out | failed | released
    status              TEXT NOT NULL,
    -- outcome: completed | blocked | crashed | timed_out | spawn_failed | gave_up | reclaimed | null
    outcome             TEXT,
    claim_lock          TEXT,
    claim_expires       timestamptz,
    worker_pid          INTEGER,
    last_heartbeat_at   timestamptz,
    max_runtime_seconds INTEGER,
    started_at          timestamptz NOT NULL DEFAULT now(),
    ended_at            timestamptz
);
CREATE INDEX idx_runs_task   ON task_runs(task_id, started_at);
CREATE INDEX idx_runs_status ON task_runs(status);

CREATE TABLE task_attachments (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    board_id     TEXT NOT NULL REFERENCES boards(id),
    task_id      TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    filename     TEXT NOT NULL,
    stored_path  TEXT NOT NULL,
    content_type TEXT,
    size_bytes   BIGINT NOT NULL DEFAULT 0,
    uploaded_by  TEXT,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_attachments_task ON task_attachments(task_id, created_at);

-- Multi-channel notify subscriptions (the OpenClaw-style proactive notify hook).
CREATE TABLE notify_subs (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    board_id   TEXT NOT NULL REFERENCES boards(id),
    task_id    TEXT REFERENCES tasks(id) ON DELETE CASCADE,
    platform   TEXT NOT NULL,            -- slack | sms | email | webhook | ...
    target     TEXT NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_notify_task ON notify_subs(task_id);

-- =============================================================================
-- REVIEW GATE (D2) — deterministic critics + human approval
-- =============================================================================
-- One row per critic evaluation against a task/run. The engine refuses to move
-- a task out of 'review' unless every REQUIRED critic's latest verdict is 'pass'
-- AND tasks.approved_at is set.
CREATE TABLE task_gate_results (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    board_id     TEXT NOT NULL REFERENCES boards(id),
    task_id      TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    run_id       BIGINT,
    critic_name  TEXT NOT NULL,
    required     BOOLEAN NOT NULL DEFAULT true,
    verdict      TEXT NOT NULL CHECK (verdict IN ('pass', 'fail', 'warn')),
    evidence_uri TEXT,                    -- anchor to the artifact the verdict is grounded in
    details      JSONB,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_gate_task ON task_gate_results(task_id, created_at);

-- Read model: latest verdict per (task, critic), and whether the gate is satisfied.
CREATE VIEW v_gate_status AS
WITH latest AS (
    SELECT DISTINCT ON (task_id, critic_name)
           task_id, critic_name, required, verdict
    FROM   task_gate_results
    ORDER  BY task_id, critic_name, created_at DESC
)
SELECT t.id AS task_id,
       t.board_id,
       bool_and(l.verdict = 'pass') FILTER (WHERE l.required) AS all_required_pass,
       (t.approved_at IS NOT NULL)                            AS human_approved,
       (bool_and(l.verdict = 'pass') FILTER (WHERE l.required)
            AND t.approved_at IS NOT NULL)                    AS gate_satisfied
FROM   tasks t
LEFT   JOIN latest l ON l.task_id = t.id
GROUP  BY t.id, t.board_id, t.approved_at;

-- =============================================================================
-- SPACE AGENT LAYER — the board IS a Space. Time-travel via versions.
-- =============================================================================
-- A "space" is a view/layout the agent can reshape. Widgets are agent-authored
-- panels (tag-trace, dependency map, P&L, etc.). Both ride the SAME gate:
--   proposed -> in_review -> pinned   (or reverted / rejected)
-- IMPORTANT: time-travel versions the *layout/definition*, never task records.
CREATE TABLE spaces (
    id                 TEXT PRIMARY KEY,
    board_id           TEXT NOT NULL REFERENCES boards(id),
    name               TEXT NOT NULL,
    kind               TEXT NOT NULL DEFAULT 'board',   -- board | client | task | report
    status             TEXT NOT NULL DEFAULT 'proposed',
    current_version_id BIGINT,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_spaces_board ON spaces(board_id);

CREATE TABLE space_versions (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    space_id    TEXT NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
    board_id    TEXT NOT NULL REFERENCES boards(id),
    version_no  INTEGER NOT NULL,
    definition  JSONB NOT NULL,          -- the layout/space definition
    source_ref  TEXT,                    -- git sha of the backing definition repo
    author      TEXT,                    -- agent or human
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (space_id, version_no)
);

CREATE TABLE widgets (
    id                 TEXT PRIMARY KEY,
    board_id           TEXT NOT NULL REFERENCES boards(id),
    space_id           TEXT REFERENCES spaces(id) ON DELETE CASCADE,
    task_id            TEXT REFERENCES tasks(id) ON DELETE CASCADE,   -- nullable: board-level widget
    name               TEXT NOT NULL,
    kind               TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'proposed',  -- proposed | in_review | pinned | reverted | rejected
    current_version_id BIGINT,
    created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_widgets_space ON widgets(space_id);
CREATE INDEX idx_widgets_task  ON widgets(task_id);

CREATE TABLE widget_versions (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    widget_id      TEXT NOT NULL REFERENCES widgets(id) ON DELETE CASCADE,
    board_id       TEXT NOT NULL REFERENCES boards(id),
    version_no     INTEGER NOT NULL,
    source         TEXT NOT NULL,        -- the agent-authored JS/JSX (or a git ref)
    sandbox_policy JSONB,                -- CSP / allowed board-API scopes for this widget
    author         TEXT,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (widget_id, version_no)
);

-- =============================================================================
-- ROW-LEVEL SECURITY (D1) — hard board isolation, enforced by the database.
-- =============================================================================
-- The engine sets `SET talos.board_id = '<board>'` per connection/transaction;
-- every query is then transparently scoped to that board. Example for tasks
-- (repeat the pattern for each board-scoped table):
--
--   ALTER TABLE tasks ENABLE ROW LEVEL SECURITY;
--   CREATE POLICY tasks_board_isolation ON tasks
--       USING (board_id = current_setting('talos.board_id', true));
--
-- Edge nodes run single-board and sync only the non-sensitive coordination
-- tables (tasks status, events, comments) up to the mothership.
