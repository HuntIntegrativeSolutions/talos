"""
TALOS P2 spine graph.

Five outcomes handled by gate_node → conditional edge → deliverable_node or post_gate_node:
  read_node → deliverable_node → gate_node ──edit──→ deliverable_node (loop)
                                            └─other─→ post_gate_node → END

gate_node contains only interrupt(). All stateful side-effects live in
post_gate_node so a LangGraph resume cannot double-fire them (ADR-011, CR-12).
"""

from __future__ import annotations

import json
import os
from typing import TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from talos.critics.registry import run_all as run_all_critics
from talos.db import board_scope, get_conn


class SpineState(TypedDict):
    board_id: str
    task_id: str
    attempt_no: int
    run_id: int          # task_runs.id BIGINT — set by worker at claim time
    session_key: str     # "task:{board_id}:{task_id}:{attempt_no}"
    nexus_result: dict
    deliverable: dict
    critic_results: list  # list of critic verdict dicts (JSON-serializable for checkpoint)
    gate_outcome: str | None
    approved_by: str | None
    edited_deliverable: dict | None   # set when outcome='edit'; deliverable_node uses on re-entry
    gate_justification: str | None    # set for waive/escalate; mandatory for those outcomes


# ---------------------------------------------------------------------------
# Node 1: read_node
# ---------------------------------------------------------------------------

def read_node(state: SpineState) -> dict:
    """
    Fetch tag context from NEXUS (or stub). No Postgres writes — reference data only.
    ADR-003/007: do not persist reference data to task_events or task_gate_results.
    """
    if os.environ.get("TALOS_NEXUS_STUB") == "1":
        nexus_result = {"tag": "MOCK_TAG", "status": "confirmed"}
    else:
        raise NotImplementedError(
            "Live NEXUS MCP not yet wired. Set TALOS_NEXUS_STUB=1 to run in CI."
        )
    return {"nexus_result": nexus_result}


# ---------------------------------------------------------------------------
# Node 2: deliverable_node
# ---------------------------------------------------------------------------

