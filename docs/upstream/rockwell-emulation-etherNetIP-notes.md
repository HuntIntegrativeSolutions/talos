# Rockwell Emulation & EtherNet/IP — Technical Deep-Dive Notes

> Research date: 2026-06-12
> Source: Logix Emulate 5000 v35 + FactoryTalk Linx + pylogix
> Purpose: Establish the capability envelope for agent-driven PLC testing via emulation. Document what works, what doesn't, and the three paths forward.

---

## Executive Summary

| Area | Finding | TALOS Disposition |
| :--- | :--- | :--- |
| **EtherNet/IP bridge** | FactoryTalk Linx exposes Emulate 5000's virtual backplane as a standard EtherNet/IP device when "Listen on EtherNet/IP Encapsulation Ports" is enabled. Works with any CIP client. | **Verified.** No special license or hardware required. |
| **pylogix connectivity** | Connected to 10.0.0.11:44818, returned 488 tags, confirmed read/write on BOOL/DINT/REAL/STRING/TIMER. | **Adopt** as the primary agent-side CIP client. |
| **PlantPAx UDT access** | Every P_* UDT instance returns "Privilege violation" on read. Every UDT member returns "Path destination unknown." | **Blocked.** The FT Linx bridge exposes atomic CIP objects only — no CIP Data Table access for user-defined types. |
| **Automated testing pipeline** | Four-phase design: Index (L5X parser) → Download (programmatic deploy) → Test (pylogix I/O injection) → Report (Excel pass/fail). Rockwell's ra-logix-cicd repo implements much of this in C#. | **Architecture validated.** Gap: Emulate 5000 has no programmatic download API. Logix Echo SDK does (licensed). |
| **Initial controller state** | Emulated Unit_PLC was saved from a real commissioning session — faulted, with operational history. Cleared to clean state 0 via direct writes + HMI reset commands. | Emulated state is persistent and mutable. Snapshots critical for test repeatability. |

---

## 1. The EtherNet/IP Bridge Discovery

### Claim being tested

Community documentation states Logix Emulate 5000 cannot expose controllers over EtherNet/IP — no virtual 1756-EN2T module is available, and the virtual backplane is local-only.

### What was found

FactoryTalk Linx has a hidden configuration setting: **"Listen on EtherNet/IP Encapsulation Ports."** With this enabled, the Emulate 5000 virtual backplane presents itself as a standard EtherNet/IP device at `10.0.0.11:44818`.

pylogix connected immediately with no special configuration:

```
Connected to 10.0.0.11:44818
488 tags returned
Read/Write confirmed on BOOL, DINT, REAL, STRING, TIMER types
```

The emulated controller is **indistinguishable from a real controller** to any CIP client at the CIP Data Table level. The bridge handles 80% of a testing pipeline's data access needs.

### Implications

- Agent-driven testing does not need a physical controller or licensed emulation tier for most tag operations
- Test harnesses can run against emulated controllers on the NUC or any Windows machine running Emulate 5000
- The bridge applies to any controller loaded into Emulate 5000, not just Unit_PLC

---

## 2. Automated Testing Workflow Design

### Four-phase pipeline

#### Phase 1 — Index
- Python L5X parser extracts: tags, routines, rungs, AOI definitions, safety boundaries
- Output: structured program representation indexed in NEXUS

#### Phase 2 — Download
- Programmatic deployment of modified logic to the emulated controller
- **Gap:** Emulate 5000 has no programmatic download API. Logix Echo SDK does, but requires a separate license.

#### Phase 3 — Test
- pylogix injects inputs (BOOL writes, DINT simulations), reads outputs, compares against expected values
- Validated: read/write works for atomic types
- **Gap:** cannot simulate PlantPAx I/O because P_DIn/P_DOut/P_Intlk/P_Perm UDTs return privilege violations

#### Phase 4 — Report
- Excel workbook with pass/fail results linked to rung references
- Structural work only — no technical blockers

### Existing work

