"""
P6 Landing 2 -- emulator_consistency verifier: talos.verifiers.emulator's
scoring/refusal logic, the registry's deterministic dispatch branch, the
NEXUS sharded-sweep refinement, and an e2e claim_and_run gate row.

Live pylogix / live NEXUS are never touched here -- every test mocks
ReadOnlyEmulatorClient (or the module-level helper functions that construct
it) and, where the NEXUS path runs at all, either sets TALOS_NEXUS_STUB=1 or
monkeypatches talos.verifiers.emulator._nexus_shard_call directly. See
scripts/emulator_verify_probe.py for the live-target counterpart.
"""

from __future__ import annotations

import json
import logging

import psycopg2.extras
import pytest
from langgraph.checkpoint.memory import MemorySaver

import talos.llm
import talos.verifiers.emulator as emulator_module
from talos.critics.registry import (
    VerifierSpec,
    _verifier_registry,
    get_verifier,
    register_verifier,
    run_all_verifiers,
)
from talos.graph.spine import build_graph
from talos.verifiers.emulator import emulator_consistency_verifier
from talos.worker import claim_and_run


# ---------------------------------------------------------------------------
# Import-boundary / structural guards
# ---------------------------------------------------------------------------

def test_registry_module_does_not_import_pylogix():
    """
    Mirrors test_p6_verifiers.py's test_registry_module_does_not_import_spine_or_llm:
    talos.critics.registry imports talos.verifiers.emulator to register
    emulator_consistency_verifier, but that module must never pull pylogix
    into sys.modules at import time (pylogix is imported lazily, only inside
    the function that does the live read) -- otherwise every process that
    imports the registry (which is nearly everything) would eagerly import
    pylogix.
    """
    import os
    import subprocess
    import sys

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    result = subprocess.run(
        [
            sys.executable, "-c",
            "import talos.critics.registry; import sys; "
            "assert 'pylogix' not in sys.modules, 'registry must not import pylogix at module level'",
        ],
        capture_output=True, text=True, cwd=repo_root,
    )
    assert result.returncode == 0, result.stderr


def test_emulator_consistency_registered_as_deterministic():
    spec = get_verifier("emulator_consistency")
    assert spec is not None
    assert spec.deterministic is True
    assert spec.fn is emulator_consistency_verifier
    assert spec.advisory is True
    assert spec.fail_open is False
    assert spec.waivable is True
    assert spec.rubric_field == "emulator_consistency"
    assert spec.score_threshold == 0.95


def test_emulator_module_source_never_calls_pylogix_write():
    import inspect

    source = inspect.getsource(emulator_module)
    assert "Write(" not in source, (
        "talos.verifiers.emulator must never call pylogix's Write -- "
        "the Guardian doctrine's read-only boundary for this capability"
    )


# ---------------------------------------------------------------------------
# Registry dispatch: deterministic fn path doesn't disturb score_fn path
# ---------------------------------------------------------------------------

@pytest.fixture()
def isolated_verifier_registry():
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
        deterministic=False,
    )
    fields.update(overrides)
    return VerifierSpec(**fields)


def test_deterministic_dispatch_calls_fn_not_score_fn(isolated_verifier_registry):
    calls = []

    def fn(deliverable, rubric_text, nexus_client):
        calls.append((deliverable, rubric_text, nexus_client))
        return 1.0, "deterministic pass"

    def score_fn(spec, rubric_text):
        raise AssertionError("score_fn must not be called for a deterministic verifier")

    register_verifier(_spec(name="det", fn=fn, deterministic=True))
    results = run_all_verifiers({"k": "v"}, {"rubric": "raw text"}, score_fn=score_fn, nexus_client="sentinel")
    assert calls == [({"k": "v"}, "raw text", "sentinel")]
    assert results[0]["score"] == 1.0
    assert results[0]["verdict"] == "pass"


def test_non_deterministic_dispatch_still_uses_score_fn(isolated_verifier_registry):
    register_verifier(_spec(name="llm_scored", deterministic=False))
    results = run_all_verifiers(
        {}, {"rubric": "text"},
        score_fn=lambda spec, rubric: (0.9, "llm reasoning"),
    )
    assert results[0]["score"] == 0.9
    assert results[0]["reasoning"] == "llm reasoning"


