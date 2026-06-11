# Space Agent — Technical Deep-Dive Notes

> Research date: 2026-06-11  
> Source: `agent0ai/space-agent` — MIT License  
> Purpose: Inform TALOS cockpit / widget layer design. Code + patterns eligible for porting.

---

## Executive Summary

| Area | Key Finding | TALOS Disposition |
| :--- | :--- | :--- |
| **Spaces** | YAML-backed containers; grid of widgets; per-user home dir | Adopt: `spaces` + `space_versions` already in `engine/schema.sql` |
| **Widget system** | JS function + YAML manifest; direct DOM render; grid cells | Adopt: the canonical TALOS widget format |
| **Grid layout** | 1-24 col × 1-24 row cells, ~85px/cell; infinite pan canvas | Adopt: grid engine wholesale |
| **Kanban** | **Not built-in** — implementable as a custom widget | Build as first-party TALOS widget |
| **Gantt / timeline** | **Not built-in** — SVG/Canvas widget, D3 feasible | Build as first-party TALOS widget |
| **Time-travel** | Git-backed whole-`~/` versioning via `isomorphic-git` | Adopt: space/widget rollback; integrate with TALOS task event log |
| **Agent-authored widgets** | `upsertWidget()` API; turn-staged editing; JS compiler validation | Adopt: gate-bound version: propose → sandbox-render → critics → approve → pin |
| **Sandbox** | NOT strict — widgets run in main browser context | TALOS must add strict CSP + postMessage bridge as per Contract 4 |
| **Tech stack** | Alpine.js + Node.js + Electron; no external DB needed | Adopt Alpine.js for cockpit shell; extend with Postgres backend for TALOS data |
| **DOX** | AGENTS.md hierarchy is **mandatory**, not optional | Already adopted in this repo |

---

## 1. What Spaces Are

**File:** `app/L0/_all/mod/_core/spaces/storage.js` (lines 370-479)

A Space is a persistent, self-contained workspace container. It holds widgets, a layout, agent instructions, and metadata.

**Space schema (YAML, `space.yaml`):**

```yaml
schema: spaces/v2
id: <uuid>
title: "My Workspace"
icon: dashboard          # Material Symbols ligature name
icon_color: "#4A90D9"
created_at: 2026-06-11T10:00:00Z
updated_at: 2026-06-11T10:00:00Z
agent_instructions: |
  This space is for the ACME-PACK-01 migration project.
  Focus on packager control logic.
layout_order: [widget-a, widget-b, widget-c]
sizes:
  widget-a: "6x3"
  widget-b: "4x5"
minimized: [widget-c]
positions:            # Optional: camera-relative grid coords
  widget-a: "0,0"
  widget-b: "7,0"
```

**Space CRUD API:**

```javascript
space.spaces.createSpace({ title?, icon?, iconColor?, agentInstructions?, id? })
space.spaces.readSpace(spaceId)
space.spaces.listSpaces()        // enumerates ~/spaces/<id>/space.yaml
space.spaces.saveSpaceMeta(...)  // title, icon, instructions
space.spaces.saveSpaceLayout(...)// widgetIds, positions, sizes, minimized
space.spaces.duplicateSpace(spaceId)
space.spaces.removeSpace(spaceId)
```

**Persistence:** `~/spaces/<spaceId>/space.yaml` (YAML file). No database. Git-versioned alongside everything else in `~/`.

