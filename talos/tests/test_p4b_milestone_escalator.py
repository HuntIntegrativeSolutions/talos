"""
P4b — milestone-risk escalator completion (ADR-016 action item #7 / DoD #5).

Scope confirmed with the user during planning: this completes the ORIGINAL
pm_escalate_milestone_risk() TODO (deadline-risk escalation: HIGH=missed ->
auto-create issue-task staged for human review; MEDIUM=at_risk -> auto-dispatch
remediation task with a shortened gate) -- not a new "gate milestone met on
dependency severity" schema concept, which does not exist.
"""
from __future__ import annotations

import re
import uuid

import psycopg2.extras

from talos.pm_escalator import process_pending_escalations
from talos.worker import claim_and_run


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _seed_board(admin_conn, board_id: str) -> None:
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO boards (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (board_id, f"Board {board_id}"),
        )
    admin_conn.commit()


def _seed_milestone_missed(admin_conn, board_id: str, milestone_id: str, dep_task_id: str) -> None:
    """Seed a milestone whose deadline is already past and whose one dependency
    task is not done — recomputing scheduling will mark it 'missed' (HIGH)."""
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tasks (id, board_id, title, status) VALUES (%s, %s, %s, 'ready')",
            (dep_task_id, board_id, "dependency task"),
        )
        cur.execute(
            """
            INSERT INTO milestones (id, project_id, board_id, name, deadline, depends_on)
            VALUES (%s, %s, %s, %s, now() - interval '1 day', %s)
            """,
            (milestone_id, f"proj-{board_id}", board_id, "Missed milestone", [dep_task_id]),
        )
    admin_conn.commit()

    # Fire pm_recompute_scheduling() via a harmless UPDATE on the dependency task.
    with admin_conn.cursor() as cur:
        cur.execute("SET LOCAL app.board_id = %s", (board_id,))
        cur.execute("UPDATE tasks SET status = 'running' WHERE id = %s", (dep_task_id,))
    admin_conn.commit()


def _seed_milestone_at_risk(admin_conn, board_id: str, milestone_id: str, dep_task_id: str) -> None:
    """Seed a milestone whose deadline is within 48h and whose dependency task
    is not done — recomputing scheduling will mark it 'at_risk' (MEDIUM)."""
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tasks (id, board_id, title, status) VALUES (%s, %s, %s, 'ready')",
            (dep_task_id, board_id, "dependency task"),
        )
        cur.execute(
            """
            INSERT INTO milestones (id, project_id, board_id, name, deadline, depends_on)
            VALUES (%s, %s, %s, %s, now() + interval '10 hours', %s)
            """,
            (milestone_id, f"proj-{board_id}", board_id, "At-risk milestone", [dep_task_id]),
        )
    admin_conn.commit()

    with admin_conn.cursor() as cur:
        cur.execute("SET LOCAL app.board_id = %s", (board_id,))
        cur.execute("UPDATE tasks SET status = 'running' WHERE id = %s", (dep_task_id,))
    admin_conn.commit()


def _get_task(admin_conn, board_id: str, task_id: str) -> dict:
    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM tasks WHERE id = %s AND board_id = %s", (task_id, board_id))
        return dict(cur.fetchone())


# ---------------------------------------------------------------------------
# Existing trigger unchanged (also proves the task_id NOT NULL fix)
# ---------------------------------------------------------------------------

def test_pm_escalate_milestone_risk_trigger_unchanged(pg_setup, admin_conn):
    board_id = _uid("esc-board")
    milestone_id = _uid("milestone")
    dep_task_id = _uid("dep-task")
    _seed_board(admin_conn, board_id)
    _seed_milestone_missed(admin_conn, board_id, milestone_id, dep_task_id)

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM task_events WHERE board_id = %s AND kind = 'milestone_risk'",
            (board_id,),
        )
        rows = cur.fetchall()

    assert len(rows) == 1
    assert rows[0]["task_id"] is None
    assert rows[0]["payload"]["severity"] == "HIGH"
    assert rows[0]["payload"]["milestone_id"] == milestone_id


# ---------------------------------------------------------------------------
# process_pending_escalations — HIGH / MEDIUM / idempotency
# ---------------------------------------------------------------------------

