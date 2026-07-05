"""
talos.nexus_client unit tests (P3.5, ADR-038).
"""

from __future__ import annotations

import copy

import pytest

from talos.nexus_client import (
    allowed_nexus_tools,
    load_nexus_manifest,
    manifest_selfcheck,
    nexus_mcp_server_config,
)


def test_load_nexus_manifest_matches_disk():
    manifest = load_nexus_manifest()
    assert manifest["manifest_version"] == "1.0"
    assert manifest["capability"]["name"] == "nexus"


def test_allowed_nexus_tools_denies_by_default_and_namespaces():
    manifest = {
        "tools": [
            {"name": "some_read_tool", "profile": "read", "safety": False},
            {"name": "some_write_tool", "profile": "write", "write_kind": "offline_artifact", "safety": False},
        ]
    }
    names = allowed_nexus_tools(manifest)
    assert names == ["mcp__nexus__some_read_tool", "mcp__nexus__some_write_tool"]


def test_allowed_nexus_tools_write_grant_false_excludes_write_tools():
    manifest = {
        "tools": [
            {"name": "some_read_tool", "profile": "read", "safety": False},
            {"name": "some_write_tool", "profile": "write", "write_kind": "offline_artifact", "safety": False},
        ]
    }
    names = allowed_nexus_tools(manifest, write_grant=False)
    assert names == ["mcp__nexus__some_read_tool"]


def test_nexus_mcp_server_config_shape():
    cfg = nexus_mcp_server_config("http://10.0.0.80:8765/mcp")
    assert cfg == {"type": "http", "url": "http://10.0.0.80:8765/mcp"}


def test_manifest_selfcheck_passes_on_real_manifest():
    manifest_selfcheck(load_nexus_manifest())  # must not raise


def test_manifest_selfcheck_raises_on_tampered_manifest():
    manifest = copy.deepcopy(load_nexus_manifest())
    manifest["tools"].append({"name": "not_really_in_the_pinned_manifest", "profile": "read", "safety": False})
    with pytest.raises(ValueError, match="content_hash mismatch"):
        manifest_selfcheck(manifest)
