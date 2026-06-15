# Option A — Public Repo Hygiene Implementation Prompt

**Context:** TALOS is a pre-alpha multi-agent industrial project-execution platform at
`/mnt/i/talos/`. P0 (schema + contracts + validators), P1 (single-worker LangGraph spine), and
P2 (critics registry + five-outcome gate) are all complete. 27 tests pass. The repo is already
live at `github.com/HuntIntegrativeSolutions/talos.git`. Before beginning P3 (full distributed
dispatcher), three public-repo hygiene items must land.

**Do not modify any Python source files or SQL files.** This prompt covers documentation and CI
only. All code changes are deferred to P3.

**After completing all three items, run the test suite to confirm nothing broke:**
```bash
cd /mnt/i/talos && TALOS_NEXUS_STUB=1 .venv/bin/python -m pytest platform/ -v
```
All 27 tests must still pass.

---

## Deliverable 1 — Update `README.md`

The current README says "Not yet runnable" and its repo layout section lists `critics/`,
`gateway/`, `memory/` as live code directories — they're placeholders. The real implemented
code is in `platform/`. Fix both.

**Keep unchanged:**
- The HTML header block (`<p align="center">` image + title + tagline)
- The opening two paragraphs ("TALOS is a multi-agent work-board…")
- The Guardian doctrine blockquote
- The "Why" section
- The "What it blends (all MIT)" table
- The "Architecture at a glance" section
- The "License" section

**Replace the "Status" section** (currently lines 65–67) with:

```markdown
## Status

**Pre-alpha · P0 + P1 + P2 complete.**

The schema, contracts, critics registry, and five-outcome human-review gate are implemented
and tested. The full distributed dispatcher (P3), memory federation (P4), sim-execute (P6),
web cockpit (P7), and gateway (P8) have not been built yet.

What is runnable today:
- `platform/validators/` — capability-manifest validator (P0)
- `platform/critics/` — deterministic gate critics and registry (P2)
- `platform/graph/spine.py` — 4-node LangGraph spine with five-outcome gate (P1/P2)
- `platform/worker.py` — single-worker claim loop (P1, no dispatcher yet)
- `platform/api.py` — FastAPI board API with full gate endpoint (P1/P2)

## Quick start

Requires Python 3.11+ and Docker (tests spin up Postgres 16 via testcontainers).

```bash
git clone git@github.com:HuntIntegrativeSolutions/talos.git
cd talos
python -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"
TALOS_NEXUS_STUB=1 python -m pytest platform/ -v
```
```

**Replace the "Repo layout" section** (currently lines 69–78) with:

```markdown
## Repo layout

```
platform/      Implemented Python modules
  critics/     Deterministic gate critics and registry (P2)
  graph/       LangGraph spine with five-outcome gate (P1/P2)
  validators/  Capability-manifest validator (P0)
  tests/       27 integration + unit tests (P0–P2)
  worker.py    Single-worker claim loop (P1)
  api.py       FastAPI board API (P1/P2)
engine/        Postgres schema (schema.sql + schema-additions.sql + schema-p2.sql)
web/           Placeholder — Space Agent cockpit (not built)
gateway/       Placeholder — sandboxed proactive loops (not built)
memory/        Placeholder — polyglot memory adapters (not built)
docs/
  ARCHITECTURE.md        High-level system overview
  decisions/             ADR-001 through ADR-017 — binding design decisions
  contracts/             Four frozen seam contracts
  integration/           Reconciliation documents (integration map, build sequence, red-team)
  upstream/              Notes from upstream harnesses studied during design
BLUEPRINT.md   Authoritative living design document (v0.6)
ROADMAP.md     Phase-ordered research and documentation roadmap
assets/        Brand assets (emblem, etc.)
```
```

---

## Deliverable 2 — Add `.github/workflows/ci.yml`

Create the directory and file. This workflow runs on every push and pull request.

**File:** `.github/workflows/ci.yml`

```yaml
name: CI

on:
  push:
    branches: ["**"]
  pull_request:
    branches: ["**"]

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install -e ".[test]"

      - name: Run tests
        env:
          TALOS_NEXUS_STUB: "1"
        run: python -m pytest platform/ -v
```

**Why this is sufficient:**
- `ubuntu-latest` runners include Docker; testcontainers uses the host Docker daemon to spin up
  Postgres 16 — no `services:` block needed.
- `TALOS_NEXUS_STUB=1` is required because `read_node` in the spine raises `NotImplementedError`
  without it.
- `pip install -e ".[test]"` installs all test dependencies from `pyproject.toml`'s
  `[project.optional-dependencies]` → `test` list (pytest, testcontainers[postgres], httpx,
  fastapi, langgraph).
- No secrets or external services required for the P0–P2 test suite.

---

## Deliverable 3 — Add `.pytest_cache/` to `.gitignore`

The current `.gitignore` is missing `.pytest_cache/`. Add it under the `# Python` section.

**File:** `.gitignore`

Current `# Python` block:
```
# Python
.venv/
__pycache__/
*.py[cod]
*.egg-info/
```

Updated `# Python` block:
```
# Python
.venv/
__pycache__/
*.py[cod]
*.egg-info/
.pytest_cache/
```

---

## Deliverable 4 — Update ROADMAP.md "Immediate next actions"

The "Immediate next actions" section (currently at the bottom) still lists "git init" and
"initial commit" — actions completed long ago. Replace the entire section with:

```markdown
## Immediate next actions

P0, P1, and P2 are complete. The core gate is implemented and tested (27 tests passing).
The repo is live at `github.com/HuntIntegrativeSolutions/talos.git`.

**Next engineering phase:** P3 — Full Distributed Dispatcher.
See `docs/integration/04_build_sequence.md` for the full dependency-ordered build sequence.

P3 deliverables (planned across sub-phases):
- **P3a** — PostgresSaver + reclaim reconciliation (replace MemorySaver; RT-20)
- **P3b** — DAG-priority dispatcher, heartbeat, multi-writer reducers (RT-04, RT-10, RT-21)
- **P3c** — Docker sandbox (`network:none`, `readOnlyRoot` per ADR-010; RT-27, RT-28)
- **P3d** — ADR-016 PM hooks + severity-gated escalator + snapshot/rollback

Before P3 begins: resolve open customizability questions (model selection, memory backend
flexibility) via an interview session — results become new ADRs.
```

---

## Verification checklist

After all four deliverables:

- [ ] `README.md` says "Pre-alpha · P0 + P1 + P2 complete" (not "Not yet runnable")
- [ ] `README.md` layout section shows `platform/` as the live code directory
- [ ] `README.md` has a "Quick start" section with the clone + test commands
- [ ] `.github/workflows/ci.yml` exists and is syntactically valid YAML
- [ ] `.gitignore` includes `.pytest_cache/`
- [ ] `ROADMAP.md` "Immediate next actions" no longer references "git init" or "initial commit"
- [ ] All 27 tests still pass: `TALOS_NEXUS_STUB=1 .venv/bin/python -m pytest platform/ -v`
