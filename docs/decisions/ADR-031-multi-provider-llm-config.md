# ADR-031: Multi-provider LLM configuration — provider abstraction, OAuth, and air-gap support

**Status:** Accepted
**Date:** 2026-06-17
**Deciders:** Hunt Integrative Solutions LLC

## Context

TALOS currently runs exclusively against the Anthropic Claude API. The 2026-06-16 requirements
interview established that:

1. v1 deploys on-prem at a controls engineer's workstation, which may be air-gapped from the
   internet.
2. The first real integration harness (P3.5) requires actual LLM calls — the LLM provider system
   must exist before P3.5 begins (P3.5 prerequisite).
3. Different organizations have different provider preferences and compliance constraints (data
   residency, API key escrow, self-hosted weights).

ADR-018 already establishes the per-ladder-step model slot structure (6 slots × primary + fallback,
3-level cascade: `talos.toml` → `boards.model_config` → `tasks.model_override`). This ADR extends
ADR-018 by defining the provider abstraction layer that sits beneath those slots.

## Decision

TALOS ships a **multi-provider LLM abstraction layer** as a P3.5 prerequisite. The abstraction:

### Provider surface

Any provider that implements the `LLMProvider` protocol is supported. v1 ships drivers for:

| Provider | Auth | Air-gap |
|---|---|---|
| Anthropic Claude (claude-sonnet-4-6, etc.) | API key or OAuth (Claude Code OAuth flow) | No |
| OpenAI / Codex | API key or OAuth | No |
| Deepseek | API key | No (API); Yes (self-hosted weights) |
| Ollama (local models: llama, mistral, etc.) | None (local process) | Yes |
| Any OpenAI-compatible endpoint | API key | Yes (if self-hosted) |

### Auth methods

Both API key and OAuth are supported where the provider offers them. The ADR-029 experiment
already verified OAuth works via Claude Code credentials for Anthropic. Each driver declares
which auth methods it supports; the config layer resolves the active credential at startup.

### Configuration

Provider selection follows the ADR-018 3-level cascade. Each model slot in `talos.toml` gains
a `provider` key:

```toml
[models.triage]
provider = "anthropic"          # or "openai", "deepseek", "ollama", etc.
model = "claude-sonnet-4-6"
fallback_provider = "ollama"
fallback_model = "llama3:8b"
```

`boards.model_config` JSONB overrides per board; `tasks.model_override` is the escape hatch.

### Air-gapped deployment

For plants with no internet access, set all `provider` values to `"ollama"` with a locally-hosted
model. No cloud API calls are made. The local model path is the documented air-gap configuration.

### Budget (ADR-030 integration)

Each provider driver reports token counts in the ADR-030 4-axis budget format. Cost per token
is configurable per provider in `talos.toml`. The budget hard-cap (P3.5 exit criterion: budget
exceeded → escalate, not crash) applies uniformly across all providers.

## Options considered

- **A — Claude-only, defer multi-provider.** Rejected: P3.5 requires real API calls; on-prem
  deployments without internet access cannot call Anthropic. Deferring means P3.5 cannot be
  tested on air-gapped workstations.
- **B — LangChain LLM abstraction.** Rejected: adds a heavy dependency for a thin interface
  that TALOS can implement in ~200 lines. Keeps the abstraction in-repo and testable.
- **C — In-repo provider protocol (chosen).** TALOS defines a `LLMProvider` protocol; each
  driver is a small class. ADR-018 slots map to provider+model pairs. Minimal surface,
  full control.

## Consequences

- ADR-018 `talos.toml` schema gains a `provider` key per model slot. Existing Claude-only
  configs remain valid (default provider = `"anthropic"`).
- The `ANTHROPIC_API_KEY` environment variable is superseded by per-provider credential config,
  but remains supported as a convenience shorthand for the anthropic provider.
- P3.5 harness must test: (a) real Claude API path, (b) Ollama local path (confirm no cloud
  egress), (c) fallback path when primary provider returns an error.
- ADR-029 action item #3 (PreToolUse/PostToolUse hooks for NEXUS tool spans) must be
  provider-neutral — hooks must work regardless of which provider drives the node.
