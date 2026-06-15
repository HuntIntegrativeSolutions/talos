# Omnigent Upstream Notes

**Source:** Databricks blog (June 2026), omnigent.ai, MarkTechPost, GitHub README, POLICIES.md, shashikantjagtap.net deep-dive, and web searches. Two sources returned HTTP 403 (alphasignal.ai, develeap.com) and one community post (community.databricks.com) contained no technical content — those three are excluded from technical claims.

---

## What it is

Omnigent is an open-source meta-harness released by the Databricks AI team and Neon under Apache 2.0 in June 2026. It is currently in alpha (1.3k GitHub stars, 62 commits on main at time of research; repository: `https://github.com/omnigent-ai/omnigent`).

**The problem it solves.** Individual agent harnesses (Claude Code, Codex, Pi) each expose incompatible interfaces and have no shared governance layer. Teams running several agents simultaneously manage separate cost controls, security policies, collaboration surfaces, and deployment targets with no unified control plane. Omnigent addresses the gap "where a single harness stops" by sitting above them as a common runtime.

**What "meta-harness" means here.** A meta-harness is not itself an LLM or an agent; it is the orchestration shell that wraps terminal agents, enforces cross-cutting policies, routes credentials, and projects sessions onto multiple interfaces (terminal, browser, native macOS app, mobile, REST API). The agent does the reasoning; Omnigent governs what it may do, where it runs, and who can observe it.

**Terminal agents currently supported:**
- Claude Code (native wrapper)
- OpenAI Codex (native wrapper)
- Pi
- OpenAI Agents SDK
- Claude Agents SDK (Anthropic)
- Custom YAML-defined agents

Built-in orchestrator agents ship with Omnigent: **Polly** (multi-agent coding orchestrator that fans out to parallel git worktrees across vendors, then cross-reviews diffs) and **Debby** (model-debate agent). Both are user-customizable via YAML.

---

## The runner abstraction

A **runner** wraps a single terminal agent instance in a sandboxed, uniform session. The architectural assertion from the Databricks blog is:

> "However each agent harness calls into its LLM internally, the interface to users is the same: messages and files in, text streams and tool calls out."

The runner normalises this boundary. Regardless of whether the underlying harness is Claude Code, Codex, or a custom Python callable, the runner exposes the same session object to the policy layer and to the server.

**Common API surface (from README and CLI docs):**
```
omnigent run path/to/agent.yaml        # start a session
omnigent attach <session_id>           # re-attach to a running session
omnigent run --fork <session_id>       # fork at a conversation branch point
omnigent server start                  # start the backend server (localhost:6767)
```
Sessions are persistent, multi-device entities: a session started in the terminal can be continued in a browser tab or mobile app by re-attaching to the same session ID. The `/model` command switches the underlying model mid-session without restarting.

**How the runner wraps terminal agents vs. SDK agents.** Native wrappers (Claude Code, Codex) invoke the harness CLI in a tmux pane and intercept its tool calls at the process boundary. SDK agents (OpenAI Agents, Claude Agents SDK) are imported as Python callables and called directly. The distinction matters for sandbox scope: native wrappers need OS-level process isolation; SDK agents are isolated at the Python call boundary.

