"""
P6 / ADR-021 — verifier critic type: registry invariants, run_all_verifiers'
failure-behavior table, task_origin.extract_rubrics' rubric marker parsing,
and talos.graph.spine.deliverable_node wiring.

Registry-level tests use an isolated_verifier_registry fixture (save/clear/
restore talos.critics.registry._verifier_registry) so registering a
throwaway verifier in one test never leaks into another -- mirrors the
module-global leakage concern documented in test_p55_revise_loop.py, applied
to the verifier registry instead of the deterministic-critic one.

Spine wiring tests mock talos.llm.call_model (test_p55_revise_loop.py's
_make_call_model convention) and use the same pg_setup/admin_conn fixtures
and _seed_board_and_task/_task_events helpers.
"""

from __future__ import annotations

import logging

import psycopg2.extras
import pytest
from langgraph.checkpoint.memory import MemorySaver

import talos.llm
from talos.critics.registry import (
    VerifierSpec,
    _verifier_registry,
    get_all_verifiers,
    get_verifier,
    register_verifier,
    run_all_verifiers,
)
from talos.graph import spine as spine_module
from talos.graph.spine import (
    _parse_verifier_model,
    _parse_verifier_response,
    build_graph,
)
from talos.task_origin import extract_rubrics
from talos.worker import claim_and_run


@pytest.fixture()
def isolated_verifier_registry():
    """Save/clear/restore _verifier_registry so each test starts from an
    empty registry (including rubric_compliance's own module-load entry,
    which would otherwise also match rubric_field="rubric" test fixtures)
    and a test-registered verifier never leaks across tests."""
    saved = dict(_verifier_registry)
    _verifier_registry.clear()
    yield _verifier_registry
    _verifier_registry.clear()
    _verifier_registry.update(saved)


def _spec(name="test_verifier", **overrides) -> VerifierSpec:
    fields = dict(
        name=name,
        fn=lambda *a, **kw: None,
        required=False,
        safety_class=False,
        waivable=True,
        rubric_field="rubric",
        verifier_model=None,
        score_threshold=0.8,
        advisory=True,
        fail_open=False,
    )
    fields.update(overrides)
    return VerifierSpec(**fields)


def _seed_board_and_task(cur, board_id: str, task_id: str, body: str | None = None) -> None:
    cur.execute(
        "INSERT INTO boards (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (board_id, f"board-{board_id}"),
    )
    cur.execute(
        """
        INSERT INTO tasks (id, board_id, title, status, body)
        VALUES (%s, %s, 'test task', 'ready', %s)
        ON CONFLICT DO NOTHING
        """,
        (task_id, board_id, body),
    )


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


RUBRIC_TASK_BODY = """\
Summarize the confirmed I/O points for PLC-14 into a client-shareable deliverable.

<!-- talos:rubric:rubric
The deliverable must cite a resolvable source for every claim it makes.
- every citation in "citations" must have a non-empty finding_id
- the summary must not contradict any citation's status
-->

Keep the summary under 200 words.
"""


# ---------------------------------------------------------------------------
# Registry-level: registration invariants
# ---------------------------------------------------------------------------

def test_register_verifier_raises_for_safety_class_not_advisory(isolated_verifier_registry):
    with pytest.raises(ValueError, match="advisory=True"):
        register_verifier(_spec(safety_class=True, advisory=False, waivable=False))


def test_register_verifier_raises_for_safety_class_waivable(isolated_verifier_registry):
    with pytest.raises(ValueError, match="waivable=False"):
        register_verifier(_spec(safety_class=True, advisory=True, waivable=True))


def test_register_verifier_normalizes_fail_open_when_not_advisory(isolated_verifier_registry):
    register_verifier(_spec(name="blocking_verifier", advisory=False, fail_open=True))
    stored = get_verifier("blocking_verifier")
    assert stored.advisory is False
    assert stored.fail_open is False


def test_run_all_verifiers_unaffected_by_deterministic_critic_registry(isolated_verifier_registry):
    from talos.critics.registry import get_all as get_all_critics
    before = {c.name for c in get_all_critics()}
    register_verifier(_spec(name="does_not_touch_critics"))
    after = {c.name for c in get_all_critics()}
    assert before == after


