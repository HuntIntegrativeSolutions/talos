# ML Integration in TALOS

> **Status:** Research notes · 2026-06-11
> **Purpose:** Catalog where and how machine learning fits into TALOS's architecture, mapped to existing layers. Not an implementation plan — a map of integration points.
>
> **Historical note:** predates ADR-039 (pgvector replaced Chroma as the vector store). Retained
> as written for the historical record; see README.md / ADR-039 for current state.

---

## Guiding principle

ML inside TALOS follows the same **Guardian doctrine** as everything else: a model proposes, deterministic critics gate it, a human approves, and no model ever writes to a live system. Models are read-only capabilities behind MCP — they produce analysis artifacts that ride the same propose → review → approve lifecycle as a NEXUS audit report.

---

## Integration Point 1 — Learned Critics

**Layer:** `critics/` (gate functions)
**Replaces/extends:** Deterministic pass/fail/warn verdicts with model-driven judgment
**Gate impact:** A learned critic returns the same verdict shape (`pass`/`fail`/`warn` + `evidence_uri` + `details`) as a deterministic critic. The gate doesn't know or care which kind produced it.

**Candidates:**

| Critic | Model Type | Data Source | Value |
|---|---|---|---|
| **Anomaly-on-deliverable** — "do these 200 rungs from a unit program match the distribution of patterns seen in verified-safe programs?" | Autoencoder or isolation forest on rung embeddings (NEXUS parses → vectorize → score) | NEXUS graph + historical verified deliverables | Catches logic that *looks wrong* even if no deterministic rule fires |
| **Alarm-flood classifier** — "would this alarm configuration produce nuisance floods?" | Classifier on alarm attribute vectors (type, deadband, priority, area density) | Existing alarm configuration + alarm historian | Prevents flood conditions before they're deployed |
| **ISA-101 palette compliance** — "do these HMI screens meet the plant's visual standard?" | Vision model (ViT or small CNN) on HMI screen renders | FT View ME screen exports | Catches palette drift in agent-proposed screens |
| **Narrative consistency** — "does this FDS narrative match the referenced rung logic?" | Cross-encoder (text vs. parsed logic embedding) | NEXUS parsed L5X + generated narrative | Catches spec-vs-implementation mismatch before human review |

**Interface change required:** `task_gate_results` already stores `verdict`, `evidence_uri`, and `details` (JSONB). A learned critic adds a `model_name` and `model_version` field to `details` for audit trail. No schema change needed — `details` is already JSONB.

**Provisioning:** Learned critics register in the `critics/` manifest alongside deterministic ones, with an optional `model_endpoint` field. The engine runs them the same way — call endpoint → record result → check gate.

---

## Integration Point 2 — Orchestrator Evaluator

**Layer:** Orchestration (Strategy Ladder)
**Already ML per BLUEPRINT:** The gate-bound evaluator is described as "a cheap model that judges after each turn and picks the next ladder step."

**ML sub-components within the evaluator:**

| Function | What it does | Model |
|---|---|---|
| **Complexity estimation** | Decides ladder depth: short-circuit simple tasks (research→deliverable) vs. full 6-step | Light classifier (fastText or logistic regression on task title + tags + board history) |
| **Turn evaluation** | Given the worker's last output: is the deliverable ready for gate, does it need another turn, or is it off-track? | Small LLM or classifier — the "Ralph" judge from Hermes |
| **Route selection** | Which profile/model to dispatch for the next step given task characteristics | Lookup table at first; learned ranker with enough history |

**Parking Lot resolution:** The question *"planner autonomy threshold — how is it computed, who tunes it?"* is the complexity estimator above. Train from historical board data: task attributes → actual ladder depth used → outcome (passed gate / rework / rejected).

---

## Integration Point 3 — Memory

**Layer:** `memory/` (polyglot memory)
**Already planned:** Three ML components are already in BLUEPRINT Phase 4.