**Mapping to TALOS `task:{board_id}:{task_id}:{attempt}` session keys.** An Omnigent session ID maps most directly to a TALOS `task_id`. It does not encode a `board_id` (there is no hard isolation axis analogous to TALOS's Postgres RLS) and it does not encode an `attempt` number (crash recovery / restart increments are not exposed as part of the session identity in public docs). The Omnigent model is one session per agent invocation; TALOS's model is one session per attempt, with checkpointed crash recovery via incrementing `attempt_no`. This is a meaningful architectural difference: TALOS's session key carries idempotency semantics (`…:{attempt}:{step}` on every write-class side effect); Omnigent's session ID carries identity semantics only.

**Verdict:** adapt — the runner's uniform `messages-and-files-in / text-streams-and-tool-calls-out` boundary is the right abstraction for wrapping NEXUS and other MCP capabilities behind a common TALOS dispatch interface, but TALOS must extend it with board isolation, attempt numbering, and idempotency keys that Omnigent's runner does not carry.

---

## Stateful control policies

**What a policy is.** In Omnigent, a policy is a Python function (or class factory) registered under a string name and referenced in YAML. Policies are described in `omnigent/policies/__init__.py` as "pure evaluators, no runtime state." The runtime orchestration (composition loop, parking, fail-closed) lives in a separate runtime module. This means a policy itself is stateless; the *runtime* that calls policies carries session state and passes it in as context.

**What state a policy has access to.** From the built-in policy catalog (POLICIES.md), policies receive per-evaluation context that includes at minimum: the current tool call, cumulative session cost, tool-call history, file-access log (for write-path restrictions), cumulative risk score, and current working directory. The risk-scoring built-in specifically aggregates cumulative session risk, which requires the runtime to pass running totals rather than the current action alone.

**Policy declaration (YAML):**
```yaml
policies:
  approve_shell:
    type: function
    handler: omnigent.policies.builtins.safety.ask_on_os_tools
  cap_calls:
    type: function
    handler: omnigent.policies.builtins.safety.max_tool_calls_per_session
    factory_params:
      limit: 50
  cost_cap:
    type: function
    handler: omnigent.policies.builtins.cost.cost_budget
    factory_params:
      max_cost_usd: 5.00
      ask_thresholds_usd: [3.00]
```

**How policies compose.** Policies stack across three levels: server-wide (admin), per-agent spec (developer), per-session (user). The composition semantics are documented in POLICIES.md:

> "A DENY from any policy short-circuits the rest."

Session policies evaluate **first**, then agent-spec policies, then server-wide policies. The order is reversed from most intuitive expectations: session is strictest-first, not weakest-first. The composition model is **intersection-only with short-circuit denial**: all active policies across all three levels must either ALLOW or ASK for an action to proceed. A lower scope (session) **cannot re-grant** what a higher scope (server) denied. This is a whitelist model — deny is terminal.

**Comparison to ADR-009 (8-layer intersection-only model).** Omnigent's 3-level intersection model is structurally aligned with TALOS's ADR-009 principle that "each layer can only restrict, never expand." The semantics are compatible: both systems take the intersection of all active policies, and a denial at any level is terminal. TALOS has more layers (8 vs. 3) and an explicit "deny logged, never silent" requirement that Omnigent does not surface in public docs. TALOS's global floor ("no live writes, ever") has no Omnigent equivalent — Omnigent has no concept of a write-kind distinction (offline artifact vs. sim-only vs. live), because it is not designed for industrial/safety-critical contexts. The critical structural difference is TALOS's ADR-004 `write_kind` gate, which is structural (no live-write tool exists in any capability manifest), whereas Omnigent's write restrictions are purely policy-enforced and therefore dependent on policy correctness.

**Built-in policy categories (from POLICIES.md):**
- Safety: OS tool approval (`ask_on_os_tools`), max tool calls per session, skill blocking, sandbox enforcement (`enforce_sandbox`), PII scanning
- Cost: session budget with hard cap + soft thresholds, daily user budget
- GitHub: repository and branch access control
- Google Workspace: Drive, Gmail, Calendar access policies
- Working directory: `cd` blocking, git worktree restrictions
- Risk scoring: cumulative risk escalation (ASK or DENY when score threshold crossed)
- Routing: model cost gating based on task complexity

**Verdict:** adapt — the intersection-only composition model is directly compatible with ADR-009 and can inform TALOS's P3 dispatcher policy stack. The built-in catalog (risk scoring, PII scanning, GitHub policies) contains several patterns worth porting. However, TALOS cannot adopt Omnigent's policy model verbatim because it lacks the `write_kind` structural gate (ADR-004), has no analog to Postgres RLS board isolation (ADR-010), and does not include a "deny logged, never silent" audit trail requirement.

---

## Cost gate

**Per-session cost tracking mechanism.** Omnigent tracks LLM spend per session cumulatively in the runner runtime. Spend is accumulated as tool calls resolve (each LLM call reports token usage; cost is computed from model pricing). The budget policy (`cost_budget`) receives the current cumulative session spend on every tool-call evaluation.

**How the human-approval pause works.** The cost gate uses a soft-threshold + hard-cap model:
```yaml
factory_params:
  max_cost_usd: 5.00          # hard cap — session halted
  ask_thresholds_usd: [3.00]  # soft warning — paused, awaiting user continuation
```
At `$3.00`, the session parks in an ASK state: the agent is suspended, a notification is delivered (terminal/app), and the session resumes only when the user confirms. At `$5.00`, the session terminates (DENY). The Databricks blog also gives `$100` as an example threshold, indicating thresholds are user-configurable across a wide range. This is a **mid-session** gate, not a session-boundary gate: the check fires on every LLM call evaluation, not only at session start or end.

**Mapping to TALOS's 3-axis budget (tokens / elapsed time / tool-call count).** Omnigent's cost gate is a single axis: USD spend. It does not natively track tokens, elapsed wall-clock time, or tool-call count as separate budget axes. TALOS's 3-axis model (from openclaw-notes) is richer: token count catches runaway context windows before cost accrues; elapsed time catches stuck loops regardless of token spend; tool-call count catches shallow-but-wide thrashing. Omnigent's `max_tool_calls_per_session` built-in provides the third axis, but it must be configured separately from the cost gate — there is no unified 3-axis budget object.

**On budget exhaustion, Omnigent pauses for user confirmation or terminates.** TALOS's equivalent is "task returns to `review` state" — a state-machine transition that creates a durable audit record, triggers the gate UI, and requires one of 5 human outcomes (Approve / Reject / Waive / Edit-inline / Escalate). Omnigent's pause is UI-mediated (terminal prompt or app notification) with a binary resume/abort; it does not create a durable task-state transition or a gate record that an auditor could later inspect.

**Verdict:** adapt — the soft-threshold + hard-cap pattern is directly useful for TALOS's P3 dispatcher budget enforcement. Port the pattern as a unified 3-axis budget object (spend_usd, tokens, tool_calls, elapsed_seconds) with per-axis soft and hard thresholds. On hard-cap exhaustion, transition the task to `review` state (per TALOS's gate model) rather than issuing a terminal DENY, so the human can choose to waive or extend budget via the gate UI.