def test_registry_module_does_not_import_spine_or_llm():
    """
    Guards the DI-boundary design decision this whole landing rests on
    (see VerifierSpec's docstring): talos.critics.registry cannot import
    talos.graph.spine (spine already imports the registry) or talos.llm --
    the actual verifier LLM call is made by a spine-side score_fn closure,
    not by the registry calling out itself. A future edit that adds either
    import here would silently create a circular import or defeat the
    closure-injection design; this test fails loudly instead.
    """
    import os
    import subprocess
    import sys

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    result = subprocess.run(
        [
            sys.executable, "-c",
            "import talos.critics.registry; import sys; "
            "assert 'talos.graph.spine' not in sys.modules, 'registry must not import spine'; "
            "assert 'talos.llm' not in sys.modules, 'registry must not import talos.llm'",
        ],
        capture_output=True, text=True, cwd=repo_root,
    )
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# run_all_verifiers: score threshold, failure table, no-rubric skip
# ---------------------------------------------------------------------------

def test_score_exactly_at_threshold_passes(isolated_verifier_registry):
    register_verifier(_spec(score_threshold=0.8))
    results = run_all_verifiers(
        {}, {"rubric": "text"},
        score_fn=lambda spec, rubric: (0.8, "at threshold"),
    )
    assert results[0]["passed"] is True
    assert results[0]["verdict"] == "pass"


def test_score_just_below_threshold_fails(isolated_verifier_registry):
    register_verifier(_spec(score_threshold=0.8, advisory=True))
    results = run_all_verifiers(
        {}, {"rubric": "text"},
        score_fn=lambda spec, rubric: (0.79, "just under"),
    )
    assert results[0]["passed"] is False
    assert results[0]["verdict"] == "warn"  # advisory=True -> warn, never a blocking fail


def test_failure_table_advisory_fail_open_skips_and_logs(isolated_verifier_registry, caplog):
    register_verifier(_spec(advisory=True, fail_open=True))
    with caplog.at_level(logging.WARNING):
        results = run_all_verifiers(
            {}, {"rubric": "text"},
            score_fn=lambda spec, rubric: (None, None),
        )
    assert results == []
    assert any("fail_open=True" in r.message and "skipping" in r.message for r in caplog.records)


def test_failure_table_advisory_fail_closed_emits_warn(isolated_verifier_registry):
    register_verifier(_spec(advisory=True, fail_open=False))
    results = run_all_verifiers(
        {}, {"rubric": "text"},
        score_fn=lambda spec, rubric: (None, None),
    )
    assert len(results) == 1
    assert results[0]["verdict"] == "warn"
    assert results[0]["required"] is False


def test_failure_table_advisory_false_emits_blocking_fail(isolated_verifier_registry):
    register_verifier(_spec(advisory=False, waivable=True))
    results = run_all_verifiers(
        {}, {"rubric": "text"},
        score_fn=lambda spec, rubric: (None, None),
    )
    assert len(results) == 1
    assert results[0]["verdict"] == "fail"
    assert results[0]["required"] is True


def test_no_rubric_task_skips_verifier_entirely(isolated_verifier_registry):
    register_verifier(_spec())
    calls = []

    def score_fn(spec, rubric):
        calls.append(1)
        return 0.9, "should not be called"

    results = run_all_verifiers({}, {}, score_fn=score_fn)
    assert calls == []
    assert results == []


# ---------------------------------------------------------------------------
# _parse_verifier_response / _parse_verifier_model unit tests
# ---------------------------------------------------------------------------

def test_parse_verifier_response_valid_json():
    assert _parse_verifier_response('{"score": 0.9, "reasoning": "good"}') == (0.9, "good")


def test_parse_verifier_response_code_fenced_json():
    text = '```json\n{"score": 0.5, "reasoning": "ok"}\n```'
    assert _parse_verifier_response(text) == (0.5, "ok")


def test_parse_verifier_response_invalid_text_returns_none_none():
    assert _parse_verifier_response("not json") == (None, None)


def test_parse_verifier_response_out_of_range_score_returns_none_none():
    assert _parse_verifier_response('{"score": 1.5, "reasoning": "x"}') == (None, None)


def test_parse_verifier_response_missing_reasoning_returns_none_none():
    assert _parse_verifier_response('{"score": 0.5}') == (None, None)


def test_parse_verifier_model_provider_model_syntax():
    assert _parse_verifier_model("deepseek:deepseek-chat") == ("deepseek", "deepseek-chat")


def test_parse_verifier_model_bare_string_defaults_anthropic():
    assert _parse_verifier_model("claude-haiku-4-5-20251001") == ("anthropic", "claude-haiku-4-5-20251001")


# ---------------------------------------------------------------------------
# task_origin.extract_rubrics unit tests
# ---------------------------------------------------------------------------

def test_extract_rubrics_single_block():
    body = "prose before\n<!-- talos:rubric:rubric\nMust cite sources.\n-->\nprose after"
    assert extract_rubrics(body) == {"rubric": "Must cite sources."}


def test_extract_rubrics_no_block_returns_empty():
    assert extract_rubrics("just a plain task body, no markers") == {}
    assert extract_rubrics(None) == {}
    assert extract_rubrics("") == {}


