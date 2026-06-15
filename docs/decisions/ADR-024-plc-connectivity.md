# ADR-024: PLC connectivity library — Logix and PLC5/SLC adoption decision

**Status:** Draft (builder has not confirmed)
**Date:** 2026-06-14
**Deciders:** Hunt Integrative Solutions LLC

---

## Context

TALOS orchestrates agents via MCP. NEXUS is TALOS's first domain capability, behind the MCP
boundary (ADR-001). TALOS itself never communicates with a PLC. All PLC connectivity lives
inside NEXUS.

NEXUS requires at minimum one Python library to execute live reads against ControlLogix /
CompactLogix controllers (the `read` profile in ADR-004) and eventually sim-only writes
against an emulated target (P6, `sim_only` write profile). A secondary requirement exists
for legacy PLC connectivity (PLC-5, SLC 500, MicroLogix), the scope and timing of which
is not yet confirmed by site requirements.

The emulation research (`docs/upstream/rockwell-emulation-etherNetIP-notes.md`) confirmed:
- pylogix reads atomic tags (BOOL, DINT, REAL, STRING, TIMER) against Emulate 5000 via the
  FT Linx EtherNet/IP bridge.
- UDT access is blocked at the protocol layer (FT Linx returns privilege violation for all
  P_* instances). This is a bridge limitation, not a pylogix limitation.
- No Python library provides programmatic download to an emulated controller without the
  Logix Echo SDK (licensed).

No NEXUS source code is present at `/mnt/i/`. The following decisions are based on confirmed
research findings and inference from ADR-001 / ADR-004 intent.

---

## Decision

**Five binding decisions:**

### Decision 1: Logix-family library adoption

**Adopt pylogix for the initial read path (P1–P3). Plan a fork of pycomm3 inside NEXUS,
developed as P6 preparation, to deliver the production NEXUS Logix driver.**

pylogix is the proven baseline: confirmed working in emulation, zero dependencies, MIT
license, active maintenance. It is sufficient for the P1 spine read path and early NEXUS
read tools.

pycomm3 is adopted as the **upstream for a fork** (`nexus-logix`), not consumed directly
as a dependency. It has the CIP encoding, UDT dict support, and packet bundling that
pylogix lacks. The fork replaces pycomm3's synchronous transport with asyncio, adds
structured exceptions, adds L5X offline mode, and adds a connection pool. The fork is
justified by pycomm3's maintenance-only status, its Python 3.10 ceiling, and the need for
asyncio that no existing library provides.

### Decision 2: Whether to build a new Logix library or adopt/fork existing

**Fork pycomm3. Do not build from scratch.**

The CIP encoding layer (request/response framing, CIP path construction, fragmentation
handling, service codes) is the hard and error-prone part of an EtherNet/IP library.
pycomm3 has 53 releases of field-tested CIP encoding. A clean-room reimplementation
introduces regression risk with no benefit. The fork strategy:
- Preserve pycomm3's CIP encoding, UDT dict mode, and packet bundling logic
- Replace the synchronous `socket`-based transport with `asyncio.open_connection()`
- Add `LogixPool` (asyncio.Queue of open transport handles, per PLC IP)
- Add typed exception hierarchy (ConnectionError, TagNotFoundError, TypeMismatchError,
  PrivilegeError, ServiceError)
- Add `LogixOfflineDriver(l5x_path)` that parses L5X XML for tag types and defaults
- Add `MockLogixDriver(l5x_path)` for unit-testable stub mode
- Declare MIT license (consistent with pylogix/pycomm3 upstreams)

Fork maintenance: Hunt Integrative Solutions maintains the fork as an internal NEXUS
dependency. If the fork accumulates sufficient quality to be worth upstream contribution,
submit a PR to pycomm3 at that point.

### Decision 3: Whether to build a PLC5/SLC DF1 library or adopt/fork existing

**Do not build a native Python DF1 library at this time. Evaluate libplctag's DH+ bridge
path when a confirmed legacy-PLC site requirement exists.**

No production-quality Python DF1 library exists:
- pydf1 (reyanvaldes): acknowledged incomplete, 2 stars, unknown missing commands.
- leicht/Df1: unmaintained (2003-era codebase).
- libpccc: Visual Basic, not Python.

