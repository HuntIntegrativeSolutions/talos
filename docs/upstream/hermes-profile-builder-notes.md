# Hermes Agent Profile Builder & Web Dashboard — Technical Deep-Dive Notes

> **Status:** Research notes · 2026-06-11
> **Purpose:** Catalog the Hermes Agent v0.16.0 Profile Builder and web dashboard architecture. Extract patterns for TALOS cockpit design (Phase 5), profile management, plugin system, and the profile → workspace → sandbox isolation model.
> **Sources:** Official docs (`hermes-agent.nousresearch.com/docs/`), GitHub source (`NousResearch/hermes-agent`), AlphaSignal, MarkTechPost, Fast.io guide.

---

## Executive Summary

| Area | Finding | TALOS Disposition |
| :--- | :--- | :--- |
| **Profile Builder** | 5-step wizard (`/profiles/new`): Identity → Model → Skills → MCPs → Review. Writes standard `config.yaml`, `.env`, `SOUL.md`. | **Reference UX** for TALOS Phase 5 cockpit profile-management widget |
| **Profile isolation** | Separate directory per profile: config, memory, sessions, skills, cron, plugins, state DB. Command alias auto-created. | **Validates** TALOS worker session-key isolation design |
| **Dashboard tech stack** | React 19 SPA + TypeScript + Tailwind CSS v4 frontend, FastAPI/Uvicorn backend, compiled frontend ships inside Python package. | **Pattern** for TALOS cockpit frontend architecture |
| **Plugin system** | 3 drop-in layers: YAML themes, UI plugins (manifest+JS), backend plugins (FastAPI router). No fork needed. | **Adopt pattern** — TALOS adds the propose→critics→approve→pin gate |
| **REST API** | `/api/status`, `/api/config`, `/api/sessions/search`, `/api/env`, `/api/analytics/usage`, `/api/cron/jobs`, `/api/skills/toggle` | **Board API contract** reference for TALOS Contract 1 |
| **Token locks** | Second gateway using same bot token is blocked with error naming the conflict | **Reference** for TALOS `task_claim_lock` mechanism |
| **Chat tab** | xterm.js + WebGL rendering full TUI via WebSocket `/api/pty` | — |
| **Limitations** | Profiles do not sandbox filesystem. Non-loopback dashboard bind fails closed without auth. | **Matches** TALOS's own security defaults |

---

## 1. Profile Builder — Full Detail

### Release Context

- **Announced:** June 11, 2026 (MarkTechPost, AlphaSignal)
- **Ship vehicle:** Hermes Agent v0.16.0 (June 5, 2026 — "The Surface Release")
- **GitHub:** 191k stars, 33.2k forks, MIT license, Python 82.5% + TypeScript 13.5%

### Access Flow

```
hermes dashboard  →  http://127.0.0.1:9119
                  →  Profiles sidebar page
                  →  "Build" button in header (distinct from quick "Create" button)
                  →  /profiles/new wizard
```

### The 5-Step Wizard

| Step | What you configure | What it writes to disk |
| :--- | :--- | :--- |
| **1. Identity** | Agent name + description | Name → `~/.local/bin/<name>` shell alias. Description → profile tagline. Deeper personality in `SOUL.md` |
| **2. Model / Provider** | LLM + inference backend | `config.yaml`: `model.default`, provider, base URL, inference settings |
| **3. Skills** | Toggle built-in toolsets on/off; search + install from Skills Hub | `config.yaml` toolsets section; skills downloaded to `~/.hermes/skills/` |
| **4. MCP Servers** | Add by URL (HTTP/SSE) or local command (stdio). One-click catalog installs with inline API key prompts that write to `.env` | `config.yaml` `mcp_servers` section |
| **5. Review** | Confirm everything before writing to disk | Writes all profile files atomically |

### Supported Providers

Nous Portal, OpenRouter (200+ models), NovitaAI, NVIDIA NIM, Xiaomi MiMo, z.ai/GLM, Kimi/Moonshot, MiniMax, Hugging Face, OpenAI, or any OpenAI-compatible endpoint.

---

## 2. Profile Structure (On Disk)

