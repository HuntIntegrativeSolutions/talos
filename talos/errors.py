"""
TALOS shared exception types. Kept in a separate module to avoid circular imports
between talos.graph.spine and talos.worker.
"""

from __future__ import annotations


class BudgetExhaustedError(Exception):
    def __init__(self, task_id: str, run_id: int, board_id: str, reason: str):
        super().__init__(reason)
        self.task_id = task_id
        self.run_id = run_id
        self.board_id = board_id


class ModelFailureError(Exception):
    def __init__(self, task_id: str, run_id: int, board_id: str, reason: str):
        super().__init__(reason)
        self.task_id = task_id
        self.run_id = run_id
        self.board_id = board_id
