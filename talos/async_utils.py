"""
Shared async->sync bridge (P6-Sim Landing 3 live-mode-plumbing fix).

call_model's drivers each need to run a coroutine from synchronous code, but
that sync code is sometimes reached from inside an already-running event
loop (the API process's gate-approval hook chain: hooks.fire_sync starts a
loop via asyncio.run, then the async hook body calls back down into a
driver's synchronous .call()). A bare asyncio.run() there raises
"asyncio.run() cannot be called from a running event loop".

run_coro() is the one-shot fix: when no loop is running, asyncio.run() works
as-is; when one is running, the coroutine is submitted to a dedicated thread
(which has no event loop of its own) via ThreadPoolExecutor, sidestepping
the nested-loop restriction. Every talos/llm_providers driver and
talos/nexus_seed.py route their asyncio.run() calls through this one helper
instead of each re-implementing the running-loop check.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Coroutine


def run_coro(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run an async coroutine from sync code, safe whether or not the
    calling thread already has a running event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()
