"""
RT-14 — v1 NEXUS capability manifest tests.

Proves capabilities/nexus/manifest.json passes the generic validator and that no
NEXUS system-of-record writer is ever exposed through it (docs/decisions/ADR-026).
"""
from __future__ import annotations

import fnmatch
import json
import os

from talos.validators.capability_manifest import validate_manifest

MANIFEST_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "capabilities", "nexus", "manifest.json"
)

# Every NEXUS tool that mutates NEXUS's own system-of-record (tags, tag descriptions,
# the rung-pattern library, or ingestion tables). Must never appear in a NEXUS manifest.
NEXUS_SOR_WRITE_DENYLIST = [
    "tag_annotate",
    "backfill_symbol_file_sources",
    "backfill_engineer_verified_sources",
    "reconcile_descriptions",
    "add_rung_pattern",
    "ingest_factorytalk_csv",
    "ingest_quickdesigner_bindings",
    "ingest_ftview_displays",
    "ingest_ftview_project_folder",
    "nexus_reindex",
    "ingest_l5x",
    "promote_raw_addresses_to_tags",
    "onboard_plc",
    "ingest_quickdesigner_descriptions",
    "ingest_vision_descriptions",
    "ingest_rung_comments",
    "tag_diff",
    "ignition_resolve_all",
    "ingest_*",
    "backfill_*",
]


def _load_manifest() -> dict:
    with open(MANIFEST_PATH) as f:
        return json.load(f)


def _denylisted_tools(tool_names: list[str]) -> list[str]:
    hits = []
    for name in tool_names:
        for pattern in NEXUS_SOR_WRITE_DENYLIST:
            if fnmatch.fnmatch(name, pattern):
                hits.append(name)
                break
    return hits


def test_manifest_passes_validator() -> None:
    manifest = _load_manifest()
    result = validate_manifest(manifest)
    assert result.ok, result.errors


def test_manifest_excludes_all_sor_writers() -> None:
    manifest = _load_manifest()
    tool_names = [t["name"] for t in manifest["tools"]]
    assert _denylisted_tools(tool_names) == []


def test_denylist_check_catches_a_sor_writer_when_present() -> None:
    manifest = _load_manifest()
    synthetic = {
        **manifest,
        "tools": manifest["tools"] + [{"name": "tag_annotate", "profile": "read", "safety": False}],
    }
    tool_names = [t["name"] for t in synthetic["tools"]]
    assert "tag_annotate" in _denylisted_tools(tool_names)
