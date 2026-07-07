"""
P5-Crystallize — extraction pipeline (write side): three-type parsing, dedup
idempotency, contradiction handling (auto-supersede vs. verified/safety-gated
review task), skip guards, and RLS on the new rules columns.
"""
from __future__ import annotations

import asyncio
import json
import uuid

import psycopg2.extras
import pytest

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


def _seed_task(admin_conn, board_id: str, task_id: str, body: str | None = None) -> None:
    with admin_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tasks (id, board_id, title, body, status)
            VALUES (%s, %s, %s, %s, 'ready')
            ON CONFLICT DO NOTHING
            """,
            (task_id, board_id, f"Task {task_id}", body),
        )
    admin_conn.commit()


def _seed_rule(admin_conn, board_id: str, rule_id: str, rule_type: str, content: str,
                verified: bool = False, safety: bool = False) -> None:
    with admin_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO rules (id, board_id, rule_type, content, client_scope,
                                status, verified, safety)
            VALUES (%s, %s, %s, %s, 'client', 'approved_client', %s, %s)
            """,
            (rule_id, board_id, rule_type, content, verified, safety),
        )
    admin_conn.commit()


def _rules_for_board(admin_conn, board_id: str) -> list[dict]:
    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM rules WHERE board_id = %s ORDER BY created_at", (board_id,))
        return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# _parse_extracted_rules — defensive parsing (pure, no DB)
# ---------------------------------------------------------------------------

def test_parse_extracted_rules_malformed_json_returns_empty():
    assert crystallize._parse_extracted_rules("not json at all") == []


def test_parse_extracted_rules_not_a_list_returns_empty():
    assert crystallize._parse_extracted_rules(json.dumps({"rule_type": "factual", "content": "x"})) == []


def test_parse_extracted_rules_drops_invalid_candidates_keeps_valid():
    raw = json.dumps([
        {"rule_type": "factual", "content": "valid one"},
        {"rule_type": "not_a_real_type", "content": "bad type"},
        {"rule_type": "procedural", "content": ""},
        "not even an object",
        {"rule_type": "project_context", "content": "  valid two  "},
    ])
    parsed = crystallize._parse_extracted_rules(raw)
    assert parsed == [
        {"rule_type": "factual", "content": "valid one"},
        {"rule_type": "project_context", "content": "valid two"},
    ]


# ---------------------------------------------------------------------------
# extract_rules — stub-mode canned rules, dedup idempotency
# ---------------------------------------------------------------------------

def test_extract_rules_under_nexus_stub_returns_canned_rules(pg_setup, admin_conn, monkeypatch):
    monkeypatch.setenv("TALOS_NEXUS_STUB", "1")
    board_id, task_id = _uid("board"), _uid("task")
    _seed_board(admin_conn, board_id)
    _seed_task(admin_conn, board_id, task_id)

    inserted = crystallize.extract_rules(board_id, task_id, 0, {"summary": "irrelevant under stub"})
    assert len(inserted) == 3
    assert {r["rule_type"] for r in inserted} == set(crystallize._RULE_TYPES)

    rows = _rules_for_board(admin_conn, board_id)
    assert len(rows) == 3
    for row in rows:
        assert row["client_scope"] == "client"
        assert row["status"] == "approved_client"
        assert row["verified"] is False
        assert row["safety"] is False


def test_extract_rules_malformed_llm_output_skips_cleanly(pg_setup, admin_conn, monkeypatch):
    board_id, task_id = _uid("board"), _uid("task")
    _seed_board(admin_conn, board_id)
    _seed_task(admin_conn, board_id, task_id)

    monkeypatch.setattr(crystallize, "_call_extraction_llm", lambda *a, **k: "not valid json")

    inserted = crystallize.extract_rules(board_id, task_id, 0, {"summary": "x"})
    assert inserted == []
    assert _rules_for_board(admin_conn, board_id) == []


def test_dedup_key_prevents_reingest_on_replayed_extraction(pg_setup, admin_conn, monkeypatch):
    board_id, task_id = _uid("board"), _uid("task")
    _seed_board(admin_conn, board_id)
    _seed_task(admin_conn, board_id, task_id)

    fixed_output = json.dumps([{"rule_type": "factual", "content": "a stable fact"}])
    monkeypatch.setattr(crystallize, "_call_extraction_llm", lambda *a, **k: fixed_output)

    first = crystallize.extract_rules(board_id, task_id, 0, {"summary": "x"})
    second = crystallize.extract_rules(board_id, task_id, 0, {"summary": "x"})

    assert len(first) == 1
    assert second == []  # replayed hook -- idempotent no-op
    assert len(_rules_for_board(admin_conn, board_id)) == 1


