# ADR-028: Widget sandbox contract — iframe isolation, postMessage bridge, lifecycle gate

**Status:** Accepted
**Date:** 2026-06-17
**Deciders:** Hunt Integrative Solutions LLC

## Context

`docs/contracts/widget-sandbox.md` specifies how agent-authored widgets render safely in the
cockpit: locked iframe, postMessage allowlist, the propose→sandbox-render→critics→approve→pin
lifecycle, CSP/sandbox policy storage, and layout time-travel rules. This ADR formalizes the
contract as a binding decision record.

This ADR is a pointer-and-rationale record; the normative text lives in
`docs/contracts/widget-sandbox.md`.

## Decision

The widget-sandbox contract is accepted as binding. Key invariants:

1. **A widget may never write `approved_at` or submit a gate outcome.** `requestGate` may only
   raise the gate UI to the human. No widget message causes an approval write.
2. **A widget reaches the engine only through the board-API bridge.** Four allowlisted message
   types: `getTasks`, `getGateStatus`, `requestGate`, `subscribe`. No arbitrary fetch/XHR/WebSocket.
3. **A widget cannot cross `board_id`.** The host injects `board_id`; the widget never supplies it.
4. **A new or edited widget is a gated capability expansion.** It must pass critics + human
   approval before pinning. A post-pin source edit auto-reverts to `proposed` (CR-04).
5. **Time-travel restyles layout, never task truth.** Widget version rollback writes only to
   `space_versions.definition`/`widget_versions`; never to `tasks`, `task_gate_results`, or
   `task_events`.
6. **`sandbox_policy` is versioned with the widget.** Every `widget_versions` row carries its
   own CSP policy so rollback restores the policy alongside the source (no policy drift).

## v1 scope note (from 2026-06-16 interview)

The full Space Agent cockpit (including widget system) is **v1.x fast-follow**, not v1. v1
ships a minimal gate-approval UI (Markdown preview + critic verdicts + five outcome buttons).
The widget-sandbox contract governs v1.x and is frozen now so the v1 minimal UI does not
accidentally violate it.

## Open items (from contract, NEEDS-PROTOTYPE at P7)

- **CR-20** — exact CSP header set + full bridge message semantics. The four-type allowlist and
  iframe sandbox stance (`allow-scripts`, no `allow-same-origin`) are frozen; the precise CSP
  directive string, per-message param schemas, error envelope, and handshake/timeout are the
  prototype deliverable.
- `subscribe` cursor sharing — rides the board-api event-delta cursor or a filtered projection?
- Per-widget critic set + `waivable` flags (taxonomy exists; widget-specific list not enumerated).

## Consequences

- The widget-sandbox contract freeze is scheduled before Phase 5 (CR-23); the NEEDS-PROTOTYPE
  items must be resolved in a P7 prototype before the contract is considered fully closed.
- The widget bridge allowlist (four types) is frozen and may not be expanded without a new ADR.
