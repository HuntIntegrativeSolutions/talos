"""
Critic registry for TALOS P2, plus the P6/ADR-021 verifier registry.

Enforces the invariant: safety_class=True → waivable=False.
A mis-flagged critic is caught at registration time, not at review time (RT-02).

Verifiers (ADR-021) are a separate registry/dataclass/runner, deliberately not
sharing _registry/CriticSpec/run_all -- see VerifierSpec's docstring for why
this module cannot import talos.graph.spine or talos.llm (spine already
imports this module, and the actual LLM call is made by a spine-side closure
passed in as run_all_verifiers' score_fn).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
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


def run_all(deliverable: dict, nexus_client=None, client_identifiers: list[str] | None = None) -> list[dict]:
    """
    client_identifiers (P4b/RT-06): forwarded to every critic. Only
    no_client_identifiers_in_shared acts on it — the other critics accept and
    ignore the kwarg (additive signature change, zero behavior change). Pass
    a non-None list only for rule-promotion deliverables (see
    talos.graph.spine.deliverable_node) — RT-06 treats None as "not a
    promotion context" and no-ops.
    """
    results = []
    for spec in _registry.values():
        result = spec.fn(deliverable, nexus_client=nexus_client, client_identifiers=client_identifiers)
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

from talos.critics.citations_resolvable import citations_resolvable  # noqa: E402
from talos.critics.no_client_identifiers_in_shared import no_client_identifiers_in_shared  # noqa: E402
from talos.critics.no_live_write import no_live_write_in_deliverable  # noqa: E402

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

register(CriticSpec(
    name="no_client_identifiers_in_shared",
    fn=no_client_identifiers_in_shared,
    required=True,
    safety_class=True,
    waivable=False,
))


# ---------------------------------------------------------------------------
# P6 / ADR-021 — verifier critics: LLM-scored critics judged against a
# per-task rubric. Separate registry, dataclasses, and runner from the
# deterministic critics above (ADR-021 option D) -- run_all/_registry are
# untouched by anything below this line.
# ---------------------------------------------------------------------------


@dataclass
class VerifierSpec:
    """
    ADR-021's verifier critic type.

    `fn` is retained for ADR-021 field-list fidelity but is NOT invoked
    directly by run_all_verifiers in this landing -- the actual LLM call is
    made by a spine-side closure (talos.graph.spine._make_verifier_score_fn)
    passed in as run_all_verifiers' `score_fn`, because this module must not
    import talos.graph.spine (spine already imports talos.critics.registry)
    or talos.llm. A future landing may invert this and call verifiers as
    first-class async callables.

    `required` is vestigial this landing: the row persisted to
    task_gate_results derives its `required` flag from `advisory`, NOT from
    this field (see run_all_verifiers). Kept only for ADR-021 field-list
    fidelity.

    `verifier_model`, if set, is "{provider}:{model}" (matching the
    [pricing] config key format); a bare string with no ":" defaults
    provider to "anthropic". None falls back entirely to
    resolve_model("execute").

    Registration-time invariants enforced by register_verifier(), both
    raising ValueError:
      - safety_class=True and advisory=False (CR-06 amendment: nondeterminism
        + safety_class = the human reviewer can never be bypassed)
      - safety_class=True and waivable=True (mirrors CriticSpec's existing
        invariant)
    Additionally, advisory=False silently forces fail_open=False at
    registration (not an error): an auto-blocking verifier that fails OPEN
    on an LLM timeout is a safety gap with no structural enforcer (ADR-021's
    failure-behavior table).

    `deterministic` (P6 Landing 2): when True, run_all_verifiers calls
    `fn(deliverable, rubric_text, nexus_client)` directly instead of
    `score_fn(spec, rubric_text)` -- an explicit flag rather than duck-typing
    on `fn`, so the LLM-scored path (score_fn, used when False) is untouched.
    `fn` must return the same `(score: float | None, reasoning: str)` tuple
    contract as `score_fn` -- the rest of run_all_verifiers' failure-table /
    threshold / persistence logic is reused unchanged for either path.
    Defaults to False so existing/future LLM-scored verifiers need no change.
    """
    name: str
    fn: Callable
    required: bool
    safety_class: bool
    waivable: bool
    rubric_field: str
    verifier_model: str | None
    score_threshold: float
    advisory: bool
    fail_open: bool
    deterministic: bool = False


@dataclass
class VerifierVerdict:
    score: float
    passed: bool
    reasoning: str


_verifier_registry: dict[str, VerifierSpec] = {}


def register_verifier(spec: VerifierSpec) -> None:
    if spec.safety_class and not spec.advisory:
        raise ValueError(
            f"verifier {spec.name!r}: safety_class=True requires advisory=True"
        )
    if spec.safety_class and spec.waivable:
        raise ValueError(
            f"verifier {spec.name!r}: safety_class=True requires waivable=False"
        )
    if not spec.advisory and spec.fail_open:
        spec = replace(spec, fail_open=False)
    _verifier_registry[spec.name] = spec


def get_all_verifiers() -> list[VerifierSpec]:
    return list(_verifier_registry.values())


def get_verifier(name: str) -> VerifierSpec | None:
    return _verifier_registry.get(name)


def run_all_verifiers(
    deliverable: dict,
    rubrics: dict[str, str],
    *,
    score_fn: Callable[[VerifierSpec, str], "tuple[float | None, str | None]"],
    nexus_client=None,
) -> list[dict]:
    """
    rubrics: {rubric_field: rubric_text}, pre-resolved by the caller (e.g.
    talos.task_origin.extract_rubrics(task_body)) -- this function does not
    parse task_body itself, so it stays testable with plain dicts.

    score_fn(spec, rubric_text) -> (score, reasoning). Contract:
      - score is a float in [0.0, 1.0] and reasoning is set -> LLM call
        succeeded.
      - score is None -> LLM call FAILED (unparseable, out-of-range, or the
        call itself errored). score_fn owns accounting any tokens/spend it
        burned into its own closed-over budget dict BEFORE returning None --
        a failed call still costs money.

    For spec.deterministic=True verifiers, `spec.fn(deliverable, rubric_text,
    nexus_client)` is called directly instead of score_fn, with the identical
    (score, reasoning) contract -- score=None means "refused or could not
    verify" (invalid config, unreachable target, timeout, etc.), not an LLM
    failure, but goes through the same failure-table branch below since the
    branch's behavior (skip / warn / fail by advisory+fail_open) is exactly
    what's wanted either way. No budget contribution: deterministic verifiers
    make no LLM call.

    Per verifier, in registration order:
      1. rubric_text = rubrics.get(spec.rubric_field). If absent: SKIP
         entirely -- no score_fn call, no row in the returned list, therefore
         no task_gate_results row and no budget contribution. This is the
         default, zero-cost path for the overwhelming majority of tasks,
         which carry no rubric marker at all.
      2. score, reasoning = score_fn(spec, rubric_text).
      3. If score is None (LLM failure), apply ADR-021's failure table:
           advisory=True,  fail_open=True  -> skip (log WARNING); no row
           advisory=True,  fail_open=False -> emit verdict="warn"
           advisory=False                  -> emit verdict="fail"
                                               (fail_open already normalized
                                               False at registration, so this
                                               row is unconditional)
      4. Else: passed = score >= spec.score_threshold; verdict = "pass" if
         passed else ("warn" if spec.advisory else "fail"). An advisory=True
         verifier that scores below threshold still reaches the human gate
         as informational ("warn"), never silently "fail" -- only
         advisory=False produces a blocking "fail" verdict.

    required-mapping persisted for each emitted row (mirrors CriticSpec's
    shape so talos.graph.spine._persist_gate_results can be reused
    unchanged):
      advisory=True  -> required=False (v_gate_status ignores it; the human
                         reviewer still sees score/reasoning in the gate UI)
      advisory=False -> required=True  (v_gate_status.all_required_pass goes
                         false on a "fail" row, blocking auto-advance until
                         waived (waivable verifiers only) or a later passing
                         row lands)

    Returns a list of dicts shaped identically to run_all()'s critic verdict
    dicts -- {name, required, safety_class, waivable, passed, reason,
    verdict} -- PLUS "score" and "reasoning", so _persist_gate_results (which
    json.dumps(v) into task_gate_results.details) works unchanged and both
    fields land in the JSONB for free.
    """
    results = []
    for spec in _verifier_registry.values():
        rubric_text = rubrics.get(spec.rubric_field)
        if rubric_text is None:
            continue  # zero-cost skip: no LLM call, no row, no budget contribution

        if spec.deterministic:
            score, reasoning = spec.fn(deliverable, rubric_text, nexus_client)
        else:
            score, reasoning = score_fn(spec, rubric_text)

        if score is None:
            failure_kind = "verification" if spec.deterministic else "LLM call"
            if spec.advisory and spec.fail_open:
                logging.getLogger(__name__).warning(
                    "verifier %s: %s failed, fail_open=True -> skipping", spec.name, failure_kind
                )
                continue
            verdict = "warn" if spec.advisory else "fail"
            default_reason = (
                "verifier could not complete deterministic verification"
                if spec.deterministic
                else "verifier LLM call failed or returned unparseable output"
            )
            results.append({
                "name": spec.name,
                "required": not spec.advisory,
                "safety_class": spec.safety_class,
                "waivable": spec.waivable,
                "passed": False,
                "reason": reasoning or default_reason,
                "verdict": verdict,
                "score": None,
                "reasoning": reasoning,
            })
            continue

        passed = score >= spec.score_threshold
        verdict = "pass" if passed else ("warn" if spec.advisory else "fail")
        results.append({
            "name": spec.name,
            "required": not spec.advisory,
            "safety_class": spec.safety_class,
            "waivable": spec.waivable,
            "passed": passed,
            "reason": reasoning or "",
            "verdict": verdict,
            "score": score,
            "reasoning": reasoning,
        })
    return results


# ---------------------------------------------------------------------------
# P6 starter verifier registry — registered at module load.
# ---------------------------------------------------------------------------

register_verifier(VerifierSpec(
    name="rubric_compliance",
    fn=lambda *a, **kw: None,  # unused this landing; see VerifierSpec docstring
    required=False,
    safety_class=False,
    waivable=True,
    rubric_field="rubric",
    verifier_model=None,
    score_threshold=0.8,
    advisory=True,
    fail_open=False,
))

# P6 Landing 2 -- deterministic emulator-consistency verifier (ADR-021/024).
# Imported here (not at module top) so this module's own import graph stays
# free of pylogix at load time; talos.verifiers.emulator itself only imports
# pylogix lazily inside the function that does the live read, so this import
# does not violate the "registry must not import spine/llm/pylogix at module
# level" invariant (see the module docstring and
# test_registry_module_does_not_import_pylogix).
from talos.verifiers.emulator import emulator_consistency_verifier  # noqa: E402

register_verifier(VerifierSpec(
    name="emulator_consistency",
    fn=emulator_consistency_verifier,
    required=False,
    safety_class=False,
    waivable=True,
    rubric_field="emulator_consistency",
    verifier_model=None,
    score_threshold=0.95,
    advisory=True,
    fail_open=False,
    deterministic=True,
))
