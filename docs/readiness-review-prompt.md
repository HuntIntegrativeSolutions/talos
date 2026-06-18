# TALOS — First-Coding-Task Readiness Review Prompt

Paste this into a **fresh** Claude Code session. Working directory: `/mnt/i/talos/`

Its job is to (1) verify TALOS is ready to begin its first **coding** task, (2) confirm the
recommended task to start first, and (3) emit a second, detailed implementation prompt for that
task — written to a file — that a later session can execute one task at a time.

---

You are reviewing **TALOS**, a multi-agent project-execution platform for industrial and automation
work. The design phase (P0–P3 core) is complete and tested. We are about to begin the **v1 build**,
and we work **one task at a time**. Your job in this session is **readiness review and prompt
authorship — not implementation.** Do not write or edit any source code, schema, or ADR in this
session. The only file you create is the implementation prompt described in Step 4.

**Do not start by summarizing what TALOS is.** Read the source of record, assess readiness, then act.

## Step 1 — Read the source of record (in this order)

1. `CLAUDE.md` — project status, Guardian doctrine, the two hard boundaries, ADR index.
2. `ROADMAP.md` — especially **"v1 Charter"** and **"Current status and next phase sequence."**
   Note the line: *"First recommended task: JWT local auth server (RT-01)."*
3. `BLUEPRINT.md` — authoritative design doc (skim for auth, gate, and the human-approval invariant;
   BLUEPRINT wins on any conflict).
4. `docs/integration/03_redteam_review.md` — read the **RT-01** entries in full. RT-01 is the
   **BLOCKER** ("forged approval"): the gate-outcome write trusts a self-asserted `actor` string;
   nothing binds `approved_by` to an authenticated human identity.
5. `docs/integration/04_build_sequence.md` — the P0→P8 build order and the **§ First-phase detail**
   for P1, including the RT-01 verification criteria.
6. The current gate write path in code — at minimum `talos/api.py` (the board API and gate
   endpoint) and the gate/approval columns in `engine/schema.sql`. Establish **how `approved_by`
   is set today** and where an authenticated identity would have to be injected.
7. ADRs that govern auth and the gate: **ADR-011** (five gate outcomes), and scan
   `docs/decisions/` for any auth ADR. **Note explicitly:** the v1 Charter cites "ADR-028" for
   auth, but ADR-028 is the *widget-sandbox* contract — there is **no dedicated auth ADR yet**.
   Flag whether one should be written as part of, or before, the first coding task.

Use the `Explore` agent for breadth if helpful, but read `api.py`, `schema.sql`, the RT-01
red-team entries, and the build-sequence P1 detail yourself.

## Step 2 — Assess readiness for the first coding task

Produce a short **readiness verdict** (a table is fine). For each item state READY / NOT-READY /
N-A with one line of evidence (cite `file:line`):

- **Schema** — do the gate/approval columns and RLS needed by an auth-bound gate write exist?
- **Gate write path** — is there a single, identifiable place where `approved_by` is set that auth
  must protect? Is the non-human-caller rejection point clear?
- **Contracts** — does `docs/contracts/board-api.md` already specify (or need to specify) the
  authn model for the gate write? (Red-team RT-16 flags this contract gap.)
- **ADR coverage** — is the auth decision recorded anywhere, or is an ADR a prerequisite?
- **Tests/tooling** — test harness in place (testcontainers Postgres), and would the first task
  need new fixtures (e.g. a JWT/auth fixture)?
- **Dependencies** — is anything *upstream* of RT-01 unfinished that would block it?

End Step 2 with a one-line GO / NO-GO. If NO-GO, list the blocking gaps and **stop** — do not
author an implementation prompt for a task that cannot start.

## Step 3 — Confirm the recommended first task

The roadmap recommends **RT-01 — JWT local auth server** as the first coding task, because it is
the P1 spine BLOCKER: no live client data should be handled until the human-approval invariant has
a structural enforcer. **Independently confirm or challenge this.** Recommend RT-01 unless your
review surfaces a concrete reason a different task must come first; if so, name that task, cite the
evidence, and recommend it instead. State your single recommended task in one sentence with its
rationale.

Respect the v1 Charter constraints when scoping it: JWT, **local** auth server (username/password),
on-prem / air-gapped by default, multi-provider-LLM-agnostic. `approved_by` must derive from the
**authenticated session identity, never from a request-body field.**

## Step 4 — Author the implementation prompt (the deliverable)

Write a second, self-contained prompt that a **later** Claude Code session will paste in to execute
**only** the recommended task. Save it to:

```
docs/<task-id>-implementation-prompt.md      e.g. docs/rt-01-jwt-auth-implementation-prompt.md
```

Match the house style of `docs/next-steps-prompt.md` and `docs/p1-spine-prompt.md` (read both
first). The implementation prompt must contain:

1. **Header** — paste-target, working directory, and a one-line "what this task is and why it's
   first" (the RT-01 BLOCKER framing).
2. **Read-first list** — the exact files the implementing session must read before writing,
   with line anchors where you found the relevant code (the gate write path, schema columns,
   board-api contract section, ADR-011, red-team RT-01).
3. **Scope — in and out.** Explicitly in: bind `approved_by` to an authenticated human session;
   reject `submitGateOutcome` from non-human/service callers. Explicitly out: anything beyond the
   minimum needed to close RT-01 (no cockpit UI work, no multi-tenant SSO, no LLM changes).
4. **Decisions to settle before coding** — list every open choice the implementer must resolve
   *and how to resolve it* (e.g. whether to write a new auth ADR first; token storage; password
   hashing choice; where the auth module lives — propose a path and have them confirm with the
   user). Carry forward the ADR-028-is-not-auth discrepancy you found.
5. **Definition of Done** — concrete, testable, derived from the build-sequence P1 RT-01
   verification: `approved_by` equals the authenticated session identity (not any request field);
   a non-human / service-token `submitGateOutcome` is **rejected**; tests prove both; existing
   56 tests still pass (`TALOS_NEXUS_STUB=1 .venv/bin/python -m pytest talos/ -v`).
6. **Test plan** — the specific new tests to add and where, plus the command to run them.
7. **Rules** — one task only; read before editing; match existing style; don't refactor unrelated
   code; update `ROADMAP.md`/`CLAUDE.md` status and any relevant ADR when done; report a one-line
   status and any new open questions at the end.

Derive every specific (file paths, column names, function names, line anchors) from what you
actually read — do not invent. Where the implementer must make a judgment call, say so explicitly
rather than guessing.

## Step 5 — Report back

In this session's final message, give me: (a) the GO/NO-GO verdict, (b) the one recommended task
and why, (c) the path to the implementation prompt you wrote, and (d) any open questions I should
decide before the implementing session starts (especially: do we write an auth ADR first?).

## Rules for this session

- **Review and authorship only.** No source/schema/ADR edits. The only new file is the
  implementation prompt in `docs/`.
- Read the real files before asserting readiness; cite `file:line` for each readiness claim.
- If the review turns up a reason RT-01 is *not* the right first task, say so plainly — do not
  rubber-stamp the roadmap.
- Keep the implementation prompt scoped to **one** task. We build one task at a time.

Start with Step 1.
