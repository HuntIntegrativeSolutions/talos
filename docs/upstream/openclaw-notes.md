# OpenClaw — Technical Deep-Dive Notes

> Research date: 2026-06-11  
> Source: `openclaw/openclaw` — MIT License  
> Purpose: Inform TALOS gateway layer design. Patterns only — zero ported code.

---

## Executive Summary

OpenClaw is a **multi-channel AI gateway** with a "hub-and-spoke" control-plane architecture. It places a gateway process between messaging platforms and an agentic runtime, enforcing tool policy, session routing, and optional sandboxing. The entire framework is designed for a **single-operator personal-assistant deployment** — it explicitly is NOT a secure multi-tenant boundary.

The most important finding for TALOS: OpenClaw's "loaded skill = trusted" hole is real and documented by the project itself. Skills inject directly into the agent's system prompt with no capability mediation, no signing, no manifest enforcement, no runtime verification. This is Phase 1 of their own security RFC (unimplemented). TALOS closes this hole.

| Aspect | OpenClaw | TALOS Addition |
| :--- | :--- | :--- |
| **Trust model** | Single operator; session key = routing, not auth | Per-client session keys = hard auth boundaries |
| **Skill loading** | Filesystem scan → system prompt injection, no gate | `propose → critics → human approve → pin` before injection |
| **Tool policy** | Static 7-layer pipeline, config-time | Same pipeline + per-skill capability grants enforced at runtime |
| **Proactive loops** | Cron/heartbeat use same tool policy as user turns | Same; rate-limited per 3-axis budget (ADR-009) |
| **Sandboxing** | Optional, per-tool, Docker/SSH backends | Required for write-class workers; no elevated-exec escape |
| **Multi-tenancy** | Not supported for adversarial users | `board_id` + RLS; hard client isolation by design |
| **Rate limits** | Auth-attempt throttling only; no token/cost budgets | 3-axis budget (tokens, time, tool-calls) per task |

---

## 1. The Gateway Architecture

**File:** `src/gateway/` (main control plane)

```
Messaging Channels (untrusted)
  WhatsApp, Discord, Telegram, Slack, Signal…
         │ TLS + AllowFrom policy
         ▼
GATEWAY (Control Plane — stays on HOST)
  • Auth (token / password / Tailscale / device-token)
  • Session routing: incoming message → binding → agent:session
  • Tool policy enforcement (7-layer pipeline)
  • Dangerous-tool surface filtering
  • Sandboxing orchestration
         │
         ▼
AGENT SESSIONS (per-agent isolation)
  • Session key: agent:<agentId>:<mainKey>
  • Tool allowlist / denylist per agent / profile
  • Skill filtering → system prompt injection
  • Model invocation + tool calls
         │
   ┌─────┴──────────────────┐
   │                        │
SANDBOX (optional)     HOST EXECUTION
Docker / SSH / Shell   (with approval)
Isolated tool exec     Elevated exec
```

**What the gateway does:**

1. **Auth** (`src/gateway/auth.ts`): token, password, Tailscale, device-token. Auth-attempt rate-limited per IP.
2. **Routing** (`src/gateway/call.ts`): incoming messages matched against `bindings` config → agent ID → session key. Default session key is `agent:<agentId>:main`.
3. **Tool resolution** (`src/gateway/tool-resolution.ts`): collects tools from core + plugins, then runs the 7-layer policy pipeline.
4. **Surface filtering** (`src/security/dangerous-tools.ts`): HTTP gateway surface denies a hardcoded set of dangerous tools by default (see §3). Loopback / embedded calls allow them with operator identity check.

**What the gateway does NOT do:**

- The gateway process itself is not sandboxed — it runs on the host.
- No per-user capability grants; only operator-level trust.
- Session keys do not provide an authorization boundary (by design).

---

## 2. Proactive Loops — Cron and Heartbeat

**Files:** `src/cron/`, `src/agents/embedded-agent.ts`

Proactive loops are **registered scheduler jobs** that trigger agent turns without a user initiating them.

**Cron job interface** (`src/cron/service-contract.ts`):

```typescript
add(input: CronAddInput)    // Register a new job
run(id, mode?)              // Trigger immediately
wake(mode, text, sessionKey?) // Trigger with message
```

**Schedule types:** `"at"` (one-time), `"every"` (interval), `"cron"` (5-field cron expression with timezone + optional stagger).

