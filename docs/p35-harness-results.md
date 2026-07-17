# P3.5 Harness Results — real + stub evidence for the four exit criteria

> **Historical note:** this record predates the P5.5 rename of `max_tool_calls` to
> `max_model_invocations`. Retained as written for the historical record.

Evidence references task/run/board IDs, timestamps, and counts only. No NDA plant data
(tag names, rung content, generated documentation text) is reproduced here — NEXUS
document content referenced below lives only in NEXUS's own derived-artifact store on
its own host, never copied into this repo.

All live runs used a temporary, disposable Postgres 16 container (testcontainers),
bootstrapped identically to `talos/tests/conftest.py`'s `pg_setup` fixture, against the
real NEXUS MCP server at `http://10.0.0.80:8765/mcp` and a real Claude Agent SDK call
(model `claude-haiku-4-5-20251001`). Captured 2026-07-05.

## Criterion 1 — no reclaim double-apply

- **Stub** (CI): `talos/tests/test_p35_harness.py::test_no_reclaim_during_long_nexus_call`.
- **Real**: board `p35-live-board`, task `p35-live-task-2`, run `id=1`, `attempt_no=1`.
  `TALOS_HEARTBEAT_INTERVAL_S` shortened to 2s for observability; `reclaim_dead_workers()`
  was force-invoked while the task's real `full_plc_documentation` call (against a real,
  already-ingested client PLC — identifier withheld per NDA; see local, uncommitted run
  notes) was still in flight, after 2 distinct heartbeats had already been observed.
  Result: `reclaimed = 0`. `task_runs` row count for the task after completion: 1 (no
  duplicate run created). Task reached `status='review'` with no error.

## Criterion 2 — heartbeat during long NEXUS call

- **Stub** (CI): `talos/tests/test_p35_harness.py::test_heartbeat_beats_during_long_node`.
- **Real**: same run as above (board `p35-live-board`, task `p35-live-task-2`). 8 distinct
  `last_heartbeat_at` values observed over the ~15-second real `full_plc_documentation`
  call, confirming the background heartbeat thread (b153476) advances independent of node
  boundaries even under real NEXUS/model latency.

## Criterion 3 — model fallback on primary failure

- **Stub** (CI): `talos/tests/test_p35_harness.py::test_fallback_on_primary_failure_end_to_end`
  (drives the real `_call_with_fallback` path in `talos/graph/spine.py` through
  `claim_and_run` end-to-end, with `talos.llm.call_model` mocked to fail for the resolved
  primary model and succeed for the fallback).
- **Real**: direct `talos.llm.call_model` proof (no mocking) — an invalid model id
  (`not-a-real-model-id`) raised `ModelCallError` cleanly via the real Claude Agent SDK
  path (`_async_call`); the real fallback model (`claude-haiku-4-5-20251001`) then
  succeeded normally in a separate call, confirming the SDK's real failure/success
  behavior matches what `_call_with_fallback`'s try/except loop expects.

## Criterion 4 — budget hard cap escalates, not a crash

- **Stub** (CI): `talos/tests/test_p35_harness.py::test_budget_hard_cap_end_to_end` (drives
  `claim_and_run` with `initial_budget={"max_tokens": 1, ...}` end-to-end; the real
  `BudgetExhaustedError` raise path added to `read_node` in `talos/graph/spine.py`, not a
  directly-constructed exception).
- **Real**: board `p35-live-budget-board`, task `p35-live-budget-task`, `initial_budget`
  with `max_tokens=1`, real (cheap, non-tool) Claude Agent SDK call. Real token usage
  returned by the SDK was 4 tokens, exceeding the cap. `BudgetExhaustedError` was raised
  from `read_node`'s real budget-check logic (not constructed directly), caught, and
  handled by `talos.worker._handle_budget_exhaustion` exactly as the production
  `_worker_slot` exception handler would. Result: `tasks.status='review'`,
  `task_runs.outcome='budget_exhausted'`, `attempt_no` unchanged at 1 — no crash, no loop.

## Manifest verification evidence (Step 2.2 — `reconcile_descriptions`)

