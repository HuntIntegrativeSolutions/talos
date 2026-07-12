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


import functools


@functools.lru_cache(maxsize=1)
def get_embed_fn():
    """Load the configured local embedding model and return an encode callable.

    Cached for the process lifetime: both spine read branches and every
    ingest/upsert call this, and reloading the model each time costs ~1s on
    the CPU-only reference box. (lru_cache does not cache exceptions, so the
    air-gap RuntimeError below is still raised on every call until fixed.)

    torch's thread count is set once here, at model-load time (not per call --
    torch.set_num_threads is process-global, so re-applying it on every
    embedding call would be misleading about intent).
    """
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
        cache_dir = os.environ.get(
            "SENTENCE_TRANSFORMERS_HOME", "~/.cache/torch/sentence_transformers"
        )
        raise RuntimeError(
            f"Local embedding model {model_name!r} is not pre-downloaded and this "
            f"module will not fetch it silently (air-gap rule, P4a). Pre-download it "
            f"on a machine with network access and place it under {cache_dir!r} "
            f"(or set SENTENCE_TRANSFORMERS_HOME to a directory that already has it), "
            f"or set talos.toml's [memory] embedding_model to a model that is present."
        ) from exc

    try:
        import torch
        torch.set_num_threads(get_resources_config()["embed_threads"])
    except ImportError:
        pass

    def _encode(texts: list[str]) -> list[list[float]]:
        return model.encode(list(texts)).tolist()

    return _encode