def test_extract_rubrics_multiple_named_blocks():
    body = (
        "<!-- talos:rubric:rubric\nFirst rubric.\n-->\n"
        "some prose\n"
        "<!-- talos:rubric:other_field\nSecond rubric.\n-->\n"
    )
    assert extract_rubrics(body) == {"rubric": "First rubric.", "other_field": "Second rubric."}


def test_extract_rubrics_coexists_with_surrounding_prose():
    body = RUBRIC_TASK_BODY
    rubrics = extract_rubrics(body)
    assert rubrics == {
        "rubric": (
            "The deliverable must cite a resolvable source for every claim it makes.\n"
            "- every citation in \"citations\" must have a non-empty finding_id\n"
            "- the summary must not contradict any citation's status"
        )
    }
    # The prose that IS the generation prompt is untouched by the marker parse.
    assert "Summarize the confirmed I/O points" in body
    assert "Keep the summary under 200 words." in body


# ---------------------------------------------------------------------------
# Spine wiring: budget accounting, no-rubric default path, e2e persistence
# ---------------------------------------------------------------------------

def test_no_rubric_task_through_deliverable_node_makes_zero_verifier_calls(
    pg_setup, admin_conn, monkeypatch
):
    board_id, task_id = "verifier-board-1", "verifier-task-1"
    with admin_conn.cursor() as cur:
        _seed_board_and_task(cur, board_id, task_id, body="no rubric marker here")
    admin_conn.commit()

    fake_call_model = _make_call_model([])
    monkeypatch.setattr(talos.llm, "call_model", fake_call_model)

    graph = build_graph(checkpointer=MemorySaver())
    session_key = claim_and_run(board_id, task_id, graph=graph)
    config = {"configurable": {"thread_id": session_key}}
    state = graph.get_state(config).values

    assert len(fake_call_model.calls) == 0
    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM task_gate_results WHERE task_id = %s AND critic_name = 'rubric_compliance'",
            (task_id,),
        )
        assert cur.fetchone()["cnt"] == 0
    # Read fan-out baseline only (2: read_node + nexus_secondary) -- no revise
    # attempt, no verifier call, so deliverable_node contributes no budget write.
    assert state["budget"]["model_invocations"] == 2


def test_verifier_run_adds_exactly_one_model_invocation_to_budget_delta(
    pg_setup, admin_conn, monkeypatch
):
    board_id, task_id = "verifier-board-2", "verifier-task-2"
    with admin_conn.cursor() as cur:
        _seed_board_and_task(cur, board_id, task_id, body=RUBRIC_TASK_BODY)
    admin_conn.commit()

    monkeypatch.setattr(
        "talos.config.get_pricing_config",
        lambda: {"anthropic:claude-sonnet-4-6": {"input_per_1k_usd": 0.0, "output_per_1k_usd": 1.0}},
    )
    fake_call_model = _make_call_model([('{"score": 0.9, "reasoning": "solid"}', 20)])
    monkeypatch.setattr(talos.llm, "call_model", fake_call_model)

    graph = build_graph(checkpointer=MemorySaver())
    session_key = claim_and_run(board_id, task_id, graph=graph)
    config = {"configurable": {"thread_id": session_key}}
    state = graph.get_state(config).values

    assert len(fake_call_model.calls) == 1
    baseline_from_reads = 2  # read_node + nexus_secondary only (test_p55 precedent)
    assert state["budget"]["model_invocations"] == baseline_from_reads + 1, (
        "expected exactly +1 (one verifier call, as a delta) on top of the read "
        "fan-out's baseline -- a doubled/duplicated write would produce +2 instead"
    )
    assert state["budget"]["spent_usd"] == pytest.approx(20 / 1000.0 * 1.0)


