"""
TALOS P3 spine graph.

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


class TaskBudget(TypedDict):
    max_spend_usd: float        # hard cap; 0.0 = unlimited
    max_tokens: int             # hard cap; 0 = unlimited
    max_tool_calls: int         # hard cap; 0 = unlimited
    max_elapsed_seconds: int    # hard cap; 0 = unlimited
    soft_spend_usd: float       # soft threshold → emit span
    spent_usd: float            # running total
    tokens_used: int            # running total
    tool_calls: int             # running total


def default_budget() -> TaskBudget:
    return TaskBudget(
        max_spend_usd=0.0,
        max_tokens=0,
        max_tool_calls=0,
        max_elapsed_seconds=0,
        soft_spend_usd=0.0,
        spent_usd=0.0,
        tokens_used=0,
        tool_calls=0,
    )


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
    sdk_session_ids: dict             # {"read_node": session_id, ...}; P3b Agent SDK continuity
    budget: TaskBudget                # 4-axis budget tracking (ADR-030)
    task_body: str | None             # tasks.body — read_node's live-mode prompt, when set (P3.5)


# ---------------------------------------------------------------------------
# LLM call helper (P3b)
# ---------------------------------------------------------------------------

def _call_with_fallback(
    primary: "ModelRef",
    fallback: "ModelRef",
    *,
    prompt: str,
    resume: str | None,
    state: "SpineState",
    allowed_tools: list[str] | None = None,
    mcp_servers: dict | None = None,
    manifest: dict | None = None,
    budget_check=None,
) -> tuple[str, str, int]:
    """
    Try primary model, then fallback. Returns (text, session_id, tokens).
    Raises ModelFailureError if both fail — worker catches and escalates to gate.

    BudgetExhaustedError (raised mid-call by call_model when budget_check aborts
    a driver's tool loop, ADR-030/031) is not caught here — it is not
    ModelCallError, so it propagates straight past this loop, aborting the
    fallback attempt entirely. Retrying an already-over-budget call against the
    fallback model would defeat the point of the budget cap.
    """
    from talos.llm import ModelCallError, call_model
    from talos.errors import ModelFailureError

    for model_ref in (primary, fallback):
        try:
            return call_model(
                model_ref, prompt, resume=resume,
                allowed_tools=allowed_tools, mcp_servers=mcp_servers,
                manifest=manifest, budget_check=budget_check,
            )
        except ModelCallError as exc:
            import logging
            logging.getLogger(__name__).warning(
                "model %s/%s failed: %s", model_ref.provider, model_ref.model, exc
            )

    raise ModelFailureError(
        task_id=state["task_id"],
        run_id=state["run_id"],
        board_id=state["board_id"],
        reason=f"both primary ({primary!r}) and fallback ({fallback!r}) failed",
    )


# ---------------------------------------------------------------------------
# Node 1: read_node
# ---------------------------------------------------------------------------

def read_node(state: SpineState) -> dict:
    """
    Fetch tag context from NEXUS (or stub). No Postgres writes of its own — this node
    never writes to task_events or task_gate_results (ADR-003/007); TALOS's own state
    stays read-only here.

    In live mode (ADR-038), calls the Claude Agent SDK via talos.llm.call_model() with the
    NEXUS MCP server wired in and the manifest-filtered tool list (talos.nexus_client).
    The model may invoke manifest-declared NEXUS tools, including write:offline_artifact
    tools (e.g. full_plc_documentation) that write only to NEXUS's own derived-artifact
    store, never TALOS's SoR or any live processor. The session ID is stored in
    sdk_session_ids for continuity on resume (ADR-029). Also tracks and enforces the
    ADR-030 token/tool-call budget for this call, raising BudgetExhaustedError on a hard
    cap (P3.5).
    """
    from talos.spans import SpanContext, emit_span
    ctx = SpanContext(board_id=state["board_id"], task_id=state["task_id"], run_id=state["run_id"])
    emit_span(ctx, "spine.node.read_node.entry")

    sdk_session_ids = dict(state.get("sdk_session_ids") or {})

    budget = dict(state.get("budget") or default_budget())

    if os.environ.get("TALOS_NEXUS_STUB") == "1":
        nexus_result = {"tag": "MOCK_TAG", "status": "confirmed"}
        sdk_session_ids["read_node"] = "stub-session-id"
        # Emit a stub llm.call span with > 0 latency so P3d tests can assert latency_ms.
        emit_span(
            ctx, "llm.call",
            model_id="stub-model",
            provider="stub",
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=1,
        )
    else:
        from talos.config import resolve_model, TALOS_NEXUS_URL
        from talos.errors import BudgetExhaustedError
        from talos.llm import ModelCallError, call_model
        from talos.nexus_client import allowed_nexus_tools, load_nexus_manifest, nexus_mcp_server_config

        board_id = state["board_id"]
        primary, fallback = resolve_model("research")

        manifest = load_nexus_manifest()
        allowed_tools = allowed_nexus_tools(manifest)
        mcp_servers = {"nexus": nexus_mcp_server_config(TALOS_NEXUS_URL)}

        def _budget_check(tokens_so_far: int, tool_calls_so_far: int) -> None:
            # Read-only check against the current budget snapshot — mutation
            # still happens exactly once, post-hoc, below. Only drivers with
            # their own tool loop (openai_compat) ever call this; the
            # anthropic driver's behavior is unchanged (post-hoc check only).
            total_tokens = budget["tokens_used"] + tokens_so_far
            total_calls = budget["tool_calls"] + tool_calls_so_far
            if budget["max_tokens"] and total_tokens > budget["max_tokens"]:
                raise BudgetExhaustedError(
                    task_id=state["task_id"], run_id=state["run_id"], board_id=board_id,
                    reason=f"max_tokens={budget['max_tokens']} exceeded mid-call (used {total_tokens})",
                )
            if budget["max_tool_calls"] and total_calls > budget["max_tool_calls"]:
                raise BudgetExhaustedError(
                    task_id=state["task_id"], run_id=state["run_id"], board_id=board_id,
                    reason=f"max_tool_calls={budget['max_tool_calls']} exceeded mid-call (used {total_calls})",
                )

        resume_id = sdk_session_ids.get("read_node")
        prompt = state.get("task_body") or f"Fetch NEXUS context for task {state['task_id']}"
        text, session_id, tokens = _call_with_fallback(
            primary, fallback, prompt=prompt,
            resume=resume_id, state=state,
            allowed_tools=allowed_tools, mcp_servers=mcp_servers,
            manifest=manifest, budget_check=_budget_check,
        )
        sdk_session_ids["read_node"] = session_id
        nexus_result = {"tag": text or "UNKNOWN", "status": "confirmed"}

        # ADR-030 budget tracking (P3.5): accumulate this call's usage and
        # enforce the hard caps. Soft-threshold handling is unaffected.
        #
        # tool_calls here counts model invocations (calls to call_model), not
        # individual MCP tool invocations the model makes within one call — the
        # SDK's ResultMessage does not expose a per-MCP-tool-call count. Only
        # max_tokens is a faithfully-enforced cap; max_tool_calls is tracked
        # against this coarser proxy and will under-count real tool-call volume.
        budget["tokens_used"] += tokens
        budget["tool_calls"] += 1
        if budget["max_tokens"] and budget["tokens_used"] > budget["max_tokens"]:
            raise BudgetExhaustedError(
                task_id=state["task_id"], run_id=state["run_id"], board_id=board_id,
                reason=f"max_tokens={budget['max_tokens']} exceeded (used {budget['tokens_used']})",
            )
        if budget["max_tool_calls"] and budget["tool_calls"] > budget["max_tool_calls"]:
            raise BudgetExhaustedError(
                task_id=state["task_id"], run_id=state["run_id"], board_id=board_id,
                reason=f"max_tool_calls={budget['max_tool_calls']} exceeded (used {budget['tool_calls']}, "
                       f"counting model invocations, not individual MCP tool calls)",
            )

    emit_span(ctx, "spine.node.read_node.exit")
    return {"nexus_result": nexus_result, "sdk_session_ids": sdk_session_ids, "budget": budget}


# ---------------------------------------------------------------------------
# Node 2: deliverable_node
# ---------------------------------------------------------------------------

def _fire_review_email(board_id: str, task_id: str) -> None:
    """Optional SMTP notification when a task enters review. No-op if unconfigured.
    Failures are logged and swallowed — mirrors _fire_escalation_webhook."""
    import os
    host = os.environ.get("TALOS_SMTP_HOST", "").strip()
    if not host:
        return
    to_addrs = [a.strip() for a in os.environ.get("TALOS_SMTP_TO", "").split(",") if a.strip()]
    if not to_addrs:
        return
    try:
        import smtplib
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["Subject"] = f"TALOS: task {task_id} awaiting review"
        msg["From"] = os.environ.get("TALOS_SMTP_FROM", "talos@localhost")
        msg["To"] = ", ".join(to_addrs)
        msg.set_content(
            f"Task {task_id} on board {board_id} entered review.\n"
            f"Review it at the gate UI."
        )
        port = int(os.environ.get("TALOS_SMTP_PORT", "587"))
        use_tls = os.environ.get("TALOS_SMTP_TLS", "1") != "0"
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            if use_tls:
                smtp.starttls()
            user = os.environ.get("TALOS_SMTP_USER")
            password = os.environ.get("TALOS_SMTP_PASSWORD")
            if user and password:
                smtp.login(user, password)
            smtp.send_message(msg)
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "review-entry email failed for task %s; gate transition unaffected", task_id
        )


def deliverable_node(state: SpineState) -> dict:
    """
    Build (or accept an edited) deliverable, run all registered critics via the
    registry, persist one task_gate_results row per critic, and move the task to review.

    On re-entry via the edit outcome, state["edited_deliverable"] carries the human's
    revised deliverable — critics re-run against it without a NEXUS re-read.
    """
    from talos.spans import SpanContext, emit_span
    ctx = SpanContext(board_id=state["board_id"], task_id=state["task_id"], run_id=state["run_id"])
    emit_span(ctx, "spine.node.deliverable_node.entry")

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

    # Emit one critic span per verdict.
    for v in verdicts:
        emit_span(ctx, f"spine.critic.{v['name']}", payload={"passed": v.get("passed"), "verdict": v.get("verdict")})

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
                """
                UPDATE tasks
                SET status = 'review',
                    deliverable = %s::jsonb,
                    review_entered_at = NOW()
                WHERE id = %s AND board_id = %s
                """,
                (json.dumps(deliverable), state["task_id"], state["board_id"]),
            )
    finally:
        conn.close()

    _fire_review_email(state["board_id"], state["task_id"])

    # Gate interrupt is semantically "task is now in review awaiting human decision."
    emit_span(ctx, "spine.gate.interrupt")
    emit_span(ctx, "spine.node.deliverable_node.exit")

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

    spine.gate.interrupt is emitted by deliverable_node (avoids double-emit on resume).
    spine.gate.resume is emitted by post_gate_node.

    On resume, interrupt() returns the Command.resume value from the gate API:
        {"outcome": "approve"|"reject"|"waive"|"edit"|"escalate",
         "approved_by": "<session>",
         "reason": "...",           # reject
         "justification": "...",   # waive | escalate
         "new_deliverable": {...}}  # edit
    """
    from talos.spans import SpanContext, emit_span
    ctx = SpanContext(board_id=state["board_id"], task_id=state["task_id"], run_id=state["run_id"])
    emit_span(ctx, "spine.node.gate_node.entry")

    outcome = interrupt(
        {
            "task_id": state["task_id"],
            "deliverable": state["deliverable"],
            "critic_results": state["critic_results"],
        }
    )
    emit_span(ctx, "spine.node.gate_node.exit")
    return {
        "gate_outcome": outcome["outcome"],
        "approved_by": outcome.get("approved_by"),
        "gate_justification": outcome.get("justification") or outcome.get("reason"),
        "edited_deliverable": outcome.get("new_deliverable"),
    }


# ---------------------------------------------------------------------------
# Node 4: post_gate_node — idempotent; all side-effects in one transaction
# ---------------------------------------------------------------------------

def _fire_escalation_webhook(board_id: str, task_id: str, run_id: int | None) -> None:
    """HTTP POST escalation webhook. Failures are logged and swallowed."""
    import datetime
    import os
    webhook_url = os.environ.get("TALOS_ESCALATION_WEBHOOK_URL", "").strip()
    if not webhook_url:
        return
    try:
        import requests  # type: ignore[import]
        payload = {
            "event": "gate_escalated",
            "board_id": board_id,
            "task_id": task_id,
            "run_id": run_id,
            "escalated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        requests.post(webhook_url, json=payload, timeout=5)
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "escalation webhook failed for task %s; gate transition unaffected", task_id
        )


def post_gate_node(state: SpineState) -> dict:
    """
    Persist the gate outcome for approve / reject / waive / escalate.
    (edit never reaches this node — it loops back to deliverable_node.)

    Idempotency guard: if task status is already 'approved' or 'rejected',
    this node already ran — return early without writing.
    """
    from talos.spans import SpanContext, emit_span
    ctx = SpanContext(board_id=state["board_id"], task_id=state["task_id"], run_id=state["run_id"])
    emit_span(ctx, "spine.node.post_gate_node.entry")
    emit_span(ctx, "spine.gate.resume")

    outcome = state["gate_outcome"]
    approved_by = state["approved_by"]
    justification = state.get("gate_justification")

    conn = get_conn()
    try:
        with board_scope(conn, state["board_id"]) as cur:
            # Idempotency guard — keyed off gate markers, not status (SEC-01).
            # approved_at is set by approve/waive/escalate; rejected_at by reject.
            # Neither column is settable via PATCH /status, so this guard is unforgeable.
            cur.execute(
                "SELECT status, approved_at, rejected_at FROM tasks WHERE id = %s AND board_id = %s",
                (state["task_id"], state["board_id"]),
            )
            row = cur.fetchone()
            if row and (row["approved_at"] is not None or row["rejected_at"] is not None):
                return {}  # gate already decided — idempotent no-op

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

    emit_span(ctx, "spine.post_gate.write", payload={"outcome": outcome})
    emit_span(ctx, "spine.node.post_gate_node.exit")

    # Escalation webhook — fires after DB commit; never blocks gate transition.
    if outcome == "escalate":
        _fire_escalation_webhook(state["board_id"], state["task_id"], state["run_id"])

    # PM hooks — fire-and-forget; approved states include approve/waive/escalate.
    if outcome in ("approve", "waive", "escalate"):
        from talos.hooks import default_registry
        hook_payload = {
            "board_id": state["board_id"],
            "task_id": state["task_id"],
            "run_id": state["run_id"],
            "outcome": outcome,
            "approved_by": approved_by,
        }
        default_registry.fire_sync("on_task_approved", hook_payload)

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

def build_graph(checkpointer=None, node_callback=None):
    """
    Build and compile the spine graph.

    Pass checkpointer=MemorySaver() in tests.
    Pass checkpointer=PostgresSaver(...) in production.
    Pass node_callback(state) to fire a heartbeat write at every node entry (P3a).

    No interrupt_before — interrupt() inside gate_node is the sole pause point.
    Using interrupt_before alongside interrupt() would cause a double-pause on resume.
    """
    if checkpointer is None:
        checkpointer = MemorySaver()

    def maybe_wrap(fn):
        if node_callback is None:
            return fn
        def wrapped(state):
            node_callback(state)
            return fn(state)
        return wrapped

    builder = StateGraph(SpineState)
    builder.add_node("read_node", maybe_wrap(read_node))
    builder.add_node("deliverable_node", maybe_wrap(deliverable_node))
    builder.add_node("gate_node", maybe_wrap(gate_node))
    builder.add_node("post_gate_node", maybe_wrap(post_gate_node))

    builder.add_edge(START, "read_node")
    builder.add_edge("read_node", "deliverable_node")
    builder.add_edge("deliverable_node", "gate_node")
    builder.add_conditional_edges("gate_node", _route_after_gate)
    builder.add_edge("post_gate_node", END)

    return builder.compile(checkpointer=checkpointer)
