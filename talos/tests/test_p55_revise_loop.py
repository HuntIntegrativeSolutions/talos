"""
P5.5 item 2 — bounded critic-fail -> revise loop (talos.graph.spine.deliverable_node).

Critic failures are forced by monkeypatching talos.graph.spine.run_all_critics
directly (the spine-local binding), not by registering fake critics into the
module-global talos.critics.registry -- Part 1's debugging saga (orphaned
task_runs rows leaking into an unrelated test's global reclaim_dead_workers()
scan) showed how easily module-global state causes cross-test leakage.
Monkeypatching the spine-local binding is fully test-scoped and needs no
teardown/registry-mutation risk.

Revision model calls go through talos.llm.call_model, mocked per test (same
convention as test_p35_harness.py's nexus_live_mode tests) -- this works
regardless of TALOS_NEXUS_STUB, since mock.patch.object replaces the function
outright rather than routing through its internal stub-mode check.
"""

from __future__ import annotations

import psycopg2.extras
import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

import talos.llm
from talos.errors import BudgetExhaustedError
from talos.graph import spine as spine_module
from talos.graph.spine import (
    _build_revision_prompt,
    _deliverable_hash,
    _parse_revised_deliverable,
    build_graph,
    default_budget,
)
from talos.worker import _handle_budget_exhaustion, claim_and_run


