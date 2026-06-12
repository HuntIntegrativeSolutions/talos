# GitHub Agentic Workflows — TALOS Implementation Analysis

> **Status:** Research notes · 2026-06-12
> **Source:** https://github.com/github/gh-aw (4.5k★) · https://github.github.com/gh-aw/
> **Phase 0I** — upstream deep-dive for TALOS blueprint refinement
> **Purpose:** Map 10 GitHub Agentic Workflows mechanisms to concrete TALOS implementation points with phase placement. Not a summary — a build guide.

---

## What It Is

GitHub Agentic Workflows (gh-aw) lets you write agent automation in **Markdown + YAML frontmatter** and compile it to hardened GitHub Actions YAML (`.lock.yml`). Agents (Copilot, Claude, Codex, Gemini) execute with **read-only permissions** by default; writes go through the **Safe Outputs** pipeline: filter → moderate → threat-detect → apply. Built by GitHub Next + Microsoft Research. Entered public preview June 11, 2026.

**What makes it relevant to TALOS:** The security architecture is a near-exact parallel to the Guardian doctrine, but production-hardened at GitHub scale. Every TALOS mechanism has a counterpart here, and some are more mature. This note identifies exactly what to borrow and where to slot it.

---

## Mechanism 1: Compilation-Time Security (Schema + Expression + Action Validation)

**What GitHub does:**
Before any agent runs, the `gh aw compile` step validates:
- **Frontmatter schema** — YAML keys must match the allowed set (no injection through config)
- **Expression allowlisting** — only safe template expressions are accepted (no arbitrary code in frontmatter)
- **Action pinning** — all downstream Actions are pinned to SHA hashes, not version tags
- **Supply chain security** — dependencies verified before runtime

**TALOS implementation:**
TALOS already has a compiler adjacency in the Strategy Ladder — the **plan relay** (step 3) where a draft plan is refined before gating. Add a **plan schema validation** step that checks the plan artifact against a schema before it reaches the gate:

```
TALOS current:    Triage → Research → Plan Relay → Gate → Execute → Crystallize
                    ↑                           ↑
TALOS + compile:   Plan Schema Validation ──────┘
                    (reject malformed plans before the gate evaluator spends tokens)
```

**Phase:** Phase 2 (gate + critics). Add `plan_schema` field to deliverable templates — a JSON Schema that the plan artifact must conform to before the gate evaluator runs. Rejected plans go back to the planner with a structured error instead of burning evaluator tokens.

**Concrete artifact:** `plan_schemas/` directory in the TALOS repo, with:
- `audit-plan-schema.json` — required fields: `scope`, `tags_queried`, `routines_checked`, `deliverable_type`
- `fds-plan-schema.json` — required fields: `tag_references`, `rung_references`, `safety_function_mapping`
- Gate evaluator checks plan against schema before first critic runs

---

## Mechanism 2: Zero-Secret Agents (chroot Jail + Host Mount)

**What GitHub does:**
Instead of giving the agent a container with secrets injected, GitHub mounts the **entire runner filesystem read-only** at `/host`, overlays selected paths with empty `tmpfs` layers, and launches the agent in a `chroot` rooted at `/host`. The agent never sees API keys, MCP auth tokens, or GitHub tokens — those live in separate privileged containers (API proxy, MCP Gateway) that the agent talks to over firewalled localhost.

```
  Agent Container (chroot /host)
       │
       ├──→ API Proxy (holds LLM keys) ── localhost:8080
       ├──→ MCP Gateway (holds MCP auth) ── host.docker.internal:80
       └──→ Network Firewall (Squid, allowlist only)
```

**TALOS implementation:**
TALOS's current design says "MCP boundary = security boundary" and "layered tool policy." Add the **chroot jail pattern** for the worker execution sandbox:

```
TALOS current worker:  [Docker container with env vars + mounted secrets]
TALOS + chroot:        [chroot jail at /host with overlay tmpfs]
                          → talks to API Proxy for LLM (token never in jail)
                          → talks to MCP Gateway for NEXUS (NEXUS tokens never in jail)
                          → all network through AWF-style firewall
```

