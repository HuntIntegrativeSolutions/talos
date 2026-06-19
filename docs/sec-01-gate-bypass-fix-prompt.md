# TALOS — Fix SEC-01: Unauthenticated Gate-Approval Bypass (Critical)

Paste this into a **fresh** Claude Code session. Working directory: `/mnt/i/talos/`

**What this task is and why it's first.** The RT-01 security review (`docs/security-review-rt01-findings.md`)
found **SEC-01 (Critical)**: the unauthenticated `PATCH /boards/{board_id}/tasks/{task_id}/status`
endpoint can set `status='approved'` (or `'rejected'`/`'done'`), and `post_gate_node`'s idempotency
guard trusts `tasks.status`. Together this lets an unauthenticated caller (1) forge an approval that
downstream DAG dispatch may act on, and (2) permanently poison the gate — a pre-set `status='approved'`
makes the *real* human gate decision a silent no-op. This blocks putting real credentials or NDA
client data in front of the system. It is the highest-priority fix and the only task in this session.

This is **one task, two changes, one coherent closure.** Both attack paths use the same entry point;
both fixes belong together.

---

## Step 1 — Read before editing (with anchors)

1. `docs/security-review-rt01-findings.md` — the SEC-01 finding in full (impact, attack chain).
2. `talos/api.py:61` — `VALID_STATUSES` (the set the PATCH endpoint validates against; **its only
   consumer is the PATCH endpoint at line 188**).
3. `talos/api.py:184-206` — `patch_task_status`: unauthenticated `UPDATE tasks SET status = %s`.
4. `talos/graph/spine.py:330-344` — `post_gate_node` idempotency guard (`SELECT status`; `if
   row["status"] in ("approved", "rejected"): return {}`).
5. `talos/graph/spine.py:345-363` (approve), `365-390` (reject), `392-440` (waive), `~500-512`
   (escalate) — confirm **which terminal column each outcome writes**:
   - approve / waive / escalate → `status='approved', approved_at=NOW()` (lines 359 / 436 / 510)
   - reject → `status='rejected', rejected_at=NOW()` (lines 383-384) — **note: reject does NOT set
     `approved_at`.** This is why the fix must check both columns.
6. `CLAUDE.md` (Guardian doctrine) — the gate invariant is *"`tasks.approved_at` is set by a human,"*
   never `status` alone. The fixes restore that invariant as the trusted signal.

## Step 2 — The two changes

### Change A — `talos/api.py` — terminal/gate states are not PATCH-settable

The PATCH endpoint must not be able to set gate-owned terminal states. Do **not** bolt a human-JWT
guard onto the whole endpoint — legitimate non-terminal transitions (e.g. backlog→ready) and any
worker/admin moves do not carry a human JWT, so a blanket guard is wrong. Use an allowlist instead.

- Introduce a clearly named PATCH-specific constant (e.g. `PATCH_ALLOWED_STATUSES`) rather than
  silently shrinking the shared `VALID_STATUSES`. Exclude `"approved"`, `"rejected"`, and `"done"`
  from what PATCH may set. Validate `req.status` against that constant; on a disallowed value return
  the existing `422` with a message that names the gate-only states.
- Add a one-line comment citing SEC-01 / the gate invariant so the next reader knows *why* these
  states are excluded here.

**Note for the report-back, not a change to make:** removing `"done"` removes the only current path
that sets `'done'` (nothing in the engine transitions to `'done'` yet). That is acceptable — an
authenticated/automated done-transition is future work — but call it out as a new open item.

### Change B — `talos/graph/spine.py` — idempotency guard trusts the gate marker, not `status`

The guard at `spine.py:342` currently keys off `status`, which Change A no longer fully protects on
its own (defense in depth) and which is the poisoning vector. Switch it to the authenticated gate
markers:

- Change the `SELECT status` at line 338 to also select `approved_at` and `rejected_at`.
- Change the guard to:
  ```python
  if row and (row["approved_at"] is not None or row["rejected_at"] is not None):
      return {}  # gate already decided — idempotent no-op
  ```

**Do not use `approved_at IS NOT NULL` alone** — reject sets `rejected_at`, not `approved_at`, so an
approved-only check would break reject idempotency (a second `post_gate_node` pass after a reject
would re-run and duplicate the `gate_outcome` event). Both columns are required, and neither is
settable through the PATCH endpoint, so the guard becomes both correct and unforgeable.

