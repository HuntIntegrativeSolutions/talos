"""
Table-driven proof that web/gate/outcome-rules.js and talos/api.py::submit_gate_outcome
enforce the identical five-outcome (ADR-011) matrix. There is no browser-automation
harness for this repo (P7a doesn't warrant one) — this test is the real guard for the
UI's outcome-enable/require logic.

Part 1 parses outcome-rules.js as text (never executes it) and asserts its per-outcome
requiredField/enabledWhen shape matches this file's canonical Python copy — so a change
to one without the other fails here.

Part 2 drives the real endpoint via TestClient for each outcome, proving the server's
422/409 behavior matches what the client-side matrix would have gated on.
"""

from __future__ import annotations

import os
import pathlib
import re
import uuid

import pytest
from fastapi.testclient import TestClient

_OUTCOME_RULES_JS = (
    pathlib.Path(__file__).resolve().parent.parent.parent / "web" / "gate" / "outcome-rules.js"
)

# Canonical Python copy of the matrix in outcome-rules.js. Keep in lockstep.
CANONICAL_MATRIX = {
    "approve":  {"requiredField": None,              "enabledWhenMarker": "ctx.allRequiredPass"},
    "reject":   {"requiredField": "reason",          "enabledWhenMarker": "() => true"},
    "waive":    {"requiredField": "justification",   "enabledWhenMarker": "!ctx.hasNonWaivableFailingRequired"},
    "edit":     {"requiredField": "new_deliverable", "enabledWhenMarker": "() => true"},
    "escalate": {"requiredField": "justification",   "enabledWhenMarker": "() => true"},
}


def _seed(conn, board_id: str, task_id: str) -> None:
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO boards (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (board_id, f"Board {board_id}"),
        )
        cur.execute(
            "INSERT INTO tasks (id, board_id, title, status) VALUES (%s, %s, %s, 'ready') "
            "ON CONFLICT DO NOTHING",
            (task_id, board_id, f"Task {task_id}"),
        )
    conn.commit()


def _gate(client, board_id, task_id, human_jwt, **payload):
    return client.post(
        f"/boards/{board_id}/tasks/{task_id}/gate",
        json=payload,
        headers={"X-Human-Session": human_jwt},
    )


# ---------------------------------------------------------------------------
# Part 1: outcome-rules.js text matches the canonical Python matrix
# ---------------------------------------------------------------------------

def test_outcome_rules_js_matches_canonical_matrix():
    js_text = _OUTCOME_RULES_JS.read_text()
    for outcome, expected in CANONICAL_MATRIX.items():
        # Find the outcome's object literal line, e.g.:
        #   approve:  { requiredField: null,              enabledWhen: (ctx) => ctx.allRequiredPass },
        pattern = rf'{outcome}:\s*\{{\s*requiredField:\s*([^,]+),\s*enabledWhen:\s*(.+?)\s*\}},'
        m = re.search(pattern, js_text)
        assert m, f"could not find outcome {outcome!r} in outcome-rules.js"

        required_field_js = m.group(1).strip()
        enabled_when_js = m.group(2).strip()

        expected_field = expected["requiredField"]
        if expected_field is None:
            assert required_field_js == "null", (
                f"{outcome}: expected requiredField null, js has {required_field_js!r}"
            )
        else:
            assert required_field_js == f'"{expected_field}"', (
                f"{outcome}: expected requiredField {expected_field!r}, "
                f"js has {required_field_js!r}"
            )

        assert expected["enabledWhenMarker"] in enabled_when_js, (
            f"{outcome}: expected enabledWhen to reference "
            f"{expected['enabledWhenMarker']!r}, js has {enabled_when_js!r}"
        )


# ---------------------------------------------------------------------------
# Part 2: server enforces the identical matrix
# ---------------------------------------------------------------------------

def test_approve_requires_all_required_pass(pg_setup, admin_conn, test_graph, human_jwt):
    os.environ["TALOS_NEXUS_STUB"] = "1"
    from talos import api as api_module
    from talos.worker import claim_and_run

    client = TestClient(api_module.app)

    # Force citations_resolvable to fail (a required, non-safety critic).
    b, t = f"b-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    _seed(admin_conn, b, t)
    claim_and_run(b, t, graph=test_graph)
    resp = _gate(
        client, b, t, human_jwt, outcome="edit",
        new_deliverable={"citations": [{"finding_id": "X", "status": "proposed"}],
                          "summary": "not yet confirmed"},
    )
    assert resp.status_code == 200, resp.text

    # approve must be blocked: matches CANONICAL_MATRIX['approve'].enabledWhen.
    resp = _gate(client, b, t, human_jwt, outcome="approve")
    assert resp.status_code == 409
    assert "required critics have not passed" in resp.json()["detail"]["error"]


