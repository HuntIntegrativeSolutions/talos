"""
P3.5 exit-criteria harness — CI-safe stub-mode proofs of the four properties
required before real NEXUS wiring ships (ROADMAP.md's P3.5-Harness gate).

Live-mode evidence for the same four criteria, gathered against the real
NEXUS server (never run in CI — network access to 10.0.0.80, real model
calls), is recorded in docs/p35-harness-results.md.

These tests run under TALOS_NEXUS_STUB=0 for the fallback/budget cases so the
real read_node non-stub branch executes (MCP config construction, budget
tracking, _call_with_fallback) — talos.llm.call_model itself is monkeypatched
so no network or real model call ever happens.
"""

from __future__ import annotations

import threading
import time
import unittest.mock as mock

import psycopg2.extras
import pytest

import talos.llm
import talos.worker
from talos.errors import BudgetExhaustedError, ModelFailureError
from talos.graph.spine import TaskBudget, default_budget
from talos.worker import (
    TALOS_HEARTBEAT_INTERVAL_S,
    _handle_budget_exhaustion,
    claim_and_run,
    reclaim_dead_workers,
)


@pytest.fixture(scope="module", autouse=True)
def _stub_embedding_backed_reads_module_wide():
    """
    P5.5: read_branch_chroma / read_node's rules-prompt-injection query
    always attempt a real get_embed_fn() call regardless of stub mode --
    without a local embedding model pre-downloaded, that raises after a real
    (non-instant, network-dependent) resolution attempt. Stubbed module-wide
    so every live-mode claim_and_run() call in this file stays fast and
    network-independent, not just the tests that care about rules content.
    """
    mp = pytest.MonkeyPatch()
    mp.setattr("talos.memory.pgvector_store.query_rules", lambda board_id, text, k=5: [])
    mp.setattr("talos.memory.pgvector_store.query", lambda board_id, text, k=5: [])
    yield
    mp.undo()


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


@pytest.fixture()
def nexus_live_mode(monkeypatch):
    """Force read_node's non-stub branch without hitting the network — the
    NEXUS manifest is loaded from disk and talos.llm.call_model is mocked by
    each test below. read_branch_chroma / read_node's rules-prompt-injection
    query are stubbed for the whole module by
    _stub_embedding_backed_reads_module_wide above."""
    monkeypatch.delenv("TALOS_NEXUS_STUB", raising=False)


# ---------------------------------------------------------------------------
# Criterion 1 — no reclaim double-apply while a NEXUS-backed call is in flight
# ---------------------------------------------------------------------------

def test_no_reclaim_during_long_nexus_call(pg_setup, admin_conn, monkeypatch, nexus_live_mode):
    """
    Regression guard for b153476 (heartbeat thread + reclaim predicate fix),
    exercised through the real (non-stub) read_node path with a mocked
    call_model standing in for a long-blocking NEXUS tool call.
    """
    monkeypatch.setattr(talos.worker, "_RECLAIM_INTERVAL_S", 1)
    monkeypatch.setattr(talos.worker, "TALOS_HEARTBEAT_INTERVAL_S", 0.5)

    call_blocking = threading.Event()
    call_lock = threading.Lock()
    call_count: list[int] = []

    def slow_call_model(model, prompt, resume=None, *, span_ctx=None, allowed_tools=None,
                        mcp_servers=None, manifest=None, budget_check=None, board_id=None):
        with call_lock:
            call_count.append(1)
            entry = len(call_count)
        if entry == 1:
            call_blocking.wait(timeout=10)
        return "long-nexus-response", "session-1", 10

    board_id, task_id = "p35-reclaim-board", "p35-reclaim-task"
    with admin_conn.cursor() as cur:
        _seed_board_and_task(cur, board_id, task_id)
    admin_conn.commit()

    thread_exc: list[Exception] = []

    def run_worker():
        try:
            claim_and_run(board_id, task_id)
        except Exception as exc:  # noqa: BLE001
            thread_exc.append(exc)

    with mock.patch.object(talos.llm, "call_model", slow_call_model):
        worker_thread = threading.Thread(target=run_worker, daemon=True)
        worker_thread.start()

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
                if thread_exc:
                    raise RuntimeError("claim_and_run failed before heartbeat fired") from thread_exc[0]
                raise RuntimeError("timed out waiting for heartbeat — test setup failure")

        time.sleep(1.5)
        reclaimed = reclaim_dead_workers()

        call_blocking.set()
        worker_thread.join(timeout=10)

    assert reclaimed == 0, "reclaim_dead_workers() reclaimed a task whose worker was still alive"
    assert len(call_count) == 1, f"call_model invoked {len(call_count)} times — task executed twice"

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT COUNT(*) AS cnt FROM task_runs WHERE task_id = %s", (task_id,))
        assert cur.fetchone()["cnt"] == 1


