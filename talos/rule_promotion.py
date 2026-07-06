"""
P4b: flips rules.client_scope to 'shared' when a rule-promotion task's gate
outcome is approve. Mirrors talos.memory.chroma_store's register_ingest_hook
pattern for on_task_approved.

Design decision (safety-preserving, not the "escalate flips scope too"
alternative): only outcome == "approve" flips client_scope. post_gate_node
fires on_task_approved for approve/waive/escalate alike (with `outcome` in
the payload), so this handler filters explicitly rather than relying on the
hook's name. Waive is structurally impossible here — RT-06 is non-waivable,
so submit_gate_outcome already 409s any waive attempt against a failing RT-06
before this hook could ever see one. Escalate IS a live path, but escalate
means "route to a second human reviewer," not "ship it" — RT-06 exists
precisely to stop a real client-identifier leak from reaching [shared], and
flipping scope on escalate would let the exact failure mode RT-06 was built
to prevent slip through via the one override path that bypasses a
non-waivable critic. So escalate leaves client_scope='client' and
status='pending_review' unchanged; only a subsequent approve (by the second
reviewer, on the same or a follow-up task) flips it.
"""
from __future__ import annotations

import logging

from talos.task_origin import parse_origin

log = logging.getLogger(__name__)


async def _on_task_approved(payload: dict) -> None:
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
                if not origin or origin.get("talos_origin") != "rule_promotion":
                    return
                cur.execute(
                    """
                    UPDATE rules SET client_scope = 'shared', status = 'approved_shared',
                        updated_at = NOW()
                    WHERE id = %s AND board_id = %s
                    """,
                    (origin["rule_id"], board_id),
                )
        finally:
            conn.close()
    except Exception:
        log.exception("rule_promotion client_scope flip failed for task_id=%s", task_id)


def register_rule_promotion_hook() -> None:
    from talos.hooks import default_registry
    default_registry.register("on_task_approved", _on_task_approved)
