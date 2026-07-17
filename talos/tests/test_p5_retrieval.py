"""
P5-Crystallize — retrieval at task start (read side): retrieval_k config,
read_branch_rules degradation, rule-context labeling, and merge_node folding.

P5.5 additions: read_node's own independent rules-prompt injection
(_build_rules_prompt_block), superseded/rejected exclusion
(pgvector_store.exclude_superseded_and_rejected), and the whole-rule token
cap (_truncate_rules_to_token_cap).
"""
from __future__ import annotations

import unittest.mock as mock
import uuid

from talos.config import get_memory_config
from talos.graph.spine import (
    RULE_CONTEXT_HEADER,
    RULES_PROMPT_BLOCK_HEADER,
    STUB_DOCUMENT,
    _truncate_rules_to_token_cap,
    format_rules_context,
    merge_node,
    read_branch_rules,
    read_node,
)


# ---------------------------------------------------------------------------
# retrieval_k config
# ---------------------------------------------------------------------------

def test_get_memory_config_default_retrieval_k_is_5():
    assert get_memory_config()["retrieval_k"] == 5


def test_toml_retrieval_k_override(monkeypatch, tmp_path):
    toml_path = tmp_path / "talos.toml"
    toml_path.write_text("[memory]\nretrieval_k = 8\n")
    monkeypatch.setattr("talos.config._TOML_PATH", toml_path)
    assert get_memory_config()["retrieval_k"] == 8


# ---------------------------------------------------------------------------
# read_branch_rules — degrades to empty on failure
# ---------------------------------------------------------------------------

def test_read_branch_rules_degrades_to_empty_on_query_failure(monkeypatch):
    def _raise(*a, **k):
        raise RuntimeError("no embedding model cached")

    monkeypatch.setattr("talos.memory.pgvector_store.query_rules", _raise)
    state = {"board_id": "b", "task_id": "t", "run_id": 0, "task_body": None}
    result = read_branch_rules(state)
    assert result["context_branches"] == {"rules": {"rules": []}}
    # P5.5: vector queries are not model invocations — this branch contributes 0.
    assert result["budget"]["model_invocations"] == 0


def test_read_branch_rules_uses_configured_k(monkeypatch):
    captured = {}

    def _fake_query_rules(board_id, text, k=5):
        captured["k"] = k
        return []

    monkeypatch.setattr("talos.memory.pgvector_store.query_rules", _fake_query_rules)
    monkeypatch.setattr("talos.config.get_memory_config", lambda: {"retrieval_k": 7})
    state = {"board_id": "b", "task_id": "t", "run_id": 0, "task_body": "task body text"}
    read_branch_rules(state)
    assert captured["k"] == 7


# ---------------------------------------------------------------------------
# format_rules_context — labeling
# ---------------------------------------------------------------------------

def test_format_rules_context_labels_rule_type_verified_and_age():
    rules = [
        {
            "id": "rule-1",
            "document": "always verify interlock Z",
            "metadata": {"rule_type": "procedural", "verified": True, "created_at": "2026-07-01T00:00:00"},
            "distance": 0.05,
        },
        {
            "id": "rule-2",
            "document": "tag T_PUMP_01 maps to M-100",
            "metadata": {"rule_type": "factual", "verified": False, "created_at": "2026-07-05T00:00:00"},
            "distance": 0.20,
        },
    ]
    formatted = format_rules_context(rules)
    assert formatted == [
        {"content": "always verify interlock Z", "rule_type": "procedural", "verified": True,
         "created_at": "2026-07-01T00:00:00", "distance": 0.05},
        {"content": "tag T_PUMP_01 maps to M-100", "rule_type": "factual", "verified": False,
         "created_at": "2026-07-05T00:00:00", "distance": 0.20},
    ]


def test_format_rules_context_defaults_verified_false_when_missing():
    rules = [{"id": "rule-1", "document": "x", "metadata": {"rule_type": "factual"}, "distance": None}]
    formatted = format_rules_context(rules)
    assert formatted[0]["verified"] is False


def test_format_rules_context_empty_input_returns_empty():
    assert format_rules_context([]) == []


def test_rule_context_header_states_unverified_is_suggestion():
    assert "suggestion" in RULE_CONTEXT_HEADER.lower()
    assert "not an instruction" in RULE_CONTEXT_HEADER.lower()