# ---------------------------------------------------------------------------
# Criterion 2 — heartbeat continues to beat during a long-blocking NEXUS call
# ---------------------------------------------------------------------------

def test_heartbeat_beats_during_long_node(pg_setup, admin_conn, monkeypatch, nexus_live_mode):
    monkeypatch.setattr(talos.worker, "TALOS_HEARTBEAT_INTERVAL_S", 0.3)

    call_blocking = threading.Event()

    def slow_call_model(model, prompt, resume=None, *, span_ctx=None, allowed_tools=None,
                        mcp_servers=None, manifest=None, budget_check=None, board_id=None):
        call_blocking.wait(timeout=10)
        return "long-nexus-response", "session-1", 10

    board_id, task_id = "p35-heartbeat-board", "p35-heartbeat-task"
    with admin_conn.cursor() as cur:
        _seed_board_and_task(cur, board_id, task_id)
    admin_conn.commit()

    def run_worker():
        claim_and_run(board_id, task_id)

    with mock.patch.object(talos.llm, "call_model", slow_call_model):
        worker_thread = threading.Thread(target=run_worker, daemon=True)
        worker_thread.start()

        beats_seen: set = set()
        deadline = time.time() + 5
        with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            while time.time() < deadline and len(beats_seen) < 2:
                cur.execute(
                    "SELECT last_heartbeat_at FROM task_runs "
                    "WHERE task_id = %s ORDER BY id DESC LIMIT 1",
                    (task_id,),
                )
                row = cur.fetchone()
                if row and row["last_heartbeat_at"] is not None:
                    beats_seen.add(row["last_heartbeat_at"])
                time.sleep(0.2)

        call_blocking.set()
        worker_thread.join(timeout=10)

    assert len(beats_seen) >= 2, (
        f"expected last_heartbeat_at to advance at least twice during the blocked call, "
        f"saw {len(beats_seen)} distinct values"
    )


# ---------------------------------------------------------------------------
# Criterion 3 — model fallback on primary failure, driven end-to-end
# ---------------------------------------------------------------------------

def test_fallback_on_primary_failure_end_to_end(pg_setup, admin_conn, nexus_live_mode):
    from talos.config import resolve_model

    primary, fallback = resolve_model("research")
    call_log: list[str] = []

    def flaky_call_model(model, prompt, resume=None, *, span_ctx=None, allowed_tools=None,
                         mcp_servers=None, manifest=None, budget_check=None, board_id=None):
        call_log.append(model)
        if model == primary:
            raise talos.llm.ModelCallError("primary failed (simulated)")
        return "fallback response", "fallback-session-id", 5

    board_id, task_id = "p35-fallback-board", "p35-fallback-task"
    with admin_conn.cursor() as cur:
        _seed_board_and_task(cur, board_id, task_id)
    admin_conn.commit()

    with mock.patch.object(talos.llm, "call_model", flaky_call_model):
        claim_and_run(board_id, task_id)  # must not raise ModelFailureError

    assert call_log == [primary, fallback], f"expected [primary, fallback] call order, got {call_log}"

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT status FROM tasks WHERE id = %s", (task_id,))
        assert cur.fetchone()["status"] == "review", "task did not reach the review gate after fallback"


# ---------------------------------------------------------------------------
# Criterion 4 — budget hard cap escalates via the real raise path, not a crash
# ---------------------------------------------------------------------------

