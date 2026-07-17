# TALOS P2 — Critics & the Five-Outcome Gate

> **Historical note:** this prompt predates the `platform/` → `talos/` rename; current code lives
> in `talos/`. Retained as written for the historical record.

## What this is

A precise implementation prompt for Phase 2 of the TALOS build sequence
(`docs/integration/04_build_sequence.md` §1). P1 (single-worker spine) is complete;
all 6 P1 tests pass. P2 completes the gate.

**Run to confirm P1 before starting:**
```bash
cd /mnt/i/talos && .venv/bin/python -m pytest platform/tests/test_spine.py -v
```
All six must be green. If any fail, stop and fix them before proceeding.

---

## Goal

P2 delivers the complete gate: deterministic critic registry, all five outcomes,
safety-class → non-waivable enforced **structurally** (not by convention), learned
critics advisory-only, fail-closed citation path, and a contradiction filter before
the human queue.

**Red-team items closed by P2:** RT-02, RT-03, RT-05, RT-29.

---

## Read these files before writing a single line of code

In this order — the implementation decisions follow directly from them:

1. `docs/integration/04_build_sequence.md` §1 (P2 row) and §4 (P1 detail for context)
2. `docs/decisions/ADR-011-gate-outcomes.md` — five outcomes, safety escalate-only, solo-operator waiver
3. `docs/integration/03_redteam_review.md` RT-02, RT-03, RT-05, RT-29 (lines 42–43, 45, 69)
4. `engine/schema.sql` lines 165–200 (`task_gate_results`, `v_gate_status`)
5. `platform/critics/citations_resolvable.py` — what already exists
6. `platform/graph/spine.py` — the current 4-node graph and `SpineState`
7. `platform/api.py` — the current gate endpoint (POST `/boards/{board_id}/tasks/{task_id}/gate`)
8. `platform/tests/conftest.py` — how schema is applied and how `SCHEMA_FILES` works

---

## Schema additions — `engine/schema-p2.sql`

Create this new file. It applies **on top of** `schema.sql` and `schema-additions.sql`.
The conftest (step 7 below) will apply all three files in order.

```sql
-- =============================================================================
-- TALOS P2 schema additions — Critics & five-outcome gate
-- =============================================================================

-- 1. Extend verdict types to include 'waived'
--    Allows the registry to record waivers as a verdict, not just a details blob.
ALTER TABLE task_gate_results DROP CONSTRAINT task_gate_results_verdict_check;
ALTER TABLE task_gate_results ADD CONSTRAINT task_gate_results_verdict_check
    CHECK (verdict IN ('pass', 'fail', 'warn', 'waived'));

-- 2. Critic metadata columns (set at insert time by the registry)
ALTER TABLE task_gate_results ADD COLUMN safety_class BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE task_gate_results ADD COLUMN waivable     BOOLEAN NOT NULL DEFAULT true;
-- INVARIANT (enforced by registry + meta-test): safety_class = true → waivable = false.

-- 3. Rebuild v_gate_status to honour waivers.
--    A required critic is "satisfied" when its latest verdict is 'pass'
--    OR it is 'waived' AND waivable = true.
--    Safety critics (waivable=false) are NEVER satisfied by a 'waived' verdict —
--    they require the escalation path.
CREATE OR REPLACE VIEW v_gate_status AS
WITH latest AS (
    SELECT DISTINCT ON (task_id, critic_name)
           task_id, critic_name, required, verdict, waivable, safety_class
    FROM   task_gate_results
    ORDER  BY task_id, critic_name, created_at DESC
)
SELECT
    t.id        AS task_id,
    t.board_id,
    bool_and(
        l.verdict = 'pass'
        OR (l.verdict = 'waived' AND l.waivable = true)
    ) FILTER (WHERE l.required)                              AS all_required_pass,
    (t.approved_at IS NOT NULL)                              AS human_approved,
    (
        bool_and(
            l.verdict = 'pass'
            OR (l.verdict = 'waived' AND l.waivable = true)
        ) FILTER (WHERE l.required)
        AND t.approved_at IS NOT NULL
    )                                                        AS gate_satisfied
FROM tasks t
LEFT JOIN latest l ON l.task_id = t.id
GROUP BY t.id, t.board_id, t.approved_at;

-- 4. Permanent escalation audit table.
--    Solo-operator escalation (RT-03): operator writes a justification to resolve
--    an escalated safety finding. This record is PERMANENT and NEVER DELETED.
--    A safety critic is satisfied after escalation by inserting a new
--    task_gate_results row with verdict='pass' + details carrying escalation_id.
CREATE TABLE task_gate_escalations (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    board_id        TEXT NOT NULL REFERENCES boards(id),
    task_id         TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    critic_name     TEXT NOT NULL,
    escalated_by    TEXT NOT NULL,          -- human session identity
    justification   TEXT NOT NULL,          -- mandatory; cannot be empty
    escalated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_escalations_task ON task_gate_escalations(task_id);

ALTER TABLE task_gate_escalations ENABLE ROW LEVEL SECURITY;
CREATE POLICY task_gate_escalations_board_isolation ON task_gate_escalations
    USING     (board_id = current_setting('app.board_id', true))
    WITH CHECK (board_id = current_setting('app.board_id', true));
CREATE POLICY task_gate_escalations_admin_bypass ON task_gate_escalations
    USING (current_user = 'talos_admin');
```

