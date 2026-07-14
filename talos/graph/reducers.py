"""
P4b (ADR-030/RT-21) commutative/associative reducers for the spine's
multi-writer LangGraph channels.

RT-21 (docs/integration/03_redteam_review.md:61): LangGraph Gotcha 2 — a
multi-writer channel's reducer must be commutative and associative, or
concurrent writes produce order-dependent, non-deterministic state, which
would undermine the gate's "reproducible blocking decision" guarantee.

Every multi-writer SpineState channel must use one of these. Every other
SpineState field is single-writer (exactly one node writes it per invocation)
and stays a plain, last-write-wins field — see SpineState's docstring for the
enumerated list and why each one qualifies.
"""
from __future__ import annotations


def merge_disjoint_dicts(left: dict | None, right: dict | None) -> dict:
    """
    Commutative, associative merge for dict-valued channels (context_branches,
    sdk_session_ids) whose concurrent writers each own disjoint keys (their own
    branch id). Because no two branches ever write the same key, dict-union is:
      - commutative: {**a, **b} == {**b, **a} when a and b share no keys.
      - associative: folding any subset together and then folding in the rest
        yields the same final dict, for the same reason.
    A writer MAY also redundantly re-copy keys it already saw in its input
    state (e.g. sdk_session_ids' existing entries) — that's safe too, since
    re-writing an existing key with its own unchanged value is idempotent and
    doesn't reintroduce order-dependence.
    """
    return {**(left or {}), **(right or {})}


_BUDGET_LIMIT_KEYS = (
    "max_spend_usd", "max_tokens", "max_model_invocations", "max_elapsed_seconds", "soft_spend_usd",
)
_BUDGET_ACCUM_KEYS = ("spent_usd", "tokens_used", "model_invocations")


def merge_budget(left: dict | None, right: dict | None) -> dict:
    """
    Commutative, associative merge for the `budget` channel (talos.graph.spine.TaskBudget).

    Every concurrent writer of this channel must return a *delta* for the
    accumulator fields (spent_usd, tokens_used, model_invocations) — the
    amount it contributed this call, starting from zero, never the running
    total. Summing deltas is commutative/associative; summing running totals
    (which double-count the baseline already present in the channel) is not.

    LangGraph applies this reducer pairwise across whatever writes land in a
    superstep, in no guaranteed order — a naive "copy limit fields from
    whichever side looks like the full baseline" approach is NOT actually
    commutative, because which operand ends up as `left` vs `right` in a given
    pairwise fold isn't fixed. So every writer must copy the limit fields
    (max_spend_usd, max_tokens, max_model_invocations, max_elapsed_seconds,
    soft_spend_usd) forward into its own delta dict unchanged (they're read
    from the same pre-fan-out state by every branch and never modified by any
    of them) — that makes `left` and `right` structurally symmetric, so this
    reducer can pick limits from either side with an identical result
    regardless of fold order.
    """
    if left is None:
        return right
    if right is None:
        return left
    merged = {}
    for key in _BUDGET_LIMIT_KEYS:
        if key in left:
            merged[key] = left[key]
        elif key in right:
            merged[key] = right[key]
    for key in _BUDGET_ACCUM_KEYS:
        merged[key] = (left.get(key) or 0) + (right.get(key) or 0)
    return merged
