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

The design priorities, in order: **compact**, **queryable**, **provenanced**,
**emergent-but-disciplined**. JSONL is the source of truth; markdown is only a derived
view. Never hand-edit the derived files.

## File layout

```
kg/
  nodes.jsonl      one JSON object per line — entity identity records
  edges.jsonl      one per line — every relation AND every time-stamped metric
  ontology.jsonl   the emergent type registry (predicates + node types)
  sources.jsonl    provenance registry (one row per source document)
  index.md         derived, human-readable index (regenerate; do not edit)
```

Everything lives under one graph directory (default `kg/`). Pass `--dir <path>` to put
it wherever the user keeps research. All four files are append-only and line-oriented,
so they diff cleanly, grep cleanly, and never require rewriting the whole store.

## Data model

**Node** — identity only, not facts:
```json
{"id": "AAPL", "type": "company", "name": "Apple Inc.", "aka": ["Apple"], "ticker": "AAPL"}
```
`id` is a short stable key you choose (ticker, ISO code, slug). `aka` holds synonyms so
the same entity is never created twice.

**Edge** — one row carries either a *relation* (object is another node, field `o`) or a
*metric* (a literal, fields `val` + `unit`). This unification means relations and
quantitative facts share one file and one traversal engine:
```json
{"s":"AAPL","p":"supplies_to","o":"TSMC","as_of":"2025-12-31","src":"10k-aapl-2025","conf":0.9}
{"s":"AAPL","p":"revenue","val":391035,"unit":"USD_m","as_of":"2025-09-28","src":"10k-aapl-2025"}
```
`as_of` and `src` are **mandatory on every edge** — every fact is timestamped and
sourced. This is what powers the time-series vs. contradiction logic below.

**Predicate registry** (`ontology.jsonl`) — the ontology is *emergent*: you mint new
node types and predicates as topics arise. But every new predicate must be registered
once, with its direction and shape, and then **reused**:
```json
{"p":"supplies_to","kind":"relation","domain":"company","range":"company","inverse":"supplied_by","card":"many"}
{"p":"country_of_risk","kind":"relation","domain":"company","range":"country","inverse":"risk_to","card":"one"}
{"p":"revenue","kind":"metric","domain":"company","unit":"USD_m","card":"many"}
```
`card:"one"` marks a *functional* predicate (a company has one country of risk *at a
given as_of*) — the linter uses this to detect real contradictions.

## Operations

There are four things you do with this skill. Run the engine with
`python scripts/graph.py <cmd>` (stdlib-only; no install).

### 1. Capture from dialogue
When research surfaces a fact worth keeping, write it to the graph **before moving on**.
Procedure: resolve or create the nodes → ensure the predicate is registered (reuse an
existing one if it means the same thing) → register the source → append the edge(s).
Always attach `as_of` (the date the fact is true *for*, not today) and `src`.

### 2. Ingest
When given a filing, transcript, or note, extract entities and relations in bulk. Batch
the `add-node` / `add-pred` / `add-src` / `add-edge` calls. See `references/playbooks.md`.

### 3. Query / traverse
- `find "<text>"` — locate a node by id, name, or alias.
- `neighbors --id AAPL --depth 2 [--pred supplies_to] [--dir out|in|both]` — local graph.
- `path --from AAPL --to TWN` — shortest relation chain (e.g. exposure tracing).
- `subgraph --id AAPL --depth 2` — JSON export for a dossier or further analysis.
- `stats` — size and shape of the graph.

A question like *"what's exposed to Taiwan risk?"* is `neighbors --id TWN --dir in`
or a set of `path` queries — the graph answers it by traversal, not recall.

### 4. Lint
Run `validate` after any batch of writes. It flags, as **errors**: missing `as_of`/`src`,
a `src` not in the registry, dangling node references, predicates not in the ontology
(with a *"did you mean …?"* synonym hint), and contradictions (a `card:"one"` predicate
with conflicting objects at the same `as_of`). Warnings cover unused sources.

## Ontology discipline (the thing that keeps it compact)

The ontology is allowed to grow, but it must not *rot* into synonyms
(`supplies_to` / `supplier_of` / `provides`). Before minting a predicate:

1. `python scripts/graph.py stats` and skim existing predicates.
2. If an existing predicate means the same thing, **reuse it**.
3. Only if genuinely new, `add-pred` with `domain`, `range`, `inverse`, and `card`.
4. Run `validate` — the fuzzy linter will catch a near-duplicate you missed.

Record relations in their canonical direction; the `inverse` field lets traversal walk
both ways, so you never store both `supplies_to` and `supplied_by` for the same fact.

## Reference files

- `references/schema.md` — full field specs and a worked example for each relation type
  the user cares about (country-of-risk, multi-level sector hierarchy, supply chain,
  segment tech-moat, import/export dependency, ownership).
- `references/playbooks.md` — ingest, query, and reconciliation procedures, including the
  contradiction-vs-time-series rule.

## Conventions

- Prefer stable real-world ids: tickers for companies, ISO-3166 alpha-3 for countries
  (`TWN`, `USA`), GICS-style slugs for sectors.
- `as_of` is an ISO date for the period the fact describes. The same fact at different
  `as_of` values is a **time series**, not a conflict — keep all of them.
- Keep `conf` (0–1) on edges that are inferred or uncertain; omit it when a source states
  the fact directly.
- Never delete history. Supersede by appending a newer `as_of`.
