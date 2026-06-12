# Contract — `widget-sandbox`

> **What this is:** the frozen specification of how agent-authored widgets render safely in the
> cockpit. It pins the propose→sandbox-render→critics→approve→pin lifecycle, the CSP/postMessage
> boundary, the restricted board-API scopes a widget may call, and how layout time-travel works
> without touching task truth.
> **What this is not:** the exact CSP byte-string or the full bridge message semantics (that is
> **CR-20, NEEDS-PROTOTYPE** — see Open questions), the cockpit's internal rendering engine, or new
> architecture. It encodes ADR-002, ADR-011, ADR-012, CR-04, and CR-20, over the existing
> `widget_versions.sandbox_policy` schema placeholder.
>
> **Citation key:** `02 §N` = `docs/integration/02_unified_architecture.md` (`§8 #n` = numbered
> invariants); `ADR-0NN` = `docs/decisions/`; `CR-NN` = `docs/integration/01_conflicts_and_resolutions.md`;
> schema objects quote `engine/schema.sql`.

**Status:** Draft for freeze · **Date:** 2026-06-12 · **Deciders:** Hunt Integrative Solutions LLC
**Seam:** view ↔ widget (`02 §6`, row 4) · **Freeze order:** before Phase 5 (CR-23)

---

## Purpose

The board is a Space; widgets are agent-authored panels (tag-trace, dependency map, P&L, etc.). The
upstream Space Agent widget runtime is **intentionally open** — main browser context, no CSP — which a
multi-client cockpit cannot allow (CR-20, `space-agent-notes`). This contract is the gate TALOS adds:
agent-authored UI runs in a **locked iframe**, reaches the engine **only through the board API**, and
must pass critics before it is pinned (ADR-002, ADR-012). It is the view-layer enforcer in the
isolation stack (`02 §5`).

## The two sides it decouples

| Side | Owner | Builds against this contract to… |
| :--- | :--- | :--- |
| **Cockpit host** (enforcer) | `web/` — the Space Agent surface | mount the iframe under the CSP, broker the postMessage allowlist, run the lifecycle, version/pin |
| **Widget** (sandboxed guest) | agent-authored JS/JSX in `widget_versions.source` | render inside the iframe, request data only via the allowlisted bridge |

The widget author cannot reach Postgres, the network, or the DOM outside the iframe; the host never
executes widget code outside the sandbox. The bridge allowlist + CSP are the only shared surface.

## Interface

### 1. postMessage bridge allowlist **[D] — exactly four message types**

A widget reaches the engine **only** through the board-API bridge, via this exact allowlist (CR-20,
`02 §5`): `getTasks`, `getGateStatus`, `requestGate`, `subscribe`. No arbitrary `fetch`, no DOM outside
the iframe. Representative envelope (the *allowlist* is [D]; exact field semantics are [I] / Open):

```json
// widget → host
{ "v": 1, "id": "req-7", "type": "getGateStatus",
  "params": { "task_id": "t-1042" } }          // board_id is injected by the host, never by the widget
// host → widget
{ "v": 1, "id": "req-7", "ok": true,
  "data": { "task_id": "t-1042", "all_required_pass": false,
            "human_approved": false, "gate_satisfied": false } }
```

| Message | Maps to board-API op | Class |
| :--- | :--- | :--- |
| `getTasks` | `getTasks` (read) | read |
| `getGateStatus` | `getGateStatus` (read) | read |
| `requestGate` | `requestGate` (board-api §3) — raises the gate UI, returns gate context, **no write** | request |
| `subscribe` | `subscribe` event-delta stream (read) | read |

**`requestGate` is a *request*, not an approval.** It asks the host to surface the gate UI to the
human; it can never carry or cause an `approved_at` write.

### 2. Board-API scopes = a restricted READ+REQUEST subset **[D]**

The widget's reachable surface is a **strict subset** of [`board-api.md`](./board-api.md): the four
read/request messages above, board-scoped by the host. The trusted **gate-outcome submission**
(Approve/Reject/Waive/Edit/Escalate → `approved_at`, `task_gate_results`) and **widget-upsert request**
paths stay in board-api, invoked by the **human view host**, and are **out of the widget's reach**.
This is the seam split: board-api owns the trusted human paths; widget-sandbox owns a read+request
subset and the sandboxing.

### 3. Lifecycle state machine **[D]**

Widgets ride the same gate as spaces (`schema.sql` lines 205-207, `widgets.status` line 240):

```
proposed ──▶ in_review ──▶ pinned
   ▲             │             │
   │             ▼             ▼
   └──────── rejected      reverted
   (post-pin source edit auto-reverts to 'proposed')
```

- `status` ∈ `proposed | in_review | pinned | reverted | rejected` (verbatim, `schema.sql` line 240).
- **propose** — `requestWidgetUpsert` (board-api) creates `widgets` + `widget_versions` at
  `status='proposed'`.
- **sandbox-render** — the host renders the candidate inside the locked iframe for critics to inspect;
  no pin yet.
- **critics** — deterministic widget critics run; required critics must `pass`; safety critics are
  escalate-only (ADR-011, `02 §8 #5`). `waivable` flags follow `task_gate_results`.
- **approve → pin** — on human approval, `widgets.current_version_id` points at the pinned
  `widget_versions` row.
