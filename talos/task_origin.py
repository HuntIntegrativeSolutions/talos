"""
P4b shared helper: parse the {"talos_origin": ...} marker convention stored in
tasks.body. Used by the milestone escalator (talos.pm_escalator) and rule
promotion (talos.rule_promotion) to mark tasks created for a specific
downstream purpose, and by talos.graph.spine.deliverable_node /
talos.api.get_gate_status to branch on that purpose.

Defensive by design: never raises, so a malformed or unrelated tasks.body
(most tasks have none of this) never breaks the read path.
"""
from __future__ import annotations

import json


def parse_origin(task_body: str | None) -> dict | None:
    """Return the parsed origin marker dict, or None if task_body is missing,
    unparseable, not a JSON object, or lacks a "talos_origin" key."""
    if not task_body:
        return None
    try:
        parsed = json.loads(task_body)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) and "talos_origin" in parsed else None