**Session targets:** `"main"` | `"isolated"` | `session:<customKey>`. Isolated sessions get their own key: `agent:<agentId>:cron:<jobId>:run:<timestamp>`.

**Key finding:** Proactive loops do **not** bypass tool policy. Cron turns route through the same `resolveEffectiveToolPolicy()` and `applyToolPolicyPipeline()` as user-initiated turns. The "separation" between proactive loops and privileged tools is **by surface** (HTTP vs embedded) and **by operator identity**, not by turn origin.

`GATEWAY_OWNER_ONLY_CORE_TOOLS = ["cron", "gateway", "nodes"]` — cron itself requires operator identity on HTTP surfaces.

**TALOS disposition:** The cron/heartbeat model is the right architecture for TALOS's gateway layer: proactive loops register as scheduled turns, use a restricted tool policy, and deliver results back to the board. TALOS adds: a 3-axis budget (tokens, elapsed time, tool-call count) per proactive loop, and a sandbox-required flag for any loop that writes files or calls external APIs.

---

## 3. Privileged Tools — Hardcoded Dangerous-Tool Set

**File:** `src/security/dangerous-tools.ts` (42 lines)

```typescript
// HTTP gateway default deny
const DEFAULT_GATEWAY_HTTP_TOOL_DENY = [
    "exec", "spawn", "shell",           // RCE
    "fs_write", "fs_delete", "fs_move", // file mutation
    "apply_patch",                       // arbitrary file rewrite
    "sessions_spawn", "sessions_send",   // cross-session injection
    "cron", "gateway", "nodes",          // control-plane actions
]

// Owner-only on gateway surfaces
const GATEWAY_OWNER_ONLY_CORE_TOOLS = ["cron", "gateway", "nodes"]
```

**Enforcement:**

1. HTTP surface (`src/gateway/tool-resolution.ts`): external callers cannot invoke exec/file/session tools even if agent policy allows them.
2. Operator scope (`src/gateway/method-scopes.ts`): owner-only tools are filtered for non-owner callers.
3. Elevated exec (`src/agents/bash-tools.exec.ts`): `tools.elevated` explicitly bypasses sandbox. Requires operator identity + optional approval prompt. This is intentional for the personal-assistant UX model.

**Critical gap:** All privileged tools are accessible to every agent turn (user, cron, webhook) through the same pipeline. There is no turn-origin–based privilege separation.

**TALOS disposition:**

- Adopt the hardcoded dangerous-tool deny list as TALOS's **global floor** (ADR-009: global "no live writes" is the floor).
- The elevated-exec bypass is explicitly NOT adopted — TALOS has no `elevated: { enabled: true }` equivalent. No agent ever writes to a live processor.
- Add a TALOS-specific deny: any tool whose capability profile is `write` requires a human gate result on record before execution (not just policy-allow).

---

## 4. The "Loaded Skill = Trusted" Hole

**Files:** `src/skills/loading/config.ts`, `src/skills/loading/source.ts`, `docs/gateway/security/index.md`

### How Skills Are Loaded

**Discovery roots** (in precedence):
1. Bundled: `openclaw/skills/` (in repo)
2. Workspace: `~/.openclaw/workspace/skills/` (agent-specific)
3. Extra dirs: `skills.load.extraDirs` config
4. ClawHub: downloaded via `openclaw skills install <name>`

**Filtering** (`src/skills/loading/config.ts:90-122`):

```typescript
function shouldIncludeSkill(params): boolean {
    const skillConfig = resolveSkillConfig(config, skillKey)
    if (skillConfig?.enabled === false) return false      // disable by name
    if (!isBundledSkillAllowed(entry, bundledAllowlist)) return false
    return evaluateRuntimeEligibility({
        os: entry.metadata?.os,
        requires: entry.metadata?.requires,  // binary, env var, config key
        hasEnv: (envName) => Boolean(process.env[envName] || skillConfig?.env?.[envName]),
    })
}
```

File permissions: symlink-target validation, path containment (workspace root), optional `security.installPolicy` (operator-provided pre-install command).

### The Vulnerability

Once a skill passes `shouldIncludeSkill()`, its Markdown body is **injected directly into the agent's system prompt**. There is:
- No capability manifest enforcement
- No code signing or author verification
- No runtime sandboxing of the injected instructions
- No per-skill tool grant (skill can request any tool the agent policy allows)

**Attack surface:**

