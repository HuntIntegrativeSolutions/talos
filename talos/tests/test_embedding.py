"""
talos.memory.embedding.get_embed_fn concurrency regression (live-mode
plumbing landing).

The read fan-out's read_branch_chroma and read_branch_rules run on separate
worker threads and both call get_embed_fn() on a cold process -- a bare
functools.lru_cache does not guarantee the wrapped function body itself only
runs once under concurrent first-callers. get_embed_fn now uses an explicit
double-checked lock instead; this test proves only one SentenceTransformer
gets constructed under concurrent first-callers.
"""

from __future__ import annotations

import threading

import pytest


@pytest.fixture(autouse=True)
def _reset_embed_cache():
    from talos.memory import embedding

    embedding.get_embed_fn.cache_clear()
    yield
    embedding.get_embed_fn.cache_clear()


def test_get_embed_fn_loads_model_exactly_once_under_concurrent_callers(monkeypatch):
    import time

    import sentence_transformers

    construct_count = {"n": 0}
    construct_lock = threading.Lock()

    class FakeModel:
        def encode(self, texts):
            class _Arr(list):
                def tolist(self):
                    return list(self)

            return _Arr([[0.0] for _ in texts])

    def fake_constructor(model_name, local_files_only=True):
        # Hold the "load" open briefly to widen the race window that would
        # expose a second concurrent construction if the lock were missing.
        time.sleep(0.05)
        with construct_lock:
            construct_count["n"] += 1
        return FakeModel()

    monkeypatch.setattr(sentence_transformers, "SentenceTransformer", fake_constructor)

    from talos.memory import embedding

    results = []
    results_lock = threading.Lock()

    def _call():
        fn = embedding.get_embed_fn()
        with results_lock:
            results.append(fn)

    threads = [threading.Thread(target=_call) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert construct_count["n"] == 1, "SentenceTransformer must be constructed exactly once"
    assert len(results) == 8
    assert len({id(fn) for fn in results}) == 1, "all callers must get the same cached encode fn"
