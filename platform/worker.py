"""
TALOS P1 single-worker claim loop.

claim_and_run() claims one ready task, mints a session key, inserts a task_runs
row (capturing attempt_no and run_id), invokes the spine graph, and returns the
session key so the caller can resume the graph after the human gate fires.

P1 deliberately excludes: dispatcher (claim-racing, heartbeat, breaker), checkpoint
reclaim, multi-worker coordination. Those are P3.
"""

from __future__ import annotations

from platform.db import board_scope, get_conn
from platform.graph.spine import SpineState, build_graph


def claim_and_run(board_id: str, task_id: str, graph=None) -> str:
    """
    Claim a ready task and drive the spine graph until it pauses at gate_node.

    Returns the session key ("task:{board_id}:{task_id}:{attempt_no}") which
    is also the LangGraph thread_id. The caller saves this to resume after the
    human gate.

    Parameters
    ----------
    graph:
        A compiled LangGraph graph. Defaults to build_graph() (MemorySaver).
        Tests inject a shared graph instance so the same in-process MemorySaver
        is available to both claim_and_run and the gate API endpoint.
    """
    if graph is None:
        graph = build_graph()

    conn = get_conn()
    try:
        with board_scope(conn, board_id) as cur:
            # 1. Fetch task, assert status == 'ready'
            cur.execute(
                "SELECT id, status FROM tasks WHERE id = %s AND board_id = %s",
                (task_id, board_id),
            )
            task = cur.fetchone()
            if task is None:
                raise ValueError(f"task {task_id!r} not found in board {board_id!r}")
            if task["status"] != "ready":
                raise ValueError(
                    f"task {task_id!r} status is {task['status']!r}, expected 'ready'"
                )

            # 2. Compute attempt_no — no trigger auto-increments it.
            #    The UNIQUE INDEX idx_runs_attempt on task_runs(task_id, attempt_no)
            #    guards against duplicate attempt numbers in a multi-worker scenario.
            cur.execute(
                "SELECT COALESCE(MAX(attempt_no), 0) + 1 AS next_attempt "
                "FROM task_runs WHERE task_id = %s",
                (task_id,),
            )
            attempt_no: int = cur.fetchone()["next_attempt"]

            # 3. INSERT task_runs row — RETURNING id gives us the global run_id.
            cur.execute(
                """
                INSERT INTO task_runs (board_id, task_id, status, attempt_no)
                VALUES (%s, %s, 'running', %s)
                RETURNING id
                """,
                (board_id, task_id, attempt_no),
            )
            run_id: int = cur.fetchone()["id"]

            # 4. Mint session key (ADR-010: "task:{board_id}:{task_id}:{attempt}").
            session_key = f"task:{board_id}:{task_id}:{attempt_no}"

            # 5. Claim the task.
            cur.execute(
                """
                UPDATE tasks
                SET status = 'running',
                    session_id = %s,
                    current_run_id = %s,
                    started_at = NOW()
                WHERE id = %s AND board_id = %s
                """,
                (session_key, run_id, task_id, board_id),
            )
    finally:
        conn.close()

    # 6. Build initial state and invoke. The graph pauses at gate_node's interrupt()
    #    and returns here. The session_key is the LangGraph thread_id for checkpointing.
    initial_state: SpineState = {
        "board_id": board_id,
        "task_id": task_id,
        "attempt_no": attempt_no,
        "run_id": run_id,
        "session_key": session_key,
        "nexus_result": {},
        "deliverable": {},
        "critic_results": [],
        "gate_outcome": None,
        "approved_by": None,
    }

    graph.invoke(
        initial_state,
        config={"configurable": {"thread_id": session_key}},
    )

    return session_key
