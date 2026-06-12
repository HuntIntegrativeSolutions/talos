# ADR-011: Gate outcomes — five, not two; safety critics escalate-only

**Status:** Accepted
**Date:** 2026-06-11
**Deciders:** Hunt Integrative Solutions LLC

## Context

A binary approve/reject gate forces bad choices: a reviewer who spots one fixable flaw must bounce the
whole deliverable, and a critic that is wrong in context has no sanctioned override path. TALOS needs a
richer gate vocabulary that still cannot become a doctrine bypass (`BLUEPRINT.md` §117–124).

## Decision

Gate outcomes are **five** (`BLUEPRINT.md` §120–124): **Approve** · **Reject-with-reason** (back to
the worker with a note) · **Waive-with-justification** (a critic failed but the human overrides it —
recorded, not skipped) · **Edit-inline** (fix the deliverable, then approve) · **Escalate** (second
reviewer). `task_gate_results` carries `waivable`, `waived_by`, and `justification`; **safety critics
are `waivable: false → escalate-only`**, so a waiver can never become a doctrine bypass. A gated task
can't advance until every required critic is `pass` **and** a human sets `approved_at`. Folded in from
the reconciliation pass:

- **Learned (LLM) critics are advisory-only** — they may `warn` or emit a non-required `fail` a human
  weighs, never auto-block or auto-approve; every *required* critic and *all* safety critics stay
  deterministic (CR-06).
- The **gate node is pure** (`interrupt()` only); *all* side effects live in a separate post-gate node
  so a resume can't double-fire them, each carrying an idempotency key (CR-12).
- A cited NEXUS finding must be **`confirmed`-status**, never `proposed`/`dismissed` — enforced by the
  `citations-resolvable` critic, not a second human gate (CR-18).
- **"Shortened gate" = a smaller required-critic set / expedited review, never skip-human-approval**;
  safety critics stay escalate-only regardless of how short the gate is (CR-26).
- The milestone-risk escalator emits **MEDIUM** by default (auto-dispatch a *gated* remediation task),
  but **safety-significant milestones emit HIGH → stage for human review, never auto-dispatch** (CR-22,
  ADR-016).

## Options considered

- **A — Binary approve/reject.** Rejected: no override audit trail, no inline fix, and every flaw forces
  a full re-loop.
- **B — Five outcomes with recorded waivers and an escalate-only class for safety critics.** Chosen.

## Trade-off analysis

Five outcomes match how review actually works — most flaws are fixable or waivable-with-reason — while
the `waivable:false → escalate` rule keeps the safety floor un-waivable: a waiver is *recorded*, not a
hole. The cost is more gate states and an audit schema (`waivable` / `waived_by` / `justification`).
Keeping learned critics advisory keeps the *blocking* decision reproducible. Upstream:
`langgraph-notes.md` → "Human-in-the-Loop" (the 5-way `Command` mapping; side-effects in a post-gate
node); `hermes-notes.md` → "Dispatcher Loop" (a `review` status already exists but is ungated — TALOS
adds critics + `approved_at`).

## Consequences

- **Easier:** reviewers fix / override / escalate instead of bouncing everything; every override is
  audited.
- **Harder:** more gate states; the critic taxonomy (deterministic-required vs learned-advisory) and
  the shortened-gate definition must be encoded.
- **Revisit:** the required-critic set per deliverable type; the milestone-severity mapping (ADR-016
  action item).

## Action items

1. [ ] Implement the five outcomes + `task_gate_results` (`waivable` / `waived_by` / `justification`).
2. [ ] Mark safety critics `waivable:false → escalate-only`; classify learned critics advisory-only.
3. [ ] Keep the gate node pure; put all side effects in a post-gate node with idempotency keys.
4. [ ] Add the `citations-resolvable` critic requiring `confirmed`-status NEXUS findings.
5. [ ] Define "shortened gate" as a narrower critic set, never human-less.
