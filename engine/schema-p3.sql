-- TALOS P3 schema additions.
--
-- Apply after schema.sql + schema-additions.sql + schema-p2.sql.
-- task_runs.last_heartbeat_at and task_runs.max_runtime_seconds already exist
-- in schema.sql — do NOT re-add them here.

-- Board-level model config override (ADR-018).
ALTER TABLE boards ADD COLUMN IF NOT EXISTS model_config JSONB;

-- Observability spans — matches task_events column conventions (ADR-022):
--   board_id / task_id = TEXT (not UUID), run_id = BIGINT (no FK, nullable).
CREATE TABLE IF NOT EXISTS task_spans (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    board_id          TEXT NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
    task_id           TEXT REFERENCES tasks(id) ON DELETE CASCADE,
    run_id            BIGINT,
    parent_span_id    BIGINT REFERENCES task_spans(id),
    span_name         TEXT NOT NULL,
    model_id          TEXT,
    provider          TEXT,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    latency_ms        INTEGER,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at          TIMESTAMPTZ,
    payload           JSONB,
    otlp_exported_at  TIMESTAMPTZ         -- wired in P7 (OTLP exporter)
);

ALTER TABLE task_spans ENABLE ROW LEVEL SECURITY;

CREATE POLICY board_isolation ON task_spans
    USING (board_id = current_setting('app.board_id', true)::TEXT);

CREATE POLICY admin_bypass ON task_spans
    USING (current_user = 'talos_admin');