def test_extraction_never_sets_client_scope_shared(pg_setup, admin_conn):
    board_id, task_id = _uid("board"), _uid("task")
    _seed_board(admin_conn, board_id)
    _seed_task(admin_conn, board_id, task_id)

    import os
    os.environ["TALOS_NEXUS_STUB"] = "1"
    crystallize.extract_rules(board_id, task_id, 0, {"summary": "x"})

    rows = _rules_for_board(admin_conn, board_id)
    assert rows  # sanity: something was extracted
    assert all(r["client_scope"] == "client" for r in rows)


# ---------------------------------------------------------------------------
# _on_task_approved — skip guards
# ---------------------------------------------------------------------------

def test_hook_noop_on_non_approve_outcome():
    asyncio.run(crystallize._on_task_approved({
        "board_id": "b", "task_id": "t", "run_id": 0, "outcome": "waive",
    }))
    # No DB access attempted -- would raise if it tried without a DSN.


@pytest.mark.parametrize("origin_type", ["rule_promotion", "rule_contradiction_review", "milestone_remediation"])
def test_on_task_approved_skips_when_any_origin_marker_present(pg_setup, admin_conn, origin_type):
    board_id, task_id = _uid("board"), _uid("task")
    _seed_board(admin_conn, board_id)
    body = json.dumps({"talos_origin": origin_type})
    _seed_task(admin_conn, board_id, task_id, body=body)
    with admin_conn.cursor() as cur:
        cur.execute("UPDATE tasks SET deliverable = %s WHERE id = %s", (json.dumps({"summary": "x"}), task_id))
    admin_conn.commit()

    asyncio.run(crystallize._on_task_approved({
        "board_id": board_id, "task_id": task_id, "run_id": 0, "outcome": "approve",
    }))

    assert _rules_for_board(admin_conn, board_id) == []


def test_on_task_approved_noop_on_null_deliverable(pg_setup, admin_conn):
    board_id, task_id = _uid("board"), _uid("task")
    _seed_board(admin_conn, board_id)
    _seed_task(admin_conn, board_id, task_id)

    asyncio.run(crystallize._on_task_approved({
        "board_id": board_id, "task_id": task_id, "run_id": 0, "outcome": "approve",
    }))

    assert _rules_for_board(admin_conn, board_id) == []


# ---------------------------------------------------------------------------
# Contradiction handling
# ---------------------------------------------------------------------------

def test_contradicting_unverified_rule_auto_supersedes(pg_setup, admin_conn, monkeypatch):
    board_id, task_id = _uid("board"), _uid("task")
    _seed_board(admin_conn, board_id)
    _seed_task(admin_conn, board_id, task_id)

    old_rule_id = f"rule-{uuid.uuid4().hex[:12]}"
    _seed_rule(admin_conn, board_id, old_rule_id, "factual", "the pump runs at 60hz", verified=False, safety=False)

    fixed_output = json.dumps([{"rule_type": "factual", "content": "The Pump Runs At 60Hz"}])
    monkeypatch.setattr(crystallize, "_call_extraction_llm", lambda *a, **k: fixed_output)

    inserted = crystallize.extract_rules(board_id, task_id, 0, {"summary": "x"})
    assert len(inserted) == 1
    new_rule_id = inserted[0]["id"]

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT superseded_by FROM rules WHERE id = %s", (old_rule_id,))
        old_row = dict(cur.fetchone())
    assert old_row["superseded_by"] == new_rule_id


@pytest.mark.parametrize("flag", ["verified", "safety"])
def test_contradicting_verified_or_safety_rule_creates_review_task_not_superseded(pg_setup, admin_conn, monkeypatch, flag):
    board_id, task_id = _uid("board"), _uid("task")
    _seed_board(admin_conn, board_id)
    _seed_task(admin_conn, board_id, task_id)

    old_rule_id = f"rule-{uuid.uuid4().hex[:12]}"
    kwargs = {"verified": flag == "verified", "safety": flag == "safety"}
    _seed_rule(admin_conn, board_id, old_rule_id, "procedural", "always lock out valve V-1 first", **kwargs)

    fixed_output = json.dumps([{"rule_type": "procedural", "content": "Always Lock Out Valve V-1 First"}])
    monkeypatch.setattr(crystallize, "_call_extraction_llm", lambda *a, **k: fixed_output)

    inserted = crystallize.extract_rules(board_id, task_id, 0, {"summary": "x"})
    assert len(inserted) == 1
    new_rule_id = inserted[0]["id"]

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT superseded_by FROM rules WHERE id = %s", (old_rule_id,))
        old_row = dict(cur.fetchone())
        cur.execute(
            "SELECT id, body, status FROM tasks WHERE board_id = %s AND body LIKE %s",
            (board_id, '%rule_contradiction_review%'),
        )
        review_task = cur.fetchone()

    assert old_row["superseded_by"] is None, "verified/safety rule must never be auto-superseded"
    assert review_task is not None
    origin = json.loads(review_task["body"])
    assert origin["talos_origin"] == "rule_contradiction_review"
    assert origin["old_rule_id"] == old_rule_id
    assert origin["new_rule_id"] == new_rule_id
    assert review_task["status"] == "ready"