def test_reject_requires_reason(pg_setup, admin_conn, test_graph, human_jwt):
    os.environ["TALOS_NEXUS_STUB"] = "1"
    from talos import api as api_module
    from talos.worker import claim_and_run

    client = TestClient(api_module.app)
    b, t = f"b-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    _seed(admin_conn, b, t)
    claim_and_run(b, t, graph=test_graph)

    resp = _gate(client, b, t, human_jwt, outcome="reject")
    assert resp.status_code == 422
    assert "reason is required for reject" in resp.json()["detail"]

    resp = _gate(client, b, t, human_jwt, outcome="reject", reason="not good enough")
    assert resp.status_code == 200


def test_waive_requires_justification_and_blocks_safety_critics(
    pg_setup, admin_conn, test_graph, human_jwt
):
    os.environ["TALOS_NEXUS_STUB"] = "1"
    from talos import api as api_module
    from talos.worker import claim_and_run

    client = TestClient(api_module.app)

    # Missing justification.
    b, t = f"b-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    _seed(admin_conn, b, t)
    claim_and_run(b, t, graph=test_graph)
    resp = _gate(
        client, b, t, human_jwt, outcome="edit",
        new_deliverable={"citations": [{"finding_id": "X", "status": "proposed"}],
                          "summary": "not yet confirmed"},
    )
    assert resp.status_code == 200, resp.text
    resp = _gate(client, b, t, human_jwt, outcome="waive")
    assert resp.status_code == 422
    assert "justification is required for waive" in resp.json()["detail"]

    resp = _gate(client, b, t, human_jwt, outcome="waive", justification="risk accepted")
    assert resp.status_code == 200

    # Blocked when a required, non-waivable (safety_class) critic is failing —
    # matches CANONICAL_MATRIX['waive'].enabledWhen (hasNonWaivableFailingRequired).
    b2, t2 = f"b-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    _seed(admin_conn, b2, t2)
    claim_and_run(b2, t2, graph=test_graph)
    resp = _gate(
        client, b2, t2, human_jwt, outcome="edit",
        new_deliverable={"live_write": True,
                          "citations": [{"finding_id": "X", "status": "confirmed"}]},
    )
    assert resp.status_code == 200, resp.text
    resp = _gate(client, b2, t2, human_jwt, outcome="waive", justification="try anyway")
    assert resp.status_code == 409
    assert "safety critics cannot be waived" in resp.json()["detail"]["error"]


def test_edit_requires_new_deliverable(pg_setup, admin_conn, test_graph, human_jwt):
    os.environ["TALOS_NEXUS_STUB"] = "1"
    from talos import api as api_module
    from talos.worker import claim_and_run

    client = TestClient(api_module.app)
    b, t = f"b-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    _seed(admin_conn, b, t)
    claim_and_run(b, t, graph=test_graph)

    resp = _gate(client, b, t, human_jwt, outcome="edit")
    assert resp.status_code == 422
    assert "new_deliverable is required for edit" in resp.json()["detail"]

    resp = _gate(
        client, b, t, human_jwt, outcome="edit",
        new_deliverable={"citations": [{"finding_id": "X", "status": "confirmed"}],
                          "summary": "fine"},
    )
    assert resp.status_code == 200


def test_escalate_requires_justification(pg_setup, admin_conn, test_graph, human_jwt):
    os.environ["TALOS_NEXUS_STUB"] = "1"
    from talos import api as api_module
    from talos.worker import claim_and_run

    client = TestClient(api_module.app)
    b, t = f"b-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    _seed(admin_conn, b, t)
    claim_and_run(b, t, graph=test_graph)

    resp = _gate(client, b, t, human_jwt, outcome="escalate")
    assert resp.status_code == 422
    assert "justification is required for escalate" in resp.json()["detail"]

    resp = _gate(client, b, t, human_jwt, outcome="escalate", justification="second reviewer")
    assert resp.status_code == 200
