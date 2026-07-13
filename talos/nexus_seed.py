"""NEXUS entity seeding via known-fact injection (ADR-039 action item #4).

seed_entities() reads a board's entity inventory (controllers, programs,
routines, tags) and upserts pointer rows into the V0010 `entities` table --
mechanical known-fact injection, no LLM calls, no judgment. Per ADR-027,
NEXUS remains system-of-record for PLC facts: an entities row is identity +
external_ref only, never a copy of NEXUS domain knowledge (no description,
logic, or value columns).

Stub gate matches talos.crystallize's idiom exactly: TALOS_NEXUS_STUB=1
short-circuits to a canned inventory so seeding is exercisable in CI without
a live NEXUS connection. The live path calls a read-profile NEXUS tool
through talos.nexus_client, gated by the manifest's allowed-tool list.

Staleness, not deletion: an entity present in a prior seed but absent from
the current inventory is marked metadata={'stale': true} rather than
deleted. NEXUS remains system of record; absence in one read is not evidence
of deletion (same stance as ADR-035's nexus_cache staleness handling). A
later seed that sees the entity again clears the flag via the normal upsert.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging

log = logging.getLogger(__name__)

_ENTITY_TYPES = ("controller", "program", "routine", "tag")

# Canned inventory returned under TALOS_NEXUS_STUB=1 so seeding is
# exercisable in CI without a live NEXUS connection.
_STUB_ENTITY_INVENTORY = [
    {"entity_type": "controller", "name": "PLC_WEST_01", "external_ref": "controller:PLC_WEST_01"},
    {"entity_type": "controller", "name": "PLC_EAST_01", "external_ref": "controller:PLC_EAST_01"},
    {"entity_type": "program", "name": "MainProgram", "external_ref": "PLC_WEST_01:MainProgram"},
    {"entity_type": "routine", "name": "PumpControl", "external_ref": "PLC_WEST_01:MainProgram:PumpControl"},
    {"entity_type": "tag", "name": "PIT_01", "external_ref": "PLC_WEST_01:PIT_01"},
    {"entity_type": "tag", "name": "TIT_01", "external_ref": "PLC_WEST_01:TIT_01"},
]


@dataclasses.dataclass
class SeedStats:
    created: int = 0
    updated: int = 0
    stale: int = 0


def _entity_id(board_id: str, entity_type: str, external_ref: str) -> str:
    """Deterministic from (board_id, entity_type, external_ref) so re-seeding
    is idempotent -- same idiom as vault/indexer.py's _note_id."""
    raw = f"{board_id}:{entity_type}:{external_ref}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _fetch_inventory(board_id: str) -> list[dict]:
    import os

    if os.environ.get("TALOS_NEXUS_STUB") == "1":
        return _STUB_ENTITY_INVENTORY

    import asyncio

    from talos.nexus_client import (
        allowed_nexus_tool_names,
        call_nexus_tool_raw,
        load_nexus_manifest,
    )

    manifest = load_nexus_manifest()
    allowed = set(allowed_nexus_tool_names(manifest, write_grant=False))
    tool_name = "list_documented_plcs"
    if tool_name not in allowed:
        raise RuntimeError(
            f"NEXUS tool {tool_name!r} is not in the read-profile allow-list "
            "for entity seeding"
        )

    url = _nexus_url()
    result = asyncio.run(call_nexus_tool_raw(url, tool_name, {"board_id": board_id}))
    return _inventory_from_nexus_result(result)


def _nexus_url() -> str:
    import os

    url = os.environ.get("TALOS_NEXUS_URL")
    if not url:
        raise RuntimeError("TALOS_NEXUS_URL is required for live NEXUS entity seeding")
    return url


def _inventory_from_nexus_result(result) -> list[dict]:
    """Translate a raw NEXUS tool-call result into the seed_entities() input
    shape. Left intentionally thin -- the exact NEXUS response schema for
    list_documented_plcs is not fixed by this change; live seeding is wired
    but not exercised by CI (TALOS_NEXUS_STUB=1 is always set in tests)."""
    raw = getattr(result, "content", result)
    if isinstance(raw, (str, bytes)):
        raw = json.loads(raw)
    return raw if isinstance(raw, list) else []


def seed_entities(board_id: str) -> SeedStats:
    from talos.db import board_scope, get_conn

    inventory = _fetch_inventory(board_id)
    stats = SeedStats()

    conn = get_conn()
    try:
        with board_scope(conn, board_id) as cur:
            cur.execute(
                "SELECT entity_type, external_ref FROM entities WHERE board_id = %s",
                (board_id,),
            )
            prior = {(r["entity_type"], r["external_ref"]) for r in cur.fetchall()}

            seen = set()
            for entry in inventory:
                entity_type = entry.get("entity_type")
                external_ref = entry.get("external_ref")
                name = entry.get("name")
                if entity_type not in _ENTITY_TYPES or not external_ref or not name:
                    log.warning("nexus_seed: dropping malformed entity entry: %r", entry)
                    continue

                seen.add((entity_type, external_ref))
                entity_id = _entity_id(board_id, entity_type, external_ref)
                metadata = entry.get("metadata") or {}
                is_new = (entity_type, external_ref) not in prior

                cur.execute(
                    """
                    INSERT INTO entities (id, board_id, entity_type, name, external_ref, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (board_id, entity_type, external_ref) DO UPDATE SET
                        name = EXCLUDED.name,
                        metadata = EXCLUDED.metadata
                    """,
                    (entity_id, board_id, entity_type, name, external_ref, json.dumps(metadata)),
                )
                if is_new:
                    stats.created += 1
                else:
                    stats.updated += 1

            stale = prior - seen
            for entity_type, external_ref in stale:
                cur.execute(
                    """
                    UPDATE entities SET metadata = metadata || '{"stale": true}'::jsonb
                    WHERE board_id = %s AND entity_type = %s AND external_ref = %s
                    """,
                    (board_id, entity_type, external_ref),
                )
                stats.stale += 1
    finally:
        conn.close()

    return stats
