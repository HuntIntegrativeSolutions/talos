"""
P5-Crystallize: post-approval rule extraction + contradiction handling.

Fires from the on_task_approved hook (talos/hooks.py) after post_gate_node
commits an approve/waive/escalate outcome — filtered here to strict
outcome == "approve", mirroring talos.rule_promotion / talos.memory.chroma_store.

v1 storage adaptation (ADR-023 amendment): all three rule types (factual,
procedural, project_context) land in the same Postgres `rules` table (V0007/
V0008) plus a Chroma `talos-rules-{board}` collection for semantic retrieval.
Graphiti/Neo4j bi-temporal edges are deferred post-v1 and were never built;
`superseded_by` + a verified/safety-gated review task (below) is the v1
replacement for Graphiti's invalid_at contradiction handling.

Extraction is sequential-for-simplicity (ROADMAP P5's "whether parallel or
sequential, prove it" instruction) — there is no write-side fan-out here to
reduce. What talos/tests/test_p5_fanout.py proves order-independent is the
dedup/contradiction candidate set, not a LangGraph reducer.

Every extracted rule lands at client_scope='client', status='approved_client',
verified=false, safety=false. Extraction NEVER promotes a rule to shared scope
— that only happens through the existing /promote_rule + RT-06 gate
(talos/api.py, talos/rule_promotion.py), untouched by this module.

Budget accounting: this hook fires post-gate, inside the API process, detached
from the spine's SpineState/TaskBudget reducer channel — there is no live
per-task budget object to accumulate into or enforce a cap against at this
point (the gate has already closed). call_model's normal llm.call span still
records token usage/latency for observability; there is no post-gate per-task
budget enforcement for extraction calls.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid

from talos.task_origin import parse_origin

log = logging.getLogger(__name__)

_RULE_TYPES = ("factual", "procedural", "project_context")
_CONTRADICTION_COSINE_THRESHOLD = 0.15

# Canned rules returned under TALOS_NEXUS_STUB=1 so extraction is exercisable
# in CI without a live LLM call (call_model already short-circuits to a bare
# "stub-response" string that can't be parsed as extraction JSON).
_STUB_EXTRACTED_RULES = [
    {"rule_type": "factual", "content": "PLC tag T_PUMP_01 maps to motor M-100 in area West."},
    {"rule_type": "procedural", "content": "Always verify interlock Z before modifying a setpoint on equipment type X."},
    {"rule_type": "project_context", "content": "On this project, tags follow ISA-5.1 with a Wrk_ prefix."},
]


def _dedup_key(board_id: str, task_id: str, crystallize_run_id: str, content: str) -> str:
    """hash(board_id + task_id + crystallize_run_id + rule_content), ADR-023.
    crystallize_run_id is derived deterministically by the caller (see
    _on_task_approved) as f"{task_id}:{run_id}", not a fresh uuid4() — so a
    replayed hook produces identical keys and rule_ingestion_log's
    UNIQUE(board_id, dedup_key) gates the reinsert."""
    raw = f"{board_id}|{task_id}|{crystallize_run_id}|{content}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _normalize_content(content: str) -> str:
    return re.sub(r"\s+", " ", content.strip().lower())


def _build_extraction_prompt(deliverable: dict | None) -> str:
    summary = (deliverable or {}).get("summary", "")
    return (
        "Extract durable rules from the following completed task deliverable. "
        'Return a JSON array of objects, each with "rule_type" (one of '
        'factual, procedural, project_context) and "content" (a single, '
        "self-contained statement). Return [] if nothing durable was learned.\n\n"
        f"Deliverable:\n{summary}"
    )


def _call_extraction_llm(board_id: str, task_id: str, deliverable: dict | None, board: dict | None, task: dict | None) -> str:
    import os

    if os.environ.get("TALOS_NEXUS_STUB") == "1":
        return json.dumps(_STUB_EXTRACTED_RULES)

    from talos.config import resolve_model
    from talos.llm import call_model

    primary, _fallback = resolve_model("crystallize", board, task)
    prompt = _build_extraction_prompt(deliverable)
    text, _session_id, _tokens = call_model(primary, prompt, board_id=board_id)
    return text


def _parse_extracted_rules(raw_json: str) -> list[dict]:
    """Defensive parse: malformed/invalid candidates are logged and dropped
    individually — a single bad candidate never fails the whole batch."""
    try:
        candidates = json.loads(raw_json)
    except (ValueError, TypeError):
        log.warning("crystallize: extraction output was not valid JSON; skipping batch")
        return []

    if not isinstance(candidates, list):
        log.warning("crystallize: extraction output was not a JSON array; skipping batch")
        return []

    valid: list[dict] = []
    for c in candidates:
        if not isinstance(c, dict):
            log.warning("crystallize: dropping non-object rule candidate: %r", c)
            continue
        rule_type = c.get("rule_type")
        content = c.get("content")
        if rule_type not in _RULE_TYPES or not isinstance(content, str) or not content.strip():
            log.warning("crystallize: dropping malformed rule candidate: %r", c)
            continue
        valid.append({"rule_type": rule_type, "content": content.strip()})
    return valid


def _find_contradiction(cur, board_id: str, rule_type: str, content: str, exclude_rule_ids: list[str]) -> dict | None:
    """Same rule_type AND (identical normalized content OR Chroma cosine
    distance < _CONTRADICTION_COSINE_THRESHOLD). Excludes rows already
    inserted earlier in THIS run (exclude_rule_ids) so the heuristic only
    compares against previously-committed rules — this is what makes the
    write-side order-independence proof in test_p5_fanout.py hold (dedup,
    not this scan, resolves same-batch literal duplicates).

    The Chroma arm degrades defensively: if the embedding model/store is
    unavailable, log and fall back to the exact-normalized-content arm only
    — never abort the enclosing Postgres transaction over a Chroma failure.
    """
    normalized = _normalize_content(content)
    exclude_ids = exclude_rule_ids or []

    cur.execute(
        """
        SELECT id, content, verified, safety FROM rules
        WHERE board_id = %s AND rule_type = %s AND superseded_by IS NULL
          AND NOT (id = ANY(%s))
        """,
        (board_id, rule_type, exclude_ids or [""]),
    )
    candidates = {row["id"]: row for row in cur.fetchall()}

    for row in candidates.values():
        if _normalize_content(row["content"]) == normalized:
            return dict(row)

    try:
        from talos.memory.chroma_store import query_rules
        matches = query_rules(board_id, content, k=5, where={"rule_type": rule_type})
    except Exception:
        log.warning(
            "crystallize: contradiction Chroma query failed for board_id=%s; "
            "falling back to exact-match only", board_id,
        )
        return None

    for m in matches:
        rid = m.get("id")
        if rid in candidates and m.get("distance") is not None and m["distance"] < _CONTRADICTION_COSINE_THRESHOLD:
            return dict(candidates[rid])
    return None


def _create_contradiction_review_task(cur, board_id: str, old_rule: dict, new_rule_id: str) -> str:
    """Mirrors talos/api.py's promote_rule task-creation pattern. Does NOT
    mutate the old rule — it stays untouched until a human approves this
    task, at which point _on_contradiction_review_approved sets
    superseded_by."""
    review_task_id = f"review-{uuid.uuid4().hex[:12]}"
    origin_body = json.dumps({
        "talos_origin": "rule_contradiction_review",
        "old_rule_id": old_rule["id"],
        "new_rule_id": new_rule_id,
    })
    cur.execute(
        """
        INSERT INTO tasks (id, board_id, title, body, status, priority)
        VALUES (%s, %s, %s, %s, 'ready', 5)
        """,
        (
            review_task_id, board_id,
            f"Review rule contradiction: {old_rule['id']} vs {new_rule_id}",
            origin_body,
        ),
    )
    return review_task_id


def build_contradiction_review_deliverable(board_id: str, origin: dict) -> dict:
    """Called from talos.graph.spine.deliverable_node's
    is_rule_contradiction_review branch. Shows the human reviewer both the
    existing verified/safety rule and the proposed replacement side by side
    — without this, the task would fall through to the generic NEXUS-tag
    scaffold and the reviewer would never see the actual conflict."""
    from talos.db import board_scope, get_conn

    conn = get_conn()
    try:
        with board_scope(conn, board_id) as cur:
            cur.execute(
                "SELECT id, content, rule_type, verified, safety FROM rules WHERE id = %s AND board_id = %s",
                (origin["old_rule_id"], board_id),
            )
            old_rule = cur.fetchone()
            cur.execute(
                "SELECT id, content, rule_type FROM rules WHERE id = %s AND board_id = %s",
                (origin["new_rule_id"], board_id),
            )
            new_rule = cur.fetchone()
    finally:
        conn.close()

    old_label = "safety" if (old_rule and old_rule["safety"]) else "verified"
    old_content = old_rule["content"] if old_rule else "(not found)"
    new_content = new_rule["content"] if new_rule else "(not found)"
    summary = (
        f"Existing {old_label} rule ({origin['old_rule_id']}):\n{old_content}\n\n"
        f"Proposed replacement ({origin['new_rule_id']}):\n{new_content}"
    )
    return {
        "summary": summary,
        "rule_type": old_rule["rule_type"] if old_rule else None,
        # Synthetic citations entry (mirrors _build_rule_promotion_deliverable)
        # keeps citations_resolvable passing without any change to that critic.
        "citations": [{"finding_id": origin["new_rule_id"], "status": "confirmed"}],
    }


def _ingest_one_rule(
    cur, board_id: str, task_id: str, crystallize_run_id: str,
    rule_type: str, content: str, inserted_this_run: list[dict],
) -> dict | None:
    """Per-rule pipeline inside one board_scope transaction. Returns the
    inserted rule dict (also appended to inserted_this_run), or None if the
    dedup gate already ingested this exact (task, run, content) tuple."""
    dedup_key = _dedup_key(board_id, task_id, crystallize_run_id, content)

    cur.execute(
        """
        INSERT INTO rule_ingestion_log (board_id, dedup_key)
        VALUES (%s, %s)
        ON CONFLICT (board_id, dedup_key) DO NOTHING
        RETURNING id
        """,
        (board_id, dedup_key),
    )
    if cur.fetchone() is None:
        return None  # already ingested -- idempotent no-op (ADR-023)

    exclude_ids = [r["id"] for r in inserted_this_run]
    contradiction = _find_contradiction(cur, board_id, rule_type, content, exclude_ids)

    rule_id = f"rule-{uuid.uuid4().hex[:12]}"
    cur.execute(
        """
        INSERT INTO rules (id, board_id, rule_type, content, client_scope,
                            source_task_id, status, verified, safety)
        VALUES (%s, %s, %s, %s, 'client', %s, 'approved_client', false, false)
        RETURNING created_at
        """,
        (rule_id, board_id, rule_type, content, task_id),
    )
    created_at = cur.fetchone()["created_at"]

    cur.execute(
        "UPDATE rule_ingestion_log SET rule_id = %s WHERE board_id = %s AND dedup_key = %s",
        (rule_id, board_id, dedup_key),
    )

    if contradiction:
        if contradiction["verified"] or contradiction["safety"]:
            _create_contradiction_review_task(cur, board_id, contradiction, rule_id)
        else:
            cur.execute(
                "UPDATE rules SET superseded_by = %s, updated_at = NOW() WHERE id = %s AND board_id = %s",
                (rule_id, contradiction["id"], board_id),
            )

    inserted = {
        "id": rule_id, "rule_type": rule_type, "content": content,
        "verified": False, "safety": False, "status": "approved_client",
        "created_at": created_at,
    }
    inserted_this_run.append(inserted)
    return inserted


def extract_rules(
    board_id: str, task_id: str, run_id, deliverable: dict | None,
    board: dict | None = None, task: dict | None = None,
) -> list[dict]:
    """Entry point called from the on_task_approved hook. Returns the list of
    newly-inserted rule dicts (empty if nothing was extracted or everything
    was a dedup no-op). Never raises internally -- malformed LLM output is
    logged and skipped per-candidate by _parse_extracted_rules; the caller
    hook wraps this in its own try/except for defense in depth."""
    crystallize_run_id = f"{task_id}:{run_id}"
    raw = _call_extraction_llm(board_id, task_id, deliverable, board, task)
    candidates = _parse_extracted_rules(raw)
    if not candidates:
        return []

    from talos.db import board_scope, get_conn

    inserted: list[dict] = []
    for c in candidates:
        conn = get_conn()
        try:
            with board_scope(conn, board_id) as cur:
                _ingest_one_rule(
                    cur, board_id, task_id, crystallize_run_id,
                    c["rule_type"], c["content"], inserted,
                )
        finally:
            conn.close()

    return inserted


async def _on_task_approved(payload: dict) -> None:
    """Extraction trigger. Skips whenever parse_origin(body) is not None --
    covers rule_promotion, rule_contradiction_review, and
    milestone_remediation uniformly, so none of P4b's derived-task types get
    crystallized into new rules (loop guard)."""
    if payload.get("outcome") != "approve":
        return

    board_id = payload["board_id"]
    task_id = payload["task_id"]
    run_id = payload.get("run_id")

    try:
        from talos.db import board_scope, get_conn

        conn = get_conn()
        try:
            with board_scope(conn, board_id) as cur:
                cur.execute(
                    "SELECT body, deliverable FROM tasks WHERE id = %s AND board_id = %s",
                    (task_id, board_id),
                )
                row = cur.fetchone()
        finally:
            conn.close()

        if row is None:
            return
        if parse_origin(row["body"]) is not None:
            return
        if row["deliverable"] is None:
            return

        inserted = extract_rules(board_id, task_id, run_id, row["deliverable"])

        # Best-effort, post-commit Chroma embedding -- failure here must
        # never affect the already-committed rule rows (mirrors
        # chroma_store._on_task_approved's ingestion-failure isolation).
        for r in inserted:
            try:
                from talos.memory.chroma_store import upsert_rule
                created_at = r["created_at"]
                upsert_rule(
                    board_id, r["id"], r["content"],
                    {
                        "rule_type": r["rule_type"],
                        "verified": r["verified"],
                        "safety": r["safety"],
                        "status": r["status"],
                        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
                        "source_task_id": task_id,
                    },
                )
            except Exception:
                log.warning(
                    "crystallize: chroma embed failed for rule_id=%s board_id=%s",
                    r["id"], board_id,
                )
    except Exception:
        log.exception(
            "crystallize extraction failed for board_id=%s task_id=%s", board_id, task_id
        )


async def _on_contradiction_review_approved(payload: dict) -> None:
    """Second hook: the ONLY place a verified/safety row's superseded_by is
    ever set. Filtered to origin talos_origin == rule_contradiction_review
    and outcome == approve."""
    if payload.get("outcome") != "approve":
        return

    board_id = payload["board_id"]
    task_id = payload["task_id"]

    try:
        from talos.db import board_scope, get_conn

        conn = get_conn()
        try:
            with board_scope(conn, board_id) as cur:
                cur.execute(
                    "SELECT body FROM tasks WHERE id = %s AND board_id = %s",
                    (task_id, board_id),
                )
                row = cur.fetchone()
                origin = parse_origin(row["body"]) if row else None
                if not origin or origin.get("talos_origin") != "rule_contradiction_review":
                    return
                cur.execute(
                    """
                    UPDATE rules SET superseded_by = %s, updated_at = NOW()
                    WHERE id = %s AND board_id = %s
                    """,
                    (origin["new_rule_id"], origin["old_rule_id"], board_id),
                )
        finally:
            conn.close()
    except Exception:
        log.exception(
            "crystallize contradiction-review approval failed for board_id=%s task_id=%s",
            board_id, task_id,
        )


def register_crystallize_hooks() -> None:
    from talos.hooks import default_registry
    default_registry.register("on_task_approved", _on_task_approved)
    default_registry.register("on_task_approved", _on_contradiction_review_approved)
