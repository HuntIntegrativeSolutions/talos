"""
TALOS PM hook registry (P3d).

Hooks are fire-and-forget async callbacks registered for task lifecycle events.
A hook that raises is logged and swallowed — hooks never block task transitions.

P3d events:
  on_task_approved — fires after post_gate_node completes for approve/waive/escalate outcomes.

P4b events:
  on_milestone_risk_escalated — fires after talos.pm_escalator.process_pending_escalations
    creates an issue-task (HIGH/missed) or remediation-task (MEDIUM/at_risk).

Future events (P4+): on_task_rejected, on_rule_extracted. `on_milestone_met`
remains a documented placeholder (no consumer exists yet) for a future
gate-UI feature firing when a milestone reaches status='met' — distinct from
on_milestone_risk_escalated above, which fires on the *risk* path (at_risk/
missed), not on 'met'.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Awaitable, Callable

log = logging.getLogger(__name__)

HookFn = Callable[[dict], Awaitable[None]]


class HookRegistry:
    def __init__(self):
        self._hooks: dict[str, list[HookFn]] = defaultdict(list)

    def register(self, event: str, fn: HookFn) -> None:
        self._hooks[event].append(fn)

    async def fire(self, event: str, payload: dict) -> None:
        for fn in self._hooks.get(event, []):
            try:
                await fn(payload)
            except Exception:
                log.exception("hook %r raised; continuing", event)

    def fire_sync(self, event: str, payload: dict) -> None:
        """Synchronous wrapper — runs async fire() in a new event loop."""
        fns = self._hooks.get(event, [])
        if not fns:
            return
        try:
            asyncio.run(self.fire(event, payload))
        except RuntimeError:
            # Already inside an event loop (e.g. test environment).
            loop = asyncio.get_event_loop()
            loop.run_until_complete(self.fire(event, payload))


# Module-level default registry. Worker registers hooks into this at startup.
default_registry = HookRegistry()