```
~/.hermes/profiles/<name>/
├── config.yaml      # model, provider, toolsets, mcp_servers, all 150+ settings
├── .env             # API keys, bot tokens (grouped by category)
├── SOUL.md          # personality, instructions, behavioral guidance
├── memory/          # separate MEMORY.md, USER.md per profile
├── sessions/        # separate SQLite session database (FTS5)
├── skills/          # separate skill installs (from bundled + Skills Hub)
├── cron/            # separate scheduled jobs
├── plugins/         # separate plugin state
└── state.db         # separate SQLite state DB
```

### Command Alias

Every profile auto-creates `~/.local/bin/<name>`:
```bash
coder chat                     # hermes -p coder chat
coder setup                    # configure coder's API keys, model, gateway
coder gateway start            # start coder's gateway as separate process
coder doctor                   # check coder's health
coder skills list              # list coder's skills
coder config set model.default anthropic/claude-sonnet-4
```

Alias is equivalent to `hermes -p <name> <subcommand>`.

### Sticky Default

```bash
hermes profile use coder       # sets persistent default
hermes chat                    # now targets coder
hermes profile use default     # switch back
```

Like `kubectl config use-context`. Prompt shows `coder ❯` instead of `❯`.

### Profile Creation Options

| Command | What it does |
| :--- | :--- |
| `hermes profile create <name>` | Blank profile, seeds bundled skills |
| `--description "<role>"` | Sets kanban worker description for routing |
| `--clone` | Copies `config.yaml`, `.env`, `SOUL.md` from current profile |
| `--clone-all` | Copies everything — config, keys, SOUL, memories, sessions, skills, cron, plugins |
| `--clone-from <name>` | Copies from a named profile instead of current |
| `hermes setup --portal` | Quick model + tool configuration inside a new profile |

---

## 3. Profile vs Workspace vs Sandbox

| Concept | Purpose | Filesystem access |
| :--- | :--- | :--- |
| **Profile** | State directory (config, memory, sessions, skills, cron, plugins) | Agent has same access as user on `local` backend |
| **Workspace** | Working directory for terminal commands (`terminal.cwd`) | `cwd: "."` = the directory Hermes was launched from, not the profile dir |
| **Sandbox** | Limits filesystem access | **Profiles do not sandbox** — set explicit absolute `terminal.cwd` to constrain |

