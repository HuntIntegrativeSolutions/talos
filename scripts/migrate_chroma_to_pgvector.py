#!/usr/bin/env python3
"""
One-shot migration: copy every talos-board-*/talos-rules-* Chroma collection
into the V0009 `chunks` table (ADR-039 action item #3).

Does NOT re-embed -- copies the vectors Chroma already computed and stored,
so migrated rows can never silently drift from what the running system has
been retrieving against. Idempotent: skips any (board_id, source, content_hash)
combination that already exists in `chunks`, so a re-run after a partial
failure only inserts what's missing.

Board-ID recovery is fail-loud, not best-effort. chroma_store._collection_name()
sanitizes board_id through re.sub(r"[^a-zA-Z0-9_-]", "_", board_id) before
building the collection name, so reversing it is lossy. Landing rows under
the wrong board_id in an RLS-scoped table is a tenancy-isolation bug, not a
cosmetic one -- the whole point of `board_id = current_setting('app.board_id')`
is that a wrong value here means one client can read another client's data.
So every recovered board_id is cross-checked against the `boards` table
before any write: a collection name must reverse-map to exactly one existing
board, or the run aborts (dry-run included) listing every ambiguous/
unmatched collection. --board-map/--board-map-file let an operator resolve
those cases explicitly; the mapped board_id is re-checked against `boards`
too before being trusted.

Usage:
    python scripts/migrate_chroma_to_pgvector.py --dry-run
    python scripts/migrate_chroma_to_pgvector.py
    python scripts/migrate_chroma_to_pgvector.py --board acme
    python scripts/migrate_chroma_to_pgvector.py --board-map talos-board-legacy_1=acme
    python scripts/migrate_chroma_to_pgvector.py --board-map-file board_map.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

_DOC_PREFIX = "talos-board-"
_RULE_PREFIX = "talos-rules-"


def _sanitize(board_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", board_id)


def _load_board_map(args) -> dict[str, str]:
    board_map: dict[str, str] = {}
    if args.board_map_file:
        with open(args.board_map_file) as f:
            board_map.update(json.load(f))
    for entry in args.board_map or []:
        key, _, value = entry.partition("=")
        if not value:
            raise SystemExit(f"--board-map entry {entry!r} must be COLLECTION_NAME=BOARD_ID")
        board_map[key] = value
    return board_map


def _existing_board_ids(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM boards")
        return [row[0] for row in cur.fetchall()]


def resolve_collections(collection_names: list[str], existing_board_ids: list[str], board_map: dict[str, str]):
    """Returns (resolved: list[(collection_name, board_id, source)], errors: list[str])."""
    sanitized_to_real = {}
    for real_id in existing_board_ids:
        sanitized_to_real.setdefault(_sanitize(real_id), []).append(real_id)

    resolved = []
    errors = []
    for name in collection_names:
        if name.startswith(_DOC_PREFIX):
            prefix, source = _DOC_PREFIX, "doc"
        elif name.startswith(_RULE_PREFIX):
            prefix, source = _RULE_PREFIX, "rule"
        else:
            errors.append(f"{name!r}: does not match talos-board-*/talos-rules-* naming, skipping")
            continue

        suffix = name[len(prefix):]

        if name in board_map:
            candidate = board_map[name]
            if candidate not in existing_board_ids:
                errors.append(
                    f"{name!r}: --board-map maps to board_id {candidate!r}, "
                    f"which does not exist in boards table"
                )
                continue
            resolved.append((name, candidate, source))
            continue

        matches = sanitized_to_real.get(suffix, [])
        if len(matches) == 1:
            resolved.append((name, matches[0], source))
        elif len(matches) == 0:
            errors.append(
                f"{name!r}: no board in `boards` sanitizes to suffix {suffix!r} -- "
                f"provide --board-map {name}=<board_id>"
            )
        else:
            errors.append(
                f"{name!r}: AMBIGUOUS -- multiple boards ({matches!r}) sanitize to "
                f"suffix {suffix!r} -- provide --board-map {name}=<board_id> to disambiguate"
            )

    return resolved, errors


def migrate_collection(chroma_client, conn, collection_name: str, board_id: str, source: str, dry_run: bool):
    from talos.db import board_scope

    collection = chroma_client.get_collection(name=collection_name)
    got = collection.get(include=["documents", "metadatas", "embeddings"])
    documents = got.get("documents")
    metadatas = got.get("metadatas")
    embeddings = got.get("embeddings")
    documents = [] if documents is None else documents
    metadatas = [] if metadatas is None else metadatas
    embeddings = [] if embeddings is None else embeddings

    count_in = len(documents)
    count_out = 0

    with board_scope(conn, board_id) as cur:
        for document, metadata, embedding in zip(documents, metadatas, embeddings):
            content_hash = hashlib.sha256(document.encode()).hexdigest()
            cur.execute(
                "SELECT 1 FROM chunks WHERE board_id = %s AND source = %s "
                "AND metadata->>'content_hash' = %s",
                (board_id, source, content_hash),
            )
            if cur.fetchone() is not None:
                continue  # already migrated -- idempotent re-run

            if dry_run:
                count_out += 1
                continue

            merged_metadata = {**(metadata or {}), "content_hash": content_hash}
            vector_literal = "[" + ",".join(repr(float(x)) for x in embedding) + "]"
            cur.execute(
                """
                INSERT INTO chunks (board_id, source, chunk_text, embedding, metadata)
                VALUES (%s, %s, %s, %s::vector, %s)
                """,
                (board_id, source, document, vector_literal, json.dumps(merged_metadata)),
            )
            count_out += 1

    return count_in, count_out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--chroma-dir", default=os.environ.get("TALOS_CHROMA_DIR", "./chroma_data"))
    parser.add_argument("--dry-run", action="store_true", help="Report counts only, write nothing")
    parser.add_argument("--board", help="Only migrate collections resolving to this board_id")
    parser.add_argument("--board-map", action="append", help="COLLECTION_NAME=BOARD_ID override, repeatable")
    parser.add_argument("--board-map-file", help="JSON file of {collection_name: board_id} overrides")
    args = parser.parse_args()

    import chromadb
    from talos.db import get_conn

    board_map = _load_board_map(args)

    chroma_client = chromadb.PersistentClient(path=args.chroma_dir)
    collection_names = [c.name for c in chroma_client.list_collections()]

    conn = get_conn()
    try:
        existing_board_ids = _existing_board_ids(conn)
        resolved, errors = resolve_collections(collection_names, existing_board_ids, board_map)

        if errors:
            print("ABORTING -- board_id recovery failed for one or more collections:", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
            return 1

        if args.board:
            resolved = [r for r in resolved if r[1] == args.board]

        if not resolved:
            print("No collections to migrate.")
            return 0

        total_in = total_out = 0
        for collection_name, board_id, source in resolved:
            count_in, count_out = migrate_collection(chroma_client, conn, collection_name, board_id, source, args.dry_run)
            total_in += count_in
            total_out += count_out
            mode = "would migrate" if args.dry_run else "migrated"
            print(f"{collection_name} (board_id={board_id}, source={source}): {mode} {count_out}/{count_in}")

        print(f"\nTOTAL: count_in={total_in} count_out={total_out} "
              f"({'dry-run, no writes performed' if args.dry_run else 'committed'})")
        if total_in != total_out and not args.dry_run:
            print("NOTE: count_in != count_out is expected on a re-run of an already-migrated "
                  "collection (skipped rows are prior migrations, not data loss).")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
