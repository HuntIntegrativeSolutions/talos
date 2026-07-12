"""Pure markdown-chunking tests (talos/memory/chunking.py) — backend-agnostic,
shared by both the Chroma and pgvector stores. Moved out of
test_chroma_store.py (ADR-039 action item #3) so this pure-function surface
is tested once, not duplicated per backend."""

from __future__ import annotations

from talos.memory import chunking


def test_chunk_by_heading_splits_on_headings():
    markdown = "# Title\nintro text\n\n## Section A\ncontent A\n\n## Section B\ncontent B\n"
    chunks = chunking.chunk_by_heading(markdown, max_tokens=500)
    assert len(chunks) == 3
    assert chunks[0].startswith("# Title")
    assert chunks[1].startswith("## Section A")
    assert chunks[2].startswith("## Section B")


def test_chunk_by_heading_no_headings_returns_single_chunk():
    markdown = "just plain text with no headings at all"
    chunks = chunking.chunk_by_heading(markdown, max_tokens=500)
    assert chunks == [markdown]


def test_chunk_by_heading_empty_returns_no_chunks():
    assert chunking.chunk_by_heading("", max_tokens=500) == []
    assert chunking.chunk_by_heading("   \n  ", max_tokens=500) == []


def test_chunk_by_heading_oversize_fallback_splits_by_paragraph():
    para_a = "wordA " * 300
    para_b = "wordB " * 300
    markdown = f"# Big Section\n{para_a}\n\n{para_b}"
    chunks = chunking.chunk_by_heading(markdown, max_tokens=100)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c.split()) <= 350  # roughly bounded, allows heading overhead


def test_chunk_by_heading_oversize_no_paragraphs_splits_by_word_count():
    markdown = "# H\n" + " ".join(f"w{i}" for i in range(1000))
    chunks = chunking.chunk_by_heading(markdown, max_tokens=200)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c.split()) <= 201  # +1 for the heading token