This replaces the current vague notion of "sandboxed execution" with a concrete, Microsoft Research-validated pattern. The key insight: **even if the agent is fully compromised, the attacker gets no secrets and writes nothing.**

**Phase:** Phase 2 (gate + critics), specifically the worker isolation subsystem. The current session-key pattern gives logical isolation; chroot gives **kernel-enforced** isolation.

**What to build:**
- `sandbox/chroot.py` — sets up chroot jail with host mount + tmpfs overlay + network namespace
- `sandbox/api_proxy.py` — lightweight reverse proxy that holds LLM credentials and routes model calls
- `sandbox/mcp_gateway.py` — port of the MCP Gateway pattern (spawns MCP servers in isolated containers, agent never touches their auth)

---

## Mechanism 3: Safe Outputs (Write-Buffered Operations)

**What GitHub does:**
The agent never holds write credentials. It writes structured output to `agent_output.json` (a local artifact). A separate pipeline reads that artifact, validates it against the allowed output types, moderates content, runs secret detection, and **then** executes the writes with scoped credentials.

```yaml
safe-outputs:
  create-issue:
    max: 1
    title-prefix: "[report]"
  create-pull-request:
    threat-detection: true
```

Allowed output types: create/update/close issues, discussions, PRs, comments, labels, assignments, milestones — each with configurable volume limits.

**TALOS implementation:**
This is TALOS's **gate** mechanism, already in the blueprint. But GitHub's version has two refinements TALOS doesn't:

1. **Volume limits** — max N creates per run (TALOS should add `max_writes` to each capability profile)
2. **Per-output-type threat detection** — different prompts and checks for PRs vs. issues vs. comments

**Phase:** Phase 2. Extend `task_gate_results` with:
- `max_writes: int` — hard ceiling on write-capable tool invocations per task
- `threat_detection: {enabled: bool, prompt: str}` — per-deliverable-type threat detection config

---

## Mechanism 4: MCP Gateway (Credential Isolation for Capabilities)

**What GitHub does:**
The MCP Gateway (`gh-aw-mcpg`) is a privileged container that:
- Spawns MCP server containers on demand
- Holds all MCP authentication material (agent never sees it)
- Routes tool calls from agent → MCP server, enforcing tool allowlists
- All MCP traffic stays within the host (never leaves via Docker networks)

```yaml
tools:
  github:
    mode: gh-proxy        # GitHub MCP via proxy, agent has no token
    toolsets: [default]
  mcp:
    servers:
      - name: my-server
        command: node server.mjs
        allowed: [tool1, tool2]    # tool allowlisting
```

**TALOS implementation:**
TALOS's blueprint says "capabilities behind MCP" but doesn't specify **how** the agent connects to them. Add the MCP Gateway pattern explicitly:

```
TALOS current:  Agent → MCP (agent holds MCP creds)
TALOS + MCPG:   Agent → MCP Gateway → MCP servers
                         ↑ holds creds
                         ↑ enforces tool allowlists
                         ↑ logs every invocation
```

**Phase:** Phase 2. Build `gateway/mcp_gateway.py` as a standalone process that:
- Loads MCP server configs from a manifest (yaml)
- Holds credentials in memory (never written to disk as plaintext)
- Exposes an agent-facing API at `localhost:<port>` with tool-name routing
- Logs every tool call to the event log

---

## Mechanism 5: Agent Workflow Firewall (AWF)

**What GitHub does:**
A dedicated firewall container (Squid proxy) sits between the agent and the internet. It enforces a **domain allowlist** declared in frontmatter:

```yaml
network:
  firewall: true
  allowed:
    - defaults      # GitHub API, basic infra
    - python        # PyPI ecosystem
    - node          # npm ecosystem
    - "api.example.com"
```

After setup, the container drops `iptables` capabilities so even a compromised agent can't modify the firewall.

