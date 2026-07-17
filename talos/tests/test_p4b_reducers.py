"""
P4b — commutative/associative reducer proofs (RT-21 / DoD #3) and the
read fan-out (dispatch_reads -> read_node | read_branch_nexus_secondary |
read_branch_chroma | read_branch_rules -> merge_node). The 4th branch
(read_branch_rules) was added in P5 -- see talos/tests/test_p5_retrieval.py
for its own degradation/labeling tests.
"""
from __future__ import annotations

import functools
import os
import uuid

import psycopg2.extras
import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Send

from talos.graph.reducers import merge_budget, merge_disjoint_dicts
from talos.graph.spine import (
    STUB_DOCUMENT,
    build_graph,
    default_budget,
    dispatch_reads,
    read_branch_chroma,
)


# ---------------------------------------------------------------------------
# merge_disjoint_dicts — commutativity / associativity / edges
# ---------------------------------------------------------------------------

def test_merge_disjoint_dicts_commutative():
    a, b = {"nexus_primary": {"tag": "X"}}, {"chroma": {"chunks": []}}
    assert merge_disjoint_dicts(a, b) == merge_disjoint_dicts(b, a)


def test_merge_disjoint_dicts_associative():
    a = {"nexus_primary": {"tag": "X"}}
    b = {"nexus_secondary": {"tag": "Y"}}
    c = {"chroma": {"chunks": [1, 2]}}
    left_assoc = merge_disjoint_dicts(merge_disjoint_dicts(a, b), c)
    right_assoc = merge_disjoint_dicts(a, merge_disjoint_dicts(b, c))
    assert left_assoc == right_assoc


def test_merge_disjoint_dicts_empty_and_none_edges():
    assert merge_disjoint_dicts(None, {"a": 1}) == {"a": 1}
    assert merge_disjoint_dicts({"a": 1}, None) == {"a": 1}
    assert merge_disjoint_dicts(None, None) == {}
    assert merge_disjoint_dicts({}, {}) == {}


# ---------------------------------------------------------------------------
# merge_budget — commutativity / associativity / edges
#
# Every real writer (talos.graph.spine._budget_delta) carries the limit fields
# forward unchanged alongside its accumulator delta — LangGraph applies this
# reducer pairwise in no guaranteed order, so a "limits only on one side"
# shape would NOT actually be commutative (see reducers.py's docstring). These
# tests build deltas the same way real branches do, from a shared baseline.
# ---------------------------------------------------------------------------

def _delta(baseline, spent=0.0, tokens=0, calls=0):
    return {
        **{k: baseline[k] for k in (
            "max_spend_usd", "max_tokens", "max_model_invocations",
            "max_elapsed_seconds", "soft_spend_usd",
        )},
        "spent_usd": spent, "tokens_used": tokens, "model_invocations": calls,
    }


def test_merge_budget_commutative():
    baseline = default_budget()
    a = _delta(baseline, spent=1.0, tokens=10, calls=1)
    b = _delta(baseline, spent=2.0, tokens=20, calls=1)
    left = merge_budget(a, b)
    right = merge_budget(b, a)
    assert left == right
    assert left["spent_usd"] == 3.0
    assert left["tokens_used"] == 30
    assert left["model_invocations"] == 2


def test_merge_budget_associative():
    baseline = default_budget()
    a, b, c = _delta(baseline, calls=1), _delta(baseline, calls=1), _delta(baseline, calls=1)
    left_assoc = merge_budget(merge_budget(a, b), c)
    right_assoc = merge_budget(a, merge_budget(b, c))
    assert left_assoc == right_assoc
    assert left_assoc["model_invocations"] == 3


def test_merge_budget_none_and_zero_edges():
    baseline = default_budget()
    assert merge_budget(None, baseline) == baseline
    assert merge_budget(baseline, None) == baseline
    merged = merge_budget(baseline, _delta(baseline))
    assert merged["model_invocations"] == baseline["model_invocations"]


def test_merge_budget_sums_across_three_simulated_branches_limits_unchanged():
    baseline = {**default_budget(), "max_tokens": 100}
    branches = [_delta(baseline, calls=1), _delta(baseline, calls=1), _delta(baseline, calls=1)]
    merged = functools.reduce(merge_budget, branches, baseline)
    assert merged["model_invocations"] == 3
    assert merged["max_tokens"] == 100  # limit field untouched by branch deltas


def test_merge_budget_is_commutative_even_when_a_bare_delta_is_left_operand():
    """
    Regression guard: the original (buggy) implementation copied limit fields
    via `dict(left)`, silently dropping them whenever a bare delta happened to
    land as `left` in a pairwise fold — this is exactly the bug that broke
    read_node's real-mode budget checks (KeyError: 'max_tokens') once a
    sibling branch's delta was folded in before read_node's own contribution.
    """
    baseline = {**default_budget(), "max_tokens": 500}
    full_delta = _delta(baseline, calls=1)
    merged = merge_budget(full_delta, baseline)  # bare-shaped delta as `left`
    assert merged["max_tokens"] == 500
    assert merged["model_invocations"] == baseline["model_invocations"] + 1


# ---------------------------------------------------------------------------
# read_branch_chroma — degrades to empty on failure
# ---------------------------------------------------------------------------

