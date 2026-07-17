# Research Prompt — PLC Connectivity: pylogix, PLC5, and What to Build

> **Historical note:** this prompt predates the `platform/` → `talos/` rename; current code lives
> in `talos/`. Retained as written for the historical record.

You are doing a deep technical research dive for TALOS, a pre-alpha multi-agent industrial
project-execution platform at `/mnt/i/talos/`. TALOS is NOT a coding assistant. It
orchestrates agents for operations work including PLC analysis, maintenance, and project
management.

**Architecture context before you read anything else:**

TALOS talks to domain capabilities via MCP. NEXUS is TALOS's first domain capability — it
lives behind the MCP boundary and handles PLC analysis. TALOS itself never talks directly
to a PLC. The question this research answers is: **what PLC connectivity library should
NEXUS use, and should we build a better one?**

This research session produces one output file and one architecture decision document.
Do not modify any other files.

---

## Context — read these files silently first

- `/mnt/i/talos/docs/decisions/ADR-001.md` — TALOS is a platform; NEXUS is a capability
  behind MCP (not merged into TALOS)
- `/mnt/i/talos/docs/decisions/ADR-004.md` — capability tool profiles (read = read-only
  from live systems; write = offline artifact or sim only; no live writes in any profile)
- `/mnt/i/talos/docs/integration/04_build_sequence.md` — P6 (sim-execute) is the phase
  that builds the write-path capability; PLC read connectivity is relevant earlier

After reading, you will know: NEXUS is responsible for PLC data; TALOS receives structured
results from NEXUS via MCP. Any PLC library lives inside NEXUS, not in TALOS's `platform/`
directory. The `read` profile in ADR-004 covers live PLC reads; the `write` profile covers
offline artifacts and sim-only writes (never live device writes).

---

## Sources to fetch and study

**pylogix:**
1. `https://github.com/dmroeder/pylogix` — full README and code structure
2. `https://industrialmonitordirect.com/blogs/knowledgebase/python-library-selection-for-allen-bradley-compactlogix-communication-on-armlinux-platforms`
   — library comparison article
3. `https://industrialmonitordirect.com/blogs/knowledgebase/using-pylogix-for-offline-plc-data-parsing-alternatives-and-workarounds`
   — pylogix limitations and alternatives

**pycomm3:**
4. `https://pycomm3.readthedocs.io/en/latest/` — pycomm3 documentation
5. `https://github.com/ottowayi/pycomm3` — pycomm3 repository

**PLC5 / DF1 protocol:**
6. `https://github.com/reyanvaldes/pydf1` — the active Python 3 fork of pydf1
   (original pydf1 is unmaintained)
7. Search for: "Allen Bradley PLC5 DF1 protocol Python library 2025 2026"
8. Search for: "Allen Bradley SLC MicroLogix Python PCCC protocol"

**Broader landscape:**
9. Search for: "Python EtherNet/IP CIP library async asyncio 2025 2026"
10. Search for: "cpppo pycomm cppLogix industrial Python PLC communication comparison"
11. Search for: "open62541 Python OPC-UA Allen Bradley PLC alternative"
12. Search for: "pylogix limitations async thread-safety production use"

---

## What to extract

### Part 1 — Landscape analysis

**pylogix:**
- Protocol: EtherNet/IP + CIP (Common Industrial Protocol)
- Supported families: ControlLogix, CompactLogix, Micro8xx (with flag)
- Explicitly unsupported: PLC5, SLC, MicroLogix (different protocol)
- API: Read(tag), Write(tag, value), tag browsing, batch reads
- Dependencies: zero external deps, Python 2/3/MicroPython compatible
- Thread safety: is it thread-safe? Can it handle concurrent reads from multiple workers?
- Async: is there asyncio support?
- Error handling: what does a connection failure look like? Timeout? Retry?
- Limitations: online only (no offline L5X parsing), no PLC5/SLC

**pycomm3:**
- Protocol: EtherNet/IP + CIP (same as pylogix)
- Supported families: ControlLogix, CompactLogix, Micro8xx; SLC/MicroLogix code exists
  but not yet ported — verify current status
- API surface: how does it differ from pylogix?
- Type handling: is it better or worse at complex UDTs (User-Defined Types)?
- Async support: any asyncio?
- Error handling: more or less verbose than pylogix?
- Last release date: is it actively maintained?

**pydf1 / legacy PLC5:**
- Protocol: DF1 (Data Highway Plus serial and Ethernet variants)
- Supported families: PLC-5, SLC 500, MicroLogix (these are the DF1 devices)
- What does the reyanvaldes fork add over the original?
- What is the API surface? (read/write by address like N7:0, F8:0?)
- What are the limitations? (addressed-based only, no tag names, no UDTs)
- Is it production-quality or proof-of-concept?

**Gap analysis:**
- Is there any single Python library that covers BOTH the EtherNet/IP Logix family AND
  the DF1 PLC5/SLC family?
