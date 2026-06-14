# Architecture Decision Records

Formal decision records for TALOS. Each ADR captures one architecture decision in a uniform template
(Context · Decision · Options considered · Trade-off analysis · Consequences · Action items).

**Authority:** `BLUEPRINT.md` is the design of record. Where an ADR and an upstream research note
(`docs/upstream/`) disagree, BLUEPRINT wins and the ADR notes the discrepancy. Seam-level resolutions
between already-chosen pieces live in `docs/integration/01_conflicts_and_resolutions.md` (cited as
`CR-NN`); ADRs fold those resolutions in by ID rather than cross-linking each other inline.

## Index

| ADR | Title | Status |
| :-- | :-- | :-- |
| [001](ADR-001-platform-vs-nexus.md) | TALOS is a platform; NEXUS is a capability behind MCP | Accepted |
| [002](ADR-002-board-as-space.md) | Board engine + Space Agent view (the board *is* a Space) | Accepted |
| [003](ADR-003-polyglot-memory.md) | Polyglot memory — four stores, one per job | Accepted |
| [004](ADR-004-capability-tool-profiles.md) | Capability tool profiles — read by default, write = offline/sim only | Accepted |
| [005](ADR-005-cross-client-memory-promotion-gate.md) | Cross-client memory split — `[client]` default, one promotion gate | Accepted |
| [006](ADR-006-strategy-ladder.md) | The Strategy Ladder — a declarable six-step task execution pattern | Accepted |
| [007](ADR-007-parser-ownership.md) | Parser ownership — NEXUS owns parsers; TALOS couples to the output contract | Accepted |
| [008](ADR-008-vault-topology.md) | Vault topology — graph-as-linker on the mothership, versioned pull to thick edges | Accepted |
| [009](ADR-009-layered-tool-policy.md) | Layered tool policy — intersection-only, restrict-never-expand, no-live-writes floor | Accepted |
| [010](ADR-010-worker-isolation.md) | Worker isolation — session keys + restrict-only config inheritance | Accepted |
| [011](ADR-011-gate-outcomes.md) | Gate outcomes — five, not two; safety critics escalate-only | Accepted |
| [012](ADR-012-view-platform.md) | View platform — a web Space Agent surface, not native WinUI | Accepted |
| [013](ADR-013-coherence-model.md) | Coherence model — coherence at the planner, isolation at the workers | Accepted |
| [014](ADR-014-consolidation-boundaries.md) | Consolidation boundaries — autonomous within one client scope; cross-scope MERGE forbidden | Accepted |
| [015](ADR-015-phase-reorder.md) | Phase reorder — gate + critics before the full dispatcher | Accepted |
| [016](ADR-016-dag-driven-project-scheduling.md) | DAG-driven project scheduling — board, Gantt, and dispatcher are one system | Proposed |
| [017](ADR-017-data-egress-residency.md) | Data-egress and residency — hosted model endpoints, not air-gapped | Accepted |

> **On the numbering:** the **PageRank context map** (`BLUEPRINT.md` §175–178) is named in BLUEPRINT's
> "to formalize next" list but was deliberately **not** assigned an ADR number — CR-10 flags it for
> folding into ADR-003 or a future dedicated PageRank ADR. ADR-017 was added 2026-06-14 to resolve the
> RT-07 air-gap contradiction (data-egress/residency decision).
