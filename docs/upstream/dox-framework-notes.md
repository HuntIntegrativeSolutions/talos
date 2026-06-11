# DOX Framework — Technical Deep-Dive Notes

> Research date: 2026-06-11  
> Source: agent0ai/space-agent (root `AGENTS.md`), NEXUS codebase (`mcp_server.py`, `tests/`),  
>         TALOS repo AGENTS.md chain  
> Purpose: Capture DOX mechanics so TALOS can apply the pattern consistently.

---

## Executive Summary

DOX is a **hierarchical documentation and contracts framework** — a convention enforced by protocol, not by a runtime parser. It solves the drift problem: as a codebase grows, implicit rules scatter across comments, wikis, and tribal knowledge and no longer travel with the code. DOX makes ownership explicit (nearest AGENTS.md owns the subtree) and rules auditable (they live in the tree, not in someone's head).

| Aspect | Key Finding |
| :--- | :--- |
| **Type** | Pure convention — markdown AGENTS.md files, no external library |
| **Enforcement** | LLM-context injection + human protocol; no live parser |
| **Relationship to SKILL.md** | Orthogonal — DOX owns the module; SKILL.md is a deliverable artifact it owns |
| **generate_dox_tree** | NEXUS tool that renders live DB findings into a DOX tree; read-only, atomic swap, never hand-edit |
| **Space Agent usage** | Mandatory; root AGENTS.md is 354 lines; all modules have child AGENTS.md files |
| **TALOS usage** | Already adopted — `_Tools/AGENTS.md` chain governs the toolchain |

---

## 1. What DOX Is

DOX is not a library you install. It is a **pattern for writing and reading AGENTS.md files** that is already in use in this repo and in every agent0ai project.

The core idea: every folder has an optional `AGENTS.md` that is a **binding work contract** for that folder and all descendants. An agent editing a file in `nexus/nexus/parsers/` reads the chain:

```
_Tools/AGENTS.md
  nexus/AGENTS.md
    nexus/nexus/AGENTS.md
      nexus/nexus/parsers/AGENTS.md
```

The nearest applicable AGENTS.md controls local details. No child AGENTS.md may weaken a parent rule.

---

## 2. AGENTS.md Format

### Frontmatter (optional but recommended)

```yaml
---
dox_level: root | branch | leaf
dox_parent: "../AGENTS.md"
dox_scope: "One-line description of what this subtree owns"
tags:
  - dox/child
  - dox/branch
  - custom_domain_tag
---
```

### Section Order (canonical)

```markdown
# <Area> DOX — <Title>

> Brief prose anchor: what this tree is and why it matters

---

## Purpose
What this scope owns. Not just file paths — the responsibility.

## Ownership
Who owns which files or subsystems in this tree.

## Local Contracts
Explicit invariants. Example:
### Contract 1 — Schema is forward-only
ALTER TABLE ADD COLUMN only. No DROP, no RENAME.
### Contract 2 — address_xref is not automatic
Always run `nexus xref build` after ingest.

## Work Guidance
Process rules, preferred patterns, anti-patterns.

## Verification
How to test that work in this scope is correct.

## Child DOX Index
- [child-name](child/AGENTS.md) — one-line scope summary
- [another-child](another/AGENTS.md) — one-line scope summary
```

### Hierarchy Rules

1. A child AGENTS.md applies to its directory and all descendants.
2. Parent contracts propagate down. Child can only **add restrictions**, not weaken parent rules.
3. When two AGENTS.md files conflict on a rule, the **closer** (child) one controls the local detail, but if that detail would weaken the parent's intent, the parent wins.
4. Read the full chain root → target before editing. Do not skip levels.

### What to Keep in AGENTS.md

- Durable structure, ownership, and contracts.
- Non-obvious constraints (why something is done this way, not just what).
- Work protocols specific to this scope.

### What NOT to Put in AGENTS.md

- Ephemeral task notes (use tasks / PR descriptions).
- Diary entries ("we decided this on June 11").
- Duplicated rules from parent (only local additions belong here).
- History of what changed (git log is authoritative).

---

## 3. How Agents Read AGENTS.md

### Protocol (from Space Agent root AGENTS.md)

```
Before editing any file:
1. Start at the repo root AGENTS.md.
2. Identify all files you plan to touch.
3. Walk from root to each target path.
4. Read every AGENTS.md found on each path.
5. Use the nearest AGENTS.md as the local contract.
6. If a child rule contradicts a parent: closer doc controls the detail,
   but no child may weaken the parent's intent.
```

### Is This Parsed Programmatically?

**Current state: No.** Agents read the markdown files as prose. The convention is enforced by:
- Human protocol (PR review catches violations).
- LLM context injection (agents receiving the AGENTS.md chain understand what's required).

**Emerging tooling** (NEXUS / TALOS):
- `generate_dox_tree()` writes AGENTS.md files programmatically as rendered output (not as input parsing).
- CI linting can validate: "are all Child DOX Index entries real files?" and "does every `plugin.yaml` have an owning AGENTS.md?"
- The test `test_dox_findings.py` enforces contracts programmatically for the most critical invariant (read-only rendering).

---

## 4. generate_dox_tree — NEXUS Implementation

**Location:** `nexus/nexus/mcp_server.py:5681`

`generate_dox_tree` is a **one-way rendering function**: takes live DB state → writes an AGENTS.md tree. It never reads AGENTS.md files; it produces them.

### What It Generates

```
.nexus/dox/
  AGENTS.md                             ← Root: "Never hand-edit. Regenerated from DB."
  ACME/
    ACME-HVAC-01/
      AGENTS.md                         ← PLC summary + anomalies + child index
      areas/
        01-hvac-zone/
          AGENTS.md                     ← Area: derivation, ANM, FND, live-routed tools
        02-compressor-unloader/
          AGENTS.md
        ...
```

### Generation Pipeline

```
generate_dox_tree(plc_id)
  [1] Validate PLC exists
  [2] Resolve output root (NEXUS_SOURCE_ROOT or NEXUS_LIBRARY_ROOT)
  [3] Derive client code from PLC ID (ACME-EQ-NN → ACME)
  [4] Build paths: dox_root / client_code / plc_id
  [5] Leftover detection (crash recovery)
  [6] Derive areas from DB via group_io_by_area()
  [7] Build address → area mapping
  [8] Rung co-occurrence scan (interlocks between co-occurring areas only)
  [9] Cross-area interlock calls → NOT inlined; marked "live-routed"
  [10] Fetch ANM (anomalies) and confirmed FND only (proposed/dismissed excluded)
  [11] Write to tmp_dir: root AGENTS.md, PLC root, area files × N
  [12] Atomic swap: live → bak, tmp → live, rmtree(bak)
  [13] Return { generated_root, files, area_count, findings_counts }
```

### Atomic Swap (Crash-Safe)

```
Step 1: rename(live_dir → bak_dir)   ← old tree is now bak
Step 2: rename(tmp_dir → live_dir)   ← new tree is now live
Step 3: rmtree(bak_dir)              ← clean up

Crash recovery on entry:
- bak exists, tmp absent   → step 3 interrupted. Live is good. rmtree(bak).
- tmp exists               → crashed before step 1. rmtree(tmp). Retry.
```

### Inlined vs. Live-Routed Data

**Inlined at generation time** (stamped once, slow-changing):
- Confirmed findings (FND) — curated, human-ratified.
- Anomalies (ANM) — deterministic from DB scan.
- Area derivation summary (address count, file type distribution).

**Marked as live-routed** (dynamic, computed at query time):
- `address_xref()` — changes with every ingest.
- `find_interlocks()` — changes if logic changes.
- `coverage_gap()` — changes as HMI coverage improves.

This prevents the DOX tree from becoming stale for dynamic data while still providing a durable record of confirmed analysis.

### Key Invariant (Enforced by Test)

```python
# nexus/tests/test_dox_findings.py
def test_generate_dox_tree_contains_no_writes(source):
    """generate_dox_tree must be read-only (no DB writes)."""
    for dml in ["INSERT", "UPDATE", "DELETE"]:
        assert dml not in inspect.getsource(generate_dox_tree)
```

The tree is always regenerated fresh from the DB. Hand-edits are overwritten. This is by design — the DB is the source of truth, not the tree.

---

## 5. SKILL.md and Its Relationship to DOX

SKILL.md files are **not part of DOX core**. They are deliverable artifacts owned by their parent module's AGENTS.md.

**SKILL.md schema:**

```yaml
---
name: memory
description: Persist user-scoped behavior through prompt-include memory files
metadata:
  loaded: true            # Auto-load at boot (vs. on-demand)
  placement: system       # system | transient | history
---

# Skill Body (Markdown)

Rules and instructions for the agent...
```

**Relationship:**

- DOX contract says: "module `_core/memory/` owns the memory skill."
- Module's AGENTS.md documents: "skills live at `ext/skills/memory/SKILL.md`; placement is `system`."
- The SKILL.md is the content; the AGENTS.md is the ownership contract.

**TALOS mapping:**

- TALOS skills live at `skills/<name>/SKILL.md` (project scope) or `clients/<id>/skills/<name>/SKILL.md` (client scope).
- The module AGENTS.md that owns a skill group documents: what skills exist, what their trigger patterns are, which allowed_tools are permitted.
- Skills in `proposed` state are not listed in the module's AGENTS.md yet — only `pinned` skills appear.

---

## 6. The Rules System

Rules in DOX live inside `AGENTS.md`, not in separate RULES.md files. They appear under `## Local Contracts` or `## Work Guidance`.

**Contract format:**

```markdown
## Local Contracts

### Contract 1 — Schema is forward-only
`ALTER TABLE ADD COLUMN` only. No `DROP`, no `RENAME`. Column renames silently
break the UI (nexus-ui hardcodes column names). Any schema change requires
a matched update in `nexus-ui/src/lib/nexus-db.ts`.

### Contract 2 — address_xref is never automatic
`nexus index` does NOT build xref. Always run `nexus xref build` after ingest.
The separation is intentional — xref is expensive and not always needed.
```

**Effective scope:** A rule stated in a contract block applies to that AGENTS.md's tree and all descendants unless a descendant explicitly narrows it (never broadens it).

**Example rule propagation:**

```
_Tools/AGENTS.md: "No git push without lead approval."
  nexus/AGENTS.md: "Eval harness always hits production nexus.db. Run after schema changes."
    nexus/nexus/parsers/AGENTS.md: "Parsers return data; never write DB."
```

All three rules apply when editing `nexus/nexus/parsers/plc5doc_parser.py`. The parser-level rule narrows ("return data only") — it doesn't weaken the parent rules ("no git push" still applies).

---

## 7. Enforcement Mechanisms (Present and Planned)

### Current

| Mechanism | Scope | Hardness |
| :--- | :--- | :--- |
| Agent reads AGENTS.md chain before editing | All scopes | Soft (protocol) |
| Human PR review catches contract violations | All scopes | Human gate |
| `test_generate_dox_tree_contains_no_writes` | `generate_dox_tree` specifically | Hard (CI) |
| Findings lifecycle fail-closed (`queued → proposed → confirmed`) | DB findings | Hard (code) |

### Planned (TALOS Phase 1/2)

| Mechanism | Scope | ADR |
| :--- | :--- | :--- |
| Session-key scope enforcement | Worker config inheritance | ADR-010 |
| Capability manifest validator | Tool policy (read vs. write) | ADR-004 |
| Widget CSP + postMessage bridge | Cockpit widgets | ADR-012 / Contract 4 |
| Skill `allowed_tools` critic | Skill proposal gate | ADR-009 |
| Cross-client MERGE hard block | Memory consolidation | ADR-014 |
| `propose → review → pin` state machine | Skills, widgets, strategy paths | ADR-011 |

---

## 8. DOX in TALOS — Adoption Plan

TALOS already uses DOX through the `_Tools/AGENTS.md` hierarchy. The following additions complete the adoption.

### 8.1 TALOS-Specific AGENTS.md Tree

Every TALOS component directory should have an AGENTS.md:

```
_Tools/talos/AGENTS.md               → talos root (owned by lead; top-level contracts)
  engine/AGENTS.md                   → schema + migration rules
  critics/AGENTS.md                  → critic library contracts
  gateway/AGENTS.md                  → gateway + proactivity layer
  memory/AGENTS.md                   → memory adapter contracts
  web/AGENTS.md                      → cockpit shell + widget contracts
  docs/AGENTS.md                     → documentation standards
  docs/decisions/AGENTS.md           → ADR format rules
  docs/contracts/AGENTS.md           → contract format rules
  docs/upstream/AGENTS.md            → upstream notes format
```

**Immediate work:** Write `_Tools/talos/AGENTS.md` (currently absent — the root).

### 8.2 DOX for Agent-Authored Artifacts

When a TALOS agent crystallizes a skill or widget, the associated module AGENTS.md is updated as part of the crystallization step:

```
crystallize(skill)
  [1] Gate: propose → critics pass → human approve
  [2] Pin: write SKILL.md to skills/<client>/<name>/
  [3] DOX: update owning module's AGENTS.md Child DOX Index
  [4] Commit: conventional commit message, push
```

This keeps the AGENTS.md tree authoritative over what artifacts exist in a module.

### 8.3 generate_dox_tree as a TALOS Pattern

The NEXUS `generate_dox_tree` pattern is directly applicable to TALOS task summaries:

```
generate_talos_dox_tree(board_id)
  → Fetch confirmed findings for board
  → Fetch active tasks + gate status
  → Write docs/dox/<client>/<board_id>/AGENTS.md
      (confirmed findings, gate history, open risks)
  → Write area-level child docs per phase/workstream
```

This gives a project manager a read-only, always-fresh DOX tree that documents what the agents have confirmed — without any hand-editing.

---

## 9. Key Files Reference

| File | Role |
| :--- | :--- |
| `/mnt/d/Cert Projects/space-agent/AGENTS.md` | Root DOX contract — best example of a complete root AGENTS.md |
| `/mnt/d/Cert Projects/space-agent/app/AGENTS.md` | 354-line module AGENTS.md — reference for complex subtrees |
| `/mnt/d/Cert Projects/space-agent/app/L0/_all/mod/_core/spaces/AGENTS.md` | Widget/spaces subsystem AGENTS.md |
| `/mnt/d/Cert Projects/AGENTS.md` | Plant repo root — enforcement example (multi-agent coordination rules) |
| `/mnt/d/Cert Projects/_Tools/AGENTS.md` | Toolchain root — governs nexus, nexus-agents, nexus-ui, talos |
| `/mnt/d/Cert Projects/_Tools/nexus/AGENTS.md` | Nexus subsystem DOX |
| `nexus/nexus/mcp_server.py:5681` | `generate_dox_tree` implementation |
| `nexus/tests/test_dox_findings.py` | Programmatic contract enforcement example |
| `.nexus/dox/ACME/ACME-HVAC-01/AGENTS.md` | Generated DOX tree — PLC-level example |
| `.nexus/dox/ACME/ACME-HVAC-01/areas/01-hvac-zone/AGENTS.md` | Generated DOX tree — area-level example |