---

## OS sandbox

**Isolation mechanism.** Omnigent supports two named sandbox types in public docs:

1. `darwin_seatbelt` — macOS's kernel sandbox framework (the same mechanism used for App Store isolation). This is the native sandbox for macOS developer machines.
2. `linux_bwrap` — bubblewrap, a user-space Linux namespacing tool that provides unprivileged process isolation via Linux user namespaces and mount namespaces. It does not require root and does not use Docker, seccomp filters, or eBPF directly. Bubblewrap creates a restricted view of the filesystem and optionally isolates network namespaces.

Cloud-hosted sessions run on Modal or Daytona, which provide their own container isolation (Modal uses gVisor-backed containers; Daytona uses standard Docker). Omnigent does not directly manage cloud sandbox internals in those cases — it delegates to the provider.

**How "intercept and transform network requests" works technically.** From POLICIES.md and the sandbox documentation, the mechanism is an **egress proxy**: network traffic from the sandboxed agent process is routed through an Omnigent-managed proxy. The proxy enforces domain allowlists and performs **credential injection** — secrets such as GitHub tokens are withheld from the agent's environment and injected by the proxy into approved outbound requests at the HTTP header layer. The agent never sees the raw credential. This is closer to a transparent HTTPS proxy with header injection than a syscall hook or eBPF intercept. No ptrace or eBPF evidence was found in public docs.

