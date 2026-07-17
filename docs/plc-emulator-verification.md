# PLC Emulator Verification (P6 Landing 2)

## What this verifier asserts, and why

`emulator_consistency` (registered in `talos/critics/registry.py`, implemented in
`talos/verifiers/emulator.py`) is a deterministic ADR-021 verifier critic. It connects
read-only to a FactoryTalk Logix Echo emulator via [pylogix](https://github.com/dmroeder/pylogix),
reads the running controller's program and controller-scope tag inventory, and cross-checks
it against NEXUS's documented knowledge of the same PLC. The result — a 0.0–1.0 score and a
human-readable mismatch summary — lands as a `task_gate_results` row, visible to the human
reviewer at the gate alongside every deterministic critic and other verifier.

This exists so that "NEXUS's documentation of a PLC" and "what the PLC actually is" can be
checked against each other mechanically, before a human spends review time trusting either
one. It's evidence, not a substitute for the human gate: this landing ships the verifier as
`advisory=True` (a `warn` row, never blocking), so a drifted PLC never silently stops a task
from reaching review — it just shows up in the gate UI.

**Guardian doctrine, applied to a new capability class.** TALOS's structural rule is that AI
proposes, humans review, deterministic critics gate, and nothing writes to a live system
without a human's approval. This verifier extends that rule to industrial-protocol reads:

- **Zero writes, structurally.** `talos/verifiers/emulator.py`'s `ReadOnlyEmulatorClient`
  exposes only `GetDeviceProperties`, `GetProgramsList`, `GetTagList`, and `Read` — no other
  pylogix method is ever referenced in the module, and a unit test
  (`test_emulator_module_source_never_calls_pylogix_write`) asserts the string `Write(` never
  appears in the module's source at all. The verifier fn itself doesn't even call `Read` —
  only `GetDeviceProperties`/`GetProgramsList`/`GetTagList` are needed for the consistency
  check, so the emulator round-trip count stays minimal.
- **No address reachable except an allow-listed, explicitly-confirmed emulator.** A task-body
  rubric marker (below) picks an emulator by *name* — it can never supply a host or slot
  itself. Only `talos.toml`'s `[emulators]` section (or its shipped defaults in
  `talos/config.py`) defines real addresses, and only entries with `confirmed_emulator = true`
  are ever connected to. This is the structural guard against ever pointing the verifier at a
  production PLC through task-body content alone.
- **No hang can stall the gate.** The verifier tracks a wall-clock deadline
  (`read_timeout_s`, default 10s) across the whole emulator + NEXUS read sequence. If it's
  exceeded, the fn returns a refusal (`score=None`) rather than blocking `deliverable_node`
  indefinitely. No LLM call is made, so there's no budget/spend interaction either.

## Logix Echo setup notes

The emulator this landing was built and probed against: **FactoryTalk Logix Echo**,
identifying as `"Emulator R35.11"`, running the `Dryer_PLC` test program at
`10.0.0.11`, processor slot `0`, EtherNet/IP TCP `44818`.

- Echo runs on a Windows host (a `DESKTOP-*`-named box in this deployment). Ensure the
  Windows Firewall allows inbound TCP 44818 from the TALOS host, and that Echo's "remote
  connections" setting is enabled so pylogix (on a different box) can reach it — by default
  Echo instances only accept loopback connections.
- `GetTagList(False)` (controller scope only) is what the verifier reads; `GetTagList(True)`
  (controller + program-scoped) is intentionally not used, since NEXUS's own inventory (via
  `tag_find_plant_wide`, below) is also controller-scope only for the tags side of the
  comparison — program *names* are still recovered from NEXUS's tag `scope` field.
- `GetProgramsList()` returns names prefixed `Program:` (e.g. `Program:P_Seq`); the verifier
  strips that prefix before comparing against NEXUS.

## `talos.toml [emulators]` reference