- **content-addressed revert** — a post-pin edit to `widget_versions.source` reverts the widget to
  `proposed` and re-enters the gate (CR-04).

### 4. CSP + sandbox policy object **[D shape / Open bytes]**

Per-widget policy is stored in **`widget_versions.sandbox_policy` (JSONB)** — "CSP / allowed board-API
scopes for this widget" (`schema.sql` line 253). The **shape** is frozen; the **exact CSP directive set
is NEEDS-PROTOTYPE** (CR-20):

```json
{
  "policy_version": "1.0",
  "csp": { "default-src": "'none'", "script-src": "'self'",
           "connect-src": "'none'", "frame-ancestors": "'self'" },   // [I] illustrative — exact set OPEN
  "bridge_allowlist": ["getTasks", "getGateStatus", "requestGate", "subscribe"],  // [D] frozen
  "iframe": { "sandbox": ["allow-scripts"] }                          // no allow-same-origin, no allow-top-navigation
}
```
`connect-src: 'none'` encodes "no arbitrary `fetch`"; the absence of `allow-same-origin` encodes "no
DOM outside the iframe." The `bridge_allowlist` is frozen at the four types; the surrounding CSP bytes
are the prototype deliverable.

### 5. Layout time-travel **[D]**

A widget/layout rollback replays `space_versions.version_no` (or `widget_versions.version_no`) — it
**restyles the layout/definition only**. See Invariants. (Versions are `space_versions` /
`widget_versions`; task history is not a widget concern.)

## Invariants & forbidden operations

1. **A widget may never write `approved_at` or submit a gate outcome.** — anchor **02 §8 #2** ("a human
   always sets `approved_at`") + **ADR-011** (five outcomes are the trusted human path). *Forbidden:* a
   widget message that approves/rejects/waives a task, or any bridge route to `tasks.approved_at` /
   `task_gate_results`. `requestGate` may only *raise the gate UI to the human*. **This is the
   load-bearing widget forbidden-op.**

2. **A widget reaches the engine only through the board-API bridge.** — anchor **ADR-002** / **ADR-012**
   ("agent-authored widgets run in a locked iframe reaching the engine only through the board API") +
   **02 §8 #3** ("widgets are iframe-sandboxed"); CR-20. *Forbidden:* arbitrary `fetch`/`XHR`/WebSocket
   from the widget, direct DB access, DOM access outside the iframe, top-level navigation, or any
   message type outside the four-item allowlist.

3. **Time-travel restyles layout, never task truth.** — anchor **ADR-002** ("time-travel versions the
   layout, never task records … must never mutate task truth"); `schema.sql` line 208. *Forbidden:* a
   widget version rollback that writes `tasks`, `task_gate_results`, or `task_events`. Rollback acts on
   `space_versions.definition` / `widget_versions` only.

4. **A widget cannot cross `board_id`.** — anchor **02 §8 #3** + CR-02. The host injects `board_id`
   into every brokered call; the widget never supplies it. *Forbidden:* a widget reading or rendering
   another board's data; the iframe is scoped to one board.

5. **A new or edited widget is a gated capability expansion.** — anchor **02 §8 #6** ("capability
   expansion is itself gated … content-addressed and manifest-enforced") + CR-04. *Forbidden:* a widget
   rendering at `pinned` without passing critics + human approval; a post-pin source edit that keeps
   `pinned` status instead of reverting to `proposed`.

## Versioning rule

- Each widget revision is a monotone **`widget_versions.version_no`** (`UNIQUE (widget_id,
  version_no)`, `schema.sql` line 256); the live revision is `widgets.current_version_id`.
- **`sandbox_policy` is versioned *with* the widget** — every `widget_versions` row carries its own
  policy, so a rollback restores that version's policy too (no policy drift between source and CSP).
- **Pin/revert:** approval pins (`current_version_id` → new row); a layout rollback repoints
  `current_version_id` at an earlier version (read-only replay of `definition`).
- **Content-addressed revert:** any post-pin edit to `source` (and therefore the version's hash)
  auto-reverts the widget to `proposed` and re-enters the gate (CR-04). A widget can never silently
  change behavior under a pinned status.

## Open questions for a human

1. **CR-20 — exact CSP header set + full bridge message semantics** is **NEEDS-PROTOTYPE**. The
   four-type allowlist and the iframe sandbox stance are frozen; the precise CSP directive string,
   per-message param schemas, error envelope, and handshake/timeout are the prototype deliverable
   (ROADMAP Phase 2 §283-293).
2. **`subscribe` cursor sharing** — whether a widget's `subscribe` rides the same `task_events.id`
   event-delta cursor as the board-api stream, or a filtered per-widget projection (ties to board-api
   Open question #1).
3. **Per-widget critic set + `waivable` flags** — which deterministic critics gate a widget (e.g. a
   CSP-conformance critic, an "uses only allowlisted messages" critic, an ISA-101-palette critic) and
   which are `waivable`. The taxonomy exists (ADR-011) but the widget-specific critic list is not yet
   enumerated.
4. **Layer model mapping** — Space Agent's L0/L1/L2 widgets map to TALOS core / client-scoped /
   user-scoped (`ROADMAP.md` §95); whether scope affects the allowed policy is unresolved.