**Credential injection mechanism.** Four credential classes are supported: first-party API keys, Claude/ChatGPT CLI subscriptions, OpenAI/Anthropic-compatible gateway endpoints (OpenRouter, Ollama, etc.), and Databricks workspace profiles. Credentials are auto-detected from the host environment and brokered through the proxy — not passed into the sandbox environment directly.

**Comparison to ADR-010 (`network:none` + `readOnlyRoot` Docker sandbox).** This is the most significant security gap between Omnigent and TALOS:

- Omnigent: intercept-and-transform — network is reachable but filtered through an allowlisting proxy. A policy misconfiguration or proxy bypass opens a network exfiltration path.
- TALOS (ADR-010): `network:none` — the Docker container has no network interface. There is no proxy to bypass. The only data path out is the explicit MCP boundary.

Omnigent's model is **strictly softer** than TALOS's. "Intercept and transform" requires the proxy to be correct for every possible egress destination. "Block all" requires only that the network interface is absent. For TALOS's write-class workers on industrial-adjacent data, the softer model is not acceptable: a proxy misconfiguration on a worker with write-class tool access could exfiltrate PLC tag data or P&ID schemas.

On the read-class side (`readOnlyRoot`), Omnigent's `write_paths` allowlist in YAML is the equivalent — the agent can only write to explicitly declared paths. This is the same pattern as Docker's `readOnlyRoot:true` + explicit volume mounts. TALOS could align read-class runners with this pattern.

The `linux_bwrap` primitive is lighter weight than Docker but covers the same threat model for local dev. It does not provide the Docker-level image reproducibility or the CI/CD container registry integration TALOS will need for P3+ workers.

**Verdict:** ignore for write-class workers — Omnigent's intercept-and-transform network model is too soft for TALOS's ADR-010 hard requirement of `network:none` on write-class Docker workers. The `darwin_seatbelt` mechanism is macOS-only and irrelevant to TALOS's Linux/server deployment. The `linux_bwrap` mechanism is worth watching for light-weight read-class local dev tooling (P7 cockpit agent helpers) but not for the P3 claim-loop workers. The write-path allowlist pattern (`write_paths`) is adoptable as a complement to `readOnlyRoot`.

---

## Session sharing

**Transport protocol.** Not documented in the README, POLICIES.md, or any publicly accessible source at time of research. The server runs on `localhost:6767` and exposes a REST API with an OpenAPI spec (`openapi.json`). Real-time session sync across terminal, web UI, desktop app, and mobile is described as a product capability, implying a persistent connection (WebSocket or SSE) behind the scenes, but the specific protocol is not disclosed in public alpha docs.

**How collaborators steer a live session.** A session is shared via URL invitation. Collaborators can:
- View the live session (read-only observation)
- Comment on files within the session
- Send commands (co-drive the session)
- Fork the session at a conversation branch point (`omnigent run --fork <session_id>`)

The forking capability is notable: a reviewer can branch the session history at any point, creating a parallel thread that can be merged or discarded without affecting the original session.

**Gate/approval in shared session flow.** No concept equivalent to a gate UI is documented in Omnigent's session sharing. Collaboration is co-driving semantics — any invited user with access can send commands. There is no documented role distinction between owner, reviewer, and approver within a session. The cost-gate ASK state pauses the session for the primary user (the session owner), but it is not clear whether a separate approver role is enforced.

**Mapping to TALOS's P7 cockpit.** TALOS's P7 design centers on an append-only event log (temporal replay and scrubbing), a gate UI with 5 outcomes (Approve / Reject-with-reason / Waive-with-justification / Edit-inline / Escalate), and a three-level drill-down task view. Omnigent's session history provides a chronological view (the equivalent of the event log) and fork semantics (the equivalent of branching a review decision). However, Omnigent has no gate UI, no distinction between owner/reviewer/approver roles, and no durable state transition that records a human decision for audit. The session timeline without role-gated approval is insufficient for TALOS's Guardian doctrine ("nothing is written to a live system without a human's approval" recorded in `tasks.approved_at`).

