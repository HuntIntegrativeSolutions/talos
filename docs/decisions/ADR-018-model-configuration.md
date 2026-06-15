# ADR-018: Model configuration — per-ladder-step mapping, TOML defaults, board and task overrides

**Status:** Accepted
**Date:** 2026-06-14
**Deciders:** Hunt Integrative Solutions LLC

## Context

P3b (multi-worker dispatcher) will invoke AI models as part of task execution. The Strategy Ladder
has six distinct steps — triage, research, plan, gate, execute, crystallize — each with different
cost/quality trade-offs. A fixed single model across all steps under-serves some steps (cheap
drafter is wasteful for triage) and over-spends on others (strong model is overkill for triage).

No existing ADR specifies how model identity is selected, cascaded, or overridden. The schema
already has `tasks.model_override TEXT` (comment: "e.g. DeepSeek on the ACME edge vs Opus on the
mothership"), but there is no policy on how config reaches that column or what its format means.

ADR-017 dropped the air-gap claim: PLC context egresses to hosted model API endpoints. This makes
model selection a data-egress routing decision. The config format must allow egress tracing even
though the model string itself is opaque.

## Decision

**Per-ladder-step model mapping with a three-level cascade and opaque model strings.**

### Cascade (high-to-low, later levels override earlier)

1. **`talos.toml [models]` — global defaults.** A `[models]` section defines 12 values: one
   primary and one fallback for each of the six Strategy Ladder steps.

   ```toml
   [models]
   triage         = "claude-haiku-4-5"
   triage_fallback = "claude-sonnet-4-6"
   research        = "claude-haiku-4-5"
   research_fallback = "claude-sonnet-4-6"
   plan            = "claude-sonnet-4-6"
   plan_fallback   = "claude-opus-4-8"
   gate            = "claude-sonnet-4-6"
   gate_fallback   = "claude-opus-4-8"
   execute         = "claude-sonnet-4-6"
   execute_fallback = "claude-opus-4-8"
   crystallize     = "claude-haiku-4-5"
   crystallize_fallback = "claude-sonnet-4-6"
   ```

2. **`boards.model_config JSONB` — board-level override.** Any or all of the 12 slots can be
   overridden per board. Missing keys fall back to `talos.toml`. Board overrides enable different
   model tiers for different deployment targets (e.g., Acme thick edge vs. mothership).

3. **`tasks.model_override TEXT` — per-task escape hatch (already in schema).** A single model
   name string. When set, this model is used for **all** Strategy Ladder steps for that task,
   overriding both `talos.toml` and `boards.model_config`. Coarse-grained: one string, all steps.

### Model string format

Model strings are **opaque names** (e.g., `"claude-opus-4-8"`, `"deepseek-chat"`). TALOS does
not parse or validate them. The LLM client layer resolves the provider/endpoint from its own
configuration (environment variables or LLM client config). This keeps the engine provider-agnostic
and allows site-specific endpoint URLs on thick edges without an engine code change.

Observability spans (ADR-022) capture both the opaque model string AND the resolved
provider/endpoint at call time, satisfying ADR-017's egress audit requirement without embedding
provider context in the config string.

### Failure behavior

On model call failure (rate limit, timeout, 5xx from provider):

1. **Try primary model** (as configured for this step).
2. **Try fallback model** (from the same config level).
3. **Both fail → escalate to human review immediately.** The task does not crash and does not
   increment `attempt_no`. Instead, a gate event is fired with outcome `escalate`, surfacing the
   double model failure to a human reviewer who decides whether to retry, reassign, or abort.

This treats double model failure as a gate event, not a crash, keeping the reliability mechanism
consistent with the five-outcome gate doctrine (ADR-011).

## Options considered

- **A — One global default model.** Simplest, but cannot differentiate triage cost from plan
  quality. Rejected.
- **B — Two-slot (fast/strong).** Steps resolve to fast or strong. Less config than per-step,
  harder to tune one step independently. Rejected.
- **C — Per-step mapping with three-level cascade (chosen).** Matches real cost/quality
  differentiation needs; cascade is predictable and auditable.
- **D — Model registry with validation.** TALOS validates model strings against a known list.
  Rejected: blocks site-specific endpoint URLs (Acme thick edge) and adds maintenance overhead
  for zero safety gain.

## Consequences

- **Easier:** step-level model differentiation from day one; `talos.toml` defaults cover 90% of
  deployments without board or task overrides.
- **Harder:** 12 config values instead of 1; `boards.model_config JSONB` column must be added
  to the `boards` table (currently has no config columns).
- **Revisit:** whether the 6-step × primary/fallback structure needs a per-step secondary fallback
  beyond the configured one.

## What this closes

- Unblocks P3b (multi-worker dispatcher + model config).
- Satisfies the ADR-017 egress audit requirement via span-level provider capture (ADR-022).

## Action items

1. [ ] Add `boards.model_config JSONB` column to `boards` table in `schema-additions.sql`.
2. [ ] Define `talos.toml [models]` section schema and a loader module in `platform/config.py`.
3. [ ] Wire model resolution in P3b dispatcher: `task.model_override → board config → toml config`.
4. [ ] Ensure LLM call spans (ADR-022) capture both model string and resolved provider/endpoint.
