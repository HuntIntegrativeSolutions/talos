# Contract — `capability-manifest`

> **What this is:** the frozen declaration any capability pack must publish to attach behind the MCP
> boundary — NEXUS first, future packs next. It pins identity/version, the per-tool read/write/safety
> profile, declared scopes, the resumable-cursor protocol, and the finding-status surface, plus how
> the platform enforces every declared limit at invocation time.
> **What this is not:** the pack's internal implementation, a transport choice for the manifest bytes,
> or new architecture. It encodes ADR-004, ADR-009/CR-04, ADR-010, CR-13, CR-16, and CR-18. Genuinely
> unpinned details are escalated under **Open questions**.
>
> **Citation key:** `02 §N` = `docs/integration/02_unified_architecture.md` (`§8 #n` = numbered
> invariants); `ADR-0NN` = `docs/decisions/`; `CR-NN` = `docs/integration/01_conflicts_and_resolutions.md`;
> the NEXUS findings lifecycle quotes `_Tools/CLAUDE.md`.

**Status:** Draft for freeze · **Date:** 2026-06-12 · **Deciders:** Hunt Integrative Solutions LLC
**Seam:** platform ↔ capability (`02 §6`, row 2) · **Freeze order:** before Phase 1 (CR-23, ADR-015)

---

## Purpose

TALOS is the platform; every domain capability (NEXUS ICS analysis, and future packs) attaches
**behind the MCP boundary** as a privileged-but-contained tool provider (ADR-001). For the gate-bound
evaluator to grant tools safely and auto-approve under threshold, "what can this capability do, and
which of its tools write or touch safety" must be a **declared, deterministic property** — never an
LLM judgment (ADR-004, CR-13). The manifest is that declaration: the reviewed contract the platform
pins, hashes, and enforces on every call.

## The two sides it decouples

| Side | Owner | Builds against this contract to… |
| :--- | :--- | :--- |
| **Platform** (enforcer) | TALOS gate-bound evaluator + the 8th tool-policy layer | validate a pack, grant `read`/`write` per gate state, deny any undeclared tool at invocation, read finding status |
| **Capability pack** (declarer) | NEXUS `mcp_server.py`; future packs | publish identity/version, per-tool profiles, scopes, resumable cursor, sim-targets, and finding status |

A pack author can build a conformant manifest without reading TALOS internals; the platform can
enforce it without reading the pack's source. The manifest is the only shared artifact.

## Interface — the manifest

A capability publishes a single manifest document. The **shape and required fields are frozen [D]**;
its serialization/transport (a JSON resource over MCP, a signed sidecar file, etc.) is **[I] / Open**.
Field semantics are anchored to ADR-004 / CR-04 / CR-13 / CR-16 / CR-18.

```json
{
  "manifest_version": "1.0",                  // [D] this contract's schema version
  "capability": {
    "name": "nexus", "version": "2.4.0",      // [D] pack identity + semver
    "content_hash": "sha256:…"                 // [D] hash of the pinned manifest body (CR-04)
  },
  "tools": [
    { "name": "tag_context",       "profile": "read",  "safety": false },
    { "name": "rung_search",       "profile": "read",  "safety": false },
    { "name": "address_trace_chain","profile": "read", "safety": false },
    { "name": "generate_io_package","profile": "write", "safety": false,
      "write_kind": "offline_artifact" },                          // [D] offline/sim only
    { "name": "plc_test_bridge",   "profile": "write", "safety": true,
      "write_kind": "sim_only",
      "sim_target": { "kind": "emulator",
                      "verify_critic": "target-ip-is-emulator" } } // [D] CR-16
  ],
  "scopes": { "default": "nexus:read",
              "grantable": ["nexus:read", "nexus:write"],
              "domain_restrictable": true },                       // [D] e.g. UNIT_* (ADR-010)
  "resumable_cursor": {                                            // [D] required (CR-23/ADR-010)
    "supported": true, "token_field": "cursor",
    "progress_shape": { "stage": "string", "done": "int", "total": "int" }
  },
  "findings": {                                                    // [D] CR-18
    "exposes_status": true,
    "states": ["queued", "proposed", "confirmed", "dismissed"],
    "citable_states": ["confirmed"]
  }
}
```

**Field semantics (all [D]):**

- **`profile` ∈ `read | write`.** A task gets `read` by default; `write` requires a **gate-approved
  plan** (ADR-004). There is no third profile for live action — see Forbidden ops.
- **`write_kind`.** `write` for a capability fronting industrial equipment means **offline artifacts
  and simulation only** — generated ladder, HMI screens, OpenPLC/Emulate sandbox. It **never** means
  download, online edit, mode change, or tag write to a live device (ADR-004). `sim_only` writes
  additionally require a `sim_target`.
- **`safety` (boolean).** Declared, deterministic "safety-touching" flag. The evaluator hard-stops on
  any `safety:true` tool; safety critics are escalate-only (ADR-011, `02 §8 #5`).
