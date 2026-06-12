# OpenLumara — Token-Efficient Local-First AI Agent Framework

> **Status:** Research notes · 2026-06-12
> **Source:** [github.com/Rose22/openlumara](https://github.com/Rose22/openlumara)
> **License:** GPL-3.0
> **Stars:** 262 | **Forks:** 24 | **Commits:** 812

---

## What It Is

OpenLumara is a modular, token-efficient AI agent framework written **from scratch** in Python (not forked from OpenClaw, which the README explicitly addresses). Its core design goal: **minimum token waste** for local models. The system prompt can be as small as ~4000 tokens with normal use, compared to the 10K–20K+ typical of comparable agents.

Created by Rose22, the project targets **life management** — todos, notes, morning routines, habit tracking — and is particularly aimed at users with ADHD/autism/executive dysfunction who need a tightly integrated personal AI. Despite this lifestyle focus, the architecture is general-purpose.

**Design philosophy:** "The security of many popular agents was also lackluster. They all rely on a `SKILL.md` system that tell an agent how to do something — usually by invoking a shell command. Not only is a skill.md file usually many words (and thus tokens) in size, but they also force you to let your AI have total access to a command shell. OpenLumara solves this by using Tools instead, and granting the AI agent only exactly the tools it needs."

---

## What "Token Efficiency" Actually Means Here (9 mechanisms)

OpenLumara doesn't use a single breakthrough — it layers many small optimizations:

### 1. Minimal system prompt — module-level injection
Each module has an `on_system_prompt()` hook that returns a **fragment** of text. The Manager collects all fragments and concatenates them. No unused module contributes anything. The baseline with default modules is ~4000 tokens. In `--pure` mode the system prompt is **empty** — you talk to the bare model.

### 2. Binary-search context trimming
When the combined context (system + history + end prompt) exceeds `0.95 × max_context`, a binary search finds the **minimum number of messages to drop from the front** to fit within limits. This is O(log N) instead of O(N) trimming. If even empty history exceeds max, it pushes an error and disconnects.

### 3. Multimodal stripping
For every message **except the last one** (and tool messages), if the content is a multimodal array (text + images + audio), only the text parts are retained. If nothing remains, the content is replaced with `"[multimedia content]"`. The most recent message keeps full context so the AI can see what the user just said or showed.

### 4. Summarization cutoff signal
When a chat is summarized (e.g., via `/compress`), a special signal message `{"signal": "SUMMARIZATION_CUTOFF"}` is inserted. The context builder scans from the end backward for this signal; when found, it replaces it with `{"role": "user", "content": "Summarize our chat so far."}` and drops **everything before it**. The full log is preserved in storage but the AI only sees the compressed summary + recent messages.

### 5. Ghost messages
Messages can be marked `ghost: True` — they appear in the chat display for the user but are **filtered out** before constructing the AI's context window. Used for system notifications, command outputs, and other noise the AI doesn't need to see.

### 6. Token threshold warnings
The `token_threshold` module (enabled by default) injects a warning into the end prompt when usage exceeds 80% of the context limit. It tells the AI to proactively warn the user and suggest `/compress` or `/new`.

### 7. Turn-order enforcement with spacer messages
The context builder enforces strict `system → user → assistant → user → assistant → ...` ordering. If two consecutive assistant messages appear, a spacer user message (`" "`) is inserted. If two consecutive user messages appear, a spacer assistant message is inserted. This prevents models from getting confused by role sequences, which would waste tokens on confusion/recovery.

### 8. Conservative token counting
The `Chat.count_tokens()` method uses tiktoken when available (selected by model name, falling back to `cl100k_base` or character-based `len//4`), and adds **3 tokens overhead per message** + **1 priming token** at the end. This conservative overestimate prevents hard context-limit failures.

### 9. Pure mode (`--pure`)
Disables **all modules** — identity, memory, scheduler, web search, everything. The system prompt is empty, no tools are registered. You get a chat interface directly to the underlying LLM with zero scaffolding. The companion `--coder` mode enables only the `coder` module for a focused coding agent.

---

## Architecture

### Layers

```
┌─────────────────────────────────────────┐
│  Channels (6)                            │
│  WebUI · CLI · Telegram · Discord        │
│  Matrix (+E2EE) · CLI-Lite              │
├─────────────────────────────────────────┤
│  Manager — central orchestrator          │
│  ┌──────────────────────────────────────┐│
│  │ Context — prompt building, trimming  ││
│  │ Chat — message history, persistence  ││
│  │ APIClient — OpenAI wrapper, stream   ││
│  │ ToolcallManager — JSON repair, exec  ││
│  │ Config — YAML, auto-discovery, cache ││
│  └──────────────────────────────────────┘│
├─────────────────────────────────────────┤
│  Modules (26 built-in)                   │
│  identity · memory · writing_style       │
│  web_search · web_reader · http          │
│  sandboxed_shell · file_manager          │
│  coder · scheduler · calendar            │
│  calculator · token_threshold · time     │
│  notes · lists · chars · config          │
│  chats · context · channel · modules     │
│  models · tutorial · docs · unsafe_shell │
├─────────────────────────────────────────┤
│  Data Layer                               │
│  JSON · MessagePack · StorageList/Dict    │
└─────────────────────────────────────────┘
```

### Manager — Central Orchestrator

The `Manager` class handles:
- Loading channels and modules from config
- Dynamic tool registration — scans every module for public methods, inspects type annotations, generates OpenAI-compatible function tools with strict mode
- System prompt assembly — collects `on_system_prompt()` fragments from each module, ordered with awareness/identity/memory on top, time/system at bottom
- Lifecycle — `run()` → `shutdown()` → optional `restart()` loop
- Connection management — API connect/disconnect/reconnect

### Module System (core.module.Module)

Modules get lifecycle hooks:
| Hook | Purpose |
|------|---------|
| `on_ready()` | Startup initialization |
| `on_background()` | Continuous async task (scheduler, etc.) |
| `on_user_message(content)` | React to user input |
| `on_assistant_message(content)` | React to AI output |
| `on_system_prompt()` | Inject text into system prompt |
| `on_end_prompt()` | Inject text at end of context |
| `on_shutdown()` | Cleanup |

Any public method with type annotations becomes an AI-accessible tool (unless prefixed `_`, named `result`, or decorated as a command). The tool name format is `{ModuleName}_{method_name}`. Google-style docstrings are parsed for parameter descriptions.

### Context Builder (core.Context)

The full context assembly pipeline:
1. Fetch system prompt from Manager (`get_system_prompt()`)
2. Deep-copy chat history from Chat
3. Scan for SUMMARIZATION_CUTOFF — truncate if found
4. Remove ghost messages and signal flags
5. Strip invalid assistant messages (no content, no tool_calls)
6. Strip `reasoning_content` if configured
7. Apply max_messages cap
8. Strip multimodal data from all but the last message
9. Enforce turn order with spacer messages
10. Append end prompt from Manager (`get_end_prompt()`)
11. Token-trim with binary search if over 95% limit

### APIClient — OpenAI-Compatible Wrapper
- Async streaming with cancellation support
- Token usage tracking from API (overrides local counting when available)
- `supports_developer_role` detection (newer APIs use `"developer"` instead of `"system"`)
- Error mapping: model-not-found, auth, connection, rate-limit, cancellation
- Custom fields passthrough (`api.custom_fields` merged into request body)
- Insecure TLS option for self-signed certs

### ToolcallManager — JSON Repair + Execution
- Uses `json_repair` library to fix malformed JSON from LLMs
- Asyncio timeout per tool (configurable, default 15s)
- Recursive tool chains — tool results fed back to the model for another turn
- Disabled tool detection — returns error message if tool not in `manager.tool_names`
- Cancellation flag checked before and during streaming

---

## Security Architecture

OpenLumara's security is distinctive: it enforces security **in Python code around the model**, not through prompts.

### Defense layers (from introduction.md)
> "Everything that accesses the filesystem is sandboxed by default. The module that lets your AI see your configuration values redacts all API keys, usernames and passwords without relying on prompting, instead redacting all known keys matching patterns such as `token`, `username`, `password`, etc, using pure python code."

### Sandboxed Shell
- Docker or Podman required
- Per-command containers: create → execute → `--rm` teardown (killed + removed in `finally` block)
- Runs as UID 65534 (nobody) by default
- Network: `none` by default (internet opt-in)
- CPU/memory/PID limits enforced
- Persistent data mounted from `~/sandbox`; temporary mode uses tmpfs
- Configurable image (default `python:3.11-slim`)

### Web Search Security
- HTTPS-only by default
- Local/private network addresses blocked
- Dangerous ports blocked
- Domain whitelist/blacklist
- Content sanitization pipeline: HTML entity decode, URL decode, zero-width char removal, homoglyph normalization, base64 payload detection
- Injection detection with risk logging (medium/high/critical)

### Path Traversal Protection
- `sandbox_path()` function validates against traversal attacks
- Triple URL-decode to catch double/triple encoding attacks
- Symlink resolution and blocking
- Prevented at every file access point

### Open Security Issues (3 of 11 open issues are security)
1. **Stronger sandbox protection** (#31) — improvement on shell sandbox
2. **gVisor or microVM (qemu) as extra security layer** (#28) — beyond container sandbox
3. **Proper path normalization across OSes** (#32) — fixes Windows path traversal edge case

---

## Channels (6 currently supported)

| Channel | Method | Notes |
|---------|--------|-------|
| WebUI | FastAPI + WebSockets | Full-featured, settings panel, streaming |
| CLI | `prompt_toolkit` | Autocomplete, syntax highlighting |
| CLI-Lite | Simple stdin/stdout | Minimal terminal interface |
| Telegram | `python-telegram-bot` | Long polling |
| Discord | `discord.py` | Bot integration |
| Matrix | Matrix SDK | With E2EE encryption support |

---

## Project Maturity & Community

- **Created:** Late May / early June 2026 (approximately 2 weeks old at time of research)
- **Commit velocity:** 812 commits — extremely active
- **Open issues:** 11 (4 by Rose22, all security-focused)
- **Open PRs:** 2
- **Known bugs:**
  - Telegram: recursive "Your request exceeded the token limit" (#7)
  - CLI: Recursive "I will not use markdown formatting" (#6)
  - Chat deletion not working (#4)
  - User modules with CamelCase disabled on restart (#17)
- **License:** GPL-3.0

### Author's AI use disclaimer
> "OpenLumara's core framework (everything in the core/ folder) was designed and coded by hand, but i used AI to ask it how to further improve things, and how to fix certain bugs... This is not a vibe-coded project, but it IS an AI-assisted project."

---

## TALOS Relevance

### What TALOS should borrow

| Technique | OpenLumara | TALOS application |
|-----------|-----------|-------------------|
| **Binary-search context trimming** | O(log N) trim to fit within 95% limit | Strategy Ladder context windows — trim agent conversation history efficiently |
| **Summarization cutoff signal** | Special message replaces old context | When a complex multi-step task exceeds context, preserve full log but cutoff old turns |
| **Ghost messages** | User-visible, AI-invisible | Show tool execution results to the reviewer in the cockpit but don't pollute the agent's context |
| **Module-level system prompt injection** | Each module contributes a fragment | Critics, orchestration, and memory layers each inject their own system prompt fragment |
| **`on_end_prompt()` hook** | Injects dynamic content at end of context | Perfect for injecting current NEXUS analysis results, tag values, or board state |
| **Per-command container sandbox** | Create → execute → kill immediately | Gateway sandbox for executing untrusted agent-generated scripts |
| **Security enforced in code, not prompts** | API keys redacted by Python regex patterns | TALOS's Guardian doctrine — deterministic enforcement of security boundaries |
| **Pure mode (`--pure`)** | Strip all scaffolding to bare model | Quick-ask mode in the cockpit — bypass all agents and talk directly to the model |

### What makes OpenLumara different (strengths vs. TALOS)

- **Personal assistant focus** — daily routines, memory of preferences, character personalities. TALOS is a project execution platform.
- **Single-user, single-channel** — one agent per session. TALOS routes multiple agents across a DAG.
- **No gate/review system** — OpenLumara trusts the AI to act autonomously (within sandbox). TALOS's Guardian doctrine is its defining feature.
- **No DAG scheduling** — no task dependency graphs, no project management layer.
- **No deterministic critics** — all security is runtime enforcement, not pre-execution gating.
- **No LangGraph or Graphiti** — simpler execution model, no graph-based orchestration or memory.
- **No hub-and-spoke deployment** — single-instance local only.

### What makes it weaker (limitations vs. TALOS)

- **No air-gap deployment model** — designed for home/personal use, not industrial clients
- **No audit trail** — no append-only event log for compliance
- **No multi-tenant isolation** — no RLS, no per-client data boundaries
- **No proposal→gate→approve workflow** — no structural human-in-the-loop
- **No crystallize step** — doesn't learn from execution trajectories
- **Immature project** — 2 weeks old, security issues still open, community is small

### Bottom Line

OpenLumara and TALOS are **complementary rather than overlapping**. OpenLumara is a tight, local-first personal agent framework optimized for token efficiency and security on consumer hardware. TALOS is an industrial multi-agent project execution platform with deterministic gating and structured human oversight.

The most valuable patterns to import: **binary-search context trimming**, **summarization cutoff signal**, **ghost messages**, **module-level prompt fragments**, and **per-command container sandboxing**. The architectural philosophy of enforcing security in code rather than prompts is already TALOS's Guardian doctrine — OpenLumara provides a clean reference implementation of that principle in the personal-agent space.

---

## Key Files Referenced

| File | Purpose |
|------|---------|
| `core/main.py` | Entry point, CLI arg generation, restart loop |
| `core/manager.py` | Central orchestrator, module/channel lifecycle, tool registration |
| `core/context.py` | Context building, trimming, summarization cutoff |
| `core/chat.py` | Message history, token counting, chat persistence |
| `core/api_client.py` | OpenAI wrapper, streaming, error mapping, cancellation |
| `core/toolcalls.py` | Tool call lifecycle, JSON repair, recursive chains |
| `core/module.py` | Module base class, lifecycle hooks, command decorator |
| `core/modules.py` | Dynamic module discovery with pkgutil |
| `core/config.py` | YAML config, auto-discovery, schema cache |
| `core/functions.py` | Logging, path sandbox, traversal protection |
| `modules/token_threshold.py` | Context-limit warning module |
| `modules/sandboxed_shell.py` | Docker/Podman per-command container execution |
| `modules/web_search.py` | Multilayer prompt injection defense |
| `modules/memory.py` | Persistent memory with pinning, tags, search |
