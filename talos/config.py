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

_MEMORY_DEFAULTS: dict[str, object] = {
    "embedding_provider": "local",
    "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
    "retrieval_k": 5,
    # Must match chunks.embedding's hardcoded vector(384) in
    # V0009_unified_memory.py (ADR-039) -- migrations are static raw SQL and
    # can't read talos.toml at migration time, so the two are kept in sync by
    # convention, not by a shared source.
    "embedding_dimension": 384,
    # ADR-039 action item #3: "pgvector" (default) or "chroma". Interim
    # dual-backend toggle for the Chroma -> pgvector migration; chroma_store.py
    # and its chromadb/sentence-transformers-adjacent dependency are removed in
    # a follow-up once pgvector is proven in production (see ADR-039's action
    # item list).
    "backend": "pgvector",
    # P5.5: whole-rule token cap for read_node's rules prompt-injection block
    # (talos.graph.spine._build_rules_prompt_block). Whitespace word-count
    # approximation (see talos.memory.chunking), not a real tokenizer -- 0 = unlimited.
    "rule_context_max_tokens": 1000,
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


def get_memory_config() -> dict[str, object]:
    """Returns {embedding_provider, embedding_model, retrieval_k,
    embedding_dimension, backend, rule_context_max_tokens}, talos.toml
    [memory] over defaults. retrieval_k (P5) is the default k for the spine's
    rules read branch (talos.graph.spine.read_branch_rules)."""
    return {**_MEMORY_DEFAULTS, **_load_toml_memory()}


def get_memory_backend() -> str:
    """'pgvector' (default) or 'chroma' (ADR-039 action item #3 interim
    toggle). TALOS_MEMORY_BACKEND env var overrides talos.toml [memory]
    backend, for ops rollback without a config edit."""
    import os

    return os.environ.get("TALOS_MEMORY_BACKEND") or get_memory_config().get("backend", "pgvector")


_RESOURCES_DEFAULTS: dict[str, object] = {
    # Worker pool size for CPU-bound local work (embedding, index builds) on
    # the air-gapped single-box deployment target (ADR-039). Defaults to
    # min(2, cpu_count - 2), floored at 1, so a small box still leaves
    # headroom for the dispatcher/API rather than saturating all cores.
    "cpu_workers": max(1, min(2, (os.cpu_count() or 2) - 2)),
    # Thread count for the local embedding model (P4a's sentence-transformers
    # path); independent of cpu_workers so embedding batches don't starve
    # other CPU-bound work.
    "embed_threads": 2,
    # pgvector index type for chunks.embedding (ADR-039 action item #5).
    # "ivfflat" is the CPU-only-friendly default; "hnsw" is opt-in for boxes
    # with headroom to spare on build time/memory.
    "index_type": "ivfflat",
    # Concurrent worker cap for HNSW index builds specifically (HNSW build is
    # far more CPU/memory-hungry than IVFFlat's; kept separate from
    # cpu_workers so switching index_type doesn't silently change concurrency
    # elsewhere).
    "hnsw_build_workers": 1,
    # Whether a local (non-Anthropic, non-cloud) LLM driver is available on
    # this box (ADR-031's Ollama zero-egress path). Gates whether
    # local-only-eligible ladder steps may resolve to it.
    "local_llm_enabled": False,
    # When true, the dispatcher/scheduler must prioritize requests serving the
    # human review gate (approval path) over long-running analysis work on the
    # shared LLM endpoint, so an analysis run can never starve an engineer
    # waiting at the gate (ROADMAP R8; serves the time-to-confident-approval KPI).
    "gate_path_priority": True,
    # "HH:MM-HH:MM" local-time window (empty string = disabled) during which
    # low-priority sleeptime/background work (e.g. index rebuilds) is allowed
    # to run, so it doesn't compete with interactive ladder steps.
    "sleeptime_window": "",
    # Token budget cap for sleeptime/background work per window; 0 = no cap.
    "sleeptime_max_tokens": 0,
}


def _load_toml_resources() -> dict[str, object]:
    """talos.toml [resources] section (ADR-039 action item #5) -- CPU/index-
    build knobs for the air-gapped single-box deployment target.
    Absent-file/parse-failure handling mirrors _load_toml_models() and
    _load_toml_memory() exactly."""
    if not _TOML_PATH.exists():
        return {}
    try:
        with open(_TOML_PATH, "rb") as f:
            data = tomllib.load(f)
        return data.get("resources", {})
    except Exception:
        log.warning("talos.toml could not be parsed; using hardcoded resources defaults")
        return {}


def get_resources_config() -> dict[str, object]:
    """Returns {cpu_workers, embed_threads, index_type, hnsw_build_workers,
    local_llm_enabled, gate_path_priority, sleeptime_window,
    sleeptime_max_tokens}, talos.toml [resources] over defaults. Not yet
    consumed by any index-build code -- ADR-039 action item #5 is
    config-loader-only at this stage."""
    return {**_RESOURCES_DEFAULTS, **_load_toml_resources()}


# P5.5: per-model USD pricing for ADR-030's max_spend_usd axis. Keys are
# "{provider}:{model}" (matching talos.llm_providers.base.ModelRef). These are
# placeholder public-list-price approximations, not verified against any
# actual billing contract -- override via talos.toml [pricing] before relying
# on spent_usd for a real enforcement decision. A model absent from this map
# prices at {0, 0} (spend.py's read_node logs a once-per-run warning rather
# than silently pretending the ceiling is enforced -- see talos.graph.spine).
_PRICING_DEFAULTS: dict[str, dict[str, float]] = {
    "anthropic:claude-haiku-4-5-20251001": {"input_per_1k_usd": 0.0008, "output_per_1k_usd": 0.004},
    "anthropic:claude-sonnet-4-6":         {"input_per_1k_usd": 0.003,  "output_per_1k_usd": 0.015},
    "anthropic:claude-opus-4-8":           {"input_per_1k_usd": 0.015,  "output_per_1k_usd": 0.075},
}


def _load_toml_pricing() -> dict[str, dict[str, float]]:
    """talos.toml [pricing] section (P5.5). Absent-file/parse-failure handling
    mirrors _load_toml_models() exactly."""
    if not _TOML_PATH.exists():
        return {}
    try:
        with open(_TOML_PATH, "rb") as f:
            data = tomllib.load(f)
        return data.get("pricing", {})
    except Exception:
        log.warning("talos.toml could not be parsed; using hardcoded pricing defaults")
        return {}


def get_pricing_config() -> dict[str, dict[str, float]]:
    """Returns {"{provider}:{model}": {input_per_1k_usd, output_per_1k_usd}},
    talos.toml [pricing] over defaults. Consumed by talos.graph.spine.read_node
    to price ADR-030's max_spend_usd axis."""
    return {**_PRICING_DEFAULTS, **_load_toml_pricing()}


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
