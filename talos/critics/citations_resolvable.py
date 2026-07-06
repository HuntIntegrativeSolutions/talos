"""
Deterministic critic: citations_resolvable.

Checks that every NEXUS finding cited in a deliverable has status 'confirmed'.
No LLM, no network. Pure function — inject nexus_client for future live lookups.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CriticResult:
    passed: bool
    reason: str
    waivable: bool = True


def citations_resolvable(deliverable: dict, nexus_client=None, client_identifiers=None) -> CriticResult:
    """
    Pass iff every deliverable["citations"] entry has status == 'confirmed'.

    When nexus_client is provided and raises any exception, fail closed —
    stale cache results are never served (RT-05).
    """
    citations = deliverable.get("citations", [])

    if nexus_client is not None:
        try:
            nexus_client(citations)
        except Exception as e:
            return CriticResult(
                passed=False,
                reason=f"NEXUS unavailable — fail closed: {e}",
                waivable=True,
            )
    if not citations:
        return CriticResult(
            passed=False,
            reason="deliverable contains no citations",
        )

    for citation in citations:
        finding_id = citation.get("finding_id", "<unknown>")
        status = citation.get("status")
        if status != "confirmed":
            return CriticResult(
                passed=False,
                reason=(
                    f"finding {finding_id!r} has status {status!r}; expected 'confirmed'"
                ),
            )

    return CriticResult(passed=True, reason="all citations confirmed")
