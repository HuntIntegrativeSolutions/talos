"""
P3.5 step 1 — heartbeat-starvation / mid-flight reclaim double-execution bug reproduction.

Bug: heartbeats fire only at node boundaries (maybe_wrap in build_graph). While a node is
executing a long synchronous call, no heartbeat fires. reclaim_dead_workers() sees the frozen
last_heartbeat_at, declares the worker dead, re-queues the task, and a second worker can claim
and execute it — double execution.

The test is marked xfail(raises=AssertionError, strict=True):
  - Today:  assert reclaimed == 0 raises AssertionError (reclaimed == 1) → XFAIL ✓
  - Setup failure (DB error, poll timeout): raises RuntimeError → ERROR, not false XFAIL ✓
  - After fix: all assertions pass → XPASS → strict=True makes it a suite failure → remove marker

Timing invariant (the tripwire contract):
    HEARTBEAT_S < THRESHOLD_S < AGING_SLEEP < NODE_BLOCK_S
    0.5 s        1 s           1.5 s          blocks on Event

HEARTBEAT_S is patched into TALOS_HEARTBEAT_INTERVAL_S so a background-heartbeat fix (which
must wire its beat to that constant) can satisfy the invariant without changing any constant here.
Without that patch, a correct fix with the default 30 s beat still fails this test, making the
tripwire unsatisfiable.
"""

from __future__ import annotations

import threading
import time
import unittest.mock as mock

import psycopg2.extras
import pytest

import talos.graph.spine as spine_module
import talos.worker
from talos.worker import (
    TALOS_HEARTBEAT_INTERVAL_S,
    TALOS_RECLAIM_AFTER_MISSES,
    claim_and_run,
    reclaim_dead_workers,
)

# ---------------------------------------------------------------------------
# Timing constants
# ---------------------------------------------------------------------------

HEARTBEAT_S = 0.5   # patched TALOS_HEARTBEAT_INTERVAL_S — the fix's background beat interval
THRESHOLD_S = 1     # patched _RECLAIM_INTERVAL_S
AGING_SLEEP = 1.5   # main thread waits this long after heartbeat is detected before reclaiming
# NODE_BLOCK_S: slow_read blocks on a threading.Event (no fixed sleep), released by main thread


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _seed_board_and_task(cur, board_id: str, task_id: str) -> None:
    cur.execute(
        "INSERT INTO boards (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (board_id, f"board-{board_id}"),
    )
    cur.execute(
        """
        INSERT INTO tasks (id, board_id, title, status)
        VALUES (%s, %s, 'test task', 'ready')
        ON CONFLICT DO NOTHING
        """,
        (task_id, board_id),
    )


# ---------------------------------------------------------------------------
# Bug-reproduction test
# ---------------------------------------------------------------------------

