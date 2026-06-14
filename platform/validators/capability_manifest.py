"""
Capability manifest validator for TALOS.

Accepts a manifest dict and returns a ValidationResult. Pure and deterministic:
no LLM, no network. Run at capability attach-time to reject malformed manifests
before any tool is ever granted (docs/contracts/capability-manifest.md).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field


VALID_PROFILES = {"read", "write"}
VALID_WRITE_KINDS = {"offline_artifact", "sim_only"}


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)


def validate_manifest(manifest: dict) -> ValidationResult:
    errors: list[str] = []

    # manifest_version
    if manifest.get("manifest_version") != "1.0":
        errors.append(
            f"manifest_version must be '1.0', got {manifest.get('manifest_version')!r}"
        )

    # capability block
    cap = manifest.get("capability")
    if not isinstance(cap, dict):
        errors.append("capability must be an object")
    else:
        for field_name in ("name", "version", "content_hash"):
            if not cap.get(field_name):
                errors.append(f"capability.{field_name} is required")

    # tools array
    tools = manifest.get("tools")
    if not isinstance(tools, list):
        errors.append("tools must be an array")
    else:
        for i, tool in enumerate(tools):
            prefix = f"tools[{i}] ({tool.get('name', '<unnamed>')})"

            if not tool.get("name"):
                errors.append(f"{prefix}: name is required")

            profile = tool.get("profile")
            if profile not in VALID_PROFILES:
                errors.append(
                    f"{prefix}: profile must be one of {sorted(VALID_PROFILES)}, got {profile!r}"
                )

            if not isinstance(tool.get("safety"), bool):
                errors.append(f"{prefix}: safety must be a boolean")

            if profile == "write":
                write_kind = tool.get("write_kind")
                if write_kind not in VALID_WRITE_KINDS:
                    errors.append(
                        f"{prefix}: write tools require write_kind in "
                        f"{sorted(VALID_WRITE_KINDS)}, got {write_kind!r}"
                    )
                elif write_kind == "sim_only":
                    sim = tool.get("sim_target")
                    if not isinstance(sim, dict):
                        errors.append(
                            f"{prefix}: sim_only tools require a sim_target object"
                        )
                    else:
                        if not sim.get("kind"):
                            errors.append(f"{prefix}: sim_target.kind is required")
                        if not sim.get("verify_critic"):
                            errors.append(
                                f"{prefix}: sim_target.verify_critic is required"
                            )

    # resumable_cursor (optional; validate if present)
    cursor = manifest.get("resumable_cursor")
    if cursor is not None:
        if not isinstance(cursor, dict):
            errors.append("resumable_cursor must be an object")
        elif "supported" not in cursor:
            errors.append("resumable_cursor.supported is required when resumable_cursor is present")
        elif not isinstance(cursor["supported"], bool):
            errors.append("resumable_cursor.supported must be a boolean")

    # findings (optional; validate if present)
    findings = manifest.get("findings")
    if findings is not None:
        if not isinstance(findings, dict):
            errors.append("findings must be an object")
        elif "exposes_status" not in findings:
            errors.append("findings.exposes_status is required when findings is present")
        elif not isinstance(findings["exposes_status"], bool):
            errors.append("findings.exposes_status must be a boolean")

    return ValidationResult(ok=len(errors) == 0, errors=errors)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m platform.validators.capability_manifest <manifest.json>")
        sys.exit(2)

    path = sys.argv[1]
    try:
        with open(path) as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL\n  cannot load manifest: {exc}")
        sys.exit(1)

    result = validate_manifest(manifest)
    if result.ok:
        print("PASS")
    else:
        print("FAIL")
        for err in result.errors:
            print(f"  - {err}")
        sys.exit(1)