- What is missing from all current libraries that a production NEXUS deployment would need?

### Part 2 — "What would a better library look like?"

The builder wants to know: if we were going to build a better pylogix and a new PLC5 library,
what would they look like? Research and reason through:

**For a better Logix library (ControlLogix/CompactLogix):**
- Async/await native (asyncio) — does pylogix or pycomm3 support this? If not, what
  would it take to add?
- Thread-safe connection pooling — can multiple NEXUS workers share one EtherNet/IP session?
- Richer type support — full UDT/AOI (Add-On Instruction) structure reading
- Offline mode — reading tag definitions from an L5X export without a live PLC (this is
  explicitly what pylogix cannot do; pycomm3 cannot either)
- MCP-ready design — could the library be designed from the start as an MCP server so
  NEXUS wraps it with zero glue code?
- Stub/simulation mode — testable without live hardware (the Logix Echo SDK from Rockwell
  fills this today; does the library need a built-in stub mode?)
- Error model — structured errors (connection refused, tag not found, type mismatch, etc.)
  not just status strings

**For a new PLC5 / SLC / MicroLogix library:**
- Protocol: DF1 full-duplex (Ethernet) and half-duplex (serial)
- Address format: file-based (N7:0, F8:1, B3/5, etc.) — no tag names
- PCCC (Programmable Controller Communication Commands) command set: which commands matter?
  (read data file, write data file, get diagnostic status)
- What does pydf1 currently miss that production use requires?
- Is OPC-UA a better path than DF1 for PLC5 (if the site has an OPC-UA server)?

**Scope decision:**
- Should these be a single repo with two drivers (Logix + PLC5), or two separate libraries?
- Should they live inside NEXUS, or as standalone packages that NEXUS depends on?
- What license fits? (MIT to match TALOS's upstream chain? Apache 2.0 like Omnigent?)
- Is there an existing library that is close enough that contributing to it upstream is
  better than building fresh?

### Part 3 — NEXUS architecture implications

Given what you find, reason through:

- NEXUS currently has MCP tools that read PLC data. What library do those tools use today?
  (Check if there is a NEXUS directory or config at `/mnt/i/` — do NOT assume its structure,
  just look for what exists)
- If we build a new Logix library and a new PLC5 library, how do they attach to NEXUS?
  (New MCP tools in NEXUS? A new internal adapter layer?)
- ADR-004 says read-profile tools can read from live PLCs. What connection management does
  NEXUS need? (connection pool per PLC IP? session per MCP call? persistent connection?)
- The Logix Echo SDK (Rockwell's emulator) is already mentioned in TALOS context. Does the
  new Logix library need to detect and route to Echo vs. live hardware transparently?

---

## What to produce

**File 1:** `/mnt/i/talos/docs/upstream/plc-connectivity-notes.md`

```
# PLC Connectivity — Research Notes

## Landscape overview
[one paragraph summary of what exists and what the gaps are]

## pylogix — technical details
[protocol, supported families, API, thread safety, async, limitations]

## pycomm3 — technical details
[same structure as pylogix; explicit comparison at the end]

## pydf1 (reyanvaldes fork) — technical details
[DF1 protocol basics, address format, API, what the fork adds, production readiness]

## Head-to-head comparison
[table: feature × library — pylogix / pycomm3 / pydf1 / any others found]

## Gap analysis
[what NO existing library provides that production NEXUS needs]

## Key TALOS findings
[bulleted — what to adopt, what to adapt, what to build]

## What a better Logix library looks like
[async, connection pooling, offline mode, MCP-ready, stub mode, error model]

## What a new PLC5/SLC/DF1 library looks like
[PCCC commands, address format, DF1 full/half duplex, what pydf1 misses, OPC-UA path]

## Scope and ownership recommendation
[single repo or two; inside NEXUS or standalone; license; upstream-contribution vs. build]

## NEXUS architecture implications
[how the new libraries attach to NEXUS; connection management; Echo SDK routing]

## Open questions for the builder
[questions research couldn't settle — especially: which PLC families are on actual client
 sites today?]

## Build-phase impact
[P6 (sim-execute) is where write-path lands. Read-path connectivity: which phase?
 Does a new PLC library need its own pre-P6 phase?]
```

**File 2:** `/mnt/i/talos/docs/decisions/ADR-024-plc-connectivity.md`

Draft an ADR for the PLC connectivity decision. Follow the format of existing ADRs in
`docs/decisions/`. The ADR must decide:
1. Which library (or combination) NEXUS uses for Logix-family PLCs
2. Whether to build a new Logix library or adopt/fork an existing one
3. Whether to build a PLC5/SLC DF1 library or adopt/fork an existing one
4. Where these libraries live (inside NEXUS, separate repo, TALOS monorepo)
5. The licensing decision

Mark the ADR status as `draft` since the builder has not yet confirmed it.

Write both files. Do not modify any other file.