def test_deterministic_none_score_routes_through_failure_table(isolated_verifier_registry, caplog):
    def fn(deliverable, rubric_text, nexus_client):
        return None, "refused: unknown target"

    register_verifier(_spec(name="det_refuse", fn=fn, deterministic=True, advisory=True, fail_open=False))
    results = run_all_verifiers({}, {"rubric": "text"}, score_fn=lambda *a: (None, None))
    assert len(results) == 1
    assert results[0]["verdict"] == "warn"
    assert results[0]["reason"] == "refused: unknown target"


# ---------------------------------------------------------------------------
# emulator_consistency_verifier: refusal paths
# ---------------------------------------------------------------------------

VALID_MARKER = json.dumps({"plc_id": "NFK-DRYER-TEST-V2", "emulator": "dryer_echo"})


def test_invalid_marker_json_refuses():
    score, reasoning = emulator_consistency_verifier({}, "not json", None)
    assert score is None
    assert "invalid emulator_consistency marker JSON" in reasoning


def test_marker_missing_fields_refuses():
    score, reasoning = emulator_consistency_verifier({}, json.dumps({"plc_id": "X"}), None)
    assert score is None
    assert "plc_id" in reasoning and "emulator" in reasoning


def test_unknown_emulator_key_refuses(monkeypatch):
    monkeypatch.setattr("talos.config.get_emulators_config", lambda: {"dryer_echo": {"confirmed_emulator": True}})
    marker = json.dumps({"plc_id": "X", "emulator": "nonexistent_key"})
    score, reasoning = emulator_consistency_verifier({}, marker, None)
    assert score is None
    assert "unknown emulator key" in reasoning


def test_unconfirmed_emulator_refuses(monkeypatch):
    monkeypatch.setattr(
        "talos.config.get_emulators_config",
        lambda: {"dryer_echo": {"host": "10.0.0.11", "slot": 0, "confirmed_emulator": False}},
    )
    score, reasoning = emulator_consistency_verifier({}, VALID_MARKER, None)
    assert score is None
    assert "confirmed_emulator=true" in reasoning


def test_unreachable_emulator_refuses(monkeypatch):
    monkeypatch.setattr(
        "talos.config.get_emulators_config",
        lambda: {"dryer_echo": {"host": "10.0.0.11", "slot": 0, "confirmed_emulator": True, "read_timeout_s": 5}},
    )

    def boom(cfg, deadline):
        raise ConnectionError("no route to host")

    monkeypatch.setattr(emulator_module, "_read_emulator_inventory", boom)
    score, reasoning = emulator_consistency_verifier({}, VALID_MARKER, None)
    assert score is None
    assert "emulator/NEXUS read failed" in reasoning


def test_timeout_during_emulator_read_refuses(monkeypatch):
    monkeypatch.setattr(
        "talos.config.get_emulators_config",
        lambda: {"dryer_echo": {"host": "10.0.0.11", "slot": 0, "confirmed_emulator": True, "read_timeout_s": 5}},
    )

    def timeout(cfg, deadline):
        raise TimeoutError("emulator read timed out reading tag list")

    monkeypatch.setattr(emulator_module, "_read_emulator_inventory", timeout)
    score, reasoning = emulator_consistency_verifier({}, VALID_MARKER, None)
    assert score is None
    assert "timed out" in reasoning


class _FakeResponse:
    def __init__(self, value, status="Success"):
        self.Value = value
        self.Status = status


def test_response_value_raises_on_non_success_status_instead_of_returning_empty():
    """
    Live-probed regression guard: pylogix's GetProgramsList/GetTagList can
    return Status != "Success" with Value=None/empty WITHOUT raising a Python
    exception (confirmed against the reference emulator during a transient
    read). Treating that silently as "the PLC has zero tags" would misreport
    a read failure as real inventory drift -- _response_value must raise
    instead, so the caller's refusal contract catches it.
    """
    with pytest.raises(ConnectionError, match="GetTagList failed"):
        emulator_module._response_value(_FakeResponse(None, status="Request timed out"), "GetTagList")


def test_response_value_passes_through_on_success():
    resp = _FakeResponse(["a", "b"], status="Success")
    assert emulator_module._response_value(resp, "GetProgramsList") == ["a", "b"]


def test_read_emulator_inventory_refuses_empty_programs_even_with_success_status(monkeypatch):
    """Belt-and-suspenders: even a Status="Success" empty programs list is
    refused rather than silently treated as "this PLC has zero programs" --
    a real controller always has at least one program."""
    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def get_device_properties(self):
            return _FakeResponse("device info")

        def get_programs_list(self):
            return _FakeResponse([])

        def close(self):
            pass

    monkeypatch.setattr(emulator_module, "ReadOnlyEmulatorClient", FakeClient)
    import time
    with pytest.raises(ConnectionError, match="GetProgramsList returned no programs"):
        emulator_module._read_emulator_inventory({"host": "x", "slot": 0}, time.monotonic() + 30)