| ML Component | Status (from BLUEPRINT) | Notes |
|---|---|---|
| **Embeddings for semantic search** | Planned — pgvector or Chroma | Standard text-embedding model (NEXUS output → embedding → index). Needed for hybrid FTS5+vector retrieval. |
| **Consolidation pipeline** | Planned — Agent Zero pattern | LLM-mediated: similarity threshold → MERGE / REPLACE / KEEP_SEPARATE / UPDATE decision. **Gated by scope**: within one `client_scope` runs autonomously; cross-scope MERGE is forbidden; anything touching a safety node is a proposal to the gate. |
| **PageRank context map** | Planned — Aider pattern, Phase 4 | Personalized PageRank over the NEXUS graph, seeded by task tags/routines. Not a learned model but a graph algorithm. Yields ~1k-token relevance map injected into the planner. NetworkX on subgraphs <500 nodes; defer Cypher GDS for scale. |

**Additional ML opportunity — consolidation quality critic.** Before a consolidation auto-write executes, a cheap critic verifies: "does the merged entry preserve all factual claims from both sources without contradiction?" If the critic fails, the consolidation is staged for human review instead of auto-applied.

---

## Integration Point 4 — Crystallize (Trajectory Learning)

**Layer:** Orchestration (Strategy Ladder, step 6)
**What it does:** Turn a successful task trajectory into a re-usable skill and a new path in the Strategy Graph.

**The learning problem:**
- **Input:** task spec + tool call sequence + intermediate artifacts + critic verdicts + human outcome (approve/reject/edit)
- **Output:** structured skill definition (SKILL.md propose→review→pin) + graph path update
- **Model job:** Extract the reusable pattern, strip instance-specific details (client names, tag numbers, dates), classify the scope (`client` vs. `shared`)

**Implementation shape:**
- Each successful trajectory is a structured log: `task_events` (ordered by `created_at`) + `task_gate_results` + final artifact
- Crystallize = compile that log into a **proposed** skill (not yet trusted)
- The skill proposal goes through the **same gate** as everything else — critics evaluate it, human approves or rejects
- A rejected crystallize is not lost; it stays in the event log as a failed trajectory and can inform future model training

**This is the ML loop that compounds:** every gated approval generates a training signal for the complexity estimator, the evaluator, and the crystallizer.

---

## Integration Point 5 — Domain ML Inside Capability Packs

**Layer:** MCP capability packs (NEXUS domain)
**Architecture:** Models live behind the MCP boundary, exactly like NEXUS analysis tools. They are `read`-profile capabilities — produce analysis artifacts, never side effects.

**Candidate models for NEXUS as the first ML-capable pack:**

| Model | Input | Output | Runs on |
|---|---|---|---|
| **Tag-trend anomaly detection** | Historian tag history (read-only) | "PID_UnitTemp diverging since 14:23 — 92nd percentile of historical deviation" | Edge workstation (Ollama / sklearn) |
| **Equipment health classifier** | Trend data + cross-reference with maintenance records | "Unit_IDF bearing: degraded (87% confidence) — 3 features outside expected range" | Edge or mothership |
| **Process parameter forecaster** | 30-day tag history for a loop | "Baghouse DP reaches high limit in ~6h at current rate" | Edge (lightweight — Prophet or similar) |
| **Alarm pattern miner** | Alarm/event historian export | "Recurring compressor surge cluster at shift change — 14 events, mean interval 11min" | Edge |
| **Vision: panel/meter reader** | Camera stills from edge workstation | "Indicator 2A-F1 shows amber — verify per PM-Schedule-04" | Edge (small ViT) |

**Integration pattern:**
```
NEXUS MCP tool call (read) → model inference → structured analysis artifact
    → attached to task as `task_attachments` with `content_type: application/vnd.talos.ml-analysis+json`
    → task enters `review` status
    → critics evaluate (including a "model-confidence-threshold" critic)
    → human approves / rejects / edits
```

