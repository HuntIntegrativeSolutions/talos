# TALOS — SEC-03: Make RLS Real and Tested (FORCE RLS + worker RLS-correctness)

Paste this into a **fresh** Claude Code session. Working directory: `/mnt/i/talos/`

**What this task is and why it's next.** The RT-01 security review
(`docs/security-review-rt01-findings.md`) found **SEC-03 (Medium)**: board-scoped tables have
`ENABLE ROW LEVEL SECURITY` but never `FORCE`, and the **test suite connects as the Postgres
superuser** (`conftest.py:67` sets `TALOS_DB_DSN = admin_dsn`), which **bypasses RLS entirely**.
Production runs as `talos_app` (`install.md:37`, NOSUPERUSER), where RLS *is* enforced. That gap
masks the board-isolation guarantee — TALOS's load-bearing "MCP boundary = security boundary"
property — and it is currently **untested on every integration path**.

Investigating it surfaced a **P3.5-blocking correctness bug** (not a security regression — the
SEC-01 "safe for credentials" verdict stands): under real `talos_app` RLS, the worker **heartbeat
write fails silently** and **reclaim cannot see other boards**. The first real worker run as
`talos_app` at P3.5 would hit double-execution. This task closes SEC-03 *and* clears that blocker by
making RLS real end-to-end.

This is **one comprehensive task** (the user chose the comprehensive scope). It includes a short
ADR. Read everything in Step 1 before writing.

---

## Step 1 — Read before editing (with anchors)

1. `docs/security-review-rt01-findings.md` — the **SEC-03** detail (Gap A: no FORCE RLS; Gap B:
   tests use superuser DSN).
2. `engine/schema.sql:273–366` — the `ENABLE ROW LEVEL SECURITY` + `board_isolation` /
   `admin_bypass` policy blocks. Also `engine/schema-additions.sql:462` (milestones),
   `engine/schema-p2.sql:63` (task_gate_escalations), `engine/schema-p3.sql:30` (task_spans).
   **Enumerate every table that has `ENABLE ROW LEVEL SECURITY` — grep for it; do not hardcode a
   list that might miss one.**
3. `talos/tests/conftest.py` — the harness. Note three things: it applies the four `.sql` files
   **directly** (lines ~77–90), creates `users` + `talos_app` by hand, grants
   `SELECT, INSERT, UPDATE` on all tables + `USAGE, SELECT` on sequences (~108–113), and then
   **`alembic_command.stamp(head)`** (~128–131) — **it stamps, it does NOT run `upgrade()`.** So a
   new Alembic migration's DDL will **not** reach the tests unless you mirror it in conftest (this
   is exactly how V0002's `users` table is handled). Fixtures: `admin_conn` (superuser, bypasses
   RLS — for seeding) and `app_conn` (talos_app, RLS enforced).
4. `talos/db.py` — `get_conn()` reads `TALOS_DB_DSN`; `board_scope()` does
   `SET LOCAL app.board_id = %s` inside a transaction. Note `SET LOCAL` requires a transaction —
   it does nothing under `autocommit=True`.
5. `talos/worker.py:47–103` — `reclaim_dead_workers` (the cross-board scan) and
   `make_heartbeat_callback` (the unscoped, autocommit heartbeat). Confirm for yourself: reclaim's
   `SELECT ... FROM task_runs WHERE ...` has **no `board_id` filter** (it is a system janitor across
   all boards); the heartbeat `UPDATE task_runs ... WHERE id = %s` has **no `board_scope`**.
6. `talos/api.py:130–160` — `create_board` / `create_task` inserts (RLS-on-insert wrinkle, below).
7. `docs/decisions/ADR-019-postgres-everywhere.md` and `ADR-020-heartbeat-reclaim.md` — the worker
   infrastructure framing your new ADR-037 must stay consistent with.
8. `docs/decisions/ADR-034-schema-migrations.md` — Alembic is the source of truth for schema
   changes; FORCE RLS goes in a **migration**, not in `schema.sql`.

## Step 2 — Decisions to settle (write ADR-037 first, confirm with the user)