def test_process_pending_escalations_high_creates_staged_issue_task(pg_setup, admin_conn):
    board_id = _uid("esc-board")
    milestone_id = _uid("milestone")
    dep_task_id = _uid("dep-task")
    _seed_board(admin_conn, board_id)
    _seed_milestone_missed(admin_conn, board_id, milestone_id, dep_task_id)

    results = process_pending_escalations(board_id)

    assert len(results) == 1
    assert results[0]["outcome"] == "issue_staged"
    assert results[0]["severity"] == "HIGH"

    task = _get_task(admin_conn, board_id, results[0]["created_task_id"])
    assert task["status"] == "backlog"
    assert '"talos_origin": "milestone_issue"' in task["body"]


def test_process_pending_escalations_medium_creates_ready_remediation_task(pg_setup, admin_conn):
    board_id = _uid("esc-board")
    milestone_id = _uid("milestone")
    dep_task_id = _uid("dep-task")
    _seed_board(admin_conn, board_id)
    _seed_milestone_at_risk(admin_conn, board_id, milestone_id, dep_task_id)

    results = process_pending_escalations(board_id)

    assert len(results) == 1
    assert results[0]["outcome"] == "remediation_dispatched"
    assert results[0]["severity"] == "MEDIUM"

    task = _get_task(admin_conn, board_id, results[0]["created_task_id"])
    assert task["status"] == "ready"
    assert '"talos_origin": "milestone_remediation"' in task["body"]


def test_process_pending_escalations_idempotent_on_repeat_call(pg_setup, admin_conn):
    board_id = _uid("esc-board")
    milestone_id = _uid("milestone")
    dep_task_id = _uid("dep-task")
    _seed_board(admin_conn, board_id)
    _seed_milestone_missed(admin_conn, board_id, milestone_id, dep_task_id)

    first = process_pending_escalations(board_id)
    second = process_pending_escalations(board_id)

    assert len(first) == 1
    assert second == []

    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM tasks WHERE board_id = %s AND title LIKE '%%Milestone%%'",
            (board_id,),
        )
        assert cur.fetchone()[0] == 1


# ---------------------------------------------------------------------------
# Shortened gate — remediation-origin tasks get non-safety critics downgraded
# ---------------------------------------------------------------------------

def test_remediation_task_gets_shortened_gate_non_safety_critics_advisory(pg_setup, admin_conn, monkeypatch):
    monkeypatch.setenv("TALOS_NEXUS_STUB", "1")
    board_id = _uid("esc-board")
    milestone_id = _uid("milestone")
    dep_task_id = _uid("dep-task")
    _seed_board(admin_conn, board_id)
    _seed_milestone_at_risk(admin_conn, board_id, milestone_id, dep_task_id)

    results = process_pending_escalations(board_id)
    remediation_task_id = results[0]["created_task_id"]

    claim_and_run(board_id, remediation_task_id)

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT critic_name, required FROM task_gate_results WHERE task_id = %s",
            (remediation_task_id,),
        )
        rows = {r["critic_name"]: r["required"] for r in cur.fetchall()}
        cur.execute(
            "SELECT all_required_pass FROM v_gate_status WHERE task_id = %s",
            (remediation_task_id,),
        )
        gate_status = cur.fetchone()

    assert rows["citations_resolvable"] is False
    assert rows["no_live_write_in_deliverable"] is True
    assert gate_status["all_required_pass"] is True


def test_issue_task_still_requires_full_gate_when_eventually_run(pg_setup, admin_conn, monkeypatch):
    monkeypatch.setenv("TALOS_NEXUS_STUB", "1")
    board_id = _uid("esc-board")
    milestone_id = _uid("milestone")
    dep_task_id = _uid("dep-task")
    _seed_board(admin_conn, board_id)
    _seed_milestone_missed(admin_conn, board_id, milestone_id, dep_task_id)

    results = process_pending_escalations(board_id)
    issue_task_id = results[0]["created_task_id"]

    # Issue-tasks start in 'backlog' (staged, never auto-dispatched); move it to
    # 'ready' manually to prove the downgrade never applies to this origin type.
    with admin_conn.cursor() as cur:
        cur.execute("UPDATE tasks SET status = 'ready' WHERE id = %s", (issue_task_id,))
    admin_conn.commit()

    claim_and_run(board_id, issue_task_id)

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT critic_name, required FROM task_gate_results WHERE task_id = %s",
            (issue_task_id,),
        )
        rows = {r["critic_name"]: r["required"] for r in cur.fetchall()}

    assert rows["citations_resolvable"] is True
    assert rows["no_live_write_in_deliverable"] is True


