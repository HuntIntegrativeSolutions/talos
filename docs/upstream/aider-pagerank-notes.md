# Aider Repo-Map / PageRank — Technical Deep-Dive Notes

> Research date: 2026-06-11  
> Source: `Aider-AI/aider` — Apache 2.0 License  
>
> **Historical note:** predates ADR-039 (Neo4j/Redis options discussed below for the PageRank
> context map were later cancelled repo-wide; recursive CTEs / Apache AGE in Postgres is the
> current direction — see BLUEPRINT.md). This PageRank work is post-v1 and undesigned either way.
> Retained as written for the historical record.
> Purpose: Inform TALOS graph-seeding mechanism. Patterns only — zero ported code.

---

## Executive Summary

Aider's repo-map solves a precise problem: *given a task in a large codebase, which symbols and files are most likely to be relevant?* It answers with a compact text representation (~1k tokens) built by running **Personalized PageRank** over a file-to-file dependency graph, seeded by the current chat context. The result is injected into the LLM's system prompt and gives the model a navigational map of the codebase without having to read every file.

For TALOS, the same mechanism is adapted over the NEXUS knowledge graph: tags/routines become nodes, interlocks/references become edges, and the current task's tags seed the personalization vector. The output is a ~1k-token relevant subgraph description injected into the planner's prompt.

| Aspect | Aider | TALOS Adaptation |
| :--- | :--- | :--- |
| **Nodes** | Source files | NEXUS tags, routines, equipment items |
| **Edges** | Symbol references between files | Interlocks, cross-program references, I/O bindings |
| **Seeds** | Chat context files + mentioned identifiers | Current task tags + mentioned routines in task description |
| **Graph library** | NetworkX MultiDiGraph | Neo4j + Python networkx or native Cypher PageRank |
| **Symbol extraction** | Tree-sitter AST | Already in NEXUS DB (`tag_where_used`, `find_interlocks`) |
| **Output format** | Code snippets with `│` markers | NEXUS tag descriptions + rung text |
| **Token budget** | 1024 tokens (binary search fit) | Same approach: ~1k tokens, binary search |
| **Caching** | diskcache (file mtime) + in-memory | NEXUS indexed state + Redis TTL |

---

## 1. The Repo-Map Output Format

**File:** `aider/repomap.py`

The repo-map is plain text, not structured data. It is a condensed view of the most relevant parts of the codebase:

```
utils/helpers.py:
⋮
│def calculate_circle_area(radius):
│    return Math.PI * radius * radius
⋮

models/car.py:
│class Car:
│    def __init__(self, make, model, year):
│        self.make = make
⋮
│    def accelerate(self, increment):
```

**Conventions:**

| Marker | Meaning |
| :--- | :--- |
| `filename:` | File section header |
| `│` prefix | A "line of interest" — definition or reference with context |
| `⋮` | Omitted code (other lines not shown) |
| 100-char truncation | Long lines are cut; `output = "\n".join([line[:100] for line in ...])`|

**Token budget:** Default **1024 tokens** (configurable via `--map-tokens`). Scaled up to `max_context_window - 4096` when no files are in chat context (multiplier = 8).

**Injection:** The map string is prepended to the user file list in the system prompt under a `repo_content_prefix` wrapper ("Here's the repo map:").

---

## 2. How the Graph Is Built

**File:** `aider/repomap.py:get_ranked_tags()` (line 365)

**Graph type:** `networkx.MultiDiGraph` — directed, allows multiple parallel edges between the same pair of nodes.

**Nodes:** One node per source file (relative path string). Not classes or functions — the file is the unit.

**Edges:** Each edge represents "File A references identifier X, which is defined in File B":

```python
G.add_edge(referencer_file, definer_file, weight=W, ident="identifier_name")
```

**Build process:**

```python
defines = defaultdict(set)       # identifier → {files that define it}
references = defaultdict(list)   # identifier → [files that reference it]

for tag in all_tags:
    if tag.kind == "def":
        defines[tag.name].add(tag.rel_fname)
    elif tag.kind == "ref":
        references[tag.name].append(tag.rel_fname)

for ident in defines.keys() & references.keys():
    for referencer in references[ident]:
        for definer in defines[ident]:
            G.add_edge(referencer, definer, weight=W, ident=ident)
```

