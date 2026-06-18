# ADR-034: Schema migration versioning — tool choice and ORM stance

**Status:** Accepted
**Date:** 2026-06-17
**Deciders:** Hunt Integrative Solutions LLC

## Context

TALOS currently applies its Postgres schema through a sequence of raw SQL files:
`engine/schema.sql` (primary) → `engine/schema-additions.sql` (PM/scheduling layer) →
`engine/schema-p2.sql` (gate additions, if separate). There is no migration framework, no
version table, and no rollback support. On a workstation install, re-applying requires dropping
and re-creating the database.

The 2026-06-17 requirements interview established Alembic as the migration framework. This ADR
captures both the tool choice and the ORM stance, which are separable decisions.

## Decision

### 1. Migration tool: Alembic

Alembic is adopted as the migration versioning and application tool.

- The current `engine/schema.sql` + `schema-additions.sql` sequence becomes the **Alembic
  baseline migration** (`V0001_baseline.py`). It is marked as "already applied" on existing
  installations via `alembic stamp head`.
- All future schema changes are Alembic migration scripts. The `alembic upgrade head` command
  applies pending migrations in version order.
- Migrations are **reviewed before application** — autogenerate is used to produce candidates;
  a human reviews the diff before committing the migration script.
- The version table (`alembic_version`) is created in the `public` schema, outside the
  board-scoped RLS tables.

### 2. ORM stance: raw SQL for schema definition, SQLAlchemy Core for query-building (optional)

TALOS does **not** adopt a full SQLAlchemy ORM (declarative models with relationship mappings).

Reasons:
- The schema uses Postgres-specific features (RLS policies, triggers, JSONB operators, CTE
  views) that an ORM would abstract away poorly or require raw SQL overrides anyway.
- Alembic works with raw SQL migration scripts; it does not require SQLAlchemy models.
- An ORM adds a translation layer between the TALOS code and the schema, which is a source of
  subtle bugs (N+1, lazy loading, unexpected INSERTs on relationship traversal).

**What is permitted:** SQLAlchemy Core (`text()`, `select()`, `insert()`) may be used in TALOS
Python code for parameterized query construction where it prevents SQL injection. Raw SQL
strings with `psycopg3` named parameters are also acceptable. Neither forces an ORM migration.

### 3. Backup / migration safety rule

Before any `alembic upgrade` in a non-empty database: take a `pg_dump` snapshot. This is a
manual step for v1 workstation installs (documented in the install runbook). A helper script
`scripts/safe_migrate.sh` wraps `pg_dump` + `alembic upgrade head` + exits non-zero on
pg_dump failure.

## Consequences

- `engine/` gains an `alembic.ini` and a `migrations/` directory.
- `engine/schema.sql` remains as the human-readable reference; `migrations/V0001_baseline.py`
  is the machine-authoritative baseline.
- CI uses `alembic upgrade head` against the testcontainers Postgres instance (replacing the
  current raw `psql -f schema.sql` step).
- ADR-032 schema additions (`boards.manifest_hash`, `boards.manifest_json`) land in a named
  migration, not in `schema.sql` directly.
