"""Storage-agnostic markdown chunker (extracted from chroma_store.py, ADR-039
action item #3 -- shared by both the Chroma and pgvector stores so neither
store depends on the other)."""

from __future__ import annotations

import re

DEFAULT_MAX_TOKENS = 500
_HEADING_RE = re.compile(r"^#{1,6}\s", re.MULTILINE)


def chunk_by_heading(markdown: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> list[str]:
    """Split on markdown headings; fallback-split any oversize chunk at
    paragraph boundaries (approximate whitespace-token count, no tokenizer dep)."""
    if not markdown.strip():
        return []

    positions = [m.start() for m in _HEADING_RE.finditer(markdown)]
    if not positions or positions[0] != 0:
        positions = [0] + positions
    positions.append(len(markdown))

    sections = [
        markdown[positions[i]:positions[i + 1]].strip()
        for i in range(len(positions) - 1)
    ]
    sections = [s for s in sections if s]

    chunks: list[str] = []
    for section in sections:
        chunks.extend(_split_oversize(section, max_tokens))
    return chunks


def _split_oversize(section: str, max_tokens: int) -> list[str]:
    words = section.split()
    if len(words) <= max_tokens:
        return [section]

    paragraphs = [p for p in section.split("\n\n") if p.strip()]
    if len(paragraphs) <= 1:
        # No paragraph breaks to split on — chunk by raw word count.
        return [
            " ".join(words[i:i + max_tokens])
            for i in range(0, len(words), max_tokens)
        ]

    out: list[str] = []
    current: list[str] = []
    current_len = 0
    for p in paragraphs:
        p_len = len(p.split())
        if current and current_len + p_len > max_tokens:
            out.append("\n\n".join(current))
            current, current_len = [], 0
        current.append(p)
        current_len += p_len
    if current:
        out.append("\n\n".join(current))
    return out
