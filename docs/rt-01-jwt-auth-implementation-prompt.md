# TALOS — RT-01 JWT Auth Implementation Prompt

Paste this into a new Claude Code session. Working directory: `/mnt/i/talos/`

---

You are closing **RT-01 — forged approval BLOCKER** in TALOS. The Guardian doctrine requires that
`approved_by` derives from a verified human identity — never a self-asserted string. Today the
`submit_gate_outcome` endpoint at `talos/api.py:210` trusts the `X-Human-Session` header as a plain
username; any caller can send `X-Human-Session: thunt` and the gate accepts it. Your job is to
replace that trust with a verified JWT and record the decision as an ADR.

**One task. One ADR. No scope creep.**

---

## Read these files before writing a single line of code

Read all of them. Column names, function signatures, and test patterns are derived from the real
code — do not guess.

- `talos/api.py:210–312` — `submit_gate_outcome()`: where `X-Human-Session` is read and `approved_by`
  is set; the `_SYSTEM_ACCOUNTS` rejection block; how `x_human_session` flows into the LangGraph
  `Command` resume payload
- `engine/schema.sql:71–77` — `approved_by`, `approved_at`, `rejected_by/at`, `rejection_reason`
  columns on `tasks`
- `engine/schema.sql:171–183` — `task_gate_results` structure
- `docs/contracts/board-api.md:117–151` — §3 gate-outcome write path; the illustrative envelope
  with `actor: "thunt"`; Open question #4 (human vs service authn model); note RT-16
- `docs/decisions/ADR-011-gate-outcomes.md` — the five gate outcomes; human approval invariant
- `docs/decisions/ADR-034-schema-migration-versioning.md` — Alembic is required for all future
  schema changes; baseline migration is a prerequisite before any new migration
- `docs/integration/03_redteam_review.md` — RT-01 and RT-16 entries in full (the BLOCKER and
  the contract-gap that RT-01 closes together)
- `docs/integration/04_build_sequence.md §4` — P1 RT-01 verification criteria (use as your DoD checklist)
- `ROADMAP.md:351–395` — v1 Charter auth constraints: JWT, local username/password, on-prem /
  air-gapped by default; note the discrepancy on line 362 (`ADR-028/RT-01` should be `ADR-036/RT-01`)
- `talos/tests/test_spine.py:88–180` — existing test pattern for the gate; `X-Human-Session`
  header usage at lines 90, 169, 177
- `talos/tests/test_p2_gate.py:68–72` — `_gate()` helper and default `headers={"X-Human-Session": "thunt"}`
- `talos/tests/conftest.py` — testcontainers fixture pattern to match in new tests
- `docs/decisions/README.md` — confirm ADR-035 is last, so ADR-036 is the next free number
- `pyproject.toml` — existing deps; note what auth/crypto libs are already present

---

## Scope

### In scope

1. **Write ADR-036 first** (`docs/decisions/ADR-036-human-gate-authentication.md`) recording:
   - v1 decision: local JWT, username/password, argon2id hashing, `token_class: "human"` claim
   - The human-vs-service token-class boundary (designed not to preclude future OIDC/SSO)
   - Three explicitly-deferred security items (see Decisions §6 below)
   - Closes RT-01 and RT-16 together

2. **Establish Alembic baseline** (P0 task per ROADMAP; required by ADR-034 before any schema change):
   - Read ADR-034 and `conftest.py` to confirm which SQL files the test harness currently applies
   - Create `engine/alembic.ini` and `engine/migrations/` directory structure
   - Write `engine/migrations/V0001_baseline.py` — a raw SQL migration that applies
     `schema.sql` + `schema-additions.sql` (and `schema-p2.sql` if it exists) as a single baseline
   - `alembic stamp head` marks existing dev databases as already at baseline
   - Update the testcontainers fixture in `conftest.py` to use `alembic upgrade head` instead of
     raw `psql -f schema.sql`

3. **New `talos/auth/` module:**
   - `talos/auth/__init__.py`
   - `talos/auth/tokens.py` — `issue_token(username, password) -> str`,
     `validate_token(token: str) -> dict`
   - `talos/auth/users.py` — `add_user(username: str, password: str)`,
     `verify_user(username: str, password: str) -> bool`
   - `talos/auth/__main__.py` — CLI entry point: `python -m talos.auth add-user <username>`
     prompts for password, hashes with argon2id, inserts into `users` table. This is the only way
     to bootstrap the first user on a fresh air-gapped install; document this in the install runbook
     or a `docs/install.md` file.

