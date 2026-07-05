"""
NEXUS MCP wiring (P3.5, ADR-038).

Turns capabilities/nexus/manifest.json into Claude Agent SDK-consumable MCP
config: a Streamable HTTP server config and a manifest-filtered allowed_tools
list. This is the ADR-033 minimum-viable enforcement point for P3.5 — an
in-process deny-by-default filter, not the full PreToolUse hook or the Layer 2
gateway proxy (both deferred; see ADR-038's "P3.5 harness scope note").

TALOS_NEXUS_STUB=1 bypasses this module entirely — talos/llm.py's stub branch
returns before any of these helpers are called.
"""

from __future__ import annotations

import json
import os

from talos.validators.capability_manifest import compute_manifest_hash

MANIFEST_PATH = os.path.join(
    os.path.dirname(__file__), "..", "capabilities", "nexus", "manifest.json"
)


def load_nexus_manifest() -> dict:
    with open(MANIFEST_PATH) as f:
        return json.load(f)


def allowed_nexus_tools(manifest: dict, *, write_grant: bool = True) -> list[str]:
    """
    ADR-033-minimum-viable tool-list filter: deny-by-default anything not
    declared in the manifest. Returns SDK-namespaced tool names
    (mcp__nexus__<tool>) for the "nexus" MCP server.

    write_grant=True (the value used everywhere today) allows all manifest
    write:offline_artifact tools unconditionally — there is no per-task
    gate-approved-plan check yet (no such field exists on tasks/SpineState),
    and no live-device write is reachable through any NEXUS tool regardless
    (write tools are offline_artifact/sim_only only, per ADR-026). This is a
    deliberate, documented scope reduction — see ADR-038.
    """
    names = []
    for tool in manifest["tools"]:
        profile = tool["profile"]
        if profile == "read" or (profile == "write" and write_grant):
            names.append(f"mcp__nexus__{tool['name']}")
    return names


def nexus_mcp_server_config(url: str) -> dict:
    """SDK Streamable HTTP MCP server config (claude_agent_sdk.types.McpHttpServerConfig)."""
    return {"type": "http", "url": url}


def manifest_selfcheck(manifest: dict) -> None:
    """
    Recomputes capability.content_hash and compares against the stored value.
    Raises ValueError on mismatch.

    This is the lightweight, disk-file self-consistency check described in
    ADR-038 — NOT the DB-pinned boards.manifest_hash/manifest_json check
    ADR-032/034 describe (deferred; requires a schema migration).
    """
    computed = compute_manifest_hash(manifest)
    stored = manifest["capability"]["content_hash"]
    if computed != stored:
        raise ValueError(
            f"NEXUS manifest content_hash mismatch: computed {computed!r} != "
            f"stored {stored!r} — manifest.json was edited without recomputing "
            f"the hash (see capabilities/nexus/dispositions.md)."
        )