**TALOS mapping:** The `spaces` table in `engine/schema.sql` stores the same data relationally. `space_versions` captures the layout history. `agent_instructions` becomes `board_instructions` in TALOS (same concept: per-space context injected into the planner's system prompt).

---

## 2. Widget System

**Files:** `app/L0/_all/mod/_core/spaces/widget-sdk-core.js`, `constants.js`, `space-runtime.d.ts`

### Widget Schema (YAML, `<widgetId>.yaml`)

```yaml
schema: space-widget/v1
id: task-board
name: Task Board
cols: 8           # Grid columns (1-24)
rows: 5           # Grid rows (1-24)
metadata:
  author: talos
  version: "1.0"
  description: "TALOS execution task board"
renderer: |-
  async (parent, currentSpace, context) => {
    // JavaScript source — runs in browser
    const tasks = await fetchTasks(context)
    render(parent, tasks)
    return () => { /* cleanup */ }
  }
```

**Size presets:** `small` (4×2), `medium` (6×3), `large` (8×4), `tall` (4×5), `full` (12×4).

**One grid cell ≈ 85 px** (5.3 rem at 16px root). A "6×3" widget is ~510 px × 255 px.

### Widget Renderer Signature

```javascript
// Function signature: (parent, currentSpace, context) => cleanup | void
async (parent, currentSpace, context) => {
    // Direct DOM manipulation into parent element
    const el = document.createElement('div')
    el.textContent = 'Hello from widget'
    parent.appendChild(el)
    
    // Optional cleanup function returned
    return () => { el.remove() }
}
```

`parent` is the inner DOM node inside the widget card. Renderer owns it entirely.

### Context Object

```javascript
context = {
    widget: {
        id: string,
        metadata: object,   // Persisted per-widget config
    },
    space: { /* space metadata */ },
    paths: {
        root:    "~/spaces/<spaceId>/",
        data:    "~/spaces/<spaceId>/data/",
        assets:  "~/spaces/<spaceId>/assets/",
        scripts: "~/spaces/<spaceId>/scripts/",
        widget:  "~/spaces/<spaceId>/widgets/<widgetId>/",
    },
    import: (specifier) => import(`...scripts/${specifier}`),
    // globalThis.space — full space runtime
}
```

### Widget Lifecycle

```
upsertWidget()
  → Renderer source validated (JS compilation check)
  → YAML file written to ~/spaces/<id>/widgets/<widgetId>.yaml
  → Widget mounted: outer card + inner [data-widget-body] render target
  → renderer(parent, currentSpace, context) called
  → cleanup fn stored
  
patchWidget(widgetId, { edits })   → line-based or snippet edit
renderWidget(widgetId, newSrc)     → full renderer rewrite
reloadWidget(widgetId)             → re-runs renderer in-place
removeWidget(widgetId)             → cleanup fn called; YAML deleted
```

### Agent-Authored Widgets

Agents create/modify widgets via:

```javascript
space.spaces.upsertWidget({
    spaceId: "my-space",
    widgetId: "my-widget",   // omit for auto-generated ID
    name: "My Widget",
    cols: 6, rows: 3,
    renderer: "async (parent) => { ... }",
    metadata: { author: "talos-agent" }
})

// Batch install
space.spaces.upsertWidgets({ spaceId, widgets: [...], resetCamera: true, refresh: true })
```

**Turn-staged editing protocol** (from `spaces/AGENTS.md`):

1. Call `listWidgets()` or `readWidget()` — get numbered readback.
2. Analyze on that turn (do not edit).
3. Next turn: make edits using numbered line ranges as anchors.
4. Never echo line numbers inside patch `content`.

This prevents race conditions where an agent overwrites the widget it just read.

**TALOS gate addition:** Agent-authored widgets enter `proposed` state, not `pinned`. Critics validate:
- JS syntax compiles clean.
- No `fetch()` calls outside the allowed list.
- No `localStorage`/`sessionStorage`/`indexedDB` access.
- No access to `window.parent` or `window.opener`.
- `postMessage` calls use only the allowed board-API message types.

---

## 3. Kanban Board — Not Built-In, Must Be Built

Space Agent has **no native kanban board**. Tasks are not a first-class concept in the base framework.

**What exists:**

- The widget system supports arbitrary layouts — columns, rows, drag-drop.
- Widgets can call any backend API via `fetch()`.
- Widget metadata persists arbitrary JSON.

**TALOS kanban implementation plan:**

The kanban widget is a first-party TALOS widget. It connects to the Hermes-derived board API, not the Space Agent file API.

```javascript
// TALOS Kanban Widget (first-party)
async (parent, currentSpace, context) => {
    // Fetch tasks from TALOS board API (via postMessage bridge)
    const tasks = await boardApi.getTasks({ boardId: context.space.id })
    
    // Render columns: Backlog | In Progress | Review | Done
    const board = renderKanban(parent, tasks)
    
    // Gate-aware drag-drop: move to 'review' posts a gate request, not a direct update
    board.onDrop((taskId, newStatus) => {
        boardApi.requestGate({ taskId, targetStatus: newStatus })
    })
    
    // Subscribe to SSE for live updates
    const cleanup = boardApi.subscribe(({ type, payload }) => {
        if (type === 'task_updated') board.refresh(payload)
    })
    return cleanup
}
```

**Columns for TALOS kanban:**

| Column | Hermes Status | TALOS Addition |
| :--- | :--- | :--- |
| Backlog | `todo` | — |
| Claimed | `in_progress` | shows claim lock owner |
| Review Gate | `review` | shows gate outcome history |
| Done | `done` | shows approved_by / approved_at |
| Blocked | `error` | shows circuit breaker count |

---

## 4. Gantt / Timeline — Must Be Built

Space Agent has no native Gantt chart or timeline view.

**What is feasible with the widget system:**

A Gantt widget is implementable using:
- SVG or Canvas rendering inside the widget `parent` element.
- D3.js or a lightweight Gantt library loaded via `context.import()`.
- Task data from the TALOS board API.
- Dependencies from `task_links` table.

**Recommended approach for TALOS v1:**

Start with a simpler **timeline view** (horizontal bars per task, no dependency arrows) before adding dependency Gantt arrows. The board API needs to expose:
- Task `scheduled_start` and `scheduled_end` (add to `engine/schema.sql`).
- `task_links` as a dependency list per task.

**TALOS Gantt widget (first-party, deferred to v1.1):** Prioritize kanban first. Gantt is the Phase 2 cockpit widget.

---

## 5. Other PM Views — Gap Analysis

| View | Status | Recommendation |
| :--- | :--- | :--- |
| Kanban board | Not built-in | First-party widget, build for v1.0 |
| Timeline / Gantt | Not built-in | First-party widget, build for v1.1 |
| Task detail panel | Not built-in | First-party widget, build for v1.0 |
| Gate audit log | Not built-in | First-party widget (event feed from `task_events`) |
| Agent execution log | Not built-in | First-party widget (SSE stream from dispatcher) |
| Worker status panel | Not built-in | First-party widget (claim lock + heartbeat) |
| Burndown chart | Not built-in | Third-party widget, deferred |
| Risk heatmap | Not built-in | Third-party widget, deferred |

The widget system gives TALOS the flexibility to build any of these progressively. The core investment is in the **board API** (Contract 1) and the **postMessage bridge** — once those are solid, widgets are composable.

---

## 6. Time-Travel / Version History

**Files:** `app/L0/_all/mod/_core/time_travel/store.js`, `server/lib/git.js`

Space Agent versions the **entire user home directory (`~/`)** using `isomorphic-git` on the server side. Every file write triggers a scheduled commit.

**API:**

```javascript
space.api.gitHistoryList(path?, limit?)
  // Returns: { commits[], currentHash, enabled, hasMore }

space.api.gitHistoryDiff(path, commitHash?, filePath?)
  // Returns: { patch, file, hash, shortHash, backend }

space.api.gitHistoryPreview(path, commitHash?, operation?)
  // operation: "travel" | "revert"
  // Dry-run: shows affected files before applying

space.api.gitHistoryRollback(path, commitHash?)
  // Hard reset to a specific commit

space.api.gitHistoryRevert(path, commitHash?)
  // New commit that undoes a prior commit
  // Returns 409 Conflict if newer edits affect same files
```

**Scope:** Spaces, widgets, scripts, data files — all versioned together.

**Granularity:** Per-edit commits (backend decides scheduling). No explicit branching (single timeline per user).

**Conflict detection:** `revert` fails with 409 if a newer commit touches the same files. Caller must handle.

**TALOS mapping:**

Space/widget versions (layout definitions) map to TALOS's `space_versions` table. Task records are NOT versioned via git — they are append-only event-sourced via `task_events`. The two systems are complementary:

- **Space/widget history** → git (rollback the UI shell)
- **Task execution history** → `task_events` (immutable, append-only, replayable)

The cockpit's time-travel scrubber shows both: left panel scrubs through task events, right panel rolls the space layout back to how it looked at that moment.

---

## 7. Backend/Data Connection

**File:** `app/L0/_all/mod/_core/framework/js/api-client.js`

Space Agent widgets access data through the `space.api` object:

```javascript
// File I/O (relative to ~/; per-user isolated)
space.api.fileRead(path)
space.api.fileWrite(path, content)
space.api.fileDelete(path)
space.api.fileList(path, recursive?)
space.api.fileInfo(path)

// Git history
space.api.gitHistoryList(...)
space.api.gitHistoryRollback(...)

// Outbound HTTP (CORS proxy)
space.fetchExternal(url)   // proxied through /api/proxy

// User info
space.api.userSelfInfo()
space.api.health()
```

**Real-time:** Space Agent uses **polling only** — `setInterval()` in widgets, no built-in WebSocket or SSE push. The crypto ticker example polls every 60 seconds.

**TALOS must add:** WebSocket or SSE push for live task state updates. The board API (Contract 1) will expose an SSE endpoint that the kanban and log widgets subscribe to.

**CORS proxy:** `space.fetchExternal(url)` automatically retries blocked origins through `/api/proxy`. Useful for external data in widgets (weather, metrics APIs).

---

## 8. Widget Sandbox — Security Gap

**Critical finding:** Space Agent widgets do NOT run in a strict sandbox. They execute directly in the browser's main JavaScript context.

**What IS isolated:**

- DOM target: each widget renders into its own `[data-widget-body]` element. CSS leakage between widgets is the main risk at the DOM level.
- File I/O: `space.api.fileRead/Write` enforces per-user `~/` root at the server. No widget can read another user's files.
- No inter-widget direct state access (no shared `window` variable by convention).

**What is NOT isolated:**

- Widgets can call any `fetch()` URL.
- Widgets can access `window`, `document`, `localStorage`, `sessionStorage`.
- Widgets can interact with the DOM outside their render target.
- Widgets can inject scripts via dynamic `<script>` elements.

**TALOS requirement (Contract 4):**

TALOS must add a strict CSP layer and a postMessage bridge before TALOS widgets can be considered safe for multi-client deployment. The proposed → pin gate evaluates a widget against these policies before it becomes active.

**Recommended sandbox architecture:**

```
Widget code runs inside <iframe sandbox="allow-scripts">
  │
  └── postMessage bridge ←→ cockpit shell
          │
          └── Board API proxy (allowlist of message types)
                  │
                  └── TALOS board API (Postgres / SSE)
```

The widget gets:
- A `TALOS_API` object injected via postMessage setup.
- Methods: `getTasks()`, `getGateStatus()`, `requestGate()`, `subscribe()`.
- Nothing else. No `fetch()` to arbitrary URLs. No direct DOM access outside the iframe.

---

## 9. Tech Stack

**Frontend:**

| Technology | Role |
| :--- | :--- |
| Alpine.js | Reactive data binding (stores, `x-data`, `x-model`, `x-for`) |
| ES modules | No bundler — loads directly in browser |
| Plain CSS | Modular by folder; no framework |
| SSE/fetch | No bundled WebSocket client |

**Backend:**

| Technology | Role |
| :--- | :--- |
| Node.js v20+ | HTTP server + router |
| `isomorphic-git` | Server-side git operations (time-travel) |
| `archiver` | ZIP export of spaces |
| `electron-updater` | Desktop app update mechanism |
| Custom router | Lightweight, not Express |
| Session auth | Per-user `~/` isolation |

**Desktop app:** Electron + custom build chain. Bundles frontend + Node.js server. macOS, Windows, Linux installers.

**TALOS additions to the stack:**

| Technology | Role |
| :--- | :--- |
| Postgres | Task records, gate results, audit trail |
| Neo4j | Knowledge graph (federated to NEXUS) |
| pgvector | Memory area embeddings |
| Redis | Working memory, claim locks, heartbeats |
| FastAPI + SSE | Replace Node custom router for board API |

TALOS retains Alpine.js for the cockpit shell. The widget grid engine is ported from Space Agent. The backend data layer is replaced with the TALOS polyglot store.

---

## 10. DOX Integration

**Files:** `AGENTS.md` (root), `app/AGENTS.md`, `app/L0/_all/mod/_core/spaces/AGENTS.md`

Space Agent treats DOX as **mandatory infrastructure**, not optional documentation.

The root `AGENTS.md` specifies:
- AGENTS.md files are binding work contracts.
- Agents must walk from root to target file, reading every AGENTS.md on the path, before editing.
- Child docs may be stricter but not weaker than their parent.
- The "Child DOX Index" section in every AGENTS.md enumerates direct children with one-line scope descriptions.

The spaces module AGENTS.md is 296 lines and documents:
- Persistence contract (what constitutes a canonical space representation)
- Widget schema invariants
- Layout engine rules
- Onboarding flow and example widget standards
- Agent editing protocol (turn-staged)
- Verification steps

**TALOS already uses this hierarchy.** The `_Tools/AGENTS.md` chain governs the TALOS repo the same way. No adoption work needed — it's already in place.

---

## 11. Overall Architecture

### Repo Structure

```
space-agent/
  app/
    L0/_all/mod/_core/
      spaces/          → Spaces + widgets: CRUD, layout, SDK, storage
      framework/js/    → Alpine stores, API client, utilities
      time_travel/     → Git history UI
      onscreen_agent/  → Chat/LLM interface
      admin/           → Admin panel
      dashboard_welcome/ → Home/launcher
    L1/                → Group-level overrides
    L2/                → Per-user overrides
    space-runtime.d.ts → Full TypeScript type definitions (reference)
  server/
    api/               → API endpoint handlers
    lib/               → Auth, git, file watch, storage services
    router/            → HTTP router
    app.js             → Server bootstrap
  commands/            → CLI (user, serve, get, set, supervise)
  packaging/           → Electron build chain
  AGENTS.md            → Root DOX contract
```

### URL Routing

| Route | Target |
| :--- | :--- |
| `#/` | Home / launcher (dashboard_welcome) |
| `#/spaces?id=<spaceId>` | Specific space |
| `#/spaces` | Spaces browser |
| `#/admin` | Admin time-travel panel |

### Request Flow

```
Browser fetch → /api/<endpoint>
  → server/router → server/api/<handler>.js
  → File auth enforcement (per-user ~/root)
  → Response streamed to browser
  → Alpine store reactivity updates UI
```

---

## Key Patterns for TALOS (Summary)

### Pattern 1 — Grid Widget Canvas

Infinite pan canvas. 1-24 col × 1-24 row grid. ~85px per cell. Each widget is a JS renderer + YAML manifest. Direct DOM render into isolated card. No framework inside widgets — plain JS only.

### Pattern 2 — Agent-Authored Widgets (Turn-Staged)

Agent reads widget source (numbered), analyzes, edits on the next turn using line/snippet patches. Full renderer rewrite is the fallback. Compilation validation before persistence.

TALOS gate adds: propose → sandbox-render → critics → human-approve → pin.

### Pattern 3 — File + Git as the Backing Store

YAML files for space/widget definitions. Git (`isomorphic-git`) for version history. Atomic writes prevent partial state. No database needed for basic operation. TALOS extends this with Postgres for task records and graph for knowledge.

### Pattern 4 — Layered Browser Customization (L0/L1/L2)

L0 = platform firmware (update-controlled). L1 = group customization. L2 = per-user. Each layer can override the one above. TALOS maps this to: L0 = TALOS core widgets, L1 = client-scoped widgets, L2 = user-scoped widgets.

### Pattern 5 — `space.api` as the Abstraction Layer

Widgets never touch the filesystem or backend directly. They go through `space.api`. TALOS replaces the Space Agent `space.api` implementation with a board-API proxy that adds the gate and scope enforcement. Widget code is unchanged; the adapter is swapped.

### Pattern 6 — Time-Travel as First-Class Feature

All state versioned via git. `rollback` (hard reset) and `revert` (new undo commit) as distinct operations. Conflict detection on revert. TALOS cockpit scrubber exposes this alongside the task event replay.