The most practical path to PLC-5 / SLC access in a modern plant is the DH+ bridge:
a 1756-DHRIO module in a Logix chassis routes EtherNet/IP CIP requests to the DH+ network.
libplctag (C, actively maintained, v2.7.0 May 2026) supports this path via CIP routing
segments. A thin Python wrapper around libplctag handles PLC-5 / SLC without implementing
PCCC from scratch.

If a site has Ethernet-connected SLC or MicroLogix without any Logix chassis, pycomm3's
SLCDriver (which handles PCCC-over-EtherNet/IP encapsulation) is the fallback, with the
caveat that it is "legacy, limited development." This is acceptable for read-only tools.

**Trigger for building native DF1:** A confirmed NEXUS deployment at a site with PLC-5 or
SLC-5/02 (serial DF1, no DH+ bridge, no OPC-UA server). This trigger has not been met.

### Decision 4: Where these libraries live

**Inside NEXUS as NEXUS dependencies. Outside the TALOS monorepo (`/mnt/i/talos/platform/`).
Two separate packages.**

- `nexus-logix` — the pycomm3 fork for Logix EtherNet/IP
- `nexus-legacy` — libplctag wrapper for DH+ bridge (when triggered)

The TALOS monorepo contains no PLC driver code. ADR-001 makes the MCP boundary a security
boundary: a fully compromised TALOS orchestrator cannot reach a live PLC because the PLC
libraries are on the NEXUS side of that boundary. Putting them in `platform/` would erase
this guarantee.

Each library is exposed to TALOS via NEXUS MCP tools declared in NEXUS's capability
manifest. Examples:
```json
{ "name": "logix_browse_tags", "profile": "read", "safety": false }
{ "name": "logix_read_tag",    "profile": "read", "safety": false }
{ "name": "logix_write_sim",   "profile": "write", "write_kind": "sim_only",
  "sim_target": { "kind": "logix_echo", "verify_critic": "echo_sdk_verify" } }
```

There is no live-write tool declaration. ADR-004 prohibits it at the capability manifest
level; NEXUS's validator enforces it deterministically.

### Decision 5: License

**MIT for `nexus-logix`.** Consistent with pylogix and pycomm3 upstream licenses. Minimal
friction for commercial use.

**MPL 2.0 compliance required for any libplctag wrapper** (`nexus-legacy`). libplctag is
dual-licensed MPL 2.0 / LGPL 2+. A thin wrapper that links to the compiled C library must
comply. MPL 2.0 requires open-sourcing the wrapper file but not NEXUS as a whole; this is
acceptable. If proprietary is required, substitute a pure-Python PCCC implementation (which
would then not be derivative of libplctag and can be MIT).

---

## Options considered

### Option A — Adopt pylogix directly (no fork)
Simplest. Confirmed working. But: synchronous only, no async, no UDT dict support, no
connection pool, no structured exceptions, no offline mode. Adequate for P1 but insufficient
for production NEXUS serving concurrent MCP tool calls.

### Option B — Adopt pycomm3 directly (no fork)
Has UDT support and packet bundling. But: Python 3.10 ceiling, maintenance-only, synchronous,
no async, no connection pool, latency issues reported. Same NEXUS production concerns as A.

### Option C — Fork pycomm3 (chosen for Logix)
Preserves proven CIP encoding. Adds asyncio, pooling, structured errors, offline mode.
Fork maintenance burden is real but bounded — CIP encoding is stable; the fork replaces only
the transport and adds extension modules.

### Option D — Build from scratch (asyncio-native CIP)
Clean design but 6–12 months of encoding work before NEXUS has a testable driver. The risk
of subtle CIP framing bugs in unproven code outweighs the clean-slate benefit.

### Option E — Use libplctag for Logix too (uniform C wrapper)
libplctag covers ControlLogix, SLC 500, and PLC-5. Eliminates the two-library split.
But: MPL 2.0 / LGPL 2+ license for the Logix path (non-trivial for commercial use), C
dependency (requires compiled binary on every deployment target including ARM Linux),
minimal Python binding (not a first-class Python API), no asyncio in the C layer.
Rejected for Logix; retained as the evaluation candidate for legacy DH+ bridge.

