# ADR-026: Capability manifest contract — frozen declaration for MCP capability packs

**Status:** Accepted
**Date:** 2026-06-17
**Deciders:** Hunt Integrative Solutions LLC

## Context

`docs/contracts/capability-manifest.md` defines the frozen JSON declaration any capability pack
must publish before attaching behind the MCP boundary. The validator at
`talos/validators/capability_manifest.py` enforces it deterministically. This ADR formalizes the
contract as a binding decision record.

This ADR is a pointer-and-rationale record; the normative text lives in
`docs/contracts/capability-manifest.md`.

## Decision

The capability-manifest contract is accepted as binding. Key invariants:

1. **Read by default; unknown ⇒ write (fail-closed).** If `profile` is absent or unrecognized,
   the tool is treated as `write`.
2. **No live-device action in any profile.** `write` tools are either `offline_artifact` or
   `sim_only`. There is no live-write kind. Live operations are performed by humans by hand.
3. **`safety: true` tools are escalate-only.** They can never be waived; the gate outcome for
   a failing safety critic is always Escalate.
4. **`sim_only` tools require a `sim_target`** with `kind` and `verify_critic`. The critic is
   deterministic; it cannot be LLM-authored.
5. **Manifest is content-addressed.** The SHA-256 hash of the pinned manifest is stored in
   `boards.manifest_hash` (ADR-032) and checked at worker startup. A manifest edit auto-reverts
   to `proposed` (CR-04).
6. **Capability attachment is itself gated.** A new or updated manifest goes through
   propose → critics → human approve → pin before taking effect.

## RT-14 action item (closed 2026-07-05)

NEXUS exposes **90 tools** at v1.26.0 (the earlier "~85" figure was pre-growth). All 90 are
explicitly dispositioned in `capabilities/nexus/dispositions.md`; 18 SoR-writers (`tag_annotate`,
`ingest_*`, `promote_raw_addresses_to_tags`, `reconcile_descriptions`, `nexus_reindex`,
`backfill_*`, and others surfaced during classification — see dispositions.md's "flagged for
human review" section) are excluded from `capabilities/nexus/manifest.json`. The remaining 72
tools are declared `read` (60) or `write`/`offline_artifact` (12). A CI test at
`talos/tests/test_rt14_nexus_manifest.py` proves the manifest passes the generic validator and
that no tool name in it matches the NEXUS SoR-write denylist, exercised both positively (real
manifest) and negatively (a synthetic manifest exposing `tag_annotate` is caught).

## Consequences

- The v1 NEXUS manifest must enumerate every read tool by name; SoR-writers are excluded.
- `talos/validators/capability_manifest.py` is the enforcement point at parse time. ADR-033
  adds runtime enforcement at invocation time.
- Changes to the manifest contract require a new ADR and a new manifest version, not an in-place
  edit to `docs/contracts/capability-manifest.md`.
