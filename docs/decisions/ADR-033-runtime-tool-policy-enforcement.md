# ADR-033: Runtime tool-policy enforcement — PreToolUse hook + MCP gateway proxy

**Status:** Accepted
**Date:** 2026-06-17
**Deciders:** Hunt Integrative Solutions LLC

## Context

ADR-009 defines a layered tool policy (intersection-only, restrict-never-expand). It specifies
an 8th policy layer: invocation-time enforcement against the pinned capability manifest. Today,
the manifest validator (`talos/validators/capability_manifest.py`) runs at parse time only.
Nothing denies an undeclared tool call at runtime. This ADR specifies the runtime enforcement
mechanism.

ADR-001 establishes: "MCP boundary = security boundary. The orchestrator can be fully
compromised and still cannot reach a live processor." This claim requires a hard enforcement
point that survives orchestrator compromise.

## Decision

Runtime tool-policy enforcement is **defense-in-depth**: two independent enforcement points.

### Layer 1 — PreToolUse hook (in-process, fast path)

A `PreToolUse` hook is registered in the LangGraph spine (ADR-029 notes the hook point for
NEXUS tool spans). Before any MCP tool call executes:

1. The hook checks the tool name against the board's pinned manifest (`boards.manifest_json`).
2. If the tool is not declared in the manifest → raise `PolicyViolation`; the node catches it
   and routes to a gate escalation event. The task transitions to `blocked`.
3. If the tool is declared as `write` but no gate-approved plan covers this tool call →
   raise `PolicyViolation`.
4. If the tool is declared as `safety: true` → verify the active critic chain includes the
   required safety critic; if not → raise `PolicyViolation`.

The hook has access to `SpineState` (board_id, task_id, current plan approval status).

### Layer 2 — MCP gateway proxy (external boundary, defense against compromised orchestrator)

A thin proxy process sits between the TALOS worker and the NEXUS MCP server:

```
worker ──[internal]──▶ proxy ──[stdio]──▶ NEXUS MCP server
```

The proxy holds its own copy of the pinned manifest (loaded from Postgres at startup).
Before forwarding any tool call to NEXUS:

1. Check tool name against the manifest allowlist.
2. If not in allowlist → return an MCP error response to the worker; never forward to NEXUS.
3. Log the denied call as a security event.

The proxy is a separate OS process. If the TALOS worker process is compromised (prompt injection
causes arbitrary code execution), the proxy still holds the manifest allowlist and cannot be
instructed to bypass it without modifying the proxy binary.

### v1 topology (stdio, same machine)

For the v1 on-prem stdio topology, the proxy is a lightweight Python process launched alongside
the NEXUS MCP subprocess. It wraps the stdio pipe:

```
worker → proxy (stdin/stdout) → nexus-mcp-server (stdin/stdout)
```

This adds ~1ms of latency per tool call. Acceptable for v1.

## Options considered

- **A — PreToolUse hook only.** Fast and simple, but hook runs in the same process as the
  orchestrator. A successful prompt injection that achieves code execution in the worker process
  could disable the hook. Rejected as sole mechanism.
- **B — MCP gateway proxy only.** Separates enforcement from the orchestrator process, but
  loses fast in-process rejection and the SpineState context needed to check plan-approval
  status. Rejected as sole mechanism.
- **C — Both (chosen).** Hook provides fast rejection with rich context; proxy provides the
  hard external boundary that survives orchestrator compromise. Defense-in-depth.

## Consequences

- The PreToolUse hook is the ADR-029 action item #3 location (NEXUS tool spans). Hook
  registration combines tool-policy enforcement with span emission.
- The proxy adds one new process to the v1 workstation deployment. It must be started
  alongside the NEXUS MCP server.
- `TALOS_NEXUS_STUB=1` bypasses the proxy and hook for CI (existing stub behavior).
- Policy violations are recorded as `task_events` entries with `event_type = 'policy_violation'`
  (ADR-022 observability).
