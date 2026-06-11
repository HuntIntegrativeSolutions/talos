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

**Pre-alpha.** The architecture and the board schema land first (see
[`engine/schema.sql`](engine/schema.sql)); the engine port and the web view follow. Not yet runnable.

## Repo layout

```
engine/    board source of truth (Postgres) + task lifecycle
web/       Space Agent view layer (the board, rendered as a Space)
critics/   deterministic review-gate functions
gateway/   sandboxed proactive loops + notifications
memory/    polyglot memory adapters
docs/      architecture + decision records (ADRs)
assets/    mascot + brand
```

## License

MIT © 2026 Hunt Integrative Solutions LLC. See [LICENSE](LICENSE).
