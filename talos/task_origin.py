"""
P4b shared helper: parse the {"talos_origin": ...} marker convention stored in
tasks.body. Used by the milestone escalator (talos.pm_escalator) and rule
promotion (talos.rule_promotion) to mark tasks created for a specific
downstream purpose, and by talos.graph.spine.deliverable_node /
talos.api.get_gate_status to branch on that purpose.

Defensive by design: never raises, so a malformed or unrelated tasks.body
(most tasks have none of this) never breaks the read path.

P6/ADR-021: extract_rubrics() is a second, independent marker convention in
the same file for the same reason (per-task metadata riding in tasks.body).
It deliberately does NOT reuse parse_origin()'s "whole body is one JSON
object" approach: task_body doubles as the deliverable-generation prompt for
ordinary tasks (see talos.graph.spine.read_node), so it can't be forced into
pure JSON without breaking normal task execution. Rubrics instead use an
HTML-comment marker that coexists with free-form prose and renders invisible
in any markdown view of the task card.
"""
from __future__ import annotations

import json
import re


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


_RUBRIC_PATTERN = re.compile(r"<!--\s*talos:rubric:([\w-]+)\s*\r?\n(.*?)-->", re.DOTALL)


def extract_rubrics(task_body: str | None) -> dict[str, str]:
    """
    Parses zero or more `<!-- talos:rubric:<field>\\n...-->` blocks out of a
    task's free-form body. Returns {field_name: rubric_text} (stripped of
    leading/trailing whitespace) for every block found; a verifier whose
    rubric_field has no matching block is simply absent from the returned
    dict -- talos.critics.registry.run_all_verifiers treats that as "skip
    this verifier, zero cost," the default path for the overwhelming
    majority of tasks, which carry no rubric marker at all.

    Example:
        <!-- talos:rubric:rubric
        The deliverable must cite a resolvable source for every claim it
        makes.
        -->

    Multiple named blocks (different rubric_field values) may appear in one
    body and are all extracted. Known, documented limitation: a rubric body
    containing the literal substring "-->" truncates at the first
    occurrence (non-greedy DOTALL match) -- acceptable for prose rubrics.

    Deliberate scope note: task_body is also read_node's deliverable-
    generation prompt, so the rubric text stays visible to the generating
    model -- this is not stripped out for this landing. Arguably a feature,
    not a leak: the generator can aim directly at the bar it will be judged
    against, and a rubric only ever describes quality/procedural criteria,
    never secrets. A future blind-generator mode (stripping rubric markers
    before building the generation prompt) is a small, separate addition if
    an operator wants it -- not implied by anything here.
    """
    if not task_body:
        return {}
    return {field: text.strip() for field, text in _RUBRIC_PATTERN.findall(task_body)}
