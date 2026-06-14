<p align="center">
  <img src="./assets/talos-emblem.png" alt="TALOS" width="160" />
</p>

<h1 align="center">TALOS</h1>

<p align="center"><strong>An agent harness for business and industrial operations.</strong></p>

---

TALOS is a multi-agent work-board with a hardened task lifecycle, a polyglot memory, and a
hard human-review gate — purpose-built so AI can accelerate the repetitive and bookkeeping
layers of real operational work *without ever touching what it shouldn't*.

It blends the strongest pieces of several open-source harnesses into one platform built for
plants, shops, and the businesses that run them.

> **The Guardian doctrine** — AI proposes, humans review, deterministic critics gate, and
> nothing is written to a live system (a processor, a ledger, production) without a human's
> approval. Talos, the bronze guardian of myth, watches the gate.

## Why

Most agent harnesses are built for writing code. Operations work — automation, manufacturing,
maintenance, and the businesses around them — needs the same orchestration but with things
coding harnesses don't have: hard client isolation, a real review-and-approval gate, memory
that spans projects and years, and first-class integration with the domain tools and business
systems that actually run the work.

## What it blends (all MIT)

| Capability | Borrowed from |
| --- | --- |
| Multi-agent board + hardened task lifecycle | [Hermes](https://github.com/NousResearch/hermes-agent) (NousResearch) |
| Self-reshaping UI, "spaces", time-travel rollback | [Space Agent](https://github.com/agent0ai/space-agent) (agent0ai) |
| Hierarchical delegation, memory areas | [Agent Zero](https://github.com/agent0ai/agent-zero) (agent0ai) |
| Sandboxed gateway + proactive (cron) loops | OpenClaw patterns |

Each upstream is MIT-licensed. TALOS takes their patterns and, where it helps, ported code —
and credits them.

## Architecture at a glance

- **Engine** (Python + Postgres) — the board's source of truth: tasks, dependencies, runs, the
  append-only event log, and the review gate. Ported from Hermes' board, hardened for multi-client.
- **Web** (Space Agent view) — the board *is* a Space. Columns, cards, and per-task widgets are
  self-reshaping and time-travel-versioned. The view talks to the engine only through the board API.
- **Critics** — deterministic gate functions. A task cannot leave **Review** until every required
  critic passes *and* a human approves.
- **Gateway** — a sandboxed orchestration layer for proactive loops and notifications, walled off
  from privileged tools.
- **Memory** — polyglot by design: Postgres (system of record), a graph (knowledge & topology), a
  vector store (semantic + episodic recall), and Redis (working memory + live dashboard).
- **Capabilities** — domain packs (e.g. NEXUS for PLC analysis) attach *behind the MCP boundary*,
  which doubles as a security boundary.

Deployment is **hub-and-spoke**: a control-plane mothership where you work across all clients, and
slim per-client edges that keep each client's data local and sync only coordination state.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design and
[`docs/decisions/`](docs/decisions) for the decision records.

## Status

**Pre-alpha · P0 + P1 + P2 complete.**

The schema, contracts, critics registry, and five-outcome human-review gate are implemented
and tested. The full distributed dispatcher (P3), memory federation (P4), sim-execute (P6),
web cockpit (P7), and gateway (P8) have not been built yet.

What is runnable today:
- `platform/validators/` — capability-manifest validator (P0)
- `platform/critics/` — deterministic gate critics and registry (P2)
- `platform/graph/spine.py` — 4-node LangGraph spine with five-outcome gate (P1/P2)
- `platform/worker.py` — single-worker claim loop (P1, no dispatcher yet)
- `platform/api.py` — FastAPI board API with full gate endpoint (P1/P2)

## Quick start

Requires Python 3.11+ and Docker (tests spin up Postgres 16 via testcontainers).

```bash
git clone git@github.com:HuntIntegrativeSolutions/talos.git
cd talos
python -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"
TALOS_NEXUS_STUB=1 python -m pytest platform/ -v
```

## Repo layout

```
platform/      Implemented Python modules
  critics/     Deterministic gate critics and registry (P2)
  graph/       LangGraph spine with five-outcome gate (P1/P2)
  validators/  Capability-manifest validator (P0)
  tests/       27 integration + unit tests (P0–P2)
  worker.py    Single-worker claim loop (P1)
  api.py       FastAPI board API (P1/P2)
engine/        Postgres schema (schema.sql + schema-additions.sql + schema-p2.sql)
web/           Placeholder — Space Agent cockpit (not built)
gateway/       Placeholder — sandboxed proactive loops (not built)
memory/        Placeholder — polyglot memory adapters (not built)
docs/
  ARCHITECTURE.md        High-level system overview
  decisions/             ADR-001 through ADR-017 — binding design decisions
  contracts/             Four frozen seam contracts
  integration/           Reconciliation documents (integration map, build sequence, red-team)
  upstream/              Notes from upstream harnesses studied during design
BLUEPRINT.md   Authoritative living design document (v0.6)
ROADMAP.md     Phase-ordered research and documentation roadmap
assets/        Brand assets (emblem, etc.)
```

## License

MIT © 2026 Hunt Integrative Solutions LLC. See [LICENSE](LICENSE).