**Verdict:** adapt — the fork-at-branch-point capability is worth examining for P7's Edit-inline gate outcome (allowing the reviewer to branch a task's execution history and re-run from a decision point). The URL-based sharing model is simpler than TALOS needs (no role distinction), but the underlying session-sync architecture could inform the real-time event-log streaming design. Do not adopt the co-driving model without adding owner/reviewer/approver role enforcement.

---

## Agent composition and YAML specs

**YAML agent spec fields.** From the README, POLICIES.md, and the shashikantjagtap.net deep-dive, the following fields are documented:

```yaml
spec_version: 1                          # schema version
name: string                             # agent identifier
prompt: string                           # system instructions (inline or file path)
instructions: .metaharness/AGENTS.md    # alternative: file-based instructions

executor:
  harness: [claude-sdk | codex | codex-native | claude-native | openai-agents | pi]
  # Note: codex-native and claude-native use the CLI wrappers;
  # claude-sdk and openai-agents use the Python SDK

os_env:
  type: caller_process                   # inherit parent process environment
  cwd: /workspace/path                   # working directory
  sandbox:
    type: [darwin_seatbelt | linux_bwrap]
    allow_network: [true | false]
    write_paths:                         # explicit filesystem write allowlist
      - AGENTS.md
      - config.yaml

tools:
  <tool_name>:
    type: [function | agent]             # local Python callable or sub-agent delegation
    callable: module.path.function       # for type: function
    handler: omnigent.policies.builtins.*  # for policy tools
    factory_params: {...}                # kwargs passed to the handler factory

policies:
  <policy_name>:
    type: function
    handler: omnigent.policies.builtins.*
    factory_params: {...}

budget:                                  # shorthand for cost_budget policy
  type: function
  handler: omnigent.policies.builtins.cost.cost_budget
  factory_params:
    max_cost_usd: 5.00
    ask_thresholds_usd: [3.00]
```

The `tools` section accepts two types: (1) local Python functions, with JSON schema auto-generated from the function signature; (2) sub-agents, enabling supervisor delegation patterns. The same YAML file can declare both tools and sub-agents. `inherit` keyword (referenced but not fully documented in public docs) appears to support tool inheritance across agent specs.

**Multi-harness switching mechanism.** Changing the `executor.harness` field with a one-line edit switches the underlying terminal agent without changing the prompt, tools, or policies. This is the claimed portability mechanism.

**Comparison to TALOS's capability manifest contract.** TALOS's capability manifest (frozen seam at `docs/contracts/capability-manifest.md`) and Omnigent's agent YAML spec address different things:

| Dimension | TALOS capability manifest | Omnigent agent YAML spec |
|---|---|---|
| Purpose | Declares what a capability pack may propose (read/write, tool profiles, safety flags) | Configures how an agent runs (harness, tools, prompt, policies) |
| Scope | Inbound MCP capability registration | Outbound agent execution configuration |
| Safety gate | `write_kind ∈ {offline_artifact, sim_only}` is structural — no live-write tool can exist | `write_paths` is a policy-enforced allowlist — bypass is possible if policy is missing |
| Profile | `profile: read | write` on every tool, fail-closed default | No per-tool profile; policies apply session-wide |
| Validator | Deterministic Python validator, no LLM, no network | No standalone validator documented |
| Version | Frozen seam, cannot change unilaterally | `spec_version: 1`, alpha — may change |

The Omnigent YAML spec is richer in execution configuration (harness choice, sandbox type, working directory, multi-tool delegation) but weaker in capability governance (no per-tool safety classification, no `write_kind` structural gate, no deterministic pre-flight validator). They are complementary, not redundant.

