# ADR-038: NEXUS transport is Streamable HTTP, not stdio

**Status:** Accepted
**Date:** 2026-07-05
**Deciders:** Hunt Integrative Solutions LLC

## Context

ADR-027's "v1 topology" section and ADR-033's "v1 topology (stdio, same machine)" section
both describe NEXUS as a local stdio subprocess co-located with the TALOS worker on the
same workstation. The live NEXUS deployment enumerated for RT-14
(`capabilities/nexus/dispositions.md`) is in fact a Streamable HTTP MCP server at
`http://10.0.0.80:8765/mcp`, running on a separate host. This is a deviation from the
documented topology and must be recorded explicitly rather than silently reconciled or
absorbed into an in-place edit — the same discipline ADR-027 already uses for CR-07
(explicit override section, not a silent rewrite of accepted text).

## Decision

Transport for NEXUS MCP is Streamable HTTP: the Claude Agent SDK's native
`http`/streamable-http MCP server config type, pointed at `TALOS_NEXUS_URL` (default
`http://10.0.0.80:8765/mcp`).

### Security posture — precise claims, not a blanket "strengthened"

- **ADR-001's "cannot reach a live processor" invariant is preserved, and it is
  transport-independent.** It holds because no NEXUS tool reaches a live processor in any
  profile — every tool is `read`, `write:offline_artifact`, `write:sim_only`, or an
  excluded fact-SoR writer (ADR-026). That property does not depend on whether the
  connection is a local pipe or a network socket, and moving the host does not make an
  already-absolute property "stronger."
- **Host separation does provide one real, narrower benefit**: it protects NEXUS's process
  and OS integrity from a compromised TALOS orchestrator — no shared filesystem, no shared
  process tree, no ability to signal or ptrace the NEXUS process directly.
- **For the threat this ADR does *not* close — a compromised TALOS orchestrator reaching
  NEXUS's excluded fact-SoR writers (`ingest_l5x`, `tag_annotate`, `nexus_reindex`,
  `promote_raw_addresses_to_tags`, etc.) — HTTP without the ADR-033 Layer 2 gateway proxy is
  no better than, and arguably weaker than, stdio-with-proxy.** The only enforcement in
  place under this ADR is the in-process `allowed_tools` filter (ADR-033 Layer 1,
  minimum-viable form — see P3.5 harness notes below). A compromised orchestrator process
  can bypass that filter simply by issuing its own HTTP requests directly to
  `10.0.0.80:8765/mcp`, since nothing external to the orchestrator process enforces the
  manifest allowlist. This is a known, accepted gap for the P3.5 scope — it closes only
  when the ADR-033 Layer 2 proxy (redesigned for HTTP forwarding, not a stdio pipe wrapper)
  is actually built.
- Stdio remains a valid deployment shape for a future air-gapped, packaged,
  single-workstation install with no network path to a NEXUS host. This ADR does not retire
  stdio as an option — it records that stdio is not what v1 actually runs.

### P3.5 harness scope note

The P3.5 NEXUS-wiring harness implements only the minimum-viable form of ADR-033 Layer 1:
an `allowed_tools` list built from the manifest's declared tool set (deny-by-default for any
tool absent from the manifest). It does not implement:
- A per-task/per-plan write-grant check (no such field exists yet in `tasks` or
  `SpineState`) — all manifest `write:offline_artifact` tools are allowed unconditionally
  rather than gated on a gate-approved plan, since none of NEXUS's `write` tools reach a
  live device (they write only to NEXUS's own regenerable derived-artifact store).
- The full `PolicyViolation`-raising `PreToolUse` hook class (no `task_events` denial
  logging, no safety-critic-chain verification).
- The ADR-033 Layer 2 MCP gateway proxy process (external enforcement boundary).
- The ADR-032/034 DB-pinned `boards.manifest_hash`/`boards.manifest_json` check (requires a
  schema migration). What is implemented instead is a disk-file self-consistency check
  (`talos.nexus_client.manifest_selfcheck`) at worker-process startup only — it recomputes
  `capability.content_hash` from `capabilities/nexus/manifest.json` and fails startup on
  mismatch, but it is not board-scoped and not transactional.

These are deliberate, documented scope reductions for P3.5, not oversights. Closing them is
future work under ADR-032/ADR-033's original designs.

## Consequences

- ADR-027's "v1 topology" section is superseded for transport only; all other ADR-027
  content (SoR ownership, CR-07, Graphiti/NEXUS coexistence) stands unchanged. A one-line
  pointer to this ADR is added there rather than rewriting the section.
- ADR-033's "v1 topology (stdio, same machine)" section describing the Layer 2 proxy as a
  stdio pipe wrapper does not apply to an HTTP transport. A one-line pointer to this ADR is
  added there. The Layer 2 proxy is not redesigned for HTTP in this ADR or in P3.5 — a future
  ADR must do so before Layer 2 is actually built.
- ROADMAP.md's P3.5-Harness bullet is corrected from "stdio subprocess" wording to
  Streamable HTTP.
- `TALOS_NEXUS_STUB=1` is unaffected — the stub bypasses all transport code paths and all of
  the above enforcement code entirely, preserving existing CI behavior.