@pytest.fixture(scope="module", autouse=True)
def _stub_embedding_backed_reads_module_wide():
    """
    P5.5: read_branch_chroma / read_node's and _revise_deliverable's rules
    prompt-injection queries always attempt a real get_embed_fn() call
    regardless of stub mode -- without a local embedding model pre-downloaded,
    that raises after a real, non-instant resolution attempt. Stubbed
    module-wide so every claim_and_run() call in this file stays fast and
    network-independent (matches test_p35_harness.py's precedent).
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


def _verdict(name: str, passed: bool, *, safety_class: bool = False, waivable: bool = True, reason: str = "") -> dict:
    return {
        "name": name,
        "required": True,
        "safety_class": safety_class,
        "waivable": waivable,
        "passed": passed,
        "reason": reason,
        "verdict": "pass" if passed else "fail",
    }


def _task_events(admin_conn, task_id: str, kind: str) -> list[dict]:
    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT payload FROM task_events WHERE task_id = %s AND kind = %s ORDER BY id",
            (task_id, kind),
        )
        return [row["payload"] for row in cur.fetchall()]


def _make_call_model(responses):
    """responses: list of (text, tokens) consumed in order, one per call."""
    calls: list[int] = []

    def fake_call_model(model, prompt, resume=None, *, span_ctx=None, allowed_tools=None,
                         mcp_servers=None, manifest=None, budget_check=None, board_id=None):
        idx = len(calls)
        calls.append(1)
        text, tokens = responses[idx]
        if budget_check is not None:
            budget_check(tokens, 0)
        return text, f"session-{idx}", tokens

    fake_call_model.calls = calls
    return fake_call_model


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------

def test_deliverable_hash_stable_across_key_order():
    a = {"summary": "x", "citations": [{"a": 1, "b": 2}]}
    b = {"citations": [{"b": 2, "a": 1}], "summary": "x"}
    assert _deliverable_hash(a) == _deliverable_hash(b)


def test_deliverable_hash_differs_on_content_change():
    a = {"summary": "x"}
    b = {"summary": "y"}
    assert _deliverable_hash(a) != _deliverable_hash(b)


def test_parse_revised_deliverable_valid_json():
    assert _parse_revised_deliverable('{"summary": "fixed"}') == {"summary": "fixed"}


def test_parse_revised_deliverable_code_fenced_json():
    text = '```json\n{"summary": "fixed"}\n```'
    assert _parse_revised_deliverable(text) == {"summary": "fixed"}


def test_parse_revised_deliverable_invalid_text_returns_none():
    assert _parse_revised_deliverable("not json at all") is None


def test_parse_revised_deliverable_non_dict_json_returns_none():
    assert _parse_revised_deliverable("[1, 2, 3]") is None


def test_build_revision_prompt_includes_failing_critics_and_rules_block():
    deliverable = {"summary": "bad"}
    failing = [{"name": "some_critic", "reason": "because reasons"}]
    prompt = _build_revision_prompt(deliverable, failing, "Board rules learned from prior approved work:\n- rule 1")
    assert "some_critic" in prompt
    assert "because reasons" in prompt
    assert "Board rules learned from prior approved work:" in prompt


def test_build_revision_prompt_omits_rules_section_when_empty():
    prompt = _build_revision_prompt({"summary": "bad"}, [{"name": "c", "reason": "r"}], "")
    assert "Board rules learned" not in prompt


# ---------------------------------------------------------------------------
# End-to-end revise-loop scenarios
# ---------------------------------------------------------------------------

def test_advisory_failure_triggers_one_revise_then_passes(pg_setup, admin_conn, monkeypatch):
    board_id, task_id = "revise-board-1", "revise-task-1"
    with admin_conn.cursor() as cur:
        _seed_board_and_task(cur, board_id, task_id)
    admin_conn.commit()

    def fake_run_all_critics(deliverable, nexus_client=None, client_identifiers=None):
        if deliverable.get("summary") == "fixed":
            return [_verdict("advisory_check", True)]
        return [_verdict("advisory_check", False, reason="bad summary")]

    monkeypatch.setattr(spine_module, "run_all_critics", fake_run_all_critics)
    fake_call_model = _make_call_model([('{"summary": "fixed", "citations": []}', 5)])
    monkeypatch.setattr(talos.llm, "call_model", fake_call_model)

    graph = build_graph(checkpointer=MemorySaver())
    session_key = claim_and_run(board_id, task_id, graph=graph)
    config = {"configurable": {"thread_id": session_key}}
    state = graph.get_state(config).values

    assert state["revise_history"] == [
        {"attempt_no": 1, "failing_critics": [], "outcome": "revised"}
    ]
    assert len(fake_call_model.calls) == 1

    attempted = _task_events(admin_conn, task_id, "revise_attempted")
    result = _task_events(admin_conn, task_id, "revise_result")
    assert attempted == [{"attempt_no": 1, "failing_critics": ["advisory_check"]}]
    assert result == [{"attempt_no": 1, "outcome": "revised", "still_failing_critics": []}]

    # Read fan-out baseline (2: read_node + nexus_secondary; chroma/rules contribute
    # 0 -- see test_p4b_reducers.py) + exactly this one revise attempt.
    assert state["budget"]["model_invocations"] == 3

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT status, deliverable FROM tasks WHERE id = %s", (task_id,))
        row = cur.fetchone()
        assert row["status"] == "review"
        assert row["deliverable"]["summary"] == "fixed"


def test_safety_failure_bypasses_revision_entirely(pg_setup, admin_conn, monkeypatch):
    board_id, task_id = "revise-board-2", "revise-task-2"
    with admin_conn.cursor() as cur:
        _seed_board_and_task(cur, board_id, task_id)
    admin_conn.commit()

    def fake_run_all_critics(deliverable, nexus_client=None, client_identifiers=None):
        return [_verdict("no_live_write_in_deliverable", False, safety_class=True, waivable=False, reason="unsafe")]

    monkeypatch.setattr(spine_module, "run_all_critics", fake_run_all_critics)
    fake_call_model = _make_call_model([('{"summary": "fixed"}', 5)])
    monkeypatch.setattr(talos.llm, "call_model", fake_call_model)

    graph = build_graph(checkpointer=MemorySaver())
    session_key = claim_and_run(board_id, task_id, graph=graph)
    config = {"configurable": {"thread_id": session_key}}
    state = graph.get_state(config).values

    assert state["revise_history"] == [
        {"attempt_no": 0, "failing_critics": ["no_live_write_in_deliverable"], "outcome": "safety_bypass"}
    ]
    assert len(fake_call_model.calls) == 0
    assert _task_events(admin_conn, task_id, "revise_attempted") == []
    assert _task_events(admin_conn, task_id, "revise_result") == [
        {"attempt_no": 0, "outcome": "safety_bypass", "still_failing_critics": ["no_live_write_in_deliverable"]}
    ]
    # No revise attempt was made, so deliverable_node contributed no budget write --
    # the channel value is exactly the read fan-out's baseline (2: read_node +
    # nexus_secondary; chroma/rules contribute 0 -- see test_p4b_reducers.py).
    assert state["budget"]["model_invocations"] == 2

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT status FROM tasks WHERE id = %s", (task_id,))
        assert cur.fetchone()["status"] == "review"


def test_cap_exhaustion_proceeds_to_gate_with_all_verdicts(pg_setup, admin_conn, monkeypatch):
    board_id, task_id = "revise-board-3", "revise-task-3"
    with admin_conn.cursor() as cur:
        _seed_board_and_task(cur, board_id, task_id)
    admin_conn.commit()

    def fake_run_all_critics(deliverable, nexus_client=None, client_identifiers=None):
        return [_verdict("never_passes", False, reason="always broken")]

    monkeypatch.setattr(spine_module, "run_all_critics", fake_run_all_critics)
    fake_call_model = _make_call_model([
        ('{"summary": "attempt-1"}', 5),
        ('{"summary": "attempt-2"}', 5),
    ])
    monkeypatch.setattr(talos.llm, "call_model", fake_call_model)

    graph = build_graph(checkpointer=MemorySaver())
    session_key = claim_and_run(board_id, task_id, graph=graph)
    config = {"configurable": {"thread_id": session_key}}
    state = graph.get_state(config).values

    assert len(fake_call_model.calls) == 2
    assert state["revise_history"][-1]["outcome"] == "gave_up_cap"
    assert len(state["revise_history"]) == 3  # attempt 1, attempt 2, gave_up_cap

    attempted = _task_events(admin_conn, task_id, "revise_attempted")
    result = _task_events(admin_conn, task_id, "revise_result")
    assert len(attempted) == 2
    assert len(result) == 3
    assert result[-1]["outcome"] == "gave_up_cap"

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT status FROM tasks WHERE id = %s", (task_id,))
        assert cur.fetchone()["status"] == "review"


def test_identical_deliverable_short_circuits(pg_setup, admin_conn, monkeypatch):
    board_id, task_id = "revise-board-4", "revise-task-4"
    with admin_conn.cursor() as cur:
        _seed_board_and_task(cur, board_id, task_id)
    admin_conn.commit()

    def fake_run_all_critics(deliverable, nexus_client=None, client_identifiers=None):
        return [_verdict("never_passes", False, reason="always broken")]

    monkeypatch.setattr(spine_module, "run_all_critics", fake_run_all_critics)
    # Byte-identical revision text on every attempt -- same failing critic set both times.
    fake_call_model = _make_call_model([
        ('{"summary": "same"}', 5),
        ('{"summary": "same"}', 5),
    ])
    monkeypatch.setattr(talos.llm, "call_model", fake_call_model)

    import talos.config as config_module
    monkeypatch.setattr(
        config_module, "get_resources_config",
        lambda: {**config_module._RESOURCES_DEFAULTS, "revise_max_iterations": 3},
    )

    graph = build_graph(checkpointer=MemorySaver())
    session_key = claim_and_run(board_id, task_id, graph=graph)
    config = {"configurable": {"thread_id": session_key}}
    state = graph.get_state(config).values

    # Cap is 3, but only 2 attempts should run -- the 2nd is byte-identical to the 1st.
    assert len(fake_call_model.calls) == 2
    assert state["revise_history"][-1]["outcome"] == "short_circuited_identical"
    assert state["revise_history"][-1]["attempt_no"] == 2


def test_gate_status_reflects_pass_after_successful_revision(pg_setup, admin_conn, monkeypatch):
    board_id, task_id = "revise-board-5", "revise-task-5"
    with admin_conn.cursor() as cur:
        _seed_board_and_task(cur, board_id, task_id)
    admin_conn.commit()

    def fake_run_all_critics(deliverable, nexus_client=None, client_identifiers=None):
        if deliverable.get("summary") == "fixed":
            return [_verdict("advisory_check", True)]
        return [_verdict("advisory_check", False, reason="bad summary")]

    monkeypatch.setattr(spine_module, "run_all_critics", fake_run_all_critics)
    fake_call_model = _make_call_model([('{"summary": "fixed"}', 5)])
    monkeypatch.setattr(talos.llm, "call_model", fake_call_model)

    graph = build_graph(checkpointer=MemorySaver())
    claim_and_run(board_id, task_id, graph=graph)

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT all_required_pass FROM v_gate_status WHERE task_id = %s AND board_id = %s", (task_id, board_id))
        row = cur.fetchone()
        assert row is not None
        assert row["all_required_pass"] is True


def test_budget_exhaustion_mid_revise_escalates_with_history_persisted(pg_setup, admin_conn, monkeypatch):
    board_id, task_id = "revise-board-6", "revise-task-6"
    with admin_conn.cursor() as cur:
        _seed_board_and_task(cur, board_id, task_id)
    admin_conn.commit()

    def fake_run_all_critics(deliverable, nexus_client=None, client_identifiers=None):
        return [_verdict("never_passes", False, reason="always broken")]

    monkeypatch.setattr(spine_module, "run_all_critics", fake_run_all_critics)
    # Huge output price, zero input price -- spend is driven purely by `tokens`,
    # independent of prompt-length estimation, for a deterministic trip point.
    monkeypatch.setattr(
        "talos.config.get_pricing_config",
        lambda: {"anthropic:claude-sonnet-4-6": {"input_per_1k_usd": 0.0, "output_per_1k_usd": 1.0}},
    )
    fake_call_model = _make_call_model([
        ('{"summary": "attempt-1"}', 15),   # spend = 0.015, under the 0.02 cap
        ('{"summary": "attempt-2"}', 15),   # cumulative spend = 0.030, over the 0.02 cap
    ])
    monkeypatch.setattr(talos.llm, "call_model", fake_call_model)

    tiny_budget = {**default_budget(), "max_spend_usd": 0.02}
    graph = build_graph(checkpointer=MemorySaver())

    with pytest.raises(BudgetExhaustedError) as excinfo:
        claim_and_run(board_id, task_id, graph=graph, initial_budget=tiny_budget)

    assert excinfo.value.axis == "spend"
    _handle_budget_exhaustion(excinfo.value)  # avoid leaving an orphaned running task_run (see Part 1 postmortem)

    # First attempt's audit trail must already be durably committed even
    # though the graph run itself aborted mid-second-attempt.
    attempted = _task_events(admin_conn, task_id, "revise_attempted")
    assert len(attempted) >= 1
    assert attempted[0] == {"attempt_no": 1, "failing_critics": ["never_passes"]}

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM task_gate_results WHERE task_id = %s",
            (task_id,),
        )
        assert cur.fetchone()["cnt"] >= 2  # initial run + at least the first revise attempt's run

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT status FROM tasks WHERE id = %s", (task_id,))
        assert cur.fetchone()["status"] == "review"


def test_revise_max_iterations_zero_restores_today_exact_behavior(pg_setup, admin_conn, monkeypatch):
    board_id, task_id = "revise-board-7", "revise-task-7"
    with admin_conn.cursor() as cur:
        _seed_board_and_task(cur, board_id, task_id)
    admin_conn.commit()

    call_count: list[int] = []

    def fake_run_all_critics(deliverable, nexus_client=None, client_identifiers=None):
        call_count.append(1)
        return [_verdict("never_passes", False, reason="always broken")]

    monkeypatch.setattr(spine_module, "run_all_critics", fake_run_all_critics)
    fake_call_model = _make_call_model([('{"summary": "attempt-1"}', 5)])
    monkeypatch.setattr(talos.llm, "call_model", fake_call_model)

    import talos.config as config_module
    monkeypatch.setattr(
        config_module, "get_resources_config",
        lambda: {**config_module._RESOURCES_DEFAULTS, "revise_max_iterations": 0},
    )

    graph = build_graph(checkpointer=MemorySaver())
    session_key = claim_and_run(board_id, task_id, graph=graph)
    config = {"configurable": {"thread_id": session_key}}
    state = graph.get_state(config).values

    assert state["revise_history"] == []
    assert len(call_count) == 1  # run_all_critics called exactly once, no revision attempted
    assert len(fake_call_model.calls) == 0
    assert _task_events(admin_conn, task_id, "revise_attempted") == []
    assert _task_events(admin_conn, task_id, "revise_result") == []
    # state["budget"]["model_invocations"] is exactly what the read fan-out alone
    # produced (2: read_node + nexus_secondary; chroma/rules contribute 0 -- see
    # test_p4b_reducers.py) -- deliverable_node contributed no budget write at all
    # on this no-revise pass.
    assert state["budget"]["model_invocations"] == 2

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT status FROM tasks WHERE id = %s", (task_id,))
        assert cur.fetchone()["status"] == "review"


def test_revise_budget_accumulates_as_delta_not_total_across_two_attempts(pg_setup, admin_conn, monkeypatch):
    board_id, task_id = "revise-board-8", "revise-task-8"
    with admin_conn.cursor() as cur:
        _seed_board_and_task(cur, board_id, task_id)
    admin_conn.commit()

    def fake_run_all_critics(deliverable, nexus_client=None, client_identifiers=None):
        return [_verdict("never_passes", False, reason="always broken")]

    monkeypatch.setattr(spine_module, "run_all_critics", fake_run_all_critics)
    fake_call_model = _make_call_model([
        ('{"summary": "attempt-1"}', 5),
        ('{"summary": "attempt-2"}', 5),
    ])
    monkeypatch.setattr(talos.llm, "call_model", fake_call_model)

    graph = build_graph(checkpointer=MemorySaver())
    session_key = claim_and_run(board_id, task_id, graph=graph)
    config = {"configurable": {"thread_id": session_key}}
    state = graph.get_state(config).values

    baseline_from_reads = 2  # read_node + nexus_secondary only; chroma/rules contribute 0 (test_p4b_reducers.py precedent)
    assert state["budget"]["model_invocations"] == baseline_from_reads + 2, (
        f"expected exactly +2 (one per revise attempt, as a delta) on top of the read "
        f"fan-out's baseline, got {state['budget']['model_invocations']} -- a reducer "
        f"double-count would produce +4/+6/etc instead"
    )


def test_edit_loop_reentry_gets_full_revise_behavior(pg_setup, admin_conn, monkeypatch):
    board_id, task_id = "revise-board-9", "revise-task-9"
    with admin_conn.cursor() as cur:
        _seed_board_and_task(cur, board_id, task_id)
    admin_conn.commit()

    def fake_run_all_critics(deliverable, nexus_client=None, client_identifiers=None):
        summary = deliverable.get("summary", "")
        # The initial (pre-edit) pass's default deliverable always passes --
        # only the human-edited deliverable fails, so it's the edit-loop
        # re-entry's revise attempt we're proving here, not the first pass's.
        if summary.startswith("Tag context retrieved") or summary == "fixed-after-edit":
            return [_verdict("advisory_check", True)]
        return [_verdict("advisory_check", False, reason="human edit still broken")]

    monkeypatch.setattr(spine_module, "run_all_critics", fake_run_all_critics)
    fake_call_model = _make_call_model([('{"summary": "fixed-after-edit"}', 5)])
    monkeypatch.setattr(talos.llm, "call_model", fake_call_model)

    graph = build_graph(checkpointer=MemorySaver())
    session_key = claim_and_run(board_id, task_id, graph=graph)
    config = {"configurable": {"thread_id": session_key}}

    graph.invoke(
        Command(resume={
            "outcome": "edit",
            "approved_by": "test-human",
            "new_deliverable": {"summary": "human-edit-still-broken"},
        }),
        config=config,
    )

    state = graph.get_state(config).values
    assert len(fake_call_model.calls) == 1
    assert state["revise_history"] == [
        {"attempt_no": 1, "failing_critics": [], "outcome": "revised"}
    ]

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT deliverable FROM tasks WHERE id = %s", (task_id,))
        assert cur.fetchone()["deliverable"]["summary"] == "fixed-after-edit"


def test_parse_failure_stops_loop_and_still_counts_burned_usage(pg_setup, admin_conn, monkeypatch):
    """
    Regression (review fix): a revise attempt whose output fails to parse
    still burned real tokens on the model call -- the returned budget delta
    must include that attempt's tokens/spend/model_invocation, not silently
    drop them because the parse failed after the fact.
    """
    board_id, task_id = "revise-board-10", "revise-task-10"
    with admin_conn.cursor() as cur:
        _seed_board_and_task(cur, board_id, task_id)
    admin_conn.commit()

    def fake_run_all_critics(deliverable, nexus_client=None, client_identifiers=None):
        return [_verdict("never_passes", False, reason="always broken")]

    monkeypatch.setattr(spine_module, "run_all_critics", fake_run_all_critics)
    fake_call_model = _make_call_model([("this is not json at all", 7)])
    monkeypatch.setattr(talos.llm, "call_model", fake_call_model)

    graph = build_graph(checkpointer=MemorySaver())
    session_key = claim_and_run(board_id, task_id, graph=graph)
    config = {"configurable": {"thread_id": session_key}}
    state = graph.get_state(config).values

    # Loop stops on the first parse failure -- no second attempt despite cap=2.
    assert len(fake_call_model.calls) == 1
    assert state["revise_history"][-1]["outcome"] == "gave_up_parse_failure"
    assert _task_events(admin_conn, task_id, "revise_result")[-1]["outcome"] == "gave_up_parse_failure"

    # Burned usage is still accounted: read fan-out baseline (read_node +
    # nexus_secondary stubs = 2; chroma/rules contribute 0) plus the one
    # failed revise attempt, and its 7 output tokens (stub fan-out adds none).
    assert state["budget"]["model_invocations"] == 2 + 1
    assert state["budget"]["tokens_used"] == 7
