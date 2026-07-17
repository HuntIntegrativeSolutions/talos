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
- **Memory** — unified Postgres (ADR-039): one database is the system of record, pgvector holds
  vector search over rules and documentation, and a markdown vault holds human-facing docs. An
  earlier design split memory across a separate graph store and Redis; both were cancelled in
  favor of one Postgres-backed store.
- **Capabilities** — domain packs (e.g. NEXUS for PLC analysis) attach *behind the MCP boundary*,
  which doubles as a security boundary.

Deployment is **hub-and-spoke**: a control-plane mothership where you work across all clients, and
slim per-client edges that keep each client's data local and sync only coordination state.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design and
[`docs/decisions/`](docs/decisions) for the decision records.

## Status

**Pre-alpha · P0–P3 core complete, P4/P5/P5.5 closed, P6-Sim Landing 1 closed, P7a closed.**

The schema, contracts, critics registry, five-outcome human-review gate, full asyncio dispatcher,
unified Postgres memory (pgvector + markdown vault, ADR-039), Crystallize rule extraction, a
bounded critic-fail→revise loop, and the first verifier critic are implemented and tested. The
full Space Agent cockpit (P7b), the rest of the sim-execute capability (P6), and the gateway (P8)
have not been built yet.

What is runnable today:
- `talos/validators/` — capability-manifest validator (P0)
- `talos/critics/` — deterministic gate critics, registry, and verifier-critic infrastructure (P2/P6)
- `talos/graph/spine.py` — LangGraph spine with five-outcome gate, bounded critic-fail→revise loop,
  and a 4-branch read fan-out (P1/P2/P4b/P5/P5.5)
- `talos/worker.py` — asyncio dispatcher, heartbeat, and dead-worker reclaim (P3)
- `talos/api.py` — FastAPI board API with full gate endpoint, JWT auth, review-queue/SLA endpoints
  (P1/P2/RT-01/P4a/P4b)
- `talos/auth/` — local JWT auth (RT-01)
- `talos/memory/` — pgvector-backed documentation and rule stores (P4a/P4b/P5)
- `web/gate/` — the P7a minimal gate-approval web UI

See [`CLAUDE.md`](CLAUDE.md) for the full module-by-module breakdown.

## Quick start

Requires Python 3.11+ and Docker (tests spin up Postgres 16 via testcontainers).

```bash
git clone git@github.com:HuntIntegrativeSolutions/talos.git
cd talos
python -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"
TALOS_NEXUS_STUB=1 python -m pytest talos/ -v
```

Or with `uv` (this repo ships a `uv.lock`):

```bash
uv sync --extra test
TALOS_NEXUS_STUB=1 uv run pytest talos/ -v
```

Either path also installs the `talos` CLI entry point (`talos.cli:main`) — run `talos --help`
once installed.

## Repo layout

```
talos/         Implemented Python modules
  critics/     Deterministic gate critics, registry, verifier-critic infrastructure (P2/P6)
  graph/       LangGraph spine with five-outcome gate + revise loop + read fan-out (P1/P2/P4b/P5/P5.5)
  validators/  Capability-manifest validator (P0)
  auth/        Local JWT auth (RT-01)
  memory/      pgvector-backed documentation and rule stores (P4a/P4b/P5)
  llm_providers/  Multi-provider LLM abstraction (ADR-031)
  tests/       300+ tests, count moves with every landing — run `pytest talos/ -v` for the total
  worker.py    Asyncio dispatcher, heartbeat, dead-worker reclaim (P3)
  api.py       FastAPI board API (P1/P2/RT-01/P4a/P4b)
  cli.py       `talos` CLI entry point
engine/        Postgres schema (schema*.sql) + migrations/ (Alembic, ADR-034)
web/gate/      Live P7a minimal gate-approval web UI (static HTML/JS/CSS, no build system)
gateway/       Placeholder — sandboxed proactive loops (not built)
memory/        Placeholder doc — real memory code lives in talos/memory/
capabilities/  NEXUS capability-manifest dispositions
scripts/       One-off maintenance scripts (e.g. Chroma→pgvector migration)
docs/
  ARCHITECTURE.md        High-level system overview
  decisions/             ADR-001 through ADR-039 — binding design decisions
  contracts/             Four frozen seam contracts
  integration/           Reconciliation documents (integration map, build sequence, red-team)
  upstream/              Notes from upstream harnesses studied during design
BLUEPRINT.md   Authoritative living design document
ROADMAP.md     Phase-ordered roadmap
assets/        Brand assets (emblem, etc.)
```

## License

MIT © 2026 Hunt Integrative Solutions LLC. See [LICENSE](LICENSE).