Rockwell maintains **ra-logix-cicd** on GitHub (MIT-licensed, C#): a CI/CD pipeline using Logix Echo SDK for controller lifecycle and LDSDK for tag manipulation. TALOS should read this repo for patterns but the Python-based approach is preferred for agent-native integration.

---

## 3. Live Controller Exploration

### Architecture discovered

Unit_PLC is a full PlantPAx application:

| Scope | Tag Count | Details |
| :--- | :--- | :--- |
| Controller-scoped | 313 | - |
| Program-scoped (P_Seq) | 175 | - |

**Seven sub-sequences:** Burner, DryST, Init, Feed, Sample, Emergency, IO_Map

**Equipment:** 9 screw conveyors (STR103-116), 2 VFDs

**PlantPAx UDT coverage:**

| UDT Type | Instance Count |
| :--- | :--- |
| P_DIn | 23 |
| P_DOut | 14 |
| P_Perm | 15 |
| P_Intlk | 18 |
| P_Motor | 10 |
| P_VSD | 4 |
| P_AIn | 9 |
| P_Reset | 18 |

### Initial state

The emulator was saved from a real commissioning session and started in a faulted state:

| Component | State | Fault Code |
| :--- | :--- | :--- |
| Global_Emergency_Active | TRUE | - |
| IDF_Emergency_Active | TRUE | - |
| Burner | 99 | 172 |
| DryST | 99 | 6106 |
| Init | 99 | 888 |

Operational history showed 2 unit runs and 1 burner run, all ending in faults.

### Recovery

The controller was cleared to clean state 0 via two steps:

1. **Direct writes** to the emergency latch BOOLs (killed both emergency signals)
2. **HMI reset commands** zeroed all four sequencers to state 0 with no faults

Result: controller went from fully locked-out to fully clean without touching the VM, using only pylogix writes.

---

## 4. Capability Matrix

| Operation | Status | Detail |
| :--- | :--- | :--- |
| Read/write BOOL | ✅ | Controller and program scoped |
| Read/write DINT | ✅ | All scopes |
| Read/write REAL | ✅ | All scopes |
| Read/write STRING | ✅ | Tested, confirmed |
| Read TIMER | ✅ | Raw bytes, parseable |
| Read UDT instance | ❌ | Privilege violation |
| Read/write UDT member | ❌ | Path destination unknown |

### Root cause

The FactoryTalk Linx bridge exposes **atomic CIP objects only** (BOOL, DINT, REAL, TIMER, STRING). It does not expose the full CIP Data Table model for user-defined types. Every P_* UDT instance returns privilege violation because the bridge cannot resolve the CIP path segments for a type it doesn't natively understand.

**What this blocks:**
- Setting `DI_BIO_ESP.Inp` (P_DIn member) to simulate a field input
- Reading `Burner_Intlk_PLC.Sts_OK` (P_Intlk member) to check interlock health
- Checking `BIO_Permissive.Sts_Available` (P_Perm member) for permissive state
- The entire PlantPAx I/O abstraction layer is opaque

### What this means

The sequencers (Burner, DryST, Init) sit at state 0 because their start permissive chain — `P_Perm → P_Intlk → P_DIn` — is unreadable and unwritable. The atomic tags work, but the PlantPAx orchestration layer is a black box.

---

## 5. Three Paths Forward

### Path A — Logix Echo SDK (licensed)

- Full CIP object model access including UDTs
- Programmatic download (unlocks Phase 2 of the testing pipeline)
- **Cost:** requires Logix Echo SDK license
- **TALOS integration:** C# SDK, would need a Python bridge or wrapper service

### Path B — Direct BOOL forcing via L5X analysis

- Parse the L5X to find which atomic BOOLs the UDT outputs cascade to
- Force those atomic BOOLs directly, bypassing the PlantPAx abstraction
- **Cost:** engineering time per-application, fragile to program changes
- **TALOS integration:** native Python, no license needed
- **Risk:** the UDT-to-atomic mapping is not always 1:1; some logic paths may be unreachable

### Path C — Modify the ACD before loading

- Add bypass/test-mode logic that maps UDT internals to exposed BOOL tags
- Load the modified program into Emulate 5000
- **Cost:** one-time modification per application
- **TALOS integration:** requires the L5X parser (Phase 1) to inject the bypass rungs
- **Risk:** the modified program must be verified against the original to ensure test-mode doesn't mask real faults

### Recommendation

Path C for the PlantPAx UDT gap (modification is auditable and reversible), Path A for the download gap if the license cost is acceptable. Path B is a fallback for quick ad-hoc testing where audit trail is less critical.

---

## 6. Vault Documents Created

| Path | Purpose |
| :--- | :--- |
| `20-Engineering/TALOS/Rockwell-Emulation-EtherNetIP-Limitations.md` | Corrected from "can't" to verified working with FT Linx setting |
| `20-Engineering/TALOS/Automated-PLC-Testing-Workflow.md` | Four-phase design + Rockwell CI/CD analysis |
| `20-Engineering/TALOS/Unit-PLC-Exploration.md` | Live controller exploration findings |

---

## 7. Open Questions

- Does Logix Echo SDK expose the same CIP Data Table model, or does it have its own UDT access mechanism?
- Can the emulator snapshot/restore state programmatically for test repeatability?
- Is the "Listen on EtherNet/IP Encapsulation Ports" setting persistent across FT Linx restarts, or does it require a scripted enable at boot?
- What is the exact cost/licensing model for Logix Echo SDK (per-seat, per-instance, subscription)?

---

## References

- Ra-logix-cicd: https://github.com/rockwellautomation/ra-logix-cicd
- pylogix: https://github.com/ruscito/pylogix
- TALOS BLUEPRINT.md v0.6 — §Capabilities, §Gate & critics
- TALOS `docs/upstream/ml-integration-notes.md` — §Domain ML (anomaly detection path uses the same emulation bridge)
