"""Vault -> Postgres projection (ADR-039 action item #2).

index_vault() walks a markdown vault and projects it into the V0009
notes/links/tags/chunks tables. The vault is the source of truth; these
tables are a derived, fully-rebuildable index -- like Obsidian's own index.

Connection idiom matches talos/memory/pgvector_store.py exactly: one
board_scope(conn, board_id) transaction per logical unit of work (here, per
file) rather than one giant transaction for the whole vault, so a single bad
file doesn't roll back an otherwise-successful run.

Two deletion behaviors are forced by the schema, not a free design choice
(see the plan this module was built from):
  - A deleted note's OUTGOING links are hard-DELETEd (links.src_note_id is
    NOT NULL REFERENCES notes(id) with no cascade -- the note row cannot be
    removed while they exist).
  - A deleted note's INCOMING links are DOWNGRADED to target_slug rather
    than deleted, since the source files that hold them are unchanged on
    disk and would be skipped by the content-hash short-circuit -- deleting
    those rows would silently go stale. Both open and historically-closed
    incoming rows must be downgraded (the FK doesn't distinguish live vs.
    closed rows), but only rows that were live get a fresh replacement row
    inserted, preserving "never UPDATE a target in place" for current state.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from talos.config import get_resources_config
from talos.memory.chunking import chunk_by_heading
from talos.vault.parser import ParsedNote, parse_note_safe, slugify

log = logging.getLogger(__name__)


@dataclasses.dataclass
class IndexStats:
    notes_created: int = 0
    notes_updated: int = 0
    notes_deleted: int = 0
    notes_unchanged: int = 0
    links_created: int = 0
    links_closed: int = 0
    tags_written: int = 0
    chunks_written: int = 0


def _note_id(board_id: str, rel_path: str) -> str:
    """Deterministic from (board_id, vault-relative path) -- not absolute
    path -- so moving the vault directory doesn't change note identity and
    rebuilds stay idempotent."""
    return hashlib.sha256(f"{board_id}:{rel_path}".encode("utf-8")).hexdigest()[:32]


def _json_dump(obj) -> str:
    return json.dumps(obj)


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def _walk_vault(vault_path: Path) -> list[Path]:
    files = []
    for p in vault_path.rglob("*.md"):
        rel = p.relative_to(vault_path)
        if any(part.startswith(".") for part in rel.parts[:-1]):
            continue
        files.append(p)
    return files


def _resolve_slug_winners(rel_paths: list[str]) -> dict[str, str]:
    """slug -> canonical rel_path. Two notes in different folders can share a
    filename stem (projects/pump.md and archive/pump.md both slugify to
    "pump"); the lexicographically-first path wins, deterministically, and
    the collision is logged. Deterministic-plus-logged keeps rebuild
    idempotence honest if a fixture ever grows a collision."""
    by_slug: dict[str, list[str]] = {}
    for rp in rel_paths:
        slug = slugify(Path(rp).stem)
        by_slug.setdefault(slug, []).append(rp)

    winners: dict[str, str] = {}
    for slug, paths in by_slug.items():
        paths_sorted = sorted(paths)
        winners[slug] = paths_sorted[0]
        if len(paths_sorted) > 1:
            log.warning(
                "vault indexer: slug collision for %r among %s -- resolving to %r",
                slug, paths_sorted, paths_sorted[0],
            )
    return winners


def index_vault(board_id: str, vault_path: Path, rebuild: bool = False) -> IndexStats:
    from talos.db import board_scope, get_conn

    vault_path = Path(vault_path)
    stats = IndexStats()

    conn = get_conn()
    try:
        if rebuild:
            with board_scope(conn, board_id) as cur:
                cur.execute("DELETE FROM links WHERE board_id = %s", (board_id,))
                cur.execute("DELETE FROM tags WHERE board_id = %s", (board_id,))
                cur.execute(
                    "DELETE FROM chunks WHERE board_id = %s AND source = 'vault'",
                    (board_id,),
                )
                cur.execute("DELETE FROM notes WHERE board_id = %s", (board_id,))

        disk_files = _walk_vault(vault_path)
        rel_paths = [f.relative_to(vault_path).as_posix() for f in disk_files]
        path_to_abs = dict(zip(rel_paths, disk_files))

        slug_winners = _resolve_slug_winners(rel_paths)
        slug_map = {slug: _note_id(board_id, rp) for slug, rp in slug_winners.items()}

        with board_scope(conn, board_id) as cur:
            cur.execute(
                "SELECT id, path, content_hash FROM notes WHERE board_id = %s",
                (board_id,),
            )
            existing = {row["path"]: row for row in cur.fetchall()}

        cpu_workers = max(1, int(get_resources_config()["cpu_workers"]))
        if disk_files:
            with ThreadPoolExecutor(max_workers=cpu_workers) as pool:
                parsed_list = list(pool.map(parse_note_safe, disk_files))
            # None = unparseable file (logged by parse_note_safe): skipped
            # this run, but still ON DISK -- it must count as neither
            # changed nor deleted (a stale projection of its last good
            # parse beats purging it).
            parsed_by_path = {
                rp: p for rp, p in zip(rel_paths, parsed_list) if p is not None
            }
            unparseable = {rp for rp, p in zip(rel_paths, parsed_list) if p is None}
            rel_paths = [rp for rp in rel_paths if rp in parsed_by_path]
        else:
            parsed_by_path = {}
            unparseable = set()

        # Two phases, not one: a file processed first may link to a sibling
        # file processed later in the same run. Phase 1 upserts every
        # changed note's row (id/path/title/frontmatter/hash/mtime) so all
        # of this run's notes exist before phase 2 inserts any links that
        # might target them -- otherwise the earlier file's INSERT INTO
        # links hits links_target_note_id_fkey before the later file's
        # notes row exists.
        to_process = []
        for rel_path in rel_paths:
            parsed = parsed_by_path[rel_path]
            prior = existing.get(rel_path)
            if prior is not None and prior["content_hash"] == parsed.content_hash:
                stats.notes_unchanged += 1
                continue

            is_new = prior is None
            note_id = _note_id(board_id, rel_path)
            mtime = datetime.fromtimestamp(path_to_abs[rel_path].stat().st_mtime, tz=timezone.utc)

            try:
                with board_scope(conn, board_id) as cur:
                    _upsert_note_row(cur, board_id, note_id, rel_path, parsed, mtime)
            except Exception:
                log.exception(
                    "vault indexer: failed to upsert note row for %s (board_id=%s)",
                    rel_path, board_id,
                )
                continue

            to_process.append((rel_path, note_id, parsed, is_new))

        for rel_path, note_id, parsed, is_new in to_process:
            try:
                with board_scope(conn, board_id) as cur:
                    _index_note_relations(
                        cur, board_id, note_id, rel_path, parsed,
                        slug_winners, slug_map, stats,
                    )
            except Exception:
                log.exception(
                    "vault indexer: failed to index relations for %s (board_id=%s)",
                    rel_path, board_id,
                )
                continue

            if is_new:
                stats.notes_created += 1
            else:
                stats.notes_updated += 1

        deleted_paths = set(existing) - set(rel_paths) - unparseable
        for rel_path in deleted_paths:
            try:
                with board_scope(conn, board_id) as cur:
                    _delete_note(cur, board_id, existing[rel_path]["id"], rel_path, stats)
                stats.notes_deleted += 1
            except Exception:
                log.exception(
                    "vault indexer: failed to delete %s (board_id=%s)", rel_path, board_id
                )
                continue
    finally:
        conn.close()

    return stats


def _upsert_note_row(
    cur, board_id: str, note_id: str, rel_path: str, parsed: ParsedNote, mtime
) -> None:
    cur.execute(
        """
        INSERT INTO notes (id, board_id, path, title, frontmatter, content_hash, mtime)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            path = EXCLUDED.path,
            title = EXCLUDED.title,
            frontmatter = EXCLUDED.frontmatter,
            content_hash = EXCLUDED.content_hash,
            mtime = EXCLUDED.mtime
        """,
        (
            note_id, board_id, rel_path, parsed.title,
            _json_dump(parsed.frontmatter), parsed.content_hash, mtime,
        ),
    )


def _index_note_relations(
    cur, board_id: str, note_id: str, rel_path: str, parsed: ParsedNote,
    slug_winners: dict[str, str], slug_map: dict[str, str], stats: IndexStats,
) -> None:
    """Links/tags/chunks for a note whose row already exists (see the
    two-phase split in index_vault: all of this run's notes rows must exist
    before any of them can be linked to, since links.target_note_id's FK
    would otherwise fail for a note that's processed later in the same
    run)."""
    own_slug = slugify(Path(rel_path).stem)
    is_slug_winner = slug_winners.get(own_slug) == rel_path

    # Outgoing links: bi-temporal supersession -- close what's gone, insert
    # what's new, never UPDATE an existing row's target in place.
    cur.execute(
        "SELECT id, target_note_id, target_slug, link_type FROM links "
        "WHERE board_id = %s AND src_note_id = %s AND valid_until IS NULL",
        (board_id, note_id),
    )
    existing_open = cur.fetchall()
    existing_set = {
        (r["target_note_id"], r["target_slug"], r["link_type"]) for r in existing_open
    }

    desired_set = set()
    for link in parsed.links:
        slug = slugify(link.target)
        target_note_id = slug_map.get(slug)
        target_slug = None if target_note_id else slug
        desired_set.add((target_note_id, target_slug, link.link_type))

    for row in existing_open:
        key = (row["target_note_id"], row["target_slug"], row["link_type"])
        if key not in desired_set:
            cur.execute("UPDATE links SET valid_until = now() WHERE id = %s", (row["id"],))
            stats.links_closed += 1

    for target_note_id, target_slug, link_type in desired_set - existing_set:
        cur.execute(
            "INSERT INTO links (board_id, src_note_id, target_note_id, target_slug, link_type) "
            "VALUES (%s, %s, %s, %s, %s)",
            (board_id, note_id, target_note_id, target_slug, link_type),
        )
        stats.links_created += 1

    # Forward-reference resolution: only the canonical slug winner claims
    # pending unresolved links (a collision loser must not steal them).
    if is_slug_winner:
        cur.execute(
            "SELECT id, src_note_id, link_type FROM links "
            "WHERE board_id = %s AND target_slug = %s "
            "AND target_note_id IS NULL AND valid_until IS NULL",
            (board_id, own_slug),
        )
        for row in cur.fetchall():
            cur.execute("UPDATE links SET valid_until = now() WHERE id = %s", (row["id"],))
            cur.execute(
                "INSERT INTO links (board_id, src_note_id, target_note_id, target_slug, link_type) "
                "VALUES (%s, %s, %s, NULL, %s)",
                (board_id, row["src_note_id"], note_id, row["link_type"]),
            )
            stats.links_closed += 1
            stats.links_created += 1

    # Tags: delete-then-insert, same idiom as pgvector_store's chunks upsert.
    cur.execute("DELETE FROM tags WHERE board_id = %s AND note_id = %s", (board_id, note_id))
    for tag in parsed.tags:
        cur.execute(
            "INSERT INTO tags (note_id, board_id, tag) VALUES (%s, %s, %s)",
            (note_id, board_id, tag),
        )
        stats.tags_written += 1

    # Chunks: delete-then-insert, source='vault', note_id populated (the
    # only writer of this nullable column).
    cur.execute(
        "DELETE FROM chunks WHERE board_id = %s AND note_id = %s AND source = 'vault'",
        (board_id, note_id),
    )
    chunks = chunk_by_heading(parsed.body)
    if chunks:
        from talos.memory.embedding import get_embed_fn

        embeddings = get_embed_fn()(chunks)
        for i, (chunk_text, embedding) in enumerate(zip(chunks, embeddings)):
            cur.execute(
                "INSERT INTO chunks (board_id, note_id, source, chunk_text, embedding, metadata) "
                "VALUES (%s, %s, 'vault', %s, %s::vector, %s)",
                (
                    board_id, note_id, chunk_text, _vector_literal(embedding),
                    _json_dump({"note_id": note_id, "chunk_index": i}),
                ),
            )
            stats.chunks_written += 1


def _delete_note(cur, board_id: str, note_id: str, rel_path: str, stats: IndexStats) -> None:
    own_slug = slugify(Path(rel_path).stem)

    # Outgoing links: FK forces a hard delete regardless of validity window.
    cur.execute(
        "DELETE FROM links WHERE board_id = %s AND src_note_id = %s", (board_id, note_id)
    )

    # Incoming links: capture the currently-live ones before downgrading, so
    # they get a fresh unresolved replacement row (current state stays
    # resolvable if the note reappears). ALL rows referencing this note as
    # target -- open or historically closed -- must be downgraded, since the
    # FK doesn't care whether a row is "current"; closed rows just keep
    # their existing valid_until.
    cur.execute(
        "SELECT id, src_note_id, link_type FROM links "
        "WHERE board_id = %s AND target_note_id = %s AND valid_until IS NULL",
        (board_id, note_id),
    )
    open_incoming = cur.fetchall()

    cur.execute(
        "UPDATE links SET target_note_id = NULL, target_slug = %s, "
        "valid_until = COALESCE(valid_until, now()) "
        "WHERE board_id = %s AND target_note_id = %s",
        (own_slug, board_id, note_id),
    )
    stats.links_closed += len(open_incoming)

    for row in open_incoming:
        cur.execute(
            "INSERT INTO links (board_id, src_note_id, target_note_id, target_slug, link_type) "
            "VALUES (%s, %s, NULL, %s, %s)",
            (board_id, row["src_note_id"], own_slug, row["link_type"]),
        )
        stats.links_created += 1

    cur.execute("DELETE FROM tags WHERE board_id = %s AND note_id = %s", (board_id, note_id))
    cur.execute(
        "DELETE FROM chunks WHERE board_id = %s AND note_id = %s AND source = 'vault'",
        (board_id, note_id),
    )
    cur.execute("DELETE FROM notes WHERE board_id = %s AND id = %s", (board_id, note_id))