Only identifiers that appear both as definitions and as references contribute edges. Pure-definition symbols (never referenced elsewhere) are excluded.

---

## 3. Symbol Extraction — Tree-Sitter

**File:** `aider/repomap.py:get_tags_raw()` (line 279)

Aider uses **Tree-sitter AST parsing**, not ctags. Tree-sitter parses source files into an Abstract Syntax Tree, then runs `.scm` (Scheme) query files to extract symbol definitions and references.

**Tag namedtuple:**

```python
Tag = namedtuple("Tag", "rel_fname fname line name kind".split())
# kind: "def" (definition) or "ref" (reference)
```

**Example Python query (`queries/python-tags.scm`):**

```scheme
(class_definition
  name: (identifier) @name.definition.class) @definition.class

(function_definition
  name: (identifier) @name.definition.function) @definition.function

(call
  function: [(identifier) @name.reference.call
             (attribute attribute: (identifier) @name.reference.call)]) @reference.call
```

**Language support:** 30+ languages via `.scm` query files. Fallback: if a language has no reference queries, Pygments lexer extracts all identifiers as references (lines 346-363).

**Tag cache:** `diskcache.Cache` at `.aider.tags.cache.v{VERSION}/`. Key = (abs_path, mtime). Avoids re-parsing unchanged files.

---

## 4. Personalized PageRank

**File:** `aider/repomap.py:get_ranked_tags()` (lines 519-531)

```python
ranked = nx.pagerank(
    G,
    weight="weight",
    personalization=personalization,  # Seed vector
    dangling=personalization,          # Teleport targets for sink nodes
)
```

**Algorithm:** Personalized PageRank (not standard). Personalization vector biases the random walk toward seed nodes. The walker teleports to a seed node (with probability 1-alpha) instead of a random node.

**Parameters:**
- **Damping factor (alpha):** 0.85 (NetworkX default — not overridden)
- **Convergence tolerance:** ~1×10⁻⁶ (NetworkX default)
- **Iterations:** Runs until convergence

**Seed selection (lines 374-445):**

```python
personalize = 100 / len(fnames)   # Base value per file

for rel_fname in all_fnames:
    score = 0.0
    
    if rel_fname in chat_fnames:
        score += personalize    # File is in active chat context
    
    if rel_fname in mentioned_fnames:
        score = max(score, personalize)  # File explicitly mentioned in prompt
    
    path_parts = set(Path(rel_fname).parts)
    if path_parts & mentioned_idents:
        score += personalize    # Identifier mentioned in prompt matches path component
    
    if score > 0:
        personalization[rel_fname] = score
```

**Sources for seeds:**
1. Files in active chat context (currently being edited)
2. File paths mentioned explicitly in the user's message
3. Identifier names mentioned in the user's message that match file/directory names

---

## 5. Edge Weight Formula

**File:** `aider/repomap.py` (lines 481-514)

The edge weight for `(referencer → definer, ident)` is computed as:

```python
mul = 1.0

if ident in mentioned_idents:   mul *= 10    # Mentioned in task
if is_well_named(ident):        mul *= 10    # camelCase/snake_case/kebab-case AND ≥8 chars
if ident.startswith("_"):       mul *= 0.1   # Private identifier
if len(defines[ident]) > 5:     mul *= 0.1   # Defined in >5 files (too generic)
if referencer in chat_fnames:   mul *= 50    # File actively being edited (dominant boost)

freq = math.sqrt(num_refs)      # Sublinear scaling of reference frequency

edge_weight = mul * freq
```

**Factor table:**

| Factor | Multiplier | Rationale |
| :--- | :--- | :--- |
| Mentioned in current task | 10× | Explicit relevance signal |
| Well-named identifier (≥8 chars + naming convention) | 10× | API symbols tend to be well-named; implementation details tend to be short |
| Private / internal (`_` prefix) | 0.1× | Less likely to be cross-file dependencies |
| Defined in >5 files (generic) | 0.1× | Generic utilities don't discriminate relevance |
| In active chat context | 50× | Current edit files dominate neighborhood |
| Reference frequency | √N | Sublinear to prevent `print()` / `log()` drowning everything else |

