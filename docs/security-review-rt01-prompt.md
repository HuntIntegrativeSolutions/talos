# TALOS — Security Review of the RT-01 Auth + Gate Surface

Paste this into a **fresh** Claude Code session. Working directory: `/mnt/i/talos/`

**What this task is and why it's next.** RT-01 just landed: the gate endpoint now trusts a
validated HS256 JWT (`token_class="human"`) instead of a forged header string, and a new local
auth server (`talos/auth/`, `POST /auth/login`, argon2id users, Alembic V0002) issues those
tokens. This is the most security-critical code in TALOS so far, and the next thing the roadmap
gates on is exactly this: **"Security review (`/security-review`) before real client data with
real credentials"** (`ROADMAP.md:383`, P0-Foundation). The human-approval invariant is now
*structurally* enforced by this code — so before any real credentials or NDA client data touch the
system, we audit the auth/gate attack surface and the RLS boundary it sits behind.

This session is **review and triage only.** Produce a findings report. Do **not** fix anything
yet — each fix is its own small task the user will approve individually (one task at a time).

---

## Step 1 — Read the surface under review (in this order)

Read these fully before asserting anything. Cite `file:line` for every finding.

1. `docs/decisions/ADR-036-human-gate-authentication.md` — the auth decision RT-01 implements;
   the threat it closes and the boundaries it claims.
2. `docs/integration/03_redteam_review.md` — the **RT-01** entries ("forged approval") and
   **RT-16** (board-api authn contract gap). Confirm the implementation actually closes them.
3. `talos/auth/tokens.py` — `_get_secret`, `issue_token`, `validate_token`. The JWT issue/verify
   core (HS256).
4. `talos/auth/users.py` — `add_user`, `verify_user` (argon2id, raw psycopg2).
5. `talos/auth/__main__.py` — the `add-user` bootstrap CLI (first-user provisioning).
6. `talos/api.py` — the login endpoint (`/auth/login`, ~line 116) and the gate write path
   (`submit_gate_outcome`, ~line 241): JWT validation at lines 249–267, `approved_by =
   _claims["sub"]` at 267, and the spine resume / `post_gate_node` write at 339–356.
7. `engine/migrations/versions/V0002_auth_users.py` — the `users` table DDL (constraints,
   nullability, uniqueness).
8. `engine/schema.sql` + `talos/tests/conftest.py` — confirm RLS is still enabled on board-scoped
   tables and that the `talos_app` role grants were **not widened** by RT-01. `users` is
   intentionally non-RLS; verify that's the only table exempted.
9. `docs/install.md` — the air-gapped install runbook: how `TALOS_JWT_SECRET` is generated and
   stored, and how the first user is created.

Use the `Explore` agent for breadth if helpful, but read `tokens.py`, `users.py`, the
`submit_gate_outcome` path in `api.py`, and `V0002` yourself.

## Step 2 — Run the diff-based pass, then go deeper

RT-01 is already committed (clean tree), so a bare `/security-review` finds no pending changes.
Run it against the RT-01 commit range instead, then layer the manual deep-dive on top:

```bash
git show dc99641 --stat        # the RT-01 commit
git diff dc99641^..dc99641     # the full RT-01 diff to review
```

Invoke `/security-review` and point it at that diff as the starting engine. Then manually examine
the focus areas in Step 3 — the skill is a floor, not the ceiling, for this audit.

## Step 3 — Focus areas (examine each; confirm or refute)

These are *questions to investigate*, not assertions that something is broken. For each, state the
finding, severity, and `file:line`.

**JWT handling**
- Is the signing algorithm pinned on decode (no `alg=none` / RS↔HS confusion)? Confirm
  `algorithms=["HS256"]` is enforced and no caller bypasses `validate_token`.
- `approved_by = _claims["sub"]` (`api.py:267`): what happens if a validly-signed token omits
  `sub`? Trace whether that is a `KeyError → 500` instead of a clean `401/400`.
