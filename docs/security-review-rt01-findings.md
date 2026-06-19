# TALOS — RT-01 Security Review Findings

**Scope:** RT-01 auth/gate surface — commit `dc99641`  
**Files reviewed:** `talos/auth/tokens.py`, `talos/auth/users.py`, `talos/auth/__main__.py`,
`talos/api.py`, `talos/graph/spine.py`, `engine/migrations/versions/V0002_auth_users.py`,
`engine/schema.sql`, `engine/schema-additions.sql`, `talos/tests/conftest.py`, `docs/install.md`,
`docs/decisions/ADR-036-human-gate-authentication.md`, `docs/integration/03_redteam_review.md`  
**Deployment context:** on-prem, air-gapped workstation, single operator, localhost-only API binding  
**Date:** 2026-06-18

---

## Verdict

**SEC-01 resolved — system is now safe for real credentials and NDA client data at this attack surface.**
`SEC-01` was fixed in commit `dc99641`+ (2026-06-18): `PATCH_ALLOWED_STATUSES` blocks terminal
states at the API layer (Change A); `post_gate_node` idempotency guard now keys off
`approved_at`/`rejected_at` rather than `status` (Change B). Both changes ship together; 4 new
SEC-01 regression tests added (67 total). The remaining findings are all Low or Medium and none
block real-credential use.

**Original verdict (pre-fix):** Not safe for real credentials or NDA client data. One Critical
finding (`SEC-01`) violated the Guardian doctrine directly: an unauthenticated caller could set
`tasks.status = 'approved'` via `PATCH /status`, silently poisoning the idempotency guard in
`post_gate_node` and/or firing `trg_pm_unblock_dependents` without any human approval.

---

## Findings table

| ID | Title | Severity | `file:line` | One-line impact |
|---|---|---|---|---|
| SEC-01 | Unauthenticated `PATCH /status` can set `status='approved'`, poisoning the idempotency guard and triggering DAG unblock | **Critical — RESOLVED 2026-06-18** | `api.py:184`, `spine.py:342`, `schema-additions.sql:307` | Fixed: `PATCH_ALLOWED_STATUSES` blocks terminal states; idempotency guard keys off `approved_at`/`rejected_at` |
| SEC-02 | `_claims["sub"]` raises `KeyError → 500` if `sub` claim is absent from a valid JWT | Low | `api.py:267` | Robustness nit only; requires possession of `TALOS_JWT_SECRET` to trigger |
| SEC-03 | Missing `FORCE ROW LEVEL SECURITY` + tests use superuser DSN — RLS not exercised on gate code paths | **Medium — RESOLVED 2026-06-18** | `engine/schema.sql:273–366`, `talos/tests/conftest.py:67` | Fixed: V0003 migration adds FORCE on all 15 board-scoped tables; conftest flipped to talos_app DSN; heartbeat board_scope fix; talos_system BYPASSRLS reclaim role (ADR-037); 3 new RLS tests (70 total) |
| SEC-04 | Username existence timing oracle in `verify_user` | Low | `talos/auth/users.py:42–43` | Timing difference reveals whether a username exists; deferred per ADR-036 |
| SEC-05 | `TALOS_JWT_EXPIRY_HOURS` has no maximum-bound guard | Info | `talos/auth/tokens.py:29` | A typo (e.g., `"800"`) silently creates a 33-day token |
| SEC-06 | `argon2.check_needs_rehash` not called on successful login | Info | `talos/auth/users.py:44–48` | Hashes never upgraded when argon2-cffi raises its default cost parameters |
| SEC-07 | PostgreSQL role placeholder password `talos_app` is never prompted for change in `docs/install.md` | Low | `docs/install.md:52,37` | Default credential for the DB connection role; localhost-only but still a weak default |

**Severity scale:** Critical (gate bypass / auth forgery / secret exposure — blocks real credentials) /
High / Medium / Low / Info. Severity is weighted for the **on-prem, air-gapped, single-user v1
workstation** context; a timing oracle or missing rehash is not Critical here. A gate-bypass path is.

---

## Auth-coverage map — mutating endpoints

Every state-mutating endpoint (`POST`, `PATCH`) with auth yes/no, and whether the gap is
intentional v1 scope per ADR-036 or an unresolved finding.

