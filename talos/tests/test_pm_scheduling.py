"""
PM-layer regression test: guards the app.board_id GUC fix in schema-additions.sql.

Before the fix, v_forward_pass/v_critical_path/pm_recompute_scheduling() used
current_setting('talos.board_id', true) — a GUC that is never set anywhere — causing
all PM views to return 0 rows. This file verifies:

  1. v_critical_path returns correct non-empty rows under a board-scoped session.
  2. DAG chain scheduling propagates: child.earliest_start == root.earliest_finish.
  3. pm_recompute_scheduling() trigger writes tasks.float_hours when the GUC is set.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import psycopg2.extras
import pytest


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def test_pm_critical_path_view_returns_rows_and_correct_scheduling(
    pg_setup, admin_conn, app_conn
):
    """
    v_critical_path must return >= 1 row under a board-scoped app_conn session.

    With the old GUC bug (talos.board_id is never set → NULL), the view returns 0 rows
    because WHERE board_id = NULL matches nothing. After the fix (app.board_id),
    the view returns the seeded tasks with correct DAG-chain scheduling values.
    """
    board_id = _uid("pm-board")
    task_a = _uid("pm-task-a")
    task_b = _uid("pm-task-b")

    # Seed via admin (bypasses RLS). Commit so the separate app_conn can see the rows.
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO boards (id, name) VALUES (%s, %s)",
            (board_id, "PM regression board"),
        )
        cur.execute(
            "INSERT INTO tasks (id, board_id, title, status, estimated_hours)"
            " VALUES (%s, %s, %s, %s, %s)",
            (task_a, board_id, "root task", "ready", 2.0),
        )
        cur.execute(
            "INSERT INTO tasks (id, board_id, title, status, estimated_hours)"
            " VALUES (%s, %s, %s, %s, %s)",
            (task_b, board_id, "child task", "backlog", 3.0),
        )
        cur.execute(
            "INSERT INTO task_links (board_id, parent_id, child_id) VALUES (%s, %s, %s)",
            (board_id, task_a, task_b),
        )
    admin_conn.commit()

    try:
        # Query via app_conn (RLS-enforced talos_app role), scoped to our board.
        # All reads in one transaction so now() = transaction_timestamp() is stable.
        with app_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SET LOCAL app.board_id = %s", (board_id,))
            cur.execute("SELECT * FROM v_critical_path ORDER BY depth")
            rows = cur.fetchall()

        # --- Core regression guard ---
        assert len(rows) >= 1, (
            "v_critical_path returned 0 rows — RLS GUC mismatch bug still present. "
            "Check that schema-additions.sql uses current_setting('app.board_id', true) "
            "not current_setting('talos.board_id', true) in v_forward_pass / backward_leaves."
        )
        assert len(rows) == 2, f"Expected 2 rows (one per task), got {len(rows)}: {rows}"

        by_id = {r["id"]: r for r in rows}
        assert task_a in by_id, f"Root task {task_a!r} missing from v_critical_path"
        assert task_b in by_id, f"Child task {task_b!r} missing from v_critical_path"

        row_a = by_id[task_a]
        row_b = by_id[task_b]

        # DAG chain: child.earliest_start must equal root.earliest_finish.
        # now() is constant (transaction_timestamp()) within one transaction, so exact equality holds.
        assert row_b["earliest_start"] == row_a["earliest_finish"], (
            f"DAG chain not propagating correctly: "
            f"child.earliest_start={row_b['earliest_start']} != "
            f"root.earliest_finish={row_a['earliest_finish']}"
        )

        # Child earliest_finish = child.earliest_start + estimated_hours (3 h).
        expected_b_finish = row_b["earliest_start"] + timedelta(hours=3)
        delta_s = abs((row_b["earliest_finish"] - expected_b_finish).total_seconds())
        assert delta_s < 1, (
            f"Child earliest_finish off by {delta_s:.1f}s: "
            f"got {row_b['earliest_finish']}, expected {expected_b_finish}"
        )
    finally:
        # Remove committed rows so they don't bleed into other tests in the session container.
        with admin_conn.cursor() as cur:
            cur.execute("DELETE FROM task_links WHERE board_id = %s", (board_id,))
            cur.execute("DELETE FROM tasks WHERE board_id = %s", (board_id,))
            cur.execute("DELETE FROM boards WHERE id = %s", (board_id,))
        admin_conn.commit()
        app_conn.rollback()  # reset SET LOCAL


def test_pm_trigger_writes_back_computed_columns(pg_setup, admin_conn):
    """
    pm_recompute_scheduling() reads current_setting('app.board_id', true) when deciding
    which task rows to update (lines 356 and 363 in schema-additions.sql). Guards that
    the trigger writes tasks.float_hours when the GUC is set correctly.

    Uses admin_conn only (single session): seed + SET LOCAL + trigger UPDATE + assert.
    The trigger runs in the same transaction so it sees the uncommitted seed rows.
    Rollback at fixture teardown undoes everything — no explicit cleanup needed.
    """
    board_id = _uid("pm-trig-board")
    task_a = _uid("pm-trig-a")
    task_b = _uid("pm-trig-b")

    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO boards (id, name) VALUES (%s, %s)",
            (board_id, "PM trigger board"),
        )
        cur.execute(
            "INSERT INTO tasks (id, board_id, title, status, estimated_hours)"
            " VALUES (%s, %s, %s, %s, %s)",
            (task_a, board_id, "root task", "ready", 2.0),
        )
        cur.execute(
            "INSERT INTO tasks (id, board_id, title, status, estimated_hours)"
            " VALUES (%s, %s, %s, %s, %s)",
            (task_b, board_id, "child task", "backlog", 3.0),
        )
        cur.execute(
            "INSERT INTO task_links (board_id, parent_id, child_id) VALUES (%s, %s, %s)",
            (board_id, task_a, task_b),
        )

        # Scope the session GUC so pm_recompute_scheduling() can read v_critical_path
        # and filter the UPDATE to this board.
        cur.execute("SET LOCAL app.board_id = %s", (board_id,))

        # Status change fires trg_pm_recompute_scheduling. Transition to 'running'
        # avoids also firing pm_unblock_dependents (which only fires on done/approved).
        cur.execute("UPDATE tasks SET status = 'running' WHERE id = %s", (task_a,))

        cur.execute("SELECT float_hours FROM tasks WHERE id = %s", (task_a,))
        row = cur.fetchone()

    assert row is not None, f"Task {task_a!r} not found after trigger"
    assert row[0] is not None, (
        "tasks.float_hours is NULL after pm_recompute_scheduling() fired — "
        "GUC mismatch bug still present in trigger body. "
        "Check schema-additions.sql lines 356/363 for 'talos.board_id'."
    )