```toml
[emulators.dryer_echo]
host = "10.0.0.11"
slot = 0
confirmed_emulator = true
connect_timeout_s = 3   # per-request pylogix socket timeout
read_timeout_s = 10     # wall-clock ceiling for the whole verifier read sequence
```

`dryer_echo` ships as a hardcoded default (`talos/config.py`'s `_EMULATORS_DEFAULTS`) even
with no `talos.toml` on disk, so the verifier works against the reference emulator out of the
box. Overriding `dryer_echo` in `talos.toml`, or adding another `[emulators.<name>]` block,
replaces that key's config **wholesale** (the merge is shallow, per-key — not a deep field
merge). Any key without `confirmed_emulator = true` is refused at read time, even if a task
names it.

## Task-marker syntax

The verifier is opted into per-task via the same rubric-marker rail as every other P6
verifier (`talos.task_origin.extract_rubrics`), but the marker body is JSON config, not
prose:

```
<!-- talos:rubric:emulator_consistency
{"plc_id": "NFK-DRYER-TEST-V2", "emulator": "dryer_echo"}
-->
```

- `plc_id` — the NEXUS `plc_id` to cross-check against (NOT the emulator config key).
- `emulator` — a key in `talos.toml`'s `[emulators]` (or the shipped defaults). This is the
  *only* thing that selects a real network target; `plc_id` never resolves to an address.

Worked example, `Dryer_PLC` on the reference emulator:

```
Read the Dryer_PLC test controller inventory and cross-check it against NEXUS.

<!-- talos:rubric:emulator_consistency
{"plc_id": "NFK-DRYER-TEST-V2", "emulator": "dryer_echo"}
-->
```

## Score formula

```
program_recall    = |nexus_programs ∩ emulator_programs| / |nexus_programs|
tag_coverage_e2n   = |emulator_tags found in nexus| / |emulator_tags|
tag_coverage_n2e   = |nexus_tags found in emulator| / |nexus_tags|
type_agreement     = |intersecting tags, case-insensitive, matching data_type| / |intersecting tags|

score = 0.2 * program_recall + 0.2 * tag_coverage_e2n + 0.2 * tag_coverage_n2e + 0.4 * type_agreement
```

`program_recall` (not a Jaccard/union comparison) only asks "does every program NEXUS *does*
know about still exist on the controller" — NEXUS's program signal is derived from tag
`scope` values (there is no populated program-list source in NEXUS today; see below), which
under-counts programs that have no program-scoped tags documented. A Jaccard score would
wrongly punish a healthy PLC twin for programs NEXUS simply hasn't documented tags in;
recall doesn't. Emulator programs NEXUS hasn't documented are still reported by name in the
reasoning text, just not scored against.

`type_agreement` is weighted highest (0.4) because a data-type drift between the live
controller and NEXUS's documentation is the single highest-value catch this verifier exists
to make. Both sides compare tag names and data types case-insensitively — NEXUS and pylogix
report UDT/AOI type names in the same vocabulary (e.g. `P_AIn`, `L_ModuleSts`), so no
name-mapping table is needed.

`score_threshold = 0.95` — strict by design. Since the verifier ships `advisory=True` this
landing, a low score never blocks anything; it surfaces as a `warn` row for the human
reviewer.

### Why the NEXUS tag source is a sharded sweep, not a single call

Two natural-looking NEXUS read-profile tools turned out not to work for this: `tag_search`'s
`"*"` is FTS5 word-matching, not a wildcard (returns 0 rows), and `get_plc_knowledge_graph`
is empty until the full documentation pipeline has run for a PLC (it hadn't, for the
reference test PLC — `list_documented_plcs` is empty today). `tag_find_plant_wide` does
support `"*"` as a real SQL-`LIKE` wildcard, but hard-caps at 500 rows **plant-wide**
(alphabetical) — a single `"*"` query for any PLC that doesn't sort first in the plant
starves down to a handful of surviving rows.

