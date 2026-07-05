# NEXUS v1.26.0 capability dispositions (RT-14)

**Server:** NEXUS v1.26.0 · `http://10.0.0.80:8765/mcp` (Streamable HTTP)
**Enumeration date:** 2026-07-05
**Total live tools:** 90 (ADR-026's "~85" figure was pre-growth)
**Excluded (SoR-writers):** 18 · **read:** 60 · **write:offline_artifact:** 12 · **in manifest.json:** 72

## content_hash convention

`capability.content_hash` is computed as:

```python
sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
```

with `capability.content_hash` set to the empty string `""` in `manifest` at hash time. Sorted
keys, no whitespace, UTF-8 encoding — this exact recipe must be used by anyone recomputing the
hash (ADR-032 runtime re-verification is out of scope for RT-14; this is the only written record).

## Denylist scope

The EXCLUDED bucket covers **fact-SoR writers only**: tags, tag descriptions, the
rung-pattern library, and ingestion tables. `write:offline_artifact` legitimately includes tools
that write NEXUS's *derived* doc/diagram store (e.g. `full_plc_documentation`) under a
gate-approved write grant — that is not a contradiction of "SoR-writers excluded."

## Dispositions (all 90 tools)

| name | disposition | reason |
|---|---|---|
| `add_rung_pattern` | EXCLUDED | Adds a new validated rung pattern to the library for future reuse — writes the rung-pattern-library SoR. |
| `address_trace_chain` | read | Traces a signal chain through PLC logic for an address — read-only analysis. |
| `address_xref` | read | Full cross-reference for a PLC address — read-only. |
| `address_xref_summary` | read | Lightweight address cross-reference counts — read-only. |
| `aoi_instances_find` | read | Finds AOI instances plant-wide or in one PLC — read-only. |
| `aoi_library_search` | read | Searches AOI definitions in the Engineering Library — read-only. |
| `aoi_version_check` | read | Compares AOI instance revisions against the plant standard library — read-only comparison. |
| `backfill_engineer_verified_sources` | EXCLUDED | Backfills description_sources table from engineer-verified tag descriptions — ingestion into the reconciliation SoR. |
| `backfill_symbol_file_sources` | EXCLUDED | Backfills description_sources table from symbol-file tag descriptions — ingestion into the reconciliation SoR. |
| `classify_signal_roles` | read | Classifies PLC-5 addresses by structural role from existing evidence — computed/returned, no persistence. |
| `coverage_gap` | read | Measures ladder-logic address coverage against the tags table — read-only. |
| `dint_bit_audit` | read | Audits DINT arbitration tags for bit-level write conflicts — read-only audit, returns findings only. |
| `extract_rung_comments` | read | Explicitly a "dry run"; description states it does NOT write to description_sources, returns a candidate list only — read-only. |
| `find_area_interlocks` | read | Finds rungs where addresses from two I/O areas co-appear — read-only. |
| `find_docs_for_tag` | read | Finds vault notes referencing a tag — read-only. |
| `find_first_out_faults` | read | Returns detected first-out fault-latch groups — read-only. |
| `find_interlocks` | read | Traces interlocks/permissives for an output — read-only. |
| `find_orphan_tags` | read | Finds tags with no description/vault note/rung comment — read-only. |
| `ftview_find_objects` | read | Finds FT View HMI objects by type/screen/tag pattern — read-only. |
| `ftview_navigation_graph` | read | Returns the FTView screen-to-screen navigation graph — read-only. |
| `ftview_object_catalog` | read | Returns the FT View object type catalog — read-only. |
| `ftview_object_example` | read | Returns one example XML snippet for an object type — read-only. |
| `ftview_rung_xref` | read | Combines HMI bindings and PLC rung search for a tag address — read-only. |
| `ftview_screen_layout` | read | Returns all objects on a FT View screen in tree form — read-only. |
| `ftview_state_labels` | read | Returns operator-visible state labels for an address — read-only. |
| `ftview_tag_pattern` | read | Finds FT View objects referencing a tag pattern — read-only. |
| `ftview_validate` | write:offline_artifact | When fix=True, auto-fixes and writes {filename}_fixed.xml (never overwrites original) — conditional offline-artifact write; tool-level profile covers the write capability (judgment call #5). |
| `ftview_writable_tags_audit` | read | Lists HMI elements that can write to PLC tags — a safety audit; returns a list only, writes nothing itself. |
| `full_plc_documentation` | write:offline_artifact | Runs the full 19-step documentation pipeline; stores generated docs/diagrams/knowledge-graph in NEXUS's derived analysis store (not the tag/description fact SoR) — offline/generated artifact per task's explicit bucket-C instruction; flagged nuance (judgment call #8). |
| `generate_ascii_rung` | write:offline_artifact | Produces deployable ASCII ladder logic for a human to paste into a PLC program — textbook generated-ladder artifact per ADR-004's definition of offline_artifact by content, not by persistence mechanism (judgment call #4). |
| `generate_dox_tree` | write:offline_artifact | Writes a regenerable AGENTS.md doc tree to disk; description explicitly states "NO DB writes and NO processor communication" — pure file artifact; resolves prior redteam concern (judgment call #7). |
| `generate_io_inventory` | write:offline_artifact | Generates a flat physical I/O inventory document for a PLC — offline artifact. |
| `generate_io_package` | write:offline_artifact | Generates a complete I/O documentation package (inventory + verification plan) — offline artifact; named as the contract's own write example. |
| `generate_logic_flow` | write:offline_artifact | Generates a Graphviz DOT signal/logic-flow diagram — offline artifact. |
| `generate_network_topology` | write:offline_artifact | Generates a Graphviz DOT diagram of plant network topology — offline artifact. |
| `generate_plc_impact_diagram` | write:offline_artifact | Generates a Graphviz DOT impact diagram for a PLC — offline artifact. |
| `generate_sequence_of_operations` | write:offline_artifact | Generates a Sequence of Operations document for a PLC — offline artifact. |
| `generate_subsystem_diagram` | write:offline_artifact | Generates a Graphviz DOT subsystem diagram — offline artifact. |
| `get_plc5_instruction_reference` | read | Returns semantic definition/operands/timing for a PLC-5 instruction — static reference lookup. |
| `get_plc_diagram` | read | Retrieves a previously stored diagram for a PLC — read-only retrieval. |
| `get_plc_document` | read | Retrieves a previously stored document for a PLC — read-only retrieval. |
| `get_plc_knowledge_graph` | read | Retrieves the knowledge graph for a PLC, optionally filtered — read-only retrieval. |
| `get_rung_pattern` | read | Returns a validated rung pattern template with a plant example — read-only. |
| `get_state_machine` | read | Returns detected ladder state machines for a PLC — read-only. |
| `get_vault_note` | read | Reads a vault note's frontmatter + body from disk — read-only (rejects path traversal). |
| `group_io_by_area` | read | Clusters a PLC's I/O points into functional areas — computed/returned, no persistence. |
| `hmi_coverage_by_plc` | read | Percentage of a PLC's tags displayed on at least one HMI — read-only. |
| `hmi_project_summary` | read | Summary of HMI project(s) with live binding counts — read-only. |
| `hmi_search_by_address` | read | Finds HMI bindings across projects referencing a PLC address — read-only. |
| `ignition_device_map_tool` | read | Lists Ignition OPC device-to-PLC mappings — read-only. |
| `ignition_resolve_all` | EXCLUDED | Batch-resolves unresolved Ignition tags and seeds new device-map entries — writes the ignition device-map SoR; flagged for human review (judgment call #3, not in the task's seed denylist). |
| `ignition_tag_search` | read | Searches indexed Ignition tags — read-only. |
| `ingest_factorytalk_csv` | EXCLUDED | Ingests a FactoryTalk View ME CSV tag database export into HMI tables — ingestion writer. |
| `ingest_ftview_displays` | EXCLUDED | Ingests FT View ME display XML files into the NEXUS HMI object catalog — ingestion writer. |
| `ingest_ftview_project_folder` | EXCLUDED | Walks a project folder and ingests all found FT View display XML — ingestion writer. |
| `ingest_l5x` | EXCLUDED | Ingests a ControlLogix L5X export file into NEXUS — ingestion writer. |
| `ingest_quickdesigner_bindings` | EXCLUDED | Ingests QuickDesigner JSON bindings into hmi_projects + hmi_tag_bindings — ingestion writer. |
| `ingest_quickdesigner_descriptions` | EXCLUDED | Ingests HMI label descriptions from a QuickDesigner parser JSON file into the description-sources SoR — ingestion writer. |
| `ingest_rung_comments` | EXCLUDED | Extracts rung comments and inserts them into description_sources as source_type='rung_comment' — ingestion writer. |
| `ingest_vision_descriptions` | EXCLUDED | Ingests HMI label descriptions from an Ignition Vision extractor output into the description-sources SoR — ingestion writer. |
| `list_documented_plcs` | read | Lists PLCs documented via the full pipeline — read-only. |
| `migration_gap_analysis` | read | Analyzes what's required to migrate a PLC to ControlLogix — read-only analysis. |
| `migration_readiness` | read | Returns migration readiness details for a PLC — read-only. |
| `migration_readiness_all` | read | Returns compact migration readiness summaries for all PLCs — read-only. |
| `nexus_reindex` | EXCLUDED | Triggers a re-index of PLC source files into the NEXUS database — core SoR (re)write. |
| `nexus_status` | read | NEXUS server health: schema version, PLC count, tag count — read-only. |
| `onboard_plc` | EXCLUDED | Runs the full analysis pipeline including promotion of raw addresses to tags (stage 2, not skippable by default) and reindex — composite pipeline that includes SoR-write stages. |
| `ote_conflict_audit` | read | Scans output-coil write sites and flags multi-writer addresses — read-only audit, returns findings only. |
| `plant_summary` | read | High-level statistics for the entire NEXUS knowledge graph — read-only. |
| `plc_impact_analysis` | read | Returns the communication footprint of a PLC — read-only. |
| `promote_raw_addresses_to_tags` | EXCLUDED | Promotes rung-referenced addresses into synthetic tag rows — writes new tag SoR rows. |
| `reconcile_descriptions` | EXCLUDED | Description states "Does NOT modify any table" (pure read), but ADR-026/task brief mandate exclusion regardless — fail-closed per explicit instruction; flagged for human review (judgment call #1). |
| `render_rung` | read | Renders an existing PLC rung as structured text (boolean/indented/annotated) — displays existing logic, no artifact produced; contrast with generate_ascii_rung which produces new logic. |
| `routine_call_tree` | read | Walks the JSR call graph from a routine — read-only. |
| `rung_forensic` | write:offline_artifact | Has an output_file arg: "If set, write Markdown to this path" — conditional offline-artifact write (judgment call #6). |
| `rung_search` | read | Full-text search across rung content/comments — read-only. |
| `screen_inventory` | read | Lists indexed HMI screens with binding counts — read-only. |
| `screen_tag_bindings` | read | Gets tag bindings for a named HMI screen — read-only. |
| `search_rung_patterns` | read | Searches the rung pattern library by natural-language description — read-only. |
| `semantic_rung_search` | read | Natural-language semantic search over PLC ladder rungs via local embeddings — read-only. |
| `tag_annotate` | EXCLUDED | Writes tag description/confidence directly (verified_by, verified_at, confidence tier) — the canonical tag-description SoR writer. |
| `tag_context` | read | Unified 360-degree view of a single tag (definition, refs, links) — read-only. |
| `tag_diff` | EXCLUDED | In snapshot mode (compare_to=None) each call creates/updates an internal snapshot — a hidden state mutation; cross-PLC mode is pure read. Fail-closed exclusion since profile is declared per-tool; flagged for human review (judgment call #2). |
| `tag_find_plant_wide` | read | Searches for tags across all PLCs — read-only. |
| `tag_full_chain` | read | Traces a tag from PLC logic through HMI displays — read-only. |
| `tag_screen_usage` | read | Finds HMI screens displaying a given tag — read-only. |
| `tag_search` | read | Fuzzy/multi-word tag search (FTS5 trigram) — read-only. |
| `tag_suspect_descriptions` | read | Finds tags with unreliable descriptions — read-only. |
| `tag_where_used` | read | Returns every routine/rung where a tag is used — read-only. |
| `verification_summary` | read | Lists PLCs with at least one verified tag — read-only. |

## Flagged for human review (judgment calls)

1. **`reconcile_descriptions`** — live description explicitly says "Does NOT modify any
   table" (pure read). ADR-026 and the RT-14 task brief both name it as a required
   EXCLUDED SoR-writer regardless. Excluded anyway per fail-closed doctrine and explicit
   instruction; a human should confirm or correct this in a future manifest revision.
2. **`tag_diff`** — in snapshot mode (`compare_to=None`) each call creates/updates an
   internal snapshot (a NEXUS-side state mutation); in cross-PLC mode it is pure read.
   Since profile is declared per-tool, not per-argument, and one mode mutates state,
   fail-closed says EXCLUDE. Not named in the task's seed denylist — a new finding.
3. **`ignition_resolve_all`** — "seeds new device map entries for known unresolved
   devices" — a genuine write to NEXUS's ignition device-map table. Not named in the
   task's seed denylist — a new finding, excluded fail-closed.
4. **`generate_ascii_rung`** — produces deployable ladder-logic text for a human to paste
   into a PLC program. Per ADR-004, `offline_artifact` is defined by artifact content
   ("generated ladder, HMI screens"), not by whether a file is literally written to disk.
   Classified `write:offline_artifact` — contrast with `render_rung`, which only displays
   *existing* logic and stays `read`.
5. **`ftview_validate`** — writes `{filename}_fixed.xml` (never overwrites the original)
   only when called with `fix=True`; otherwise pure read. Tool-level profile must cover
   the write capability, so it is classified `write:offline_artifact`.
6. **`rung_forensic`** — has an `output_file` arg: "If set, write Markdown to this path."
   Same conditional-write shape as `ftview_validate` — classified `write:offline_artifact`.
7. **`generate_dox_tree`** — `docs/integration/03_redteam_review.md:54` listed this among
   suspected SoR-writers, but its live description explicitly states "performs NO DB
   writes and NO processor communication" (atomic file-tree writes only). Classified
   `write:offline_artifact`; this resolves the earlier redteam concern.
8. **`full_plc_documentation`** — description says it "Stores everything in the NEXUS
   database for future retrieval" — writes NEXUS's own DB, but into a derived
   documents/diagrams/knowledge-graph store, not the tags/description-sources SoR the
   contract's invariants protect. See "Denylist scope" above.
9. **`resumable_cursor` / `findings` blocks** — no tool among the 90 exposes cursor-based
   pagination or a finding-lifecycle field (`queued/proposed/confirmed/dismissed`) at MCP
   level. Both are set to unsupported/false honestly rather than fabricating capability
   claims; open items for a future manifest version.