# ---------------------------------------------------------------------------
# merge_node folds the rules branch
# ---------------------------------------------------------------------------

def test_merge_node_folds_rules_branch_into_rule_context():
    state = {
        "context_branches": {
            "rules": {"rules": [
                {"id": "rule-1", "document": "x", "metadata": {"rule_type": "factual", "verified": True}, "distance": 0.1},
            ]},
        },
    }
    result = merge_node(state)
    assert result["rule_context"] == [
        {"content": "x", "rule_type": "factual", "verified": True, "created_at": None, "distance": 0.1},
    ]


def test_merge_node_rule_context_empty_when_no_rules_branch():
    result = merge_node({"context_branches": {}})
    assert result["rule_context"] == []


# ---------------------------------------------------------------------------
# P5.5 — _truncate_rules_to_token_cap
# ---------------------------------------------------------------------------

def test_truncate_rules_to_token_cap_keeps_whole_rules_only():
    labeled = [
        {"content": "one two three four five"},   # 5 words
        {"content": "six seven eight nine ten"},   # 5 words
        {"content": "eleven twelve"},               # 2 words
    ]
    out = _truncate_rules_to_token_cap(labeled, max_tokens=8)
    # First rule (5) fits; second rule (5 more = 10 > 8) does not, so it's
    # dropped whole rather than truncated mid-rule.
    assert out == [labeled[0]]


def test_truncate_rules_to_token_cap_zero_is_unlimited():
    labeled = [{"content": "a " * 5000}]
    assert _truncate_rules_to_token_cap(labeled, max_tokens=0) == labeled