### Option F — OPC-UA first (python-opcua / asyncua)
Site-agnostic if the site has KEPServerEX or similar. asyncua supports asyncio. But:
adds OPC-UA server infrastructure dependency, typically adds one extra latency tier,
does not help sites without an existing OPC server (brownfield common). Not a replacement
for direct EtherNet/IP in NEXUS's initial deployment targets.

### Option G — Native Python DF1 (build or fork pydf1)
Pydf1 is incomplete; building full PCCC from scratch is multi-month scope for a library
needed at zero confirmed sites. Rejected in favor of DH+ bridge via libplctag when the need
is confirmed.

---

## Trade-off analysis

| Criterion | pylogix (P1 stop) | pycomm3 fork (chosen) | From scratch |
|---|---|---|---|
| Time to working read path | Immediate | 4–6 weeks fork work | 4–6 months |
| Async / concurrent MCP calls | Executor workaround | Native asyncio | Native asyncio |
| UDT dict access | Blocked (bridge) | Yes (from pycomm3) | Yes (if implemented) |
| Offline L5X mode | No | Yes (add to fork) | Yes (if implemented) |
| Structured exceptions | No | Yes (add to fork) | Yes (if implemented) |
| Maintenance burden | None (adopt) | Medium (own fork) | High (own codebase) |
| CIP encoding risk | Low (proven) | Low (preserved) | High (unproven) |
| Python version ceiling | 2 + 3 | Remove 3.10 ceiling | None |

---

## Consequences

**Easier:**
- P1 pylogix adoption requires no new code — the emulation research already confirmed it works.
- The fork strategy does not require reimplementing CIP encoding.
- Two-library split (`nexus-logix` / `nexus-legacy`) means a ControlLogix-only NEXUS
  deployment never loads DF1 code.

**Harder:**
- NEXUS must maintain the `nexus-logix` fork (rebasing against pycomm3 upstream if security
  fixes land; tracking Python version compatibility).
- The asyncio transport replacement is non-trivial: pycomm3's internals assume synchronous
  socket access throughout the session lifecycle.
- UDT access against a real ControlLogix (not emulated via FT Linx) must be tested to confirm
  pycomm3's dict mode works for PlantPAx P_* types. The emulation research did not test this.
- MPL 2.0 compliance for any libplctag-derived code requires careful boundary management.

**What this does NOT decide:**
- Echo SDK license acquisition (required before P6 sim-write tests; separate procurement
  decision outside this ADR).
- Specific MicroLogix site coverage (pycomm3 SLCDriver vs. libplctag vs. OPC-UA — deferred
  to confirmed site requirement).
- NEXUS architecture internals beyond the library-level: connection pool sizing, retry
  policy, health check interval.

---

## Action items

1. [ ] Continue using pylogix directly for P1–P2 NEXUS read stubs. No fork work needed yet.
2. [ ] Schedule `nexus-logix` fork kickoff as P6 preparation inside NEXUS (fork pycomm3,
       replace transport with asyncio, add pool, add structured exceptions). This is NEXUS
       development work, not a TALOS phase. Target: fork complete and integrated into NEXUS
       MCP tools before TALOS P6 (sim-execute) begins.
3. [ ] Test pycomm3 SLCDriver against a real MicroLogix 1100 (or emulated equivalent) to
       characterize reliability before recommending it for any legacy-PLC read tool.
4. [ ] Test pycomm3's LogixDriver UDT dict mode against a real ControlLogix (not Emulate
       5000 + FT Linx bridge) to confirm P_* UDT member access works at the CIP layer.
5. [ ] When a site with PLC-5 or DF1-serial SLC is confirmed, evaluate libplctag DH+ bridge
       path before committing to `nexus-legacy` scope.
6. [ ] Echo SDK license cost/model: separate procurement track. Required for P6 sim-write
       (programmatic download to emulated controller).
7. [ ] Document NEXUS capability manifest tool declarations for `logix_browse_tags`,
       `logix_read_tag`, and `logix_write_sim` as part of P3 NEXUS scaffolding.