# ---------------------------------------------------------------------------
# emulator_consistency_verifier: scoring, with TALOS_NEXUS_STUB=1
# ---------------------------------------------------------------------------

_EMULATOR_CFG = {
    "dryer_echo": {"host": "10.0.0.11", "slot": 0, "confirmed_emulator": True, "read_timeout_s": 5},
}


def _patch_config(monkeypatch):
    monkeypatch.setattr("talos.config.get_emulators_config", lambda: _EMULATOR_CFG)


def test_perfect_match_scores_1_and_passes(monkeypatch):
    monkeypatch.setenv("TALOS_NEXUS_STUB", "1")
    _patch_config(monkeypatch)

    emulator_programs = {"P_Dev", "P_Seq"}
    # Controller-scope tags only (GetTagList(False)) -- matches _STUB_NEXUS_TAGS'
    # 3 controller-scope NFK-DRYER-TEST-V2 rows exactly. Seq_Step is
    # program:P_Seq-scoped in NEXUS, so it's excluded here too (a
    # program-scoped tag never appears in GetTagList(False)'s real output).
    emulator_tags = {"Active_Alarms": "BOOL", "AutoStart_State": "DINT", "Dryer_Temp_PV": "REAL"}
    monkeypatch.setattr(
        emulator_module, "_read_emulator_inventory",
        lambda cfg, deadline: (emulator_programs, emulator_tags),
    )
    score, reasoning = emulator_consistency_verifier({}, VALID_MARKER, None)
    assert score == pytest.approx(1.0)
    assert "score=1.000" in reasoning


def test_missing_tag_lowers_score(monkeypatch):
    monkeypatch.setenv("TALOS_NEXUS_STUB", "1")
    _patch_config(monkeypatch)

    # Emulator is missing Dryer_Temp_PV, which NEXUS documents -> n2e coverage drops.
    emulator_programs = {"P_Dev", "P_Seq"}
    emulator_tags = {"Active_Alarms": "BOOL", "AutoStart_State": "DINT", "Seq_Step": "DINT"}
    monkeypatch.setattr(
        emulator_module, "_read_emulator_inventory",
        lambda cfg, deadline: (emulator_programs, emulator_tags),
    )
    score, reasoning = emulator_consistency_verifier({}, VALID_MARKER, None)
    assert score < 1.0
    assert "Dryer_Temp_PV" in reasoning


def test_type_mismatch_lowers_score(monkeypatch):
    monkeypatch.setenv("TALOS_NEXUS_STUB", "1")
    _patch_config(monkeypatch)

    emulator_programs = {"P_Dev", "P_Seq"}
    # Dryer_Temp_PV documented as REAL in NEXUS, but emulator reports DINT.
    emulator_tags = {"Active_Alarms": "BOOL", "AutoStart_State": "DINT", "Dryer_Temp_PV": "DINT"}
    monkeypatch.setattr(
        emulator_module, "_read_emulator_inventory",
        lambda cfg, deadline: (emulator_programs, emulator_tags),
    )
    score, reasoning = emulator_consistency_verifier({}, VALID_MARKER, None)
    assert score < 1.0
    assert "Dryer_Temp_PV" in reasoning
    assert "data-type mismatches" in reasoning


def test_type_comparison_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("TALOS_NEXUS_STUB", "1")
    _patch_config(monkeypatch)

    emulator_programs = {"P_Dev", "P_Seq"}
    # 'bool'/'real' lowercase vs NEXUS's 'BOOL'/'REAL' must still agree.
    emulator_tags = {"Active_Alarms": "bool", "AutoStart_State": "dint", "Dryer_Temp_PV": "real"}
    monkeypatch.setattr(
        emulator_module, "_read_emulator_inventory",
        lambda cfg, deadline: (emulator_programs, emulator_tags),
    )
    score, _ = emulator_consistency_verifier({}, VALID_MARKER, None)
    assert score == pytest.approx(1.0)