**Rank distribution (lines 534-545):**

After PageRank assigns a score to each file-node, the score is distributed proportionally across the node's outgoing edges:

```python
for src in G.nodes:
    src_rank = ranked[src]
    total_weight = sum(edge["weight"] for edge in G.out_edges(src))
    for _, dst, data in G.out_edges(src, data=True):
        data["rank"] = src_rank * data["weight"] / total_weight
        ranked_definitions[(dst, data["ident"])] += data["rank"]
```

Final output: `ranked_definitions[(file, identifier)] = float_score`, sorted descending.

---

## 6. Token Budget Fitting — Binary Search

**File:** `aider/repomap.py` (lines 676-706)

After ranking, Aider includes as many top-ranked tags as fit in the token budget using binary search:

```python
lower, upper = 0, num_tags
best_tree, best_tokens = None, 0

while lower <= upper:
    tree = to_tree(ranked_tags[:middle])
    tokens = token_count(tree)
    
    pct_err = abs(tokens - max_tokens) / max_tokens
    
    if tokens <= max_tokens and tokens > best_tokens:
        best_tree, best_tokens = tree, tokens
    
    if pct_err < 0.15:  # Within 15% → accept immediately
        break
    
    if tokens < max_tokens:
        lower = middle + 1
    else:
        upper = middle - 1
    middle = (lower + upper) // 2
```

**Token counting** uses model-specific tokenizer. For large text, samples every N-th line for speed estimation.

---

## 7. Caching Architecture

**Three levels:**

| Level | Storage | Key | Invalidation |
| :--- | :--- | :--- | :--- |
| File tags | `diskcache.Cache` (SQLite on disk) | (abs_path, mtime) | File modification |
| Tree render | In-memory dict | (rel_fname, lois_tuple, mtime) | Process restart |
| Final map | In-memory dict | (chat_files, other_files, tokens, mentioned_fnames, mentioned_idents) | Mode-dependent |

**Map cache refresh modes:**
- `"always"` — never cache (development mode)
- `"files"` — cache when file list unchanged
- `"auto"` — cache if last build took >1 second
- `"manual"` — cache until explicit `force_refresh=True`

---

## 8. End-to-End Example

**User message:** "Fix the Car class to add logging"

**Step 1 — Extract seeds:**
```
chat_fnames     = {"models/car.py"}           (file in chat)
mentioned_idents = {"Car", "logging"}          (mentioned in message)
```

**Step 2 — Build graph (simplified):**
```
Nodes: car.py, logger.py, main.py, helpers.py
Edges:
  main.py → car.py  (ident="Car",    weight=50 * √3 = 86.6)  [chat + mentioned]
  car.py → logger.py (ident="Logger", weight=10 * √1 = 10.0)  [mentioned "logging"]
  helpers.py → car.py (ident="Car",  weight=10 * √1 = 10.0)  [mentioned]
```

**Step 3 — Personalization:**
```
personalization = {"models/car.py": 20}
```

**Step 4 — Personalized PageRank:**
```
ranked ≈ {"car.py": 0.45, "main.py": 0.25, "logger.py": 0.15, "helpers.py": 0.10}
```

**Step 5 — Rank distributed to definitions:**
```
ranked_definitions ≈ {
  ("logger.py", "Logger"): 0.10,
  ("main.py",   "main"):   0.20,
  ("helpers.py","helper"): 0.05,
}
```

**Step 6 — Filter (skip chat files: car.py), sort, binary search to 1024 tokens.**

**Step 7 — Render:**
```
logger.py:
│class Logger:
│    def __init__(self, name):
⋮

main.py:
⋮
│def main():
│    car = Car("Toyota", "Camry", 2020)
⋮
```

---

## 9. TALOS Adaptation — NEXUS Graph Seeding

### What Maps to What

