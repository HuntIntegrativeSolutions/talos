# ADR-030 — Budget Enforcement: 4-Axis Budget Object and Threshold Semantics

**Status:** Accepted  
**Date:** 2026-06-14  
**Phase:** P3b

---

## Context

The Omnigent research (`docs/upstream/omnigent-notes.md`) identified budget enforcement as a necessary
control for long-running agent tasks. Without budget limits, a single runaway task can exhaust API
credits, cause uncontrolled tool-call loops, or run indefinitely.

Two failure modes require distinct handling:
1. **Hard cap exceeded** — task has consumed more than its allowed budget on any axis.
2. **Soft threshold crossed** — task is approaching its budget limit but has not yet exceeded it.

The Guardian Doctrine (`CLAUDE.md`) establishes that humans own all state transitions. This constrains
how budget exhaustion is handled: auto-terminating a task or crashing the worker would bypass the
human review gate.

---

## Decision

### 4-axis budget object (added to SpineState in P3b)

```python
class TaskBudget(TypedDict):
    max_spend_usd: float        # hard cap on API spend; 0.0 = unlimited
    max_tokens: int             # hard cap on total tokens; 0 = unlimited
    max_tool_calls: int         # hard cap on tool invocations; 0 = unlimited
    max_elapsed_seconds: int    # hard cap on wall-clock time; 0 = unlimited
    soft_spend_usd: float       # soft threshold (not yet a hard cap)
    spent_usd: float            # running total
    tokens_used: int            # running total
    tool_calls: int             # running total
```

### Hard cap exceeded → forced early gate (status = 'review')

When any hard cap is exceeded, the worker:
1. Sets `task_runs.outcome = 'budget_exhausted'` and `task_runs.ended_at = now()`.
2. Sets `tasks.status = 'review'` — the task is queued for human decision.
3. Does NOT increment `attempt_no` (this is not a crash; the attempt is still live in the human's hands).
4. Does NOT route through `post_gate_node`'s escalate branch (which auto-approves). Budget exhaustion
   lands the task in `review` awaiting a human decision: retry, extend budget, or reject.

This keeps budget enforcement consistent with the five-outcome gate doctrine (ADR-011): no automated
approval, no silent termination. The human decides what happens next.

### Soft threshold crossed → continue + span

When `soft_spend_usd` is crossed (and hard caps are not yet exceeded):
1. Continue execution normally.
2. Emit a `spine.budget.soft_threshold` span with the current spend.
3. Do NOT interrupt the task.

### Hard caps of 0 = unlimited

A zero value on any axis means that axis is not enforced. Default budgets have all caps at 0 (unlimited).

---

## Rationale

**Why route through review instead of a new gate outcome?**  
Budget exhaustion is operationally similar to `gate_outcome = escalate` but semantically different:
escalation is a human operator overriding a safety critic, while budget exhaustion is an automated
enforcement action. Keeping them separate (different `task_runs.outcome` values, different UI affordances
in P7) avoids conflating two distinct concepts. The final human decision uses the same five-gate outcomes.

**Why 4 axes?**  
Derived from Omnigent research: spend_usd covers API cost, tokens covers context-window exhaustion,
tool_calls covers loop-detection, elapsed_seconds covers wall-clock runaway. Together they cover the
four most common runaway failure modes in production agent deployments.

**Why not crash the worker?**  
A crash loses the current graph checkpoint. Routing to `review` preserves the checkpoint (PostgresSaver),
allowing a human to inspect the partial result and decide whether to resume, extend, or reject.

---

## Consequences

- Workers must catch `BudgetExhaustedError` in the dispatcher loop and call `_handle_budget_exhaustion()`.
- Budget is initialized at claim time from task metadata (P4 will wire task-level budget fields to the DB).
- `task_runs.outcome` gains a new value: `'budget_exhausted'` (schema already has `outcome TEXT` — no migration needed).
- `spine.budget.soft_threshold` is added to the P3 minimum span set (ADR-022).

---

## Source

Omnigent research (`docs/upstream/omnigent-notes.md`), sections on write-paths allowlist and budget
object design patterns.
