# PLC Connectivity — Research Notes

> Research date: 2026-06-14
> Purpose: Characterize the Python PLC connectivity library landscape for NEXUS. NEXUS is the
> MCP-bounded domain capability behind TALOS (ADR-001). TALOS itself never talks to a PLC.
> All libraries discussed here live inside NEXUS, not in `platform/`.
>
> Note: No NEXUS source code is present at `/mnt/i/`. The TALOS monorepo contains only the
> TALOS orchestration platform. NEXUS exists as a design target (MCP server) whose internals
> are to be built. Reasoning about NEXUS architecture is therefore inference from ADR-001 and
> ADR-004 intent, not from inspected code.

---

## Landscape overview

Two distinct protocol families cover the Allen-Bradley (Rockwell) PLC world:

**EtherNet/IP + CIP (modern Logix family)**
ControlLogix, CompactLogix, Micro8xx. Tag-name-based access. All modern Rockwell platforms.
Python libraries: pylogix, pycomm3, cpppo, libplctag, eeip.py.

**DF1 / PCCC (legacy family)**
PLC-5, SLC 500, MicroLogix. Address-based access (N7:0, F8:1, B3/5). Serial or Ethernet.
MicroLogix 1100 exposes EtherNet/IP but requires PCCC encapsulation (service code 0x4B) — it
does not implement native CIP services (Get/Set Attribute Single, Multiple Service Packet).
Python libraries: pydf1 (reyanvaldes fork), leicht/Df1 (unmaintained), robertlarue/DF1Comm
(unmaintained). libpccc (SourceForge, Visual Basic, not Python).

**OPC-UA path**
As of v36 (firmware), 1756-L8x ControlLogix controllers support OPC UA natively on their
Ethernet port. OPC-UA is viable as an abstraction layer if a site already has an OPC-UA server
(e.g. Kepware). open62541 (C library) has Python wrappers (o6Python via Cython). This path adds
a required infrastructure dependency and latency tier; it does not replace EtherNet/IP for
direct connectivity.

---

## pylogix — technical details

**Source:** https://github.com/dmroeder/pylogix  
**Protocol:** EtherNet/IP with CIP. Port 44818.  
**Supported families:** ControlLogix, CompactLogix, Micro8xx (programmed in Studio5000 or CCW).
RSEmulate supported with additional configuration.  
**Explicitly unsupported:** PLC-5, SLC 500, MicroLogix — different protocol family; maintainer
states no plans to add support.

**API surface:**
- `Read(tag)` / `Read([tag1, tag2, ...])` — batch reads in one packet, efficient
- `Write(tag, value)` — single or list
- `GetTagList()` — tag browsing / discovery
- Returns `Response(TagName, Value, Status)` per tag

**Dependencies:** Zero external dependencies. Pure Python. Works on Python 2, Python 3,
MicroPython.

**Thread safety:** Not thread-safe for shared instances. Each thread must create its own
`PLC()` instance. This is documented community knowledge, not an explicit library guarantee.
Concurrent reads from multiple NEXUS workers require one connection per worker or a
connection-per-call acquire/release pattern.

**Asyncio:** No native async/await support. All operations are synchronous and blocking.
Async use requires `loop.run_in_executor()` (threadpool delegation) or worker-per-coroutine.

**Error handling:** `Response.Status` is a string (e.g. "Success", "Connection timed out",
"Tag not found"). No structured exception hierarchy. Distinguishing error classes (connection
refused vs. tag not found vs. type mismatch) requires string parsing.

**Batch reads:** Passing a list to `Read()` is the efficient path — bundles multiple CIP
service requests into Multiple Service Packets. Single-tag loops are inefficient.

**Confirmed working (from `docs/upstream/rockwell-emulation-etherNetIP-notes.md`):**
- Connected to Emulate 5000 via FT Linx EtherNet/IP bridge at 10.0.0.11:44818
- 488 tags returned
- Read/write confirmed: BOOL, DINT, REAL, STRING, TIMER
- **UDT access:** ALL P_* UDT instances return "Privilege violation." All UDT members return
  "Path destination unknown." Root cause: FT Linx bridge exposes atomic CIP objects only.

**Maintenance status:** Active. 705 commits, current version 0.7.10. MIT license.

**Offline mode:** Not supported. pylogix requires a live responding CIP endpoint. L5X files
cannot be parsed. The library uses actual TCP/UDP sockets; a non-responding host times out.

---

## pycomm3 — technical details

