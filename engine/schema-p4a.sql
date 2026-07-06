-- TALOS P4a schema additions — board-scoped NEXUS read cache (ADR-035).
--
-- Documentation mirror of V0005_nexus_cache.py — NOT referenced by V0001's
-- _SCHEMA_FILES; V0005 applies this DDL directly. Do not add this file to
-- V0001_baseline.py's file list, or the table would be double-created.

CREATE TABLE nexus_cache (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    board_id    TEXT NOT NULL REFERENCES boards(id),
    tool_name   TEXT NOT NULL,
    params_hash TEXT NOT NULL,
    result_json JSONB NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    UNIQUE (board_id, tool_name, params_hash)
);

ALTER TABLE nexus_cache ENABLE ROW LEVEL SECURITY;
CREATE POLICY nexus_cache_board_isolation ON nexus_cache
    USING     (board_id = current_setting('app.board_id', true))
    WITH CHECK (board_id = current_setting('app.board_id', true));
CREATE POLICY nexus_cache_admin_bypass ON nexus_cache
    USING (current_user = 'talos_admin');
ALTER TABLE nexus_cache FORCE ROW LEVEL SECURITY;