**Verdict:** adapt — the Omnigent YAML spec's executor harness abstraction and tool-delegation pattern are directly applicable to TALOS's P3 dispatcher agent spec (the "pinned skill manifest" referenced in ADR-009). The `write_paths` sandbox allowlist can complement TALOS's Docker `readOnlyRoot`. However, TALOS must not replace its capability manifest with Omnigent's YAML spec: the manifest's structural `write_kind` gate and deterministic validator are non-negotiable safety properties that the YAML spec does not provide.

---

## Key TALOS findings

1. **Policy composition is intersection-only (confirms ADR-009 alignment, P3).** Omnigent's published POLICIES.md confirms a short-circuit DENY model: a denial at any of three levels (server/agent/session) is terminal; lower scopes cannot re-grant. This directly validates TALOS's ADR-009 design. For P3 dispatcher, implement the 8-layer TALOS policy stack with the same short-circuit-DENY semantics. Evaluate session-level policies first (following Omnigent's stricter-first pattern) so that task-pinned skill manifests can impose additional restrictions without waiting for global-floor evaluation.

2. **Soft-threshold + hard-cap cost model is directly portable to the 3-axis budget (P3).** Omnigent's `ask_thresholds_usd` (soft, pause) + `max_cost_usd` (hard, terminate) pattern is the right structure. TALOS should extend it to a 4-field budget object (spend_usd, tokens, tool_calls, elapsed_seconds), each with configurable soft thresholds (→ `review` state) and hard caps (→ `review` state, not silent termination). Use `review` rather than abort so the human gate (ADR-011's 5 outcomes) remains the terminal decision. Touches ADR-009, ADR-011; P3.

3. **`linux_bwrap` is a valid lightweight sandbox primitive for local read-class workers (P3/P4).** Bubblewrap (unprivileged user namespaces) is lighter than Docker for read-class local dev tooling. TALOS currently specifies Docker for all write-class workers (ADR-010). For read-class tasks on developer machines, `linux_bwrap` as an alternative worker backend avoids Docker daemon dependency. Add a `sandbox_kind: [docker | bwrap | daytona]` field to the TALOS worker spec at P3. Write-class workers must remain Docker (`network:none`).

4. **Credential brokering via egress proxy is the right pattern for read-class MCP tool calls (P4/P5).** Omnigent's proxy-based credential injection (withholds secrets from the agent, injects them at the HTTP layer on approved egress) is more secure than passing credentials into the agent environment. TALOS's memory layer (P4) and crystallize phase (P5) will need to call external services (vector stores, Postgres). The proxy-based credential model prevents a compromised orchestrator from exfiltrating credentials even if it can issue arbitrary tool calls. Touches ADR-001, ADR-010; P4.

5. **Fork-at-branch-point as an implementation pattern for Edit-inline gate outcome (P7).** Omnigent's `omnigent run --fork <session_id>` creates a parallel session history from a branch point. TALOS's P7 cockpit needs an Edit-inline gate outcome (ADR-011): the human reviewer modifies the proposed action and re-runs. Implementing this as a session fork (branch the append-only event log at the review decision point, re-run from the branch) is cleaner than in-place mutation of the event log and preserves the original proposed path for audit. Touches ADR-011; P7.

6. **Built-in PII scanning policy is directly adoptable for NEXUS data handling (P3/P5).** Omnigent's `omnigent.policies.builtins.safety` includes PII scanning. NEXUS sessions will process PLC tag descriptions, P&ID schemas, and engineering documents that may contain sensitive site-specific data. A PII/sensitivity scan policy on NEXUS tool-call outputs before they enter the task event log (P3 dispatch) or crystallize into memory (P5) closes a data-hygiene gap TALOS does not currently address. Touches ADR-001, ADR-009; P3/P5.

