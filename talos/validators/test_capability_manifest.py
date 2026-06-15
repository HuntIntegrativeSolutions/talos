"""Unit tests for capability_manifest.validate_manifest."""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from talos.validators.capability_manifest import validate_manifest


VALID_MANIFEST = {
    "manifest_version": "1.0",
    "capability": {
        "name": "nexus",
        "version": "2.4.0",
        "content_hash": "sha256:abc123",
    },
    "tools": [
        {"name": "tag_context", "profile": "read", "safety": False},
        {
            "name": "generate_io_package",
            "profile": "write",
            "safety": False,
            "write_kind": "offline_artifact",
        },
        {
            "name": "plc_test_bridge",
            "profile": "write",
            "safety": True,
            "write_kind": "sim_only",
            "sim_target": {"kind": "emulator", "verify_critic": "target-ip-is-emulator"},
        },
    ],
    "resumable_cursor": {"supported": True, "token_field": "cursor"},
    "findings": {"exposes_status": True},
}


class TestValidateManifest(unittest.TestCase):

    def test_valid_manifest(self):
        result = validate_manifest(VALID_MANIFEST)
        self.assertTrue(result.ok)
        self.assertEqual(result.errors, [])

    def test_missing_content_hash(self):
        manifest = {
            **VALID_MANIFEST,
            "capability": {
                "name": "nexus",
                "version": "2.4.0",
                # content_hash omitted
            },
        }
        result = validate_manifest(manifest)
        self.assertFalse(result.ok)
        self.assertTrue(any("content_hash" in e for e in result.errors))

    def test_write_tool_missing_write_kind(self):
        manifest = {
            **VALID_MANIFEST,
            "tools": [
                {
                    "name": "generate_io_package",
                    "profile": "write",
                    "safety": False,
                    # write_kind omitted
                }
            ],
        }
        result = validate_manifest(manifest)
        self.assertFalse(result.ok)
        self.assertTrue(any("write_kind" in e for e in result.errors))

    def test_sim_only_tool_missing_verify_critic(self):
        manifest = {
            **VALID_MANIFEST,
            "tools": [
                {
                    "name": "plc_test_bridge",
                    "profile": "write",
                    "safety": True,
                    "write_kind": "sim_only",
                    "sim_target": {"kind": "emulator"},  # verify_critic omitted
                }
            ],
        }
        result = validate_manifest(manifest)
        self.assertFalse(result.ok)
        self.assertTrue(any("verify_critic" in e for e in result.errors))

    def test_unknown_profile_value(self):
        manifest = {
            **VALID_MANIFEST,
            "tools": [
                {"name": "tag_context", "profile": "execute", "safety": False}
            ],
        }
        result = validate_manifest(manifest)
        self.assertFalse(result.ok)
        self.assertTrue(any("profile" in e for e in result.errors))


if __name__ == "__main__":
    unittest.main()