4. **New `users` table as a named Alembic migration** (`engine/migrations/V0002_auth_users.py`):
   ```sql
   CREATE TABLE users (
       username         TEXT PRIMARY KEY,
       hashed_password  TEXT NOT NULL,
       created_at       timestamptz NOT NULL DEFAULT now()
   );
   ```
   `users` is board-agnostic (not RLS-scoped); auth is global. Document this in ADR-036.
   **Do NOT add to `schema.sql` or `schema-additions.sql`** — ADR-034 requires Alembic from here on.

5. **New `POST /auth/login` endpoint** in `talos/api.py`:
   - Body: `{username: str, password: str}`
   - Returns: `{token: "<jwt>"}` on success; HTTP 401 on bad credentials
   - JWT payload: `{sub: username, token_class: "human", iat: <now>, exp: <now + expiry>}`
   - Token expiry: 8 hours (configurable via `TALOS_JWT_EXPIRY_HOURS` env var; default 8)
   - Secret key: `TALOS_JWT_SECRET` env var (fail loudly at startup if not set — see Decisions §5)

6. **Update `submit_gate_outcome()`** (`talos/api.py:210–223`):
   - **Before removing `_SYSTEM_ACCOUNTS`**: run `grep -rn "_SYSTEM_ACCOUNTS" talos/` and confirm
     the only consumer is `submit_gate_outcome()`. The 56 existing tests should catch a regression,
     but verify explicitly before deleting.
   - Replace the plain-string trust on `X-Human-Session` with JWT validation: parse the header
     value as a JWT; reject if missing, invalid, or expired; check `token_class == "human"`,
     reject otherwise.
   - On valid human JWT: `approved_by = token_claims["sub"]` — never read `approved_by` from the
     request body.
   - On any rejection: HTTP 403 with `{"error": "human session required"}`.
   - Remove `_SYSTEM_ACCOUNTS` hardcoded blocklist after confirming no other consumers (the
     token-class claim handles this structurally).
   - Update the `GateOutcomeRequest` docstring comment to clarify `approved_by` comes from JWT `sub`.

7. **Update `docs/contracts/board-api.md`:**
   - §3: **close Open question #4** (designated at board-api.md:217, flagged by RT-16). State
     explicitly that this closes the designated open question, citing ADR-036 as the authority.
     This is not a unilateral change to a frozen [D] decision — RT-16 explicitly named this authn
     model as needing specification before the contract could be frozen. Record: the
     `X-Human-Session` header must carry a valid TALOS JWT with `token_class: "human"`; the engine
     rejects anything else with HTTP 403.
   - Update the illustrative envelope's `actor: "thunt"` comment to reflect that `actor` = JWT
     `sub` claim extracted server-side, never a field the caller supplies in the request body.

8. **Fix `ROADMAP.md` line 362**: change `ADR-028/RT-01` → `ADR-036/RT-01`

9. **Update existing tests** — four call sites that send `{"X-Human-Session": "thunt"}` must now
   send `{"X-Human-Session": "<valid_jwt>"}` using a shared fixture:
   - `talos/tests/test_spine.py:90, 169, 177`
   - `talos/tests/test_p2_gate.py:72`
   - Add a `pytest` fixture (in `conftest.py`) that creates a test user `"thunt"` in the `users`
     table and issues a valid JWT signed with `TALOS_JWT_SECRET=test-secret-dev-only`. The `_gate()`
     helper in `test_p2_gate.py` should use this fixture as its default `headers` value.
   - The "bad session" test at `test_spine.py:169` should switch to sending an invalid JWT string
     or a service-class token in `X-Human-Session`; preserve the intent (403 rejection).

10. **Update `CLAUDE.md` and `ROADMAP.md`** to mark RT-01 closed and note auth is live.

### Out of scope