- Is `exp` actually enforced (PyJWT default) and is there any clock-skew or long-expiry concern?
  Is `TALOS_JWT_EXPIRY_HOURS` bounded?

**Secret management**
- `TALOS_JWT_SECRET`: fail-closed when unset is good — but is there any minimum-entropy/length
  guard, and does `docs/install.md` mandate a strong, randomly-generated secret? Could a weak
  secret enable offline brute-force of the HMAC?
- Can the secret leak into logs, tracebacks, `task_spans`, or error responses anywhere?

**Credential / login path**
- `verify_user` returns `False` immediately when the user row is missing, but runs argon2 verify
  when it exists — does this create a username-enumeration timing oracle? (Severity is lower for a
  single-user air-gapped workstation; still record it.)
- Is there any rate-limiting / lockout on `/auth/login`? What's the brute-force exposure?
- Does `/auth/login` leak which of username/password was wrong? (It should not.)
- argon2id: are defaults acceptable, and is `check_needs_rehash` needed? Is the password ever
  logged or echoed (CLI uses `getpass` — confirm)?

**The gate boundary itself (the load-bearing invariant)**
- Is there **any** path to set `approved_at` / `approved_by` that does *not* pass through JWT
  validation + the `token_class=="human"` check? Check the spine `post_gate_node`, direct DB
  writes, and any test-only hooks.
- Can a service/worker token (or any non-human `token_class`) reach the gate write? Confirm the
  rejection at `api.py:262` cannot be bypassed.
- Does `approved_by` still derive **only** from the authenticated session, never from the request
  body? (RT-01's core claim — verify the body has no `approved_by` field path.)

**RLS / data isolation**
- Confirm RT-01 did not disable RLS, widen `talos_app` grants, or add a board-scoped query that
  forgets to `SET app.board_id`. Is `users` the only non-RLS table, and is that intentional and
  safe (no `board_id` leakage through it)?

**SQL / injection**
- Confirm every auth query is parameterized (spot-check `users.py`). Confirm the CLI username arg
  cannot inject.

## Step 4 — Write the findings report (the deliverable)

Write a triaged report to:

```
docs/security-review-rt01-findings.md
```

Structure:
- **Verdict line** — is the RT-01 auth/gate surface safe to put in front of real credentials as-is,
  or are there blockers to fix first?
- **Findings table** — one row per finding: ID, title, severity, `file:line`, one-line impact.
- **Severity scale** — `Critical` (gate bypass / auth forgery / secret exposure — blocks real
  credentials) / `High` / `Medium` / `Low` / `Info`. Weight severity for the actual v1 deployment:
  **on-prem, air-gapped, single/few-user workstation** (per the v1 Charter) — a timing oracle or
  missing rate-limit is real but not Critical in that context; a gate-bypass path *is* Critical.
- **Per-finding detail** — for each: what it is, why it matters in the v1 context, and a concrete
  recommended fix (the smallest change that closes it). Do **not** apply the fix.
- **Explicit "no findings" confirmations** — list the things you checked and found sound (alg
  pinning, parameterized queries, fail-closed secret, RLS intact, etc.) so the user knows the
  audit was real, not a rubber stamp.

## Step 5 — Report back

Final message: (a) the verdict (safe-for-real-credentials yes/no), (b) the count of findings by
severity, (c) the path to the findings report, (d) the single highest-priority fix you'd do first
and whether it should be its own task. Then **stop** — the user decides which findings become the
next coding task(s).

## Rules for this session

- **Review and triage only. Do not edit source, schema, ADRs, or fix any finding.** The only new
  file is `docs/security-review-rt01-findings.md`.
- Read the real files before asserting; cite `file:line` for every finding and every confirmation.
- Severity must reflect the **on-prem air-gapped single-user v1 deployment**, not a public
  internet-facing service. Say so when it changes a rating.
- Do not rubber-stamp. If the surface is genuinely clean, say that plainly with evidence. If
  there's a gate-bypass or auth-forgery path, that is Critical and the headline finding.
- One task at a time: this audit produces the *menu* of fixes; it does not perform them.

Start with Step 1.