def deliverable_node(state: SpineState) -> dict:
    """
    Build (or accept an edited) deliverable, run all registered critics via the
    registry, persist one task_gate_results row per critic, and move the task to review.

    On re-entry via the edit outcome, state["edited_deliverable"] carries the human's
    revised deliverable — critics re-run against it without a NEXUS re-read.
    """
    is_edit = state.get("edited_deliverable") is not None
    if is_edit:
        deliverable = state["edited_deliverable"]
    else:
        deliverable = {
            "citations": [
                {
                    "finding_id": state["nexus_result"].get("tag", "unknown"),
                    "status": state["nexus_result"].get("status", "proposed"),
                }
            ],
            "summary": f"Tag context retrieved: {state['nexus_result']}",
        }

    verdicts = run_all_critics(deliverable, nexus_client=None)

    conn = get_conn()
    try:
        with board_scope(conn, state["board_id"]) as cur:
            for v in verdicts:
                cur.execute(
                    """
                    INSERT INTO task_gate_results
                        (board_id, task_id, run_id, critic_name, required,
                         verdict, safety_class, waivable, details)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        state["board_id"],
                        state["task_id"],
                        state["run_id"],
                        v["name"],
                        v["required"],
                        v["verdict"],
                        v["safety_class"],
                        v["waivable"],
                        json.dumps(v),
                    ),
                )

            if is_edit:
                # Record the edit event in the audit log.
                cur.execute(
                    """
                    INSERT INTO task_events (board_id, task_id, run_id, kind, payload)
                    VALUES (%s, %s, %s, 'gate_edit', %s::jsonb)
                    """,
                    (
                        state["board_id"],
                        state["task_id"],
                        state["run_id"],
                        json.dumps({"approved_by": state.get("approved_by")}),
                    ),
                )

            cur.execute(
                "UPDATE tasks SET status = 'review' WHERE id = %s AND board_id = %s",
                (state["task_id"], state["board_id"]),
            )
    finally:
        conn.close()

    return {
        "deliverable": deliverable,
        "critic_results": verdicts,
        # Clear the edited_deliverable so a subsequent re-entry check is clean.
        "edited_deliverable": None,
    }


# ---------------------------------------------------------------------------
# Node 3: gate_node — PURE; contains only interrupt()
# ---------------------------------------------------------------------------

def gate_node(state: SpineState) -> dict:
    """
    Human review gate. Contains ONLY interrupt().

    Any code placed before interrupt() will execute twice: once on first
    invocation (when the graph pauses) and again on resume (when LangGraph
    re-runs the node from line 1). All side-effects belong in post_gate_node
    or deliverable_node.

    On resume, interrupt() returns the Command.resume value from the gate API:
        {"outcome": "approve"|"reject"|"waive"|"edit"|"escalate",
         "approved_by": "<session>",
         "reason": "...",           # reject
         "justification": "...",   # waive | escalate
         "new_deliverable": {...}}  # edit
    """
    outcome = interrupt(
        {
            "task_id": state["task_id"],
            "deliverable": state["deliverable"],
            "critic_results": state["critic_results"],
        }
    )
    return {
        "gate_outcome": outcome["outcome"],
        "approved_by": outcome.get("approved_by"),
        "gate_justification": outcome.get("justification") or outcome.get("reason"),
        "edited_deliverable": outcome.get("new_deliverable"),
    }


# ---------------------------------------------------------------------------
# Node 4: post_gate_node — idempotent; all side-effects in one transaction
# ---------------------------------------------------------------------------

def post_gate_node(state: SpineState) -> dict:
    """
    Persist the gate outcome for approve / reject / waive / escalate.
    (edit never reaches this node — it loops back to deliverable_node.)

    Idempotency guard: if task status is already 'approved' or 'rejected',
    this node already ran — return early without writing.
    """
    outcome = state["gate_outcome"]
    approved_by = state["approved_by"]
    justification = state.get("gate_justification")

    conn = get_conn()
    try:
        with board_scope(conn, state["board_id"]) as cur:
            # Idempotency guard — covers approve (approved), reject (rejected),
            # waive (approved), and escalate (approved).
            cur.execute(
                "SELECT status FROM tasks WHERE id = %s AND board_id = %s",
                (state["task_id"], state["board_id"]),
            )
            row = cur.fetchone()
            if row and row["status"] in ("approved", "rejected"):
                return {}  # already ran — idempotent no-op

            if outcome == "approve":
                cur.execute(
                    """
                    INSERT INTO task_events (board_id, task_id, run_id, kind, payload)
                    VALUES (%s, %s, %s, 'gate_outcome', %s::jsonb)
                    """,
                    (
                        state["board_id"], state["task_id"], state["run_id"],
                        json.dumps({"outcome": outcome, "approved_by": approved_by}),
                    ),
                )
                cur.execute(
                    """
                    UPDATE tasks
                    SET status = 'approved', approved_at = NOW(), approved_by = %s
                    WHERE id = %s AND board_id = %s
                    """,
                    (approved_by, state["task_id"], state["board_id"]),
                )

            elif outcome == "reject":
                cur.execute(
                    """
                    INSERT INTO task_events (board_id, task_id, run_id, kind, payload)
                    VALUES (%s, %s, %s, 'gate_outcome', %s::jsonb)
                    """,
                    (
                        state["board_id"], state["task_id"], state["run_id"],
                        json.dumps({
                            "outcome": outcome,
                            "rejected_by": approved_by,
                            "reason": justification,
                        }),
                    ),
                )
                cur.execute(
                    """
                    UPDATE tasks
                    SET status = 'rejected',
                        rejected_at = NOW(),
                        rejected_by = %s,
                        rejection_reason = %s
                    WHERE id = %s AND board_id = %s
                    """,
                    (approved_by, justification, state["task_id"], state["board_id"]),
                )

            elif outcome == "waive":
                # Insert a waived verdict row for each failing required critic that is waivable.
                cur.execute(
                    """
                    SELECT DISTINCT ON (critic_name) critic_name, waivable, safety_class
                    FROM task_gate_results
                    WHERE task_id = %s AND board_id = %s AND required = true AND verdict = 'fail'
                    ORDER BY critic_name, created_at DESC
                    """,
                    (state["task_id"], state["board_id"]),
                )
                failing = cur.fetchall()
                for row in failing:
                    cur.execute(
                        """
                        INSERT INTO task_gate_results
                            (board_id, task_id, run_id, critic_name, required,
                             verdict, safety_class, waivable, details)
                        VALUES (%s, %s, %s, %s, true, 'waived', %s, %s, %s::jsonb)
                        """,
                        (
                            state["board_id"], state["task_id"], state["run_id"],
                            row["critic_name"],
                            row["safety_class"],
                            row["waivable"],
                            json.dumps({
                                "waived_by": approved_by,
                                "justification": justification,
                            }),
                        ),
                    )
                cur.execute(
                    """
                    INSERT INTO task_events (board_id, task_id, run_id, kind, payload)
                    VALUES (%s, %s, %s, 'gate_waiver', %s::jsonb)
                    """,
                    (
                        state["board_id"], state["task_id"], state["run_id"],
                        json.dumps({"waived_by": approved_by, "justification": justification}),
                    ),
                )
                cur.execute(
                    """
                    UPDATE tasks
                    SET status = 'approved', approved_at = NOW(), approved_by = %s
                    WHERE id = %s AND board_id = %s
                    """,
                    (approved_by, state["task_id"], state["board_id"]),
                )

            elif outcome == "escalate":
                # 1. Insert permanent escalation record for each blocking safety critic.
                cur.execute(
                    """
                    SELECT DISTINCT ON (critic_name) critic_name
                    FROM task_gate_results
                    WHERE task_id = %s AND board_id = %s
                      AND required = true AND safety_class = true AND verdict = 'fail'
                    ORDER BY critic_name, created_at DESC
                    """,
                    (state["task_id"], state["board_id"]),
                )
                safety_failures = [r["critic_name"] for r in cur.fetchall()]

                # If no safety failures exist, escalate acts like approve but logs the escalation.
                escalation_ids = []
                for critic_name in (safety_failures or ["_general"]):
                    cur.execute(
                        """
                        INSERT INTO task_gate_escalations
                            (board_id, task_id, critic_name, escalated_by, justification)
                        VALUES (%s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (
                            state["board_id"], state["task_id"],
                            critic_name, approved_by, justification,
                        ),
                    )
                    escalation_ids.append(cur.fetchone()["id"])

                # 2. Insert synthetic pass rows for each safety critic override.
                for i, critic_name in enumerate(safety_failures):
                    cur.execute(
                        """
                        INSERT INTO task_gate_results
                            (board_id, task_id, run_id, critic_name, required,
                             verdict, safety_class, waivable, details)
                        VALUES (%s, %s, %s, %s, true, 'pass', true, false, %s::jsonb)
                        """,
                        (
                            state["board_id"], state["task_id"], state["run_id"],
                            critic_name,
                            json.dumps({
                                "escalated": True,
                                "escalation_id": escalation_ids[i] if i < len(escalation_ids) else None,
                                "justification": justification,
                            }),
                        ),
                    )

                cur.execute(
                    """
                    INSERT INTO task_events (board_id, task_id, run_id, kind, payload)
                    VALUES (%s, %s, %s, 'gate_escalation', %s::jsonb)
                    """,
                    (
                        state["board_id"], state["task_id"], state["run_id"],
                        json.dumps({
                            "escalated_by": approved_by,
                            "justification": justification,
                            "escalation_ids": escalation_ids,
                        }),
                    ),
                )
                cur.execute(
                    """
                    UPDATE tasks
                    SET status = 'approved', approved_at = NOW(), approved_by = %s
                    WHERE id = %s AND board_id = %s
                    """,
                    (approved_by, state["task_id"], state["board_id"]),
                )

    finally:
        conn.close()

    return {}


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def _route_after_gate(state: SpineState) -> str:
    if state.get("gate_outcome") == "edit":
        return "deliverable_node"
    return "post_gate_node"


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_graph(checkpointer=None):
    """
    Build and compile the spine graph.

    Pass checkpointer=MemorySaver() in tests.
    Pass checkpointer=PostgresSaver(...) in production.

    No interrupt_before — interrupt() inside gate_node is the sole pause point.
    Using interrupt_before alongside interrupt() would cause a double-pause on resume.
    """
    if checkpointer is None:
        checkpointer = MemorySaver()

    builder = StateGraph(SpineState)
    builder.add_node("read_node", read_node)
    builder.add_node("deliverable_node", deliverable_node)
    builder.add_node("gate_node", gate_node)
    builder.add_node("post_gate_node", post_gate_node)

    builder.add_edge(START, "read_node")
    builder.add_edge("read_node", "deliverable_node")
    builder.add_edge("deliverable_node", "gate_node")
    builder.add_conditional_edges("gate_node", _route_after_gate)
    builder.add_edge("post_gate_node", END)

    return builder.compile(checkpointer=checkpointer)
