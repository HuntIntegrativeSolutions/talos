"""
TALOS model configuration cascade (ADR-018; provider dimension added ADR-031).

resolve_model(step, board, task) -> (primary: ModelRef, fallback: ModelRef)

Cascade priority (highest to lowest):
  task.model_override TEXT  -- all-step override for one task
  board.model_config JSONB  -- board-level slot overrides
  talos.toml [models]       -- global defaults
  _HARDCODED_DEFAULTS       -- fallback when talos.toml is absent

The 6 Strategy Ladder steps: triage, research, plan, gate, execute, crystallize.
Model strings are opaque; TALOS passes them through to the LLM client unchanged.

Provider keys (ADR-031): each slot may set "{step}_primary_provider" and
"{step}_fallback_provider" (flat keys, matching this module's existing
"{step}_primary"/"{step}_fallback" convention -- NOT the nested [models.<step>]
table shown as an illustration in ADR-031's text, which does not match how this
module actually reads config; see ADR-018's own pre-existing, documented
discrepancy for precedent). Absent provider keys default to "anthropic" for
_HARDCODED_DEFAULTS (so every existing config is unaffected), and a missing
fallback_provider defaults to the resolved primary_provider for that slot (not
a hardcoded "anthropic") -- this keeps a partial air-gap override (only
{step}_primary_provider set to "ollama") from silently reaching for Anthropic
on fallback. Unknown provider names raise UnknownProviderError here, at
resolution time, before any NEXUS/model I/O.
"""

from __future__ import annotations

import os
import tomllib
import pathlib
import logging

log = logging.getLogger(__name__)

_TOML_PATH = pathlib.Path(__file__).resolve().parent.parent / "talos.toml"

# NEXUS MCP server URL (ADR-038: Streamable HTTP, not stdio). Ignored when
# TALOS_NEXUS_STUB=1.
TALOS_NEXUS_URL = os.environ.get("TALOS_NEXUS_URL", "http://10.0.0.80:8765/mcp")

_STEPS = ("triage", "research", "plan", "gate", "execute", "crystallize")

_HARDCODED_DEFAULTS: dict[str, str] = {
    "triage_primary":        "claude-haiku-4-5-20251001",
    "triage_fallback":       "claude-haiku-4-5-20251001",
    "research_primary":      "claude-sonnet-4-6",
    "research_fallback":     "claude-haiku-4-5-20251001",
    "plan_primary":          "claude-sonnet-4-6",
    "plan_fallback":         "claude-haiku-4-5-20251001",
    "gate_primary":          "claude-sonnet-4-6",
    "gate_fallback":         "claude-haiku-4-5-20251001",
    "execute_primary":       "claude-sonnet-4-6",
    "execute_fallback":      "claude-haiku-4-5-20251001",
    "crystallize_primary":   "claude-opus-4-8",
    "crystallize_fallback":  "claude-sonnet-4-6",
}


def _load_toml_models() -> dict[str, str]:
    if not _TOML_PATH.exists():
        return {}
    try:
        with open(_TOML_PATH, "rb") as f:
            data = tomllib.load(f)
        return data.get("models", {})
    except Exception:
        log.warning("talos.toml could not be parsed; using hardcoded model defaults")
        return {}


_TOML_MODELS: dict[str, str] = _load_toml_models()

_MEMORY_DEFAULTS: dict[str, str] = {
    "embedding_provider": "local",
    "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
}


def _load_toml_memory() -> dict[str, str]:
    """talos.toml [memory] section (P4a) — embedding_provider/embedding_model.
    Absent-file/parse-failure handling mirrors _load_toml_models() exactly."""
    if not _TOML_PATH.exists():
        return {}
    try:
        with open(_TOML_PATH, "rb") as f:
            data = tomllib.load(f)
        return data.get("memory", {})
    except Exception:
        log.warning("talos.toml could not be parsed; using hardcoded memory defaults")
        return {}


def get_memory_config() -> dict[str, str]:
    """Returns {embedding_provider, embedding_model}, talos.toml [memory] over defaults."""
    return {**_MEMORY_DEFAULTS, **_load_toml_memory()}


def resolve_model(
    step: str,
    board: dict | None = None,
    task: dict | None = None,
) -> "tuple[ModelRef, ModelRef]":
    """
    Return (primary, fallback) ModelRefs for the given Strategy Ladder step.

    Parameters
    ----------
    step:   one of triage | research | plan | gate | execute | crystallize
    board:  dict with optional 'model_config' key (JSONB decoded dict)
    task:   dict with optional 'model_override' key (TEXT)

    Raises UnknownProviderError if a resolved provider name isn't registered
    (talos.llm_providers) -- surfaced here, at resolution time, not deep in a
    spine node.
    """
    from talos.llm_providers import ModelRef, get_driver

    if step not in _STEPS:
        raise ValueError(f"Unknown ladder step {step!r}; must be one of {_STEPS}")

    # Per-task all-step override — coarsest granularity.
    if task and task.get("model_override"):
        model = task["model_override"]
        provider = task.get("model_override_provider", "anthropic")
        get_driver(provider)
        ref = ModelRef(provider, model)
        return ref, ref

    # Start from hardcoded defaults, layer toml on top.
    merged: dict[str, str] = {**_HARDCODED_DEFAULTS, **_TOML_MODELS}

    # Board-level slot overrides.
    if board and board.get("model_config"):
        board_cfg = board["model_config"]
        if isinstance(board_cfg, dict):
            merged.update(board_cfg)

    primary_model = merged.get(f"{step}_primary", merged.get(f"{step}", "claude-sonnet-4-6"))
    primary_provider = merged.get(f"{step}_primary_provider", "anthropic")
    fallback_model = merged.get(f"{step}_fallback", primary_model)
    fallback_provider = merged.get(f"{step}_fallback_provider", primary_provider)

    get_driver(primary_provider)
    get_driver(fallback_provider)

    return ModelRef(primary_provider, primary_model), ModelRef(fallback_provider, fallback_model)