def test_contradiction_review_task_approve_sets_superseded_by(pg_setup, admin_conn):
    board_id, task_id = _uid("board"), _uid("task")
    _seed_board(admin_conn, board_id)

    old_rule_id = f"rule-{uuid.uuid4().hex[:12]}"
    new_rule_id = f"rule-{uuid.uuid4().hex[:12]}"
    _seed_rule(admin_conn, board_id, old_rule_id, "factual", "old content", verified=True)
    _seed_rule(admin_conn, board_id, new_rule_id, "factual", "new content")

    review_task_id = _uid("review")
    body = json.dumps({
        "talos_origin": "rule_contradiction_review",
        "old_rule_id": old_rule_id,
        "new_rule_id": new_rule_id,
    })
    _seed_task(admin_conn, board_id, review_task_id, body=body)

    asyncio.run(crystallize._on_contradiction_review_approved({
        "board_id": board_id, "task_id": review_task_id, "outcome": "approve",
    }))

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT superseded_by FROM rules WHERE id = %s", (old_rule_id,))
        old_row = dict(cur.fetchone())
    assert old_row["superseded_by"] == new_rule_id


def test_contradiction_review_hook_ignores_non_approve_and_other_origins(pg_setup, admin_conn):
    board_id = _uid("board")
    _seed_board(admin_conn, board_id)
    old_rule_id = f"rule-{uuid.uuid4().hex[:12]}"
    _seed_rule(admin_conn, board_id, old_rule_id, "factual", "old content", verified=True)

    # non-approve outcome -- pure early return, no DB touch attempted.
    asyncio.run(crystallize._on_contradiction_review_approved({
        "board_id": board_id, "task_id": "nonexistent", "outcome": "escalate",
    }))

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT superseded_by FROM rules WHERE id = %s", (old_rule_id,))
        old_row = dict(cur.fetchone())
    assert old_row["superseded_by"] is None


# ---------------------------------------------------------------------------
# deliverable_node's rule_contradiction_review branch (not a generic scaffold)
# ---------------------------------------------------------------------------

def test_contradiction_review_task_deliverable_shows_both_rules_under_stub_mode(pg_setup, admin_conn, monkeypatch):
    monkeypatch.setenv("TALOS_NEXUS_STUB", "1")
    from talos.worker import claim_and_run

    board_id = _uid("board")
    _seed_board(admin_conn, board_id)

    old_rule_id = f"rule-{uuid.uuid4().hex[:12]}"
    new_rule_id = f"rule-{uuid.uuid4().hex[:12]}"
    _seed_rule(admin_conn, board_id, old_rule_id, "factual", "existing verified content", verified=True)
    _seed_rule(admin_conn, board_id, new_rule_id, "factual", "proposed replacement content")

    review_task_id = _uid("review")
    body = json.dumps({
        "talos_origin": "rule_contradiction_review",
        "old_rule_id": old_rule_id,
        "new_rule_id": new_rule_id,
    })
    _seed_task(admin_conn, board_id, review_task_id, body=body)

    claim_and_run(board_id, review_task_id)

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT deliverable FROM tasks WHERE id = %s", (review_task_id,))
        deliverable = cur.fetchone()["deliverable"]

    assert "existing verified content" in deliverable["summary"]
    assert "proposed replacement content" in deliverable["summary"]
    assert deliverable["citations"][0]["status"] == "confirmed"


# ---------------------------------------------------------------------------
# RLS on new columns
# ---------------------------------------------------------------------------

def test_rules_verified_safety_superseded_by_respects_board_isolation(pg_setup, admin_conn, app_conn):
    board_a, board_b = _uid("board-a"), _uid("board-b")
    _seed_board(admin_conn, board_a)
    _seed_board(admin_conn, board_b)

    rule_a = f"rule-{uuid.uuid4().hex[:12]}"
    _seed_rule(admin_conn, board_a, rule_a, "factual", "x", verified=True, safety=True)

    with app_conn.cursor() as cur:
        cur.execute("SET LOCAL app.board_id = %s", (board_b,))
        cur.execute("SELECT COUNT(*) FROM rules WHERE verified = true OR safety = true")
        assert cur.fetchone()[0] == 0

    with app_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SET LOCAL app.board_id = %s", (board_a,))
        cur.execute("SELECT verified, safety, superseded_by FROM rules WHERE id = %s", (rule_a,))
        row = dict(cur.fetchone())
    assert row["verified"] is True
    assert row["safety"] is True
    assert row["superseded_by"] is None