---

## Deliverable 1 — Critic registry: `platform/critics/registry.py`

The registry is the structural enforcer for RT-02. A mis-flagged critic is caught
**at registration time**, not at review time.

### Spec

```
CriticSpec:
    name:         str               — unique critic identifier
    fn:           Callable          — function(deliverable: dict, nexus_client=None) → CriticResult
    required:     bool              — required=True means a fail blocks the gate
    safety_class: bool              — True → waivable forced to False (structural invariant)
    waivable:     bool              — True = human can waive; False = must escalate

Invariant: if safety_class is True, waivable MUST be False.
           Registration raises ValueError if safety_class=True and waivable=True.
```

### Files to create

**`platform/critics/registry.py`:**
- `register(spec: CriticSpec) -> None` — registers a critic; raises `ValueError` if safety-invariant violated
- `get_all() -> list[CriticSpec]` — returns all registered specs
- `get(name: str) -> CriticSpec | None` — lookup by name
- `run_all(deliverable: dict, nexus_client=None) -> list[dict]` — runs every registered critic, returns list of dicts with `{name, required, safety_class, waivable, passed, reason, verdict}` where `verdict = "pass" if passed else "fail"`

**`platform/critics/test_registry.py`:**

Write exactly these tests (all must pass; the meta-test is the CI invariant):

1. `test_meta_critic_invariant_safety_class_not_waivable` — **BLOCKER TEST** —
   register a critic with `safety_class=True, waivable=True`; assert `ValueError` is raised.
   This is the "meta-critic" that fails the build if the invariant is violated.

2. `test_register_safety_critic_non_waivable` —
   register a critic with `safety_class=True, waivable=False`; assert it succeeds.

3. `test_run_all_returns_verdict_per_critic` —
   register two critics (one pass, one fail); call `run_all()`; assert each dict has
   the correct `verdict` field (`"pass"` or `"fail"`).

4. `test_learned_critic_required_false` —
   register a critic with `required=False`; run a deliverable that would fail it;
   assert the gate is not blocked (learned critics are advisory, never auto-block).

---

## Deliverable 2 — Fail-closed citations_resolvable: update `platform/critics/citations_resolvable.py`

RT-05 fix: when a live `nexus_client` is provided but the call raises any exception
(connection error, timeout, unexpected status), the critic must **fail closed** —
return `CriticResult(passed=False, reason="NEXUS unavailable — fail closed", ...)`.

It must **not** serve a stale cache result. The P1 `nexus_client=None` stub path stays
unchanged (stub returns confirmed for CI). The new behaviour is only for the live path.

### What to add