**Key rule:** A model is an MCP tool with `profile: read`. It cannot write to a historian, change a setpoint, or modify a PLC program. The proposal pattern from NEXUS applies unchanged.

---

## Integration Point 6 — PM Layer Prediction

**Layer:** Project management (on top of board engine)
**Data source:** The board event log (`task_events` + `task_gate_results` + `task_runs`) accumulates training data with every task.

**Predictive models once the board has cross-project history:**

| Prediction | Features | Model | Consumed by |
|---|---|---|---|
| **Schedule risk** — "tasks on board ACME-007 have 3× average rework rate" | Task type, board, client, tags, prior rework count, critic outcomes | Gradient boosting (XGBoost/LightGBM) | Cockpit project-economics gauge |
| **Budget burn** — "at current rate, 3-axis budget exhausts before gate opens" | Runtime per step, model cost per invocation, remaining task count | Time-series (ARIMA or simple extrapolation) | Cockpit + gateway alerts |
| **Critic failure prediction** — "for this deliverable type, critic X has 40% first-pass failure" | Deliverable type, client, prior task similarity, critic name | Logistic regression or lookup table (small data) | Planner — pre-allocate reviewer time |
| **Gate outcome prediction** — "this task is 70% likely to be rejected at gate" | All of the above + current critic partial results | Gradient boosting | Cockpit — highlight high-risk tasks |

**Note:** These are small models. They don't need GPUs or large pipelines. Feature engineering from the board's event log + `task_gate_results` is the bulk of the work.

---

## Recommended First Step

**Phase 2 scope** — before any model runs, define the *learned critic interface*:

1. Extend the critics manifest schema to include `model_endpoint` and `model_version` fields
2. Decide: does the engine call the endpoint synchronously (blocking gate evaluation) or asynchronously (critic runs in background, gate re-evaluates on arrival)?
3. Ensure `task_gate_results.details` captures model provenance (`model_name`, `model_version`, `confidence`, `input_snapshot`)

This gives TALOS the ability to slot in a learned critic without any schema changes later — the ML work becomes pure model development, not integration plumbing.

After that, the highest-value first model is **tag-trend anomaly detection** inside NEXUS: it uses data already accessible to NEXUS (tag history via read-only historian query), produces an analysis artifact in the existing `review` lifecycle, and produces something a human can immediately verify or reject.

---

## Relationship to Existing Architecture

```
┌─────────────────────────────────────────────────┐
│                   Cockpit                        │
│    (project-economics gauge, risk indicators)    │
├─────────────────────────────────────────────────┤
│               PM Layer (Phase 3)                 │
│    schedule/burn/gate-outcome prediction         │
├─────────────────────────────────────────────────┤
│    Orchestration (gate-bound evaluator)          │
│    └─ complexity estimator                       │
│    └─ turn evaluator (Ralph)                     │
│    └─ route selector                             │
├───────────────────────┬─────────────────────────┤
│     Critics (Phase 2) │  Memory (Phase 4)        │
│  ┌ learned critics ─┐ │  ┌ embeddings           │
│  │ anomaly-on-deliv │ │  │ consolidation LLM    │
│  │ ISA-101 vision   │ │  │ PageRank context     │
│  │ narrative check  │ │  │ hybrid search        │
│  └──────────────────┘ │  └──────────────────────│
├───────────────────────┴─────────────────────────┤
│   MCP Boundary (security + capability edge)      │
├─────────────────────────────────────────────────┤
│          NEXUS (with ML tools)                   │
│    ┌ anomaly detection ┐                        │
│    │ health classifier │  ┌ other packs ─┐      │
│    │ forecaster        │  │ (future)     │      │
│    │ alarm miner       │  └─────────────┘      │
│    └───────────────────┘                        │
└─────────────────────────────────────────────────┘
```

The ML system is not a separate layer — it's a cross-cutting concern that manifests in four existing TALOS layers (critics, orchestrator, memory, capabilities), plus one adjacent layer (PM prediction), all under the same gate.
