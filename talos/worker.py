"""
TALOS P3 worker: claim loop with heartbeat, dead-worker reclaim, PostgresSaver injection,
and multi-worker dispatcher.

P3a additions:
  - TALOS_HEARTBEAT_INTERVAL_S / TALOS_RECLAIM_AFTER_MISSES env vars (ADR-020)
  - reclaim_dead_workers(): scan for stale task_runs and re-queue them
  - node_callback: writes last_heartbeat_at at every LangGraph node boundary
  - PostgresSaver injected at startup via TALOS_DB_DSN

P3b additions:
  - TALOS_WORKER_COUNT: asyncio dispatcher launching N concurrent worker slots
  - BudgetExhaustedError / ModelFailureError: caught in worker loop → status='review'
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading

from talos.db import board_scope, get_conn, get_system_conn
from talos.errors import BudgetExhaustedError, ModelFailureError
from talos.graph.spine import SpineState, build_graph, default_budget

log = logging.getLogger(__name__)

TALOS_HEARTBEAT_INTERVAL_S = int(os.environ.get("TALOS_HEARTBEAT_INTERVAL_S", "30"))
TALOS_RECLAIM_AFTER_MISSES = int(os.environ.get("TALOS_RECLAIM_AFTER_MISSES", "3"))
TALOS_WORKER_COUNT = int(os.environ.get("TALOS_WORKER_COUNT", "1"))

_RECLAIM_INTERVAL_S = TALOS_HEARTBEAT_INTERVAL_S * TALOS_RECLAIM_AFTER_MISSES

# Re-export for callers that imported these from talos.worker previously.
__all__ = [
    "BudgetExhaustedError",
    "ModelFailureError",
    "claim_and_run",
    "reclaim_dead_workers",
    "run_dispatcher",
    "TALOS_HEARTBEAT_INTERVAL_S",
    "TALOS_RECLAIM_AFTER_MISSES",
    "TALOS_WORKER_COUNT",
]


def reclaim_dead_workers() -> int:
    """
    Scan task_runs for rows where last_heartbeat_at has gone stale and re-queue
    the associated task. Returns the number of runs reclaimed.

    Uses get_system_conn() (BYPASSRLS talos_system role) so it can see stale
    runs on all boards without a board_scope. See ADR-037.
    """
    import psycopg2.extras
    from talos.spans import SpanContext, emit_span

    conn = get_system_conn()
    try:
        reclaimed = 0
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT tr.id AS run_id, tr.task_id, tr.board_id
                FROM task_runs tr
                JOIN tasks t ON t.id = tr.task_id AND t.board_id = tr.board_id
                WHERE tr.ended_at IS NULL
                  AND tr.last_heartbeat_at IS NOT NULL
                  AND tr.last_heartbeat_at < NOW() - (%s * INTERVAL '1 second')
                  AND t.status = 'running'
                """,
                (_RECLAIM_INTERVAL_S,),
            )
            dead = cur.fetchall()
            for row in dead:
                cur.execute(
                    "UPDATE task_runs SET ended_at = NOW(), outcome = 'reclaimed' WHERE id = %s",
                    (row["run_id"],),
                )
                cur.execute(
                    "UPDATE tasks SET status = 'ready' WHERE id = %s AND board_id = %s",
                    (row["task_id"], row["board_id"]),
                )
                log.warning(
                    "reclaimed dead task_run id=%s task_id=%s board_id=%s",
                    row["run_id"], row["task_id"], row["board_id"],
                )
                emit_span(
                    SpanContext(board_id=row["board_id"], task_id=row["task_id"], run_id=row["run_id"]),
                    "worker.reclaim",
                    payload={"stale_run_id": row["run_id"]},
                    db_conn=conn,
                )
                reclaimed += 1
        conn.commit()
        return reclaimed
    finally:
        conn.close()


