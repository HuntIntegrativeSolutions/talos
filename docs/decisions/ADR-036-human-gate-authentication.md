# ADR-036: Human gate authentication — local JWT with username/password

**Status:** Accepted  
**Date:** 2026-06-17  
**Deciders:** Hunt Integrative Solutions LLC  
**Closes:** RT-01 (forged-approval BLOCKER), RT-16 (board-api.md Open question #4)

## Context

`submit_gate_outcome()` previously trusted the `X-Human-Session` header as a plain
username string. Any caller could send `X-Human-Session: thunt` and the gate accepted
it. The Guardian doctrine requires `approved_by` to derive from a verified human
identity — never a self-asserted string.

TALOS v1 is deployed on-prem at an engineer's workstation, air-gapped by default
(ROADMAP v1 Charter). A cloud IdP or OAuth provider is not assumed to be reachable.
The auth system must be bootstrappable from a USB transfer and a single CLI command.

## Decision

### 1. JWT format and signing

- **Library:** PyJWT (HMAC-HS256). Pure Python, no native extension required.
- **Algorithm:** HS256 (HMAC-SHA256). Sufficient for a single-operator workstation;
  asymmetric keys (RS256) are deferred to a future OIDC/SSO phase.
- **Secret:** `TALOS_JWT_SECRET` environment variable. Required; the server raises
  `RuntimeError` at startup (FastAPI lifespan) if absent. Never hard-coded.
- **Expiry:** 8 hours default; configurable via `TALOS_JWT_EXPIRY_HOURS` env var.

### 2. JWT payload

```json
{
  "sub": "<username>",
  "token_class": "human",
  "iat": <unix timestamp>,
  "exp": <unix timestamp>
}
```

`token_class: "human"` is a structural boundary. The gate endpoint rejects any token
whose `token_class` is not `"human"` with HTTP 403. This design does not preclude
future service/machine token classes; it makes them structurally incapable of
substituting for a human approval.

### 3. Password hashing

- **Library:** argon2-cffi (argon2id). OWASP-recommended memory-hard KDF.
- Passwords are never stored in plaintext or with a fast hash (SHA-256, PBKDF2, etc.).

### 4. `users` table

```sql
CREATE TABLE users (
    username         TEXT PRIMARY KEY,
    hashed_password  TEXT NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now()
);
```

- Board-agnostic — not RLS-scoped. Auth is global; a user is not bound to a board.
- Created via Alembic migration `V0002_auth_users.py`. Not added to `schema.sql` or
  any raw SQL file (ADR-034 requires Alembic from here on).
- `talos_app` role has SELECT/INSERT/UPDATE on `users` via the existing blanket GRANT.

### 5. Bootstrap CLI

Fresh air-gapped install: `python -m talos.auth add-user <username>` prompts for
password, hashes with argon2id, inserts into `users`. This is the only path to
create the first user. Documented in `docs/install.md`.

### 6. SSO/OIDC explicitly deferred

Multi-tenant SSO, OIDC, OAuth, and SAML are out of scope for v1. This ADR records
the intent explicitly so future sessions can address them without re-litigating the
v1 decision. HS256 + local password is the full auth story for v1.

### 7. Air-gap note

v1 is "on-prem, air-gapped by default." The following pip wheels must be pre-staged
on the target workstation (download on a networked machine; transfer via USB or local
PyPI mirror) before install:

- `alembic`
- `sqlalchemy`
- `PyJWT`
- `cryptography`
- `argon2-cffi`

## Deferred security items

These are **explicitly deferred, not forgotten**. Each is an acceptable gap for a
single-operator air-gapped v1 workstation; each would need to be addressed before
a multi-user or networked deployment.

1. **No token revocation** — a leaked JWT is valid for the full 8-hour window.
   There is no revocation list, no session table, and no logout endpoint.
2. **No login rate-limiting** — argon2id's memory-hardness is the only brute-force
   backstop. No lockout counter or delay is implemented in v1.
3. **Authn, not authz** — the JWT proves *who* the human is, not *which boards* they
   may approve on. Per-board authorization is deferred to a later phase.

## Consequences

- `X-Human-Session` header must carry a valid TALOS JWT (`token_class: "human"`).
  The engine rejects anything else with HTTP 403 `{"error": "human session required"}`.
- `approved_by` is always set from the JWT `sub` claim — never from any request body
  field, and never from a self-asserted header string.
- `docs/contracts/board-api.md` Open question #4 is closed by this ADR.
