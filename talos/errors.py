"""
TALOS shared exception types. Kept in a separate module to avoid circular imports
between talos.graph.spine and talos.worker.
"""

from __future__ import annotations


class BudgetExhaustedError(Exception):
    """axis (P5.5): which TaskBudget axis tripped -- "tokens" | "elapsed" |
    "spend" | "model_invocations" -- so the gate-visible outcome can surface
    *which* cap fired, not just a free-text reason string. Optional/defaults
    to None for legacy 4-arg construction sites."""

    def __init__(self, task_id: str, run_id: int, board_id: str, reason: str, axis: str | None = None):
        super().__init__(reason)
        self.task_id = task_id
        self.run_id = run_id
        self.board_id = board_id
        self.axis = axis


class ModelFailureError(Exception):
    def __init__(self, task_id: str, run_id: int, board_id: str, reason: str):
        super().__init__(reason)
        self.task_id = task_id
        self.run_id = run_id
        self.board_id = board_id