def test_heartbeat_starvation_reclaim(pg_setup, admin_conn, monkeypatch):
    """
    Proves the heartbeat-starvation / mid-flight reclaim double-execution bug.

    Correct invariant: a task_runs row whose worker thread is still alive must
    NEVER be reclaimed. Today that invariant is violated — this test documents it
    as a strict xfail so the suite turns red the moment a fix makes it pass.
    """
    # 1. Patch both import-frozen constants (env vars set here have no effect).
    monkeypatch.setattr(talos.worker, "_RECLAIM_INTERVAL_S", THRESHOLD_S)
    monkeypatch.setattr(talos.worker, "TALOS_HEARTBEAT_INTERVAL_S", HEARTBEAT_S)

    # 2. threading.Event blocks slow_read until the main thread is ready to release it.
    #    This eliminates the wall-clock race: the main thread sets the event only AFTER
    #    reclaim + snapshot, guaranteeing the node is still in-flight when reclaim runs.
    node_blocking = threading.Event()
    call_lock = threading.Lock()
    call_count_list: list[int] = []

    def slow_read(state):
        with call_lock:
            call_count_list.append(1)
            entry = len(call_count_list)
        if entry == 1:
            node_blocking.wait(timeout=10)  # hard cap so a logic error can't hang the suite
        return {
            "nexus_result": {"tag": "STUB", "status": "confirmed"},
            "sdk_session_ids": {"read_node": "stub-session-id"},
        }

    board_id = "starvation-board"
    task_id = "starvation-task"

    with admin_conn.cursor() as cur:
        _seed_board_and_task(cur, board_id, task_id)
    admin_conn.commit()

    thread_exc: list[Exception] = []

    def run_worker():
        try:
            claim_and_run(board_id, task_id)
        except Exception as exc:  # noqa: BLE001
            thread_exc.append(exc)

    with mock.patch.object(spine_module, "read_node", slow_read):
        # 3. Start worker thread — blocks inside slow_read after the first heartbeat fires.
        #    The DB lock from the claim transaction is released (conn.close, worker.py:192)
        #    before graph.invoke (worker.py:218), so the second claim cannot deadlock.
        worker_thread = threading.Thread(target=run_worker, daemon=True)
        worker_thread.start()

        # 4. Poll until the first heartbeat is committed.
        #    make_heartbeat_callback opens its own connection and commits via board_scope
        #    (db.py:58), so READ COMMITTED polling sees it even within this transaction.
        deadline = time.time() + 10
        with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            while time.time() < deadline:
                cur.execute(
                    "SELECT last_heartbeat_at FROM task_runs "
                    "WHERE task_id = %s ORDER BY id DESC LIMIT 1",
                    (task_id,),
                )
                row = cur.fetchone()
                if row and row["last_heartbeat_at"] is not None:
                    break
                time.sleep(0.1)
            else:
                # Re-raise the thread's exception so setup failures appear as ERROR, not XFAIL.
                if thread_exc:
                    raise RuntimeError(
                        "claim_and_run failed before heartbeat fired"
                    ) from thread_exc[0]
                raise RuntimeError(
                    "Timed out waiting for heartbeat — test setup failure"
                )

        # 5. Age the heartbeat past the patched threshold, then reclaim.
        time.sleep(AGING_SLEEP)
        reclaimed = reclaim_dead_workers()

        # Snapshot task state while the node is still blocked.
        with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT status FROM tasks WHERE id = %s", (task_id,))
            task_status = cur.fetchone()["status"]

            cur.execute(
                "SELECT outcome FROM task_runs WHERE task_id = %s ORDER BY id DESC LIMIT 1",
                (task_id,),
            )
            run_outcome = cur.fetchone()["outcome"]  # noqa: F841 (kept for debugging)

        # Release the node now — main thread work is done, worker can continue.
        node_blocking.set()

        # 6. Demonstrate double execution: the bug flipped status to 'ready', so a second
        #    claim succeeds. slow_read entry 2 returns immediately (no block).
        if task_status == "ready":
            claim_and_run(board_id, task_id)

        with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM task_runs WHERE task_id = %s",
                (task_id,),
            )
            total_runs = int(cur.fetchone()["cnt"])

        # Join inside the patch context so read_node stays patched until thread exits.
        # Do NOT assert on thread_exc — the background graph continues post-sleep and
        # may throw after the fix lands; that must not be treated as a failure.
        worker_thread.join(timeout=10)

    # 7. Assert correct invariants (FAIL today → XFAIL; pass post-fix → XPASS → error).
    assert reclaimed == 0, (
        f"BUG: reclaim_dead_workers() reclaimed a task_run whose worker thread is still "
        f"alive (reclaimed={reclaimed})"
    )
    assert len(call_count_list) < 2, (
        f"BUG: read_node was called {len(call_count_list)} times — task executed twice"
    )
    assert total_runs < 2, (
        f"BUG: {total_runs} task_run rows exist for a task that should have run once"
    )


# ---------------------------------------------------------------------------
# Bug 2 — gate-parked task reclaim
# ---------------------------------------------------------------------------

def test_review_task_not_reclaimed(pg_setup, admin_conn):
    """
    Proves reclaim_dead_workers() re-queues review-parked tasks (bug 2).

    A task sitting at the human gate has ended_at IS NULL and a frozen heartbeat —
    indistinguishable from a dead worker under the current predicate. The fix must
    add AND t.status = 'running' to the reclaim query so review-parked tasks are
    never touched.
    """
    board_id, task_id = "gate-park-board", "gate-park-task"
    with admin_conn.cursor() as cur:
        _seed_board_and_task(cur, board_id, task_id)
        cur.execute("UPDATE tasks SET status = 'review' WHERE id = %s", (task_id,))
        stale_age_s = TALOS_HEARTBEAT_INTERVAL_S * TALOS_RECLAIM_AFTER_MISSES + 10
        cur.execute(
            """
            INSERT INTO task_runs (board_id, task_id, status, attempt_no, last_heartbeat_at)
            VALUES (%s, %s, 'running', 1, NOW() - (%s * INTERVAL '1 second'))
            """,
            (board_id, task_id, stale_age_s),
        )
    admin_conn.commit()

    reclaimed = reclaim_dead_workers()

    assert reclaimed == 0, "BUG: reclaim re-queued a task parked at the human gate"
    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT status FROM tasks WHERE id = %s", (task_id,))
        assert cur.fetchone()["status"] == "review", "BUG: gate-parked task flipped to 'ready'"