def test_truncate_rules_to_token_cap_logs_warning_when_single_rule_exceeds_cap(caplog):
    labeled = [{"content": "word " * 20}]  # 20 words, cap is 5
    with caplog.at_level("WARNING"):
        out = _truncate_rules_to_token_cap(labeled, max_tokens=5)
    assert out == labeled  # still included, uncapped
    assert any("exceeds rule_context_max_tokens" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# P5.5 — read_node's independent rules-prompt injection
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


def test_read_node_prompt_includes_rules_block_when_rules_present(pg_setup, admin_conn, monkeypatch):
    import talos.llm
    from talos.worker import claim_and_run

    fixture_rules = [
        {
            "id": "rule-1",
            "document": "always verify interlock Z before restart",
            "metadata": {"rule_type": "procedural", "verified": True},
            "distance": 0.05,
        },
    ]
    monkeypatch.delenv("TALOS_NEXUS_STUB", raising=False)
    monkeypatch.setattr("talos.memory.pgvector_store.query_rules", lambda board_id, text, k=5: fixture_rules)
    monkeypatch.setattr(
        "talos.memory.pgvector_store.exclude_superseded_and_rejected", lambda board_id, rules: rules
    )

    captured = {}

    def fake_call_model(model, prompt, resume=None, *, span_ctx=None, allowed_tools=None,
                        mcp_servers=None, manifest=None, budget_check=None, board_id=None):
        captured["prompt"] = prompt
        return "response", "session-1", 5

    board_id, task_id = f"p55-rules-b-{uuid.uuid4().hex[:8]}", f"p55-rules-t-{uuid.uuid4().hex[:8]}"
    with admin_conn.cursor() as cur:
        _seed_board_and_task(cur, board_id, task_id)
    admin_conn.commit()

    with mock.patch.object(talos.llm, "call_model", fake_call_model):
        claim_and_run(board_id, task_id)

    assert RULES_PROMPT_BLOCK_HEADER in captured["prompt"]
    assert "always verify interlock Z before restart" in captured["prompt"]


def test_read_node_prompt_omits_rules_header_when_no_rules(pg_setup, admin_conn, monkeypatch):
    import talos.llm
    from talos.worker import claim_and_run

    monkeypatch.delenv("TALOS_NEXUS_STUB", raising=False)
    monkeypatch.setattr("talos.memory.pgvector_store.query_rules", lambda board_id, text, k=5: [])

    captured = {}

    def fake_call_model(model, prompt, resume=None, *, span_ctx=None, allowed_tools=None,
                        mcp_servers=None, manifest=None, budget_check=None, board_id=None):
        captured["prompt"] = prompt
        return "response", "session-1", 5

    board_id, task_id = f"p55-norules-b-{uuid.uuid4().hex[:8]}", f"p55-norules-t-{uuid.uuid4().hex[:8]}"
    with admin_conn.cursor() as cur:
        _seed_board_and_task(cur, board_id, task_id)
    admin_conn.commit()

    with mock.patch.object(talos.llm, "call_model", fake_call_model):
        claim_and_run(board_id, task_id)

    assert RULES_PROMPT_BLOCK_HEADER not in captured["prompt"]


def test_read_node_stub_mode_never_calls_rules_retrieval(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("stub mode must never call query_rules/exclude_superseded_and_rejected")

    monkeypatch.setenv("TALOS_NEXUS_STUB", "1")
    monkeypatch.setattr("talos.memory.pgvector_store.query_rules", _boom)
    monkeypatch.setattr("talos.memory.pgvector_store.exclude_superseded_and_rejected", _boom)

    state = {"board_id": "b", "task_id": "t", "run_id": 0, "task_body": None}
    result = read_node(state)
    assert result["nexus_result"] == {"document": STUB_DOCUMENT, "status": "confirmed"}


# ---------------------------------------------------------------------------
# P5.5 — exclude_superseded_and_rejected (Postgres cross-check)
# ---------------------------------------------------------------------------

def test_exclude_superseded_and_rejected_drops_superseded_rows(pg_setup, admin_conn):
    from talos.memory.pgvector_store import exclude_superseded_and_rejected

    board_id = f"p55-super-{uuid.uuid4().hex[:8]}"
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO boards (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (board_id, "board"),
        )
        cur.execute(
            "INSERT INTO rules (id, board_id, rule_type, content) VALUES (%s, %s, %s, %s)",
            ("rule-old", board_id, "factual", "old content"),
        )
        cur.execute(
            "INSERT INTO rules (id, board_id, rule_type, content) VALUES (%s, %s, %s, %s)",
            ("rule-new", board_id, "factual", "new content"),
        )
        cur.execute("UPDATE rules SET superseded_by = %s WHERE id = %s", ("rule-new", "rule-old"))
    admin_conn.commit()

    rules = [
        {"id": "rule-old", "document": "old content", "metadata": {"rule_id": "rule-old"}, "distance": 0.1},
        {"id": "rule-new", "document": "new content", "metadata": {"rule_id": "rule-new"}, "distance": 0.2},
    ]
    result = exclude_superseded_and_rejected(board_id, rules)
    assert [r["id"] for r in result] == ["rule-new"]


def test_exclude_superseded_and_rejected_drops_rejected_status(pg_setup, admin_conn):
    from talos.memory.pgvector_store import exclude_superseded_and_rejected

    board_id = f"p55-reject-{uuid.uuid4().hex[:8]}"
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO boards (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (board_id, "board"),
        )
        cur.execute(
            "INSERT INTO rules (id, board_id, rule_type, content, status) VALUES (%s, %s, %s, %s, %s)",
            ("rule-rejected", board_id, "factual", "rejected content", "rejected"),
        )
        cur.execute(
            "INSERT INTO rules (id, board_id, rule_type, content) VALUES (%s, %s, %s, %s)",
            ("rule-live", board_id, "factual", "live content"),
        )
    admin_conn.commit()

    rules = [
        {"id": "rule-rejected", "document": "rejected content", "metadata": {}, "distance": 0.1},
        {"id": "rule-live", "document": "live content", "metadata": {}, "distance": 0.2},
    ]
    result = exclude_superseded_and_rejected(board_id, rules)
    assert [r["id"] for r in result] == ["rule-live"]


def test_exclude_superseded_and_rejected_empty_input_is_noop(pg_setup, admin_conn):
    from talos.memory.pgvector_store import exclude_superseded_and_rejected

    assert exclude_superseded_and_rejected("any-board", []) == []


# ---------------------------------------------------------------------------
# P5.5 — [memory] rule_context_max_tokens config
# ---------------------------------------------------------------------------

def test_get_memory_config_default_rule_context_max_tokens_is_1000():
    assert get_memory_config()["rule_context_max_tokens"] == 1000


def test_toml_rule_context_max_tokens_override(monkeypatch, tmp_path):
    toml_path = tmp_path / "talos.toml"
    toml_path.write_text("[memory]\nrule_context_max_tokens = 250\n")
    monkeypatch.setattr("talos.config._TOML_PATH", toml_path)
    assert get_memory_config()["rule_context_max_tokens"] == 250
