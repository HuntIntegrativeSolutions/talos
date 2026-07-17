# TALOS — Architecture

## Context & goals

TALOS is an agent harness for **business and industrial operations**, not coding. It exists to
let AI carry the repetitive and bookkeeping layers of operational work — task coordination,
documentation, analysis, follow-ups — while humans keep ownership of every safety- and
money-critical decision.

Design constraints that shape everything below:

- **Hard client isolation.** A consulting practice serves several clients (e.g. GLOBEX, INITECH, ACME);
  one client's data must never be visible from another's context.
- **A real review gate.** Nothing reaches a live system without deterministic checks *and* a
  human's approval.
- **Memory that lasts.** Knowledge and episodic recall must span projects and years.
- **Mixed deployment.** Some work runs in a homelab; some must run on a client's isolated,
  air-gapped network where data cannot leave.

## What it blends

TALOS takes the strongest piece of each upstream (all MIT) and lets each do what it is best at:

| Piece | Source | What we take |
| --- | --- | --- |
| Board engine + hardened task lifecycle | Hermes (NousResearch) | The SQLite board ported to Postgres: tasks, dependency DAG, runs, append-only event log, heartbeat, circuit breaker, per-task model routing, tenants. |
| Self-reshaping UI + time-travel | Space Agent (agent0ai) | The board rendered as a "Space"; agent-authored widgets; layout versioning / rollback. |
| Hierarchy + memory areas | Agent Zero (agent0ai) | Superior/subordinate delegation; the "verified solutions" memory area; project isolation. |
| Gateway + proactive loops | OpenClaw patterns | The gateway-in-front-of-model pattern and cron proactivity — adopted as *patterns*, sandboxed away from privileged tools because of OpenClaw's documented attack surface. |

## The stack (in priority order)

1. **Board engine** — `engine/`. The source of truth (`engine/schema.sql`). Server-authoritative:
   task state, runs, the event log, and the review gate live here. A dispatcher loop claims ready
   tasks, spawns assigned profiles, and reclaims crashed/stale workers.
2. **Industrial / domain integration** — domain capability packs (e.g. **NEXUS** for PLC analysis)
   attach *behind the MCP boundary*. Agents claim a task → call capability tools → output lands in
   **Review** → critics gate → human approves. The capability never moves on its own.
3. **Unified Postgres memory** — `talos/memory/`. One Postgres database is the system of record;
   pgvector holds vector search over rules and documentation; a markdown vault holds human-facing
   docs. ADR-039 supersedes ADR-003's four-store split — no separate graph store or Redis.
4. **Business layer** — invoicing, time, per-client P&L, proposals (QuickBooks and similar),
   exposed as ordinary capabilities behind the gateway.

## Board as a Space (view layer)

The board's *truth* lives in Postgres; its *presentation* is a Space Agent space. Columns, cards,
and per-task widgets are agent-rendered and time-travel-versioned. The view reaches the engine only
through the **board API** — never the database directly. Time-travel versions the *layout*, never
task records: a UI rollback can restyle the board, but it can never un-complete a task. (ADR-002.)

## The review gate

A first-class `review` status sits between `running` and `done`. The engine refuses to advance a
gated task until **every required critic returns `pass`** (`task_gate_results`) **and a human sets
`approved_at`** (see `v_gate_status`). Agent-authored widgets and self-written skills ride the same
gate — a new skill is a *proposal*, not a trusted instruction, which closes the OpenClaw
"loaded skill = trusted" hole.

## Deployment — hub-and-spoke

- **Mothership (control plane):** the board (Postgres), pgvector, the markdown vault, the
  dispatcher, and the business layer. Where the operator works across all clients.
- **Edges (per client):** a slim, single-board runtime that runs the agents touching that client's
  systems, keeps their data local, and syncs only non-sensitive coordination state up over the
  private network (e.g. Tailscale). An air-gapped client workstation is an edge.

## The MCP boundary as a security boundary

Domain capability packs sit behind MCP. That boundary is also a trust boundary: the orchestrator
can be fully compromised and still cannot write to a live system, because the capability enforces
its own propose-only / critics-gate doctrine at its own edge. This is the architectural reason
TALOS is a *platform that calls* its capabilities rather than a monolith that absorbs them. (ADR-001.)

## Component map

```
engine/    Postgres schema + migrations (schema-only; the runtime lives in talos/)
talos/     spine, dispatcher, board API, critics, validators, auth, memory adapters
web/gate/  Space Agent-style gate UI (board-as-Space, widgets, time-travel is P7b, not yet built)
critics/   deterministic gate functions (verdict: pass | fail | warn) — lives in talos/critics/
gateway/   sandboxed proactive loops, notifications, channel adapters (not yet built)
memory/    adapters: postgres | pgvector | vault — lives in talos/memory/
```

## Decision records

See [`docs/decisions/README.md`](decisions/README.md) for the full ADR index (ADR-001 through
ADR-039). ADR-039 supersedes ADR-003 (polyglot memory → unified Postgres).