def test_budget_hard_cap_end_to_end(pg_setup, admin_conn, nexus_live_mode):
    tiny_budget: TaskBudget = {**default_budget(), "max_tokens": 1}

    def big_call_model(model, prompt, resume=None, *, span_ctx=None, allowed_tools=None,
                       mcp_servers=None, manifest=None, budget_check=None, board_id=None):
        return "response", "session-1", 1000  # exceeds max_tokens=1

    board_id, task_id = "p35-budget-board", "p35-budget-task"
    with admin_conn.cursor() as cur:
        _seed_board_and_task(cur, board_id, task_id)
    admin_conn.commit()

    with mock.patch.object(talos.llm, "call_model", big_call_model):
        with pytest.raises(BudgetExhaustedError) as excinfo:
            claim_and_run(board_id, task_id, initial_budget=tiny_budget)

    assert excinfo.value.axis == "tokens"

    # Mirrors _worker_slot's except-clause handling of a real raised
    # BudgetExhaustedError (worker.py) — not a directly-constructed exception.
    _handle_budget_exhaustion(excinfo.value)

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT status FROM tasks WHERE id = %s", (task_id,))
        assert cur.fetchone()["status"] == "review"
        cur.execute(
            "SELECT outcome, attempt_no FROM task_runs WHERE task_id = %s ORDER BY id DESC LIMIT 1",
            (task_id,),
        )
        row = cur.fetchone()
        assert row["outcome"] == "budget_exhausted"
        assert row["attempt_no"] == 1, "budget exhaustion must not increment attempt_no"
        # P5.5: structured, gate-visible axis recorded in task_events.
        cur.execute(
            "SELECT payload FROM task_events WHERE task_id = %s AND kind = 'budget_exhausted'",
            (task_id,),
        )
        assert cur.fetchone()["payload"]["axis"] == "tokens"


# ---------------------------------------------------------------------------
# P5.5 — elapsed-time hard cap, end-to-end
# ---------------------------------------------------------------------------

def test_budget_elapsed_cap_end_to_end(pg_setup, admin_conn, nexus_live_mode):
    tiny_budget: TaskBudget = {**default_budget(), "max_elapsed_seconds": 1}

    def slow_call_model(model, prompt, resume=None, *, span_ctx=None, allowed_tools=None,
                        mcp_servers=None, manifest=None, budget_check=None, board_id=None):
        time.sleep(1.2)  # exceeds max_elapsed_seconds=1
        return "response", "session-1", 5

    board_id, task_id = "p55-elapsed-board", "p55-elapsed-task"
    with admin_conn.cursor() as cur:
        _seed_board_and_task(cur, board_id, task_id)
    admin_conn.commit()

    with mock.patch.object(talos.llm, "call_model", slow_call_model):
        with pytest.raises(BudgetExhaustedError) as excinfo:
            claim_and_run(board_id, task_id, initial_budget=tiny_budget)

    assert excinfo.value.axis == "elapsed"

    # Mirrors _worker_slot's except-clause handling (worker.py) -- also
    # prevents this test's task_runs row from being left in a permanently
    # 'running'/ended_at-IS-NULL state, which would otherwise be a live
    # target for any *later* test's reclaim_dead_workers() call (that scan is
    # global, not board-scoped -- see test_p35_heartbeat_starvation.py).
    _handle_budget_exhaustion(excinfo.value)


# ---------------------------------------------------------------------------
# P5.5 — spend hard cap, end-to-end (real + estimated-input pricing)
# ---------------------------------------------------------------------------

def test_budget_spend_cap_end_to_end(pg_setup, admin_conn, nexus_live_mode, monkeypatch):
    from talos.config import resolve_model

    primary, _fallback = resolve_model("research")
    monkeypatch.setattr(
        "talos.config.get_pricing_config",
        lambda: {f"{primary.provider}:{primary.model}": {"input_per_1k_usd": 100.0, "output_per_1k_usd": 100.0}},
    )
    tiny_budget: TaskBudget = {**default_budget(), "max_spend_usd": 0.01}

    def big_call_model(model, prompt, resume=None, *, span_ctx=None, allowed_tools=None,
                       mcp_servers=None, manifest=None, budget_check=None, board_id=None):
        return "response", "session-1", 1000

    board_id, task_id = "p55-spend-board", "p55-spend-task"
    with admin_conn.cursor() as cur:
        _seed_board_and_task(cur, board_id, task_id)
    admin_conn.commit()

    with mock.patch.object(talos.llm, "call_model", big_call_model):
        with pytest.raises(BudgetExhaustedError) as excinfo:
            claim_and_run(board_id, task_id, initial_budget=tiny_budget)

    assert excinfo.value.axis == "spend"

    # See test_budget_elapsed_cap_end_to_end's comment -- avoids leaving an
    # orphaned 'running' task_runs row for a later test's global reclaim scan.
    _handle_budget_exhaustion(excinfo.value)