(Optional hardening, not required: the truly authoritative idempotency signal is an existing
`task_events` row of `kind='gate_outcome'` for this `run_id`. The two-column check is sufficient and
minimal; only mention the events-based approach if you see a concrete reason to prefer it.)

## Step 3 — Decisions to settle before coding

- **Constant naming / location** — propose `PATCH_ALLOWED_STATUSES` (or similar) near `VALID_STATUSES`
  in `api.py` and confirm the spelling with the user only if it collides with existing names.
- **Is `archived` left PATCH-settable?** Yes — `archived` is administrative, not a gate-forgery
  vector. Leave it in the allowlist; note the decision in the report-back.
- **Existing callers/tests** — grep the test suite and any caller for a `PATCH .../status` that sets
  `approved`/`rejected`/`done` (e.g. tests using PATCH as an approval shortcut). Any such test must be
  migrated to go through the gate (`submit_gate_outcome` with a valid human JWT) — that is the
  correct path now, and a test that still PATCHes to `approved` is itself the bug class we are
  closing. List what you changed.

## Step 4 — Definition of Done

1. `PATCH .../status` with `status` in {`approved`, `rejected`, `done`} returns `422` and does **not**
   write — proven by a new test.
2. `PATCH .../status` with a legal non-terminal status (e.g. `ready`) still works — proven by a test
   (no regression for legitimate use).
3. The `post_gate_node` guard keys off `approved_at`/`rejected_at`; a genuine **second** gate run is
   still a no-op for **both** approve and reject — proven by tests covering each.
4. SEC-01 regression test: pre-set `status='approved'` is no longer reachable via PATCH (Change A),
   **and** even if `status` were `'approved'` with `approved_at` NULL, a real gate approval still
   writes `approved_at`/`approved_by` correctly (Change B) — i.e. the poisoning path is closed.
5. All existing tests still pass:
   `TALOS_JWT_SECRET=test-secret-dev-only TALOS_NEXUS_STUB=1 .venv/bin/python -m pytest talos/ -v`
   (expect the prior 63 plus the new SEC-01 tests).

## Step 5 — Test plan

Add tests to the existing gate/auth suites (match where similar tests live — `talos/tests/test_p2_gate.py`
for gate behavior, `talos/tests/test_auth.py` for the rejection of forged writes; pick the closest fit
rather than creating a new file unless none fits):

- `test_patch_status_rejects_terminal_states` — PATCH to `approved`/`rejected`/`done` → 422.
- `test_patch_status_allows_nonterminal` — PATCH to `ready` → 200.
- `test_gate_idempotent_after_reject` — run the reject path twice; second `post_gate_node` is a no-op
  (no duplicate `gate_outcome` event). **This is the test that would have caught the report's buggy
  `approved_at`-only proposal.**
- `test_gate_idempotent_after_approve` — same for approve.
- `test_sec01_patch_cannot_poison_gate` — attempt the original attack (PATCH `approved`), confirm it
  is now blocked, and confirm a subsequent real gate approval writes `approved_at`.

Run: `TALOS_JWT_SECRET=test-secret-dev-only TALOS_NEXUS_STUB=1 .venv/bin/python -m pytest talos/ -v`

## Step 6 — Close-out

- Update `docs/security-review-rt01-findings.md`: mark **SEC-01 resolved** with the commit/date and a
  one-line description of the two changes.
- Update `ROADMAP.md` / `CLAUDE.md` status if they reference the security review as a pending gate.
- Report back: one-line status, the exact files/lines changed, the new test count, the `archived`
  and `done`-has-no-authenticated-setter decisions as new open items, and any other finding from the
  report you recommend doing next.

## Rules for this session

- **One task only: SEC-01.** Do not fix the other findings (timing oracle, rate limit, FORCE RLS,
  `_claims["sub"]` guard, broader PATCH auth) in this session — they are separate, lower-severity
  tasks the user will sequence.
- Read the real files before editing; match existing style; no unrelated refactors.
- The gate invariant is `approved_at`/`rejected_at`, never `status` alone — every change must
  reinforce that, not work around it.
- Both changes ship together: Change A closes the entry point, Change B closes the poisoning path.
  Neither alone is sufficient.

Start with Step 1.
