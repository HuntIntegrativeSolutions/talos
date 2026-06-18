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
export TALOS_DB_DSN="postgresql://talos_app:talos_app@localhost/talos"
export TALOS_JWT_SECRET="<strong random string — at least 32 chars>"
export TALOS_JWT_EXPIRY_HOURS="8"  # optional, default is 8
```

Generate a strong secret:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

## 3. Initialise the database

```bash
# Create the talos database and roles as postgres superuser:
psql -U postgres -c "CREATE DATABASE talos;"
psql -U postgres -c "CREATE ROLE talos_app NOSUPERUSER NOINHERIT LOGIN PASSWORD 'talos_app';"
psql -U postgres -c "GRANT ALL ON DATABASE talos TO talos_app;"

# Apply all schema migrations:
alembic -c engine/alembic.ini upgrade head

# Grant talos_app access to all tables (run as postgres superuser):
psql -U postgres -d talos -c "GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO talos_app;"
psql -U postgres -d talos -c "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO talos_app;"
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