In `citations_resolvable(deliverable, nexus_client=None)`:
- If `nexus_client` is not None, call it inside a `try/except Exception`
- On any exception: return `CriticResult(passed=False, reason="NEXUS unavailable — fail closed: {e}", waivable=True)`
- Add `waivable: bool = True` field to `CriticResult` (it's missing in P1's version; the registry reads it)

### Tests to add in `platform/critics/test_citations_resolvable.py` (new file)

1. `test_confirmed_citation_passes` — a deliverable with `status="confirmed"` citation passes.
2. `test_proposed_citation_blocks` — a deliverable with `status="proposed"` citation fails.
3. `test_nexus_unavailable_fails_closed` — pass a `nexus_client` mock that raises `ConnectionError`;
   assert `result.passed is False` and `"fail closed"` in `result.reason`.
4. `test_no_citations_fails` — a deliverable with no `citations` key fails.

---

## Deliverable 3 — Contradiction filter: `platform/critics/contradiction_filter.py`

RT-29 fix: before contradiction findings reach the human queue, deduplicate and
rate-limit them so the operator isn't flooded.

### Spec

```python
def filter_contradictions(
    findings: list[dict],
    *,
    window_seconds: int = 300,
    max_per_severity: dict[str, int] | None = None,
) -> list[dict]:
    """
    Deduplicate and rate-limit contradiction findings.

    Each finding dict must have:
        finding_id: str
        severity:   str   — "HIGH" | "MEDIUM" | "LOW"
        kind:       str   — contradiction kind (e.g. "nexus_vs_episodic")
        detected_at: float — unix timestamp

    Dedup rule:  same (finding_id, kind) → keep only the most recent within window.
    Rate-limit:  per severity tier, keep at most max_per_severity[severity] findings.
                 Default: HIGH=5, MEDIUM=10, LOW=20.

    Returns the filtered list, sorted by severity (HIGH first) then detected_at.
    """
```

### Tests in `platform/critics/test_contradiction_filter.py`

1. `test_dedup_same_finding_keeps_most_recent` — 10 findings for the same `(finding_id, kind)`
   within the window → exactly 1 in output, the most recent.
2. `test_rate_limit_per_severity` — flood 100 LOW findings + 6 HIGH findings;
   output has ≤20 LOW and ≤5 HIGH.
3. `test_outside_window_not_deduped` — two findings with same `(finding_id, kind)` but
   `detected_at` more than `window_seconds` apart → both survive (separate incidents).
4. `test_severity_sort_order` — output is sorted HIGH → MEDIUM → LOW.

---

## Deliverable 3b — Wire the registry into `deliverable_node` and register the P2 critic set

This is the step that makes the registry load-bearing instead of dead code.

### Register critics at module load time in `platform/critics/registry.py`

At the bottom of `registry.py`, after defining `register()`, call it to register the
P2 starter set:

```python
# P2 starter registry — registered at module load; imported by deliverable_node
register(CriticSpec(
    name="citations_resolvable",
    fn=citations_resolvable,
    required=True,
    safety_class=False,
    waivable=True,
))

register(CriticSpec(
    name="no_live_write_in_deliverable",
    fn=no_live_write_in_deliverable,
    required=True,
    safety_class=True,   # safety critic: waivable forced to False
    waivable=False,
))
```

Add `no_live_write_in_deliverable` in a new file `platform/critics/no_live_write.py`:

```python
from platform.critics.citations_resolvable import CriticResult

def no_live_write_in_deliverable(deliverable: dict, nexus_client=None) -> CriticResult:
    """
    Safety critic (safety_class=True, waivable=False).
    Blocks any deliverable that contains a 'live_write' key set to True.
    In P2 this is a structural proof that safety critics exist and cannot be waived.
    In P6 this critic grows to cover write-profile tools.
    """
    if deliverable.get("live_write") is True:
        return CriticResult(
            passed=False,
            reason="deliverable contains live_write=True — safety critic blocks approval",
            waivable=False,
        )
    return CriticResult(passed=True, reason="no live write detected", waivable=False)
```

