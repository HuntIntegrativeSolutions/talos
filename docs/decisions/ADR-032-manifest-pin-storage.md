# ADR-032: Capability manifest pin storage and verification

**Status:** Accepted
**Date:** 2026-06-17
**Deciders:** Hunt Integrative Solutions LLC

## Context

`docs/contracts/capability-manifest.md` (ADR-026) requires content-addressed pinning: a
capability manifest, once pinned, is immutable — any edit reverts the manifest to `proposed`
and re-enters the gate (CR-04). Today, the manifest validator
(`talos/validators/capability_manifest.py`) exists but nothing stores or verifies the hash
at runtime. This ADR specifies where the pin is stored and how it is checked.

## Decision

### Storage

The SHA-256 hash of the pinned capability manifest JSON is stored in **`boards.manifest_hash`**
(a new column, `TEXT NOT NULL DEFAULT ''`). A board without a pinned manifest has `manifest_hash = ''`
and operates in development mode (NEXUS stub allowed). A board with a pinned manifest has
`manifest_hash = '<hex>'`.

The manifest JSON itself is stored in **`boards.manifest_json`** (`JSONB`). Together:
`boards.manifest_hash` = `sha256(boards.manifest_json::text)`.

### Verification

At worker startup, before claiming any task:

1. Load `boards.manifest_json` for the target board.
2. Compute SHA-256 of the loaded JSON.
3. Compare against `boards.manifest_hash`.
4. If mismatch: worker refuses to start; emits a `manifest_hash_mismatch` gate event; task
   stays `ready`.

This check runs in the same transaction as the worker claim to prevent TOCTOU races.

### Revert-on-edit (CR-04)

A Postgres trigger on `boards.manifest_json` recomputes the hash on UPDATE. If the hash
changes after the board is in production state (`boards.status = 'active'`), the trigger:
1. Clears `boards.manifest_hash` (sets to `''`).
2. Sets `boards.manifest_status = 'proposed'`.
3. Writes a `manifest_reverted` event to `task_events` (scoped to the board, not a task).

The board returns to draft mode until the new manifest is re-reviewed and re-pinned.

## Options considered

- **A — File on disk with hash in Postgres.** More complex to manage on a workstation (file
  path tracking, permissions). Rejected for v1 on-prem simplicity.
- **B — Embedded in MCP server startup response.** NEXUS MCP server returns its manifest on
  connect; TALOS hashes on first contact. Depends on MCP transport reliability. Rejected:
  the manifest is a TALOS concern, not a NEXUS transport concern.
- **C — Postgres `boards` table (chosen).** The boards table is already the hard isolation
  boundary and the home of board-level config. The manifest lives with the board.

## Consequences

- `engine/schema.sql` gains `manifest_hash TEXT NOT NULL DEFAULT ''` and `manifest_json JSONB`
  on the `boards` table (migration ADR-034).
- Worker startup path in `talos/worker.py` adds a manifest-hash check before task claim.
- The validator (`talos/validators/capability_manifest.py`) is called both at pin time (via the
  gate) and at worker startup (fast sanity check against the stored JSON).
- `TALOS_NEXUS_STUB=1` bypasses manifest verification for CI runs (existing behavior preserved).