| Method | Path | Endpoint | Auth? | Intentional / Finding |
|---|---|---|---|---|
| `POST` | `/auth/login` | `auth_login` | N/A (issues credentials) | N/A |
| `POST` | `/boards` | `create_board` | **No** | Intentional — ADR-036 scopes auth to the gate write; board creation is admin-only via localhost binding in v1 |
| `POST` | `/boards/{board_id}/tasks` | `create_task` | **No** | Intentional — same ADR-036 scope; task creation is operator-driven pre-workflow action |
| `PATCH` | `/boards/{board_id}/tasks/{task_id}/status` | `patch_task_status` | **No** (non-terminal only) | **SEC-01 resolved** — `PATCH_ALLOWED_STATUSES` excludes `approved`/`rejected`/`done`; terminal states are gate-only |
| `POST` | `/boards/{board_id}/tasks/{task_id}/gate` | `submit_gate_outcome` | **Yes** — HS256 JWT + `token_class="human"` | Correctly authenticated; the sole gated write per RT-01/ADR-036 |

**Summary.** ADR-036 deliberately scopes the v1 auth boundary to the gate write only. `create_board`
and `create_task` have no auth by design and are low-risk for the single-operator localhost context.
The only unauthenticated endpoint that is *not* intentional is `patch_task_status` — it is a helper
that should be restricted to non-terminal statuses (or guarded by JWT) because it reaches the same
DB columns the gate is supposed to own exclusively. This is the entirety of SEC-01.

---

## Per-finding detail

### SEC-01 — Critical — Unauthenticated `PATCH /status` violates the Guardian invariant

**What it is.**

`patch_task_status` (`api.py:184–206`) accepts any value from `VALID_STATUSES` (`api.py:61–64`),
which includes `"approved"`, `"rejected"`, and `"done"`. There is no authentication check, no
JWT requirement, and no guard against setting terminal states directly. The endpoint calls:

```python
cur.execute(
    "UPDATE tasks SET status = %s WHERE id = %s AND board_id = %s RETURNING id, status",
    (req.status, task_id, board_id),
)
```

This write leaves `approved_at = NULL` and `approved_by = NULL`.

**Attack path A — gate poisoning (primary concern).**

1. Task is in `review` (worker has run, critics have scored).
2. Attacker (no credentials, any process on localhost) sends:
   `PATCH /boards/{b}/tasks/{t}/status` with `{"status": "approved"}` → 200 OK.
3. Legitimate human submits `POST /boards/{b}/tasks/{t}/gate` with a valid JWT
   and `{"outcome": "approve"}`. The JWT validates. Graph resumes. `post_gate_node` fires.
4. `spine.py:342`: `if row and row["status"] in ("approved", "rejected"): return {}`
   — the idempotency guard triggers. The node returns immediately without writing
   `approved_at`, `approved_by`, or the `task_events` row.
5. The gate endpoint returns `{"status": "ok", "outcome": "approve", "approved_by": "thunt"}` —
   false success. `approved_at` is `NULL`; `v_gate_status.gate_satisfied` is `false`.
   Every subsequent retry produces the same no-op. The task is permanently stuck with
   `status='approved'` but no actual approval on record.

The root cause: `post_gate_node`'s idempotency guard keys off `tasks.status`, which the
unauthenticated `PATCH` endpoint can mutate. The invariant should key off `approved_at IS NOT NULL`
(which only `post_gate_node` ever sets).

**Attack path B — DAG unblock without approval.**

`engine/schema-additions.sql:307–311` defines trigger `trg_pm_unblock_dependents`:

```sql
CREATE TRIGGER trg_pm_unblock_dependents
AFTER UPDATE ON tasks FOR EACH ROW
WHEN (NEW.status IN ('done', 'approved') AND OLD.status NOT IN ('done', 'approved'))
EXECUTE FUNCTION pm_unblock_dependents();
```

`pm_unblock_dependents` walks `task_links` and promotes all dependent tasks from `backlog/blocked`
to `ready` if their parents are `done/approved`. The `PATCH /status` endpoint fires this trigger
unconditionally in the same transaction. An unauthenticated attacker can unblock the entire
downstream DAG for any task, causing dependent tasks to be picked up and executed by workers
without any legitimate approval.

