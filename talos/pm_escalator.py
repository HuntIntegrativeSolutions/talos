"""
P4b milestone-risk escalator (ADR-016 action item #7, completes the
pm_escalate_milestone_risk() TODO in engine/schema-additions.sql).

The SQL trigger (unchanged) logs a 'milestone_risk' task_events row on every
transition to at_risk/missed. This module polls those rows and reacts:
  HIGH (missed)    -> create an issue-task, staged in 'backlog' for human
                      triage (NEVER auto-dispatched to a worker).
  MEDIUM (at_risk) -> create a remediation-task directly in 'ready' (the
                      dispatcher picks it up), tagged so deliverable_node
                      shortens its gate (non-safety critics become
                      advisory); human approval is still mandatory (CR-26).

There is no in-process scheduler in TALOS yet — process_pending_escalations
is a test-invokable function the operator runs via cron/gateway-equivalent
until P8's real gateway exists, mirroring the original TODO comment's own
framing ("the gateway/cron layer can poll task_events for unhandled
escalations").

milestones.status stays trigger-computed (pm_recompute_scheduling /
pm_escalate_milestone_risk in engine/schema-additions.sql) — this module
never writes to milestones directly.
"""
from __future__ import annotations

import json
import uuid

from talos.db import board_scope, get_conn


def process_pending_escalations(board_id: str) -> list[dict]:
    """
    Claim and process every unhandled milestone_risk event for one board.

    Returns a list of {"milestone_id", "severity", "created_task_id", "outcome"}
    dicts, one per event processed this call.

    Claim-before-create: the milestone_escalation_log row is inserted (with a
    placeholder created_task_id) and claimed via UNIQUE(task_event_id) BEFORE
    the issue/remediation task is created, not after — inserting the task
    first and claiming second would let two concurrent callers both pass the
    earlier pending-events read and both create a task, with only the log
    row deduped afterward.
    """
    conn = get_conn()
    results = []
    try:
        with board_scope(conn, board_id) as cur:
            cur.execute(
                """
                SELECT te.id AS event_id, te.payload
                FROM task_events te
                LEFT JOIN milestone_escalation_log mel ON mel.task_event_id = te.id
                WHERE te.board_id = %s AND te.kind = 'milestone_risk'
                  AND mel.id IS NULL
                ORDER BY te.created_at ASC
                """,
                (board_id,),
            )
            pending = cur.fetchall()

            for row in pending:
                payload = row["payload"]
                milestone_id = payload["milestone_id"]
                severity = payload["severity"]

                cur.execute(
                    """
                    INSERT INTO milestone_escalation_log
                        (board_id, task_event_id, milestone_id, severity, created_task_id)
                    VALUES (%s, %s, %s, %s, NULL)
                    ON CONFLICT (task_event_id) DO NOTHING
                    RETURNING id
                    """,
                    (board_id, row["event_id"], milestone_id, severity),
                )
                claimed = cur.fetchone()
                if claimed is None:
                    continue  # another caller already claimed this event

                created_task_id = _create_escalation_task(cur, board_id, milestone_id, payload, severity)
                cur.execute(
                    "UPDATE milestone_escalation_log SET created_task_id = %s WHERE id = %s",
                    (created_task_id, claimed["id"]),
                )

                outcome = "issue_staged" if severity == "HIGH" else "remediation_dispatched"
                results.append({
                    "milestone_id": milestone_id, "severity": severity,
                    "created_task_id": created_task_id, "outcome": outcome,
                })
    finally:
        conn.close()

    for r in results:
        from talos.hooks import default_registry
        default_registry.fire_sync("on_milestone_risk_escalated", {"board_id": board_id, **r})

    return results


def _create_escalation_task(cur, board_id: str, milestone_id: str, payload: dict, severity: str) -> str:
    task_id = f"milestone-esc-{milestone_id}-{uuid.uuid4().hex[:8]}"
    milestone_name = payload.get("milestone_name", milestone_id)
    if severity == "HIGH":
        origin = {"talos_origin": "milestone_issue", "milestone_id": milestone_id, "severity": severity}
        status = "backlog"  # staged for human triage, never auto-dispatched
        title = f"[Milestone MISSED] {milestone_name}"
    else:
        origin = {"talos_origin": "milestone_remediation", "milestone_id": milestone_id, "severity": severity}
        status = "ready"  # auto-dispatched with a shortened gate (see deliverable_node)
        title = f"[Milestone AT RISK] remediation: {milestone_name}"

    cur.execute(
        """
        INSERT INTO tasks (id, board_id, title, body, status, priority)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (task_id, board_id, title, json.dumps(origin), status, 10),
    )
    return task_id
