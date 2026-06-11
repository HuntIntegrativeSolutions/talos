# Contributing to TALOS

TALOS is pre-alpha and the architecture is still settling. The most useful contributions right now
are design review against `docs/` and the board schema in `engine/schema.sql`.

## Ground rules

- **The Guardian doctrine is non-negotiable.** Nothing should add a path that writes to a live
  system without passing the review gate (deterministic critics + human approval).
- **Keep the boundaries clean.** The view never touches the database directly — only the board API.
  Domain capabilities stay behind MCP.
- **Credit upstreams.** TALOS blends MIT-licensed projects; keep their notices and attribute ported
  code.

## Workflow

1. Open an issue describing the change and which boundary it touches.
2. For design changes, propose or update an ADR in `docs/decisions/`.
3. Keep PRs scoped to one component (`engine`, `web`, `critics`, `gateway`, `memory`).
