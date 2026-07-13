#!/usr/bin/env python3
"""TALOS command-line entry point.

    talos vault index --board <id> --path <dir>              # one-shot
    talos vault index --board <id> --path <dir> --rebuild     # wipe + re-walk
    talos vault index --board <id> --path <dir> --watch       # keep running

ADR-039 action item #2. Mirrors scripts/migrate_chroma_to_pgvector.py's
argparse + main() -> int idiom -- the only prior CLI-shaped code in the repo.
"""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path


def _print_stats(stats) -> None:
    print(
        f"notes: created={stats.notes_created} updated={stats.notes_updated} "
        f"deleted={stats.notes_deleted} unchanged={stats.notes_unchanged}"
    )
    print(f"links: created={stats.links_created} closed={stats.links_closed}")
    print(f"tags: written={stats.tags_written}")
    print(f"chunks: written={stats.chunks_written}")
    print(
        f"entity_links: created={stats.entity_links_created} "
        f"closed={stats.entity_links_closed}"
    )


def _print_seed_stats(stats, board_id: str) -> None:
    print(f"entities: created={stats.created} updated={stats.updated} stale={stats.stale}")
    print(
        "to pick up newly-seeded entities in already-indexed notes, run: "
        f"talos vault index --board {board_id} --path <vault-path> --rematch-entities"
    )


def _watch(board_id: str, vault_path: Path, rebuild: bool, match_entities: bool) -> int:
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        print(
            "error: --watch requires the 'watchdog' package "
            "(pip install 'talos[vault-watch]')",
            file=sys.stderr,
        )
        return 1

    from talos.vault.indexer import index_vault

    lock = threading.Lock()
    state = {"timer": None}

    def _reindex() -> None:
        stats = index_vault(board_id, vault_path, rebuild=False, match_entities=match_entities)
        _print_stats(stats)

    class _Handler(FileSystemEventHandler):
        def on_any_event(self, event) -> None:
            if event.is_directory or not event.src_path.endswith(".md"):
                return
            with lock:
                if state["timer"] is not None:
                    state["timer"].cancel()
                state["timer"] = threading.Timer(0.5, _reindex)
                state["timer"].start()

    _print_stats(index_vault(board_id, vault_path, rebuild=rebuild, match_entities=match_entities))

    observer = Observer()
    observer.schedule(_Handler(), str(vault_path), recursive=True)
    observer.start()
    print(f"watching {vault_path} for board {board_id} (ctrl-c to stop)")
    try:
        while True:
            observer.join(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
    return 0


def _cmd_vault_index(args: argparse.Namespace) -> int:
    from talos.vault.indexer import index_vault

    vault_path = Path(args.path)
    if not vault_path.is_dir():
        print(f"error: --path {vault_path} is not a directory", file=sys.stderr)
        return 1

    match_entities = not args.no_entities

    if args.watch:
        return _watch(args.board, vault_path, rebuild=args.rebuild, match_entities=match_entities)

    stats = index_vault(
        args.board, vault_path,
        rebuild=args.rebuild,
        match_entities=match_entities,
        rematch_entities=args.rematch_entities,
    )
    _print_stats(stats)
    return 0


def _cmd_entities_seed(args: argparse.Namespace) -> int:
    from talos.nexus_seed import seed_entities

    stats = seed_entities(args.board)
    _print_seed_stats(stats, args.board)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="talos")
    subparsers = parser.add_subparsers(dest="command", required=True)

    vault_parser = subparsers.add_parser("vault", help="Vault indexer commands")
    vault_sub = vault_parser.add_subparsers(dest="vault_command", required=True)

    index_parser = vault_sub.add_parser(
        "index", help="Index a vault directory into notes/links/tags/chunks"
    )
    index_parser.add_argument("--board", required=True, help="board_id to index into")
    index_parser.add_argument("--path", required=True, help="vault directory to walk")
    index_parser.add_argument(
        "--rebuild", action="store_true", help="wipe this board's vault rows and re-walk"
    )
    index_parser.add_argument(
        "--watch", action="store_true", help="keep running, re-indexing on file changes"
    )
    index_parser.add_argument(
        "--no-entities", action="store_true", help="skip NEXUS entity matching"
    )
    index_parser.add_argument(
        "--rematch-entities", action="store_true",
        help="re-run entity matching against hash-unchanged notes too "
        "(run after seeding new entities so already-indexed notes pick them up)",
    )
    index_parser.set_defaults(func=_cmd_vault_index)

    entities_parser = subparsers.add_parser("entities", help="NEXUS entity seeding commands")
    entities_sub = entities_parser.add_subparsers(dest="entities_command", required=True)

    seed_parser = entities_sub.add_parser(
        "seed", help="Seed NEXUS entities (controllers/programs/routines/tags) into Postgres"
    )
    seed_parser.add_argument("--board", required=True, help="board_id to seed into")
    seed_parser.set_defaults(func=_cmd_entities_seed)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
