# ADR-011 Clarification: Gate outcome configurability and shortened-gate definition

**Status:** Accepted (clarification record)
**Date:** 2026-06-14
**Deciders:** Hunt Integrative Solutions LLC
**Amends:** ADR-011 (Gate outcomes — five, not two; safety critics escalate-only)

## Context

ADR-011 defines five gate outcomes and the safety critic invariant. Downstream design questions
asked whether:

1. Operators can disable the `waive` outcome to enforce escalate-or-reject-only workflows.
2. The `escalate` outcome can be disabled for solo-reviewer deployments.
3. "Shortened gate" means a smaller critic set, restricted outcomes, or both.

RT-03 (2026-06-14) already decided that `escalate` is always available and a solo operator can
waive an escalated safety finding with a mandatory audit note. RT-18 (2026-06-14) decided that
plan-gate and deliverable-gate are separate. These are not re-opened here.

## Clarifications

### Waive availability

`waive` is **always available for non-safety critics**. Operators cannot disable it. The audit
trail (`task_gate_results.waived_by`, `task_gate_results.justification`) provides sufficient
accountability without removing the outcome. Making waive disableable would require a new
`boards.gate_config` restriction mechanism with no benefit beyond the existing audit columns.

Safety critics remain `waivable: False` (escalate-only) by the registry invariant. This is not
configurable.

### Shortened gate definition

**"Shortened gate" = a smaller required-critic set and nothing else.** All five outcomes remain
available in a shortened gate. A shortened gate is not a human-less gate — human approval via
`tasks.approved_at` is always required.

The five outcomes are fixed: `approve | reject | waive | edit | escalate`. No deployment
configuration can reduce or expand this set. The scope of reviewer discretion (which outcomes
are available) is identical across all boards.

## What this closes

- Closes the gate-outcome configurability question from the P3 pre-interview.
- Confirms that no `boards.gate_config` restriction mechanism for outcomes is needed.
- Aligns "shortened gate" definition with ADR-011's existing language.

## Action items

1. [ ] No code changes required — ADR-011 behavior is already implemented correctly.
2. [ ] Confirm in the operator guide that shortened gate = smaller critic set, not fewer outcomes.