- Cockpit login UI (that's P7a — built alongside this auth server, not in this task)
- Multi-tenant SSO / OIDC / OAuth — ADR-036 records this as deliberately deferred
- Any LLM or worker changes
- Other RT-* items (RT-06, RT-09, RT-14, RT-20 are separate tasks)
- Raw SQL schema files (`schema-auth.sql` or similar) — ADR-034 is unambiguous: all future schema
  changes are Alembic migrations. The `users` table goes in V0002, not in any `.sql` file.
- Adding `waivable` / `waived_by` / `justification` columns to `task_gate_results` (noted gap in
  board-api.md:141–144 but belongs to a future task, not RT-01)
- Refactoring unrelated code

---

## Decisions to settle before coding

Work through these in order. For each, state your chosen approach in a short comment before writing.

### 1. ADR-036 number — verify it is still ADR-036

`ls docs/decisions/` and confirm ADR-035 is the last numbered file. If a later ADR has been added
since this prompt was written, use the actual next free number and update all references below.

### 2. Auth header — DECIDED: keep `X-Human-Session`, require JWT value

**Decision (confirmed by user):** Keep `X-Human-Session` header; require it to hold a validated
JWT string, not a plain username.
- Before: `X-Human-Session: thunt` (trusted string, no validation)
- After: `X-Human-Session: <jwt>` (validated; `approved_by` = JWT `sub` claim)
- Test migration: 4 fixture call sites switch from a plain string to a JWT token; behavior unchanged.

Close board-api.md Open question #4 to record this decision.

### 3. Password hashing — argon2id (recommended)

`argon2-cffi` (`pip install argon2-cffi`) is the OWASP-recommended KDF — memory-hard and modern.
Acceptable fallback: `bcrypt`. Do not use SHA-256, PBKDF2, or any fast hash for password storage.
Record the choice in ADR-036.

### 4. JWT library — PyJWT (recommended)

`pip install PyJWT cryptography`. Pure Python, widely used in FastAPI ecosystems. Alternative:
`python-jose`. Choose one, add it to `pyproject.toml`, record in ADR-036.

**Air-gap note:** v1 is "on-prem, air-gapped by default" (ROADMAP v1 Charter). `PyJWT`,
`cryptography`, and `argon2-cffi` are new pip wheels that must be pre-staged on the target
workstation (download on a networked machine; transfer via USB or local PyPI mirror). Add a note
in ADR-036 and in the install runbook.

### 5. `TALOS_JWT_SECRET` missing at startup

At `talos/api.py` module load time, check for `TALOS_JWT_SECRET` and raise a descriptive
`RuntimeError` if absent — do not silently sign with a placeholder. Record this in ADR-036.

### 6. Three deferred security items — record in ADR-036

These are out of scope for v1 but must be explicitly stated (not silently dropped) in ADR-036's
"Deferred" section so future sessions can address them with full context:

- **No token revocation** — a leaked JWT is valid for the full 8-hour window. Acceptable for a
  single-operator air-gapped v1 workstation, but stated explicitly.
- **No login rate-limiting** — argon2id's memory-hardness is the only brute-force backstop in v1;
  no lockout counter or delay is implemented.
- **Authn, not authz** — the JWT proves *who* the human is, not *which boards* they may approve
  on. Per-board authorization is deferred to a later phase.

### 7. Alembic baseline scope

ADR-034 requires the Alembic baseline to cover the full current schema. Read `conftest.py` to see
which SQL files the test harness currently applies (likely `schema.sql` + `schema-additions.sql`;
check whether `schema-p2.sql` exists and is applied). V0001_baseline must cover everything that
`alembic upgrade head` on a fresh database needs to produce an identical schema to the current
raw-SQL sequence.

---

## Definition of Done

All of the following must be true before this task is complete:

**0. Alembic baseline established** — `engine/alembic.ini` and `engine/migrations/V0001_baseline.py`
   exist; existing dev databases can be stamped as at baseline; testcontainers fixture uses
   `alembic upgrade head`. Required by ADR-034 before the V0002 migration.

**1. ADR-036 exists** at `docs/decisions/ADR-036-human-gate-authentication.md` with status
   "Accepted", recording: local JWT, username/password, argon2id, `token_class: "human"`,
   8-hour default expiry, `TALOS_JWT_SECRET` env var, SSO/OIDC explicitly deferred, and the three
   deferred security items named above (no revocation, no rate-limiting, authn not authz).

**2. `talos/auth/` module exists** with `issue_token`, `validate_token`, `add_user`, `verify_user`,
   and `__main__.py` CLI (`python -m talos.auth add-user <username>` creates the initial user;
   install runbook documents this as the bootstrap step).

**3. `users` table created via Alembic migration `V0002_auth_users.py`** — not present in
   `schema.sql` or `schema-additions.sql`; only in the new migration.

**4. `POST /auth/login`** exists in `talos/api.py`; returns a valid JWT on correct credentials;
   HTTP 401 on bad credentials.

**5. `submit_gate_outcome()`** at `api.py:210`:
   - Validates `X-Human-Session` header value as a TALOS JWT (not a plain string)
   - `approved_by` = JWT `sub` claim (never from request body)
   - HTTP 403 if header is missing, value is not a valid JWT, token is expired, or
     `token_class != "human"`
   - `_SYSTEM_ACCOUNTS` blocklist removed (greppable check done first)

**6. `board-api.md` §3 Open question #4** is closed, citing ADR-036 as the authority: the
   `X-Human-Session` header must carry a valid TALOS JWT with `token_class: "human"`.

**7. `ROADMAP.md` line 362** reads `ADR-036/RT-01` not `ADR-028/RT-01`.

**8. All 56 existing tests still pass:**
   ```
   TALOS_JWT_SECRET=test-secret-dev-only TALOS_NEXUS_STUB=1 .venv/bin/python -m pytest talos/ -v
   ```

**9. 7 new tests pass** (see Test plan below).

**10. `CLAUDE.md` and `ROADMAP.md`** updated to show RT-01 closed.

---

## Test plan

Add `talos/tests/test_auth.py`. Use testcontainers Postgres matching the pattern in existing test
files (read `conftest.py` for the `pg_conn` / `admin_conn` fixture). Set
`TALOS_JWT_SECRET=test-secret-dev-only` via `monkeypatch.setenv` or `pytest-env`.

### Required new tests

```
test_issue_token_valid_user
  - add_user("thunt", "hunter2") in a fresh users table
  - issue_token("thunt", "hunter2") returns a non-empty string
  - validate_token(that string) returns claims with sub="thunt", token_class="human"

test_issue_token_bad_password
  - add_user("thunt", "hunter2")
  - issue_token("thunt", "wrong") raises ValueError (or returns None — pick a convention and
    document it; ensure validate_token does not accept the result)

test_gate_rejects_missing_token
  - POST /boards/{b}/tasks/{t}/gate with no X-Human-Session header → HTTP 403
  - response body contains "human session required"

test_gate_rejects_service_token
  - mint a JWT with token_class="service" (direct call to your token lib)
  - POST with X-Human-Session: <service_token> → HTTP 403

test_gate_accepts_human_token
  - add_user and issue_token for "thunt"
  - POST /gate with X-Human-Session: <valid_human_jwt>, outcome="approve"
    (with required critics passing first — follow the happy-path setup from test_p2_gate.py)
  - HTTP 200; response approved_by == "thunt"

test_add_user_cli_smoke
  - Invoke python -m talos.auth add-user with mocked password input ("hunter2")
  - Verify users table has a row with username="thunt" and a non-empty hashed_password
  - Verify verify_user("thunt", "hunter2") returns True
  - Verify verify_user("thunt", "wrong") returns False

test_approved_by_not_from_body
  - Issue a valid human JWT for "thunt"
  - POST /gate with X-Human-Session: <jwt>; outcome="approve"
    (critics passing; task in review state)
  - Verify via GET /boards/{b}/tasks/{t} that tasks.approved_by == "thunt" (the JWT sub)
```

Run command after all changes:
```bash
TALOS_JWT_SECRET=test-secret-dev-only TALOS_NEXUS_STUB=1 .venv/bin/python -m pytest talos/ -v
```

All 56 existing + 7 new tests must pass (total ≥ 63).

---

## Rules

- **One task only.** Do not fix RT-06, RT-09, RT-14, RT-20, or any other open item in this session.
- Read every file in the read-first list before writing code.
- Match the code style of `talos/api.py` — raw `psycopg2` with `RealDictCursor`, no ORM, no
  unnecessary abstractions.
- Do not refactor code unrelated to auth.
- Update `CLAUDE.md` project status and `ROADMAP.md` when the task is done.
- End report: one-line status (e.g., "RT-01 closed: JWT auth wired, 63 tests pass") and any new
  open questions that surfaced during implementation.
