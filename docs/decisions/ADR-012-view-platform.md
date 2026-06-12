# ADR-012: View platform — a web Space Agent surface, not native WinUI

**Status:** Accepted
**Date:** 2026-06-11
**Deciders:** Hunt Integrative Solutions LLC

## Context

The cockpit replaces the chatbot as the primary surface — chat is the training wheels, the board is the
tool. It must run on the mothership *and* locally on an air-gapped edge, serve every client deployment,
and match the board-as-Space decision (ADR-002). The choice was a **web** surface vs a **native
Windows (WinUI)** application (`BLUEPRINT.md` §187–191).

## Decision

Build the cockpit as a **web Space Agent surface**, resolved against native WinUI
(`BLUEPRINT.md` §187–191, §317–320): one codebase serves every deployment, runs locally on an
air-gapped edge, and matches board-as-Space. A thin **WebView2 shell** is optional for a desktop feel;
native GUI-driving (Studio 5000 / FactoryTalk with no API) is a **worker capability**, not a cockpit
concern. Folded in: agent-authored widgets run in a **locked iframe** reaching the engine only through
the board API, behind the gate (CR-20).

## Options considered

- **A — A native WinUI desktop app.** Rejected: a second codebase per platform, harder to run on an
  arbitrary edge, and a divergence from board-as-Space.
- **B — A web Space Agent surface (optional WebView2 shell).** Chosen.

## Trade-off analysis

One web codebase deploys to mothership and air-gapped edge alike and inherits the Space model directly.
The cost — no native desktop affordances — is covered by an optional WebView2 wrapper, and the one
thing native is actually needed for (driving the Studio 5000 / FactoryTalk GUIs that have no API) is
pushed to a worker capability where it belongs. Upstream stacks: `space-agent-notes.md` → "Tech Stack"
(YAML-backed Spaces, Alpine.js/Node); `hermes-profile-builder-notes.md` → "Dashboard Tech Stack"
(FastAPI + a React SPA). The changelog records the resolution to web (`BLUEPRINT.md` §317–320).

## Consequences

- **Easier:** one codebase everywhere, including air-gapped edges; inherits board-as-Space; per-board
  RLS scoping re-scopes everything on a client switch.
- **Harder:** a native desktop feel needs the WebView2 shell; widget sandboxing (locked iframe + CSP +
  board-API bridge) must be built.
- **Revisit:** the widget postMessage bridge allowlist + CSP set (CR-20, the `widget-sandbox` contract,
  to be prototyped).

## Action items

1. [ ] Build the cockpit as a web Space Agent surface over the board API.
2. [ ] Provide an optional WebView2 desktop shell.
3. [ ] Confine agent-authored widgets to a locked iframe via the board-API bridge (the widget gate).
