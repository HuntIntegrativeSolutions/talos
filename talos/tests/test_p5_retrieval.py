"""
P5-Crystallize — retrieval at task start (read side): retrieval_k config,
read_branch_rules degradation, rule-context labeling, and merge_node folding.
"""
from __future__ import annotations

from talos.config import get_memory_config
from talos.graph.spine import RULE_CONTEXT_HEADER, format_rules_context, merge_node, read_branch_rules


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

    monkeypatch.setattr("talos.memory.chroma_store.query_rules", _raise)
    state = {"board_id": "b", "task_id": "t", "run_id": 0, "task_body": None}
    result = read_branch_rules(state)
    assert result["context_branches"] == {"rules": {"rules": []}}
    assert result["budget"]["tool_calls"] == 1


def test_read_branch_rules_uses_configured_k(monkeypatch):
    captured = {}

    def _fake_query_rules(board_id, text, k=5):
        captured["k"] = k
        return []

    monkeypatch.setattr("talos.memory.chroma_store.query_rules", _fake_query_rules)
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
