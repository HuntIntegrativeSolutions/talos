# ADR-022: Observability and span-level tracing — task_spans table, P3 minimum set, woven into each phase

**Status:** Accepted
**Date:** 2026-06-14
**Deciders:** Hunt Integrative Solutions LLC

## Context

Agentic systems are opaque by default: a task fails or slows and there is no structured record of
which step took how long, which model was called, or where the gate stalled. The chase-ai pattern
is "instrument first, surface second" — build the structured telemetry infrastructure before
building the dashboard, so the cockpit (P7) has queryable data when it ships.

TALOS needs span-level tracing at every significant boundary: worker lifecycle, spine node
transitions, critic evaluations, gate transitions, and LLM calls. ADR-017 further requires that
every LLM call is egress-auditable — which model (and which provider/endpoint) received PLC
context.

The existing `task_events` table is an append-only audit log. Spans are operational telemetry.
Conflating them pollutes both: the audit log becomes hard to read, and the telemetry table
inherits audit semantics it doesn't need.

## Decision

**Write spans to a new `task_spans` table separated from `task_events`. Instrument P3 with
the full dispatcher + spine + gate + LLM span set. Wire webhook alerting for gate escalations in
P3. Weave instrumentation into each phase as it ships.**

### task_spans table

```sql
CREATE TABLE task_spans (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    board_id        UUID NOT NULL REFERENCES boards(id),
    task_id         UUID REFERENCES tasks(id),
    run_id          BIGINT REFERENCES task_runs(id),
    parent_span_id  BIGINT REFERENCES task_spans(id),
    span_name       TEXT NOT NULL,
    model_id        TEXT,              -- opaque model string; set for LLM call spans
    provider        TEXT,              -- resolved provider/endpoint at call time (ADR-017)
    prompt_tokens   INTEGER,
    completion_tokens INTEGER,
    latency_ms      INTEGER,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at        TIMESTAMPTZ,
    payload         JSONB,             -- span-type-specific metadata
    otlp_exported_at TIMESTAMPTZ       -- NULL until OTLP exporter is wired (P7)
);

-- RLS: same policies as task_events (board_isolation + admin_bypass)
ALTER TABLE task_spans ENABLE ROW LEVEL SECURITY;

CREATE POLICY board_isolation ON task_spans
    USING (board_id = current_setting('app.board_id', true)::uuid);

CREATE POLICY admin_bypass ON task_spans
    TO talos_admin USING (true);
```

The `admin_bypass` policy allows cross-board observability queries for operators and the cockpit
(P7). Without it, `task_spans` is a cross-board data leak through the RLS gap.

### P3 minimum span set

Every span below ships **woven into P3** — not as a separate instrumentation pass.

**Dispatcher spans (P3a):**
- `worker.claim` — successful task claim (board_id, task_id, run_id, attempt_no)
- `worker.claim_race_loss` — another worker claimed first (board_id, task_id)
- `worker.heartbeat_miss` — dispatcher detected a missed heartbeat (task_id, run_id, miss_count)
- `worker.reclaim` — dead-worker reclaim fired (task_id, old_run_id, new_run_id)

**Spine node spans (P3b):**
- `spine.node.{name}.entry` / `spine.node.{name}.exit` — for each of read_node,
  deliverable_node, gate_node, post_gate_node
- `spine.critic.{name}` — one span per critic run, with `passed: bool`, `verdict: str`
- `spine.gate.interrupt` — the `interrupt()` fires; human review begins
- `spine.gate.resume` — the human has submitted a gate outcome; resume begins
- `spine.post_gate.write` — idempotent side-effect write confirmed

**LLM call spans (P3b, every model invocation):**
- `llm.call` — with `model_id` (opaque string), `provider` (resolved endpoint), `prompt_tokens`,
  `completion_tokens`, `latency_ms`

The `provider` field satisfies ADR-017's egress audit requirement. Even though the model string
is opaque in config (ADR-018), the LLM client layer resolves the actual provider/endpoint at call
time and the span captures it self-contained, without needing to correlate to config state.

### Alerting in P3

P3 ships **webhook-based alerting for gate escalations only**. When `gate_outcome = escalate`,
TALOS fires an HTTP POST to `TALOS_ESCALATION_WEBHOOK_URL` (configured in `talos.toml` or env
var). No other alert types in P3. Broader alerting (critic failure rate thresholds, span timeout
alerts, multi-channel notifications) is deferred to P7 (cockpit).

### Instrumentation strategy

Instrumentation is **woven into each phase as it ships**:
- P3 ships with the P3 span set above.
- P4 adds spans for memory federation operations (Graphiti ingest, vector store writes, Redis ops).
- P5 adds spans for verifier critic runs and rule extraction episodes.
- P7 (cockpit) wires the OTLP exporter and sets `otlp_exported_at` on exported spans.

No dedicated standalone instrumentation phase. Deferred instrumentation never ships.

## Options considered

- **A — Append to `task_events`.** No new table; reuses existing RLS. Rejected: pollutes audit
  log narrative; cross-board observability requires same admin bypass anyway, gaining nothing.
- **B — `task_spans` table (chosen).** Clean separation; dedicated RLS policies; forward-compatible
  with OTLP via `otlp_exported_at`.
- **C — OTLP only (no Postgres storage).** External dependency (Jaeger, Honeycomb, Datadog)
  before there is a consumer. Rejected: P3 ships spans to Postgres first; OTLP is a future add.

## Consequences

- **Easier:** structured, queryable telemetry from P3 onward; egress audit (model + provider)
  self-contained in each span; cockpit can query spans without reading raw logs.
- **Harder:** new table + RLS setup; webhook URL must be configured for escalation alerts;
  every LLM call must resolve and capture provider at call time.
- **Revisit:** OTLP exporter and external collector in P7; per-board alert threshold
  configuration in P7.

## What this closes

- Satisfies ADR-017 egress audit via `provider` field in LLM call spans.
- Provides the data substrate for the P7 cockpit's task-timeline and latency views.

## Action items

1. [ ] Add `task_spans` table and RLS policies to `schema-p3.sql`.
2. [ ] Implement span writer utility (`platform/telemetry.py`) for use across all nodes.
3. [ ] Instrument P3a dispatcher with claim, claim-race, heartbeat-miss, and reclaim spans.
4. [ ] Instrument P3b spine nodes, critics, and gate transitions with spans.
5. [ ] Capture `model_id` and `provider` on every LLM call span.
6. [ ] Add `TALOS_ESCALATION_WEBHOOK_URL` to `talos.toml` and wire in post-gate node.