| Aider Concept | NEXUS / TALOS Equivalent |
| :--- | :--- |
| Source file (node) | Tag or Routine (`plc_id + tag_name`, or `plc_id + routine_name`) |
| Symbol definition (`tag.kind == "def"`) | Tag defined in a routine (rung has OTE/OTL on this tag) |
| Symbol reference (`tag.kind == "ref"`) | Tag read in a routine (rung has XIC/XIO on this tag) |
| File-to-file edge | Cross-routine or cross-program reference |
| Chat context files → 50× boost | Current task's explicitly listed tags → 50× boost |
| Mentioned identifiers → 10× boost | Tag names in task description → 10× boost |
| `personalization` vector | Seed scores from task metadata |
| `to_tree()` → code snippets | NEXUS `rung_search` results → rung text + description |
| Token budget (1024) | Same; NEXUS output is typically 30-80 tokens per rung reference |

### NEXUS Data Sources for Graph Construction

All data already exists in the NEXUS DB:

| Graph need | NEXUS tool |
| :--- | :--- |
| Tag → routines where it appears | `tag_where_used(plc_id, tag_name)` |
| Cross-program references | `address_trace_chain(plc_id, address)` |
| Interlock relationships | `find_interlocks(plc_id, tag_name)` |
| Tag descriptions | `tag_context(plc_id, tag_name)` |
| Rung text for output | `rung_search(plc_id, term)` |

### Adaptation Pseudocode

```python
def build_nexus_seed_graph(task: Task) -> dict:
    """
    Returns: personalization dict {node_id: float} for PageRank seeding.
    """
    seeds = {}
    
    # Seed from task's primary tags (equivalent to chat context files)
    for tag in task.primary_tags:
        seeds[node_id(tag)] = 100 / len(task.primary_tags)
    
    # Seed from task description mentions
    mentioned = extract_tag_names(task.description)
    for tag_name in mentioned:
        results = nexus.tag_find_plant_wide(query=tag_name)
        for match in results:
            node = node_id(match)
            seeds[node] = max(seeds.get(node, 0), 50 / len(task.primary_tags))
    
    return seeds

def build_nexus_reference_graph(plc_id: str, tags: list[str]) -> nx.MultiDiGraph:
    """
    Build file-to-file equivalent graph over NEXUS tags.
    Nodes: routines. Edges: tag references between routines.
    """
    G = nx.MultiDiGraph()
    
    for tag in tags:
        usages = nexus.tag_where_used(plc_id, tag)
        defn_routines = [u.routine for u in usages if u.kind == "write"]
        ref_routines  = [u.routine for u in usages if u.kind == "read"]
        
        for referencer in ref_routines:
            for definer in defn_routines:
                mul = compute_weight(tag, referencer, definer, task_seeds)
                G.add_edge(referencer, definer, weight=mul, tag=tag)
    
    return G

def compute_weight(tag, referencer, definer, seeds) -> float:
    mul = 1.0
    if tag in task.mentioned_tags:     mul *= 10   # Mentioned in task
    if is_well_named(tag):             mul *= 10   # Follows ISA-5.1 naming convention
    if tag.startswith("Wrk_"):         mul *= 0.1  # Working/temp tag
    if tag.count_definitions > 5:     mul *= 0.1  # Too generic (shared permissive)
    if referencer in task.seeds:       mul *= 50   # Task seed routine
    return mul * math.sqrt(ref_count)

def get_nexus_context_map(task: Task, token_budget: int = 1024) -> str:
    """
    Full pipeline: seeds → graph → PageRank → render.
    """
    seeds = build_nexus_seed_graph(task)
    G = build_nexus_reference_graph(task.plc_id, task.all_related_tags)
    
    ranked = nx.pagerank(G, weight="weight",
                         personalization=seeds, dangling=seeds)
    
    # Distribute rank to (routine, tag) pairs
    ranked_items = distribute_rank(G, ranked)
    
    # Binary search to fit token budget
    return render_to_budget(ranked_items, token_budget, task.context)
```

### Output Format (TALOS equivalent of repo-map)

```
MainProgram / Rung 47 — FeedConveyor_Start [O:013/02]:
│XIC B3:5/1         ; System_Ready
│XIC T4:2.DN        ; Start_Delay.DN
│OTE O:013/02       ; FeedConveyor_Start

SubA / Rung 12 — System_Ready [B3:5/1]:
│XIC I:012/03       ; E-Stop_OK
│XIC B3:0/8         ; Air_Pressure_OK
│OTE B3:5/1         ; System_Ready
```