**Key limitation for TALOS:** Hermes profiles provide state isolation but not filesystem isolation. TALOS must add its own sandbox layer (OpenClaw's Docker sandbox pattern with `network:none` + `readOnlyRoot:true`) on top of session-key isolation.

---

## 4. Dashboard Tech Stack

### Backend

| Component | Technology |
| :--- | :--- |
| HTTP server | FastAPI + Uvicorn |
| Install extra | `pip install 'hermes-agent[web]'` |
| PTY support | `pip install 'hermes-agent[web,pty]'` or `[all]` |
| Auto-build | If `npm` available, frontend builds on first `hermes dashboard` |

### Frontend

| Component | Technology |
| :--- | :--- |
| Framework | React 19 SPA |
| Language | TypeScript |
| Styling | Tailwind CSS v4 |
| Terminal rendering | xterm.js + WebGL renderer |
| Terminal resize | `@xterm/addon-fit` |
| Build output | Compiled static files bundled inside Python package |

### CLI Launch Options

| Flag | Default | Description |
| :--- | :--- | :--- |
| `--port` | `9119` | Web server port |
| `--host` | `127.0.0.1` | Bind address |
| `--no-open` | off | Don't auto-open browser |
| `--insecure` | off | Allow non-localhost (exposes API keys — use firewall + auth) |
| `--tui` | off | Enable Chat tab (embedded TUI) |

Default bind is loopback-only. Non-loopback bind **fails closed** unless auth provider is configured.

### Dashboard REST API

Same endpoints the UI uses, available for scripts:

| Method | Endpoint | Purpose |
| :--- | :--- | :--- |
| GET | `/api/status` | Agent version + gateway state |
| GET | `/api/sessions/search?q=...` | FTS5 full-text session search |
| GET/PUT | `/api/config` | Read/write full `config.yaml` |
| PUT | `/api/env` | Set environment variables in `.env` |
| GET | `/api/analytics/usage?days=30` | Token/cost data |
| POST | `/api/cron/jobs` | Create a scheduled job |
| PUT | `/api/skills/toggle` | Enable/disable a skill |

---

## 5. Plugin Architecture (3 Drop-In Layers)

All three are runtime drop-in — no fork, no `npm run build`, no patching.

### Layer 1 — Themes

- YAML files in `~/.hermes/dashboard-themes/<name>.yaml`
- 3-layer palette: `background` / `midground` / `foreground` — auto-derived colors via CSS `color-mix()`
- Every field optional (missing keys fall back to built-in `default` theme)
- 6 built-in themes: Hermes Teal, Hermes Teal Large, Midnight, Ember, Mono, Cyberpunk
- Appears in theme switcher immediately

### Layer 2 — UI Plugins

- Directory with `manifest.json` + JavaScript bundle
- Can: register a tab, replace a built-in page, augment via page-scoped slots, inject into named shell slots

### Layer 3 — Backend Plugins

- Python file inside the plugin directory exposing a FastAPI `router`
- Routes mounted at `/api/plugins/<name>/`
- Called from the plugin's UI frontend

---

## 6. Complete Dashboard Pages (14 Pages)

| Page | Key Features |
| :--- | :--- |
| **Status** | Landing page. Agent version, gateway state (running/stopped/PID/platforms), active sessions (last 5 min), recent 20 sessions. 5s auto-refresh. |
| **Chat** | Full TUI via xterm.js + WebGL over WebSocket `/api/pty`. Resume sessions from Sessions tab. Close tab → PTY reaped. POSIX only (Linux/macOS/WSL2). |
| **Config** | Form editor for `config.yaml` — 150+ fields auto-discovered, tabbed categories: model, terminal, display, agent, delegation, memory, approvals. Dropdowns for enums, toggles for booleans. Export/import JSON. |
| **API Keys** | `.env` manager: grouped by LLM Providers, Tool API Keys, Messaging Platforms, Agent Settings. Redacted previews, signup links, set/update/delete. Advanced keys behind toggle. |
| **Sessions** | Full FTS5 search across message content. Color-coded by role. Collapsible tool-call blocks. Stats bar: total/active/archived/messages/per-source. Rename, Export JSON, Prune, Delete. |
| **Logs** | Filter by source file, severity (ALL/DEBUG/INFO/WARNING/ERROR), component (gateway/agent/tools/cli/cron). Choose lines (50/100/200/500). Auto-refresh live tail (5s). |
| **Analytics** | 7/30/90 day periods. Summary cards: total tokens in/out, cache hit %, cost, session count + daily avg. Stacked bar chart, per-model breakdown table. |
| **Cron** | Create named jobs: prompt + cron expression + delivery (local/Telegram/Discord/Slack/email). Pause/Resume/Edit/Trigger now/Delete. |
| **Skills** | Browse installed skills (grouped by category). Toggle on/off. Toolsets: built-in tools with active/inactive/requirements. Browse hub: search + install by ID with live log. "Update all" button. |
| **MCP** | Add HTTP/SSE or stdio server. Enable/disable (config retained). Test (connect → list tools → disconnect). Remove. Catalog install with inline key prompts. |
| **Webhooks** | Dynamic subscriptions. Name, description, event filter, delivery target, optional direct-delivery mode, agent prompt. Shows route URL + one-time HMAC secret. Hot-reloaded (no restart). |
| **Pairing** | Approve/revoke messaging users. Pending requests, approved users, clear pending. |
| **Channels** | Connect 14+ platforms from browser. Per-platform form with secret password inputs, setup guide links. Enable/disable, Test, Restart gateway. |
| **System** | Host OS, kernel, arch. |

---

## 7. Gateway & Token Isolation

Each profile runs its own gateway as a **separate process** with its own bot token:

```bash
coder gateway start              # starts coder's gateway
assistant gateway start          # starts assistant's gateway (separate process)
```

**Token locks:** If two profiles accidentally share the same bot token, the second gateway is **blocked** with a clear error naming the conflicting profile. Supported for Telegram, Discord, Slack, WhatsApp, Signal.

**Persistent services:**
```bash
coder gateway install            # creates hermes-gateway-coder systemd/launchd service
```

In the official Docker image: per-profile gateways supervised by s6-overlay. `hermes profile create <name>` auto-registers an s6 service slot. `hermes -p <name> gateway start/stop/restart` dispatches to `s6-svc` — crashes auto-restart.

---

## 8. Skills Hub Integration

- Browse and install skills from `agentskills.io` open standard
- Built-in: 40+ skills (MLOps, GitHub, diagramming, note-taking, security)
- Skills are `SKILL.md` files with name, description, procedure
- Agent reads short descriptions cheaply; loads full content only when a task needs it
- Auto-created skills: agent writes new skills when it solves hard problems (the learning loop)
- Community hubs: search across all sources, install by identifier with live install log
- The "Update all" button refreshes all installed skills

---

## 9. TALOS-Specific Analysis

### What to Adopt Directly

| Hermes Feature | TALOS Equivalent | Notes |
| :--- | :--- | :--- |
| 5-step Profile Builder wizard | Phase 5 cockpit profile-management widget | Same user flow: Identity → Model → Skills → MCPs → Review |
| 3-layer theme system (YAML + `color-mix()`) | TALOS cockpit theming | Industrial palette: charcoal, amber, safety-red. PlantPAx-inspired defaults. |
| Plugin architecture (UI tab + backend router) | Widget sandbox | TALOS adds propose→critics→approve→pin gate over the same pattern |
| REST API surface | Board API contract (Contract 1) | Model the GET/PUT patterns on Hermes's `/api/config` and `/api/sessions/search` |
| Token locks | `task_claim_lock` | Conflict detection naming the conflicting claim |

### What to Do Differently

| Hermes Limitation | TALOS Fix |
| :--- | :--- |
| Profiles don't sandbox filesystem | Add OpenClaw Docker sandbox (`network:none`, `readOnlyRoot:true`) on top of session-key isolation |
| Skill/MCP changes only take effect on next session or gateway restart | TALOS gate already forces this — a proposed skill is not loaded until approved |
| No plugin gate | TALOS requires all plugins/widgets to pass deterministic critics + human approve before they can render |
| Skill injection vulnerability | TALOS's propose→review→pin gate closes this (per BLUEPRINT §Trust boundaries) |

### Dashboard Pages TALOS Needs That Hermes Doesn't Have

- **Gate results viewer** — per-task critic verdicts with evidence URIs and human approval status
- **NEXUS review canvas** — deliverable beside PageRank-selected graph slice
- **Project economics gauge** — 3-axis budget burn (time/cost/iterations)
- **Milestone Gantt** — timeline of project phases with gate dependencies
- **Temporal replay** — scrub the task event log to see how a deliverable reached its gate
- **Risk heatmap** — schedule risk, critic failure prediction, budget burn rate

---

## 10. Open Questions for TALOS

- Should the TALOS cockpit share Hermes's `color-mix()` palette derivation, or use fixed industrial-safe colors?
- Plugin sandbox: does TALOS render widgets inside locked iframes (CSP `frame-src 'self'`), or does it use Hermes's postMessage bridge approach?
- Profile visibility: can one profile's tasks be visible from another on the same board, or is RLS the hard boundary?

---

## References

- Hermes Agent GitHub: https://github.com/NousResearch/hermes-agent
- Official Dashboard Docs: https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard
- Official Profiles Docs: https://hermes-agent.nousresearch.com/docs/user-guide/profiles/
- Extending the Dashboard: https://hermes-agent.nousresearch.com/docs/user-guide/features/extending-the-dashboard
- MarkTechPost (June 11, 2026): "Nous Research Ships Hermes Agent Profile Builder"
- AlphaSignal (June 11, 2026): "Nous Research's Hermes Agent Drops a Visual Builder for Multi-Agent Setup"
- Fast.io Guide: "Hermes Agent Web UI Dashboard Setup and Features Guide"
- TALOS BLUEPRINT.md v0.6 — §Cockpit, §Trust boundaries, §Contracts
- TALOS `engine/schema.sql` — spaces/widgets tables (Phase 5)
