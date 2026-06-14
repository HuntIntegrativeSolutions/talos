"""
Critic registry for TALOS P2.

Enforces the invariant: safety_class=True → waivable=False.
A mis-flagged critic is caught at registration time, not at review time (RT-02).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class CriticSpec:
    name: str
    fn: Callable
    required: bool
    safety_class: bool
    waivable: bool


_registry: dict[str, CriticSpec] = {}


def register(spec: CriticSpec) -> None:
    if spec.safety_class and spec.waivable:
        raise ValueError(
            f"critic {spec.name!r}: safety_class=True requires waivable=False"
        )
    _registry[spec.name] = spec


def get_all() -> list[CriticSpec]:
    return list(_registry.values())


def get(name: str) -> CriticSpec | None:
    return _registry.get(name)


def run_all(deliverable: dict, nexus_client=None) -> list[dict]:
    results = []
    for spec in _registry.values():
        result = spec.fn(deliverable, nexus_client=nexus_client)
        verdict = "pass" if result.passed else "fail"
        results.append({
            "name": spec.name,
            "required": spec.required,
            "safety_class": spec.safety_class,
            "waivable": spec.waivable,
            "passed": result.passed,
            "reason": result.reason,
            "verdict": verdict,
        })
    return results


# ---------------------------------------------------------------------------
# P2 starter registry — registered at module load; imported by deliverable_node
# ---------------------------------------------------------------------------

from platform.critics.citations_resolvable import citations_resolvable  # noqa: E402
from platform.critics.no_live_write import no_live_write_in_deliverable  # noqa: E402

register(CriticSpec(
    name="citations_resolvable",
    fn=citations_resolvable,
    required=True,
    safety_class=False,
    waivable=True,
))

register(CriticSpec(
    name="no_live_write_in_deliverable",
    fn=no_live_write_in_deliverable,
    required=True,
    safety_class=True,
    waivable=False,
))