### Rewrite `deliverable_node` to call `registry.run_all()` (not the bare function)

Replace the direct `citations_resolvable(...)` call in `deliverable_node` with:

```python
from platform.critics.registry import run_all as run_all_critics
# ...
critic_verdicts = run_all_critics(deliverable, nexus_client=None)
```

Then, for each critic verdict in `critic_verdicts`, INSERT one `task_gate_results` row
using the real columns, setting `safety_class` and `waivable` from the verdict dict:

```python
cur.execute(
    """
    INSERT INTO task_gate_results
        (board_id, task_id, run_id, critic_name, required,
         verdict, safety_class, waivable, details)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
    """,
    (
        state["board_id"], state["task_id"], state["run_id"],
        v["name"], v["required"],
        v["verdict"],           # "pass" or "fail"
        v["safety_class"],      # from registry spec
        v["waivable"],          # from registry spec (False for safety critics)
        json.dumps(v),
    ),
)
```

This makes `safety_class` and `waivable` database-resident at critic-run time, which is
what the escalate path (Deliverable 4d) queries when checking whether `waive` is allowed.

---

## Deliverable 4 — Five gate outcomes: update `platform/api.py` and `platform/graph/spine.py`

### 4a. Update `SpineState` in `platform/graph/spine.py`

Add these fields to `SpineState`:
```python
edited_deliverable: dict | None   # set when outcome='edit'; deliverable_node uses it on re-entry
gate_justification: str | None    # set for waive/escalate; mandatory for those outcomes
```

### 4b. Update the graph to support edit-inline

Add a **conditional edge from `gate_node`**:
- If `gate_outcome == "edit"` → go back to `deliverable_node` (critic re-run with edited deliverable)
- All other outcomes → `post_gate_node`

In `deliverable_node`, check `state.get("edited_deliverable")` first:
- If set: use it as the deliverable (skip the nexus re-read)
- If not set: original path (build deliverable from nexus_result)

### 4c. Update `post_gate_node` to handle all five outcomes

| Outcome  | Writes to DB                                                              |
|----------|---------------------------------------------------------------------------|
| approve  | `tasks` → `status='approved', approved_by, approved_at`; `task_events.kind='gate_outcome'` |
| reject   | `tasks` → `status='rejected', rejected_by, rejected_at, rejection_reason`; `task_events.kind='gate_outcome'` |
| waive    | `task_gate_results` row: `verdict='waived', waivable=True, details={"waived_by": ..., "justification": ...}`; `task_events.kind='gate_waiver'`; then sets `tasks.status='approved', approved_by, approved_at` |
| edit     | (edit path loops back to `deliverable_node` via conditional edge — `post_gate_node` only runs on approve/reject/waive/escalate) |
| escalate | Inserts row into `task_gate_escalations` (mandatory justification); then inserts a new `task_gate_results` row: `verdict='pass', safety_class=True, waivable=False, details={"escalated": True, "escalation_id": <id>, "justification": ...}`; then sets `tasks.status='approved', approved_by, approved_at`; `task_events.kind='gate_escalation'` |

**RT-03 compliance for escalate:** a safety critic (`waivable=False`) cannot be silently
waived — it must follow the `escalate` path, which requires a non-empty `justification`
and creates a permanent `task_gate_escalations` record. The API must reject `outcome="waive"`
if any failing required critic has `waivable=False`.

### 4d. Update the gate API endpoint

Extend `GateOutcomeRequest`:
```python
class GateOutcomeRequest(BaseModel):
    outcome:       str            # approve | reject | waive | edit | escalate
    reason:        str | None = None       # required for reject
    justification: str | None = None       # required for waive and escalate; must be non-empty
    new_deliverable: dict | None = None    # required for edit
```

