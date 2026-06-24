---
name: investment-knowledge-graph
description: >-
  Build and maintain a compact, local knowledge graph for investment research —
  companies, sovereigns, sectors, supply chains, trade dependencies. Use when the
  user wants to capture, persist, or query relationships and time-varying facts
  learned during research: country-of-risk, sector/industry hierarchy, supply-chain
  networks, segment-level technology moats, import/export dependency graphs, and
  ownership. Triggers on "remember this for the graph", "what's exposed to X",
  "trace the supply chain", "add to my research KG", or any request to relate or
  traverse entities across sessions.
---

# Investment Knowledge Graph

## Mental model

A single graph is the long-term memory for investment research. Entities (companies,
sovereigns, sectors, people, products) are **nodes**; everything you learn about how
they relate or what they measure is an **edge**. The graph grows *as the conversation
goes* — when a dialogue surfaces a new relation ("TSMC fabs most of Apple's chips in
Taiwan"), you capture it as edges and it is available in every future session.

Two layers, by design:

- **The graph core (JSONL)** stays small and machine-fast — pure identity, typed
  relations, and time-stamped metrics. This is what you traverse.
- **An optional OKF content layer (markdown)** holds the heavy, human-readable material
  — entity dossiers, attached source excerpts/notes, and archived source text — under
  `docs/`. A node of kind `okf` is just a *pointer* to a file. You traverse the
  lightweight graph and only open an OKF file when you actually need the detail.

Design priorities, in order: **compact**, **queryable**, **provenanced**,
**emergent-but-disciplined**. JSONL is the source of truth; the OKF bundle is a
derived/companion view. Never hand-edit generated files.

## File layout

```
kg/
  nodes.jsonl      one JSON object per line — entity identity records + doc pointers
  edges.jsonl      one per line — every relation AND every time-stamped metric
  ontology.jsonl   the emergent type registry (predicates + node types)
  sources.jsonl    provenance registry (one row per source document)
  index.md         derived, human-readable index (regenerate; do not edit)
  docs/            OKF bundle — conformant markdown concept files (open on demand)
    index.md         bundle root (carries okf_version)
    log.md           export history
    <type>/<id>.md   generated entity dossiers (one per entity node)
    excerpts/<id>.md attached source excerpts / notes (authored, not regenerated)
    references/<src-id>.md  archived source text (authored, not regenerated)
```

Everything lives under one graph directory (default `kg/`). Pass `--dir <path>` to put
it wherever the user keeps research. The four JSONL files are append-only and
line-oriented, so they diff cleanly, grep cleanly, and never require rewriting the whole
store. The OKF bundle is fully local — `cat` to read, `git` to version; no cloud.

## Data model

**Node** — identity only, not facts:
```json
{"id": "AAPL", "type": "company", "name": "Apple Inc.", "aka": ["Apple"], "ticker": "AAPL"}
```
`id` is a short stable key you choose (ticker, ISO code, slug). `aka` holds synonyms so
the same entity is never created twice.

**Doc node** — a lightweight pointer to an OKF content file (kind `okf`). Carries no
heavy text itself, just where to find it:
```json
{"id": "excerpts/tsmc-cap", "type": "excerpt", "name": "TSMC capacity note", "path": "docs/excerpts/tsmc-cap.md", "kind": "okf"}
```

**Edge** — one row carries either a *relation* (object is another node, field `o`) or a
*metric* (a literal, fields `val` + `unit`). Relations and quantitative facts share one
file and one traversal engine:
```json
{"s":"AAPL","p":"supplies_to","o":"TSMC","as_of":"2025-12-31","src":"10k-aapl-2025","conf":0.9}
{"s":"AAPL","p":"revenue","val":391035,"unit":"USD_m","as_of":"2025-09-28","src":"10k-aapl-2025"}
```
A **reference edge** links an entity to a doc node — the same lightweight shape, object
is a doc node. One entity may reference many docs; one doc may be referenced by many
entities:
```json
{"s":"AAPL","p":"has_excerpt","o":"excerpts/tsmc-cap","as_of":"2026-06-24","src":"10k-aapl-2025"}
{"s":"TSMC","p":"has_excerpt","o":"excerpts/tsmc-cap","as_of":"2026-06-24","src":"10k-aapl-2025"}
```
`as_of` and `src` are **mandatory on every edge** — every fact is timestamped and
sourced. This powers the time-series vs. contradiction logic below.

**Predicate registry** (`ontology.jsonl`) — the ontology is *emergent*: you mint new
node types and predicates as topics arise, but every new predicate is registered once
with its direction and shape, then **reused**:
```json
{"p":"supplies_to","kind":"relation","domain":"company","range":"company","inverse":"supplied_by","card":"many"}
{"p":"country_of_risk","kind":"relation","domain":"company","range":"country","inverse":"risk_to","card":"one"}
{"p":"revenue","kind":"metric","domain":"company","unit":"USD_m","card":"many"}
```
`card:"one"` marks a *functional* predicate (a company has one country of risk *at a
given as_of*) — the linter uses this to detect real contradictions.

## Conformance contract

A knowledge base is **conformant** if (the `validate` command enforces all six):

1. every node has `id` + `type`;
2. every edge has `s`, `p`, `as_of`, `src`, and **exactly one** of `o` (relation) or
   `val` (metric);
3. every `src` resolves to a `sources.jsonl` entry;
4. every predicate `p` is registered in `ontology.jsonl`;
5. no functional (`card:"one"`) predicate has conflicting objects at the same `as_of`;
6. every doc pointer and every archived source resolves to a file on disk.

This is the stable interop surface — point other agents or colleagues at these six rules
and a one-command linter, rather than at prose. Derived OKF files are additionally
conformant with OKF v0.1 (every concept file carries YAML frontmatter with a `type`).

## Operations

Run the engine with `python scripts/graph.py <cmd>` (stdlib-only; no install).

### 1. Capture from dialogue
When research surfaces a fact worth keeping, write it to the graph **before moving on**.
Resolve or create the nodes → ensure the predicate is registered (reuse an existing one
if it means the same thing) → register the source → append the edge(s). Always attach
`as_of` (the date the fact is true *for*, not today) and `src`.

### 2. Ingest
When given a filing, transcript, or note, extract entities and relations in bulk. Batch
the `add-node` / `add-pred` / `add-src` / `add-edge` calls. See `references/playbooks.md`.

### 3. Query / traverse (stays lightweight)
- `find "<text>"` — locate a node by id, name, or alias.
- `neighbors --id AAPL --depth 2 [--pred supplies_to] [--dir out|in|both]` — local graph.
- `path --from AAPL --to TWN` — shortest relation chain (e.g. exposure tracing).
- `subgraph --id AAPL --depth 2` — JSON export for a dossier or further analysis.
- `stats` — size and shape of the graph.

A question like *"what's exposed to Taiwan risk?"* is `neighbors --id TWN --dir in` or a
set of `path` queries — answered by traversal, not recall. Doc pointers show up in
traversal as edges but their content is never loaded; filter with `--pred` if you want
only entity-to-entity relations.

### 4. OKF content layer (read more only when needed)
- `attach-doc --id excerpts/<slug> --type excerpt --title "..." --for AAPL --for TSMC --rel has_excerpt --src <srcid> --body "..."`
  writes a conformant OKF markdown file, registers a lightweight doc node (a pointer),
  registers the doc as a source, auto-registers the reference predicate if new, and links
  it from each `--for` entity. `--body-file` attaches longer material; the same doc id can
  back several entities.
- `archive-src --id <srcid> --title "..." --url ... --kind filing --body-file extract.txt`
  archives the salient extracted text of a source as a first-class OKF concept under
  `docs/references/<srcid>.md` and records the `archive` path on the `sources.jsonl` entry.
  Use it for material that rots — paywalled articles, edited web pages, pulled transcripts —
  so provenance stays durable. Citations in dossiers then link to the local archive.
- `read-doc --id <concept-or-src-id>` — prints one OKF file (an excerpt, note, or archived
  source). Progressive disclosure: traverse cheaply, then open exactly what you need.
- `export-okf [--out kg/docs]` — (re)generates the bundle: one OKF-conformant dossier per
  entity (frontmatter with Title-cased `type`, `resource: kg://node/<id>` back-pointer,
  `title`/`description`/`tags`/`timestamp`; body with Relationships + Metrics + Documents +
  Referenced-by + Citations), per-directory `index.md`, a root `index.md` with
  `okf_version`, a `references/index.md` for archived sources, and a dated `log.md` entry.
  Attached docs and archived sources are **never** overwritten — only dossiers and indexes
  are regenerated.

### 5. Lint
Run `validate` after any batch of writes; it enforces the six-rule conformance contract
above. **Errors**: node missing `type`; edge missing `as_of`/`src`, or carrying both `o`
and `val`, or neither; a `src` not in the registry; dangling node references; predicates
not in the ontology (with a *"did you mean …?"* synonym hint); contradictions.
**Warnings**: unused sources; doc pointers or archived sources whose file is missing.

## Ontology discipline (the thing that keeps it compact)

The ontology may grow but must not *rot* into synonyms (`supplies_to` / `supplier_of` /
`provides`). Before minting a predicate: `stats` and skim existing predicates → reuse one
that means the same thing → only if genuinely new, `add-pred` with `domain`, `range`,
`inverse`, `card` → run `validate`; the fuzzy linter catches near-duplicates. Record
relations in their canonical direction; the `inverse` field lets traversal walk both ways.

## Reference files

- `references/schema.md` — full field specs (doc nodes, reference edges, archived sources,
  the OKF bundle layout, dossier frontmatter) plus a worked example for each relation type.
- `references/playbooks.md` — ingest, query, attach-excerpt, archive-source, export, and
  reconciliation procedures, including the contradiction-vs-time-series rule.

## Conventions

- Prefer stable real-world ids: tickers for companies, ISO-3166 alpha-3 for countries
  (`TWN`, `USA`), GICS-style slugs for sectors.
- Doc node `id` is its OKF concept id (path under `docs/`, minus `.md`), so the graph and
  the bundle interoperate. Dossiers carry `resource: kg://node/<id>` so an OKF consumer can
  map a concept file back to its graph node.
- `as_of` is an ISO date for the period the fact describes. The same fact at different
  `as_of` values is a **time series**, not a conflict — keep all of them.
- Keep `conf` (0–1) on inferred or uncertain edges; omit it when a source states the fact
  directly.
- Never delete history. Supersede by appending a newer `as_of`.
