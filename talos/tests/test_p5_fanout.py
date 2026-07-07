"""
P5-Crystallize — Piece 3: order-independence proof for the (sequential, not
parallel) extraction/dedup/contradiction pipeline.

Extraction is sequential-for-simplicity (talos/crystallize.py's module
docstring) -- there is no LangGraph reducer here, unlike P4b's read fan-out
(talos/tests/test_p4b_reducers.py). What must be proven order-independent is
the dedup/contradiction candidate set: because the contradiction scan
excludes rows inserted earlier in THIS run, two same-run candidates can never
supersede each other -- the rule_ingestion_log dedup-key gate is what
resolves literal same-batch duplicates. So the invariant these tests prove is
the final live-content set and the dedup-key count, not "which row wins a
same-batch tie" (there is no such tie to resolve).
"""
from __future__ import annotations

import json
import uuid

import psycopg2.extras

from talos import crystallize


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _seed_board(admin_conn, board_id: str) -> None:
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO boards (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (board_id, f"Board {board_id}"),
        )
    admin_conn.commit()


def _seed_task(admin_conn, board_id: str, task_id: str) -> None:
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tasks (id, board_id, title, status) VALUES (%s, %s, %s, 'ready') ON CONFLICT DO NOTHING",
            (task_id, board_id, f"Task {task_id}"),
        )
    admin_conn.commit()


def _seed_rule(admin_conn, board_id: str, rule_id: str, rule_type: str, content: str) -> None:
    with admin_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO rules (id, board_id, rule_type, content, client_scope, status, verified, safety)
            VALUES (%s, %s, %s, %s, 'client', 'approved_client', false, false)
            """,
            (rule_id, board_id, rule_type, content),
        )
    admin_conn.commit()


def _rules_for_board(admin_conn, board_id: str) -> list[dict]:
    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM rules WHERE board_id = %s ORDER BY created_at", (board_id,))
        return [dict(r) for r in cur.fetchall()]


def _dedup_log_count(admin_conn, board_id: str) -> int:
    with admin_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM rule_ingestion_log WHERE board_id = %s", (board_id,))
        return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# Order independence of the final live-content set (cross-run contradiction)
# ---------------------------------------------------------------------------

def _run_batch(admin_conn, monkeypatch, candidates: list[dict]) -> tuple[set[str], int]:
    """Seed a fresh board with one pre-existing (prior-run) unverified rule,
    then extract a 3-candidate batch in the given order. Returns
    (live_content_set, dedup_log_row_count)."""
    board_id, task_id = _uid("board"), _uid("task")
    _seed_board(admin_conn, board_id)
    _seed_task(admin_conn, board_id, task_id)
    _seed_rule(admin_conn, board_id, f"rule-{uuid.uuid4().hex[:12]}", "factual", "the valve closes automatically")

    monkeypatch.setattr(crystallize, "_call_extraction_llm", lambda *a, **k: json.dumps(candidates))
    crystallize.extract_rules(board_id, task_id, 0, {"summary": "x"})

    rows = _rules_for_board(admin_conn, board_id)
    live_content = {r["content"] for r in rows if r["superseded_by"] is None}
    return live_content, _dedup_log_count(admin_conn, board_id)


def test_rule_ingestion_order_independence_two_explicit_orderings(pg_setup, admin_conn, monkeypatch):
    candidate_a = {"rule_type": "factual", "content": "The Valve Closes Automatically"}  # contradicts the seeded rule
    candidate_b = {"rule_type": "procedural", "content": "always check pressure gauge first"}
    candidate_c = {"rule_type": "project_context", "content": "widgets ship from area west"}

    order1 = [candidate_a, candidate_b, candidate_c]
    order2 = [candidate_c, candidate_b, candidate_a]

    live1, dedup_count1 = _run_batch(admin_conn, monkeypatch, order1)
    live2, dedup_count2 = _run_batch(admin_conn, monkeypatch, order2)

    expected_live = {candidate_a["content"], candidate_b["content"], candidate_c["content"]}
    assert live1 == expected_live, "seeded rule must be superseded regardless of processing order"
    assert live2 == expected_live
    assert dedup_count1 == dedup_count2 == 3


# ---------------------------------------------------------------------------
# _dedup_key — deterministic, order-independent
# ---------------------------------------------------------------------------

def test_dedup_key_is_order_independent_and_deterministic():
    key1 = crystallize._dedup_key("board-x", "task-y", "task-y:0", "some rule content")
    key2 = crystallize._dedup_key("board-x", "task-y", "task-y:0", "some rule content")
    assert key1 == key2

    # Interleaving with other computations never perturbs the result.
    _ = crystallize._dedup_key("other-board", "other-task", "other-task:5", "unrelated content")
    key3 = crystallize._dedup_key("board-x", "task-y", "task-y:0", "some rule content")
    assert key3 == key1


def test_dedup_key_differs_on_any_input_change():
    base = crystallize._dedup_key("b", "t", "t:0", "content")
    assert crystallize._dedup_key("b2", "t", "t:0", "content") != base
    assert crystallize._dedup_key("b", "t2", "t:0", "content") != base
    assert crystallize._dedup_key("b", "t", "t:1", "content") != base
    assert crystallize._dedup_key("b", "t", "t:0", "different content") != base


# ---------------------------------------------------------------------------
# Same-run exclusion: dedup, not contradiction, resolves same-batch matches
# ---------------------------------------------------------------------------

def test_contradiction_scan_excludes_same_run_rows(pg_setup, admin_conn, monkeypatch):
    board_id, task_id = _uid("board"), _uid("task")
    _seed_board(admin_conn, board_id)
    _seed_task(admin_conn, board_id, task_id)

    # Different raw strings (distinct dedup keys) but identical after
    # normalization -- if the contradiction scan didn't exclude same-run rows,
    # the second candidate would spuriously supersede the first.
    candidates = [
        {"rule_type": "factual", "content": "The Valve Closes Automatically"},
        {"rule_type": "factual", "content": "the valve   closes automatically"},
    ]
    monkeypatch.setattr(crystallize, "_call_extraction_llm", lambda *a, **k: json.dumps(candidates))

    inserted = crystallize.extract_rules(board_id, task_id, 0, {"summary": "x"})
    assert len(inserted) == 2  # both got a distinct dedup_key -- neither was skipped

    rows = _rules_for_board(admin_conn, board_id)
    assert len(rows) == 2
    assert all(r["superseded_by"] is None for r in rows), (
        "same-run candidates must never supersede each other -- the "
        "contradiction scan excludes rows inserted earlier in this run"
    )
