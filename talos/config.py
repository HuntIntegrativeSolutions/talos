"""
TALOS model configuration cascade (ADR-018).

resolve_model(step, board, task) -> (primary: str, fallback: str)

Cascade priority (highest to lowest):
  task.model_override TEXT  -- all-step override for one task
  board.model_config JSONB  -- board-level slot overrides
  talos.toml [models]       -- global defaults
  _HARDCODED_DEFAULTS       -- fallback when talos.toml is absent

The 6 Strategy Ladder steps: triage, research, plan, gate, execute, crystallize.
Model strings are opaque; TALOS passes them through to the LLM client unchanged.
"""

from __future__ import annotations

import tomllib
import pathlib
import logging

log = logging.getLogger(__name__)

_TOML_PATH = pathlib.Path(__file__).resolve().parent.parent / "talos.toml"

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


def resolve_model(
    step: str,
    board: dict | None = None,
    task: dict | None = None,
) -> tuple[str, str]:
    """
    Return (primary, fallback) model strings for the given Strategy Ladder step.

    Parameters
    ----------
    step:   one of triage | research | plan | gate | execute | crystallize
    board:  dict with optional 'model_config' key (JSONB decoded dict)
    task:   dict with optional 'model_override' key (TEXT)
    """
    if step not in _STEPS:
        raise ValueError(f"Unknown ladder step {step!r}; must be one of {_STEPS}")

    # Per-task all-step override — coarsest granularity.
    if task and task.get("model_override"):
        override = task["model_override"]
        return override, override

    # Start from hardcoded defaults, layer toml on top.
    merged: dict[str, str] = {**_HARDCODED_DEFAULTS, **_TOML_MODELS}

    # Board-level slot overrides.
    if board and board.get("model_config"):
        board_cfg = board["model_config"]
        if isinstance(board_cfg, dict):
            merged.update(board_cfg)

    primary = merged.get(f"{step}_primary", merged.get(f"{step}", "claude-sonnet-4-6"))
    fallback = merged.get(f"{step}_fallback", primary)

    return primary, fallback