- PLC used: a real, already-ingested client PLC (identifier withheld per NDA — see local,
  uncommitted run notes; 2001 tags with `description_sources` entries).
- Two consecutive full `reconcile_descriptions(plc_id=...)` calls against that PLC produced
  byte-identical output (sha256 `26743807b0f26e1b25e7d129df160ec7e72666ffff72e10c87f34a19a0cfe34e`
  for both, 1,336,114 characters, 2001 result rows).
- Independent per-tag `tag_context` reads for two sampled tags (identifiers withheld) were
  field-for-field identical before/after, including `description_confidence`,
  `description_verified_by` (null), and `verified_at` (null).
- Plant-wide `plant_summary.description_confidence_summary` was unchanged before/after
  (`verified: 31, imported_likely: 605, imported_unverified: 0, suspected_wrong: 0,
  no_description: 283`); `last_indexed` timestamp also unchanged, ruling out a reindex
  side effect.
- Conclusion: no mutation on any observed path. Reclassified EXCLUDED → `read` in
  `capabilities/nexus/manifest.json`; `capability.content_hash` recomputed via
  `talos.validators.capability_manifest.compute_manifest_hash()`.

## full_plc_documentation live invocation (independent confirmation)

Verified via a direct `nexus_status`/`list_documented_plcs` check (not part of the TALOS
test suite) that both P3.5 live runs genuinely invoked `full_plc_documentation` against the
same real client PLC (identifier withheld) through the real MCP wiring —
`list_documented_plcs` showed `document_count=7` (types: anomaly_report, io_map,
master_index, migration_assessment, pid_analysis, program_structure,
sequence_of_operations) with `last_generated` advancing between the two runs, confirming
the tool re-executed for real each time rather than returning a cached response.

## Deferred items (see ADR-038 "P3.5 harness scope note")

- ADR-032/034's DB-pinned `boards.manifest_hash`/`boards.manifest_json` check — requires a
  schema migration, not undertaken in P3.5. What's implemented instead:
  `talos.nexus_client.manifest_selfcheck()`, a disk-file self-consistency check run once at
  `talos.worker.run_dispatcher` startup (non-board-scoped, non-transactional).
- ADR-033's full Layer 1 `PolicyViolation`-raising `PreToolUse` hook class (no
  `task_events` denial logging, no safety-critic-chain verification) — not built. What's
  implemented instead: `talos.nexus_client.allowed_nexus_tools()`, a coarser
  deny-by-default `allowed_tools` list filter built from the manifest.
- ADR-033's Layer 2 MCP gateway proxy (external enforcement boundary) — not built; the
  stdio-pipe-wrapper design in ADR-033 doesn't apply to the real HTTP transport anyway
  (ADR-038). A compromised TALOS orchestrator could bypass the Layer 1 filter by calling
  NEXUS's HTTP endpoint directly — a known, accepted gap for P3.5 (see ADR-038's security
  posture section).
- Per-task/per-plan write-grant check — not built; all manifest `write:offline_artifact`
  tools are allowed unconditionally (`allowed_nexus_tools(..., write_grant=True)` is the
  only value used), since no NEXUS write tool reaches a live device regardless.
- ADR-031 multi-provider LLM config — out of scope for P3.5, untouched.
- `budget["tool_calls"]`/`max_tool_calls` count model invocations (calls to
  `call_model`), not individual MCP tool calls the model makes within one invocation — the
  Claude Agent SDK's `ResultMessage` doesn't expose a per-MCP-tool-call count. Only
  `max_tokens` is a faithfully-enforced cap in the current implementation; the live budget
  evidence above exercises `max_tokens`, not `max_tool_calls`.

## Live-server residue from this harness

Both P3.5 live runs (criteria 1–2 and the manifest-verification runs) executed real,
non-destructive calls against the shared NEXUS host. `full_plc_documentation` regenerated
7 derived document types for the target PLC (previously undocumented — `list_documented_plcs`
returned empty for it before these runs). This is regenerable derived-artifact data, not a
fact-SoR write, and the runs were explicitly authorized — but it is a real change to shared
NEXUS state as a side effect of this harness. Flagging for the user to decide whether to
leave it (harmless, regenerable) or clear it.
