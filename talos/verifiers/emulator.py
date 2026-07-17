"""
P6 Landing 2 -- deterministic emulator-consistency verifier (ADR-021/024).

Guardian doctrine, applied to a new capability class (industrial-protocol
reads): this module is a read-only boundary around pylogix. The read-only
wrapper below is structurally limited to GetDeviceProperties / GetProgramsList
/ GetTagList / Read -- no other pylogix method is ever called anywhere in this
module, and `Read` exists only for scripts/emulator_verify_probe.py; the
verifier fn itself never calls it (fewer emulator round-trips, and the
no-write guard over the fn's actual call surface stays trivially auditable).
No PLC address is reachable except an allow-listed, explicitly-confirmed
emulator target from talos.config.get_emulators_config() -- a task-body
rubric marker picks a config key by NAME, it can never supply a host/slot
itself. This is the structural guard that no task-body content can ever
point the verifier at a production PLC.

pylogix is imported lazily, only inside _make_plc(), never at module top --
`import talos.critics.registry` (which imports this module to register
emulator_consistency_verifier) must never pull pylogix into sys.modules; see
talos.tests.test_p6_emulator_verifier.test_registry_module_does_not_import_pylogix.

NEXUS inventory source (empirically revised during plan review, live-probed
against 10.0.0.80): tag_search's "*" is FTS5 word-matching, not a wildcard
(returns 0 rows), and get_plc_knowledge_graph is empty until the full
documentation pipeline has run for a PLC (it hasn't, for the target test
PLC). tag_find_plant_wide DOES support "*" -> SQL LIKE, but hard-caps at 500
rows plant-wide (alphabetical) -- for any PLC that doesn't sort first, a
single "*" query starves down to a handful of surviving rows. This module
instead recursively shards tag_find_plant_wide by prefix (A*, B*, ... 0*-9*,
_*), refining any shard that comes back truncated one level deeper (SA*,
SB*, ...), bounded by a wall-clock deadline and a max-call backstop. Program
names are derived from the distinct program:* scopes seen across that sweep
(NEXUS has no populated program-list source today) -- this under-counts
programs with no program-scoped tags, which is why the score formula uses
program RECALL (does every program NEXUS knows about still exist on the
controller) rather than a Jaccard/union comparison that would punish a
healthy PLC twin for programs NEXUS simply hasn't documented tags in.

Future work: the correct long-term fix is a list_plc_inventory(plc_id)
read-profile tool added to NEXUS itself, giving a real program+tag inventory
in one call -- a NEXUS-repo change plus a manifest re-pin (ADR-032
territory), out of this landing's scope.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time

log = logging.getLogger(__name__)

_NEXUS_TOOL = "tag_find_plant_wide"
# NO "_" at the top level: tag_find_plant_wide translates * -> % but leaves _
# untouched, and _ is SQL LIKE's single-char wildcard -- a "_*" shard queries
# LIKE '_%', matching EVERY tag in the plant, so it always truncates and its
# refinements ("_A%" = any-char-then-A, ...) explode recursively until the
# call budget dies (observed live: 31 dead "_"-family shards per sweep).
# Leading-underscore tag names therefore cannot be isolated via this tool --
# a documented limitation (rare in Logix naming; emulator-side ones still
# surface honestly as "only in emulator") until NEXUS grows a real
# list_plc_inventory tool (see module docstring).
_SHARD_ALPHABET = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
# "_" IS kept for refinement positions ("AI_*" catches "AI_PDT_115" when
# "AI*" truncates) -- but a truncated "_"-suffixed pattern is reported, never
# recursed (each of its children over-matches the same way "_%" does).
_REFINE_ALPHABET = _SHARD_ALPHABET + ["_"]
_MAX_SHARD_CALLS = 200  # backstop; the wall-clock deadline is the real bound
_MAX_SHARD_DEPTH = 4  # e.g. "SABC*" -- deep enough for any real tag prefix collision

# Canned NEXUS inventory returned under TALOS_NEXUS_STUB=1, matching
# talos.nexus_seed's stub-gate idiom, so CI never needs live NEXUS.
_STUB_NEXUS_TAGS = [
    {"plc_id": "NFK-DRYER-TEST-V2", "name": "Active_Alarms", "data_type": "BOOL", "scope": "controller"},
    {"plc_id": "NFK-DRYER-TEST-V2", "name": "AutoStart_State", "data_type": "DINT", "scope": "controller"},
    {"plc_id": "NFK-DRYER-TEST-V2", "name": "Dryer_Temp_PV", "data_type": "REAL", "scope": "controller"},
    {"plc_id": "NFK-DRYER-TEST-V2", "name": "Seq_Step", "data_type": "DINT", "scope": "program:P_Seq"},
    {"plc_id": "NFK-DRYER-01", "name": "Unrelated_Tag", "data_type": "BOOL", "scope": "controller"},
]


class ReadOnlyEmulatorClient:
    """
    Thin read-only wrapper around a pylogix PLC instance. Exposes ONLY
    GetDeviceProperties/GetProgramsList/GetTagList/Read -- no write method of
    pylogix's PLC class is ever referenced here. See module docstring.
    """

    def __init__(self, host: str, slot: int, connect_timeout_s: float):
        self._comm = _make_plc(host, slot, connect_timeout_s)

    def get_device_properties(self):
        return self._comm.GetDeviceProperties()

    def get_programs_list(self):
        return self._comm.GetProgramsList()

    def get_tag_list(self, all_tags: bool = False):
        return self._comm.GetTagList(all_tags)

    def read(self, tag: str):
        return self._comm.Read(tag)

    def close(self) -> None:
        self._comm.Close()


def _make_plc(host: str, slot: int, connect_timeout_s: float):
    import pylogix

    comm = pylogix.PLC()
    comm.IPAddress = host
    comm.ProcessorSlot = slot
    # pylogix's socket-timeout attribute name has varied across versions;
    # set defensively so a missing attribute never raises here.
    for attr in ("SocketTimeout", "Timeout"):
        if hasattr(comm, attr):
            setattr(comm, attr, connect_timeout_s)
            break
    return comm


def _response_value(resp, what: str):
    """
    pylogix responses are typically a Response object with `.Value`/`.Status`
    (or are already the plain value -- accept either). A failed pylogix call
    (e.g. transient timeout, device busy) does NOT always raise a Python
    exception -- it can return Status != "Success" with Value=None/empty
    instead. Silently treating that as "the PLC has zero tags/programs"
    would misreport a read failure as real inventory drift, so this raises
    loudly instead (caught by the caller's refusal contract). Confirmed live
    against the reference emulator: a transient non-"Success" status here is
    real and must not be swallowed.
    """
    status = getattr(resp, "Status", None)
    if status is not None and status != "Success":
        raise ConnectionError(f"{what} failed: status={status}")
    return getattr(resp, "Value", resp)


def _normalize_program_name(raw: str) -> str:
    """'Program:P_Dev' -> 'P_Dev' (pylogix's GetProgramsList prefix); passes
    through unprefixed names unchanged."""
    prefix = "program:"
    if isinstance(raw, str) and raw.lower().startswith(prefix):
        return raw[len(prefix):]
    return raw


def _tag_name_type(tag) -> tuple[str | None, str | None]:
    if isinstance(tag, dict):
        name = tag.get("TagName") or tag.get("tag_name") or tag.get("name")
        dtype = tag.get("DataType") or tag.get("data_type") or tag.get("type")
        return name, dtype
    name = getattr(tag, "TagName", None) or getattr(tag, "tag_name", None) or getattr(tag, "Name", None)
    dtype = getattr(tag, "DataType", None) or getattr(tag, "data_type", None) or getattr(tag, "Type", None)
    return name, dtype


def _read_emulator_inventory(cfg: dict, deadline: float) -> tuple[set[str], dict[str, str]]:
    """
    Returns (program_names, {tag_name: data_type}) from the live emulator.
    Raises TimeoutError if `deadline` (time.monotonic()) is exceeded, and lets
    any pylogix/connection exception propagate -- the caller maps both to the
    verifier's (None, reason) refusal contract.
    """
    client = ReadOnlyEmulatorClient(cfg["host"], cfg["slot"], cfg.get("connect_timeout_s", 3))
    try:
        _response_value(client.get_device_properties(), "GetDeviceProperties")
        if time.monotonic() > deadline:
            raise TimeoutError("emulator read timed out reading device properties")

        programs_raw = _response_value(client.get_programs_list(), "GetProgramsList")
        if not programs_raw:
            raise ConnectionError("GetProgramsList returned no programs -- refusing to treat as real drift")
        programs = {_normalize_program_name(p) for p in programs_raw}
        if time.monotonic() > deadline:
            raise TimeoutError("emulator read timed out reading program list")

        tags_raw = _response_value(client.get_tag_list(False), "GetTagList")
        if not tags_raw:
            raise ConnectionError("GetTagList returned no tags -- refusing to treat as real drift")
        tags: dict[str, str] = {}
        for tag in tags_raw:
            name, dtype = _tag_name_type(tag)
            # Skip connection/module internal objects (e.g. "Cxn:Diagnostic:...",
            # "Cxn:Float:..."): pylogix's GetTagList surfaces them, but a Logix
            # USER tag name can never contain ":" -- NEXUS's L5X-derived
            # inventory will never document them, so counting them permanently
            # depresses tag_coverage_e2n with noise unrelated to real drift.
            if name and ":" not in name:
                tags[name] = dtype or ""
        if time.monotonic() > deadline:
            raise TimeoutError("emulator read timed out reading tag list")

        return programs, tags
    finally:
        client.close()


def _nexus_url() -> str:
    url = os.environ.get("TALOS_NEXUS_URL")
    if not url:
        raise RuntimeError("TALOS_NEXUS_URL is required for live NEXUS emulator-consistency checks")
    return url


def _rows_from_result(result) -> list[dict]:
    """Translate one tag_find_plant_wide MCP result into a plain row list.

    Prefer structuredContent (mcp SDK's already-parsed {"result": [...]}
    per tag_find_plant_wide's outputSchema) -- .content is a list of
    TextContent blocks, one per row, each needing its own json.loads; live
    probing showed real NEXUS responses carry both, so structuredContent is
    the reliable path."""
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and "result" in structured:
        raw = structured["result"]
    else:
        raw = getattr(result, "content", result)
        if isinstance(raw, (str, bytes)):
            raw = json.loads(raw)
        elif isinstance(raw, list):
            parsed = []
            for block in raw:
                text = getattr(block, "text", None)
                if text is not None:
                    parsed.append(json.loads(text))
                elif isinstance(block, dict):
                    parsed.append(block)
            raw = parsed

    if isinstance(raw, dict):
        raw = raw.get("result", [])
    return raw if isinstance(raw, list) else []


def _sweep_shards(fetch, deadline: float) -> tuple[list[dict], list[str]]:
    """
    Prefix-shard sweep over tag_find_plant_wide via `fetch(tag_pattern) ->
    list[dict]` (injected: the live thread-bridged session fetch, or a plain
    fake in tests). Returns (rows, truncated_shard_patterns); rows are NOT
    yet filtered to plc_id/scope -- the caller does that so this stays a
    thin, independently-testable shard-recursion seam.

    Dedup key is (plc_id, name, scope) -- NOT (plc_id, name): a program-scoped
    tag that shares its name with a controller-scoped tag is a distinct row,
    and collapsing them would drop either the tag from the inventory or the
    program:* scope the program-recall term is derived from.
    """
    all_rows: dict[tuple[str, str, str], dict] = {}
    truncated_shards: list[str] = []
    calls = 0

    def sweep(pattern: str) -> None:
        nonlocal calls
        if calls >= _MAX_SHARD_CALLS or len(pattern) > _MAX_SHARD_DEPTH or time.monotonic() > deadline:
            truncated_shards.append(pattern)
            return
        calls += 1
        rows = fetch(f"{pattern}*")
        truncated = False
        for row in rows:
            if row.get("truncated"):
                truncated = True
                continue
            key = (row.get("plc_id"), row.get("name"), row.get("scope"))
            all_rows[key] = row
        if truncated:
            if pattern.endswith("_"):
                # "_" is a LIKE single-char wildcard: every child of a
                # truncated "_"-suffixed pattern over-matches identically, so
                # recursing can never converge -- report instead.
                truncated_shards.append(pattern)
            else:
                for ch in _REFINE_ALPHABET:
                    sweep(pattern + ch)

    for ch in _SHARD_ALPHABET:
        sweep(ch)

    return list(all_rows.values()), truncated_shards


@contextlib.contextmanager
def _nexus_sweep_session(url: str, deadline: float):
    """
    ONE MCP session for a whole shard sweep, bridged to the sync sweep code.

    The first implementation ran asyncio.run(call_nexus_tool_raw(...)) per
    shard -- a fresh HTTP connection + MCP initialize handshake per call. At
    37+ calls per sweep, session setup dominated wall-clock: live probing
    showed a 60s budget leaving 25 shards unenumerated. One persistent
    session (opened here in a dedicated event-loop thread; the sync sweep
    calls asyncio.run_coroutine_threadsafe per shard) brings a full sweep to
    a few seconds and makes the score effectively latency-independent.

    Yields fetch(tag_pattern) -> list[dict]. Raises ConnectionError if the
    session can't be established; per-fetch timeouts are bounded by the
    remaining sweep deadline.
    """
    import asyncio
    import threading

    loop = asyncio.new_event_loop()
    ready = threading.Event()
    holder: dict = {}

    async def _runner():
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client

            async with streamablehttp_client(url) as (read, write, _sid):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    holder["session"] = session
                    holder["stop"] = asyncio.Event()
                    ready.set()
                    await holder["stop"].wait()
        except BaseException as e:  # noqa: BLE001 -- must cross the thread boundary
            holder["error"] = e
            ready.set()
            raise

    def _thread_main():
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_runner())
        except BaseException:  # noqa: BLE001 -- surfaced via holder["error"]
            pass
        finally:
            loop.close()

    thread = threading.Thread(target=_thread_main, name="nexus-sweep-session", daemon=True)
    thread.start()
    setup_budget = max(1.0, deadline - time.monotonic())
    if not ready.wait(timeout=setup_budget):
        raise ConnectionError(f"NEXUS MCP session setup exceeded {setup_budget:.0f}s deadline")
    if "error" in holder:
        raise ConnectionError(f"NEXUS MCP session setup failed: {holder['error']}")

    def fetch(tag_pattern: str) -> list[dict]:
        remaining = max(1.0, deadline - time.monotonic())
        future = asyncio.run_coroutine_threadsafe(
            holder["session"].call_tool(_NEXUS_TOOL, {"tag_pattern": tag_pattern}), loop
        )
        return _rows_from_result(future.result(timeout=remaining))

    try:
        yield fetch
    finally:
        loop.call_soon_threadsafe(holder["stop"].set)
        thread.join(timeout=10)


def _fetch_nexus_tag_inventory(plc_id: str, deadline: float) -> tuple[list[dict], list[str]]:
    """
    Returns (rows, truncated_shard_patterns). rows are NEXUS tag_find_plant_wide
    result dicts (plc_id, name, data_type, scope, description), NOT yet
    filtered to plc_id/scope -- see _sweep_shards/_score_and_reason.
    """
    if os.environ.get("TALOS_NEXUS_STUB") == "1":
        return list(_STUB_NEXUS_TAGS), []

    from talos.nexus_client import allowed_nexus_tool_names, load_nexus_manifest

    manifest = load_nexus_manifest()
    allowed = set(allowed_nexus_tool_names(manifest, write_grant=False))
    if _NEXUS_TOOL not in allowed:
        raise RuntimeError(
            f"NEXUS tool {_NEXUS_TOOL!r} is not in the read-profile allow-list "
            "for emulator-consistency verification"
        )

    with _nexus_sweep_session(_nexus_url(), deadline) as fetch:
        return _sweep_shards(fetch, deadline)


def _safe_ratio(numerator: int, denominator: int) -> float:
    """Empty denominator -> 1.0 (vacuous agreement), documented in the score
    formula: neither side claims anything to disagree about."""
    return 1.0 if denominator == 0 else numerator / denominator


def _score_and_reason(
    plc_id: str,
    emulator_key: str,
    emulator_programs: set[str],
    emulator_tags: dict[str, str],
    nexus_rows: list[dict],
    truncated_shards: list[str],
) -> tuple[float, str]:
    """
    Weighted score:

        program_recall   = |nexus_programs ∩ emulator_programs| / |nexus_programs|
        tag_coverage_e2n = |emulator_tags found in nexus| / |emulator_tags|
        tag_coverage_n2e = |nexus_tags found in emulator| / |nexus_tags|
        type_agreement   = |intersecting tags (case-insensitive name), matching
                             (case-insensitive) data_type| / |intersecting tags|

        score = 0.2 * program_recall + 0.2 * tag_coverage_e2n
              + 0.2 * tag_coverage_n2e + 0.4 * type_agreement

    program_recall (not a Jaccard/union) only asks "does every program NEXUS
    knows about still exist on the controller" -- NEXUS's program signal is
    derived from tag `scope` values (no populated program-list source exists
    in NEXUS today), which under-counts programs with no program-scoped tags
    documented; a Jaccard score would wrongly punish a healthy PLC twin for
    programs NEXUS simply hasn't documented tags in. Emulator programs NEXUS
    hasn't documented are still reported by name in the reasoning text, just
    not scored against. type_agreement is weighted highest (0.4): a
    mismatched data type between the live controller and NEXUS's
    documentation is the highest-value catch this verifier exists to make.
    An empty denominator anywhere (_safe_ratio) scores 1.0 (vacuous
    agreement) rather than dividing by zero.
    """
    nexus_rows_for_plc = [r for r in nexus_rows if r.get("plc_id") == plc_id and r.get("scope") == "controller"]
    nexus_program_rows = [r for r in nexus_rows if r.get("plc_id") == plc_id and str(r.get("scope", "")).startswith("program:")]
    nexus_programs = {r["scope"].split(":", 1)[1] for r in nexus_program_rows if ":" in r.get("scope", "")}

    nexus_tags = {r["name"]: (r.get("data_type") or "") for r in nexus_rows_for_plc if r.get("name")}

    # Case-insensitive key -> (original-case name, data_type), so matching is
    # case-insensitive but reasoning text still shows the real tag names.
    e_tags = {name.lower(): (name, dtype or "") for name, dtype in emulator_tags.items()}
    n_tags = {name.lower(): (name, dtype or "") for name, dtype in nexus_tags.items()}
    e_keys, n_keys = set(e_tags), set(n_tags)
    intersect = e_keys & n_keys

    program_recall = _safe_ratio(len(nexus_programs & emulator_programs), len(nexus_programs))
    tag_coverage_e2n = _safe_ratio(len(intersect), len(e_keys))
    tag_coverage_n2e = _safe_ratio(len(intersect), len(n_keys))
    type_matches = sum(1 for key in intersect if e_tags[key][1].lower() == n_tags[key][1].lower())
    type_agreement = _safe_ratio(type_matches, len(intersect))

    score = (
        0.2 * program_recall
        + 0.2 * tag_coverage_e2n
        + 0.2 * tag_coverage_n2e
        + 0.4 * type_agreement
    )

    missing_programs = sorted(nexus_programs - emulator_programs)
    undocumented_programs = sorted(emulator_programs - nexus_programs)
    tags_only_emulator = sorted(e_tags[key][0] for key in (e_keys - n_keys))
    tags_only_nexus = sorted(n_tags[key][0] for key in (n_keys - e_keys))
    type_mismatches = sorted(
        f"{e_tags[key][0]} (emulator={e_tags[key][1] or '?'}, nexus={n_tags[key][1] or '?'})"
        for key in intersect
        if e_tags[key][1].lower() != n_tags[key][1].lower()
    )

    n = 10
    lines = [
        f"emulator_consistency for plc_id={plc_id!r} emulator={emulator_key!r}: "
        f"score={score:.3f} (program_recall={program_recall:.2f}, "
        f"tag_coverage_e2n={tag_coverage_e2n:.2f}, tag_coverage_n2e={tag_coverage_n2e:.2f}, "
        f"type_agreement={type_agreement:.2f})",
        f"programs: {len(missing_programs)} NEXUS-documented missing from emulator"
        + (f" (e.g. {missing_programs[:n]})" if missing_programs else ""),
        f"programs: {len(undocumented_programs)} on emulator not documented in NEXUS"
        + (f" (e.g. {undocumented_programs[:n]})" if undocumented_programs else ""),
        f"tags: {len(tags_only_emulator)} only in emulator"
        + (f" (e.g. {tags_only_emulator[:n]})" if tags_only_emulator else ""),
        f"tags: {len(tags_only_nexus)} only in NEXUS"
        + (f" (e.g. {tags_only_nexus[:n]})" if tags_only_nexus else ""),
        f"tags: {len(type_mismatches)} data-type mismatches"
        + (f" (e.g. {type_mismatches[:n]})" if type_mismatches else ""),
    ]
    if truncated_shards:
        lines.append(
            f"NEXUS sweep: {len(truncated_shards)} shard(s) could not be fully enumerated "
            f"(e.g. {truncated_shards[:n]}) -- coverage figures above may undercount NEXUS tags"
        )
    return score, "\n".join(lines)


def emulator_consistency_verifier(deliverable: dict, rubric_text: str, nexus_client) -> tuple[float | None, str]:
    """
    VerifierSpec.fn for the "emulator_consistency" verifier (deterministic=True).
    `nexus_client` is accepted for signature parity with run_all_verifiers'
    dispatch but unused -- this fn makes its own NEXUS call (see module
    docstring / plan design decision #2), independent of deliverable_node's
    (currently always-None) injected client.

    rubric_text is a JSON object, not prose: {"plc_id": "...", "emulator": "..."}
    where "emulator" names a key in talos.config.get_emulators_config().

    Returns (score, reasoning) with the exact contract run_all_verifiers
    expects from score_fn: score=None means "could not verify" (invalid
    config, unknown/unconfirmed emulator target, unreachable, timeout) and is
    handled uniformly by run_all_verifiers' existing failure table -- with
    advisory=True, fail_open=False (this verifier's registration), every
    refusal mode below lands as a visible "warn" row, never silently dropped.
    """
    try:
        config = json.loads(rubric_text)
    except (TypeError, ValueError) as e:
        return None, f"invalid emulator_consistency marker JSON: {e}"

    if not isinstance(config, dict):
        return None, "emulator_consistency marker JSON must be an object"

    plc_id = config.get("plc_id")
    emulator_key = config.get("emulator")
    if not plc_id or not emulator_key:
        return None, "emulator_consistency marker JSON must include 'plc_id' and 'emulator'"

    from talos.config import get_emulators_config

    emulator_cfg = get_emulators_config().get(emulator_key)
    if emulator_cfg is None:
        return None, f"unknown emulator key {emulator_key!r} -- not present in talos.toml [emulators]"
    if not emulator_cfg.get("confirmed_emulator"):
        return None, f"emulator {emulator_key!r} is not marked confirmed_emulator=true -- refusing to connect"

    read_timeout_s = emulator_cfg.get("read_timeout_s", 10)
    deadline = time.monotonic() + read_timeout_s

    try:
        emulator_programs, emulator_tags = _read_emulator_inventory(emulator_cfg, deadline)
        nexus_rows, truncated_shards = _fetch_nexus_tag_inventory(plc_id, deadline)
    except TimeoutError as e:
        return None, str(e)
    except Exception as e:
        return None, f"emulator/NEXUS read failed: {e}"

    return _score_and_reason(plc_id, emulator_key, emulator_programs, emulator_tags, nexus_rows, truncated_shards)