**Source:** https://github.com/ottowayi/pycomm3  
**Docs:** https://pycomm3.readthedocs.io/en/latest/  
**Protocol:** EtherNet/IP with CIP. Derives from the original `pycomm` (Python 2); is not
backward-compatible with pycomm API.  
**Supported families:**
- **LogixDriver:** ControlLogix, CompactLogix, Micro800
- **SLCDriver:** SLC500, MicroLogix — present and documented, but explicitly marked "legacy;
  development will be on a limited basis"
- **CIPDriver:** Generic EtherNet/IP devices (drives, switches, meters)

**SLC/MicroLogix status:** The SLCDriver exists as a port of pycomm's SlcDriver with minimal
changes. The driver handles PCCC-encapsulated CIP (service code 0x4B) for legacy devices.
Active bugs acknowledged in GitHub issues (#51, #15); limited maintainer bandwidth. Treat as
best-effort, not production-grade for legacy PLCs.

**API differences from pylogix:**
- Returns `Tag` objects with `.error` and `.__bool__()` for success/failure evaluation
- Automatic packet optimization — bundles requests respecting connection size limits
- Handles fragmentation for tags that exceed single-packet capacity
- UDT/structure support: can read/write full UDT instances as Python dicts
  (structure definitions are read from the PLC's symbol table)

**UDT/AOI handling:** Better than pylogix in theory. pycomm3 can enumerate structure
definitions from the controller's symbol table and reconstruct UDT instances as dicts. In
practice, reported latency issues on some deployments: reading a single tag can introduce
multi-second delays. UDT traversal quality depends on the controller version and connection
mode.

**Asyncio:** No native async/await. All operations synchronous.

**Error model:** Slightly more verbose than pylogix. `Tag.error` carries an error message;
boolean coercion on `Tag` gives pass/fail. Still string-based, not a typed exception hierarchy.

**Maintenance status:** Repository notice states "pycomm3 is no longer actively developed."
Latest release 1.2.16 (December 22, 2025). 504 stars, 53 total releases. Functionally
complete but in maintenance-only mode. Python 3.6.1–3.10 supported (3.6.0 explicitly excluded).

**Dependencies:** Zero external dependencies (standard library only).

---

## pydf1 (reyanvaldes fork) — technical details

**Source:** https://github.com/reyanvaldes/pydf1  
**Forked from:** metalsartigan/pydf1  
**Package name on PyPI:** `df1py3`  
**Protocol:** Allen-Bradley DF1 — the serial/Ethernet byte-oriented protocol for legacy PLCs.
Different from EtherNet/IP; uses PCCC command set directly.

**Supported PLC families:** SLC 500, MicroLogix (serial and Ethernet transport). PLC-5 is
architecturally in scope for DF1 but not confirmed as tested.

**Transport:** Both Ethernet and serial connection supported. The modular architecture is
"ready to accept a new PLC class" per the README.

**API surface:** Address-based (Allen-Bradley file-notation), NOT tag-name-based:
- Integer file: `N7:0`, `N7:1`
- Float file: `F8:1`, `F8:2`
- Boolean file: `B3:0/5`
- Timer/Counter attributes: `T4:0.PRE`, `T4:0.dn`

No UDTs, no tag names, no symbol table browsing. Strictly file-number + element-number access.

**What the fork adds over metalsartigan/pydf1:** Not explicitly documented in the README.
The fork has 91 commits vs. the leicht/Df1 implementation (which is unmaintained, from a
2003-era codebase). The reyanvaldes fork maintains Python 3.6+ compatibility. The original
metalsartigan repo is the more commonly referenced upstream.

**Known limitations acknowledged in the repo:**
- "Implementation is incomplete" — some DF1 commands not implemented
- Only Ethernet and serial connections currently supported
- No mention of DH+ or DH-485 bridge support

**Production quality assessment:** Low-to-medium. 2 GitHub stars. "Working and quite stable"
per the README but incomplete command set. Suitable for proof-of-concept or constrained
use (specific file types, specific PLC models). Not suitable as a production-grade library
without additional testing and hardening. The command gaps are unknown without source audit.

---

## Head-to-head comparison

| Feature | pylogix | pycomm3 | pydf1 (reyanvaldes) | libplctag | cpppo |
|---|---|---|---|---|---|
| Protocol | EtherNet/IP + CIP | EtherNet/IP + CIP | DF1 (PCCC) | EtherNet/IP + CIP + Modbus | EtherNet/IP + CIP |
| Language | Pure Python | Pure Python | Pure Python | C + bindings | Pure Python |
| ControlLogix | Yes | Yes | No | Yes | Yes |
| CompactLogix | Yes | Yes | No | Yes | Yes |
| Micro8xx | Yes | Yes | No | Yes | Partial |
| SLC 500 | No | Legacy only | Yes | Via bridge | No |
| MicroLogix | No | Legacy only | Yes | Via bridge | No |
| PLC-5 | No | No | Partial | Via bridge | No |
| Tag-name access | Yes | Yes | No | Yes | Yes |
| File-address access | No | Via SLCDriver | Yes | Yes | No |
| UDT/structure read | Atomic only | Dict (best-effort) | No | Partial | No |
| Batch reads | Yes (list API) | Yes (auto-bundle) | No | No | Yes (pipelined) |
| Async/await native | No | No | No | Callback-based | Thread-based pipeline |
| Thread safety | No (one instance/thread) | Unknown | Unknown | Internal threads | Unknown |
| Offline / L5X parsing | No | No | No | No | No |
| Zero Python deps | Yes | Yes | Yes | No (C library) | No (greenery, etc.) |
| ARM/Linux | Yes | Yes | Yes | Requires C build | Yes |
| Active maintenance | Yes | Maintenance-only | Low activity | Active (v2.7.0, May 2026) | Low activity |
| License | MIT | MIT | Unknown | MPL 2.0 / LGPL 2+ | MIT |
| Python version | 2 + 3 + MicroPython | 3.6.1–3.10 | 3.6+ | Via wrapper | 2 + 3 |

**Other libraries noted:**
- **eeip.py** (rossmann-engineering): EtherNet/IP implicit + explicit messaging; generic device
  support; 31 commits, no releases, minimal activity. Not Allen-Bradley specific.
- **leicht/Df1**: DF1 for SLC500/MicroLogix via serial telnet interface. Last commit ~2003.
  Unmaintained.
- **robertlarue/DF1Comm**: Fork of ABDF1 from SourceForge. Not Python-native.
- **libpccc** (SourceForge): PCCC in Visual Basic. Not Python.
- **OpenOPC**: Windows COM bridge to KEPServerEX. Deprecated, Windows-only.

---

## Gap analysis

**No single library covers both EtherNet/IP Logix and DF1 PLC5/SLC in Python.**

libplctag (C) comes closest: ControlLogix/SLC 500/PLC-5 via EtherNet/IP or DH+ bridge,
Modbus TCP. But its Python binding is minimal (thin C wrapper), not a first-class Python API.
MPL 2.0 / LGPL 2+ license introduces copyleft complexity if NEXUS is proprietary.

**Gaps across all current libraries for production NEXUS deployment:**

1. **No asyncio.** Every library is synchronous. NEXUS, as an MCP server likely running async
   Python, must wrap all PLC calls in executor threadpools. This creates connection-per-call
   pressure and makes connection pooling hard.

2. **No connection pooling.** pylogix: one instance per thread. pycomm3: unspecified. No
   library provides a pool-of-sessions abstraction that multiple MCP tool calls can share
   against the same PLC IP.

3. **No structured error model.** Status strings, not typed exceptions. Distinguishing
   "PLC unreachable" from "tag not found" from "type mismatch" from "access denied" requires
   string matching — fragile.

4. **No offline / L5X mode.** All libraries require a live PLC. NEXUS's analysis tools
   (which parse L5X exports for documentation, impact analysis, etc.) already work offline via
   the `mcp__nexus__ingest_l5x` pattern. The connectivity layer cannot participate in that
   workflow without a live controller or emulator.

5. **UDT access incomplete.** pylogix cannot read UDT instances at all through the FT Linx
   bridge (privilege violation, confirmed in emulation research). pycomm3's UDT dict mode
   has latency issues and is not confirmed against PlantPAx P_* types. Accessing P_DIn,
   P_DOut, P_Intlk members requires either the Logix Echo SDK or Path C (L5X bypass logic).

6. **No MCP-native design.** No existing library is structured as an MCP server. NEXUS must
   write adapter/glue code to wrap whichever library is chosen into MCP tool definitions.

7. **DF1 coverage is inadequate.** pydf1 is incomplete; leicht/Df1 is 2003-era unmaintained.
   No Python library reliably handles the full PCCC command set for PLC-5 / SLC 500.

8. **No Echo SDK routing.** Testing against Emulate 5000 requires FT Linx bridge; against
   Logix Echo SDK requires a separate licensed SDK. No Python library provides transparent
   routing between live hardware, FT Linx emulation, and Echo SDK targets.

---

## Key TALOS findings

- NEXUS does not exist as source code at `/mnt/i/`. All statements about NEXUS internals
  are design inference from ADR-001 (NEXUS behind MCP) and ADR-004 (read profile allows
  live reads; write profile is offline/sim only).
- pylogix is confirmed working for atomic tags against Emulate 5000 via FT Linx bridge.
  UDT access is confirmed blocked (privilege violation) — this is a protocol-layer constraint,
  not a pylogix bug.
- ADR-004 `read` profile explicitly allows live PLC reads. The EtherNet/IP libraries (pylogix,
  pycomm3) are within scope for the read path.
- ADR-004 `write` profile allows only `offline_artifact` or `sim_only`. Writing to a live PLC
  falls outside what any TALOS-orchestrated agent may do, regardless of what the library
  supports technically. NEXUS enforces this via its own capability manifest.
- The FT Linx bridge approach (emulation research, Path A/B/C) confirms that a live-hardware
  EtherNet/IP path and an emulation path use identical pylogix code. Routing is transparent.
- No Python library handles both protocol families (Logix EtherNet/IP and DF1/PCCC) in a
  single package.

---

## What a better Logix library looks like

**Async/await native (asyncio):**
Build on `asyncio` streams or `trio`. Each PLC connection is a persistent TCP session
(`asyncio.open_connection()` to port 44818). CIP request/response framing is not pipelined
by default (one request-response cycle on the same socket), but multiple concurrent coroutines
can multiplex over the same connection with a request queue and correlation IDs. This matches
the CIP "Multiple Request Service" model and avoids the one-thread-per-connection cost.

**Thread-safe connection pooling:**
A `LogixPool(ip, max_connections=4)` context that manages N persistent EtherNet/IP sessions.
Each MCP tool call checks out a connection, issues its read batch, and returns the connection.
Pools isolate per PLC IP. Internally implemented as `asyncio.Queue` of open transport handles.

**Full UDT/AOI structure traversal:**
The fundamental blocker is not pylogix — it's the FT Linx bridge stripping UDT paths.
A production library against a real ControlLogix (or Logix Echo SDK) can read UDT members
via CIP symbolic path segments: `[tag_name, member_name]` as CIP path steps. The library
must implement multi-level path construction, not just flat tag names. This is the feature
pycomm3's LogixDriver partially implements (UDT as dict) but pylogix does not.

**Offline mode (L5X tag definitions):**
Parse `<Tags>` and `<DataTypes>` sections from the L5X XML without a live PLC. Expose
a `read(tag)` call that returns the type and default/last-saved value from the L5X. This
enables NEXUS analysis tools (impact analysis, documentation generation) to call the same
library API regardless of whether a live PLC is present. Tag type resolution, UDT structure
expansion, and AOI parameter mapping can all run from the L5X schema without network access.

**MCP-ready design:**
Structure the library as an MCP server itself — each CIP operation is an MCP tool. NEXUS
wraps it with zero glue. This is the cleanest architecture: the library handles connection
lifecycle, credential injection, and error serialization; NEXUS supplies tool policy and
gate enforcement above it.

**Stub/simulation mode:**
A `MockLogixDriver(l5x_path)` that loads an L5X and behaves like a live driver. Tags read
from the L5X schema; writes are in-memory only. Tests run without hardware or Emulate 5000.
The Logix Echo SDK is the production emulation layer (licensed); the stub covers unit tests.

**Structured error model:**
```python
class LogixError(Exception): pass
class ConnectionError(LogixError): pass      # TCP refused / timed out
class TagNotFoundError(LogixError): pass     # CIP path not resolved
class TypeMismatchError(LogixError): pass    # Write type != tag type
class PrivilegeError(LogixError): pass       # CIP privilege violation (UDT via bridge)
class ServiceError(LogixError):              # CIP service returned error status
    def __init__(self, service, status_code, extended_status): ...
```

**Build from or fork:** pycomm3 is the closest upstream — it already has UDT dict support
and automatic packet bundling. However, it targets Python 3.6–3.10, uses sync-only patterns,
and is in maintenance-only mode. A clean fork rebasing pycomm3's CIP encoding on top of an
asyncio transport layer is likely faster than building from scratch.

---

## What a new PLC5/SLC/DF1 library looks like

**Protocol:** DF1 full-duplex (Ethernet) and half-duplex (serial RS-232). The two variants
share the PCCC command set but differ in framing (BCC vs CRC, ENQ/ACK for half-duplex).

**File-based address format:** No tag names. Every address is `[FileType][FileNumber]:[Element][/Bit]`:
- `N7:0` — Integer file 7, element 0
- `F8:1` — Float file 8, element 1
- `B3:0/5` — Bit file 3, word 0, bit 5
- `T4:0.PRE` — Timer file 4, element 0, PRE field
- `C5:1.ACC` — Counter file 5, element 1, ACC field

**PCCC command set (priority order for NEXUS):**
1. `0x0F / 0xA2` — Protected Typed Logical Read (the normal read command)
2. `0x0F / 0xAA` — Protected Typed Logical Write
3. `0x06` — Diagnostic Status (controller status, fault codes)
4. `0x0F / 0x03` — Unprotected Bit Write (coil forcing for test injection)
5. `0x1A` — Read SLC Status (for MicroLogix variants)

**What pydf1 currently misses:**
- Incomplete command set (unspecified which commands are missing)
- No structured exception hierarchy
- No async support
- No connection pooling
- No PLC-5 confirmation (only SLC/MicroLogix tested)
- No documentation of the DH+ bridge path (for PLC-5 accessed via 1756-DHRIO in a Logix chassis)

**OPC-UA as an alternative:** If a site already has Kepware or RSLinx Classic OPC running,
an OPC-UA client (python-opcua, asyncua) is a better path than native DF1. It abstracts
protocol differences, provides a standard subscription model, and is maintained by the OPC
Foundation. The cost is an OPC-UA server infrastructure dependency. For greenfield NEXUS
deployments against legacy PLCs with no existing OPC infrastructure, native DF1 is lower
friction.

**DH+ bridge path:** PLC-5 and SLC-5/04 with DH+ can be accessed from Logix via a
1756-DHRIO module. libplctag supports this path via EtherNet/IP routing through the Logix
chassis. This may be the most practical production path for PLC-5 (avoid DF1 serial entirely;
route through whatever modern Logix chassis is on the network). The library should detect and
support CIP routing segments for DH+ bridge targets.

---

## Scope and ownership recommendation

**Two separate packages, both outside the TALOS monorepo, both inside NEXUS:**
- `nexus-logix` — Logix EtherNet/IP driver (asyncio, pooled, UDT-capable)
- `nexus-legacy` — PLC-5/SLC DF1 driver (or thin wrapper over libplctag's DH+ bridge path)

**Rationale for separate packages:** The two protocol families have no shared code path.
Bundling them adds dependency weight to every NEXUS deployment regardless of which PLC
family is present. The MCP capability manifest (ADR-004) declares each tool individually;
a site with only ControlLogix does not need DF1 code loaded.

**Inside NEXUS, not in the TALOS monorepo:** Per ADR-001, TALOS never talks to a PLC.
Putting PLC libraries in `platform/` would violate the MCP boundary. NEXUS is a separate
MCP server; its dependencies are NEXUS dependencies.

**Build fresh vs. fork:**
- Logix driver: fork pycomm3. It has CIP encoding, UDT dict support, and packet bundling.
  Replace the synchronous transport with `asyncio.open_connection()`, add connection pool,
  add structured exceptions, add L5X offline mode. Estimated scope: medium (not a from-scratch
  rewrite — CIP encoding is the hard part and pycomm3 has it).
- DF1 driver: evaluate libplctag as a C dependency for the DH+ bridge path first. If that
  covers the actual site equipment (most PLC-5 sites have a modern Logix chassis with DHRIO),
  a thin Python wrapper around libplctag is faster than implementing PCCC from scratch.
  Build native Python DF1 only if Ethernet-direct DF1 to a SLC without any Logix chassis
  is a confirmed site requirement.

**License:** MIT for `nexus-logix` (consistent with pylogix upstream, simplest for
commercial use). MPL 2.0 for any libplctag wrapper (to comply with libplctag's dual license).

---

## NEXUS architecture implications

**No NEXUS source at `/mnt/i/`:** NEXUS exists only as a design target. The following
is reasoned from ADR-001 (NEXUS is the MCP server; TALOS connects to it as an MCP client)
and ADR-004 (capability profiles govern what each tool may do).

**How new libraries attach to NEXUS:**
Each library operation becomes an MCP tool declaration in NEXUS's capability manifest.
Examples:
```json
{ "name": "logix_read_tag", "profile": "read", "safety": false }
{ "name": "logix_browse_tags", "profile": "read", "safety": false }
{ "name": "logix_write_sim", "profile": "write", "write_kind": "sim_only",
  "sim_target": { "kind": "logix_echo", "verify_critic": "echo_sdk_verify" } }
```
There is no `write_kind: live` — ADR-004 prohibits it. Live writes from an agent are
architecturally impossible regardless of what the library technically supports.

**Connection management:**
- One `LogixPool` instance per PLC IP in NEXUS's process lifetime (persistent, not per-call).
- Pool size: 2–4 connections per PLC (EtherNet/IP CIP supports multiple simultaneous sessions).
- MCP tool call checks out a connection, issues the read batch, returns the connection — all
  within the async MCP handler.
- Connection health check (heartbeat ping or reconnect-on-error) inside the pool.

**Echo SDK routing:**
The emulation research (Emulate 5000 via FT Linx) uses identical pylogix code to a real
controller. For the Echo SDK path (licensed), the routing is at the IP/port level — a
different IP or a multiplexed port. The library does not need Echo SDK awareness; NEXUS's
tool configuration injects the correct IP/port per environment (emulation vs. live vs. Echo SDK).

**Read path (ADR-004 `read` profile):**
Activated in P1 (single-worker spine proves the read flow). The first live read is
`logix_browse_tags` (tag discovery) followed by `logix_read_tag` for specific values.

**Write path (ADR-004 `write` profile, `sim_only`):**
Activated in P6 (sim-execute). The gate critic (`echo_sdk_verify`) must pass before any
simulated write. No live write path exists.

---

## Open questions for the builder

1. **Which NEXUS PLC families are in scope first?** If all current sites are ControlLogix /
   CompactLogix, `nexus-logix` alone covers the initial deployment. DF1/legacy work can wait
   until a site with PLC-5 or SLC-without-DHRIO is onboarded.

2. **DH+ bridge availability:** Do the PLC-5 / SLC sites have a modern Logix chassis with
   1756-DHRIO? If yes, libplctag's CIP routing covers them; native DF1 is unnecessary.

3. **Echo SDK license cost:** The emulation research confirmed no programmatic download API
   without Echo SDK. If the test-pipeline Phase 2 (download) is required, the Echo SDK
   license decision must happen before P6.

4. **UDT access in production:** The FT Linx bridge blocks UDT reads (privilege violation).
   Is this also true against a real ControlLogix (no bridge, direct EtherNet/IP)? The
   emulation research does not answer this. pycomm3's UDT dict mode against a real Logix
   controller needs to be tested before P6.

5. **Logix firmware version coverage:** pycomm3 targets 3.6.1–3.10 Python; does it work
   against Logix firmware v20 (common in brownfield)? v35+? Version matrix needs testing.

6. **MicroLogix via pycomm3 SLCDriver:** The driver exists but is "legacy, limited
   development." Is it reliable enough for read-only NEXUS tools against MicroLogix? Needs
   a controlled connectivity test.

7. **pycomm3 Python 3.10 ceiling:** pycomm3 explicitly supports up to Python 3.10. If NEXUS
   targets 3.11+ (for asyncio improvements), pycomm3 would need a fork or compatibility work.

8. **Connection count limits:** ControlLogix controllers have a per-controller limit on
   simultaneous EtherNet/IP sessions (typically 16–64 depending on controller model). The pool
   size must stay well below the controller's session limit.

---

## Build-phase impact

**Read-path connectivity (P1 context):**
The P1 spine (single-worker) proved the read flow end-to-end using direct pylogix calls
inside NEXUS tool stubs. A production-grade `nexus-logix` library is not required for P1 —
pylogix is sufficient for the spine to demonstrate the full read path.

**Library development phase recommendation:**
The `nexus-logix` library (asyncio, pooled, UDT-capable) should be developed as a parallel
workstream between P2 and P4, so it is ready to replace the P1 stub before P4 (memory
federation) requires reliable tag reads for graph construction. Call this **P3.5** or a
named spike deliverable within P3.

**P6 sim-execute / write path:**
P6 is the first phase that exercises writes (sim_only profile). The library must support the
`logix_write_sim` tool by P6. The Echo SDK decision (for programmatic download) must be made
before P6 planning begins.

**DF1 / legacy library:**
No current build phase requires DF1 connectivity. If legacy PLC sites are in scope, a
pre-P6 spike to evaluate libplctag's DH+ bridge path (or to build `nexus-legacy`) should be
planned when a specific site requirement is confirmed. Do not build ahead of a confirmed need.
