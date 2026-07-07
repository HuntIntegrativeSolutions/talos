# Prompt: Docs-hygiene pass (post-P5)

> Paste everything below this line to the implementing session.

---

# Task: Documentation-hygiene pass — bring the record up to the build

You are working in /mnt/i/talos (the TALOS repo). Read CLAUDE.md first. P0–P5 and P7a are
complete (223 tests green). This task is purely documentation and one small doc-generator:
NO changes to talos/ runtime code, engine/migrations/, or any contract's binding content.
Every item comes from docs/vision-alignment-review-2026-07.md §5/§6 — read that file first;
it is the source of truth for scope.

## Items, in order (one commit per numbered group)

### 1. ADR index + numbering
- Regenerate docs/decisions/README.md: one table row per ADR file on disk (001–038+),
  title and status pulled from each file's header. Keep the existing PageRank footnote.
- Fix duplicate numbers: ADR-010-clarification-docker-sandbox and
  ADR-011-clarification-gate-outcomes → renumber as ADR-010a / ADR-011a (filename + title
  line), grep the whole repo for references to the old names and update them.
- ADR status sweep: any ADR whose decision is now implemented (016, 018–023, 029–038)
  gets a one-line "Implemented: <phase/commit>" note under its Status field if it lacks
  one. Do not change any Status that is genuinely still Proposed.

### 2. Three short new ADRs (follow the house format; ~1 page each)
- ADR-039 license-and-dependency-policy: MIT-compatible only in-repo; GPL/AGPL = patterns
  clean-room only, never vendored (CR-14 OpenLumara precedent); every vendored asset
  carries a license header (P7a marked/DOMPurify precedent); Neo4j/GDS licensing must be
  verified before the post-v1 Neo4j phase (RT-23). This records existing practice.
- ADR-040 state-snapshot-and-rollback: Postgres is the sole authoritative store; Chroma
  collections are derived and rebuildable from Postgres (rules table + deliverables);
  backup = Postgres PITR/pg_dump; Chroma is never backed up, only rebuilt (RT-28).
  Document the rebuild path as a required future `talos audit --rebuild-vectors` hook.
- ADR-041 capability-manifest-signing: manifests gain a detached signature verified at
  attach time and worker startup, alongside the existing content_hash (ADR-032). Record
  the OpenClaw ClawHub incident (≈1,467 malicious skills, signing now required there) as
  the motivating precedent. Decision + format choice (minisign/ed25519 detached sig,
  key held by the platform operator); implementation deferred to the hardening pass —
  status Accepted, implementation pending.

### 3. Red-team status ledger
- New docs/integration/rt-status.md: one line per RT-01…RT-30 and UA-1…UA-12 from
  docs/integration/03_redteam_review.md — status (closed / partial / open / obsolete),
  evidence link (ADR, commit, test file, or code path). Research each honestly from the
  repo; where the sweep in vision-alignment-review §5 already states a status, verify it.
  Add a maintenance rule at the top: "update this ledger in every phase's docs commit."

### 4. ROADMAP restructure + stale-claim sweep
- ROADMAP.md: move "Current status and next phase sequence" + "v1 Charter" to the top,
  directly after the header; the Phase-0 research sections and license audit move below
  a "## Historical — June 2026 research plan (complete)" fold. Content preserved, not
  rewritten.
- docs/integration/*: sweep for claims now stale (e.g. "contracts unwritten", "~85 NEXUS
  tools", stdio transport assumptions). Do NOT rewrite history — add a dated
  "> **Status note (2026-07):** …" blockquote under each stale claim pointing at the
  superseding artifact (ADR-038, capabilities/nexus/, etc.).

### 5. ARCHITECTURE.md: CISA mapping
- Add a short section mapping the Guardian doctrine onto the CISA/ASD "Careful Adoption
  of Agentic AI Services" five risk categories (privilege escalation → MCP boundary +
  manifest profiles; design/config failure → deterministic critics + frozen contracts;
  behavioral misalignment → human gate + non-waivable safety critics; structural
  brittleness → budget caps + heartbeat/reclaim + checkpointing; accountability gaps →
  append-only task_events + task_spans). Cite the guidance by name and date (Apr 2026).

### 6. DOX AGENTS.md tree (the one code-adjacent item)
- Per docs/upstream/dox-framework-notes.md: hand-author a first AGENTS.md for each major
  component dir (talos/, talos/critics/, talos/graph/, talos/llm_providers/, talos/memory/,
  talos/auth/, engine/, web/gate/, capabilities/nexus/) — terse: what the module is, its
  binding ADRs/contracts, invariants a child must not weaken (parent→child strict rule).
- scripts/generate_dox_tree.py: assembles/refreshes the root-level tree listing from the
  per-dir files (one-way render, read-only re: sources, atomic write). Plus a CI-runnable
  test asserting every listed dir has an AGENTS.md and the tree is current.
  This is the only new code; keep it under ~100 lines.

## Out of scope
- Implementing ADR-041 signing, `talos audit`, ADR-032/033 hardening (next pass)
- Any change to talos/ runtime, migrations, tests (except the DOX freshness test)
- Rewriting BLUEPRINT.md (living doc, owner-edited)

## Done when
- Every §5 row in vision-alignment-review-2026-07.md is closed or explicitly deferred
  with a reason; full test suite still green (only the DOX test added)
- Six commits, style: docs(hygiene): ... / feat(dox): ... for item 6