**Why severity is Critical (not just High).** The severity scale defines Critical as: "gate bypass /
auth forgery / secret exposure — blocks real credentials." Path B meets that bar without any
pre-conditions: an unauthenticated local call to `PATCH /status` with `{"status": "approved"}`
directly fires `trg_pm_unblock_dependents` in the same DB transaction, which writes
`status = 'ready'` to all downstream tasks (`schema-additions.sql:287`). Workers pick up `ready`
tasks unconditionally (`worker.py:233`). No JWT, no `token_class` check, no `approved_at` — the
unauthenticated caller has executed a gate bypass that dispatches real work. The API is bound to
`127.0.0.1:8000`, so only local processes can reach it — but the Guardian doctrine's explicit threat
model is "the orchestrator can be fully compromised," and the orchestrator runs on the same machine.
This is exactly the forgery vector RT-01 closed on the gate endpoint, left open on the status
endpoint.

**Recommended fix.**

Two independent changes; both are needed:

1. Add the same JWT guard as `submit_gate_outcome` to `patch_task_status`. Or (simpler for v1):
   remove `"approved"`, `"rejected"`, and `"done"` from the set of statuses the PATCH endpoint
   may write. Terminal states should only be reachable through the gate.

2. Change the idempotency guard at `spine.py:342` from keying on `status` to keying on
   `approved_at IS NOT NULL`:
   ```python
   # Before:
   if row and row["status"] in ("approved", "rejected"):
       return {}
   # After:
   if row and row["approved_at"] is not None:
       return {}
   ```
   Include `approved_at` in the `SELECT` at `spine.py:338` to support this.

---

### SEC-02 — Low — `_claims["sub"]` KeyError → 500 instead of 403

**What it is.** `api.py:267`:
```python
approved_by = _claims["sub"]
```
`validate_token` (`tokens.py:44–45`) calls `jwt.decode` without requiring specific claims.
A token that is validly signed (correct secret, valid `exp`) but omits the `sub` claim
passes the `token_class` check at `api.py:262` and then raises `KeyError` at line 267.
FastAPI converts this to HTTP 500.

**Why severity is Low.** Triggering this requires a validly-signed token, which requires
possession of `TALOS_JWT_SECRET`. An attacker holding the secret can already forge any
token — the 500 response provides no useful escalation. This is a robustness issue, not
an access-control bypass.

**Recommended fix.** Either:
```python
approved_by = _claims.get("sub")
if not approved_by:
    raise HTTPException(status_code=403, detail={"error": "human session required"})
```
Or add `options={"require": ["sub", "exp", "iat"]}` to `jwt.decode` in `validate_token`
so missing required claims raise `MissingRequiredClaimError` (a `PyJWTError` subclass)
and fall into the existing HTTP 403 catch at `api.py:254–261`.

---

### SEC-03 — Medium — FORCE RLS absent; tests connect as superuser — **RESOLVED 2026-06-18**

**What it is.** Two related gaps:

**Gap A: No `FORCE ROW LEVEL SECURITY`.**  
`engine/schema.sql:273–366` enables RLS on all board-scoped tables
(`ALTER TABLE tasks ENABLE ROW LEVEL SECURITY`, etc.) but never adds
`ALTER TABLE tasks FORCE ROW LEVEL SECURITY`. In PostgreSQL, `ENABLE` alone does not apply
policies to the table owner. Alembic migrations run as the postgres superuser, so all tables
are owned by postgres. Any connection that uses the postgres superuser role bypasses all
`board_isolation` policies silently.

In production this is mitigated: `docs/install.md:37` sets `TALOS_DB_DSN` to `talos_app`
(NOSUPERUSER, non-owner), so the runtime connection correctly sees RLS. But the protection
depends entirely on the DSN being correct — there is no structural backstop.

**Gap B: Integration tests use superuser DSN.**  
`conftest.py:67`: `os.environ["TALOS_DB_DSN"] = admin_dsn` — where `admin_dsn` is the
testcontainers superuser connection. Every `get_conn()` call in the tested code paths
(spine, API, worker, auth) therefore connects as the table owner and bypasses RLS.
The `app_conn` fixture exists for explicit RLS unit tests but is never used on the gate,
worker, or API integration paths. Board isolation is a stated hard guarantee (CLAUDE.md:
"MCP boundary = security boundary") that is not exercised by the test suite.

**Why it matters in v1.** For a single-board, single-user air-gapped install the practical
risk from gap A is low — there is no cross-board data to isolate. Gap B means the test suite
does not verify the board isolation guarantee. If multiple boards or clients are ever loaded
(the stated v1 use-case involves per-client NEXUS instances), both gaps matter.

**Fix applied.**

- **Gap A:** `engine/migrations/versions/V0003_force_rls.py` adds `FORCE ROW LEVEL SECURITY` for
  all 15 board-scoped tables; `downgrade()` reverses. Mirrored in `conftest.py` (conftest stamps,
  not upgrades). `pg_class.relforcerowsecurity = true` for all 15 tables after migration.