The equivalent of Aider's `│` lines is rung text with inline descriptions.

---

## 10. Implementation Notes for TALOS

### Where to Implement

The NEXUS context map belongs in the **planner** component, not in the capability layer:

```
TALOS Planner (strategy-ladder step: "research")
  → calls nexus_context_map(task)
  → inserts result into planner system prompt
  → planner now has ~1k-token relevant graph slice
  → decides which NEXUS tools to call next
```

NEXUS provides the raw data; TALOS builds the graph and runs PageRank. This respects the ADR-007 boundary (TALOS couples to NEXUS's output contract, not its internals).

### Neo4j vs. NetworkX

Two options:

**Option A — NetworkX (Python, in-process):**
- Pull relevant tags from NEXUS DB into a NetworkX graph.
- Run `nx.pagerank()` in-process.
- Same code path as Aider; easy to adapt.
- Suitable for graphs up to ~10k nodes.

**Option B — Native Cypher PageRank:**
```cypher
CALL gds.pageRank.stream('myGraph', {
  maxIterations: 20,
  dampingFactor: 0.85,
  sourceNodes: $seed_node_ids
})
YIELD nodeId, score
RETURN gds.util.asNode(nodeId).name AS tag, score
ORDER BY score DESC
LIMIT 50
```
- No Python graph copy needed.
- GDS library required on Neo4j instance.
- Better for graphs >10k nodes.
- For Acme's NEXUS graph (~127k address_xref entries), Option B is preferred.

**Recommendation:** Start with Option A (NetworkX) using a subgraph extracted by NEXUS queries. The subgraph only needs tags relevant to the current PLC's area — typically <500 nodes. Defer Option B until graph sizes warrant it.

### Token Budget for NEXUS Context

Aider targets 1024 tokens for a codebase repo-map. NEXUS rung text is denser:
- One rung reference with inline description: ~30-80 tokens.
- A useful slice: 15-30 rungs = 450-2400 tokens.

**Recommended budget:** 1500 tokens (enough for ~25 ranked rung references). Configurable per task type.

### Caching

- Graph structure: cache in Redis with a TTL tied to NEXUS last-indexed timestamp.
- PageRank results: cache per `(task_id, seed_set_hash)` — expires when task metadata changes.
- Rung text rendering: cache per `(plc_id, rung_number, mtime)` — same pattern as Aider's tag cache.

---

## Key Findings for TALOS

1. **The graph is file-level, not symbol-level.** This is intentional — symbol-level graphs become expensive at scale. For TALOS, "routine-level" nodes are the right analogy (not individual rung nodes).

2. **The 50× chat-context boost is the dominant factor.** Seeds pull rank toward their neighborhood. Everything else is a small multiplier in comparison. The seed selection algorithm matters more than the weight formula.

3. **Binary search for token fitting is essential.** You cannot predict the output size from the input. Always binary-search from the ranked list.

4. **Edge weight `is_well_named` heuristic is ISA-5.1 compatible.** PLC tags following ISA-5.1 (e.g., `PACK01_M500`, `Cfg_OvSpdHiSP`, `Alm_HiHiSP`) are inherently "well-named" by convention — they encode area, equipment, and function. The 10× boost for well-named identifiers directly rewards the naming standard already in use.

5. **Private/temp tag penalty (0.1×) maps directly.** `Wrk_` prefix tags in TALOS/NEXUS are exactly the "private implementation detail" that Aider's `_` prefix penalty targets.

6. **Generic permissives map to "defined in >5 files" penalty.** Tags like `B3:5/1 (System_Ready)` that appear in every program as an input are the PLC equivalent of Aider's "too popular = too generic" heuristic.

7. **The whole pipeline can run from existing NEXUS tools.** No new indexing infrastructure needed — `tag_where_used`, `find_interlocks`, `rung_search`, and `tag_context` provide all the data. The PageRank layer is a thin compute layer on top of NEXUS queries.