Add these checks before calling the graph:
- `outcome` must be in `{"approve", "reject", "waive", "edit", "escalate"}` → 422 if not
- `reject` requires `reason` (non-empty string) → 422 if missing
- `waive` requires `justification` (non-empty string) → 422 if missing
- `escalate` requires `justification` (non-empty string) → 422 if missing
- `edit` requires `new_deliverable` (non-empty dict) → 422 if missing
- For `waive`: query `task_gate_results` for any failing required critic with `waivable=False`;
  if any exist → HTTP 409 (`"error": "safety critics cannot be waived — use escalate"`)
- Existing `approve` check stays: `all_required_pass` must be true via `v_gate_status`

Pass the outcome data to the graph via `Command(resume={...})`.

---

## Deliverable 5 — Update `platform/tests/conftest.py`

Add `engine/schema-p2.sql` to the `SCHEMA_FILES` list:

```python
SCHEMA_FILES = [
    "/mnt/i/talos/engine/schema.sql",
    "/mnt/i/talos/engine/schema-additions.sql",
    "/mnt/i/talos/engine/schema-p2.sql",
]
```

Also grant the `talos_app` role access to the new `task_gate_escalations` table
(add a `GRANT` statement in the `pg_setup` fixture, mirroring the existing grants).

---

## Deliverable 6 — P2 test file: `platform/tests/test_p2_gate.py`

Write exactly these **four** tests (the P2 Definition of Done):

### Test 1: Meta-critic invariant — CI blocker
```
test_meta_critic_safety_not_waivable
```
Import the registry and attempt to register a critic with `safety_class=True, waivable=True`.
Assert `ValueError` is raised. This test is the structural enforcer of RT-02.
It must be in this file so it runs in CI with the full DB stack, not just the unit-test file.

### Test 2: All five outcomes write correct columns
```
test_all_five_outcomes_write_correct_columns(pg_setup, admin_conn, test_graph)
```
Use five separate board+task pairs (one per outcome). For each:

- **approve**: run spine → human sends `{"outcome": "approve"}` via gate endpoint →
  assert `tasks.status='approved'`, `tasks.approved_by='thunt'`, `tasks.approved_at IS NOT NULL`,
  `task_events` has a `gate_outcome` row.

- **reject**: run spine → human sends `{"outcome": "reject", "reason": "needs more detail"}` →
  assert `tasks.status='rejected'`, `tasks.rejected_by='thunt'`,
  `tasks.rejection_reason='needs more detail'`.

- **waive**: run spine → human sends `{"outcome": "waive", "justification": "risk accepted"}` →
  assert a `task_gate_results` row exists with `verdict='waived'` and `waivable=True`;
  assert `tasks.status='approved'` (waive + approve is one atomic operation).
  `task_events` has a `gate_waiver` row.

- **edit**: run spine → human sends `{"outcome": "edit", "new_deliverable": {"citations": [{"finding_id": "REAL_TAG", "status": "confirmed"}], "summary": "updated"}}` →
  assert critics re-ran (new `task_gate_results` row exists), task is still in `review`
  (edit loops back to deliverable_node → gate_node; the gate is NOT yet approved),
  `task_events` has a `gate_edit` row.
  Then approve it: human sends `{"outcome": "approve"}` → task moves to `approved`.

- **escalate**: run a task where the stub deliverable is set up with a critic that has
  `safety_class=True, waivable=False` and `verdict='fail'`; human sends
  `{"outcome": "escalate", "justification": "accepted after full manual review"}` →
  assert `task_gate_escalations` has 1 row with the justification;
  assert a new `task_gate_results` row with `verdict='pass', safety_class=True` (the override);
  assert `tasks.status='approved'`, `task_events` has a `gate_escalation` row.

### Test 3: Fail-closed on NEXUS unavailable
```
test_citations_resolvable_fail_closed_on_nexus_unavailable(pg_setup, admin_conn)
```
Create a `nexus_client` mock that raises `ConnectionError("timeout")`.
Call `citations_resolvable(deliverable_with_confirmed_citation, nexus_client=mock_client)`.
Assert `result.passed is False`.
Assert `"fail closed"` in `result.reason`.
This proves that even a "confirmed" citation is blocked when NEXUS is unreachable.