- **Gap B:** `conftest.py` now sets `TALOS_DB_DSN` to a `talos_app` DSN; the full test suite
  runs under enforced RLS. `admin_conn` remains superuser for seeding only.
- **Heartbeat correctness:** `make_heartbeat_callback` now accepts `board_id`, drops
  `autocommit=True`, and wraps the UPDATE in `board_scope`. Previously the UPDATE was silently
  blocked under `talos_app` RLS.
- **Reclaim correctness:** `reclaim_dead_workers` now opens its own `talos_system` (BYPASSRLS)
  connection via `get_system_conn()` — it no longer shares the `talos_app` conn that lacks
  cross-board visibility. `TALOS_RECLAIM_DSN` required; fail-closed if unset. See ADR-037.
- **Tests:** 3 new tests in `talos/tests/test_rls.py` (cross-board isolation, heartbeat writes
  under app role, reclaim across boards). Total: 70 tests passing.

---

### SEC-04 — Low — Username timing oracle in `verify_user`

**What it is.** `users.py:42–43`:
```python
if row is None:
    return False      # fast — no argon2 work (~0 ms)
_ph.verify(...)       # slow — argon2id ~300 ms
```
An unknown username returns immediately; a known username (wrong password) pays the full
argon2 verification cost. A caller with sub-millisecond timing resolution can enumerate
valid usernames.

**Why severity is Low in v1.** The deployment is an air-gapped, localhost-only,
single-user workstation. A network timing attacker is not in scope. ADR-036 explicitly
defers rate-limiting and timing hardening.

**Recommended fix.** Compute a module-level dummy hash at import time and always call
`_ph.verify` even on the miss path:
```python
_DUMMY_HASH = _ph.hash("_dummy_")

def verify_user(username, password):
    ...
    if row is None:
        try:
            _ph.verify(_DUMMY_HASH, password)
        except Exception:
            pass
        return False
```

---

### SEC-05 — Info — `TALOS_JWT_EXPIRY_HOURS` has no upper bound