A malicious or compromised skill can:
1. **Inject arbitrary instructions** into the system prompt (manipulate model behavior)
2. **Exfiltrate data** via tool calls to attacker-controlled endpoints (web_fetch to external URL)
3. **Escalate privileges** by directing the model to request powerful tools already in the agent's allowlist
4. **Manipulate memory/sessions** via injected narrative

**OpenClaw's own documentation** (`docs/gateway/security/index.md`):
> "Treat third-party skills as untrusted code."

Their RFC (#10890) proposes a 3-phase fix:
- Phase 1: Permission manifests (JSON declaring required tools, paths, domains) — partially implemented
- Phase 2: Author verification (GPG signing) — not implemented
- Phase 3: Runtime enforcement (Deno-like explicit grants) — not implemented

**TALOS disposition:**

This is the central pattern TALOS addresses. The TALOS gate layer adds:
- Skills carry a `capabilities` manifest (required tools, allowed domains, sensitivity level)
- Critics validate the manifest against the session's tool policy before injection
- Human approval required before a skill moves from `proposed` to `pinned`
- Pinned skill = the manifest is what was reviewed; the system prompt injection matches the manifest
- Any skill that was modified after pinning reverts to `proposed` automatically

---

## 5. Sandboxing Architecture

**Files:** `src/agents/sandbox/config.ts`, `docs/gateway/sandboxing.md`

Sandboxing is **optional** and **per-tool**, not per-turn or per-agent:

```typescript
{
  sandbox: {
    mode: "off" | "non-main" | "all",
    scope: "agent" | "session" | "shared",
    backend: "docker" | "ssh" | "openshell",
    workspaceAccess: "none" | "ro" | "rw",
    docker: {
      image: "...",
      network: "none",       // No egress by default
      readOnlyRoot: true,    // Mutation only via /tmp, /var/tmp, /run
    }
  }
}
```

**Sandbox modes:**

| Mode | Effect |
| :--- | :--- |
| `"off"` | All tools run on host |
| `"non-main"` | Non-main sessions sandboxed (cron, group chats); user DMs on host |
| `"all"` | Every session sandboxed |

**What gets sandboxed:** `exec`, `read`, `write`, `edit`, `apply_patch`, `process`, browser (optional). The gateway process itself is never sandboxed.

**Sandbox escape:** `tools.elevated` explicitly bypasses sandbox for host execution. Docker defaults to `network: "none"` (no egress, SSRF protection). `readOnlyRoot: true` prevents filesystem mutation outside `/tmp`.

**Key limitation:** Sandboxing prevents unauthorized **file/process access**, but a sandboxed agent with an injected malicious skill can still be manipulated to make dangerous tool calls if those tools are in the policy allowlist. Sandbox is defense-in-depth against tool misuse, not against prompt injection.

**TALOS disposition:**

- Adopt Docker backend as the TALOS worker execution environment.
- Set `network: "none"` as the default; allowlist specific external domains in the capability manifest.
- Reject the `tools.elevated` escape entirely — no worker ever gets a host-bypass path.
- Sandbox mode is `"all"` in TALOS (no exceptions for "main" sessions).

---

## 6. Tool Policy Pipeline

**File:** `src/agents/tool-policy-pipeline.ts`

A 7-layer pipeline; layers apply in order, first match wins:

```
Layer 1: Profile policy        (preset: "messaging", "minimal", custom)
Layer 2: Provider-profile      (per-model-provider overrides)
Layer 3: Global allow/deny     (tools.allow / tools.deny)
Layer 4: Global provider       (tools.byProvider.allow)
Layer 5: Agent allow/deny      (per-agent tools.allow)
Layer 6: Agent provider        (per-agent tools.byProvider.allow)
Layer 7: Group/sender policy   (per-sender tools.toolsBySender)
```

Each layer can specify:
- `allow`: whitelist
- `deny`: blacklist
- `group`: reference named plugin tool groups (`group:automation`, `group:runtime`, `group:fs`)
- `plugin-only`: restrict to plugin tools; core tools bypass

**Tool profiles:**

| Profile | Tools included |
| :--- | :--- |
| `"minimal"` | Read-only (web_fetch, sessions_list) |
| `"messaging"` | Text, image, message tools; no exec, no file mutation |
| Custom | User-defined allow/deny lists |

**Audit logging** (`src/agents/tool-policy-audit.ts`): tracks which layer blocked/allowed each tool; deduplicates warnings per session.

**Gap:** Tool policy is static per session (config-time). No per-skill capability grant. A skill can direct the agent to invoke any tool in the session's allowlist.

**TALOS disposition:**

- Adopt the 7-layer pipeline structure.
- Add an 8th layer: **per-skill capability grant** — at invocation time, check whether the skill's pinned manifest declares the requested tool. If not declared, deny even if the session policy allows.
- This turns the policy from "what is the agent allowed to do" to "what is THIS SKILL allowed to do in THIS context."

---

## 7. Session Management

**Files:** `docs/concepts/session.md`, `src/agents/`

**Session storage:**

```
~/.openclaw/agents/<agentId>/sessions/
  sessions.json           # session metadata
  <sessionId>.jsonl       # transcript (append-only)
```

**Session key format:** `agent:<agentId>:<mainKey>` (e.g., `agent:main:main`)

**DM isolation levels:**

| Level | Transcript isolation | Access control |
| :--- | :--- | :--- |
| `"main"` | All users share one transcript | None |
| `"per-channel-peer"` | Per user (recommended) | None — still shared authority |
| `"per-account-channel-peer"` | Per account + user | None |

**Critical finding:** Session key is **routing, not auth**. Multiple callers in the same session share context. `sessionKey` cannot be used as a security boundary. This is explicitly documented:

> "OpenClaw security guidance assumes a personal assistant deployment: one trusted operator boundary. NOT a supported security boundary: one shared gateway/agent used by mutually untrusted or adversarial users."

**Cross-session access:** `sessions_history` tool returns a bounded, sanitized view (stripped thinking tags, no raw transcript dump). Agents can access their own transcripts; cross-agent access is opt-in.

**TALOS disposition:**

- Reject OpenClaw's session-as-routing model entirely for TALOS's multi-client use case.
- TALOS uses `board_id` + Postgres RLS as the hard isolation boundary.
- Session keys in TALOS (from Codex pattern) are per-worker-execution scoped credentials, not routing labels.
- Cross-client data access is impossible at the DB layer, not just discouraged at the application layer.

---

## 8. Rate Limiting and Budget Controls

**File:** `src/gateway/auth-rate-limit.ts`

**What exists:** Auth-attempt throttling only (sliding-window):

```typescript
{
  maxAttempts: 10,        // per window
  windowMs: 60_000,       // 1 minute
  lockoutMs: 300_000,     // 5 minutes after lockout
  exemptLoopback: true,   // localhost exempt
}
```

Scopes: shared-secret, device-token, node-pairing, bootstrap-token, hook-auth.

**What does NOT exist:** Token budgets, API cost limits, per-session model-call caps, per-agent daily quotas. OpenClaw defers to API provider-level limits (Anthropic/OpenAI console) for cost control.

**TALOS disposition:**

TALOS adds the 3-axis budget (ADR-009):

```
Axis 1: Token budget      (LLM tokens consumed per task)
Axis 2: Elapsed time      (wall-clock time per task)
Axis 3: Tool-call count   (number of tool invocations per task)
```

Each task specifies its budget. Workers claim a task and inherit its budget. When any axis is exhausted, the task returns to `review` state for human evaluation (not just killed). Rate-limit exit code 75 signals budget exhaustion to the dispatcher.

---

## 9. Security Audit Tooling

**File:** `docs/cli/security.md`, `src/cli/security-audit.ts`

OpenClaw provides a `security audit` command:

```bash
openclaw security audit          # cold, read-only; reports standing issues
openclaw security audit --deep   # adds live gateway probes + plugin collectors
openclaw security audit --fix    # safe remediations (open groups → allowlists, file perms)
```

**What audit checks:**
- Overly-permissive session bindings (open groups)
- Dangerous `dangerouslyAllow*` flags in config
- File permission issues on sensitive paths
- Open pairing channels
- Missing sandboxing on high-risk agents

**What it does NOT fix:** Does not rotate tokens, disable tools, or remove plugins. Does not touch operational state.

**Suppressions:** Known-good findings can be suppressed (audit trails preserved).

**TALOS disposition:** Adopt this pattern as the TALOS `talos audit` command. TALOS audit covers: critic coverage gaps, unsigned/unreviewed skills, tasks stuck in review, capability manifest drift (skill was modified after pinning), and orphaned session keys.

---

## 10. Overall Architecture

**Tech stack:**

| Layer | Technology |
| :--- | :--- |
| Runtime | Node.js (ESM modules) |
| Language | TypeScript (compiled via TSDown) |
| Package manager | pnpm monorepo |
| Testing | Vitest |
| Build/UI | Vite |
| Plugin SDK | `@openclaw/plugin-sdk/*` narrow imports |

**Repo structure:**

```
src/
  gateway/        → Control plane (auth, routing, tool policy)
  agents/         → Agent runtime (embedded runner, tool execution, sandbox)
  channels/       → Channel integrations (Discord, Telegram, WhatsApp…)
  skills/         → Skill loading and filtering
  cron/           → Cron scheduler + isolated execution
  security/       → Audit, threat model, dangerous-tool constants
  config/         → Config schema (Zod), validation, hot-reload
extensions/       → Plugin sources (Discord, Anthropic, Google…)
docs/             → Architecture, threat model, security, how-tos
```

**Entry points:**
- `openclaw.mjs` — CLI binary
- `src/entry.ts` — CLI dispatch
- `src/gateway/boot.ts` — gateway startup, plugin loading
- `src/gateway/call.ts` — RPC client

---

## 11. Threat Model Atlas

**File:** `docs/security/THREAT-MODEL-ATLAS.md`

Five trust boundaries documented by OpenClaw:

1. **Channel access** — TLS + AllowFrom + auth required before any agent turn
2. **Session isolation** — DM scope separates transcripts; does NOT separate authority
3. **Tool execution** — Tool policy pipeline; surface-based dangerous-tool deny
4. **External content** — Web content, skill instructions, and tool results are untrusted input
5. **Supply chain** — ClawHub (public skill registry) has no verification; any installed skill is trusted

**Known attack vectors documented:**

| Attack | Current mitigations | Gaps |
| :--- | :--- | :--- |
| Prompt injection via external content | None (relies on model) | No structured output enforcement |
| Skill injection (malicious SKILL.md) | Filesystem containment + enablement config | No signing, no manifest enforcement |
| ClawHub supply chain | Curator review | No code signing |
| Cross-session injection via sessions_send | sessions_send in HTTP deny list | Not denied on embedded surface |
| Token theft | Short-lived device pairing | 1h grace period for DM channels |

---

## Key Patterns for TALOS (Summary)

### Pattern 1 — Surface-Based Tool Denial

HTTP surface gets a hardcoded dangerous-tool deny that cannot be overridden by agent policy. The surface is determined at the gateway before any policy layer runs.

TALOS adopts this as the **global floor** (ADR-009): no capability profile can re-enable the global "no live writes" rule.

### Pattern 2 — 7-Layer Policy Pipeline

Layered tool policy gives flexible composition: defaults → provider → global → agent → group → sender. Each layer narrows; none can expand the layer above it.

TALOS adds an 8th layer: per-skill capability grant — the narrowest scope of all.

### Pattern 3 — Proactive Loop Architecture

Proactive loops are agent turns triggered by a scheduler, not by a user. They share the tool policy enforcement with user turns. The scheduler produces a session key so each loop run has its own transcript.

TALOS adopts this for: status digests, overdue-deadline nudges, audit-freshness checks, Vault-pull cadence.

### Pattern 4 — Sandboxed Execution Backend

Docker (network: none, readOnlyRoot: true) as the execution environment for write-class tool calls. The gateway process stays on the host and orchestrates the sandbox; it doesn't run inside it.

TALOS adopts Docker as the worker execution backend. Gateway never enters the sandbox.

### Pattern 5 — Security Audit Command

A cold, read-only audit pass that reports standing issues without modifying operational state. `--fix` applies only safe, mechanical remediations.

TALOS `talos audit` covers: capability manifest drift, orphaned session keys, unsigned skills, overdue gate decisions.

### What NOT to Adopt

| OpenClaw Feature | Reason to Reject |
| :--- | :--- |
| Session key as routing only (not auth) | TALOS needs hard per-client isolation |
| `tools.elevated` host bypass | No agent ever reaches a live system |
| Single-operator trust model | TALOS serves multiple clients; adversarial isolation is required |
| Skills injected without capability mediation | The hole TALOS closes — mandatory gate |
| No token/cost budgets | TALOS requires 3-axis budget enforcement |
| `"non-main"` sandbox mode (DMs on host) | TALOS sandboxes all write-class workers |
