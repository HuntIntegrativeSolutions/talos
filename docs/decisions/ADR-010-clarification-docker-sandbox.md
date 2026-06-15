# ADR-010 Clarification: Docker sandbox containment model and hard-requirement status

**Status:** Accepted (clarification record)
**Date:** 2026-06-14
**Deciders:** Hunt Integrative Solutions LLC
**Amends:** ADR-010 (Worker isolation — session keys + restrict-only config inheritance)

## Context

ADR-010 specifies that workers run in a Docker FS sandbox with `network:none` and `readOnlyRoot`.
P3c is the phase that implements this sandbox. Two questions needed clarification before P3c:

1. **Containment scope:** The TALOS worker process must reach Postgres, NEXUS MCP, and hosted
   model APIs (ADR-017). These cannot coexist in a single `network:none` container. ADR-010 did
   not specify which process layer receives the sandbox.

2. **Hard requirement vs. soft requirement:** Whether Docker is mandatory for all deployments,
   or whether a sandboxless mode is permitted.

## Clarifications

### Containment scope

`network:none` and `readOnlyRoot` apply to the **untrusted code execution subprocess** only —
not the TALOS worker process itself. The containment model has two levels:

```
[TALOS worker process]       — has network (Postgres, NEXUS MCP, model APIs)
    └── [Docker subprocess]  — network:none, readOnlyRoot
         └── [agent-generated code runs here]
```

The worker process is not sandboxed at the network level. It spawns a Docker subprocess for each
execution of agent-generated code (scripts, generated ladder logic, simulation code). The
subprocess has no network and a read-only filesystem; it receives its inputs via stdin/volume
mount and returns outputs via stdout/mounted output path.

This seam must be explicit in P3c's implementation: the Docker client call is in the worker, not
around it.

### Hard requirement vs. soft requirement

Docker is a **soft requirement** with a `TALOS_SANDBOX_MODE=none` escape hatch.

When `TALOS_SANDBOX_MODE=none` is set:
- TALOS starts and runs without spawning any Docker subprocess.
- A **CRITICAL-level warning is written to a durable log file** (not stderr only) at startup.
  The warning message must include: "TALOS_SANDBOX_MODE=none is set. Agent-generated code
  executes in the worker process without filesystem or network isolation. This is a security
  risk. Do not use in production."
- The CRITICAL log entry must be **durable** — written to a file (e.g., `talos.log` or a
  configured log path) that persists beyond process startup. Writing only to stderr is
  insufficient because stderr is not captured in long-running deployment environments.
- No concrete deployment scenario currently requires this mode. It exists for flexibility only.
  **Production use is not recommended and the operator assumes full responsibility for reduced
  isolation.**

CI already requires Docker (testcontainers). This precedent supports Docker as the normal
requirement; the escape hatch is for environments where Docker is genuinely unavailable.

### No standalone `TALOS_SANDBOX_MODE=warn` or similar gradations

There is no intermediate mode. Either the sandbox runs (normal) or it doesn't
(`TALOS_SANDBOX_MODE=none`). Adding gradations increases complexity without increasing safety.

## What this closes

- Defines the exact process layer that receives Docker sandboxing (P3c implementation target).
- Documents the soft-requirement decision and its security implications.
- Specifies that the CRITICAL warning must be file-durable, not stderr-only.

## Action items

1. [ ] P3c: Docker subprocess wraps agent-generated code execution only; worker has network.
2. [ ] P3c: Check `TALOS_SANDBOX_MODE` at startup; log CRITICAL to file if `none`.
3. [ ] P3c: Document in operator guide that `TALOS_SANDBOX_MODE=none` is not for production.
