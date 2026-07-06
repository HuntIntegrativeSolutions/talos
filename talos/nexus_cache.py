"""
Board-scoped NEXUS read cache (ADR-035; amended P4a).

Caches NEXUS tool results in Postgres, keyed by (board_id, tool_name,
params_hash), with a per-board TTL from boards.model_config. Only wired into
talos/llm_providers/openai_compat.py's explicit tool loop — the Anthropic
Agent SDK path (talos/llm_providers/anthropic.py) dispatches MCP tool calls
internally via claude_agent_sdk.query(), with no Python interception point,
so it is NOT cached. See ADR-035's P4a amendment for the full rationale.

Cacheable tools: profile == "read", OR (profile == "write" and
write_kind == "offline_artifact") — both are non-live/idempotent per
ADR-026's own classification, and this matches the existing write_grant
semantics in talos.nexus_client.allowed_nexus_tool_names(). The ADR's own
motivating example, full_plc_documentation, is write:offline_artifact, not
read — a strict read-only cache would never speed up the tool it targets.
"""

from __future__ import annotations

import hashlib
import json

from talos.db import board_scope, get_conn

DEFAULT_TTL_SECONDS = 300


def _canonical_params_hash(params: dict) -> str:
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def is_cacheable(tool_name: str, manifest: dict) -> bool:
    for tool in manifest["tools"]:
        if tool["name"] != tool_name:
            continue
        profile = tool["profile"]
        if profile == "read":
            return True
        if profile == "write" and tool.get("write_kind") == "offline_artifact":
            return True
        return False
    return False


def get_ttl_seconds(board_id: str) -> int:
    """
    Reads boards.model_config['nexus_cache_ttl_seconds'] (default 300).
    0 means caching is disabled entirely for this board — always live, no
    read, no write. boards has no RLS (it is the isolation root itself), so
    a plain connection is sufficient.
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT model_config FROM boards WHERE id = %s", (board_id,))
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    model_config = (row[0] if row else None) or {}
    return model_config.get("nexus_cache_ttl_seconds", DEFAULT_TTL_SECONDS)


def get_cached(board_id: str, tool_name: str, params: dict) -> str | None:
    params_hash = _canonical_params_hash(params)
    conn = get_conn()
    try:
        with board_scope(conn, board_id) as cur:
            cur.execute(
                "SELECT result_json FROM nexus_cache "
                "WHERE board_id = %s AND tool_name = %s AND params_hash = %s "
                "AND expires_at > now()",
                (board_id, tool_name, params_hash),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return row["result_json"]


def put_cached(
    board_id: str, tool_name: str, params: dict, result_json: str, ttl_seconds: int
) -> None:
    """Only call after a successful tool result — never cache an error/exception."""
    if ttl_seconds <= 0:
        return
    params_hash = _canonical_params_hash(params)
    conn = get_conn()
    try:
        with board_scope(conn, board_id) as cur:
            cur.execute(
                """
                INSERT INTO nexus_cache
                    (board_id, tool_name, params_hash, result_json, fetched_at, expires_at)
                VALUES (%s, %s, %s, %s, now(), now() + (%s || ' seconds')::interval)
                ON CONFLICT (board_id, tool_name, params_hash) DO UPDATE
                    SET result_json = excluded.result_json,
                        fetched_at  = excluded.fetched_at,
                        expires_at  = excluded.expires_at
                """,
                (board_id, tool_name, params_hash, json.dumps(result_json), ttl_seconds),
            )
    finally:
        conn.close()


def invalidate(board_id: str, tool_name: str) -> int:
    """
    Expires matching rows in place (sets expires_at = now()) rather than
    deleting them — talos_app only holds SELECT/INSERT/UPDATE grants, and
    expiry produces the identical externally-visible effect (get_cached
    filters on expires_at > now()).
    """
    conn = get_conn()
    try:
        with board_scope(conn, board_id) as cur:
            cur.execute(
                "UPDATE nexus_cache SET expires_at = now() "
                "WHERE board_id = %s AND tool_name = %s AND expires_at > now()",
                (board_id, tool_name),
            )
            count = cur.rowcount
    finally:
        conn.close()
    return count