The verifier instead recursively shards `tag_find_plant_wide` by prefix (`A*`, `B*`, ...
`0*`-`9*`, `_*`), refining any shard that comes back `truncated: true` one level deeper
(`SA*`, `SB*`, ...), bounded by the same wall-clock deadline and a max-call backstop. A shard
still truncated at the recursion ceiling is reflected in the reasoning text
("N shard(s) could not be fully enumerated"), never silently dropped.

**Sweep performance**: the whole shard sweep runs over ONE persistent MCP session (opened
once in a dedicated event-loop thread; each shard is a `call_tool` on that session, not a
fresh `streamablehttp_client` handshake). Live-probed against the real NEXUS instance, a
full sweep of a ~950-tag plant completes well inside the shipped `read_timeout_s = 10`
default with zero incomplete shards. If a much larger plant ever does exhaust the deadline,
the verifier still reports honestly (score reflects only what it actually saw, and the
reasoning text names every incomplete shard) rather than claiming coverage it doesn't have —
raise `read_timeout_s` in `talos.toml`, or treat it as the concrete motivation for the
`list_plc_inventory` NEXUS tool noted in Roadmap below (one call replacing the whole sweep).

**Known enumeration limits** (both live-probed): NEXUS's `tag_find_plant_wide` translates
`*` to SQL LIKE `%` but leaves `_` untouched — and `_` is LIKE's single-char wildcard — so
tag names BEGINNING with an underscore cannot be isolated by any shard pattern and are
invisible to the NEXUS side of the comparison (rare in Logix naming; such tags on the
emulator side still surface honestly as "only in emulator"). Emulator-side
connection/module internal objects (names containing `:`, e.g. `Cxn:Diagnostic:…`) are
filtered out before comparison — a Logix user tag name can never contain `:`, so NEXUS can
never document them and counting them would permanently depress coverage with non-drift noise.

## Failure modes

| Condition | Result | Notes |
|---|---|---|
| Unknown emulator key (not in `[emulators]`) | `warn` row, `score=None` | Refuses to guess at an address |
| `confirmed_emulator` not `true` | `warn` row, `score=None` | Structural guard against production PLCs |
| Invalid/missing marker JSON | `warn` row, `score=None` | e.g. missing `plc_id` or `emulator` key |
| Emulator unreachable / connection error | `warn` row, `score=None` | pylogix exception caught and reported |
| Read exceeds `read_timeout_s` | `warn` row, `score=None` | Wall-clock deadline across the whole read sequence |
| NEXUS tool not read-profile allow-listed | Raised as a `RuntimeError`, caught → `warn` row | Should not occur with the shipped manifest |
| Perfect / partial match | `pass` (score ≥ 0.95) or `warn` (below threshold) | Never a blocking `fail` this landing (`advisory=True`) |

All refusal paths route through the same `(score=None, reason)` contract, which — because this
verifier is registered `advisory=True, fail_open=False` — always produces a visible `warn` row
at the gate. Nothing is ever silently dropped.

## Roadmap

- **Long-term fix for the NEXUS tag source**: a `list_plc_inventory(plc_id)` read-profile
  tool added to NEXUS itself, giving a real program+tag inventory in one call, replacing the
  sharded `tag_find_plant_wide` sweep above. That's a NEXUS-repo change plus a manifest
  re-pin (ADR-032 territory) — out of this landing's scope.
- **Making this blocking**: flipping the registration's `advisory=False` (a one-line change
  in `talos/critics/registry.py`) turns a below-threshold score into a blocking `fail`,
  waivable at the gate. Not done this landing — the verifier ships informational-only until
  its scoring has run against enough real tasks to trust the threshold.
- **Logix Echo SDK (UDT/snapshot/download) and FactoryTalk Linx** are both documented,
  future-considered paths for deeper emulator interaction (structured UDT reads, full
  project snapshot/download workflows) — neither is built here; pylogix's tag-level reads
  are sufficient for this verifier's consistency check.
- **Hooks**: TALOS has no runtime hook system yet (ADR-033 pending) — this verifier runs
  synchronously inside `deliverable_node`, not via any hook.
