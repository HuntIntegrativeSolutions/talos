"""Owned frontmatter + wikilink parser (no obsidiantools dependency, per
ADR-039 action item #2's scope). Uses python-frontmatter for the YAML
frontmatter split; wikilinks/embeds/tags are parsed with a small hand-rolled
regex set -- Obsidian's own syntax is simple enough not to need a grammar."""

from __future__ import annotations

import dataclasses
import hashlib
import re

import frontmatter

# [[Target]], [[Target|alias]], [[Target#Heading]], [[Target#Heading|alias]],
# and the embed form ![[Target]]. Heading anchors are parsed but discarded --
# links/tags in V0009 are note-to-note, not note-to-heading.
_WIKILINK_RE = re.compile(
    r"(?P<embed>!)?\[\[(?P<target>[^\]|#]+)(?:#[^\]|]*)?(?:\|(?P<alias>[^\]]+))?\]\]"
)

# #tag / #nested/tag -- must not be preceded by a non-space character (so it
# doesn't match mid-word or inside a URL fragment like "example.com/#section").
_TAG_RE = re.compile(r"(?<!\S)#([A-Za-z0-9_/-]+)")

_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")


@dataclasses.dataclass
class LinkRef:
    target: str
    alias: str | None
    link_type: str  # 'wikilink' | 'embed'


@dataclasses.dataclass
class ParsedNote:
    title: str
    frontmatter: dict
    body: str
    raw: str
    content_hash: str
    links: list[LinkRef]
    tags: list[str]


def slugify(name: str) -> str:
    """Normalize a wikilink target or note title into its resolution key.

    Obsidian links resolve by basename regardless of folder, so a target of
    "folder/Note Name" and a bare "Note Name" both slugify to the same key --
    this is also why two notes with the same filename stem in different
    folders collide (see indexer.py's slug collision policy)."""
    name = name.strip()
    if name.lower().endswith(".md"):
        name = name[:-3]
    name = name.split("/")[-1]
    name = re.sub(r"\s+", " ", name).strip().lower()
    return name


def _strip_code(body: str) -> str:
    """Remove fenced and inline code so tag extraction ignores '#' inside
    code (e.g. shell comments, C preprocessor directives)."""
    body = _FENCE_RE.sub("", body)
    body = _INLINE_CODE_RE.sub("", body)
    return body


def _extract_links(body: str) -> list[LinkRef]:
    links = []
    for m in _WIKILINK_RE.finditer(body):
        target = m.group("target").strip()
        if not target:
            continue
        alias = m.group("alias")
        alias = alias.strip() if alias else None
        link_type = "embed" if m.group("embed") else "wikilink"
        links.append(LinkRef(target=target, alias=alias, link_type=link_type))
    return links


def _extract_tags(body: str, fm_tags) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()

    def _add(tag: str) -> None:
        tag = tag.strip().lstrip("#")
        if tag and tag not in seen:
            seen.add(tag)
            tags.append(tag)

    for m in _TAG_RE.finditer(_strip_code(body)):
        _add(m.group(1))

    if isinstance(fm_tags, (list, tuple)):
        for t in fm_tags:
            _add(str(t))
    elif isinstance(fm_tags, str):
        _add(fm_tags)

    return tags


def parse_note(path) -> ParsedNote:
    """Parse one markdown file into a ParsedNote. `path` is a pathlib.Path."""
    raw = path.read_text(encoding="utf-8")
    post = frontmatter.loads(raw)
    fm = post.metadata if isinstance(post.metadata, dict) else {}
    body = post.content

    title = fm.get("title") or path.stem

    return ParsedNote(
        title=str(title),
        frontmatter=fm,
        body=body,
        raw=raw,
        content_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        # Links, like tags, are extracted with code stripped: a code example
        # containing [[...]] is not a link (chunks still embed the full body).
        links=_extract_links(_strip_code(body)),
        tags=_extract_tags(body, fm.get("tags")),
    )


def parse_note_safe(path) -> ParsedNote | None:
    """parse_note, but a malformed file (bad YAML frontmatter, non-UTF-8
    bytes) returns None instead of raising -- the indexer's bulk parse phase
    runs before its per-file transaction guards, so without this a single
    bad file would abort the whole run."""
    try:
        return parse_note(path)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("vault parser: failed to parse %s -- skipping", path)
        return None