### Test 4: Contradiction filter deduplicates a flood
```
test_contradiction_filter_dedupes_flood()
```
Create 100 findings all with the same `(finding_id, kind)` and `detected_at` within a
60-second window. Call `filter_contradictions(findings, window_seconds=300)`.
Assert `len(result) == 1`.
Assert the surviving finding has the latest `detected_at`.
(No DB needed — pure Python.)

---

## Deliverable 7 — Update `CLAUDE.md`

Update the "Project status" section and "Running the existing tests" section to
reflect P1's completion and how to run the full test suite:

```
## Project status

**Pre-alpha, P1 complete, P2 in progress.** TALOS is an agent harness for industrial
and business operations. The engine port and web view have not been built. Runnable code:
- `platform/validators/` — capability-manifest validator (P0)
- `platform/critics/` — deterministic gate critics and registry
- `platform/graph/spine.py` — 4-node LangGraph spine (P1)
- `platform/worker.py` — single-worker claim loop (P1, no dispatcher)
- `platform/api.py` — FastAPI board API with gate endpoint (P1)
- `platform/tests/` — 6 P1 spine tests + P2 gate tests

## Running tests

Tests require Docker (testcontainers spins up Postgres 16).

Run all tests:
```bash
.venv/bin/python -m pytest platform/ -v
```

Run P1 spine tests only:
```bash
.venv/bin/python -m pytest platform/tests/test_spine.py -v
```

Run P2 gate tests only:
```bash
.venv/bin/python -m pytest platform/tests/test_p2_gate.py -v
```
```

Also update the "Repository layout" section — `critics/` is no longer a placeholder.

---

## Definition of Done — P2 is complete when all of the following are true

1. `.venv/bin/python -m pytest platform/ -v` runs **all tests green** — both the 6 P1
   spine tests and the 4 P2 gate tests.

2. `test_meta_critic_safety_not_waivable` raises `ValueError` (the build breaks if someone
   registers a safety critic as waivable).

3. All five gate outcomes (`approve`, `reject`, `waive`, `edit`, `escalate`) produce the
   correct DB writes in `test_all_five_outcomes_write_correct_columns`.

4. `test_citations_resolvable_fail_closed_on_nexus_unavailable` passes — NEXUS unavailable
   blocks a confirmed-citation deliverable.

5. `test_contradiction_filter_dedupes_flood` passes — 100 duplicate findings collapse to 1.

6. `CLAUDE.md` reflects P1 complete and the test commands work.

7. No P1 test regressions.

---

## What NOT to build in P2

- Full dispatcher (claim-racing, heartbeat, circuit breaker) → P3
- LangGraph PostgreSQL checkpointer → P3
- Polyglot memory stores (Neo4j, pgvector, Redis) → P4
- Crystallize / promotion gate → P5
- Sim-execute capability → P6
- Cockpit (Space Agent web view) → P7
- Proactive gateway / cron loops → P8
- The `add_triplet` / Graphiti integration → P4

---

## Known schema gotchas — do NOT introduce these mistakes

1. **`task_events.kind`** — the column is `kind`, not `event_type`. P1 already uses
   `kind='gate_outcome'`. P2 adds `kind='gate_waiver'`, `kind='gate_edit'`,
   `kind='gate_escalation'`.

2. **`task_events.task_id NOT NULL`** — the `pm_escalate_milestone_risk` trigger in
   `schema-additions.sql` inserts `task_id=NULL` (a pre-existing bug). DO NOT attempt
   to fix it in P2. If you apply the schemas and that trigger fires, the insert fails.
   That is expected; the milestone escalation logic is not exercised in P2 tests.

3. **`v_gate_status` board scoping** — the view inherits the RLS from `tasks` and
   `task_gate_results`. Always query it inside `board_scope()`. It uses
   `current_setting('app.board_id', true)` indirectly through underlying table RLS.

4. **`boards` has no RLS** — the `boards` table is the isolation boundary itself; it
   has no `board_isolation` policy. Do not add one.