def test_program_not_documented_in_nexus_is_not_scored_against(monkeypatch):
    """program_recall only checks NEXUS-known programs still exist on the
    controller -- an emulator program NEXUS hasn't documented must not lower
    the score (this is the fix that replaced program_jaccard)."""
    monkeypatch.setenv("TALOS_NEXUS_STUB", "1")
    _patch_config(monkeypatch)

    emulator_programs = {"P_Dev", "P_Seq", "P_Fast", "P_Map_IO", "P_Sfty", "P_Sys"}  # 6 real programs
    emulator_tags = {"Active_Alarms": "BOOL", "AutoStart_State": "DINT", "Dryer_Temp_PV": "REAL"}
    monkeypatch.setattr(
        emulator_module, "_read_emulator_inventory",
        lambda cfg, deadline: (emulator_programs, emulator_tags),
    )
    score, reasoning = emulator_consistency_verifier({}, VALID_MARKER, None)
    assert score == pytest.approx(1.0), "undocumented emulator programs must not lower the score"
    assert "not documented in NEXUS" in reasoning


# ---------------------------------------------------------------------------
# NEXUS sharded tag_find_plant_wide sweep
# ---------------------------------------------------------------------------

def test_shard_refinement_recurses_on_truncated(monkeypatch):
    monkeypatch.delenv("TALOS_NEXUS_STUB", raising=False)
    calls = []

    def fake_shard_call(tag_pattern):
        calls.append(tag_pattern)
        if tag_pattern == "A*":
            return [
                {"plc_id": "X", "name": "A1", "data_type": "BOOL", "scope": "controller"},
                {"truncated": True, "total_hint": 900},
            ]
        if tag_pattern.startswith("AA"):
            return [{"plc_id": "X", "name": "AA1", "data_type": "DINT", "scope": "controller"}]
        return []

    rows, truncated = emulator_module._sweep_shards(fake_shard_call, deadline=__import__("time").monotonic() + 30)
    names = {r["name"] for r in rows}
    assert "A1" in names
    assert "AA1" in names, "truncated 'A*' shard must recurse into 'AA*'..'AZ*' etc."
    assert any(p.startswith("AA") for p in calls)


def test_shard_still_truncated_at_recursion_ceiling_is_reported_not_dropped(monkeypatch):
    monkeypatch.delenv("TALOS_NEXUS_STUB", raising=False)

    def always_truncated(tag_pattern):
        return [{"truncated": True, "total_hint": 5000}]

    import time
    rows, truncated = emulator_module._sweep_shards(always_truncated, deadline=time.monotonic() + 30)
    assert truncated, "a shard still truncated at the recursion ceiling must be reported, not silently dropped"


def test_emulator_inventory_filters_connection_objects(monkeypatch):
    """
    Regression (review fix): pylogix GetTagList surfaces connection/module
    internal objects ("Cxn:Diagnostic:...") whose names contain ":" -- a
    Logix user tag name never can. NEXUS can never document them, so leaving
    them in permanently depresses tag_coverage_e2n with non-drift noise.
    """
    class FakeResp:
        def __init__(self, value):
            self.Status = "Success"
            self.Value = value

    class FakeTag:
        def __init__(self, name, dtype):
            self.TagName = name
            self.DataType = dtype

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def get_device_properties(self):
            return FakeResp(object())

        def get_programs_list(self):
            return FakeResp(["Program:P_Main"])

        def get_tag_list(self, all_tags=False):
            return FakeResp([
                FakeTag("Real_Tag", "BOOL"),
                FakeTag("Cxn:Diagnostic:4a2fbb14", "connection"),
            ])

        def close(self):
            pass

    monkeypatch.setattr(emulator_module, "ReadOnlyEmulatorClient", FakeClient)
    import time
    programs, tags = emulator_module._read_emulator_inventory(
        {"host": "h", "slot": 0}, deadline=time.monotonic() + 30
    )
    assert "Real_Tag" in tags
    assert not any(":" in name for name in tags), "connection objects must be filtered"


def test_sweep_never_issues_top_level_underscore_shard():
    """
    Regression (review fix): "_" is SQL LIKE's single-char wildcard --
    tag_find_plant_wide translates * -> % but leaves _ alone, so a "_*"
    top-level shard queries LIKE '_%' (every tag in the plant), always
    truncates, and its refinements explode until the call budget dies.
    """
    patterns = []

    def fetch(tag_pattern):
        patterns.append(tag_pattern)
        return []

    import time
    emulator_module._sweep_shards(fetch, deadline=time.monotonic() + 30)
    assert "_*" not in patterns
    assert all(not p.startswith("_") for p in patterns)


