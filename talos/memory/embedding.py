"""Shared local-embedding accessor (extracted from chroma_store.py, ADR-039
action item #3).

Air-gap rule: this module must never silently download an embedding model or
call a cloud endpoint at runtime unless talos.toml's [memory] section
explicitly names a cloud embedding_provider. The default ("local") only ever
loads a pre-downloaded sentence-transformers model from local cache; if it
isn't present, get_embed_fn() raises a clear, operator-facing error naming
the model and the cache directory to pre-populate.

get_embed_fn() returns a plain `list[str] -> list[list[float]]` callable, not
a Chroma-specific EmbeddingFunction object -- chroma_store.py wraps this in
its own EmbeddingFunction adapter class for Chroma's API; pgvector_store.py
consumes it directly.
"""

from __future__ import annotations

import os
import threading

_embed_lock = threading.Lock()
_embed_fn_cache = None


def get_embed_fn():
    """Load the configured local embedding model and return an encode callable.

    Cached for the process lifetime: both spine read branches and every
    ingest/upsert call this, and reloading the model each time costs ~1s on
    the CPU-only reference box. A double-checked lock (not a bare
    functools.lru_cache) guards the first load -- concurrent first-callers
    from separate worker threads (e.g. read_branch_chroma and
    read_branch_rules in the same fan-out) must block on the same load
    rather than each racing to construct their own SentenceTransformer.
    Exceptions are never cached (mirrors lru_cache's own behavior): the
    air-gap RuntimeError below is still raised on every call until the model
    is actually present.

    torch's thread count is set once here, at model-load time (not per call --
    torch.set_num_threads is process-global, so re-applying it on every
    embedding call would be misleading about intent).
    """
    global _embed_fn_cache
    if _embed_fn_cache is not None:
        return _embed_fn_cache

    with _embed_lock:
        if _embed_fn_cache is not None:
            return _embed_fn_cache

        from talos.config import get_memory_config, get_resources_config

        cfg = get_memory_config()
        provider = cfg["embedding_provider"]
        model_name = cfg["embedding_model"]

        if provider != "local":
            raise NotImplementedError(
                f"embedding_provider={provider!r} is not implemented — only 'local' "
                "is supported today. A cloud provider must be added explicitly and "
                "deliberately; it is never reached silently."
            )

        from sentence_transformers import SentenceTransformer

        try:
            model = SentenceTransformer(model_name, local_files_only=True)
        except Exception as exc:
            hub_cache = os.environ.get(
                "HF_HOME", os.environ.get("HUGGINGFACE_HUB_CACHE", "~/.cache/huggingface")
            )
            legacy_dir = os.environ.get(
                "SENTENCE_TRANSFORMERS_HOME", "~/.cache/torch/sentence_transformers"
            )
            raise RuntimeError(
                f"Local embedding model {model_name!r} is not pre-downloaded and this "
                f"module will not fetch it silently (air-gap rule, P4a). Pre-download it "
                f"on a machine with network access and transfer the Hugging Face hub "
                f"cache ({hub_cache!r} — where local_files_only=True actually resolves "
                f"models from; see docs/install.md) or the legacy sentence-transformers "
                f"cache ({legacy_dir!r}), or set talos.toml's [memory] embedding_model "
                f"to a model that is present."
            ) from exc

        try:
            import torch
            torch.set_num_threads(get_resources_config()["embed_threads"])
        except ImportError:
            pass

        def _encode(texts: list[str]) -> list[list[float]]:
            return model.encode(list(texts)).tolist()

        _embed_fn_cache = _encode
        return _encode


def _cache_clear() -> None:
    """Reset the cached embedding fn -- for test isolation only. Mirrors the
    functools.lru_cache API this replaced, so existing
    get_embed_fn.cache_clear() call sites in the test suite work unchanged."""
    global _embed_fn_cache
    with _embed_lock:
        _embed_fn_cache = None


get_embed_fn.cache_clear = _cache_clear
