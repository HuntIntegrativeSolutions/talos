# ADR-001: TALOS is a platform; NEXUS is a capability behind MCP

**Status:** Accepted
**Date:** 2026-06-10
**Deciders:** Hunt Integrative Solutions LLC

## Context

NEXUS already exists as an MCP-native PLC analysis platform (a large tool surface, a knowledge
graph, deterministic critics, and a strict "never write to live processors" doctrine). The new
harness needs orchestration, a work-board, polyglot memory, and a business layer. The question is
how the two relate.

## Decision

Build TALOS as the **platform** (the orchestration / operations layer). NEXUS attaches as the
**first and most privileged capability**, behind the MCP boundary. TALOS calls NEXUS; it does not
absorb it.

## Options considered

### Option A: Orchestration layer *of* NEXUS (merge)
| Dimension | Assessment |
|-----------|------------|
| Coupling | High — business/multi-client logic bolted onto a PLC brand |
| Reuse | Low — hard to use the harness for non-PLC work |
| Security | Weaker — broad-access orchestration shares a process with plant tools |

### Option B / C: Platform that calls NEXUS as a capability (chosen)
| Dimension | Assessment |
|-----------|------------|
| Coupling | Low — clean MCP contract already exists |
| Reuse | High — NEXUS is one pack; other domains/clients can attach others |
| Security | Strong — MCP boundary doubles as a trust boundary |

## Trade-off analysis

The decisive factor is security. OpenClaw-style proactivity (channels, cron, exec) is valuable but
carries a large attack surface. Keeping NEXUS behind MCP means the orchestrator can be fully
compromised and still cannot reach a live processor, because NEXUS enforces propose-only at its own
edge. Merging would dissolve a boundary that is free today and is also the safety boundary.

## Consequences

- **Easier:** selling/reusing either piece independently; attaching new domain packs; keeping NEXUS
  focused.
- **Harder:** two systems to run; memory must **federate** (NEXUS's graph stays the system of record
  for PLC knowledge; TALOS owns operational/episodic/business memory on top — never duplicate it).
- **Revisit:** the federation contract between TALOS memory and the NEXUS graph.

## Action items
1. [ ] Define the MCP capability manifest TALOS expects from a domain pack.
2. [ ] Specify the read-through contract from TALOS memory to the NEXUS graph.