# ---------------------------------------------------------------------------
# Hook firing
# ---------------------------------------------------------------------------

def test_on_milestone_risk_escalated_hook_fires_with_expected_payload(pg_setup, admin_conn):
    from talos.hooks import default_registry

    board_id = _uid("esc-board")
    milestone_id = _uid("milestone")
    dep_task_id = _uid("dep-task")
    _seed_board(admin_conn, board_id)
    _seed_milestone_missed(admin_conn, board_id, milestone_id, dep_task_id)

    captured = []

    async def _capture(payload):
        captured.append(payload)

    default_registry.register("on_milestone_risk_escalated", _capture)
    try:
        process_pending_escalations(board_id)
    finally:
        default_registry._hooks["on_milestone_risk_escalated"].remove(_capture)

    assert len(captured) == 1
    assert captured[0]["board_id"] == board_id
    assert captured[0]["milestone_id"] == milestone_id
    assert captured[0]["severity"] == "HIGH"
    assert captured[0]["outcome"] == "issue_staged"
    assert "created_task_id" in captured[0]


# ---------------------------------------------------------------------------
# Gate GET surfaces the escalation origin
# ---------------------------------------------------------------------------

def test_gate_status_surfaces_milestone_escalation_origin(pg_setup, admin_conn, human_jwt):
    from fastapi.testclient import TestClient
    from talos.api import app

    board_id = _uid("esc-board")
    milestone_id = _uid("milestone")
    dep_task_id = _uid("dep-task")
    _seed_board(admin_conn, board_id)
    _seed_milestone_at_risk(admin_conn, board_id, milestone_id, dep_task_id)

    results = process_pending_escalations(board_id)
    remediation_task_id = results[0]["created_task_id"]

    # Move it into review so the gate GET has something to show.
    with admin_conn.cursor() as cur:
        cur.execute(
            "UPDATE tasks SET status = 'review' WHERE id = %s AND board_id = %s",
            (remediation_task_id, board_id),
        )
    admin_conn.commit()

    client = TestClient(app)
    resp = client.get(
        f"/boards/{board_id}/tasks/{remediation_task_id}/gate",
        headers={"X-Human-Session": human_jwt},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["milestone_escalation_origin"]["talos_origin"] == "milestone_remediation"

    # An ordinary task has no escalation origin.
    plain_task_id = _uid("plain-task")
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tasks (id, board_id, title, status) VALUES (%s, %s, %s, 'review')",
            (plain_task_id, board_id, "plain task"),
        )
    admin_conn.commit()
    resp2 = client.get(
        f"/boards/{board_id}/tasks/{plain_task_id}/gate",
        headers={"X-Human-Session": human_jwt},
    )
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["milestone_escalation_origin"] is None


# ---------------------------------------------------------------------------
# Grep regression: milestones.status stays trigger-computed
# ---------------------------------------------------------------------------

def test_no_new_code_sets_milestones_status_directly():
    import pathlib
    repo_root = pathlib.Path(__file__).resolve().parent.parent.parent
    pattern = re.compile(r"UPDATE\s+milestones\s+SET\s+status", re.IGNORECASE)
    hits = []
    for path in (repo_root / "talos").rglob("*.py"):
        if "/tests/" in str(path):
            continue
        text = path.read_text()
        if pattern.search(text):
            hits.append(str(path))
    assert hits == [], f"Found direct milestones.status writes outside the trigger: {hits}"


# ---------------------------------------------------------------------------
# RLS
# ---------------------------------------------------------------------------

def test_milestone_escalation_log_rls_cross_board_isolation(pg_setup, admin_conn, app_conn):
    board_a = _uid("esc-board-a")
    board_b = _uid("esc-board-b")
    milestone_id = _uid("milestone")
    dep_task_id = _uid("dep-task")
    _seed_board(admin_conn, board_a)
    _seed_board(admin_conn, board_b)
    _seed_milestone_missed(admin_conn, board_a, milestone_id, dep_task_id)
    process_pending_escalations(board_a)

    with app_conn.cursor() as cur:
        cur.execute("SET LOCAL app.board_id = %s", (board_b,))
        cur.execute("SELECT COUNT(*) FROM milestone_escalation_log")
        assert cur.fetchone()[0] == 0
