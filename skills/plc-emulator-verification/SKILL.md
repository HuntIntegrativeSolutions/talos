---
name: plc-emulator-verification
description: Operator recipe for the P6 emulator_consistency verifier — how to add a FactoryTalk Logix Echo emulator target to talos.toml, opt a task into a live emulator-vs-NEXUS consistency check, and read the resulting task_gate_results evidence row. Use when a task needs to verify that NEXUS's documented PLC knowledge matches a live (emulator) controller, or when troubleshooting why an emulator_consistency row shows warn/score=None.
---

# PLC Emulator Verification

Operator recipe for `talos.verifiers.emulator`'s `emulator_consistency` verifier (P6 Landing
2). Full design background and the score formula are in
`docs/plc-emulator-verification.md` — this skill is the short "how do I actually use it"
path.

## 1. Register an emulator target

Only targets listed in `talos.toml`'s `[emulators]` section (or the shipped default,
`dryer_echo`) can ever be connected to — a task can never supply a raw host/slot itself.

```toml
[emulators.my_new_emulator]
host = "10.0.0.X"
slot = 0
confirmed_emulator = true   # required -- the verifier refuses any target without this
connect_timeout_s = 3
read_timeout_s = 10
```

`confirmed_emulator = true` is a deliberate manual step: it exists so nobody can point this
verifier at a production PLC by editing a task body alone. Only edit `talos.toml` for a
target you've personally confirmed is an emulator (Logix Echo or equivalent), never a live
processor.

## 2. Opt a task into the check

Add a rubric marker to the task body — this is JSON config, not prose, and the field name is
`emulator_consistency`:

```
<!-- talos:rubric:emulator_consistency
{"plc_id": "<NEXUS plc_id>", "emulator": "<key from [emulators]>"}
-->
```

`plc_id` is the NEXUS-side identifier to cross-check against (e.g. `NFK-DRYER-TEST-V2`).
`emulator` is the config key from step 1 (e.g. `dryer_echo`) — never a host/address.

The verifier runs automatically once the task's deliverable is generated
(`talos.graph.spine.deliverable_node`), no LLM call involved. If the marker is absent, the
verifier is skipped entirely at zero cost — most tasks never trigger it.

## 3. Read the gate evidence

At the human gate, look for a `task_gate_results` row (or the equivalent gate-UI entry) with
`critic_name = 'emulator_consistency'`. Its `details` JSON carries:

- `score` — 0.0–1.0, or `null` if the verifier refused/couldn't complete (see the failure
  table in the doc).
- `reasoning` — a human-readable summary: the score breakdown, counts + example names of
  missing/mismatched programs and tags, and a note if any NEXUS shard couldn't be fully swept.
- `verdict` — `pass` (score ≥ 0.95), `warn` (below threshold, or a refusal) — never a blocking
  `fail` in this landing (the verifier is informational/advisory).

A `warn` with `score=null` and a short reason (e.g. `"unknown emulator key..."` or
`"emulator/NEXUS read failed: ..."`) means the check couldn't run at all — check the reason
text first; it names the exact failure (bad marker JSON, unconfirmed/unknown emulator key,
unreachable target, or a timeout).

## 4. Manually probe a live target

To run the same check outside of a task, against a real emulator + real NEXUS (not the
`TALOS_NEXUS_STUB=1` mocks CI uses):

```bash
TALOS_NEXUS_URL="http://<nexus-host>:8765/mcp" \
  python scripts/emulator_verify_probe.py --host 10.0.0.11 --slot 0 --plc-id NFK-DRYER-TEST-V2
```

`--host`/`--slot` must match an existing `[emulators]` entry's `host`/`slot` exactly (and
that entry must be `confirmed_emulator = true`) — the probe script enforces the same
allow-list guard the verifier itself does.
