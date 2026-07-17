#!/usr/bin/env python3
"""
Manual integration probe for the P6 Landing 2 emulator_consistency verifier
(talos.verifiers.emulator.emulator_consistency_verifier).

Runs the SAME fn CI exercises with mocks against a LIVE emulator + LIVE
NEXUS -- this is the one piece of evidence the mocked test suite can't give
(CI has no emulator and no live NEXUS reachable). Requires TALOS_NEXUS_URL to
be set (or defaults to talos.config.TALOS_NEXUS_URL) and TALOS_NEXUS_STUB
unset/not "1".

--host/--slot are validated against talos.config.get_emulators_config()
before any read: they must match an existing config key's host/slot exactly
and that key must have confirmed_emulator=true, mirroring the verifier's own
structural guard (a probe run can't point at a target the config doesn't
already trust).

Usage:
    python scripts/emulator_verify_probe.py --host 10.0.0.11 --slot 0 --plc-id NFK-DRYER-TEST-V2
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def _find_emulator_key(host: str, slot: int) -> str:
    from talos.config import get_emulators_config

    emulators = get_emulators_config()
    for key, cfg in emulators.items():
        if cfg.get("host") == host and cfg.get("slot") == slot:
            if not cfg.get("confirmed_emulator"):
                print(
                    f"error: emulator key {key!r} matches --host/--slot but is not "
                    "confirmed_emulator=true in talos.toml [emulators] -- refusing to probe",
                    file=sys.stderr,
                )
                sys.exit(1)
            return key
    print(
        f"error: no talos.toml [emulators] entry matches host={host!r} slot={slot!r} -- "
        "add one (with confirmed_emulator=true) before probing",
        file=sys.stderr,
    )
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="Emulator IP/hostname, e.g. 10.0.0.11")
    parser.add_argument("--slot", type=int, required=True, help="Processor slot, e.g. 0")
    parser.add_argument("--plc-id", required=True, help="NEXUS plc_id to cross-check against, e.g. NFK-DRYER-TEST-V2")
    args = parser.parse_args()

    if os.environ.get("TALOS_NEXUS_STUB") == "1":
        print("error: unset TALOS_NEXUS_STUB before running a live probe", file=sys.stderr)
        sys.exit(1)

    emulator_key = _find_emulator_key(args.host, args.slot)

    from talos.verifiers.emulator import emulator_consistency_verifier

    rubric_text = json.dumps({"plc_id": args.plc_id, "emulator": emulator_key})
    score, reasoning = emulator_consistency_verifier({}, rubric_text, None)

    print(f"plc_id: {args.plc_id}")
    print(f"emulator: {emulator_key} ({args.host} slot {args.slot})")
    print(f"score: {score}")
    print("reasoning:")
    print(reasoning)

    if score is None:
        sys.exit(1)


if __name__ == "__main__":
    main()