7. **Risk-scoring accumulation pattern for session-level escalation (P3).** Omnigent's cumulative risk-scoring built-in escalates to ASK/DENY when session-wide risk crosses a threshold (not per-action risk, but accumulated). This is richer than per-call gating and catches sessions that make individually-acceptable calls but collectively cross a risk boundary. TALOS's 5-outcome gate operates per task; a cumulative session risk score could trigger an Escalate outcome without waiting for a budget exhaustion event. Touches ADR-011; P3.

---

## What TALOS should NOT take

1. **Omnigent's network intercept-and-transform sandbox for write-class workers (violates ADR-010).** Omnigent's egress proxy allowlist is softer than `network:none`. Any allowlist can be misconfigured; any proxy can have a bypass. ADR-010's hard requirement is `network:none` + `readOnlyRoot:true` on Docker for write-class workers. No Omnigent pattern relaxes this. A compromised write-class worker with outbound network access could exfiltrate PLC schemas or trigger external side effects before the human gate fires.

2. **Omnigent's co-driving session sharing without role gates (violates Guardian doctrine / ADR-011).** Omnigent allows any invited collaborator to send commands to a live session. TALOS's Guardian doctrine requires that nothing is written to a live system without a human's explicit, role-gated approval recorded in `tasks.approved_at`. Adopting co-driving semantics without owner/reviewer/approver role enforcement would let any session collaborator bypass the gate.

3. **Replacing TALOS's capability manifest with the Omnigent YAML spec (violates ADR-004).** The YAML spec has no per-tool `write_kind` gate and no deterministic pre-flight validator. ADR-004's structural safety guarantee ("no live-write tool can exist in any manifest") is not replicable with Omnigent's policy-enforced `write_paths`. A missing policy is silently absent; a missing tool entry in the capability manifest causes the validator to reject the manifest before it can attach.

4. **Omnigent's union/session-expansion interpretation of "session first" policy evaluation.** Clarification: Omnigent's composition is intersection-only (DENY short-circuits), but the session-evaluates-first order could be misread as "session can override server policy." It cannot. Implementing any TALOS layer that allows session-level grants to expand higher-scope denials would violate ADR-009's "each layer can only restrict, never expand."

5. **Omnigent's three-level trust model as a substitute for TALOS's 8-layer stack (violates ADR-009).** Omnigent has server/agent/session. TALOS has global / board / client / task / pinned-skill-manifest / capability-manifest / write-kind / safety-critic. The extra layers exist for reasons: board isolation (Postgres RLS, hard `board_id` axis), capability manifest (frozen seam, deterministic pre-flight), write-kind gate (structural, not policy), and safety critic (escalate-only, never waivable). Collapsing to three levels loses these structural guarantees.

---

## Open questions for the builder

1. **Policy composition with conflicting ASK outcomes across scopes.** POLICIES.md documents that DENY short-circuits. What happens when two different policies from different scopes both return ASK, but with different prompts or notification channels? Does the first ASK win, do all ASKs queue, or does the runtime merge them? This matters for P3's gate UI design when multiple soft thresholds trigger simultaneously.

2. **Session transport protocol (WebSocket vs. SSE vs. long-poll).** Not documented in any public source. Knowing whether Omnigent uses WebSocket or SSE on `localhost:6767` would inform TALOS P7 cockpit real-time event streaming design. Requires reading the `openapi.json` or network-inspecting a live session.

3. **Policy evaluation on streaming tool-call chunks vs. complete tool calls.** Omnigent's policies evaluate "every action," but terminal agents stream tool calls in chunks. Is the policy check on the complete tool-call object after streaming completes, or does the proxy intercept mid-stream? The answer changes the latency profile of the ASK gate (mid-stream pause vs. post-stream pause).

4. **Linux `linux_bwrap` sandbox and network namespace isolation.** POLICIES.md confirms `linux_bwrap` exists. Does it create a separate network namespace (equivalent to Docker `network:none`) or does it only restrict filesystem writes? Bubblewrap supports `--unshare-net` for full network namespace isolation, but whether Omnigent uses that flag when `allow_network: false` is not documented.