@contextlib.contextmanager
def _heartbeat_thread(run_id: int, board_id: str):
    """Beat last_heartbeat_at every TALOS_HEARTBEAT_INTERVAL_S while a node may be blocking.

    Reads the module-level TALOS_HEARTBEAT_INTERVAL_S at call time so tests can monkeypatch the
    cadence. Daemon thread; always stopped on context exit (including on exception).
    """
    stop = threading.Event()

    def _beat():
        while not stop.wait(TALOS_HEARTBEAT_INTERVAL_S):
            try:
                conn = get_conn()
                try:
                    with board_scope(conn, board_id) as cur:
                        cur.execute(
                            "UPDATE task_runs SET last_heartbeat_at = NOW() WHERE id = %s",
                            (run_id,),
                        )
                finally:
                    conn.close()
            except Exception:
                log.exception("heartbeat thread: write failed for run_id=%s", run_id)

    t = threading.Thread(target=_beat, name=f"hb-{run_id}", daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join(timeout=5)


def make_heartbeat_callback(run_id: int, board_id: str):
    """Return a node_callback that writes last_heartbeat_at for the given run.

    Requires board_id so board_scope sets app.board_id before the UPDATE —
    without SET LOCAL the talos_app RLS policy blocks the write (task_runs
    is board-scoped; autocommit=True would prevent SET LOCAL from applying).
    """
    def callback(state: SpineState) -> None:
        conn = get_conn()
        try:
            with board_scope(conn, board_id) as cur:
                cur.execute(
                    "UPDATE task_runs SET last_heartbeat_at = NOW() WHERE id = %s",
                    (run_id,),
                )
        finally:
            conn.close()
    return callback


def claim_and_run(board_id: str, task_id: str, graph=None, initial_budget=None) -> str:
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
    initial_budget:
        Optional TaskBudget override. Defaults to default_budget() (all caps
        unlimited). Tests inject a tiny budget (e.g. max_tokens=1) to drive
        the real ADR-030 hard-cap raise path end-to-end (P3.5 harness) without
        a schema migration for a per-task budget column.
    """
    conn = get_conn()
    try:
        # Scan for dead workers before claiming (ADR-020).
        reclaim_dead_workers()

        with board_scope(conn, board_id) as cur:
            # 1. Fetch task and board (for model_config), assert status == 'ready'
            cur.execute(
                "SELECT t.id, t.status, t.model_override, t.max_runtime_seconds, t.body, "
                "       b.model_config "
                "FROM tasks t JOIN boards b ON b.id = t.board_id "
                "WHERE t.id = %s AND t.board_id = %s",
                (task_id, board_id),
            )
            task = cur.fetchone()
            if task is None:
                raise ValueError(f"task {task_id!r} not found in board {board_id!r}")
            if task["status"] != "ready":
                raise ValueError(
                    f"task {task_id!r} status is {task['status']!r}, expected 'ready'"
                )

            # 2. Compute attempt_no.
            cur.execute(
                "SELECT COALESCE(MAX(attempt_no), 0) + 1 AS next_attempt "
                "FROM task_runs WHERE task_id = %s",
                (task_id,),
            )
            attempt_no: int = cur.fetchone()["next_attempt"]

            # 3. INSERT task_runs row — copy max_runtime_seconds from task (ADR-020).
            cur.execute(
                """
                INSERT INTO task_runs (board_id, task_id, status, attempt_no, max_runtime_seconds)
                VALUES (%s, %s, 'running', %s, %s)
                RETURNING id
                """,
                (board_id, task_id, attempt_no, task.get("max_runtime_seconds")),
            )
            run_id: int = cur.fetchone()["id"]

            # 4. Mint session key (ADR-010).
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

    if graph is None:
        graph = build_graph(node_callback=make_heartbeat_callback(run_id, board_id))

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
        "edited_deliverable": None,
        "gate_justification": None,
        "sdk_session_ids": {},
        "budget": initial_budget if initial_budget is not None else default_budget(),
        "task_body": task.get("body"),
        "context_branches": {},
        "chroma_chunks": [],
        "nexus_supplemental": [],
    }

    from talos.spans import SpanContext, emit_span
    span_ctx = SpanContext(board_id=board_id, task_id=task_id, run_id=run_id)
    emit_span(span_ctx, "worker.claim")

    with _heartbeat_thread(run_id, board_id):
        graph.invoke(
            initial_state,
            config={"configurable": {"thread_id": session_key}},
        )

    return session_key


# ---------------------------------------------------------------------------
# P3b multi-worker dispatcher
# ---------------------------------------------------------------------------

async def _worker_slot(slot_id: int, graph, board_id: str, task_id: str | None = None):
    """
    Single worker coroutine. If task_id is provided, claims exactly that task
    (used in tests). Otherwise loops claiming the next available ready task.
    """
    while True:
        try:
            if task_id:
                await asyncio.to_thread(claim_and_run, board_id, task_id, graph)
                return  # single-task mode for tests
            else:
                # Poll for next ready task — production mode.
                conn = get_conn()
                try:
                    with board_scope(conn, board_id) as cur:
                        cur.execute(
                            "SELECT id FROM tasks WHERE board_id = %s AND status = 'ready' LIMIT 1",
                            (board_id,),
                        )
                        row = cur.fetchone()
                finally:
                    conn.close()

                if row is None:
                    await asyncio.sleep(1)
                    continue

                await asyncio.to_thread(claim_and_run, board_id, row["id"], graph)

        except BudgetExhaustedError as exc:
            _handle_budget_exhaustion(exc)
        except ModelFailureError as exc:
            _handle_model_failure(exc)
        except Exception:
            log.exception("worker slot %d: unhandled error", slot_id)
            await asyncio.sleep(1)

        if task_id:
            return  # don't loop in single-task test mode


def _handle_budget_exhaustion(exc: BudgetExhaustedError) -> None:
    conn = get_conn()
    try:
        with board_scope(conn, exc.board_id) as cur:
            cur.execute(
                "UPDATE task_runs SET ended_at = NOW(), outcome = 'budget_exhausted' WHERE id = %s",
                (exc.run_id,),
            )
            cur.execute(
                "UPDATE tasks SET status = 'review', review_entered_at = NOW() "
                "WHERE id = %s AND board_id = %s",
                (exc.task_id, exc.board_id),
            )
    finally:
        conn.close()
    log.warning("budget exhausted for task %s: %s", exc.task_id, exc)


def _handle_model_failure(exc: ModelFailureError) -> None:
    conn = get_conn()
    try:
        with board_scope(conn, exc.board_id) as cur:
            cur.execute(
                "UPDATE task_runs SET ended_at = NOW(), outcome = 'model_failure' WHERE id = %s",
                (exc.run_id,),
            )
            cur.execute(
                "UPDATE tasks SET status = 'review', review_entered_at = NOW() "
                "WHERE id = %s AND board_id = %s",
                (exc.task_id, exc.board_id),
            )
    finally:
        conn.close()
    log.warning("model failure for task %s: %s", exc.task_id, exc)


async def run_dispatcher(graph, board_id: str):
    """
    Launch TALOS_WORKER_COUNT concurrent worker slots.

    Under real (non-stub) NEXUS wiring, verifies the on-disk manifest's
    content_hash is self-consistent before claiming any task (ADR-038's
    lightweight, non-DB-pinned self-check; ADR-032/034's full board-scoped
    manifest_hash check is deferred, requires a migration).
    """
    if os.environ.get("TALOS_NEXUS_STUB") != "1":
        from talos.nexus_client import load_nexus_manifest, manifest_selfcheck
        manifest_selfcheck(load_nexus_manifest())

    await asyncio.gather(*[
        _worker_slot(i, graph, board_id)
        for i in range(TALOS_WORKER_COUNT)
    ])