The reclaim scan is **inherently cross-board** and cannot be `board_scope`d without defeating its
purpose. Under `talos_app` + RLS it sees zero rows on other boards. This needs a deliberate
**system-actor decision** — write it up as a short **ADR-037 (worker reclaim system role)** and
confirm the choice with the user before coding. Present these options and recommend one:

- **(A) Dedicated `talos_system` role with `BYPASSRLS`**, used *only* by the dispatcher's reclaim
  path (a separate `TALOS_RECLAIM_DSN`, or a `SET ROLE` for that connection). Keeps reclaim's Python
  logic and per-row span emission (`emit_span`, `worker.py:79–84`) intact. **Recommended for v1** —
  minimal refactor, clear "worker = system infrastructure" boundary, request-handling stays
  `talos_app`.
- **(B) `SECURITY DEFINER` SQL function** owned by the table owner that does the cross-board
  scan/update; `talos_app` calls it. Surgical RLS-wise, but moves logic into plpgsql and loses the
  Python span emission per reclaimed run.
- **(C) Per-board iteration** — reclaim only the current board. Simplest, but narrows semantics (a
  board with no active claim never gets its dead workers reclaimed). Note this regression explicitly
  if chosen.

Also decide and record: the heartbeat fix threads `board_id` into the callback (below) — confirm
that's acceptable vs. routing the heartbeat through the same system role. (Recommended: board-scope
the heartbeat — it *is* board-specific; reserve the system role for the genuinely cross-board
reclaim.)

## Step 3 — The changes

### A — FORCE RLS migration (Gap A) + mirror in conftest
- New Alembic migration **`V0003`**: for **every** table that has `ENABLE ROW LEVEL SECURITY`, add
  `ALTER TABLE <t> FORCE ROW LEVEL SECURITY;` (downgrade removes it). Use `op.execute(...)`.