5. **Shared graph instance** — `test_graph` fixture is session-scoped and shared between
   `claim_and_run()` and the API's gate endpoint. Both must use the same MemorySaver
   instance (already wired in conftest; do not create a second `build_graph()` instance
   inside the test or the interrupt checkpoint will not be found).

6. **Idempotency in post_gate_node** — the P1 guard checks `approved_at IS NOT NULL`.
   That only works for the approve branch. In P2, the reject branch never sets
   `approved_at`, so a re-run would double-write. Fix: change the guard to:
   ```sql
   SELECT status FROM tasks WHERE id = %s AND board_id = %s
   ```
   and short-circuit when `status IN ('approved', 'rejected')`. This covers approve,
   reject, waive (which ends in `status='approved'`), and escalate (which ends in
   `status='approved'`). Edit never reaches `post_gate_node`, so that path is excluded.
   The existing P1 test (`test_post_gate_idempotency`) already tests the approve path;
   the P2 `test_all_five_outcomes` test must include a re-run assertion for the reject
   path too.

7. **`task_gate_results` new columns default safely** — `schema-p2.sql` adds
   `safety_class BOOLEAN NOT NULL DEFAULT false` and `waivable BOOLEAN NOT NULL DEFAULT true`.
   The P1 critic insertions in `deliverable_node` do not set these columns, so they get
   the defaults. In P2, the registry's `run_all()` provides explicit values.

8. **`rejected_by`, `rejected_at`, `rejection_reason`** already exist on `tasks`
   (schema.sql lines ~71-76). Do NOT add them again. Just write to them.

9. **Escalate auto-approves** — this is intentional under ADR-011 RT-03 (solo-operator
   mode). The escalate path inserts a `task_gate_escalations` row (permanent audit note),
   inserts a synthetic `verdict='pass'` row in `task_gate_results` for the blocking critic,
   then immediately transitions `status='approved'`. The human is both the escalator and the
   approver in solo mode. Do not add a separate "escalate then still-wait-for-approval"
   flow in P2 — that belongs to P7 (multi-reviewer cockpit).

10. **`contradiction_filter.py` is a standalone utility** — in P2 it has no caller inside
    the spine graph; there is no finding-producer queue to wire it into until P4. Build it,
    test it, and leave it unwired. Do not hunt for a queue or import it into `deliverable_node`
    or `spine.py`. The P2 DoD test (`test_contradiction_filter_dedupes_flood`) verifies the
    module directly by calling `filter_contradictions()`.

---

## Test isolation note

P2's `test_all_five_outcomes_write_correct_columns` is a single parametrized test or a
single function that tests all five outcomes sequentially. Use **separate board+task pairs**
for each outcome (as `test_spine_happy_path` already does) — uuid-suffixed IDs prevent
state leakage between branches.

The `edit` outcome re-runs critics and loops back to `gate_node`. The LangGraph graph
must be invoked twice for the edit path: once by `claim_and_run()` and again by the
`edit` gate call (which drives `deliverable_node → gate_node` re-entry). Then a third
invocation via `approve` completes the task. All three share the same `test_graph` fixture.

---

## Suggested implementation order

1. Write `engine/schema-p2.sql` and verify it applies cleanly after the existing two schema files.
2. Update `platform/tests/conftest.py` (`SCHEMA_FILES` + grant).
3. Write `platform/critics/registry.py` and `platform/critics/test_registry.py`; run them.
4. Update `platform/critics/citations_resolvable.py` (add `waivable` field, add fail-closed path).
5. Write `platform/critics/test_citations_resolvable.py`; run them.
6. Write `platform/critics/contradiction_filter.py` and `platform/critics/test_contradiction_filter.py`; run them.
7. Update `platform/graph/spine.py` (`SpineState` extensions, conditional edge, all-outcome `post_gate_node`).
8. Update `platform/api.py` (extend `GateOutcomeRequest`, add outcome validation, update graph resume).
9. Write `platform/tests/test_p2_gate.py`.
10. Run `.venv/bin/python -m pytest platform/ -v` — fix failures before declaring done.
11. Update `CLAUDE.md`.
