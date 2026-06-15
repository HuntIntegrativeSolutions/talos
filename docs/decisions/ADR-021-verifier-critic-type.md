# ADR-021: Verifier critic type — VerifierSpec, configurable advisory/blocking, P5 implementation

**Status:** Accepted
**Date:** 2026-06-14
**Deciders:** Hunt Integrative Solutions LLC

## Context

Operators want a critic variant that runs an LLM sub-agent against a deliverable and a rubric,
scoring whether the deliverable meets quality or procedural criteria. This is architecturally
distinct from deterministic critics (which check structural invariants) because it introduces
nondeterminism and LLM cost into the gate path.

ADR-011/CR-06 states that learned (LLM) critics are advisory-only and their verdict never
auto-blocks or auto-approves. A verifier is an LLM critic. The question of whether a verifier
FAIL can block task progression before the human gate fires requires either reaffirming CR-06 or
amending it explicitly.

CriticSpec today has 5 fields (name, fn, required, safety_class, waivable). Verifiers need
additional fields: a rubric pointer, a judge model, a scoring threshold, an advisory flag, and a
failure-open/closed flag.

## Decision

**Verifiers are registered as a separate `VerifierSpec` dataclass via `register_verifier()`.
Advisory behavior is configurable per verifier. CR-06 is amended to allow non-safety-class
verifiers to be auto-blocking. Safety-class verifiers must always be advisory.**

### CR-06 Amendment

CR-06 ("LLM critics are advisory-only") is amended as follows:

- LLM critics with `safety_class=True` **must always be `advisory=True`**.
  Nondeterminism + safety-class = the human reviewer cannot be bypassed.
- LLM critics with `safety_class=False` **may be `advisory=False`** (auto-blocking).
  Operators who set `advisory=False` are knowingly delegating a pre-screening decision to a
  nondeterministic model. The task cannot proceed automatically if the verifier fails, but the
  Guardian doctrine is preserved — the human reviewer still receives all five gate outcomes and
  can waive, edit, or escalate. The practical effect is that a nondeterministic LLM stalls work
  before the human gate fires. This risk is the operator's to accept explicitly.

The registry enforces: `safety_class=True → advisory=True` for all `VerifierSpec` instances.
Attempting to register a `VerifierSpec` with `safety_class=True` and `advisory=False` raises at
registration time (same pattern as the existing `safety_class=True → waivable=False` check).

### VerifierSpec dataclass

```python
@dataclass
class VerifierSpec:
    name: str
    fn: Callable           # async callable: (deliverable, rubric_text, nexus_client) → VerifierVerdict
    required: bool
    safety_class: bool
    waivable: bool
    rubric_field: str      # name of the task metadata field holding the rubric text
    verifier_model: str | None  # None → falls back to the task's execution model
    score_threshold: float      # 0.0–1.0; score >= threshold = pass
    advisory: bool         # True = FAIL routes to human; False = FAIL blocks before human gate
    fail_open: bool        # advisory only: True = skip verifier on LLM failure; False = emit warn verdict
```

`fail_open` is ignored (treated as `False`) when `advisory=False`. An auto-blocking verifier
that fails open on LLM timeout is a safety gap with no structural enforcer; the closed behavior
is mandatory for auto-blocking verifiers.

### Rubric attachment

Rubrics are **per-task**, not per-verifier type. Each `VerifierSpec` declares `rubric_field`:
the name of the task body or metadata field that holds the rubric text for this verifier.
The verifier function receives the resolved rubric text at runtime. This allows the same verifier
type (e.g., "procedure-compliance verifier") to evaluate different rubrics on different tasks.

### Score format

The verifier callable returns a `VerifierVerdict` with:

```python
@dataclass
class VerifierVerdict:
    score: float           # 0.0–1.0
    passed: bool           # score >= spec.score_threshold
    reasoning: str         # LLM's explanation
```

`passed` is computed against `score_threshold` at call time. Both score and reasoning are stored
in `task_gate_results` (JSONB payload field) alongside the standard `verdict` field.

### Failure behavior

| verifier mode | LLM call fails |
|---|---|
| `advisory=True, fail_open=True` | Skip verifier; log WARNING; deterministic critics proceed |
| `advisory=True, fail_open=False` | Emit `warn` verdict; human reviewer sees the failure in gate |
| `advisory=False` | Fail closed; emit `fail` verdict; task cannot auto-advance; routes to human gate |

### Build phase

**P5, alongside Crystallize.** The verifier runner fits naturally in P5 because:
- P5 is already the first phase with significant LLM sub-agent calls (Crystallize step).
- CriticSpec stability is deferred until P4 (when verifier fields are finalized); VerifierSpec
  is also declared stable in P4 alongside CriticSpec.
- Adding LLM calls to the gate path in P3 would complicate P3's dispatcher + model work.

The `VerifierSpec` dataclass and `register_verifier()` registration function are defined in P4
when CriticSpec is declared stable. The runner implementation ships in P5.

## Options considered

- **A — All LLM critics are advisory (CR-06 unchanged).** Simplest; preserves original doctrine.
  Rejected: the operator explicitly wants auto-blocking verifiers for pre-screening and accepted
  the nondeterminism risk.
- **B — Advisory only per CR-06, with auto-blocking via a separate mechanism outside the critic
  framework.** Rejected: unnecessary indirection when the flag approach is clear and auditable.
- **C — `advisory: bool` on extended CriticSpec optional fields (no separate class).**
  Rejected: the builder prefers a separate `VerifierSpec` class with its own registration path
  for cleaner gate runner separation.
- **D — `VerifierSpec` with configurable `advisory` (chosen).** Two types in the gate runner but
  cleaner separation; CR-06 amended with explicit operator acknowledgment.

## Consequences

- **Easier:** rubric-driven quality evaluation without writing a new critic from scratch;
  advisory mode preserves CR-06 doctrine; the score is stored for audit alongside the verdict.
- **Harder:** two gate runner code paths (deterministic critics and verifiers); the advisory=False
  mode requires operators to understand they are delegating to a nondeterministic model; LLM cost
  in the gate path must be tracked against the three-axis budget (cost axis).
- **Revisit:** whether advisory=False verifiers should require explicit operator acknowledgment
  (e.g., a `confirmed_nondeterministic_blocking: bool` flag) to prevent accidental misuse.

## What this closes

- Defines the verifier critic type for P5 implementation.
- Amends CR-06 with an explicit doctrine note on the operator-acknowledged risk.
- Defers CriticSpec stability to P4.

## Action items

1. [ ] Define `VerifierSpec` and `VerifierVerdict` dataclasses in `platform/critics/` in P4.
2. [ ] Add `register_verifier()` to the registry with the safety-class invariant check in P4.
3. [ ] Implement the verifier runner in the gate path in P5.
4. [ ] Add `fail_open` ignored-for-auto-blocking invariant to `register_verifier()` in P4.
5. [ ] Store verifier `score` and `reasoning` in `task_gate_results.payload JSONB` in P5.