- **Mirror the same `FORCE` statements in `conftest.py`** after the `.sql` files are applied
  (because conftest stamps, doesn't upgrade — same pattern as the manual `users` DDL). A loop over
  the enumerated table list is fine.
- **Verify the `admin_bypass` policy isn't dead:** it keys on `current_user = 'talos_admin'`, but
  nothing connects as `talos_admin` (tests use the superuser; migrations run as `postgres`). Under
  FORCE RLS this matters more. Report whether `talos_admin` is used anywhere; if it's vestigial, say
  so and note whether seeding/migrations should adopt it or it should be removed (do not silently
  delete a policy — surface it).

### B — Flip the test DSN to talos_app (Gap B — the core fix)
- Change `conftest.py:67` from `admin_dsn` to a **`talos_app` DSN** (derive it like the `app_conn`
  fixture does — `user='talos_app', password='talos_app'`). Now every `get_conn()` in tested code
  runs under enforced RLS, exactly like production.
- **Expect a cascade.** Run the suite and triage each failure into one of:
  - **Seeding that should use `admin_conn`** — test setup that inserts fixtures via the app role;
    move it to the `admin_conn` (RLS-bypass) fixture. (Seeding bypasses RLS by design; *code under
    test* runs as `talos_app`.)
  - **A genuine missing `board_scope`** — a board-scoped query in product code that forgot to set
    `app.board_id`. **This is a real latent bug — fix it** (the heartbeat is the known one).
  - **A cross-board system op** — reclaim. Handle via the ADR-037 path, not by bolting `board_scope`
    onto a janitor.
  - **Board/task creation under RLS** — `create_board`/`create_task` (`api.py:134,154`): inserting a
    board as `talos_app` needs `app.board_id` set to the new id (a `board_scope(conn, new_id)` around
    the insert) or an appropriate `WITH CHECK` INSERT policy. Resolve and note which.
- Grants are currently `SELECT, INSERT, UPDATE` (+ sequences). No code path `DELETE`s today (grep
  confirms), so no DELETE grant is needed; if the reclaim/ADR work introduces a `DELETE`, add the
  grant in conftest **and** `install.md`.

### C — Fix the heartbeat (the P3.5 correctness bug)
- `make_heartbeat_callback` (`worker.py:90`) must become board-aware: thread `board_id` in
  (`make_heartbeat_callback(run_id, board_id)`), **drop `autocommit=True`**, and wrap the UPDATE in
  `board_scope(conn, board_id)` so `SET LOCAL app.board_id` actually applies. Update the call site
  that constructs the callback to pass `board_id`.

### D — Wire reclaim through the ADR-037 decision
- Implement the chosen reclaim path (recommended: the `talos_system`/BYPASSRLS connection used only
  for `reclaim_dead_workers`). Keep the span emission. Ensure `claim_and_run` (`worker.py:124`) still
  calls reclaim correctly under the new role/connection.

## Step 4 — Definition of Done

1. Every RLS-enabled table has `FORCE ROW LEVEL SECURITY` (migration V0003 **and** mirrored in
   conftest); `downgrade()` reverses it.
2. `conftest.py` runs tested code as **`talos_app`**; the full suite passes under enforced RLS.
3. **New cross-board isolation test:** seed two boards via `admin_conn`; via a `talos_app`
   connection scoped to board A, assert board B's tasks are invisible (0 rows) on `tasks` and at
   least one other board-scoped table. This directly proves the isolation guarantee that was
   previously untested.
4. Heartbeat writes succeed under `talos_app` (board-scoped) — a test exercises it as `talos_app`
   and confirms `last_heartbeat_at` is actually written.
5. Reclaim works across boards under the ADR-037 path — a test seeds a stale heartbeat on one board
   and confirms reclaim re-queues it while running as the (non-superuser) runtime.
6. **ADR-037** is written (worker reclaim system role), status Accepted, and the SEC-03 finding is
   marked resolved in `docs/security-review-rt01-findings.md`.
7. No regressions: the prior 67 tests plus the new ones pass:
   `TALOS_JWT_SECRET=test-secret-dev-only TALOS_NEXUS_STUB=1 .venv/bin/python -m pytest talos/ -v`

## Step 5 — Test plan

- `test_rls_cross_board_isolation` — the two-board denial test (DoD #3). Place in a new
  `talos/tests/test_rls.py` or the closest existing fit.
- `test_heartbeat_writes_under_app_role` — heartbeat persists as `talos_app` (DoD #4).
- `test_reclaim_across_boards_under_runtime_role` — reclaim re-queues a stale run on another board
  (DoD #5).
- Existing tests that fail after the DSN flip: fix per the Step 3B triage. Document in the
  report-back what each failure was (seeding-moved / board_scope-bug-fixed / creation-policy /
  reclaim-role) — that list is evidence the flip surfaced real coverage.

## Step 6 — Close-out / report-back

- Mark **SEC-03 resolved** in `docs/security-review-rt01-findings.md` (date, the FORCE-RLS +
  DSN-flip + worker fixes).
- Update `ROADMAP.md`: this closes **RT-09** (RLS policy-presence/exercise) in P0-Foundation — mark
  it. Note the worker heartbeat/reclaim fix as a P3.5 prerequisite now cleared.
- If `talos_admin`/`admin_bypass` turned out vestigial, record the finding (and recommendation) as a
  new open item — do not delete it in this task.
- Report-back: files/lines changed, the ADR-037 decision, the triage list from the DSN flip, the new
  test count, and any remaining open item.

## Rules for this session

- **One task, comprehensive RLS closure.** In scope: FORCE RLS, DSN flip, heartbeat fix, reclaim
  system-role (ADR-037), isolation test. **Out of scope:** SEC-02/04/05/06/07 (separate
  lower-severity tasks), RT-14, RT-20.
- Schema changes go through **Alembic** (ADR-034), mirrored in conftest because the harness stamps
  rather than upgrades. Do **not** add FORCE statements to `schema.sql`.
- Seeding bypasses RLS via `admin_conn`; code under test runs as `talos_app`. Don't blur the two.
- Don't bolt `board_scope` onto the cross-board reclaim janitor — that's what ADR-037 is for.
- Read before editing; match existing style; no unrelated refactors.

Start with Step 1, then write ADR-037 and confirm the reclaim approach with the user before coding.