- **`sim_target` + `verify_critic`.** For any `sim_only` tool, the manifest names the emulator target;
  a deterministic critic (`target-ip-is-emulator`) must verify the target IP is the emulator, not a
  real controller, and the bridge must be network-isolated from any live processor (CR-16). Iteration
  cap: "any offline/sim write ≤1", "no auto-retry on anything live" (ADR-006).
- **`scopes`.** Declared grantable scopes; intersect-only with session/role policy — a child worker
  may only *further* restrict (e.g. `nexus:read` on `UNIT_*`), never expand (ADR-009, ADR-010).
- **`resumable_cursor`.** A progress-state + resume-token protocol so a worker re-claimed from its
  checkpoint resumes mid-operation rather than restarting (CR-23, ADR-010, `02 §7`). Required for
  Phase 1.
- **`findings`.** The pack exposes its finding lifecycle status over MCP. The states are the NEXUS
  lifecycle `queued → proposed → confirmed | dismissed` (`_Tools/CLAUDE.md`). **This is the surface
  [`nexus-federation.md`](./nexus-federation.md) consumes** — defined here, referenced there, not
  duplicated.

## Invariants & forbidden operations

1. **Read by default; write requires a gate-approved plan; unknown ⇒ write (fail-closed).** — anchor
   **ADR-004** + **02 §8 #2**; CR-13. *Forbidden:* the platform granting a `write` tool without a
   gate-approved plan; treating an **unprofiled or unknown** capability/tool as `read` — it must be
   treated as `write` and stopped at the gate.

2. **No live-device action exists in any profile.** — anchor **02 §8 #1** ("no live writes, ever … not
   in any agent's reach at all") + **ADR-001** (the MCP boundary is the security boundary). *Forbidden:*
   a manifest declaring a tool that performs live download, online edit, mode change, or live tag write
   under any `profile`/`write_kind`. Such operations have **no tool at all**; a human performs them by
   hand from the proposal. A pack that declares one is non-conformant and must be rejected at
   validation.

3. **The pinned manifest is enforced at invocation by the 8th policy layer.** — anchor **ADR-009** +
   **02 §8 #6**; CR-04. *Forbidden:* invoking any tool the **pinned** manifest did not declare, even if
   the session/global policy would allow it. The manifest is the reviewed contract; the live tool set
   must match it.

4. **A capability may capture/propose findings but never confirm them.** — anchor **02 §8 #4** ("NEXUS
   is propose-only at its edge"); CR-18 + `_Tools/CLAUDE.md` ("LLMs may only capture and propose; never
   write `status='confirmed'`"; "No MCP tool or HTTP endpoint may write `status='confirmed'`").
   *Forbidden:* a manifest exposing a tool that writes `status='confirmed'`. Confirmation is reserved
   for `ratify-human` / `ratify-critic` paths outside the agent's reach.

5. **Capability attachment/expansion is itself gated.** — anchor **02 §8 #6**; CR-04, ADR-005. A new
   pack, or a manifest edit, is a capability expansion and must pass the propose→review→pin gate before
   it can claim tasks; promotion to `[shared]` rides the one promotion gate (ADR-005). *Forbidden:*
   self-advancing a capability across the gate.

## Versioning rule

- The pack carries an explicit **semver** (`capability.version`) and a **`content_hash`** of the pinned
  manifest body (content-addressed, CR-04 / ADR-009).
- **Any post-pin edit to the manifest body changes the hash, which auto-reverts the capability to
  `proposed` and re-enters the gate** — the same content-addressed revert applied to skills/widgets
  (CR-04). The injected/live tool set must always match the pinned hash.
- `manifest_version` (this contract's schema) evolves additively: new optional fields may be added;
  the meaning of `profile`/`write_kind`/`safety`/the finding states never changes within a major.
- A pack declares the lowest `manifest_version` it conforms to; the platform refuses a pack whose
  `manifest_version` it cannot enforce.

## Open questions for a human

1. **CR-16 — Rockwell emulation test path** is **NEEDS-HUMAN-DECISION**: Logix Echo SDK (licensed,
   full UDT + download) vs BOOL-forcing (fragile) vs auditable ACD modification — and the Logix Echo
   SDK **licensing cost**. The *safety envelope* is frozen above (`sim_only` + network isolation +
   `target-ip-is-emulator` critic); only the **path and licensing** are escalated (ADR-004 action item
   4; CR-16).
2. **Manifest serialization/transport** — a JSON resource served over MCP vs a signed sidecar file vs
   an MCP capability-descriptor extension. Shape is frozen; bytes-on-the-wire is open.
3. **Pack validation gate** — the exact checks TALOS runs before a pack may claim tasks (manifest
   schema-validity, hash signature, no live-write tool, every `sim_only` has a `sim_target`); ROADMAP
   Phase 2 §279 names this as a deliverable but the check list is not enumerated in any ADR.
4. **Signing/trust root** — whether `content_hash` must be cryptographically signed and by whom (the
   `talos audit` "unsigned skills" check, ROADMAP §168, implies signing but no ADR fixes the trust
   root).