def test_e2e_rubric_bearing_task_persists_score_and_reasoning(pg_setup, admin_conn, monkeypatch):
    board_id, task_id = "verifier-board-3", "verifier-task-3"
    with admin_conn.cursor() as cur:
        _seed_board_and_task(cur, board_id, task_id, body=RUBRIC_TASK_BODY)
    admin_conn.commit()

    fake_call_model = _make_call_model([('{"score": 0.9, "reasoning": "solid citations"}', 20)])
    monkeypatch.setattr(talos.llm, "call_model", fake_call_model)

    graph = build_graph(checkpointer=MemorySaver())
    claim_and_run(board_id, task_id, graph=graph)

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT details FROM task_gate_results
            WHERE task_id = %s AND critic_name = 'rubric_compliance'
            ORDER BY created_at DESC LIMIT 1
            """,
            (task_id,),
        )
        row = cur.fetchone()
    assert row is not None
    details = row["details"]
    assert details["score"] == 0.9
    assert details["reasoning"] == "solid citations"
    assert details["passed"] is True
    assert details["verdict"] == "pass"
    assert details["required"] is False


def test_budget_exhausted_during_verifier_scoring_propagates(pg_setup, admin_conn, monkeypatch):
    from talos.errors import BudgetExhaustedError
    from talos.graph.spine import default_budget
    from talos.worker import _handle_budget_exhaustion

    board_id, task_id = "verifier-board-4", "verifier-task-4"
    with admin_conn.cursor() as cur:
        _seed_board_and_task(cur, board_id, task_id, body=RUBRIC_TASK_BODY)
    admin_conn.commit()

    monkeypatch.setattr(
        "talos.config.get_pricing_config",
        lambda: {"anthropic:claude-sonnet-4-6": {"input_per_1k_usd": 0.0, "output_per_1k_usd": 1.0}},
    )
    # 20 output tokens * $1/1k = $0.02 spend from the verifier call alone --
    # exceeds a $0.001 cap, so the post-hoc _check_budget inside
    # _make_verifier_score_fn must trip after accounting, before returning.
    fake_call_model = _make_call_model([('{"score": 0.9, "reasoning": "solid"}', 20)])
    monkeypatch.setattr(talos.llm, "call_model", fake_call_model)

    tiny_budget = {**default_budget(), "max_spend_usd": 0.001}
    graph = build_graph(checkpointer=MemorySaver())

    with pytest.raises(BudgetExhaustedError) as excinfo:
        claim_and_run(board_id, task_id, graph=graph, initial_budget=tiny_budget)

    assert excinfo.value.axis == "spend"
    _handle_budget_exhaustion(excinfo.value)  # avoid leaving an orphaned running task_run

    # _handle_budget_exhaustion itself routes the task to 'review' (see
    # talos.worker's module docstring: "BudgetExhaustedError ... caught in
    # worker loop -> status='review'") -- the thing under test is that the
    # verifier's own row never landed, not that status stayed off 'review'.
    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT status FROM tasks WHERE id = %s", (task_id,))
        assert cur.fetchone()["status"] == "review"
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM task_gate_results WHERE task_id = %s AND critic_name = 'rubric_compliance'",
            (task_id,),
        )
        assert cur.fetchone()["cnt"] == 0


def test_advisory_false_verifier_blocks_gate_until_waived(
    pg_setup, admin_conn, monkeypatch, test_graph, human_jwt, isolated_verifier_registry
):
    import uuid
    from fastapi.testclient import TestClient
    from talos import api as api_module

    monkeypatch.setenv("TALOS_NEXUS_STUB", "1")
    register_verifier(_spec(
        name="test_blocking_verifier", advisory=False, safety_class=False,
        waivable=True, rubric_field="rubric", score_threshold=0.8,
    ))

    board_id, task_id = f"b-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    with admin_conn.cursor() as cur:
        _seed_board_and_task(cur, board_id, task_id, body=RUBRIC_TASK_BODY)
    admin_conn.commit()

    fake_call_model = _make_call_model([('{"score": 0.1, "reasoning": "misses the mark"}', 10)])
    monkeypatch.setattr(talos.llm, "call_model", fake_call_model)

    claim_and_run(board_id, task_id, graph=test_graph)

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT all_required_pass FROM v_gate_status WHERE task_id = %s AND board_id = %s",
            (task_id, board_id),
        )
        assert cur.fetchone()["all_required_pass"] is False

    client = TestClient(api_module.app)
    resp = client.post(
        f"/boards/{board_id}/tasks/{task_id}/gate",
        json={"outcome": "waive", "justification": "risk accepted for this landing"},
        headers={"X-Human-Session": human_jwt},
    )
    assert resp.status_code == 200, resp.text

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM task_gate_results WHERE task_id = %s AND critic_name = 'test_blocking_verifier' AND verdict = 'waived'",
            (task_id,),
        )
        assert cur.fetchone()["cnt"] == 1
        cur.execute("SELECT status FROM tasks WHERE id = %s", (task_id,))
        assert cur.fetchone()["status"] == "approved"


# ---------------------------------------------------------------------------
# Regression: existing rubric-less e2e suites must be untouched by this landing.
# (No new tests here -- documented as a re-run confirmation. test_p4b_*.py,
# test_p35_*.py, and test_p55_revise_loop.py's task bodies carry no
# <!-- talos:rubric:... --> marker, so extract_rubrics() returns {} and the
# entire verifier block in deliverable_node short-circuits before any
# side-effecting code runs.)
# ---------------------------------------------------------------------------