def test_truncated_underscore_refinement_is_reported_not_recursed():
    """A truncated "_"-suffixed refinement (e.g. "A_*") must be reported in
    truncated_shards, never expanded -- its children over-match identically."""
    patterns = []

    def fetch(tag_pattern):
        patterns.append(tag_pattern)
        if tag_pattern in ("A*", "A_*"):
            return [{"truncated": True, "total_hint": 5000}]
        return []

    import time
    rows, truncated = emulator_module._sweep_shards(fetch, deadline=time.monotonic() + 30)
    assert "A_" in truncated
    assert not any(p.startswith("A_") and p != "A_*" for p in patterns), (
        "children of a truncated underscore refinement must not be fetched"
    )


def test_sweep_dedup_keeps_same_name_across_scopes():
    """
    Regression (review fix): dedup key must be (plc_id, name, scope) -- a
    program-scoped tag sharing its name with a controller-scoped tag is a
    distinct row; collapsing on (plc_id, name) dropped one of them, losing
    either the tag or the program:* scope the program-recall term derives
    from.
    """
    def fetch(tag_pattern):
        if tag_pattern == "S*":
            return [
                {"plc_id": "X", "name": "Seq_Step", "data_type": "DINT", "scope": "controller"},
                {"plc_id": "X", "name": "Seq_Step", "data_type": "DINT", "scope": "program:P_Seq"},
            ]
        return []

    import time
    rows, truncated = emulator_module._sweep_shards(fetch, deadline=time.monotonic() + 30)
    assert len([r for r in rows if r["name"] == "Seq_Step"]) == 2, (
        "controller-scoped and program-scoped rows with the same tag name must both survive dedup"
    )


def test_shard_call_refused_when_tool_not_allow_listed(monkeypatch):
    monkeypatch.delenv("TALOS_NEXUS_STUB", raising=False)
    monkeypatch.setattr(emulator_module, "_nexus_url", lambda: "http://fake")
    monkeypatch.setattr(
        "talos.nexus_client.allowed_nexus_tool_names",
        lambda manifest, write_grant=True: [],  # tag_find_plant_wide NOT allow-listed
    )
    import time
    with pytest.raises(RuntimeError, match="allow-list"):
        emulator_module._fetch_nexus_tag_inventory("X", deadline=time.monotonic() + 30)


# ---------------------------------------------------------------------------
# e2e: claim_and_run lands a task_gate_results row with score/reasoning
# ---------------------------------------------------------------------------

EMULATOR_TASK_BODY = f"""\
Read the Dryer_PLC test controller inventory and cross-check it against NEXUS.

<!-- talos:rubric:emulator_consistency
{VALID_MARKER}
-->

No further prose needed for this task.
"""


def _seed_board_and_task(cur, board_id: str, task_id: str, body: str) -> None:
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


def test_e2e_emulator_task_persists_gate_row(pg_setup, admin_conn, monkeypatch):
    board_id, task_id = "emulator-board-1", "emulator-task-1"
    with admin_conn.cursor() as cur:
        _seed_board_and_task(cur, board_id, task_id, body=EMULATOR_TASK_BODY)
    admin_conn.commit()

    monkeypatch.setenv("TALOS_NEXUS_STUB", "1")
    monkeypatch.setattr("talos.config.get_emulators_config", lambda: _EMULATOR_CFG)
    monkeypatch.setattr(
        emulator_module, "_read_emulator_inventory",
        lambda cfg, deadline: (
            {"P_Dev", "P_Seq"},
            {"Active_Alarms": "BOOL", "AutoStart_State": "DINT", "Dryer_Temp_PV": "REAL"},
        ),
    )
    # No LLM call is expected on this task (no rubric_compliance marker, and
    # emulator_consistency is deterministic) -- an unset call_model would
    # raise if ever invoked, catching an accidental score_fn dispatch.
    def unexpected_call_model(*a, **kw):
        raise AssertionError("no LLM call expected for a deterministic-only verifier task")
    monkeypatch.setattr(talos.llm, "call_model", unexpected_call_model)

    graph = build_graph(checkpointer=MemorySaver())
    claim_and_run(board_id, task_id, graph=graph)

    with admin_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT details FROM task_gate_results
            WHERE task_id = %s AND critic_name = 'emulator_consistency'
            ORDER BY created_at DESC LIMIT 1
            """,
            (task_id,),
        )
        row = cur.fetchone()
    assert row is not None
    details = row["details"]
    assert details["score"] == pytest.approx(1.0)
    assert "NFK-DRYER-TEST-V2" in details["reasoning"]
    assert "dryer_echo" in details["reasoning"]
    assert details["verdict"] == "pass"
    assert details["required"] is False