**What it is.** `tokens.py:29`:
```python
expiry_hours = int(os.environ.get("TALOS_JWT_EXPIRY_HOURS", "8"))
```
Any positive integer is accepted. A misconfiguration or typo (`"800"` instead of `"8"`)
silently creates a 33-day token. For a single-operator deployment with no token revocation
(ADR-036 deferred item #1), a long-lived token is a larger blast radius on theft.

**Recommended fix.** Add an upper-bound guard:
```python
MAX_EXPIRY_HOURS = 24
expiry_hours = min(int(os.environ.get("TALOS_JWT_EXPIRY_HOURS", "8")), MAX_EXPIRY_HOURS)
```

---

### SEC-06 — Info — `argon2.check_needs_rehash` never called

**What it is.** `users.py:44` calls `_ph.verify(row["hashed_password"], password)` on login
but never calls `_ph.check_needs_rehash(row["hashed_password"])`. When argon2-cffi raises its
default parameters in a future version, all stored hashes silently stay at the old cost.

**Recommended fix.** After a successful verify, call `check_needs_rehash` and update the DB
row if needed:
```python
if _ph.check_needs_rehash(row["hashed_password"]):
    new_hash = _ph.hash(password)
    # UPDATE users SET hashed_password = %s WHERE username = %s
```

---

### SEC-07 — Low — Default PostgreSQL role password never prompted for change

**What it is.** `docs/install.md:52`:
```bash
psql -U postgres -c "CREATE ROLE talos_app NOSUPERUSER NOINHERIT LOGIN PASSWORD 'talos_app';"
```
The `TALOS_DB_DSN` example at line 37 also hardcodes `talos_app:talos_app`. Unlike
`TALOS_JWT_SECRET`, the install runbook does not prompt the operator to replace this password.

**Why severity is Low.** The PostgreSQL port is typically not exposed externally on an air-gapped
workstation, and `talos_app` is NOSUPERUSER. But a second local process (including a compromised
orchestrator) can connect directly to PostgreSQL as `talos_app` and bypass the API layer entirely.

**Recommended fix.** Change `install.md` step 3 to use a placeholder prompt (like
`TALOS_JWT_SECRET`):
```bash
# Generate a strong DB password:
python -c "import secrets; print(secrets.token_urlsafe(24))"
# Then use it in the CREATE ROLE command and TALOS_DB_DSN.
```

---

## Explicit "no findings" confirmations

These areas were checked and found sound. The audit was not a rubber stamp — each was verified
with `file:line` citations.

| Area | Result | Evidence |
|---|---|---|
| HS256 algorithm pinning on encode | PASS | `tokens.py:37` — `algorithm="HS256"` |
| HS256 algorithm pinning on decode | PASS | `tokens.py:45` — `algorithms=["HS256"]`; no `alg=none` / RS↔HS confusion possible |
| JWT `exp` claim enforced | PASS | PyJWT enforces `exp` by default; no `options={"verify_exp": False}` anywhere |
| `TALOS_JWT_SECRET` fail-closed at startup | PASS | `api.py:31–34` raises RuntimeError if absent; `tokens.py:14–18` same |
| `TALOS_JWT_SECRET` not in logs or error responses | PASS | No logging calls in `tokens.py`, `users.py`, or the auth path of `api.py`; RuntimeError and PyJWTError messages contain no secret value |
| `approved_by` derives only from JWT `sub`, never request body | PASS | `GateOutcomeRequest` (`api.py:103–109`) has no `approved_by` field; comment at line 108 confirms intent; `approved_by = _claims["sub"]` at line 267 |
| `token_class == "human"` check is the only gate into `post_gate_node` | PASS | `api.py:262–266` rejects non-human token classes with HTTP 403; no alternative invoke path sets `approved_at` |
| SQL parameterization in `users.py` | PASS | `add_user` uses `(%s, %s)` at line 22; `verify_user` uses `%s` at line 36; no f-string or string-interpolated SQL |
| SQL parameterization in `api.py` | PASS | All `cur.execute()` calls use `%s` placeholders; `board_scope`'s `SET LOCAL app.board_id = %s` at `db.py` is also parameterized |
| `getpass.getpass()` used in CLI (password not echoed) | PASS | `__main__.py:20` — `getpass.getpass(f"Password for {username!r}: ")` |
| argon2id used (not a fast hash) | PASS | `users.py:6,11` — `PasswordHasher()` uses argon2id variant; `add_user` hashes before DB insert |
| Login endpoint error does not discriminate username vs password | PASS | `api.py:121–122` — catches all `ValueError` from `issue_token`, returns generic HTTP 401 `"invalid credentials"` |
| Empty password rejected by CLI | PASS | `__main__.py:21–23` — exits with error if `not password` |
| `users` table is the only non-RLS table (intentional) | PASS | `schema.sql` enables RLS on all board-scoped tables; `users` is board-agnostic by design (ADR-036 §4); no `board_id` column in `users` |
| RT-01 (forged-approval BLOCKER) is closed by this commit | PASS | `api.py:249–267` — three-layer check: header presence → JWT signature/exp → `token_class == "human"`; all three return HTTP 403 with identical message (no information leak about which layer failed) |
| RT-16 (board-api authn contract gap) is closed | PASS | ADR-036 specifies the authn model; `docs/contracts/board-api.md` Open question #4 is marked closed |
| Production runtime role is `talos_app` (NOSUPERUSER, non-owner) → RLS applies | PASS | `docs/install.md` — `TALOS_DB_DSN` uses `talos_app`; role is `NOSUPERUSER NOINHERIT`; V0003 adds FORCE |
| No `approved_at` write path exists outside `post_gate_node` | PASS | Grepped all `.py` files; `approved_at` is only set in `spine.py:359,436,510` (all inside `post_gate_node`) |

---

## Open items surfaced by SEC-03 work (not fixed in this task)

| ID | Title | Notes |
|---|---|---|
| OPEN-01 | Vestigial `admin_bypass` RLS policy | All 15 board-scoped tables have `CREATE POLICY {t}_admin_bypass ... USING (current_user = 'talos_admin')`. Nothing connects as `talos_admin` — migrations run as postgres (superuser, FORCE exempt), tests use superuser for admin_conn, product code uses talos_app. The policy is dead code. Low-risk to leave; recommend removing in a dedicated cleanup task to reduce policy surface. |
| OPEN-02 | `boards` table has no RLS — full board enumeration possible as `talos_app` | `SELECT * FROM boards` returns all boards regardless of `app.board_id`. Low-risk for the air-gapped single-operator v1 deployment, but is a real gap in the "board isolation" story if multiple clients are ever loaded. Future task: add RLS to `boards`, or accept the gap in a documented ADR. |
