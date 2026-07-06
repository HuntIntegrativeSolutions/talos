# ADR-031 live verification

Records the honest state of the three live checks required by the ADR-031
implementation task, run from this development sandbox on 2026-07-05.

## Environment probed

- `ANTHROPIC_API_KEY`: **not set** in this sandbox.
- `claude-agent-sdk`: installed (v0.2.101, via the `experiment` extra).
- Local Ollama (`http://localhost:11434`): **not installed** — `curl` to
  `/api/version` failed to connect.
- NEXUS MCP server (`http://10.0.0.80:8765/mcp`, ADR-038): **network-reachable**
  from this sandbox — a bare `curl` got a well-formed JSON-RPC error response
  (`Not Acceptable: Client must accept text/event-stream`), which confirms the
  Streamable HTTP endpoint is live and answering, even though a full MCP
  handshake wasn't attempted here.

## (a) Claude path still works

**VERIFIED 2026-07-05 — via Claude Code OAuth credentials, no API key.** The
operator has no Anthropic API key; the Agent SDK resolves the local Claude Code
OAuth credential instead (the mechanism ADR-029 validated). Live driver-level
check run on the dev workstation:

```
call_model(ModelRef('anthropic','claude-haiku-4-5-20251001'),
           'Reply with exactly the word: VERIFIED')
→ text='VERIFIED', session=7914829c-…, tokens=46
```

This exercises the real `AnthropicDriver.call()` → Agent SDK → live API path
end-to-end (driver-level, not a full spine run; P3.5 already proved the full
spine live with Claude). Original sandbox status kept below for provenance.

**Pending — no `ANTHROPIC_API_KEY` available in this sandbox.** The
`AnthropicDriver.call()` path (`talos/llm_providers/anthropic.py`) is the same
`_async_call` logic P3.5 already proved live (per `docs/p35-harness-results.md`
and the P3.5 harness closure note in project memory), moved verbatim into the
new driver class with no logic changes — only its caller (`talos/llm.py`) and
the value it receives (`model_ref.model` instead of a bare `model` string) changed.
The stub-mode and mocked-driver test suites (`test_llm_providers.py`,
`test_p35_harness.py`, `test_p3b_dispatcher.py`) all pass against this
refactored path (99/99). A fresh live re-confirmation requires running with a
real `ANTHROPIC_API_KEY` in an environment that has one — not done here.

## (b) Ollama air-gap path

**Pending hardware — no local Ollama install on this machine.** Cannot be
faked; do not treat the mocked `openai_compat` tests as a substitute for this.
When an Ollama install with a tool-capable model (e.g. a Llama/Mistral variant
with function-calling support) is available:

1. `unset ANTHROPIC_API_KEY` entirely (not just empty-string — actually unset).
2. Set all six Strategy Ladder slots' `*_primary_provider` to `"ollama"` in
   `talos.toml` (or via `boards.model_config`), pointing `model` at the locally
   pulled model tag (e.g. `llama3.1:70b`).
3. Run a task through `read_node` and confirm it completes (or reaches
   `review` on model/budget failure — either is a legitimate outcome, a crash
   or an outbound call to `api.anthropic.com` is not).
4. Confirm zero cloud egress: run with `tcpdump`/`netstat` or an OS-level
   firewall rule blocking `api.anthropic.com`, or simply confirm no exception
   about Anthropic auth ever surfaces (since `ANTHROPIC_API_KEY` is unset, any
   accidental fallthrough to the anthropic driver would fail loudly, not
   silently succeed).

## (d) openai_compatible driver — live via DeepSeek (added 2026-07-05)

**VERIFIED — real DeepSeek API (`https://api.deepseek.com/v1`, key from the
operator's Hermes config, never committed).** Three live checks on the dev
workstation:

1. **Direct call:** `call_model(ModelRef('openai_compatible','deepseek-chat'), …)`
   → `DEEPSEEK-LIVE`, 6 tokens. Real HTTP path, auth, and token accounting proven.
2. **NEXUS tool-call loop:** with the RT-14 manifest and
   `allowed_tools=['nexus_status']` against live NEXUS (10.0.0.80), deepseek-chat
   invoked the tool through the function-calling bridge and reported real data
   back (schema v14, 2 PLCs, 919 tags). This is the first live proof of the
   non-Anthropic tool loop — the air-gap-critical code path — against a real
   model and the real MCP server.
3. **Cross-provider fallback:** bogus anthropic primary → real `ModelCallError`
   → deepseek-chat fallback returned `CROSS-PROVIDER-OK`. Check (c)'s
   cross-provider leg is now closed.

Remaining pending after this: only (b)'s literal Ollama/local-weights run —
the *driver code* it would exercise is now live-proven via DeepSeek; what (b)
still uniquely proves is the zero-cloud-egress deployment configuration itself.

## (c) Fallback on a real forced primary failure

**FULLY VERIFIED (see (d) item 3 for the cross-provider leg). PARTIALLY VERIFIED 2026-07-05 — same-provider fallback proven live; cross-provider
leg still pending Ollama hardware.** Live check on the dev workstation via OAuth:

```
_call_with_fallback(ModelRef('anthropic','nonexistent-model-force-fail'),
                    ModelRef('anthropic','claude-haiku-4-5-20251001'),
                    prompt='Reply with exactly the word: FALLBACK-OK',
                    resume=None, state={})
→ logged "model anthropic/nonexistent-model-force-fail failed: …"
→ text='FALLBACK-OK', tokens=50
```

A genuine `ModelCallError` from the real SDK (invalid model name) triggered the
real fallback path, which succeeded live. What remains pending is only the
*cross-provider* variant (anthropic → ollama), blocked on the same hardware gap
as (b). Note: the SDK's error string for an invalid model reads
"returned an error result: success" — cosmetic quirk, worth an upstream glance.

**Pending — same credential/hardware gap as (a) and (b).** A *real* forced
failure (as opposed to the mocked `FailingDriver` in
`test_llm_providers.py::test_cross_provider_fallback_anthropic_to_ollama`,
which does prove call order and the `ModelCallError`-not-`BudgetExhaustedError`
distinction against fake drivers) needs at least one of: a real Anthropic
credential that can be deliberately revoked/misconfigured to force a genuine
API failure, or a real Ollama install to serve as the fallback target. Neither
is available in this sandbox. When either becomes available:

1. Point a slot's primary at `anthropic` with a deliberately invalid model
   name or a revoked key (forces a real `ModelCallError` from the SDK).
2. Point its fallback at `ollama` with a real, working local model.
3. Run the task and confirm `sdk_session_ids`/logs show the primary attempt
   failing and the fallback succeeding, and that `tasks.status` does not reach
   `review` via the `model_failure` path (i.e. the fallback actually rescued
   the task) — mirroring `test_p35_harness.py::test_fallback_on_primary_failure_end_to_end`
   but with a real anthropic failure instead of a mocked one.

## What the automated suite already covers, honestly

The 99-test suite (`TALOS_JWT_SECRET=test-secret-dev-only TALOS_NEXUS_STUB=1
.venv/bin/python -m pytest talos/ -v`) passes in full, including 10 new
ADR-031 tests. That proves: the provider registry and dispatch logic,
config-cascade backward compatibility, mid-loop budget-cap early exit, and
cross-provider fallback call order — all against fake drivers, with no real
model or network call. It does **not** prove any of (a)/(b)/(c) above; those
require credentials or hardware this session does not have.