def test_budget_spend_counts_estimated_input_tokens(pg_setup, admin_conn, nexus_live_mode, monkeypatch):
    """
    spent_usd must reflect both the len(prompt)/4 input estimate and the real
    output-token count -- not output-only pricing, which would under-count
    real spend 5-10x on documentation tasks where input dominates (P5.5).
    """
    from talos.config import resolve_model

    primary, _fallback = resolve_model("research")
    price = {"input_per_1k_usd": 1.0, "output_per_1k_usd": 1.0}
    monkeypatch.setattr(
        "talos.config.get_pricing_config",
        lambda: {f"{primary.provider}:{primary.model}": price},
    )
    monkeypatch.setattr("talos.memory.pgvector_store.query_rules", lambda board_id, text, k=5: [])

    long_task_body = "x " * 4000  # long input, small output — input must dominate spend

    def small_output_call_model(model, prompt, resume=None, *, span_ctx=None, allowed_tools=None,
                                mcp_servers=None, manifest=None, budget_check=None, board_id=None):
        return "ok", "session-1", 10  # tiny output-token count

    board_id, task_id = "p55-spend-input-board", "p55-spend-input-task"
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO boards (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (board_id, f"board-{board_id}"),
        )
        cur.execute(
            "INSERT INTO tasks (id, board_id, title, status, body) "
            "VALUES (%s, %s, 'test task', 'ready', %s) ON CONFLICT DO NOTHING",
            (task_id, board_id, long_task_body),
        )
    admin_conn.commit()

    # Cap chosen so output-only pricing could NOT trip it: output spend is
    # (10/1000)*1.0 == $0.01 < $0.50, while the input estimate alone is
    # (len(prompt)//4)/1000 * 1.0 == $2.00 > $0.50. If the input estimate
    # were dropped from pricing, no exception would be raised and this test
    # would fail — unlike an arithmetic-only assertion on the fixture.
    prompt_len = len(long_task_body)  # rules block is empty, so prompt == task_body
    expected_input_spend = (prompt_len // 4) / 1000.0 * price["input_per_1k_usd"]
    assert expected_input_spend > 0.5, "test fixture didn't make input dominate — fix the fixture"

    with mock.patch.object(talos.llm, "call_model", small_output_call_model):
        with pytest.raises(BudgetExhaustedError) as excinfo:
            claim_and_run(board_id, task_id, initial_budget={**default_budget(), "max_spend_usd": 0.5})

    assert excinfo.value.axis == "spend"

    # See test_budget_elapsed_cap_end_to_end's comment -- avoids leaving an
    # orphaned 'running' task_runs row for a later test's global reclaim scan.
    _handle_budget_exhaustion(excinfo.value)


# ---------------------------------------------------------------------------
# P5.5 — legacy max_tool_calls alias
# ---------------------------------------------------------------------------

def test_max_tool_calls_legacy_alias_folds_into_max_model_invocations():
    from talos.graph.spine import _normalize_budget_aliases

    legacy = {**default_budget(), "max_tool_calls": 5}
    del legacy["max_model_invocations"]
    normalized = _normalize_budget_aliases(legacy)
    assert normalized["max_model_invocations"] == 5


def test_max_tool_calls_legacy_alias_does_not_override_explicit_new_key():
    legacy = {**default_budget(), "max_tool_calls": 5, "max_model_invocations": 9}
    from talos.graph.spine import _normalize_budget_aliases

    normalized = _normalize_budget_aliases(legacy)
    assert normalized["max_model_invocations"] == 9


# ---------------------------------------------------------------------------
# P5.5 — _check_budget helper covers all four axes directly
# ---------------------------------------------------------------------------

def test_check_budget_helper_covers_all_four_axes():
    import datetime as _dt

    from talos.graph.spine import _check_budget

    state = {"task_id": "t", "run_id": 1, "board_id": "b", "run_started_at": None}

    with pytest.raises(BudgetExhaustedError) as exc:
        _check_budget(state, {**default_budget(), "max_tokens": 1, "tokens_used": 2})
    assert exc.value.axis == "tokens"

    long_ago = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=100)).isoformat()
    elapsed_state = {**state, "run_started_at": long_ago}
    with pytest.raises(BudgetExhaustedError) as exc:
        _check_budget(elapsed_state, {**default_budget(), "max_elapsed_seconds": 1})
    assert exc.value.axis == "elapsed"

    with pytest.raises(BudgetExhaustedError) as exc:
        _check_budget(state, {**default_budget(), "max_spend_usd": 0.01, "spent_usd": 0.02})
    assert exc.value.axis == "spend"

    with pytest.raises(BudgetExhaustedError) as exc:
        _check_budget(state, {**default_budget(), "max_model_invocations": 1, "model_invocations": 2})
    assert exc.value.axis == "model_invocations"

    # Passing budget never raises.
    _check_budget(state, default_budget())
