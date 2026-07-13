# TALOS Installation Runbook (v1 — on-prem workstation)

## Prerequisites

TALOS v1 targets an on-prem engineer workstation, air-gapped by default. Before
installing, download the following pip wheels on a networked machine and transfer
via USB or local PyPI mirror:

```
alembic
sqlalchemy
PyJWT>=2.0
cryptography
argon2-cffi
fastapi
uvicorn[standard]
psycopg2-binary
psycopg[binary]
langgraph>=0.2
langgraph-checkpoint-postgres
requests
```

You also need Python 3.11+ and PostgreSQL 16 on the target machine.

## 1. Install Python dependencies

```bash
pip install --no-index --find-links /path/to/wheels -e ".[app]"
```

## 2. Configure environment variables

Set the following in your shell profile or a `.env` file (not committed):

```bash
export TALOS_DB_DSN="postgresql://talos_app:<password>@localhost/talos"
export TALOS_RECLAIM_DSN="postgresql://talos_system:<password>@localhost/talos"
export TALOS_JWT_SECRET="<strong random string — at least 32 chars>"
export TALOS_JWT_EXPIRY_HOURS="8"  # optional, default is 8
```

Generate strong secrets/passwords:
```bash
python -c "import secrets; print(secrets.token_hex(32))"  # for TALOS_JWT_SECRET
python -c "import secrets; print(secrets.token_urlsafe(24))"  # for role passwords
```

`TALOS_RECLAIM_DSN` must point to the `talos_system` BYPASSRLS role (created in step 3). If
unset, the worker dispatcher will raise `RuntimeError` on startup rather than silently
returning zero rows for cross-board reclaim.

## 3. Initialise the database

```bash
# Create the talos database and roles as postgres superuser.
# Replace <talos_app_pw> and <talos_system_pw> with strong generated passwords.
psql -U postgres -c "CREATE DATABASE talos;"

# talos_app: application role — RLS enforced (NOSUPERUSER)
psql -U postgres -c "CREATE ROLE talos_app NOSUPERUSER NOINHERIT LOGIN PASSWORD '<talos_app_pw>';"
psql -U postgres -c "GRANT ALL ON DATABASE talos TO talos_app;"

# talos_system: cross-board reclaim janitor — BYPASSRLS, minimal grants (ADR-037)
psql -U postgres -c "CREATE ROLE talos_system BYPASSRLS NOSUPERUSER NOINHERIT LOGIN PASSWORD '<talos_system_pw>';"
psql -U postgres -c "GRANT CONNECT ON DATABASE talos TO talos_system;"

# Apply all schema migrations (includes FORCE ROW LEVEL SECURITY — V0003):
alembic -c engine/alembic.ini upgrade head

# Grant talos_app access to all tables (run as postgres superuser):
psql -U postgres -d talos -c "GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO talos_app;"
psql -U postgres -d talos -c "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO talos_app;"

# talos_app additionally needs DELETE on chunks: talos.memory.pgvector_store's
# upsert_rule/ingest_deliverable implement idempotent re-embedding as
# delete-then-insert scoped by (board_id, rule_id/task_id), since chunks has
# no external-id column to ON CONFLICT against (ADR-039 action item #3).
psql -U postgres -d talos -c "GRANT DELETE ON chunks TO talos_app;"

# talos_app additionally needs DELETE on notes/links/tags: talos.vault.indexer's
# --rebuild and file-deletion handling delete this board's vault-owned rows
# (talos_app isn't the table owner and has no TRUNCATE grant) -- ADR-039 action
# item #2.
psql -U postgres -d talos -c "GRANT DELETE ON notes, links, tags TO talos_app;"

# talos_app additionally needs DELETE on note_entity_links: talos.vault.indexer's
# --rebuild and note-deletion handling hard-delete this note's entity links
# -- ADR-039 action item #4.
psql -U postgres -d talos -c "GRANT DELETE ON note_entity_links TO talos_app;"

# Grant talos_system access for cross-board reclaim:
# SELECT on all tables is required so triggers (pm_recompute_scheduling) can read
# v_critical_path and underlying tables when talos_system updates task status.
psql -U postgres -d talos -c "GRANT USAGE ON SCHEMA public TO talos_system;"
psql -U postgres -d talos -c "GRANT SELECT ON ALL TABLES IN SCHEMA public TO talos_system;"
psql -U postgres -d talos -c "GRANT UPDATE ON task_runs, tasks TO talos_system;"
psql -U postgres -d talos -c "GRANT INSERT ON task_spans TO talos_system;"
psql -U postgres -d talos -c "GRANT USAGE, SELECT ON SEQUENCE task_spans_id_seq TO talos_system;"
```

## 4. Bootstrap the first user

This is the only way to create users on an air-gapped install:

```bash
python -m talos.auth add-user <your-username>
# Prompts for password (hidden). Uses argon2id hashing.
```

You can repeat this command for additional users.

## 5. Start the server

```bash
uvicorn talos.api:app --host 127.0.0.1 --port 8000
```

The server raises `RuntimeError` at startup if `TALOS_JWT_SECRET` is not set.

## 6. Obtain a session token

```bash
curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "<your-username>", "password": "<your-password>"}' \
  | python -m json.tool
# Returns: {"token": "<jwt>"}
```

Pass the token in the `X-Human-Session` header when calling the gate endpoint:
```bash
curl -s -X POST http://localhost:8000/boards/<b>/tasks/<t>/gate \
  -H "X-Human-Session: <jwt>" \
  -H "Content-Type: application/json" \
  -d '{"outcome": "approve"}'
```

## Upgrading the schema

Before any `alembic upgrade`, take a backup:

```bash
pg_dump talos > talos_backup_$(date +%Y%m%d).sql
alembic -c engine/alembic.ini upgrade head
```

Existing installations at the pre-Alembic schema can be stamped as baseline without
re-applying the schema:

```bash
alembic -c engine/alembic.ini stamp V0001
# Then apply new migrations only:
alembic -c engine/alembic.ini upgrade head
```
