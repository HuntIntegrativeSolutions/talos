"""TALOS memory package — pgvector (default) / Chroma (interim) chunk stores.

ADR-039 action item #3: get_store() resolves to talos.memory.pgvector_store
or talos.memory.chroma_store per talos.config.get_memory_backend(), so
callers get a drop-in-swappable module without hardcoding either backend."""

from __future__ import annotations


def get_store():
    """Returns the memory-store module selected by get_memory_backend().

    Resolved at call time (not import time) so callers stay behind a stable
    try/except-degrade block and tests can monkeypatch the concrete backend
    module's functions directly."""
    from talos.config import get_memory_backend

    if get_memory_backend() == "chroma":
        from talos.memory import chroma_store
        return chroma_store

    from talos.memory import pgvector_store
    return pgvector_store