5. **Crash recovery and session resumption semantics.** Omnigent's session re-attach (`omnigent attach <session_id>`) implies crash recovery, but the mechanism is undocumented. Does the session replay from an event log checkpoint, or does re-attach start a fresh conversation with the session's file state? This is the equivalent of TALOS's `attempt_no` increment and checkpoint-log replay, and the answer determines whether mid-task crashes are recoverable without re-running from scratch.

6. **`inherit` keyword in tool declarations.** The GitHub README references an `inherit` keyword for cross-spec tool inheritance but does not document its semantics. Does `inherit` pull tools from a parent spec by reference (late binding) or by copy (snapshot at registration)? Late binding creates a supply-chain risk if the parent spec changes after the child is registered.

7. **Reviewer/approver role in shared sessions.** No documentation of role-based access within a shared session. Can role distinctions (view-only, comment, co-drive, approve) be configured? Without this, session sharing is not safe for TALOS's review gate in multi-user deployments.

8. **Polly multi-agent parallel worktree implementation.** The Polly orchestrator fans out to parallel git worktrees, each running a different harness (Claude Code, Codex, Pi), then cross-reviews diffs. The inter-agent communication mechanism (how Polly receives results from sub-agents, how it routes diffs for cross-review) is not documented. Understanding this would inform TALOS's P3 dispatcher multi-worker fan-out pattern.

---

## Build-phase impact

| Omnigent finding | TALOS phase | Action |
|---|---|---|
| Intersection-only DENY short-circuit confirmed | P3 (dispatcher) | Implement 8-layer policy stack with session-first evaluation order; DENY at any layer short-circuits; log all denials |
| Soft-threshold + hard-cap budget pattern | P3 (dispatcher) | Port as 4-axis budget object (spend_usd, tokens, tool_calls, elapsed_s) per task; exhaustion → `review` state, not abort |
| `linux_bwrap` lightweight sandbox primitive | P3 (dispatcher) | Add `sandbox_kind: [docker | bwrap | daytona]` to worker spec; bwrap for read-class local dev; Docker `network:none` mandatory for write-class |
| Cumulative risk-score escalation policy | P3 (dispatcher) | Implement session-level risk accumulator; threshold crossing → Escalate gate outcome without waiting for budget exhaustion |
| PII scanning built-in policy pattern | P3 (dispatcher) + P5 (crystallize) | Add PII/sensitivity scan on NEXUS tool-call outputs before event log; scan again before crystallize writes to memory |
| Credential brokering via egress proxy | P4 (memory) | Adopt proxy-based credential injection for read-class workers calling external stores; withhold raw creds from agent environment |
| Fork-at-branch-point for session history | P7 (cockpit) | Implement Edit-inline gate outcome as append-only event log branch; preserve original proposed path for audit |
| URL-based session sharing with role gaps | P7 (cockpit) | Adopt URL-sharing model with mandatory owner/reviewer/approver role enforcement; no co-driving without gate clearance |
| Harness executor abstraction (YAML spec) | P3 (dispatcher) + P6 (sim capability) | Port executor harness field to TALOS skill manifest pinned-harness field; sim capability (P6) declares `executor.harness: sim_target` |
| Session fork for parallel worktree review | P3 (dispatcher) | Polly-style fan-out (parallel worktrees, cross-vendor diff review) is a valid P3 multi-worker dispatch pattern for NEXUS analysis tasks |
| `darwin_seatbelt` macOS sandbox | P7 (cockpit) | No impact on server infrastructure; relevant only if TALOS ships a local macOS developer tooling companion (not in roadmap through P8) |
| Omnigent YAML spec as pinned skill manifest | P3 (dispatcher) | Do NOT replace TALOS capability manifest; instead, extend TALOS manifest schema with executor harness field and sandbox_kind from Omnigent YAML |
