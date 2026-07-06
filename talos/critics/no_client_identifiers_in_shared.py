"""
RT-06 (docs/integration/03_redteam_review.md:46,121-132): required,
non-waivable, deterministic critic. Blocks promotion of a deliverable to
[shared] scope if it contains any of the board's registered client
identifiers, an IP address, or a hostname-like pattern. ADR-005's "strip the
instance" sanitization is human-judgment only today ("Revisit / undefined"
checklist) -- this is the structural enforcer the red-team review demanded.

Pure function of (deliverable, client_identifiers) -- no DB access, no LLM,
no network. Registered safety_class=True / waivable=False in
talos.critics.registry (the safety_class=True -> waivable=False invariant is
enforced at registration time, ADR-021).

Scope guard (the single most important design decision in this critic):
this critic is registered GLOBALLY, so it runs against every task's
deliverable, not just promotion ones. To avoid false-failing every ordinary
deliverable that happens to contain an IP-looking string or similar,
`client_identifiers=None` means "not a promotion context" and the critic
no-ops (passes immediately). talos.graph.spine.deliverable_node only passes
a non-None client_identifiers list when the task's origin marker is
"rule_promotion" (see talos.task_origin.parse_origin). This keeps RT-06
required+non-waivable in the registry (so promotion tasks are ALWAYS
covered) while never touching any of the pre-existing non-promotion tests'
deliverables.
"""
from __future__ import annotations

import json
import re

from talos.critics.citations_resolvable import CriticResult

_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_HOSTNAME_RE = re.compile(r"\b[a-zA-Z0-9-]+\.(?:local|internal|lan|corp)\b", re.IGNORECASE)


def no_client_identifiers_in_shared(
    deliverable: dict, nexus_client=None, client_identifiers: list[str] | None = None,
) -> CriticResult:
    if client_identifiers is None:
        return CriticResult(
            passed=True,
            reason="not a promotion deliverable — RT-06 scan skipped",
            waivable=False,
        )

    haystack = _flatten_text(deliverable)
    matches = []

    for identifier in client_identifiers:
        if not identifier:
            continue
        for m in re.finditer(re.escape(identifier), haystack, re.IGNORECASE):
            matches.append({"kind": "client_identifier", "value": identifier, "pos": m.start()})

    for m in _IPV4_RE.finditer(haystack):
        matches.append({"kind": "ip_address", "value": m.group(0), "pos": m.start()})

    for m in _HOSTNAME_RE.finditer(haystack):
        matches.append({"kind": "hostname", "value": m.group(0), "pos": m.start()})

    if matches:
        return CriticResult(
            passed=False,
            reason=f"{len(matches)} client-identifier leak(s) detected: {matches[:5]}",
            waivable=False,
        )
    return CriticResult(passed=True, reason="no client identifiers detected", waivable=False)


def _flatten_text(deliverable: dict) -> str:
    return json.dumps(deliverable, default=str)
