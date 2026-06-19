# Snort / OT-Network-Security Monitoring — Capability Candidate Notes

> Research date: 2026-06-18
> Source: `/mnt/i/HIS-Obsidian-Vault/20-Engineering/OT-Security/Snort-IDS-IPS-HIS-Analysis.md`
> (Snort 3 / Snort++, GPLv2; native `cip`/`modbus`/`dnp3`/`s7commplus` inspectors; SnortML is
> IT-trained, *not* OT-trained)
> Purpose: Record OT-network-security monitoring as a **post-v1 TALOS capability candidate**.
> Status: Captured, not scheduled. Patterns/disposition only — zero code.

---

## Disposition

Snort-based OT network monitoring is a **strong doctrinal fit** for TALOS but is **post-v1**. It is
recorded here so the architectural fit is not lost; it does **not** change the v1 build sequence
(v1 = PLC documentation, air-gapped, zero live writes).

**Decision (2026-06-18):** This was originally researched for a separate "Hermes" system. It is
instead folded into the **TALOS custom harness** — the propose-only triage role belongs to TALOS's
own **Gateway** layer (`CLAUDE.md:82`), not a second orchestrator. One system, not two. (Note: the
repo's `hermes-notes.md` is the unrelated NousResearch board-schema source, not this "Hermes.")

## Why it fits the Guardian doctrine

| Snort/OT reality | TALOS invariant it mirrors |
| :--- | :--- |
| Always passive **IDS**, never inline **IPS** on Level 0–3 (blocking deterministic I/O can trip safety) | "Nothing reaches a live processor." Monitoring observes; it never acts on the network. |
| A "suggested PLC lockout" is executed **by a human**, never by the tool | `ADR-004`: live operations are "not in any agent's reach at all" — a human acts from the proposal. |
| Tiered triage: CIP unauthorized write → HIGH, Modbus scan → MEDIUM, IT noise → LOW | Existing severity-gating (HIGH auto-stage / MEDIUM shortened gate / LOW log). |

## How it would attach (when picked up)

Two pieces, both already-modeled TALOS concepts:

1. **A read-profile capability pack behind MCP** (e.g. `his-security`). Alert-enrichment tools are
   textbook `read` tools — `profile:"read", safety:false`, no `write_kind`, no `sim_target`:
   - `enrich_alert(dst_ip) → {tags, zone, criticality}`
   - `zone_lookup(...)`
   They validate cleanly against the frozen contract (`docs/contracts/capability-manifest.md`,
   `talos/validators/capability_manifest.py`) and attach exactly like NEXUS. An unprofiled
   capability is treated as `write` (fail-closed), so a read-only monitor is the lowest-risk class.

2. **A P8-Gateway propose-only loop.** Poll Snort syslog → enrich via the capability → propose
   tasks to a board (HIGH/MEDIUM/LOW). The Gateway "may notify/propose, never approve". It reuses
   existing notification infra: the gate-escalation webhook and `notify_subs` table (`ADR-022`);
   multi-channel notify is itself deferred to P7.

## Why post-v1 (the real caveat)

This is a **different deployment posture** than v1. OT monitoring is a *connected, live-network*
deployment (SPAN/TAP off a cell switch), whereas the v1 charter is on-prem, **air-gapped by
default**, doc-generation. Standing this up means deciding how a Gateway loop reaches a live
syslog/sensor feed **without widening TALOS's air-gap** — a posture decision, not just code.

Much of the source note's "Concrete Next Steps" (Snort-in-Docker lab, writing OT rules, IEC 62443
deliverables) is standalone HIS/lab work that is **not** TALOS engineering and can proceed
independently of this capability.

## Open questions for pickup

- **Capability boundary:** does enrichment call NEXUS over MCP (`dst_ip → PLC tags`), or hold its
  own asset map? (MCP-to-MCP composition vs. self-contained pack.)
- **Air-gap posture:** where the sensor sits and how the Gateway ingests syslog without breaking
  the air-gapped default.
- **Alert-storm handling:** rate-limiting (3-axis budget) and dedup/clustering — in the Gateway
  loop, or a pre-Gateway filter?
- **Blind spots to document for clients:** SnortML is IT-trained (don't sell as "OT AI"); encrypted
  CIP Security traffic is uninspectable; Snort detects but does not segment.
