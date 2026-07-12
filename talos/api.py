"""
TALOS P1 board API.

Implements exactly the five read endpoints and the gate-outcome endpoint
specified in docs/contracts/board-api.md. Nothing more.

Gate POST enforces three doctrine rules:
  RT-01: approved_by is set from the JWT sub claim in X-Human-Session; the
         header must carry a validated TALOS JWT (token_class="human"), never
         a plain string. (ADR-036)
  RT-02: critics must pass (all_required_pass=true) before a human can approve.
  RT-03: approved_at is set exclusively by post_gate_node, never by the API.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

import jwt as _jwt
import psycopg2
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from talos.db import board_scope, get_conn


@asynccontextmanager
async def _lifespan(app):
    if not os.environ.get("TALOS_JWT_SECRET"):
        raise RuntimeError(
            "TALOS_JWT_SECRET is required. Set it before starting the server."
        )
    # post_gate_node (and its on_task_approved hook fire) only ever runs here,
    # inside the API process — submit_gate_outcome() resumes the graph past
    # the gate interrupt via Command(resume=...); worker.py's graph.invoke()
    # only ever runs the pre-interrupt portion of the spine (P4a).
    from talos.memory import get_store
    get_store().register_ingest_hook()
    from talos.rule_promotion import register_rule_promotion_hook
    register_rule_promotion_hook()
    from talos.crystallize import register_crystallize_hooks
    register_crystallize_hooks()
    yield


app = FastAPI(title="TALOS Board API", lifespan=_lifespan)

# The compiled LangGraph graph. Tests inject a MemorySaver-backed instance;
# production wires in a PostgresSaver-backed one via set_graph().
_graph = None


def set_graph(g) -> None:
    global _graph
    _graph = g


def _get_graph():
    if _graph is None:
        from talos.graph.spine import build_graph
        set_graph(build_graph())
    return _graph


# ---------------------------------------------------------------------------
# Task status enum (from schema.sql lines 38-40)
# ---------------------------------------------------------------------------

VALID_STATUSES = {
    "backlog", "ready", "running", "blocked", "review",
    "approved", "rejected", "done", "archived",
}

# SEC-01: approved/rejected/done are gate-owned terminal states.
# They must only be written by post_gate_node (which sets approved_at/rejected_at)
# or a future authenticated done-transition — never by an unauthenticated PATCH.
# archived is administrative (not a DAG-dispatch trigger) and remains PATCH-settable.
PATCH_ALLOWED_STATUSES = VALID_STATUSES - {"approved", "rejected", "done"}

# Columns the view must never expose (board-api.md §1 — least-privilege projection)
_HIDDEN = {
    "claim_lock", "worker_pid", "session_id",
    "idempotency_key", "model_override", "last_failure_error",
}


def _mask_task(row: dict) -> dict:
    return {k: v for k, v in row.items() if k not in _HIDDEN}


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class CreateBoardRequest(BaseModel):
    id: str
    name: str


class CreateTaskRequest(BaseModel):
    id: str
    title: str
    body: str | None = None
    assignee: str | None = None
    priority: int = 0


class PatchStatusRequest(BaseModel):
    status: str


class LoginRequest(BaseModel):
    username: str
    password: str


class GateOutcomeRequest(BaseModel):
    outcome: str
    reason: str | None = None         # required for reject
    justification: str | None = None  # required for waive and escalate
    new_deliverable: dict | None = None  # required for edit
    # NOTE: approved_by is absent — it is extracted from the JWT sub claim
    # in X-Human-Session, never from the request body. (ADR-036)


class PatchSlaRequest(BaseModel):
    sla_minutes: int | None = None


# ---------------------------------------------------------------------------
# Auth dependency (ADR-036) — shared by the gate-outcome write and the
# gate-UI read endpoints below.
# ---------------------------------------------------------------------------

def require_human_session(
    x_human_session: str | None = Header(default=None, alias="X-Human-Session"),
) -> dict:
    """Validate X-Human-Session per ADR-036. Returns the decoded JWT claims.

    Raises HTTP 403 with the frozen board-api.md error shape on any failure:
    missing header, invalid/expired signature, or token_class != "human".
    """
    if not x_human_session:
        raise HTTPException(status_code=403, detail={"error": "human session required"})
    try:
        from talos.auth.tokens import validate_token
        claims = validate_token(x_human_session)
    except _jwt.PyJWTError:
        raise HTTPException(status_code=403, detail={"error": "human session required"})
    if claims.get("token_class") != "human":
        raise HTTPException(status_code=403, detail={"error": "human session required"})
    return claims


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/auth/login")
def auth_login(req: LoginRequest) -> dict:
    from talos.auth.tokens import issue_token
    try:
        return {"token": issue_token(req.username, req.password)}
    except ValueError:
        raise HTTPException(status_code=401, detail="invalid credentials")


@app.post("/boards", status_code=201)
def create_board(req: CreateBoardRequest) -> dict:
    # boards has no RLS policy — no board_scope needed here.
    conn = get_conn()
    try:
        conn.autocommit = False
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO boards (id, name) VALUES (%s, %s) RETURNING id, name",
                (req.id, req.name),
            )
            row = dict(cur.fetchone())
        conn.commit()
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        raise HTTPException(status_code=409, detail="board already exists")
    finally:
        conn.close()
    return row


@app.post("/boards/{board_id}/tasks", status_code=201)
def create_task(board_id: str, req: CreateTaskRequest) -> dict[str, Any]:
    conn = get_conn()
    try:
        with board_scope(conn, board_id) as cur:
            cur.execute(
                """
                INSERT INTO tasks (id, board_id, title, body, assignee, priority, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'ready')
                RETURNING id, board_id, title, status, priority, created_at
                """,
                (req.id, board_id, req.title, req.body, req.assignee, req.priority),
            )
            row = dict(cur.fetchone())
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail="task already exists")
    finally:
        conn.close()
    return row


class PromoteRuleRequest(BaseModel):
    rule_type: str          # 'factual' | 'procedural' | 'project_context' (ADR-023)
    content: str
    source_task_id: str | None = None


_RULE_TYPES = ("factual", "procedural", "project_context")


@app.post("/boards/{board_id}/promote_rule", status_code=201)
def promote_rule(
    board_id: str, req: PromoteRuleRequest, claims: dict = Depends(require_human_session),
) -> dict[str, Any]:
    """
    ADR-005: promotion to [shared] passes ONE gate, regardless of artifact
    type. This endpoint does NOT promote anything immediately — it creates a
    `rules` row (client_scope='client', status='pending_review') and a
    promotion task that flows through the identical gate/critic/human-approval
    pipeline as any other task (talos.graph.spine.deliverable_node builds its
    deliverable from the rule content; talos.critics.no_client_identifiers_in_shared
    (RT-06) is required+non-waivable for it). Only a subsequent `approve`
    outcome flips client_scope to 'shared' (talos.rule_promotion).
    """
    import json
    import uuid

    if req.rule_type not in _RULE_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"rule_type must be one of {_RULE_TYPES}",
        )

    rule_id = f"rule-{uuid.uuid4().hex[:12]}"
    promotion_task_id = f"promote-{rule_id}"

    conn = get_conn()
    try:
        with board_scope(conn, board_id) as cur:
            # Task must exist before `rules.promotion_task_id` can reference it (FK).
            origin_body = json.dumps({
                "talos_origin": "rule_promotion",
                "rule_id": rule_id,
                "rule_type": req.rule_type,
                "source_task_id": req.source_task_id,
            })
            cur.execute(
                """
                INSERT INTO tasks (id, board_id, title, body, status, priority)
                VALUES (%s, %s, %s, %s, 'ready', 5)
                """,
                (promotion_task_id, board_id, f"Promote rule {rule_id} to [shared]", origin_body),
            )
            cur.execute(
                """
                INSERT INTO rules (id, board_id, rule_type, content, client_scope,
                                    source_task_id, promotion_task_id, status)
                VALUES (%s, %s, %s, %s, 'client', %s, %s, 'pending_review')
                """,
                (rule_id, board_id, req.rule_type, req.content, req.source_task_id, promotion_task_id),
            )
    finally:
        conn.close()

    return {"rule_id": rule_id, "promotion_task_id": promotion_task_id, "status": "pending_review"}


@app.get("/boards/{board_id}/tasks/{task_id}")
def get_task(board_id: str, task_id: str) -> dict[str, Any]:
    conn = get_conn()
    try:
        with board_scope(conn, board_id) as cur:
            cur.execute(
                "SELECT * FROM tasks WHERE id = %s AND board_id = %s",
                (task_id, board_id),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="task not found")
    return _mask_task(dict(row))


@app.patch("/boards/{board_id}/tasks/{task_id}/status")
def patch_task_status(
    board_id: str, task_id: str, req: PatchStatusRequest
) -> dict[str, Any]:
    if req.status not in PATCH_ALLOWED_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"status must be one of {sorted(PATCH_ALLOWED_STATUSES)}; "
                "approved/rejected/done are gate-only states (use POST /gate)"
            ),
        )
    conn = get_conn()
    try:
        with board_scope(conn, board_id) as cur:
            cur.execute(
                "UPDATE tasks SET status = %s WHERE id = %s AND board_id = %s "
                "RETURNING id, status",
                (req.status, task_id, board_id),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="task not found")
    return dict(row)


@app.get("/boards/{board_id}/tasks/{task_id}/gate")
def get_gate_status(board_id: str, task_id: str) -> dict[str, Any]:
    conn = get_conn()
    try:
        with board_scope(conn, board_id) as cur:
            # v_gate_status is in schema.sql and filtered by RLS on underlying tables.
            cur.execute(
                "SELECT * FROM v_gate_status WHERE task_id = %s AND board_id = %s",
                (task_id, board_id),
            )
            gate_row = cur.fetchone()
            # Also return the per-critic detail rows for the cockpit.
            cur.execute(
                """
                SELECT critic_name, required, verdict, evidence_uri, details,
                       waivable, safety_class, created_at
                FROM task_gate_results
                WHERE task_id = %s AND board_id = %s
                ORDER BY created_at DESC
                """,
                (task_id, board_id),
            )
            critics = [dict(r) for r in cur.fetchall()]
            # The deliverable the human reviews (P7a). Null for tasks that
            # entered review via a worker error-escalation path rather than
            # deliverable_node (no deliverable was ever produced).
            cur.execute(
                "SELECT deliverable, body FROM tasks WHERE id = %s AND board_id = %s",
                (task_id, board_id),
            )
            deliverable_row = cur.fetchone()
            # Board-scoped NEXUS read cache staleness (ADR-035/P4a). nexus_cache
            # has no task_id column (matches the ADR's own DDL) — this is every
            # non-expired cache entry for the board, not scoped to this task's run.
            cur.execute(
                """
                SELECT tool_name, fetched_at,
                       EXTRACT(EPOCH FROM (now() - fetched_at))::bigint AS nexus_cache_age_seconds
                FROM nexus_cache
                WHERE board_id = %s AND expires_at > now()
                ORDER BY fetched_at DESC
                """,
                (board_id,),
            )
            nexus_results_freshness = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    if gate_row is None:
        raise HTTPException(status_code=404, detail="task not found")
    from talos.task_origin import parse_origin

    origin = parse_origin(deliverable_row["body"]) if deliverable_row else None
    is_milestone_origin = bool(origin) and origin.get("talos_origin") in (
        "milestone_issue", "milestone_remediation",
    )

    result = dict(gate_row)
    result["critics"] = critics
    result["deliverable"] = deliverable_row["deliverable"] if deliverable_row else None
    result["nexus_results_freshness"] = nexus_results_freshness
    result["milestone_escalation_origin"] = origin if is_milestone_origin else None
    return result


@app.post("/boards/{board_id}/nexus_cache/invalidate")
def invalidate_nexus_cache(
    board_id: str, tool_name: str, claims: dict = Depends(require_human_session),
) -> dict[str, Any]:
    """Force re-fetch of a cached NEXUS tool result (ADR-035/P4a)."""
    from talos.nexus_cache import invalidate

    count = invalidate(board_id, tool_name)
    return {"board_id": board_id, "tool_name": tool_name, "invalidated": count}


@app.get("/boards/{board_id}/review-queue")
def get_review_queue(
    board_id: str, claims: dict = Depends(require_human_session),
) -> dict[str, Any]:
    conn = get_conn()
    try:
        with board_scope(conn, board_id) as cur:
            cur.execute(
                """
                SELECT id AS task_id, title, assignee, review_entered_at,
                       EXTRACT(EPOCH FROM (NOW() - review_entered_at))::bigint
                           AS seconds_in_review
                FROM tasks
                WHERE board_id = %s AND status = 'review'
                ORDER BY review_entered_at ASC NULLS LAST
                """,
                (board_id,),
            )
            tasks = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    # boards carries no RLS policy — plain read, same precedent as create_board().
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT sla_minutes FROM boards WHERE id = %s", (board_id,))
            board_row = cur.fetchone()
    finally:
        conn.close()
    if board_row is None:
        raise HTTPException(status_code=404, detail="board not found")
    sla_minutes = board_row["sla_minutes"]
    for t in tasks:
        t["overdue"] = bool(
            sla_minutes is not None
            and t["seconds_in_review"] is not None
            and t["seconds_in_review"] / 60.0 > sla_minutes
        )
    return {"board_id": board_id, "sla_minutes": sla_minutes, "tasks": tasks}


@app.get("/boards/{board_id}/sla")
def get_board_sla(
    board_id: str, claims: dict = Depends(require_human_session),
) -> dict[str, Any]:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, sla_minutes FROM boards WHERE id = %s", (board_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="board not found")
    return {"board_id": row["id"], "sla_minutes": row["sla_minutes"]}


@app.patch("/boards/{board_id}/sla")
def patch_board_sla(
    board_id: str, req: PatchSlaRequest, claims: dict = Depends(require_human_session),
) -> dict[str, Any]:
    conn = get_conn()
    try:
        conn.autocommit = False
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "UPDATE boards SET sla_minutes = %s WHERE id = %s "
                "RETURNING id, sla_minutes",
                (req.sla_minutes, board_id),
            )
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="board not found")
    return {"board_id": row["id"], "sla_minutes": row["sla_minutes"]}


@app.post("/boards/{board_id}/tasks/{task_id}/gate")
def submit_gate_outcome(
    board_id: str,
    task_id: str,
    req: GateOutcomeRequest,
    claims: dict = Depends(require_human_session),
) -> dict[str, Any]:
    # RT-01: approved_by = JWT sub claim, never client-supplied. (ADR-036)
    approved_by = claims["sub"]

    _VALID_OUTCOMES = {"approve", "reject", "waive", "edit", "escalate"}
    if req.outcome not in _VALID_OUTCOMES:
        raise HTTPException(
            status_code=422,
            detail=f"outcome must be one of {sorted(_VALID_OUTCOMES)}",
        )
    if req.outcome == "reject" and not req.reason:
        raise HTTPException(status_code=422, detail="reason is required for reject")
    if req.outcome == "waive" and not req.justification:
        raise HTTPException(status_code=422, detail="justification is required for waive")
    if req.outcome == "escalate" and not req.justification:
        raise HTTPException(status_code=422, detail="justification is required for escalate")
    if req.outcome == "edit" and not req.new_deliverable:
        raise HTTPException(status_code=422, detail="new_deliverable is required for edit")

    conn = get_conn()
    try:
        with board_scope(conn, board_id) as cur:
            # RT-02: enforce critic satisfaction before allowing approve.
            if req.outcome == "approve":
                cur.execute(
                    "SELECT all_required_pass FROM v_gate_status "
                    "WHERE task_id = %s AND board_id = %s",
                    (task_id, board_id),
                )
                gate_row = cur.fetchone()
                if gate_row is None:
                    raise HTTPException(status_code=404, detail="task not found")
                if not gate_row["all_required_pass"]:
                    raise HTTPException(
                        status_code=409,
                        detail={"error": "required critics have not passed"},
                    )

            # For waive: block if any failing required critic has waivable=False.
            if req.outcome == "waive":
                cur.execute(
                    """
                    SELECT 1 FROM task_gate_results
                    WHERE task_id = %s AND board_id = %s
                      AND required = true AND waivable = false AND verdict = 'fail'
                    LIMIT 1
                    """,
                    (task_id, board_id),
                )
                if cur.fetchone() is not None:
                    raise HTTPException(
                        status_code=409,
                        detail={"error": "safety critics cannot be waived — use escalate"},
                    )

            # Retrieve session_key (= LangGraph thread_id) to resume the graph.
            cur.execute(
                "SELECT session_id, status FROM tasks WHERE id = %s AND board_id = %s",
                (task_id, board_id),
            )
            task_row = cur.fetchone()
    finally:
        conn.close()

    if task_row is None:
        raise HTTPException(status_code=404, detail="task not found")
    if not task_row["session_id"]:
        raise HTTPException(
            status_code=409,
            detail="task has no active session — run the worker first",
        )

    session_key = task_row["session_id"]

    # Resume the spine graph. post_gate_node writes approved_at, approved_by,
    # task_events, and the status update — never this endpoint (RT-03).
    from langgraph.types import Command

    _get_graph().invoke(
        Command(
            resume={
                "outcome": req.outcome,
                "approved_by": approved_by,
                "reason": req.reason,
                "justification": req.justification,
                "new_deliverable": req.new_deliverable,
            }
        ),
        config={"configurable": {"thread_id": session_key}},
    )

    return {"status": "ok", "outcome": req.outcome, "approved_by": approved_by}


# ---------------------------------------------------------------------------
# P7a minimal gate UI — static file mount (ADR-002: view never touches Postgres
# directly, only this board-api). Mounted at /gate, not /, so it never shadows
# any API route above.
# ---------------------------------------------------------------------------

import pathlib as _pathlib
from fastapi.staticfiles import StaticFiles as _StaticFiles

_WEB_DIR = _pathlib.Path(__file__).resolve().parent.parent / "web" / "gate"
app.mount("/gate", _StaticFiles(directory=str(_WEB_DIR), html=True), name="gate-ui")