**TALOS implementation:**
TALOS's gateway layer (Phase 6) already plans sandboxed proactive loops. Add the AWF pattern to the **worker sandbox** — every worker container gets a sidecar Squid proxy with a per-task allowlist derived from the task's capability profile.

**Phase:** Phase 6 (gateway + sandbox). But the **api_proxy** from Mechanism 2 should be built in Phase 2 and share the same architecture.

---

## Mechanism 6: Threat Detection as a Separate Job

**What GitHub does:**
After the agent finishes and Safe Outputs generates `agent_output.json`, a **separate job** (different container, no write permissions, no access to agent's runtime) downloads the artifacts and runs AI-powered analysis:

- Secret leak detection (API keys, tokens, passwords)
- Malicious patch detection (backdoors, suspicious patterns)
- Prompt injection detection (instructions in proposed content)

```yaml
safe-outputs:
  create-pull-request:
    threat-detection:
      prompt: "Focus on SQL injection and hardcoded credentials"
      steps:
        - name: Run TruffleHog
          run: trufflehog filesystem /tmp/gh-aw --only-verified
```

**TALOS implementation:**
TALOS already has **deterministic critics** in the blueprint — rules like `no-hallucinated-tags`, `citations-resolvable`, `tag-exists-in-NEXUS`. Add a **learned critic** slot that runs an AI-powered threat detection model (or delegates to a cheap LLM) on the deliverable before it reaches the human gate.

**Phase:** Phase 2. Add `learned_critics` to the critic set schema. The threat detection critic:
- Is a `waivable: true` critic by default (human can override)
- Runs in a separate process with no write access
- Gets the plan artifact + deliverable + source context
- Returns structured findings: `[{type: "secret"|"injection"|"policy_violation", severity: "low"|"medium"|"high", detail: "..."}]`

---

## Mechanism 7: Compiled Lock Files (Source of Truth Separation)

**What GitHub does:**
The workflow lives in two files:
- `.md` — human-editable source (Markdown + YAML frontmatter)
- `.lock.yml` — compiled GitHub Actions YAML (machine-generated, committed to repo)

This separates **intent** from **execution**. Humans edit the `.md`; the compiler produces the exact `.lock.yml`. Recompilation only needed when frontmatter changes (markdown body changes don't need recompilation because the agent interprets the body at runtime).

**TALOS implementation:**
TALOS's **Strategy Ladder steps** are currently a runtime abstraction (the evaluator picks the next step). Add a **lock-file pattern** for the Strategy Ladder itself — compile the task's plan into a structured execution graph that can be audited before execution:

```
.mermaid.md  ──(compile)──→  .plan.json  (execution graph)
(human edits)                (machine-readable, auditable)
                              - steps with explicit permissions
                              - data dependencies between steps
                              - critic gates between steps
                              - max_turns, max_writes, budget caps
```

**Phase:** Phase 2–3. The compilation step fits naturally after the **plan relay** (step 3 of the ladder) and before **gate the plan** (step 4). The compiled plan artifact (`.plan.json`) becomes the input to both the critics and the executor, giving a clear audit trail of "what was approved" vs. "what actually ran."

---

## Mechanism 8: Command-Triggered Workflows (ChatOps via `/`)

**What GitHub does:**
Users trigger workflows by commenting on issues/PRs with slash commands:

| Command | Workflow |
|---------|----------|
| `/plan` | Break issue into sub-tasks |
| `/archie` | Generate Mermaid diagrams |
| `@agentic-workflow triage` | Trigger issue triage |

The comment event starts a workflow run that sees the issue context, executes the agent, and posts results back.

**TALOS implementation:**
TALOS's cockpit is a web UI, but for edge deployments (Acme thick edge) where the operator is in FT View, not a browser, a **ChatOps interface** via Telegram/Discord would be valuable. This maps to TALOS's **gateway layer** (Phase 6).

The Hermes Telegram channel already exists. TALOS could add slash commands in the chat:
- `/talos audit UNIT_PLC` — dispatch a NEXUS audit task
- `/talos status` — current board state
- `/talos approve TASK-042` — approve a gated task from chat

**Phase:** Phase 6 (gateway + proactivity). But the command trigger concept should be designed in Phase 3 alongside the cockpit — the same event model serves both UI and chat triggers.

---

## Mechanism 9: OpenTelemetry Cost Analysis

**What GitHub does:**
Every agent workflow run exports traces and token data to OTLP-compatible backends. This enables cost dashboards, per-workflow spend analysis, and budget enforcement at the organization level.

```bash
gh aw audit <run-id>          # per-workflow cost breakdown
gh aw logs --cost             # cost-annotated logs
```

Environment variable `OTEL_EXPORTER_OTLP_ENDPOINT` configures the destination.

**TALOS implementation:**
TALOS's blueprint already has the **three-axis budget** (time/cost/iterations) as a hard ceiling on the Strategy Ladder. Add an OpenTelemetry exporter to the worker runtime so every turn emits:

- `talos.task.turns` — counter
- `talos.task.tokens.input` — histogram
- `talos.task.tokens.output` — histogram
- `talos.task.cost` — histogram (converted from tokens × model rate)
- `talos.task.latency` — histogram
- `talos.critic.{name}.duration` — histogram
- `talos.gate.outcome` — counter with label (approve/reject/waive/edit/escalate)

**Phase:** Phase 2. The gate/critics build is the right time to instrument. Use `opentelemetry-api` and `opentelemetry-sdk` Python packages — they're standard and don't lock TALOS into any backend.

---

## Mechanism 10: Markdown-First Workflow Authoring

**What GitHub does:**
Workflows are plain Markdown files with YAML frontmatter. No YAML indentation battles, no complex `${{ }}` expressions. The Markdown part is the **intent**; the compiler produces the executable form.

```markdown
---
on: schedule: daily
safe-outputs:
  create-issue:
    title-prefix: "[status]"
---
# Daily Status Report
Create a summary of repository activity over the last 24 hours.
```

**TALOS implementation:**
TALOS's **deliverable templates** (versioned critic sets per deliverable type) could be authored in Markdown with frontmatter, then compiled to the critic set schema:

```markdown
---
deliverable: fds
critics:
  - tag-exists-in-NEXUS
  - rung-references-resolvable
  - safety-function-mapped
version: 2
---
# Functional Design Specification
An FDS maps process control narratives to PLC implementations.
It must reference specific tag names, rung locations, and safety functions.
```

**Phase:** Phase 3 (PM layer). The deliverable template format should be plain Markdown, compiled to the structured critic set. This makes templates human-editable without touching the schema.

---

## Implementation Map: TALOS Phase Grid

| # | Mechanism | TALOS Phase | Current BLUEPRINT Coverage | What to Build |
|---|-----------|-------------|---------------------------|---------------|
| 1 | **Compile-time validation** | Phase 2 | Not covered | `plan_schemas/` directory, schema check before gate evaluator |
| 2 | **Zero-secret chroot jail** | Phase 2 | "Sandboxed execution" (vague) | `sandbox/chroot.py` + `api_proxy.py` + `mcp_gateway.py` |
| 3 | **Safe Outputs + volume limits** | Phase 2 | Gate exists, no volume limits | Add `max_writes` to capability profiles |
| 4 | **MCP Gateway** | Phase 2 | "MCP boundary = security boundary" (principle only) | `gateway/mcp_gateway.py` — credential isolation layer |
| 5 | **Agent Workflow Firewall** | Phase 6 | Gateway sandbox planned | Sidecar Squid proxy per worker container |
| 6 | **Threat detection critic** | Phase 2 | Deterministic critics only | `learned_critic` slot in critic set schema |
| 7 | **Lock-file compilation** | Phase 2–3 | Not covered | `.plan.json` artifact from plan relay → gate |
| 8 | **ChatOps command triggers** | Phase 6 | Not covered | Slash command model in Telegram/Discord gateway |
| 9 | **OpenTelemetry cost export** | Phase 2 | Three-axis budget (principle) | OTLP metrics instrumentation in worker runtime |
| 10 | **Markdown-first templates** | Phase 3 | Deliverable templates (general) | Markdown + frontmatter → compiled critic set |

---

## What GitHub Does Better (and TALOS Should Copy Verbatim)

| Feature | GitHub Implementation | TALOS Action |
|---------|----------------------|-------------|
| **chroot + host mount** | Single mount of `/` read-only, overlay tmpfs, chroot | Replace "Docker container with env vars" with this pattern |
| **Volume limits** | `max: 1` on every output type | Add `max_writes` to every capability tool profile |
| **Per-output threat detection** | Different prompts for PRs vs. issues vs. comments | Add `threat_detection` config to each critic |
| **Compile-time schema validation** | Frontmatter validated before runtime | Add plan schema validation before gate evaluator |
| **MCP Gateway credential isolation** | Agent never touches MCP auth | Build `mcp_gateway.py` as Phase 2 blocker |
| **OpenTelemetry by default** | Every run exports traces | Instrument with OTLP from Phase 2, not later |

## What TALOS Does Better (and GitHub Should Copy)

| Feature | TALOS | GitHub |
|---------|-------|--------|
| **Deterministic critics** | Safe by construction, no LLM needed | Only has AI-powered threat detection |
| **DAG task scheduling** | Dependency graph across tasks | Flat workflows only |
| **Hub-and-spoke deployment** | Air-gapped edges | SaaS-only |
| **Crystallize** | Learn from trajectories | No equivalent |
| **Multi-agent isolation** | Per-task session keys, config inheritance | Single-agent-per-workflow |
| **Industrial domain** | L5X parsers, PlantPAx UDTs, PLC sim | Code-only |

---

## Concrete Next Actions (Phase 2 Priority)

These are ordered by impact-to-effort ratio:

1. **Build `plan_schemas/`** — 2 hours. Three JSON Schema files for the most common deliverable types. Gate evaluator checks plan against schema before first critic runs. Rejects malformed plans early.

2. **Add `max_writes` to capability profiles** — 1 hour. Extend the READ tool profile schema with `max_writes: int`. The gate enforces this as a hard ceiling on write-capable tool invocations per task.

3. **Add `learned_critic` slot to critic set schema** — 3 hours. A critic can be `learned: {model: str, prompt: str}` in addition to `deterministic: {fn: str}`. The engine runs both types the same way — the slot just points to an inference endpoint.

4. **Build `gateway/mcp_gateway.py`** — 2 days. Standalone process that loads MCP server configs, holds credentials, routes agent tool calls, logs invocations. The hardest but highest-impact piece — it unlocks all subsequent sandbox work.

5. **Instrument worker runtime with OpenTelemetry** — 4 hours. Add `opentelemetry-api` dependency, emit task/critic/gate metrics. Store OTEL endpoint in config.yaml. Enables cost visibility from day one.

---

## Key Files Referenced

| File | Purpose |
|------|---------|
| `github/gh-aw` | CLI tool, compiler, runtime (4.5k★) |
| `github/gh-aw/.github/aw/github-agentic-workflows.md` | Workflow format spec, compile rules |
| `github/gh-aw/SKILL.md` | Prompt surface for agentic workflow authoring |
| `github/gh-aw/install.md` | Setup protocol (used by TALOS skill __init__) |
| `github.github.com/gh-aw/introduction/architecture/` | Security architecture: 3-layer trust, substrate/config/plan |
| `github.github.com/gh-aw/reference/safe-outputs/` | Safe Outputs type reference with volume limits |
| `github.github.com/gh-aw/reference/threat-detection/` | Threat detection pipeline spec |
| `github.next/agentics` (githubnext/agentics) | 26 prebuilt workflow examples (760★) |
| Microsoft Research blog (Mar 2026) | "Under the hood" security architecture deep-dive |