def test_read_branch_chroma_degrades_to_empty_on_embedding_failure(monkeypatch):
    def _raise(*a, **k):
        raise RuntimeError("no embedding model cached")

    monkeypatch.setattr("talos.memory.pgvector_store.query", _raise)
    state = {"board_id": "b", "task_id": "t", "run_id": 0, "task_body": None}
    result = read_branch_chroma(state)
    assert result["context_branches"] == {"chroma": {"chunks": []}}
    # P5.5: vector queries are not model invocations — this branch contributes 0.
    assert result["budget"]["model_invocations"] == 0
    assert result["budget"]["spent_usd"] == 0.0
    assert result["budget"]["tokens_used"] == 0
    assert result["budget"]["max_tokens"] == default_budget()["max_tokens"]


# ---------------------------------------------------------------------------
# 3-branch order-independence (explicit orderings, not scheduling luck)
# ---------------------------------------------------------------------------

def test_three_branch_order_independence_integration():
    baseline = default_budget()
    branch_a = {
        "context_branches": {"nexus_primary": {"tag": "MOCK_TAG"}},
        "sdk_session_ids": {"read_node": "sid-a"},
        "budget": _delta(baseline, tokens=5, calls=1),
    }
    branch_b = {
        "context_branches": {"nexus_secondary": {"tag": "MOCK_TAG_SUPPLEMENTAL"}},
        "sdk_session_ids": {"nexus_secondary": "sid-b"},
        "budget": _delta(baseline, tokens=3, calls=1),
    }
    branch_c = {
        "context_branches": {"chroma": {"chunks": ["chunk-1"]}},
        "budget": _delta(baseline, tokens=0, calls=1),
    }

    def fold(order):
        ctx, sids, bud = {}, {}, None
        for br in order:
            ctx = merge_disjoint_dicts(ctx, br.get("context_branches"))
            sids = merge_disjoint_dicts(sids, br.get("sdk_session_ids"))
            bud = merge_budget(bud, br.get("budget"))
        return ctx, sids, bud

    order1 = [branch_a, branch_b, branch_c]
    order2 = [branch_c, branch_a, branch_b]

    ctx1, sids1, bud1 = fold(order1)
    ctx2, sids2, bud2 = fold(order2)

    assert ctx1 == ctx2
    assert sids1 == sids2
    assert bud1 == bud2
    assert bud1["model_invocations"] == 3
    assert bud1["tokens_used"] == 8


# ---------------------------------------------------------------------------
# dispatch_reads — 3 Send objects under stub mode
# ---------------------------------------------------------------------------

def test_dispatch_reads_returns_four_sends(monkeypatch):
    monkeypatch.setenv("TALOS_NEXUS_STUB", "1")
    state = {"board_id": "b", "task_id": "t", "run_id": 0, "task_body": None}
    sends = dispatch_reads(state)
    assert len(sends) == 4
    assert all(isinstance(s, Send) for s in sends)
    assert {s.node for s in sends} == {
        "read_node", "read_branch_nexus_secondary", "read_branch_chroma", "read_branch_rules",
    }


# ---------------------------------------------------------------------------
# Full compiled-graph invoke, stub mode — behaviorally identical to the old
# linear path, plus real multi-writer accumulation
# ---------------------------------------------------------------------------

def _seed_board_and_task(conn, board_id: str, task_id: str) -> None:
    conn.autocommit = False
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO boards (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (board_id, f"Board {board_id}"),
        )
        cur.execute(
            """
            INSERT INTO tasks (id, board_id, title, status)
            VALUES (%s, %s, %s, 'ready')
            ON CONFLICT DO NOTHING
            """,
            (task_id, board_id, f"Task {task_id}"),
        )
    conn.commit()


def test_full_graph_invoke_stub_mode_end_to_end(pg_setup, admin_conn, monkeypatch):
    monkeypatch.setenv("TALOS_NEXUS_STUB", "1")
    board_id = f"b-{uuid.uuid4().hex[:8]}"
    task_id = f"t-{uuid.uuid4().hex[:8]}"
    _seed_board_and_task(admin_conn, board_id, task_id)

    saver = MemorySaver()
    graph = build_graph(checkpointer=saver)

    initial_state = {
        "board_id": board_id,
        "task_id": task_id,
        "attempt_no": 1,
        "run_id": 0,
        "session_key": f"task:{board_id}:{task_id}:1",
        "nexus_result": {},
        "deliverable": {},
        "critic_results": [],
        "gate_outcome": None,
        "approved_by": None,
        "edited_deliverable": None,
        "gate_justification": None,
        "sdk_session_ids": {},
        "budget": default_budget(),
        "task_body": None,
        "context_branches": {},
        "chroma_chunks": [],
        "nexus_supplemental": [],
        "rule_context": [],
    }

    graph.invoke(initial_state, config={"configurable": {"thread_id": f"thread-{task_id}"}})

    checkpoint = saver.get({"configurable": {"thread_id": f"thread-{task_id}"}})
    state = checkpoint["channel_values"]

    assert state["nexus_result"] == {"document": STUB_DOCUMENT, "status": "confirmed"}
    assert state["deliverable"]["citations"][0]["status"] == "confirmed"
    assert state["budget"]["model_invocations"] == 2, (
        "expected read_node + nexus_secondary stub model calls only (chroma/rules "
        f"vector queries contribute 0 — P5.5), got {state['budget']}"
    )
    assert state["sdk_session_ids"]["read_node"] == "stub-session-id"
    assert state["sdk_session_ids"]["nexus_secondary"] == "stub-session-id-secondary"
