# ADR-004: Capability tool profiles — read by default, write means offline/sim only

**Status:** Accepted
**Date:** 2026-06-11
**Deciders:** Hunt Integrative Solutions LLC

## Context

Capabilities behind the MCP boundary (NEXUS first among them) expose tools that range from pure reads
to actions that produce artifacts. TALOS needs a principled rule for which tools a task may call, and
an absolute floor on what "write" can ever mean for a capability that fronts live industrial
equipment. Without it, an orchestrator bug or a prompt injection could escalate from analysis to a
live-device action — the exact failure the whole design exists to prevent
(`BLUEPRINT.md` §16–18, §125–137).

## Decision

Each capability exposes two tool profiles, `read` and `write`. Tasks get `read` by default; a `write`
scope requires the plan to be gate-approved (`BLUEPRINT.md` §125–126). For NEXUS, `write` means
**offline artifacts and simulation only** — generated ladder, HMI screens, the OpenPLC/Emulate
sandbox. It **never** means download, online edit, mode change, or tag write to a live device; those
operations are not gated — they are *not in any agent's reach at all*. A human performs them by hand
from NEXUS's proposal, optionally with a second at-the-moment confirmation (`BLUEPRINT.md` §127–131).

Whether a capability step is "write-capable" or "safety-touching" is a **declared, deterministic
property** of the capability profile, checked by the gate-bound evaluator — never an LLM judgment —
and an unknown or unprofiled capability is treated as `write` (fail-closed)
(`01_conflicts_and_resolutions.md` CR-13).

## Options considered

- **A — Single undifferentiated tool grant** (every task may call every tool the policy allows).
  Rejected: no separation between analysis and artifact generation; the broadest blast radius by
  default.
- **B — Read by default; write requires gate approval; "write" capped at offline/sim; live actions
  out of reach entirely.** Chosen.

## Trade-off analysis

The decisive property is that the line between "write" and "live action" is **structural, not
policy**: live operations have no tool at all, so a fully compromised orchestrator still cannot reach
a processor — the same logic that makes the MCP edge a security boundary (ADR-001). Declaring
write/safety as a deterministic capability property (CR-13, `capability-manifest`) keeps the
auto-approve-under-threshold path safe and removes LLM judgment from the safety classification. The
Emulate 5000 / OpenPLC sandbox sits between "write (offline)" and "human deploys" as the proving
ground (`BLUEPRINT.md` §133–137). Upstream patterns agree on the direction —
`openclaw-notes.md` → "Privileged Tools" (default-deny on `fs_write`/`exec`),
`github-agentic-workflows-notes.md` → "Safe Outputs" (the agent never holds write credentials) — but
BLUEPRINT's "live actions are in no agent's reach" is a stronger floor than either.

## Consequences

- **Easier:** a single default (`read`) covers the vast majority of analysis tasks; the safety floor
  is identical everywhere; auto-approval is safe because write/safety is deterministic.
- **Harder:** every capability must declare `read`/`write` profiles and tag write/safety on each tool;
  the `capability-manifest` contract must carry this.
- **Revisit / open question for human:** the Rockwell emulation **test path** (CR-16) — Logix Echo SDK
  (licensed, full UDT + download) vs BOOL-forcing vs ACD modification — is escalated. The safety
  envelope is *resolved* (the sim target must be network-isolated from any live processor, and a
  deterministic critic must verify the target IP is the emulator); the **path and licensing cost** are
  a human call.

## Action items

1. [ ] Define `read` / `write` tool profiles per capability in the `capability-manifest`.
2. [ ] Tag each tool with declared `write` / `safety` properties; default unknown → `write`
      (fail-closed).
3. [ ] Specify the deterministic "target-IP-is-emulator" critic for offline/sim writes.
4. [ ] Escalate the CR-16 Rockwell test-path / Logix Echo SDK licensing decision to a human.
